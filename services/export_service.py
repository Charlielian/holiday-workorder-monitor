import os
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
from sqlalchemy import func

from db.database import db_manager


class ExportService:
    """
    工单监控 Excel 报表导出服务
    """
    EXPORT_DIR = "data/exports"

    @classmethod
    def export_workorder_excel(cls, order_type: str = "all",
                               city: Optional[str] = None,
                               status: Optional[str] = None,
                               keyword: Optional[str] = None,
                               ids_flag: Optional[str] = None,
                               start_date: Optional[str] = None,
                               end_date: Optional[str] = None) -> str:
        """
        导出工单监控全量或明细数据为多 Sheet Excel 报表
        - Sheet 1: 工单汇总统计（各地市工单分布与办结率，IDS 工单已排除）
        - Sheet 2: 集团工单明细
        - Sheet 3: 省内工单明细（含"是否IDS工单"标记列）

        ids_flag: None/'all' 全部, 'ids' 仅 IDS 工单, 'non_ids' 仅非 IDS 工单
        """
        import json
        from db.models import WorkOrderRecord
        from services.workorder_service import WorkOrderService

        os.makedirs(cls.EXPORT_DIR, exist_ok=True)
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")

        city_tag = city if (city and city != "全部") else "全省"
        time_tag = f"{start_date}至{end_date}"
        type_tag = {"all": "全量工单", "group": "集团工单", "province": "省内工单"}.get(order_type, "工单")
        fname = f"工单监控_{type_tag}_{city_tag}_{time_tag}.xlsx"
        file_path = os.path.join(cls.EXPORT_DIR, fname)

        # 1. 查询汇总数据
        summary_data = WorkOrderService.get_summary(city=city, start_date=start_date, end_date=end_date)
        city_stats = summary_data.get("city_stats", [])
        df_summary = pd.DataFrame(city_stats)
        if not df_summary.empty:
            summary_rename = {
                "city": "地市",
                "group_total": "集团工单总数",
                "group_processed": "集团已处理",
                "group_unprocessed": "集团未处理",
                "group_rate": "集团处理率(%)",
                "province_total": "省内工单总数",
                "province_finished": "省内已办结",
                "province_unfinished": "省内未办结",
                "province_rate": "省内办结率(%)",
                "ids_excluded": "已排除IDS工单数",
                "total": "合计工单数",
                "finished": "合计完成数",
                "finish_rate": "综合完成率(%)"
            }
            df_summary = df_summary.rename(columns=summary_rename)

        selected_cities = []
        if city and city != "全部":
            selected_cities = [c.strip() for c in city.split(",") if c.strip() and c.strip() != "全部"]

        with db_manager.get_session() as session:
            # 2. 查询集团工单 (若 order_type 是 all 或 group)
            df_group = pd.DataFrame()
            if order_type in ("all", "group"):
                q_g = session.query(WorkOrderRecord).filter(WorkOrderRecord.order_type == "group")
                if selected_cities:
                    q_g = q_g.filter(WorkOrderRecord.city.in_(selected_cities))
                if start_date:
                    q_g = q_g.filter(WorkOrderRecord.create_time >= f"{start_date} 00:00:00")
                if end_date:
                    q_g = q_g.filter(WorkOrderRecord.create_time <= f"{end_date} 23:59:59")
                if status and status != "全部":
                    if status in ("已办结", "已处理"):
                        q_g = q_g.filter(WorkOrderRecord.is_finished == 1)
                    elif status in ("未办结", "未处理"):
                        q_g = q_g.filter(WorkOrderRecord.is_finished == 0)
                    else:
                        q_g = q_g.filter(WorkOrderRecord.status == status)
                if keyword:
                    kw = f"%{keyword.strip()}%"
                    q_g = q_g.filter(
                        (WorkOrderRecord.order_code.like(kw)) |
                        (WorkOrderRecord.title.like(kw)) |
                        (WorkOrderRecord.cell_name.like(kw)) |
                        (WorkOrderRecord.cluster_code.like(kw))
                    )

                g_rows = q_g.order_by(WorkOrderRecord.create_time.desc()).limit(20000).all()
                group_list = []
                for r in g_rows:
                    item = {
                        "工单编号": r.order_code,
                        "聚类工单序号": r.cluster_code or "",
                        "地市": r.city or "",
                        "问题小区名": r.cell_name or r.title or "",
                        "问题小区CGI": r.cell_id or "",
                        "当前状态": r.status or "",
                        "处理状态": "已处理" if r.is_finished == 1 else "未处理",
                        "是否人工标记": "是(手动)" if (r.is_manual_finished or 0) == 1 else "否",
                        "专项标签": r.special_label or "",
                        "派发时间": r.create_time or "",
                        "方案确认时间": r.finish_time or "",
                    }
                    if r.extra_json:
                        try:
                            extra = json.loads(r.extra_json)
                            item["覆盖场景"] = extra.get("覆盖场景") or ""
                            item["网络制式"] = extra.get("网络制式") or ""
                            item["责任网格"] = extra.get("责任网格") or ""
                            item["问题原因"] = extra.get("问题原因") or ""
                            item["方案类型"] = extra.get("方案类型") or ""
                            item["紧急程度"] = extra.get("紧急程度") or ""
                            item["数据来源"] = extra.get("数据来源") or ""
                            item["评估状态"] = extra.get("评估状态") or ""
                            item["评估情况"] = extra.get("评估情况") or ""
                        except Exception:
                            pass
                    group_list.append(item)
                df_group = pd.DataFrame(group_list)

            # 3. 查询省内工单 (若 order_type 是 all 或 province)
            df_province = pd.DataFrame()
            if order_type in ("all", "province"):
                q_p = session.query(WorkOrderRecord).filter(WorkOrderRecord.order_type == "province")
                if selected_cities:
                    q_p = q_p.filter(WorkOrderRecord.city.in_(selected_cities))
                if start_date:
                    q_p = q_p.filter(WorkOrderRecord.create_time >= f"{start_date} 00:00:00")
                if end_date:
                    q_p = q_p.filter(WorkOrderRecord.create_time <= f"{end_date} 23:59:59")
                if status and status != "全部":
                    if status == "已办结":
                        q_p = q_p.filter(WorkOrderRecord.is_finished == 1)
                    elif status == "未办结":
                        q_p = q_p.filter(WorkOrderRecord.is_finished == 0)
                    else:
                        q_p = q_p.filter(WorkOrderRecord.status == status)
                if ids_flag == "ids":
                    q_p = q_p.filter(func.coalesce(WorkOrderRecord.is_ids, 0) == 1)
                elif ids_flag == "non_ids":
                    q_p = q_p.filter(func.coalesce(WorkOrderRecord.is_ids, 0) != 1)
                if keyword:
                    kw = f"%{keyword.strip()}%"
                    q_p = q_p.filter(
                        (WorkOrderRecord.order_code.like(kw)) |
                        (WorkOrderRecord.title.like(kw))
                    )

                p_rows = q_p.order_by(WorkOrderRecord.create_time.desc()).limit(20000).all()
                province_list = []
                for r in p_rows:
                    item = {
                        "工单编号": r.order_code,
                        "地市": r.city or "",
                        "标题/小区": r.title or "",
                        "流程名称": r.process_name or "",
                        "当前节点": r.current_node or "",
                        "工单状态": r.status or "",
                        "办结状态": "已办结" if r.is_finished == 1 else "未办结",
                        "是否人工标记": "是(手动)" if (r.is_manual_finished or 0) == 1 else "否",
                        "是否IDS工单": "是" if (r.is_ids or 0) == 1 else "否",
                        "创建时间": r.create_time or "",
                        "办结时间": r.finish_time or "",
                    }
                    if r.extra_json:
                        try:
                            extra = json.loads(r.extra_json)
                            item["发起人/系统"] = extra.get("createName") or extra.get("creator") or ""
                            item["流程实例ID"] = extra.get("processInstanceId") or ""
                            item["业务分类"] = extra.get("category") or ""
                        except Exception:
                            pass
                    province_list.append(item)
                df_province = pd.DataFrame(province_list)

        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            # Sheet 1: 汇总
            if not df_summary.empty:
                df_summary.to_excel(writer, sheet_name="工单汇总统计", index=False)
            else:
                pd.DataFrame([{"说明": "该区间无汇总统计数据"}]).to_excel(writer, sheet_name="工单汇总统计", index=False)

            # Sheet 2: 集团工单
            if order_type in ("all", "group"):
                if not df_group.empty:
                    df_group.to_excel(writer, sheet_name="集团工单", index=False)
                else:
                    pd.DataFrame([{"说明": "无匹配的集团工单数据"}]).to_excel(writer, sheet_name="集团工单", index=False)

            # Sheet 3: 省内工单
            if order_type in ("all", "province"):
                if not df_province.empty:
                    df_province.to_excel(writer, sheet_name="省内工单", index=False)
                else:
                    pd.DataFrame([{"说明": "无匹配的省内工单数据"}]).to_excel(writer, sheet_name="省内工单", index=False)

        logger.info(f"成功导出工单报表 Excel: {file_path}")
        return file_path
