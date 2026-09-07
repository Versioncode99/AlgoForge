import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, KeyRound, Pause, RefreshCw, Send, Server, X } from 'lucide-react'
import { useState } from 'react'
import { getJson, patchJson, postJson } from '../api'
import { StoragePanel } from '../components/StoragePanel'
import { PanelHead, Stat } from '../components/ui'
import type { AskResult, Evolution, OracleInfo, SettingsPayload } from '../types'

export function SettingsView() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => getJson<SettingsPayload>('/settings') })
  const oracles = useQuery({ queryKey: ['oracles'], queryFn: () => getJson<OracleInfo[]>('/oracles') })
  const testGateway = useMutation({
    mutationFn: () => postJson<SettingsPayload['ai']['gateway']>('/ai/test'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
    onError: (e: Error) => setError(e.message),
  })

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchJson<SettingsPayload>('/settings', body),
    onSuccess: () => { setError(null); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: Error) => setError(e.message),
  })

  const s = settings.data
  if (!s) return <div className="state">Loading settings…</div>
  const gateway = testGateway.data ?? s.ai.gateway
  const nautilus = oracles.data?.[0]

  return (
    <section className="stack">
      <div className="section-title">
        <p>OPERATOR SETTINGS</p>
        <h2>What runs, on which model, at what cost</h2>
      </div>

      {error && <p className="warning bad">{error}</p>}

      <StoragePanel />

      <div className="panel">
        <header>
          <h2>AI team · Model routing</h2>
          <div className="panel-actions">
            <button className="btn" onClick={() => testGateway.mutate()} disabled={testGateway.isPending}>
              <RefreshCw /> {testGateway.isPending ? 'Testing...' : 'Test connection'}
            </button>
            <button className={s.ai.enabled ? 'btn primary' : 'btn'} onClick={() => patch.mutate({ ai_enabled: !s.ai.enabled })}>
              {s.ai.enabled ? <Check /> : <X />} {s.ai.enabled ? 'Enabled' : 'Disabled'}
            </button>
          </div>
        </header>
        <div className="panel-body">
          <label className="gateway-url">Provider
            <select
              aria-label="Model provider"
              value={s.ai.provider}
              onChange={(e) => patch.mutate({ ai_provider: e.target.value })}
            >
              {s.providers.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </label>
          <p className="provider-detail">
            {s.providers.find((p) => p.id === s.ai.provider)?.detail}
          </p>

          <div className="gateway-strip">
            <div className={gateway.connected ? 'gateway-light is-up' : 'gateway-light'}><Server /></div>
            <div><span>Provider</span><strong>{gateway.provider}</strong></div>
            <div><span>Status</span><strong className={gateway.connected ? 'good' : 'warn'}>{gateway.connected ? 'CONNECTED' : 'OFFLINE · LOCAL FALLBACK'}</strong></div>
            <div><span>Models</span><strong>{gateway.model_count}</strong></div>
            <div><span>Latency</span><strong>{gateway.latency_ms} ms</strong></div>
            <div><span>Credential</span><strong className={gateway.credential_present ? 'good' : 'warn'}>{gateway.credential_present ? gateway.credential_source : 'not detected'}</strong></div>
          </div>
          <label className="gateway-url">OpenAI-compatible base URL
            <input defaultValue={s.ai.base_url} onBlur={(e) => { if (e.target.value !== s.ai.base_url) patch.mutate({ ai_base_url: e.target.value }) }} />
          </label>
          {gateway.selected && gateway.selected !== 'omniroute' && (
            <p className="warning">
              OmniRoute is not listening at <code>{s.ai.base_url}</code>, so
              <strong> {gateway.selected}</strong> is answering instead. OmniRoute is a local
              gateway process — it has to be running on this machine before it can be used.
            </p>
          )}
          {!gateway.connected && (
            <p className="warning">
              No provider is reachable. Agent roles stay usable through deterministic local-ledger
              answers; no AI claim is substituted for missing evidence.
            </p>
          )}
          <table className="tbl role-table">
            <thead>
              <tr><th>Role</th><th>What it does</th><th>Model</th></tr>
            </thead>
            <tbody>
              {s.roles.map((role) => (
                <tr key={role.key}>
                  <td>{role.label}</td>
                  <td className="muted">{role.detail}</td>
                  <td>
                    <select
                      aria-label={`Model for ${role.label}`}
                      value={s.ai.routing[role.key] ?? 'none'}
                      onChange={(e) => patch.mutate({ routing: { [role.key]: e.target.value } })}
                    >
                      {s.models.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.label}
                          {m.status === 'needs_credit' ? ' · needs credit' : ''}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {s.models.some((m) => m.status === 'needs_credit') && (
            <p className="warning">
              Models marked <b>needs credit</b> are real and correctly configured, but the
              account has no balance, so they answer with a billing error rather than a
              completion.
            </p>
          )}
          <p className="warning">
            Routing exists because the jobs differ in cost profile: hypothesis work is rare and
            hard, tagging is constant and easy. Putting everything on a frontier model is the
            fastest way to spend money on nothing.
          </p>
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <header><h2>Execution limits and budget planning</h2></header>
          <div className="panel-body">
            <p className="sub">Specialist tasks enforce 48 model calls per UTC day, two concurrent model requests, and a 1,600-token response limit. Dollar values below are planning preferences; provider billing is not metered here.</p>
            <div className="stack">
              {([
                ['daily_usd_hard', 'Daily allocation', 'Planning value in USD'],
                ['daily_usd_soft', 'Daily target', 'Planning value in USD'],
                ['monthly_usd_hard', 'Monthly allocation', 'Planning value in USD'],
                ['per_session_usd', 'Per task target', 'Planning value in USD'],
              ] as const).map(([key, label, note]) => (
                <label className="budget-row" key={key}>
                  <span>{label}<small>{note}</small></span>
                  <input
                    type="number" step="0.5" min="0"
                    defaultValue={s.ai.budget[key]}
                    onBlur={(e) => {
                      const value = Number(e.target.value)
                      if (value !== s.ai.budget[key]) patch.mutate({ budget: { [key]: value } })
                    }}
                  />
                </label>
              ))}
            </div>
          </div>
        </div>

        <div className="panel">
          <header><h2><KeyRound size={11} /> Credentials</h2></header>
          <div className="panel-body">
            <table className="tbl">
              <tbody>
                {s.credentials.map((c) => (
                  <tr key={c.key}>
                    <td>{c.label}</td>
                    <td className={c.present ? 'good' : 'bad'}>{c.hint}</td>
                    <td className="muted">{c.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="warning">
              Secret values and paths never cross the API boundary. Only configured state and a
              generic source class are returned to this interface.
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <header>
          <h2>Execution-semantics oracle · NautilusTrader</h2>
          <span className={`oracle-state ${nautilus?.ready ? 'good' : 'warn'}`}>{nautilus?.ready ? 'READY' : 'OPTIONAL / NOT READY'}</span>
        </header>
        <div className="panel-body oracle-layout">
          <div className="oracle-summary">
            <div><span>Installed</span><strong>{nautilus?.installed ? `yes · ${nautilus.version}` : 'no'}</strong></div>
            <div><span>Configured data</span><strong>{nautilus?.configured_data_levels.join(', ') || 'none'}</strong></div>
            <div><span>Role</span><strong>{nautilus?.role.replaceAll('_', ' ') ?? '--'}</strong></div>
            <div><span>Licence</span><strong>{nautilus?.licence ?? '--'}</strong></div>
          </div>
          <div className="oracle-caveats"><span>Boundary conditions</span><ul>{nautilus?.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>
        </div>
        <p className="warning">NautilusTrader is an optional comparison oracle. Installing it does not validate fills, and it has no authority to promote a strategy.</p>
      </div>

      <div className="panel">
        <header><h2>Research defaults</h2></header>
        <div className="panel-body">
          <div className="stack">
            <label className="budget-row">
              <span>Continuous research<small>Runs while the local API is open; independent of the backtest engine</small></span>
              <button className={s.research_loop.enabled ? 'btn primary' : 'btn'} onClick={() => patch.mutate({ research_loop_enabled: !s.research_loop.enabled })}>
                {s.research_loop.enabled ? <Check /> : <Pause />} {s.research_loop.enabled ? 'Enabled' : 'Paused'}
              </button>
            </label>
            <label className="budget-row">
              <span>Research cadence<small>Minutes between bounded evidence scans</small></span>
              <input type="number" step="5" min="5" max="1440" defaultValue={s.research_loop.interval_minutes}
                onBlur={(e) => patch.mutate({ research_interval_minutes: Number(e.target.value) })} />
            </label>
            <label className="budget-row">
              <span>Engine cycle seconds<small>Gap between candidates</small></span>
              <input type="number" step="1" min="1" defaultValue={s.engine_cycle_seconds}
                onBlur={(e) => patch.mutate({ engine_cycle_seconds: Number(e.target.value) })} />
            </label>
            <label className="budget-row">
              <span>Strategy ceiling<small>Engine stops at this many</small></span>
              <input type="number" step="5" min="1" defaultValue={s.engine_max_strategies}
                onBlur={(e) => patch.mutate({ engine_max_strategies: Number(e.target.value) })} />
            </label>
            <label className="budget-row">
              <span>Databento cost ceiling<small>Per request, USD</small></span>
              <input type="number" step="0.5" min="0" defaultValue={s.databento_max_cost_usd}
                onBlur={(e) => patch.mutate({ databento_max_cost_usd: Number(e.target.value) })} />
            </label>
          </div>
        </div>
      </div>

      <UpdatesPanel />
    </section>
  )
}

/** Updates and provenance.
 *
 * This used to be a top-level tab, which put a read-only release checker on the
 * same footing as the strategy workspace. It is a settings concern: nothing on
 * it is part of research, and nothing on it changes without a person acting.
 */
function UpdatesPanel() {
  const evolution = useQuery({
    queryKey: ['evolution'],
    queryFn: () => getJson<Evolution>('/evolution/overview'),
  })
  const e = evolution.data
  return (
    <div className="panel af-panel-in">
      <PanelHead title="Updates and provenance" meta="research intake only" />
      <div className="headline-row">
        <Stat label="Automatic live changes" value={<span className="mono">NEVER</span>} tone="good"
          note="no code path can promote itself without a person" />
        <Stat label="Paper only" value={<span className="mono">{e?.paper_only === false ? 'NO' : 'YES'}</span>}
          tone={e?.paper_only === false ? 'bad' : 'good'} note="no live-order path exists in the repo" />
        <Stat label="Releases tracked" value={<span className="mono">{e?.release_count ?? 0}</span>}
          note="signed build records on disk" />
        <Stat label="Candidate lane" value={<span className="mono">{e?.candidate?.lane ?? '—'}</span>}
          tone="unknown" note="discovered references stay quarantined until reviewed" />
      </div>
      {e?.candidate && (
        <div className="panel-body">
          <p className="sub">
            Watching <b>{e.candidate.repository}</b> as a research reference. Features it proposes
            ({e.candidate.proposed_features.join(', ') || 'none listed'}) are read, never merged
            automatically.
          </p>
        </div>
      )}
    </div>
  )
}

/** Ask questions about this instance. Grounded in the local ledger; falls back to
 *  deterministic answers when no model is configured, and says so. */
export function ConsoleView() {
  const [question, setQuestion] = useState('')
  const [thread, setThread] = useState<{ q: string; a: AskResult }[]>([])

  const ask = useMutation({
    mutationFn: (q: string) => postJson<AskResult>('/ask', { question: q }),
    onSuccess: (a, q) => { setThread((prev) => [...prev, { q, a }]); setQuestion('') },
  })

  const suggestions = [
    'How many strategies are there and are any profitable?',
    'Why did the last candidates get rejected?',
    'What families can you build?',
  ]

  return (
    <section className="stack">
      <div className="section-title">
        <p>CONSOLE</p>
        <h2>Ask about what this instance has actually done</h2>
      </div>

      <div className="panel">
        <div className="panel-body chat-thread">
          {thread.length === 0 && (
            <div className="chat-empty">
              <Bot />
              <p>Grounded in this machine&apos;s ledger — strategies, backtests, verdicts and
                engine activity. It will not predict markets or suggest trades.</p>
              <div className="chat-suggest">
                {suggestions.map((s) => (
                  <button key={s} className="btn tiny" onClick={() => ask.mutate(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}
          {thread.map((turn, i) => (
            <div className="chat-turn" key={i}>
              <p className="chat-q">{turn.q}</p>
              <div className="chat-a">
                <span className="chat-model">{turn.a.model}</span>
                <p>{turn.a.answer}</p>
                {turn.a.note && <small className="chat-note">{turn.a.note}</small>}
              </div>
            </div>
          ))}
          {ask.isPending && <p className="chat-pending">Thinking…</p>}
        </div>
        <form
          className="chat-input"
          onSubmit={(e) => { e.preventDefault(); if (question.trim()) ask.mutate(question.trim()) }}
        >
          <input
            aria-label="Ask a question"
            placeholder="Ask about strategies, verdicts or engine state…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <button className="btn primary" type="submit" disabled={ask.isPending || !question.trim()}>
            <Send /> Ask
          </button>
        </form>
      </div>
    </section>
  )
}
