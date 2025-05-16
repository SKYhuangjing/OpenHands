from typing import Dict, List, Optional
from openhands.runtime.proxy.models import RuntimeServer, SessionInfo, ServerStatus
from openhands.core.logger import openhands_logger as logger
from datetime import datetime


class ServerRegistry:
    """服务注册中心"""

    def __init__(self):
        self._servers: Dict[str, RuntimeServer] = {}
        self._sessions: Dict[str, SessionInfo] = {}
        self._server_sessions: Dict[str, List[str]] = {}

    async def register_server(self, server: RuntimeServer) -> bool:
        """注册服务器"""
        try:
            self._servers[server.server_id] = server
            self._server_sessions[server.server_id] = []
            logger.info(f"服务器注册成功: {server.server_id}")
            return True
        except Exception as e:
            logger.error(f"服务器注册失败: {str(e)}")
            return False

    async def unregister_server(self, server_id: str) -> bool:
        """注销服务器"""
        try:
            if server_id in self._servers:
                del self._servers[server_id]
                sessions = self._server_sessions.pop(server_id, [])
                for session_id in sessions:
                    if session_id in self._sessions:
                        del self._sessions[session_id]
                logger.info(f"服务器注销成功: {server_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"服务器注销失败: {str(e)}")
            return False

    async def get_server(self, server_id: str) -> Optional[RuntimeServer]:
        """获取服务器信息"""
        return self._servers.get(server_id)

    async def list_servers(self) -> List[RuntimeServer]:
        """列出所有服务器"""
        return list(self._servers.values())

    async def update_server_status(self, server_id: str, status: ServerStatus) -> bool:
        """更新服务器状态"""
        if server_id in self._servers:
            self._servers[server_id].status = status
            logger.info(f"服务器 {server_id} 状态更新为 {status}")
            return True
        return False

    async def register_session(self, session: SessionInfo) -> bool:
        """注册会话"""
        try:
            self._sessions[session.session_id] = session
            self._server_sessions[session.server_id].append(session.session_id)
            return True
        except Exception as e:
            logger.error(f"会话注册失败: {str(e)}")
            return False

    async def unregister_session(self, session_id: str) -> bool:
        """注销会话"""
        try:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                self._server_sessions[session.server_id].remove(session_id)
                del self._sessions[session_id]
                return True
            return False
        except Exception as e:
            logger.error(f"会话注销失败: {str(e)}")
            return False

    async def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息"""
        return self._sessions.get(session_id)

    async def list_sessions(self) -> List[SessionInfo]:
        """列出所有会话"""
        return list(self._sessions.values())

    async def get_sessions_by_server(self, server_id: str) -> List[SessionInfo]:
        """获取指定服务器上的所有会话"""
        session_ids = self._server_sessions.get(server_id, [])
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]
