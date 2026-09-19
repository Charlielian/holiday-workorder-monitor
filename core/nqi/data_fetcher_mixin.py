# -*- coding: utf-8 -*-
"""
Data fetch mixin
负责查询计数、分批获取、数据提取等数据获取逻辑
"""
import copy
import json
import logging

import requests

from core.nqi.common import (
    CountRequestError, CountNotFoundError, SessionExpiredError,
    DataFetchError, BatchFetchError, get_cookie_value,
)
from core.nqi.utils.config import JXCX_COUNT_URL, JXCX_URL, HEADERS
from core.nqi.utils.constants import (
    TIMEOUT_EXTRA_LONG, MAX_SINGLE_QUERY,
    RETRY_TIMES, RETRY_DELAY,
)
from core.nqi.utils.helpers import encode_datatables_payload
from core.nqi.utils.logger import get_report_logger
from core.nqi.utils.retry import RetryError, retry_with_backoff

logger = logging.getLogger(__name__)


class DataFetcherMixin:
    """Data fetch methods.

    JXCX 将 ``getTableCount`` 与 ``getTable`` 作为两步协议：先用裁剪后的
    payload 估算行数，再按 ``start/length`` 分页取数。这里保留多种响应字段
    和失败回退，是为了兼容不同报表后端及 DataTables 风格，而不是因为所有
    接口都同时返回这些字段。
    """

    def get_table_count(self, payload, retry_times=None, retry_delay=None, report_name=None):
        """获取查询结果行数（使用指数退避重试）

        Args:
            payload: 请求参数
            retry_times: 重试次数（默认使用常量）
            retry_delay: 重试间隔（保留兼容参数，实际由 retry_with_backoff 指数退避控制）
            report_name: 报表名称（用于日志标识，传入时使用report_logger）
        """
        # 如果提供了report_name，使用report_logger；否则使用模块级logger
        if report_name:
            log = get_report_logger(report_name)
        else:
            log = logger
            report_name = "QueryModule"

        if retry_times is None:
            retry_times = RETRY_TIMES
        if retry_delay is None:
            retry_delay = RETRY_DELAY

        if not self.enabled:
            self.enter_jxcx()

        # count接口只接受查询维度、where和字段result；columns/order/search是
        # getTable 的 DataTables 参数，混入 count 请求会导致部分报表返回异常。
        # count 失败时使用 MAX_SINGLE_QUERY，故意让后续 getTable 继续尝试，
        # 避免把“统计接口故障”误报成“查询结果为空”。
        key_list = ['geographicdimension', 'timedimension', 'enodebField', 'cgiField',
                    'timeField', 'cellField', 'cityField', 'result', 'where', 'indexcount']
        payload_count = {key: value for key, value in payload.items() if key in key_list}
        payload_encoded = encode_datatables_payload(payload_count)

        # ========== 详细调试日志 ==========
        log.info("")
        log.info("╔══════════════════════════════════════════════════════════════════════════════╗")
        log.info("║ [get_table_count] 获取数据总数 [%s]                                      ║", report_name)
        log.info("╠══════════════════════════════════════════════════════════════════════════════╣")
        log.info("║ 请求URL: %s", JXCX_COUNT_URL)
        log.info("║")

        # Session/Cookie状态诊断
        log.info("║ [Session状态诊断]")
        castgc = get_cookie_value(self.sess.cookies, 'CASTGC', domain='nqi.gmcc.net')
        if not castgc:
            castgc = get_cookie_value(self.sess.cookies, 'CASTGC')
        if castgc:
            log.info("║   CASTGC cookie: 存在 (长度=%d)", len(castgc))
        else:
            log.info("║   CASTGC cookie: 不存在!")

        jsessionid = get_cookie_value(self.sess.cookies, 'JSESSIONID', domain='nqi.gmcc.net')
        if not jsessionid:
            jsessionid = get_cookie_value(self.sess.cookies, 'JSESSIONID')
        if jsessionid:
            log.info("║   JSESSIONID cookie: 存在")
        else:
            log.info("║   JSESSIONID cookie: 不存在")

        # 请求头信息
        log.info("║")
        log.info("║ [请求Headers]")
        for key in ['User-Agent', 'Content-Type', 'Cookie']:
            if key == 'Cookie':
                cookie_str = '; '.join([f"{c.name}={c.value[:20]}..." if len(c.value) > 20 else f"{c.name}={c.value}"
                                       for c in self.sess.cookies])
                log.info("║   %s: %s", key, cookie_str[:200])
            elif key in HEADERS:
                log.info("║   %s: %s", key, HEADERS[key])
        log.info("║")
        log.info("║ [筛选后的payload参数]")
        for k, v in payload_count.items():
            if k == 'result':
                log.info("║   %s: (JSON, 长度=%d字符)", k, len(json.dumps(v)))
            elif k == 'where':
                log.info("║   %s: %s", k, json.dumps(v)[:200])
            elif k == 'columns':
                log.info("║   %s: (列表, %d项)", k, len(v) if isinstance(v, list) else 0)
            else:
                log.info("║   %s: %s", k, v)
        log.info("║")
        log.info("║ [编码后的请求体] (长度=%d字符)", len(payload_encoded))
        log.info("║ %s", payload_encoded[:300])
        if len(payload_encoded) > 300:
            log.info("║ ... (省略 %d 字符)", len(payload_encoded) - 300)
        log.info("╚══════════════════════════════════════════════════════════════════════════════╝")
        log.info("")

        try:
            return retry_with_backoff(
                lambda: self._send_count_request(payload_encoded, report_name),
                max_retries=retry_times,
                base_delay=2.0,
                exceptions=(
                    requests.exceptions.RequestException,
                    SessionExpiredError,
                    CountNotFoundError,
                )
            )
        except (CountRequestError, RetryError) as e:
            log.warning("查询总数失败: %s，返回MAX_SINGLE_QUERY (%d)", e, MAX_SINGLE_QUERY)
            return MAX_SINGLE_QUERY

    def _send_count_request(self, payload_encoded, report_name):
        """发送单次数据总数请求并解析响应（供 retry_with_backoff 调用）

        Returns:
            int: 数据总数

        Raises:
            SessionExpiredError: Session过期，可重试
            CountNotFoundError: 未找到count字段，可重试
            CountRequestError: HTTP层错误，不重试
            requests.exceptions.RequestException: 网络层错误，可重试
        """
        log = get_report_logger(report_name) if report_name else logger

        res = self.sess.post(JXCX_COUNT_URL, data=payload_encoded, headers=HEADERS, timeout=TIMEOUT_EXTRA_LONG)

        if res.status_code != 200:
            log.error("HTTP状态码异常: %s", res.status_code)
            self.enabled = False
            raise CountRequestError(f"HTTP {res.status_code}")

        if not res.content or len(res.content.strip()) == 0:
            log.error("响应内容为空，可能是Session过期")
            self.enabled = False
            if self.enter_jxcx():
                raise SessionExpiredError("响应为空，Session可能过期")
            raise CountRequestError("响应为空且无法重新进入Session")

        try:
            result = json.loads(res.content)
        except json.JSONDecodeError as e:
            log.error("JSON解析失败: %s", e)
            self.enabled = False
            if self.enter_jxcx():
                raise SessionExpiredError(f"JSON解析失败: {e}")
            raise CountRequestError(f"JSON解析失败: {e}")

        # 检查是否有错误消息
        if 'message' in result and result['message']:
            msg = str(result['message'])
            if any(err in msg for err in ['不存在', '失败', '错误', 'error', 'Error', '异常', 'timeout', 'Timeout']):
                log.warning("API返回错误: %s", msg)
                raise CountRequestError(f"API返回错误: {msg}")

        # 提取count字段
        count = self._extract_count_from_response(result)
        if count is not None:
            return int(count) if count != '' else 0

        raise CountNotFoundError("未能在响应中找到count字段")

    def _extract_count_from_response(self, result):
        """从API响应中提取count字段

        Args:
            result: API响应的JSON对象

        Returns:
            count值或None（如果未找到）
        """
        # count响应在不同接口中可能位于顶层、data对象或DataTables字段；按
        # 稳定性从专用字段到通用字段尝试，避免仅依赖某一个版本的响应包装。
        if 'count' in result:
            return result['count']

        # 位置2: result['data']['count']
        if 'data' in result and isinstance(result['data'], dict):
            if 'count' in result['data']:
                return result['data']['count']

        # 位置3: result['data']['total']
        if 'data' in result and isinstance(result['data'], dict):
            if 'total' in result['data']:
                return result['data']['total']

        # 位置4: result['recordsTotal']
        if 'recordsTotal' in result:
            return result['recordsTotal']

        # 位置5: result['data'] 是数字
        if 'data' in result and isinstance(result['data'], (int, float)):
            return result['data']

        # 位置6: result['result'] 是数字
        if 'result' in result and isinstance(result['result'], (int, float)):
            return result['result']

        # 位置7: result['recordsFiltered'] - DataTables标准格式
        if 'recordsFiltered' in result:
            return result['recordsFiltered']

        return None

    def _fetch_data(self, payload, timeout=None, report_name=None):
        """发送请求获取数据（从API获取单页数据）

        Args:
            payload: 请求参数
            timeout: 超时时间（秒）
            report_name: 报表名称（用于日志标识）

        Returns:
            list: 数据列表，失败返回空列表
        """
        # 检查是否已取消
        if self.is_cancelled():
            logger.info("[_fetch_data] 查询已被取消，跳过数据获取")
            return []
        if report_name:
            log = get_report_logger(report_name)
        else:
            log = logger

        if timeout is None:
            timeout = getattr(self, '_current_batch_timeout', 300)

        # ========== 详细日志记录（用于排查问题） ==========
        logger.info("")
        logger.info("┌──────────────────────────────────────────────────────────────────┐")
        logger.info("│ [_fetch_data] 发送数据请求                                        │")
        logger.info("├──────────────────────────────────────────────────────────────────┤")
        logger.info("│ 请求URL: %s", JXCX_URL)
        logger.info("│ 请求方法: POST")
        logger.info("│ 超时时间: %ds", timeout)

        # 编码payload
        payload_encoded = encode_datatables_payload(payload)
        logger.info("│ 编码后Payload长度: %d 字符", len(payload_encoded))

        # 显示关键参数
        logger.info("│ [关键参数]")
        for key in ['start', 'length', 'geographicdimension', 'timedimension']:
            if key in payload:
                logger.info("│   %s: %s", key, payload[key])
        logger.info("└──────────────────────────────────────────────────────────────────┘")

        try:
            res = self.sess.post(JXCX_URL, data=payload_encoded, headers=HEADERS, timeout=timeout)

            # ========== 详细响应日志 ==========
            logger.info("")
            logger.info("┌──────────────────────────────────────────────────────────────────┐")
            logger.info("│ [_fetch_data] 响应信息                                           │")
            logger.info("├──────────────────────────────────────────────────────────────────┤")
            logger.info("│ HTTP状态码: %s", res.status_code)
            logger.info("│ 响应头 Content-Type: %s", res.headers.get('Content-Type', 'N/A'))
            logger.info("│ 响应内容长度: %d 字节", len(res.content))

            if res.status_code != 200:
                logger.error("│ 响应内容: %s", res.text[:500])
                logger.error("└──────────────────────────────────────────────────────────────────┘")
                self.enabled = False
                raise DataFetchError(f"HTTP {res.status_code}")

            if not res.content or len(res.content.strip()) == 0:
                logger.error("│ 响应内容为空，可能是Session过期")
                logger.error("└──────────────────────────────────────────────────────────────────┘")
                self.enabled = False
                raise DataFetchError("响应内容为空")

            try:
                result = json.loads(res.content)
            except json.JSONDecodeError as e:
                logger.error("│ JSON解析失败: %s", e)
                logger.error("│ 响应内容: %s", res.text[:500])
                logger.error("└──────────────────────────────────────────────────────────────────┘")
                self.enabled = False
                raise DataFetchError(f"JSON解析失败: {e}") from e

            # 打印完整响应内容用于调试
            logger.info("│ 响应keys: %s", list(result.keys()) if isinstance(result, dict) else type(result))
            logger.info("│ 响应内容 (前500字符): %s", str(result)[:500])
            if len(str(result)) > 500:
                logger.info("│                  ... (省略 %d 字符)", len(str(result)) - 500)

            # 检查是否有错误消息
            if 'message' in result and result['message']:
                msg_text = str(result['message'])
                logger.warning("│ 服务器消息: %s", msg_text)
                if '不存在' in msg_text:
                    logger.warning("└──────────────────────────────────────────────────────────────────┘")
                    raise DataFetchError(msg_text)

            # 获取数据列表 - 支持多种响应格式
            data_list = result.get('data') or []
            if not data_list and isinstance(result, dict):
                # 尝试其他可能的数据字段
                for key in ['result', 'records', 'rows', 'dataList']:
                    if key in result and result[key]:
                        data_list = result[key] if isinstance(result[key], list) else []
                        logger.info("│ 使用备用字段 '%s' 获取到 %d 条数据", key, len(data_list))
                        break

            logger.info("│ 返回数据条数: %d", len(data_list))

            if not data_list:
                logger.warning("│ [警告] 数据为空")
                logger.warning("└──────────────────────────────────────────────────────────────────┘")
            else:
                logger.info("│ 第一条数据字段: %s", list(data_list[0].keys())[:10])
                logger.info("└──────────────────────────────────────────────────────────────────┘")

            return data_list

        except requests.exceptions.Timeout as e:
            log.error("请求超时 (timeout=%ds)", timeout)
            raise DataFetchError(f"请求超时: {e}") from e
        except requests.exceptions.ConnectionError as e:
            log.error("连接错误: %s", str(e)[:100])
            raise DataFetchError(f"连接错误: {e}") from e
        except DataFetchError:
            raise
        except Exception as e:
            log.error("请求异常: %s", str(e)[:100])
            raise DataFetchError(f"请求异常: {e}") from e

    def _fetch_by_loop(self, payload, total_count, progress_callback=None):
        """分批获取全部数据（防止单次请求超时/内存溢出）

        Args:
            payload: 查询参数
            total_count: 预期总行数
            progress_callback: 进度回调函数 callback(current, total, message)

        Returns:
            list: 数据列表
        """
        logger.debug("开始获取数据，预期总量: %d", total_count)

        BATCH_SIZE = 50000  # 后端和内存之间的折中阈值；取消只在批次边界轮询，
        # 因而不会强行中断已经发出的 HTTP 请求。
        all_data = []

        if total_count <= BATCH_SIZE:
            # 检查是否已取消
            if self.is_cancelled():
                logger.info("[_fetch_by_loop] 查询已被取消，跳过单次获取")
                return []

            # 数据量小，一次性获取
            p = copy.deepcopy(payload)
            p['start'] = 0
            p['length'] = total_count

            timeout = max(120, min(600, 30 + (total_count // 1000) * 60))
            logger.info("获取数据: 总%d条，单次请求", total_count)

            if progress_callback:
                progress_callback(0, total_count, f"正在获取 {total_count} 条数据...")

            try:
                data_list = self._fetch_data(p, timeout=timeout)
                if total_count > 0 and not data_list:
                    raise DataFetchError(f"预期 {total_count} 条数据，但请求返回空结果")
                if data_list:
                    all_data = data_list
                if progress_callback:
                    progress_callback(len(all_data), total_count, f"获取完成: {len(all_data)} 条")
                return all_data
            except Exception as e:
                logger.error("获取数据失败: %s", str(e)[:200])
                if progress_callback:
                    progress_callback(0, total_count, f"获取失败: {str(e)[:50]}")
                raise

        # 数据量大，分批获取
        logger.info("获取数据: 总%d条，分批查询（每批%d条）", total_count, BATCH_SIZE)
        batches = (total_count + BATCH_SIZE - 1) // BATCH_SIZE

        batch_idx = 0
        while batch_idx < batches:
            # 检查是否已取消（每批之间轮询，及时响应停止）
            if self.is_cancelled():
                logger.info("[_fetch_by_loop] 收到取消请求，已获取 %d/%d 条，停止后续批次", len(all_data), total_count)
                if progress_callback:
                    progress_callback(len(all_data), total_count, "已取消查询，停止获取")
                break

            start = batch_idx * BATCH_SIZE
            length = min(BATCH_SIZE, total_count - start)

            p = copy.deepcopy(payload)
            p['start'] = start
            p['length'] = length

            timeout = max(120, min(600, 30 + (length // 1000) * 60))

            if progress_callback:
                progress_callback(len(all_data), total_count,
                                  f"正在获取第 {batch_idx + 1}/{batches} 批 ({start}-{start + length})")

            try:
                batch_data = self._fetch_data(p, timeout=timeout)
                if batch_data:
                    all_data.extend(batch_data)
                    logger.info("  ✓ 第%d批: 获取%d条（累计%d/%d）",
                                batch_idx + 1, len(batch_data), len(all_data), total_count)
                else:
                    logger.warning("  ⚠ 第%d批返回空数据", batch_idx + 1)
                    raise BatchFetchError(batch_idx + 1, start, length, "返回空数据")
            except BatchFetchError:
                raise
            except Exception as e:
                logger.error("  ✗ 第%d批获取失败: %s", batch_idx + 1, str(e)[:100])
                raise BatchFetchError(batch_idx + 1, start, length, str(e)) from e

            batch_idx += 1

        if progress_callback:
            if self.is_cancelled():
                progress_callback(len(all_data), total_count, f"已取消查询，结果不完整: {len(all_data)} 条")
            else:
                progress_callback(len(all_data), total_count, f"获取完成: {len(all_data)} 条")

        if not self.is_cancelled() and len(all_data) != total_count:
            raise DataFetchError(f"数据获取不完整: 期望 {total_count} 条，实际 {len(all_data)} 条")

        return all_data

    def _get_field_mapping(self, payload):
        """获取字段中英文映射

        Args:
            payload: 查询参数

        Returns:
            DataFrame: 包含字段映射的DataFrame，第一行是字段名
        """
        import pandas as pd

        if 'result' not in payload or 'result' not in payload['result']:
            logger.warning("payload中缺少result字段")
            return pd.DataFrame()

        # 上游字段元数据使用 feild/feildName（原始拼写），先拼成一行
        # 临时数据，get_table 再把它转成 DataFrame 的真实列名并删除该行。
        result_list = payload['result']['result']
        result_df = pd.DataFrame(result_list)

        en = list(result_df['feild'])
        zn = list(result_df['feildName'])
        en_zh_dict = dict(zip(en, zn))

        return pd.DataFrame([en_zh_dict])

    def get_table(self, payload, to_df=True, progress_callback=None, report_name="未知报表"):
        """执行完整的 JXCX 查询并返回全部结果。

        先验证模块和 Session，再通过 ``getTableCount`` 确定分页规模，随后
        用完整 DataTables payload 获取数据。count 为零时仍保留一次
        ``getTable`` 兜底，因为部分周粒度或旧报表的统计接口并不可靠。
        返回 DataFrame 时会暂时用 payload 中的 ``feild``/``feildName``
        生成中文列名；非 DataFrame 模式保持 ``{'data': [...]}`` 契约。

        Args:
            payload: 请求参数。
            to_df: 是否转换为 DataFrame。
            progress_callback: callback(current, total, message)。
            report_name: 报表名称（用于日志标识）。
        """
        import pandas as pd

        # 获取报表专用的日志记录器
        report_logger = get_report_logger(report_name)

        report_logger.info("▶ 开始提取报表: %s", report_name)

        if not self.enabled:
            report_logger.info("JXCX 未启用，尝试进入...")
            if not self.enter_jxcx():
                report_logger.error("无法进入即席查询模块")
                return pd.DataFrame() if to_df else {'data': []}

        # 主动检测 Session 有效性
        if not self.check_session_valid():
            report_logger.warning("Session已过期，尝试重新进入...")
            if not self.enter_jxcx(retry_times=2, timeout=60):
                report_logger.error("重新进入失败，请尝试重新登录")
                return pd.DataFrame() if to_df else {'data': []}

        # ========== 详细调试信息（仅文件） ==========
        import json as json_lib

        # 请求目标详情（DEBUG）
        if 'result' in payload and 'result' in payload['result']:
            table_configs = payload['result']['result']
            table_names = list(set([r.get('table', '') for r in table_configs]))
            field_names = list(set([r.get('feildName', '') for r in table_configs]))
            report_logger.debug("表名: %s", table_names)
            report_logger.debug("字段: %s", field_names)

        geo_dim = payload.get('geographicdimension', 'N/A')
        time_dim = payload.get('timedimension', 'N/A')
        report_logger.debug("地理维度: %s, 时间维度: %s", geo_dim, time_dim)

        # ========== 周粒度报表日期范围校验 ==========
        if '周' in time_dim and 'where' in payload and payload['where']:
            try:
                from datetime import datetime
                date_conditions = []
                for cond in payload['where']:
                    if cond.get('feild', '') == 'starttime':
                        val = cond.get('val', '')
                        if ' ' in val:
                            val = val.split(' ')[0]
                        date_conditions.append(val)

                if len(date_conditions) >= 2:
                    start = datetime.strptime(date_conditions[0], '%Y-%m-%d')
                    end = datetime.strptime(date_conditions[1], '%Y-%m-%d')
                    days_diff = (end - start).days

                    if days_diff < 7:
                        report_logger.warning("")
                        report_logger.warning("╔══════════════════════════════════════════════════════════════════╗")
                        report_logger.warning("║ [警告] 周粒度报表的日期范围不足一周!                              ║")
                        report_logger.warning("║ 当前范围: %s ~ %s (共 %d 天)",
                            date_conditions[0], date_conditions[1], days_diff)
                        report_logger.warning("║ 周粒度报表需要完整的周数据，单日查询可能返回0条                    ║")
                        report_logger.warning("║ 建议: 将结束日期延长至开始日期后7天以上                          ║")
                        report_logger.warning("╚══════════════════════════════════════════════════════════════════╝")
                        report_logger.warning("")
            except Exception as e:
                report_logger.debug("日期范围校验异常: %s", e)

        # 查询条件（DEBUG）- 添加更详细的日志
        if 'where' in payload and payload['where']:
            report_logger.info("=" * 60)
            report_logger.info("[查询条件详情]")
            for i, cond in enumerate(payload['where']):
                report_logger.info("  条件[%d]: %s %s %s", i, cond.get('feild', ''), cond.get('symbol', ''), cond.get('val', ''))
            report_logger.info("=" * 60)

        # 完整Payload（DEBUG）
        report_logger.debug("请求Payload: %s", json_lib.dumps(payload, ensure_ascii=False, indent=2))

        # 检查是否已取消
        if self.is_cancelled():
            report_logger.info("[get_table] 查询已被取消，跳过")
            return pd.DataFrame() if to_df else {'data': []}

        # ========== 第一步：获取总行数 ==========
        count_payload = payload.copy()
        key_list = ['geographicdimension', 'timedimension', 'enodebField', 'cgiField',
                    'timeField', 'cellField', 'cityField', 'result', 'where', 'indexcount']
        count_payload = {key: value for key, value in count_payload.items() if key in key_list}

        report_logger.debug("获取数据总数...")

        total_count = self.get_table_count(count_payload, report_name=report_name)
        report_logger.info("预期数据行数: %d", total_count)

        if progress_callback:
            progress_callback(0, total_count, f"开始查询，共 {total_count} 行数据")

        if total_count == 0:
            report_logger.warning("数据为空，尝试直接调用 getTable 获取数据...")
            report_logger.info("┌──────────────────────────────────────────────────────────────────┐")
            report_logger.info("│ [调试模式] getTableCount 返回 0，尝试直接获取数据                │")
            report_logger.info("└──────────────────────────────────────────────────────────────────┘")

            data_payload = payload.copy()
            data_payload['draw'] = 1
            data_payload['start'] = 0
            data_payload['length'] = 200
            data_payload['total'] = 0
            if 'columns' not in data_payload:
                data_payload['columns'] = []
            if 'order' not in data_payload:
                data_payload['order'] = [{'column': 0, 'dir': 'desc'}]
            if 'search' not in data_payload:
                data_payload['search'] = {'value': '', 'regex': False}
            if 'indexcount' in data_payload:
                data_payload['indexcount'] = 2

            try:
                debug_data = self._fetch_data(data_payload, timeout=120, report_name=report_name)
                if self.is_cancelled():
                    report_logger.info("[get_table] 查询已被取消，返回空结果")
                    return pd.DataFrame() if to_df else {'data': []}
                if debug_data and len(debug_data) > 0:
                    report_logger.info("✓ [调试模式] getTable 成功返回 %d 条数据!", len(debug_data))
                    if to_df:
                        res_df = pd.DataFrame(debug_data)
                        en_zh_df = self._get_field_mapping(payload)
                        if not en_zh_df.empty:
                            res_df = pd.concat([en_zh_df, res_df], ignore_index=True)
                            index_first = res_df.index.tolist()[0]
                            to_colname = list(res_df.loc[index_first])
                            res_df.columns = to_colname
                            res_df.drop(index=index_first, inplace=True)
                        report_logger.info("✓ 报表完成: %s, 数据: %d行 x %d列", report_name, len(res_df), len(res_df.columns))
                        return res_df
                    else:
                        return {'data': debug_data}
                else:
                    report_logger.warning("getTable 也返回空数据，确认该日期范围内无数据")
                    report_logger.warning("提示: 周粒度报表需要至少7天的日期范围")
                    return pd.DataFrame() if to_df else {'data': []}
            except Exception as e:
                report_logger.error("调试模式 getTable 调用失败: %s", e)
                return pd.DataFrame() if to_df else {'data': []}

        # ========== 第二步：获取数据 ==========
        report_logger.info("")
        report_logger.info("┌──────────────────────────────────────────────────────────────────┐")
        report_logger.info("│ Step 2: 获取数据内容                                              │")
        report_logger.info("├──────────────────────────────────────────────────────────────────┤")
        report_logger.info("│ 请求URL: %s", JXCX_URL)
        report_logger.info("│ 预计超时时间: %ds", max(60, min(300, total_count // 1000 * 2)))
        report_logger.info("└──────────────────────────────────────────────────────────────────┘")

        # getTable需要完整参数，与浏览器成功请求一致
        data_payload = payload.copy()
        data_payload['draw'] = 1
        data_payload['start'] = 0
        data_payload['length'] = 200  # 浏览器使用200作为初始值
        data_payload['total'] = 0

        if 'columns' not in data_payload:
            report_logger.warning("警告: payload中缺少columns参数")

        if 'order' not in data_payload:
            data_payload['order'] = [{'column': 0, 'dir': 'desc'}]

        if 'search' not in data_payload:
            data_payload['search'] = {'value': '', 'regex': False}

        if 'indexcount' in data_payload:
            data_payload['indexcount'] = 2

        data_list = self._fetch_by_loop(data_payload, total_count, progress_callback)

        report_logger.info("[结果] 实际获取数据: %d 条", len(data_list))

        if to_df:
            res_df = pd.DataFrame(data_list) if data_list else pd.DataFrame()

            if res_df.empty:
                report_logger.warning("DataFrame为空，未获取到任何数据")
                report_logger.warning("数据为空的常见原因:")
                report_logger.warning("  1. 该日期范围内确实没有数据")
                report_logger.warning("  2. 地市名称与数据库不匹配")
                report_logger.warning("  3. 查询条件(where)字段与数据库表结构不匹配")
                report_logger.warning("  4. 时间字段格式不正确")
                return pd.DataFrame()

            # 应用字段映射（与旧版保持一致）
            en_zh_df = self._get_field_mapping(payload)
            if not en_zh_df.empty:
                res_df = pd.concat([en_zh_df, res_df], ignore_index=True)
                index_first = res_df.index.tolist()[0]
                to_colname = list(res_df.loc[index_first])
                res_df.columns = to_colname
                res_df.drop(index=index_first, inplace=True)
                report_logger.info("[字段映射] 已转换为中文字段名，共 %d 个字段", len(to_colname))

            report_logger.info("✓ 报表完成: %s, 数据: %d行 x %d列", report_name, len(res_df), len(res_df.columns))
            return res_df
        else:
            report_logger.info("✓ 报表完成: %s, 数据: %d条", report_name, len(data_list))
            return {'data': data_list}