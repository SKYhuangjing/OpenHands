import os
import uvicorn
from openhands.runtime.proxy.models import ProxyConfig
from openhands.runtime.proxy.server import ProxyServer
from openhands.core.logger import openhands_logger as logger


def main():
    """主函数"""
    # 加载配置
    config = ProxyConfig(
        api_key=os.environ.get("OPENHANDS_PROXY_API_KEY", "default_key"),
        health_check_interval=int(os.environ.get("HEALTH_CHECK_INTERVAL", "30")),
        session_timeout=int(os.environ.get("SESSION_TIMEOUT", "3600")),
    )

    # 创建代理服务器
    server = ProxyServer(config)

    # 启动服务
    host = os.environ.get("PROXY_HOST", "0.0.0.0")
    port = int(os.environ.get("PROXY_PORT", "8080"))

    logger.info(f"启动代理服务器 - 主机: {host}, 端口: {port}")

    # 使用 uvicorn 启动服务
    uvicorn.run(server.app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
