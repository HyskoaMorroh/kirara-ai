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

## 二、九种连接状态与各自的处置

适配器详情页的状态标签直接对应后端的 `AdapterHealthSnapshot.status`。
**这九种状态不能混为一谈**——它们的处置动作完全不同：

| 状态 | 界面文案 | 含义 | 该做什么 |
|---|---|---|---|
| `initializing` | 正在启动 | 适配器还没完成 `start()`，**或**刚起来还在首次连接宽限期内 | **什么都不用做**，等上游冷启动完 QQ 再拨进来 |
| `waiting` | 等待连接 | 首次连接宽限期已过，仍然没有任何上游接入 | 查 OneBot 侧的反向地址是否填对、网络是否可达 |
| `connected` | 已连接 · N 个账号 | 至少一个账号在线 | 无需处理 |
| `reconnecting` | 正在重连 | 曾经连上，刚掉线且仍在重连宽限期内 | **什么都不用做**，等它自己回来 |
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

> **`reconnecting` 是这张表里唯一一个不需要动手的状态**，也正是
> 「`docker compose down && pull && up -d` 之后 QQ 显示未连接」这个现象的答案。
> OneBot 实现掉了反向 WebSocket 会按自身间隔回连（LLOneBot 默认 3000 ms），
> 此时什么都不用做；而「未连接」这个词会让人去重查地址与令牌——那两项从来没错。
>
> 宽限期由适配器配置里的 `reconnect_grace_seconds` 控制，**默认 45 秒**。
> 超过它仍未连上就转为 `disconnected`：连着十分钟「正在重连」的链路就是断了，
> 继续显示等待状态只是换个措辞掩盖故障。填 `0` 关闭该状态，拿回旧行为。
>
> 三条不会被它盖住的状态：凭据被拒、握手被拒、存储不可写。那三类都要求操作者
> 动手改点什么，显示成「正在重连」会让他一直等一件不会自己好的事。
> 从未连上过的适配器不会进入这个状态——那时的处境是「还在等第一次连接」，
> 由下面的 `initializing` 覆盖，两者宽限期长度不同。

> **`initializing` 覆盖的是冷启动那段空窗**，也就是
> 「`docker compose down && pull && up -d` 之后 QQ 显示未连接」的另一半答案。
> 反向 WebSocket 由 OneBot 实现主动拨入，而它要先冷启动 QQ 再完成登录——
> 现场日志里这一段跨了 19 分钟（`05:37:19` 容器启动，`05:56` 才 `QQ 登录成功`），
> 最快也在 90 秒以上。这段时间里 Kirara 侧**不可能**有连接：它是服务端，只能等。
>
> 此前这段时间报 `waiting`，readiness 随之给出「检查 IM 适配器运行状态、登录状态
> 和连接心跳」——那是这个窗口里最不该给的建议。心跳、令牌、地址三项都没有问题，
> 上游还没起来而已；照着查一遍全部正常，然后开始怀疑配置，而配置从一开始就是对的。
>
> 宽限期由 `initial_connect_grace_seconds` 控制，**默认 180 秒**（覆盖 QQ 冷启动
> 加登录的实测时长）。超过它仍然没有任何上游接入才转为 `waiting`——那时确实该查了。
> 填 `0` 关闭，拿回旧行为（立刻显示「等待连接」）。
>
> 与 `reconnecting` 的区别是**有没有连上过**：`reconnecting` 的前提是本进程内
> 至少成功连过一次，`initializing` 的前提正相反。手动停掉的适配器显示
> `disconnected` 而不是「正在启动」——运维需要知道它是被停的。

同一份状态也会出现在 `GET /backend-api/api/system/readiness` 的
`im_available` 检查里，`evidence` 中分别给出
`waiting_count` / `reconnecting_count` / `credential_rejected_count` /
`upstream_refused_count` / `initializing_count` / `storage_unavailable_count`，
可用于外部监控。

`reconnecting_count` 单独成项是必需的：把它并进 `disconnected_count` 会让
一次正常的 compose 重启与一次真实故障给出同一个数字，
于是外部告警只能在两者之间二选一——要么漏掉真故障，要么每次重启都误报。

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
| 5 秒–3 分钟 | `initializing` | 等 LLOneBot 冷启动 QQ 并完成登录（首次连接宽限期） |
| 超过宽限期仍无接入 | `waiting` | 该查地址与令牌了 |
| QQ 登录完成后 | `connected` | 出现账号数 |

QQ 侧从容器启动到登录完成通常需要 30–90 秒（要拉起 Electron、注入、登录），
现场日志里见过跨 19 分钟的。**这段时间内看到「正在启动」是正常的**，不要反复重启；
面板每 10 秒自己刷一次，连上了会自动变。

宽限期默认 180 秒（`initial_connect_grace_seconds`）。超过它转为「等待连接」，
那时再按第二节的原因码排查。

### `down` 之前那一次必须是干净停下的

上面所有「重启后恢复」的前提是**上一次进程走完了收尾**：flush 记忆的异步写队列、
把 `sending` 状态的投递隔离成 `ambiguous`、关数据库、有序断开每个适配器。

这件事此前是**不成立**的。`docker/start.sh` 的最后一行原本是
`python -m kirara_ai`——没有 `exec`，于是容器里 PID 1 是 bash，而 bash 在等待子进程
期间不转发信号：`docker compose down` 发来的 SIGTERM 被它吞掉，10 秒后整个容器
被 SIGKILL。`entry.py` 里那段完整的 `finally` **一次都不会跑**。

三个可观察后果：

- 记忆的异步写队列没 flush，最后几条对话记忆丢失；
- `sending` 状态的投递不会被隔离成 `ambiguous`，下次启动的恢复面对的是一份
  不完整的现场；
- 适配器不走 `stop()`，反向 WebSocket 被硬切而不是有序断开。

现已改为 `exec python -m kirara_ai`（Python 直接成为 PID 1），并在两份 compose 里
声明 `stop_grace_period: 60s`——Docker 默认只给 10 秒，超时仍会 SIGKILL，
那样 `exec` 等于白加：信号到了，但没时间用完。

验证方法：

```bash
docker compose exec kirara-agent ps -o pid,comm | head -3   # PID 1 应当是 python
docker compose stop kirara-agent && docker compose logs --tail=40 kirara-agent
```

日志尾部应当看到 `Shutting down memory system...`、`Stopping web server...`
这几行；只看到容器直接消失说明信号仍未到达。

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

### QQ 热更新：先看得见，再决定要不要关

日志里的 `hotUpdate ... startAutoUpdate` 是 QQ 自身的热更新。它会在后台下载
新版本包（几十 MB），与 Kirara 无关，但**会占用带宽并可能拖慢这段时间内的登录与
消息投递**。

**它是「这条回复为什么慢」的一个候选原因，因此必须先可观测。** 现场日志里它与一次
真实对话完全重叠：

```
07:56:20.995 [QQ hotUpdate] onStatusChanged status:  DOWNLOADING
07:56:56.423 [QQ hotUpdate] addressStatusChanged: { status: 'complete',
07:56:56.424 [QQ hotUpdate] onUpdateDownloaded .../3.2.32-52194.zip
[I] core [收-私] ...：写一个回火算法          ← 恰好落在下载完成这一刻
07:57:10.097 [QQ hotUpdate] onStatusChanged status:  READY
```

配好 `qr_login_log_path` 之后，`qr_login.hot_update` 会给出这一轮的状态：

| 字段 | 含义 |
|---|---|
| `state` | `checking` / `up_to_date` / `downloading` / `downloaded` / `ready` / `failed` |
| `target_version` | 目标版本号（如 `3.2.32-52194`）；日志没给出时为 `null`，不猜 |
| `started_at` / `completed_at` | 本轮起止。**这些日志行只有时分秒、没有日期**，因此它们只承诺「时分秒可比」，不要当墙上时间格式化 |
| `duration_seconds` | 下载窗口长度（上例是 35.4 秒）。**还在下载时为 `null` 而不是 0**——0 会被读成「瞬间完成」，正好与「它正在占着带宽」相反 |

`hot_update` 整个为 `null` 表示**日志里没有任何热更新行**，与 `up_to_date`
（检查过、无需更新）是两件事：前者可能只是日志没挂全。

WebUI 在「机器人」页把它显示成**一枚独立标签**，只在 `downloading` 与 `failed`
时出现——它与扫码状态是两条独立的线（热更新不影响「这张码能不能扫」），
合并显示会让「正在下载更新」顶掉「等待扫码」，而后者才是操作者此刻要看的。
`ready` / `up_to_date` 不显示：它们此刻不影响任何事，常驻一枚「已就绪」只会挤占
状态区，让真正需要注意的标签更难被看见。

要彻底关掉：LLOneBot 镜像通过 `versions/config.json` 关闭它，该文件需要随 `./QQ`
一起持久化。

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
| 生成时间 | `generated_at` | 同上那行的时间戳；**日志无时间戳时为 `null`** |
| 剩余时间 | `remaining_seconds` | 由 `generated_at + validity` 与当前时刻算出；无生成时刻时为 `null` |
| 当前状态 | `state` | 见下表十种取值 |
| 刷新次数 | `refresh_count` | 每出现一次新的 `onQRCodeGetPicture` 记一次 |
| 失败原因 | `failure_reason` | `onLoginFailed` / `QR code unavailable` / `uin not in saved-credential list` |
| 最新二维码路径 | `latest_qr_path` | `二维码文件已保存: <path>` |

> **有些 PMHQ 构建的二维码日志行不带任何日期**，例如
> `[PMHQ login] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68`。
> 这时无从得知那张码是什么时候生成的，`generated_at` 与 `remaining_seconds`
> 一律返回 `null`，`state` 是 `age_unknown`。
>
> 为什么不拿「读日志的时刻」当生成时刻：那会让每一张码都报「还剩 120 秒」，
> 无论它实际上是十分钟前生成的——面板永远说有效，手机永远说过期，而这正是
> 「二维码总是过期」这个报障的形态。拿不到生成时刻时唯一诚实的答复是「不知道」，
> 而它指向的动作恰好是对的：点「刷新扫码状态」取最新一张再扫。
>
> `validity_seconds` 仍然给出——`expireTime= 120` 是日志里的真实信息，
> 它回答「这种码能撑多久」，与「这张还剩多久」是两个问题。

**刷新动作**是一个独立入口，不是上面那个计数：

```
POST /backend-api/api/im/adapters/<adapter_id>/qr-login
```

WebUI 在「机器人」页每一行给出「刷新扫码状态」按钮（只在该适配器真的有扫码
环节时出现——给 Telegram 放一个永远无事可做的按钮比没有按钮更让人困惑）。

它存在的理由是时间尺度：二维码有效期实测 120 秒，而适配器状态的自动刷新是 10 秒
一轮——足以看到「正在启动 → 已连接」这类迁移，但对「这张码还剩几秒」仍然太慢。
操作者的真实动作序列是「打开面板看到一张码 → 走去拿手机 → 回来扫」，第三步时屏幕上
那张常常已经过期，而上游其实早就生成了新的。手动刷新是这个尺度上唯一确定的动作。

> **面板本身每 10 秒自己刷一次**（与容错面板同一间隔），实例列表上方有开关可关。
> 这一点是「正在启动，等就行」这句处置建议成立的前提：此前这一页只在打开时拉一次，
> 于是用户重启容器后看到「正在启动」，然后一直是「正在启动」——上游两分钟前就连上了，
> 他要手动刷新整页才知道。排查时可以关掉，避免正在读某个状态时它被刷走。
>
> 倒计时是另一回事：那个每秒重算，但**只算本地时间**，不发请求。

**这个动作只重读日志，不让上游重新生成。** 生成方是 LLOneBot / PMHQ 自己，
它在过期时会自行重新请求。把「重新生成」写进按钮文案是对所有权的谎报：
点了没反应时，操作者会去排查 Kirara，而要看的是上游容器。

三种「拿不到」在响应里严格分开，因为处置完全不同：

| 响应 | 含义 | 该做什么 |
|---|---|---|
| 404 | 没有这个适配器 | 查 adapter_id |
| `supported: false` | 该适配器没有扫码环节（Telegram / WeCom） | 无需处理 |
| `supported: true` + `qr_login: null` | 支持，但没配 `qr_login_log_path` | 填那个配置项，不是查挂载 |

响应里没有二维码内容，只有路径：二维码是登录凭据材料，状态面板不该成为它
流经的地方；扫码在上游自己的 WebUI（8888 / 8889）完成。

`state` 的十种取值：

| 状态 | 含义与处置 |
|---|---|
| `unknown` | 还没有可解读的事件；确认日志路径配对 |
| `pending` | 已请求，等待上游出图 |
| `waiting_scan` | 有有效二维码，去扫最新路径下那张 |
| `age_unknown` | 有二维码，但日志没给时间戳，无从判断是否还有效；**先刷新再扫** |
| `scanned` | 已扫码，去手机 QQ 确认 |
| `expired` | 已过期；上游会自动生成新的，取最新路径 |
| `succeeded` | 已登录，登录态已落盘，后续重启免扫 |
| `failed` | 明确失败，查上游错误码 |
| `unavailable` | 上游暂时给不出图；**启动期正常**，持续出现才排查 |
| `quick_login` | 走免扫码快速登录，本轮不会有二维码 |

界面上这一行还带一个**会自己走的倒计时**（`waiting_scan` 时显示「剩 N 秒」）。
它按 `expires_at` 每秒重算，而不是照抄取快照那一刻的 `remaining_seconds`——
后者在 120 秒的尺度上必然说谎，且说的谎恰好是「还来得及」：用户走开去拿手机再
回来，屏幕上还写着打开页面时的那个读数。倒数到 0 时标签自动改成「二维码已过期」
并提示去刷新，而不是继续显示「待扫码（剩 0 秒）」。

三条设计约束，直接影响你怎么读这些字段：

1. **过期由时钟判定，不等上游那行日志。** 等它就会在这段时间里一直把死码显示成
   有效——这正是「二维码总是过期」的根因：面板说有效，手机说过期。
   前提是拿到了真实的生成时刻；拿不到时报 `age_unknown` 而不是编一个满额剩余时间。
2. **`unavailable` 不是 `failed`。** 前者是启动期噪声，后者是真失败。
   混成一个「出错」会让人在正常启动过程里白等或白重启。
3. **快照里没有任何账号标识。** 日志含 uin、uid、昵称与头像地址，这些一个都不会
   出现在接口响应里；登录状态面板不该成为账号身份泄露的地方。
4. **`refresh_count` 是观测值，不是可点的按钮。** 二维码由 LLOneBot 自己刷新
   （过期后自动出新图），Kirara 只读日志、不向上游请求刷新——因此这里没有
   「刷新」接口。需要一张新码时的正确动作是**取最新路径下那张**，
   而不是找一个能点的按钮：给出一个假的刷新入口比不给更糟，
   点了没反应会让人以为上游挂了。

Kirara 侧的**连接层**状态与原因码是另一层（本文第二节的九态与
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
   但整次投递的节流总额有上界，不会随页数线性累加。

### 发送节流：宁可慢一点，也不要被风控

多页回复的**页与页之间**会主动等待，默认开启，第一页不等。等待时长由两部分组成：

- **下界**（`send_pacing_minimum_seconds`，默认 1 秒）：每个间隙都要付。
  它是「不把两条消息紧贴着发出去」的硬保证，也正是风控真正针对的行为。
- **按长度追加的部分**（`send_pacing_per_character_seconds`，默认每字符 0.1 秒）：
  受 `send_pacing_maximum_total_seconds`（默认 6 秒）约束，按这次投递的间隙数摊开。

抖动围绕算出的值双向摆动（`±send_pacing_jitter_seconds`），并夹在
`[下界, send_pacing_maximum_seconds]` 之内。

> **为什么必须有那个总额上界**：`0.1 秒/字符` 这个系数是从被融入的 OneBot 适配器
> 项目照搬的，而那个项目按**消息段**计费——一个文本段通常只有几十个字符。
> 本项目先分页再发送，一页 3800 字节（约 1300–1900 字符）。同一个系数换了计费
> 单位之后，长度项在 **80 字符**就撞上 8 秒的单页上界，于是：
>
> - 「按长度递增」对任何真实页面都不再起作用，每一页算出同一个数；
> - 抖动被上界一起裁掉，页间间隔变成恒定的 8.000 秒——而那是最可识别的机器特征，
>   恰好违反引入抖动的初衷；
> - 代价随页数线性累加：一条 4578 字符的回复分成 3 条要纯等 13.7 秒，
>   带 3 个代码块的回复分成 10 条要纯等 54.6 秒。
>
> 而现场报障正是「系统显示成功，QQ 却要等很久才收到」，且 Telegram 与 WeCom
> 没有这个现象——后半句成立的原因很直接：节流全仓只有 OneBot 一家在用。
>
> 加上总额上界之后，同样那条 3 页回复的纯等待降到 6–10 秒，10 页的从 72 秒
> 降到 24 秒以内，而每个间隙仍然至少有一秒。

这与投递队列的失败重试退避是**两件不同的事**，方向相反：

| | 触发时机 | 目的 |
|---|---|---|
| 失败重试退避（`outbox_retry_delay_seconds`） | 这一页**发失败了** | 等一会儿再试一次 |
| 发送节流（`send_pacing_*`） | 这一页**发成功了** | 下一页不要被判定为刷屏 |

为什么必须有它：QQ 对短时间内连发多条消息有风控，命中之后账号被限制发言。
**这种故障的表现和「发送失败」完全不同**——所有接口都返回成功、日志里一切正常，
可消息到不了对方，而且要等很久才恢复。排查时最容易走的弯路就是去查投递队列，
因为队列显示「全部已投递」。

六个配置项在「即时通讯 → OneBot」里：

| 配置 | 默认 | 何时改 |
|---|---|---|
| `send_pacing_enabled` | `true` | 本地自建、内网、压测可关。生产环境不建议关 |
| `send_pacing_per_character_seconds` | `0.1` | 长文本本身已占用阅读时间；短文本连发才敏感 |
| `send_pacing_minimum_seconds` | `1.0` | 曾被风控过的账号可调大 |
| `send_pacing_jitter_seconds` | `1.0` | 抖动是必要的：固定间隔本身就是可识别的机器特征 |
| `send_pacing_maximum_seconds` | `8.0` | 单个间隙的上界 |
| `send_pacing_maximum_total_seconds` | `6.0` | 整次回复按长度追加的等待总额。调小让长回复更快，调大更保守 |

两条投递路径（直发与 outbox）都会节流，且都会把总页数传给节流器。只在一条上做
是半个修复：走哪条取决于部署有没有配数据库，而风控与这个无关；漏传总页数则会
静默回到「按单页上界累加」，症状就是上面那条报障。

> **超长回复现在会被截断而不是丢失**：四个渠道（OneBot / QQ 机器人 /
> Telegram / WeCom）超出页数或总字节预算时，都会发送前 N 页并在末页附上
> 「已截断」提示，同时在服务端记一条 warning。此前 QQ 机器人、Telegram、
> WeCom 会让分页异常穿出发送路径，表现为**用户什么都收不到**——排查时
> 极易被误判成「上游没收到」。看到截断提示说明该缩小提问范围或分次获取。

### QQ 的排版符号表：解析共享，符号各出一份

QQ 的消息是纯文本，没有任何富文本渲染——`**粗体**` 在屏幕上就是五个字加两个星号。
因此每种 Markdown 标记都要换成一个 QQ 上有语义的符号：

| Markdown | QQ / OneBot | 企业微信 | 为什么不一样 |
| --- | --- | --- | --- |
| `# 一级标题` | `■ 标题` | `━━━ 标题 ━━━` | QQ 气泡更窄（见下节的宽度推导），两侧长横线会把标题挤到折行 |
| `## 二级标题` | `▎标题` | `━━ 标题 ━━` | 同上 |
| `### 三级及以下` | `· 标题` | `▸ 标题` | — |
| `**粗体**` | `【粗体】` | `「粗体」` | QQ 群聊里方头括号更接近「重点」的语感 |
| `*斜体*` / `_斜体_` | 去掉标记 | 去掉标记 | 两个平台都没有斜体，留着星号只是噪声 |
| `~~删除~~` | 去掉标记 | 去掉标记 | 同上，保留内容比保留标记重要 |
| `` `代码` `` | `「代码」` | `『代码』` | 反引号在 QQ 上就是两个可见字符，而它要表达的是「这是一个标识符」 |
| `- 列表项` | `• 列表项` | `• 列表项` | 一致 |
| `> 引用` | `┃ 引用` | `┃ 引用` | `>` 在 QQ 上不表示引用，只是一个大于号 |
| `[文本](url)` | `文本（url）` | `文本 (url)` | URL 必须留着——删掉等于给出一个点不开的词 |
| ```` ```代码块``` ```` | **原样保留围栏** | `［代码］…［/代码］` | QQ 侧「代码单独成条」这条复制路径靠围栏识别，渲染掉围栏会让它失效 |

**解析是共享的，只有符号表按平台各出一份。** 项目的约定是不允许各平台各写一套
Markdown 解析——那会让「四反引号里包三反引号」这类边界在每个平台上各错一次。
块结构由 `parse_text_document` 统一解析，`render_rich_text` 拿着平台的符号表渲染。

> **未闭合的围栏不补闭合。** 解析器把它也收成代码块（否则后面的内容会散成正文），
> 但渲染时补一个闭合围栏，会把一条被上游截断的回复里剩下的正文变成合法代码块——
> 随后「代码单独成条」判它是代码并跟上一句「长按可整段复制」，而用户复制走的是
> 半句话。企业微信侧同理：补 `［/代码］` 等于宣称「代码到这里结束」，
> 而事实是上游被截断了。

官方 QQ 机器人渠道（QQBot）共用这张表：两条接入方式面对的是同一个 QQ 客户端，
给出两种排版会让用户以为在跟两个不同的机器人说话。

### 围栏代码：三种写法都识别，四渠道同一套管线

模型写代码块有三种合法写法，**现在都识别**：

| 写法 | 场景 |
|---|---|
| ```` ```lang ```` | 最常见 |
| `~~~lang` | CommonMark 同等合法，部分模型偏好它 |
| ```` ````lang ```` （四个及以上） | 正文里要展示 Markdown 本身时，用更长的围栏把 ```` ``` ```` 包住 |

不识别围栏的后果不是「少一个代码框」，而是**代码块内部被当成正文处理**：
数学降级会改写代码里的 `$x$`、表格渲染器会改写代码里的 `|`、分页会把代码
劈开且不补围栏、「代码单独成条」的复制路径失效。四反引号块里的三反引号
还会被误判成闭合，把整块切成三段——那是内容，不是结构。

闭合判定遵循 CommonMark：**同字符、且不短于开围栏**。因此 ```` ```` ```` 块内部的
```` ``` ```` 属于代码内容，整块仍然是一条可复制的代码消息。

WeCom 与其余渠道走**同一套结构化管线**（差异只剩 `［代码］` 这个围栏样式——
企业微信渲染不了 Markdown 围栏）。此前 WeCom 有一条独立的正则链，
对四反引号块产出 `『［代码］…』`（行内码包住块级码）、对 `~~~` 完全不识别，
于是同一段模型回复在 QQ 上正常、在企业微信上是坏的。平台差异只应体现在
渲染层，不该表现为「有的平台处理了、有的没有」。

### 代码怎么复制走：四个渠道各自能做到什么

需求要求「代码框旁边有直接复制键」。这件事**受平台能力约束**，因此四个渠道的落点不同，
但都必须是真能用的路径，不是一个点不动的按钮：

| 渠道 | 复制方式 | 为什么是这个 |
| --- | --- | --- |
| WebUI | 代码框右上角**真的复制按钮** | 浏览器 `navigator.clipboard` 直接可用，没有平台限制 |
| Telegram | 消息下方**原生复制按钮** | Bot API 的 `InlineKeyboardButton.copy_text`，点一下进剪贴板，不走回调 |
| QQ / OneBot | 代码**单独成一条消息** + 一句复制指引 | OneBot V11 没有可用的交互按钮原语；整条消息就是代码本体，任意客户端长按全选即可，不会混入正文或页码 |
| 企业微信 | 同上 | 同上 |

三条边界值得知道：

- **复制的是代码原文**，不是渲染结果。Telegram 的 MarkdownV2 会把 `_` 转义成 `\_`，
  复制走那份粘进编辑器就是坏代码，所以复制载荷单独取自围栏内的原始文本。
- **Telegram 的按钮有 256 字符上限**，超过时**退回没有按钮，但会追加一句指引**。
  挂上去会让整条 sendMessage 被平台拒绝——那等于「顺手加个按钮」把一条本来能发出去
  的回复变成发不出去。而 256 字符只够十来行代码，所以「超限」是常态：一条 300 字符
  的代码此前什么提示都没有，旁边 200 字符的那条带着显眼的「复制代码」按钮，
  两条看起来能力不同，实际都能复制——Telegram 客户端在 Markdown 代码块右上角
  **自带**复制图标。缺的不是途径，是用户不知道有。那句指引单独成一条消息
  （代码那条整体是可复制的代码，掺中文会污染复制结果），且文案不含任何
  MarkdownV2 保留字符：一个未转义的 `_` 会让整条消息被拒收。
- **一个代码块被分页拆开时按钮只挂第一页**。每页都挂等于给出几个内容不同却看不出
  区别的「复制」，点错一个就拿到半段代码。

### 表格：按手机一行的容量决定形态
表格不是一律画框线。宽度放得进手机气泡一行时画规整框线表，放不下就改成纵向
「字段：值」分组——错位的框线连「哪个值属于哪一列」都保证不了，而纵向布局至少
保住这一点。

阈值是 38 显示列，按 375pt 手机的气泡正文区（约 280pt、默认字号一行 17–18 个汉字）
取值。此前是 60，依据写成「30 个汉字的两倍」，而 30 个汉字偏大：实测一张 4 列中文
参数表 48 列、一张 3 列长键名配置表 57 列，两者在 60 之下都画框线，而它们都放不进
手机一行。

还有一层无法靠计算修正的风险：制表符 U+2500–257F 的 East_Asian_Width 是
**Ambiguous**——西文字体里占 1 列，中日韩字体里占 2 列，而宽度计算按 1 列。
边框行全由制表符组成、数据行是制表符与内容混排，因此在把 Ambiguous 当全角渲染的
客户端上两者膨胀幅度不同（实测边框行 48→96、数据行 48→53），对齐彻底失效。
这让「窄表才画框线」从一个美观取舍变成了正确性要求：越窄，出问题的概率越低。

QQ 与企业微信共用这个阈值，因此同一段回复不会一个画框线、一个走字段。
Telegram 把表放进围栏走等宽字体，宽表在那里本来更安全，但目前共用同一个值——
一致的口径比多一点宽度更重要。

### 分页页码：一条回复一个序列

QQ 上代码要单独成条，因此一条「正文 + 代码 + 正文」的回复会拆成好几条消息。
页码**跨这些消息连成一个序列**，而不是每一段各自从「第 1 页」数起。

这一点必须钉住，因为它直接对应「回复内容可能不够全，有时候出现数据丢失」这个报障：
按段分别编号时，用户被告知「共 2 页」却收到 6 条消息，其中「第 1 页」出现两次——
他唯一能得出的结论就是内容不全，而内容一条都没少。

三条规则：

- **代码消息不带页码。** 长按复制会把「第 1 页 / 共 3 页」一起复制走，粘进编辑器
  就是坏代码，而代码单独成条的全部目的正是让它可以整段复制。它在序列里仍然**占一位**
  （否则总数与收到的条数不符），所以你会看到页码跳过某个数字——缺的那个就是代码。
- **只因长度被切开时才加页码。** 一条两句话的回复因为「代码单独成条」变成 3 条消息，
  不会标页码：标上「第 1 页 / 共 3 页」会让人以为内容太长被截了。
- **一个代码块跟一条复制指引**，不是每页一条。代码本身跨多条消息时，
  指引里会写「这段代码共 N 条，请按顺序拼接」——代码消息不能带页码，
  但「我收齐了吗」这个问题仍然要回答，于是放进紧随其后、不参与复制的那句话里。

明确不做的事：**不在 QQ 上画一个点不动的「复制」按钮**。1.txt 19.3 直接禁止显示
不可用按钮，而一个看起来能点、点了没反应的按钮比没有按钮更糟——用户会反复点它，
并认为消息发送出了问题。

数学降级的判据同时放宽了：`$x = 5$` 这类**不含反斜杠命令**的公式此前原样把
`$` 发到 QQ（正是「不得出现成片 `$...$`」要禁的形态）。现改为按货币形态排除，
因此 `价格 $5 和 $7` 仍然完整保留——两者都要对，不能靠牺牲一头换另一头。

Telegram 与 WeCom 使用同一套阶段命名，可直接横向比较同一问题在三个渠道的耗时。
Agent 路径与遗留工作流路径也用同一套口径落库，两者可以直接比较。

这些耗时同时落库（表 `im_delivery_timings`），因此可以按时间范围回查：

```bash
# 上周二 QQ 慢在哪一段
curl -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8080/backend-api/api/tracing/delivery/summary?channel=onebot&start_time=2026-08-25T00:00:00%2B08:00&end_time=2026-08-26T00:00:00%2B08:00"

# 是 QQ 这条链路慢，还是模型本来就慢？（三个渠道并排）
curl -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8080/backend-api/api/tracing/delivery/compare?start_time=2026-08-25T00:00:00%2B08:00&end_time=2026-08-26T00:00:00%2B08:00"
```

上面第二条是排查顺序里的**第一步**，不是补充：单看 QQ 的「模型生成 8 秒」无法判断
这 8 秒是不是 QQ 的问题。若三个渠道的生成段都是 8 秒而只有 QQ 的发送段是 4 秒，
要查的是发送链路（分段数、限流、反向 WebSocket），而不是模型。界面上这张对比表在
「可观测性 → 投递时间线」，与单渠道明细同一页。

返回里每个阶段都带 `samples`（样本数）：非流式请求没有首字节，
这类记录不会被按 0 计入平均值，因此 `llm_first_byte_seconds` 的样本数
可能小于总投递数。表中只有时长与计数，**不含任何消息正文**，会话键以摘要存储。

#### 发送段分成三个数，因为处置不同

`send_seconds` 是 `send_started → send_succeeded` 的整段墙钟时间，它回答
「用户等了多久」。但它回答不了「该去查谁」——里面同时含着两件性质相反的事：

| 字段 | 含义 | 慢的时候该做什么 |
|---|---|---|
| `send_seconds` | 整段。用户实际等待 | 先看下面两项哪个大 |
| `send_pacing_seconds` | 我们为防刷屏**主动等**的时间 | 调 `send_pacing` 配置（见第九节） |
| `send_upstream_seconds` | 上游**真的慢**的时间 | 查 QQ / 网络 / OneBot 实现 |

不拆开时，一条十页回复因节流等了 20 秒会显示成「平台发送 20 秒」，运维会去查 QQ，
而 QQ 什么问题都没有。现场报障那句「系统显示成功到收到回复中间隔了很久」正是这个
形态：Kirara 侧已经 `send_succeeded`，用户手机上还没收到，而那段时间的大头是节流。

**`null` 与 `0` 在这两列上含义相反**：`0` 是「测了，这次没等」（单页回复不触发节流）；
`null` 是「这条链路没有测量节流」——没有节流概念的 Telegram / WeCom，
以及不上报这一项的第三方适配器。把前者当后者会让运维排除掉一个其实没被测量的原因。

发送**失败**的那次同样分开归因：「等了 18 秒然后失败」与「上游 18 秒后拒了」
是两个不同的故障，而它们的 `send_seconds` 相同。

## 八、Compose 验收矩阵

每次改动 Compose 或升级镜像后，建议按下表逐项过一遍：

| 场景 | 预期状态 | 日志证据 | 恢复上限 | 需人工扫码 |
|---|---|---|---|---|
| 首次启动（无 `./QQ`） | `initializing` → 扫码 → `connected` | 上游 `onQRCodeGetPicture`；`qr_login.state=waiting_scan` | 取决于扫码 | 是 |
| 已有登录态重启 | `initializing` → `connected` | 上游 `quickLoginWithUin`；`qr_login.state=quick_login` | 3 分钟 | 否 |
| `pull` 后重建 | 同上；上游冷启动 QQ 那几分钟是 `initializing` 而不是「未连接」 | 同上 | 3 分钟 | 否 |
| 上游冷启动超过宽限期 | `initializing` → `waiting` | readiness 摘要从「正在启动」转为「尚未建立连接」 | `initial_connect_grace_seconds`（默认 180 秒） | 否 |
| 单个 QQ 实例故障 | 故障实例 `stale`，其余 `connected` | `last_disconnect_reason=heartbeat_timeout` | 90 秒转 `stale` | 否 |
| 多个 QQ 实例并存 | 各自独立 `connected` | 各自 `self_id`；`connected_account_count` 等于实例数 | 3 分钟 | 否 |
| Kirara 先启动 | `waiting` → `connected` | Kirara 侧无接入日志，直到上游拨入 | 3 分钟 | 否 |
| QQ 先启动 | 上游按自身间隔重连直到 Kirara 就绪 | **上游侧**日志（LLOneBot 的重连记录；Kirara 侧只会看到最终的 `lifecycle connect`） | 3 分钟 | 否 |
| OneBot 断线后恢复 | `connected` → `reconnecting` → `connected` | `OneBot 连接已断开` 与 `OneBot 连接已建立` 各一条 | 上游重连间隔（默认 3 秒）；超过 `reconnect_grace_seconds` 未回则转 `disconnected` | 否 |
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
