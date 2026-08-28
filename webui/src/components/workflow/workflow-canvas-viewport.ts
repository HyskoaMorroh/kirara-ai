import type { Rect } from '@vue-flow/core'

export interface CanvasRect {
  left: number
  top: number
  right: number
  bottom: number
  width: number
  height: number
}

export interface CanvasInsets {
  top: number
  right: number
  bottom: number
  left: number
}

export interface ViewportTransform {
  x: number
  y: number
  zoom: number
}

export interface FitViewTransformOptions {
  padding?: number
  minZoom: number
  maxZoom: number
}

// Vue Flow's default lower bound leaves narrow mobile canvases unable to show
// a useful horizontal workflow after edge panels are accounted for.
export const WORKFLOW_CANVAS_MIN_ZOOM = 0.05

const EDGE_COVERAGE_THRESHOLD = 0.5
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)
const touches = (first: number, second: number, tolerance: number) =>
  Math.abs(first - second) <= tolerance

/** Return the canvas area occupied by full-height or full-width edge panels. */
export function getCanvasOverlayInsets(
  canvas: CanvasRect,
  overlays: CanvasRect[],
  edgeTolerance = 1
): CanvasInsets {
  const insets: CanvasInsets = { top: 0, right: 0, bottom: 0, left: 0 }

  for (const overlay of overlays) {
    const isVerticalPanel = overlay.height >= canvas.height * EDGE_COVERAGE_THRESHOLD
    const isHorizontalPanel = overlay.width >= canvas.width * EDGE_COVERAGE_THRESHOLD

    if (isVerticalPanel && touches(overlay.left, canvas.left, edgeTolerance)) {
      insets.left = Math.max(insets.left, overlay.right - canvas.left)
    }
    if (isVerticalPanel && touches(overlay.right, canvas.right, edgeTolerance)) {
      insets.right = Math.max(insets.right, canvas.right - overlay.left)
    }
    if (isHorizontalPanel && touches(overlay.top, canvas.top, edgeTolerance)) {
      insets.top = Math.max(insets.top, overlay.bottom - canvas.top)
    }
    if (isHorizontalPanel && touches(overlay.bottom, canvas.bottom, edgeTolerance)) {
      insets.bottom = Math.max(insets.bottom, canvas.bottom - overlay.top)
    }
  }

  return insets
}

/** Fit graph bounds into the canvas space that remains visible around edge panels. */
export function getInsetFitViewTransform(
  bounds: Rect,
  canvas: Pick<CanvasRect, 'width' | 'height'>,
  insets: CanvasInsets,
  options: FitViewTransformOptions
): ViewportTransform {
  const padding = options.padding ?? 0.1
  const usableWidth = Math.max(1, canvas.width - insets.left - insets.right)
  const usableHeight = Math.max(1, canvas.height - insets.top - insets.bottom)
  const xZoom = usableWidth / (Math.max(1, bounds.width) * (1 + padding))
  const yZoom = usableHeight / (Math.max(1, bounds.height) * (1 + padding))
  const zoom = clamp(Math.min(xZoom, yZoom), options.minZoom, options.maxZoom)
  const boundsCenterX = bounds.x + bounds.width / 2
  const boundsCenterY = bounds.y + bounds.height / 2
  const usableCenterX = insets.left + usableWidth / 2
  const usableCenterY = insets.top + usableHeight / 2

  return {
    x: usableCenterX - boundsCenterX * zoom,
    y: usableCenterY - boundsCenterY * zoom,
    zoom
  }
}
