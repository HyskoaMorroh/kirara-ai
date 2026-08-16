# 区块 API 🧩

区块 API 提供了查询工作流构建块类型的功能。每个区块类型定义了其输入、输出和配置项。

>> 注意：文档由 Claude 生成，可能存在错误，请以实际代码为准。
## API 端点

### 获取所有区块类型

```http
GET/backend-api/api/block/types
```

获取所有可用的区块类型列表。

**响应示例：**
```json
{
  "types": [
    {
      "type_name": "MessageBlock",
      "name": "消息区块",
      "description": "处理消息的基础区块",
      "inputs": [
        {
          "name": "content",
          "description": "消息内容",
          "type": "string",
          "required": true
        }
      ],
      "outputs": [
        {
          "name": "message",
          "description": "处理后的消息",
          "type": "IMMessage"
        }
      ],
      "configs": [
        {
          "name": "format",
          "description": "消息格式",
          "type": "string",
          "required": false,
          "default": "text"
        }
      ]
    }
  ]
}
```

### 获取特定区块类型

```http
GET/backend-api/api/block/types/{type_name}
```

获取指定区块类型的详细信息。

**响应示例：**
```json
{
  "type": {
    "type_name": "LLMBlock",
    "name": "大语言模型区块",
    "description": "调用 LLM 进行对话的区块",
    "inputs": [
      {
        "name": "prompt",
        "description": "提示词",
        "type": "string",
        "required": true
      }
    ],
    "outputs": [
      {
        "name": "response",
        "description": "LLM 的响应",
        "type": "string"
      }
    ],
    "configs": [
      {
        "name": "model",
        "description": "使用的模型",
        "type": "string",
        "required": true,
        "default": "gpt-4"
      },
      {
        "name": "temperature",
        "description": "温度参数",
        "type": "float",
        "required": false,
        "default": 0.7
      }
    ]
  }
}
```

### 注册区块类型

```http
POST/backend-api/api/block/types
```

注册新的区块类型。

**请求体：**
```json
{
  "type": "image_process",
  "name": "图像处理",
  "description": "处理图像数据",
  "category": "media",
  "config_schema": {
    "type": "object",
    "properties": {
      "operation": {
        "type": "string",
        "enum": ["resize", "crop", "rotate"]
      },
      "params": {
        "type": "object"
      }
    }
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "image": {
        "type": "string",
        "format": "binary"
      }
    }
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "image": {
        "type": "string",
        "format": "binary"
      }
    }
  }
}
```

### 更新区块类型

```http
PUT/backend-api/api/block/types/{type}
```

更新现有区块类型。

### 删除区块类型

```http
DELETE/backend-api/api/block/types/{type}
```

删除指定区块类型。

### 获取区块实例

```http
GET/backend-api/api/block/instances/{workflow_id}
```

获取指定工作流中的所有区块实例。

**响应示例：**
```json
{
  "instances": [
    {
      "block_id": "input_1",
      "type": "input",
      "workflow_id": "chat:normal",
      "config": {
        "format": "text"
      },
      "state": {
        "status": "ready",
        "last_run": "2024-03-10T12:00:00Z",
        "error": null
      }
    }
  ]
}
```

### 获取特定区块实例

```http
GET/backend-api/api/block/instances/{workflow_id}/{block_id}
```

获取指定区块实例的详细信息。

### 更新区块实例

```http
PUT/backend-api/api/block/instances/{workflow_id}/{block_id}
```

更新区块实例的配置。

## 数据模型

### BlockInput
- `name`: 输入名称
- `description`: 输入描述
- `type`: 数据类型
- `required`: 是否必需
- `default`: 默认值(可选)

### BlockOutput
- `name`: 输出名称
- `description`: 输出描述
- `type`: 数据类型

### BlockConfig
- `name`: 配置项名称
- `description`: 配置项描述
- `type`: 数据类型
- `required`: 是否必需
- `default`: 默认值(可选)

### BlockType
- `type_name`: 区块类型名称
- `name`: 显示名称
- `description`: 描述
- `inputs`: 输入定义列表
- `outputs`: 输出定义列表
- `configs`: 配置项定义列表

### BlockInstance
- `block_id`: 区块实例 ID
- `type`: 区块类型
- `workflow_id`: 所属工作流 ID
- `config`: 区块配置
- `state`: 区块状态
- `metadata`: 元数据(可选)

### BlockState
- `status`: 状态(ready/running/error)
- `last_run`: 最后运行时间
- `error`: 错误信息(如果有)
- `metrics`: 性能指标(可选)


## 相关代码

- [区块基类](../../../workflow/core/block/base.py)
- [区块注册表](../../../workflow/core/block/registry.py)
- [系统区块实现](../../../workflow/implementations/blocks)

## 错误处理

所有 API 端点在发生错误时都会返回适当的 HTTP 状态码和错误信息：

```json
{
  "error": "错误描述信息"
}
```

常见状态码：
- 400: 请求参数错误
- 404: 区块类型不存在
- 500: 服务器内部错误

## 使用示例

### 获取所有区块类型
```python
import requests

response = requests.get(
    'http://localhost:8080/api/block/types',
    headers={'Authorization': f'Bearer {token}'}
)
```

### 获取特定区块类型
```python
import requests

response = requests.get(
    'http://localhost:8080/api/block/types/LLMBlock',
    headers={'Authorization': f'Bearer {token}'}
)
```

## 相关文档

- [工作流系统概述](../../README.md#工作流系统-)
- [项目使用说明](../../../../README.md)
- [API 认证](../../README.md#api认证-)
