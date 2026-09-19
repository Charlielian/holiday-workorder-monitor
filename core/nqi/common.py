# -*- coding: utf-8 -*-
"""
公共模块
提供 query 模块共享的异常类与工具函数
"""
import logging
import requests

logger = logging.getLogger(__name__)


class CountRequestError(Exception):
    """Count请求不可恢复错误（不重试，直接返回0）"""
    pass


class CountNotFoundError(Exception):
    """Count字段未找到（可重试）"""
    pass


class SessionExpiredError(Exception):
    """Session过期（可重试，需重新进入）"""
    pass


class DataFetchError(Exception):
    """单次数据请求失败。"""
    pass


class BatchFetchError(DataFetchError):
    """分批数据请求失败，结果不完整。"""

    def __init__(self, batch_index, start, length, message):
        self.batch_index = batch_index
        self.start = start
        self.length = length
        super().__init__(f"第{batch_index}批请求失败 (start={start}, length={length}): {message}")


def get_cookie_value(cookie_jar, name, domain=None):
    """安全获取cookie值，处理多个同名cookie的情况

    Args:
        cookie_jar: requests的CookieJar对象
        name: cookie名称
        domain: 可选的domain过滤

    Returns:
        cookie值，如果不存在则返回None
    """
    try:
        # 先尝试直接获取
        if domain:
            value = cookie_jar.get(name, domain=domain)
            if value:
                return value
        # 尝试不带domain获取
        return cookie_jar.get(name)
    except requests.cookies.CookieConflictError:
        # 存在多个同名cookie，手动遍历获取第一个
        cookies = [c for c in cookie_jar if c.name == name]
        if cookies:
            # 如果指定了domain，优先返回匹配domain的
            if domain:
                for c in cookies:
                    if c.domain == domain or (domain in c.domain):
                        return c.value
            return cookies[0].value
        return None


def convert_where_conditions(conditions):
    """将调用方条件转换为 NQI ``where`` 协议。

    调用方可继续传入旧版已经包含 ``feild/symbol/val`` 的条件，也可使用
    更易读的 ``field/operator/value`` 形式；旧格式必须原样保留，因为
    ``feild`` 是上游接口的既有拼写。日期边界会补齐为当天的起止时间，
    并把 ``<=`` 改写为开区间 ``<``，这是浏览器请求与后端时间比较规则的
    兼容处理，而不是通用的 Python 条件求值。

    Args:
        conditions: 新格式条件列表，或旧格式 API 条件列表。

    Returns:
        转换后的条件列表；空输入返回空列表，无法识别的条件仅记录警告并跳过。
    """
    if not conditions:
        return []

    result = []
    for cond in conditions:
        # 判断是旧格式还是新格式
        if 'feild' in cond and 'symbol' in cond and 'val' in cond:
            # 已经是旧格式，直接使用
            result.append(cond)
        elif 'field' in cond and 'operator' in cond and 'value' in cond:
            # 新格式，需要转换
            where_con = 'and'  # 所有条件均为 AND 连接

            # 根据字段名和操作符推断datatype
            field = cond['field']
            operator = cond['operator']
            value = cond['value']

            # 推断数据类型
            datatype = 'character'  # 默认
            if 'time' in field.lower() or 'date' in field.lower():
                datatype = 'timestamp'
                # 时间格式使用空格分隔符（与浏览器保持一致）
                if '+' in value:
                    value = value.replace('+', ' ')
                elif ' ' not in value and '+' not in value:
                    if operator in ('>=', '>'):
                        value = value + ' 00:00:00'
                    elif operator in ('<=', '<', '='):
                        value = value + ' 23:59:59'
                if operator == '<=':
                    operator = '<'
            elif operator.lower() == 'in':
                datatype = 'character'

            symbol = operator

            result.append({
                'datatype': datatype,
                'feild': field,
                'feildName': '',
                'symbol': symbol,
                'val': value,
                'whereCon': where_con,
                'query': True
            })
        else:
            logger.warning("无法识别的条件格式: %s", cond)

    return result