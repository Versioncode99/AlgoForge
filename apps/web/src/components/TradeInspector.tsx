import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import type { TradeRow } from './StrategyChart'

/* Why this trade happened, and where the claim comes from.
 *
 * Every field here is read off the backtest artifact. Nothing is recomputed
 * against today's bars, because a stop recomputed now is not the stop the trade
 * ran under and an MFE recomputed now is not the excursion that was measured.
 * Where a run predates a measurement, the field says "not recorded" — an
 * unrecorded MFE and an MFE of zero are different claims and the inspector must
 * not collapse them.
 */

type TradeDetail = {
  trade: TradeRow
  holding_seconds: number
  regime: {
    entry?: string
    entry_label?: string
    exit?: string
    dominant?: string
    dominant_share?: number
    entry_volatility?: number | null
    entry_trend_strength?: number | null
    entry_vol_percentile?: number | null
    percentile_basis?: string
    settings?: Record<string, unknown>
    series_fingerprint?: string
    unavailable?: string
  }
  strategy: {
    strategy_id: string
    name: string
    family: string | null
    symbol: string | null
    hypothesis: string | null
  }
  provenance: Record<string, unknown>
}

const money = (value: number | null | undefined) =>
  value === null || value === undefined
    ? null
    : `${value >= 0 ? '+' : '−'}$${Math.abs(value).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`

const price = (value: number | null | undefined) =>
  value === null || value === undefined ? null : value.toFixed(2)

function duration(totalSeconds: number) {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return '—'
  const minutes = Math.round(totalSeconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours < 24) return rest ? `${hours}h ${rest}m` : `${hours}h`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

/** A value that may not have been recorded. Never renders a zero for absence. */
function Field({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string | null
  hint?: string
  tone?: 'up' | 'down'
}) {
  return (
    <div className="ti-field">
      <span className="ti-label">{label}</span>
      {value === null ? (
        <span className="ti-value absent" title={hint}>
          not recorded
        </span>
      ) : (
        <span className={`ti-value mono${tone ? ` ${tone}` : ''}`}>{value}</span>
      )}
      {hint && value !== null && <span className="ti-hint">{hint}</span>}
    </div>
  )
}

export function TradeInspector({
  strategyId,
  backtestId,
  trade,
  onClose,
  onStep,
  position,
}: {
  strategyId: string
  backtestId: string
  trade: TradeRow
  onClose: () => void
  onStep: (delta: number) => void
  position: { index: number; total: number }
}) {
  const detail = useQuery({
    queryKey: ['trade', strategyId, backtestId, trade.trade_id],
    queryFn: () =>
      getJson<TradeDetail>(
        `/strategies/${strategyId}/trades/${trade.trade_id}?backtest_id=${backtestId}`,
      ),
    staleTime: 5 * 60_000,
  })

  const regime = detail.data?.regime ?? {}
  const held = detail.data?.holding_seconds ?? 0
  const context = Object.entries(trade.entry_context)

  return (
    <aside className="trade-inspector af-glass" aria-label={`Trade ${trade.sequence}`}>
      <header className="ti-head">
        <div>
          <span className="ti-eyebrow mono">TRADE #{trade.sequence}</span>
          <h3 className={trade.side === 'long' ? 'long' : 'short'}>
            {trade.side.toUpperCase()} · {trade.exit_reason}
          </h3>
        </div>
        <div className="ti-step">
          <button
            type="button"
            onClick={() => onStep(-1)}
            disabled={position.index <= 0}
            aria-label="Previous trade"
          >
            ‹
          </button>
          <span className="mono">
            {position.index + 1}/{position.total}
          </span>
          <button
            type="button"
            onClick={() => onStep(1)}
            disabled={position.index >= position.total - 1}
            aria-label="Next trade"
          >
            ›
          </button>
          <button type="button" className="ti-close" onClick={onClose} aria-label="Close inspector">
            ×
          </button>
        </div>
      </header>

      <div className="ti-pnl">
        <span className={trade.net_pnl >= 0 ? 'up' : 'down'}>{money(trade.net_pnl)}</span>
        <span className="ti-sub mono">
          gross {money(trade.gross_pnl)} · costs {money(-Math.abs(trade.costs))}
        </span>
      </div>

      <section className="ti-grid">
        <Field label="Entry" value={`${trade.entry_time.replace('T', ' ').slice(0, 19)}`} />
        <Field label="Entry price" value={price(trade.entry_price)} />
        <Field label="Exit" value={`${trade.exit_time.replace('T', ' ').slice(0, 19)}`} />
        <Field label="Exit price" value={price(trade.exit_price)} />
        <Field label="Holding" value={`${duration(held)} · ${trade.bars_held} bars`} />
        <Field
          label="Regime"
          value={regime.entry_label ?? trade.regime_label ?? null}
          hint={
            regime.dominant_share !== undefined
              ? `dominant ${regime.dominant} for ${(regime.dominant_share * 100).toFixed(0)}% of the hold`
              : undefined
          }
        />
      </section>

      <section className="ti-section">
        <h4>Excursion</h4>
        <div className="ti-grid">
          <Field
            label="MFE"
            value={money(trade.mfe)}
            tone="up"
            hint="Best the position reached, gross of costs"
          />
          <Field
            label="MAE"
            value={money(trade.mae)}
            tone="down"
            hint="Worst the position reached, gross of costs"
          />
        </div>
        {trade.mfe !== null && trade.gross_pnl !== null && trade.mfe > trade.gross_pnl && (
          <p className="ti-note">
            Gave back {money(trade.mfe - trade.gross_pnl)} from its best point.
          </p>
        )}
      </section>

      <section className="ti-section">
        <h4>Levels in force</h4>
        <div className="ti-grid">
          <Field
            label="Stop"
            value={price(trade.stop_price)}
            hint="Frozen when the position opened"
          />
          <Field label="Target" value={price(trade.target_price)} />
          <Field label="Trailing stop" value={price(trade.trailing_stop_price)} />
        </div>
      </section>

      {context.length > 0 && (
        <section className="ti-section">
          <h4>At the decision bar</h4>
          <table className="ti-table">
            <tbody>
              {context.map(([name, value]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td className="num mono">{value.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="ti-note">
            The values the strategy read one bar before the fill. This is what it decided on.
          </p>
        </section>
      )}

      {regime.entry_vol_percentile !== null && regime.entry_vol_percentile !== undefined && (
        <section className="ti-section">
          <h4>Market at entry</h4>
          <div className="ti-grid">
            <Field
              label="Volatility"
              value={regime.entry_volatility?.toFixed(4) ?? null}
              hint={`${regime.entry_vol_percentile.toFixed(1)}th percentile`}
            />
            <Field
              label="Trend strength"
              value={regime.entry_trend_strength?.toFixed(3) ?? null}
              hint="(close − trailing mean) ÷ volatility"
            />
          </div>
          {regime.percentile_basis && <p className="ti-note">{regime.percentile_basis}</p>}
        </section>
      )}

      {regime.unavailable && (
        <p className="ti-note warn">Regime unavailable: {regime.unavailable}</p>
      )}

      <section className="ti-section ti-provenance">
        <h4>Provenance</h4>
        <dl>
          {(
            [
              ['Strategy', detail.data?.strategy.name ?? strategyId],
              ['Run', String(detail.data?.provenance.backtest_id ?? backtestId)],
              ['Dataset', String(detail.data?.provenance.dataset_key ?? '—')],
              ['Partition', String(detail.data?.provenance.partition_name ?? '—')],
              ['Tier', String(detail.data?.provenance.evidence_tier ?? '—')],
              ['Spec', String(detail.data?.provenance.spec_hash ?? '—').slice(0, 16)],
              ['Code', String(detail.data?.provenance.code_hash ?? '—').slice(0, 16)],
              ['Data', String(detail.data?.provenance.data_hash ?? '—').slice(0, 16)],
            ] as [string, string][]
          ).map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className="mono">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </aside>
  )
}
