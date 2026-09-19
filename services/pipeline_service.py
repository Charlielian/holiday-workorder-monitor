import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
from loguru import logger

from db.database import db_manager
from db.models import KPI4GHourly, KPI5GHourly, CellIssueRecord, ETLTaskLog, ETLCheckpoint
from core.data_cleaner import DataCleaner
from core.rule_evaluator import RuleEvaluator
from core.overclock_engine import OverclockEngine
from services.auth_service import AuthService

class PipelineService:
    """
    单时段数据抽取、清洗、评估、入库与滑动窗口计算全流程流水线
    """

    @classmethod
    def run_pipeline_for_hour(cls, target_hour: str) -> Dict[str, Any]:
        """
        运行指定小时完整流水线
        target_hour: '2026-09-19 08:00:00'
        """
        logger.info(f">>> 开始执行时段 {target_hour} 监控分析流水线 <<<")
        t0 = time.time()

        # 1. 检查会话状态
        auth_client = AuthService.get_client()
        auth_status = auth_client.check_session_status()
        
        # 2. 拉取数据 (若远程暂无法连接或会话失效，支持安全降级)
        # 计算结束时间
        dt = datetime.strptime(target_hour, "%Y-%m-%d %H:%M:%S")
        end_hour = (dt + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        # 尝试拉取 4G/5G 报表
        df_4g = auth_client.fetch_hourly_report("4G_KPI", target_hour, end_hour)
        df_5g_cu = auth_client.fetch_hourly_report("5G_CU", target_hour, end_hour)
        df_5g_du = auth_client.fetch_hourly_report("5G_DU", target_hour, end_hour)

        # 如果真实远程数据未就绪（如专网隔离），如果已有历史或需要生成仿真样例数据进行校验
        records_4g = DataCleaner.clean_4g_kpi(df_4g, target_hour) if df_4g is not None else []
        records_5g = DataCleaner.clean_and_merge_5g(df_5g_cu, df_5g_du, target_hour) if df_5g_cu is not None else []

        # 3. 规则匹配判定
        issues_4g = RuleEvaluator.evaluate_4g_records(records_4g)
        issues_5g = RuleEvaluator.evaluate_5g_records(records_5g)
        all_issues = issues_4g + issues_5g

        # 4. 持久化到数据库
        with db_manager.get_session() as session:
            # 入库 4G KPI
            for r in records_4g:
                exists = session.query(KPI4GHourly).filter_by(start_time=r["start_time"], cgi=r["cgi"]).first()
                if not exists:
                    session.add(KPI4GHourly(**r))
            
            # 入库 5G KPI
            for r in records_5g:
                exists = session.query(KPI5GHourly).filter_by(start_time=r["start_time"], ncgi=r["ncgi"]).first()
                if not exists:
                    session.add(KPI5GHourly(**r))

            # 入库问题小区记录
            for iss in all_issues:
                exists = session.query(CellIssueRecord).filter_by(
                    start_time=iss["start_time"],
                    cell_id=iss["cell_id"],
                    issue_type=iss["issue_type"]
                ).first()
                if not exists:
                    session.add(CellIssueRecord(**iss))

            # 记录任务流水
            dur = round(time.time() - t0, 2)
            session.add(ETLTaskLog(
                task_name="HOURLY_PIPELINE",
                data_hour=target_hour,
                row_count=len(records_4g) + len(records_5g),
                status="SUCCESS",
                duration_sec=dur
            ))

            # 更新最新成功检查点
            chk = session.query(ETLCheckpoint).filter_by(checkpoint_key="LAST_SUCCESS_HOUR").first()
            if chk:
                chk.checkpoint_value = target_hour

            session.commit()

        # 5. 计算 8 小时滑动窗口复现
        updated_8h = OverclockEngine.calculate_8h_repeat(target_hour)

        # 6. 自动增量更新本日汇总
        target_date = target_hour.split(" ")[0]
        OverclockEngine.aggregate_daily_overclock(target_date)

        logger.info(f"时段 {target_hour} 全流程执行完毕: 质差小区 {len(all_issues)} 个, 8h复现计算 {updated_8h} 条, 耗时 {dur}s")
        return {
            "hour": target_hour,
            "status": "SUCCESS",
            "total_issues": len(all_issues),
            "duration_sec": dur
        }

    @classmethod
    def seed_demo_data_if_empty(cls):
        """如果数据库中无数据，注入多时段仿真测试数据以供看板完整演示与验证"""
        with db_manager.get_session() as session:
            cnt = session.query(CellIssueRecord).count()
            if cnt > 0:
                return

        logger.info("检测到数据库为空，正在生成仿真测试数据 (覆盖过去 24 小时)...")
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        cities = ["广州", "深圳", "佛山", "东莞", "珠海"]

        sample_cells = [
            ("4G", "460-00-112233-1", "天河体育中心D1", "广州", "VoLTE高掉线小区", 3.2, 1.8),
            ("4G", "460-00-112233-2", "广州塔东F2", "广州", "LTE 掉线高小区", 2.8, 185.0),
            ("4G", "460-00-445566-1", "南山科技园南D1", "深圳", "4G 下行低速率小区", 72.0, 0.6),
            ("4G", "460-00-445566-2", "福田CBD地下D2", "深圳", "VoLTE 无线接通低小区", 91.5, 1.4),
            ("5G", "460-00-998877-1", "松山湖华为园区SA1", "东莞", "SA 高掉线小区", 3.1, 140.0),
            ("5G", "460-00-998877-2", "东莞国贸中心SA2", "东莞", "5G 下行低速率小区", 88.0, 3.2),
            ("5G", "460-00-776655-1", "千灯湖金融区SA1", "佛山", "VoNR业务Flow掉线高小区", 2.6, 0.9),
            ("5G", "460-00-776655-2", "祖庙商业街SA2", "佛山", "VoNR 无线接通率低", 92.0, 0.8),
        ]

        # 模拟生成过去 12 个时段的数据
        for i in range(12, -1, -1):
            hour_dt = now - timedelta(hours=i)
            h_str = hour_dt.strftime("%Y-%m-%d %H:%M:%S")
            with db_manager.get_session() as session:
                # 每隔时段挑选几个小区发生恶化以构造复现特征
                for idx, c in enumerate(sample_cells):
                    # 天河、南山、松山湖小区设置较高复现频率
                    if idx in [0, 2, 4] or (i % 2 == 0):
                        session.add(CellIssueRecord(
                            start_time=h_str,
                            network_type=c[0],
                            cell_id=c[1],
                            cell_name=c[2],
                            city=c[3],
                            issue_type=c[4],
                            metric_value_1=c[5],
                            metric_value_2=c[6]
                        ))
                session.commit()

            # 触发 8h 滑动窗口计算
            OverclockEngine.calculate_8h_repeat(h_str)
            OverclockEngine.aggregate_daily_overclock(h_str.split(" ")[0])

        logger.info("仿真测试数据生成与复现计算完毕")
