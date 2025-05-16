import asyncio
import json
import os
import sys
import uuid
import time
from typing import Dict, List, Any
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Header,
    Request,
    APIRouter,
    Body,
    Path,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
import uvicorn
from contextlib import asynccontextmanager

from openhands.core.config.utils import load_app_config
from openhands.core.logger import openhands_logger as logger
from openhands.storage import get_file_store
from openhands.events import EventStream
from openhands.runtime.impl.docker.docker_runtime import DockerRuntime
from openhands.runtime.plugins import PluginRequirement
from openhands.utils.async_utils import call_sync_from_async
from openhands.runtime.server.registration import RuntimeRegistrationClient


# 建议将配置相关的常量抽取出来
class RuntimeConstants:
    """运行时常量配置"""

    DEFAULT_IMAGE = "docker.all-hands.dev/all-hands-ai/runtime:0.28-nikolaik"
    DEFAULT_PLUGINS = ["agent_skills", "jupyter", "vscode"]
    STORE_PATH = "/Users/sky/Downloads/OpenHands"
    STORE_FILE_NAME = "/runtime_state.json"


# 配置模型
class RuntimeConfig(BaseModel):
    """运行时配置模型"""

    max_idle_runtimes: int = Field(default=2, description="最大空闲运行时数量")
    api_key: str = Field(default="1data", description="API密钥")
    session_api_key: str = Field(default="", description="会话API密钥")


# 响应模型
class ResponseModel(BaseModel):
    """统一响应模型"""

    status: str
    data: Any = None
    message: str = ""
    error: str = ""


# 请求模型
class StartRuntimeRequest(BaseModel):
    """启动运行时请求模型"""

    session_id: str = Field(..., description="会话ID")
    image: str = Field(..., description="Docker镜像名称")
    plugins: List[str] = Field(default=[], description="插件列表")
    environment: Dict[str, str] = Field(default={}, description="环境变量")


class BaseRuntimeRequest(BaseModel):
    """运行时请求模型"""

    runtime_id: str = Field(..., description="运行时ID")


class StopRuntimeRequest(BaseRuntimeRequest):
    """停止运行时请求模型"""


class PauseRuntimeRequest(BaseRuntimeRequest):
    """暂停运行时请求模型"""


class ResumeRuntimeRequest(BaseRuntimeRequest):
    """恢复运行时请求模型"""


class RuntimeResponse(BaseModel):
    """运行时响应模型"""

    runtime_id: str = Field(..., description="运行时ID")
    url: str = Field(..., description="运行时URL")
    work_hosts: Dict[str, int] = Field(..., description="工作主机列表")
    session_api_key: str = Field(..., description="会话API密钥")


class SessionInfo(BaseModel):
    """会话信息模型"""

    session_id: str = Field(..., description="会话ID")
    runtime_id: str = Field(..., description="运行时ID")
    vscode_url: str = Field(..., description="VSCode URL")
    web_hosts: Dict[str, int] = Field(..., description="Web主机列表")


class SessionStatus(BaseModel):
    """会话状态模型"""

    runtime_id: str = Field(..., description="运行时ID")
    status: str = Field(..., description="容器状态")
    url: str = Field(..., description="访问URL")


# 运行时状态管理
class RuntimeManager:
    """运行时管理器"""

    def __init__(self):
        self.active_runtimes: Dict[str, DockerRuntime] = {}
        self.idle_runtimes: Dict[str, DockerRuntime] = {}
        self.runtime_state: Dict[str, Dict[str, Any]] = {}
        self.config = RuntimeConfig()
        self.store_path = RuntimeConstants.STORE_PATH
        self.store_file_name = RuntimeConstants.STORE_FILE_NAME
        self.file_store = get_file_store("local", self.store_path)

    async def initialize(self):
        """初始化运行时管理器"""
        await self._init_idle_runtimes()
        await self._load_runtime_state()

    async def shutdown(self):
        """关闭运行时管理器"""
        for runtime in self.active_runtimes.values():
            runtime.close()
        for runtime in self.idle_runtimes.values():
            runtime.close()

    async def _init_idle_runtimes(self):
        """初始化空闲运行时"""
        for _ in range(self.config.max_idle_runtimes):
            runtime_id = str(uuid.uuid4())
            runtime = await self._create_runtime(
                runtime_id=runtime_id,
                image=RuntimeConstants.DEFAULT_IMAGE,
                plugins=RuntimeConstants.DEFAULT_PLUGINS,
                environment={},
            )
            self.idle_runtimes[runtime_id] = runtime

    async def _load_runtime_state(self):
        """加载运行时状态"""
        self.runtime_state = {}
        files = self.file_store.list("/")
        if self.store_file_name in files:
            runtime_id_store = self.file_store.read(self.store_file_name)
            if runtime_id_store:
                self.runtime_state = json.loads(runtime_id_store)
                for session_id, runtime_info in self.runtime_state.items():
                    runtime_id = runtime_info["runtime_id"]
                    runtime = await self._create_runtime(
                        runtime_id=runtime_id,
                        image=runtime_info["image"],
                        plugins=runtime_info["plugins"],
                        environment=runtime_info["environment"],
                    )
                    self.active_runtimes[runtime_id] = runtime
                    await runtime.connect()

    def get_runtime_by_session_id(
        self, session_id: str
    ) -> tuple[str | None, DockerRuntime | None]:
        """获取运行时ID"""
        runtime_id = self.runtime_state.get(session_id, {}).get("runtime_id")
        if runtime_id:
            return runtime_id, self.get_runtime_by_runtime_id(runtime_id)
        return None, None

    def get_runtime_by_runtime_id(self, runtime_id: str) -> DockerRuntime | None:
        """获取运行时ID"""
        return self.active_runtimes.get(runtime_id, None)

    def get_session_id(self, runtime_id: str) -> str | None:
        """获取会话ID"""
        for session_id, runtime_info in self.runtime_state.items():
            if runtime_info["runtime_id"] == runtime_id:
                return session_id
        return None

    async def _save_runtime_state(
        self, runtime: DockerRuntime, request: StartRuntimeRequest
    ):
        self.active_runtimes[runtime.sid] = runtime
        # 保存可序列化配置数据
        self.runtime_state[request.session_id] = {
            "runtime_id": runtime.sid,
            "image": request.image,
            "plugins": request.plugins,
            "environment": request.environment,
        }

        """保存运行时状态"""
        self.file_store.write(self.store_file_name, json.dumps(self.runtime_state))

    async def _delete_runtime_state(self, session_id: str):
        """删除运行时状态"""
        runtime_id = self.runtime_state.get(session_id, {}).get("runtime_id")
        if runtime_id:
            del self.active_runtimes[runtime_id]
        del self.runtime_state[session_id]
        self.file_store.write(self.store_file_name, json.dumps(self.runtime_state))

    async def _create_runtime(
        self,
        runtime_id: str,
        image: str,
        plugins: List[str] = None,
        environment: Dict = None,
    ) -> DockerRuntime:
        """创建运行时实例"""

        config = load_app_config()
        config.sandbox.runtime_container_image = image

        runtime_plugins = [PluginRequirement(name=plugin) for plugin in (plugins or [])]

        file_store = get_file_store(config.file_store, config.file_store_path)
        event_stream = EventStream(runtime_id, file_store)

        runtime = DockerRuntime(
            config=config,
            event_stream=event_stream,
            sid=runtime_id,
            plugins=runtime_plugins,
            env_vars=environment or {},
            status_callback=self._queue_status_message,
            headless_mode=False,
            attach_to_existing=False,
        )

        return runtime

    def _queue_status_message(self, msg_type: str, msg_id: str, msg: str):
        """状态消息队列"""
        logger.info(f"会话状态更新 - ID: {msg_id}, 类型: {msg_type}, 消息: {msg}")


# 应用生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    app.state.runtime_manager = RuntimeManager()

    # 创建注册客户端
    proxy_url = os.environ.get("OPENHANDS_PROXY_URL", "http://localhost:8080")
    register_host = "127.0.0.1"
    register_port = 8000
    server_id = f"{register_host}_{register_port}"
    app.state.registration_client = RuntimeRegistrationClient(
        proxy_url=proxy_url,
        host=register_host,
        port=register_port,
        server_id=server_id,
        heartbeat_interval=30,
    )

    await app.state.runtime_manager.initialize()
    # 启动注册客户端
    await app.state.registration_client.start()
    yield
    # 关闭时
    await app.state.registration_client.stop()
    await app.state.runtime_manager.shutdown()


# 创建FastAPI应用
app = FastAPI(
    title="OpenHands Remote Docker Runtime 服务",
    description="OpenHands Remote Docker 运行时管理服务，提供容器生命周期管理和会话管理功能",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# 自定义OpenAPI文档
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

# 路由分组
runtime_router = APIRouter(
    prefix="",
    tags=["运行时管理"],
    responses={404: {"description": "未找到"}},
)

session_router = APIRouter(
    prefix="/sessions",
    tags=["会话管理"],
    responses={404: {"description": "未找到"}},
)

# 中间件配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 请求日志中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.time()

    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(
            f"[请求完成] ID:{request_id} {request.method} {request.url.path} "
            f"状态:{response.status_code} 耗时:{process_time:.3f}秒"
        )
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(
            f"[请求异常] ID:{request_id} {request.method} {request.url.path} "
            f"错误:{str(e)} 耗时:{process_time:.3f}秒"
        )
        raise


# 认证依赖
async def verify_api_key(x_api_key: str = Header(None)):
    api_key = app.state.runtime_manager.config.api_key
    if not api_key or x_api_key != api_key:
        raise HTTPException(status_code=403, detail="无效的API密钥")
    return x_api_key


# 路由处理
@runtime_router.post(
    "/start",
    status_code=201,
    response_model=ResponseModel,
    summary="启动运行时",
    description="启动一个新的Docker运行时容器，或重用现有的空闲容器",
    responses={
        201: {"description": "运行时启动成功"},
        400: {"description": "请求参数错误"},
        403: {"description": "API密钥无效"},
        500: {"description": "服务器内部错误"},
    },
)
async def start_runtime(
    request: StartRuntimeRequest = Body(...), x_api_key: str = Depends(verify_api_key)
):
    """启动运行时"""
    try:
        runtime_id, runtime = app.state.runtime_manager.get_runtime_by_session_id(
            request.session_id
        )

        if runtime:
            await runtime.connect()
        else:
            if app.state.runtime_manager.idle_runtimes:
                runtime_id, runtime = app.state.runtime_manager.idle_runtimes.popitem()
            else:
                runtime_id = str(uuid.uuid4())
                runtime = await app.state.runtime_manager._create_runtime(
                    runtime_id=runtime_id,
                    image=request.image,
                    plugins=request.plugins,
                    environment=request.environment,
                )
            await runtime.connect()

        await app.state.runtime_manager._save_runtime_state(runtime, request)

        return ResponseModel(
            status="success",
            data=RuntimeResponse(
                runtime_id=runtime_id,
                url=runtime.api_url,
                work_hosts=runtime.web_hosts,
                session_api_key=app.state.runtime_manager.config.session_api_key,
            ),
        )

    except Exception as e:
        logger.error(f"创建会话失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")


@runtime_router.post(
    "/stop",
    response_model=ResponseModel,
    summary="停止运行时",
    description="停止指定的Docker运行时容器",
    responses={
        200: {"description": "运行时停止成功"},
        404: {"description": "运行时不存在"},
        403: {"description": "API密钥无效"},
        500: {"description": "服务器内部错误"},
    },
)
async def stop_runtime(
    request: StopRuntimeRequest = Body(...), x_api_key: str = Depends(verify_api_key)
):
    """停止运行时"""
    try:
        session_id = app.state.runtime_manager.get_session_id(request.runtime_id)
        runtime = app.state.runtime_manager.get_runtime_by_runtime_id(
            request.runtime_id
        )
        if not runtime:
            raise HTTPException(
                status_code=404, detail=f"运行时 {request.runtime_id} 不存在"
            )

        runtime.close()
        await runtime.delete(request.runtime_id)
        await app.state.runtime_manager._delete_runtime_state(session_id)

        return ResponseModel(status="success")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"停止运行时失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"停止运行时失败: {str(e)}")


@runtime_router.post(
    "/pause",
    response_model=ResponseModel,
    summary="暂停运行时",
    description="暂停指定的Docker运行时容器",
    responses={
        200: {"description": "运行时暂停成功"},
        404: {"description": "运行时不存在"},
        403: {"description": "API密钥无效"},
        500: {"description": "服务器内部错误"},
    },
)
async def pause_runtime(
    request: PauseRuntimeRequest = Body(...), x_api_key: str = Depends(verify_api_key)
):
    """暂停运行时"""
    if (
        not request.runtime_id
        or request.runtime_id not in app.state.runtime_manager.active_runtimes
    ):
        raise HTTPException(
            status_code=404, detail=f"运行时 {request.runtime_id} 不存在"
        )

    try:
        runtime = app.state.runtime_manager.get_runtime_by_runtime_id(
            request.runtime_id
        )
        await call_sync_from_async(runtime.pause)

        return ResponseModel(status="success", message="运行时暂停成功")

    except Exception as e:
        logger.error(f"暂停运行时失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"暂停运行时失败: {str(e)}")


@runtime_router.post(
    "/resume",
    response_model=ResponseModel,
    summary="恢复运行时",
    description="恢复指定的Docker运行时容器",
    responses={
        200: {"description": "运行时恢复成功"},
        404: {"description": "运行时不存在"},
        403: {"description": "API密钥无效"},
        500: {"description": "服务器内部错误"},
    },
)
async def resume_runtime(
    request: ResumeRuntimeRequest = Body(...), x_api_key: str = Depends(verify_api_key)
):
    """恢复运行时"""
    if (
        not request.runtime_id
        or request.runtime_id not in app.state.runtime_manager.active_runtimes
    ):
        raise HTTPException(
            status_code=404, detail=f"运行时 {request.runtime_id} 不存在"
        )

    try:
        runtime = app.state.runtime_manager.get_runtime_by_runtime_id(
            request.runtime_id
        )
        await call_sync_from_async(runtime.resume)

        return ResponseModel(status="success", message="运行时恢复成功")

    except Exception as e:
        logger.error(f"恢复运行时失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"恢复运行时失败: {str(e)}")


@session_router.get(
    "/{session_id}",
    response_model=ResponseModel,
    summary="获取会话状态",
    description="获取指定会话的运行时状态信息",
    responses={
        200: {"description": "获取成功"},
        404: {"description": "会话不存在"},
        403: {"description": "API密钥无效"},
        500: {"description": "服务器内部错误"},
    },
)
async def get_session(
    session_id: str = Path(..., description="会话ID"),
    x_api_key: str = Depends(verify_api_key),
):
    """获取会话状态"""
    runtime_id, runtime = app.state.runtime_manager.get_runtime_by_session_id(
        session_id
    )

    if not runtime:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    status = "unknown"

    try:
        container = runtime.container
        if container:
            container.reload()
            status = container.status
    except Exception as e:
        logger.error(f"获取容器状态失败: {str(e)}")

    return ResponseModel(
        status="success",
        data=SessionStatus(
            runtime_id=runtime_id,
            status=status,
            url=f"http://localhost:{runtime._host_port}",
        ),
    )


@session_router.get(
    "",
    response_model=ResponseModel,
    summary="列出所有会话",
    description="获取所有活跃会话的信息",
    responses={
        200: {"description": "获取成功"},
        403: {"description": "API密钥无效"},
        500: {"description": "服务器内部错误"},
    },
)
async def list_sessions(x_api_key: str = Depends(verify_api_key)):
    """列出所有会话"""
    sessions_info = {}
    for sid, runtime_info in app.state.runtime_manager.runtime_state.items():
        runtime_id, runtime = app.state.runtime_manager.get_runtime_by_session_id(sid)
        if runtime:
            sessions_info[sid] = SessionInfo(
                session_id=sid,
                runtime_id=runtime_id,
                vscode_url=runtime.vscode_url,
                web_hosts=runtime.web_hosts,
            )

    return ResponseModel(status="success", data={"sessions": sessions_info})


@runtime_router.get(
    "/runtime/{runtime_id}",
    response_model=ResponseModel,
    summary="获取运行时信息",
    description="获取指定运行时的详细信息，包括容器状态、重启次数等",
    responses={
        200: {"description": "获取成功"},
        404: {"description": "运行时不存在"},
        403: {"description": "API密钥无效"},
        500: {"description": "服务器内部错误"},
    },
)
async def get_runtime_info(
    runtime_id: str = Path(..., description="运行时ID"),
    x_api_key: str = Depends(verify_api_key),
):
    """获取运行时信息"""
    runtime = app.state.runtime_manager.get_runtime_by_runtime_id(runtime_id)
    if not runtime:
        raise HTTPException(status_code=404, detail=f"运行时 {runtime_id} 不存在")

    try:
        # 获取容器状态
        pod_status = "unknown"
        restart_count = 0
        try:
            container = runtime.container
            if container:
                container.reload()
                pod_status = (
                    "ready" if container.status == "running" else container.status
                )
                restart_count = getattr(container, "restart_count", 0)
        except Exception as e:
            logger.error(f"获取容器状态失败: {str(e)}")

        return ResponseModel(
            status="success",
            data={
                "runtime_id": runtime_id,
                "pod_status": pod_status,
                "restart_count": restart_count,
                "restart_reasons": [],
            },
        )

    except Exception as e:
        logger.error(f"获取运行时信息失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取运行时信息失败: {str(e)}")


# 注册路由
app.include_router(runtime_router)
app.include_router(session_router)


# 健康检查
@app.get(
    "/",
    response_model=ResponseModel,
    summary="健康检查",
    description="检查服务是否正常运行",
    responses={200: {"description": "服务正常运行"}},
)
async def root():
    return ResponseModel(
        status="success",
        data={"message": "OpenHands Docker Runtime 服务正在运行"},
    )


@app.get(
    "/alive",
    response_model=ResponseModel,
    summary="存活检查",
    description="检查服务是否存活",
    responses={200: {"description": "服务存活"}},
)
async def check_alive():
    return ResponseModel(status="success", data={"status": "alive"})


def start_server(host="0.0.0.0", port=8000):
    """启动服务器"""
    logger.info(f"启动OpenHands Docker Runtime服务，监听地址: {host}:{port}")
    logger.info(f"Python版本: {sys.version}")
    logger.info(f"系统环境: {os.name}")
    logger.info(f"工作目录: {os.getcwd()}")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    host = os.environ.get("OPENHANDS_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("OPENHANDS_SERVER_PORT", "8000"))

    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    if len(sys.argv) > 2:
        host = sys.argv[2]

    logger.info(f"服务启动参数 - 主机: {host}, 端口: {port}")
    start_server(host, port)
