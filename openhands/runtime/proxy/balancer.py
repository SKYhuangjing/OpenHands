from typing import Optional
from openhands.runtime.proxy.models import RuntimeServer, ServerStatus
from openhands.runtime.proxy.registry import ServerRegistry


class LoadBalancer:
    """负载均衡器"""

    def __init__(self, registry: ServerRegistry):
        self.registry = registry

    async def select_server(self) -> Optional[RuntimeServer]:
        """选择最佳服务器"""
        servers = await self.registry.list_servers()

        # 过滤出在线的服务器
        available_servers = [s for s in servers if s.status == ServerStatus.ONLINE]

        if not available_servers:
            return None

        # 按负载率排序
        sorted_servers = sorted(
            available_servers,
            key=lambda s: s.capacity.current_sessions / s.capacity.max_sessions,
        )

        # 返回负载最低的服务器
        return sorted_servers[0] if sorted_servers else None
