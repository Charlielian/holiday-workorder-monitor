# -*- coding: utf-8 -*-
"""
日志系统模块
提供同时输出到控制台和日志文件的日志功能

特性:
- 按报表名称生成独立日志文件
- 按日期分目录管理日志
- 支持日志轮转
- 区分界面显示（简洁）和后台日志（详细）
- 日志脱敏（敏感信息保护）
"""

import sys
import logging
import io
import os
import re
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path


class LogSanitizer:
    """日志脱敏过滤器"""

    # 敏感字段模式
    SENSITIVE_PATTERNS = [
        # 密码相关
        (re.compile(r'(password["\']?\s*[:=]\s*["\']?)([^"\'\s,&]+)', re.IGNORECASE), r'\1***'),
        (re.compile(r'("password"\s*:\s*)("[^"]+")'), r'\1"***"'),
        (re.compile(r'(password["\']?\s*:\s*)[^\s,\}]+'), r'\1***'),

        # 密钥相关
        (re.compile(r'(aes_key["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),
        (re.compile(r'(secret["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),
        (re.compile(r'(api_key["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),

        # Token/Cookie相关
        (re.compile(r'(CASTGC["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),
        (re.compile(r'(token["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),
        (re.compile(r'(cookie["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),

        # 加密密码（RSA加密后的密码）
        (re.compile(r'("password_e"\s*:\s*)("[^"]+")'), r'\1"***"'),
        (re.compile(r'(password_e["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),

        # 短信验证码
        (re.compile(r'(msgCode["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', re.IGNORECASE), r'\1***'),
    ]

    @classmethod
    def sanitize(cls, message):
        """对日志消息进行脱敏处理

        Args:
            message: 原始日志消息

        Returns:
            str: 脱敏后的消息
        """
        if not message:
            return message

        result = message
        for pattern, replacement in cls.SENSITIVE_PATTERNS:
            result = pattern.sub(replacement, result)

        return result


class SanitizedFormatter(logging.Formatter):
    """带脱敏功能的日志格式化器"""

    def format(self, record):
        """格式化日志记录"""
        # 对消息进行脱敏
        if record.msg:
            record.msg = LogSanitizer.sanitize(str(record.msg))
        # 对args进行脱敏
        if record.args:
            record.args = tuple(
                LogSanitizer.sanitize(str(arg)) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return super().format(record)


class ReportLogger:
    """按报表分类的日志记录器"""

    _instance = None
    _loggers = {}  # 缓存已创建的logger

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.handlers = {}
        self.log_dir = None
        self.console_handler = None
        self.console_log_level = logging.INFO  # 界面日志级别

    def _get_date_dir(self):
        """获取当日志目录，不存在则创建"""
        today = datetime.now().strftime('%Y-%m-%d')
        # 如果 log_dir 未初始化，使用临时目录
        base_dir = self.log_dir if self.log_dir else os.path.join(os.path.expanduser('~'), '.nqi_logs')
        date_dir = os.path.join(base_dir, today)
        if not os.path.exists(date_dir):
            os.makedirs(date_dir, exist_ok=True)
        return date_dir

    def _get_report_log_path(self, report_name):
        """获取报表日志文件路径"""
        safe_name = self._sanitize_filename(report_name)
        date_dir = self._get_date_dir()
        return os.path.join(date_dir, f"{safe_name}.log")

    def _sanitize_filename(self, filename):
        """清理文件名，移除非法字符"""
        illegal_chars = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
        safe = filename
        for char in illegal_chars:
            safe = safe.replace(char, '_')
        return safe

    def setup_logging(self, log_dir, console=True, console_level=logging.INFO):
        """设置日志系统

        Args:
            log_dir: 日志根目录
            console: 是否启用控制台输出
            console_level: 界面日志级别（默认INFO）
        """
        self.log_dir = log_dir
        self.console_log_level = console_level
        os.makedirs(log_dir, exist_ok=True)

        print(f"[日志系统] 日志目录: {log_dir}")
        print(f"[日志系统] 界面日志级别: {logging.getLevelName(console_level)}")

    def get_logger(self, report_name, file_level=logging.DEBUG, console_level=None):
        """获取指定报表的日志记录器

        Args:
            report_name: 报表名称
            file_level: 文件日志级别（默认DEBUG，详细信息）
            console_level: 界面日志级别（默认使用全局设置）

        Returns:
            logging.Logger: 日志记录器实例
        """
        if report_name in self._loggers:
            return self._loggers[report_name]

        # 创建新的logger
        logger = logging.getLogger(report_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        # 界面日志处理器（简洁）
        if console_level is None:
            console_level = self.console_log_level

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_formatter = logging.Formatter(
            '%(message)s',  # 界面只显示消息
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # 文件日志处理器（详细）- 使用脱敏格式化器
        log_path = self._get_report_log_path(report_name)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(file_level)
        file_formatter = SanitizedFormatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        self._loggers[report_name] = logger
        self.handlers[report_name] = {'console': console_handler, 'file': file_handler}

        return logger

    def close_all(self):
        """关闭所有日志处理器"""
        for name, handlers in self.handlers.items():
            for handler in handlers.values():
                handler.close()
                logging.getLogger(name).removeHandler(handler)
        self.handlers.clear()
        self._loggers.clear()


# 全局实例
_report_logger = ReportLogger()


def setup_report_logging(log_dir, console=True, console_level=logging.INFO):
    """设置日志系统

    Args:
        log_dir: 日志根目录
        console: 是否启用控制台输出
        console_level: 界面日志级别（默认INFO，仅显示普通信息）
    """
    _report_logger.setup_logging(log_dir, console, console_level)


def get_report_logger(report_name, file_level=logging.DEBUG, console_level=None):
    """获取指定报表的日志记录器

    Args:
        report_name: 报表名称
        file_level: 文件日志级别（默认DEBUG）
        console_level: 界面日志级别（默认None，使用全局设置）

    Returns:
        logging.Logger: 日志记录器实例
    """
    return _report_logger.get_logger(report_name, file_level, console_level)


def close_all_loggers():
    """关闭所有日志记录器"""
    _report_logger.close_all()


def ensure_dirs():
    """确保必要的目录存在"""
    from core.nqi.utils.config import OUTPUT_DIR, COOKIE_DIR, CAPTCHA_DIR, LOG_DIR
    for dir_path in [OUTPUT_DIR, COOKIE_DIR, CAPTCHA_DIR, LOG_DIR]:
        os.makedirs(dir_path, exist_ok=True)


# ========== 便捷日志函数 ==========

def log_info(report_name, message):
    """记录INFO级别日志（界面可见）"""
    logger = get_report_logger(report_name)
    logger.info(message)


def log_warning(report_name, message):
    """记录WARNING级别日志（界面可见）"""
    logger = get_report_logger(report_name)
    logger.warning(message)


def log_error(report_name, message):
    """记录ERROR级别日志（界面可见）"""
    logger = get_report_logger(report_name)
    logger.error(message)


def log_debug(report_name, message):
    """记录DEBUG级别日志（仅文件）"""
    logger = get_report_logger(report_name)
    logger.debug(message)


def log_console_only(message):
    """仅输出到界面控制台（不写入文件）"""
    print(message)


def sanitize_log(message):
    """对日志消息进行脱敏处理

    Args:
        message: 原始消息

    Returns:
        str: 脱敏后的消息
    """
    return LogSanitizer.sanitize(message)


# ========== 兼容旧API ==========

_log_file_path = None
_original_print = print


def set_log_file(filepath, max_bytes=10*1024*1024, backup_count=5):
    """设置日志文件路径并启用日志记录（兼容旧API）"""
    global _log_file_path
    _log_file_path = filepath

    log_dir = os.path.dirname(filepath)
    report_name = os.path.splitext(os.path.basename(filepath))[0]

    _report_logger.setup_logging(log_dir, console=True)
    _report_logger.get_logger(report_name)

    _log_file_path = filepath


def debug_print(*args, **kwargs):
    """增强的print函数，同时输出到控制台和日志文件

    注意：不会全局替换内置print，仅在需要时显式调用。
    旧版 set_log_file + 自动猴子补丁 print 的方式已被移除，
    避免影响第三方库的 print 输出。
    """
    output = io.StringIO()
    kwargs_copy = {k: v for k, v in kwargs.items()}
    kwargs_copy['file'] = output
    kwargs_copy['end'] = kwargs_copy.get('end', '\n')
    _original_print(*args, **kwargs_copy)
    message = output.getvalue()
    output.close()

    _original_print(*args, **kwargs)

    if _log_file_path:
        with open(_log_file_path, 'a', encoding='utf-8') as f:
            f.write(message)
