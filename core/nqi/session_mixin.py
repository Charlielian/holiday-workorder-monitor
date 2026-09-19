# -*- coding: utf-8 -*-
"""
Session 管理 mixin
负责即席查询模块的进入、Session 验证、取消标志管理
"""
import logging
import random
import time
import requests

from core.nqi.common import get_cookie_value
from core.nqi.utils.config import BASE_URL, HEADERS
from core.nqi.utils.constants import TIMEOUT_SHORT, TIMEOUT_LONG, RETRY_TIMES
from core.nqi.utils.retry import RetryError, retry_with_backoff

logger = logging.getLogger(__name__)


class SessionMixin:
    """Session/连接管理方法。

    JXCX 的登录态由主站 Cookie 提供，其中 CASTGC 只是进入模块的前提；
    真正可用性还要通过 urlAction.action 访问即席查询入口确认。取消标志
    只负责让查询在请求边界主动收尾，不会强行终止正在执行的 requests 调用。
    """

    def check_session_valid(self):
        """检查Session是否有效，以及JXCX模块是否可访问

        Returns:
            bool: True 表示 Session 有效且 JXCX 可用，False 表示无效或已过期。

        CASTGC 只证明仍持有统一认证票据；这里还要访问实际的
        ``urlAction.action`` 入口，成功仅代表模块入口可达，不代表后续
        count/getTable 业务请求一定成功。
        """
        import random
        try:
            # CASTGC 证明仍持有统一认证票据，但不等于 JXCX 页面可访问；
            # 因此必须访问真实的 urlAction.action 入口，而不是只检查首页。
            castgc = get_cookie_value(self.sess.cookies, 'CASTGC', domain='nqi.gmcc.net')
            if not castgc:
                castgc = get_cookie_value(self.sess.cookies, 'CASTGC')

            if not castgc:
                logger.warning("[Session检测] 未找到CASTGC cookie")
                return False

            logger.info("[Session检测] CASTGC存在，验证JXCX模块可访问性...")

            # 真正检查 JXCX 模块是否可用（而不是只检查主站首页）
            url = f'{BASE_URL}/pro-portal/pure/urlAction.action'
            params = {
                'url': 'pro-adhoc/index',
                'random': random.random(),
                '__PID': 'JXCX',
                'token': castgc
            }
            url_with_params = f"{url}?url={params['url']}&__PID={params['__PID']}&random={params['random']}&token={params['token']}"

            res = self.sess.get(url_with_params, headers=HEADERS, timeout=TIMEOUT_SHORT)

            if res.status_code == 200:
                # 该入口返回 200 即表示当前 Session 能进入 JXCX；后续真正
                # 的 getTable 请求仍可能失败，调用方不能把这里当作数据成功。
                logger.info("[Session检测] JXCX模块可访问，Session有效")
                return True
            else:
                logger.warning("[Session检测] JXCX模块不可访问，状态码: %d", res.status_code)
                return False
        except Exception as e:
            logger.warning("[Session检测] Session检测失败: %s", str(e)[:100])
            return False

    def cancel_query(self):
        """取消当前正在进行的查询"""
        logger.info("[取消请求] 收到取消查询请求")
        self._cancel_flag = True

    def reset_cancel_flag(self):
        """重置取消查询标志"""
        self._cancel_flag = False
        logger.info("[取消请求] 取消标志已重置")

    def is_cancelled(self):
        """检查是否已取消查询"""
        return self._cancel_flag

    def _enter_jxcx_once(self, timeout):
        """单次进入即席查询（供 retry_with_backoff 调用）

        Returns:
            True 成功

        Raises:
            requests.exceptions.RequestException: 网络层错误，可重试
            ValueError: 缺少CASTGC等不可恢复错误，不重试
        """
        castgc = get_cookie_value(self.sess.cookies, 'CASTGC', domain='nqi.gmcc.net')
        if not castgc:
            castgc = get_cookie_value(self.sess.cookies, 'CASTGC')

        if not castgc:
            raise ValueError("未找到CASTGC cookie")

        logger.info("CASTGC获取成功: %s...", castgc[:20] if len(castgc) >= 20 else castgc)

        url = f'{BASE_URL}/pro-portal/pure/urlAction.action'
        params = {
            'url': 'pro-adhoc/index',
            'random': random.random(),
            '__PID': 'JXCX',
            'token': castgc
        }

        url_with_params = f"{url}?url={params['url']}&__PID={params['__PID']}&random={params['random']}&token={params['token']}"
        logger.info("请求URL: %s...", url_with_params[:200] if len(url_with_params) >= 200 else url_with_params)

        cookies_before = set((c.name, c.value) for c in self.sess.cookies)

        start_time = time.time()
        res = self.sess.get(url_with_params, headers=HEADERS, timeout=timeout, allow_redirects=True)
        elapsed_time = time.time() - start_time

        logger.info("响应状态码: %s, 耗时: %.2f秒", res.status_code, elapsed_time)

        cookies_after = set((c.name, c.value) for c in self.sess.cookies)
        new_cookies = cookies_after - cookies_before
        if new_cookies:
            new_cookie_names = [name for name, _ in new_cookies]
            logger.info("检测到新Cookie: %s", new_cookie_names)

        # 成功判定采用三层启发式：重定向后的目标 URL 最可靠，其次是
        # 200 响应正文中的即席查询标识，最后兼容只更新 JSESSIONID 的旧环境。
        # 这些信号用于恢复模块状态，不代表业务数据已经返回。
        final_url = res.url if hasattr(res, 'url') else ''
        if 'pro-adhoc' in final_url or 'index' in final_url:
            logger.info("成功到达即席查询页面: %s", final_url[:100])
            self.enabled = True
            logger.info("即席查询模块初始化成功！")
            return True

        # 即使状态码不是200，检查是否包含有效的adhoc内容
        if res.status_code == 200 and ('adhoc' in res.text or 'jxcx' in res.text.lower()):
            self.enabled = True
            logger.info("即席查询模块初始化成功！（内容检测）")
            return True

        # 如果有JSESSIONID更新，也认为成功
        jsessionid = get_cookie_value(self.sess.cookies, 'JSESSIONID')
        if jsessionid:
            logger.info("检测到JSESSIONID，模块可能已初始化")
            self.enabled = True
            return True

        logger.error("进入即席查询失败，状态码: %s, 最终URL: %s", res.status_code, final_url[:100])
        raise requests.exceptions.RequestException(
            f"进入即席查询失败，状态码: {res.status_code}"
        )

    def enter_jxcx(self, retry_times=None, timeout=None):
        """进入即席查询模块（使用指数退避重试）

        Args:
            retry_times: 重试次数（默认使用常量）
            timeout: 超时时间（默认使用常量）
        """
        if retry_times is None:
            retry_times = RETRY_TIMES
        if timeout is None:
            timeout = TIMEOUT_LONG

        logger.info("========== 进入即席查询模块 ==========")

        try:
            result = retry_with_backoff(
                lambda: self._enter_jxcx_once(timeout),
                max_retries=retry_times,
                base_delay=2.0,
                exceptions=(requests.exceptions.RequestException,)
            )
            return True
        except (RetryError, ValueError) as e:
            logger.error("进入即席查询失败: %s", e)
            return False