from typing import Optional, Dict, Tuple
from loguru import logger

class NqiClient:
    """
    NQI 大数据平台客户端封装 (仅保留登录与会话管理)：
    内聚自研 NQI 鉴权核心，支持会话持久化、会话校验、验证码刷新与分阶段登录
    """
    def __init__(self):
        try:
            from core.nqi.auth import LoginManager
            from core.nqi.utils.config import load_config
            self.LoginManager = LoginManager
            self.nqi_config = load_config()
            self.auth_manager = LoginManager(
                username=self.nqi_config.get("auth", {}).get("username", ""),
                password=self.nqi_config.get("auth", {}).get("password", "")
            )
            # 初始化时立即尝试载入本地持久化的 Cookie
            if self.auth_manager:
                self.auth_manager.load_cookies()
            logger.info("内聚版 NqiClient 登录核心加载成功 (无外部项目依赖)")
        except Exception as e:
            logger.error(f"内聚版 NqiClient 初始化失败: {e}")
            self.auth_manager = None

    def check_session_status(self) -> Dict[str, object]:
        """
        检测当前 Cookie 是否有效
        返回: {'valid': bool, 'msg': str, 'require_captcha': bool}
        """
        if not self.auth_manager:
            return {"valid": False, "msg": "NqiClient未正确初始化", "require_captcha": False}

        try:
            # 尝试加载本地现有 Cookie
            cookies = self.auth_manager.load_cookies()
            if not cookies:
                return {"valid": False, "msg": "本地未找到已保存的 Cookie，请登录", "require_captcha": True}

            # 简单验证请求
            is_valid = self.auth_manager.validate_session()
            if is_valid:
                return {"valid": True, "msg": "Cookie 有效且在线", "require_captcha": False}
            else:
                return {"valid": False, "msg": "Cookie 已失效，需重新认证", "require_captcha": True}
        except Exception as e:
            return {"valid": False, "msg": f"Session 检测异常: {str(e)}", "require_captcha": True}

    def get_captcha_image(self) -> Optional[bytes]:
        """获取最新的图形验证码图片字节流"""
        if not self.auth_manager:
            return None
        try:
            captcha_bytes = self.auth_manager.get_captcha()
            return captcha_bytes
        except Exception as e:
            logger.error(f"获取验证码失败: {e}")
            return None

    def login_with_credentials(self, username: str, password: str, captcha_code: str = "", msg_code: str = "") -> Tuple[bool, str]:
        """通过账号密码、图形验证码及短信验证码分步/统一登录"""
        if not self.auth_manager:
            return False, "NqiClient 未初始化"
        try:
            return self.auth_manager.login_with_credentials(
                username=username,
                password=password,
                captcha=captcha_code,
                msg_code=msg_code
            )
        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False, str(e)
