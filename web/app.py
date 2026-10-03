import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from loguru import logger

from services.auth_service import AuthService
from services.user_service import (
    UserService, USER_ALLOWED_VIEWS, ADMIN_ALLOWED_VIEWS
)
from services.export_service import ExportService
from services.workorder_service import WorkOrderService
from services.workorder_collector import WorkOrderCollector
from core.scheduler import TaskScheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    logger.info("节假日监控工单监控服务正在启动...")
    # 初始化平台登录账号 (admin / user)
    UserService.ensure_default_users()
    TaskScheduler.start()
    yield
    # 停止时安全退出
    TaskScheduler.stop()
    logger.info("节假日监控工单监控服务已关闭")

app = FastAPI(title="节假日监控工单监控", lifespan=lifespan)

# 静态文件挂载 (单页应用前端)
app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/")
def read_index():
    return FileResponse("web/static/index.html")

# ------------------ 平台用户登录与权限 ------------------
class PlatformLoginRequest(BaseModel):
    username: str
    password: str

def _current_user(request: Request) -> Optional[dict]:
    """从请求头解析平台登录令牌"""
    token = request.headers.get("X-User-Token")
    return UserService.parse_token(token)

@app.post("/api/user/login")
def platform_login(req: PlatformLoginRequest):
    """平台账号登录，返回令牌与角色信息"""
    user = UserService.verify_login(req.username, req.password)
    if not user:
        return {"success": False, "message": "账号或密码错误"}
    token = UserService.make_token(user)
    views = ADMIN_ALLOWED_VIEWS if user["role"] == "admin" else USER_ALLOWED_VIEWS
    return {
        "success": True,
        "token": token,
        "username": user["username"],
        "role": user["role"],
        "display_name": user["display_name"],
        "allowed_views": views,
        "message": f"登录成功 ({'管理员' if user['role']=='admin' else '普通用户'})"
    }

@app.post("/api/user/logout")
def platform_logout(request: Request):
    """退出登录，吊销令牌会话"""
    UserService.logout(request.headers.get("X-User-Token"))
    return {"success": True, "message": "已退出登录"}

@app.get("/api/user/profile")
def platform_profile(request: Request):
    """校验当前令牌并返回用户信息 (前端刷新时恢复会话)"""
    user = _current_user(request)
    if not user:
        return {"logged_in": False}
    views = ADMIN_ALLOWED_VIEWS if user["role"] == "admin" else USER_ALLOWED_VIEWS
    return {"logged_in": True, "username": user["username"], "role": user["role"],
            "display_name": user["display_name"], "allowed_views": views}

# ------------------ 大数据平台认证与状态 API ------------------
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
    msg_code: Optional[str] = ""

@app.post("/api/auth/login")
def login(req: LoginRequest):
    """提交大数据平台凭据续期 Cookie"""
    res = AuthService.submit_login(req.username, req.password, req.captcha or "", req.msg_code or "")
    return res

# ------------------ 工单监控 API ------------------
@app.get("/api/workorder/cities")
def get_workorder_cities():
    """获取所有支持的地市列表"""
    return {"success": True, "cities": WorkOrderService.list_cities()}

@app.get("/api/workorder/summary")
def get_workorder_summary(city: Optional[str] = None,
                          cities: Optional[str] = None,
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None):
    """
    工单汇总看板数据 (指标卡、地市分布统计表、最近动态)
    支持按单个地市、逗号分隔多地市(cities)以及时间区间统计
    """
    city_param = cities or city
    city_list = [c.strip() for c in city_param.split(",") if c and c.strip()] if city_param else None
    return WorkOrderService.get_summary(city=city_param, cities=city_list,
                                        start_date=start_date, end_date=end_date)

@app.get("/api/workorder/orders")
def get_workorder_orders(type: str = "group",
                         city: Optional[str] = None,
                         status: Optional[str] = None,
                         keyword: Optional[str] = None,
                         ids: Optional[str] = None,
                         start_date: Optional[str] = None,
                         end_date: Optional[str] = None,
                         page: int = 1,
                         page_size: int = 20):
    """
    分页查询集团工单或省内工单明细
    type: 'group' (集团工单) 或 'province' (省内工单)
    ids: all(默认) / ids(仅 IDS 工单) / non_ids(仅非 IDS 工单)
    """
    if type not in ("group", "province"):
        return JSONResponse(status_code=400, content={"detail": "type 必须为 group 或 province"})
    return WorkOrderService.query_orders(
        order_type=type, city=city, status=status, keyword=keyword, ids_flag=ids,
        start_date=start_date, end_date=end_date, page=page, page_size=page_size
    )

class WorkOrderIdsRequest(BaseModel):
    is_ids: bool = False

@app.patch("/api/workorder/orders/{order_id}/ids")
def update_workorder_ids(order_id: int, req: WorkOrderIdsRequest, request: Request):
    """
    人工标记工单是否为 IDS 工单 (省内工单优化流程页可编辑)

    标记为 IDS 后该工单不参与汇总统计计算。任何已登录用户均可标记,
    因为该字段只能靠人工判读业务单据后填写。
    """
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"detail": "请先登录平台账号"})
    return WorkOrderService.update_ids_flag(order_id, req.is_ids)

class WorkOrderManualFinishRequest(BaseModel):
    is_finished: bool = True

@app.patch("/api/workorder/orders/{order_id}/manual-finish")
def update_workorder_manual_finish(order_id: int, req: WorkOrderManualFinishRequest, request: Request):
    """
    人工标记工单为已处理/取消人工标记

    - 标记为已处理: is_finished=1, is_manual_finished=1 (计入已处理统计)
    - 取消人工标记: 回退为未处理 (仅对人工标记的工单生效)

    后续自动拉取数据若证明工单已办结, 采集器会自动清除 is_manual_finished 标记,
    表示该"已处理"状态已由系统数据证实。
    """
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"detail": "请先登录平台账号"})
    return WorkOrderService.mark_manual_finished(order_id, req.is_finished)

class WorkOrderCollectRequest(BaseModel):
    start_date: str
    end_date: str
    city: Optional[str] = None
    scope: str = "all"  # all / group / province

@app.post("/api/workorder/collect")
def trigger_workorder_collect(req: WorkOrderCollectRequest, background_tasks: BackgroundTasks):
    """手动触发工单拉取/增量同步 (支持后台任务)"""
    def _run_collect():
        try:
            if req.scope == "group":
                WorkOrderCollector.collect_group_orders(req.start_date, req.end_date, req.city)
            elif req.scope == "province":
                WorkOrderCollector.collect_province_orders(req.start_date, req.end_date)
            else:
                WorkOrderCollector.collect_all(req.start_date, req.end_date, req.city)
        except Exception as e:
            logger.error(f"[WorkOrder] 手动采集异常: {e}")

    background_tasks.add_task(_run_collect)
    return {"success": True, "message": f"已在后台启动工单采集任务: {req.start_date} ~ {req.end_date}"}

@app.get("/api/export/workorder")
def export_workorder(type: str = "all",
                     city: Optional[str] = None,
                     status: Optional[str] = None,
                     keyword: Optional[str] = None,
                     ids: Optional[str] = None,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None):
    """
    导出工单全部信息为多 Sheet 结构化 Excel
    包含：工单汇总统计、集团工单明细、省内工单明细
    ids: all(默认) / ids(仅 IDS 工单) / non_ids(仅非 IDS 工单)
    """
    file_path = ExportService.export_workorder_excel(
        order_type=type, city=city, status=status, keyword=keyword, ids_flag=ids,
        start_date=start_date, end_date=end_date
    )
    return FileResponse(file_path, filename=os.path.basename(file_path),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
