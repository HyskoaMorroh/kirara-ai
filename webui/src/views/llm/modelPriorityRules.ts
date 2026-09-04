/**
 * 模型优先链的排列规则。
 *
 * 为什么需要它
 * ----------
 * 「Agent 管理」的模型优先链是从上到下依次尝试的队列，而从 `GET /llm/backends`
 * 拉出来的候选是**按后端与声明顺序**排的——同一家族的新旧版本混在一起，
 * 一条 16 项的链里可能第 1 项是 gemini、第 3 项是已经过时的 flash、
 * 第 8 项才是当前最强的 gpt。队列顺序决定「先用哪个」，因此这个顺序本身就是配置。
 *
 * 三条规则
 * ------
 * 1. **家族顺序固定**：gpt → claude → gemini → 其余（保持原相对顺序）。
 * 2. **同家族只留最高编号系列**：gpt 最新 5.6 时只留 `gpt-5.6-*`；
 *    claude 最新 5 时只留 `claude-*-5`。不写死版本号，按**实际拉到的模型**算。
 * 3. **gemini 只允许带 `pro` 的**：`gemini-3.5-flash` 编号更高但不是 pro。
 *
 * 一处必须写对的顺序
 * ----------------
 * gemini 要**先筛 pro、再在 pro 里取最高编号**。反过来（先按全体算出最新系列
 * 是 3.5、再筛 pro）会得到空列表——不存在 `gemini-3.5-pro`。这个错不抛异常，
 * 只让 gemini 整族消失，而界面上看起来就像「这几个模型没配」。
 */

/** 家族的固定次序。不在表里的家族排在最后。 */
const FAMILY_ORDER = ['gpt', 'claude', 'gemini'] as const

type Family = (typeof FAMILY_ORDER)[number] | 'other'

/**
 * 同系列内的智能度次序：**下标越小越强**。
 *
 * 这份表的依据是使用者给出的规则，不是从上游 API 推出来的。
 * 查过 CLIProxyAPI 的模型目录：`internal/registry/models/models.json` 只有
 * description 营销文案，没有任何 rank / tier / intelligence 字段；
 * `codex_client_models.json` 里的 `priority`（sol=6 / terra=7 / luna=8）由
 * `models.go:149-160` **按 display_name 字母序**算出，是列表显示顺序而不是能力评级——
 * sol/terra/luna 恰好字母序一致，属于巧合，不能当依据。
 *
 * 因此这里是一份**显式声明**：改它要改这张表，而不是去猜某个数字的含义。
 * 表里没有的档位排在已知档位之后（保持相对顺序），因为「不认识」不等于「最弱」——
 * 新上游随时会出一个没见过的档位名，把它当最弱会让一个可能更强的模型沉到链尾。
 */
const TIER_ORDER: Readonly<Record<string, readonly string[]>> = {
  gpt: ['sol', 'terra', 'luna'],
  claude: ['opus', 'sonnet', 'fable', 'haiku'],
}

/**
 * 一个模型名解析出来的坐标。
 *
 * `series` 是**系列号**而不是完整版本：`claude-sonnet-4-20250514` 的系列是 4，
 * 那串日期不参与比较。取不到系列时是 `null`——那种模型不参与淘汰（见下）。
 */
interface ModelCoordinate {
  raw: string
  family: Family
  series: number | null
  isPro: boolean
  /**
   * 智能度档位在 {@link TIER_ORDER} 里的下标；表里没有时是 `Number.MAX_SAFE_INTEGER`
   * （排在已知档位之后，见 TIER_ORDER 的说明）。
   */
  tier: number
  /**
   * 去掉供应商前缀后的模型名。
   *
   * `ANT/claude-opus-5` 与 `claude-opus-5` 在网关上是**同一个上游模型**：
   * CLIProxyAPI 的 `conductor_models.go:685-698` 把前缀 `TrimPrefix` 掉才发给上游，
   * `server_routes.go:403-404` 明确注明前缀不转发。但它们是**两个独立的路由目标**
   * （`service_models.go:600-614` 为裸 ID 与带前缀 ID 各注册一条 ModelInfo）：
   * 带前缀的把候选收缩到那一条凭据，不带前缀的匹配所有凭据由调度器轮询。
   *
   * 因此排序时按这个字段**归组**（同一模型的各种前缀连续排列），
   * 但**绝不去重**——去重等于把使用者配好的多凭据冗余删掉。
   */
  bareName: string
}

/** 去掉供应商前缀：真实链里是 `CHMA/claude-opus-5`、`GLE/gemini-3.1-pro` 这种形态。 */
function stripProviderPrefix(model: string): string {
  const slash = model.lastIndexOf('/')
  return slash >= 0 ? model.slice(slash + 1) : model
}

function familyOf(name: string): Family {
  for (const family of FAMILY_ORDER) {
    if (name.startsWith(family)) return family
  }
  return 'other'
}

/**
 * 解析系列号。
 *
 * `-3-5` 与 `-3.5` 等价，因此两种分隔符都按「主版本.次版本」读。
 * 只取**第一段**版本号：`claude-sonnet-4-20250514` 读成 4，
 * 而不是把日期当成次版本读成 4.20250514。
 */
function seriesOf(name: string): number | null {
  // 先试 `<主>.<次>` 或 `<主>-<次>`，两位都要是数字且次版本不超过两位——
  // 这排除了日期后缀（20250514 有八位）。
  const paired = name.match(/(?:^|[-_])(\d+)[.-](\d{1,2})(?![\d])/)
  if (paired) {
    return Number.parseFloat(`${paired[1]}.${paired[2]}`)
  }
  const single = name.match(/(?:^|[-_])(\d+)(?![\d.])/)
  if (single) {
    return Number.parseFloat(single[1])
  }
  return null
}

/**
 * 解析智能度档位。
 *
 * 档位名必须作为**独立词段**出现：`claude-opus-5` 的 `opus` 命中，
 * 而一个恰好含 `luna` 子串的模型名（`lunar-...`）不该被当成 gpt 的 luna 档。
 */
function tierOf(family: Family, name: string): number {
  const order = TIER_ORDER[family]
  if (!order) return Number.MAX_SAFE_INTEGER
  for (let index = 0; index < order.length; index += 1) {
    const pattern = new RegExp(`(?:^|[-_/])${order[index]}(?:$|[-_.])`)
    if (pattern.test(name)) return index
  }
  return Number.MAX_SAFE_INTEGER
}

function parse(model: string): ModelCoordinate {
  const bareName = stripProviderPrefix(model).toLowerCase()
  const family = familyOf(bareName)
  return {
    raw: model,
    family,
    series: seriesOf(bareName),
    // `pro` 必须作为独立词段出现，避免把 `improved` 这类子串误判成 pro。
    isPro: /(?:^|[-_/])pro(?:$|[-_.])/.test(bareName),
    tier: tierOf(family, bareName),
    bareName,
  }
}

/**
 * 在一组同家族模型里，只留系列号最高的那些。
 *
 * 没有系列号的模型**一律保留**：它们没有可比较的版本，丢掉等于让用户配好的
 * 模型凭空消失，而保留最多只是多一个备选。
 */
function keepLatestSeries(items: ModelCoordinate[]): ModelCoordinate[] {
  const withSeries = items.filter((item) => item.series !== null)
  if (withSeries.length === 0) return items

  const highest = Math.max(...withSeries.map((item) => item.series as number))
  return items.filter((item) => item.series === null || item.series === highest)
}

/**
 * 同家族内排序：先按智能度档位，同档位的按裸名归组。
 *
 * 两条都不是可选项：
 *
 * - **档位排序**：`claude-opus-5` 与 `claude-sonnet-5` 系列号相同，
 *   不排档位时它们的先后取决于上游返回顺序——也就是随机。而模型优先链是
 *   「从上到下依次尝试」的队列，第一项决定绝大多数请求用哪个模型。
 * - **同裸名归组**：`ANT/claude-opus-5` 与 `claude-opus-5` 是同一个上游模型的
 *   两个路由目标（见 `bareName` 的说明），把它们排在一起才能让「先试钉死的凭据、
 *   再试轮询」这个意图在链上看得出来；穿插排列会让人以为是两个不同模型。
 *
 * 排序用稳定的多级比较而不是多次 `sort`：`Array.prototype.sort` 在同键时的
 * 相对顺序由实现决定（V8 现在稳定，但依赖它是隐含前提），因此把「原始下标」
 * 作为最后一级键显式写出来。
 */
function orderWithinFamily(items: ModelCoordinate[]): string[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      if (left.item.tier !== right.item.tier) return left.item.tier - right.item.tier
      if (left.item.bareName !== right.item.bareName) {
        return left.item.bareName.localeCompare(right.item.bareName)
      }
      return left.index - right.index
    })
    .map((entry) => entry.item.raw)
}

/**
 * 按规则重排模型优先链。
 *
 * 输入是用户当前的链（或候选清单），输出是排好序并淘汰过旧系列的新链。
 * 不去重同一模型的带前缀与不带前缀两种写法——它们指向不同后端，
 * 是故障转移的正常形态。
 */
export function rankModelPriority(models: readonly string[]): string[] {
  const seen = new Set<string>()
  const coordinates: ModelCoordinate[] = []
  for (const model of models) {
    const trimmed = String(model ?? '').trim()
    if (!trimmed || seen.has(trimmed)) continue
    seen.add(trimmed)
    coordinates.push(parse(trimmed))
  }

  const ranked: string[] = []
  for (const family of FAMILY_ORDER) {
    let group = coordinates.filter((item) => item.family === family)
    if (family === 'gemini') {
      // 先筛 pro，再取最高系列。顺序反了会让 gemini 整族消失（见文件头说明）。
      group = group.filter((item) => item.isPro)
    }
    ranked.push(...orderWithinFamily(keepLatestSeries(group)))
  }
  // 未知家族保持原相对顺序排在最后：它们可能来自尚未登记的后端，
  // 没有可靠的系列语义可用来淘汰。
  for (const item of coordinates) {
    if (item.family === 'other') ranked.push(item.raw)
  }
  return ranked
}
