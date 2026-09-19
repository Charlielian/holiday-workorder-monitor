# -*- coding: utf-8 -*-
"""
辅助函数模块
提供验证码处理、Cookie管理、数据类型转换、URL编码等辅助功能
"""

import os
import json
import http.cookiejar
import logging
from urllib.parse import quote

from core.nqi.utils.config import COOKIE_DIR, CAPTCHA_DIR, HEADERS
from core.nqi.utils.logger import ensure_dirs

logger = logging.getLogger(__name__)


class HttpCookieEncoder:
    """Cookie序列化编码器 - 使用JSON替代pickle（更安全）"""

    @staticmethod
    def encode(cookie_jar):
        """将CookieJar编码为可存储的JSON格式

        Args:
            cookie_jar: http.cookiejar.CookieJar 对象

        Returns:
            str: JSON格式的Cookie字符串
        """
        cookies = []
        for cookie in cookie_jar:
            cookie_dict = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'secure': cookie.secure,
                'expires': cookie.expires if hasattr(cookie, 'expires') else None,
            }
            cookies.append(cookie_dict)
        return json.dumps(cookies, ensure_ascii=False)

    @staticmethod
    def decode(json_str, cookie_jar=None):
        """从JSON字符串解码为CookieJar

        Args:
            json_str: JSON格式的Cookie字符串
            cookie_jar: 可选的现有CookieJar对象

        Returns:
            requests.cookies.RequestsCookieJar: 解码后的CookieJar对象（支持get方法）
        """
        from requests.cookies import RequestsCookieJar
        if cookie_jar is None:
            cookie_jar = RequestsCookieJar()

        try:
            cookies_data = json.loads(json_str)
            for cookie_dict in cookies_data:
                cookie = http.cookiejar.Cookie(
                    version=0,
                    name=cookie_dict.get('name', ''),
                    value=cookie_dict.get('value', ''),
                    port=None,
                    port_specified=False,
                    domain=cookie_dict.get('domain', ''),
                    domain_specified=bool(cookie_dict.get('domain')),
                    domain_initial_dot=False,
                    path=cookie_dict.get('path', '/'),
                    path_specified=bool(cookie_dict.get('path')),
                    secure=cookie_dict.get('secure', False),
                    expires=cookie_dict.get('expires'),
                    discard=True,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False
                )
                cookie_jar.set_cookie(cookie)
        except (json.JSONDecodeError, KeyError) as e:
            # 旧版本pickle格式的cookie无法解析，返回空的CookieJar
            pass

        return cookie_jar


def save_cookie(cookie_jar, username):
    """保存cookie到文件（JSON格式，更安全）

    Args:
        cookie_jar: http.cookiejar.CookieJar 对象
        username: 用户名（用于文件名）
    """
    ensure_dirs()
    filepath = os.path.join(COOKIE_DIR, f'{username}.json')

    # 同时保留旧版本兼容性（.pkl文件会被覆盖）
    old_filepath = os.path.join(COOKIE_DIR, f'{username}.pkl')
    if os.path.exists(old_filepath):
        try:
            os.remove(old_filepath)
        except OSError:
            pass

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(HttpCookieEncoder.encode(cookie_jar))


def load_cookie(username):
    """从文件加载cookie（支持JSON和旧版pickle格式）

    Args:
        username: 用户名

    Returns:
        requests.cookies.RequestsCookieJar: CookieJar对象，失败返回None
    """
    # 先尝试新格式 JSON
    json_filepath = os.path.join(COOKIE_DIR, f'{username}.json')
    if os.path.exists(json_filepath):
        try:
            with open(json_filepath, 'r', encoding='utf-8') as f:
                json_str = f.read()
            return HttpCookieEncoder.decode(json_str)
        except Exception:
            return None

    # 兼容旧格式 pickle
    pkl_filepath = os.path.join(COOKIE_DIR, f'{username}.pkl')
    if os.path.exists(pkl_filepath):
        try:
            import pickle
            from requests.cookies import RequestsCookieJar
            with open(pkl_filepath, 'rb') as f:
                cookie_jar = pickle.load(f)
            # 转换为 RequestsCookieJar（支持 get 方法）
            if not isinstance(cookie_jar, RequestsCookieJar):
                new_jar = RequestsCookieJar()
                for cookie in cookie_jar:
                    new_jar.set_cookie(cookie)
                cookie_jar = new_jar
            # 迁移到新格式
            save_cookie(cookie_jar, username)
            return cookie_jar
        except Exception:
            return None

    return None


def delete_cookie(username):
    """删除保存的cookie文件

    Args:
        username: 用户名
    """
    # 删除 JSON 格式的cookie
    json_filepath = os.path.join(COOKIE_DIR, f'{username}.json')
    if os.path.exists(json_filepath):
        try:
            os.remove(json_filepath)
            logger.info(f"已删除过期的Cookie文件: {json_filepath}")
        except OSError as e:
            logger.warning(f"删除Cookie文件失败: {e}")

    # 删除旧版 pickle 格式的cookie
    pkl_filepath = os.path.join(COOKIE_DIR, f'{username}.pkl')
    if os.path.exists(pkl_filepath):
        try:
            os.remove(pkl_filepath)
            logger.info(f"已删除过期的Cookie文件: {pkl_filepath}")
        except OSError as e:
            logger.warning(f"删除Cookie文件失败: {e}")


def captcha_handle(img_content, attempt=1):
    """验证码处理（OCR识别）"""
    try:
        from PIL import Image, ImageFilter
        import pytesseract
        from io import BytesIO

        bytes_stream = BytesIO(img_content)
        img = Image.open(bytes_stream)
        img_gray = img.convert('L')
        img_black_white = img_gray.point(lambda x: 255 if x > 85 else 0)
        img_qucao = img_black_white.filter(ImageFilter.SMOOTH_MORE)
        img = img_qucao.convert('RGB')

        config = '--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
        result = pytesseract.image_to_string(img, config=config)[0:4].replace('\n', '')
        return result
    except Exception as e:
        print(f"验证码OCR识别失败: {e}")
        ensure_dirs()
        img_path = os.path.join(CAPTCHA_DIR, f'captcha_{attempt}.jpg')
        with open(img_path, 'wb') as f:
            f.write(img_content)
        return None


def encode_payload(payload):
    """URL编码payload"""
    from urllib.parse import urlencode
    return urlencode(payload, safe='', encoding='utf-8')


def datatype_to_code(datatype_str):
    """将数据类型字符串转换为浏览器使用的数字代码

    浏览器使用的格式:
    - "2" = bigint/timestamp/integer
    - "1" = character varying/varchar/character
    - "decimal" = 小数类型
    - "boolean" = 布尔类型（保持原样）

    Args:
        datatype_str: 数据类型字符串

    Returns:
        转换后的值（可能是数字代码或字符串）
    """
    if not datatype_str:
        return datatype_str

    datatype_lower = datatype_str.lower()

    # bigint, timestamp, integer 类型使用 "2"
    # 注意：有些表的city字段在API中返回的是integer类型，需要转为"2"
    if datatype_lower in ('bigint', 'timestamp', 'integer', 'int', '2'):
        return '2'

    # character varying, varchar, character 使用 "1"
    if datatype_lower in ('character varying', 'character', 'varchar', 'text', '1'):
        return '1'

    # boolean 类型使用 "1"（与浏览器一致）
    if datatype_lower in ('boolean',):
        return '1'

    # decimal 类型保持原样
    if datatype_lower in ('decimal', 'numeric', 'double', 'float', 'real'):
        return datatype_str

    # 其他类型保持原样
    return datatype_str


def _encode_columns_param(columns):
    """编码 DataTables columns 参数为扁平 URL 格式"""
    col_parts = []
    for i, col in enumerate(columns):
        if isinstance(col, str):
            col_parts.append(f'columns[{i}]={quote(col)}')
            continue
        try:
            for sub_key, sub_val in col.items():
                if isinstance(sub_val, dict):
                    for ss_key, ss_val in sub_val.items():
                        col_parts.append(f'columns[{i}][{sub_key}][{ss_key}]={quote(str(ss_val), safe="")}')
                else:
                    col_parts.append(f'columns[{i}][{sub_key}]={quote(str(sub_val), safe="")}')
        except AttributeError:
            continue
    return '&'.join(col_parts)


def _encode_order_param(order):
    """编码 DataTables order 参数为扁平 URL 格式"""
    order_parts = []
    for i, ord_item in enumerate(order):
        for sub_key, sub_val in ord_item.items():
            order_parts.append(f'order[{i}][{sub_key}]={quote(str(sub_val), safe="")}')
    return '&'.join(order_parts)


def _encode_search_param(search):
    """编码 DataTables search 参数为扁平 URL 格式"""
    search_parts = []
    for sub_key, sub_val in search.items():
        search_parts.append(f'search[{sub_key}]={quote(str(sub_val), safe="")}')
    return '&'.join(search_parts)


def _encode_json_param(key, value):
    """编码 JSON 序列化参数（result / where）"""
    json_str = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return quote(key) + '=' + quote(json_str, safe='/:= ')


def encode_datatables_payload(payload):
    """URL编码payload - 使用DataTables标准格式，与浏览器完全一致

    处理 columns / order / search（DataTables 扁平格式）以及 result / where（JSON 序列化）。

    Args:
        payload: 请求参数dict

    Returns:
        str: URL编码后的请求体
    """
    out_list = []
    for key in payload:
        if key == 'columns':
            if isinstance(payload[key], str):
                out_list.append(quote(key) + '=' + quote(payload[key]))
                continue
            elif not isinstance(payload[key], list):
                out_list.append(quote(key) + '=' + quote(str(payload[key])))
                continue
            out_list.append(_encode_columns_param(payload[key]))
        elif key == 'order':
            out_list.append(_encode_order_param(payload[key]))
        elif key == 'search':
            out_list.append(_encode_search_param(payload[key]))
        elif key in ['result', 'where']:
            out_list.append(_encode_json_param(key, payload[key]))
        elif isinstance(payload[key], int):
            out_list.append(quote(key) + '=' + str(payload[key]))
        else:
            out_list.append(quote(key) + '=' + quote(str(payload[key]) if payload[key] is not None else ''))
    return '&'.join(out_list)


def get_timestamp():
    """获取当前时间戳（毫秒）"""
    import time
    return str(int(time.time() * 1000))
