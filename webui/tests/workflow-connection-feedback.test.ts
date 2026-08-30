// 需求 20.4：连线被拒时必须说清楚是哪一种拒绝。
//
// `validateWorkflowConnection` 会区分六种 reason，但画布只取 `.valid`，
// 一律提示「类型不兼容，无法连接」。端口不存在、端点缺失、真正的类型不兼容
// 被同一句话覆盖，用户既无法区分也不知道该改哪一侧。
//
// 更严重的是：两个节点组件都把 `isValidConnection` 传给了 Handle，
// 而 vue-flow 在 Handle 判定为 invalid 时**不会**触发 `onConnect`——
// 于是 `handleConnect` 里那句 message.error 根本不可达，
// 所有拒绝都是静默的，用户看到的只是「线拉过去又弹回来」。
//
// 「同一输入只允许一条边」这条规则同样静默，且它与类型不兼容的现象完全一样，
// 用户无从判断该删掉那条已有的线还是该换端口。

import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import {
  connectionRejectionMessage,
  findWorkflowConnectionRejection,
  type WorkflowConnectionRejectionReason
} from '../src/components/workflow/workflow-data'

const ALL_REASONS: WorkflowConnectionRejectionReason[] = [
  'missing_endpoint',
  'missing_source_block',
  'missing_target_block',
  'unknown_source_port',
  'unknown_target_port',
  'incompatible_types',
  'input_already_connected'
]

describe('connectionRejectionMessage', () => {
  it('gives every reason its own message', () => {
    const messages = ALL_REASONS.map((reason) => connectionRejectionMessage(reason))
    // 每种拒绝都要有独立文案：共用一句话等于没有说明。
    expect(new Set(messages).size).toBe(ALL_REASONS.length)
    for (const text of messages) {
      expect(text.length).toBeGreaterThan(0)
    }
  })

  it('tells the user which side to change when the types do not match', () => {
    const text = connectionRejectionMessage('incompatible_types')
    expect(text).toContain('类型')
  })

  it('says an input is already taken instead of blaming the type', () => {
    const text = connectionRejectionMessage('input_already_connected')
    // 这条最容易被误读成类型问题：必须明确指向「已有连线」。
    expect(text).toContain('已有')
    expect(text).not.toContain('类型不兼容')
  })

  it('distinguishes a missing port from a mismatched type', () => {
    expect(connectionRejectionMessage('unknown_source_port')).not.toBe(
      connectionRejectionMessage('incompatible_types')
    )
    expect(connectionRejectionMessage('unknown_target_port')).toContain('端口')
  })

  it('falls back to a generic message for an unrecognized reason', () => {
    // 将来新增 reason 时不能让界面出现空白提示。
    const text = connectionRejectionMessage('something_new' as WorkflowConnectionRejectionReason)
    expect(text.length).toBeGreaterThan(0)
  })
})

describe('findWorkflowConnectionRejection', () => {
  const connection = {
    source: 'a',
    sourceHandle: 'out',
    target: 'b',
    targetHandle: 'in'
  }

  it('reports the already-connected input before anything else', () => {
    const reason = findWorkflowConnectionRejection(connection, {
      inputAlreadyConnected: true
    })
    expect(reason).toBe('input_already_connected')
  })

  it('returns null when nothing rejects the connection', () => {
    const reason = findWorkflowConnectionRejection(connection, {
      inputAlreadyConnected: false
    })
    expect(reason).toBeNull()
  })

  it('passes through the validation reason when there is one', () => {
    const reason = findWorkflowConnectionRejection(connection, {
      inputAlreadyConnected: false,
      validation: { valid: false, reason: 'unknown_target_port' }
    })
    expect(reason).toBe('unknown_target_port')
  })

  it('does not invent a rejection from a successful validation', () => {
    const reason = findWorkflowConnectionRejection(connection, {
      inputAlreadyConnected: false,
      validation: { valid: true }
    })
    expect(reason).toBeNull()
  })
})

// 下面几组是源码级断言。纯函数正确不代表画布真的会提示：
// 这个缺陷的形态恰恰是「判定对了但没人把结果说出来」，而那一跳只能在接线处校验。
describe('canvas wiring', () => {
  const canvas = readFileSync(
    new URL('../src/components/workflow/WorkflowCanvas.vue', import.meta.url),
    'utf-8'
  )
  const codeNode = readFileSync(
    new URL('../src/components/workflow/nodes/CodeNode.vue', import.meta.url),
    'utf-8'
  )
  const customNode = readFileSync(
    new URL('../src/components/workflow/nodes/CustomNode.vue', import.meta.url),
    'utf-8'
  )

  it('reports the reason when the drag ends without a connection', () => {
    // 提示必须挂在 connect-end 上：Handle 判定 invalid 时 onConnect 不触发，
    // 挂在 onConnect 上的提示永远不会执行。
    expect(canvas).toContain('@connect-start="handleConnectStart"')
    expect(canvas).toContain('@connect-end="handleConnectEnd"')
    expect(canvas).toContain('connectionRejectionMessage(lastConnectionRejection)')
  })

  it('does not warn when the connection actually succeeded', () => {
    expect(canvas).toContain('connectionEstablished = true')
    expect(canvas).toContain('if (connectionEstablished || !lastConnectionRejection)')
  })

  it('leaves the single-input rule to the canvas so it can be explained', () => {
    // 两个节点组件都不得在这条规则上提前 return false：
    // 那样画布拿不到原因，这条拒绝就退回静默。
    for (const source of [codeNode, customNode]) {
      expect(source).not.toContain('getHandleConnections')
      expect(source).not.toContain('incomers')
    }
  })

  it('checks the already-connected input inside the canvas validator', () => {
    expect(canvas).toContain('const alreadyConnected = inputAlreadyConnected(connection)')
    expect(canvas).toContain('inputAlreadyConnected: alreadyConnected')
    // 已占用的输入不得进校验缓存：删掉那条已有的线之后同一端口必须重新可连。
    expect(canvas).toContain('if (alreadyConnected) return false')
  })

  it('no longer collapses every rejection into one type message', () => {
    // 三处旧文案全部替换：它们是这个缺陷在界面上的样子。
    expect(canvas).not.toContain("message.error('类型不兼容，无法连接')")
  })
})
