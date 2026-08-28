import { describe, expect, it } from 'vitest'

import {
  getCanvasOverlayInsets,
  getInsetFitViewTransform,
  WORKFLOW_CANVAS_MIN_ZOOM
} from '../src/components/workflow/workflow-canvas-viewport'

describe('workflow canvas viewport fitting', () => {
  it('keeps fitted nodes to the right of the visible node list', () => {
    const canvas = { left: 0, top: 0, right: 1440, bottom: 872, width: 1440, height: 872 }
    const nodeList = { left: 0, top: 0, right: 340, bottom: 872, width: 340, height: 872 }
    const insets = getCanvasOverlayInsets(canvas, [nodeList])
    const bounds = { x: 0, y: 0, width: 1600, height: 240 }

    const transform = getInsetFitViewTransform(bounds, canvas, insets, {
      minZoom: 0.2,
      maxZoom: 4,
      padding: 0.2
    })

    expect(insets).toEqual({ top: 0, right: 0, bottom: 0, left: 340 })
    expect(transform.x + bounds.x * transform.zoom).toBeGreaterThanOrEqual(insets.left)
    expect(transform.x + (bounds.x + bounds.width) * transform.zoom).toBeLessThanOrEqual(
      canvas.width - insets.right
    )
  })

  it('accounts for panels anchored to both sides of the canvas', () => {
    const canvas = { left: 10, top: 20, right: 1210, bottom: 820, width: 1200, height: 800 }
    const nodeList = { left: 10, top: 20, right: 310, bottom: 820, width: 300, height: 800 }
    const inspector = {
      left: 810,
      top: 20,
      right: 1210,
      bottom: 820,
      width: 400,
      height: 800
    }

    expect(getCanvasOverlayInsets(canvas, [nodeList, inspector])).toEqual({
      top: 0,
      right: 400,
      bottom: 0,
      left: 300
    })
  })

  it('ignores floating overlays that are not attached to a canvas edge', () => {
    const canvas = { left: 0, top: 0, right: 1000, bottom: 700, width: 1000, height: 700 }
    const toolbar = { left: 300, top: 16, right: 700, bottom: 64, width: 400, height: 48 }

    expect(getCanvasOverlayInsets(canvas, [toolbar])).toEqual({
      top: 0,
      right: 0,
      bottom: 0,
      left: 0
    })
  })

  it('fits a horizontal workflow above the mobile bottom node list', () => {
    const canvas = { left: 0, top: 0, right: 360, bottom: 772, width: 360, height: 772 }
    const nodeList = { left: 0, top: 512, right: 360, bottom: 772, width: 360, height: 260 }
    const insets = getCanvasOverlayInsets(canvas, [nodeList])
    const bounds = { x: 0, y: 0, width: 1828, height: 295 }

    const transform = getInsetFitViewTransform(bounds, canvas, insets, {
      minZoom: WORKFLOW_CANVAS_MIN_ZOOM,
      maxZoom: 4,
      padding: 0.2
    })

    expect(WORKFLOW_CANVAS_MIN_ZOOM).toBeLessThan(0.2)
    expect(insets).toEqual({ top: 0, right: 0, bottom: 260, left: 0 })
    expect(transform.x + bounds.x * transform.zoom).toBeGreaterThanOrEqual(0)
    expect(transform.x + (bounds.x + bounds.width) * transform.zoom).toBeLessThanOrEqual(360)
    expect(transform.y + bounds.y * transform.zoom).toBeGreaterThanOrEqual(0)
    expect(transform.y + (bounds.y + bounds.height) * transform.zoom).toBeLessThanOrEqual(512)
  })
})
