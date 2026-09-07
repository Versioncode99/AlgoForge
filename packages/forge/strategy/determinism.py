"""Does the execution engine produce the same result twice?

G7 is named "Engine consistency" and its rule string read "oracle tolerance
passes". Nothing compared anything: `engine_consistent` was a dataclass default
of ``True`` that no call site ever overrode, and the module that used to be
called `oracles` was renamed to `capabilities` in an earlier pass precisely
because it evaluates nothing — it reports whether `nautilus_trader` is
importable. So G7 stamped PASS on a tolerance comparison against a reference
engine that is never run, in a system whose own report says "nothing is
calibrated".

There are two different claims hiding under one gate name:

1. **Venue calibration** — do these fills match what a real broker would have
   given? AlgoForge cannot answer this. It has no execution venue, and until a
   run is reconciled against something like NinjaTrader's Strategy Analyzer,
   any answer would be invented. This gate does **not** assert it, and the rule
   string now says so.

2. **Engine determinism** — does re-executing the same strategy over the same
   bars with the same parameters produce the same trades? AlgoForge *can*
   answer this, cheaply, and it is worth answering. A backtest that does not
   reproduce is not evidence about anything: the number in the artifact is one
   draw from an unspecified distribution, every downstream statistic inherits
   that noise, and a "reproducible run snapshot" of it records a result nobody
   can obtain again.

This module implements (2). It can genuinely fail — mutable module-level state
in a strategy, an unseeded RNG, iteration over a set, or a dependence on wall
clock all break it — and those are real defects that currently pass unnoticed.

The comparison is exact, not tolerant. Two runs of deterministic float
arithmetic over identical inputs in one process produce identical bits; a
tolerance would only serve to hide the very state leakage this looks for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from forge.contracts.hashing import content_hash


@dataclass(frozen=True)
class DeterminismReport:
    """Whether a re-run of the same inputs reproduced the same trades."""

    strategy_id: str
    code_hash: str
    first_hash: str
    second_hash: str
    trade_count: int
    bars_checked: int = 0
    divergence: str | None = None
    error: str | None = None

    @property
    def reproduced(self) -> bool | None:
        """``None`` when the check could not be performed at all."""
        if self.error is not None:
            return None
        return self.first_hash == self.second_hash

    @property
    def reason(self) -> str:
        if self.error is not None:
            return f"NOT_CHECKED: {self.error}"
        if self.first_hash != self.second_hash:
            return f"DIVERGED: {self.divergence or 'run digests differ'}"
        return f"REPRODUCED_{self.trade_count}_TRADES_OVER_{self.bars_checked}_BARS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "code_hash": self.code_hash,
            "reproduced": self.reproduced,
            "reason": self.reason,
            "first_hash": self.first_hash,
            "second_hash": self.second_hash,
            "trade_count": self.trade_count,
            "bars_checked": self.bars_checked,
            "divergence": self.divergence,
            "error": self.error,
            # Stated on every report so a reader cannot mistake this for the
            # other claim. The gate says the engine repeats itself. It does not
            # say the fills are realistic.
            "does_not_assert": "venue calibration; fills remain modelled, not reconciled",
        }


def run_digest(result: Any) -> str:
    """A content hash over everything a re-run must reproduce.

    Deliberately excludes `backtest_id`, `started_at` and `finished_at`: those
    differ between two runs by construction and hashing them would make every
    check fail for the wrong reason.
    """
    return content_hash(
        {
            "trades": [
                [
                    trade.direction,
                    trade.entry_decision_index,
                    trade.entry_index,
                    trade.exit_decision_index,
                    trade.exit_index,
                    trade.entry_price,
                    trade.exit_price,
                    trade.gross_pnl,
                    trade.costs,
                    trade.net_pnl,
                    trade.bars_held,
                    trade.exit_reason,
                ]
                for trade in result.trades
            ],
            "equity": list(result.equity),
            "net_pnl": result.net_pnl,
            "gross_pnl": result.gross_pnl,
            "total_costs": result.total_costs,
            "win_rate": result.win_rate,
            "max_drawdown": result.max_drawdown,
            "lookahead_clean": result.lookahead_clean,
        }
    )


def describe_divergence(first: Any, second: Any) -> str:
    """The first concrete difference, so a failure is actionable.

    "The digests differ" tells an operator nothing. "Trade 41 exits at index
    9,213 on one run and 9,220 on the other" tells them where to look.
    """
    if len(first.trades) != len(second.trades):
        return f"trade count {len(first.trades)} then {len(second.trades)}"
    for index, (left, right) in enumerate(zip(first.trades, second.trades, strict=True)):
        for field in ("entry_index", "exit_index", "entry_price", "exit_price", "net_pnl"):
            a, b = getattr(left, field), getattr(right, field)
            if a != b:
                return f"trade {index} {field}: {a!r} then {b!r}"
    if first.net_pnl != second.net_pnl:
        return f"net pnl {first.net_pnl!r} then {second.net_pnl!r}"
    if list(first.equity) != list(second.equity):
        return "equity curve differs with identical trades"
    return "run digests differ outside the compared fields"


def check_determinism(
    strategy_id: str,
    *,
    code_hash: str,
    backtest: Any,
    bars: Sequence[Any],
    parameters: dict[str, float] | None = None,
) -> DeterminismReport:
    """Execute ``backtest`` twice over the same bars and compare exactly.

    ``backtest`` is a zero-context callable taking ``(bars, parameters)`` so
    this module never needs to know how a run is configured — the caller has
    already decided that, and the point is to repeat *its* configuration.

    The caller also chooses how many bars to hand over, and is expected to hand
    over a **window**, not the whole series. Non-determinism is a property of
    the code, not of the data volume: module-level mutable state, an unseeded
    RNG, iteration over a set or a wall-clock read all diverge within a few
    thousand bars exactly as reliably as within a few million. Checking a window
    costs a few percent of a run; checking the full series would double the most
    expensive operation in the system to learn the same fact.

    What that trades away is honest and recorded in ``bars_checked``: a
    divergence that only appears deep into a long series would be missed.
    """
    try:
        first = backtest(bars, parameters)
        second = backtest(bars, parameters)
    except Exception as exc:
        return DeterminismReport(
            strategy_id=strategy_id,
            code_hash=code_hash,
            first_hash="",
            second_hash="",
            trade_count=0,
            bars_checked=len(bars),
            error=f"{type(exc).__name__}: {exc}",
        )

    first_hash, second_hash = run_digest(first), run_digest(second)
    return DeterminismReport(
        strategy_id=strategy_id,
        code_hash=code_hash,
        first_hash=first_hash,
        second_hash=second_hash,
        trade_count=len(first.trades),
        bars_checked=len(bars),
        divergence=None if first_hash == second_hash else describe_divergence(first, second),
    )
