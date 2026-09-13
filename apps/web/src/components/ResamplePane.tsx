import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import { PathBand } from '../charts'
import { money, pct } from '../lib'
import { readGap, rows, type ResampleComparison } from '../resample'
import { Empty, PanelHead, Stat } from './ui'

/** One backtest read as a distribution, twice, and the difference between them.
 *
 * The route behind this has existed and been correct for some time with nothing
 * rendering it, which is how a whole analysis stays invisible: nothing fails,
 * because nothing runs it. The comparison is the product here rather than
 * either resampler on its own -- an IID study of a strategy whose losses
 * cluster is not slightly optimistic, it is optimistic in precisely the case
 * that matters.
 */
export function ResamplePane({ strategyId }: { strategyId: string }) {
  const query = useQuery({
    queryKey: ['resample', strategyId],
    queryFn: () => getJson<ResampleComparison>(`/strategies/${strategyId}/resample`),
    retry: false,
  })

  if (query.isPending) return <p className="muted">Resampling…</p>
  if (query.isError) {
    return (
      <Empty
        title="Nothing to resample"
        detail={
          query.error instanceof Error
            ? query.error.message
            : 'This strategy has no backtest with enough trades to resample.'
        }
      />
    )
  }

  const comparison = query.data
  const gap = readGap(comparison)
  const table = rows(comparison)
  const regime = comparison.regime_aware

  return (
    <div className="stack">
      <div className="headline-row">
        <Stat label="Clustering premium" value={
          <span className="mono">{gap.gap === null ? '—' : money(gap.gap)}</span>
        } tone={gap.tone} note={gap.headline} />
        <Stat label="Worst case, IID"
          value={<span className="mono">{money(comparison.iid.p95_max_drawdown)}</span>}
          note="p95 drawdown with the trades shuffled independently" />
        <Stat label="Worst case, regime-aware"
          value={<span className="mono">{regime ? money(regime.p95_max_drawdown) : '—'}</span>}
          tone={regime ? 'plain' : 'unknown'}
          note={regime ? 'p95 drawdown with the runs kept' : 'no chain could be fitted'} />
        <Stat label="Trades resampled"
          value={<span className="mono">{comparison.trade_count.toLocaleString()}</span>}
          note={`${comparison.iid.paths.toLocaleString()} paths per method, fixed seed`} />
      </div>

      <p className="fan-note">{gap.detail}</p>

      <div className="panel">
        <PanelHead title="The two studies, side by side" meta="same trades, different ordering" />
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Figure</th>
                <th className="num">IID</th>
                <th className="num">Regime-aware</th>
                <th className="num">Difference</th>
              </tr>
            </thead>
            <tbody>
              {table.map((row) => {
                const show = (value: number) =>
                  row.kind === 'money' ? money(value) : pct(value)
                return (
                  <tr key={row.key}>
                    <th scope="row">{row.label}</th>
                    <td className="mono num">{show(row.iid)}</td>
                    {/* Empty, never a repeat of the IID number: a column that
                        borrowed the other one's value would read as a measured
                        agreement between two studies, one of which never ran. */}
                    <td className="mono num">{row.regime === null ? '—' : show(row.regime)}</td>
                    <td className="mono num sub">
                      {row.regime === null ? '—' : show(row.regime - row.iid)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <PanelHead title="IID resample" meta="p05 · median · p95" />
          <div className="panel-body">
            <PathBand
              p05={comparison.iid.p05_path}
              median={comparison.iid.median_path}
              p95={comparison.iid.p95_path}
            />
          </div>
        </div>
        <div className="panel">
          <PanelHead title="Regime-aware resample" meta={regime ? 'p05 · median · p95' : 'not measured'} />
          <div className="panel-body">
            {regime ? (
              <PathBand p05={regime.p05_path} median={regime.median_path} p95={regime.p95_path} />
            ) : (
              <p className="muted">
                Too few transitions to fit a regime chain. Drawing the IID band here instead would
                show two studies where there was one.
              </p>
            )}
          </div>
        </div>
      </div>

      {(comparison.warnings.length > 0 || comparison.pooled.length > 0) && (
        <div className="panel">
          <PanelHead title="What the resampler had to work around"
            meta={`${comparison.warnings.length + comparison.pooled.length} noted`} />
          <ul className="prop-reasons">
            {comparison.pooled.map((item) => (
              <li key={`pooled-${item}`}>
                <span className="mono">{item}</span>
                <strong>pooled</strong>
                <em>too few trades</em>
              </li>
            ))}
            {comparison.warnings.map((warning) => (
              <li key={warning}>
                <span>{warning}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="fan-note">
        Every resampled path is built from this strategy's own realised trades. Resampling changes
        the order and never the values, so nothing here is a number the backtest did not produce.
      </p>
    </div>
  )
}
