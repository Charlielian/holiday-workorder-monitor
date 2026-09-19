from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import text, desc
from loguru import logger

from db.database import db_manager
from db.models import CellIssueRecord, KPI4GHourly, KPI5GHourly, ETLCheckpoint
from core.overclock_engine import OverclockEngine

class MonitorService:
    """
    监控与多维聚合统计服务 (最新时段 / 本日看板 / 上周五至本周四周考核)
    """

    @classmethod
    def get_latest_hour_overview(cls, specific_hour: Optional[str] = None) -> Dict[str, Any]:
        """
        获取最新时段监控数据
        """
        with db_manager.get_session() as session:
            # 若未指定时段，查询数据库中最新入库的时段
            if not specific_hour:
                latest_rec = session.query(CellIssueRecord.start_time).order_by(desc(CellIssueRecord.start_time)).first()
                if not latest_rec:
                    # 如果没有问题记录，查查是否有原始指标
                    kpi_rec = session.query(KPI4GHourly.start_time).order_by(desc(KPI4GHourly.start_time)).first()
                    if not kpi_rec:
                        return {
                            "latest_hour": "暂无数据",
                            "total_issues": 0,
                            "repeated_8h_count": 0,
                            "g4_count": 0,
                            "g5_count": 0,
                            "category_stats": [],
                            "issue_records": []
                        }
                    specific_hour = kpi_rec[0]
                else:
                    specific_hour = latest_rec[0]

            # 统计该时段的数据
            records = session.query(CellIssueRecord).filter(
                CellIssueRecord.start_time == specific_hour
            ).all()

            total_issues = len(records)
            repeated_8h_count = sum(1 for r in records if r.is_repeated_8h == 1)
            g4_count = sum(1 for r in records if r.network_type == "4G")
            g5_count = sum(1 for r in records if r.network_type == "5G")

            # 10类问题分布统计
            cat_counts: Dict[str, int] = {}
            for r in records:
                cat_counts[r.issue_type] = cat_counts.get(r.issue_type, 0) + 1

            category_stats = [{"name": k, "count": v} for k, v in sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)]

            # 格式化明细清单 (按是否8h复现高亮排在前面)
            sorted_records = sorted(records, key=lambda x: (x.is_repeated_8h, x.repeat_count_8h), reverse=True)
            issue_list = []
            for r in sorted_records:
                issue_list.append({
                    "id": r.id,
                    "start_time": r.start_time,
                    "network_type": r.network_type,
                    "cell_id": r.cell_id,
                    "cell_name": r.cell_name,
                    "city": r.city,
                    "issue_type": r.issue_type,
                    "metric_value_1": r.metric_value_1,
                    "metric_value_2": r.metric_value_2,
                    "is_repeated_8h": r.is_repeated_8h,
                    "repeat_count_8h": r.repeat_count_8h
                })

            return {
                "latest_hour": specific_hour,
                "total_issues": total_issues,
                "repeated_8h_count": repeated_8h_count,
                "g4_count": g4_count,
                "g5_count": g5_count,
                "category_stats": category_stats,
                "issue_records": issue_list
            }

    @classmethod
    def get_daily_overview(cls, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        获取本日超频看板数据 (以网元/小区为主体聚合展示为一行，超频定义: 出现复现问题三天及以上)
        """
        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")

        day_start = f"{target_date} 00:00:00"
        day_end = f"{target_date} 23:59:59"

        # 往前追溯30天，统计小区历史上发生复现问题(is_repeated_8h=1)的自然天数
        try:
            cur_dt = datetime.strptime(target_date, "%Y-%m-%d")
        except Exception:
            cur_dt = datetime.now()
        history_start_dt = cur_dt - timedelta(days=30)
        history_start = f"{history_start_dt.strftime('%Y-%m-%d')} 00:00:00"

        with db_manager.get_session() as session:
            # 1. 统计历史各小区出现复现问题的天数（以 cell_id 为维度，只要该小区当天发生过复现就算作1个复现天）
            rep_days_sql = text("""
                SELECT cell_id, COUNT(DISTINCT SUBSTR(start_time, 1, 10)) as rep_days
                FROM cell_issue_records
                WHERE start_time >= :h_start AND start_time <= :day_end
                  AND is_repeated_8h = 1
                GROUP BY cell_id
            """)
            rep_days_rows = session.execute(rep_days_sql, {"h_start": history_start, "day_end": day_end}).fetchall()
            rep_days_map = {r[0]: r[1] for r in rep_days_rows}

            # 2. 查询当天所有质差记录，并在内存中按 cell_id 聚合为独立一行
            sql = text("""
                SELECT 
                    cell_id, network_type, cell_name, city, issue_type,
                    start_time, is_repeated_8h, repeat_count_8h
                FROM cell_issue_records
                WHERE start_time >= :start_time AND start_time <= :end_time
                ORDER BY start_time ASC
            """)
            records = session.execute(sql, {"start_time": day_start, "end_time": day_end}).fetchall()

            # 以 cell_id 为主键进行字典聚合
            cell_map: Dict[str, Dict[str, Any]] = {}
            for r in records:
                c_id = r[0]
                n_type = r[1]
                c_name = r[2]
                city = r[3]
                i_type = r[4]
                s_time = r[5]
                is_rep = r[6]
                rep_cnt = r[7]

                if c_id not in cell_map:
                    rep_days = rep_days_map.get(c_id, 0)
                    cell_map[c_id] = {
                        "cell_id": c_id,
                        "network_type": n_type,
                        "cell_name": c_name,
                        "city": city,
                        "issue_types": set(),
                        "occurrences": 0,
                        "repeat_days": rep_days,
                        "is_overclock": 1 if rep_days >= 3 else 0,
                        "max_repeat": 1,
                        "first_time": s_time,
                        "last_time": s_time
                    }

                info = cell_map[c_id]
                info["issue_types"].add(i_type)
                info["occurrences"] += 1
                if rep_cnt and rep_cnt > info["max_repeat"]:
                    info["max_repeat"] = rep_cnt
                if s_time > info["last_time"]:
                    info["last_time"] = s_time

            # 转换集合为列表
            top_list = []
            overclock_count = 0
            for item in cell_map.values():
                if item["is_overclock"]:
                    overclock_count += 1
                item["issue_types"] = sorted(list(item["issue_types"]))
                top_list.append(item)

            # 按是否超频、恶化天数、本日恶化时段数倒序排列
            top_list.sort(key=lambda x: (x["is_overclock"], x["repeat_days"], x["occurrences"]), reverse=True)

            # 24小时各时段问题分布走势
            hourly_dist_sql = text("""
                SELECT SUBSTR(start_time, 12, 2) as hour_part, COUNT(*) as cnt
                FROM cell_issue_records
                WHERE start_time >= :start_time AND start_time <= :end_time
                GROUP BY hour_part
                ORDER BY hour_part ASC
            """)
            dist_rows = session.execute(hourly_dist_sql, {"start_time": day_start, "end_time": day_end}).fetchall()
            hourly_distribution = {f"{h:02d}": 0 for h in range(24)}
            for r in dist_rows:
                hourly_distribution[r[0]] = r[1]

            return {
                "date": target_date,
                "total_degraded_cells": len(top_list),
                "total_overclock_cells": overclock_count,
                "hourly_distribution": [{"hour": f"{k}:00", "count": v} for k, v in hourly_distribution.items()],
                "top_cells": top_list[:100]
            }

    @classmethod
    def get_weekly_overview(cls, ref_date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        获取上周五至本周四考核周期的监控数据
        """
        if ref_date_str:
            try:
                ref_date = datetime.strptime(ref_date_str, "%Y-%m-%d")
            except:
                ref_date = datetime.now()
        else:
            ref_date = datetime.now()

        start_dt, end_dt, cycle_key = OverclockEngine.get_weekly_cycle_range(ref_date)
        start_time = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_time = end_dt.strftime("%Y-%m-%d %H:%M:%S")

        with db_manager.get_session() as session:
            # 按小区统计周期内恶化天数、总时段数
            sql = text("""
                SELECT 
                    cell_id, network_type, cell_name, city, issue_type,
                    COUNT(*) as total_occurrences,
                    COUNT(DISTINCT SUBSTR(start_time, 1, 10)) as active_days,
                    MAX(is_repeated_8h) as ever_repeated,
                    MIN(start_time) as first_time,
                    MAX(start_time) as last_time
                FROM cell_issue_records
                WHERE start_time >= :start_time AND start_time <= :end_time
                GROUP BY cell_id, network_type, cell_name, city, issue_type
                ORDER BY active_days DESC, total_occurrences DESC
            """)
            rows = session.execute(sql, {"start_time": start_time, "end_time": end_time}).fetchall()

            # 重点考核治理网元: 恶化天数 >= 2 或 累计时段数 >= 5
            key_focus_cells = []
            city_stats: Dict[str, int] = {}
            for r in rows:
                city = r[3] or "未知地市"
                active_days = r[6]
                total_occ = r[5]
                is_focus = (active_days >= 2 or total_occ >= 5)
                
                if is_focus:
                    city_stats[city] = city_stats.get(city, 0) + 1
                    key_focus_cells.append({
                        "cell_id": r[0],
                        "network_type": r[1],
                        "cell_name": r[2],
                        "city": city,
                        "issue_type": r[4],
                        "total_occurrences": total_occ,
                        "active_days": active_days,
                        "ever_repeated": r[7],
                        "first_time": r[8],
                        "last_time": r[9]
                    })

            return {
                "cycle_title": f"考核周期: {start_dt.strftime('%Y-%m-%d')}(周五) ~ {end_dt.strftime('%Y-%m-%d')}(周四)",
                "start_time": start_time,
                "end_time": end_time,
                "total_impacted_cells": len(rows),
                "focus_overclock_cells_count": len(key_focus_cells),
                "city_distribution": [{"city": k, "count": v} for k, v in sorted(city_stats.items(), key=lambda x: x[1], reverse=True)],
                "focus_cells": key_focus_cells
            }
