import { isRef, toRaw, unref } from 'vue'

/**
 * 统一的深拷贝实现。
 *
 * 项目里原本有两份各自残缺的深拷贝：规则编辑器用 `JSON.parse(JSON.stringify())`
 * （丢 Date / Map / Set，遇环直接抛错），工作流历史用 `structuredClone` 并在失败时
 * 回退到 JSON。两者语义不同，同一份数据在不同入口会得到不同结果。这里收敛成
 * 唯一实现。
 *
 * 明确支持：
 *
 * - 嵌套对象与数组；
 * - `Date` / `RegExp` / `Map` / `Set` 保持类型（JSON 版本会把它们压成 `{}` 或字符串）；
 * - `ArrayBuffer` / `DataView` / 各类 TypedArray（按各自构造器复制底层字节）；
 * - `null` / `undefined` / 原始值原样返回；
 * - 循环引用（JSON 版本会抛 `Converting circular structure to JSON`）；
 * - Vue 响应式数据：每一层都先 `toRaw`，`ref` 按 `reactive` 的读取语义解包成裸值；
 * - 函数按引用保留（`structuredClone` 会直接抛 `DataCloneError`）。
 *
 * 明确不支持（这里不是 `structuredClone` 的超集，请勿据此假设）：
 *
 * - `WeakMap` / `WeakSet`：其内容本质上不可枚举，因此**按引用返回**，不是副本；
 * - 带私有字段（`#field`）或依赖内部插槽的 class 实例：拷贝会保留原型，但私有
 *   字段不会被复制，首次访问就会抛错。普通字段的 class 实例可以正常拷贝；
 * - `Blob` / `File` / `ImageData` 等宿主对象：走普通对象分支，结果不可用；
 * - `Symbol` 作为值时按引用返回（与 `structuredClone` 抛错不同）。
 *
 * 深度上限见 {@link MAX_CLONE_DEPTH}：超限会抛一条说明清楚的 `RangeError`，
 * 而不是让引擎在某个不确定的层数上抛匿名的栈溢出。
 *
 * 为什么不直接用 `structuredClone`：它对 Vue 的响应式代理并不安全——顶层
 * `toRaw` 只剥掉一层，嵌套层仍是 Proxy；代理上的 getter、`__v_skip` 之类的扩展字段
 * 以及函数属性都会让它抛 `DataCloneError`，而 Pinia/自建 store 的 state 正是这种结构。
 * 手写递归可以在每一层都 `toRaw`，因此不存在这个陷阱。
 */
export const deepClone = <T>(value: T): T => deepCloneInternal(value, new WeakMap(), 0)

/**
 * 递归深度上限。
 *
 * 手写递归吃的是 JS 调用栈，超过约两万层就会抛没有任何上下文的 `RangeError:
 * Maximum call stack size exceeded`，排查时完全看不出是深拷贝的问题。这里主动
 * 设一个远低于栈上限的阈值并抛出可读的错误：工作流配置、调度规则这类真实数据
 * 的嵌套深度都在两位数，触发它基本只有两种情况——数据结构异常，或是我们漏掉了
 * 某种自引用形式。
 */
export const MAX_CLONE_DEPTH = 512

const deepCloneInternal = <T>(value: T, seen: WeakMap<object, unknown>, depth: number): T => {
  if (depth > MAX_CLONE_DEPTH) {
    throw new RangeError(
      `deepClone: 嵌套层数超过上限 ${MAX_CLONE_DEPTH}，请检查数据结构是否异常。`
    )
  }

  // 先剥掉响应式代理：嵌套层同样需要，否则克隆结果里会残留 Proxy。
  const raw = toRaw(value)

  // `toRaw` 不会解包 ref，而透过 reactive 代理读取时 ref 是自动解包的。
  // 这里跟随代理的读取语义拷贝裸值，避免克隆结果里出现 RefImpl 内部字段。
  if (isRef(raw)) {
    return deepCloneInternal(unref(raw), seen, depth + 1) as T
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

  // 二进制数据：这些类型的内容存在内部插槽里，`Reflect.ownKeys` 看不到，
  // 走下面的普通对象分支只会得到一个原型空壳，首次读取就抛 TypeError。
  if (raw instanceof ArrayBuffer) {
    return raw.slice(0) as T
  }

  if (typeof SharedArrayBuffer !== 'undefined' && raw instanceof SharedArrayBuffer) {
    // 共享内存的语义就是共享，复制反而会破坏调用方预期，因此按引用返回。
    return raw as T
  }

  if (raw instanceof DataView) {
    return new DataView(
      raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength)
    ) as T
  }

  if (ArrayBuffer.isView(raw)) {
    // TypedArray：`slice()` 返回同类型的新实例（Uint8Array 不会退化成 Int8Array），
    // 底层 buffer 也是新的，因此不与原数据共享内存。
    return (raw as unknown as Uint8Array).slice() as T
  }

  // WeakMap / WeakSet 的成员不可枚举，从语言层面就无法复制内容。
  // 按引用返回（structuredClone 在这里直接抛错），并在文档里写明这不是副本。
  if (raw instanceof WeakMap || raw instanceof WeakSet) {
    return raw as T
  }

  if (Array.isArray(raw)) {
    const cloned: unknown[] = []
    seen.set(raw as object, cloned)
    for (const item of raw) {
      cloned.push(deepCloneInternal(item, seen, depth + 1))
    }
    return cloned as T
  }

  if (raw instanceof Map) {
    const cloned = new Map()
    seen.set(raw as object, cloned)
    raw.forEach((mapValue, mapKey) => {
      cloned.set(
        deepCloneInternal(mapKey, seen, depth + 1),
        deepCloneInternal(mapValue, seen, depth + 1)
      )
    })
    return cloned as T
  }

  if (raw instanceof Set) {
    const cloned = new Set()
    seen.set(raw as object, cloned)
    raw.forEach((setValue) => {
      cloned.add(deepCloneInternal(setValue, seen, depth + 1))
    })
    return cloned as T
  }

  // 普通对象：保留原型，避免把 class 实例降级成裸对象。
  // 注意带私有字段的 class 实例只会拿到原型，私有字段不会被复制（见顶部文档）。
  const cloned = Object.create(Object.getPrototypeOf(raw)) as Record<string | symbol, unknown>
  seen.set(raw as object, cloned)
  for (const key of Reflect.ownKeys(raw as object)) {
    cloned[key as string] = deepCloneInternal(
      (raw as Record<string | symbol, unknown>)[key as string],
      seen,
      depth + 1
    )
  }
  return cloned as T
}
