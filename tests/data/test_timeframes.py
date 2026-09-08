"""Aggregating one-minute bars, and the session convention behind a daily one.

A chart is where most people will form their beliefs about a strategy, so a bar
that is subtly wrong is worse than a chart that refuses to draw. The properties
here are the ones that would be invisible on screen if they broke: an open taken
from the wrong minute, a gap filled with a price that never traded, a daily bar
straddling two sessions.
"""

from __future__ import annotations

import pandas as pd
import pytest
from forge.data.timeframes import TIMEFRAMES, aggregate, timeframe, trading_dates


def _minutes(start: str, count: int, *, step_minutes: int = 1) -> pd.DataFrame:
    """`count` one-minute bars whose close walks upward by 1 a bar."""
    times = pd.date_range(start, periods=count, freq=f"{step_minutes}min", tz="UTC")
    return pd.DataFrame(
        {
            "event_time": times,
            "open": [100.0 + i for i in range(count)],
            "high": [100.5 + i for i in range(count)],
            "low": [99.5 + i for i in range(count)],
            "close": [100.2 + i for i in range(count)],
            "volume": [10.0] * count,
        }
    )


def test_one_minute_is_a_passthrough() -> None:
    frame = _minutes("2026-01-05 14:00", 10)
    out = aggregate(frame, "1m")
    assert len(out) == 10
    assert list(out["close"]) == list(frame["close"])


def test_a_five_minute_bar_takes_the_right_value_from_each_minute() -> None:
    """open first, high max, low min, close last, volume summed."""
    frame = _minutes("2026-01-05 14:00", 10)
    out = aggregate(frame, "5m")

    assert len(out) == 2
    first = out.iloc[0]
    assert first["open"] == 100.0  # the first minute's open
    assert first["close"] == 104.2  # the fifth minute's close
    assert first["high"] == 104.5  # the highest high in the bucket
    assert first["low"] == 99.5  # the lowest low
    assert first["volume"] == 50.0  # five minutes of ten


def test_buckets_are_anchored_to_the_clock_not_to_the_first_bar() -> None:
    """A 14:03 start belongs to the 14:00 bucket, not to a bucket of its own.

    Anchoring on the first row would make the same minute land in different
    bars depending on how much history was loaded, which is a chart that
    changes shape when you scroll.
    """
    frame = _minutes("2026-01-05 14:03", 5)
    out = aggregate(frame, "5m")
    assert list(out["event_time"].dt.strftime("%H:%M")) == ["14:00", "14:05"]


def test_a_gap_is_left_as_a_gap() -> None:
    """No bar is invented across a break in trading.

    Filling one forward would put a price on the chart that never traded, and
    it would look exactly like a real bar.
    """
    before = _minutes("2026-01-05 14:00", 5)
    after = _minutes("2026-01-05 18:00", 5)
    out = aggregate(pd.concat([before, after], ignore_index=True), "5m")

    assert len(out) == 2
    stamps = list(out["event_time"].dt.strftime("%H:%M"))
    assert stamps == ["14:00", "18:00"]


def test_empty_input_produces_no_bars() -> None:
    empty = _minutes("2026-01-05 14:00", 0)
    assert aggregate(empty, "1h").empty


# ── the daily session ────────────────────────────────────────────────────────


def test_the_evening_open_belongs_to_the_next_trading_day() -> None:
    """18:00 New York starts the session named for the following date.

    This is the property a UTC-midnight bucket gets wrong, and it gets it wrong
    invisibly: the bar still looks like a daily bar.
    """
    # 22:00 UTC on 5 January is 17:00 New York -- still Monday's session.
    # 23:00 UTC is 18:00 New York, which opens Tuesday's.
    times = pd.to_datetime(
        ["2026-01-05 22:00", "2026-01-05 23:30", "2026-01-06 20:00"], utc=True
    )
    dates = trading_dates(pd.Series(times))
    assert [str(d.date()) for d in dates] == ["2026-01-05", "2026-01-06", "2026-01-06"]


def test_a_daily_bar_spans_the_whole_session_not_the_calendar_day() -> None:
    evening = _minutes("2026-01-05 23:00", 60)  # 18:00 NY, opens the 6th
    morning = _minutes("2026-01-06 15:00", 60)  # 10:00 NY, same session
    out = aggregate(pd.concat([evening, morning], ignore_index=True), "1d")

    assert len(out) == 1, "one session, one bar"
    assert out.iloc[0]["open"] == 100.0
    assert out.iloc[0]["volume"] == 1200.0


def test_the_daily_timeframe_states_its_convention() -> None:
    """A boundary nobody can look up is a number nobody can check."""
    note = timeframe("1d").note
    assert "18:00" in note
    assert "America/New_York" in note


def test_every_timeframe_is_labelled(  ) -> None:
    for key, spec in TIMEFRAMES.items():
        assert spec.key == key
        assert spec.label


def test_an_unknown_timeframe_is_refused_with_the_list() -> None:
    with pytest.raises(KeyError, match="1w"):
        timeframe("1w")
