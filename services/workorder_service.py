# -*- coding: utf-8 -*-
"""
工单数据业务服务模块
提供工单汇总统计、分页多维检索、地市分布计算与缓存管理
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import text, func

from db.database import db_manager
from db.models import WorkOrderRecord, WorkOrderCollectLog
from services.workorder_collector import ALL_CITIES, CITY_TO_CODE


class WorkOrderService:
    """工单监控业务服务"""

    @classmethod
    def list_cities(cls) -> List[str]:
        """返回所有支持的标准地市"""
        return ALL_CITIES

    @classmethod
    def get_summary(cls, city: Optional[str] = None,
                    cities: Optional[List[str]] = None,
                    start_date: Optional[str] = None,
                    end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        获取工单汇总看板数据：
        - 支持按单个地市或多个地市列表筛选 (cities 参数优先)
        - 集团工单总数、已处理、未处理、处理率
        - 省内工单总数、已办结、未办结、办结率
        - 合计总数、综合完成率
        - 所选地市分布明细（地市、集团数、省内数、合计、各率）
        - 最近更新的工单流转动态 (Top 15)
        """
        # 默认区间为近 7 天
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")

        dt_start = f"{start_date} 00:00:00"
        dt_end = f"{end_date} 23:59:59"

        # 解析多地市过滤
        selected_cities: List[str] = []
        if cities:
            selected_cities = [c.strip() for c in cities if c and c.strip() and c.strip() != "全部"]
        elif city and city != "全部":
            if "," in city:
                selected_cities = [c.strip() for c in city.split(",") if c.strip() and c.strip() != "全部"]
            else:
                selected_cities = [city.strip()]

        with db_manager.get_session() as session:
            # IDS 工单由人工标记, 一律不计入统计口径 (含汇总卡片、地市分布、最新动态)
            not_ids = func.coalesce(WorkOrderRecord.is_ids, 0) != 1

            # 基础过滤条件
            filters = [
                WorkOrderRecord.create_time >= dt_start,
                WorkOrderRecord.create_time <= dt_end,
                not_ids
            ]
            if selected_cities:
                filters.append(WorkOrderRecord.city.in_(selected_cities))

            # 1. 汇总卡片统计 (按 order_type, is_finished 分组)
            q_cards = (
                session.query(
                    WorkOrderRecord.order_type,
                    WorkOrderRecord.is_finished,
                    func.count(WorkOrderRecord.id).label("cnt")
                )
                .filter(*filters)
                .group_by(WorkOrderRecord.order_type, WorkOrderRecord.is_finished)
                .all()
            )

            group_finished = 0
            group_unfinished = 0
            province_finished = 0
            province_unfinished = 0

            for o_type, is_fin, cnt in q_cards:
                if o_type == "group":
                    if is_fin == 1:
                        group_finished += cnt
                    else:
                        group_unfinished += cnt
                elif o_type == "province":
                    if is_fin == 1:
                        province_finished += cnt
                    else:
                        province_unfinished += cnt

            # 1.1 已排除的 IDS 工单数 (人工标记为 IDS 的省内工单, 只提示不计数)
            ids_excluded_q = session.query(func.count(WorkOrderRecord.id)).filter(
                WorkOrderRecord.order_type == "province",
                WorkOrderRecord.create_time >= dt_start,
                WorkOrderRecord.create_time <= dt_end,
                func.coalesce(WorkOrderRecord.is_ids, 0) == 1
            )
            if selected_cities:
                ids_excluded_q = ids_excluded_q.filter(WorkOrderRecord.city.in_(selected_cities))
            ids_excluded_total = ids_excluded_q.scalar() or 0

            group_total = group_finished + group_unfinished
            province_total = province_finished + province_unfinished
            total_orders = group_total + province_total
            total_finished = group_finished + province_finished
            finish_rate = round((total_finished / total_orders * 100), 1) if total_orders > 0 else 0.0

            # 2. 地市分布统计 (如果选了地市，只展示所选地市；否则展示全省21地市)
            city_query_filters = [
                WorkOrderRecord.create_time >= dt_start,
                WorkOrderRecord.create_time <= dt_end,
                not_ids
            ]
            if selected_cities:
                city_query_filters.append(WorkOrderRecord.city.in_(selected_cities))

            q_city = (
                session.query(
                    WorkOrderRecord.city,
                    WorkOrderRecord.order_type,
                    WorkOrderRecord.is_finished,
                    func.count(WorkOrderRecord.id).label("cnt")
                )
                .filter(*city_query_filters)
                .group_by(WorkOrderRecord.city, WorkOrderRecord.order_type, WorkOrderRecord.is_finished)
                .all()
            )

            # 各地市被排除的 IDS 省内工单数 (供导出报表追溯统计口径)
            ids_city_q = session.query(
                WorkOrderRecord.city,
                func.count(WorkOrderRecord.id).label("cnt")
            ).filter(
                WorkOrderRecord.order_type == "province",
                WorkOrderRecord.create_time >= dt_start,
                WorkOrderRecord.create_time <= dt_end,
                func.coalesce(WorkOrderRecord.is_ids, 0) == 1
            )
            if selected_cities:
                ids_city_q = ids_city_q.filter(WorkOrderRecord.city.in_(selected_cities))
            ids_city_map = {(c or "未知"): n for c, n in ids_city_q.group_by(WorkOrderRecord.city).all()}

            city_map = {}
            target_cities = selected_cities if selected_cities else ALL_CITIES
            for c in target_cities:
                city_map[c] = {
                    "city": c,
                    "group_total": 0,
                    "group_processed": 0,
                    "group_unprocessed": 0,
                    "group_rate": 0.0,
                    "province_total": 0,
                    "province_finished": 0,
                    "province_unfinished": 0,
                    "province_rate": 0.0,
                    "ids_excluded": 0,
                    "total": 0,
                    "finished": 0,
                    "finish_rate": 0.0
                }

            for c_name, o_type, is_fin, cnt in q_city:
                c_clean = c_name or "未知"
                if c_clean not in city_map:
                    city_map[c_clean] = {
                        "city": c_clean,
                        "group_total": 0, "group_processed": 0, "group_unprocessed": 0, "group_rate": 0.0,
                        "province_total": 0, "province_finished": 0, "province_unfinished": 0, "province_rate": 0.0,
                        "ids_excluded": 0, "total": 0, "finished": 0, "finish_rate": 0.0
                    }
                item = city_map[c_clean]
                if o_type == "group":
                    item["group_total"] += cnt
                    if is_fin == 1:
                        item["group_processed"] += cnt
                    else:
                        item["group_unprocessed"] += cnt
                else:
                    item["province_total"] += cnt
                    if is_fin == 1:
                        item["province_finished"] += cnt
                    else:
                        item["province_unfinished"] += cnt

                item["total"] += cnt
                if is_fin == 1:
                    item["finished"] += cnt

            # 计算各市的集团处理率、省内办结率及综合完成率，按工单总数降序排列
            city_stats = []
            for item in city_map.values():
                tot = item["total"]
                fin = item["finished"]
                g_tot = item["group_total"]
                g_proc = item["group_processed"]
                p_tot = item["province_total"]
                p_fin = item["province_finished"]

                item["group_rate"] = round((g_proc / g_tot * 100), 1) if g_tot > 0 else 0.0
                item["province_rate"] = round((p_fin / p_tot * 100), 1) if p_tot > 0 else 0.0
                item["finish_rate"] = round((fin / tot * 100), 1) if tot > 0 else 0.0
                item["ids_excluded"] = ids_city_map.get(item["city"], 0)
                city_stats.append(item)

            city_stats.sort(key=lambda x: (x["total"], x["group_total"]), reverse=True)

            # 3. 最新工单动态 (最近 15 条)
            recent_rows = (
                session.query(WorkOrderRecord)
                .filter(*filters)
                .order_by(WorkOrderRecord.create_time.desc(), WorkOrderRecord.id.desc())
                .limit(15)
                .all()
            )

            recent_list = []
            for r in recent_rows:
                recent_list.append({
                    "id": r.id,
                    "order_type": r.order_type,
                    "order_code": r.order_code,
                    "city": r.city,
                    "title": r.title or r.cell_name or r.order_code,
                    "status": r.status,
                    "is_finished": r.is_finished,
                    "current_node": r.current_node,
                    "create_time": r.create_time
                })

            # 4. 获取最后采集时间
            last_log = (
                session.query(WorkOrderCollectLog)
                .order_by(WorkOrderCollectLog.id.desc())
                .first()
            )
            last_collect_time = last_log.created_at.strftime("%Y-%m-%d %H:%M:%S") if last_log else "暂无记录"

            result = {
                "success": True,
                "filter": {
                    "city": ",".join(selected_cities) if selected_cities else "全部",
                    "cities": selected_cities,
                    "start_date": start_date,
                    "end_date": end_date
                },
                "cards": {
                    "group": {
                        "total": group_total,
                        "finished": group_finished,
                        "unfinished": group_unfinished,
                        "finish_rate": round(group_finished / group_total * 100, 1) if group_total > 0 else 0.0
                    },
                    "province": {
                        "total": province_total,
                        "finished": province_finished,
                        "unfinished": province_unfinished,
                        "finish_rate": round(province_finished / province_total * 100, 1) if province_total > 0 else 0.0,
                        "ids_excluded": ids_excluded_total
                    },
                    "total": {
                        "total": total_orders,
                        "finished": total_finished,
                        "unfinished": total_orders - total_finished,
                        "finish_rate": finish_rate
                    }
                },
                "city_stats": city_stats,
                "recent": recent_list,
                "last_collect_time": last_collect_time
            }

            return result

    @classmethod
    def query_orders(cls, order_type: str,
                     city: Optional[str] = None,
                     status: Optional[str] = None,
                     keyword: Optional[str] = None,
                     ids_flag: Optional[str] = None,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None,
                     page: int = 1,
                     page_size: int = 20) -> Dict[str, Any]:
        """
        分页查询指定类型（集团/省内）的工单明细

        ids_flag: None/'all' 全部, 'ids' 仅 IDS 工单, 'non_ids' 仅非 IDS 工单
                  (IDS 工单由人工标记, 标记后不参与汇总统计)
        """
        page = max(1, int(page))
        page_size = min(max(10, int(page_size)), 500)

        with db_manager.get_session() as session:
            q = session.query(WorkOrderRecord).filter(WorkOrderRecord.order_type == order_type)

            if city and city != "全部":
                q = q.filter(WorkOrderRecord.city == city)

            if ids_flag == "ids":
                q = q.filter(func.coalesce(WorkOrderRecord.is_ids, 0) == 1)
            elif ids_flag == "non_ids":
                q = q.filter(func.coalesce(WorkOrderRecord.is_ids, 0) != 1)

            if start_date:
                q = q.filter(WorkOrderRecord.create_time >= f"{start_date} 00:00:00")
            if end_date:
                q = q.filter(WorkOrderRecord.create_time <= f"{end_date} 23:59:59")

            if status and status != "全部":
                if status in ("已办结", "已处理"):
                    q = q.filter(WorkOrderRecord.is_finished == 1)
                elif status in ("未办结", "未处理"):
                    q = q.filter(WorkOrderRecord.is_finished == 0)
                else:
                    q = q.filter(WorkOrderRecord.status == status)

            if keyword:
                kw = f"%{keyword.strip()}%"
                q = q.filter(
                    (WorkOrderRecord.order_code.like(kw)) |
                    (WorkOrderRecord.title.like(kw)) |
                    (WorkOrderRecord.cell_name.like(kw)) |
                    (WorkOrderRecord.cluster_code.like(kw))
                )

            total = q.count()
            rows = (
                q.order_by(WorkOrderRecord.create_time.desc(), WorkOrderRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )

            records = []
            for r in rows:
                rec = {
                    "id": r.id,
                    "order_code": r.order_code,
                    "cluster_code": r.cluster_code,
                    "city": r.city,
                    "title": r.title,
                    "cell_id": r.cell_id,
                    "cell_name": r.cell_name,
                    "status": r.status,
                    "is_finished": r.is_finished,
                    "is_ids": r.is_ids or 0,
                    "is_manual_finished": r.is_manual_finished or 0,
                    "current_node": r.current_node,
                    "process_name": r.process_name,
                    "special_label": r.special_label,
                    "create_time": r.create_time,
                    "finish_time": r.finish_time,
                }
                # 解析 extra_json 附带详细列
                if r.extra_json:
                    try:
                        extra = json.loads(r.extra_json)
                        rec["cover_scene"] = extra.get("覆盖场景") or ""
                        rec["network_type"] = extra.get("网络制式") or ""
                        rec["grid"] = extra.get("责任网格") or ""
                        rec["eval_status"] = extra.get("评估状态") or ""
                        rec["data_source"] = extra.get("数据来源") or ""
                    except Exception:
                        pass
                records.append(rec)

            return {
                "success": True,
                "total": total,
                "page": page,
                "page_size": page_size,
                "records": records
            }

    @classmethod
    def update_ids_flag(cls, order_id: int, is_ids: bool) -> Dict[str, Any]:
        """
        人工标记/取消标记工单为 IDS 工单

        标记为 IDS 的工单不参与汇总看板的统计计算 (总数、办结率、地市分布)，
        但仍保留在明细列表中供查看。采集/增量同步只更新业务字段，
        不会覆盖该人工标记。
        """
        with db_manager.get_session() as session:
            rec = session.query(WorkOrderRecord).filter(WorkOrderRecord.id == order_id).first()
            if not rec:
                return {"success": False, "message": "工单不存在"}

            rec.is_ids = 1 if is_ids else 0
            rec.updated_at = datetime.now()
            session.commit()

            return {
                "success": True,
                "id": rec.id,
                "order_code": rec.order_code,
                "is_ids": rec.is_ids
            }

    @classmethod
    def mark_manual_finished(cls, order_id: int, is_finished: bool) -> Dict[str, Any]:
        """
        人工标记工单为已处理/取消人工标记

        - 标记为已处理: 设置 is_finished=1, is_manual_finished=1
        - 取消人工标记: 恢复 is_finished=0, is_manual_finished=0

        当后续自动拉取数据证明工单已办结时, 采集器会自动将 is_manual_finished 重置为 0,
        表示该工单的"已处理"状态已由系统数据证实, 不再需要人工标记。
        """
        with db_manager.get_session() as session:
            rec = session.query(WorkOrderRecord).filter(WorkOrderRecord.id == order_id).first()
            if not rec:
                return {"success": False, "message": "工单不存在"}

            if is_finished:
                rec.is_finished = 1
                rec.is_manual_finished = 1
                # 若尚无办结时间, 用当前时间填充, 便于导出与展示
                if not rec.finish_time:
                    rec.finish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                # 取消人工标记: 仅当当前是人工标记状态时才回退, 避免误改系统已证实的已办结
                if (rec.is_manual_finished or 0) == 1:
                    rec.is_finished = 0
                    rec.is_manual_finished = 0
                    rec.finish_time = None

            rec.updated_at = datetime.now()
            session.commit()

            return {
                "success": True,
                "id": rec.id,
                "order_code": rec.order_code,
                "is_finished": rec.is_finished,
                "is_manual_finished": rec.is_manual_finished
            }
