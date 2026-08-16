# 系统管理 API 🛠️

系统管理 API 提供了监控和管理系统状态的功能。

## API 端点

### 获取系统状态

```http
GET/backend-api/api/system/status
```

获取系统的当前运行状态，包括版本信息、运行时间、资源使用情况等。

**响应示例：**
```json
{
  "status": {
    "version": "1.0.0",
    "uptime": 3600,  // 运行时间(秒)
    "active_adapters": 2,  // 活跃的 IM 适配器数量
    "active_backends": 3,  // 活跃的 LLM 后端数量
    "loaded_plugins": 5,   // 已加载的插件数量
    "workflow_count": 10,  // 工作流数量
    "memory_usage": {
      "rss": 256.5,       // 物理内存使用(MB)
      "vms": 512.8,       // 虚拟内存使用(MB)
      "percent": 2.5      // 内存使用百分比
    },
    "cpu_usage": 1.2      // CPU 使用百分比
  }
}
```

### 获取系统配置

```http
GET/backend-api/api/system/config
```

获取系统当前配置。

### 更新系统配置

```http
PUT/backend-api/api/system/config
```

更新系统配置。

**请求体：**
```json
{
  "log_level": "INFO",
  "max_connections": 100,
  "timeout": 30,
  "storage": {
    "type": "local",
    "path": "/data"
  }
}
```

### 获取系统日志

```http
GET/backend-api/api/system/logs
```

获取系统日志。支持分页和过滤。

**查询参数：**
- `level`: 日志级别 (DEBUG/INFO/WARNING/ERROR)
- `start_time`: 开始时间
- `end_time`: 结束时间
- `limit`: 每页条数
- `offset`: 偏移量

### 获取用户列表

```http
GET/backend-api/api/system/users
```

获取系统用户列表。

### 创建用户

```http
POST/backend-api/api/system/users
```

创建新用户。

**请求体：**
```json
{
  "username": "admin",
  "password": "password123",
  "role": "admin",
  "permissions": ["read", "write", "admin"]
}
```

### 更新用户

```http
PUT/backend-api/api/system/users/{username}
```

更新用户信息。

### 删除用户

```http
DELETE/backend-api/api/system/users/{username}
```

删除指定用户。

## 数据模型

### SystemStatus
- `version`: 系统版本
- `uptime`: 运行时间(秒)
- `active_adapters`: 活跃的 IM 适配器数量
- `active_backends`: 活跃的 LLM 后端数量
- `loaded_plugins`: 已加载的插件数量
- `workflow_count`: 工作流数量
- `memory_usage`: 内存使用情况
  - `rss`: 物理内存使用(MB)
  - `vms`: 虚拟内存使用(MB)
  - `percent`: 内存使用百分比
- `cpu_usage`: CPU 使用百分比

### SystemConfig
- `log_level`: 日志级别
- `max_connections`: 最大连接数
- `timeout`: 超时时间(秒)
- `storage`: 存储配置

### User
- `username`: 用户名
- `role`: 角色
- `permissions`: 权限列表
- `created_at`: 创建时间
- `last_login`: 最后登录时间

## 监控指标

### 系统指标
- 运行时间
- CPU 使用率
- 内存使用情况

### 组件指标
- IM 适配器数量和状态
- LLM 后端数量和状态
- 插件数量和状态
- 工作流数量

## 相关代码

- [系统路由](routes.py)
- [数据模型](models.py)
- [系统观测与排错](../../../../docs/OBSERVABILITY.md)

## 错误处理

所有 API 端点在发生错误时都会返回适当的 HTTP 状态码和错误信息：

```json
{
  "error": "错误描述信息"
}
```

常见状态码：
- 400: 请求参数错误
- 401: 未认证或认证失败
- 403: 权限不足
- 404: 资源不存在
- 500: 服务器内部错误

## 使用示例

### 获取系统状态
```python
import requests

response = requests.get(
    'http://localhost:8080/api/system/status',
    headers={'Authorization': f'Bearer {token}'}
)
status = response.json()['status']
print(f"系统已运行: {status['uptime']} 秒")
print(f"内存使用: {status['memory_usage']['percent']}%")
print(f"CPU 使用: {status['cpu_usage']}%")
```

### 更新系统配置
```python
import requests

config_data = {
    "log_level": "DEBUG",
    "max_connections": 200,
    "timeout": 60
}

response = requests.put(
    'http://localhost:8080/api/system/config',
    headers={'Authorization': f'Bearer {token}'},
    json=config_data
)
```

### 创建新用户
```python
import requests

user_data = {
    "username": "admin",
    "password": "password123",
    "role": "admin",
    "permissions": ["read", "write", "admin"]
}

response = requests.post(
    'http://localhost:8080/api/system/users',
    headers={'Authorization': f'Bearer {token}'},
    json=user_data
)
```

## 相关文档

- [Web API 概览](../../../../README.md)
- [监控指南](../../../../docs/OBSERVABILITY.md)
- [API 认证](../../auth/routes.py)
