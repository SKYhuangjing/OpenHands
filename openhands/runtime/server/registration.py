import asyncio
import uuid
import psutil
from datetime import datetime
from typing import Optional, Dict
import httpx
from openhands.core.logger import openhands_logger as logger


class RuntimeRegistrationClient:
    """运行时注册客户端"""

    def __init__(
        self,
        proxy_url: str,
        host: str,
        port: int,
        server_id: Optional[str] = None,
        heartbeat_interval: int = 30,
    ):
        """
        初始化注册客户端

        Args:
            proxy_url: 代理服务器URL
            host: 运行时服务器主机
            port: 运行时服务器端口
            server_id: 服务器ID(可选)
            heartbeat_interval: 心跳间隔(秒)
        """
        self.proxy_url = proxy_url.rstrip('/')
        self.host = host
        self.port = port
        self.server_id = server_id or str(uuid.uuid4())
        self.heartbeat_interval = heartbeat_interval
        self.http_client = httpx.AsyncClient()
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动注册客户端"""
        try:
            # 注册服务器
            await self._register()

            # 启动心跳任务
            self._running = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            logger.info(f"运行时服务器注册成功 - ID: {self.server_id}")
        except Exception as e:
            logger.error(f"运行时服务器注册失败: {str(e)}")
            raise

    async def stop(self):
        """停止注册客户端"""
        try:
            self._running = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass

            # 注销服务器
            await self._unregister()
            await self.http_client.aclose()

            logger.info(f"运行时服务器注销成功 - ID: {self.server_id}")
        except Exception as e:
            logger.error(f"运行时服务器注销失败: {str(e)}")
            raise

    async def _register(self):
        """注册服务器"""
        registration_data = await self._get_registration_data()

        response = await self.http_client.post(
            f"{self.proxy_url}/servers/register", json=registration_data
        )

        if response.status_code != 200:
            raise Exception(f"服务器注册失败: {response.text}")

    async def _unregister(self):
        """注销服务器"""
        response = await self.http_client.post(
            f"{self.proxy_url}/servers/unregister/{self.server_id}"
        )

        if response.status_code != 200:
            raise Exception(f"服务器注销失败: {response.text}")

    async def _heartbeat_loop(self):
        """心跳循环"""
        while self._running:
            try:
                # 更新服务器容量信息
                registration_data = await self._get_registration_data()

                # 发送心跳请求
                response = await self.http_client.post(
                    f"{self.proxy_url}/servers/{self.server_id}/heartbeat",
                    json=registration_data,
                )

                if response.status_code != 200:
                    logger.warning(f"心跳请求失败: {response.text}")

            except Exception as e:
                logger.error(f"心跳更新失败: {str(e)}")

            await asyncio.sleep(self.heartbeat_interval)

    async def _get_capacity(self) -> Dict:
        """获取服务器容量信息"""
        return {
            "max_sessions": 100,  # 可配置
            "current_sessions": 0,  # 需要实现会话计数
            "cpu_usage": psutil.cpu_percent(),
            "memory_usage": psutil.virtual_memory().percent,
            "last_updated": datetime.now().isoformat(),
        }

    async def _get_registration_data(self):
        """获取注册数据"""
        return {
            "server_id": self.server_id,
            "host": self.host,
            "port": self.port,
            "status": "online",
            "capacity": await self._get_capacity(),
            "metadata": {},
        }
