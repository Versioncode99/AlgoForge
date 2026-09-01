import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, KeyRound, Send, X } from 'lucide-react'
import { useState } from 'react'
import { getJson, patchJson, postJson } from '../api'
import type { AskResult, SettingsPayload } from '../types'

export function SettingsView() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => getJson<SettingsPayload>('/settings') })

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchJson<SettingsPayload>('/settings', body),
    onSuccess: () => { setError(null); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: Error) => setError(e.message),
  })

  const s = settings.data
  if (!s) return <div className="state">Loading settings…</div>
  const anthropic = s.credentials.find((c) => c.key === 'ANTHROPIC_API_KEY')

  return (
    <section className="stack">
      <div className="section-title">
        <p>OPERATOR SETTINGS</p>
        <h2>What runs, on which model, at what cost</h2>
      </div>

      {error && <p className="warning bad">{error}</p>}

      <div className="panel">
        <header>
          <h2>AI agents</h2>
          <button
            className={s.ai.enabled ? 'btn primary' : 'btn'}
            onClick={() => patch.mutate({ ai_enabled: !s.ai.enabled })}
          >
            {s.ai.enabled ? <Check /> : <X />} {s.ai.enabled ? 'Enabled' : 'Disabled'}
          </button>
        </header>
        <div className="panel-body">
          {!anthropic?.present && (
            <p className="warning">
              No Anthropic key is set, so every role falls back to the local ledger. Add
              <code> ANTHROPIC_API_KEY</code> to <code>F:\AlgoForge\.env</code> and restart.
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
                        <option key={m.id} value={m.id}>{m.label}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="warning">
            Routing exists because the jobs differ in cost profile: hypothesis work is rare and
            hard, tagging is constant and easy. Putting everything on a frontier model is the
            fastest way to spend money on nothing.
          </p>
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <header><h2>Budget ceilings</h2></header>
          <div className="panel-body">
            <div className="stack">
              {([
                ['daily_usd_hard', 'Daily hard ceiling', 'All agent work halts'],
                ['daily_usd_soft', 'Daily soft ceiling', 'Frontier calls downgrade'],
                ['monthly_usd_hard', 'Monthly hard ceiling', 'Manual reset required'],
                ['per_session_usd', 'Per session', 'One agent invocation'],
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
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="warning">
              Keys are read from <code>.env</code> and the vault key file at startup. They are
              never sent to the interface — only whether one is present and how long it is.
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <header><h2>Research defaults</h2></header>
        <div className="panel-body">
          <div className="stack">
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
    </section>
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
