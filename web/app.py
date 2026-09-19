import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from loguru import logger

from db.database import db_manager
from db.models import ETLTaskLog
from services.auth_service import AuthService
from services.monitor_service import MonitorService
from services.export_service import ExportService
from services.pipeline_service import PipelineService
from core.scheduler import TaskScheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    logger.info("性能超频小区监控服务正在启动...")
    # 若无数据，初始化部分仿真测试样本以便前端完整预览交互
    PipelineService.seed_demo_data_if_empty()
    TaskScheduler.start()
    yield
    # 停止时安全退出
    TaskScheduler.stop()
    logger.info("性能超频小区监控服务已关闭")

app = FastAPI(title="性能超频小区监控平台", lifespan=lifespan)

# 静态文件挂载 (单页应用前端)
app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/")
def read_index():
    return FileResponse("web/static/index.html")

# ------------------ 认证与状态 API ------------------
@app.get("/api/auth/status")
def get_auth_status():
    """获取当前系统运行状态与 Cookie 有效性"""
    return AuthService.get_cookie_status()

@app.post("/api/auth/check")
def trigger_auth_check():
    """主动触发 Cookie 探测"""
    return AuthService.refresh_and_check_session()

@app.get("/api/auth/captcha")
def get_captcha():
    """获取验证码 Base64"""
    b64 = AuthService.get_captcha_base64()
    return {"captcha": b64}

class LoginRequest(BaseModel):
    username: str
    password: str
    captcha: Optional[str] = ""

@app.post("/api/auth/login")
def login(req: LoginRequest):
    """提交凭据续期 Cookie"""
    res = AuthService.submit_login(req.username, req.password, req.captcha or "")
    return res

# ------------------ 业务监控 API ------------------
@app.get("/api/monitor/latest")
def get_latest_monitor(hour: Optional[str] = None):
    """最新时段监控数据"""
    return MonitorService.get_latest_hour_overview(hour)

@app.get("/api/monitor/daily")
def get_daily_monitor(date: Optional[str] = None):
    """本日超频看板数据"""
    return MonitorService.get_daily_overview(date)

@app.get("/api/monitor/weekly")
def get_weekly_monitor(ref_date: Optional[str] = None):
    """本周(上周五至本周四)考核数据"""
    return MonitorService.get_weekly_overview(ref_date)

# ------------------ 导出 API ------------------
@app.get("/api/export/latest")
def export_latest(hour: Optional[str] = None):
    """导出最新时段 Excel"""
    file_path = ExportService.export_latest_hour_excel(hour)
    return FileResponse(file_path, filename=os.path.basename(file_path), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/api/export/weekly")
def export_weekly(ref_date: Optional[str] = None):
    """导出周考核 Excel"""
    file_path = ExportService.export_weekly_excel(ref_date)
    return FileResponse(file_path, filename=os.path.basename(file_path), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------ 任务与补采 API ------------------
@app.get("/api/tasks/logs")
def get_task_logs(limit: int = 50):
    """查询最近的 ETL 任务执行流水"""
    with db_manager.get_session() as session:
        logs = session.query(ETLTaskLog).order_by(ETLTaskLog.id.desc()).limit(limit).all()
        return [
            {
                "id": l.id,
                "task_name": l.task_name,
                "data_hour": l.data_hour,
                "row_count": l.row_count,
                "status": l.status,
                "duration_sec": l.duration_sec,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else ""
            }
            for l in logs
        ]

class BackfillRequest(BaseModel):
    start_hour: str # '2026-09-19 00:00:00'
    end_hour: str   # '2026-09-19 08:00:00'

@app.post("/api/tasks/backfill")
def trigger_backfill(req: BackfillRequest, background_tasks: BackgroundTasks):
    """触发指定时间区间的断点补采"""
    def _do_backfill(start_str, end_str):
        from datetime import datetime, timedelta
        curr = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
        while curr <= end:
            h_str = curr.strftime("%Y-%m-%d %H:%M:%S")
            PipelineService.run_pipeline_for_hour(h_str)
            curr += timedelta(hours=1)

    background_tasks.add_task(_do_backfill, req.start_hour, req.end_hour)
    return {"message": f"补采任务已提交后台处理: {req.start_hour} 至 {req.end_hour}"}
