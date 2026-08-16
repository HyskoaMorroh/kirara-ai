export type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

/** 在隐私模式或受限嵌入环境中安全读取浏览器存储。 */
export const readStorageItem = (storage: StorageLike | null | undefined, key: string) => {
  try {
    return storage?.getItem(key) ?? null
  } catch {
    return null
  }
}

/** 存储不可用时保持当前会话状态，不阻断页面功能。 */
export const writeStorageItem = (
  storage: StorageLike | null | undefined,
  key: string,
  value: string
) => {
  try {
    storage?.setItem(key, value)
  } catch {
    // 浏览器拒绝存储时无需向用户抛出异常。
  }
}

/** 读取对象形状的 JSON；损坏、数组或其他标量都安全退回为空对象。 */
export const readJsonRecord = (
  storage: StorageLike | null | undefined,
  key: string
): Record<string, boolean> => {
  const value = readStorageItem(storage, key)
  if (!value) return {}

  try {
    const parsed: unknown = JSON.parse(value)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, boolean>
    }
  } catch {
    // 损坏的旧值不应阻断引导页渲染。
  }
  return {}
}

/** localStorage 本身的 getter 也可能抛出（例如严格的隐私策略）。 */
export const getBrowserLocalStorage = (): StorageLike | undefined => {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}
