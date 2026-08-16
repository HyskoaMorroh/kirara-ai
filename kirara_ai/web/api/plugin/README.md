# 插件 API 🔌

插件 API 提供了管理插件的功能。通过这些 API，你可以安装、卸载、启用、禁用和更新插件。

## API 端点

### 获取所有插件

```http
GET/backend-api/api/plugin/plugins
```

获取所有已安装的插件列表。

**响应示例：**
```json
{
  "plugins": [
    {
      "name": "图像处理",
      "package_name": "image-processing",
      "description": "提供图像处理功能",
      "version": "1.0.0",
      "author": "开发者",
      "homepage": "https://github.com/example/image-processing",
      "license": "MIT",
      "is_internal": false,
      "is_enabled": true,
      "metadata": {
        "category": "media",
        "tags": ["image", "processing"]
      }
    }
  ]
}
```

### 获取特定插件

```http
GET/backend-api/api/plugin/plugins/{plugin_name}
```

获取指定插件的详细信息。

**响应示例：**
```json
{
  "plugin": {
    "name": "图像处理",
    "package_name": "image-processing",
    "description": "提供图像处理功能",
    "version": "1.0.0",
    "author": "开发者",
    "homepage": "https://github.com/example/image-processing",
    "license": "MIT",
    "is_internal": false,
    "is_enabled": true,
    "metadata": {
      "category": "media",
      "tags": ["image", "processing"]
    }
  }
}
```

### 安装插件

```http
POST/backend-api/api/plugin/plugins
```

安装新的插件。

**请求体：**
```json
{
  "package_name": "image-processing",
  "version": "1.0.0"  // 可选，不指定则安装最新版本
}
```

### 卸载插件

```http
DELETE/backend-api/api/plugin/plugins/{plugin_name}
```

卸载指定的插件。注意：内部插件不能被卸载。

### 启用插件

```http
POST/backend-api/api/plugin/plugins/{plugin_name}/enable
```

启用指定的插件。

### 禁用插件

```http
POST/backend-api/api/plugin/plugins/{plugin_name}/disable
```

禁用指定的插件。

### 更新插件

```http
PUT/backend-api/api/plugin/plugins/{plugin_name}
```

更新插件到最新版本。注意：内部插件不支持更新。

### 搜索插件市场

```http
GET/backend-api/api/v1/search?query={query}&page={page}&pageSize={pageSize}
```

在插件市场中搜索插件。

**参数：**
- `query`: 搜索关键词
- `page`: 页码 (默认为 1)
- `pageSize`: 每页数量 (默认为 10)

**响应示例：**
```json
{
  "plugins": [
    {
      "name": "图像处理",
      "description": "提供图像处理功能",
      "author": "开发者",
      "pypiPackage": "image-processing",
      "pypiInfo": {
        "version": "1.0.0",
        "description": "PyPI 描述",
        "author": "PyPI 作者",
        "homePage": "https://example.com"
      },
      "isInstalled": false,
      "installedVersion": null,
      "isUpgradable": false
    }
  ],
  "totalCount": 1,
  "totalPages": 1,
  "page": 1,
  "pageSize": 10
}
```

### 获取插件市场中插件的详细信息

```http
GET/backend-api/api/v1/info/{plugin_name}
```

获取插件市场中指定插件的详细信息。

**响应示例：**
```json
{
  "name": "图像处理",
  "description": "提供图像处理功能",
  "author": "开发者",
  "pypiPackage": "image-processing",
  "pypiInfo": {
    "version": "1.0.0",
    "description": "PyPI 描述",
    "author": "PyPI 作者",
    "homePage": "https://example.com"
  },
  "isInstalled": false,
  "installedVersion": null,
  "isUpgradable": false
}
```

## 数据模型

### PluginInfo
- `name`: 插件名称
- `package_name`: 包名
- `description`: 描述
- `version`: 版本号
- `author`: 作者
- `homepage`: 主页(可选)
- `license`: 许可证(可选)
- `is_internal`: 是否为内部插件
- `is_enabled`: 是否已启用
- `metadata`: 元数据(可选)

### InstallPluginRequest
- `package_name`: 包名
- `version`: 版本号(可选)

### PluginList
- `plugins`: 插件列表

### PluginResponse
- `plugin`: 插件信息

## 内置插件

### IM 适配器
- Telegram 适配器
- HTTP Legacy 适配器
- WeCom 适配器

### LLM 后端
- OpenAI 适配器
- Anthropic 适配器
- Google AI 适配器

## 相关代码

- [插件管理器](../../../plugin_manager/plugin_loader.py)
- [插件基类](../../../plugin_manager/plugin.py)
- [系统插件](../../../plugins)

## 错误处理

所有 API 端点在发生错误时都会返回适当的 HTTP 状态码和错误信息：

```json
{
  "error": "错误描述信息"
}
```

常见状态码：
- 400: 请求参数错误、插件已存在或内部插件操作限制
- 404: 插件不存在
- 500: 服务器内部错误

## 使用示例

### 获取所有插件
```python
import requests

response = requests.get(
    'http://localhost:8080/api/plugin/plugins',
    headers={'Authorization': f'Bearer {token}'}
)
```

### 安装新插件
```python
import requests

data = {
    "package_name": "image-processing",
    "version": "1.0.0"
}

response = requests.post(
    'http://localhost:8080/api/plugin/plugins',
    headers={'Authorization': f'Bearer {token}'},
    json=data
)
```

### 启用插件
```python
import requests

response = requests.post(
    'http://localhost:8080/api/plugin/plugins/image-processing/enable',
    headers={'Authorization': f'Bearer {token}'}
)
```

### 更新插件
```python
import requests

response = requests.put(
    'http://localhost:8080/api/plugin/plugins/image-processing',
    headers={'Authorization': f'Bearer {token}'}
)
```

### 搜索插件市场
```python
import requests

response = requests.get(
    'http://localhost:8080/api/v1/search?query=image&page=1&pageSize=10',
    headers={'Authorization': f'Bearer {token}'}
)
```

### 获取插件市场中插件的详细信息
```python
import requests

response = requests.get(
    'http://localhost:8080/api/v1/info/image-processing',
    headers={'Authorization': f'Bearer {token}'}
)
```

## 相关文档

- [Web API 概览](../../../../README.md)
- [API 认证](../../auth/routes.py)
- [插件开发](../../../../docs/EXTENDING.md#二编写插件)
