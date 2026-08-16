import dagre from '@dagrejs/dagre'
import { Position, useVueFlow, type Edge, type Node } from '@vue-flow/core'
import { ref } from 'vue'

/** 节点尺寸估算用的版式常量，与 CustomNode.vue / CodeNode.vue 的 CSS 保持一致 */
/**
 * 宽度上下限同时被 CustomNode.vue 以内联样式绑定，估算值与真实 CSS 盒子
 * 因此不可能再各说各话——改这里就等于改了节点的实际宽度。
 * 原先固定 200~300px，中文端口标签（如「聊天记忆内容」「用户提示词格式」）
 * 只能显示 4~5 个字后接省略号，所以把上限放宽到 360px。
 */
export const NODE_MIN_WIDTH = 220
export const NODE_MAX_WIDTH = 360
/** 代码节点的 CSS 仍是 200~300px（CodeNode.vue 不在本次改动范围内），单独取值 */
export const CODE_NODE_MIN_WIDTH = 200
export const CODE_NODE_MAX_WIDTH = 300
const NODE_HEADER_HEIGHT = 40
const PORT_ROW_HEIGHT = 28
const PORTS_PADDING = 12
const CONFIG_ROW_HEIGHT = 30
const BODY_PADDING = 24
const CODE_PREVIEW_HEIGHT = 132
/** .config-preview 的 gap：配置项之间的纵向间隔 */
const CONFIG_ROW_GAP = 4
/** .config-preview-item 的左右 padding 合计 */
const CONFIG_ROW_PADDING = 16
/** .config-name 的固定占比，值格再宽也不会挤掉名称 */
const CONFIG_NAME_RATIO = 0.45
/** .custom-node-header 的左右 padding 合计 */
const HEADER_PADDING = 28
/** 标题右侧 #id 角标的估算宽度（6 位等宽字符 + padding + margin） */
const NODE_ID_CHIP_WIDTH = 56
/** .port-label 的左右 margin 合计 */
const PORT_LABEL_MARGIN = 20
/** .ports-container 的 gap */
const PORTS_COLUMN_GAP = 8
/** 节点外框的 border 合计（上下 / 左右各 1px） */
const NODE_BORDER = 2
/**
 * 估算值宁可略大也不能偏小：dagre 与下面的去重叠扫描都按这个尺寸留空隙，
 * 一旦小于真实渲染高度，节点就会重新压在一起。
 */
const NODE_SIZE_SAFETY_MARGIN = 6

/** dagre 的节点/层级间距。留出足够空隙，让连线拐点不会压在相邻节点上 */
const NODE_SEPARATION = 60
const RANK_SEPARATION = 120
const EDGE_SEPARATION = 24

/** 拖放/复制节点时的对齐网格，与画布 Background 的 gap 一致 */
export const LAYOUT_GRID_SIZE = 20

/** 全宽（CJK、全角标点、韩文等）字符集合，这类字符的字宽约等于 font-size */
const FULL_WIDTH_PATTERN =
  /[ᄀ-ᅟ⺀-〾ぁ-㏿㐀-䶿一-鿿ꀀ-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/
/** 明显比平均值窄的西文字符 */
const NARROW_PATTERN = /[iIl1.,:;'`|!\[\]()\- ]/
/** 明显比平均值宽的西文字符 */
const WIDE_PATTERN = /[A-Z@%&WM]/

/**
 * 估算一段文本的渲染宽度。
 *
 * 旧实现用 `label.length * 14`，这是按纯中文标签调出来的系数：
 * 西文标签会被高估（"message" 被算成 98px，实际约 55px），
 * 中英混排则被低估（14px 字号下汉字本身就要 14px，再加西文部分就超了）。
 * 这里按字符类别分别累加，中西文混排都不会系统性偏差。
 */
export function measureTextWidth(text: string, fontSize: number): number {
  let width = 0
  for (const char of String(text)) {
    if (FULL_WIDTH_PATTERN.test(char)) {
      width += fontSize
    } else if (NARROW_PATTERN.test(char)) {
      width += fontSize * 0.34
    } else if (WIDE_PATTERN.test(char)) {
      width += fontSize * 0.7
    } else {
      width += fontSize * 0.56
    }
  }
  return width
}

/** 端口描述，只取排版需要的字段 */
export interface LayoutPortDescriptor {
  name?: string
  label?: string
  required?: boolean
}

/** 配置项描述，只取排版需要的字段 */
export interface LayoutConfigDescriptor {
  name?: string
  label?: string
}

/**
 * 与 vue-flow 解耦的节点描述。
 *
 * 画布内的 layout() 与画布外的脚本（webui/scripts/relayout-presets.mjs）
 * 共用同一份描述结构，保证「编辑器里整理出的坐标」与「预置 YAML 里
 * 重算出的坐标」使用完全相同的算法。
 */
export interface LayoutBlockDescriptor {
  id: string
  /** 'code' 表示代码节点，其余按普通节点排版 */
  type?: string
  label?: string
  inputs?: LayoutPortDescriptor[]
  outputs?: LayoutPortDescriptor[]
  configs?: LayoutConfigDescriptor[]
  /** 已渲染节点的实测尺寸；提供时优先于估算值 */
  size?: { width: number; height: number }
}

export interface LayoutEdgeDescriptor {
  source: string
  target: string
}

export interface LayoutBox {
  x: number
  y: number
  width: number
  height: number
}

export interface ComputeLayoutOptions {
  /** 'LR' 从左到右（默认），'TB' 从上到下 */
  direction?: string
  nodeSeparation?: number
  rankSeparation?: number
  edgeSeparation?: number
}

/** 估算普通节点的尺寸；纯函数，不依赖 vue-flow */
function estimateBlockSize(block: LayoutBlockDescriptor): { width: number; height: number } {
  const inputs = block.inputs || []
  const outputs = block.outputs || []
  const configs = block.configs || []
  const isCodeNode = block.type === 'code'

  // 端口区左右两列并排，高度由较多的一侧决定
  const portRows = Math.max(inputs.length, outputs.length)
  let height = NODE_HEADER_HEIGHT + portRows * PORT_ROW_HEIGHT + PORTS_PADDING

  if (isCodeNode) {
    // 代码节点的主体是固定高度的代码预览区，不渲染配置项列表
    height += CODE_PREVIEW_HEIGHT
  } else if (configs.length > 0) {
    // 配置项之间还有 gap，节点越高越不能漏算，否则纵向仍会压叠
    height +=
      configs.length * CONFIG_ROW_HEIGHT + (configs.length - 1) * CONFIG_ROW_GAP + BODY_PADDING
  }
  height += NODE_BORDER + NODE_SIZE_SAFETY_MARGIN

  // 标题行：标签 + 右侧 #id 角标
  const headerWidth =
    measureTextWidth(block.label || block.id, 14) + NODE_ID_CHIP_WIDTH + HEADER_PADDING

  // 端口行：同一行的输入标签与输出标签必须都放得下
  let portsWidth = 0
  for (let index = 0; index < portRows; index += 1) {
    const input = inputs[index]
    const output = outputs[index]
    const inputWidth = input
      ? measureTextWidth(`${input.label || input.name || ''}${input.required ? ' *' : ''}`, 12) +
        PORT_LABEL_MARGIN
      : 0
    const outputWidth = output
      ? measureTextWidth(output.label || output.name || '', 12) + PORT_LABEL_MARGIN
      : 0
    portsWidth = Math.max(portsWidth, inputWidth + outputWidth + PORTS_COLUMN_GAP)
  }

  // 配置行：.config-name 只占 45%，因此整行需要的宽度是名称宽度除以该比例
  let configWidth = 0
  for (const config of configs) {
    const nameWidth = measureTextWidth(config.label || config.name || '', 12)
    configWidth = Math.max(configWidth, nameWidth / CONFIG_NAME_RATIO + CONFIG_ROW_PADDING)
  }
  if (configs.length > 0) {
    configWidth += BODY_PADDING
  }

  const minWidth = isCodeNode ? CODE_NODE_MIN_WIDTH : NODE_MIN_WIDTH
  const maxWidth = isCodeNode ? CODE_NODE_MAX_WIDTH : NODE_MAX_WIDTH
  const width = Math.min(
    maxWidth,
    Math.max(minWidth, Math.ceil(Math.max(headerWidth, portsWidth, configWidth)) + NODE_BORDER)
  )

  return { width, height: Math.ceil(height) }
}

/**
 * 估算节点渲染后的尺寸。
 *
 * 布局在 setNodes() 之后立即执行，此时 Vue 尚未把节点渲染到 DOM，
 * `graphNode.dimensions` 仍是 0，若直接回退到一个固定默认值，高节点
 * （例如带 5 个模型配置的「LLM: 执行对话」）就会被按矮节点排布而纵向压叠。
 * 这里按端口数与配置项数推算高度，让 dagre 拿到接近真实的尺寸。
 */
function estimateNodeSize(node: Node): { width: number; height: number } {
  const data: any = node.data || {}
  return estimateBlockSize({
    id: node.id,
    type: node.type,
    label: data.label,
    inputs: data.inputs || [],
    outputs: data.outputs || [],
    configs: data.blockType?.configs || []
  })
}

/** 两个盒子在给定坐标轴上是否相交 */
const overlapsOnAxis = (
  aStart: number,
  aSize: number,
  bStart: number,
  bSize: number,
  gap: number
) => aStart < bStart + bSize + gap && bStart < aStart + aSize + gap

/**
 * dagre 之后的去重叠扫描。
 *
 * dagre 只保证同一层（rank）内不重叠，一旦某个节点的实际高度大于它排版时
 * 使用的尺寸，或者用户手工拖动过、预置 YAML 里写死了坐标，相邻层之间就会
 * 出现「流程图框重叠」。这里做一次确定性的扫描-推挤：
 * 按 (主轴, 副轴, id) 排序后逐个落位，与已落位的盒子冲突就沿副轴向正方向
 * 推到刚好让开为止。排序固定 ⇒ 同样的输入永远得到同样的输出。
 *
 * @param boxes 节点盒子，会被就地修改
 * @param isHorizontal LR 布局沿 y 推挤，TB 布局沿 x 推挤
 * @param separation 推挤后至少保留的间距，默认与 dagre 的 nodesep 一致
 */
export function resolveNodeOverlaps(
  boxes: Map<string, LayoutBox>,
  isHorizontal = true,
  separation = NODE_SEPARATION
): Map<string, LayoutBox> {
  const entries = [...boxes.entries()].sort(([idA, a], [idB, b]) => {
    const majorA = isHorizontal ? a.x : a.y
    const majorB = isHorizontal ? b.x : b.y
    if (majorA !== majorB) return majorA - majorB
    const minorA = isHorizontal ? a.y : a.x
    const minorB = isHorizontal ? b.y : b.x
    if (minorA !== minorB) return minorA - minorB
    return idA < idB ? -1 : idA > idB ? 1 : 0
  })

  const placed: LayoutBox[] = []

  for (const [, box] of entries) {
    // 与已落位的盒子逐个比对；每次推挤后都要重新比对一遍，
    // 因为让开 A 之后可能撞上 B。落位顺序固定，循环必然收敛。
    let guard = placed.length * 2 + 2
    let moved = true
    while (moved && guard > 0) {
      moved = false
      guard -= 1
      for (const other of placed) {
        const majorOverlap = isHorizontal
          ? overlapsOnAxis(box.x, box.width, other.x, other.width, 0)
          : overlapsOnAxis(box.y, box.height, other.y, other.height, 0)
        if (!majorOverlap) continue

        const minorOverlap = isHorizontal
          ? overlapsOnAxis(box.y, box.height, other.y, other.height, separation)
          : overlapsOnAxis(box.x, box.width, other.x, other.width, separation)
        if (!minorOverlap) continue

        if (isHorizontal) {
          box.y = Math.round(other.y + other.height + separation)
        } else {
          box.x = Math.round(other.x + other.width + separation)
        }
        moved = true
      }
    }
    placed.push(box)
  }

  return boxes
}

/**
 * 计算一组节点的无重叠坐标。
 *
 * 供两类调用方复用：
 * 1. 画布内的 useLayout().layout()（整理布局 / 缺失坐标时自动补位）；
 * 2. 画布外的脚本 webui/scripts/relayout-presets.mjs，用来重算预置工作流
 *    YAML 里写死的坐标。因此本函数不依赖 vue-flow 运行时，只吃普通对象。
 *
 * @param blocks 节点描述；未提供 size 时按 CSS 版式估算
 * @param edges 连线，仅用于 dagre 的分层
 * @param options 方向与间距，默认 LR / 60 / 120 / 24
 * @returns 以节点 id 为键的左上角坐标与所用尺寸
 */
export function computeWorkflowLayout(
  blocks: LayoutBlockDescriptor[],
  edges: LayoutEdgeDescriptor[] = [],
  options: ComputeLayoutOptions = {}
): Record<string, LayoutBox> {
  const direction = options.direction || 'LR'
  const isHorizontal = direction === 'LR'
  const nodeSeparation = options.nodeSeparation ?? NODE_SEPARATION
  const dagreGraph = new dagre.graphlib.Graph()
  dagreGraph.setDefaultEdgeLabel(() => ({}))
  dagreGraph.setGraph({
    rankdir: direction,
    nodesep: nodeSeparation,
    ranksep: options.rankSeparation ?? RANK_SEPARATION,
    edgesep: options.edgeSeparation ?? EDGE_SEPARATION,
    // 让 dagre 在层内做重心排序，减少连线交叉
    ranker: 'network-simplex'
  })

  const sizes = new Map<string, { width: number; height: number }>()
  for (const block of blocks) {
    const size =
      block.size && block.size.width > 0 && block.size.height > 0
        ? { width: block.size.width, height: block.size.height }
        : estimateBlockSize(block)
    sizes.set(block.id, size)
    dagreGraph.setNode(block.id, { ...size })
  }

  for (const edge of edges) {
    if (sizes.has(edge.source) && sizes.has(edge.target)) {
      dagreGraph.setEdge(edge.source, edge.target)
    }
  }

  dagre.layout(dagreGraph)

  // dagre 的坐标是节点中心，vue-flow 用的是左上角，需要按各节点自身尺寸换算
  const boxes = new Map<string, LayoutBox>()
  for (const block of blocks) {
    const size = sizes.get(block.id)!
    const laidOut = dagreGraph.node(block.id)
    boxes.set(block.id, {
      x: Math.round((laidOut?.x ?? 0) - size.width / 2),
      y: Math.round((laidOut?.y ?? 0) - size.height / 2),
      width: size.width,
      height: size.height
    })
  }

  resolveNodeOverlaps(boxes, isHorizontal, nodeSeparation)

  const result: Record<string, LayoutBox> = {}
  for (const [id, box] of boxes) {
    result[id] = box
  }
  return result
}

/** 把坐标对齐到网格，避免拖放出现 1px 级别的错位 */
export const snapToGrid = (value: number, grid = LAYOUT_GRID_SIZE) =>
  Math.round(value / grid) * grid

/**
 * 从期望位置出发找一个不与既有节点重叠的落点。
 *
 * 用于拖放新节点与复制节点：期望位置可用就原样返回，否则沿对角线按网格
 * 逐步试探，找到第一个空位。返回值同样对齐到网格。
 */
export function findFreeNodePosition(
  preferred: { x: number; y: number },
  size: { width: number; height: number },
  occupied: LayoutBox[],
  options: { grid?: number; separation?: number; maxSteps?: number } = {}
): { x: number; y: number } {
  const grid = options.grid ?? LAYOUT_GRID_SIZE
  const separation = options.separation ?? 24
  const maxSteps = options.maxSteps ?? 40
  const stepX = Math.max(grid, snapToGrid(size.width / 2, grid))
  const stepY = Math.max(grid, snapToGrid(size.height / 2, grid))

  const collides = (x: number, y: number) =>
    occupied.some(
      (box) =>
        overlapsOnAxis(x, size.width, box.x, box.width, separation) &&
        overlapsOnAxis(y, size.height, box.y, box.height, separation)
    )

  let x = snapToGrid(preferred.x, grid)
  let y = snapToGrid(preferred.y, grid)
  for (let step = 0; step <= maxSteps; step += 1) {
    if (!collides(x, y)) return { x, y }
    x = snapToGrid(preferred.x, grid) + stepX * (step + 1)
    y = snapToGrid(preferred.y, grid) + stepY * (step + 1)
  }
  return { x, y }
}

/**
 * 找出当前画布上互相重叠的节点。
 *
 * 供画布把「框重叠」和校验问题一起以角标形式呈现出来，让用户知道该点
 * 「自动排布」。判定用真实渲染尺寸，尺寸缺失时退回估算值。
 */
export function findOverlappingNodes(
  boxes: { id: string; x: number; y: number; width: number; height: number }[]
): Set<string> {
  const overlapping = new Set<string>()
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i]
      const b = boxes[j]
      if (
        overlapsOnAxis(a.x, a.width, b.x, b.width, 0) &&
        overlapsOnAxis(a.y, a.height, b.y, b.height, 0)
      ) {
        overlapping.add(a.id)
        overlapping.add(b.id)
      }
    }
  }
  return overlapping
}

/**
 * Composable to run the layout algorithm on the graph.
 * It uses the `dagre` library to calculate the layout of the nodes and edges.
 */
export function useLayout() {
  const { findNode } = useVueFlow()

  const graph = ref(new dagre.graphlib.Graph())

  const previousDirection = ref('LR')

  /**
   * @param options.measured 传 true 时只认 DOM 实测尺寸（用于「整理布局」这类
   *   节点已经渲染过的场合），实测缺失才回退估算；默认沿用「有实测就用实测」。
   */
  function layout(
    nodes: Node[],
    edges: Edge[],
    direction: string,
    options: { measured?: boolean } = {}
  ) {
    const isHorizontal = direction === 'LR'
    previousDirection.value = direction

    const descriptors: LayoutBlockDescriptor[] = nodes.map((node) => {
      // if you need width+height of nodes for your layout, you can use the dimensions property of the internal node (`GraphNode` type)
      const graphNode = findNode(node.id)
      const data: any = node.data || {}
      // 已渲染过的节点用实测尺寸，未渲染的用估算值
      const measuredWidth = graphNode?.dimensions.width || 0
      const measuredHeight = graphNode?.dimensions.height || 0
      const hasMeasured = measuredWidth > 0 && measuredHeight > 0
      return {
        id: node.id,
        type: node.type,
        label: data.label,
        inputs: data.inputs || [],
        outputs: data.outputs || [],
        configs: data.blockType?.configs || [],
        size: hasMeasured ? { width: measuredWidth, height: measuredHeight } : undefined
      }
    })

    if (options.measured) {
      // 显式声明走热路径：估算值只在极端情况下（节点刚插入还没测量）兜底
      for (const descriptor of descriptors) {
        if (!descriptor.size) {
          descriptor.size = estimateBlockSize(descriptor)
        }
      }
    }

    const boxes = computeWorkflowLayout(
      descriptors,
      edges.map((edge) => ({ source: edge.source, target: edge.target })),
      { direction }
    )

    // 保留原有的 graph 引用语义：调用方可以拿到最近一次布局的 dagre 图
    const dagreGraph = new dagre.graphlib.Graph()
    dagreGraph.setDefaultEdgeLabel(() => ({}))
    for (const [id, box] of Object.entries(boxes)) {
      dagreGraph.setNode(id, { ...box })
    }
    for (const edge of edges) {
      dagreGraph.setEdge(edge.source, edge.target)
    }
    graph.value = dagreGraph

    // set nodes with updated positions
    return nodes.map((node) => {
      const box = boxes[node.id]

      return {
        ...node,
        targetPosition: isHorizontal ? Position.Left : Position.Top,
        sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
        position: {
          x: box ? box.x : node.position.x,
          y: box ? box.y : node.position.y
        }
      }
    })
  }

  return { graph, layout, previousDirection }
}
