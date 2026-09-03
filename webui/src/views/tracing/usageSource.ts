/**
 * 用量来源的显示文案。**一份**，两处共用。
 *
 * 此前这张表在两个文件里各有一份（`llm-tracing.vm.ts` 与 `LLMStatistics.vue`），
 * 而 `usage-source-partial.test.ts` 分别 grep 各自那份——两份漂移时测试全绿，
 * 因为每一份自己都「包含那个字符串」。请求日志说「供应商部分回报」而统计图
 * 说别的，是这种重复唯一会显形的方式，而它显形在用户眼里而不是测试里。
 *
 * `provider_partial` 与 `provider` 必须是两个词，这是需求 22.1 的实质：
 * 前者的总额是**补出来的**（上游没回报缓存维度，缺失维度按 0 计价），
 * 而缓存读取单价通常只有输入 Token 的 1/5 到 1/10、缓存写入往往更贵。
 * 显示成同一个词时，一份系统性偏低的账单看起来与完全可信的账单毫无区别。
 *
 * 后端枚举见 `kirara_ai/llm/format/response.py::UsageSource`。
 */
export const USAGE_SOURCE_LABELS: Record<string, string> = {
  provider: '供应商返回',
  provider_partial: '供应商部分回报',
  estimated: '本地估算',
  unknown: '未知'
}

/**
 * 把后端的来源枚举值翻成中文。
 *
 * 查不到时**回落到原始值**而不是空串：后端将来新增一个成员时，
 * 界面显示那个原始字符串仍然可读，而空白读起来像「这一行没有数据」。
 */
export function usageSourceLabel(value: string | null | undefined): string {
  const key = String(value ?? '')
  return USAGE_SOURCE_LABELS[key] || key
}
