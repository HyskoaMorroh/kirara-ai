# Web API 系统 🌐

本系统提供了一套完整的RESTful API，用于管理和监控ChatGPT-Mirai机器人的各个组件。

## 系统架构 🏗️

- 基于 [Quart](https://pgjones.gitlab.io/quart/) 异步Web框架
- 使用 [Pydantic](https://docs.pydantic.dev/) 进行数据验证
- JWT认证保护所有API端点
- CORS支持跨域请求
- 模块化设计，易于扩展

## 模块说明 📦

### 1. 认证模块 🔐
- 路径: [`auth`](auth)
- 功能: 用户认证、JWT令牌管理
- API文档: [认证路由](auth/routes.py)

### 2. IM适配器管理 💬
- 路径: [`api/im`](api/im)
- 功能: 管理即时通讯平台适配器
- API文档: [IM API文档](api/im/README.md)

### 3. LLM后端管理 🤖
- 路径: [`api/llm`](api/llm)
- 功能: 管理大语言模型后端
- API文档: [LLM API文档](api/llm/README.md)

### 4. 调度规则管理 📋
- 路径: [`api/dispatch`](api/dispatch)
- 功能: 管理消息处理规则
- API文档: [调度规则API文档](api/dispatch/README.md)

### 5. Block查询 🧩
- 路径: [`api/block`](api/block)
- 功能: 查询工作流构建块信息
- API文档: [Block API文档](api/block/README.md)

### 6. Workflow管理 ⚡
- 路径: [`api/workflow`](api/workflow)
- 功能: 管理工作流定义和执行
- API文档: [Workflow API文档](api/workflow/README.md)

### 7. 插件管理 🔌
- 路径: [`api/plugin`](api/plugin)
- 功能: 管理系统插件
- API文档: [插件API文档](api/plugin/README.md)

### 8. 系统状态 📊
- 路径: [`api/system`](api/system)
- 功能: 监控系统运行状态
- API文档: [系统状态API文档](api/system/README.md)

## 快速开始 🚀

1. 安装依赖:
```bash
uv sync --frozen
```

2. 配置系统:
- 复制 `config.yaml.example` 到 `config.yaml`
- 修改配置文件中的相关设置

3. 启动服务:
```bash
uv run python -m kirara_ai
```

首次启动时会自动创建管理员密码。

## API认证 🔑

除了首次设置密码的接口外，所有API都需要在请求头中携带JWT令牌：
```http
Authorization: Bearer <your-jwt-token>
```

获取令牌：
```http
POST/backend-api/api/auth/login
Content-Type: application/json

{
    "password": "your-password"
}
```

## 开发指南 💻

### 添加新的API端点

1. 在相应模块下创建路由文件
2. 定义数据模型（使用Pydantic）
3. 实现API逻辑
4. 在 [`app.py`](app.py) 中注册蓝图

示例:
```python
from quart import Blueprint, request
from pydantic import BaseModel

# 定义数据模型
class MyModel(BaseModel):
    name: str
    value: int

# 创建蓝图
my_bp = Blueprint('my_api', __name__)

# 实现API端点
@my_bp.route('/endpoint', methods=['POST'])
@require_auth
async def my_endpoint():
    data = await request.get_json()
    model = MyModel(**data)
    # 处理逻辑
    return model.model_dump()
```

### 错误处理

使用HTTP状态码表示错误类型：
- 400: 请求参数错误
- 401: 未认证或认证失败
- 404: 资源不存在
- 500: 服务器内部错误

返回统一的错误格式：
```json
{
    "error": "错误描述信息"
}
```

## 依赖说明 📚

主要依赖包：
- quart: 异步Web框架
- pydantic: 数据验证
- PyJWT: JWT认证
- hypercorn: ASGI服务器
- psutil: 系统监控

完整依赖列表见 [`pyproject.toml`](../../pyproject.toml) 与 [`uv.lock`](../../uv.lock)

## 测试 🧪

运行单元测试：
```bash
pytest tests/web
```

## 贡献指南 🤝

1. Fork 本仓库
2. 创建特性分支
3. 提交更改
4. 创建Pull Request
