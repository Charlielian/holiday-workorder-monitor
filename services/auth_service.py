import os
import base64
from datetime import datetime
from typing import Dict, Any, Optional
from loguru import logger

from db.database import db_manager
from db.models import ETLCheckpoint
from core.nqi_client import NqiClient

class AuthService:
    """
    NQI 登录认证、Cookie 状态感知与断点续采协调服务
    """
    _client = None

    @classmethod
    def get_client(cls) -> NqiClient:
        if cls._client is None:
            cls._client = NqiClient()
        return cls._client

    @classmethod
    def get_cookie_status(cls) -> Dict[str, Any]:
        """
        获取当前系统的 Cookie 状态与全局运行状态
        """
        return cls._compute_cookie_status()

    @classmethod
    def _compute_cookie_status(cls) -> Dict[str, Any]:
        with db_manager.get_session() as session:
            g_status = session.query(ETLCheckpoint).filter_by(checkpoint_key="GLOBAL_STATUS").first()
            c_status = session.query(ETLCheckpoint).filter_by(checkpoint_key="COOKIE_STATUS").first()
            last_h = session.query(ETLCheckpoint).filter_by(checkpoint_key="LAST_SUCCESS_HOUR").first()
            last_check = session.query(ETLCheckpoint).filter_by(checkpoint_key="COOKIE_LAST_CHECK").first()

            return {
                "global_status": g_status.checkpoint_value if g_status else "ACTIVE",
                "cookie_status": c_status.checkpoint_value if c_status else "VALID",
                "last_success_hour": last_h.checkpoint_value if last_h else "无",
                "last_check_time": last_check.checkpoint_value if last_check else "未检测",
            }

    @classmethod
    def refresh_and_check_session(cls) -> Dict[str, Any]:
        """主动触发探测 NQI 接口检测 Cookie 是否有效"""
        client = cls.get_client()
        res = client.check_session_status()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with db_manager.get_session() as session:
            c_status = session.query(ETLCheckpoint).filter_by(checkpoint_key="COOKIE_STATUS").first()
            last_check = session.query(ETLCheckpoint).filter_by(checkpoint_key="COOKIE_LAST_CHECK").first()
            g_status = session.query(ETLCheckpoint).filter_by(checkpoint_key="GLOBAL_STATUS").first()

            if res.get("valid"):
                if c_status: c_status.checkpoint_value = "VALID"
                if g_status and g_status.checkpoint_value == "STANDBY_AUTH":
                    g_status.checkpoint_value = "ACTIVE"
            else:
                if c_status: c_status.checkpoint_value = "EXPIRED"
                if g_status and g_status.checkpoint_value == "ACTIVE":
                    g_status.checkpoint_value = "STANDBY_AUTH"

            if last_check:
                last_check.checkpoint_value = now_str
            session.commit()

        return res

    @classmethod
    def get_captcha_base64(cls) -> Optional[str]:
        """获取验证码的 Base64 图片"""
        client = cls.get_client()
        img_bytes = client.get_captcha_image()
        if img_bytes:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            return f"data:image/png;base64,{b64}"
        return None

    @classmethod
    def submit_login(cls, username: str, password: str, captcha: str = "", msg_code: str = "") -> Dict[str, Any]:
        """提交登录凭据更新 Cookie (支持分阶段短信码)"""
        client = cls.get_client()
        success, msg = client.login_with_credentials(username, password, captcha, msg_code)
        if msg == "NEED_SMS_CODE":
            return {"success": False, "need_sms_code": True, "message": "图形验证码已通过，短信验证码已发送，请查收后输入"}
        if success:
            with db_manager.get_session() as session:
                c_status = session.query(ETLCheckpoint).filter_by(checkpoint_key="COOKIE_STATUS").first()
                g_status = session.query(ETLCheckpoint).filter_by(checkpoint_key="GLOBAL_STATUS").first()
                if c_status: c_status.checkpoint_value = "VALID"
                if g_status: g_status.checkpoint_value = "ACTIVE"
                session.commit()
            logger.info(f"用户 {username} 重新登录成功，会话已更新")
            return {"success": True, "message": "登录成功，Cookie已刷新并激活续采"}
        else:
            return {"success": False, "message": f"登录失败: {msg}"}
