/* The workspace manager: the desks a person owns, and what they can do to them.
 *
 * Every button here calls the same HTTP action the agent calls. There is no
 * manager-only endpoint and no client-side layout state the server does not
 * know about, which is what keeps "duplicate this for ES" typed at the
 * assistant and clicked here from drifting apart.
 *
 * Two distinctions the surface has to carry, because the backend went to
 * trouble to keep them:
 *
 * **Open is not default.** Opening a workspace is for now; the default is what
 * greets you on a cold start. They are separate controls because conflating
 * them means a desk opened once to check something becomes the one you see
 * every morning.
 *
 * **Deleting a workspace deletes no research.** Said in the confirmation
 * rather than left to be assumed, because it is the thing a person hesitates
 * over and the answer is reassuring.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Copy, Download, History, Pin, RotateCcw, Trash2, Upload, X,
} from 'lucide-react'
import { useRef, useState } from 'react'
import { API, getJson } from '../api'
import { playSound } from '../sound'

type Summary = {
  workspace_id: string
  name: string
  template_key: string | null
  panel_count: number
  updated_at: string
}

type Version = {
  version: number
  name: string
  summary: string
  actor: string
  previous_version: number | null
  panel_count: number
  at: string
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
    throw new Error(String(payload?.detail?.reason ?? payload?.detail?.code ?? response.statusText))
  }
  return payload.data as T
}

export function WorkspaceManager({
  activeId,
  onClose,
}: {
  activeId: string | null
  onClose: () => void
}) {
  const client = useQueryClient()
  const fileInput = useRef<HTMLInputElement | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [historyFor, setHistoryFor] = useState<string | null>(null)

  const list = useQuery({
    queryKey: ['workspace-list'],
    queryFn: () => getJson<{
      workspaces: Summary[]
      active: string | null
      default: string | null
    }>('/workspaces'),
  })
  const templates = useQuery({
    queryKey: ['workspace-templates'],
    queryFn: () => getJson<{ templates: TemplateInfo[] }>('/workspaces/templates'),
  })
  const history = useQuery({
    queryKey: ['workspace-history', historyFor],
    queryFn: () => getJson<{ versions: Version[]; name: string }>(
      `/workspaces/${historyFor}/history`,
    ),
    enabled: !!historyFor,
  })

  const defaultId = list.data?.default ?? null

  const refresh = () => {
    for (const key of ['workspace-list', 'workspace-active', 'workspace-history']) {
      void client.invalidateQueries({ queryKey: [key] })
    }
  }

  const run = useMutation({
    mutationFn: (job: { path: string; method: string; body?: unknown }) =>
      send<unknown>(job.path, job.method, job.body),
    onSuccess: () => { setError(null); playSound('workspace.save'); refresh() },
    onError: (e: Error) => { setError(e.message); playSound('error') },
  })

  const exportWorkspace = async (id: string, name: string) => {
    try {
      const payload = await getJson<{ document: unknown }>(`/workspaces/${id}/export`)
      const blob = new Blob([JSON.stringify(payload.document, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${name.replace(/[^\w-]+/g, '-').toLowerCase()}.workspace.json`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const importWorkspace = async (file: File) => {
    try {
      const document = JSON.parse(await file.text())
      run.mutate({ path: '/workspaces/import', method: 'POST', body: { document } })
    } catch (exc) {
      setError(`That file is not a workspace export: ${(exc as Error).message}`)
    }
  }

  const rows = list.data?.workspaces ?? []

  return (
    <aside className="ws-manager" aria-label="Workspace manager">
      <header>
        <h2>My workspaces</h2>
        <button type="button" className="ghost" onClick={onClose} aria-label="Close">
          <X size={14} />
        </button>
      </header>

      {error && <p className="state error" role="alert">{error}</p>}

      <ul className="ws-list">
        {rows.map((row) => (
          <li
            key={row.workspace_id}
            data-active={row.workspace_id === activeId ? 'yes' : undefined}
            data-default={row.workspace_id === defaultId ? 'yes' : undefined}
          >
            <button
              type="button"
              className="ws-open"
              onClick={() => run.mutate({ path: `/workspaces/${row.workspace_id}/open`, method: 'POST' })}
            >
              <strong>
                {row.name}
                {row.workspace_id === defaultId && <em title="Opens on start">default</em>}
              </strong>
              <span className="mono">
                {row.panel_count} panel{row.panel_count === 1 ? '' : 's'}
                {row.template_key ? ` · from ${row.template_key}` : ''}
              </span>
            </button>
            <div className="ws-actions">
              <button
                type="button"
                title={
                  row.workspace_id === defaultId
                    ? 'Already the workspace that opens on start'
                    : 'Make this the workspace that opens on start'
                }
                aria-pressed={row.workspace_id === defaultId}
                onClick={() => run.mutate({ path: `/workspaces/${row.workspace_id}/default`, method: 'POST' })}
              >
                <Pin size={13} />
              </button>
              <button
                type="button"
                title="Duplicate"
                onClick={() => run.mutate({
                  path: `/workspaces/${row.workspace_id}/clone`,
                  method: 'POST',
                  body: { name: `${row.name} (copy)` },
                })}
              >
                <Copy size={13} />
              </button>
              <button
                type="button"
                title="Version history"
                aria-expanded={historyFor === row.workspace_id}
                onClick={() => setHistoryFor(historyFor === row.workspace_id ? null : row.workspace_id)}
              >
                <History size={13} />
              </button>
              <button
                type="button"
                title="Export"
                onClick={() => exportWorkspace(row.workspace_id, row.name)}
              >
                <Download size={13} />
              </button>
              <button
                type="button"
                title="Delete"
                className="danger"
                onClick={() => setConfirming(row.workspace_id)}
              >
                <Trash2 size={13} />
              </button>
            </div>

            {confirming === row.workspace_id && (
              <p className="ws-confirm" role="alertdialog">
                Delete “{row.name}”? This removes the layout and its history. No experiment,
                verdict or holdout is touched — a workspace holds none.
                <span>
                  <button type="button" className="ghost" onClick={() => setConfirming(null)}>
                    Keep it
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => {
                      run.mutate({ path: `/workspaces/${row.workspace_id}`, method: 'DELETE' })
                      setConfirming(null)
                    }}
                  >
                    Delete
                  </button>
                </span>
              </p>
            )}

            {historyFor === row.workspace_id && (
              <ol className="ws-history">
                {(history.data?.versions ?? []).map((version) => (
                  <li key={version.version}>
                    <span className="mono">v{version.version}</span>
                    <span className="ws-history-summary">{version.summary}</span>
                    <span className="mono muted">
                      {version.actor} · {version.at.slice(11, 16)}
                    </span>
                    <button
                      type="button"
                      title="Restore this version"
                      onClick={() => run.mutate({
                        path: `/workspaces/${row.workspace_id}/history/${version.version}/restore`,
                        method: 'POST',
                      })}
                    >
                      <RotateCcw size={12} />
                    </button>
                    <button
                      type="button"
                      title="Duplicate this version into a new workspace"
                      onClick={() => run.mutate({
                        path: `/workspaces/${row.workspace_id}/history/${version.version}/duplicate`,
                        method: 'POST',
                        body: { name: `${row.name} — v${version.version}` },
                      })}
                    >
                      <Copy size={12} />
                    </button>
                  </li>
                ))}
                {!history.data?.versions.length && (
                  <li className="muted">No saved versions yet.</li>
                )}
              </ol>
            )}
          </li>
        ))}
      </ul>

      <section className="ws-new">
        <h3>New workspace</h3>
        <div className="ws-new-actions">
          <button
            type="button"
            onClick={() => run.mutate({
              path: '/workspaces',
              method: 'POST',
              body: { name: 'Workspace', activate: true },
            })}
          >
            Blank
          </button>
          <button type="button" onClick={() => fileInput.current?.click()}>
            <Upload size={13} /> Import
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void importWorkspace(file)
              event.target.value = ''
            }}
          />
        </div>
        <ul className="ws-templates">
          {(templates.data?.templates ?? []).map((template) => (
            <li key={template.key}>
              <button
                type="button"
                onClick={() => run.mutate({
                  path: '/workspaces',
                  method: 'POST',
                  body: { name: template.name, template_key: template.key, activate: true },
                })}
              >
                <strong>{template.name}</strong>
                <span>{template.summary}</span>
              </button>
            </li>
          ))}
        </ul>
        <p className="muted">
          Or describe what you want to do in the Assistant — it composes a workspace from the
          same panels and actions available here, and explains why each one is there.
        </p>
      </section>
    </aside>
  )
}
