# -*- coding: utf-8 -*-
"""
登录认证模块
负责用户登录、验证码处理和会话管理
"""

import requests
import json
import logging

from lxml import etree

from core.nqi.utils.config import (
    DEFAULT_USERNAME, DEFAULT_PASSWORD, BASE_URL,
    LOGIN_URL, CAPTCHA_URL, GET_CONFIG_URL, SEND_CODE_URL, HEADERS, HEADERS_JSON
)
from core.nqi.utils.constants import TIMEOUT_SHORT, RETRY_TIMES
from core.nqi.utils.crypto import rsa_encrypt
from core.nqi.utils.helpers import save_cookie, load_cookie, captcha_handle, delete_cookie

logger = logging.getLogger(__name__)


class LoginManager:
    """登录管理器。

    登录分为复用 Cookie、图形验证码、短信验证码和 CASTGC 成功判定几个
    状态阶段。HTTP 200 只说明请求被服务器处理，不能替代业务成功标志；
    当前实现还保留了 verify=False 的企业证书兼容方案，部署时应视为安全
    技术债务而不是安全存储或安全传输保证。
    """

    def __init__(self, username=None, password=None, parent=None):
        self.username = username or DEFAULT_USERNAME
        self.password = password or DEFAULT_PASSWORD
        self.parent = parent
        self.sess = requests.Session()
        # 配置 SSL 验证（企业内部系统可能使用自签名证书）
        # TODO: 禁用 SSL 验证存在 MITM 攻击风险，且全项目 5+ 处重复此设置
        # 建议：1) 统一在 Session 工厂中设置 2) 尝试添加企业自签名证书到信任链
        self.sess.verify = False  # 禁用 SSL 证书验证
        # 禁用 urllib3 的 SSL 警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _update_login_ui(self, **kwargs):
        """批量更新登录UI状态"""
        if not self.parent:
            return
        for key, value in kwargs.items():
            self.parent.after(0, lambda k=key, v=value: self._apply_ui_update(k, v))

    def _apply_ui_update(self, key, value):
        """应用单个UI更新"""
        if key == 'status_text':
            self.parent.status_text.config(text=value)
        elif key == 'status_dot_color':
            self.parent.status_dot.config(fg=value)
        elif key == 'login_icon':
            icon, color = value
            self.parent.login_status_icon.config(text=icon, fg=color)
        elif key == 'login_label':
            label, color = value
            self.parent.login_status_lbl.config(text=label, fg=color)
        elif key == 'log':
            self.parent.log(value[0], value[1])

    def login(self, try_times=None):
        """执行登录

        Args:
            try_times: 登录尝试次数（默认使用常量）

        Returns:
            requests.Session: 登录成功后的会话对象，失败返回 None
        """
        if try_times is None:
            try_times = RETRY_TIMES

        print("=" * 60)
        print("开始登录大数据平台...")
        print(f"账号: {self.username}")
        print("=" * 60)

        saved_cookie = load_cookie(self.username)
        if saved_cookie:
            self.sess.cookies = saved_cookie
            if self._check_session():
                # 保存的 Cookie 只有通过登录接口返回的 loginId 校验才可复用；
                # Cookie 存在或 HTTP 200 本身都不足以证明仍是当前用户的有效会话。
                print("✓ 使用保存的Cookie登录成功！")
                return self.sess
            else:
                print("⚠ 保存的Cookie已失效，自动清除并重新登录...")
                delete_cookie(self.username)
                self.sess = requests.Session()
                # 配置 SSL 验证（企业内部系统可能使用自签名证书）
                self.sess.verify = False
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        for i in range(try_times):
            if self._login_once(i):
                print(f"✓ 登录成功！（尝试次数: {i+1}）")
                save_cookie(self.sess.cookies, self.username)
                return self.sess
            else:
                print(f"⚠ 登录失败，尝试次数: {i+1}/{try_times}")

        print("✗ 登录失败，已达到最大尝试次数")
        return None

    def _check_session(self):
        """检查session是否有效"""
        try:
            url = f'{BASE_URL}/pro-wfm-biz-server/cas/login/info'
            res = self.sess.get(url, headers=HEADERS, timeout=TIMEOUT_SHORT)
            if res.status_code == 200:
                try:
                    data = json.loads(res.text)
                except (json.JSONDecodeError, ValueError):
                    return False
                if not isinstance(data, dict):
                    return False
                data_part = data.get('data')
                if not isinstance(data_part, dict):
                    return False
                return data_part.get('loginId') == self.username
        except (requests.RequestException, KeyError, TypeError, AttributeError, ValueError):
            pass
        return False

    def _login_once(self, attempt=0):
        """执行一次完整的登录流程"""
        try:
            if self.parent:
                return self._login_with_gui()
            return self._login_with_input(attempt)
        except Exception as e:
            print(f"✗ 登录过程出错: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _login_with_gui(self):
        """使用 GUI 对话框进行登录验证"""
        # TODO: 核心模块 core/auth.py 反向依赖 gui/login_dialog.py，导致 CLI 与 GUI 严重耦合。
        # 建议：将登录交互抽象为回调接口，由调用方（GUI/CLI）注入，核心模块不感知界面。
        from gui.login_dialog import LoginDialog
        dialog = LoginDialog(self.parent, self.username, self.password, self.sess)
        result = dialog.show()
        return result

    def _login_with_input(self, attempt=0):
        """使用命令行输入进行登录验证。

        先从登录页提取 execution 和 RSA 公钥，再循环处理图形验证码；
        验证码通过后首个完整尝试发送短信，最终以 CASTGC Cookie 判定
        登录成功。图形验证码和短信验证码是两个独立阶段，不能用前一阶段
        的 HTTP 状态码跳过后一阶段。
        """
        from core.nqi.utils.config import CAPTCHA_DIR
        from core.nqi.utils.logger import ensure_dirs

        try:
            res = self.sess.get(LOGIN_URL, headers=HEADERS)
            res.encoding = 'utf-8'
            html = etree.HTML(res.text)

            execution = html.xpath('//*[@id="fm1"]/div[4]/input[1]')[0].attrib.get('value')
            public_key = html.xpath('//*[@type="text/javascript"]/text()')[0].split('setPublicKey("')[1].split('")')[0]

            username_e = rsa_encrypt(self.username, public_key)
            password_e = rsa_encrypt(self.password, public_key)

            captcha_code = None
            for i in range(10):
                captcha_res = self.sess.get(CAPTCHA_URL)

                if i < 5:
                    captcha_code = captcha_handle(captcha_res.content, attempt * 10 + i)

                if not captcha_code:
                    ensure_dirs()
                    img_path = f'{CAPTCHA_DIR}/captcha_{attempt}_{i}.jpg'
                    with open(img_path, 'wb') as f:
                        f.write(captcha_res.content)
                    captcha_code = input(f'请查看图片 {img_path} 并输入验证码: ')

                data = {
                    'password': password_e,
                    'loginId': username_e,
                    'captcha': captcha_code,
                }
                res = self.sess.post(GET_CONFIG_URL, data=json.dumps(data), headers=HEADERS_JSON)

                if res.status_code == 200:
                    result = json.loads(res.text)
                    # 图形验证码接口的 code=1 才表示该阶段通过；普通 200
                    # 只表示服务端返回了响应，不能直接进入短信登录阶段。
                    if result.get('code') == '1':
                        print(f"✓ 图形验证码验证通过（尝试次数: {i+1}）")
                        break
                    else:
                        print(f"⚠ 图形验证码错误，重试...")
                        captcha_code = None
            else:
                print("✗ 图形验证码验证失败次数过多")
                return False

            if attempt == 0:
                data_sms = {'loginId': username_e, 'password': password_e}
                res_sms = self.sess.post(SEND_CODE_URL, data=json.dumps(data_sms), headers=HEADERS_JSON)
                result_sms = json.loads(res_sms.text)
                if result_sms.get('msg') == 'success':
                    print("✓ 短信验证码已发送")
                else:
                    print("⚠ 短信验证码发送失败")

            msg_code = input('请输入短信验证码: ')

            login_data = {
                'password': password_e,
                'username': username_e,
                'msgCode': msg_code,
                'captcha': captcha_code,
                'uuid': '',
                'execution': execution,
                '_eventId': 'submit',
                'geolocation': ''
            }

            res_login = self.sess.post(LOGIN_URL, data=login_data, headers=HEADERS)

            # CASTGC 是统一认证成功后的业务 Cookie；登录 POST 即使返回 200，
            # 没有该 Cookie 也只能视为验证码或认证流程失败。
            if self.sess.cookies.get('CASTGC'):
                return True
            else:
                print("⚠ 短信验证码错误")
                return False

        except Exception as e:
            print(f"✗ 登录过程出错: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ------------------------------------------------------------------
    # Web 端无 UI 登录接口（分阶段，供 AuthService / Web 路由调用）
    # ------------------------------------------------------------------
    def load_cookies(self) -> bool:
        """加载本地已保存的 Cookie 到当前 session。返回是否成功加载。"""
        try:
            saved = load_cookie(self.username)
            if saved:
                self.sess.cookies = saved
                return True
        except Exception as e:
            logger.warning("加载 Cookie 失败: %s", e)
        return False

    def validate_session(self) -> bool:
        """检查当前 session 的 Cookie 是否仍然有效。"""
        return self._check_session()

    def get_captcha(self) -> bytes | None:
        """拉取最新图形验证码字节流。"""
        try:
            res = self.sess.get(CAPTCHA_URL, timeout=TIMEOUT_SHORT)
            if res.status_code == 200:
                return res.content
        except Exception as e:
            logger.warning("获取图形验证码失败: %s", e)
        return None

    def login_with_credentials(self, username: str, password: str,
                               captcha: str = "", msg_code: str = "",
                               send_sms: bool = True) -> tuple[bool, str]:
        """
        面向 Web 的无 UI 分阶段登录。

        阶段一（必须）：username+password+captcha → 校验图形码 & 加密凭据
        阶段二（可选）：send_sms=True 时发送短信验证码
        阶段三（最终）：msg_code 提交 → 校验 CASTGC → 保存 Cookie

        返回 (success, message)。
        - 若只完成阶段一/二，返回 (False, "NEED_SMS_CODE")，调用方应继续提交 msg_code 重试。
        - 阶段三完成返回 (True, "...") 或 (False, "短信验证码错误")
        """
        try:
            res = self.sess.get(LOGIN_URL, headers=HEADERS, timeout=TIMEOUT_SHORT)
            res.encoding = 'utf-8'
            html = etree.HTML(res.text)

            execution = html.xpath('//*[@id="fm1"]/div[4]/input[1]')[0].attrib.get('value')
            public_key = html.xpath('//*[@type="text/javascript"]/text()')[0].split('setPublicKey("')[1].split('")')[0]

            username_e = rsa_encrypt(username, public_key)
            password_e = rsa_encrypt(password, public_key)

            if not captcha:
                return False, "请输入图形验证码"

            # ---------- 阶段一：图形验证码 ----------
            data = {'password': password_e, 'loginId': username_e, 'captcha': captcha}
            res_cfg = self.sess.post(GET_CONFIG_URL, data=json.dumps(data),
                                     headers=HEADERS_JSON, timeout=TIMEOUT_SHORT)
            result = json.loads(res_cfg.text)
            if result.get('code') != '1':
                return False, "图形验证码错误，请刷新后重试"

            # ---------- 阶段二：发送短信验证码（可选） ----------
            if send_sms and not msg_code:
                data_sms = {'loginId': username_e, 'password': password_e}
                res_sms = self.sess.post(SEND_CODE_URL, data=json.dumps(data_sms),
                                         headers=HEADERS_JSON, timeout=TIMEOUT_SHORT)
                try:
                    sms_res = json.loads(res_sms.text)
                    if sms_res.get('msg') != 'success':
                        logger.warning("短信验证码发送响应异常: %s", sms_res)
                except Exception:
                    pass
                return False, "NEED_SMS_CODE"

            if not msg_code:
                return False, "NEED_SMS_CODE"

            # ---------- 阶段三：提交短信验证码 ----------
            login_data = {
                'password': password_e,
                'username': username_e,
                'msgCode': msg_code,
                'captcha': captcha,
                'uuid': '',
                'execution': execution,
                '_eventId': 'submit',
                'geolocation': ''
            }
            self.sess.post(LOGIN_URL, data=login_data, headers=HEADERS, timeout=TIMEOUT_SHORT)

            if self.sess.cookies.get('CASTGC'):
                save_cookie(self.sess.cookies, username)
                return True, "登录成功"
            return False, "短信验证码错误"

        except Exception as e:
            logger.exception("Web 登录异常")
            return False, f"登录异常: {e}"
