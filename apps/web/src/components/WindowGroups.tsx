/* The windows the shell has open, and what belongs with what.
 *
 * The main process has tracked window groups for some time and nothing could
 * form one: the registry, the IPC channel and the session snapshot were all
 * there, reachable only by a renderer nobody had written. This is that
 * renderer.
 *
 * A group here is membership rather than geometry -- the shell records which
 * windows belong together so closing and restoring treat them as a unit, and
 * where they sit on screen stays the operator's business and the window
 * manager's. Saying so in the surface matters: a control labelled "group" that
 * does not move anything is confusing until you know that is the point.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, Unlink } from 'lucide-react'
import { useState } from 'react'
import { canOpenWindows, groupWindows, shellWindows, ungroupWindow } from '../desktop'
import { describeGroup, groupings, loose, readSelection } from '../windowgroups'

/** Polled rather than pushed: window lifecycle events reach the shell, not the
 *  page, and a stale list is a list that offers to group a window that closed.
 *  Five seconds is slow enough to be free and quick enough that a window opened
 *  in another workspace appears before somebody goes looking for it. */
const REFRESH_MS = 5000

export function WindowGroups({ names }: { names: Map<string, string> }) {
  const client = useQueryClient()
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [note, setNote] = useState<string | null>(null)

  const shell = useQuery({
    queryKey: ['shell-windows'],
    queryFn: () => shellWindows(),
    refetchInterval: REFRESH_MS,
    enabled: canOpenWindows(),
  })

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['shell-windows'] })
  }

  const compose = useMutation({
    mutationFn: (ids: number[]) => groupWindows(ids),
    onSuccess: (groupId) => {
      setNote(
        groupId
          ? 'Grouped. They close and restore together now.'
          : 'The shell refused that grouping — one of those windows may have closed.',
      )
      setSelected(new Set())
      refresh()
    },
  })

  const separate = useMutation({
    mutationFn: (windowId: number) => ungroupWindow(windowId),
    onSuccess: (done) => {
      setNote(done ? 'Taken out of its group.' : 'The shell refused that.')
      refresh()
    },
  })

  if (!canOpenWindows()) {
    return (
      <p className="state">
        Window groups need the desktop application. In a browser a workspace opens in place,
        and there are no OS windows to compose.
      </p>
    )
  }

  const windows = shell.data?.windows ?? []
  const self = shell.data?.self ?? null
  const state = readSelection(windows, selected)
  const composed = groupings(windows)
  const single = loose(windows)

  const toggle = (windowId: number) => {
    setNote(null)
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(windowId)) next.delete(windowId)
      else next.add(windowId)
      return next
    })
  }

  const label = (workspaceId: string | null) =>
    workspaceId ? (names.get(workspaceId) ?? workspaceId) : 'an empty window'

  const row = (windowId: number, workspaceId: string | null) => (
    <li key={windowId} className="wg-row">
      <label>
        <input
          type="checkbox"
          checked={selected.has(windowId)}
          onChange={() => toggle(windowId)}
        />
        <span className="wg-name">{label(workspaceId)}</span>
      </label>
      {windowId === self && <span className="wg-self">this window</span>}
    </li>
  )

  return (
    <section className="wg" aria-label="Window groups">
      <header className="wg-head">
        <h3>Windows</h3>
        <span className="wg-count">
          {windows.length === 1 ? '1 window' : `${windows.length} windows`}
        </span>
      </header>

      {windows.length === 0 && <p className="state">No windows reported by the shell.</p>}

      {composed.map((group) => (
        <div key={group.groupId} className="wg-group">
          <div className="wg-group-head">
            <strong>{describeGroup(group, names)}</strong>
            <span className="mono wg-id">{group.groupId}</span>
          </div>
          <ul className="wg-list">
            {group.windows.map((window) => (
              <li key={window.windowId} className="wg-row">
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(window.windowId)}
                    onChange={() => toggle(window.windowId)}
                  />
                  <span className="wg-name">{label(window.workspaceId)}</span>
                </label>
                {window.windowId === self && <span className="wg-self">this window</span>}
                <button
                  type="button"
                  className="ghost"
                  aria-label={`Take ${label(window.workspaceId)} out of its group`}
                  disabled={separate.isPending}
                  onClick={() => separate.mutate(window.windowId)}
                >
                  <Unlink size={13} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}

      {single.length > 0 && (
        <div className="wg-group">
          <div className="wg-group-head"><strong>Not grouped</strong></div>
          <ul className="wg-list">
            {single.map((window) => row(window.windowId, window.workspaceId))}
          </ul>
        </div>
      )}

      <div className="wg-actions">
        <button
          type="button"
          className="btn"
          disabled={!state.canGroup || compose.isPending}
          onClick={() => compose.mutate(state.ids)}
        >
          <Link2 size={13} /> Group selected
        </button>
        {/* The rule the main process will apply, stated before it applies it.
            A refused button that says nothing is indistinguishable from one
            that is broken. */}
        {state.reason && <span className="wg-reason">{state.reason}</span>}
      </div>

      {note && <p className="state" role="status">{note}</p>}

      <p className="wg-note">
        Grouping records that these windows belong together, so the shell closes and restores them
        as one arrangement. It does not move them: where they sit is yours and your window
        manager's.
      </p>
    </section>
  )
}
