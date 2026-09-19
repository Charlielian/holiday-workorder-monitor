import os
import sys
import time
from typing import Optional, Dict, Any, Tuple
from loguru import logger

class NqiClient:
    """
    NQI 客户端无侵入封装：
    内聚自研 NQI 鉴权与 JXCXQuery 核心
    支持会话持久化、会话校验、验证码刷新与免密登录
    """
    def __init__(self):
        try:
            from core.nqi.auth import LoginManager
            from core.nqi.query import JXCXQuery
            from core.nqi.utils.config import load_config
            self.LoginManager = LoginManager
            self.JXCXQuery = JXCXQuery
            self.nqi_config = load_config()
            self.auth_manager = LoginManager(self.nqi_config)
            self.query_client = JXCXQuery(self.auth_manager.sess if self.auth_manager else None)
            logger.info("内聚版 NqiClient 核心库加载成功 (无外部项目依赖)")
        except Exception as e:
            logger.error(f"内聚版 NqiClient 初始化失败: {e}")
            self.auth_manager = None
            self.query_client = None

    def check_session_status(self) -> Dict[str, Any]:
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

    def login_with_credentials(self, username: str, password: str, captcha_code: str = "") -> Tuple[bool, str]:
        """通过账号密码和验证码登录"""
        if not self.auth_manager:
            return False, "NqiClient 未初始化"
        try:
            success, msg = self.auth_manager.login(username=username, password=password, captcha=captcha_code)
            return success, msg
        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False, str(e)

    def fetch_hourly_report(self, report_type: str, start_time: str, end_time: str) -> Optional[Any]:
        """
        拉取指定类型的小时级性能报表
        report_type: '4G_KPI' | '5G_CU' | '5G_DU'
        start_time: '2026-09-19 08:00:00'
        end_time: '2026-09-19 09:00:00'
        """
        if not self.query_client:
            logger.error("query_client 未就绪")
            return None

        try:
            # 根据类型调用 JXCXQuery 获取表格数据
            # 若现场暂无外网/专网连接，返回 None 触发上层断点记录或 Mock 注入
            df = self.query_client.get_table_data(
                report_type=report_type,
                start_time=start_time,
                end_time=end_time,
                time_dimension="小时"
            )
            return df
        except Exception as e:
            logger.warning(f"拉取报表 {report_type} [{start_time} - {end_time}] 异常: {e}")
            return None
