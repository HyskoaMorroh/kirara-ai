<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NTag, useDialog, useMessage } from 'naive-ui'

import { llmApi, type PricingVersion } from '@/api/llm'
import { HttpRequestError } from '@/utils/http'
import {
  defaultEffectiveFrom,
  EFFECTIVE_FROM_HINT,
  normalizeEffectiveFrom
} from './pricing-effective-from'

const message = useMessage()
const dialog = useDialog()
const versions = ref<PricingVersion[]>([])
const backupGenerations = ref<number[]>([])
const revision = ref(0)
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const conflictMessage = ref('')
const savedMessage = ref('')
const editingId = ref<string | null>(null)
const editorOpen = ref(false)
const form = ref<PricingVersion>(emptyVersion())

function emptyVersion(): PricingVersion {
  return {
    version_id: '',
    provider: '',
    model: '',
    effective_from: defaultEffectiveFrom(),
    currency: 'USD',
    input_per_million: '0',
    output_per_million: '0',
    cache_read_per_million: '0',
    cache_write_per_million: '0'
  }
}

function copyVersion(version: PricingVersion): PricingVersion {
  return { ...version }
}

async function loadCatalog() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await llmApi.listPricing()
    revision.value = response.data.revision
    versions.value = response.data.versions
    backupGenerations.value = response.data.backup_generations
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '成本定价目录加载失败'
  } finally {
    loading.value = false
  }
}

function startCreate() {
  editingId.value = null
  editorOpen.value = true
  conflictMessage.value = ''
  savedMessage.value = ''
  form.value = emptyVersion()
}

function startEdit(version: PricingVersion) {
  editingId.value = version.version_id
  editorOpen.value = true
  conflictMessage.value = ''
  savedMessage.value = ''
  form.value = copyVersion(version)
}

function cancelEdit() {
  editorOpen.value = false
  editingId.value = null
  conflictMessage.value = ''
}

function currentVersionAfterConflict() {
  if (!editingId.value) return
  const current = versions.value.find((version) => version.version_id === editingId.value)
  if (current) form.value = copyVersion(current)
}

function isRevisionConflict(error: unknown) {
  return error instanceof HttpRequestError && error.status === 409 &&
    typeof error.data === 'object' && error.data !== null &&
    (error.data as Record<string, unknown>).code === 'revision_conflict'
}

async function savePricing() {
  if (!form.value.version_id || !form.value.provider || !form.value.model) {
    errorMessage.value = '请填写版本 ID、Provider 和模型'
    return
  }
  // 生效时间在本地就能判定，不该往返一次才被 pydantic 拒绝：那只是把错误延后，
  // 而返回的英文校验错误对填表的人没有可操作性。
  // 归一化到 UTC 的理由是后端按 UTC 存储与比较——不归一化时「界面上显示的时刻」
  // 与「用于计费判定的时刻」是两个值，而这类偏差没有任何症状，
  // 直到有人去核对账单。
  const effectiveFrom = normalizeEffectiveFrom(form.value.effective_from)
  if (!effectiveFrom.ok) {
    errorMessage.value = effectiveFrom.error
    return
  }
  form.value.effective_from = effectiveFrom.value
  saving.value = true
  errorMessage.value = ''
  conflictMessage.value = ''
  savedMessage.value = ''
  try {
    if (editingId.value) {
      await llmApi.updatePricing(editingId.value, {
        expected_revision: revision.value,
        version: copyVersion(form.value)
      })
    } else {
      await llmApi.createPricing({
        expected_revision: revision.value,
        version: copyVersion(form.value)
      })
    }
    message.success('定价版本已保存')
    savedMessage.value = '定价版本已保存'
    editorOpen.value = false
    editingId.value = null
    await loadCatalog()
  } catch (error) {
    if (isRevisionConflict(error)) {
      await loadCatalog()
      currentVersionAfterConflict()
      conflictMessage.value = '定价目录已被其他操作更新，请核对当前值后重试'
      return
    }
    errorMessage.value = error instanceof Error ? error.message : '定价版本保存失败'
  } finally {
    saving.value = false
  }
}

function removePricing(version: PricingVersion) {
  dialog.warning({
    title: '删除定价版本',
    content: `确定删除 ${version.version_id} 吗？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await llmApi.deletePricing(version.version_id, {
          expected_revision: revision.value,
          confirmed: true
        })
        message.success('定价版本已删除')
        await loadCatalog()
      } catch (error) {
        if (isRevisionConflict(error)) {
          await loadCatalog()
          conflictMessage.value = '定价目录已被其他操作更新，请重试删除'
        } else {
          errorMessage.value = error instanceof Error ? error.message : '定价版本删除失败'
        }
      }
    }
  })
}

function restorePricing(generation: number) {
  dialog.warning({
    title: '恢复定价备份',
    content: `确定恢复第 ${generation} 代备份吗？当前目录会作为新版本保留。`,
    positiveText: '恢复',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await llmApi.restorePricing({
          expected_revision: revision.value,
          generation,
          confirmed: true
        })
        message.success('定价目录已恢复')
        await loadCatalog()
      } catch (error) {
        if (isRevisionConflict(error)) {
          await loadCatalog()
          conflictMessage.value = '定价目录已被其他操作更新，请重试恢复'
        } else {
          errorMessage.value = error instanceof Error ? error.message : '定价目录恢复失败'
        }
      }
    }
  })
}

async function importPricing(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    const catalog = JSON.parse(await file.text())
    await llmApi.importPricing({ expected_revision: revision.value, catalog })
    message.success('定价目录已导入')
    await loadCatalog()
  } catch (error) {
    if (isRevisionConflict(error)) {
      await loadCatalog()
      conflictMessage.value = '定价目录已被其他操作更新，请重试导入'
    } else {
      errorMessage.value = error instanceof Error ? error.message : '定价目录导入失败'
    }
  }
}

function exportFileName(response: Response) {
  const header = response.headers.get('Content-Disposition') || ''
  const match = header.match(/filename\*?=(?:UTF-8''|")?([^;"]+)/i)
  const candidate = match?.[1] ? decodeURIComponent(match[1].trim()) : 'price-catalog.json'
  return candidate.replace(/[^a-zA-Z0-9._-]/g, '_') || 'price-catalog.json'
}

async function exportPricing() {
  try {
    const response = await llmApi.exportPricing()
    if (!response.ok) throw new Error(`导出失败 (HTTP ${response.status})`)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = exportFileName(response)
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '定价目录导出失败'
  }
}

const isEditing = computed(() => editingId.value !== null)

onMounted(loadCatalog)
</script>

<template>
  <div class="pricing-view">
    <header class="page-header">
      <div>
        <p class="eyebrow">LLM</p>
        <h1>成本定价</h1>
        <p class="subtitle">按 Provider、模型和生效时间维护成本版本，历史请求使用自己的价格快照。</p>
      </div>
      <div class="header-actions">
        <span class="revision">修订 {{ revision }}</span>
        <n-button data-test="export-pricing" @click="exportPricing">导出</n-button>
        <label class="file-button">
          导入
          <input type="file" accept="application/json,.json" @change="importPricing" />
        </label>
        <n-button type="primary" data-test="create-pricing" @click="startCreate">新建版本</n-button>
      </div>
    </header>

    <n-alert v-if="errorMessage" type="error" role="alert" class="notice">{{ errorMessage }}</n-alert>
    <n-alert v-if="conflictMessage" type="warning" role="alert" class="notice">{{ conflictMessage }}</n-alert>
    <n-alert v-if="savedMessage" type="success" role="status" class="notice">{{ savedMessage }}</n-alert>

    <n-card v-if="editorOpen" class="editor-card" :title="isEditing ? '编辑定价版本' : '新建定价版本'">
      <form class="pricing-form" @submit.prevent="savePricing">
        <label>Provider<input v-model="form.provider" name="provider" autocomplete="off" /></label>
        <label>模型<input v-model="form.model" name="model" autocomplete="off" /></label>
        <label>版本 ID<input v-model="form.version_id" name="version_id" autocomplete="off" /></label>
        <!--
          格式提示常驻在字段旁，而不是只在出错后出现：后者等于让每个人都先错一次。
          后端强制要求带时区，而「带时区」不是一个能猜到的约定。
        -->
        <label>
          生效时间
          <input v-model="form.effective_from" name="effective_from" autocomplete="off" />
          <small class="field-hint">{{ EFFECTIVE_FROM_HINT }}</small>
        </label>
        <label>货币<input v-model="form.currency" name="currency" maxlength="3" autocomplete="off" /></label>
        <label>输入 / 百万 Token<input v-model="form.input_per_million" name="input_per_million" inputmode="decimal" /></label>
        <label>输出 / 百万 Token<input v-model="form.output_per_million" name="output_per_million" inputmode="decimal" /></label>
        <label>缓存读取 / 百万 Token<input v-model="form.cache_read_per_million" name="cache_read_per_million" inputmode="decimal" /></label>
        <label>缓存写入 / 百万 Token<input v-model="form.cache_write_per_million" name="cache_write_per_million" inputmode="decimal" /></label>
        <div class="form-actions">
          <n-button type="primary" data-test="save-pricing" :loading="saving" @click="savePricing">保存</n-button>
          <n-button @click="cancelEdit">取消</n-button>
        </div>
      </form>
    </n-card>

    <section v-if="loading" class="loading-state" aria-busy="true">正在加载成本定价目录...</section>
    <section v-else-if="versions.length === 0" class="empty-state" role="status">
      <h2>还没有成本定价版本</h2>
      <p>创建第一个版本后，统计和历史账单才能按价格计算。</p>
      <n-button type="primary" data-test="create-pricing" @click="startCreate">新建版本</n-button>
    </section>
    <section v-else class="catalog-section" aria-label="成本定价版本列表">
      <div class="catalog-heading"><h2>定价版本</h2><span>{{ versions.length }} 个版本</span></div>
      <div class="version-list">
        <article v-for="version in versions" :key="version.version_id" class="version-row">
          <div class="version-main">
            <strong>{{ version.model }}</strong>
            <span>{{ version.provider }} · {{ version.version_id }}</span>
            <small>生效 {{ version.effective_from }} · {{ version.currency }}</small>
          </div>
          <div class="rate-grid">
            <span>输入 <b>{{ version.input_per_million }}</b></span>
            <span>输出 <b>{{ version.output_per_million }}</b></span>
            <span>缓存读 <b>{{ version.cache_read_per_million }}</b></span>
            <span>缓存写 <b>{{ version.cache_write_per_million }}</b></span>
          </div>
          <div class="row-actions">
            <n-button :aria-label="`编辑定价 ${version.version_id}`" @click="startEdit(version)">编辑</n-button>
            <n-button :aria-label="`删除定价 ${version.version_id}`" @click="removePricing(version)">删除</n-button>
          </div>
        </article>
      </div>
    </section>

    <section v-if="backupGenerations.length" class="backup-section" aria-label="定价备份">
      <div class="catalog-heading"><h2>可恢复备份</h2><span>恢复会生成新的修订</span></div>
      <div class="backup-list">
        <n-tag v-for="generation in backupGenerations" :key="generation" type="info">
          第 {{ generation }} 代
          <button type="button" :aria-label="`恢复第 ${generation} 代备份`" @click="restorePricing(generation)">恢复</button>
        </n-tag>
      </div>
    </section>
  </div>
</template>

<style scoped>
.pricing-view { max-width: 1180px; margin: 0 auto; padding: 28px 32px 48px; color: var(--text-color); }
.page-header, .catalog-heading, .version-row, .header-actions, .form-actions { display: flex; align-items: center; }
.page-header { justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.eyebrow { margin: 0 0 6px; color: var(--primary-color); font-size: 12px; font-weight: 700; text-transform: uppercase; }
h1, h2, p { margin-top: 0; } h1 { margin-bottom: 8px; font-size: 28px; } h2 { margin-bottom: 0; font-size: 18px; }
.subtitle, .catalog-heading span, .version-main span, .version-main small { color: var(--text-color-2); }
.subtitle { margin-bottom: 0; max-width: 660px; line-height: 1.6; }
.header-actions { flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.revision { padding: 6px 10px; color: var(--text-color-2); font-size: 13px; white-space: nowrap; }
.file-button { display: inline-flex; align-items: center; min-height: 34px; padding: 0 14px; border: 1px solid var(--border-color); border-radius: var(--radius-sm); cursor: pointer; }
.file-button input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
.notice { margin-bottom: 16px; }
.editor-card { margin-bottom: 24px; }
.pricing-form { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.pricing-form label { display: grid; gap: 6px; font-size: 13px; color: var(--text-color-2); }
.pricing-form input { min-width: 0; padding: 8px 10px; color: var(--text-color); background: var(--card-color); border: 1px solid var(--border-color); border-radius: var(--radius-sm); }
.form-actions { grid-column: 1 / -1; gap: 8px; margin-top: 4px; }
.loading-state, .empty-state { padding: 64px 24px; text-align: center; border: 1px dashed var(--border-color); }
.empty-state h2 { margin-bottom: 8px; } .empty-state p { color: var(--text-color-2); }
.catalog-heading { justify-content: space-between; margin: 24px 0 10px; }
.version-list { border-top: 1px solid var(--border-color); }
.version-row { gap: 20px; padding: 16px 0; border-bottom: 1px solid var(--border-color); }
.version-main { flex: 1 1 250px; min-width: 0; display: grid; gap: 4px; }
.version-main span, .version-main small { overflow-wrap: anywhere; }
.rate-grid { display: grid; grid-template-columns: repeat(4, minmax(74px, 1fr)); gap: 10px; flex: 1 1 380px; font-size: 12px; color: var(--text-color-2); }
.rate-grid span { display: grid; gap: 3px; } .rate-grid b { color: var(--text-color); font-size: 14px; }
.row-actions { display: flex; gap: 6px; }
.backup-section { margin-top: 32px; } .backup-list { display: flex; flex-wrap: wrap; gap: 8px; }
.backup-list button { margin-left: 6px; padding: 0; border: 0; color: inherit; background: transparent; text-decoration: underline; cursor: pointer; }
@media (max-width: 768px) {
  .pricing-view { padding: 20px 16px 40px; }
  .page-header, .version-row { align-items: stretch; flex-direction: column; }
  .header-actions { justify-content: flex-start; }
  .pricing-form { grid-template-columns: 1fr; }
  .rate-grid { grid-template-columns: repeat(2, minmax(100px, 1fr)); }
  .row-actions { justify-content: flex-start; }
}
</style>
