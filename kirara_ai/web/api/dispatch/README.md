# 调度规则 API 📋

调度规则 API 提供了消息处理规则的管理功能。调度规则决定了如何根据消息内容选择合适的工作流进行处理。

## API 端点

### 获取规则列表

```http
GET/backend-api/api/dispatch/rules
```

获取所有已配置的调度规则。

**响应示例：**
```json
{
  "rules": [
    {
      "rule_id": "chat_normal",
      "name": "普通聊天",
      "description": "普通聊天，使用默认参数",
      "workflow_id": "chat:normal",
      "priority": 5,
      "enabled": true,
      "rule_groups": [
        {
          "operator": "or",
          "rules": [
            {
              "type": "prefix",
              "config": {
                "prefix": "/chat"
              }
            },
            {
              "type": "keyword",
              "config": {
                "keywords": ["聊天", "对话"]
              }
            }
          ]
        }
      ],
      "metadata": {
        "category": "chat",
        "permission": "user",
        "temperature": 0.7
      }
    }
  ]
}
```

### 获取特定规则

```http
GET/backend-api/api/dispatch/rules/{rule_id}
```

获取指定规则的详细信息。

### 创建规则

```http
POST/backend-api/api/dispatch/rules
```

创建新的调度规则。

**请求体：**
```json
{
  "rule_id": "chat_creative",
  "name": "创意聊天",
  "description": "创意聊天，使用更高的温度参数",
  "workflow_id": "chat:creative",
  "priority": 5,
  "enabled": true,
  "rule_groups": [
    {
      "operator": "and",
      "rules": [
        {
          "type": "prefix",
          "config": {
            "prefix": "/creative"
          }
        },
        {
          "type": "keyword",
          "config": {
            "keywords": ["创意", "发散"]
          }
        }
      ]
    }
  ],
  "metadata": {
    "category": "chat",
    "permission": "user",
    "temperature": 0.9
  }
}
```

### 更新规则

```http
PUT/backend-api/api/dispatch/rules/{rule_id}
```

更新现有规则。

### 删除规则

```http
DELETE/backend-api/api/dispatch/rules/{rule_id}
```

删除指定规则。

### 启用规则

```http
POST/backend-api/api/dispatch/rules/{rule_id}/enable
```

启用指定规则。

### 禁用规则

```http
POST/backend-api/api/dispatch/rules/{rule_id}/disable
```

禁用指定规则。

## 数据模型

### SimpleRule
- `type`: 规则类型 (prefix/keyword/regex)
- `config`: 规则类型特定的配置

### RuleGroup
- `operator`: 组合操作符 (and/or)
- `rules`: 规则列表

### CombinedDispatchRule
- `rule_id`: 规则唯一标识符
- `name`: 规则名称
- `description`: 规则描述
- `workflow_id`: 关联的工作流ID
- `priority`: 优先级(数字越大优先级越高)
- `enabled`: 是否启用
- `rule_groups`: 规则组列表（组之间是 AND 关系）
- `metadata`: 元数据(可选)

## 规则类型

### 前缀匹配 (prefix)
根据消息前缀进行匹配，例如 "/help"。

配置参数：
- `prefix`: 要匹配的前缀

### 关键词匹配 (keyword)
检查消息中是否包含指定关键词。

配置参数：
- `keywords`: 关键词列表

### 正则匹配 (regex)
使用正则表达式进行匹配，提供最灵活的匹配方式。

配置参数：
- `pattern`: 正则表达式模式

## 组合规则说明

新版本的调度规则系统支持复杂的条件组合：

1. 每个规则可以包含多个规则组（RuleGroup）
2. 规则组之间是 AND 关系，即所有规则组都满足时才会触发
3. 每个规则组内可以包含多个简单规则（SimpleRule）
4. 规则组内的规则可以选择 AND 或 OR 关系
5. 每个简单规则都有自己的类型和配置

例如，可以创建如下规则：

```json
{
  "rule_groups": [
    {
      "operator": "or",
      "rules": [
        { "type": "prefix", "config": { "prefix": "/creative" } },
        { "type": "keyword", "config": { "keywords": ["创意", "发散"] } }
      ]
    },
    {
      "operator": "and",
      "rules": [
        { "type": "regex", "config": { "pattern": ".*问题.*" } },
        { "type": "keyword", "config": { "keywords": ["帮我", "请问"] } }
      ]
    }
  ]
}
```

这个规则表示：
- 当消息以 "/creative" 开头 或 包含 "创意"/"发散" 关键词
- 且 消息包含 "问题" 且 包含 "帮我"/"请问" 中的任一关键词
时触发。

## 相关代码

- [调度规则定义](../../../workflow/core/dispatch/models/dispatch_rules.py)
- [调度规则注册表](../../../workflow/core/dispatch/registry.py)
- [调度器实现](../../../workflow/core/dispatch/dispatcher.py)
- [系统预设规则](../../../../data/dispatch_rules)

## 错误处理

所有 API 端点在发生错误时都会返回适当的 HTTP 状态码和错误信息：

```json
{
  "error": "错误描述信息"
}
```

常见状态码：
- 400: 请求参数错误或规则配置无效
- 404: 规则不存在
- 409: 规则ID已存在
- 500: 服务器内部错误

## 使用示例

### 创建组合规则
```python
import requests

rule_data = {
    "rule_id": "chat_creative",
    "name": "创意聊天",
    "description": "创意聊天模式",
    "workflow_id": "chat:creative",
    "priority": 5,
    "enabled": True,
    "rule_groups": [
        {
            "operator": "or",
            "rules": [
                {
                    "type": "prefix",
                    "config": {
                        "prefix": "/creative"
                    }
                },
                {
                    "type": "keyword",
                    "config": {
                        "keywords": ["创意", "发散"]
                    }
                }
            ]
        }
    ]
}

response = requests.post(
    'http://localhost:8080/api/dispatch/rules',
    headers={'Authorization': f'Bearer {token}'},
    json=rule_data
)
```

### 更新规则优先级
```python
import requests

response = requests.put(
    'http://localhost:8080/api/dispatch/rules/chat_creative',
    headers={'Authorization': f'Bearer {token}'},
    json={"priority": 8}
)
```

## 相关文档

- [工作流系统概述](../../README.md#工作流系统-)
- [项目使用说明](../../../../README.md)
- [API 认证](../../README.md#api认证-)
