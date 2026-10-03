# -*- coding: utf-8 -*-
"""
工单数据采集器模块
负责从大数据平台「方案库」（集团工单）与「BPMN优化流程」（省内工单）拉取最新工单并持久化入库
"""

import re
import json
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import text

from db.database import db_manager
from db.models import WorkOrderRecord, WorkOrderCollectLog
from services.auth_service import AuthService
from core.nqi.utils.config import BASE_URL, load_config

# 方案库（集团工单）地市编码映射
CITY_CODE_MAP = {
    "86020": "广州", "860757": "佛山", "860755": "深圳", "860769": "东莞",
    "860754": "汕头", "860756": "珠海", "860752": "惠州", "860760": "中山",
    "860750": "江门", "860759": "湛江", "860668": "茂名", "860663": "揭阳",
    "860751": "韶关", "860762": "河源", "860753": "梅州", "860660": "汕尾",
    "860662": "阳江", "860758": "肇庆", "860763": "清远", "860768": "潮州",
    "860766": "云浮"
}
CODE_TO_CITY = CITY_CODE_MAP
CITY_TO_CODE = {v: k for k, v in CITY_CODE_MAP.items()}

# 省内工单号前缀到地市映射
PROVINCE_PREFIX_MAP = {
    "GZ": "广州", "SZ": "深圳", "FS": "佛山", "DG": "东莞", "ZH": "珠海",
    "ST": "汕头", "HZ": "惠州", "ZS": "中山", "JM": "江门", "ZJ": "湛江",
    "MM": "茂名", "JY": "揭阳", "SG": "韶关", "HY": "河源", "MZ": "梅州",
    "SW": "汕尾", "YJ": "阳江", "ZQ": "肇庆", "QY": "清远", "CZ": "潮州",
    "YF": "云浮"
}

ALL_CITIES = [
    "广州", "深圳", "佛山", "东莞", "珠海", "汕头", "惠州", "中山", "江门",
    "湛江", "茂名", "揭阳", "韶关", "河源", "梅州", "汕尾", "阳江", "肇庆",
    "清远", "潮州", "云浮"
]


class WorkOrderCollector:
    """工单采集器，支持单点登录会话复用、分页循环、错误重试与结果幂等写入"""

    _lock = threading.Lock()

    GROUP_URL = f"{BASE_URL}/pro-ltemr-cicd/modules/disquery/queryProposal"
    PROVINCE_URL = f"{BASE_URL}/pro-wfm-engine-extend-fak/bpmn/runtime/task/work-order/all"

    @classmethod
    def _get_config(cls) -> Dict[str, Any]:
        cfg = load_config()
        # 从 config.yaml 读取 workorder 配置
        from core.nqi.utils.config import get_project_root
        import os
        import yaml
        root = get_project_root()
        config_path = os.path.join(root, "config", "config.yaml")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    y = yaml.safe_load(f) or {}
                    return y.get("workorder", {})
            except Exception:
                pass
        return {}

    @classmethod
    def _get_session(cls):
        """复用现有的 NQI 登录态 Session"""
        client = AuthService.get_client()
        return client.auth_manager.sess

    @classmethod
    def _warmup_subsystem_session(cls, subsystem: str = "group") -> bool:
        """
        利用当前 CAS CASTGC 票据预热/激活子系统 Session。
        方案库 (pro-ltemr-cicd) 和 工作流 (pro-wfm-biz-client-fak) 分布在不同的 context path，
        首次调用数据接口前需先走一次 CAS 302 换取该系统的 JSESSIONID 与会话 Cookie。
        """
        sess = cls._get_session()
        try:
            if subsystem == "group":
                portal_url = f"{BASE_URL}/pro-ltemr-cicd/portal"
                resp = sess.get(portal_url, timeout=15, allow_redirects=True)
                # 进一步访问方案库统一查询入口初始化
                gis_url = f"{BASE_URL}/pro-ltemr-cicd/modules/ltescheme/unify/disquery/showgis.jsp?firstQuery=1"
                sess.get(gis_url, timeout=15, allow_redirects=True)
                logger.info(f"[WorkOrder] 方案库子系统 Session 预热完成, HTTP {resp.status_code}")
                return True
            elif subsystem == "province":
                wfm_url = f"{BASE_URL}/pro-wfm-biz-client-fak/homePage/all"
                resp = sess.get(wfm_url, timeout=15, allow_redirects=True)
                logger.info(f"[WorkOrder] BPMN 工作流子系统 Session 预热完成, HTTP {resp.status_code}")
                return True
        except Exception as e:
            logger.warning(f"[WorkOrder] 子系统 [{subsystem}] Session 预热重定向异常: {e}")
        return False

    @classmethod
    def _get_username(cls) -> str:
        client = AuthService.get_client()
        return client.auth_manager.username or "dwlianchangli"

    @classmethod
    def strip_html_tags(cls, text: str) -> str:
        """剥除流水号等字段中的 HTML 标签"""
        if not text:
            return ""
        return re.sub(r"<[^>]+>", "", str(text)).strip()

    @classmethod
    def parse_province_city(cls, code: str, title: str) -> str:
        """从省内工单号前缀或标题中识别广东地市"""
        if code:
            parts = str(code).split("_")
            if parts and parts[0].upper() in PROVINCE_PREFIX_MAP:
                return PROVINCE_PREFIX_MAP[parts[0].upper()]
        if title:
            t = str(title)
            for c in ALL_CITIES:
                if c in t:
                    return c
        return "未知"

    @classmethod
    def collect_group_orders(cls, start_date: str, end_date: str,
                             city_name: Optional[str] = None) -> Dict[str, Any]:
        """
        采集集团工单（方案库）
        :param start_date: 'YYYY-MM-DD'
        :param end_date: 'YYYY-MM-DD'
        :param city_name: 中文地市名称 (None 或空表示全省)
        """
        t0 = time.time()
        sess = cls._get_session()
        wo_cfg = cls._get_config()
        labels = wo_cfg.get("group_labels", ["集团假日保障"])
        finished_statuses = set(wo_cfg.get("finished_status_group", ["已办结", "已归档", "已完成", "办结"]))
        page_size = wo_cfg.get("page_size", 100)

        city_code = CITY_TO_CODE.get(city_name, "") if city_name else ""

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/pro-ltemr-cicd/modules/ltescheme/unify/disquery/showgis.jsp?firstQuery=1"
        }

        total_pages = 1
        current_page = 1
        rows_fetched = 0
        rows_inserted = 0
        rows_updated = 0
        error_msg = None

        logger.info(f"[WorkOrder] 开始采集集团工单: {start_date} ~ {end_date}, 地市: {city_name or '全省'}")
        cls._warmup_subsystem_session("group")

        with db_manager.get_session() as db_sess:
            while current_page <= total_pages:
                form_data = {
                    "firstQuery": "",
                    "timeType": "问题生成时间",
                    "start_date": start_date,
                    "end_date": end_date,
                    "city": city_code,
                    "area_grid": "",
                    "order_code": "",
                    "problemSource": "",
                    "question_type": "",
                    "problem_status": "",
                    "cover_scene": "",
                    "special_label": "",
                    "value_label": "",
                    "vcfirst_submitter": "",
                    "vcdetail_submitter": "",
                    "vcevaluator": "",
                    "vcdetail_cause": "",
                    "vcdetail_measures": "",
                    "handover": "",
                    "intevaluate_type": "",
                    "search_uuid": "",
                    "alllikequery": "",
                    "vcimport": "",
                    "vcdatatype": "",
                    "intproposal_company": "",
                    "isquery": "ture",
                    "ordercheck": "",
                    "ischeck": "",
                    "isDuplicateRemoval": "false",
                    "intisprovince": "",
                    "vccellviplevel": "",
                    "query_type": "null",
                    "query_detail_type": "",
                    "intisupscale": "",
                    "vcupscale_code": "",
                    "vcbilling_plbtype": "",
                    "vcnetwork_type": "",
                    "intorderanaly_record": "",
                    "vcdataroot": "",
                    "iscs": "",
                    "intis_warranty": "",
                    "vcorder_type": "",
                    "isspecial": "false",
                    "rows": str(page_size),
                    "pagination[pageSize]": str(page_size),
                    "pagination[currentPage]": str(current_page)
                }

                # 增加 multi-select 数组参数 detailed_type[]
                post_data = list(form_data.items())
                for lb in labels:
                    post_data.append(("detailed_type[]", lb))

                try:
                    resp = sess.post(cls.GROUP_URL, data=post_data, headers=headers, timeout=25)
                    if resp.status_code != 200:
                        error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        break
                    res_json = resp.json()
                    rows = res_json.get("rows", [])
                    pagination = res_json.get("pagination", {})
                    total_pages = pagination.get("totalPage", 1) or 1
                    rows_fetched += len(rows)

                    for r in rows:
                        code = cls.strip_html_tags(r.get("order_code") or r.get("工单流水号") or "")
                        if not code:
                            continue
                        city = r.get("地市") or city_name or "未知"
                        cluster_code = r.get("聚类工单序号") or ""
                        cell_id = r.get("问题小区") or ""
                        cell_name = r.get("问题小区名") or ""
                        status = r.get("当前状态") or ""
                        special_label = r.get("专项标签") or ""
                        create_time = r.get("派发时间") or r.get("市组长派发时间") or f"{start_date} 00:00:00"
                        finish_time = r.get("方案确认时间") or ""

                        # 办结判定
                        is_finished = 1 if status in finished_statuses else 0

                        # upsert
                        exist = db_sess.query(WorkOrderRecord).filter_by(order_type="group", order_code=code).first()
                        if exist:
                            exist.city = city
                            exist.cluster_code = cluster_code
                            exist.cell_id = cell_id
                            exist.cell_name = cell_name
                            exist.status = status
                            exist.is_finished = is_finished
                            exist.special_label = special_label
                            exist.finish_time = finish_time
                            # 自动拉取数据证明已办结: 清除人工标记, 表示已由系统数据证实
                            if is_finished == 1:
                                exist.is_manual_finished = 0
                            exist.extra_json = json.dumps(r, ensure_ascii=False)
                            rows_updated += 1
                        else:
                            new_rec = WorkOrderRecord(
                                order_type="group",
                                order_code=code,
                                cluster_code=cluster_code,
                                city=city,
                                title=cell_name or cluster_code,
                                cell_id=cell_id,
                                cell_name=cell_name,
                                status=status,
                                is_finished=is_finished,
                                current_node=status,
                                special_label=special_label,
                                create_time=create_time,
                                finish_time=finish_time,
                                extra_json=json.dumps(r, ensure_ascii=False)
                            )
                            db_sess.add(new_rec)
                            rows_inserted += 1

                    current_page += 1
                except Exception as e:
                    logger.error(f"[WorkOrder] 拉取集团工单第 {current_page} 页异常: {e}")
                    error_msg = str(e)
                    break

            # 显式提交事务，确保所有新增和更新的工单落库
            db_sess.commit()

            # 记录日志
            log = WorkOrderCollectLog(
                scope="group",
                start_date=start_date,
                end_date=end_date,
                city=city_name or "全省",
                pages_fetched=current_page - 1,
                rows_fetched=rows_fetched,
                rows_inserted=rows_inserted,
                rows_updated=rows_updated,
                status="FAILED" if error_msg and rows_fetched == 0 else ("PARTIAL" if error_msg else "SUCCESS"),
                error_message=error_msg,
                duration_sec=round(time.time() - t0, 2)
            )
            db_sess.add(log)
            db_sess.commit()

        logger.info(f"[WorkOrder] 集团工单采集完成: 拉取 {rows_fetched}, 新增 {rows_inserted}, 更新 {rows_updated}, 耗时 {round(time.time() - t0, 2)}s")
        return {
            "scope": "group", "rows_fetched": rows_fetched,
            "rows_inserted": rows_inserted, "rows_updated": rows_updated,
            "error": error_msg
        }

    @classmethod
    def collect_province_orders(cls, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        采集省内工单（BPMN 优化流程）
        :param start_date: 'YYYY-MM-DD'
        :param end_date: 'YYYY-MM-DD'
        """
        t0 = time.time()
        sess = cls._get_session()
        wo_cfg = cls._get_config()
        keys = wo_cfg.get("province_keys", ["proc_gtssxn", "proc_jzfx_ssyhlc"])
        page_size = wo_cfg.get("page_size", 100)
        username = cls._get_username()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/pro-wfm-biz-client-fak/homePage/all",
            "Connection": "close"
        }

        # 补全格式 YYYY-MM-DD HH:MM:SS
        dt_start = f"{start_date} 00:00:00" if len(start_date) == 10 else start_date
        dt_end = f"{end_date} 23:59:59" if len(end_date) == 10 else end_date

        current_page = 1
        total_objects = 1
        rows_fetched = 0
        rows_inserted = 0
        rows_updated = 0
        error_msg = None

        logger.info(f"[WorkOrder] 开始采集省内工单: {dt_start} ~ {dt_end}, Keys: {keys}")
        cls._warmup_subsystem_session("province")

        with db_manager.get_session() as db_sess:
            while (current_page - 1) * page_size < total_objects:
                payload = {
                    "userId": username,
                    "key": keys,
                    "createTimeStart": dt_start,
                    "createTimeEnd": dt_end,
                    "pageIndex": current_page,
                    "pageSize": page_size
                }

                try:
                    resp = sess.post(cls.PROVINCE_URL, json=payload, headers=headers, timeout=25)
                    if resp.status_code != 200:
                        error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        break
                    res_json = resp.json()
                    rows = res_json.get("data", [])
                    total_objects = res_json.get("totalObjects", 0) or 0
                    rows_fetched += len(rows)

                    if not rows:
                        break

                    for r in rows:
                        code = str(r.get("code") or r.get("id") or "").strip()
                        if not code:
                            continue
                        title = str(r.get("title") or "").strip()
                        city = cls.parse_province_city(code, title)
                        state = r.get("state") or ""
                        wo_status = r.get("woStatusName") or ""
                        node_name = r.get("currentNodeName") or ""
                        proc_key = r.get("processDefinitionKey") or ""
                        proc_name = r.get("processDefinitionName") or ""
                        create_time = r.get("createTime") or dt_start
                        finish_time = r.get("endTime") or ""

                        # 办结判定
                        is_finished = 1 if (wo_status == "已办结" or state in ("COMPLETED", "EXTERNALLY_TERMINATED")) else 0

                        exist = db_sess.query(WorkOrderRecord).filter_by(order_type="province", order_code=code).first()
                        if exist:
                            exist.city = city
                            exist.title = title
                            exist.status = wo_status or state
                            exist.is_finished = is_finished
                            exist.current_node = node_name
                            exist.finish_time = finish_time
                            # 自动拉取数据证明已办结: 清除人工标记, 表示已由系统数据证实
                            if is_finished == 1:
                                exist.is_manual_finished = 0
                            exist.extra_json = json.dumps(r, ensure_ascii=False)
                            rows_updated += 1
                        else:
                            new_rec = WorkOrderRecord(
                                order_type="province",
                                order_code=code,
                                city=city,
                                title=title,
                                status=wo_status or state,
                                is_finished=is_finished,
                                current_node=node_name,
                                process_key=proc_key,
                                process_name=proc_name,
                                create_time=create_time,
                                finish_time=finish_time,
                                extra_json=json.dumps(r, ensure_ascii=False)
                            )
                            db_sess.add(new_rec)
                            rows_inserted += 1

                    current_page += 1
                except Exception as e:
                    logger.error(f"[WorkOrder] 拉取省内工单第 {current_page} 页异常: {e}")
                    error_msg = str(e)
                    break

            # 显式提交事务，确保所有新增和更新的工单落库
            db_sess.commit()

            log = WorkOrderCollectLog(
                scope="province",
                start_date=start_date,
                end_date=end_date,
                city="全省",
                pages_fetched=current_page - 1,
                rows_fetched=rows_fetched,
                rows_inserted=rows_inserted,
                rows_updated=rows_updated,
                status="FAILED" if error_msg and rows_fetched == 0 else ("PARTIAL" if error_msg else "SUCCESS"),
                error_message=error_msg,
                duration_sec=round(time.time() - t0, 2)
            )
            db_sess.add(log)
            db_sess.commit()

        logger.info(f"[WorkOrder] 省内工单采集完成: 拉取 {rows_fetched}, 新增 {rows_inserted}, 更新 {rows_updated}, 耗时 {round(time.time() - t0, 2)}s")
        return {
            "scope": "province", "rows_fetched": rows_fetched,
            "rows_inserted": rows_inserted, "rows_updated": rows_updated,
            "error": error_msg
        }

    @classmethod
    def collect_all(cls, start_date: str, end_date: str, city: Optional[str] = None) -> Dict[str, Any]:
        """同时拉取集团与省内工单"""
        with cls._lock:
            g_res = cls.collect_group_orders(start_date, end_date, city)
            p_res = cls.collect_province_orders(start_date, end_date)
            return {
                "group": g_res,
                "province": p_res,
                "total_inserted": g_res["rows_inserted"] + p_res["rows_inserted"],
                "total_updated": g_res["rows_updated"] + p_res["rows_updated"]
            }
