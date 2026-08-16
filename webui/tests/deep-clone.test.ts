import { describe, expect, it } from 'vitest'
import { computed, reactive, ref, isProxy } from 'vue'

import { deepClone } from '../src/utils/deep-clone'

describe('deepClone', () => {
  it('copies nested objects and arrays without sharing references', () => {
    const source = { a: { b: [{ c: 1 }] }, list: [1, [2, [3]]] }

    const cloned = deepClone(source)
    cloned.a.b[0].c = 2
    ;(cloned.list[1] as number[])[0] = 9

    expect(source.a.b[0].c).toBe(1)
    expect((source.list[1] as number[])[0]).toBe(2)
    expect(cloned).not.toBe(source)
    expect(cloned.a).not.toBe(source.a)
  })

  it('preserves Date, RegExp, Map and Set instead of flattening them like JSON cloning', () => {
    const source = {
      when: new Date('2024-01-02T03:04:05.000Z'),
      pattern: /ab+c/gi,
      byId: new Map([['a', { n: 1 }]]),
      tags: new Set(['x', 'y'])
    }

    const cloned = deepClone(source)

    expect(cloned.when).toBeInstanceOf(Date)
    expect(cloned.when.getTime()).toBe(source.when.getTime())
    expect(cloned.when).not.toBe(source.when)
    expect(cloned.pattern).toBeInstanceOf(RegExp)
    expect(cloned.pattern.source).toBe('ab+c')
    expect(cloned.pattern.flags).toBe(source.pattern.flags)
    expect(cloned.byId).toBeInstanceOf(Map)
    expect(cloned.byId.get('a')).toEqual({ n: 1 })
    expect(cloned.byId.get('a')).not.toBe(source.byId.get('a'))
    expect(cloned.tags).toBeInstanceOf(Set)
    expect([...cloned.tags]).toEqual(['x', 'y'])
  })

  it('returns primitives, null and undefined unchanged', () => {
    expect(deepClone(null)).toBeNull()
    expect(deepClone(undefined)).toBeUndefined()
    expect(deepClone(42)).toBe(42)
    expect(deepClone('text')).toBe('text')
    expect(deepClone(false)).toBe(false)
    expect(deepClone({ maybe: undefined, nothing: null })).toEqual({
      maybe: undefined,
      nothing: null
    })
  })

  it('survives circular references that JSON cloning cannot express', () => {
    const source: Record<string, any> = { name: 'root' }
    source.self = source
    source.children = [source]

    const cloned = deepClone(source)

    expect(cloned).not.toBe(source)
    expect(cloned.self).toBe(cloned)
    expect(cloned.children[0]).toBe(cloned)
  })

  it('unwraps Vue reactive proxies at every level so structuredClone pitfalls cannot bite', () => {
    // 关键回归：structuredClone 只在顶层 toRaw 时，嵌套层仍是 Proxy，
    // 带 getter / 函数字段的 store state 会抛 DataCloneError。
    const state = reactive({
      blocks: [{ config: { nested: { text: 'before' } } }],
      meta: { tags: new Set(['a']) }
    })

    const cloned = deepClone(state)

    expect(isProxy(cloned)).toBe(false)
    expect(isProxy(cloned.blocks[0])).toBe(false)
    expect(isProxy(cloned.blocks[0].config.nested)).toBe(false)
    expect(cloned.blocks[0].config.nested.text).toBe('before')

    state.blocks[0].config.nested.text = 'after'
    expect(cloned.blocks[0].config.nested.text).toBe('before')
  })

  it('clones reactive state that structuredClone would reject outright', () => {
    // 含函数与 computed getter 的响应式 state：structuredClone 直接抛错，
    // 共享的深拷贝必须仍能产出隔离副本。
    const state = reactive({
      value: { count: 1 },
      handler: () => 'noop',
      derived: computed(() => 'derived'),
      cursor: ref(3)
    })

    expect(() => structuredClone(state)).toThrow()

    const cloned = deepClone(state)
    cloned.value.count = 2

    expect(state.value.count).toBe(1)
    expect(typeof cloned.handler).toBe('function')
    // reactive 会自动解包 ref，深拷贝沿用同一语义。
    expect(cloned.cursor).toBe(3)
  })
})
