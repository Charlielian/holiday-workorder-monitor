# -*- coding: utf-8 -*-
import os
import sys
import yaml
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_project_root():
    """获取当前监控项目根目录"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后: 以可执行文件所在目录作为项目根目录
        return os.path.dirname(os.path.abspath(sys.executable))
    current_file = os.path.abspath(__file__)
    # utils -> nqi -> core -> root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))

def load_config():
    root = get_project_root()
    config_file = os.path.join(root, "config", "config.yaml")
    nqi_cfg = {
        "auth": {"username": "", "password": ""},
        "paths": {
            "output_dir": os.path.join(root, "data", "exports"),
            "cookie_dir": os.path.join(root, "data", "cookies"),
            "captcha_dir": os.path.join(root, "data", "captchas"),
            "log_dir": os.path.join(root, "logs")
        },
        "server": {"base_url": "https://nqi.gmcc.net:20443"},
        "logging": {"detailed": False}
    }
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                c = yaml.safe_load(f) or {}
                nqi_part = c.get("nqi", {})
                nqi_cfg["auth"]["username"] = nqi_part.get("account", "")
                nqi_cfg["auth"]["password"] = nqi_part.get("password", "")
                nqi_cfg["server"]["base_url"] = nqi_part.get("base_url", "https://nqi.gmcc.net:20443")
        except Exception as e:
            print(f"Warning: load config failed: {e}")

    os.makedirs(nqi_cfg["paths"]["cookie_dir"], exist_ok=True)
    os.makedirs(nqi_cfg["paths"]["captcha_dir"], exist_ok=True)
    os.makedirs(nqi_cfg["paths"]["output_dir"], exist_ok=True)
    return nqi_cfg

_config = load_config()

DEFAULT_USERNAME = _config['auth']['username']
DEFAULT_PASSWORD = _config['auth']['password']
OUTPUT_DIR = _config['paths']['output_dir']
COOKIE_DIR = _config['paths']['cookie_dir']
CAPTCHA_DIR = _config['paths']['captcha_dir']
LOG_DIR = _config['paths']['log_dir']
BASE_URL = _config['server']['base_url']

LOGIN_URL = f'{BASE_URL}/cas/login?service={BASE_URL}/pro-portal/'
CAPTCHA_URL = f'{BASE_URL}/cas/captcha.jpg'
GET_CONFIG_URL = f'{BASE_URL}/cas/getConfig'
SEND_CODE_URL = f'{BASE_URL}/cas/sendCode1'
JXCX_URL = f'{BASE_URL}/pro-adhoc/adhocquery/getTable'
JXCX_COUNT_URL = f'{BASE_URL}/pro-adhoc/adhocquery/getTableCount'
JXCX_SEARCH_URL = f'{BASE_URL}/pro-adhoc/adhocquery/search'
JXCX_TABLE_URL = f'{BASE_URL}/pro-adhoc/adhocquery/getSelectTable'

MAX_SINGLE_QUERY = 500000

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Origin': BASE_URL,
    'Referer': f'{BASE_URL}/pro-adhoc/',
    'x-requested-with': 'XMLHttpRequest',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
}

HEADERS_JSON = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36',
    'Content-Type': 'application/json'
}

CONFIG_DIR = os.path.join(get_project_root(), "config")
