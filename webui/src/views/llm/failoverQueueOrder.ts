/**
 * 故障转移队列的次序计算。
 *
 * 抽成纯函数，是因为「上移一位」的正确性**是**这个功能，而它此前只被源码 grep
 * 覆盖：`llm-failover-queue-reorder.test.ts` 断言
 * `toMatch(/queue-move-up[\s\S]{0,300}index === 0/)`——那检查的是模板里
 * 有没有那个禁用条件，不检查交换出来的次序对不对。
 *
 * 算错的后果不是界面错位：这个次序会提交给后端并写进 `config.yaml`，
 * 决定真实请求先打哪一家。交换错一位，故障转移就按一个用户没选的顺序跑，
 * 而界面显示的是他选的那个。
 */

/**
 * 把 `index` 处的元素与它的邻居交换，返回新数组。
 *
 * 越界时返回 `null` 而不是原数组：调用方据此**不发请求**。
 * 返回原数组会让「点了最上面那一行的上移」提交一次内容相同的写操作，
 * 在 `config.yaml` 上留下一条无意义的备份。
 *
 * 不原地修改：`rows` 是响应式数据，原地交换会让界面在请求还没回来时就跳动，
 * 而这个页面唯一的论断就是「当前生效的次序」。
 */
export function swappedOrder<T>(
  items: readonly T[],
  index: number,
  offset: -1 | 1
): T[] | null {
  const target = index + offset
  if (index < 0 || index >= items.length) return null
  if (target < 0 || target >= items.length) return null
  const next = [...items]
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

/** 这一行的上移按钮该不该禁用。 */
export function canMoveUp(index: number, total: number): boolean {
  return total > 1 && index > 0
}

/** 这一行的下移按钮该不该禁用。 */
export function canMoveDown(index: number, total: number): boolean {
  return total > 1 && index < total - 1
}
