from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
import httpx
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from openhands.runtime.proxy.models import (
    ProxyConfig,
    RuntimeServer,
    ServerStatus,
    SessionInfo,
    ServerCapacity,
)
from openhands.runtime.proxy.registry import ServerRegistry
from openhands.runtime.proxy.balancer import LoadBalancer
from openhands.core.logger import openhands_logger as logger


@asynccontextmanager
async def lifespan(app: FastAPI, proxy_server):
    """FastAPI 生命周期事件处理器"""
    # 启动事件
    await proxy_server.start()
    yield
    # 关闭事件
    await proxy_server.stop()


class ProxyServer:
    """代理服务器"""

    def __init__(self, config: ProxyConfig):
        self.config = config
        self.app = FastAPI(
            title="OpenHands 代理服务器",
            description="""
            OpenHands 代理服务器提供以下功能:
            
            * 服务器注册与管理
            * 会话管理与路由
            * 负载均衡
            * 请求转发
            * 健康检查
            """,
            version="1.0.0",
            docs_url="/docs",
            redoc_url="/redoc",
            openapi_url="/openapi.json",
            lifespan=lambda app: lifespan(app, self),
        )
        self.registry = ServerRegistry()
        self.balancer = LoadBalancer(self.registry)
        self.http_client = httpx.AsyncClient(timeout=None)

        # 心跳超时配置
        self.heartbeat_timeout = 60  # 心跳超时时间(秒)
        self.heartbeat_check_interval = 30  # 心跳检查间隔(秒)
        self._heartbeat_check_task = None
        self._running = False

        self._setup_middleware()
        self._setup_routes()
        self._setup_docs()

    def _setup_middleware(self):
        """设置中间件"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _setup_routes(self):
        """设置路由"""
        # 服务器管理接口
        self.app.post("/servers/register", tags=["服务器管理"])(self.register_server)
        self.app.post("/servers/unregister/{server_id}", tags=["服务器管理"])(
            self.unregister_server
        )
        self.app.get("/servers", tags=["服务器管理"])(self.list_servers)

        # 添加心跳路由
        self.app.post("/servers/{server_id}/heartbeat", tags=["服务器管理"])(
            self.handle_heartbeat
        )

        # 会话管理接口
        self.app.get("/servers/sessions", tags=["会话管理"])(self.list_sessions)
        self.app.get("/servers/sessions/{session_id}", tags=["会话管理"])(
            self.get_session
        )

        # 代理转发接口
        self.app.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE"],
            tags=["代理转发"],
        )(self.proxy_request)

    def _setup_docs(self):
        """配置 Swagger 文档"""

        # 自定义OpenAPI文档
        def custom_openapi():
            if self.app.openapi_schema:
                return self.app.openapi_schema

            openapi_schema = get_openapi(
                title=self.app.title,
                version=self.app.version,
                description=self.app.description,
                routes=self.app.routes,
            )

            self.app.openapi_schema = openapi_schema
            return self.app.openapi_schema

        self.app.openapi = custom_openapi

    async def start(self):
        """启动代理服务器"""
        if self._running:
            return

        self._running = True
        # 启动心跳检查任务
        self._heartbeat_check_task = asyncio.create_task(self._heartbeat_check_loop())
        logger.info("代理服务器启动成功")

    async def stop(self):
        """停止代理服务器"""
        if not self._running:
            return

        self._running = False
        # 取消心跳检查任务
        if self._heartbeat_check_task and not self._heartbeat_check_task.done():
            self._heartbeat_check_task.cancel()
            try:
                await self._heartbeat_check_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"停止心跳检查任务时出错: {str(e)}")

        # 关闭 HTTP 客户端
        await self.http_client.aclose()

        logger.info("代理服务器停止成功")

    async def _heartbeat_check_loop(self):
        """心跳检查循环"""
        try:
            while self._running:
                try:
                    await self._check_heartbeats()
                    await asyncio.sleep(self.heartbeat_check_interval)
                except asyncio.CancelledError:
                    logger.info("心跳检查任务被取消")
                    break
                except Exception as e:
                    logger.error(f"心跳检查失败: {str(e)}")
                    await asyncio.sleep(self.heartbeat_check_interval)
        except asyncio.CancelledError:
            logger.info("心跳检查任务被取消")
        except Exception as e:
            logger.error(f"心跳检查循环出错: {str(e)}")
        finally:
            logger.info("心跳检查任务结束")

    async def _check_heartbeats(self):
        """检查所有服务器的心跳状态"""
        now = datetime.now()
        servers = await self.registry.list_servers()

        for server in servers:
            # 跳过已经离线的服务器
            if server.status == ServerStatus.OFFLINE:
                continue

            # 计算最后心跳时间到现在的时间差
            last_heartbeat = server.capacity.last_updated
            time_since_last_heartbeat = (now - last_heartbeat).total_seconds()

            # 如果超过超时时间，将服务器标记为离线
            if time_since_last_heartbeat > self.heartbeat_timeout:
                logger.warning(f"服务器 {server.server_id} 心跳超时，自动标记为离线")
                await self.registry.update_server_status(
                    server.server_id, ServerStatus.OFFLINE
                )

                # 通知相关会话服务器已离线
                await self._notify_server_offline(server.server_id)

    async def _notify_server_offline(self, server_id: str):
        """通知服务器离线"""
        # 这里可以实现通知逻辑，例如发送事件或调用回调函数
        logger.info(f"服务器 {server_id} 已离线, 其服务暂不可用")

    async def register_server(self, server: RuntimeServer):
        """注册服务器"""
        success = await self.registry.register_server(server)
        if not success:
            raise HTTPException(status_code=400, detail="服务器注册失败")
        return {"status": "success", "message": "服务器注册成功"}

    async def unregister_server(self, server_id: str):
        """注销服务器"""
        success = await self.registry.unregister_server(server_id)
        if not success:
            raise HTTPException(status_code=404, detail="服务器不存在")
        return {"status": "success", "message": "服务器注销成功"}

    async def list_servers(self):
        """列出所有服务器"""
        servers = await self.registry.list_servers()
        return {"status": "success", "data": {"servers": servers}}

    async def list_sessions(self):
        """列出所有会话"""
        sessions = await self.registry.list_sessions()
        return {"status": "success", "data": {"sessions": sessions}}

    async def get_session(self, session_id: str):
        """获取会话信息"""
        session = await self.registry.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"status": "success", "data": session}

    async def proxy_request(
        self,
        request: Request,
        path: str,
        x_forwarded_for: str = Header(None),
    ):
        """代理请求转发"""
        try:
            session_id = request.headers.get("session_id")
            # 1. 根据session_id获取目标服务器
            target_server = None
            if session_id:
                session = await self.registry.get_session(session_id)
                if session:
                    target_server = await self.registry.get_server(session.server_id)

            # 2. 如果没有找到目标服务器，使用负载均衡选择一个
            if not target_server:
                target_server = await self.balancer.select_server()
                if not target_server:
                    raise HTTPException(status_code=503, detail="没有可用的服务器")
                logger.info(f"使用负载均衡选择服务器: {target_server.server_id}")
                await self.registry.register_session(
                    SessionInfo(
                        session_id=session_id,
                        server_id=target_server.server_id,
                    )
                )

            # 3. 构建目标URL
            target_url = f"http://{target_server.host}:{target_server.port}/{path}"

            # 4. 转发请求
            headers = dict(request.headers)
            headers["X-Forwarded-For"] = x_forwarded_for or request.client.host

            try:
                response = await self.http_client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=await request.body(),
                    timeout=self.http_client.timeout,
                )
                return response.json()
            except httpx.HTTPError as e:
                error_msg = f"代理请求失败: {str(e)}"
                logger.error(error_msg)
                raise HTTPException(
                    status_code=(
                        e.response.status_code if hasattr(e, 'response') else 500
                    ),
                    detail=error_msg,
                )
            except Exception as e:
                error_msg = f"代理请求异常: {str(e)}"
                logger.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)

        except HTTPException:
            raise
        except Exception as e:
            error_msg = f"代理请求处理失败: {str(e)}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)

    async def handle_heartbeat(self, server_id: str, runtime_server: RuntimeServer):
        """处理服务器心跳"""
        server = await self.registry.get_server(runtime_server.server_id)
        if not server:
            # 服务器不存在，自动注册
            success = await self.registry.register_server(runtime_server)
            if not success:
                raise HTTPException(status_code=400, detail="服务器自动注册失败")
            logger.info(f"服务器 {server_id} 自动注册成功")
        else:
            # 更新服务器容量信息
            server.capacity = runtime_server.capacity
            # 如果服务器之前是离线状态，现在收到心跳，将其标记为在线
            if server.status == ServerStatus.OFFLINE:
                await self.registry.update_server_status(server_id, ServerStatus.ONLINE)
                logger.info(f"服务器 {server_id} 重新上线")

        return {"status": "success", "message": "心跳更新成功"}
