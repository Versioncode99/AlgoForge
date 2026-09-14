import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, KeyRound, RefreshCw, Server, X } from 'lucide-react'
import { useState } from 'react'
import { getJson, patchJson, postJson } from '../api'
import { AppearancePanel } from '../components/AppearancePanel'
import { ChatPanel, type ChatOpening } from '../components/ChatPanel'
import { StoragePanel } from '../components/StoragePanel'
import { PanelHead, Stat } from '../components/ui'
import {
  BudgetPanel,
  ModelRoutingPanel,
  ResearchPanel,
  RoutingEvidencePanel,
} from './ModelRouting'
import type { Evolution, OracleInfo, SettingsPayload } from '../types'

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

      <AppearancePanel />

      <StoragePanel />

      <div className="panel">
        <header>
          <h2>Provider</h2>
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
          {/* The endpoint is derived from the provider id and is not editable.
              It used to be a text field the server accepted and discarded, so an
              operator could type a URL, watch the save succeed, and have it
              silently revert. Shown as a fact instead. */}
          <p className="muted small">
            Endpoint <code>{s.ai.base_url}</code> — fixed by the provider. Pointing a key
            at the wrong host is how a billing surprise happens, so it is not a field.
          </p>
          {!gateway.connected && (
            <p className="warning">
              No provider is reachable. Agent roles stay usable through deterministic local-ledger
              answers; no AI claim is substituted for missing evidence.
            </p>
          )}
          {s.models.some((m) => m.status === 'needs_credit') && (
            <p className="warning">
              Models marked <b>needs credit</b> are real and correctly configured, but the
              account has no balance, so they answer with a billing error rather than a
              completion.
            </p>
          )}
        </div>
      </div>

      <ModelRoutingPanel settings={s} patch={(body) => patch.mutate(body)} />

      {/* Directly under the assignments it is evidence about. */}
      <RoutingEvidencePanel patch={(body) => patch.mutate(body)} />

      <BudgetPanel settings={s} patch={(body) => patch.mutate(body)} />

      <ResearchPanel settings={s} patch={(body) => patch.mutate(body)} />

      <div className="grid-2">
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
        <header><h2>Engine defaults</h2></header>
        <div className="panel-body">
          <div className="stack">
            <label className="budget-row">
              <span>Engine cycle seconds<small>Gap between candidates</small></span>
              <input type="number" step="1" min="1" defaultValue={s.engine_cycle_seconds}
                onBlur={(e) => patch.mutate({ engine_cycle_seconds: Number(e.target.value) })} />
            </label>
            <label className="budget-row">
              <span>Strategy ceiling<small>Engine retires its weakest survivor at this many</small></span>
              <input type="number" step="5" min="1" defaultValue={s.engine_max_strategies}
                onBlur={(e) => patch.mutate({ engine_max_strategies: Number(e.target.value) })} />
            </label>
            <label className="budget-row">
              <span>Databento cost ceiling<small>Per request, USD. A data cost, not a research budget.</small></span>
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
 *  deterministic answers when no model is configured, and says so.
 *
 *  `opening` is what a link asked the conversation to start knowing -- the
 *  strategy somebody pressed "Ask about this" on. Without one the panel falls
 *  back to whatever the workspace is pointed at, which is the right default and
 *  was, until now, the only thing that ever happened: the panel had accepted an
 *  opening context since it was written and no caller had ever passed one. */
export function ConsoleView({ opening }: { opening?: ChatOpening } = {}) {
  /* The console is the conversation panel at full width.
   *
   * It used to be its own implementation: a thread in `useState`, lost on
   * reload, with no history and no way back to yesterday's research. Pointing
   * it at the same component the workspace panel uses is what stops the two
   * drifting into different products with the same name.
   */
  return (
    <section className="stack console-view">
      <div className="section-title">
        <p>CONSOLE</p>
        <h2>Ask about what this instance has actually done</h2>
      </div>
      <ChatPanel opening={opening} />
    </section>
  )
}
