import { useCallback, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderCog, Plus, Trash2, X } from 'lucide-react'
import { API, getJson } from '../api'
import { playSound } from '../sound'
import { PanelBody } from '../components/PanelBody'
import { symbolFor, useWorkstationContext } from '../workstation'
import { WorkspaceManager } from '../components/WorkspaceManager'
import type { DatasetInfo } from '../types'

/* The workstation itself: panels on a grid, arranged by the operator.
 *
 * Every mutation here goes through the same HTTP actions the agent calls. There
 * is no local layout state that the backend does not know about, and no
 * client-only path for moving a panel — so "put the DOM under the chart" typed
 * at the agent and dragging it there by hand end in the same call, and the two
 * cannot drift.
 */

const GRID_COLUMNS = 12
const ROW_HEIGHT = 44

type Panel = {
  panel_id: string
  kind: string
  title: string
  x: number
  y: number
  width: number
  height: number
  settings: Record<string, unknown>
  link_group: string | null
  collapsed: boolean
}

type Workspace = {
  workspace_id: string
  name: string
  template_key: string | null
  updated_at: string
  panels: Panel[]
}

type WorkspaceSummary = {
  workspace_id: string
  name: string
  template_key: string | null
  panel_count: number
}

type TemplateInfo = { key: string; name: string; summary: string; panel_count: number }

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const reason = payload?.detail?.reason ?? payload?.detail?.code ?? response.statusText
    throw new Error(String(reason))
  }
  return payload.data as T
}

const PANEL_KINDS = [
  'chart', 'watchlist', 'strategies', 'experiments', 'runs', 'validation',
  'evidence', 'research_memory', 'lineage', 'data_health', 'research_library',
  'dom', 'order_ticket', 'positions', 'orders', 'account', 'risk', 'prop',
  'replay', 'agent', 'activity', 'logs', 'notes',
]

export function WorkspaceView() {
  const client = useQueryClient()
  const surface = useRef<HTMLDivElement | null>(null)
  const [adding, setAdding] = useState(false)
  const [managing, setManaging] = useState(false)

  const active = useQuery({
    queryKey: ['workspace-active'],
    queryFn: () => getJson<Workspace | null>('/workspaces/active'),
  })
  const list = useQuery({
    queryKey: ['workspace-list'],
    queryFn: () => getJson<{ workspaces: WorkspaceSummary[]; active: string | null }>('/workspaces'),
  })
  const templates = useQuery({
    queryKey: ['workspace-templates'],
    queryFn: () => getJson<{ templates: TemplateInfo[] }>('/workspaces/templates'),
  })
  const datasets = useQuery({
    queryKey: ['datasets'],
    queryFn: () => getJson<DatasetInfo[]>('/datasets'),
  })

  const refresh = useCallback(() => {
    void client.invalidateQueries({ queryKey: ['workspace-active'] })
    void client.invalidateQueries({ queryKey: ['workspace-list'] })
  }, [client])

  const workspace = active.data ?? null
  const id = workspace?.workspace_id

  /* What each panel actually shows.
   *
   * `link_group` used to be a label attached to nothing: the field was
   * written, this view drew it as a badge, and no code made any panel follow
   * anything. Resolution happens here, at render, and writes nothing — so a
   * panel pinned to MNQ keeps its MNQ in the record while a linked neighbour
   * follows the workspace onto NQ, and unlinking brings MNQ straight back.
   */
  const context = useWorkstationContext()
  const resolved = context.data?.panels ?? []
  const resolvedFor = useCallback(
    (panelId: string) => symbolFor(panelId, resolved),
    [resolved],
  )
  const titleFor = useCallback(
    (panel: { panel_id: string; title: string }) => {
      const shown = symbolFor(panel.panel_id, resolved)
      if (!shown || shown.source !== 'context' || !shown.symbol) return panel.title
      return `${shown.symbol} ${shown.timeframe}`.trim()
    },
    [resolved],
  )
  const settingsFor = useCallback(
    (panel: { panel_id: string; settings: Record<string, unknown> }) => {
      const shown = symbolFor(panel.panel_id, resolved)
      if (!shown || shown.source !== 'context') return panel.settings
      // Only the facets the context actually sets are overlaid. A context with
      // no timeframe must not blank a panel's own.
      return {
        ...panel.settings,
        ...(shown.symbol ? { symbol: shown.symbol } : {}),
        ...(shown.timeframe ? { timeframe: shown.timeframe } : {}),
        // The panel's stored dataset would win over the followed symbol in
        // `PanelBody`, which resolves `settings.dataset` first. Dropping it
        // here is what makes following actually change the chart.
        dataset: '',
      }
    },
    [resolved],
  )

  const mutate = useMutation({
    mutationFn: (job: { path: string; method: string; body?: unknown }) =>
      send<Workspace>(job.path, job.method, job.body),
    // The layout is now what the server holds. That is the state change worth
    // confirming, which is why the sound is here and not on pointer-down.
    onSuccess: () => {
      playSound('workspace.save')
      refresh()
    },
    onError: () => playSound('error'),
  })

  const create = useMutation({
    mutationFn: (template_key: string) =>
      send<Workspace>('/workspaces', 'POST', {
        name: templates.data?.templates.find((t) => t.key === template_key)?.name ?? 'Workspace',
        template_key,
        activate: true,
      }),
    onSuccess: () => {
      playSound('workspace.switch')
      refresh()
    },
    onError: () => playSound('error'),
  })

  // Geometry is committed on drop, not on every pointer move: one action per
  // gesture keeps the activity log readable and the server from seeing a
  // hundred intermediate positions that were never intended.
  const drag = useRef<{ panel: Panel; startX: number; startY: number; mode: 'move' | 'resize' } | null>(null)
  const [preview, setPreview] = useState<Record<string, { x: number; y: number; width: number; height: number }>>({})

  const columnWidth = () => (surface.current?.clientWidth ?? GRID_COLUMNS * 80) / GRID_COLUMNS

  const onPointerDown = (panel: Panel, mode: 'move' | 'resize') => (event: React.PointerEvent) => {
    if (event.button !== 0) return
    event.preventDefault()
    ;(event.target as HTMLElement).setPointerCapture(event.pointerId)
    drag.current = { panel, startX: event.clientX, startY: event.clientY, mode }
  }

  const onPointerMove = (event: React.PointerEvent) => {
    const state = drag.current
    if (!state) return
    const dx = Math.round((event.clientX - state.startX) / columnWidth())
    const dy = Math.round((event.clientY - state.startY) / ROW_HEIGHT)
    const p = state.panel
    const next =
      state.mode === 'move'
        ? {
            x: Math.max(0, Math.min(GRID_COLUMNS - p.width, p.x + dx)),
            y: Math.max(0, p.y + dy),
            width: p.width,
            height: p.height,
          }
        : {
            x: p.x,
            y: p.y,
            width: Math.max(2, Math.min(GRID_COLUMNS - p.x, p.width + dx)),
            height: Math.max(3, p.height + dy),
          }
    setPreview((current) => ({ ...current, [p.panel_id]: next }))
  }

  const onPointerUp = () => {
    const state = drag.current
    drag.current = null
    if (!state || !id) return
    const next = preview[state.panel.panel_id]
    setPreview({})
    if (!next) return
    const unchanged =
      next.x === state.panel.x &&
      next.y === state.panel.y &&
      next.width === state.panel.width &&
      next.height === state.panel.height
    if (unchanged) return
    mutate.mutate({
      path: `/workspaces/${id}/panels/${state.panel.panel_id}/geometry`,
      method: 'POST',
      body: state.mode === 'move'
        ? { x: next.x, y: next.y }
        : { width: next.width, height: next.height },
    })
  }

  const rows = useMemo(() => {
    if (!workspace) return 0
    return Math.max(12, ...workspace.panels.map((p) => p.y + p.height))
  }, [workspace])

  if (active.isPending || templates.isPending) {
    return <div className="state" role="status">Opening workspace…</div>
  }
  if (active.isError) {
    return <div className="state error" role="alert">{(active.error as Error).message}</div>
  }

  // No workspace is a real state with a real answer, not an excuse to conjure
  // one. Templates are offered; none is imposed.
  if (!workspace) {
    return (
      <div className="workspace-empty">
        <h2>No workspace open</h2>
        <p>Start from a template, or from nothing. Neither locks anything: a workspace can be changed into any other afterwards.</p>
        <ul className="template-grid">
          {(templates.data?.templates ?? []).map((template) => (
            <li key={template.key}>
              <button type="button" onClick={() => create.mutate(template.key)} disabled={create.isPending}>
                <strong>{template.name}</strong>
                <span>{template.summary}</span>
                <span className="count mono">{template.panel_count} panels</span>
              </button>
            </li>
          ))}
        </ul>
        {create.isError && <p className="state error" role="alert">{(create.error as Error).message}</p>}
      </div>
    )
  }

  return (
    <div className="workspace" data-managing={managing ? 'yes' : undefined}>
      {managing && (
        <WorkspaceManager activeId={workspace.workspace_id} onClose={() => setManaging(false)} />
      )}
      <header className="workspace-bar">
        <label className="ctl">
          <span>Workspace</span>
          <select
            aria-label="Active workspace"
            value={workspace.workspace_id}
            onChange={(event) => mutate.mutate({ path: `/workspaces/${event.target.value}/open`, method: 'POST' })}
          >
            {(list.data?.workspaces ?? []).map((item) => (
              <option key={item.workspace_id} value={item.workspace_id}>
                {item.name} · {item.panel_count} panels
              </option>
            ))}
          </select>
        </label>

        <button type="button" className="ghost" onClick={() => setAdding((value) => !value)} aria-expanded={adding}>
          <Plus size={13} /> Add panel
        </button>

        <button
          type="button"
          className="ghost"
          onClick={() => setManaging((value) => !value)}
          aria-expanded={managing}
        >
          <FolderCog size={13} /> Manage
        </button>

        <span className="workspace-hint">Drag a header to move, drag the corner to resize.</span>
      </header>

      {adding && (
        <div className="panel-picker" role="group" aria-label="Panel kinds">
          {PANEL_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => {
                mutate.mutate({ path: `/workspaces/${workspace.workspace_id}/panels`, method: 'POST', body: { kind } })
                setAdding(false)
              }}
            >
              {kind.replace('_', ' ')}
            </button>
          ))}
        </div>
      )}

      {mutate.isError && (
        <p className="state error" role="alert">{(mutate.error as Error).message}</p>
      )}

      {workspace.panels.length === 0 ? (
        <div className="workspace-blank">
          <p>This workspace is empty. Add a panel to begin.</p>
        </div>
      ) : (
        <div
          ref={surface}
          className="workspace-grid"
          style={{ gridTemplateRows: `repeat(${rows}, ${ROW_HEIGHT}px)` }}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          {workspace.panels.map((panel) => {
            const shown = preview[panel.panel_id] ?? panel
            return (
              <section
                key={panel.panel_id}
                className={`wpanel${preview[panel.panel_id] ? ' dragging' : ''}`}
                style={{
                  gridColumn: `${shown.x + 1} / span ${shown.width}`,
                  gridRow: `${shown.y + 1} / span ${shown.height}`,
                }}
                aria-label={`${panel.title} panel`}
              >
                <header className="wpanel-head" onPointerDown={onPointerDown(panel, 'move')}>
                  {/* The title follows what is *shown*, not what is stored.
                    * Caught in QA on the running application: a panel pinned to
                    * MNQ and linked to a context on NQ drew "MNQ 5m" over a
                    * body reading "no archive for NQ". A header that disagrees
                    * with its own body is worse than either being wrong. */}
                  <span className="wpanel-title">{titleFor(panel)}</span>
                  {/* The badge now names something that happens. It used to be
                    * a label attached to nothing: the field was written, the
                    * badge was drawn, and no code made any panel follow any
                    * other. `title` says what the panel is actually showing
                    * and where that came from. */}
                  {panel.link_group && (
                    <span
                      className="wpanel-link mono"
                      title={
                        resolvedFor(panel.panel_id)?.source === 'context'
                          ? `Following the "${panel.link_group}" context: ${resolvedFor(panel.panel_id)?.symbol}`
                          : `In the "${panel.link_group}" group. Its context sets no instrument, so this panel shows its own.`
                      }
                    >
                      {panel.link_group}
                    </span>
                  )}
                  <button
                    type="button"
                    aria-label={`Remove ${panel.title}`}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() =>
                      mutate.mutate({
                        path: `/workspaces/${workspace.workspace_id}/panels/${panel.panel_id}`,
                        method: 'DELETE',
                      })
                    }
                  >
                    <X size={12} />
                  </button>
                </header>

                <div className="wpanel-body">
                  <PanelBody
                    kind={panel.kind}
                    /* Resolved, not stored. A panel in a link group displays
                     * the group's context; a panel outside one displays its
                     * own settings. Nothing is written either way, so
                     * unlinking reveals the symbol it was pinned to. */
                    settings={settingsFor(panel)}
                    datasets={datasets.data ?? []}
                    onSetting={(key, value) =>
                      mutate.mutate({
                        path: `/workspaces/${workspace.workspace_id}/panels/${panel.panel_id}/settings`,
                        method: 'POST',
                        body: { key, value },
                      })
                    }
                  />
                </div>

                <span
                  className="wpanel-resize"
                  role="separator"
                  aria-label={`Resize ${panel.title}`}
                  onPointerDown={onPointerDown(panel, 'resize')}
                />
              </section>
            )
          })}
        </div>
      )}

      <footer className="workspace-foot mono">
        <span>{workspace.panels.length} panels</span>
        {workspace.template_key && <span>from {workspace.template_key}</span>}
        <button
          type="button"
          className="danger"
          onClick={() => {
            // A CONFIRM-tier action: the operator answers, not the interface.
            if (window.confirm(`Delete workspace "${workspace.name}"? Research is not affected.`)) {
              mutate.mutate({ path: `/workspaces/${workspace.workspace_id}`, method: 'DELETE' })
            }
          }}
        >
          <Trash2 size={12} /> Delete workspace
        </button>
      </footer>
    </div>
  )
}
