import type { Trade } from './types'

/** Performance statistics computed from a trade list.
 *
 * Modelled on what a Strategy Analyzer summary actually shows, because that is
 * the density a person evaluating a strategy needs: not four headline figures
 * but the decomposition that tells you *why* the headline is what it is. The
 * All / Long / Short split is the important part — a strategy carried entirely
 * by its short side is a different proposition from a balanced one, and a
 * single net figure hides that completely.
 *
 * Computed on the client from the trade list the backtest already returned, so
 * switching between All, Long and Short costs nothing and needs no round trip.
 */
export type Stats = {
  trades: number
  netProfit: number
  grossProfit: number
  grossLoss: number
  commission: number
  profitFactor: number
  winners: number
  losers: number
  scratches: number
  winRate: number
  avgTrade: number
  avgWinner: number
  avgLoser: number
  payoffRatio: number
  largestWinner: number
  largestLoser: number
  maxConsecWinners: number
  maxConsecLosers: number
  maxDrawdown: number
  maxDrawdownPct: number
  maxRunup: number
  sharpe: number
  sortino: number
  calmar: number
  expectancy: number
  avgBarsHeld: number
  avgBarsWinner: number
  avgBarsLoser: number
  longestFlatBars: number
  totalBarsHeld: number
  firstTrade: string | null
  lastTrade: string | null
}

const EMPTY: Stats = {
  trades: 0, netProfit: 0, grossProfit: 0, grossLoss: 0, commission: 0, profitFactor: 0,
  winners: 0, losers: 0, scratches: 0, winRate: 0, avgTrade: 0, avgWinner: 0, avgLoser: 0,
  payoffRatio: 0, largestWinner: 0, largestLoser: 0, maxConsecWinners: 0, maxConsecLosers: 0,
  maxDrawdown: 0, maxDrawdownPct: 0, maxRunup: 0, sharpe: 0, sortino: 0, calmar: 0,
  expectancy: 0, avgBarsHeld: 0, avgBarsWinner: 0, avgBarsLoser: 0, longestFlatBars: 0,
  totalBarsHeld: 0, firstTrade: null, lastTrade: null,
}

export type Side = 'all' | 'long' | 'short'

export function filterSide(trades: Trade[], side: Side): Trade[] {
  if (side === 'all') return trades
  return trades.filter((t) => (side === 'long' ? t.direction === 1 : t.direction === -1))
}

export function computeStats(trades: Trade[], startingEquity = 0): Stats {
  if (!trades.length) return EMPTY

  const pnl = trades.map((t) => t.net_pnl)
  const wins = pnl.filter((v) => v > 0)
  const losses = pnl.filter((v) => v < 0)
  const grossProfit = wins.reduce((a, b) => a + b, 0)
  const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0))
  const netProfit = pnl.reduce((a, b) => a + b, 0)

  // Drawdown and run-up walk the realised curve, so both are in currency and
  // comparable with net profit rather than being a percentage of nothing.
  let running = 0
  let peak = 0
  let trough = 0
  let maxDrawdown = 0
  let maxRunup = 0
  for (const value of pnl) {
    running += value
    peak = Math.max(peak, running)
    trough = Math.min(trough, running)
    maxDrawdown = Math.max(maxDrawdown, peak - running)
    maxRunup = Math.max(maxRunup, running - trough)
  }

  let consecW = 0
  let consecL = 0
  let maxConsecW = 0
  let maxConsecL = 0
  for (const value of pnl) {
    if (value > 0) {
      consecW += 1
      consecL = 0
      maxConsecW = Math.max(maxConsecW, consecW)
    } else if (value < 0) {
      consecL += 1
      consecW = 0
      maxConsecL = Math.max(maxConsecL, consecL)
    } else {
      consecW = 0
      consecL = 0
    }
  }

  const mean = netProfit / pnl.length
  const variance = pnl.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, pnl.length - 1)
  const sd = Math.sqrt(variance)
  const downside = Math.sqrt(
    pnl.reduce((a, b) => a + Math.min(b, 0) ** 2, 0) / pnl.length,
  )

  const barsWin = trades.filter((t) => t.net_pnl > 0).map((t) => t.bars_held)
  const barsLose = trades.filter((t) => t.net_pnl < 0).map((t) => t.bars_held)

  // Flat time between an exit and the next entry, in bars.
  let longestFlat = 0
  for (let i = 1; i < trades.length; i += 1) {
    longestFlat = Math.max(longestFlat, trades[i].entry_index - trades[i - 1].exit_index)
  }

  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0)
  const base = startingEquity > 0 ? startingEquity : Math.max(grossLoss, Math.abs(netProfit), 1)

  return {
    trades: trades.length,
    netProfit,
    grossProfit,
    grossLoss,
    commission: trades.reduce((a, t) => a + t.costs, 0),
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0,
    winners: wins.length,
    losers: losses.length,
    scratches: pnl.length - wins.length - losses.length,
    winRate: wins.length / pnl.length,
    avgTrade: mean,
    avgWinner: avg(wins),
    avgLoser: avg(losses),
    payoffRatio: losses.length && avg(losses) !== 0 ? Math.abs(avg(wins) / avg(losses)) : 0,
    largestWinner: wins.length ? Math.max(...wins) : 0,
    largestLoser: losses.length ? Math.min(...losses) : 0,
    maxConsecWinners: maxConsecW,
    maxConsecLosers: maxConsecL,
    maxDrawdown,
    maxDrawdownPct: maxDrawdown / base,
    maxRunup,
    // Per-trade Sharpe, not annualised: trades are not evenly spaced, so
    // scaling by sqrt(252) would invent a time basis the data does not have.
    sharpe: sd > 0 ? mean / sd : 0,
    sortino: downside > 0 ? mean / downside : 0,
    calmar: maxDrawdown > 0 ? netProfit / maxDrawdown : 0,
    expectancy: mean,
    avgBarsHeld: avg(trades.map((t) => t.bars_held)),
    avgBarsWinner: avg(barsWin),
    avgBarsLoser: avg(barsLose),
    longestFlatBars: longestFlat,
    totalBarsHeld: trades.reduce((a, t) => a + t.bars_held, 0),
    firstTrade: trades[0]?.entry_time ?? null,
    lastTrade: trades[trades.length - 1]?.exit_time ?? null,
  }
}

export type PeriodRow = { key: string; net: number; trades: number; wins: number }

/** Group realised P&L into calendar buckets.
 *
 * A strategy that made all of its money in one quarter and bled for the other
 * fifteen is a different thing from one that ground it out, and only a period
 * breakdown shows which you have.
 */
export function byPeriod(trades: Trade[], grain: 'day' | 'week' | 'month' | 'year'): PeriodRow[] {
  const buckets = new Map<string, PeriodRow>()
  for (const trade of trades) {
    const key = periodKey(trade.exit_time, grain)
    const row = buckets.get(key) ?? { key, net: 0, trades: 0, wins: 0 }
    row.net += trade.net_pnl
    row.trades += 1
    if (trade.net_pnl > 0) row.wins += 1
    buckets.set(key, row)
  }
  return [...buckets.values()].sort((a, b) => a.key.localeCompare(b.key))
}

function periodKey(stamp: string, grain: 'day' | 'week' | 'month' | 'year'): string {
  const iso = stamp.slice(0, 10)
  if (grain === 'day') return iso
  if (grain === 'month') return iso.slice(0, 7)
  if (grain === 'year') return iso.slice(0, 4)
  const date = new Date(`${iso}T00:00:00Z`)
  const day = date.getUTCDay() || 7
  date.setUTCDate(date.getUTCDate() - day + 1)
  return `${date.toISOString().slice(0, 10)} wk`
}

/** Realised equity curve, one point per trade, starting at zero. */
export function equityFrom(trades: Trade[]): number[] {
  const curve: number[] = [0]
  let running = 0
  for (const trade of trades) {
    running += trade.net_pnl
    curve.push(running)
  }
  return curve
}
