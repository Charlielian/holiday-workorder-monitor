# -*- coding: utf-8 -*-
"""
数据查询模块
负责即席查询、数据获取和分批处理

聚合模块：将 JXCXQuery 拆分为多个 mixin 类，在此重新组合。
"""
import logging
import copy
import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.nqi.utils.config import (
    BASE_URL, JXCX_URL, JXCX_COUNT_URL, JXCX_SEARCH_URL, JXCX_TABLE_URL,
    HEADERS
)
from core.nqi.utils.constants import (
    TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG, TIMEOUT_EXTRA_LONG,
    RETRY_TIMES, RETRY_DELAY,
    BATCH_THRESHOLD, MAX_SINGLE_QUERY, BATCH_SIZES, BATCH_TIMEOUTS,
    MAX_PARALLEL_QUERIES,
    DEFAULT_DRAW, DEFAULT_START, DEFAULT_LENGTH
)
from core.nqi.utils.logger import get_report_logger
from core.nqi.utils.retry import RetryError, retry_with_backoff
from core.nqi.utils.helpers import datatype_to_code, encode_datatables_payload

# --- Re-export module-level names for backward compatibility ---
from core.nqi.common import (
    CountRequestError, CountNotFoundError, SessionExpiredError,
    DataFetchError, BatchFetchError, get_cookie_value, convert_where_conditions
)

# --- Mixin imports ---
from core.nqi.session_mixin import SessionMixin
from core.nqi.payload_builder_mixin import PayloadBuilderMixin
from core.nqi.data_fetcher_mixin import DataFetcherMixin
from core.nqi.voice_merger_mixin import VoiceMergerMixin

logger = logging.getLogger(__name__)


class JXCXQuery(SessionMixin, PayloadBuilderMixin, DataFetcherMixin, VoiceMergerMixin):
    """即席查询门面。

    该类只负责把 Session、payload、数据获取和语音合并 mixin 组合成兼容的
    旧查询接口。主查询对象拥有登录态和取消标志；并行查询会复制 Cookie
    与连接属性到每个线程的独立 Session，不共享连接池或字段配置缓存。
    """

    def __init__(self, session):
        self.sess = session
        self.enabled = False
        self._field_config_cache = {}
        self._cancel_flag = False  # 取消查询标志


    # ========== 并行查询支持 ==========

    def query_tables_parallel(self, table_configs, max_workers=None, progress_callback=None):
        """并行查询多个报表

        Args:
            table_configs: 表配置列表，每个元素为 (table_name, payload) 元组
            max_workers: 最大并行数（默认使用常量）
            progress_callback: 进度回调函数 callback(completed, total, table_name, status)

        Returns:
            dict: {table_name: DataFrame} 查询结果字典
        """
        if max_workers is None:
            max_workers = MAX_PARALLEL_QUERIES
        import pandas as pd

        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════════════════╗")
        logger.info("║                     并行查询模式                                  ║")
        logger.info("╠══════════════════════════════════════════════════════════════════╣")
        logger.info("║ 报表数量: %d", len(table_configs))
        logger.info("║ 最大并行数: %d", max_workers)
        logger.info("╚══════════════════════════════════════════════════════════════════╝")

        results = {}
        completed = 0
        total = len(table_configs)
        cancelled = [False]

        def make_session():
            """为每个线程创建独立的 Session（获取cookie副本）"""
            import requests as req
            s = req.Session()
            # 复制cookies
            for c in self.sess.cookies:
                s.cookies.set_cookie(c)
            for attr in ('verify', 'trust_env', 'headers', 'auth', 'proxies', 'params', 'cert'):
                if hasattr(self.sess, attr):
                    setattr(s, attr, copy.copy(getattr(self.sess, attr)))
            return s

        def query_single_table(table_name, payload):
            """查询单个表（用于线程池，每个线程使用独立Session）"""
            thread_session = make_session()
            thread_query = JXCXQuery(thread_session)
            # 与主查询共享取消状态，但不共享 Session、缓存或连接池
            thread_query.is_cancelled = self.is_cancelled
            # 确保进入即席查询（线程自己的Session）
            thread_query.enabled = self.enabled
            try:
                report_logger = get_report_logger(table_name)
                report_logger.info("[并行查询] 开始查询: %s", table_name)
                df = thread_query.get_table(payload, to_df=True, report_name=table_name)
                report_logger.info("[并行查询] ✓ 完成查询: %s, 获取 %d 行", table_name, len(df))
                logger.info("[并行查询] ✓ 完成查询: %s, 获取 %d 行", table_name, len(df))
                return table_name, df
            except Exception as e:
                logger.error("[并行查询] ✗ 查询失败: %s, 错误: %s", table_name, str(e)[:100])
                return table_name, pd.DataFrame()

        # 使用线程池并行查询（每个线程独立Session，避免共享连接/会话冲突）
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {
            executor.submit(query_single_table, name, payload): name
            for name, payload in table_configs
        }
        try:
            # 等待完成
            for future in as_completed(futures):
                # 检查是否已取消，是则取消未开始的任务并立即返回
                if self.is_cancelled():
                    logger.info("[并行查询] 收到取消请求，取消尚未开始的任务")
                    for pending in futures:
                        pending.cancel()
                    break
                table_name = futures[future]
                completed += 1
                try:
                    name, df = future.result()
                    results[name] = df
                    logger.info("[并行进度] %d/%d 完成: %s", completed, total, name)

                    # 更新进度
                    if progress_callback:
                        status = "成功" if not df.empty else "空数据"
                        progress_callback(completed, total, name, status)

                except Exception as e:
                    logger.error("[并行查询] ✗ 处理结果失败: %s, 错误: %s", table_name, str(e)[:100])
                    results[table_name] = pd.DataFrame()
                    if progress_callback:
                        progress_callback(completed, total, table_name, "失败")
        finally:
            # 不等待已在运行的 HTTP 请求；仅取消尚未开始的任务
            executor.shutdown(wait=False, cancel_futures=True)

        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════════════════╗")
        logger.info("║                     并行查询完成                                  ║")
        logger.info("╠══════════════════════════════════════════════════════════════════╣")
        for name, df in results.items():
            status = "成功" if not df.empty else "失败/空"
            logger.info("║ %s: %d 行 [%s]", name.ljust(20), len(df), status)
        logger.info("╚══════════════════════════════════════════════════════════════════╝")

        return results