from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from services.auth_service import AuthService
import yaml
import os

class TaskScheduler:
    """
    APScheduler 定时调度中枢 (仅保留 Cookie 心跳与工单自动采集)
    """
    _scheduler = None

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            # 每 10 分钟检测一次 Cookie 状态
            cls._scheduler.add_job(
                cls.heartbeat_job,
                "interval",
                minutes=10,
                id="auth_heartbeat_job",
                replace_existing=True
            )
            # 工单后台自动定时拉取 (根据 config.yaml 动态配置间隔，默认 5 分钟)
            wo_cfg = cls._get_workorder_config()
            if wo_cfg.get("enabled", True) and wo_cfg.get("auto_collect", True):
                interval_mins = max(1, int(wo_cfg.get("collect_interval_mins", 5)))
                cls._scheduler.add_job(
                    cls.workorder_collect_job,
                    "interval",
                    minutes=interval_mins,
                    id="workorder_collect_job",
                    replace_existing=True
                )
                logger.info(f"已注册工单自动采集定时任务: 每 {interval_mins} 分钟执行一次")
            cls._scheduler.start()
            logger.info("APScheduler 后台定时调度器启动成功 (Cookie 心跳 / 工单监控)")

    @classmethod
    def _get_workorder_config(cls) -> dict:
        config_path = "config/config.yaml"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                    return cfg.get("workorder", {})
            except Exception:
                pass
        return {}

    @classmethod
    def stop(cls):
        if cls._scheduler and cls._scheduler.running:
            cls._scheduler.shutdown(wait=False)
            logger.info("APScheduler 调度器已安全停止")

    @classmethod
    def heartbeat_job(cls):
        """定期心跳检测 Cookie 存活性"""
        try:
            AuthService.refresh_and_check_session()
        except Exception as e:
            logger.warning(f"Cookie 心跳检测异常: {e}")

    @classmethod
    def workorder_collect_job(cls):
        """例行定时拉取近 7 天最新工单数据（集团工单+省内工单）"""
        auth_info = AuthService.get_cookie_status()
        if auth_info.get("global_status") == "STANDBY_AUTH":
            logger.warning("[工单监控] 当前处于认证挂起状态 (STANDBY_AUTH)，跳过本次工单拉取")
            return

        from services.workorder_collector import WorkOrderCollector
        wo_cfg = cls._get_workorder_config()
        lookback = int(wo_cfg.get("lookback_days", 7))
        today = datetime.now()
        start_date = (today - timedelta(days=max(1, lookback - 1))).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        try:
            logger.info(f"[工单监控] 定时自动同步工单 (近 {lookback} 天): {start_date} ~ {end_date}")
            WorkOrderCollector.collect_all(start_date, end_date)
        except Exception as e:
            logger.error(f"[工单监控] 定时同步异常: {e}")
