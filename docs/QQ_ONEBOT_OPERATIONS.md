# QQ / OneBot 部署与故障排查

> 本文覆盖 QQ（OneBot V11 反向 WebSocket）从首次接入到重启恢复的完整操作路径。
> 文中所有 Token、账号与地址都是**示例占位值**，请替换为你自己的配置，
> 不要把真实凭据写进任何文档、截图或提交。

## 一、连接方向：谁连谁

Kirara 是 **WebSocket 服务端**，OneBot 实现（LLOneBot / NapCat 等）作为客户端反向接入。

```
QQ 客户端  ──►  OneBot 实现（容器内）  ──反向 WebSocket──►  Kirara
```

这意味着：

- **Kirara 不会主动去连 QQ**。Kirara 侧没有「重连 QQ」这个动作，只有「等待接入」。
- 如果适配器长时间显示「等待连接」，要去查 OneBot 实现那一侧的反向地址与 Token，
  而不是重启 Kirara。
- 地址由 Kirara 生成，在「IM 适配器 → OneBot」页面可以看到完整的
  `websocket_url`，形如 `/im/websocket/onebot/<随机段>/ws`。把它拼上你的公网地址
  填进 OneBot 实现的反向 WebSocket 配置。

## 二、七种连接状态与各自的处置

适配器详情页的状态标签直接对应后端的 `AdapterHealthSnapshot.status`。
**这七种状态不能混为一谈**——它们的处置动作完全不同：

| 状态 | 界面文案 | 含义 | 该做什么 |
|---|---|---|---|
| `initializing` | 正在启动 | 进程已起，适配器还没完成 `start()` | 等几秒；这不是故障 |
| `waiting` | 等待连接 | 已挂载，OneBot 实现尚未接入 | 查 OneBot 侧的反向地址是否填对、网络是否可达 |
| `connected` | 已连接 · N 个账号 | 至少一个账号在线 | 无需处理 |
| `stale` | 心跳超时 | 曾经连上，之后停止发心跳 | 查 OneBot 实现是否卡死、网络是否中断 |
| `credential_rejected` | 凭据被拒 | 对方接进来了，但 Token 不对 | **改 Token，重试无用**（见下） |
| `upstream_refused` | 握手被拒 | 对方接进来了，但握手头不合规 | 查 OneBot 实现版本与 `X-Client-Role` |
| `disconnected` | 已断开 | 适配器已停止 | 在界面上启动适配器 |

状态标签下方会显示一行**断开原因**，取值固定，不含任何凭据：

| 原因码 | 界面文案 | 典型成因 |
|---|---|---|
| `access_token_missing` | 上游未携带访问令牌 | OneBot 侧没填 Token，Kirara 侧填了 |
| `access_token_mismatch` | 上游访问令牌与本适配器配置不一致 | 两侧 Token 不同（注意首尾空格） |
| `invalid_client_role` | 上游握手缺少或使用了不支持的客户端角色 | 反向地址填错，或对方不是 OneBot V11 |
| `missing_self_id` | 上游握手缺少账号标识 | `api` / `universal` 角色未带 `X-Self-ID` |
| `heartbeat_timeout` | 曾经连上但心跳超时 | OneBot 实现卡死或网络中断 |
| `upstream_lifecycle_disconnect` | 上游主动上报断开 | QQ 掉线、OneBot 实现重启 |

> **为什么要区分「等待连接」和「凭据被拒」**：两者在旧版本里都显示为「等待连接」。
> 前者要去查地址和网络，后者再等一万年也不会自己好——必须改 Token。

同一份状态也会出现在 `GET /backend-api/api/system/readiness` 的
`im_adapters_connected` 检查里，`evidence` 中分别给出
`waiting_count` / `credential_rejected_count` / `upstream_refused_count` /
`initializing_count`，可用于外部监控。

## 三、数据目录清单

所有持久化路径都在 `DATA_PATH` 之下（默认 `$PWD/data`，可用环境变量覆盖）。
**Compose 只要挂载 `DATA_PATH` 这一个目录，重启就不会丢状态。**

### Kirara 侧（本项目）

| 内容 | 容器内路径 | 说明 | 备份 |
|---|---|---|---|
| 全局配置 | `data/config.yaml` | 含 LLM 与适配器配置 | 必须 |
| Web 凭据 | `data/web/password.hash` | 登录密码哈希 | 必须，**禁止提交** |
| 创建者身份 | `data/web/creator.subject` | 决定谁能执行服务器侧操作 | 必须，**禁止提交** |
| 数据库 | `data/db/kirara.db` | 消息队列、投递记录、入站去重收据、LLM 追踪 | 必须 |
| 会话与待确认 | `data/sessions/` | 对话历史、待确认操作 | 建议 |
| 资源（Skill/Hook/MCP/Prompt） | `data/resources/` | 已安装扩展及其版本 | 建议 |
| 工作流与调度规则 | `data/workflows/`、`data/dispatch_rules/` | 画布定义 | 必须 |
| 媒体 | `data/media/` | 收发的图片、语音、文件 | 可选 |
| 定价目录 | `data/pricing/catalog.json` | 成本核算的价格版本 | 建议 |
| 熔断状态 | `data/llm/circuit-state.json` | 重启后恢复已打开的熔断器 | 可选 |
| WeCom 媒体临时目录 | `data/temp/wecom/` | 出站媒体中转 | 不需要 |
| MCP 审计与确认 | `data/mcp/` | 工具调用审计、确认令牌 | 建议 |

> **`creator.subject` 只有一个生效位置**：`<DATA_PATH>/web/creator.subject`。
> 如果你在 `<DATA_PATH>/creator.subject` 也看到一个同名文件，那是旧版本路径留下的；
> 当前版本会在**没有**生效文件时自动继承它（保证旧 Token 不失效），
> 两个都存在且内容不同时以 `web/` 下那个为准，并在日志里给出一次提示。
> 不要手工把旧文件覆盖回去——那会让所有已签发的登录令牌立刻失效。

### QQ 侧（LLOneBot 容器，不属于本项目）

| 内容 | 容器内路径 | 说明 |
|---|---|---|
| QQ 登录态与设备身份 | `/root/.config/QQ` | **决定重启后是否免扫码**，必须挂载 |
| QQ 版本配置 | `/root/.config/QQ/versions/config.json` | 用于关闭热更新 |
| OneBot 配置与二维码 | `/root/llonebot` | 含反向 WebSocket 配置 |

### 启动前置检查

进程启动时会检查并创建上述目录，失败时给出**路径 + 原因 + 处置建议**，例如：

```
无法创建持久化目录 /app/data/plugins：Read-only file system；所在卷为只读挂载，请以可写方式重新挂载。
```

目录存在但不可写（只读挂载最常见）也会在启动阶段被探测出来，
而不是等到第一次写数据库才失败。

## 四、Compose 参考配置

```yaml
services:
  llonebot:
    image: initialencounter/llonebot:latest
    container_name: llonebot
    environment:
      # 用 .env 提供，不要写在 compose 文件里
      - AUTH_TOKEN=${LLONEBOT1_AUTH_TOKEN:?AUTH_TOKEN not set}
      - QUICK_LOGIN_QQ=${LLONEBOT1_QQ:?QQ number not set}
    volumes:
      # 这两个挂载决定「重启后是否免扫码」
      - ./QQ:/root/.config/QQ
      - ./llonebot:/root/llonebot
    ports:
      - "3007:3000"              # OneBot HTTP
      - "3004:3001"              # OneBot WebSocket
      - "127.0.0.1:5900:5900"    # noVNC（只绑本机）
      - "127.0.0.1:13000:13000" # PMHQ（只绑本机）
      - "8888:3080"              # LLOneBot WebUI
    privileged: true
    restart: always

  kirara-agent:
    image: swhesong/kirara-agent-framework:3.3.0b11
    container_name: kirara-agent
    restart: always
    environment:
      PYTHONDONTWRITEBYTECODE: "1"
    volumes:
      # 只需要这一个挂载；全部持久化状态都在里面
      - ./data:/app/data
    ports:
      - "8759:8080"
    healthcheck:
      test: ["CMD", "python", "-c", "import socket; s=socket.create_connection(('127.0.0.1', 8080), 2); s.close()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

两点务必注意：

1. **VNC 与 PMHQ 端口只绑 `127.0.0.1`**。它们能直接操作 QQ 客户端，
   暴露到公网等于把账号交出去。
2. `AUTH_TOKEN` 走 `.env`，且 `.env` 已在 `.gitignore` 里。

## 五、`docker compose down && pull && up -d` 之后

### 预期行为

`down` 会删除容器但**不会**删除挂载的宿主目录。因此：

- QQ 登录态在 `./QQ`，重启后应当免扫码自动登录。
- Kirara 的配置、数据库、会话在 `./data`，重启后适配器配置仍在。
- 两个容器的启动顺序不影响最终结果：Kirara 先起就等待接入，
  LLOneBot 先起就重连直到 Kirara 就绪。

### 恢复时间与状态序列

| 时刻 | Kirara 适配器状态 | 说明 |
|---|---|---|
| 0–5 秒 | `initializing` | 进程启动、挂载路由 |
| 5 秒–数十秒 | `waiting` | 等 LLOneBot 接入 |
| QQ 登录完成后 | `connected` | 出现账号数 |

QQ 侧从容器启动到登录完成通常需要 30–90 秒（要拉起 Electron、注入、登录）。
**在这段时间内看到「等待连接」是正常的**，不要反复重启。

如果超过 3 分钟仍为 `waiting`，按第二节的原因码排查。

### 消息不会重复发送

出站消息走持久化投递队列（`onebot_outbox_deliveries` 表）：

- 每一页回复有独立的 `delivery_id`，重复入队会返回既有记录，不会重发。
- 结果**未知**的投递（超时、断线）被标记为 `ambiguous` 并**永不自动重发**，
  避免重启后把同一条消息发两遍。
- 只有上游**明确拒绝**的操作才有限重试，退避为「指数增长 + 抖动 + 5 分钟上限」。

### 同一条上游消息不会被处理两次

入站方向有去重收据（`im_inbound_receipts` 表，四个渠道共用）：

- 反向 WebSocket 在投递中途断开时，上游无法知道我们是否已处理，
  因此重投是它唯一安全的选择——**去重必须由本侧完成**。
- 事件身份取 `self_id` + `message_id`；实现未给 `message_id` 时退回
  `self_id` + `user_id` + `time`。两者都拿不到时**照常处理但不去重**：
  丢一条消息比偶尔重复一次更糟。
- 处理失败会把收据放回可重领状态，上游重投仍能被处理一次——这是唯一
  应当重跑的情形。
- 进程中断时留在「处理中」的事件会在下次启动时重新开放认领，
  既不丢事件，也不会因此产生第二条回复。
- 两个配置实例（或两个渠道）的同一个上游 ID 视为不同事件，互不影响。

## 六、二维码与登录

二维码由 **LLOneBot 侧**生成，不经过 Kirara。日志里能看到：

```
[I] qq-protocol 二维码文件已保存: /root/llonebot/data/temp/login-qrcode.png
[PMHQ login] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68
```

要点：

- **有效期约 120 秒**。过期后 LLOneBot 会自动请求新的，日志里会出现新的
  `onQRCodeGetPicture`；请看**最新一张**，不要扫终端里往上翻出来的旧图。
- 日志里 `QR code unavailable` 出现在 QQ 服务尚未就绪时，属于**启动期的正常噪声**，
  随后会拿到真正的二维码；只有持续出现才是问题。
- 配了 `QUICK_LOGIN_QQ` 且 `./QQ` 里有历史登录态时走快速登录，**不出二维码**。
  日志里会看到 `quickLoginWithUin`。
- 若日志出现 `uin not in saved-credential list`，说明该账号在这个数据目录里
  没有历史登录态，只能扫码；扫一次之后登录态就落到 `./QQ`，后续重启免扫。

### 关闭 QQ 热更新

日志里的 `hotUpdate ... startAutoUpdate` 是 QQ 自身的热更新。它会在后台下载
新版本包（几十 MB），与 Kirara 无关，但**会占用带宽并可能拖慢首次登录**。
LLOneBot 镜像通过 `versions/config.json` 关闭它，该文件需要随 `./QQ` 一起持久化。

## 七、回复慢：先分段，再定位

「系统显示成功但 QQ 很久才收到」不能笼统归为「QQ 慢」。
每条回复现在都会记录端到端时间线，各阶段分别是：

| 阶段 | 含义 |
|---|---|
| `received_event` | 收到 IM 事件 |
| `workflow_started` | 工作流 / Agent 开始执行 |
| `llm_first_byte` | 模型首字节（非流式请求没有这一项，不会伪造） |
| `llm_completed` | 模型输出完成 |
| `formatting_started` / `formatting_completed` | 排版与分页 |
| `send_started` | 开始调用 OneBot 发送 |
| `send_succeeded` / `send_failed` | 发送结果，带重试次数 |

据此换算出的耗时字段：`queue_seconds`、`llm_first_byte_seconds`、
`llm_generation_seconds`、`formatting_seconds`、`send_seconds`、`total_seconds`。
**没测到的阶段不会给出 0**，而是不出现——「没测到」和「耗时为零」是两件事。

排查顺序：

1. `llm_generation_seconds` 大 → 是模型慢，不是 QQ 慢。去 LLM 统计页看
   该 Provider 的平均首字节与失败率。
2. `send_seconds` 大 → OneBot 侧慢或限流。查 LLOneBot 日志。
3. `queue_seconds` 大 → 事件在进入工作流前排队。查是否有并发瓶颈。
4. 各段都不大但用户仍觉得慢 → 看分页数：长回复会分成多条按序发送，
   最后一页到达时间自然晚于第一页。

Telegram 与 WeCom 使用同一套阶段命名，可直接横向比较同一问题在三个渠道的耗时。

## 八、Compose 验收矩阵

每次改动 Compose 或升级镜像后，建议按下表逐项过一遍：

| 场景 | 预期状态 | 日志证据 | 恢复上限 | 需人工扫码 |
|---|---|---|---|---|
| 首次启动（无 `./QQ`） | `waiting` → 扫码 → `connected` | `onQRCodeGetPicture` | 取决于扫码 | 是 |
| 已有登录态重启 | `initializing` → `waiting` → `connected` | `quickLoginWithUin` | 3 分钟 | 否 |
| `pull` 后重建 | 同上 | 同上 | 3 分钟 | 否 |
| 单个 QQ 实例故障 | 故障实例 `stale`，其余 `connected` | `heartbeat_timeout` | 90 秒转 `stale` | 否 |
| 多个 QQ 实例并存 | 各自独立 `connected` | 各自 `self_id` | 3 分钟 | 否 |
| Kirara 先启动 | `waiting` → `connected` | 先无接入日志 | 3 分钟 | 否 |
| QQ 先启动 | LLOneBot 重连直到成功 | `正在等待 QQ 启动进行重连` | 3 分钟 | 否 |
| OneBot 断线后恢复 | `connected` → `stale` → `connected` | 断开与重连各一条 | 90 秒 + 重连 | 否 |
| Token 配错 | `credential_rejected` | `access_token_mismatch` | 不会自愈 | 否，改配置 |
| 数据目录只读 | 启动即失败并给出原因 | `所在卷为只读挂载` | 不会自愈 | 否，改挂载 |
| 网络暂不可用 | `waiting` | 无接入日志 | 网络恢复后 3 分钟 | 否 |

> **本表未在真实 Docker 环境中逐项跑过。** 状态转换与恢复逻辑有自动化测试覆盖
> （`tests/plugins/im_onebot_adapter/test_connection_states.py`），
> 但真实容器重启、真实扫码、真实 PMHQ 注入属于外部环境，
> 需要你在自己的部署上按此表验收。

## 九、多账号

一个 OneBot 反向 WebSocket 可以复用多个 QQ 账号（不同 `self_id`）。
Kirara 按事件里的 `self_id` 区分账号，因此：

- 多账号在线时，发送操作**必须**能确定目标账号。无法确定时会直接拒绝，
  而不是随便选一个发出去——发错账号比不发更糟。
- Agent 可以按「渠道 → 账号 → 会话」三级绑定，账号级绑定就是用这个 `self_id`。
- 每个 LLOneBot 实例需要**独立的** `websocket_url`；路径冲突会在启动时报错。

## 十、相关文档

- [`QUICKSTART.md`](QUICKSTART.md) — 首次部署与第一条回复
- [`OBSERVABILITY.md`](OBSERVABILITY.md) — 日志、追踪与统计
- [`UPGRADING.md`](UPGRADING.md) — 升级与回滚
- [`AGENTS_SKILLS_HOOKS_MCP_GUIDE.md`](AGENTS_SKILLS_HOOKS_MCP_GUIDE.md) — 扩展与权限边界
