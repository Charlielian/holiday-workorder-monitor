import hashlib
import hmac
import json
import base64
import time
from typing import Dict, Any, Optional
from loguru import logger

from db.database import db_manager
from db.models import SysUser

# 平台登录令牌签名密钥（本地部署，取固定盐即可；如需更高安全性可改从环境变量读取）
_TOKEN_SECRET = "netsentinel-platform-token-v1"
_TOKEN_TTL_SEC = 12 * 3600  # 12 小时

# 页面访问权限：阉割版仅保留工单监控视图
USER_ALLOWED_VIEWS = ["workorder"]
ADMIN_ALLOWED_VIEWS = ["workorder"]


class UserService:
    """平台登录用户与权限服务 (普通用户 / 管理员)"""

    DEFAULT_USERS = [
        ("admin", "admin123", "admin", "系统管理员"),
        ("user", "user123", "user", "普通用户"),
    ]

    @classmethod
    def ensure_default_users(cls) -> None:
        """首次启动时注入默认账号 (仅当表内无任何用户时)"""
        with db_manager.get_session() as session:
            if session.query(SysUser).count() > 0:
                return
            for username, password, role, display in cls.DEFAULT_USERS:
                session.add(SysUser(
                    username=username,
                    password=password,
                    role=role,
                    display_name=display,
                ))
            session.commit()
        logger.info("已初始化默认平台账号: admin(管理员) / user(普通用户)")
        logger.warning("默认密码为弱口令，请在 config/config.yaml 的 web.users 中覆盖或尽快修改")

    @classmethod
    def verify_login(cls, username: str, password: str) -> Optional[Dict[str, Any]]:
        """校验账号密码，成功返回用户信息，失败返回 None (带短缓存)"""
        if not username or not password:
            return None
        username = username.strip()
        user = cls._load_user(username)
        if not user or user.get("password") != password:
            return None
        return {
            "username": user["username"],
            "role": user.get("role") or "user",
            "display_name": user.get("display_name") or user["username"],
        }

    @classmethod
    def _load_user(cls, username: str) -> Optional[Dict[str, Any]]:
        with db_manager.get_session() as session:
            user = session.query(SysUser).filter_by(username=username).first()
            if not user:
                return None
            return {
                "username": user.username,
                "password": user.password,
                "role": user.role or "user",
                "display_name": user.display_name or user.username,
            }

    # ---------------- 令牌签发与校验 ----------------

    @classmethod
    def _sign(cls, payload_b64: str) -> str:
        return hmac.new(_TOKEN_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:32]

    @classmethod
    def make_token(cls, user: Dict[str, Any]) -> str:
        payload = {
            "u": user.get("username"),
            "r": user.get("role", "user"),
            "n": user.get("display_name") or user.get("username"),
            "e": int(time.time()) + _TOKEN_TTL_SEC,
        }
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        sig = cls._sign(payload_b64)
        return f"{payload_b64}.{sig}"

    @classmethod
    def parse_token(cls, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """解析并校验令牌，无效/过期/已吊销返回 None"""
        if not token or "." not in token:
            return None
        payload_b64, _, sig = token.rpartition(".")
        if not hmac.compare_digest(cls._sign(payload_b64), sig):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
        except Exception:
            return None
        if int(data.get("e", 0)) < int(time.time()):
            return None
        return {
            "username": data.get("u"),
            "role": data.get("r", "user"),
            "display_name": data.get("n") or data.get("u"),
        }

    @classmethod
    def logout(cls, token: Optional[str]) -> None:
        """退出登录。

        阉割版为无状态签名令牌(无服务端会话存储)，无法即时吊销；
        客户端清除本地令牌即可，此处保留接口以兼容前端调用。
        """
        return

    @classmethod
    def is_admin(cls, user: Optional[Dict[str, Any]]) -> bool:
        return bool(user) and user.get("role") == "admin"
