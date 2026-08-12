# 媒体管理API

本模块提供了媒体文件管理的API，包括上传、查询、下载和删除媒体文件。

## API接口

### 获取媒体列表

```
POST /api/media/list
```

请求体：
```json
{
  "query": "搜索关键词",
  "content_type": "image/jpeg",
  "start_date": "2023-01-01T00:00:00Z",
  "end_date": "2023-12-31T23:59:59Z",
  "tags": ["tag1", "tag2"],
  "page": 1,
  "page_size": 20
}
```

响应：
```json
{
  "items": [
    {
      "id": "media_id",
      "url": "/api/media/file/filename.jpg",
      "thumbnail_url": "/api/media/thumbnails/media_id.jpg",
      "metadata": {
        "filename": "filename.jpg",
        "content_type": "image/jpeg",
        "size": 12345,
        "width": 800,
        "height": 600,
        "upload_time": "2023-01-01T12:00:00Z",
        "source": "qq_group",
        "uploader": "user1",
        "tags": ["tag1", "tag2"]
      }
    }
  ],
  "total": 100,
  "has_more": true
}
```

### 获取媒体文件

```
GET /api/media/file/{filename}
```

返回媒体文件的二进制内容。

### 获取缩略图

```
GET /api/media/thumbnails/{media_id}.jpg
```

返回媒体文件的缩略图。

### 上传媒体文件

```
POST /api/media/upload
```

使用 multipart/form-data 上传文件。

可选参数：
- source: 来源
- uploader: 上传者
- tags: 标签（逗号分隔）

响应：
```json
{
  "id": "media_id",
  "url": "/api/media/file/filename.jpg",
  "thumbnail_url": "/api/media/thumbnails/media_id.jpg",
  "metadata": {
    "filename": "filename.jpg",
    "content_type": "image/jpeg",
    "size": 12345,
    "width": 800,
    "height": 600,
    "upload_time": "2023-01-01T12:00:00Z",
    "source": "qq_group",
    "uploader": "user1",
    "tags": ["tag1", "tag2"]
  }
}
```

### 删除媒体文件

```
DELETE /api/media/delete/{media_id}
```

响应：
```json
{
  "success": true
}
```

### 批量删除媒体文件

```
POST /api/media/batch-delete
```

请求体：
```json
{
  "ids": ["media_id1", "media_id2"]
}
```

响应：
```json
{
  "success": true,
  "deleted_count": 2
}
```

## 数据模型

### MediaMetadata

媒体文件的元数据。

| 字段名 | 类型 | 说明 |
|-------|------|------|
| filename | string | 文件名 |
| content_type | string | 内容类型 |
| size | integer | 文件大小（字节） |
| width | integer | 图片宽度（可选） |
| height | integer | 图片高度（可选） |
| upload_time | datetime | 上传时间 |
| source | string | 来源（可选） |
| uploader | string | 上传者（可选） |
| tags | array | 标签列表 |

### MediaItem

媒体文件项。

| 字段名 | 类型 | 说明 |
|-------|------|------|
| id | string | 媒体ID |
| url | string | 媒体URL |
| thumbnail_url | string | 缩略图URL（可选） |
| metadata | MediaMetadata | 媒体元数据 | 