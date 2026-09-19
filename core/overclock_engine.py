from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from sqlalchemy import text
from loguru import logger

from db.database import db_manager
from db.models import CellIssueRecord, CellOverclockSummary

class OverclockEngine:
    """
    8小时滑动窗口复现判定与日/周超频计算引擎
    """

    @classmethod
    def calculate_8h_repeat(cls, target_hour: str) -> int:
        """
        针对指定 target_hour 时段的问题小区记录，
        计算 [target_hour - 7h, target_hour] 过去 8 小时内的复现次数，
        若累计出现次数 >= 2，则更新 is_repeated_8h = 1, repeat_count_8h = N
        返回更新记录数
        """
        try:
            dt = datetime.strptime(target_hour, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            logger.error(f"时间格式错误: {target_hour}")
            return 0

        window_start_dt = dt - timedelta(hours=7)
        window_start = window_start_dt.strftime("%Y-%m-%d %H:%M:%S")

        with db_manager.get_session() as session:
            # 1. 查找 target_hour 时段下的所有候选问题记录
            target_records = session.query(CellIssueRecord).filter(
                CellIssueRecord.start_time == target_hour
            ).all()

            if not target_records:
                return 0

            # 2. 统计滑动窗口内每个 (cell_id, issue_type) 的出现次数
            # 兼容 MySQL 与 SQLite 的通用参数化 SQL
            sql = text("""
                SELECT cell_id, issue_type, COUNT(*) as cnt
                FROM cell_issue_records
                WHERE start_time >= :window_start AND start_time <= :window_end
                GROUP BY cell_id, issue_type
            """)
            result = session.execute(sql, {"window_start": window_start, "window_end": target_hour}).fetchall()
            counts_map = {(row[0], row[1]): row[2] for row in result}

            updated_count = 0
            for rec in target_records:
                # counts_map 为当前时段及过去7小时内同 (cell_id, issue_type) 的总发生次数
                total_in_window = counts_map.get((rec.cell_id, rec.issue_type), 1)
                # 复现次数定义为：在当前时段之外，过去窗口内重复出现的次数 (即 total - 1)
                repeat_times = max(0, total_in_window - 1)
                rec.repeat_count_8h = repeat_times
                rec.is_repeated_8h = 1 if repeat_times >= 1 else 0
                updated_count += 1

            session.commit()
            logger.info(f"时段 {target_hour} 8小时滑动窗口复现判定完成，已更新 {updated_count} 条记录")
            return updated_count

    @classmethod
    def aggregate_daily_overclock(cls, target_date: str) -> int:
        """
        汇总指定日期 (例如 '2026-09-19') 00:00:00 至 23:59:59 的超频小区沉淀
        """
        day_start = f"{target_date} 00:00:00"
        day_end = f"{target_date} 23:59:59"

        with db_manager.get_session() as session:
            # 查询当天有恶化记录的小区
            sql = text("""
                SELECT 
                    cell_id, network_type, cell_name, city, issue_type,
                    COUNT(*) as occurrences,
                    MAX(repeat_count_8h) as max_repeat,
                    MIN(start_time) as first_t,
                    MAX(start_time) as last_t
                FROM cell_issue_records
                WHERE start_time >= :start_time AND start_time <= :end_time
                GROUP BY cell_id, network_type, cell_name, city, issue_type
            """)
            rows = session.execute(sql, {"start_time": day_start, "end_time": day_end}).fetchall()

            # 写入或更新 cell_overclock_summary (cycle_type='DAILY')
            for r in rows:
                summary = session.query(CellOverclockSummary).filter_by(
                    cycle_type="DAILY",
                    cycle_key=target_date,
                    cell_id=r[0],
                    issue_type=r[4]
                ).first()

                if not summary:
                    summary = CellOverclockSummary(
                        cycle_type="DAILY",
                        cycle_key=target_date,
                        network_type=r[1],
                        cell_id=r[0],
                        cell_name=r[2],
                        city=r[3],
                        issue_type=r[4],
                        occurrences_count=r[5],
                        active_days_count=1,
                        max_repeat_in_8h=r[6] or 1,
                        first_time=r[7],
                        last_time=r[8]
                    )
                    session.add(summary)
                else:
                    summary.occurrences_count = r[5]
                    summary.max_repeat_in_8h = r[6] or 1
                    summary.last_time = r[8]

            session.commit()
            return len(rows)

    @classmethod
    def get_weekly_cycle_range(cls, ref_date: datetime) -> Tuple[datetime, datetime, str]:
        """
        根据网优考核周期规范计算：上周五 00:00:00 至 本周四 23:59:59
        若今天是周五，则上周五为7天前；若周一至周四，则上周五为当前周往前推至最近的周五
        """
        # Python weekday: 0=周一, 1=周二, 2=周三, 3=周四, 4=周五, 5=周六, 6=周日
        wd = ref_date.weekday()
        if wd >= 4: # 周五(4), 周六(5), 周日(6) -> 本周期从本周五开始
            days_back = wd - 4
            start_dt = (ref_date - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=0)
        else: # 周一(0), 周二(1), 周三(2), 周四(3) -> 周期从上一周的周五开始
            days_back = wd + 3
            start_dt = (ref_date - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=0)

        cycle_key = f"{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}"
        return start_dt, end_dt, cycle_key
