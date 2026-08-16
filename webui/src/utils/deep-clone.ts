import { isRef, toRaw, unref } from 'vue'

/**
 * 统一的深拷贝实现。
 *
 * 项目里原本有两份各自残缺的深拷贝：规则编辑器用 `JSON.parse(JSON.stringify())`
 * （丢 Date / Map / Set，遇环直接抛错），工作流历史用 `structuredClone` 并在失败时
 * 回退到 JSON。两者语义不同，同一份数据在不同入口会得到不同结果。这里收敛成
 * 唯一实现，能力取两者的并集：
 *
 * - 嵌套对象与数组；
 * - `Date` / `RegExp` / `Map` / `Set` 保持类型（JSON 版本会把它们压成 `{}` 或字符串）；
 * - `null` / `undefined` / 原始值原样返回；
 * - 循环引用（JSON 版本会抛 `Converting circular structure to JSON`）；
 * - Vue 响应式数据：每一层都先 `toRaw`，`ref` 按 `reactive` 的读取语义解包成裸值。
 *
 * 为什么不直接用 `structuredClone`：它对 Vue 的响应式代理并不安全——顶层
 * `toRaw` 只剥掉一层，嵌套层仍是 Proxy；代理上的 getter、`__v_skip` 之类的扩展字段
 * 以及函数属性都会让它抛 `DataCloneError`，而 Pinia/自建 store 的 state 正是这种结构。
 * 手写递归可以在每一层都 `toRaw`，因此不存在这个陷阱。
 */
export const deepClone = <T>(value: T): T => deepCloneInternal(value, new WeakMap())

const deepCloneInternal = <T>(value: T, seen: WeakMap<object, unknown>): T => {
  // 先剥掉响应式代理：嵌套层同样需要，否则克隆结果里会残留 Proxy。
  const raw = toRaw(value)

  // `toRaw` 不会解包 ref，而透过 reactive 代理读取时 ref 是自动解包的。
  // 这里跟随代理的读取语义拷贝裸值，避免克隆结果里出现 RefImpl 内部字段。
  if (isRef(raw)) {
    return deepCloneInternal(unref(raw), seen) as T
  }

  if (raw === null || typeof raw !== 'object') {
    // 原始值、null、undefined、函数（函数按引用保留，与 structuredClone 抛错相比更宽容）
    return raw as T
  }

  const cached = seen.get(raw as object)
  if (cached !== undefined) return cached as T

  if (raw instanceof Date) {
    return new Date(raw.getTime()) as T
  }

  if (raw instanceof RegExp) {
    const cloned = new RegExp(raw.source, raw.flags)
    cloned.lastIndex = raw.lastIndex
    return cloned as T
  }

  if (Array.isArray(raw)) {
    const cloned: unknown[] = []
    seen.set(raw as object, cloned)
    for (const item of raw) {
      cloned.push(deepCloneInternal(item, seen))
    }
    return cloned as T
  }

  if (raw instanceof Map) {
    const cloned = new Map()
    seen.set(raw as object, cloned)
    raw.forEach((mapValue, mapKey) => {
      cloned.set(deepCloneInternal(mapKey, seen), deepCloneInternal(mapValue, seen))
    })
    return cloned as T
  }

  if (raw instanceof Set) {
    const cloned = new Set()
    seen.set(raw as object, cloned)
    raw.forEach((setValue) => {
      cloned.add(deepCloneInternal(setValue, seen))
    })
    return cloned as T
  }

  // 普通对象：保留原型，避免把 class 实例降级成裸对象。
  const cloned = Object.create(Object.getPrototypeOf(raw)) as Record<string | symbol, unknown>
  seen.set(raw as object, cloned)
  for (const key of Reflect.ownKeys(raw as object)) {
    cloned[key as string] = deepCloneInternal(
      (raw as Record<string | symbol, unknown>)[key as string],
      seen
    )
  }
  return cloned as T
}
