import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, FolderTree, HardDrive, RefreshCw, Search } from 'lucide-react'
import { useState } from 'react'
import { getJson, postJson } from '../api'
import type { StorageInspect, StoragePayload } from '../types'

/* Where everything the app writes ends up.
 *
 * The pointer lives in the repository and the storage lives wherever it points,
 * which is the only arrangement that works: a setting describing the location of
 * the workspace cannot itself be inside the workspace.
 *
 * The panel is deliberately explicit that a switch takes effect on restart.
 * Relocating open SQLite handles mid-run would split the ledger across two
 * roots, and a progress bar that implied otherwise would be a lie with data
 * loss attached. */

const bytes = (value: number) => {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(0)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

export function StoragePanel() {
  const qc = useQueryClient()
  const [candidate, setCandidate] = useState('')
  const [report, setReport] = useState<StorageInspect | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [switched, setSwitched] = useState<string | null>(null)

  const storage = useQuery({ queryKey: ['storage'], queryFn: () => getJson<StoragePayload>('/storage') })

  const inspect = useMutation({
    mutationFn: () => postJson<StorageInspect>('/storage/inspect', { path: candidate }),
    onSuccess: (r) => { setReport(r); setError(null) },
    onError: (e: Error) => setError(e.message),
  })
  const move = useMutation({
    mutationFn: () => postJson<{ location: string; migration: { copied: number; skipped: number } }>(
      '/storage/switch', { path: candidate, migrate_existing: true },
    ),
    onSuccess: (r) => {
      setSwitched(`${r.location} — ${r.migration.copied} items copied. Restart AlgoForge to use it.`)
      setError(null)
      qc.invalidateQueries({ queryKey: ['storage'] })
    },
    onError: (e: Error) => setError(e.message),
  })
  const reindex = useMutation({
    mutationFn: () => postJson<{ written: Record<string, number> }>('/storage/reindex'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['storage'] }),
    onError: (e: Error) => setError(e.message),
  })

  const s = storage.data
  if (!s) return <div className="panel"><div className="panel-body">Reading storage location…</div></div>

  return (
    <div className="panel af-panel-in">
      <header>
        <h2><HardDrive size={12} /> Storage location</h2>
        <div className="panel-actions">
          <button className="btn af-press" disabled={reindex.isPending} onClick={() => reindex.mutate()}>
            <RefreshCw className={reindex.isPending ? 'af-spin' : undefined} /> Rebuild vault notes
          </button>
        </div>
      </header>
      <div className="panel-body">
        <div className="storage-current">
          <div>
            <span>Everything the app writes</span>
            <strong className="mono">{s.root}</strong>
            <small>
              {s.is_obsidian_vault ? 'An Obsidian vault' : 'A plain folder'}
              {s.vault_mode ? ' · notes and machine storage kept apart' : ' · flat layout'}
              {s.writable ? '' : ' · NOT WRITABLE'}
            </small>
          </div>
          <div className="storage-counts">
            <span><b>{s.counts.strategy_notes}</b> strategy notes</span>
            <span><b>{s.counts.paper_notes}</b> paper notes</span>
            <span><b>{s.counts.backtest_notes}</b> backtest notes</span>
            <span><b>{s.counts.verdict_notes}</b> verdict notes</span>
            <span><b>{s.counts.mission_notes}</b> mission notes</span>
            <span><b>{bytes(s.note_bytes)}</b> of notes</span>
          </div>
        </div>

        {s.mirror.last_error && (
          <p className="warning bad">
            The last note could not be written: {s.mirror.last_error}. Research still runs; only
            the Markdown mirror is affected.
          </p>
        )}

        <div className="storage-folders">
          {s.folders.map((folder) => (
            <div key={folder.path} data-kind={folder.kind}>
              <FolderTree size={11} />
              <b>{folder.name}</b>
              <code title={folder.path}>{folder.path}</code>
              <span>{folder.kind === 'notes' ? 'readable in Obsidian' : 'hidden from Obsidian'}</span>
            </div>
          ))}
        </div>

        <details className="storage-stays">
          <summary>Three things stay with the code, and why</summary>
          <ul>
            {s.stays_in_repo.map((item) => (
              <li key={item.path}><b>{item.name}</b> <code>{item.path}</code><p>{item.why}</p></li>
            ))}
          </ul>
        </details>

        <div className="storage-move">
          <label htmlFor="storage-path">Move storage to</label>
          <div className="storage-move-row">
            <input
              id="storage-path" value={candidate} spellCheck={false}
              placeholder="F:\Obsidian Vaults\AlgoForge-Vault"
              onChange={(e) => { setCandidate(e.target.value); setReport(null); setSwitched(null) }}
            />
            <button className="btn af-press" disabled={candidate.length < 3 || inspect.isPending}
              onClick={() => inspect.mutate()}>
              <Search size={13} /> Check
            </button>
            <button className="btn primary af-press"
              disabled={!report?.usable || move.isPending}
              onClick={() => move.mutate()}>
              {move.isPending ? 'Copying…' : 'Move and copy existing'}
            </button>
          </div>

          {report && (
            <div className="storage-report af-panel-in" data-usable={report.usable ? 'yes' : 'no'}>
              {report.usable ? <Check size={13} /> : <AlertTriangle size={13} />}
              <div>
                <b>{report.path}</b>
                <p>
                  {report.exists ? 'Exists' : report.creatable ? 'Will be created' : 'Cannot be created'}
                  {report.is_obsidian_vault && ' · Obsidian vault'}
                  {report.already_initialised && ' · already holds AlgoForge output'}
                  {report.existing_strategies > 0 && ` · ${report.existing_strategies} strategies already there`}
                </p>
                {report.problems.map((problem) => <p key={problem} className="bad">{problem}</p>)}
              </div>
            </div>
          )}

          {switched && <p className="warning">{switched}</p>}
          {error && <p className="warning bad">{error}</p>}

          <p className="warning">
            Moving copies rather than moves: the old location is left intact, so a failed
            relocation cannot be the moment you discover it was the only copy. The switch takes
            effect on the next start — the running process keeps its open databases where they
            are, rather than splitting the ledger across two roots mid-run.
          </p>
        </div>
      </div>
    </div>
  )
}
