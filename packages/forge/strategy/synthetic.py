from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from forge.data.models import Bar

# Deterministic synthetic bars, used until a real Databento stream is wired.
#
# There is deliberately NO edge embedded in this series. It is a regime-switching
# random walk. A strategy that "works" here is overfitting to a specific seed, and
# the judge is expected to reject nearly everything run against it. That is the
# system behaving correctly, not a data problem: a generator with a planted edge
# would teach the whole pipeline to find things that are not there.


def generate_bars(
    symbol: str = "MNQ.SYNTH",
    count: int = 4000,
    seed: int = 20260901,
    start_price: float = 20000.0,
    start: datetime | None = None,
    minutes: int = 1,
) -> list[Bar]:
    rng = np.random.default_rng(seed)
    begin = start or datetime(2026, 1, 2, 14, 30, tzinfo=UTC)

    # Two-state volatility regime with sticky transitions.
    calm, wild = 0.00035, 0.00110
    stay = 0.995
    state = np.zeros(count, dtype=np.int8)
    for i in range(1, count):
        state[i] = state[i - 1] if rng.random() < stay else 1 - state[i - 1]
    sigma = np.where(state == 0, calm, wild)

    returns = rng.normal(0.0, 1.0, count) * sigma
    close = start_price * np.exp(np.cumsum(returns))

    # Intrabar range scales with the regime, so ATR-based logic sees real variation.
    span = close * sigma * rng.uniform(1.2, 3.0, count)
    open_ = np.empty(count)
    open_[0] = start_price
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + span * rng.uniform(0.1, 0.7, count)
    low = np.minimum(open_, close) - span * rng.uniform(0.1, 0.7, count)
    volume = np.round(rng.lognormal(6.6, 0.45, count) * (1 + state * 0.8))

    tick = 0.25
    quantise = lambda a: np.round(a / tick) * tick  # noqa: E731

    open_, high, low, close = map(quantise, (open_, high, low, close))
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])

    bars: list[Bar] = []
    for i in range(count):
        event = begin + timedelta(minutes=i * minutes)
        bars.append(
            Bar(
                symbol=symbol,
                event_time=event,
                knowledge_time=event + timedelta(seconds=1),
                ingestion_time=event + timedelta(seconds=2),
                open=float(open_[i]),
                high=float(high[i]),
                low=float(low[i]),
                close=float(close[i]),
                volume=float(volume[i]),
                bar_type="TIME",
                source="synthetic-generator",
            )
        )
    return bars
