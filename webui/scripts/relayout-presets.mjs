#!/usr/bin/env node
/**
 * 预置工作流坐标重算工具。
 *
 * 背景：shipped 的预置工作流 YAML（kirara_ai/workflow/presets/**.yaml）里
 * 每个 block 都写死了 position，其中不少本来就互相压叠，用户一打开编辑器
 * 就看到「流程图框重叠」。编辑器里的「自动排布」只能修当前画布，改不了
 * 随包发布的 YAML，所以需要一个能在命令行跑的重算器。
 *
 * 本脚本复用 webui/src/components/workflow/useLayout.ts 里导出的
 * computeWorkflowLayout()——与编辑器完全同一套 dagre + 去重叠算法，
 * 不做第二份实现，避免两边算出不同的结果。
 * （Node 直接以类型擦除方式加载该 .ts 文件，需要 Node >= 22.18 或 23+。）
 *
 * 只读：本脚本永远不修改任何文件，只把结果打印到 stdout，
 * 由调用方决定是否写回 YAML。
 *
 * 用法：
 *   node webui/scripts/relayout-presets.mjs <preset.yaml> [更多 yaml...]
 *   node webui/scripts/relayout-presets.mjs <preset.yaml> --json
 *   node webui/scripts/relayout-presets.mjs <preset.yaml> --block-types=types.json
 *
 * 参数：
 *   --json           以 JSON 输出 { 文件: { 节点名: {x, y} } }，便于脚本消费
 *   --block-types=F  可选。后端 GET /block/types 的响应（或其中的 types 数组）
 *                    落盘后的 JSON；提供后按真实的端口/配置项数量估算节点
 *                    尺寸，结果与编辑器里的「自动排布」一致。
 *                    不提供时退化为从 connected_to 推断用到的端口，
 *                    节点会被估得略小，但仍保证互不重叠。
 *   --direction=LR   布局方向，LR（默认，从左到右）或 TB（从上到下）
 */

import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const useLayoutPath = path.join(scriptDir, '..', 'src', 'components', 'workflow', 'useLayout.ts')

let computeWorkflowLayout
try {
  ;({ computeWorkflowLayout } = await import(pathToFileUrl(useLayoutPath)))
} catch (error) {
  console.error('无法加载 useLayout.ts：' + (error?.message || error))
  console.error('该文件是 TypeScript，需要 Node >= 22.18（或 23+）的类型擦除支持。')
  process.exit(1)
}

let yaml
try {
  yaml = (await import('js-yaml')).default
} catch {
  console.error('缺少 js-yaml。请在 webui 目录下执行：yarn add -D js-yaml')
  process.exit(1)
}

/** Windows 上必须转成 file:// URL，否则 import() 会把盘符当协议 */
function pathToFileUrl(filePath) {
  const absolute = path.resolve(filePath)
  const normalized = absolute.replace(/\\/g, '/')
  return normalized.startsWith('/') ? `file://${normalized}` : `file:///${normalized}`
}

const args = process.argv.slice(2)
const files = args.filter((arg) => !arg.startsWith('--'))
const asJson = args.includes('--json')
const directionArg = args.find((arg) => arg.startsWith('--direction='))
const direction = directionArg ? directionArg.slice('--direction='.length) : 'LR'
const blockTypesArg = args.find((arg) => arg.startsWith('--block-types='))

if (files.length === 0) {
  console.error('用法：node webui/scripts/relayout-presets.mjs <preset.yaml> [--json] [--block-types=types.json]')
  process.exit(1)
}

/** 读取可选的 block 类型表，建成 type_name -> 定义 的索引 */
async function loadBlockTypes() {
  if (!blockTypesArg) return null
  const file = blockTypesArg.slice('--block-types='.length)
  const parsed = JSON.parse(await readFile(file, 'utf8'))
  const list = Array.isArray(parsed) ? parsed : parsed.types || []
  const index = new Map()
  for (const type of list) {
    if (type?.type_name) index.set(type.type_name, type)
  }
  return index
}

/**
 * 把 YAML 的 blocks 转成 computeWorkflowLayout 需要的描述结构。
 *
 * YAML 里没有端口定义，只有 connected_to 的 mapping。没有 block 类型表时
 * 就用 mapping 里出现过的 from/to 端口名当作该节点的输出/输入，
 * 这是能从 YAML 本身得到的最好近似。
 */
function toLayoutDescriptors(blocks, blockTypes) {
  const inferredOutputs = new Map()
  const inferredInputs = new Map()
  for (const block of blocks) {
    inferredOutputs.set(block.name, new Set())
    inferredInputs.set(block.name, new Set())
  }
  for (const block of blocks) {
    for (const wire of block.connected_to || []) {
      const mapping = wire.mapping || {}
      if (mapping.from) inferredOutputs.get(block.name)?.add(mapping.from)
      if (mapping.to && inferredInputs.has(wire.target)) {
        inferredInputs.get(wire.target).add(mapping.to)
      }
    }
  }

  return blocks.map((block) => {
    const blockType = blockTypes?.get(block.type)
    if (blockType) {
      return {
        id: block.name,
        type: block.type === 'internal:code' ? 'code' : 'custom',
        label: blockType.label || block.name,
        inputs: blockType.inputs || [],
        outputs: blockType.outputs || [],
        configs: blockType.configs || []
      }
    }
    // 退化路径：标签用 type 的后半段，配置项数量按 params 的键数
    const shortName = String(block.type || '').split(':').pop() || block.name
    return {
      id: block.name,
      type: block.type === 'internal:code' ? 'code' : 'custom',
      label: shortName,
      inputs: [...(inferredInputs.get(block.name) || [])].map((name) => ({ name })),
      outputs: [...(inferredOutputs.get(block.name) || [])].map((name) => ({ name })),
      configs: Object.keys(block.params || {}).map((name) => ({ name }))
    }
  })
}

/** 从 connected_to 收集连线，供 dagre 分层 */
function toLayoutEdges(blocks) {
  const edges = []
  for (const block of blocks) {
    for (const wire of block.connected_to || []) {
      if (wire?.target) edges.push({ source: block.name, target: wire.target })
    }
  }
  return edges
}

const blockTypes = await loadBlockTypes()
const jsonResult = {}
let exitCode = 0

for (const file of files) {
  let doc
  try {
    doc = yaml.load(await readFile(file, 'utf8'))
  } catch (error) {
    console.error(`读取 ${file} 失败：${error?.message || error}`)
    exitCode = 1
    continue
  }

  const blocks = Array.isArray(doc?.blocks) ? doc.blocks : []
  if (blocks.length === 0) {
    console.error(`${file} 中没有 blocks，已跳过`)
    exitCode = 1
    continue
  }

  const boxes = computeWorkflowLayout(toLayoutDescriptors(blocks, blockTypes), toLayoutEdges(blocks), {
    direction
  })

  if (asJson) {
    jsonResult[file] = Object.fromEntries(
      blocks.map((block) => {
        const box = boxes[block.name]
        return [block.name, { x: box?.x ?? 0, y: box?.y ?? 0 }]
      })
    )
    continue
  }

  console.log(`# ${file}`)
  console.log(`# 工作流：${doc?.name || '(未命名)'}  节点数：${blocks.length}  方向：${direction}`)
  if (!blockTypes) {
    console.log('# 提示：未提供 --block-types，节点尺寸按 YAML 推断，建议补上以获得与编辑器一致的结果')
  }
  for (const block of blocks) {
    const box = boxes[block.name]
    if (!box) continue
    const before = block.position || {}
    const moved = before.x !== box.x || before.y !== box.y
    console.log(
      `  - name: ${block.name}\n` +
        `    position:\n` +
        `      x: ${box.x}\n` +
        `      y: ${box.y}` +
        (moved ? `   # 原为 x: ${before.x ?? '-'} y: ${before.y ?? '-'}` : '   # 未变化')
    )
  }
  console.log('')
}

if (asJson) {
  console.log(JSON.stringify(jsonResult, null, 2))
}

process.exit(exitCode)
