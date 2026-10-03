import uvicorn
import yaml
import os
import sys
import ctypes
from loguru import logger


def disable_quickedit():
    """禁用 Windows 控制台快速编辑模式(QuickEdit)，防止鼠标点击选中文本导致
    进程输出阻塞、服务假死。非 Windows 平台或调用失败时静默跳过。"""
    if sys.platform != "win32":
        return
    try:
        import msvcrt  # Windows 专有模块，仅在 win32 分支内导入
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080

        h_stdin = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
            return

        # 关闭快速编辑模式: 先置 ENABLE_EXTENDED_FLAGS 才允许改写该位
        new_mode = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
        if kernel32.SetConsoleMode(h_stdin, new_mode):
            logger.info("已禁用控制台快速编辑模式(防止鼠标点击导致输出阻塞)")
    except Exception as e:
        logger.warning(f"禁用控制台快速编辑模式失败(不影响服务运行): {e}")


def main():
    disable_quickedit()

    config_path = "config/config.yaml"
    host = "0.0.0.0"
    port = 8000
    debug = True

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            server_cfg = cfg.get("server", {})
            host = server_cfg.get("host", host)
            port = server_cfg.get("port", port)
            debug = server_cfg.get("debug", debug)

    logger.info(f"正在启动节假日监控工单监控服务: http://127.0.0.1:{port}")
    uvicorn.run("web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
