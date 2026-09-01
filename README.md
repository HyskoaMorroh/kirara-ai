
<p align="center">
  <h2 align="center">Kirara AI</h2>
  <p align="center">
    一款支持主流大语言模型、主流聊天平台的聊天的机器人！
    <br/>
    <br/>
    <a href="https://kirara-docs.app.lss233.com/"><strong>» 查看项目手册 »</strong></a>
    <br/>
  </p>
</p>

<p align="center">
  <a href="https://github.com/HyskoaMorroh/kirara-ai/stargazers"><img src="https://img.shields.io/github/stars/HyskoaMorroh/kirara-ai?color=F8B195&amp;logo=github&amp;style=for-the-badge" alt="Github stars"></a>
  <a href="https://pypi.org/project/kirara-ai/"><img src="https://img.shields.io/pypi/v/kirara-ai?color=F67280&amp;logo=pypi&amp;logoColor=white&amp;style=for-the-badge" alt="PyPI"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/HyskoaMorroh/kirara-ai?&amp;color=C06C84&amp;style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <a href="https://github.com/HyskoaMorroh/kirara-ai/actions/workflows/docker-latest.yml"><img src="https://img.shields.io/github/actions/workflow/status/HyskoaMorroh/kirara-ai/docker-latest.yml?color=6C5B7B&amp;logo=docker&amp;logoColor=white&amp;style=for-the-badge" alt="Docker build latest"></a>
  <a href="https://hub.docker.com/r/lss233/kirara-ai/"><img src="https://img.shields.io/docker/pulls/lss233/kirara-agent-framework?color=355C7D&amp;logo=docker&amp;logoColor=white&amp;style=for-the-badge" alt="Docker Pulls"></a>
  <a href="https://codecov.io/gh/HyskoaMorroh/kirara-ai"><img alt="Codecov" src="https://img.shields.io/codecov/c/gh/HyskoaMorroh/kirara-ai?color=A8E6CE&amp;logo=codecov&amp;logoColor=white&amp;style=for-the-badge"></a>
  <img alt="Mypy checked" src="https://img.shields.io/badge/Mypy-checked-DCEDC2?style=for-the-badge&amp;logo=python&amp;logoColor=white">
</p>

*** 

![cover](https://raw.githubusercontent.com/Haibersut/cnblog/refs/heads/main/230783378-34ddb86a-c8d3-47a6-baa5-86e39200b258.jpg)

*** 

## 🌟 社区交流

加入我们的社区，获取最新项目动态、视频教程、问题答疑和技术交流！

* [Telegram 交流群](https://t.me/kirara_ai) - 项目动态、问题答疑、技术交流，以及参与 Kirara AI 及生态开发的讨论。

> **提问前请先查看**: 加入群组前，请先查看[项目问题列表](https://github.com/HyskoaMorroh/kirara-ai/issues)，看是否能解决你的问题。
> 
> 如需提问，请准备好问题描述、**完整日志**和相关配置文件，以便我们更好地帮助你。

## 📷 功能展示

| ![猫娘问答](https://img.shields.io/badge/-%E7%8C%AB%E5%A8%98%E9%97%AE%E7%AD%94-FF6B6B?style=for-the-badge&logo=github&logoColor=white) | ![智能助手](https://img.shields.io/badge/-智能助手-4ECDC4?style=for-the-badge&logo=wechat&logoColor=white) | ![沉浸式RPG](https://img.shields.io/badge/-沉浸式RPG-FFA07A?style=for-the-badge&logo=discord&logoColor=white) |
|:-------------------------------:|:-------------------------------:|:-------------------------------:|
| ![猫娘模式](https://user-images.githubusercontent.com/8984680/230702158-73967aa9-01be-44d6-bbd9-24437e333140.png) | ![日常助手](https://user-images.githubusercontent.com/8984680/230702177-de96f89b-053e-4313-a131-715af969db04.png) | ![文字冒险](https://user-images.githubusercontent.com/8984680/230702635-fb1de3bf-acbd-46ca-8d6f-caa47368b4d4.png) |

## 🧭 WebUI  

<div align="center">  

<h3 align="center">模型管理</h3>  

![image](https://github.com/user-attachments/assets/0839bff6-47d4-4fe2-a326-056185ef1ad4)


<h3 align="center">工作流</h3>  

![image](https://github.com/user-attachments/assets/c8ded878-3cf9-4c70-925d-ee29027674ff)

<h3 align="center">插件市场</h3>  

![image](https://github.com/user-attachments/assets/d734be88-e8f6-4b95-aba8-02a544ab7a9f)

</div>

## 📚 文档导航

仓库内的 `docs/` 提供以下面向不同阶段的说明文档：

| 文档 | 用途 |
|---|---|
| [`docs/PRACTICAL_PLAN_AND_TUTORIAL.md`](docs/PRACTICAL_PLAN_AND_TUTORIAL.md) | **从这里开始**：一条从空服务器到「渠道身份 → Agent → 上游模型/备用链 → Prompt/Skill/Memory/MCP」全链路打通的落地路线，每一步都带可验证的验收点，并逐条写明哪些结论**不能**从界面推出来 |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | 首次部署走一遍：首次登录设定密码、内置模板与规则的释放、配置 LLM 后端与手动选模型、确认调度规则、发出第一条可验证的回复 |
| [`docs/QQ_ONEBOT_OPERATIONS.md`](docs/QQ_ONEBOT_OPERATIONS.md) | QQ / OneBot 专项：连接方向、九种连接状态与原因码、数据目录清单、Compose 参考与验收矩阵、二维码与登录（含有效期倒计时与刷新动作）、发送节流的两层上界、回复慢的分段定位 |
| [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) | 怎么看清系统在干什么：日志去向、LLM 请求追踪、投递时间线、成本统计、工作流结构预检与全部 issue code、调度规则试运行与静态可达性分析，以及明确不存在的观测能力 |
| [`docs/EXTENDING.md`](docs/EXTENDING.md) | 扩展开发：自定义 Block、插件、MCP 接入、预设 YAML、调度规则、事件总线、定时任务，并逐条写明当前**没有**的扩展点 |
| [`docs/WORKFLOW_OPERATIONS_GUIDE.md`](docs/WORKFLOW_OPERATIONS_GUIDE.md) | 从部署到首条回复：模板选型、手动选模型、自动探测边界、默认规则、画布操作、排错顺序与扩展边界 |
| [`docs/EXCELLENCE_DEPLOYMENT_GUIDE.md`](docs/EXCELLENCE_DEPLOYMENT_GUIDE.md) | 面向生产部署的品质路线图：现有能力的正确使用、画布体验基线、可靠性门禁，以及 Agents、Skills、Hooks、MCP 与可观测性的分阶段接入方式 |
| [`docs/UPGRADING.md`](docs/UPGRADING.md) | 从旧实例升级：独立数据副本、readiness、工作流/调度/模型核对、回滚和备份恢复 |
| [a7 历史升级清单](docs/UPGRADING_TO_3%2E3%2E0a7.md) | a7 发布周期的冻结升级核对清单（历史记录） |
| [`docs/AGENTS_SKILLS_HOOKS_MCP_GUIDE.md`](docs/AGENTS_SKILLS_HOOKS_MCP_GUIDE.md) | 基于真实 Workflow、catalog、extension manifest、lifecycle 和 MCP allowlist 的扩展指南与安全边界 |

## 🎨 主题与外观

WebUI 内置 6 套配色方案，每套都有独立的浅色与深色取值：

| 配色方案 | 键名 | 说明 |
|---|---|---|
| 经典蓝 | `classic` | 项目原生蓝白配色，主色与旧版 `#007AFF` 一致 |
| 石墨灰 | `graphite` | GitHub 风格中性灰蓝，弱饱和、久看不累 |
| 午夜蓝 | `midnight` | One Dark 风格深蓝紫，青色主调，节点辨识度高 |
| 松林绿 | `forest` | Solarized 风格暖调低蓝光，夜间护眼 |
| 高对比 | `contrast` | 为低视力与强光环境准备，全部语义色满足 WCAG AA |
| 纯黑 | `oled` | OLED 真黑省电配色，深色底为 `#000000` |

明暗模式有 **跟随系统 / 浅色 / 深色** 三档。选择「跟随系统」时会读取操作系统的深色偏好（`prefers-color-scheme`），并在系统切换时自动跟随。

### 每套配色的圆角性格

配色方案的差异不只在颜色。WebUI 有一套统一的圆角阶梯（`--radius-xs` 4px / `--radius-sm` 8px / `--radius-md` 12px / `--radius-lg` 16px / `--radius-xl` 24px / `--radius-pill` 999px），每套色板通过一个缩放系数整体调整这套阶梯，因此不会出现「某一档忘了改」的梯度断裂：

| 配色方案 | 圆角系数 | 观感 |
|---|---|---|
| 石墨灰 `graphite` | 0.5 | 方正，GitHub 式工程感 |
| 高对比 `contrast` | 0.375 | 全站最方正——弱视用户依赖清晰的矩形边界定位控件，大圆角会削弱这条线索 |
| 午夜蓝 `midnight` | 0.75 | 介于石墨与经典之间，编辑器气质 |
| 经典蓝 `classic` | 1 | 基准梯本身（4/8/12/16/24） |
| 纯黑 `oled` | 1.125 | 比经典再圆一档——纯黑底上 1px 描边几乎不可见，层级只能靠形状表达 |
| 松林绿 `forest` | 1.25 | 全站最圆润，阅读优先 |

胶囊档（标签、状态徽标、头像）不参与缩放，任何色板下都是整圆；缩放后各档都有 2px 下限，避免退化成直角。CSS 变量与 naive-ui 的主题覆盖读的是同一份计算结果，因此手写样式与组件库不会渲染出不同的圆角。外观设置里的色板缩略图也按各自系数绘制，选择时就能看出方正/圆润的差异。

修改位置：WebUI 的 **系统设置 → 外观** 标签页（`AppearanceCard`），可分别选择明暗模式与配色方案，每个色板卡片都用自身色值绘制预览。状态栏右侧还有一个明暗快速切换按钮，方便在工作流画布这类全屏页面直接切换。

设置保存在浏览器本地（`localStorage` 的 `themeMode` 与 `themePalette` 两个键），立即生效且刷新后保留；因为是浏览器本地存储，换浏览器或清理站点数据后会回到默认的浅色 + 经典蓝。首屏在主应用加载前就会按保存的选择着色，不会出现先白屏再变色的闪烁。若浏览器禁用了本地存储（隐私模式等），界面仍可正常使用，只是每次刷新回到默认值。

## 💾 完整备份与恢复

已登录的管理员可导出或恢复完整实例数据。备份包包含系统配置、机器人与模型配置、工作流、触发规则、记忆、媒体、数据库、外部插件、字体和 Web 登录密码文件；导入成功后必须重启服务。

### 图形界面

部署后，在 WebUI 的 **系统设置 → 备份与恢复** 标签页即可看到「导出完整备份」「检查备份包」「恢复数据」「自动回滚包下载」四组操作。导入必须先通过检查，恢复会再次要求确认，成功后会明确提示重启服务。

为兼容旧书签，也保留后端直连页 **`http://<你的地址>:8759/backup`**（端口按你的部署调整）。两处均复用 WebUI 登录令牌，所有请求仍经过原有 Bearer 鉴权。

### HTTP 接口

也可以直接调用接口（例如写进备份脚本）：

- 导出：`GET /backend-api/api/system/backups/export`
- 导入前检查：`POST /backend-api/api/system/backups/inspect`，使用 multipart 字段 `backup`
- 导入：`POST /backend-api/api/system/backups/import`，使用 multipart 字段 `backup`
- 自动回滚包列表：`GET /backend-api/api/system/backups/rollbacks`
- 下载回滚包：`GET /backend-api/api/system/backups/rollbacks/<文件名>`

所有接口都需要 `Authorization: Bearer <token>` 头，token 由 `POST /backend-api/api/auth/login` 获取。

每次导入都会先在 `data/backups/` 创建回滚包；导入包验证失败或写入失败时，原有数据保持不变。备份包包含 API Key、机器人 Token、Web 密钥和密码哈希，必须只保存到可信本地位置，严禁提交到 GitHub、Docker Hub 或发送给他人。

## ⚡ 核心特性
* [x] 图片发送
* [x] 关键词触发回复
* [x] 多账号支持
* [x] 人格设定
* [x] 支持 QQ、Telegram、Discord、微信  
* [x] 可作为 HTTP 服务端提供 Web API
* [x] 支持 OpenAI、DeepSeek、Claude、Gemini、Qwen、Mistral、豆包、Minimax、Kimi、Mistral 等主流大模型
* [x] 支持插件机制
* [x] 支持条件触发
* [x] 支持管理员指令
* [x] 支持 Stable Diffusion、Flux、Midjourney 等绘图模型
* [x] 支持语音回复
* [x] 支持多轮对话
* [x] 支持跨平台消息发送
* [x] 支持自定义工作流
* [x] 支持 Web 管理后台
* [x] 内置 Frpc 内网穿透

### 可靠性与可观测性

* [x] **内置 OneBot V11 / QQ 适配器**：反向 WebSocket、多账号 `self_id` 路由、
  九种连接状态与固定原因码（区分「正在启动」「等待接入」「正在重连」「凭据被拒」
  「握手被拒」「心跳超时」「存储不可写」）。**「正在启动」与「正在重连」是唯一两个
  不需要动手的状态**，而 `docker compose down && pull && up -d` 之后落在哪一个，
  取决于上游有没有连过：QQ 冷启动加登录在实测里超过 90 秒，这段时间 Kirara 侧
  不可能有连接（反向 WebSocket 由上游拨入），报「未连接」会让人去重查地址与令牌，
  而那两项从来没错。两个宽限期各有上限（默认 180 / 45 秒），超时才转「等待连接」
  或「已断开」。扫码状态可**一键刷新**（重读上游日志，不代替上游生成二维码），
  详见 [`docs/QQ_ONEBOT_OPERATIONS.md`](docs/QQ_ONEBOT_OPERATIONS.md)
* [x] **发送节流有总额上界**：QQ 对短时间连发有风控，因此多页回复的页与页之间主动
  等待——但那个等待按频率计费而不是按字数累加。页间最小等待是每个间隙都付的硬保证，
  按长度追加的部分受一次投递的总额上界约束（默认 6 秒）并按间隙数摊开，
  抖动围绕确定值双向摆动因此在任何页面尺寸下都存在。缺了这层总额，
  一条十页的回复会纯等 70 秒以上，表现为「系统显示成功，QQ 却很久才收到」
* [x] **持久化投递队列**：长回复按页独立投递，结果未知的投递被隔离而**不会重发**，
  明确失败按「指数退避 + 抖动 + 上限」有限重试。QQ / OneBot / Telegram 三家共用
  同一份退避计划，各自的 `outbox_max_attempts` 都真的生效
* [x] **入站去重**：同一条上游消息只会触发一次工作流。上游重连后重投是它唯一安全的
  选择，去重由本侧的收据表完成，避免重复计费与重复回复
* [x] **统一排版管线**：QQ / Telegram / WeCom 共用一套结构化渲染——**解析共享、
  符号表按平台各出一份**（QQ 用 `■`/`▎` 标题、`【】` 强调、`「」` 行内代码，
  企业微信用 `━━`/`「」`/`『』`；不允许各平台各写一套 Markdown 解析）。
  此前 QQ 这一层完全不存在：`##`、`**`、`>`、`` ` ``、`[]()` 六种标记原样发给用户，
  而企业微信早就有符号表——「参照 wecom 让 QQ 更美观」的方向是反的。
  表格按**手机气泡一行的实际容量**决定形态（放得下画规整框线表，放不下改纵向
  「字段：值」——错位的框线连「哪个值属于哪一列」都保证不了，而制表符在中日韩字体下
  会占两列，同一张表在不同客户端宽度不同，这一点无法靠计算修正），
  代码保持原始缩进与围栏（围栏是「代码单独成条」这条复制路径的识别依据，
  **未闭合的围栏不补闭合**——补了会把截断回复的剩余正文变成「代码」），
  **模型直接贴在正文里、一个反引号都没有的代码也算代码**——这在实际对话里是常态，
  而按正文处理会让顶格的 `# 注释` 变成标题、`_name_` 掉下划线、
  `mask = a | b | c` 被画成表格，整段还进不了复制路径；
  识别刻意保守，因为反向误判更严重（把一段中文说明包进围栏并挂上「长按可整段复制」），
  因此中文技术散文、英文等式句、日志行、URL 列表都不会被判成代码，
  常见 LaTeX 降级为可读符号、
  长回复统一标注「第 N 页 / 共 M 页」，且**一条回复只有一个页码序列**——
  代码单独成条会让一条回复变成好几条消息，按段各自编号时用户被告知「共 2 页」
  却收到 6 条、其中「第 1 页」出现两次，唯一能得出的结论就是内容不全，
  而内容一条都没少。**不留转义残片**：同义命令给出同一结果
  （`\dfrac` 与 `\frac` 都是 `(a)/(b)`，`\hat`/`\vec`/`\bar` 都变成组合记号），
  数学环境里的列分隔符 `&` 与行结束 `\\` 是排版指令而不是内容，
  真正未收录的命令去掉反斜杠只留命令名（`\foo` 而不是残片），
  落单的行内代码反引号被清掉，
  而围栏代码里的反引号是内容、一个都不动。围栏按 CommonMark 识别：
  波浪号围栏（`~~~`）与四个以上反引号的围栏同样算代码，闭合围栏必须同字符
  且不更短——因此「四反引号里包三反引号」（模型展示 Markdown 时的标准写法）
  是一个完整代码块，不会被内层围栏切开，块内的公式与表格也不会被当正文改写。
  平台差异只在渲染层：企业微信用 `［代码］` 边框而不是 Markdown 围栏，
  但解析结构与其余渠道是同一份
* [x] **代码复制按各渠道能力落地**：WebUI 代码框右上角是真的复制按钮
  （浏览器剪贴板直接可用）；Telegram 用 Bot API 的原生复制按钮，点一下进剪贴板；
  QQ / 企业微信没有可用的交互按钮原语，改为让代码单独成条、长按整段复制。
  **不画点不动的按钮**——看起来能点、点了没反应，比没有按钮更糟。
  复制载荷取代码原文而不是渲染结果（转义产生的反斜杠会被一起复制走）
* [x] **端到端投递时间线**：从收到事件、工作流开始、模型首字节、模型完成、
  排版、发送到结果，逐段计时并落库，可按时间范围回查「慢在哪一段」；
  未测到的阶段保持空值，不会用 0 冒充。另有**跨渠道对比表**把三个渠道的同一组阶段
  并排放在一起——「QQ 慢，是 QQ 这条链路慢，还是模型本来就慢」这个问题只有对照组
  能回答，切三次筛选器得到的是三次独立查询，对比被推给了读者的短期记忆
* [x] **模型目录定期刷新有界面**：「模型 → 自动检测计划」给出每个上游的间隔天数、
  上次成功时间、下一轮预计时刻与模型数，改间隔即时生效不必重启。
  上次成功为空显示「—」而不是编一个时间（那可能是从没到期、也可能是每次都失败）；
  调度循环没在跑时页首显著提示，否则逐行显示「每 5 天」是一句谎话
* [x] **多 Provider 故障转移**：优先级队列、可重试错误分类（认证/参数/内容策略不重试）、
  流式首字节与静默超时、总截止时间与取消传播、三态熔断器（状态跨重启保留），
  以及**手动重置熔断**——一次上游抖动不必靠重启整个进程来解除隔离。
  重置在容错面板每一行上，只在真的被隔离时出现、需二次确认（它把上游放回
  真实流量）、只影响那一家。总时间预算与三档取回方式也可在
  「系统设置 → Agent 运行时」里配，不必登服务器改 YAML
* [x] **流式、非流式与真流式三档**：`reply_stream_mode` 可按 **Agent / 渠道 / 进程**
  三层配置（优先级依次覆盖，三层全部可持久化并经 REST 读写）。`aggregate` 让首字节
  超时、静默超时与首字节前的故障转移真正生效，但用户端仍是一条完整回复；
  `incremental` 在能改写已交付内容的渠道上把文字**逐段推给用户**——Telegram 靠
  `editMessageText`，**WebUI 在线对话靠 SSE**（`POST /llm/chat/stream`，一条事件就是
  一次追加，界面右上角有「流式 / 非流式」开关可现场对比两条路径）。
  QQ / OneBot 与企业微信没有等价能力，在那里自动退回 `aggregate`——逐字发新消息会
  变成几十条碎片，比一条完整回复更糟。增量收尾成功后**不再整段发一次**：
  那会让同一段回复出现两遍；而占位失败或改写被限流时整段投递照常兜底，
  用户不会什么都收不到
* [x] **Tool Search：工具也走渐进披露**：工具数超过阈值（默认 12）时系统提示词里只放
  一行目录，完整 schema 由 `search_tools` 按需取回。四十个工具全量注入是每轮固定多付
  上万 token，而且名字相近的工具挤在一起会让模型选错——后者表现为「AI 变笨了」，
  比成本更难发现。阈值 `0` 关闭，拿回逐字节一致的旧行为
* [x] **真实成本统计**：按请求当时的价格快照计费，区分**四类用量来源**
  （四维齐全的供应商回报 / 只报了一部分维度 / 本地估算 / 未知）与未定价请求，
  支持按 Provider / 模型 / 失败类型聚合与 CSV 导出；WebUI 的
  「系统记录 → 使用统计」把趋势、Provider / 模型分布与成本汇总放在一页，
  并链接到请求日志与成本定价。**供应商没回报的维度按未产生计价**：只有 Claude
  系会回报缓存写入量，若要求四个维度全部有值才出总额，OpenAI / Gemini / Ollama
  形态的请求就会永远显示无成本；缺维度与「整份用量都拿不到」是两件事，
  后者仍然不计价（`NULL` 而不是 0）。**「只报了一部分」单独标注**，
  因为它的总额是补出来的——缓存读取的单价通常只有输入 Token 的 1/5 到 1/10，
  一份缺维度按 0 的账单在缓存密集的部署上会系统性偏低，
  而与完全可信的账单显示成同一个词时看不出任何区别
* [x] **价目自动同步**：定价可从上游公开价目表按周期拉取（默认 7 天，设为 0 天关闭），
  也可在「成本定价」页手动同步一次。**手工改过的价格不被自动同步覆盖**，
  数字没变时不落盘。**单位不做换算**——上游本身按每百万计价，再乘一次就是千倍偏差，
  而这种偏差在界面上只表现为"成本好像有点高"，不会报错。
  同步失败保留既有价格并单独汇报，且把「还没同步过」与「同步失败」分开显示：
  合成一个状态会让刚启动的实例看起来像上游挂了
* [x] **受控扩展**：Skill / Hook / MCP / Prompt 带 manifest、版本、来源、权限边界与审计，
  Hook 支持按工具名匹配、按事件启停与上线前预演
* [x] **资源装得进也退得出**：版本回退（内容还在，回退后保持停用等你审阅）
  与卸载（删前自动备份，释放磁盘与那个资源 ID）。「停用」不是卸载——
  停用不释放磁盘、不清注册表，那个 ID 也不会重新可用。
  回退对**所有**资源类型可用，不再要求资源先绑定工作流
  （skill / prompt / hook / mcp / memory 从设计上就没有工作流绑定）
* [x] **缺什么依赖说得出来**：stdio 型 MCP 靠 `uvx` / `npx` 拉起，而「资源装好了」
  与「那个命令在这台机器上存在」是两件事。资源列表按资源显示依赖状态并点名缺的是哪一个，
  而不是让用户对着 MCP 面板的「连接失败 / 工具数 0」去查网络、查配置、查 API Key。
  **「还没探测过」与「探测过、确实没有」分开显示**——前者的下一步是去检查，
  后者的下一步是去安装，混成一句会让人去装一个本来就在的东西
* [x] **前端构建过期会被 readiness 报出来**：静态目录与后端版本各走各的，
  本地源码部署时很容易「后端是新的、前端是旧的」——用户按新文档去点一个按钮，
  按钮不存在，而 API 探针与健康检查全都通过，因为它们查的是后端。
  readiness 把两个版本号一起报出并给出重新构建的具体动作。
  不一致判 warn 而非 fail（服务确实在正常响应），读不到版本判 skip
  （纯 API 部署没有静态目录，那是合法形态）
* [x] **创建者身份可延伸到 IM 渠道，且能在界面上声明**：受保护的插件能力
  （MCP 工具、command 型 Hook、需确认的宿主操作）此前只在 WebUI 可用——身份只由
  HTTP 中间件注入，聊天侧**所有人**都拿不到，包括创建者本人。
  现在「系统设置 → Agent 运行时 → 创建者渠道身份」可逐条增删，不必登服务器改 YAML；
  渠道类型是下拉（后端只接受六个渠道名，写错会静默匹配不上任何消息）。
  **渠道与发送者标识一起比对**：QQ 号和 Telegram 用户 ID 可能撞号，
  只比一个等于把另一个渠道的同号用户也放进来。默认空表、**群聊默认不生效**
  （群里所有人都看得到创建者发的指令并照抄，把宿主操作暴露在多人可见会话里要显式打开）；
  未声明的发送者照常得到正常回复，只是拿不到工具。改完需要重启服务
* [x] **供应商级请求策略**：推理强度四档（各家字段由适配器翻译）、
  移除回复里的 AI 自我署名（只删署名不删答案，不进代码块、不动用量）、
  禁用启动时与每次打开 WebUI 时的版本探测（手动「检查更新」照常可用）——
  逐供应商生效，且不改写调用方的请求对象
* [x] **请求整流器**：上游因参数约束**拒绝**时改一处再重试一次，而不是把一次
  必然失败原样抛给用户。修四类「不改就一定失败、原因又不在错误里」的情形：
  思考预算与最大输出长度关系不对、换模型后失效的思考签名、不支持图片的模型
  收到图片、上游不认识 `reasoning_effort` 字段。这几类**换供应商也没用**——
  同一个不合法请求发给备用上游同样会被拒，故障转移只会把队列打满。
  三条边界写进实现——只在上游真的拒绝后动（不做发送前预判）、
  错误必须命中白名单（多个特征同时出现，避免把鉴权签名错误当成思考签名问题，
  也避免把「取值非法」当成「字段不支持」而删掉整个字段）、
  每类只改一次（改完仍失败抛原始错误，不把「参数错」变成「一直在转」）。
  图片降级是唯一会改变模型看到内容的一项，可单独关闭；图片换成可见占位而非
  静默删除，否则模型会对着空内容编一个答案。非流式与流式两条路径都覆盖，
  **Claude 与十个 OpenAI 兼容适配器同一套语义**。
  **整流器不覆盖 Gemini 与 Ollama**：图片降级规则按 `messages[*].content` 遍历请求体，
  而 Gemini 用 `contents` + `parts`，两种形状不通用。这两家的供应商页上整流开关不参与
  决策——写在这里而不是留给读源码的人发现，因为「开关存在却对某个供应商无效」
  正是那种只在故障当时才被注意到的事
* [x] **上游限额余量可见**：从每个响应的限额头（`x-ratelimit-*` /
  `anthropic-ratelimit-*` / `retry-after`）读出「离上限还有多远」，随
  `GET /llm/resilience/status` 与熔断状态同行展示。熔断说「它已经坏了」，
  余量说「它还剩多少」——后者是唯一能在撞上限**之前**给出信号的东西。
  上游不报这些头时显示「未上报」而不是 0：0 表示余量用尽，是最该报警的状态
* [x] **Teammates：Agent 之间可委派**：模型获得 `delegate_to_<id>` 工具，
  队友用自己的模型链、提示词、技能与工具白名单执行子任务；
  委派深度有上限且逐层递减，自委派在定义期即被拒——防的是「A 委派 B、B 委派 A」
  这种每层都真实花钱的无限递归
* [x] **依赖可见可控**：Skill 包与它依赖的可执行程序是两件事；依赖目录列出
  Node / uv / agent-browser / graphify / rtk / memsearch 等的就绪状态，
  有真实安装器的可受控安装（只跑服务器登记的固定命令），
  Claude Code 插件明确标为「装在操作者自己的配置里」而非服务器组件
* [x] **扫码登录生命周期可查**：二维码由 LLOneBot / PMHQ 在自己的容器里生成，
  但只要把那份日志挂到可读位置，就能把「这张码还能扫吗」变成一个可回答的问题：
  10 种状态、4 个稳定失败原因码，以及有效期、生成时间、刷新次数与最新二维码路径；
  **过期由时钟判定而不等上游日志**，因此不会把已死的码继续显示成有效。
  界面上的剩余秒数**按失效时刻自己倒数**并在归零时改口——120 秒的有效期里，
  一个静态数字必然说谎，且说的谎恰好是「还来得及」。上游日志没有时间戳时
  如实报告「无从判断是否还有效」，而不是编一个满额剩余时间：那会让每张码都显示
  「还剩 120 秒」，面板说有效、手机说过期
* [x] **通知与请求事件不再静默丢弃**：被踢出群、被禁言这类会直接导致
  「机器人不回话」的事件按类型记录（影响本账号可用性的升为 warning）；
  好友申请与入群邀请也会记录（含处置所需的 `flag`），但**不自动同意**——
  那是部署者的安全决定；适配器提供 approve / reject 方法让部署者自己决定，
  多账号下不指定目标账号会被拒绝而不是路由到任意一个
* [x] **四家上游都实现流式**：OpenAI 兼容、Claude、Gemini、Ollama 各自解析
  自家帧格式（Claude 是分类型 SSE、Gemini 是 `alt=sse`、Ollama 是按行 JSON），
  上游没给用量就保持未知而不在适配器里补 0
* [x] **会话可管理**：列出持久化会话与待确认队列、清空单个会话历史（不含对话正文出网）

# **🤖 聊天平台**  

我们支持多种聊天平台。  

| 平台       | 群聊回复 | 私聊回复 | 条件触发 | 管理员指令 | 绘图  | 语音回复 |
|----------|------|------|------|-------|-----|------|
| Telegram | 支持   | 支持   | 支持 | 支持  | 支持  | 支持   |
| QQ 机器人 | 支持   | 支持   | 支持 | 支持  | 支持  | 平台不支持   |
| Discord  | 重构中   | 重构中   | 重构中 | 重构中  | 重构中  | 重构中   |
| 飞书机器人  | 重构中   | 重构中   | 重构中 | 重构中  | 重构中  | 重构中   |
| 企业微信应用 | 支持   | 支持   | 支持 | 不支持  | 支持  | 支持   |
| 微信公众号 | 支持   | 支持   | 支持 | 不支持  | 支持  | 支持   |
| OneBot   | 内置支持   | 内置支持   | 内置支持   | 内置支持    | 内置支持  | 内置支持   |

> OneBot V11 适配器已内置（反向 WebSocket + 多账号路由），无需额外安装插件。
> 接入步骤、连接状态含义与排障顺序见
> [`docs/QQ_ONEBOT_OPERATIONS.md`](docs/QQ_ONEBOT_OPERATIONS.md)。

## 🐎 命令

**你可以在 WebUI 的调度规则中自定义所有命令。**  


## 🔧 搭建

请移步至 [快速开始](https://kirara-docs.app.lss233.com/guide/getting-started.html)

### 首次可用检查

首次打开 WebUI 后，按下面顺序即可获得第一条可验证的回复：

1. 在「LLM」添加至少一个支持文本对话的模型；内置聊天模板的模型槽位默认留空，复制成自己的副本后请在下拉框里选择已配置的模型。一个模型都没选时会使用本机默认的文本模型；若已选了模型但全部不可用，默认不会静默换成别的模型，可在节点上打开「允许回退到部署默认模型」。
2. 在「聊天平台」添加并启用一个 IM 适配器。
3. 在「工作流 → 模板管理」复制一个聊天模板；复制完成后可直接创建一条预填的触发规则草稿。
4. 在「调度规则」确认规则已启用。私聊默认使用“聊天 - 角色扮演”（`chat:normal`），群聊可用 `/chat` 或 @机器人触发；`/help` 可查看当前生效规则。
5. 从已配置的聊天平台发送一条测试消息。若未回复，先检查模型和 IM 的状态，再在调度规则页检查该规则的优先级与条件。

默认游戏命令只在整条消息为 `抽卡`、`十连`、`单抽`（可带 `/`、`.` 或 `。` 前缀）时触发，因此正常讨论“抽卡概率”等内容仍会进入聊天工作流。

### 内置工作流模板

模板分两类：一类由 Python 代码在启动时注册，一类以 YAML 随包分发（`kirara_ai/workflow/presets/chat/`），首次启动时释放到 `data/workflows/`。全部模板如下：

代码注册（`kirara_ai/workflow/implementations/workflows/system_workflows.py`）：

| 工作流 ID | 名称 | 用途 |
|---|---|---|
| `system:help` | 帮助信息 | 根据当前已启用的调度规则自动汇总一份命令帮助并发送 |
| `system:clear_memory` | 清空记忆 | 清空当前会话的群聊与私聊记忆，并回复一条确认消息 |
| `game:dice` | 骰子游戏 | 识别 `.roll 1d100` 这类指令并掷骰，把结果回复到聊天中 |
| `game:gacha` | 抽卡游戏 | 模拟抽卡，说「抽卡」抽一次、说「十连」抽十次，并给出稀有度统计 |
| `chat:normal` | 聊天 - 角色扮演 | 标准的文本对话功能，扮演默认人设和大家聊天 |
| `chat:memory_store` | 记录聊天内容 | 默默记下大家的聊天内容，可以使用查询记忆模块读取出来 |

随包 YAML 模板（源文件在 `kirara_ai/workflow/presets/chat/`，首次启动后出现在 `data/workflows/chat/`）：

| 文件 | 名称 | 用途 |
|---|---|---|
| `dsr_thinking.yaml` | 聊天 - 深度思考 | DeepSeek 思考模型聊天，隐藏 `<think>` 标签内容 |
| `normal_multimodal.yaml` | 聊天 - 原生多模态对话 | 面向原生支持图片输入的模型的图文对话，读取记忆时恢复原有媒体资源 |
| `talk_break.yaml` | 聊天 - 自定义分段 | 用 `<break>` 关键词让 AI 分段回复 |
| `mcp_tools.yaml` | 聊天 - 工具调用 (MCP) | 让模型回答前自动调用 MCP 工具（联网搜索、读写文件等） |
| `function_calling.yaml` | 聊天 - 函数调用 | 手工搭建函数调用流程，工具的实际执行需自行接节点 |
| `time_aware.yaml` | 聊天 - 时间感知 | 在系统提示词里注入实时日期时间 |
| `plain_text.yaml` | 聊天 - 纯文本输出 | 去掉回复里的 Markdown 标记，适配语音播报与不支持 Markdown 的平台 |
| `sensitive_word_filter.yaml` | 聊天 - 敏感词替换 | 发送前把指定词替换成安全表述，适合公开群聊的合规需求 |
| `long_reply_split.yaml` | 聊天 - 长回复分条 | 按 `<break>` 把长回复拆成多条发送，并在末尾追加固定提示语 |
| `custom_script.yaml` | 聊天 - 自定义脚本 | 只用「基础：代码」节点处理消息，不接大模型也能回复 |
| `group_mention.yaml` | 群聊 - 提及触发 | 只在群里被 @ 时回答，先转纯文本并去掉 @ 符号再交给模型 |

`data/workflows/chat/` 下另有 `normal.yaml`（聊天 - 角色扮演）与 `memory_store.yaml`（记录聊天内容）两份 YAML，与上表代码注册的 `chat:normal`、`chat:memory_store` 同名同 ID：已保存的 YAML 优先，代码里的同 ID 预设会被跳过，因此你在 WebUI 里对它们的修改不会被升级覆盖。

**如何使用**：进入 **工作流 → 模板管理**，在模板卡片上点「以此为模板」即可复制一份副本。改动只影响你的副本，内置模板不受影响，升级也不会覆盖你的修改；复制完成后还可以直接创建一条预填的触发规则草稿。模板里的模型槽位一律留空，请在副本里从下拉框选择本机已配置的模型。

### 默认触发规则与优先级

全新安装（`data/dispatch_rules/rules.yaml` 还不存在时）会注册下面这套默认规则。只要已有规则文件，就完全以你的规则为准，不会注入或补齐任何默认规则，也不会复活你删除过的规则。优先级数值越大越先匹配，同优先级按规则 ID 升序：

| 档位 | 优先级 | 规则 |
|---|---|---|
| 系统指令 | 100 | `/help` 帮助命令、`/清空记忆` |
| 命令 | 60 | `.roll XdY` 骰子、`抽卡` / `十连` / `单抽` |
| 聊天 | 30 | 群聊（`/chat` 前缀或 @机器人）、私聊（直接发送，无需前缀） |
| 兜底 | 0 | 以上都没匹配时执行 `chat:memory_store`，只记录不回复 |

### 采样温度

「LLM: 执行对话」与「LLM: 执行对话并调用工具」节点都有 **采样温度** 配置，取值 `0.0~2.0`，越大回答越随机、越小越稳定。生效优先级为：

1. 节点上显式填写的采样温度；
2. 留空时，读取命中的那条触发规则的 `metadata.temperature`（仓库自带的 `data/dispatch_rules/rules.yaml` 里群聊为 `0.7`、私聊为 `0.9`；全新安装注册的内置默认规则不带该字段）；
3. 两者都没有时不携带该参数，交由模型自身默认值决定。

超出 `0.0~2.0` 或非数字的取值会被忽略并记录一条告警，然后回落到模型默认温度。

### 运行测试

后端（Windows 开发环境使用可执行的 `.venv-win` 解释器；不要使用仓库里的 `.venv/`）：

```bash
.venv-win/Scripts/python.exe -m pytest ./tests -q
```

前端单元测试的脚本名是 `test:unit`：

```bash
cd webui
yarn install --frozen-lockfile
yarn test:unit
```

前端类型检查（当前退出码 0）：

```bash
cd webui
npx vue-tsc --noEmit
```

发布契约检查：

```bash
.venv-win/Scripts/python.exe -m pytest tests/test_release_workflow_contract.py tests/test_webui_build_contract.py -q
```

### 智能版本升级

`pyproject.toml` 是唯一版本源。常规发布不需要手工猜测或填写下一版本号，使用版本工具先预览、再同步：

```powershell
$python = ".venv-win/Scripts/python.exe"
& $python scripts/version.py plan --remote origin
& $python scripts/version.py plan --remote origin --json
& $python scripts/version.py next --remote origin
& $python scripts/version.py bump --remote origin
$releaseTag = (& $python scripts/version.py tag).Trim()
$commit = (git rev-parse HEAD).Trim()
& $python scripts/version.py check
& $python scripts/version.py verify-tag `
  --tag $releaseTag `
  --expected-commit $commit `
  --expect-head `
  --remote origin
```

默认命令沿用当前 alpha、beta 或 rc 通道并递增编号；正式版转换使用 `--kind stable`，功能线升级使用明确的 `--kind minor` 或 `--kind major`。工具会读取本地 Tag，并自动探测发布远端：优先当前分支 upstream，其次 `origin`，最后才使用唯一远端；远端不明确或查询失败时会停止，不会悄悄退回本地结果。离线开发必须明确使用 `--local-only`。工具自动跳过已占用版本。`plan` 一次给出当前版本、候选 Python/npm/Git Tag、发布通道、远端和已占用版本，`--json` 可直接供 CI 使用；`next` 保留为只输出候选版本的兼容命令。`bump` 在真正写入前会再次读取 Tag 并重新计算完整候选，若候选已被占用或发布线已被其他流程推进则停止并要求重算；随后才同步 Python、npm、锁文件、Docker、CI、文档等活动载体。写入前默认要求工作树干净，确认已有修改可以共存时才使用 `--allow-dirty`。失败会恢复本次同步产生的文件变化。

正式发布还必须把 Tag、源码提交和所有产物锁定为同一个不可变身份：`check` 负责版本载体同步，`verify-tag` 负责确认当前源码的 Git Tag、HEAD、期望 commit 以及远端 Tag 对象一致。它必须在创建 GitHub Release、构建 Docker 镜像或生成 Windows 快速启动包前通过；GitHub Actions 的发布入口会把验证出的 commit 传给所有后续 job，避免 Tag、分支最新提交和构建产物互相漂移。

`verify-tag` 检查的是**当下**的自洽，它没有历史记录。因此推出的镜像还会把源提交写进
`org.opencontainers.image.revision`（另有 `.version` 与 `.source`）：Tag 在发布之后被
移到另一个提交、再重建同名镜像时，这条标签是唯一能把两份镜像区分开的东西——
「线上这个版本标签究竟是哪个提交构建的」由此变成一条 `docker inspect` 就能回答的问题。
`revision` 取的是已经与 preflight 提交比对过的那个值，不是 `github.sha`。

自动升级会在安装前比对 registry 声明的文件摘要（PyPI 的 `hashes.sha256`、npm 的
`dist.shasum` / `dist.integrity`）。镜像源是用户可配的，TLS 只能证明「来自这个镜像」，
证明不了「这个镜像给的东西没被换过」；摘要缺失时**拒绝安装**而不是放行——
否则等于留一个「只要别声明哈希」的绕过口。

`yarn install --frozen-lockfile` 需要能访问 `registry.npmjs.org`；`webui/yarn.lock` 里的下载地址已全部指向官方源，并有测试守卫防止再混入镜像地址。

「检查更新」的版本序在前后端保持一致。PEP 440 的预发布序号是**数字**（`b8` < `b11`），
而 semver 把 `b11` 当成一个字母数字标识符按字典序比较，会得出 `b11` < `b8`。
序号一进两位数，这个差异就会让前端把更旧的版本判成更新：装着 b11 的用户被提示
升级到 b8，而 b10 这类真正的新版本反而不提示。WebUI 因此只在**比较时**把序号
拆成独立的数字标识符，界面上显示的版本串与 npm 包版本保持一致——
展示形态与排序形态是两件事，混在一起就会出现「为了排序而改标签」。

### CI 门禁

推送与 Pull Request 上自动运行的检查：

| 工作流 | 触发 | 内容 |
|---|---|---|
| `Run Tests` | `pull_request` / `push` / `merge_group`（`main`、`master`）+ 手动 | 语法门禁（`compileall`）、全量后端用例（ubuntu + Python 3.11）；跨版本跨平台矩阵（ubuntu 3.10 / 3.13、windows 3.13）与 Docker 镜像校验默认只在默认分支、合并队列或手动触发时运行，PR 上打 `ci:full` 标签可强制拉起 |
| `Release Preflight` | `push` / `pull_request` / `merge_group` + 手动 | 发布契约检查；WebUI 类型检查 → 单元测试 → 生产构建；ESLint 报告（不阻塞） |
| `Project Check` | `push` / `merge_group` + 手动 | mypy 类型检查报告 |
| `PR Code Review` | `pull_request_target` | mypy 结果回帖到 PR |
| `Docker build latest` / `Windows Quickstart` | 发布 Release；Windows 另支持手动 | 发布产物；镜像发布前会先重跑全量后端用例 |

前端与后端的门禁分居两个工作流，互不重复执行。

### pip 安装不含 WebUI 静态文件

`webui/dist` 位于 Python 包之外，因此 **pip 安装的 wheel 里不包含构建好的 WebUI**（历史版本同样如此，不是本次引入的问题）。后端把静态目录定位在 `<当前工作目录>/web`（`kirara_ai/web/app.py` 的 `STATIC_FOLDER`），若该目录下没有 `index.html`，打开首页只会看到「Web UI 未找到」的提示页，后端会尝试从 npm 下载 `kirara-ai-webui` 的 `beta` 标签自动安装，这一步依赖外网且拿到的是独立发布的前端而非本仓库源码。

想使用本仓库的 WebUI，有两种可靠做法：

1. 自行构建并放到位：

   ```bash
   cd webui
   yarn install --frozen-lockfile
   yarn build
   ```

   然后把 `webui/dist` 的内容复制到运行目录下的 `web/`（即 `<启动 kirara-ai 的目录>/web/index.html` 存在）。

2. 使用 Docker 镜像。`Dockerfile` 已包含前端构建阶段，并通过 `COPY --from=frontend-builder /webui/dist /app/web` 把产物放到 `/app/web`，容器的工作目录为 `/app`，与 `STATIC_FOLDER` 完全对应，开箱即可访问。

### GitHub 自动发布 Docker Hub 镜像

发布 GitHub Release 后，工作流会为每个非草稿版本构建并发布对应的 Docker 镜像；只有 GitHub 标记为仓库当前 **Latest** 的正式 Release 才额外更新 `latest` 标签。预发布和非 Latest Release 仍会发布其版本镜像，但不会覆盖 `latest`。`Docker build latest` 仅由已发布的 GitHub Release 触发；需要手动重发当前分支版本时，请在 GitHub Actions 页面手动运行 `Docker build with tags`，并填写与 `scripts/version.py tag` 输出一致的 `image_tag`。仓库需在 GitHub Settings → Secrets and variables → Actions 中配置：

- Secret `DOCKERHUB_USERNAME`：Docker Hub 用户名。
- Secret `DOCKERHUB_TOKEN`：Docker Hub Access Token。
- Variable `DOCKERHUB_IMAGE`：可选，完整镜像名，例如 `your-dockerhub-username/kirara-agent-framework`；不填写时使用用户名加默认仓库名。

服务器部署时，将 `.env.example` 复制为仅保存在服务器的 `.env`，填写 `DOCKERHUB_IMAGE=your-dockerhub-username/kirara-agent-framework:latest`，然后执行 `docker compose pull` 和 `docker compose up -d --force-recreate`。Compose 不再回退到第三方镜像；未配置镜像名会直接报错，避免误部署旧版本。

需要同时跑 QQ（LLOneBot）时，用 `docker-compose.yml.example` 而不是自己拼：它包含 QQ 容器、共享网络与登录态挂载，并受 `tests/test_docker_compose_resource_storage.py` 契约测试约束。`.env` 里还需填 `LLONEBOT1_AUTH_TOKEN` 与 `LLONEBOT1_QQ`（两者以 `:?` 声明，缺失时 Compose 会直接失败而不是静默起一个无鉴权端点）。反向 WebSocket 地址的填法、数据目录清单（含宿主机路径与权限）和重启恢复验收矩阵见 [`docs/QQ_ONEBOT_OPERATIONS.md`](docs/QQ_ONEBOT_OPERATIONS.md)。

发布身份也分为两种标签：GitHub Tag 使用 `scripts/version.py tag` 生成的 `vX.Y.Z...`，Docker Hub 版本镜像使用 `scripts/version.py get` 生成的 `X.Y.Z...`，不把带 `v` 的 Git Tag 直接当作镜像标签。完整流程见 [`docs/UPGRADING.md`](docs/UPGRADING.md)。

已有 `data` 目录会保留旧工作流和规则。若要验证新镜像的默认工作流，请使用新的空数据目录或通过完整备份导入，不要直接删除现有数据。镜像会从仓库内受版本控制的 `webui/` 源码构建前端，不再依赖 npm 上的可变 `beta` 标签；更新 WebUI 时请随项目 A 一起发布新镜像。

## 🕸 HTTP API

<details>
    <summary>HTTP API 可用于接入其他平台。</summary>
在聊天平台管理中启动 http-legacy 适配器后，将提供以下接口：  

**POST**    `/v1/chat`  

**请求参数**  

|参数名|必选|类型|说明|
|:---|:---|:---|:---|
|session_id| 是 | String |会话ID，默认：`friend-default_session`|
|username| 是 | String |用户名，默认：`某人`|
|message| 是 | String |消息，不能为空|  

**请求示例**
```json
{
    "session_id": "friend-123456",
    "username": "testuser",
    "message": "ping"
}
```
**响应格式**
|参数名|类型|说明|
|:---|:---|:---|
|result| String |SUCESS,DONE,FAILED|
|message| String[] |文本返回，支持多段返回|
|voice| String[] |音频返回，支持多个音频的base64编码；参考：data:audio/mpeg;base64,...|
|image| String[] |图片返回，支持多个图片的base64编码；参考：data:image/png;base64,...|

**响应示例**  
```json
{
    "result": "DONE",
    "message": ["pong!"],
    "voice": [],
    "image": []
}
```

**POST**    `/v2/chat`  

**请求参数**  

|参数名|必选|类型|说明|
|:---|:---|:---|:---|
|session_id| 是 | String |会话ID，默认：`friend-default_session`|
|username| 是 | String |用户名，默认：`某人`|
|message| 是 | String |消息，不能为空|  

**请求示例**
```json
{
    "session_id": "friend-123456",
    "username": "testuser",
    "message": "ping"
}
```
**响应格式**
字符串：request_id

**响应示例**  
```
1681525479905
```

**GET**    `/v2/chat/response`  

**请求参数**  

|参数名|必选|类型|说明|
|:---|:---|:---|:---|
|request_id| 是 | String |请求id，/v2/chat返回的值|

**请求示例**
```
/v2/chat/response?request_id=1681525479905
```
**响应格式**
|参数名|类型|说明|
|:---|:---|:---|
|result| String |SUCESS,DONE,FAILED|
|message| String[] |文本返回，支持多段返回|
|voice| String[] |音频返回，支持多个音频的base64编码；参考：data:audio/mpeg;base64,...|
|image| String[] |图片返回，支持多个图片的base64编码；参考：data:image/png;base64,...|

* 每次请求返回增量并清空。DONE、FAILED之后没有更多返回。

**响应示例**  
```json
{
    "result": "DONE",
    "message": ["pong!"],
    "voice": ["data:audio/mpeg;base64,..."],
    "image": ["data:image/png;base64,...", "data:image/png;base64,..."]
}
```
</details>

<details>
    <summary>管理接口：容错状态、投递耗时、会话与 Hook（均需 Bearer 鉴权）</summary>

以下接口挂在 `/backend-api/api` 下，用于运维排查，全部需要登录令牌。
返回值都经过裁剪：**不含凭据、不含对话正文、不含工具参数**。

凭据字段名由 `kirara_ai/credential_keys.py` 这一份共享词表判定，接口响应、导出文件与
追踪落库三条路径共用它。此前两条路径各有一份关键词表，于是成对凭据可能只被识别一半：
`access_key_secret` 打了码而参与同一次签名的 `access_key_id` 是明文。看到打码的那一半
会让人相信整条路径是安全的，因此这类「一半正确」比整条都没做更危险。

| 接口 | 用途 |
|---|---|
| `GET /system/readiness` | 数据目录、配置、工作流、调度目标、IM 与 LLM 可用性的分项就绪检查 |
| `GET /llm/resilience/status` | 各 Provider 的熔断状态（closed / open / half-open）与最近尝试快照 |
| `GET /llm/backends/export` | 导出供应商配置文档（**不含 API Key 等凭据**，可安全转发） |
| `POST /llm/backends/import` | 导入供应商配置：整份校验后落盘，空凭据字段保留现有值，同名冲突返回 409 与名单 |
| `POST /llm/pricing/sync` | 立即从上游公开价目表同步一次：手工价不被覆盖，数字未变不落盘，拉取失败保留既有价格 |
| `PUT /llm/pricing/sync-schedule` | 改自动同步间隔（天，`0` 关闭）。上次同步状态由 `GET /llm/auto-detect-schedule` 一并汇报，「未同步过」与「同步失败」是两种取值，不合成一个 |
| `GET /tracing/llm/statistics` | 请求量、Token、成本、首字节与尝试次数，按 Provider / 模型 / 失败类型 / 用量来源聚合 |
| `POST /tracing/llm/export` | 按当前筛选导出请求日志（`json` 或 `csv`，含成本快照） |
| `GET /tracing/delivery/summary` | 回复各阶段耗时的按渠道聚合；每个阶段带样本数 |
| `GET /tracing/delivery/recent` | 最近若干条逐条投递耗时 |
| `GET /agents/sessions` | 持久化会话列表（条数与时间戳，无对话正文） |
| `DELETE /agents/sessions/<id>/history` | 清空单个会话历史，保留绑定关系 |
| `GET /agents/confirmations` | 仍在等待人工决定的确认队列 |
| `GET /agents/hooks` | 每个 Hook 声明的事件、限定工具与所需权限 |
| `POST /agents/hooks/<id>/preview` | 预演：这个 Hook 会不会因为某个工具而触发（不执行） |
| `GET /resources/dependencies` | Node / uv / agent-browser / graphify / rtk / memsearch 等依赖的就绪状态 |
| `POST /resources/dependencies/<id>/install` | 受控安装（需确认，只跑服务器登记的固定命令） |

```bash
# 上周二 QQ 慢在哪一段
curl -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8080/backend-api/api/tracing/delivery/summary?channel=onebot"
```

字段含义与精度边界见 [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md)。
</details>

## 🦊 加载预设

如果你想让机器人自动带上某种聊天风格，可以使用预设功能。  

我们自带了 `猫娘` 和 `正常` 两种预设，你可以在 `presets` 文件夹下了解预设的写法。  

使用 `加载预设 猫娘` 来加载猫娘预设。

下面是一些预设的小视频，你可以看看效果：
* MOSS： https://www.bilibili.com/video/av309604568
* 丁真：https://www.bilibili.com/video/av267013053
* 小黑子：https://www.bilibili.com/video/av309604568
* 高启强：https://www.bilibili.com/video/av779555493

关于预设系统的详细教程：[Wiki](https://github.com/lss233/kirara-ai/wiki/%F0%9F%90%B1-%E9%A2%84%E8%AE%BE%E7%B3%BB%E7%BB%9F)

你可以在 [Awesome ChatGPT QQ Presets](https://github.com/lss233/awesome-chatgpt-qq-presets/tree/master) 获取由大家分享的预设。

你也可以参考 [Awesome-ChatGPT-prompts-ZH_CN](https://github.com/L1Xu4n/Awesome-ChatGPT-prompts-ZH_CN) 来调教你的 ChatGPT，还可以参考 [Awesome ChatGPT Prompts](https://github.com/f/awesome-chatgpt-prompts) 来解锁更多技能。 

## 🎙 文字转语音

自 v2.2.5 开始，我们支持接入微软的 Azure 引擎 和 VITS 引擎，让你的机器人发送语音。

**提示**：在 Windows 平台上使用语音功能需要安装最新的 VC 运行库，你可以在[这里](https://learn.microsoft.com/zh-CN/cpp/windows/latest-supported-vc-redist?view=msvc-170)下载。`

## 🛠 贡献者名单   

欢迎提出新的点子、 Pull Request。  

<a href="https://github.com/lss233/kirara-ai/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=lss233/kirara-ai" />
</a>

Made with [contrib.rocks](https://contrib.rocks).

## 📕 相关项目

- [Kirara Registry](https://github.com/DarkSkyTeam/kirara-registry) - Kirara AI 插件市场
- [Kirara WebUI](https://github.com/DarkSkyTeam/kirara-webui) - Kirara AI 的 WebUI 前端项目
- [Kirara Docs](https://github.com/DarkSkyTeam/kirara-docs) - Kirara AI 的使用手册原始文档

## 💪 支持我们

如果我们这个项目对你有所帮助，请给我们一颗 ⭐️  

[![Star History Chart](https://api.star-history.com/svg?repos=HyskoaMorroh/kirara-ai&type=Date)](https://www.star-history.com/#HyskoaMorroh/kirara-ai&Date)
