import { describe, expect, it } from 'vitest'
import { isProxy, reactive } from 'vue'

import { cloneDispatchRule } from '../src/views/workflow/dispatch-rule-utils'

describe('cloneDispatchRule', () => {
  it('keeps an edit draft from mutating the rule shown in the list', () => {
    const source = {
      rule_id: 'chat_normal',
      name: '聊天',
      description: '',
      workflow_id: 'chat:normal',
      priority: 30,
      enabled: true,
      rule_groups: [{ operator: 'or' as const, rules: [{ type: 'prefix', config: { prefix: '/chat' } }] }],
      metadata: { source: 'preset' }
    }

    const draft = cloneDispatchRule(source)
    draft.rule_groups[0].rules[0].config.prefix = '/other'
    draft.metadata.source = 'custom'

    expect(source.rule_groups[0].rules[0].config.prefix).toBe('/chat')
    expect(source.metadata.source).toBe('preset')
  })

  it('delegates to the shared deep clone, so a reactive draft is fully unwrapped', () => {
    // 编辑草稿本身是 reactive 的：克隆结果必须是纯对象，否则把它 POST 给
    // /dispatch/reachability 时会带上代理层。
    const source = reactive({
      rule_id: 'chat_normal',
      rule_groups: [
        { operator: 'or' as const, rules: [{ type: 'prefix', config: { prefix: '/chat' } }] }
      ],
      metadata: {} as Record<string, unknown>
    })

    const draft = cloneDispatchRule(source)

    expect(isProxy(draft)).toBe(false)
    expect(isProxy(draft.rule_groups[0])).toBe(false)
    draft.rule_groups[0].rules[0].config.prefix = '/other'
    expect(source.rule_groups[0].rules[0].config.prefix).toBe('/chat')
  })
})
