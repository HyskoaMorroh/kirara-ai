import { describe, expect, it } from 'vitest'
import { createUniqueNodeName } from '../src/components/workflow/workflow-node-utils'

describe('createUniqueNodeName', () => {
  it('uses the block type suffix and increments an occupied name', () => {
    expect(createUniqueNodeName('internal:chat', ['chat', 'chat_1'])).toBe('chat_2')
  })

  it('falls back to a safe generic name when the block type has no suffix', () => {
    expect(createUniqueNodeName('', ['node'])).toBe('node_1')
  })
})
