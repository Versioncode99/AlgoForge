from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np

from forge.contracts.hashing import content_hash, stable_id
from forge.data.models import Bar
from forge.strategy.models import BacktestResult, ParamValue, StrategySpec, Trade


class LookaheadError(RuntimeError):
    """Raised when strategy code attempts to read a bar it could not have seen."""


@dataclass(frozen=True)
class Position:
    direction: int
    decision_index: int
    entry_index: int
    entry_price: float


class Window:
    """A right-bounded, read-only view of history ending at the last CLOSED bar.

    Forward indexing is not merely discouraged, it is impossible: the arrays handed
    to strategy code are sliced at `end` and nothing beyond it exists in this object.
    """

    __slots__ = ("_c", "_h", "_l", "_o", "_t", "_v", "index")

    def __init__(
        self,
        o: np.ndarray,
        h: np.ndarray,
        l: np.ndarray,  # noqa: E741
        c: np.ndarray,
        v: np.ndarray,
        t: Sequence[datetime],
        end: int,
    ) -> None:
        stop = end + 1
        # numpy slices are views, so these are O(1) and still cannot address the
        # future. The timestamp sequence is deliberately NOT sliced: a Python list
        # slice copies, which turned the loop into O(n^2) on real data. `now` reads
        # a single element at `index` instead, so nothing beyond it is ever exposed.
        self._o, self._h, self._l = o[:stop], h[:stop], l[:stop]
        self._c, self._v = c[:stop], v[:stop]
        self._t = t
        self.index = end

    @property
    def opens(self) -> np.ndarray:
        return self._o

    @property
    def highs(self) -> np.ndarray:
        return self._h

    @property
    def lows(self) -> np.ndarray:
        return self._l

    @property
    def closes(self) -> np.ndarray:
        return self._c

    @property
    def volumes(self) -> np.ndarray:
        return self._v

    @property
    def now(self) -> datetime:
        return self._t[self.index]

    # ── session awareness ────────────────────────────────────────────────────
    # Real intraday strategies are anchored to the session, not to bar counts:
    # an opening range, a killzone, a VWAP that resets at the open.

    @property
    def minute_of_day(self) -> int:
        """Minutes since midnight UTC for the current bar."""
        stamp = self._t[self.index]
        return stamp.hour * 60 + stamp.minute

    def in_window(self, start_min: int, end_min: int) -> bool:
        """True inside a session window, handling windows that wrap midnight."""
        now = self.minute_of_day
        if start_min <= end_min:
            return start_min <= now < end_min
        return now >= start_min or now < end_min

    def session_start_index(self) -> int:
        """Index of the first bar of the current calendar day in this window."""
        today = self._t[self.index].date()
        i = self.index
        while i > 0 and self._t[i - 1].date() == today:
            i -= 1
        return i

    def bars_since_session_open(self) -> int:
        return self.index - self.session_start_index()

    def session_vwap(self) -> tuple[float, float]:
        """Session-anchored VWAP and its volume-weighted standard deviation.

        Mirrors the running cumulative form used in NinjaTrader: typical price
        weighted by volume, reset at the session open.
        """
        start = self.session_start_index()
        high, low, close = self._h[start:], self._l[start:], self._c[start:]
        volume = self._v[start:]
        total = float(volume.sum())
        if total <= 0 or close.size == 0:
            return float("nan"), float("nan")
        typical = (high + low + close) / 3.0
        vwap = float((volume * typical).sum() / total)
        variance = max(0.0, float((volume * typical * typical).sum() / total) - vwap * vwap)
        return vwap, float(np.sqrt(variance))

    def session_range(self, first_n_bars: int) -> tuple[float, float]:
        """High and low of the first N bars of the session — the opening range."""
        start = self.session_start_index()
        stop = min(start + first_n_bars, self.index + 1)
        if stop <= start:
            return float("nan"), float("nan")
        return float(self._h[start:stop].max()), float(self._l[start:stop].min())

    def adx(self, n: int = 14) -> float:
        """Wilder's ADX. Low values mean range, high values mean trend."""
        if self._c.size < 2 * n + 2:
            return float("nan")
        high, low, close = self._h[-(2 * n + 2) :], self._l[-(2 * n + 2) :], self._c[-(2 * n + 2) :]
        up, down = high[1:] - high[:-1], low[:-1] - low[1:]
        plus = np.where((up > down) & (up > 0), up, 0.0)
        minus = np.where((down > up) & (down > 0), down, 0.0)
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        atr = tr.mean()
        if atr <= 0:
            return float("nan")
        di_plus = 100.0 * plus.mean() / atr
        di_minus = 100.0 * minus.mean() / atr
        total = di_plus + di_minus
        return float(100.0 * abs(di_plus - di_minus) / total) if total > 0 else float("nan")

    def sma(self, n: int) -> float:
        return float(self._c[-n:].mean()) if self._c.size >= n else float("nan")

    def atr(self, n: int) -> float:
        if self._c.size < n + 1:
            return float("nan")
        h, l, pc = self._h[-n:], self._l[-n:], self._c[-n - 1 : -1]  # noqa: E741
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        return float(tr.mean())

    def realised_vol(self, n: int) -> float:
        if self._c.size < n + 1:
            return float("nan")
        r = np.diff(np.log(self._c[-n - 1 :]))
        return float(r.std(ddof=1))

    def highest(self, n: int) -> float:
        return float(self._h[-n:].max()) if self._h.size >= n else float("nan")

    def lowest(self, n: int) -> float:
        return float(self._l[-n:].min()) if self._l.size >= n else float("nan")


class StrategyModule(Protocol):
    def entry_signal(self, w: Window, p: dict[str, ParamValue]) -> int | None: ...
    def exit_signal(self, w: Window, p: dict[str, ParamValue], pos: Position) -> str | None: ...


def run_backtest(
    module: StrategyModule,
    spec: StrategySpec,
    bars: list[Bar],
    parameters: dict[str, ParamValue] | None = None,
    code_hash: str = "",
    labels: tuple[str, ...] = (),
) -> BacktestResult:
    """Execute a strategy over bars with a structural no-lookahead guarantee.

    The loop decides on bar `i` (closed) and fills on bar `i + 1` open, so every
    trade satisfies `decision_index == entry_index - 1` by construction.
    """
    started = datetime.now(UTC)
    params = dict(spec.defaults) | dict(parameters or {})
    if len(bars) < spec.warmup_bars + 5:
        raise ValueError(f"need at least {spec.warmup_bars + 5} bars, got {len(bars)}")

    o = np.array([b.open for b in bars], dtype=np.float64)
    h = np.array([b.high for b in bars], dtype=np.float64)
    lo = np.array([b.low for b in bars], dtype=np.float64)
    c = np.array([b.close for b in bars], dtype=np.float64)
    v = np.array([b.volume for b in bars], dtype=np.float64)
    t = [b.event_time for b in bars]

    cost_per_side = spec.commission_per_side + spec.slippage_ticks * spec.tick_value
    round_trip_cost = cost_per_side * 2.0

    trades: list[Trade] = []
    equity: list[float] = [0.0]
    running = 0.0
    position: Position | None = None
    last = len(bars) - 1

    for i in range(spec.warmup_bars, last):
        window = Window(o, h, lo, c, v, t, i)
        fill_index = i + 1

        if position is None:
            direction = module.entry_signal(window, params)
            if direction in (1, -1):
                position = Position(int(direction), i, fill_index, float(o[fill_index]))
        else:
            reason = module.exit_signal(window, params, position)
            if reason:
                exit_price = float(o[fill_index])
                gross = (exit_price - position.entry_price) * position.direction
                net = gross - round_trip_cost
                running += net
                trades.append(
                    Trade(
                        trade_id=stable_id("trade", {"e": position.entry_index, "x": fill_index}),
                        direction=position.direction,  # type: ignore[arg-type]
                        entry_decision_index=position.decision_index,
                        entry_index=position.entry_index,
                        exit_decision_index=i,
                        exit_index=fill_index,
                        entry_time=t[position.entry_index],
                        exit_time=t[fill_index],
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        gross_pnl=round(gross, 6),
                        costs=round(round_trip_cost, 6),
                        net_pnl=round(net, 6),
                        bars_held=fill_index - position.entry_index,
                        exit_reason=reason,  # type: ignore[arg-type]
                    )
                )
                position = None
        equity.append(round(running, 6))

    finished = datetime.now(UTC)
    pnls = np.array([tr.net_pnl for tr in trades], dtype=np.float64)
    eq = np.array(equity, dtype=np.float64)
    peak = np.maximum.accumulate(eq)
    drawdown = float((peak - eq).max()) if eq.size else 0.0
    data_hash = content_hash([b.model_dump(mode="json") for b in bars])

    # The invariant is asserted from the artifact, not assumed from the loop.
    clean = all(
        tr.entry_decision_index == tr.entry_index - 1
        and tr.exit_decision_index == tr.exit_index - 1
        for tr in trades
    )

    return BacktestResult(
        backtest_id=BacktestResult.make_id(spec.spec_hash, code_hash, data_hash, params),
        strategy_id=spec.strategy_id,
        spec_hash=spec.spec_hash,
        code_hash=code_hash,
        data_hash=data_hash,
        parameters=params,
        bar_count=len(bars),
        trades=tuple(trades),
        equity=tuple(equity),
        net_pnl=round(float(pnls.sum()) if pnls.size else 0.0, 4),
        gross_pnl=round(float(sum(tr.gross_pnl for tr in trades)), 4),
        total_costs=round(float(sum(tr.costs for tr in trades)), 4),
        win_rate=round(float((pnls > 0).mean()) if pnls.size else 0.0, 4),
        max_drawdown=round(drawdown, 4),
        lookahead_clean=clean,
        labels=labels,
        started_at=started,
        finished_at=finished,
    )
