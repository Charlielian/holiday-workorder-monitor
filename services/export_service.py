import os
import pandas as pd
from datetime import datetime
from typing import Optional
from loguru import logger

from services.monitor_service import MonitorService

class ExportService:
    """
    多 Sheet 专业 Excel 报表导出服务
    """
    EXPORT_DIR = "data/exports"

    @classmethod
    def export_latest_hour_excel(cls, specific_hour: Optional[str] = None) -> str:
        """
        导出最新时段的性能监控报表
        """
        os.makedirs(cls.EXPORT_DIR, exist_ok=True)
        overview = MonitorService.get_latest_hour_overview(specific_hour)
        hour_str = overview.get("latest_hour", "latest").replace(":", "").replace(" ", "_")
        file_path = os.path.join(cls.EXPORT_DIR, f"最新时段监控_{hour_str}.xlsx")

        # 准备数据
        records = overview.get("issue_records", [])
        df_records = pd.DataFrame(records)
        if not df_records.empty:
            rename_map = {
                "start_time": "发生时段",
                "network_type": "制式",
                "cell_id": "小区标识(CGI/NCGI)",
                "cell_name": "小区名称",
                "city": "所属地市",
                "issue_type": "问题类型",
                "metric_value_1": "主指标值",
                "metric_value_2": "话务量/连接数",
                "is_repeated_8h": "8小时内是否复现(1是0否)",
                "repeat_count_8h": "8小时累计恶化次数"
            }
            df_records = df_records.rename(columns=rename_map)
            if "id" in df_records.columns:
                df_records = df_records.drop(columns=["id"])

        df_summary = pd.DataFrame(overview.get("category_stats", []))
        if not df_summary.empty:
            df_summary = df_summary.rename(columns={"name": "问题分类", "count": "小区数量"})

        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            if not df_records.empty:
                df_records.to_excel(writer, sheet_name="时段问题小区明细", index=False)
            else:
                pd.DataFrame([{"说明": "该时段无质差超频小区"}]).to_excel(writer, sheet_name="时段问题小区明细", index=False)

            if not df_summary.empty:
                df_summary.to_excel(writer, sheet_name="问题类别汇总", index=False)

        logger.info(f"成功生成最新时段 Excel: {file_path}")
        return file_path

    @classmethod
    def export_weekly_excel(cls, ref_date_str: Optional[str] = None) -> str:
        """
        导出上周五至本周四周考核报表
        """
        os.makedirs(cls.EXPORT_DIR, exist_ok=True)
        overview = MonitorService.get_weekly_overview(ref_date_str)
        cycle_title = overview.get("cycle_title", "weekly_report").replace(":", "_").replace(" ", "")
        file_path = os.path.join(cls.EXPORT_DIR, f"周考核超频整治清单_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

        focus_cells = overview.get("focus_cells", [])
        df_focus = pd.DataFrame(focus_cells)
        if not df_focus.empty:
            rename_map = {
                "cell_id": "小区标识(CGI/NCGI)",
                "network_type": "网络制式",
                "cell_name": "小区名称",
                "city": "所属地市",
                "issue_type": "主要恶化类型",
                "total_occurrences": "考核周期恶化时段总数",
                "active_days": "恶化天数",
                "ever_repeated": "期间是否曾8h复现",
                "first_time": "首次发生时间",
                "last_time": "最近发生时间"
            }
            df_focus = df_focus.rename(columns=rename_map)

        df_city = pd.DataFrame(overview.get("city_distribution", []))
        if not df_city.empty:
            df_city = df_city.rename(columns={"city": "地市", "count": "重点超频网元数"})

        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            if not df_focus.empty:
                df_focus.to_excel(writer, sheet_name="重点整治超频小区", index=False)
            else:
                pd.DataFrame([{"说明": "本周期无重点整治超频小区"}]).to_excel(writer, sheet_name="重点整治超频小区", index=False)

            if not df_city.empty:
                df_city.to_excel(writer, sheet_name="地市分布统计", index=False)

        logger.info(f"成功生成周考核 Excel: {file_path}")
        return file_path
