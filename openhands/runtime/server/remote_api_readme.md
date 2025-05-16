# OpenHands Docker 运行时服务 API 文档

本文档详细描述了 OpenHands 远程运行时客户端（RemoteRuntime）实际使用的 API 接口。这些接口用于管理远程运行时容器的生命周期和状态。

## 目录

- [基本信息](#基本信息)
- [会话管理接口](#会话管理接口)
- [镜像管理接口](#镜像管理接口)
- [运行时控制接口](#运行时控制接口)
- [运行时状态接口](#运行时状态接口)

## 基本信息

- **认证方式**: API密钥认证 (X-API-Key 请求头)
- **超时设置**: 通过 `remote_runtime_api_timeout` 配置项设置接口调用超时时间

## 会话管理接口

### 获取会话信息

| 项目 | 描述 |
|------|------|
| 路径 | `/sessions/{sid}` |
| 方法 | GET |
| 描述 | 检查指定会话ID的运行时是否存在及其状态 |
| 认证 | 需要API密钥 |
| 参数 | `sid` (path): 会话ID |
| 响应 | 见下方JSON示例 |
| 错误码 | 404: 会话不存在 |

**响应示例:**
```json
{
  "runtime_id": "运行时ID",
  "status": "running/paused/stopped",
  "url": "运行时URL"
}
```

## 镜像管理接口

### 获取镜像仓库前缀

| 项目 | 描述 |
|------|------|
| 路径 | `/registry_prefix` |
| 方法 | GET |
| 描述 | 获取镜像仓库的前缀，用于构建运行时镜像 |
| 认证 | 需要API密钥 |
| 响应 | 见下方JSON示例 |

**响应示例:**
```json
{
  "registry_prefix": "registry前缀"
}
```

### 检查镜像是否存在

| 项目 | 描述 |
|------|------|
| 路径 | `/image_exists` |
| 方法 | GET |
| 描述 | 检查指定镜像是否存在 |
| 认证 | 需要API密钥 |
| 参数 | `image` (query): 镜像名称 |
| 响应 | 见下方JSON示例 |

**响应示例:**
```json
{
  "exists": true|false
}
```

## 运行时控制接口

### 启动运行时

| 项目 | 描述 |
|------|------|
| 路径 | `/start` |
| 方法 | POST |
| 描述 | 使用指定镜像启动新的运行时容器 |
| 认证 | 需要API密钥 |
| 请求体 | 见下方JSON示例 |
| 响应 | 见下方JSON示例 |
| 错误码 | 400: 请求参数错误<br>500: 启动运行时失败 |

**请求体示例:**
```json
{
  "image": "容器镜像名称",
  "command": ["容器启动命令"],
  "working_dir": "/openhands/code/",
  "environment": { "环境变量名": "环境变量值" },
  "session_id": "会话ID",
  "resource_factor": 1.0
}
```

**响应示例:**
```json
{
  "runtime_id": "运行时ID",
  "url": "运行时URL",
  "work_hosts": { "主机名": 端口号 },
  "session_api_key": "会话API密钥"
}
```

### 停止运行时

| 项目 | 描述 |
|------|------|
| 路径 | `/stop` |
| 方法 | POST |
| 描述 | 停止并删除指定的运行时容器 |
| 认证 | 需要API密钥 |
| 请求体 | 见下方JSON示例 |
| 响应 | 无特定响应内容 |
| 错误码 | 404: 运行时不存在<br>500: 停止运行时失败 |

**请求体示例:**
```json
{
  "runtime_id": "运行时ID"
}
```

### 暂停运行时

| 项目 | 描述 |
|------|------|
| 路径 | `/pause` |
| 方法 | POST |
| 描述 | 暂停指定的运行时容器，但不删除 |
| 认证 | 需要API密钥 |
| 请求体 | 见下方JSON示例 |
| 响应 | 无特定响应内容 |
| 错误码 | 404: 运行时不存在<br>500: 暂停运行时失败 |

**请求体示例:**
```json
{
  "runtime_id": "运行时ID"
}
```

### 恢复运行时

| 项目 | 描述 |
|------|------|
| 路径 | `/resume` |
| 方法 | POST |
| 描述 | 恢复之前暂停的运行时容器 |
| 认证 | 需要API密钥 |
| 请求体 | 见下方JSON示例 |
| 响应 | 无特定响应内容 |
| 错误码 | 404: 运行时不存在<br>500: 恢复运行时失败 |

**请求体示例:**
```json
{
  "runtime_id": "运行时ID"
}
```

## 运行时状态接口

### 获取运行时信息

| 项目 | 描述 |
|------|------|
| 路径 | `/runtime/{runtime_id}` |
| 方法 | GET |
| 描述 | 获取指定运行时的详细状态信息 |
| 认证 | 需要API密钥 |
| 参数 | `runtime_id` (path): 运行时ID |
| 响应 | 见下方JSON示例 |
| 错误码 | 404: 运行时不存在 |

**响应示例:**
```json
{
  "runtime_id": "运行时ID",
  "pod_status": "ready/not found/pending/running/failed/unknown/crashloopbackoff",
  "restart_count": 重启次数,
  "restart_reasons": ["重启原因列表"]
}
```

## 接口使用流程

RemoteRuntime客户端使用这些API的典型流程：

1. **检查现有运行时**：调用 `/sessions/{sid}` 检查是否存在运行时
   - 如果运行时存在且状态为"running"，则直接使用
   - 如果状态为"paused"，则调用 `/resume` 恢复运行时
   - 如果状态为"stopped"或不存在，则创建新运行时

2. **创建新运行时**：
   - 调用 `/registry_prefix` 获取镜像仓库前缀
   - 构建运行时镜像
   - 调用 `/image_exists` 确认镜像存在
   - 调用 `/start` 启动新运行时

3. **等待运行时就绪**：
   - 重复调用 `/runtime/{runtime_id}` 检查运行时状态
   - 当状态为"ready"时，运行时可用

4. **关闭运行时**：
   - 如果配置为保持运行时活跃且需要暂停，调用 `/pause`
   - 否则调用 `/stop` 停止并删除运行时

## 客户端配置项

RemoteRuntime客户端会使用以下配置项来控制API行为：

- `api_key`: 用于认证的API密钥
- `remote_runtime_api_url`: 远程运行时API服务的URL
- `remote_runtime_api_timeout`: API调用超时时间
- `remote_runtime_init_timeout`: 等待运行时初始化的超时时间
- `remote_runtime_enable_retries`: 是否启用重试机制
- `keep_runtime_alive`: 是否在关闭客户端时保持运行时活跃
- `pause_closed_runtimes`: 是否在关闭客户端时暂停运行时
