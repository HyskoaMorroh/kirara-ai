import { describe, expect, it } from 'vitest'
import { computed, reactive, ref, isProxy } from 'vue'

import { MAX_CLONE_DEPTH, deepClone } from '../src/utils/deep-clone'

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

  it('copies ArrayBuffer, DataView and TypedArrays instead of throwing on internal slots', () => {
    // 回归：这些类型的内容在内部插槽里，Reflect.ownKeys 看不到；
    // 旧实现走普通对象分支只能得到原型空壳，首次读取就抛 TypeError。
    const buffer = new ArrayBuffer(8)
    new Uint8Array(buffer).set([1, 2, 3, 4, 5, 6, 7, 8])
    const source = {
      buffer,
      view: new DataView(buffer, 2, 4),
      bytes: new Uint8Array([9, 8, 7]),
      floats: new Float64Array([1.5, 2.5])
    }

    const cloned = deepClone(source)

    expect(cloned.buffer).toBeInstanceOf(ArrayBuffer)
    expect(cloned.buffer).not.toBe(source.buffer)
    expect([...new Uint8Array(cloned.buffer)]).toEqual([1, 2, 3, 4, 5, 6, 7, 8])

    expect(cloned.view).toBeInstanceOf(DataView)
    expect(cloned.view).not.toBe(source.view)
    expect(cloned.view.byteLength).toBe(4)
    expect(cloned.view.getUint8(0)).toBe(3)

    // 具体类型不能退化
    expect(cloned.bytes).toBeInstanceOf(Uint8Array)
    expect([...cloned.bytes]).toEqual([9, 8, 7])
    expect(cloned.floats).toBeInstanceOf(Float64Array)
    expect([...cloned.floats]).toEqual([1.5, 2.5])

    // 底层内存必须是新的，改副本不影响原数据
    cloned.bytes[0] = 0
    expect(source.bytes[0]).toBe(9)
  })

  it('returns WeakMap and WeakSet by reference because their contents are unenumerable', () => {
    // 文档里写明的限制：这两类无法复制内容，因此按引用返回而不是抛错或给空壳。
    const key = { id: 1 }
    const weakMap = new WeakMap([[key, 'value']])
    const weakSet = new WeakSet([key])

    const cloned = deepClone({ weakMap, weakSet })

    expect(cloned.weakMap).toBe(weakMap)
    expect(cloned.weakSet).toBe(weakSet)
    expect(cloned.weakMap.get(key)).toBe('value')
  })

  it('clones class instances with ordinary fields while keeping the prototype', () => {
    class Node {
      constructor(
        public label: string,
        public child: Node | null = null
      ) {}

      describe() {
        return `node:${this.label}`
      }
    }
    const source = new Node('root', new Node('leaf'))

    const cloned = deepClone(source)

    expect(cloned).toBeInstanceOf(Node)
    expect(cloned).not.toBe(source)
    expect(cloned.describe()).toBe('node:root')
    expect(cloned.child).toBeInstanceOf(Node)
    expect(cloned.child).not.toBe(source.child)
  })

  it('throws a readable RangeError instead of a bare stack overflow past the depth limit', () => {
    let deepest: Record<string, unknown> = {}
    const root: Record<string, unknown> = deepest
    for (let index = 0; index < MAX_CLONE_DEPTH + 5; index += 1) {
      const next: Record<string, unknown> = {}
      deepest.next = next
      deepest = next
    }

    expect(() => deepClone(root)).toThrow(RangeError)
    expect(() => deepClone(root)).toThrow(/嵌套层数超过上限/)
  })

  it('stays well within the depth limit for realistically shaped config data', () => {
    let nested: Record<string, unknown> = { leaf: true }
    for (let index = 0; index < 50; index += 1) {
      nested = { level: index, nested }
    }

    expect(() => deepClone(nested)).not.toThrow()
  })
})
