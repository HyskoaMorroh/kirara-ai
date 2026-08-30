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

## 二、八种连接状态与各自的处置

适配器详情页的状态标签直接对应后端的 `AdapterHealthSnapshot.status`。
**这八种状态不能混为一谈**——它们的处置动作完全不同：

| 状态 | 界面文案 | 含义 | 该做什么 |
|---|---|---|---|
| `initializing` | 正在启动 | 进程已起，适配器还没完成 `start()` | 等几秒；这不是故障 |
| `waiting` | 等待连接 | 已挂载，OneBot 实现尚未接入 | 查 OneBot 侧的反向地址是否填对、网络是否可达 |
| `connected` | 已连接 · N 个账号 | 至少一个账号在线 | 无需处理 |
| `stale` | 心跳超时 | 曾经连上，之后停止发心跳 | 查 OneBot 实现是否卡死、网络是否中断 |
| `credential_rejected` | 凭据被拒 | 对方接进来了，但 Token 不对 | **改 Token，重试无用**（见下） |
| `upstream_refused` | 握手被拒 | 对方接进来了，但握手头不合规 | 查 OneBot 实现版本与 `X-Client-Role` |
| `storage_unavailable` | 存储不可写 | 链路正常，但持久化目录已不可写 | 查数据卷是否被只读重挂、磁盘是否写满 |
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
| `data_directory_unwritable` | 持久化目录不可写 | 数据卷被只读重挂、磁盘写满、数据库文件被删 |

> **`storage_unavailable` 解决的是一类静默丢消息**：启动时卷是可写的，之后被
> 重新挂成只读（或写满）。此时 WebSocket 还连着，适配器以前会照旧报
> `connected`，而每一条要落库的投递都在失败——面板上一切正常，消息在丢。
> 现在投递队列每次被读取时都当作一次写入探针，读不出来就把状态改成
> `storage_unavailable`，原因码给 `data_directory_unwritable`。
>
> 它**不会**盖住凭据被拒、握手被拒或心跳超时：那三类是用户能直接动手修的，
> 被存储故障盖掉会让人去查磁盘，而真正要改的是 Token。
> 存储恢复后状态自动回到真实的链路状态，不需要重启适配器。
>
> 与启动期检查的分工：只读挂载在**启动时**就会让进程直接退出并打印
> 「所在卷为只读挂载」（见第五节）；`storage_unavailable` 覆盖的是
> **运行期**才发生的那一类，那时进程已经起来了，启动检查早已跑完。

> **`X-Client-Role` 的值大小写不敏感**：LLOneBot 与 LuckyLilliaBot 发送的是
> 首字母大写的 `Universal`，NapCat 等实现可能发小写。三种角色
> （`event` / `api` / `universal`）在任意大小写下都会被接受，
> 因此看到 `invalid_client_role` 时应当去查**反向地址是否指向了非 OneBot 服务**，
> 而不是怀疑大小写。

> **为什么要区分「等待连接」和「凭据被拒」**：两者在旧版本里都显示为「等待连接」。
> 前者要去查地址和网络，后者再等一万年也不会自己好——必须改 Token。

同一份状态也会出现在 `GET /backend-api/api/system/readiness` 的
`im_available` 检查里，`evidence` 中分别给出
`waiting_count` / `credential_rejected_count` / `upstream_refused_count` /
`initializing_count`，可用于外部监控。

## 三、数据目录清单

所有持久化路径都在 `DATA_PATH` 之下（默认 `$PWD/data`，可用环境变量覆盖）。
**Compose 只要挂载 `DATA_PATH` 这一个目录，重启就不会丢状态。**

下表的「宿主机路径」按本文第四节的 compose（`./data:/app/data`）给出。
如果你改了左侧挂载点，把 `./data` 替换成你自己的路径即可。

### Kirara 侧（本项目）

**Compose 挂载只有一条**：`./data:/app/data`。下表「Compose 挂载」列因此全部写
「随 `DATA_PATH` 一并挂载」——新版本新增的子目录会在启动时自动创建，
不需要为每个子目录再加一条挂载。单独挂子目录反而危险：漏掉一个就等于那部分
状态只存在容器里，`down` 之后消失。

| 内容 | 宿主机路径 | 容器内路径 | 需要的权限 | Compose 挂载 | 说明 | 备份 |
|---|---|---|---|---|---|---|
| 数据根目录 | `./data` | `/app/data` | 读写；owner 需为容器运行用户 | `./data:/app/data` | 挂载点本身 | — |
| 全局配置 | `./data/config.yaml` | `data/config.yaml` | 读写，建议 `600` | 随 `DATA_PATH` | 含 LLM 与适配器配置 | 必须 |
| Web 凭据 | `./data/web/password.hash` | `data/web/password.hash` | 读写，建议 `600` | 随 `DATA_PATH` | 登录密码哈希 | 必须，**禁止提交** |
| 创建者身份 | `./data/web/creator.subject` | `data/web/creator.subject` | 读写，建议 `600` | 随 `DATA_PATH` | 决定谁能执行服务器侧操作 | 必须，**禁止提交** |
| 数据库（含消息队列） | `./data/db/kirara.db` | `data/db/kirara.db` | 读写目录（SQLite 需在同目录建 `-wal`/`-shm`） | 随 `DATA_PATH` | **出站投递队列**、投递记录、入站去重收据、LLM 追踪都在这一个库里 | 必须 |
| 会话与待确认 | `./data/sessions/` | `data/sessions/` | 读写 | 随 `DATA_PATH` | 对话历史、待确认操作 | 建议 |
| 资源（Skill/Hook/MCP/Prompt） | `./data/resources/` | `data/resources/` | 读写 | 随 `DATA_PATH` | 已安装扩展及其版本 | 建议 |
| 工作流与调度规则 | `./data/workflows/`、`./data/dispatch_rules/` | 同名 | 读写 | 随 `DATA_PATH` | 画布定义 | 必须 |
| 媒体 | `./data/media/` | `data/media/` | 读写 | 随 `DATA_PATH` | 收发的图片、语音、文件 | 可选 |
| 定价目录 | `./data/pricing/catalog.json` | `data/pricing/catalog.json` | 读写 | 随 `DATA_PATH` | 成本核算的价格版本 | 建议 |
| 熔断状态 | `./data/llm/circuit-state.json` | `data/llm/circuit-state.json` | 读写 | 随 `DATA_PATH` | 重启后恢复已打开的熔断器 | 可选 |
| WeCom 媒体临时目录 | `./data/temp/wecom/` | `data/temp/wecom/` | 读写 | 随 `DATA_PATH` | 出站媒体中转 | 不需要 |
| MCP 审计与确认 | `./data/mcp/` | `data/mcp/` | 读写 | 随 `DATA_PATH` | 工具调用审计、确认令牌 | 建议 |
| 运行日志 | `./data/logs/` | `data/logs/` | 读写 | 随 `DATA_PATH`（或用 `KIRARA_LOG_DIR` 指向独立卷） | 每天轮转、保留 7 天、旧文件压缩为 zip；**第八节验收矩阵要查的「日志证据」就在这里** | 可选（排障期建议留存） |

> **消息队列没有独立目录**：它是数据库里的表（`onebot_deliveries` 等），
> 不是文件目录。备份 `data/db/` 就把队列一起备份了；单独去找一个
> 「队列目录」会找不到，而那正是这一行需要写清楚的原因。

> **日志为什么也在 `DATA_PATH` 下**：早前版本写在进程工作目录的 `logs/`，
> 既不在 `DATA_PATH` 里也没有任何卷挂它，于是 `docker compose down` 之后
> 那批日志随容器一起消失——恰好是运维按第八节去翻日志证据的时刻。
> 需要接外部日志收集器或独立卷时，用 `KIRARA_LOG_DIR` 显式覆盖该路径。

> **权限怎么设**：镜像内进程以非 root 运行时，宿主目录的 owner 必须与容器内
> uid 一致，否则启动阶段的写入探测会直接失败并打印 `Permission denied`。
> 最简做法是让 Docker 自己创建 `./data`（首次 `up -d` 时），不要手工 `mkdir`
> 后再用 root 拥有它。已经踩进去的话：
>
> ```bash
> # 先查容器内 uid，再据此改宿主目录 owner；不要盲目 chmod 777
> docker compose exec kirara-agent id -u
> sudo chown -R <上一步的 uid>:<gid> ./data
> ```
>
> `password.hash` 与 `creator.subject` 等同于凭据：只给 owner 读写（`600`），
> 且两者都已在 `.gitignore` 与 `.dockerignore` 中，绝不能提交或打进镜像。

> **`creator.subject` 只有一个生效位置**：`<DATA_PATH>/web/creator.subject`。
> 如果你在 `<DATA_PATH>/creator.subject` 也看到一个同名文件，那是旧版本路径留下的；
> 当前版本会在**没有**生效文件时自动继承它（保证旧 Token 不失效），
> 两个都存在且内容不同时以 `web/` 下那个为准，并在日志里给出一次提示。
> 不要手工把旧文件覆盖回去——那会让所有已签发的登录令牌立刻失效。

### QQ 侧（LLOneBot 容器，不属于本项目）

这一侧不由本项目管理，但它决定「重启后是否免扫码」，因此清单必须一起给出。
**备份与升级策略两列的口径与 Kirara 侧不同**：这些文件是可直接登录该 QQ 账号的
凭据，备份它们等于多一份可登录副本。

| 内容 | 宿主机路径 | 容器内路径 | 需要的权限 | Compose 挂载 | 说明 | 备份 | 升级兼容策略 |
|---|---|---|---|---|---|---|---|
| QQ 登录态与设备身份 | `./QQ` | `/root/.config/QQ` | 读写（容器内为 root） | `./QQ:/root/.config/QQ` | **决定重启后是否免扫码**，必须挂载 | 可选；**它等于账号凭据**，备份即多一份可登录副本，放在与 `password.hash` 同级的保护下 | 跨镜像升级保留即可；换 QQ 大版本后偶发需重扫一次 |
| QQ 版本配置 | `./QQ/versions/config.json` | `/root/.config/QQ/versions/config.json` | 读写 | 随 `./QQ` | 用于关闭热更新 | 可选 | **升级镜像后要复查**：新镜像可能重写它并重新打开热更新 |
| OneBot 配置与反向 WebSocket 配置 | `./llonebot` | `/root/llonebot` | 读写 | `./llonebot:/root/llonebot` | 反向 WebSocket 地址与 `AUTH_TOKEN` 都在这里 | 建议（含 Token，按凭据对待） | 保留即可；Kirara 侧改了 `websocket_url` 或 Token 必须同步改这里 |
| OneBot 运行日志与二维码 | `./llonebot`（同上目录下） | `/root/llonebot` | 读写 | 随 `./llonebot` | 二维码生命周期的唯一可读来源（见第六节） | 不需要 | 只读挂进 Kirara 容器即可供状态面板读取 |

> LLOneBot 容器以 `privileged: true` + root 运行，因此这两个宿主目录用默认
> owner 即可；但它们含有可直接登录该 QQ 账号的凭据，**不要**放进任何会被打包、
> 同步或提交的位置。

### 升级兼容策略（Kirara 侧）

- **只挂 `DATA_PATH` 一个目录**：新版本新增的子目录会在启动时自动创建，
  不需要在 compose 里为每个子目录再加一条挂载。
- **数据库自动迁移**：容器启动时执行 alembic 迁移。回滚到旧镜像前先备份
  `data/db/`，旧版本不认识新表结构。
- **凭据文件向后兼容**：`creator.subject` 的旧路径会被自动继承（见上方说明），
  升级不会导致已签发的登录令牌失效。
- **升级前最小备份**：`data/config.yaml`、`data/db/`、`data/web/`、
  `data/workflows/`、`data/dispatch_rules/`。其余目录可重建。

### 启动前置检查

进程启动时会检查并创建上述目录，失败时给出**路径 + 原因 + 处置建议**，例如：

```
无法创建持久化目录 /app/data/plugins：Read-only file system；所在卷为只读挂载，请以可写方式重新挂载。
```

目录存在但不可写（只读挂载最常见）也会在启动阶段被探测出来，
而不是等到第一次写数据库才失败。

## 四、Compose 参考配置

仓库根目录的 `docker-compose.yml.example` 就是这份配置的可执行版本，
并且受 `tests/test_docker_compose_resource_storage.py` 契约测试约束
（服务、共享网络、卷、`DATA_PATH`、PMHQ 只绑本机都会被校验）。
**直接复制那个文件**，不要照抄下面的片段——片段只用于说明每一项的作用。

```bash
cp docker-compose.yml.example docker-compose.yml
cp .env.example .env        # 填入 DOCKERHUB_IMAGE / LLONEBOT1_AUTH_TOKEN / LLONEBOT1_QQ
docker compose up -d
```

```yaml
networks:
  # 显式共享网络：LLOneBot 要用容器名解析 Kirara 的反向 WebSocket 地址。
  # 默认 bridge 下容器名不可解析，这是「配置看起来对但连不上」最常见的原因。
  kirara-net:
    driver: bridge

services:
  llonebot:
    image: initialencounter/llonebot:latest
    container_name: llonebot
    networks: [kirara-net]
    environment:
      # 用 .env 提供，不要写在 compose 文件里
      - AUTH_TOKEN=${LLONEBOT1_AUTH_TOKEN:?AUTH_TOKEN not set}
      - QUICK_LOGIN_QQ=${LLONEBOT1_QQ:?QQ number not set}
    volumes:
      # 这两个挂载决定「重启后是否免扫码」
      - ./QQ:/root/.config/QQ
      - ./llonebot:/root/llonebot
    ports:
      - "3007:3000"               # OneBot HTTP
      - "3004:3001"               # OneBot WebSocket
      - "5600:5600"               # Satori（Kirara 不使用，按需保留）
      - "8888:3080"               # LLOneBot WebUI：首次登录在这里扫码
      - "127.0.0.1:13000:13000"   # PMHQ（只绑本机）
    privileged: true
    restart: always

  kirara-agent:
    # 镜像名走 .env 的 DOCKERHUB_IMAGE，不在文档里钉死厂商账号与版本号
    image: ${DOCKERHUB_IMAGE:?Set DOCKERHUB_IMAGE in the server .env file}
    container_name: kirara-agent
    restart: always
    networks: [kirara-net]
    environment:
      PYTHONDONTWRITEBYTECODE: "1"
      DATA_PATH: /app/data
    volumes:
      # 只需要这一个挂载；全部持久化状态都在里面
      - ./data:/app/data
    ports:
      - "8759:8080"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3).read(1)"]
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

### 反向 WebSocket 地址怎么填

在 LLOneBot WebUI（`http://<宿主机IP>:8888`）里把反向 WebSocket 地址填成：

```
ws://kirara-agent:8080/im/websocket/onebot/<适配器生成的段>/ws
```

- `<适配器生成的段>` 由 Kirara 在「IM 适配器 → 新建 OneBot」时自动生成并展示，
  **不要手写**：它是随机段，写错会得到 404 而不是连接失败提示。
- 用容器名 `kirara-agent` 而不是 `127.0.0.1`：两个容器各有自己的 loopback。
  这也是为什么上面必须有 `networks:`。
- Token 填 `.env` 里的 `LLONEBOT1_AUTH_TOKEN`。请求头与
  `?access_token=...` 查询串两种形式都可以，Kirara 两者都识别。

三点务必注意：

1. **PMHQ 端口只绑 `127.0.0.1`**。它能直接操作 QQ 客户端，暴露到公网等于把账号交出去。
2. `AUTH_TOKEN` 走 `.env`，且 `.env` 已在 `.gitignore` 与 `.dockerignore` 里。
3. **参考镜像不暴露 VNC 端口**（`llonebot.nix` 的 Expose 只有
   3000/3001/5600/3080/13000）。如果你自己的镜像跑了 VNC，原始 VNC 是 5900、
   noVNC 是 6080，同样只绑 `127.0.0.1`。

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

### 二维码七项诊断信息各自在哪

二维码的生成、刷新与过期全部发生在 LLOneBot 容器内，Kirara 既不生成也不代理。
但这些事件都写在日志里，而日志目录（`./llonebot`）已经挂载到宿主机——
只要把它也让 Kirara 读到，就能把「这张码还能扫吗」变成一个**接口可回答**的问题，
而不必让操作者翻 scrollback 猜哪一行是最新的。

启用方式：在 OneBot 适配器配置里填 `qr_login_log_path`（容器内路径）。
留空则完全不读任何文件，这一项返回 `null`。

```yaml
# compose：把 LLOneBot 的日志目录也挂给 kirara-agent（只读即可）
  kirara-agent:
    volumes:
      - ./data:/app/data
      - ./llonebot:/upstream/llonebot:ro
```

配好后 `GET /backend-api/api/system/readiness` 与 IM 适配器接口都会带上
`qr_login`，字段与来源对应关系如下：

| 需要的信息 | 快照字段 | 来源日志行 |
|---|---|---|
| 有效期 | `validity_seconds` | `onQRCodeGetPicture expireTime=`（实测 120） |
| 生成时间 | `generated_at` | 同上那行的时间戳 |
| 剩余时间 | `remaining_seconds` | 由 `generated_at + validity` 与当前时刻算出 |
| 当前状态 | `state` | 见下表九种取值 |
| 刷新动作 | `refresh_count` | 每出现一次新的 `onQRCodeGetPicture` 记一次 || 失败原因 | `failure_reason` | `onLoginFailed` / `QR code unavailable` / `uin not in saved-credential list` |
| 最新二维码路径 | `latest_qr_path` | `二维码文件已保存: <path>` |

`state` 的九种取值：

| 状态 | 含义与处置 |
|---|---|
| `unknown` | 还没有可解读的事件；确认日志路径配对 |
| `pending` | 已请求，等待上游出图 |
| `waiting_scan` | 有有效二维码，去扫最新路径下那张 |
| `scanned` | 已扫码，去手机 QQ 确认 |
| `expired` | 已过期；上游会自动生成新的，取最新路径 |
| `succeeded` | 已登录，登录态已落盘，后续重启免扫 |
| `failed` | 明确失败，查上游错误码 |
| `unavailable` | 上游暂时给不出图；**启动期正常**，持续出现才排查 |
| `quick_login` | 走免扫码快速登录，本轮不会有二维码 |

三条设计约束，直接影响你怎么读这些字段：

1. **过期由时钟判定，不等上游那行日志。** 等它就会在这段时间里一直把死码显示成
   有效——这正是「二维码总是过期」的根因：面板说有效，手机说过期。
2. **`unavailable` 不是 `failed`。** 前者是启动期噪声，后者是真失败。
   混成一个「出错」会让人在正常启动过程里白等或白重启。
3. **快照里没有任何账号标识。** 日志含 uin、uid、昵称与头像地址，这些一个都不会
   出现在接口响应里；登录状态面板不该成为账号身份泄露的地方。
4. **`refresh_count` 是观测值，不是可点的按钮。** 二维码由 LLOneBot 自己刷新
   （过期后自动出新图），Kirara 只读日志、不向上游请求刷新——因此这里没有
   「刷新」接口。需要一张新码时的正确动作是**取最新路径下那张**，
   而不是找一个能点的按钮：给出一个假的刷新入口比不给更糟，
   点了没反应会让人以为上游挂了。

Kirara 侧的**连接层**状态与原因码是另一层（本文第二节的八态与
`last_disconnect_reason`）：QQ 是否登录成功会体现为
`external_login_status`，而握手被拒会给出 `credential_rejected` /
`upstream_refused` 加具体原因码。这两层不要混：
「二维码过期」是 QQ 登录问题，「凭据被拒」是 Kirara 与 OneBot 之间的 Token 问题，
处置完全不同。

readiness 的 `im_available` 会同时给出这两层：连接层是
`waiting_count` / `credential_rejected_count` 等，登录层是 `qr_waiting_scan`、
`qr_expired` 这类计数。只差扫码时 remediation 直接说「去扫码」，
而不是让你继续查地址与 Token——那是这类报障里最常见的误诊方向。

## 七、回复慢：先分段，再定位

「系统显示成功但 QQ 很久才收到」不能笼统归为「QQ 慢」。
每条回复现在都会记录端到端时间线，各阶段分别是：

| 阶段 | 含义 |
|---|---|
| `received_event` | 收到 IM 事件 |
| `workflow_started` | 工作流 / Agent 开始执行 |
| `llm_first_byte` | 模型首字节。**只有流式模式测得到** |
| `llm_completed` | 模型输出完成 |
| `formatting_started` / `formatting_completed` | 排版与分页 |
| `send_started` | 开始调用 OneBot 发送 |
| `send_succeeded` / `send_failed` | 发送结果，带重试次数 |

据此换算出的耗时字段：`queue_seconds`、`llm_first_byte_seconds`、
`llm_generation_seconds`、`formatting_seconds`、`send_seconds`、`total_seconds`。
**没测到的阶段不会给出 0**，而是不出现——「没测到」和「耗时为零」是两件事。

> **想拆开「模型思考」和「模型吐字」，需要开流式**：把 `reply_stream_mode`
> 设为 `aggregate`，`llm_first_byte_seconds` 与 `llm_generation_seconds` 才会有值。
> 非流式请求在 HTTP 响应到达前没有任何可观测的中间事件，因此这两项留空。
> 用户看到的回复内容与非流式完全一致——流式在这里换来的是可观测性与
> 「首字节之前可以安全切换 Provider」，不是打字机效果。

排查顺序：

1. `llm_generation_seconds` 大 → 是模型慢，不是 QQ 慢。去 LLM 统计页看
   该 Provider 的平均首字节与失败率。
2. `send_seconds` 大 → OneBot 侧慢或限流。查 LLOneBot 日志。
   日志里的 `retcode` 决定了投递队列怎么处理这一页，三类语义不同：

   | `retcode` | 含义 | 队列行为 |
   |---|---|---|
   | `429` / `503` | HTTP 语义透传，动作**尚未开始** | 「指数退避 + 抖动 + 5 分钟上限」有限重试 |
   | `1200` | 动作处理器已开始执行后抛错，**结果未知** | 标记 `ambiguous`，**不重发**（重发可能造成重复消息） |
   | `1400` | payload 校验失败，同一份内容永远不会通过 | 一次 `dead_letter`，不重试 |
   | 其他 | 明确拒绝（参数、权限等） | 一次 `dead_letter` |

   看到 `1200` 时应当去 QQ 客户端确认那一页**是否已经收到**：
   队列刻意不替你决定，因为「丢一页」有记录可查，「重复发送」直接呈现给用户。
3. `queue_seconds` 大 → 事件在进入工作流前排队。查是否有并发瓶颈。
4. 各段都不大但用户仍觉得慢 → 看分页数：长回复会分成多条按序发送，
   最后一页到达时间自然晚于第一页。**另外页与页之间有主动节流**（见下），
   十页的回复会比单页多等十秒左右——那是有意为之，不是卡住。

### 发送节流：宁可慢一点，也不要被风控

多页回复的**页与页之间**会主动等待，默认开启。等待时长 =
`每字符 0.1 秒`（下界 1 秒）+ `0~1 秒随机抖动`，上界 8 秒；第一页不等。

这与投递队列的失败重试退避是**两件不同的事**，方向相反：

| | 触发时机 | 目的 |
|---|---|---|
| 失败重试退避（`outbox_retry_delay_seconds`） | 这一页**发失败了** | 等一会儿再试一次 |
| 发送节流（`send_pacing_*`） | 这一页**发成功了** | 下一页不要被判定为刷屏 |

为什么必须有它：QQ 对短时间内连发多条消息有风控，命中之后账号被限制发言。
**这种故障的表现和「发送失败」完全不同**——所有接口都返回成功、日志里一切正常，
可消息到不了对方，而且要等很久才恢复。排查时最容易走的弯路就是去查投递队列，
因为队列显示「全部已投递」。

五个配置项在「即时通讯 → OneBot」里：

| 配置 | 默认 | 何时改 |
|---|---|---|
| `send_pacing_enabled` | `true` | 本地自建、内网、压测可关。生产环境不建议关 |
| `send_pacing_per_character_seconds` | `0.1` | 长文本本身已占用阅读时间；短文本连发才敏感 |
| `send_pacing_minimum_seconds` | `1.0` | 曾被风控过的账号可调大 |
| `send_pacing_jitter_seconds` | `1.0` | 抖动是必要的：固定间隔本身就是可识别的机器特征 |
| `send_pacing_maximum_seconds` | `8.0` | 上界。风控看频率而非「等得够久」，再等只是惩罚用户 |

两条投递路径（直发与 outbox）都会节流。只在一条上做是半个修复：
走哪条取决于部署有没有配数据库，而风控与这个无关。

> **超长回复现在会被截断而不是丢失**：四个渠道（OneBot / QQ 机器人 /
> Telegram / WeCom）超出页数或总字节预算时，都会发送前 N 页并在末页附上
> 「已截断」提示，同时在服务端记一条 warning。此前 QQ 机器人、Telegram、
> WeCom 会让分页异常穿出发送路径，表现为**用户什么都收不到**——排查时
> 极易被误判成「上游没收到」。看到截断提示说明该缩小提问范围或分次获取。

Telegram 与 WeCom 使用同一套阶段命名，可直接横向比较同一问题在三个渠道的耗时。
Agent 路径与遗留工作流路径也用同一套口径落库，两者可以直接比较。

这些耗时同时落库（表 `im_delivery_timings`），因此可以按时间范围回查：

```bash
# 上周二 QQ 慢在哪一段
curl -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8080/backend-api/api/tracing/delivery/summary?channel=onebot&start_time=2026-08-25T00:00:00%2B08:00&end_time=2026-08-26T00:00:00%2B08:00"
```

返回里每个阶段都带 `samples`（样本数）：非流式请求没有首字节，
这类记录不会被按 0 计入平均值，因此 `llm_first_byte_seconds` 的样本数
可能小于总投递数。表中只有时长与计数，**不含任何消息正文**，会话键以摘要存储。

## 八、Compose 验收矩阵

每次改动 Compose 或升级镜像后，建议按下表逐项过一遍：

| 场景 | 预期状态 | 日志证据 | 恢复上限 | 需人工扫码 |
|---|---|---|---|---|
| 首次启动（无 `./QQ`） | `waiting` → 扫码 → `connected` | 上游 `onQRCodeGetPicture`；`qr_login.state=waiting_scan` | 取决于扫码 | 是 |
| 已有登录态重启 | `initializing` → `waiting` → `connected` | 上游 `quickLoginWithUin`；`qr_login.state=quick_login` | 3 分钟 | 否 |
| `pull` 后重建 | 同上 | 同上 | 3 分钟 | 否 |
| 单个 QQ 实例故障 | 故障实例 `stale`，其余 `connected` | `last_disconnect_reason=heartbeat_timeout` | 90 秒转 `stale` | 否 |
| 多个 QQ 实例并存 | 各自独立 `connected` | 各自 `self_id`；`connected_account_count` 等于实例数 | 3 分钟 | 否 |
| Kirara 先启动 | `waiting` → `connected` | Kirara 侧无接入日志，直到上游拨入 | 3 分钟 | 否 |
| QQ 先启动 | 上游按自身间隔重连直到 Kirara 就绪 | **上游侧**日志（LLOneBot 的重连记录；Kirara 侧只会看到最终的 `lifecycle connect`） | 3 分钟 | 否 |
| OneBot 断线后恢复 | `connected` → `stale` → `connected` | `OneBot 连接已断开` 与 `OneBot 连接已建立` 各一条 | 90 秒 + 上游重连间隔 | 否 |
| Token 配错 | `credential_rejected` | `last_disconnect_reason=access_token_mismatch` | 不会自愈 | 否，改配置 |
| 数据目录只读 | 启动即失败并给出原因 | `所在卷为只读挂载，请以可写方式重新挂载` | 不会自愈 | 否，改挂载 |
| 网络暂不可用 | `waiting` | 无接入日志 | 网络恢复后 3 分钟 | 否 |

> **本表未在真实 Docker 环境中逐项跑过。** 状态转换与恢复逻辑有自动化测试覆盖
> （`tests/plugins/im_onebot_adapter/test_connection_states.py`），
> 但真实容器重启、真实扫码、真实 PMHQ 注入属于外部环境，
> 需要你在自己的部署上按此表验收。

> **「重连」由谁负责**：Kirara 是反向 WebSocket 的**服务端**，不主动拨号，
> 因此仓库内没有客户端重连循环，也就没有「Kirara 侧的重连退避」。
> 重连间隔由 OneBot 实现决定（OneBot 11 只规定了固定间隔
> `ws_reverse.reconnect_interval`，默认 3000 ms，无指数退避）。
> Kirara 侧有上限退避的是**出站投递重试**
> （`kirara_ai/im/outbox_backoff.py`：指数增长 + 抖动 + 5 分钟上限），
> 那是「消息发送失败后何时重试」，与「连接何时重建」是两件事，不要混。
> 上表「恢复上限 3 分钟」指的是上游按其自身间隔重连后本侧应当转为 `connected`
> 的观察窗口，不是本侧的重试计划。

## 九、入站消息段：哪些能识别，识别成什么

一条 QQ 消息由若干「段」拼成。没有对应分支的段会被忽略，而**忽略一段是安全的、
忽略整条不是**：一条只含 `poke` 的消息若所有段都被丢掉，元素列表为空，
用户看到的是「机器人毫无反应」——那和「机器人挂了」在观感上一模一样。

| 段 | 识别为 | 说明 |
|---|---|---|
| `text` | 文本 | — |
| `at` | 提及 | 只有 @ 到本账号时才产生提及元素 |
| `reply` | 引用 | — |
| `image` / `record` / `video` / `file` | 对应媒体 | 优先用已下载的数据，否则用 URL |
| `mface` | 图片或占位文本 | 市场表情；只有摘要时给可读占位 |
| `face` | 表情 | — |
| `json` | JSON 卡片 | 原样交给下游 |
| `markdown` | **文本（原样保留正文）** | `content` 就是正文，不换占位 |
| `forward` | 占位文本，或展开后的内容 | 合并转发；默认只给占位，打开 `expand_forward_messages` 后取回真实内容（见下） |
| `dice` / `rps` / `shake` | 占位文本 | 客户端渲染为动画，纯文本侧只能给结果 |
| `poke` | `[拍一拍]` | 交互动作 |
| `location` | `[位置：标题]` 或 `[位置：纬度,经度]` | 有标题优先用标题 |
| `contact` | `[推荐联系人：ID]` / `[推荐群：ID]` | 群与好友分开表达 |
| `share` / `music` | `[分享：标题]` | 有标题优先用标题 |
| `xml` | `[XML 卡片]` | 平台私有结构，不展开 |
| `anonymous` | `[匿名]` | 匿名发言标记 |
| 其他（含上游私有扩展） | 忽略 | 给每个未知类型造占位会让任何私有扩展都在回复里留噪声 |

两条刻意的边界：

- **占位就是占位，不伪装成富媒体。** `location` 不是图片、`contact` 不是文件，
  硬映射成那些类型会让下游按错误的方式处理它们。
- **`markdown` 例外。** 它的 `content` 是真实正文，换成 `[Markdown]` 是丢内容，
  比丢一个交互动作严重得多。排版随后由统一的渲染层处理（见第七节）。

### 展开合并转发：默认关闭，打开后有三道边界

占位（`[合并转发：<id>]`）在「不静默丢消息」这一层是对的，但它把内容也一起丢了：
用户转发一段对话过来问「这里说的对吗」，模型收到的只有一个 ID。

在 OneBot 适配器配置里打开 `expand_forward_messages` 后会调 `get_forward_msg`
把内容取回来，渲染成带发言人的缩进文本。**默认关闭**——每段转发多一次上游调用。

| 配置 | 默认 | 作用 |
|---|---|---|
| `expand_forward_messages` | `false` | 是否展开。关闭时行为与升级前逐字节一致 |
| `forward_max_depth` | `2` | 嵌套层数上限。转发里可以再包转发，无界递归会把一次消息转换变成一串上游调用 |
| `forward_max_nodes` | `20` | 单段最多展开多少条。一段转发可能几百条，全部展开会让提示词爆掉，随后排版层又要切成几十页 |

四条行为值得单独记住：

- **失败退回占位。** 展开是增强而不是前提：`get_forward_msg` 权限不足、ID 过期或
  上游未实现时退回原来的占位并记一条日志，绝不让整条消息失败。
- **自引用只请求一次。** 转发引用自己是最短的无限递归，第一次重复就停。
- **超出条数会明确标注**「已省略 N 条」。静默截断会让人以为转发里只有那么几条。
- **转发里的媒体不下载**，只给 `[图片]` / `[语音]` / `[视频]` / `[文件]` 标记——
  下载它们会把一次消息转换变成一串下载。

### 好友申请与入群邀请：记录，但不自动同意

这两类事件会写进日志，**并带上处置所需的 `flag`**：

```
OneBot 好友申请（friend，c2c，flag=abc123）待人工处理；框架不会自动同意。
```

`flag` 是必须的：处置动作用它标识具体哪一条申请，而运维唯一能看到事件的地方
就是日志。只记「有一条好友申请」等于把处置能力锁死在日志里。

框架**不自动同意**——自动接受入群邀请是一个安全决定，不该由框架代替部署者做。
但协议本来就有对应动作，适配器把它们暴露出来供部署者自己决定：

| 方法 | 对应 OneBot 动作 |
|---|---|
| `approve_friend_request(flag, remark="")` | `set_friend_add_request`（approve=true） |
| `reject_friend_request(flag)` | `set_friend_add_request`（approve=false） |
| `approve_group_request(flag, sub_type=...)` | `set_group_add_request`（approve=true） |
| `reject_group_request(flag, sub_type=..., reason="")` | `set_group_add_request`（approve=false） |

两处会**在发出前就拒绝**，因为它们的失败形态是「返回成功但什么都没做」——
最难排查的一类：

- **群申请必须指明 `sub_type`**：`add` 是「别人申请加入我在的群」，
  `invite` 是「别人邀请我进群」，两件不同的事。传错上游匹配不到那条请求。
  这里刻意不设默认值。
- **多账号部署必须指明 `self_id`**：同意一个入群邀请有副作用，
  用错账号同意等于让另一个机器人进了群。

## 十、多账号
一个 OneBot 反向 WebSocket 可以复用多个 QQ 账号（不同 `self_id`）。
Kirara 按事件里的 `self_id` 区分账号，因此：

- 多账号在线时，发送操作**必须**能确定目标账号。无法确定时会直接拒绝，
  而不是随便选一个发出去——发错账号比不发更糟。
- Agent 可以按「渠道 → 账号 → 会话」三级绑定，账号级绑定就是用这个 `self_id`。
- 每个 LLOneBot 实例需要**独立的** `websocket_url`；路径冲突会在启动时报错。

### 两种多账号拓扑，选一种

**A. 一个账号一个容器（推荐，也是 `docker-compose.yml.example` 的形态）**

每个 QQ 账号一个 LLOneBot 容器，在 Kirara 里各建一个 OneBot 适配器实例：

| 项 | 账号一 | 账号二 |
|---|---|---|
| 容器名 | `llonebot` | `llonebot2` |
| 登录态卷 | `./QQ` | `./QQ2` |
| OneBot 配置卷 | `./llonebot` | `./llonebot2` |
| WebUI（扫码） | `8888` | `8889` |
| OneBot HTTP / WS | `3007` / `3004` | `3008` / `3006` |
| PMHQ（只绑本机） | `13000` | `13001` |
| `.env` 变量 | `LLONEBOT1_*` | `LLONEBOT2_*` |

**两个实例绝不能共用 `./QQ`**：登录态与设备标识都在里面，共用会互相覆盖，
现象是「登录一个就把另一个挤下线」，而排查时看起来像 QQ 侧的随机掉线。
端口也必须整组错开，否则第二个容器直接起不来。
这两条都有契约测试守着（`tests/test_docker_compose_resource_storage.py`）。

好处是账号级隔离：健康面板上每个账号有独立的连接状态与失败原因码，
一个账号掉线不影响另一个的判读。

**B. 两个账号共用一个适配器实例**

OneBot 允许多个账号共享一条反向连接，Kirara 按 `self_id` 区分。配置更省，
但连接状态与 `last_disconnect_reason` 在面板上合并成一条——出问题时分不清是
哪个账号。除非确有理由，用 A。

只跑一个账号时：删掉 `llonebot2` 服务，`.env` 里的 `LLONEBOT2_*` 留空即可。
Compose 只对实际启动的服务求值 `:?`，未使用的变量留空不会报错。

## 十一、相关文档

- [`QUICKSTART.md`](QUICKSTART.md) — 首次部署与第一条回复
- [`OBSERVABILITY.md`](OBSERVABILITY.md) — 日志、追踪与统计
- [`UPGRADING.md`](UPGRADING.md) — 升级与回滚
- [`AGENTS_SKILLS_HOOKS_MCP_GUIDE.md`](AGENTS_SKILLS_HOOKS_MCP_GUIDE.md) — 扩展与权限边界
