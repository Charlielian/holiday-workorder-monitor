# -*- coding: utf-8 -*-
"""
常量配置模块
集中管理应用程序中的魔法数字和配置常量
"""

# ========== 网络请求超时配置 ==========
# 基础超时时间（秒）
TIMEOUT_SHORT = 10      # 短超时：Session检测等轻量级请求
TIMEOUT_MEDIUM = 30     # 中等超时：字段配置获取等
TIMEOUT_LONG = 60       # 长超时：进入即席查询等
TIMEOUT_EXTRA_LONG = 180  # 超长超时：获取数据总数、大批量查询

# ========== 重试配置 ==========
RETRY_TIMES = 3          # 默认重试次数
RETRY_DELAY = 5          # 重试间隔（秒）

# ========== 批次查询配置 ==========
BATCH_THRESHOLD = 10000   # 超过此数量使用分批查询
MAX_SINGLE_QUERY = 500000  # 单次最大查询数量

# 批次大小配置（从大到小）
BATCH_SIZES = [50000, 10000, 5000, 2000, 1000, 500, 200]

# 批次对应的超时时间（秒）
BATCH_TIMEOUTS = {
    50000: 300,  # 5万条 超时5分钟
    10000: 180, # 1万条 超时3分钟
    5000: 120,  # 5千条 超时2分钟
    2000: 90,   # 2千条 超时1.5分钟
    1000: 60,   # 1千条 超时1分钟
    500: 45,    # 5百条 超时45秒
    200: 30,    # 2百条 超时30秒
}

# ========== 并发配置 ==========
MAX_PARALLEL_QUERIES = 3      # 并行查询最大并发数（query_tables_parallel）
DEFAULT_THREAD_POOL_SIZE = 8  # 多任务线程池默认大小（main_window 任务级并发）
DEFAULT_THREAD_POOL_SMALL = 6 # 小任务线程池大小（main_window 表格并行）

# ========== Excel导出配置 ==========
EXCEL_BATCH_SIZE = 5000   # 流式导出每批写入的行数
EXCEL_SAVE_INTERVAL = 10000  # 定期保存间隔（行数）
EXCEL_DEFAULT_HEADER_COLOR = '165DFF'  # 默认表头颜色
EXCEL_DEFAULT_FONT_SIZE = 11  # 默认字体大小

# ========== 授权配置 ==========
UNLIMIT_TIMESTAMP = 0    # 永久授权时间戳
TIME_TAMPER_TOLERANCE = 86400 * 365  # 时间回拨容忍度（1年）

# ========== API配置 ==========
DEFAULT_DRAW = 1         # DataTables请求的draw参数
DEFAULT_START = 0        # 默认起始位置
DEFAULT_LENGTH = 200     # 默认每页长度

# ========== 时间格式配置 ==========
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'  # 日期时间格式
DATE_FORMAT = '%Y-%m-%d'           # 日期格式
