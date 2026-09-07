import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BookOpen, Boxes, Check, Database, FileCode2, FlaskConical,
  Gavel, Layers, Plus, ShieldCheck, X,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { deleteJson, getJson, postJson } from '../api'
import { Rolling } from '../components/ui'
import type {
  CatalogTemplate, DatasetInfo, EngineStatus, FamilyInfo, StrategyListItem, StoragePayload,
} from '../types'
import type { ResearchSource } from './AgentCommand'

/* The pipeline, as a graph rather than a list of tabs.
 *
 * Every other view answers "what does this one object look like". None of them
 * answered "where does a strategy come from, and what has to happen before a
 * number means anything" — which is the question a new operator actually has,
 * and the question the judge's refusals only make sense against.
 *
 * So: boxes are things that accumulate (papers, templates, strategies,
 * backtests, verdicts), arrows are the steps that move work between them, and
 * both carry live counts. A stage the engine is working in right now lights up,
 * because the alternative is a diagram that could have been a screenshot. */

type StageKey = 'sources' | 'catalog' | 'candidates' | 'measurement' | 'judgement' | 'survival'

type Node = {
  id: string
  stage: StageKey
  label: string
  icon: typeof Layers
  count: number
  unit: string
  detail: string
  tone?: 'plain' | 'good' | 'warn' | 'bad'
}

const STAGES: { key: StageKey; label: string; note: string }[] = [
  { key: 'sources', label: 'Sources', note: 'Nothing downstream is trusted further than this' },
  { key: 'catalog', label: 'Catalogue', note: 'What may be built, and out of what' },
  { key: 'candidates', label: 'Candidates', note: 'Real Python on disk' },
  { key: 'measurement', label: 'Measurement', note: 'Chronological split, modelled fills' },
  { key: 'judgement', label: 'Judgement', note: 'Deterministic gates, no narrative' },
  { key: 'survival', label: 'Survival', note: 'The question the system exists to answer' },
]

/* Arrows are named after the operation, not the direction. "Scholarly search"
 * and "template draw" are things that can be running; "next" is not. */
const EDGES: { from: string; to: string; label: string; stage: string[] }[] = [
  { from: 'papers', to: 'families', label: 'mechanism review', stage: [] },
  { from: 'datasets', to: 'strategies', label: 'bar load', stage: ['loading'] },
  { from: 'families', to: 'templates', label: 'implementation', stage: [] },
  { from: 'templates', to: 'strategies', label: 'parameter draw', stage: ['creating'] },
  { from: 'strategies', to: 'development', label: 'guard + import', stage: ['backtesting'] },
  { from: 'development', to: 'validation', label: 'screen survives', stage: ['validating'] },
  { from: 'validation', to: 'verdicts', label: 'gate ladder', stage: ['judging'] },
  { from: 'verdicts', to: 'holdout', label: 'burn-once', stage: [] },
  { from: 'holdout', to: 'prop', label: 'block bootstrap', stage: ['prop'] },
]

const ICONS: Record<string, typeof Layers> = {
  papers: BookOpen, datasets: Database, families: Boxes, templates: FileCode2,
  strategies: Layers, development: FlaskConical, validation: FlaskConical,
  verdicts: Gavel, holdout: FlaskConical, prop: ShieldCheck,
}

export function PipelineView() {
  const [selected, setSelected] = useState<string>('families')

  const engine = useQuery({
    queryKey: ['engine'], refetchInterval: 2000,
    queryFn: () => getJson<EngineStatus & { worker_stages?: Record<string, string> }>('/engine'),
  })
  const families = useQuery({ queryKey: ['families'], queryFn: () => getJson<FamilyInfo[]>('/families') })
  const templates = useQuery({ queryKey: ['catalog-templates'], queryFn: () => getJson<CatalogTemplate[]>('/templates') })
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<StrategyListItem[]>('/strategies') })
  const sources = useQuery({ queryKey: ['research-sources'], queryFn: () => getJson<ResearchSource[]>('/research/sources') })
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => getJson<DatasetInfo[]>('/datasets') })
  const storage = useQuery({ queryKey: ['storage'], queryFn: () => getJson<StoragePayload>('/storage') })

  const list = strategies.data ?? []
  const tiers = useMemo(() => {
    const counts = { development: 0, validation: 0, holdout: 0 }
    for (const item of list) {
      const tier = item.latest?.evidence_tier
      if (tier === 'DEVELOPMENT_IN_SAMPLE' || tier === 'LEGACY_IN_SAMPLE') counts.development += 1
      else if (tier === 'VALIDATION_OOS' || tier === 'TRUTH_OOS') counts.validation += 1
      else if (tier === 'HOLDOUT') counts.holdout += 1
    }
    return counts
  }, [list])

  const state = engine.data
  const activeStages = new Set(Object.values(state?.worker_stages ?? {}))
  const realDatasets = (datasets.data ?? []).filter((d) => d.is_real)

  const nodes: Node[] = [
    {
      id: 'papers', stage: 'sources', label: 'Research library', icon: ICONS.papers,
      count: sources.data?.length ?? 0, unit: 'references',
      detail: 'Curated summaries and discovered abstracts. Nothing here has been read in full.',
    },
    {
      id: 'datasets', stage: 'sources', label: 'Market data', icon: ICONS.datasets,
      count: realDatasets.length, unit: 'real datasets',
      detail: 'Databento archives and Binance endpoints. Synthetic bars can never clear G0.',
      tone: realDatasets.length ? 'good' : 'bad',
    },
    {
      id: 'families', stage: 'catalog', label: 'Families', icon: ICONS.families,
      count: families.data?.length ?? 0, unit: 'registered',
      detail: 'Research classifications with a stated mechanism. Some are blocked on data.',
    },
    {
      id: 'templates', stage: 'catalog', label: 'Templates', icon: ICONS.templates,
      count: templates.data?.length ?? 0, unit: 'executable',
      detail: 'Guarded, smoke-tested Python. What the engine actually draws from.',
    },
    {
      id: 'strategies', stage: 'candidates', label: 'Strategies', icon: ICONS.strategies,
      count: list.length, unit: 'on disk',
      detail: 'spec.json, strategy.py and a lookahead trap, per candidate.',
    },
    {
      id: 'development', stage: 'measurement', label: 'Development', icon: ICONS.development,
      count: tiers.development, unit: 'latest here',
      detail: 'The only partition selection is allowed to see. In-sample by construction.',
    },
    {
      id: 'validation', stage: 'measurement', label: 'Validation', icon: ICONS.validation,
      count: tiers.validation, unit: 'reached OOS',
      detail: 'Reserved slice. Reached only when the development screen survives.',
      tone: tiers.validation ? 'good' : 'plain',
    },
    {
      id: 'verdicts', stage: 'judgement', label: 'Gate ladder', icon: ICONS.verdicts,
      count: state?.judged ?? 0, unit: 'judged this run',
      detail: 'G0 data through G5 multiple testing. A FAIL here is the system working.',
    },
    {
      id: 'holdout', stage: 'judgement', label: 'Holdout', icon: ICONS.holdout,
      count: tiers.holdout, unit: 'burned',
      detail: 'Burn-once per lineage. Consuming it is irreversible.',
      tone: tiers.holdout ? 'good' : 'plain',
    },
    {
      id: 'prop', stage: 'survival', label: 'Prop simulation', icon: ICONS.prop,
      count: state?.prop_tested ?? 0, unit: 'accounts modelled',
      detail: 'Block-bootstrap paths against modelled funded-account rules.',
    },
  ]

  const byStage = (key: StageKey) => nodes.filter((n) => n.stage === key)
  const node = nodes.find((n) => n.id === selected)

  return (
    <section className="pipeline">
      <header className="pipe-head">
        <div>
          <p className="command-kicker"><span className="signal-dot" /> THE GRAPH</p>
          <h2>Where a number comes from.</h2>
          <p className="pipe-sub">
            Boxes accumulate. Arrows are the steps between them. Both carry live counts, and
            whatever the engine is doing right now is lit.
          </p>
        </div>
        <div className="pipe-vitals">
          <div><span>Engine</span><strong className={state?.running ? 'good' : ''}>
            {state?.running ? 'RUNNING' : state?.stopping ? 'STOPPING' : 'STOPPED'}</strong></div>
          <div><span>Workspace</span><strong className="mono" title={storage.data?.root}>
            {storage.data?.vault_mode ? 'VAULT' : 'REPO'}</strong></div>
          <div><span>Skipped by memory</span><strong>
            <Rolling value={state?.skipped_by_memory ?? 0} /></strong></div>
        </div>
      </header>

      <div className="pipe-graph" role="group" aria-label="Pipeline stages">
        {STAGES.map((stage, column) => {
          const flowing = byStage(stage.key).some((item) =>
            EDGES.some((e) => e.to === item.id && e.stage.some((s) => activeStages.has(s))),
          )
          return (
          <div
            className="pipe-stage"
            key={stage.key}
            data-flowing={flowing ? 'yes' : undefined}
            style={{ animationDelay: `${column * 60}ms` }}
          >
            <div className="pipe-stage-head">
              <span>{String(column + 1).padStart(2, '0')}</span>
              <h3>{stage.label}</h3>
              <p>{stage.note}</p>
            </div>
            <div className="pipe-nodes">
              {byStage(stage.key).map((item) => {
                const Icon = item.icon
                const feeding = EDGES.filter((e) => e.to === item.id)
                const live = feeding.some((e) => e.stage.some((s) => activeStages.has(s)))
                return (
                  <button
                    key={item.id}
                    className="pipe-node af-press"
                    data-selected={selected === item.id ? 'yes' : undefined}
                    data-live={live ? 'yes' : undefined}
                    data-tone={item.tone ?? 'plain'}
                    aria-pressed={selected === item.id}
                    onClick={() => setSelected(item.id)}
                  >
                    <span className="pipe-node-top"><Icon size={13} /> {item.label}</span>
                    <b><Rolling value={item.count} /></b>
                    <small>{item.unit}</small>
                    {feeding.length > 0 && (
                      <span className="pipe-edge-label">
                        {feeding.map((e) => e.label).join(' · ')}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
          )
        })}
      </div>

      {node && (
        <div className="pipe-detail af-panel-in" key={node.id}>
          <header>
            <h3>{node.label}</h3>
            <p>{node.detail}</p>
          </header>
          {node.id === 'families' && <FamiliesPane rows={families.data ?? []} />}
          {node.id === 'templates' && <TemplatesPane rows={templates.data ?? []} families={families.data ?? []} />}
          {node.id === 'papers' && <SourcesPane rows={sources.data ?? []} />}
          {node.id === 'datasets' && <DatasetsPane rows={datasets.data ?? []} />}
          {!['families', 'templates', 'papers', 'datasets'].includes(node.id) && (
            <StagePane node={node} state={state} />
          )}
        </div>
      )}
    </section>
  )
}

/* ── panes ───────────────────────────────────────────────────────────────── */

function FamiliesPane({ rows }: { rows: FamilyInfo[] }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ key: '', label: '', description: '', mechanism: '' })

  const create = useMutation({
    mutationFn: () => postJson<FamilyInfo>('/families', form),
    onSuccess: () => {
      setForm({ key: '', label: '', description: '', mechanism: '' })
      setOpen(false); setError(null)
      qc.invalidateQueries({ queryKey: ['families'] })
    },
    onError: (e: Error) => setError(e.message),
  })
  const remove = useMutation({
    mutationFn: (key: string) => deleteJson(`/families/${key}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['families'] }),
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="pane">
      <div className="pane-actions">
        <button className="btn primary af-press" onClick={() => setOpen(!open)}>
          <Plus size={13} /> {open ? 'Cancel' : 'New family'}
        </button>
        <span className="muted">
          {rows.filter((r) => r.runnable).length} runnable · {rows.filter((r) => !r.runnable).length} blocked on data
        </span>
      </div>
      {error && <p className="command-error" role="alert">{error}</p>}
      {open && (
        <form className="pane-form af-panel-in" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
          <label>Key<input required pattern="[a-z][a-z0-9_]{2,39}" placeholder="order_flow_imbalance"
            value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} /></label>
          <label>Label<input required placeholder="Order-flow imbalance"
            value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
          <label className="wide">Description<input placeholder="One line on what the family covers."
            value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <label className="wide">Economic mechanism
            <textarea required minLength={40} rows={3}
              placeholder="Why this should work, in terms of who is trading and why. Forty characters minimum — a family without a mechanism is a bucket, and nothing in it can be falsified."
              value={form.mechanism} onChange={(e) => setForm({ ...form, mechanism: e.target.value })} />
          </label>
          <button className="btn primary" disabled={create.isPending}>
            {create.isPending ? 'Registering…' : 'Register family'}
          </button>
        </form>
      )}
      <div className="family-grid">
        {rows.map((row, i) => (
          <article className="family-card af-row-in" key={row.key}
            data-blocked={row.runnable ? undefined : 'yes'}
            style={{ animationDelay: `${Math.min(i, 12) * 25}ms` }}>
            <header>
              <h4>{row.label}</h4>
              <span className={row.runnable ? 'chip good' : 'chip warn'}>
                {row.runnable ? <Check size={10} /> : <AlertTriangle size={10} />}
                {row.runnable ? 'RUNNABLE' : 'BLOCKED'}
              </span>
            </header>
            <code>{row.key}</code>
            <p>{row.mechanism}</p>
            {!row.runnable && (
              <p className="family-block">
                Registered, but the strategy writer refuses it: this needs{' '}
                <b>{row.blocked_by.join(', ')}</b>, which is not configured. Adding the family
                did not add the data.
              </p>
            )}
            <footer>
              <span>{row.template_count} template{row.template_count === 1 ? '' : 's'}</span>
              <span className="muted">{row.origin}</span>
              {row.origin === 'custom' && (
                <button className="icon-btn af-press" aria-label={`Delete ${row.label}`}
                  onClick={() => remove.mutate(row.key)}><X size={12} /></button>
              )}
            </footer>
          </article>
        ))}
      </div>
    </div>
  )
}

function TemplatesPane({ rows, families }: { rows: CatalogTemplate[]; families: FamilyInfo[] }) {
  const qc = useQueryClient()
  const [source, setSource] = useState<{ key: string; text: string } | null>(null)
  const runnable = families.filter((f) => f.runnable)

  const load = useMutation({
    mutationFn: (key: string) => getJson<{ key: string; source: string }>(`/templates/${key}/source`),
    onSuccess: (d) => setSource({ key: d.key, text: d.source }),
  })
  const remove = useMutation({
    mutationFn: (key: string) => deleteJson(`/templates/${key}`),
    onSuccess: () => { setSource(null); qc.invalidateQueries({ queryKey: ['catalog-templates'] }) },
  })

  return (
    <div className="pane">
      <p className="muted">
        A template is executable Python with <code>entry_signal</code> and <code>exit_signal</code>.
        Registering one runs the static guard and then executes it on synthetic bars — a template
        that crashes never reaches the engine. Ask the orchestrator to write one, or the console.
      </p>
      <table className="tbl template-table">
        <thead>
          <tr><th>Template</th><th>Family</th><th>Params</th><th>Grid</th><th>Origin</th><th /></tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.key} className="af-row-in" style={{ animationDelay: `${Math.min(i, 14) * 20}ms` }}>
              <td><b>{row.name}</b><br /><code>{row.key}</code></td>
              <td>{row.family}
                {!runnable.some((f) => f.key === row.family) && (
                  <span className="chip warn" title="Its family is blocked on missing data">BLOCKED</span>
                )}
              </td>
              <td className="mono">{row.parameters.length}</td>
              <td className="mono" title="Distinct parameter combinations this template can produce">
                {row.grid_points >= 1e9 ? '1e9+' : row.grid_points.toLocaleString()}
              </td>
              <td><span className={row.origin === 'custom' ? 'chip good' : 'chip'}>{row.origin}</span></td>
              <td className="right">
                <button className="btn tiny af-press" onClick={() => load.mutate(row.key)}>Source</button>
                {row.origin === 'custom' && (
                  <button className="btn tiny af-press" onClick={() => remove.mutate(row.key)}>Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {source && (
        <div className="source-view af-panel-in">
          <header><code>{source.key}</code>
            <button className="icon-btn af-press" aria-label="Close source" onClick={() => setSource(null)}><X size={12} /></button>
          </header>
          <pre>{source.text}</pre>
        </div>
      )}
    </div>
  )
}

function SourcesPane({ rows }: { rows: ResearchSource[] }) {
  return (
    <div className="pane">
      <p className="muted">
        {rows.filter((r) => r.origin === 'curated').length} curated ·{' '}
        {rows.filter((r) => r.origin !== 'curated').length} discovered and unreviewed. Every one of
        these is mirrored into the vault as a note.
      </p>
      <ul className="mini-list">
        {rows.slice(0, 40).map((row, i) => (
          <li key={row.id} className="af-row-in" style={{ animationDelay: `${Math.min(i, 14) * 18}ms` }}>
            <span className={row.origin === 'curated' ? 'chip good' : 'chip warn'}>
              {row.origin === 'curated' ? 'CURATED' : 'UNREVIEWED'}
            </span>
            <b>{row.title}</b>
            <small>{row.authors || 'authors not recorded'} · {row.year ?? 'undated'}</small>
          </li>
        ))}
      </ul>
    </div>
  )
}

function DatasetsPane({ rows }: { rows: DatasetInfo[] }) {
  return (
    <div className="pane">
      <table className="tbl">
        <thead><tr><th>Dataset</th><th>Provider</th><th>Bars</th><th>Span</th><th>Status</th></tr></thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.key} className="af-row-in" style={{ animationDelay: `${Math.min(i, 10) * 22}ms` }}>
              <td><b>{row.label}</b><br /><code>{row.symbol} @ {row.interval}</code></td>
              <td>{row.provider}</td>
              <td className="mono">{row.bar_count.toLocaleString()}</td>
              <td className="mono">{row.span_years ? `${row.span_years.toFixed(1)}y` : '—'}</td>
              <td className={row.is_real ? 'good' : 'warn'}>{row.is_real ? 'REAL' : 'SYNTHETIC'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StagePane({ node, state }: { node: Node; state?: EngineStatus }) {
  const rows: [string, string | number][] =
    node.id === 'verdicts'
      ? [['Judged this run', state?.judged ?? 0], ['Passed', state?.passed ?? 0],
         ['Rejected', state?.rejected ?? 0], ['Cleared validation', state?.validation_passed ?? 0]]
      : node.id === 'holdout'
        ? [['Holdout passes', state?.holdout_passed ?? 0], ['Lineages retired', state?.lineages_retired ?? 0]]
        : node.id === 'prop'
          ? [['Accounts modelled', state?.prop_tested ?? 0],
             ['Best pass rate', `${((state?.best_pass_rate ?? 0) * 100).toFixed(1)}%`],
             ['Best strategy', state?.best_strategy ?? '—'], ['Against', state?.best_rule ?? '—']]
          : [['Created this run', state?.created ?? 0], ['Backtested', state?.backtested ?? 0],
             ['Skipped by memory', state?.skipped_by_memory ?? 0], ['Retired', state?.pruned ?? 0]]
  return (
    <div className="pane">
      <div className="pane-figures">
        {rows.map(([label, value]) => (
          <div key={label}><span>{label}</span><strong className="mono">{value}</strong></div>
        ))}
      </div>
      {state?.last_error && <p className="command-error">Last engine error: {state.last_error}</p>}
    </div>
  )
}
