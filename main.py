import uvicorn
import yaml
import os
from loguru import logger

def main():
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

    logger.info(f"正在启动性能超频小区监控服务: http://127.0.0.1:{port}")
    uvicorn.run("web.app:app", host=host, port=port, reload=False)

if __name__ == "__main__":
    main()
