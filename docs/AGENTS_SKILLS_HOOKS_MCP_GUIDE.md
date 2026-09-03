# Agents、Skills、Hooks 与 MCP 实用指南

本指南只描述 `3.3.0b15` 已有的真实 primitives。这里的 Agent 和 Skill 是现有工作流、目录元数据与规则的组合，不是第二套执行器；Hook 是插件生命周期回调，不是任意中间件或安全沙箱；MCP 也没有通用人工审批中心。

## 1. 能力边界

| 名称 | 当前实现 | 不代表什么 |
| --- | --- | --- |
| Agent | 一个既有 Workflow，加模型、工具、记忆和调度策略元数据 | 没有独立 Agent 循环、规划器或绕过 Block 校验的执行器 |
| Skill | `catalog.json` 中对版本化工作流模板的名称、前置条件、能力和触发示例描述；以及从仓库/目录安装的版本化资源。绑定到 Agent 后按**渐进披露**参与对话：有前置元数据的技能在系统提示词里只占一行目录，正文由模型调用 `skill_<资源 ID>` 工具时才载入（第 3.1 节） | 不是任意脚本下载器，也不执行技能正文里的命令——正文是给模型读的指令文本，命令要落地仍需对应的系统依赖（第 8 节）|
| Hook | 插件 manifest 声明的生命周期回调（第 4 节），以及 Agent 运行时的事件 Hook（第 6 节） | 不是 Python sandbox，也不是每个请求/Block 的通用拦截器 |
| MCP | 服务器、工具、prompt、resource 的发现和调用，以及工作流工具 provider | 没有跨工具的通用审批队列；prompt/resource 当前没有工作流 Block |

插件仍在主进程内运行，可以直接导入 Python 的文件、网络和进程库。manifest 权限只约束框架注入给插件的 host facade，不能把不可信 Python 代码变成安全代码。

## 2. 工作流支持的 Agent

一个“资料助手”可以直接由 `chat:mcp_tools` 副本表示：

1. 复制「聊天 - 工具调用 (MCP)」模板，不修改内置原件。
2. 在 LLM 节点手工选择支持函数调用的模型。
3. 在 `mcp:mcp_tool_provider` 的 `enabled_tools` 中只勾选任务必需的工具；空列表表示不开放工具。
4. 保留模板的记忆节点和 `max_iterations` 上限。
5. 建立仅限管理员私聊或指定群的 DispatchRule，先调用 `POST /backend-api/api/dispatch/preview` 验证命中。

Agent 的实际执行仍由 WorkflowExecutor 完成；禁用对应规则或删除用户副本即可停止它。不要把能写文件、发消息、改配置、执行命令或产生费用的工具直接挂到公开规则。

### 可以绑定 Agent 的渠道

`POST /backend-api/api/agents/<agent_id>/bind-channel` 接受的渠道类型是一份固定枚举：

| 渠道类型 | 入口 |
| --- | --- |
| `webui` | WebUI 的对话页 |
| `onebot` | OneBot / QQ（反向 WebSocket） |
| `qqbot` | QQ 官方机器人 |
| `telegram` | Telegram |
| `wecom` | 企业微信与微信公众号 |
| `http` | HTTP Legacy API（`im_http_legacy_adapter`） |

`http` 此前**不在**这份枚举里，而适配器类名推导出的渠道类型是 `httplegacy`：
两侧对不上，绑定请求被拒，该入口只能退到全局默认 Agent——想给它单独配一条
模型链或一套 Prompt 都做不到。

现在**六个适配器全部显式声明 `channel_type`**，不再有任何一个依赖类名推导。
推导今天恰好给出正确结果，但那是巧合而非契约：一次类名重构会让该渠道的所有
Agent 绑定**静默失效**（绑定表存旧值、运行时算新值，两边对不上，请求退回全局默认
Agent），会话键也跟着漂移使历史上下文断开——两者都不报错。
源码级契约测试 `tests/agent_runtime/test_channel_type_declarations.py` 要求：
枚举里每个渠道有且只有一个适配器声明它，且改类名不影响声明值。

按账号绑定（`bind-account`）与按会话绑定（`bind-session`）对六个渠道一视同仁，
优先级为「会话 > 账号 > 渠道 > 全局默认」。

## 3. 目录支持的 Skill

`kirara_ai/workflow/presets/catalog.json` 为每个随包 YAML 提供稳定 ID、中文名、用途、前置条件、触发示例、能力和难度。比如 `chat:time_aware` 可描述成“输入聊天消息，注入当前时间后输出回复”的 Skill，但执行物仍是 `time_aware.yaml`。

通过 `GET /backend-api/api/workflow` 查看目录元数据和已加载工作流，通过 `GET /backend-api/api/workflow/<group_id>/<workflow_id>` 取得完整定义，再用 `POST /backend-api/api/workflow/validate` 静态校验。移除 Skill 时删除用户副本及指向它的规则；删除随包预设会记录 tombstone，升级不会把它复活。

### 从仓库安装的 Skill 与版本

从 GitHub 仓库安装的 Skill 会读取 `SKILL.md` front matter 里的 `version`。
声明了合法 semver 就采用它；缺失或写得不合规范时回落到 `1.0.0`。
更新时优先采用上游版本，仅当上游版本**高于**已装版本才采用，
否则做本地 patch 递增——覆盖「仓库改了内容但没动版本号」这种常见情况。
这样 `ResourceLifecycleService` 的降级保护才真正生效；
此前安装恒为 `1.0.0`、更新恒为递增，上游 semver 从不参与决策，
降级保护对远端 Skill 形同虚设。

`GET /backend-api/api/resources/updates` 返回每个 Skill 的更新状态。
非 GitHub 来源（catalog、skills.sh、本地导入）也会出现在结果里，
带 `update_channel_supported: false` 并说明应如何获取新版本——
此前这些来源被直接跳过，界面上看不到任何行，等于被当成「无更新」。

### 装进来的四条路径

| 入口 | 包在哪里 | 接口 |
| --- | --- | --- |
| 从 ZIP 安装 | 浏览器本地 | `POST /backend-api/api/resources` |
| 导入已有 | 浏览器本地 | `POST /backend-api/api/resources/imports` |
| **服务器上的包** | 服务器 `resources/imports` 目录 | `GET /backend-api/api/resources/imports` 列举、`POST .../imports/install` 安装 |
| 从仓库 / skills.sh 发现 | 远端 | `GET .../repositories/<owner>/<name>/<branch>/discover`、`GET .../skills-sh/search` |

登记仓库只需要粘一个东西：`owner/name`、仓库主页 URL、`git clone` 的地址
（`.git` 与 SSH 形态都认），或者带 `/tree/<branch>` 的深链——分支会从 URL 里取出来，
含斜杠的分支名（`release/1.x`）完整保留。界面上另有一个可选分支框，
只在坐标里没带分支时生效：粘了 `/tree/master` 就以它为准，否则让输入框覆盖它
会登记出一份用户没看过的内容。

**解析在前端，后端校验不放宽。** 后端 `owner` / `name` / `branch` 三个字段的正则
是安全边界（它们会拼进 GitHub 归档 URL 与磁盘路径）。前端把 URL 拆成三个干净字段
再提交，两层形成双重保险而不是互相替代——放宽后端等于把「URL 解析写错」升级成
「一次路径穿越的机会」。非 github.com 的主机在前端就被拒绝：后端只会去这一个主机
拉归档，接受别的主机只会给出一个必定以「仓库不存在」失败的登记，
而那条错误指向的原因是错的。

一次成功的仓库发现会把技能条数记回那条仓库记录（`discovered_skills`），
资源管理的仓库表以「识别到 N 个」显示它。这个数字是判断一个仓库配对没配对的
唯一线索：坐标拼错、分支写错、或压根不含 `SKILL.md` 的仓库，与一个装着几百个
技能的仓库在界面上此前长得一模一样，都只是「已启用」。

三态必须分开：**未发现过**（`null`，还没查）、**识别到 0 个**（查过，里面确实
没有——这才是「配错了」的信号，界面上用告警色）、**识别到 N 个**。
发现失败时不写这个数：把一次网络错误记成 0 比不记更糟。
直查一个未登记的坐标仍然可用，只是记不上数——那条路径的语义是
「给一个坐标就能看里面有什么」，不要求先登记。

第三条覆盖的是**用户手里没有可上传文件**的处境：运维用 `scp` 把一批包放进了
服务器，或者包有几十 MB 走浏览器既慢又容易断。它只扫 `resources/imports`
这一层，安装时只接受**文件名**——允许路径就等于把一个只读列举接口变成任意
文件安装接口，连子目录也不认。

列举是只读的（不解包、不落盘，`resources.read` 即可），安装与上传安装同一边界
（创建者身份，装完保持停用等待确认权限）。已装过的包标为「已安装」或
「可更新（已装 x.y.z）」而不是从列表里消失——消失会让人以为文件没放对，
于是反复重传同一个包。解析失败的包单独标错，不影响同目录其他行。

### 看正文：`GET /resources/<id>/content`

prompt 类型的全部内容就是正文，因此「提示词管理」必须能回答「现在生效的
提示词到底写了什么」。这个只读接口返回入口的包内相对路径、正文、
**已校验摘要**、来源与权限声明；WebUI 在资源详情弹窗里显示它，多版本时可切换。

摘要一起给出是必需的：它让「你看到的」与「运行时载入的是同一份」可自证，
而不是靠信任。

**没有「就地改文件」的接口，这是设计而不是遗漏。** `content_sha256` 把清单与
文件绑在一起，`read_entry` 每次读取都重新校验摘要。就地编辑的后果不是
「改了没生效」，而是那个资源在下一次载入时直接失败。提供一个会破坏完整性契约的
编辑框，比不提供更糟。

改正文有两条受支持的路径，都产出**新版本**（版本号必须递增、自动备份、
装完保持停用等待确认）：

| 接口 | 用于 |
| --- | --- |
| `POST /resources/<id>/versions` | 上传一个新的 ZIP。适用于任何类型 |
| `PUT /resources/<id>/documents` | 直接提交新正文，打包由服务器完成。只对 prompt / memory / session |

同理，`POST /resources/documents` 从一段纯文本**创建**一个新资源。
它存在的理由是提示词这个类型的全部内容就是正文——没有可执行文件、没有依赖、
没有外部来源。要求用户为一段纯文本手写 `manifest.json` 的八个必填字段、
手算 `content_sha256`（`path:size:sha256` 逐行拼接再哈希），等于把这个类型
最主要的用法排除在产品之外。

这两条路径走的是与内置目录条目**逐字节相同**的打包过程（同一套摘要算法、
同一个 `install_archive` / `update_archive`），因此落盘后与一条内置提示词同形，
校验、备份、恢复的行为完全一致。

三条边界：

- **只对 prompt / memory / session 开放**（`TEXT_AUTHORED_TYPES`）。skill 的正文是
  给模型的行为说明（会被当作可执行的操作步骤照做），hook 是能起进程的命令声明，
  mcp 启用即拉进程——那三类必须继续走「打包 + 审阅 + 显式确认」。
  更新路径的类型从**已安装的注册表**读而不是请求体，否则可以先上传一个 skill 的
  ZIP、再用纯文本接口改它的正文。
- **摘要由服务器算**，请求方提交 `content_sha256` 直接 400：自带摘要等于让调用方
  自己决定「校验通过」。
- **创建者身份**，与其他写盘路由同一边界。

`version` 必须在该资源的 `versions` 列表里，否则请求被拒——一个拼错的版本号
不能变成任意路径读取。

### 找一条资源：`GET /resources?query=`

搜索框的匹配面是**名称、ID、描述与正文**四个。前三面是元数据，前端拿到列表就能
自己筛；**正文这一面只有服务器算得出**，因为 `GET /resources` 不返回正文。

不返回正文是刻意的，三个理由：`read_entry` 每次读取都重新校验摘要（读清单、
读文件、算 SHA-256），对每条资源都做一遍等于把一次列表请求变成 N 次全文件哈希；
正文可能有几十 KB，几十条就是一次几 MB 的响应，其中绝大部分与当次搜索无关；
提示词正文里是用户写进去的规则，无条件塞进每一次列表响应，等于让一个只想看
清单的请求把全部正文都取回浏览器。

因此正文过滤在服务器侧做，而**响应里仍然不含正文**：搜索是为了缩小清单，
不是为了取回内容。

| 边界 | 行为 |
| --- | --- |
| 读正文的类型 | 只有 prompt / memory / session。skill 与 hook 的正文是行为声明，把它们并进关键词搜索会让一次搜索读遍所有 hook 命令行；它们仍可按 ID / 名称 / 描述命中 |
| 正文读不出来（文件被篡改、摘要不匹配） | **跳过正文这一面**，不让整个列表 500——一条坏资源不该让「列出资源」不可用 |
| 关键词长度 | 上限 200 字符，超出 400。无界关键词会让每条资源都做一次超长子串匹配 |
| 空关键词 | 返回全部。「没在搜」不等于「搜不到」 |
| 与 `type` 同用 | 两个维度可叠加 |

界面上前端先按三个元数据面即时过滤，同时对服务器发一次节流（300ms）的正文查询，
两者取并集。服务器返回的是前端结果的**超集**，所以请求在途时只会少显示几行，
不会显示错的行；在途期间不报「没有匹配」，那时结论还没出来。

**名称与描述是投影出来的顶层字段。** 它们存在 `source_metadata` 里
（`author_document`、`install_skill` 与目录安装都写在那），
读取时由 `_snapshot()` 提到记录顶层，没有名字时是 `null` 而不是缺字段。
投影而不是另存一份，是因为存两份就有两份可以各自漂移，而漂移之后没有症状——
列表显示旧名字、更新检查用新名字，两边都「有值」。

修补显示名走 `set_display_metadata()`：不抬版本号、不改摘要、不重新解包
（显示名不参与 `content_sha256`），且按构造只接受 `name` 与 `description`——
`owner` / `repository` / `branch` / `directory` / `catalog_id` 决定「去哪里取下一版」，
改它们等于把资源指向另一个上游，那是安装路径的权限。
每次 `install()`（含启动时的 `ensure_builtins()`）会给缺名称的已装资源补上目录里
那一份，**只补空缺**：用户已经改成自己的叫法时，用目录里的名字盖掉它
等于每次启动都撤销一次用户的重命名。

**改过的名字能过升级与回滚。** `source_metadata` 在升级与回滚时整体替换，
但 `name` / `description` 两个键按优先级单独取值，且两种情形的优先级相反：

| 动作 | 谁优先 | 为什么 |
| --- | --- | --- |
| 升级（`update_archive`） | 新清单，其次现存记录 | 新清单是上游这一版的说法；它不声明名称（手工打包的 ZIP 常见）时才沿用旧的，否则一次升级会让名字变成 `null`、列表回落到显示 ID |
| 回滚版本 / 恢复备份 | 现存记录，其次存档 | 存档记录的是**当时**的叫法，比用户之后的重命名更旧。回滚的是内容，不该顺带撤销重命名 |

「清空名称」也是一个用户动作：两份都没有这个键时它被去掉而不是留下旧值，
否则清空会在下一次升级时被悄悄撤销。带过去的只有这两个键——
来源坐标始终跟新清单走，否则更新会去错地方。

### 「已启用」不等于「生效」

装好并启用一个 Skill 之后，资源列表显示「已启用」。但它只有在被**绑定到某个
Agent** 之后才会进入 LLM 请求——`executor._build_messages` 遍历的是
`agent.prompt_bindings` / `skill_bindings` / `memory_bindings`，不是「所有已启用
的资源」。没有绑定时状态是「已启用」而实际效果是零。

这曾经是一个无法自查的状态：界面上没有任何地方在说「它还差一步」，
于是用户看到「已启用」、得到「什么都没变」，然后去怀疑模型或提示词。

现在资源响应带两个字段：

| 字段 | 含义 |
| --- | --- |
| `bound_agent_ids` | 绑定了这个资源的 Agent。即使绑定当前被停用也会列出——它解释了「为什么改这个 Agent 会影响这个资源」 |
| `in_effect` | 这个资源当前是否真的进入 LLM 请求（资源已启用 **且** 有启用的 Agent 用启用的绑定引用它） |

界面在「已启用但 `in_effect` 为 false」时额外标一个**未生效**，并给出下一步。

**字段缺失表示「不知道」**，与 `false`（确定未生效）是两件事：读不到 Agent
注册表的部署里两个字段都不出现。把「不知道」显示成「未生效」等于给出一个
我们没有依据的论断，而那会让人去解决一个不存在的问题。

## 3.1 技能的渐进披露：模型「选用」而不是被灌满
绑定的技能不再整篇塞进每一次请求。机制与主流 Agent 客户端一致：

| 技能形态 | 系统提示词里 | 正文什么时候载入 |
| --- | --- | --- |
| 有前置元数据（`name` + `description`） | 一行目录：名字、用途、对应的工具名 | 模型调用 `skill_<资源 ID>` 时 |
| 没有前置元数据（纯文本、旧资源） | 整篇注入 | 不适用 |

判据是**能不能广告**，不是一个新开关。没有 `description` 的技能无法用一行话
说明自己能做什么，广告它等于让模型去猜正文内容；那类技能仍整篇注入，
行为与本特性之前逐字节一致，升级不会让既有部署的技能突然「消失」。

`allow_tools` 关闭的 Agent 也一律整篇注入：没有工具可调时，一行目录是一句
模型无法兑现的空头承诺。

为什么不全部整篇注入：成本随「技能数 × 请求数」线性增长，而其中绝大部分与
当轮问题无关。十个技能各 2000 token，就是每一轮固定多付 20000 token。
更关键的是模型**无法选用**一个技能——没有可调用的东西，就没有「决定用它」
这个动作，而那正是 Skill 机制的核心。

正文由 `skill_<资源 ID>` 工具返回，版本取自**本轮快照里的绑定**而不是
「当前版本」：一次对话中途被更新的技能不该让前后两轮遵循不同的说明，
那种不一致无法从对话记录里看出来。

### 依赖缺失会写进技能广告

技能正文里的命令能不能真的执行，取决于对应的系统依赖装没装（第 8 节）。
运行时读依赖状态，并在广告与工具描述里加一句就绪提示：

| 依赖状态 | 广告里 |
| --- | --- |
| 已就绪 | 什么都不加 |
| 确认缺失（`ready` 为 `false`） | 点名缺失的组件，并要求模型不要假装执行过 |
| 未知（未探测、依赖服务不可用、id 未登记） | 什么都不加 |

「未知」不冒充「缺失」：那会劝退一个本来能用的技能。反过来更要紧——
没装 `agent-browser` 却照着技能写命令时，最坏的表现不是报错，而是模型把
「我已经打开了浏览器」当成事实继续往下答，而用户看不出与真的执行成功有什么
区别。就绪提示进的是**工具描述**（不只是目录行）：那是模型决定要不要调用时
唯一会读的地方。

## 4. 权限化 lifecycle Hook

扩展 manifest 的核心字段为 `name`、`version`、`capabilities` 和 `hooks`。示例只申请监听模型目录刷新：

```json
{
  "name": "catalog-audit",
  "version": "1.0.0",
  "capabilities": ["lifecycle_hooks"],
  "hooks": ["model_catalog_refreshed"]
}
```

允许的 capability 为 `lifecycle_hooks`、`events`、`file`、`network`、`process`、`config_write`、`secret`。允许的 lifecycle 为：

- `startup_completed`
- `shutdown_requested`
- `workflow_before`
- `workflow_after`
- `workflow_error`
- `dispatch_preview`
- `model_catalog_refreshed`
- `mcp_operation`

注册未声明或未知 lifecycle 会被拒绝并审计。审计记录包含扩展名、动作、结果、lifecycle/capability 等结构化元数据，不保存 payload 内容或密钥。Hook 异常会记录失败；具体主流程是否继续取决于触发点实现，不能把它当事务回滚或强制审批机制。

用 `GET /backend-api/api/plugin/plugins` 查看插件清单，用 `GET /backend-api/api/plugin/plugins/<plugin_name>` 查看单个插件及 manifest。禁用或删除插件属于运行状态变更，应先确认受影响工作流；旧式无 manifest 插件为兼容性仍可加载，不能据此推断其最小权限。

## 5. MCP 工具集成

### 5.0 工具也走渐进披露：Tool Search

工具数超过 `agent_runtime.tool_search_threshold`（默认 12）时，系统提示词里
**不再放每个工具的完整 JSON Schema**，只放一行目录（名字 + 一句用途），
完整定义由 `search_tools` 按关键词取回。机制与技能的渐进披露（第 3.1 节）
完全对称，因此只需要理解一套心智模型。

为什么按数量而不是按开关：

| 工具数 | 全量注入 | 目录 + 搜索 |
| --- | --- | --- |
| 三个 | 一次性开销很小 | 多一轮往返，纯损失 |
| 四十个 | 每轮固定多付上万 token，且模型更容易选错 | 目录几百 token，用到才付 schema |

同一个布尔开关在这两种规模上一个是纯损失、一个是纯收益，所以它是阈值。
**`0` 表示关闭**，拿回逐字节一致的全量注入行为。

四条边界：

- **搜到的工具立即进入本轮工具列表。** 只把名字告诉模型而不放进列表，
  它下一轮调用会撞 `permission denied for MCP tool`——按我们给的目录做了正确的事
  却被判成越权，而那个错误无法自查。
- **搜索工具搜完不收走。** 第一次没搜准时才有第二次机会。
- **白名单之外的工具既不进目录，也搜不出来。** 搜索是一条取回路径，不是提权路径；
  否则「工具白名单」这个边界就没了。
- **无命中返回「没找到」并建议换词，不猜最接近的那个。** 猜一个交出去比返回空更糟，
  模型会调用一个它没有要求的工具。

**怎么确认这步成了**：在「追踪 → 请求日志」里打开一次真实请求，工具列表里应只有
`search_tools`（工具多时），而系统提示词里能看到那份目录。模型用到某个工具的那一轮，
它才出现在工具列表里。

只读盘点接口：

- `GET /backend-api/api/mcp/statistics`
- `GET /backend-api/api/mcp/servers`
- `GET /backend-api/api/mcp/servers/<server_id>`
- `GET /backend-api/api/mcp/servers/<server_id>/tools`
- `GET /backend-api/api/mcp/tools`

推荐用 `mcp_tools.yaml` 集成：先连接可信服务器，再在用户工作流副本中设置 `enabled_tools` allowlist，最后把受限规则指向该副本。服务器断开、工具不在 allowlist 或模型不支持函数调用时应明确失败或退化为普通对话，不得静默扩大工具权限。

直接调用接口为 `POST /backend-api/api/mcp/servers/<server_id>/tools/call`，请求体形如：

```json
{
  "toolName": "read_file",
  "params": {"path": "/approved/example.txt"}
}
```

这是真实外部操作，不是 dry-run。工具可能读取或写入数据、执行命令、发送消息、访问网络或产生费用；调用前必须由操作人员核对服务器、工具名、参数、数据范围和费用。Kirara AI 当前没有通用 MCP 人工审批中心，`enabled_tools` 只是工作流 allowlist，不等于逐次审批。

MCP 的 prompt/resource 可通过 `/backend-api/api/mcp/servers/<server_id>/prompts`、`/backend-api/api/mcp/servers/<server_id>/resources` 等 API 浏览或采样，但当前没有对应的工作流 Block。卸载时先禁用引用规则，删除/修改工作流中的 provider，再停止或删除服务器；服务器启停、配置更新和删除都有副作用，应在 WebUI 中人工确认。

## 6. Agent Hook：按事件与按工具触发

第 4 节的 lifecycle Hook 属于**插件**生命周期。Agent 运行时另有一套 Hook，
声明在 `hook` 类型的资源里，由 `AgentHookRuntime`（`kirara_ai/agent_runtime/hooks.py`）
派发，事件取值为：`PreToolUse`、`PermissionRequest`、`PostToolUse`、
`PreCompact`、`PostCompact`、`SessionStart`、`SessionEnd`、`UserPromptSubmit`、
`SubagentStart`、`SubagentStop`、`Stop`。

这 11 个事件**全部都会被真正派发**，由 `tests/agent_runtime/test_hook_event_contract.py`
双向锁住：声明了必须能找到派发点，派发了必须已在清单里。反向那一条同样重要——
派发一个未声明的事件时，声明校验会拒绝用户为它写钩子，于是那个派发点永远没有消费者。

三个事件的派发时机需要单独说明，因为它们不在主对话回路上：

| 事件 | 何时派发 |
| --- | --- |
| `SubagentStart` | 队友委派通过参数与 Agent 校验之后、真正执行之前。校验不过时不派发——委派没有发生，报告「子代理已启动」会让审计里出现一对没有实际执行的 Start/Stop |
| `SubagentStop` | 委派结束，**无论成功、队友无输出还是抛异常**。放在 `finally` 里，因此不会留下一个永不结束的 `SubagentStart` |
| `SessionEnd` | 会话被清理时（`DELETE /agents/sessions/<id>` 与 `.../history` 两条路径），在清理**之后**派发。Hook 若去读这个会话，应当看到它已经被清理，而不是观察到一个与所告知事件相矛盾的状态 |

内置 `hook:ai-debug` 声明这 11 个事件**全部**，因此它可以直接当作
「Hook 到底有没有在跑」的现成样本：绑上它、跑一轮对话，
`GET /resources/audit` 里应当逐个事件各出现一条记录。
覆盖关系由 `tests/plugin_manager/test_builtin_hook_event_coverage.py` 锁住——
少一个事件就等于那类事件在产品上没有可验证的入口，而用户照这份声明抄的时候
抄到的里面压根没有它。

落盘的渠道身份也进界面：`GET /agents/sessions` 每一行带 `channel_identity`
（渠道类型、适配器实例、账号、会话范围、发送者），会话表显示「渠道类型 · 发送者」，
完整五元组在悬浮提示里。这一列不是装饰——`session_id` 是一个 64 位摘要，
而清空历史与删除会话都以它为唯一标识：分不清哪一行属于谁的时候，
那两个动作只能靠猜。升级前写入的会话这一列显示「未记录」，
与空白刻意区分（空白会被读成「渠道身份丢了」）。

`SessionEnd` 有一个前提值得知道：它需要一个真实的 `ChannelContext`，
而会话文件按摘要命名，渠道身份是从本版起才随历史一并落盘的。
**升级前写入的会话不会派发 `SessionEnd`**——但清理照常成功。这是刻意的：
派发一个编出来的渠道身份会污染审计，而因为拿不到身份就拒绝清理，
等于让用户永远删不掉升级前的会话。

声明是 JSON，一个事件一个对象：

```json
{
  "events": {
    "PreToolUse": {
      "handler": "audit.pre_tool",
      "matcher": "Bash|Write",
      "enabled": true,
      "timeout_ms": 1000,
      "max_output_bytes": 4096
    }
  }
}
```

两个字段值得单独说明：

- **`matcher`** 限定该事件适用于哪些工具。可以是正则字符串（`"Edit|Write"`），
  也可以是工具名列表（`["Bash", "Write"]`，会被逐个字面量转义后取并集）。
  匹配是**整名匹配**：`Bash` 不会命中 `BashOutput`。
  不写 `matcher` 时适用于所有调用，与历史行为一致。
  声明了 `matcher` 但事件本身不带工具名（例如 `SessionStart`）时**不触发**——
  一个按工具限定的 Hook 不应该在无关事件上执行。

  这条很重要：没有 matcher 时，一个只为某个危险工具写的 `PreToolUse` Hook
  会在**每次**工具调用上被拉起来，既付出进程启动开销，也让它的阻断能力
  作用到无关调用上。

- **`enabled`** 允许在不删除整个 Hook 的情况下关停单个事件，
  此前只能改文件重装。缺省为 `true`。

`type` 为 `command` 的 Hook 会启动外部进程，因此额外要求：
调用者必须是该 Agent 的创建者（`principal_can_control_agent`），
且运行时策略允许进程执行；两者任一不满足直接拒绝并审计。
声明里出现 `script` / `code` / `python` 字段会被拒绝——可执行内容必须走 `command`。

跳过与执行都会进审计流（`resource_lifecycle.append_runtime_audit`），
跳过原因区分 `binding_disabled`、`event_not_declared`、`matcher_not_matched`，
可用 `GET /backend-api/api/resources/audit?component=agent_hook` 查询。

### Hook 返回的上下文会进到哪里

`systemMessage` 与 `hookSpecificOutput.additionalContext` 会作为一条 system 消息
插入**下一次**模型请求：

| 事件 | 上下文是否进入模型 |
| --- | --- |
| `SessionStart`、`UserPromptSubmit` | 是，进入本轮第一次请求 |
| `PreToolUse`、`PostToolUse` | 是，进入工具轮之后的那次请求 |
| `Stop`、`SessionEnd` | 否——之后已经没有模型调用，无处可去 |
| 其余事件 | 否 |

`PreToolUse` / `PostToolUse` 这一条曾经是缺的：字段被解析、审计记为
`status: ok`，然后丢掉。一个想告诉模型「这个结果的单位是分而不是元」的 Hook
写得完全正确、看起来也成功了，而模型永远看不到那句话——Hook 作者只能怀疑
自己的业务逻辑。**注入只做一次**：内容进入消息序列后每轮都带着，
重复注入会让同一段文本在长对话里出现十几次，白花 token 还会被模型
当成被反复强调的重点。

### 上线前预演：不执行也能看清会不会触发
Hook 此前只能「装上再看它会不会跑」：事件名写错、matcher 写成非法正则、
把 `command` 写进不该写的位置，都只能在真实请求里暴露——而那时它已经在
生产路径上了。两个只读接口解决这件事：

| 接口 | 用途 |
| --- | --- |
| `GET /backend-api/api/agents/hooks` | 列出每个已安装 Hook 声明的事件、限定的工具、超时与是否需要进程执行权限 |
| `POST /backend-api/api/agents/hooks/<resource_id>/preview` | 传 `event` 与可选 `tool_name`，回答「这个 Hook 会不会因为这个工具而触发」 |

两者都**不执行 handler、不启动任何进程**。声明解析失败时返回错误说明而不是抛出，
因此一个坏声明不会让整份列表打不开。预演结果里的 `reason` 直接给出不触发的原因
（`matcher_not_matched`、`event_not_declared_or_disabled`、`binding_disabled`、
`declaration_invalid`），可以据此确认 matcher 是否按预期收窄。

WebUI 在「Agent 管理 → Hook 声明与预演」提供对应界面：填一个工具名，
逐个事件点「预演」即可。

## 6.1 删掉一个 Agent

`DELETE /backend-api/api/agents/<agent_id>`，界面上是 Agent 编辑器右上角的「删除」
（新建中的配置不显示该按钮——它还不存在）。

三条拒绝，各自返回可照做的原因而不是通用失败：

| 拒绝 | 原因 | 怎么解 |
| --- | --- | --- |
| 全局默认 Agent | 删掉它之后，没有显式绑定的渠道无处可落 | 先把别的 Agent 设为默认 |
| 还有渠道绑定 | 那个渠道的消息会解析到一个不存在的 Agent | 先解绑该渠道 |
| 还有账号或会话绑定 | 同上，且已有会话的历史会指向空配置 | 先解绑或删除那些会话 |

界面把后端那句话原样显示。换成「删除失败」会把三个用户自己能解决的问题变成死胡同。

**为什么这条要单独有入口**：Agent 此前只能新建和编辑。建错一个 ID 就永久留在列表里，
而它仍然参与「渠道身份 → Agent」的解析——一条发到那个渠道的消息会落到它身上。

## 7. 会话与待确认队列

Agent 运行时的会话历史与待确认操作持久化在 `data/sessions/`。
对应的只读与清理接口：

| 接口 | 用途 |
| --- | --- |
| `GET /backend-api/api/agents/sessions` | 列出持久化会话：Agent、消息条数、最近更新时间、待确认数量 |
| `DELETE /backend-api/api/agents/sessions/<session_id>` | 删除该会话的存储历史 |
| `DELETE /backend-api/api/agents/sessions/<session_id>/history` | 清空历史但保留会话 |
| `GET /backend-api/api/agents/confirmations` | 列出仍在等待人工决定的确认记录 |

三点边界：

- **返回值不含对话正文，也不含待确认操作的工具参数。** 会话列表只给计数与时间戳；
  「列会话」不应该顺带变成一个能读取全部聊天记录的接口。
- `session_id` 只接受 64 位十六进制摘要。`..`、`a/b` 这类输入被直接拒绝，
  不会被拼进文件路径。
- 未部署 Agent 运行时的实例上，这些接口返回 `503` 并说明原因，而不是 500。

WebUI 在「Agent 管理」页底部提供对应的只读列表与清空/删除动作。
需要重置某个会话的上下文时清空历史即可，Agent 绑定关系不受影响。

## 7.1 队友（Teammates）：Agent 之间的委派

`AgentDefinition.teammate_agent_ids` 非空时，模型会额外获得
`delegate_to_<agent_id>` 工具。它与 MCP 工具同一形态，因此模型侧不需要区分
「这是队友还是工具」。队友用**自己的**模型链、提示词、技能与工具白名单执行子任务。

在 WebUI「模型与 Agent → Agent」的「队友（Teammates）」区块配置，
下拉只列出已启用且不是自己的 Agent。

这条能力真正的风险不是「功能不够」，而是**无限递归**：A 委派 B、B 委派 A，
每一层都是一次真实的模型调用，账单与时延同时爆炸。因此有五道约束：

| 约束 | 为什么 |
| --- | --- |
| 深度上限 2，每次委派递减 | 递归的硬性天花板 |
| 深度耗尽时**不再暴露**委派工具 | 暴露一个必定被拒的工具会让模型反复撞墙，白花一轮 token |
| 自委派在定义期就被拒 | 最短的无限递归 |
| 只为**存在且启用**的队友生成工具 | 同上：不让模型调用一个注定失败的工具 |
| 空 `task` 作为工具错误返回 | 队友看不到主对话，凭空猜只会浪费一轮 |

两点授权边界：

- **委派不是绕过授权的旁路。** 委派本身不动服务器，所以不需要人工确认；
  但队友自身的高危工具仍走原有的 `PermissionRequest` 与创建者校验链路
  （见第 6 节）。非创建者拿不到工具，因此也无法借委派提权。
- **队友集合变化会让待确认操作失效。** 它进入 `_agent_policy_signature`；
  否则「确认的是一件事、执行的是另一件事」。

持久化在 `data/agents/registry.json`。早于本特性的注册表没有该键，
读到时缺省为空（不启用），升级不会宕机。

## 8. 系统依赖：装什么、装到哪、谁能装

Skill 包和它依赖的可执行程序是**两件事**。装上 `agent-browser` 这个 Skill 不等于
服务器上有 `agent-browser` 命令；反之亦然。依赖目录把这条边界显式化：

| 接口 | 用途 |
| --- | --- |
| `GET /backend-api/api/resources/dependencies` | 列出全部依赖及其就绪状态、版本、被谁需要 |
| `POST /backend-api/api/resources/dependencies/<id>/probe` | 只探测，不安装 |
| `POST /backend-api/api/resources/dependencies/<id>/install` | 受控安装（需要确认，且只跑服务器登记的固定命令） |
| `GET /backend-api/api/resources/dependency-tasks` | 安装任务列表与日志 |

当前目录中的条目：

| 依赖 ID | 类型 | 能否服务器侧安装 | 说明 |
| --- | --- | --- | --- |
| `node-runtime` | runtime | 否（运维安装） | Node.js / npm / npx |
| `python-tooling` | runtime | 否（运维安装） | `uv` |
| `agent-browser-cli` | cli | 是 | `npm install -g agent-browser` |
| `agent-browser-browser` | browser-runtime | 是 | `agent-browser install`（拉 Chromium，超时 900s） |
| `context7-runtime` | mcp-runtime | 否 | Context7 由 npx 启动，修 Node 即可 |
| `uvx-runtime` | mcp-runtime | 否 | `mcp:fetch` / `mcp:time` 靠 `uvx` 拉起。**与 `uv` 是两个命令**：uv 装了不代表 uvx 在 PATH 上（uvx 是 uv 0.3 起才分发的独立入口）|
| `npx-runtime` | mcp-runtime | 否 | 其余以 npx 启动的 stdio MCP（memory / sequential-thinking / filesystem / chrome-devtools / playwright）|
| `graphify-cli` | cli | 是 | `uv tool install --upgrade graphifyy` |
| `rtk-cli` | cli | 是 | 终端输出压缩；**与同名的 Rust Type Kit 不是同一个工具**，以 `rtk gain` 是否可用为准 |
| `memsearch-cli` | cli | 是 | `uv tool install --upgrade memsearch` |
| `context-mode-plugin` | claude-plugin | **否** | Claude Code 插件 |
| `caveman-plugin` | claude-plugin | **否** | Claude Code 插件 |

三条边界必须讲清楚：

- **Claude Code 插件装在操作者自己的 Claude 配置里，不是服务器运行时组件。**
  因此它们的 `install_supported` 为 `false`，接口会拒绝安装请求并返回运维指引。
  给它们编一条安装命令只会把命令跑到错误的目标上。
- **安装命令是服务器登记的固定值**，请求方不能传入命令或参数；
  这是「只有创建者能改 VPS」这条约束在依赖安装上的落地方式。
- **探测一个不存在的可执行文件是「未安装」，不是接口错误。** 探测接口在这种情况下
  返回 `missing` 状态而不是 5xx，因此前端可以正常展示「未安装」并给出下一步。
- **按名字识别依赖的那张表要覆盖每一条 CLI 登记项。** 一个技能需要哪些服务器命令，
  由技能的 `name` / 目录名去查 `_SKILL_NAME_DEPENDENCY_IDS`。此前这张表只认
  `agent-browser` 与 `graphify`，于是 `memsearch` / `rtk` 技能一律判成「不需要任何依赖」——
  技能广告里不会出现「服务器上没有这个命令」，模型照着一份它执行不了的说明自信作答。
  `tests/plugin_manager/test_skill_dependency_mapping.py` 按登记项驱动：
  新增一条 CLI 登记项而忘了加映射时立刻红。
  两条 `claude-plugin` 不进这张表——它们装在操作者本机，加进来会让技能在任何
  服务器上都显示缺依赖，而那个「缺」无从修复。

- **探测命令必须能区分同名的不同工具。** `rtk` 有两个不相关的同名程序，
  两个都响应 `--version`。只探版本号会把装错的那个判成「就绪」，
  而错误要到实际调用时才暴露——那时界面已经说过它可用了。因此 `rtk-cli` 探测
  `rtk --version` 与 `rtk gain` 两条，后者是说明里点名的判据。
  一条依赖的全部探测命令必须**逐条**成功才算就绪。

### stdio MCP 的依赖来自预设自己的声明

每个 stdio 型 MCP 预设在内置目录里声明它靠什么拉起（`runtime_dependency`：
`uvx` 或 `npx`），依赖判定读这个声明，因此**新增一个 stdio 预设时不需要再改判定逻辑**。

这一点此前是断的，值得记录失败形态：`runtime_dependency` 一度只是一个没人读的字段，
`uvx` 连登记项都没有，于是 8 个预设里 7 个的依赖判定返回空列表——而空列表的含义是
「这个资源不需要任何系统依赖」。用户装 `mcp:fetch`、启用、绑定全部成功，
机器上没有 uvx，界面唯一的线索是 MCP 面板显示「连接失败 / 工具数 0」，
没有一处说缺什么。人会去查网络、查配置、查 API Key。

现在资源列表按资源显示依赖状态并点名缺的是哪一个。**两个状态刻意不合并**：

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `unknown` | 还没探测过 | 去「系统依赖」里检查 |
| `missing` | 探测过，确实没有 | 去「系统依赖」里安装 |

混成一句「依赖未就绪」会让人去装一个本来就在的东西。
另有一种取值是**字段缺失**（老后端不提供依赖信息），此时界面不显示任何提示——
那与「不需要依赖」不是一回事，但都不该占用界面。

### 两个入口装同一批预设

八个 stdio MCP 预设有两条对等的入口，装出来的东西相同：

| 入口 | 形态 |
| --- | --- |
| 资源管理 → 发现并安装 | 目录搜索，服务器侧按内置条目打包并装成受控资源 |
| MCP → 从预设新增 | 预填一份可编辑的表单，走与手写配置同一条唯一性校验与保存路径 |

**两处的 id 必须一致**（`context7`、`fetch`、`time`、`memory`、
`sequential-thinking`、`filesystem`、`chrome-devtools`、`playwright`）。
装出两个不同 id 的同一个 MCP 之后，「为什么有两个 context7」无从解释，
而 `refresh_managed_servers` 也是按 id 对账的。

预设菜单每一项都标出它靠 `npx` 还是 `uvx` 拉起——两者都不是本项目的依赖，
运行时镜像都没装。不标的话，用户点了启用只会看到「连接失败 / 工具数 0」。

`filesystem` **不预填任何目录**：填一个具体路径等于替用户决定「哪些文件可以被
读写」，而这条 MCP 的全部风险就在那个参数上。预设给出的是一句必须补参数的提示。

预设**只填字段，不锁字段**：填完的每一项都可以改，保存走的仍是同一条校验路径。

### 受管 MCP 资源在本机怎么跑

从「资源管理 → 发现并安装」装进来的 MCP 条目住在**资源注册表**里，
不在 `config.yaml` 的 `mcp.servers`。这条区分有一个直接后果：
`PUT /mcp/servers/<id>` 只在 `config.mcp.servers` 里查找，因此它对受管资源
一律返回 404。要配置一条受管 MCP，用的是资源侧的入口——
资源列表行上的「运行时配置」按钮，即 `PUT /resources/<id>/runtime`。

可配置的只有「这台机器怎么跑它」：

| 字段 | 用途 |
| --- | --- |
| 追加启动参数 | 接在资源包自带参数**之后**。文件类服务器在这里填允许访问的目录 |
| 环境变量 | 按键合并。读回来的值是掩码，不回传就是保留 |
| 可访问根目录（`roots`） | 通过 MCP 协议声明给服务器，与启动参数是两套机制 |
| 工作目录 | 留空则用服务进程的工作目录 |
| 启动超时 | 1000–600000 毫秒，留空用默认 120000 |

**命令、启动参数本体、传输类型与 URL 不可改。** 它们来自资源包，
由 `content_sha256` 保护——那份声明回答的是「目录发布了什么」。
放开它们等于让「配一个可读目录」这个操作可以把 `npx` 换成任意程序，
或把一个本地 stdio 服务器换成一个远端地址。

因此 `filesystem` 的完整用法是两步：装进来，然后在运行时配置里追加目录。
只装不配的服务器**连得上但没有任何可操作范围**——`npx` 起得来、握手成功、
工具列得出，而每次调用都因为没有允许的路径而失败。

覆盖不写进归档，也不抬版本号：它描述的是这台机器，因此升级与回滚之后仍然生效
（新装的资源包换的是「发布了什么」，不是「本机允许什么」），
而删除资源时跟着消失，不留给下一个同名资源。

### 依赖状态会进到对话里

这份状态不只给安装界面看。绑定了技能的 Agent 在组装每一轮请求时会读它，
并在该技能的目录行与 `skill_` 工具描述里加一句就绪提示：

| 依赖状态 | 对话里的表现 |
| --- | --- |
| 全部就绪 | **一个字都不加**。就绪是常态，每句多余的话都是每轮都要付费的噪音 |
| 确认缺失（`ready` 为 `false`） | 点名缺哪个组件，并要求模型不要假装执行过其中的命令 |
| 未知（未探测、依赖服务不可用、探测抛错、目录里没登记） | **同样什么都不说** |

「未知」不能冒充「缺失」：那会劝退一个本来能用的技能，而这个损失完全来自我们的猜测。

这一跳存在的理由是，缺少它时的失败形态是**没有报错的假答案**：模型读到
`agent-browser open ...`，照着写下去，命令在服务器上并不存在，而模型无从得知，
只能把「我已经打开了浏览器」当成事实继续答。用户看不出与真的执行成功有什么区别，
因为语气一模一样。覆盖测试见 `tests/agent_runtime/test_skill_dependency_readiness.py`
（含一条端到端用例，断言警告真的出现在发给模型的请求里）。

### 谁能调这些接口

| 接口 | 需要的身份 | 为什么 |
| --- | --- | --- |
| `GET /resources/dependencies`、`GET /resources/dependency-tasks` | 登录 + `resources.read` | 只读状态属于正常使用 |
| `POST /resources/dependencies/<id>/probe` | **创建者** | 探测会在服务器上**执行**登记的 argv（`agent-browser doctor`、`rtk --version`）。「不安装」不等于「不执行」 |
| `POST /resources/dependencies/<id>/install`、任务 retry / cancel | **创建者** | 在服务器上执行安装命令 |
| `POST /resources/repositories`、`.../enabled` | **创建者** | 写 `registry.json`，改变「哪些外部来源可被安装」 |
| `DELETE /resources/repositories/<owner>/<name>/<branch>` | **创建者** | 摘掉来源登记。不可逆，因此额外要求 `{"confirmed": true}`；**不动**从它装过的资源 |
| MCP `POST /mcp/servers`、`PUT`、`DELETE`、`.../start` | **创建者** | 写 `config.yaml`；`start` 真的在服务器上拉起 stdio 子进程 |
| MCP `.../stop`、所有 `GET` | 登录 + `mcp.read` / `mcp.manage` | 停止只让扩展不再生效，不引入新的服务器副作用——与资源侧 `disable` 同一判断 |

> 曾经的自相矛盾：启用一个 **mcp 资源**要创建者身份，而直接
> `POST /mcp/servers` + `start` 达到同样效果却只要 scope。默认签发的 token 带
> `["*"]`，于是任何登录用户都能在 VPS 上起进程。源码级契约测试
> `tests/web/auth/test_creator_only_routes.py` 现在同时覆盖资源与 MCP 两个
> blueprint，新增一条只加 scope 的写操作路由会直接让它红。

### 从 IM 渠道使用受保护能力：声明创建者身份

上面那张表说的是 HTTP 接口。**聊天侧此前有一个被忽略的边界**：
`principal_can_control_agent` 是唯一门禁，而身份（principal）只由 HTTP Bearer
中间件注入。OneBot / QQ / Telegram / WeCom 的入站链路全程没有它，于是这些渠道上

- MCP 工具列表恒为空；
- command 型 Hook 恒被拒（含内置 `hook:ai-debug` 声明的全部 11 个事件）；
- 需确认的宿主操作走不到确认那一步。

注意这**不是**「非创建者不行」——设计是那样，实现成了「所有人都不行」，
包括创建者本人。缺的是一座桥：把「这个 QQ 号就是创建者」这件事声明出来。

```yaml
agent_runtime:
  creator_channel_identities:
    - channel_type: onebot        # webui / onebot / qqbot / telegram / wecom / http
      sender_scope: "10001"       # 你自己在该渠道上的用户标识，不是机器人的
      # account_scope: "20002"    # 可选：只认经由这个机器人账号收到的消息
      # adapter_instance: onebot-main  # 可选：限定适配器实例
      # allow_group_chat: false   # 群聊里是否生效，默认关闭
```

四条刻意的设计：

- **默认空表。** 不声明时行为与升级前逐字节一致，不存在「升级之后聊天里突然
  能动服务器」这种事。
- **渠道与发送者一起比。** QQ 号和 Telegram 用户 ID 可能撞号，只比一个等于
  把另一个渠道的同号用户也放了进来。填 `*` 不是通配，它只是一个匹配不到
  任何真实用户的普通字符串。
- **群聊默认不生效。** 群里所有人都看得到你发的指令并照抄；照抄的人
  `sender_scope` 不同因而拿不到身份，但把宿主操作暴露在多人可见的会话里是
  另一回事——一条误发的消息会被所有人看到并模仿尝试。要开必须显式声明。
- **主体取自 `AuthService` 的创建者身份本身**，因此它与 Agent 的
  `owner_subject`、与 WebUI 登录后的身份是同一个值。拿不到那个身份时
  返回「无身份」而不是编一个：一个匹配不上任何 Agent owner 的 subject
  只会让门禁静默失败，比没有更糟。

未声明的发送者仍然得到**正常的 AI 回复**——工具列表被清空而不是请求被拒，
这一点与需求「其他使用者收到涉及修改 VPS 的命令一律忽视，但仍会正常回复」一致，
并由 `tests/agent_runtime/test_host_authorization.py` 钉住。

## 9. 上线检查

1. manifest 只声明实际需要的 capability 与 lifecycle。
2. 工作流通过 `POST /backend-api/api/workflow/validate`，规则通过 `POST /backend-api/api/dispatch/preview` 和 `/backend-api/api/dispatch/reachability`。
3. Agent/Skill 的模型、工具、记忆、最大迭代和会话范围明确，工具 allowlist 默认为空。
4. 日志、错误和审计不含 token、密码、prompt 正文或工具 payload。
5. 已写明失败行为和移除顺序，并验证禁用扩展后主系统仍可运行。
