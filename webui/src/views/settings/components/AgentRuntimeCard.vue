<script setup lang="ts">
import { onMounted } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSpin,
  NText
} from 'naive-ui'
import {
  CREATOR_CHANNEL_TYPES,
  useAgentRuntimeViewModel
} from '../viewmodels/agent-runtime.vm'

const {
  loading,
  formData,
  channelRows,
  fetchConfig,
  addChannelRow,
  removeChannelRow,
  addCreatorIdentity,
  removeCreatorIdentity,
  handleSubmit
} = useAgentRuntimeViewModel()

/** 渠道下拉选项。后端只接受这六个，写错会静默匹配不上任何消息。 */
const creatorChannelOptions = CREATOR_CHANNEL_TYPES.map((value) => ({
  label: value,
  value
}))

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

        <n-form-item label="创建者渠道身份">
          <div class="creator-identities">
            <!--
              这不是普通配置项：它授予的是宿主操作权限。默认空表的含义是
              「聊天侧谁都拿不到创建者身份」——MCP 工具列表恒空、command Hook
              恒被拒，包括创建者本人。不把这一点说出来，用户会在 QQ 里对着自己的
              机器人说「帮我装个 skill」，得到一次正常回复、工具一个没生效，
              而界面上没有任何地方解释为什么。
            -->
            <n-alert type="warning" :show-icon="true" class="creator-warning">
              声明之后，来自这些身份的消息可以在 IM 渠道上调用 MCP 工具与
              command Hook —— 也就是能通过对话修改服务器内容。只填你自己的账号。
              未声明时聊天侧不授予任何人，包括你本人。
            </n-alert>
            <div
              v-for="(identity, index) in formData.creator_channel_identities"
              :key="index"
              class="creator-identity-row"
            >
              <n-select
                v-model:value="identity.channel_type"
                class="creator-channel"
                :options="creatorChannelOptions"
                :input-props="{ 'aria-label': `第 ${index + 1} 条身份的渠道类型` }"
              />
              <n-input
                v-model:value="identity.sender_scope"
                class="creator-sender"
                placeholder="你的用户标识，如 QQ 号"
                :input-props="{ 'aria-label': `第 ${index + 1} 条身份的发送者标识` }"
              />
              <n-input
                v-model:value="identity.account_scope"
                class="creator-optional"
                placeholder="机器人账号（可留空）"
                :input-props="{ 'aria-label': `第 ${index + 1} 条身份的机器人账号` }"
              />
              <n-checkbox
                :checked="identity.allow_group_chat"
                @update:checked="(value: boolean) => (identity.allow_group_chat = value)"
              >
                群聊也生效
              </n-checkbox>
              <n-button
                quaternary
                type="error"
                data-test="remove-creator-identity"
                :aria-label="`移除第 ${index + 1} 条身份`"
                @click="removeCreatorIdentity(index)"
              >
                移除
              </n-button>
            </div>
            <n-button
              dashed
              class="add-row"
              data-test="add-creator-identity"
              @click="addCreatorIdentity"
            >
              添加创建者身份
            </n-button>
            <n-text
              v-if="!formData.creator_channel_identities.length"
              depth="3"
              class="empty-hint"
            >
              未声明任何身份：IM 渠道上的 MCP 工具与 command Hook 对所有人不可用。
            </n-text>
          </div>
          <template #feedback>
            <n-text depth="3">
              渠道与发送者标识<strong>一起比对</strong>：QQ 号和 Telegram 用户 ID
              可能撞号，只比一个等于把另一个渠道的同号用户也放进来。
              <strong>「群聊也生效」默认关闭</strong>——群里所有人都看得到你发的
              指令并照抄，照抄的人发送者标识不同因而拿不到身份，但把宿主操作暴露在
              多人可见的会话里是另一回事。改完<strong>需要重启服务</strong>才生效。
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

/* 创建者身份编辑区。一行一条身份，窄屏时改为纵向堆叠，
   否则四个控件挤在一行会让发送者标识只剩几十像素。 */
.creator-identities {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.creator-warning {
  margin-bottom: 4px;
}

.creator-identity-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.creator-channel {
  width: 130px;
}

.creator-sender {
  flex: 1 1 180px;
  min-width: 150px;
}

.creator-optional {
  flex: 1 1 160px;
  min-width: 140px;
}

@media (max-width: 720px) {
  .creator-identity-row {
    flex-direction: column;
    align-items: stretch;
  }

  .creator-channel,
  .creator-sender,
  .creator-optional {
    width: 100%;
  }
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
