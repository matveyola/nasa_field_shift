const margin = 8
const gap = 6

type Viewport = { left: number; top: number; width: number; height: number }
type Anchor = { left: number; top: number; width: number; bottom: number }

export function contextHelpPlacement(
  anchor: Anchor,
  viewport: Viewport,
  panel: { width: number; contentHeight: number },
) {
  const minLeft = viewport.left + margin
  const maxLeft = viewport.left + viewport.width - margin - panel.width
  const above = Math.max(0, anchor.top - viewport.top - margin)
  const below = Math.max(0, viewport.top + viewport.height - margin - anchor.bottom)
  const preferredHeight = Math.min(panel.contentHeight + gap, viewport.height * 0.6)
  const opensBelow = below >= preferredHeight || below >= above
  const height = Math.min(preferredHeight, opensBelow ? below : above)

  return {
    side: opensBelow ? 'below' : 'above',
    left: Math.max(minLeft, Math.min(maxLeft, anchor.left + (anchor.width - panel.width) / 2)),
    top: opensBelow ? anchor.bottom : anchor.top - height,
    contentHeight: Math.max(0, height - gap),
  }
}
