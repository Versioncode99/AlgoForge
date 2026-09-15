/* A chart inside a conversation turn, drawn from what was actually computed.
 *
 * D4 §4F asks the chat for quantitative visual artifacts and draws the line in
 * the same paragraph: *"The visualization must be generated from actual
 * computed data. Never generate fake visual data simply to make the chat look
 * impressive."*
 *
 * So every chart here is the result of a fetch from the route that produced the
 * number, rendered by the same component the surface renders it with. An
 * artifact with nothing to fetch gets no chart and no placeholder; a fetch that
 * fails says so and offers the link out, because a conversation that quietly
 * drew something else would be worse than one that drew nothing.
 *
 * Collapsed by default. A thread is a record of a session's work and a wall of
 * charts is not readable; the reader asks for the one they want.
 *
 * **The renderers are loaded when a chart is opened, not when the chat is.**
 * They used to be static imports, which put ECharts and the canvas renderer —
 * about 1.2 MB of JavaScript between them — into the module graph of every
 * conversation, including one that never mentions a chart. Opening Chat paid
 * for the entire visualisation stack before the composer worked. Each one is
 * behind `lazy` now, so the cost arrives when somebody presses "Show the
 * chart", which is the only moment it is worth anything.
 */
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { getJson } from '../api'
import type { Artifact } from '../chat'
import {
  bandFromResample,
  equityFromTrades,
  regimeCells,
  visualFor,
  type Visual,
} from '../chat-visuals'
import type { AnalysisResult } from './AnalysisChart'
import type { RegimeCell } from './RegimeMatrix'

const AnalysisChart = lazy(() =>
  import('./AnalysisChart').then((m) => ({ default: m.AnalysisChart })))
const RegimeMatrix = lazy(() =>
  import('./RegimeMatrix').then((m) => ({ default: m.RegimeMatrix })))
const CurveChart = lazy(() => import('../charts').then((m) => ({ default: m.CurveChart })))
const PathBand = lazy(() => import('../charts').then((m) => ({ default: m.PathBand })))

export function ArtifactVisual({ artifact }: { artifact: Artifact }) {
  const visual = visualFor(artifact)
  const [open, setOpen] = useState(false)
  if (!visual) return null
  return (
    <div className="art-visual">
      <button
        type="button"
        className="art-visual-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {open ? 'Hide the chart' : 'Show the chart'}
      </button>
      {open && <Drawn visual={visual} />}
    </div>
  )
}

function Drawn({ visual }: { visual: Visual }) {
  const query = useQuery({
    queryKey: ['artifact-visual', visual.path],
    queryFn: () => getJson<unknown>(visual.path),
    retry: false,
    staleTime: 60_000,
  })

  if (query.isPending) return <p className="art-visual-state">Fetching what was computed…</p>
  if (query.isError) {
    return (
      <p className="art-visual-state">
        This could not be fetched: {(query.error as Error).message}. Nothing is drawn rather
        than something approximate — open the surface instead.
      </p>
    )
  }

  const body = drawFor(visual, query.data)
  if (body === null) {
    return (
      <p className="art-visual-state">
        The route answered with nothing to draw. That is a real answer: the analysis behind
        this reference has no cells.
      </p>
    )
  }

  return (
    <div className="art-visual-body">
      <Suspense fallback={<p className="art-visual-state">Loading the renderer…</p>}>
        {body}
      </Suspense>
      <p className="art-visual-caption">{visual.caption}</p>
    </div>
  )
}

/** The chart for one kind, or null when the payload carries nothing to draw. */
function drawFor(visual: Visual, payload: unknown): React.ReactNode | null {
  if (visual.kind === 'analysis') {
    const result = payload as AnalysisResult
    /* Shape-checked rather than trusted.
     *
     * `AnalysisChart` reads `axes` and `cells` without guarding either, which
     * is correct where it is handed a result the same screen just fetched. Here
     * the payload comes from whichever route an artifact's reference named, and
     * a conversation panel that throws because one answered oddly is worse than
     * one that says it could not draw -- the whole thread goes with it. */
    if (!Array.isArray(result?.axes) || !Array.isArray(result?.cells) || !result.cells.length) {
      return null
    }
    return <AnalysisChart result={result} onPick={() => undefined} height={300} />
  }
  if (visual.kind === 'band') {
    const band = bandFromResample(payload)
    if (!band || !Array.isArray(band.median) || band.median.length < 2) return null
    return <PathBand p05={band.p05} median={band.median} p95={band.p95} height={240} />
  }
  if (visual.kind === 'regime') {
    const cells = regimeCells(payload) as RegimeCell[]
    if (!cells.length || !cells.every((cell) => typeof cell?.regime === 'string')) return null
    return <RegimeMatrix cells={cells} />
  }
  const equity = equityFromTrades(payload)
  if (equity.length < 2) return null
  return <CurveChart equity={equity} height={220} />
}
