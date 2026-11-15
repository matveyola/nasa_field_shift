import assert from 'node:assert/strict'
import test from 'node:test'
import { contextHelpPlacement } from '../src/shared/contextHelpPlacement.ts'

test('a short explanation opens next to its trigger with a continuous pointer path', () => {
  const anchor = { left: 470, top: 100, width: 28, bottom: 128 }
  const panel = { width: 320, contentHeight: 74 }
  const placement = contextHelpPlacement(anchor, { left: 0, top: 0, width: 900, height: 600 }, panel)

  assert.equal(placement.side, 'below')
  assert.equal(placement.top, anchor.bottom)
  assert.ok(placement.left <= anchor.left)
  assert.ok(placement.left + panel.width >= anchor.left + anchor.width)
  assert.equal(placement.contentHeight, panel.contentHeight)
})

test('an explanation beside the phone bottom and right edge opens above and shifts in', () => {
  const viewport = { left: 40, top: 10, width: 320, height: 400 }
  const anchor = { left: 311, top: 349, width: 44, bottom: 393 }
  const panel = { width: 304, contentHeight: 93 }
  const placement = contextHelpPlacement(anchor, viewport, panel)

  assert.equal(placement.side, 'above')
  assert.ok(placement.left >= viewport.left)
  assert.ok(placement.left + panel.width < viewport.left + viewport.width)
  assert.ok(placement.top >= viewport.top)
  assert.ok(placement.top + placement.contentHeight < anchor.top)
})

test('a long explanation scrolls within the larger side of a short viewport', () => {
  const viewport = { left: 0, top: 0, width: 320, height: 200 }
  const anchor = { left: 0, top: 65, width: 44, bottom: 109 }
  const placement = contextHelpPlacement(anchor, viewport, { width: 304, contentHeight: 800 })

  assert.equal(placement.side, 'below')
  assert.equal(placement.top, anchor.bottom)
  assert.ok(placement.contentHeight > 0)
  assert.ok(placement.top + placement.contentHeight < viewport.height)
})
