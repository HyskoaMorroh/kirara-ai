// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import { rankModelPriority } from '../src/views/llm/modelPriorityRules'

/**
 * 模型优先链的排列规则。
 *
 * 用户给的规则（截图里那条 16 项的链是排序前的样子）：
 *
 * 1. 家族顺序固定：**gpt → claude → gemini**。
 * 2. 同一家族只留**最高编号系列**。gpt 最新是 5.6，那就只出现
 *    `gpt-5.6-sol` / `gpt-5.6-terra` 这类；claude 最新是 5，那就只出现
 *    `claude-opus-5` / `claude-sonnet-5`。
 * 3. gemini 额外一条：**只允许带 `pro` 的**。`gemini-3.5-flash` 编号更高，
 *    但因为不是 pro 所以不能进。
 * 4. `-3-5` 与 `-3.5` 等价：同一个系列的两种写法。
 * 5. 规则跟着**刷新出来的模型**动态调整，不写死任何版本号。
 *
 * 一处必须钉住的顺序
 * ----------------
 * gemini 必须**先筛 pro、再在 pro 里取最高编号**。反过来做（先按全体算出最新
 * 系列是 3.5，再筛 pro）会得到空列表——因为不存在 `gemini-3.5-pro`。
 * 这个顺序错了不会报错，只会让 gemini 整族消失。
 */

describe('家族顺序固定为 gpt → claude → gemini', () => {
  it('按家族分组排列，与输入顺序无关', () => {
    const ranked = rankModelPriority([
      'gemini-3.1-pro',
      'claude-opus-5',
      'gpt-5.6-sol'
    ])

    expect(ranked).toEqual(['gpt-5.6-sol', 'claude-opus-5', 'gemini-3.1-pro'])
  })

  it('不认识的家族排在最后，且保持相对顺序', () => {
    // 未知家族不能被丢掉：用户手填的模型可能来自尚未登记的后端。
    const ranked = rankModelPriority([
      'qwen-max',
      'gpt-5.6-sol',
      'deepseek-v3'
    ])

    expect(ranked).toEqual(['gpt-5.6-sol', 'qwen-max', 'deepseek-v3'])
  })
})

describe('同一家族只留最高编号系列', () => {
  it('gpt 只留 5.6，丢掉 5 与 4.1', () => {
    const ranked = rankModelPriority([
      'gpt-4.1',
      'gpt-5.6-sol',
      'gpt-5-codex',
      'gpt-5.6-terra'
    ])

    expect(ranked).toEqual(['gpt-5.6-sol', 'gpt-5.6-terra'])
  })

  it('claude 只留 5 系，丢掉 4.6 与 4.5', () => {
    const ranked = rankModelPriority([
      'claude-opus-4-6',
      'claude-4.5-haiku',
      'claude-opus-5',
      'claude-sonnet-5'
    ])

    expect(ranked).toEqual(['claude-opus-5', 'claude-sonnet-5'])
  })

  it('带日期后缀的旧模型同样按系列淘汰', () => {
    // `claude-sonnet-4-20250514` 的系列是 4，不是 20250514。
    const ranked = rankModelPriority(['claude-sonnet-4-20250514', 'claude-opus-5'])

    expect(ranked).toEqual(['claude-opus-5'])
  })
})

describe('gemini 只允许 pro', () => {
  it('丢掉 flash，即使它编号更高', () => {
    // 这一条是核心：3.5 > 3.1，但 flash 不是 pro。
    const ranked = rankModelPriority(['gemini-3.5-flash', 'gemini-3.1-pro'])

    expect(ranked).toEqual(['gemini-3.1-pro'])
  })

  it('先筛 pro 再取最高编号，而不是反过来', () => {
    // 反过来做会先算出「最新系列 = 3.5」，再筛 pro 得到空列表——
    // gemini 整族凭空消失，而这个错不报任何异常。
    const ranked = rankModelPriority([
      'gemini-3.5-flash',
      'gemini-3.5-flash-lite',
      'gemini-3.1-pro',
      'gemini-3.1-pro-preview'
    ])

    expect(ranked).toEqual(['gemini-3.1-pro', 'gemini-3.1-pro-preview'])
  })

  it('pro 之间仍按系列淘汰', () => {
    const ranked = rankModelPriority(['gemini-2.5-pro', 'gemini-3.1-pro'])

    expect(ranked).toEqual(['gemini-3.1-pro'])
  })

  it('一个 pro 都没有时 gemini 整族为空', () => {
    // 不硬留一个 flash 充数：规则说的是「只允许 pro」。
    expect(rankModelPriority(['gemini-3.5-flash'])).toEqual([])
  })
})

describe('`-3-5` 与 `-3.5` 是同一个系列', () => {
  it('两种写法算同一系列，一起保留', () => {
    const ranked = rankModelPriority(['claude-opus-4-6', 'claude-opus-4.6'])

    expect(ranked).toEqual(['claude-opus-4-6', 'claude-opus-4.6'])
  })

  it('连字符写法参与最高系列比较', () => {
    // `claude-opus-5` 的 5 高于 `claude-opus-4-6` 的 4.6。
    const ranked = rankModelPriority(['claude-opus-4-6', 'claude-opus-5'])

    expect(ranked).toEqual(['claude-opus-5'])
  })

  it('gemini 的两种写法同样等价', () => {
    const ranked = rankModelPriority(['gemini-3-1-pro', 'gemini-3.1-pro'])

    expect(ranked).toEqual(['gemini-3-1-pro', 'gemini-3.1-pro'])
  })
})

describe('带供应商前缀的模型名', () => {
  it('前缀不影响家族与系列识别', () => {
    // 真实链里是 `CHMA/claude-opus-5`、`GLE/gemini-3.1-pro` 这种形态。
    const ranked = rankModelPriority([
      'GLE/gemini-3.1-pro',
      'CHMA/claude-opus-5',
      'CHMA/gpt-5-codex',
      'gpt-5.6-sol'
    ])

    expect(ranked).toEqual(['gpt-5.6-sol', 'CHMA/claude-opus-5', 'GLE/gemini-3.1-pro'])
  })

  it('同一模型的带前缀与不带前缀两条都保留', () => {
    // 它们指向不同后端，是故障转移的正常形态，不能去重成一条。
    const ranked = rankModelPriority(['claude-opus-5', 'CHMA/claude-opus-5'])

    expect(ranked).toEqual(['claude-opus-5', 'CHMA/claude-opus-5'])
  })
})

describe('边界输入', () => {
  it('空列表返回空列表', () => {
    expect(rankModelPriority([])).toEqual([])
  })

  it('空串与空白跳过', () => {
    expect(rankModelPriority(['', '   ', 'gpt-5.6-sol'])).toEqual(['gpt-5.6-sol'])
  })

  it('重复项只保留一条', () => {
    expect(rankModelPriority(['gpt-5.6-sol', 'gpt-5.6-sol'])).toEqual(['gpt-5.6-sol'])
  })

  it('没有版本号的模型名不被系列规则淘汰', () => {
    // `claude-fable-5` 有系列；而一个纯名字（无数字）没有可比较的系列，
    // 保留它比丢掉安全——丢掉等于让用户配好的模型凭空消失。
    const ranked = rankModelPriority(['claude-opus-5', 'claude-instant'])

    expect(ranked).toContain('claude-opus-5')
    expect(ranked).toContain('claude-instant')
  })
})

describe('同系列内按智能度排序', () => {
  it('claude：opus > sonnet > fable > haiku', () => {
    // 这四项系列号都是 5，不排档位时先后取决于上游返回顺序——也就是随机，
    // 而链的第一项决定绝大多数请求用哪个模型。
    const ranked = rankModelPriority([
      'claude-haiku-5',
      'claude-fable-5',
      'claude-sonnet-5',
      'claude-opus-5'
    ])

    expect(ranked).toEqual([
      'claude-opus-5',
      'claude-sonnet-5',
      'claude-fable-5',
      'claude-haiku-5'
    ])
  })

  it('gpt：sol > terra > luna', () => {
    const ranked = rankModelPriority(['gpt-5.6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra'])

    expect(ranked).toEqual(['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'])
  })

  it('档位表里没有的排在已知档位之后', () => {
    // 「不认识」不等于「最弱」：新上游随时会出一个没见过的档位名，
    // 把它当最弱会让一个可能更强的模型沉到链尾。但已知档位优先是确定的。
    const ranked = rankModelPriority(['claude-newtier-5', 'claude-opus-5'])

    expect(ranked).toEqual(['claude-opus-5', 'claude-newtier-5'])
  })

  it('档位名必须是独立词段，不匹配子串', () => {
    // 一个含 `lunar` 的模型名不该被当成 gpt 的 luna 档。
    const ranked = rankModelPriority(['gpt-5.6-lunar-probe', 'gpt-5.6-luna'])

    expect(ranked[0]).toBe('gpt-5.6-luna')
  })

  it('档位排序优先于字母序', () => {
    // 字母序会给出 opus < sonnet，恰好与档位一致；haiku < opus 则相反。
    // 这一条确保排的是档位而不是名字。
    const ranked = rankModelPriority(['claude-haiku-5', 'claude-opus-5'])

    expect(ranked).toEqual(['claude-opus-5', 'claude-haiku-5'])
  })
})

describe('带前缀与不带前缀是同一模型的两个路由目标', () => {
  it('同一裸名的各种前缀连续排列', () => {
    // CLIProxyAPI 把前缀 TrimPrefix 掉才发给上游（conductor_models.go:685-698），
    // 所以它们是同一个上游模型；但网关为裸 ID 与带前缀 ID 各注册一条
    // （service_models.go:600-614），因此是两个可独立选择的路由目标。
    const ranked = rankModelPriority([
      'claude-sonnet-5',
      'ANT/claude-opus-5',
      'claude-opus-5',
      'CHMA/claude-opus-5'
    ])

    // opus 三条（含两个前缀）排在 sonnet 之前，且三条连续。
    expect(ranked.slice(0, 3).every((m) => m.endsWith('claude-opus-5'))).toBe(true)
    expect(ranked[3]).toBe('claude-sonnet-5')
  })

  it('绝不去重——去重等于删掉多凭据冗余', () => {
    // 带前缀的把候选收缩到那一条凭据（一套 key + base-url + proxy + headers），
    // 不带前缀的匹配所有凭据由调度器轮询。删掉任何一条都会改变故障转移行为。
    const ranked = rankModelPriority([
      'claude-opus-5',
      'ANT/claude-opus-5',
      'CHMA/claude-opus-5'
    ])

    expect(ranked).toHaveLength(3)
  })

  it('前缀不参与档位判定', () => {
    const ranked = rankModelPriority(['ANT/claude-haiku-5', 'CHMA/claude-opus-5'])

    expect(ranked).toEqual(['CHMA/claude-opus-5', 'ANT/claude-haiku-5'])
  })
})

describe('变体序号与次版本的歧义（真实链上的缺陷）', () => {
  /**
   * `claude-fable-5-1` 与 `claude-opus-4-6` 字面完全同形
   * （`<家族>-<档位>-<数字>-<数字>`），但含义相反：
   * 前者 `-1` 是变体序号（系列仍是 5），后者 `-6` 是次版本（4.6）。
   *
   * 坏版本把两者都读成小数，于是 `fable-5-1` 算出 5.1 盖过 `opus-5` 的 5，
   * **整个 opus 家族被当成旧系列淘汰**。真实链上出现过：19 项里只剩一条
   * `ANT/claude-fable-5-1`，而应该排第一的 `claude-opus-5` 消失了。
   * 这个错不抛异常，只是让最强的模型不参与故障转移。
   */
  it('变体序号不把同族更强的档位挤掉——这一条是修复的核心', () => {
    const ranked = rankModelPriority([
      'ANT/claude-fable-5-1',
      'claude-opus-5',
      'claude-sonnet-5'
    ])

    // 坏版本在这里只返回 `ANT/claude-fable-5-1`。
    expect(ranked).toEqual([
      'claude-opus-5',
      'claude-sonnet-5',
      'ANT/claude-fable-5-1'
    ])
  })

  it('同族存在无歧义的同系列时，连字符后的数字判为变体', () => {
    // `opus-5` 确认了 `5` 是一个系列，因此 `fable-5-1` 的 `-1` 是变体。
    const ranked = rankModelPriority(['claude-fable-5-1', 'claude-opus-5'])

    expect(ranked).toEqual(['claude-opus-5', 'claude-fable-5-1'])
  })

  it('同族没有别的同系列时，连字符仍按次版本读', () => {
    // 只有 `gemini-3-1-pro` 一种写法时 `3` 不是公认系列，
    // 因此读作 3.1——这保住了「`-3-1` 与 `-3.1` 等效」那条规则。
    const ranked = rankModelPriority(['gemini-3-1-pro', 'gemini-2.5-pro'])

    expect(ranked).toEqual(['gemini-3-1-pro'])
  })

  it('真实的次版本仍然淘汰旧系列', () => {
    // `claude-opus-4-6` 里 `4` 不是公认系列（没有别的 `*-4`），
    // 所以读作 4.6，而 5 更高。
    const ranked = rankModelPriority(['claude-opus-4-6', 'claude-opus-5'])

    expect(ranked).toEqual(['claude-opus-5'])
  })

  it('截图里那条 19 项真实链：opus 必须排在 claude 最前', () => {
    const ranked = rankModelPriority([
      'gemini-3.1-pro-preview-customtools', 'CHMA/claude-opus-5', 'gemini-3.5-flash',
      'CHMA/claude-opus-4-6', 'gemini-3.1-pro-low', 'ANT/claude-4.5-haiku',
      'gemini-3.1-pro-preview', 'gpt-5.6-sol', 'claude-sonnet-4-20250514',
      'CHMA/gpt-5-codex', 'ANT/claude-fable-5', 'GLE/gemini-3.1-pro',
      'GLE/gemini-3.1-pro-high', 'claude-opus-5', 'claude-opus-5-thinking',
      'CHMA/gemini-3.1-pro-preview-customtools', 'CHMA/gpt-5.6-sol',
      'ANT/claude-fable-5-1', 'CDX/gpt-5-codex'
    ])

    // gpt 在最前，随后 claude 段必须以 opus 开头。
    expect(ranked[0]).toBe('gpt-5.6-sol')
    const firstClaude = ranked.find((m) => m.includes('claude'))
    expect(firstClaude).toContain('claude-opus-5')
    // 旧系列与非 pro 的 gemini 被淘汰。
    expect(ranked).not.toContain('gemini-3.5-flash')
    expect(ranked).not.toContain('CHMA/claude-opus-4-6')
    expect(ranked).not.toContain('claude-sonnet-4-20250514')
  })
})
