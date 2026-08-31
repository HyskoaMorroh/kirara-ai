<script setup lang="ts">
import { ref, defineProps, defineEmits } from 'vue'
import {
  NForm,
  NFormItem,
  NSwitch,
  NInput,
  NInputNumber,
  NSelect,
  NCard,
  NButton,
  NSpace,
  NScrollbar,
  NSpin,
  NIcon,
  NTooltip,
  NCollapse,
  NCollapseItem,
  NAlert,
  NGrid,
  NGi
} from 'naive-ui'
import { AddOutline as AddIcon, RefreshOutline as RefreshIcon } from '@vicons/ionicons5'
import type { FormItemRule, FormInst } from 'naive-ui'
import type { LLMBackend, ConfigSchema } from '@/api/llm'
import { resilienceDefaults } from '@/api/llm'
import ModelListForm from '@/components/form/ModelListForm.vue'
import DynamicConfigForm from '@/components/form/DynamicConfigForm.vue'
import type { ModelInfo } from '@/components/form/types'
import { deepClone } from '@/utils/deep-clone'

const props = defineProps<{
  adapter: LLMBackend | null
  adapterTypes: string[]
  configSchema: ConfigSchema | null
  loading: boolean
  isCreating: boolean
  isAutoDetectModelsSupported: boolean
  modelAbilities: Record<string, { label: string; value: number }[]>
}>()

const emit = defineEmits<{
  (e: 'save'): void
  (e: 'delete'): void
  (e: 'add-model'): void
  (e: 'edit-model', index: number, model: ModelInfo): void
  (e: 'auto-detect-models'): void
  (e: 'update:adapter', adapter: LLMBackend): void
}>()

const formRef = ref<FormInst | null>(null)
const dynamicConfigForm = ref<InstanceType<typeof DynamicConfigForm> | null>(null)

const adapterRules = {
  name: [
    {
      required: true,
      message: '请输入配置名称',
      trigger: 'blur'
    },
    {
      required: true,
      validator: (rule: FormItemRule, value: string) => {
        return Promise.resolve()
      },
      trigger: 'blur'
    }
  ],
  adapter: {
    required: true,
    message: '请选择接口类型',
    trigger: 'blur'
  }
}

const validateForm = async (): Promise<boolean> => {
  try {
    await formRef.value?.validate()
    const isValid = await dynamicConfigForm.value?.validateForm()
    return !!isValid
  } catch (error) {
    return false
  }
}

const handleSave = async () => {
  if (await validateForm()) {
    emit('save')
  }
}

const handleDelete = () => {
  emit('delete')
}

const handleAddModel = () => {
  emit('add-model')
}

const handleEditModel = (index: number, model: ModelInfo) => {
  emit('edit-model', index, model)
}

const handleAutoDetectModels = () => {
  emit('auto-detect-models')
}

const updateAdapter = (update: (adapter: LLMBackend) => void) => {
  if (!props.adapter) return

  const adapter = deepClone(props.adapter)
  update(adapter)
  emit('update:adapter', adapter)
}

/**
 * 容错预算字段表。
 *
 * 与 `kirara_ai/config/global_config.py` 的 `LLMBackendConfig` 一一对应，分组与
 * 取值范围沿用后端校验；帮助文案说明「这个值不填会怎样」而不是复述字段名。
 * 分成三组是因为这三类参数的调整场合完全不同：重试关心瞬态失败，超时关心
 * 单次请求的等待上限，熔断关心一个 Provider 何时被整体跳过。
 */
type ResilienceField = {
  key: keyof LLMBackend
  label: string
  min: number
  max: number
  step: number
  help: string
}

const queueFields: ResilienceField[] = [
  {
    key: 'priority',
    label: '队列优先级',
    min: 0,
    max: 10000,
    step: 1,
    help: '数字越小越先被使用；同一模型下多个供应商按此升序组成故障转移队列'
  },
  {
    key: 'max_retries',
    label: '最大重试次数',
    min: 0,
    max: 10,
    step: 1,
    help: '同一供应商内部的重试次数（0-10）；只对网络、超时、限流和上游 5xx 生效'
  },
  {
    key: 'retry_backoff_seconds',
    label: '重试初始退避（秒）',
    min: 0,
    max: 600,
    step: 0.5,
    help: '第一次重试前的等待秒数，之后按 2 倍增长'
  },
  {
    key: 'retry_backoff_max_seconds',
    label: '重试退避上限（秒）',
    min: 0,
    max: 600,
    step: 0.5,
    help: '退避的封顶值，防止指数增长把单次请求拖到不可接受的时长'
  }
]

const timeoutFields: ResilienceField[] = [
  {
    key: 'stream_first_byte_timeout_seconds',
    label: '流式首字节超时（秒）',
    min: 1,
    max: 600,
    step: 1,
    help: '等待首个数据块的最长时间；超时会切换到队列中的下一个供应商'
  },
  {
    key: 'stream_idle_timeout_seconds',
    label: '流式静默超时（秒）',
    min: 1,
    max: 1200,
    step: 1,
    help: '两个数据块之间的最长间隔，用于识别中途卡住的流'
  },
  {
    key: 'stream_total_timeout_seconds',
    label: '流式总超时（秒）',
    min: 1,
    max: 3600,
    step: 10,
    help: '一次流式请求的总时间预算；必须不小于首字节超时与静默超时之和'
  },
  {
    key: 'non_stream_timeout_seconds',
    label: '非流式总超时（秒）',
    min: 1,
    max: 3600,
    step: 10,
    help: '非流式请求的总时间预算，包含该供应商内部的全部重试与退避'
  }
]

const circuitFields: ResilienceField[] = [
  {
    key: 'circuit_failure_threshold',
    label: '连续失败阈值',
    min: 1,
    max: 100,
    step: 1,
    help: '连续失败多少次后打开熔断器（建议 3-10）'
  },
  {
    key: 'circuit_error_rate_threshold',
    label: '错误率阈值',
    min: 0,
    max: 1,
    step: 0.05,
    help: '达到最小请求数后，错误率超过此值即打开熔断器（0-1）'
  },
  {
    key: 'circuit_min_requests',
    label: '最小请求数',
    min: 1,
    max: 1000,
    step: 1,
    help: '计算错误率前至少需要的样本数，避免小样本误判'
  },
  {
    key: 'circuit_recovery_timeout_seconds',
    label: '恢复等待时间（秒）',
    min: 0,
    max: 3600,
    step: 5,
    help: '熔断打开后等待多久进入半开探测（建议 30-120）'
  },
  {
    key: 'circuit_recovery_success_threshold',
    label: '恢复成功阈值',
    min: 1,
    max: 100,
    step: 1,
    help: '半开状态下连续成功多少次后关闭熔断器'
  }
]

/**
 * 推理强度档位。
 *
 * 刻意**不含**「默认」这一项：留空（clearable）就是「不指定」，
 * 把它做成一个可选项会让人以为默认也是一档具体强度。
 */
const reasoningEffortOptions = [
  { label: '低', value: 'low' },
  { label: '中', value: 'medium' },
  { label: '高', value: 'high' },
  { label: '最大', value: 'max' }
]

const resilienceValue = (key: keyof LLMBackend): number => {
  const current = props.adapter?.[key]
  if (typeof current === 'number') return current
  return resilienceDefaults()[key as keyof ReturnType<typeof resilienceDefaults>] as number
}

const updateResilience = (key: keyof LLMBackend, value: number | null) => {
  if (value === null) return
  updateAdapter((nextAdapter) => {
    ;(nextAdapter as Record<string, unknown>)[key as string] = value
  })
}

const resetResilience = () => {
  updateAdapter((nextAdapter) => {
    Object.assign(nextAdapter, resilienceDefaults())
    // 「恢复默认」必须把推理强度也恢复成「不指定」，否则点了恢复默认之后
    // 仍然留着上一次选的档位，与按钮的字面意思不符。
    delete nextAdapter.reasoning_effort
  })
}
</script>

<template>
  <div class="content-area bg" v-if="adapter">
    <div class="content-header">
      <h2>模型管理</h2>
      <n-space>
        <n-button @click="handleDelete" type="error" v-if="!isCreating"> 删除配置 </n-button>
        <n-button @click="handleSave" type="primary"> 保存配置 </n-button>
      </n-space>
    </div>

    <n-scrollbar style="height: var(--n-window-height)">
      <div class="content-body">
        <n-card class="config-section" title="基本信息">
          <n-form
            :model="adapter"
            label-placement="left"
            label-width="120"
            class="form"
            :rules="adapterRules"
            ref="formRef"
          >
            <n-form-item
              label="配置名称"
              path="name"
              feedback="用于区分不同的配置，必须保持唯一"
              required
            >
              <n-input
                :value="adapter.name"
                placeholder="请输入配置名称"
                @update:value="(value) => updateAdapter((nextAdapter) => (nextAdapter.name = value))"
              />
            </n-form-item>

            <n-form-item
              label="接口类型"
              path="adapter"
              feedback="指定模型供应商，使用与模型供应商一致的 API 接口请求模型"
              required
            >
              <n-select
                :value="adapter.adapter"
                :options="adapterTypes.map((type) => ({ label: type, value: type }))"
                placeholder="请选择接口类型"
                @update:value="
                  (value) => updateAdapter((nextAdapter) => (nextAdapter.adapter = value))
                "
              />
            </n-form-item>

            <n-form-item label="启用" path="enable">
              <n-switch
                :value="adapter.enable"
                @update:value="(value) => updateAdapter((nextAdapter) => (nextAdapter.enable = value))"
              />
            </n-form-item>

            <n-spin :show="loading">
              <dynamic-config-form
                :schema="configSchema"
                :model-value="adapter.config"
                v-if="configSchema && adapter?.adapter"
                ref="dynamicConfigForm"
                @update:model-value="
                  (value) => updateAdapter((nextAdapter) => (nextAdapter.config = value))
                "
              />
            </n-spin>
          </n-form>
        </n-card>

        <n-card class="config-section" title="模型列表">
          <template #header-extra>
            <n-space>
              <n-tooltip trigger="hover">
                <template #trigger>
                  <n-button
                    type="primary"
                    @click="handleAutoDetectModels"
                    :disabled="!isAutoDetectModelsSupported"
                    size="small"
                    class="action-button"
                  >
                    <template #icon>
                      <n-icon><refresh-icon /></n-icon>
                    </template>
                    自动检测
                  </n-button>
                </template>
                <div v-if="!isAutoDetectModelsSupported">
                  <p>当前 API 不支持自动检测模型列表，请手动添加模型。</p>
                </div>
                <div v-else>
                  <p>当前 API 支持自动检测模型列表，请确保 API 信息正确填写，然后点击这里。</p>
                </div>
              </n-tooltip>
              <n-button type="primary" @click="handleAddModel" size="small" class="action-button">
                <template #icon>
                  <n-icon><add-icon /></n-icon>
                </template>
                添加模型
              </n-button>
            </n-space>
          </template>

          <ModelListForm
            :value="adapter.models"
            @edit="handleEditModel"
            :model-abilities="modelAbilities"
            @update:value="(value) => updateAdapter((nextAdapter) => (nextAdapter.models = value))"
          />
        </n-card>

        <n-card class="config-section" title="自动故障转移">
          <template #header-extra>
            <n-button size="small" quaternary @click="resetResilience">恢复默认</n-button>
          </template>

          <n-alert type="info" :bordered="false" class="resilience-hint">
            同一模型下配置了多个供应商时，请求失败会按「队列优先级」升序依次尝试。
            某个供应商连续失败达到阈值后，熔断器打开并在恢复等待时间内跳过它。
            认证失败、参数错误和内容策略拒绝不会触发重试或转移。
          </n-alert>

          <n-form label-placement="top" class="form">
            <n-form-item label="参与故障转移队列">
              <n-switch
                :value="adapter.participate_in_failover !== false"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => (nextAdapter.participate_in_failover = value))
                "
              />
              <span class="resilience-inline-help">
                关闭后该供应商仍可被直接选用，但不会出现在自动故障转移队列里
              </span>
            </n-form-item>

            <!--
              推理强度：可清空。留空表示「不指定」，沿用上游默认——这不是
              「等于某个档位」，而是一个必须能表达的独立状态：不支持思考的模型
              收到该字段会直接报错，因此不能替用户填一个默认档位。
            -->
            <n-form-item label="推理强度">
              <n-select
                :value="adapter.reasoning_effort ?? null"
                :options="reasoningEffortOptions"
                clearable
                placeholder="沿用上游默认（不指定）"
                class="resilience-input"
                data-test="reasoning-effort"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => {
                      if (value) {
                        nextAdapter.reasoning_effort = value
                      } else {
                        delete nextAdapter.reasoning_effort
                      }
                    })
                "
              />
              <span class="resilience-inline-help">
                各家字段不同，由适配器翻译：OpenAI 系为 reasoning_effort，
                Claude 为 thinking 预算，Gemini 为 thinkingConfig；「最大」在
                Gemini 上等于交给模型自行决定预算
              </span>
            </n-form-item>

            <!--
              隐藏 AI 署名：会改写模型输出，因此默认关闭，且必须显式提交。
              放在这里而不是全局设置：不同上游的自报身份习惯差别很大，
              一个只在某家模型上出现的毛病不该由全局开关来治。
            -->
            <n-form-item label="隐藏 AI 署名">
              <n-switch
                :value="adapter.hide_ai_attribution === true"
                data-test="hide-ai-attribution"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => {
                      nextAdapter.hide_ai_attribution = value
                    })
                "
              />
              <span class="resilience-inline-help">
                删掉「作为一个 AI 助手」「本回复由 AI 生成」这类自报身份的句子。
                只删署名，不动答案本身——「作为一个 AI 助手，我建议你先备份」里
                后半句是答案；也不动代码块、工具参数与用量统计
              </span>
            </n-form-item>

            <!--
              请求整流器（需求 8）。
              四个开关，而不是一个：三类整流的风险不同，图片降级最该能单独关掉。
            -->
            <n-form-item label="请求整流">
              <n-switch
                :value="adapter.rectifier_enabled !== false"
                data-test="rectifier-enabled"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => {
                      nextAdapter.rectifier_enabled = value
                    })
                "
              />
              <span class="resilience-inline-help">
                上游因参数约束<strong>拒绝</strong>这次请求时，按白名单改一处再重试一次。
                只在上游真的失败、且错误命中已知约束时才动，每类最多改一次；
                改完仍失败就把原始错误抛出来。不做模糊改写——那会掩盖真正的参数错误
              </span>
            </n-form-item>

            <n-form-item label="修正思考签名" v-if="adapter.rectifier_enabled !== false">
              <n-switch
                :value="adapter.rectify_thinking_signature !== false"
                data-test="rectify-thinking-signature"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => {
                      nextAdapter.rectify_thinking_signature = value
                    })
                "
              />
              <span class="resilience-inline-help">
                多轮对话回传上一轮思考块时带签名，换模型或换供应商后该签名失效。
                只移除思考块与残留签名字段，正文、代码块与工具调用原样保留
              </span>
            </n-form-item>

            <n-form-item label="修正思考预算" v-if="adapter.rectifier_enabled !== false">
              <n-switch
                :value="adapter.rectify_thinking_budget !== false"
                data-test="rectify-thinking-budget"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => {
                      nextAdapter.rectify_thinking_budget = value
                    })
                "
              />
              <span class="resilience-inline-help">
                思考预算有下限，且必须小于最大输出长度；两者关系不对时整个请求被拒，
                正文一个字都出不来。<code>adaptive</code> 类型不改——那是让上游自行决定预算
              </span>
            </n-form-item>

            <n-form-item label="图片降级" v-if="adapter.rectifier_enabled !== false">
              <n-switch
                :value="adapter.rectify_media_fallback !== false"
                data-test="rectify-media-fallback"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => {
                      nextAdapter.rectify_media_fallback = value
                    })
                "
              />
              <span class="resilience-inline-help">
                这一项<strong>会改变模型看到的内容</strong>：上游拒收图片时，
                把图片替换为可见占位文本后重试，让对话不中断。
                替换而不是静默删除——否则模型会对着空内容编一个答案，
                而用户以为它真的看过那张图
              </span>
            </n-form-item>

            <n-form-item label="去掉不支持的推理强度" v-if="adapter.rectifier_enabled !== false">
              <n-switch
                :value="adapter.rectify_reasoning_effort_unsupported !== false"
                data-test="rectify-reasoning-effort-unsupported"
                @update:value="
                  (value) =>
                    updateAdapter((nextAdapter) => {
                      nextAdapter.rectify_reasoning_effort_unsupported = value
                    })
                "
              />
              <span class="resilience-inline-help">
                大量兼容网关只实现了核心字段，收到 <code>reasoning_effort</code> 直接拒绝。
                这类失败<strong>换供应商也没用</strong>——同一个不合法字段发给备用上游同样会被拒。
                只在错误里同时出现字段名与「不支持/不认识」类措辞时才动；
                取值非法（上游只认 low/medium/high 而配了最大强度）不会命中，
                否则一个只需降档的请求会被整个删掉思考能力
              </span>
            </n-form-item>
          </n-form>

          <n-collapse :default-expanded-names="['queue']">
            <n-collapse-item title="重试与队列" name="queue">
              <n-grid :cols="2" :x-gap="16" responsive="screen" item-responsive>
                <n-gi v-for="field in queueFields" :key="field.key" span="2 s:2 m:1">
                  <n-form-item :label="field.label" :feedback="field.help">
                    <n-input-number
                      :value="resilienceValue(field.key)"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      class="resilience-input"
                      @update:value="(value) => updateResilience(field.key, value)"
                    />
                  </n-form-item>
                </n-gi>
              </n-grid>
            </n-collapse-item>

            <n-collapse-item title="超时配置" name="timeout">
              <n-grid :cols="2" :x-gap="16" responsive="screen" item-responsive>
                <n-gi v-for="field in timeoutFields" :key="field.key" span="2 s:2 m:1">
                  <n-form-item :label="field.label" :feedback="field.help">
                    <n-input-number
                      :value="resilienceValue(field.key)"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      class="resilience-input"
                      @update:value="(value) => updateResilience(field.key, value)"
                    />
                  </n-form-item>
                </n-gi>
              </n-grid>
            </n-collapse-item>

            <n-collapse-item title="熔断器设置" name="circuit">
              <n-grid :cols="2" :x-gap="16" responsive="screen" item-responsive>
                <n-gi v-for="field in circuitFields" :key="field.key" span="2 s:2 m:1">
                  <n-form-item :label="field.label" :feedback="field.help">
                    <n-input-number
                      :value="resilienceValue(field.key)"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      class="resilience-input"
                      @update:value="(value) => updateResilience(field.key, value)"
                    />
                  </n-form-item>
                </n-gi>
              </n-grid>
            </n-collapse-item>
          </n-collapse>
        </n-card>
      </div>
    </n-scrollbar>
  </div>
</template>

<style scoped>
.content-area {
  display: flex;
  flex-direction: column;
  background-color: var(--bg-color);
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
}

.content-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px;
  background-color: var(--card-bg-color);
  border-bottom: 1px solid var(--border-color);
  height: var(--sidebar-title-height);
  box-shadow: var(--box-shadow-sm, 0 1px 3px rgba(0, 0, 0, 0.05));
}

.content-header h2 {
  margin: 0;
  font-size: var(--font-size-xl, 1.2rem);
  font-weight: 500;
  color: var(--text-color);
  position: relative;
}

.content-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.resilience-hint {
  margin-bottom: 16px;
  line-height: var(--line-height-normal, 1.5);
}

.resilience-inline-help {
  margin-left: 12px;
  color: var(--text-color-3, #909399);
  font-size: var(--font-size-sm, 0.85rem);
}

.resilience-input {
  width: 100%;
}

.action-button {
  transition: all 0.3s ease;
}

.action-button:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow, 0 4px 8px rgba(0, 0, 0, 0.1));
}

.config-section {
  margin-bottom: 20px;
}

@keyframes fade-in {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}
</style>
