import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, ArrowUpRight, BookOpen, BrainCircuit, Check, Clock3, Cpu, FlaskConical, Network, Pause, Play, Search, Send, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { getJson, patchJson, postJson } from '../api'
import type { EngineStatus, ResearchLoopStatus } from '../types'

type Agent = {
  id: string; label: string; mission: string; skills: string[]; tool: string
  status: string; enabled: boolean; task: string; summary: string; elapsed_seconds: number
  mode: string; model: string | null; error: string | null; runs: number; source_ids: string[]
}
type Worker = { id: number; policy: string; stage: string; paused: boolean; strategy_id: string | null }
type Task = { id: string; role: string; task: string; summary: string; status: string; mode: string; finished_at: number }
type Command = {
  roles: Agent[]; orchestrator: Agent | null; specialists: Agent[]
  engine: EngineStatus & { workers: Worker[]; stopping: boolean }
  source_count: number; model_calls_today: number; model_call_limit: number; tasks: Task[]
  proposals: { id: string; template: string; status: string; hypothesis: string }[]
}
export type ResearchSource = {
  id: string; title: string; authors: string; year: number | null; url: string
  summary: string; replication_gap: string; templates: string[]; topic: string
  origin: string; evidence: string; content_level: string
}
const SHORT: Record<string, string> = {
  research: 'Research scout', hypothesis: 'Hypothesis', strategy_code: 'Strategy engineer',
  validation: 'Validation', risk: 'Risk officer', post_mortem: 'Post-mortem', bulk: 'Bulk worker', chat: 'Console chat',
}
const isBusy = (agent: Agent) => ['running', 'queued'].includes(agent.status)

/** Roles are a registry, so their geometry must be derived from the response. */
const specialistPosition = (index: number, count: number) => {
  const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(count, 1)
  return { x: 50 + Math.cos(angle) * 38, y: 50 + Math.sin(angle) * 39 }
}

export function AgentCommandView() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState('research')
  const [task, setTask] = useState('')
  const [pane, setPane] = useState<'network' | 'research' | 'experiments'>('network')
  const command = useQuery({ queryKey: ['agent-command'], queryFn: () => getJson<Command>('/agent-command'), refetchInterval: 2000 })
  const researchLoop = useQuery({ queryKey: ['research-loop'], queryFn: () => getJson<ResearchLoopStatus>('/research-loop'), refetchInterval: 5000 })
  const sources = useQuery({ queryKey: ['research-sources'], queryFn: () => getJson<ResearchSource[]>('/research/sources'), refetchInterval: 8000 })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['agent-command'] }); qc.invalidateQueries({ queryKey: ['research-sources'] }) }
  const run = useMutation({ mutationFn: () => postJson(`/agent-command/${selected}/run`, { task }), onSuccess: () => { setTask(''); refresh() } })
  const control = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => patchJson(`/agent-command/${id}`, { enabled }), onSuccess: refresh })
  const workerControl = useMutation({ mutationFn: (worker: Worker) => patchJson(`/engine/workers/${worker.id}`, { paused: !worker.paused }), onSuccess: refresh })
  const runResearch = useMutation({ mutationFn: () => postJson<ResearchLoopStatus>('/research-loop/run'), onSuccess: () => qc.invalidateQueries({ queryKey: ['research-loop'] }) })
  const data = command.data
  const specialists = data?.specialists ?? data?.roles.filter((r) => r.id !== 'orchestrator') ?? []
  const coordinator = data?.orchestrator ?? data?.roles.find((r) => r.id === 'orchestrator') ?? null
  const agent = specialists.find((r) => r.id === selected)
  const active = specialists.filter(isBusy).length
  const error = run.error || control.error || workerControl.error
  if (!data) return <div className="command-loading" role="status">{command.isError ? 'Agent connection unavailable. Retrying…' : 'Connecting to the research team…'}<div className="skeleton" /></div>

  return <section className="command-page">
    <div className="command-hero">
      <div><div className="command-kicker"><span className="signal-dot" /> RESEARCH INTELLIGENCE / 08</div>
        <h2>Ideas into evidence.<br /><span>Your team, in view.</span></h2>
        <p>Follow every experiment. Direct every specialist. See what the research actually supports.</p>
      </div>
      <div className="command-vitals">
        <div><Network /><strong>{active}<span>/ 8</span></strong><p>specialists active</p></div>
        <div><BookOpen /><strong>{data.source_count}</strong><p>research sources</p></div>
        <div><FlaskConical /><strong>{data.engine.backtested}</strong><p>backtests this run</p></div>
      </div>
    </div>

    <div className="command-nav" aria-label="Command views">
      <button className={pane === 'network' ? 'active' : ''} onClick={() => setPane('network')}><Network size={15} /> Agent network</button>
      <button className={pane === 'research' ? 'active' : ''} onClick={() => setPane('research')}><BookOpen size={15} /> Research brain <small>{data.source_count}</small></button>
      <button className={pane === 'experiments' ? 'active' : ''} onClick={() => setPane('experiments')}><FlaskConical size={15} /> Experiment queue <small>{data.proposals.length}</small></button>
      <span className="command-nav-note">{data.model_calls_today} / {data.model_call_limit} model calls today</span>
    </div>
    {researchLoop.data && <div className="research-loop-strip" data-active={researchLoop.data.in_flight}>
      <span><i /> CONTINUOUS RESEARCH</span>
      <strong>{researchLoop.data.enabled ? researchLoop.data.in_flight ? 'SCANNING' : 'ARMED' : 'PAUSED'}</strong>
      <small>{researchLoop.data.cycles} cycles · {researchLoop.data.sources_found} references · {researchLoop.data.downstream_tasks} hand-offs</small>
      <small>{researchLoop.data.scope}</small>
      <button className="btn tiny" disabled={!researchLoop.data.enabled || researchLoop.data.in_flight || runResearch.isPending} onClick={() => runResearch.mutate()}>{runResearch.isPending ? 'Queued…' : 'Run now'}</button>
    </div>}
    {error && <p className="command-error" role="alert">{error.message}</p>}
    {command.isError && <p className="command-error" role="status">Connection lost. Showing the last received state.</p>}

    {pane === 'network' && <>
      <div className="command-workspace">
        <div className="neural-panel">
          <div className="neural-heading"><span><i /> NEURAL LINK</span><span>{active > 0 ? 'PROCESSING' : 'AWAITING WORK'}</span></div>
          <div className="neural-map">
            <svg className="neural-links" viewBox="0 0 1000 650" preserveAspectRatio="none" aria-hidden="true">
              <defs><radialGradient id="core-glow"><stop offset="0" stopColor="#71dfb5" stopOpacity=".16" /><stop offset="1" stopColor="#71dfb5" stopOpacity="0" /></radialGradient></defs>
              <ellipse cx="500" cy="325" rx="240" ry="230" fill="url(#core-glow)" />
              {[130, 200, 275].map((r) => <circle key={r} cx="500" cy="325" r={r} className="neural-orbit" />)}
              {specialists.map((role, i) => {
                const { x, y } = specialistPosition(i, specialists.length)
                const path = `M 500 325 Q ${x < 50 ? 400 : 600} ${y * 6.5} ${x * 10} ${y * 6.5}`
                return <g key={role.id} data-flow={isBusy(role) ? 'active' : 'idle'} className={selected === role.id ? 'selected-link' : ''}>
                  <path d={path} className="neural-wire" /><path d={path} className="neural-packet" />
                </g>
              })}
            </svg>
            <div className={`neural-core ${active || (coordinator && isBusy(coordinator)) ? 'is-processing' : ''}`}>
              <div className="core-ring" /><BrainCircuit size={34} /><strong>FORGE</strong><span>RESEARCH CORE</span>
              <small>{coordinator && isBusy(coordinator) ? 'Orchestrating mission' : active ? `${active} active connections` : 'Ready to explore'}</small>
            </div>
            {specialists.map((role, i) => {
              const position = specialistPosition(i, specialists.length)
              return <button key={role.id} aria-pressed={selected === role.id}
              className={`neural-node ${selected === role.id ? 'selected' : ''} ${isBusy(role) ? 'is-working' : ''}`}
              style={{ left: `${position.x}%`, top: `${position.y}%`, animationDelay: `${i * 50}ms` }}
              onClick={() => { setSelected(role.id); setTask('') }}>
              <span className="node-number">0{i + 1}<i data-status={!role.enabled ? 'paused' : role.status} /></span>
              <strong>{SHORT[role.id]}</strong><small>{!role.enabled ? 'Paused' : role.status}</small>
            </button>})}
          </div>
          <div className="neural-caption"><span><i className="legend-active" /> Live task</span><span><i /> Available</span><p>Select a specialist to inspect and direct its work</p></div>
        </div>

        {agent && <aside className="agent-inspector" key={agent.id}>
          <div className="inspector-top"><span className="inspector-icon"><Cpu size={21} /></span><span className={`agent-status status-${agent.status}`}>{!agent.enabled ? 'Paused' : agent.status}</span></div>
          <p className="command-kicker">SPECIALIST / {String(specialists.indexOf(agent) + 1).padStart(2, '0')}</p>
          <h3>{agent.label}</h3><p className="agent-mission">{agent.mission}</p>
          <div className="agent-skills">{agent.skills.map((skill) => <span key={skill}><Check size={10} />{skill}</span>)}</div>
          <div className="agent-current"><span>Current assignment</span><p>{agent.task}</p><div><Clock3 size={12} />{agent.elapsed_seconds.toFixed(1)}s <span>·</span> {agent.tool}</div></div>
          <div className="agent-summary"><span><Sparkles size={13} /> Decision summary</span><p>{agent.summary}</p></div>
          {!!agent.source_ids.length && <div className="agent-citations"><span>Sources used</span>{agent.source_ids.map(id => {
            const source = sources.data?.find(item => item.id === id)
            return source && /^https:\/\//.test(source.url) ? <a key={id} href={source.url} target="_blank" rel="noreferrer">{source.title}<ArrowUpRight size={12} /></a> : <small key={id}>{id}</small>
          })}</div>}
          <div className="agent-model"><span>Execution</span><strong>{agent.mode === 'not_run' ? 'Not run yet' : agent.mode.replaceAll('_', ' ')}</strong>{agent.model && <small>{agent.model}</small>}</div>
          <form onSubmit={(e) => { e.preventDefault(); run.mutate() }} className="agent-task-form">
            <label htmlFor="agent-assignment">Direct this specialist</label>
            <textarea id="agent-assignment" value={task} maxLength={2000} onChange={(e) => setTask(e.target.value)} placeholder={selected === 'research' ? 'Search papers on volatility-managed futures…' : 'Assign a focused task, or run its default mission…'} />
            <div><button type="submit" className="btn primary" disabled={!agent.enabled || isBusy(agent) || run.isPending}><Send size={13} />{isBusy(agent) ? 'Working…' : 'Run task'}</button>
              <button type="button" className="btn" disabled={control.isPending} onClick={() => control.mutate({ id: agent.id, enabled: !agent.enabled })}>{agent.enabled ? <Pause size={13} /> : <Play size={13} />}{agent.enabled ? 'Pause' : 'Resume'}</button></div>
            <small>Pause prevents new assignments. An active task finishes its current operation.</small>
          </form>
        </aside>}
      </div>

      <section className="compute-section">
        <div className="command-section-head"><div><p className="command-kicker">PARALLEL COMPUTE</p><h3>Eight paths to a better experiment.</h3></div><span>{data.engine.running ? 'ENGINE RUNNING' : data.engine.stopping ? 'FINISHING CURRENT WORK' : 'START ENGINE FROM OVERVIEW'}</span></div>
        <div className="compute-grid">{data.engine.workers.map((worker) => <article key={worker.id} className="compute-worker" data-working={!['idle', 'waiting', 'stopped', 'paused'].includes(worker.stage)}>
          <div><span>W{String(worker.id + 1).padStart(2, '0')}</span><button aria-label={`${worker.paused ? 'Resume' : 'Pause'} worker ${worker.id + 1}`} disabled={workerControl.isPending} onClick={() => workerControl.mutate(worker)}>{worker.paused ? <Play size={12} /> : <Pause size={12} />}</button></div>
          <h4>{worker.policy}</h4><p>{worker.paused ? 'Pause requested' : worker.stage}</p><div className="compute-track"><i /></div><small title={worker.strategy_id ?? ''}>{worker.strategy_id ?? 'No candidate assigned'}</small>
        </article>)}</div>
      </section>

      <section className="command-timeline"><div className="command-section-head"><div><p className="command-kicker">EVIDENCE TRAIL</p><h3>What happened, and why.</h3></div><Activity size={18} /></div>
        {!data.tasks.length && <div className="command-empty"><Activity /><h4>Your first task starts the trail.</h4><p>Select a specialist and run a task. Completed work, failures and source references appear here.</p></div>}
        {data.tasks.slice(0, 8).map((item) => <article key={item.id}><span className={`timeline-dot status-${item.status}`} /><time>{new Date(item.finished_at * 1000).toLocaleTimeString()}</time><div><strong>{SHORT[item.role]} <small>{item.mode.replaceAll('_', ' ')}</small></strong><p>{item.summary}</p></div><span>{item.status}</span></article>)}
      </section>
    </>}
    {pane === 'research' && <ResearchBrain />}
    {pane === 'experiments' && <div className="proposal-list"><div className="command-section-head"><div><p className="command-kicker">RESEARCH → EXPERIMENT</p><h3>Bounded ideas, executable variants.</h3></div></div>
      <p className="muted">Model proposals use known templates, validated parameter ranges and source IDs. Worker 1 consumes the queue while the engine runs.</p>
      {!data.proposals.length && <div className="command-empty"><FlaskConical /><h4>No model proposals yet.</h4><p>Ask the hypothesis analyst or strategy engineer to propose a research-backed experiment. A configured model is required.</p></div>}
      {data.proposals.map((item) => <article key={item.id}><span>{item.status}</span><h3>{item.template.replaceAll('_', ' ')}</h3><p>{item.hypothesis}</p><code>{item.id}</code></article>)}
    </div>}
  </section>
}

export function ResearchBrain() {
  const [filter, setFilter] = useState('')
  const [query, setQuery] = useState('')
  const qc = useQueryClient()
  const sources = useQuery({ queryKey: ['research-sources'], queryFn: () => getJson<ResearchSource[]>('/research/sources'), refetchInterval: 8000 })
  const search = useMutation({ mutationFn: () => postJson('/agent-command/research/run', { task: query }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['agent-command'] }) } })
  const rows = (sources.data ?? []).filter((s) => `${s.title} ${s.topic} ${s.authors}`.toLowerCase().includes(filter.toLowerCase()))
  return <section className="research-brain">
    <div className="brain-heading"><div><p className="command-kicker">THE RESEARCH LIBRARY</p><h3>A source for every hypothesis.</h3><p>Primary references, available abstracts, and the gaps between a paper and a working strategy.</p></div><BookOpen size={40} /></div>
    <form className="scholar-search" onSubmit={(e) => { e.preventDefault(); search.mutate() }}><Search size={17} /><input aria-label="Search scholarly papers" value={query} onChange={(e) => setQuery(e.target.value)} minLength={3} maxLength={240} required placeholder="Explore a mechanism. e.g. intraday futures momentum" /><button className="btn primary" disabled={search.isPending}>{search.isPending ? 'Submitting…' : 'Search papers'}<ArrowUpRight size={14} /></button></form>
    {search.isSuccess && <p className="good" role="status">Research task submitted. Follow the scout in Agent network; this library refreshes automatically.</p>}
    {search.error && <p className="command-error" role="alert">{search.error.message}</p>}
    <div className="brain-filter"><span>{rows.length} REFERENCES</span><input aria-label="Filter library" placeholder="Filter this library…" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
    {sources.isPending && <div className="skeleton" />}
    {sources.isError && <p role="alert">Research library unavailable. Retrying…</p>}
    {!sources.isPending && !rows.length && <div className="command-empty">No references match this filter.</div>}
    <div className="source-grid">{rows.map((source, i) => <article className="source-card" key={source.id} style={{ animationDelay: `${Math.min(i, 10) * 35}ms` }}>
      <div className="source-meta"><span>{source.origin === 'curated' ? 'CURATED REFERENCE' : 'DISCOVERED / UNREVIEWED'}</span><small>{source.year ?? 'Undated'}</small></div><h4><a href={/^https:\/\//.test(source.url) ? source.url : undefined} target="_blank" rel="noreferrer">{source.title}<ArrowUpRight size={14} /></a></h4><p className="source-authors">{source.authors}</p><p>{source.summary.slice(0, 450)}{source.summary.length > 450 ? '…' : ''}</p><div className="source-gap"><span>REPLICATION GAP</span><p>{source.replication_gap}</p></div><footer><span>{source.content_level.replaceAll('_', ' ')}</span><strong>{source.templates.length ? `${source.templates.length} linked templates` : 'Awaiting methodology review'}</strong></footer>
    </article>)}</div>
  </section>
}
