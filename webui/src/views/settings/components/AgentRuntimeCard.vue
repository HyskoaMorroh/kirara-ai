<script setup lang="ts">
import { onMounted } from 'vue'
import {
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSpin,
  NText
} from 'naive-ui'
import { useAgentRuntimeViewModel } from '../viewmodels/agent-runtime.vm'

const {
  loading,
  formData,
  channelRows,
  fetchConfig,
  addChannelRow,
  removeChannelRow,
  handleSubmit
} = useAgentRuntimeViewModel()

/**
 * 三档取回方式。`inherit` 刻意不在其中：它只在 Agent 层有意义
 * （「跟随上层」），进程默认没有上层可继承。
 */
const REPLY_STREAM_OPTIONS = [
  { label: 'off · 非流式一次取回', value: 'off' },
  { label: 'aggregate · 流式取回、服务端聚合', value: 'aggregate' },
  { label: 'incremental · 流式取回、边取边推', value: 'incremental' }
]

onMounted(() => {
  fetchConfig()
})
</script>

<template>
  <n-card title="Agent 运行时" class="settings-card">
    <div class="card-intro">
      <n-text depth="3">
        单轮的时间预算、回复取回方式与工具渐进披露阈值。四项都在启动时读入，
        保存后需要重启服务才生效。
      </n-text>
    </div>

    <n-spin :show="loading">
      <n-form
        :model="formData"
        label-placement="left"
        label-width="150"
        require-mark-placement="right-hanging"
      >
        <n-form-item label="单轮总时间预算" path="turn_deadline_seconds">
          <n-input-number
            v-model:value="formData.turn_deadline_seconds"
            class="numeric-field"
            :min="0"
            :max="3600"
            :step="10"
            :precision="1"
          >
            <template #suffix>秒</template>
          </n-input-number>
          <template #feedback>
            <n-text depth="3">
              超时后正在等待的上游请求会被取消，而不是让这一轮无限悬挂。
              填 <code>0</code> 表示不设总预算。它约束的是整轮（含多次工具调用）；
              单次请求的超时仍由各供应商自己的超时字段决定。
            </n-text>
          </template>
        </n-form-item>

        <n-form-item label="回复取回方式" path="reply_stream_mode">
          <n-select
            v-model:value="formData.reply_stream_mode"
            class="mode-field"
            :options="REPLY_STREAM_OPTIONS"
          />
          <template #feedback>
            <n-text depth="3">
              进程默认。<code>aggregate</code> 买到的是首字节超时、静默超时与
              首字节前故障转移，用户端**仍然是一条完整回复**；要让用户看到文字
              逐段出现必须用 <code>incremental</code>，且渠道能改写已交付内容
              （Telegram 编辑已发消息、WebUI 在线对话走 SSE），
              不具备的渠道会自动退回 <code>aggregate</code>。
            </n-text>
          </template>
        </n-form-item>

        <n-form-item label="按渠道覆盖">
          <div class="channel-rows">
            <div v-for="(row, index) in channelRows" :key="index" class="channel-row">
              <n-input
                v-model:value="row.channel"
                class="channel-name"
                placeholder="渠道类型，如 telegram"
              />
              <n-select
                v-model:value="row.mode"
                class="channel-mode"
                :options="REPLY_STREAM_OPTIONS"
              />
              <n-button quaternary type="error" @click="removeChannelRow(index)">
                移除
              </n-button>
            </div>
            <n-button dashed class="add-row" @click="addChannelRow">
              添加渠道覆盖
            </n-button>
            <n-text v-if="!channelRows.length" depth="3" class="empty-hint">
              未配置任何覆盖，全部渠道使用上面的进程默认。
            </n-text>
          </div>
          <template #feedback>
            <n-text depth="3">
              优先级是 <strong>Agent 声明 &gt; 渠道默认 &gt; 进程默认</strong>。
              渠道类型取值：<code>webui</code>、<code>onebot</code>、
              <code>qqbot</code>、<code>telegram</code>、<code>wecom</code>、
              <code>http</code>。其中 <strong><code>telegram</code> 与
              <code>webui</code> 能兑现 <code>incremental</code></strong>（前者编辑已发消息、
              后者走 SSE）；<code>onebot</code> / <code>qqbot</code> / <code>wecom</code> /
              <code>http</code> 没有等价能力，配了会自动退回 <code>aggregate</code>，
              不报错但也不会逐段显示。WebUI 在线对话默认就走 SSE，
              把 <code>webui</code> 显式配成 <code>off</code> 可以关掉它
              （反向代理关掉分块传输时用得上）。
            </n-text>
          </template>
        </n-form-item>

        <n-form-item label="工具搜索阈值" path="tool_search_threshold">
          <n-input-number
            v-model:value="formData.tool_search_threshold"
            class="numeric-field"
            :min="0"
            :max="500"
            :step="1"
            :precision="0"
          >
            <template #suffix>个工具</template>
          </n-input-number>
          <template #feedback>
            <n-text depth="3">
              工具数超过这个值时，系统提示词里只放一行目录，完整定义由
              <code>search_tools</code> 按关键词取回。用阈值而不是开关，是因为
              收益完全取决于工具数量：三个工具时多一层搜索是纯损失，四十个工具时
              全量注入是每轮固定多付上万 token。填 <code>0</code> 表示关闭，
              拿回逐字节一致的全量注入行为。
            </n-text>
          </template>
        </n-form-item>
      </n-form>

      <div class="actions">
        <n-space justify="end">
          <n-button type="primary" :loading="loading" @click="handleSubmit">
            保存配置
          </n-button>
        </n-space>
      </div>
    </n-spin>
  </n-card>
</template>

<style scoped>
.settings-card {
  max-width: 800px;
  margin: 0 auto;
}

.card-intro {
  margin-bottom: 16px;
  line-height: var(--line-height-normal, 1.6);
}

.numeric-field {
  width: 220px;
}

.mode-field {
  width: 320px;
}

.channel-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.channel-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.channel-name {
  flex: 0 0 200px;
}

.channel-mode {
  flex: 1 1 auto;
  min-width: 200px;
}

.add-row {
  align-self: flex-start;
}

.empty-hint {
  font-size: 0.85rem;
}

.actions {
  margin-top: 24px;
}

:deep(code) {
  padding: 0.1em 0.35em;
  border-radius: 4px;
  background-color: var(--code-bg-color, rgba(128, 128, 128, 0.14));
  font-family: var(--font-family-mono, ui-monospace, monospace);
  font-size: 0.9em;
}

/* 窄屏下渠道行改为纵向堆叠：三个控件挤在一行会让下拉框窄到读不出档位名。 */
@media (max-width: 640px) {
  .channel-row {
    flex-wrap: wrap;
  }

  .channel-name,
  .channel-mode {
    flex: 1 1 100%;
  }
}
</style>
