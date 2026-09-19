from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from services.auth_service import AuthService
from services.pipeline_service import PipelineService
from db.database import db_manager
from db.models import ETLCheckpoint

class TaskScheduler:
    """
    APScheduler 定时调度与任务编排中枢
    """
    _scheduler = None

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            # 每小时第 25 分钟执行例行小时采集 (如 10:25 采集 09:00~10:00)
            cls._scheduler.add_job(
                cls.hourly_job,
                "cron",
                minute=25,
                id="hourly_monitor_job",
                replace_existing=True
            )
            # 每 10 分钟检测一次 Cookie 状态
            cls._scheduler.add_job(
                cls.heartbeat_job,
                "interval",
                minutes=10,
                id="auth_heartbeat_job",
                replace_existing=True
            )
            cls._scheduler.start()
            logger.info("APScheduler 后台定时调度器启动成功")

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
    def hourly_job(cls):
        """例行定时拉取上一个完整小时"""
        # 检查是否处于安全挂起状态
        auth_info = AuthService.get_cookie_status()
        if auth_info.get("global_status") == "STANDBY_AUTH":
            logger.warning("当前处于认证挂起状态 (STANDBY_AUTH)，跳过本次定时拉取，等待用户登录续期")
            return

        # 计算上一小时时段 (如 10:25 对应 09:00:00)
        prev_hour_dt = datetime.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        prev_hour_str = prev_hour_dt.strftime("%Y-%m-%d %H:%M:%S")
        try:
            PipelineService.run_pipeline_for_hour(prev_hour_str)
        except Exception as e:
            logger.error(f"定时任务执行异常 [{prev_hour_str}]: {e}")
