import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createAgentConfiguration,
  listAgents,
  updateAgentConfiguration,
  type AgentConfigurationRequest
} from '../src/api/agent'
import { http } from '../src/utils/http'

const configuration: AgentConfigurationRequest = {
  agent_id: 'office-research',
  display_name: 'Office Research',
  enabled: true,
  workflow_id: null,
  model_priority: ['primary', 'backup'],
  provider_allowlist: ['openai'],
  capabilities: ['research', 'code'],
  prompt_bindings: [
    { resource_id: 'prompt.office-research', resource_type: 'prompt', version_policy: 'current', enabled: true }
  ],
  skill_bindings: [],
  memory_bindings: [],
  mcp_bindings: [
    {
      resource_id: 'mcp.context7',
      resource_type: 'mcp',
      version_policy: 'fixed',
      version: '1.0.0',
      enabled: true
    }
  ],
  hook_bindings: [],
  mcp_allowlist: ['mcp.context7:query-docs'],
  allow_tools: true,
  max_tool_iterations: 4,
  relations: {
    channels: ['webui', 'telegram', 'wecom'],
    accounts: [{ channel_type: 'telegram', adapter_instance: 'main', account_scope: 'default' }],
    sessions: ['webui/webui/main/c2c:research-1/research-1'],
    is_default: true
  }
}

describe('agent API client', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => null) })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('lists agents through the dedicated agent endpoint', async () => {
    const request = vi.spyOn(http, 'get').mockResolvedValue([])

    await listAgents()

    expect(request).toHaveBeenCalledWith('/agents')
  })

  it('creates and updates complete configuration snapshots without dropping bindings', async () => {
    const post = vi.spyOn(http, 'post').mockResolvedValue({})
    const put = vi.spyOn(http, 'put').mockResolvedValue({})

    await createAgentConfiguration(configuration)
    await updateAgentConfiguration('office-research', configuration)

    expect(post).toHaveBeenCalledWith('/agents/configuration', configuration)
    expect(put).toHaveBeenCalledWith('/agents/office-research/configuration', configuration)
  })
})
