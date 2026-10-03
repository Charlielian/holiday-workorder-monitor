import uvicorn
import yaml
import os
import sys
import shutil
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


def prepare_runtime():
    """打包(exe)运行时初始化。

    PyInstaller 打包后，代码内所有资源都按相对路径访问(config/web/data/logs)，
    因此这里将工作目录切换到可执行文件所在目录，并在首次运行时用示例配置
    初始化 config.yaml，保证绿色便携目录结构开箱即用。
    源码直接运行时不做任何处理。
    """
    if not getattr(sys, "frozen", False):
        return

    base_dir = os.path.dirname(os.path.abspath(sys.executable))
    os.chdir(base_dir)

    # 首次运行生成可编辑的 config.yaml (从示例配置复制)
    config_path = os.path.join(base_dir, "config", "config.yaml")
    example_path = os.path.join(base_dir, "config", "config.example.yaml")
    if not os.path.exists(config_path) and os.path.exists(example_path):
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        shutil.copyfile(example_path, config_path)
        logger.info(f"已初始化配置文件(请按需修改): {config_path}")

    # 预建运行期目录
    for rel in ("data", os.path.join("data", "exports"),
                os.path.join("data", "cookies"), os.path.join("data", "captchas"),
                "logs"):
        os.makedirs(os.path.join(base_dir, rel), exist_ok=True)


def main():
    disable_quickedit()
    prepare_runtime()

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
