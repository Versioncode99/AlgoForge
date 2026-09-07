"""Produce G9's evidence from a completed run.

One helper, shared by the engine and the operator's judge route, so the two
paths cannot drift into testing the mechanism differently — which is how G0 and
G1 ended up real in one place and literal in the other.

The cost model is rebuilt here from the spec and the instrument exactly as
`run_backtest` builds it. A control charged differently from the run it is
compared against would be measuring the cost model, not the mechanism.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from forge.data.models import Bar
from forge.research import MechanismTest, entry_timing_control
from forge.strategy.instruments import contract_units


def round_trip_cost_for(spec: Any, symbol: str) -> tuple[float, float]:
    """``(point_value, round_trip_cost)``, matching `forge.strategy.runtime`."""
    point_value, tick_size = contract_units(symbol)
    per_side = spec.commission_per_side + spec.slippage_ticks * tick_size * point_value
    return point_value, per_side * 2.0


def mechanism_for(
    spec: Any,
    result: Any,
    bars: Sequence[Bar],
    *,
    seed: int,
) -> MechanismTest:
    """Run the entry-timing control for a finished backtest.

    ``bars`` must be the same series the run filled against — the control has to
    be able to have taken the strategy's own trades.
    """
    symbol = bars[0].symbol if bars else spec.symbol
    point_value, round_trip = round_trip_cost_for(spec, symbol)
    return entry_timing_control(
        result.trades,
        [bar.open for bar in bars],
        point_value=point_value,
        round_trip_cost=round_trip,
        warmup_bars=spec.warmup_bars,
        seed=seed,
    )
