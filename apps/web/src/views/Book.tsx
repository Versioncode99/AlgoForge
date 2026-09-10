import { Limitations, NotMeasured, StatusPill } from '../components/measures'
import { PanelHead, Stat } from '../components/ui'
import { useFundOperations } from '../fund'

/* Positions, orders and fills — the same book, wherever it is opened from.
 *
 * Normal mode and Prop Firm mode both reach this, and both see exactly what
 * Hedge Fund mode's Operations screen sees, because there is one book. A second
 * "simple" positions view would be a second implementation of position
 * accounting, and the two would eventually disagree about what is open.
 *
 * The simulated label is drawn from the fills rather than from a setting, so it
 * cannot say "real" because somebody changed a preference.
 */

export function BookView() {
  const operations = useFundOperations()
  if (operations.isPending) return <div className="state" role="status">Reading the book…</div>
  if (operations.isError) {
    return (
      <div className="fund-view af-panel-in">
        <NotMeasured
          what="The book could not be read"
          why={(operations.error as Error).message}
        />
      </div>
    )
  }
  const data = operations.data!
  const book = data.book

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        <Stat label="Cash" value={money(book.cash)} />
        <Stat
          label="Realised P&L"
          value={money(book.realised_pnl)}
          tone={book.realised_pnl > 0 ? 'good' : book.realised_pnl < 0 ? 'bad' : 'plain'}
        />
        <Stat label="Open positions" value={book.positions.length} />
        <Stat label="Working orders" value={book.open_orders.length} />
        <Stat label="Commission" value={money(book.commission_paid)} />
        <Stat label="Modelled slippage" value={money(book.slippage_paid)} />
      </section>

      <p className="fund-note">
        <StatusPill label={book.simulated ? 'SIMULATED' : 'MIXED'} tone={book.simulated ? 'warn' : 'bad'} />
        {book.venues.length
          ? `Fills came from: ${book.venues.join(', ')}.`
          : 'No fill has been recorded yet.'}{' '}
        Nothing in this build reaches a broker or a venue.
      </p>

      <section className="measure-panel">
        <PanelHead title="Positions" />
        {book.positions.length === 0 ? (
          <NotMeasured
            what="Flat"
            why="No position is open. Positions appear here when an order clears the pre-trade gate and the OMS fills it."
          />
        ) : (
          <table className="measure-table">
            <thead>
              <tr>
                <th scope="col">Instrument</th>
                <th scope="col">Quantity</th>
                <th scope="col">Side</th>
                <th scope="col">Average price</th>
                <th scope="col">Realised</th>
                <th scope="col">Unrealised</th>
              </tr>
            </thead>
            <tbody>
              {book.positions.map((position) => (
                <tr key={position.symbol} className="measure-row">
                  <th scope="row"><span className="mono">{position.symbol}</span></th>
                  <td className="measure-value">{position.quantity}</td>
                  <td className="measure-status">
                    <StatusPill
                      label={position.quantity > 0 ? 'LONG' : position.quantity < 0 ? 'SHORT' : 'FLAT'}
                      tone={position.quantity === 0 ? 'unknown' : position.quantity > 0 ? 'good' : 'warn'}
                    />
                  </td>
                  <td className="measure-value">
                    {position.average_price.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                  </td>
                  <td className="measure-value">{money(position.realised_pnl)}</td>
                  {/* Not zero. There is no mark-to-market feed in this build, so
                      an open position's unrealised P&L has not been measured —
                      and a zero here would read as "flat", which it is not. */}
                  <td className="measure-value">
                    <span className="figure is-absent">not marked</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="measure-panel">
        <PanelHead title="Orders" meta={`${data.orders.length} recorded`} />
        {data.orders.length === 0 ? (
          <NotMeasured what="No orders" why="Nothing has been submitted through the OMS." />
        ) : (
          <table className="measure-table">
            <thead>
              <tr>
                <th scope="col">Accepted</th>
                <th scope="col">Instrument</th>
                <th scope="col">Side</th>
                <th scope="col">Quantity</th>
                <th scope="col">Status</th>
                <th scope="col">Filled</th>
              </tr>
            </thead>
            <tbody>
              {data.orders.map((order) => (
                <tr key={order.order_id} className="measure-row">
                  <th scope="row"><span className="mono">{order.accepted_at.slice(11, 19)}</span></th>
                  <td className="measure-value mono">{order.symbol}</td>
                  <td className="measure-value">{order.side.toUpperCase()}</td>
                  <td className="measure-value">{order.quantity}</td>
                  <td className="measure-status">
                    <StatusPill
                      label={order.status.toUpperCase()}
                      tone={
                        order.status === 'filled' ? 'good'
                          : order.status === 'rejected' ? 'bad'
                            : order.status === 'cancelled' ? 'unknown' : 'warn'
                      }
                    />
                  </td>
                  <td className="measure-detail">
                    {order.filled_quantity}
                    {order.average_price !== null ? ` @ ${order.average_price}` : ''} · {order.venue}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <Limitations
        items={[
          ...data.limitations,
          'Open positions are shown at their average fill price. Unrealised P&L is not measured, and is reported as absent rather than as zero.',
        ]}
      />
    </div>
  )
}

const money = (value: number) =>
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
