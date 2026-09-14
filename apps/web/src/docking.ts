/* What a drop means, decided away from the pointer handler that produces it.
 *
 * Dragging a panel can end in two different operations, and which one the
 * operator gets is the whole of the docking gesture: released over open grid it
 * is a move, released over the middle of another panel it is a tab. Getting
 * that boundary wrong is not a crash — it is a panel that silently swallows
 * another one when the operator meant to put it alongside.
 *
 * So the decision is a pure function of the pointer position and the panels
 * underneath it. The handler in `Workspace.tsx` does pointers; this does
 * meaning; and the meaning can be tested without a DOM, a drag, or a clock.
 */

/** A panel's rectangle in grid units, which is how the layout is stored. */
export type Rect = {
  panel_id: string
  x: number
  y: number
  width: number
  height: number
}

/** What the release should do. */
export type Drop =
  | { kind: 'move'; x: number; y: number }
  | { kind: 'stack'; onto: string }

/**
 * How much of a panel counts as its middle.
 *
 * The middle is the tab target; the border ring is not. A small ring would make
 * tabbing the default outcome of any sloppy drop, and a large one would make it
 * unreachable — 0.5 puts the boundary a quarter of the way in from each edge,
 * so an operator aiming at a panel gets a tab and one aiming past it does not.
 */
export const CENTRE = 0.5

function contains(rect: Rect, x: number, y: number): boolean {
  return x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height
}

/** Whether a point is in the middle region of a rectangle rather than its ring. */
export function inCentre(rect: Rect, x: number, y: number, ratio = CENTRE): boolean {
  if (!contains(rect, x, y)) return false
  const insetX = (rect.width * (1 - ratio)) / 2
  const insetY = (rect.height * (1 - ratio)) / 2
  return (
    x >= rect.x + insetX &&
    x < rect.x + rect.width - insetX &&
    y >= rect.y + insetY &&
    y < rect.y + rect.height - insetY
  )
}

/**
 * What releasing at (x, y) should do.
 *
 * `others` is every panel except the one being dragged — a panel cannot be
 * tabbed onto itself, and passing it in would make the common case of a small
 * nudge read as a stack.
 *
 * Later panels win when rectangles overlap, because later is drawn on top and
 * the operator is aiming at what they can see.
 */
export function dropAt(x: number, y: number, others: readonly Rect[]): Drop {
  for (let index = others.length - 1; index >= 0; index -= 1) {
    const candidate = others[index]
    if (inCentre(candidate, x, y)) return { kind: 'stack', onto: candidate.panel_id }
  }
  return { kind: 'move', x, y }
}

/**
 * The panel a drop would tab onto, for drawing the highlight mid-drag.
 *
 * Separate from `dropAt` so the preview cannot disagree with the outcome: both
 * read the same rule, rather than the indicator having its own idea of where
 * the middle is.
 */
export function stackTarget(x: number, y: number, others: readonly Rect[]): string | null {
  const drop = dropAt(x, y, others)
  return drop.kind === 'stack' ? drop.onto : null
}
