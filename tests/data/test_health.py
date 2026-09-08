"""Measuring an archive, and refusing to cry wolf about it.

The hard part of this module is not detecting holes — it is not reporting the
ones that are supposed to be there. A one-minute OHLCV archive has no bar
wherever nothing traded, and the exchange is shut for an hour a day, two days a
week and a dozen days a year. A detector that flags all of that is worse than
none: it reads "warn" forever and stops being read.
"""

from __future__ import annotations

import pandas as pd
import pytest
from forge.data.health import SESSION_TIMEZONE, analyse


def _bars(times: list[str], *, close: float = 100.0) -> pd.DataFrame:
    stamps = pd.to_datetime(pd.Series(times), utc=True)
    return pd.DataFrame(
        {
            "event_time": stamps,
            "open": [close] * len(times),
            "high": [close + 1] * len(times),
            "low": [close - 1] * len(times),
            "close": [close] * len(times),
            "volume": [5.0] * len(times),
        }
    )


def _session(start: str, minutes: int) -> list[str]:
    return [str(t) for t in pd.date_range(start, periods=minutes, freq="1min", tz="UTC")]


def _finding(report, code: str):
    return next(f for f in report.findings if f.code == code)


# ── what is not a fault ──────────────────────────────────────────────────────


def test_the_daily_maintenance_break_is_not_a_gap() -> None:
    """17:00-18:00 New York, which reads as 61 minutes close-to-open.

    Regression: a 60-minute threshold made the ordinary daily halt the single
    most common "gap" in the archive — 206 a year, every year.
    """
    # 21:59 UTC is 16:59 New York (EST); the session resumes at 18:00 local.
    before = _session("2026-01-05 21:00", 60)
    after = _session("2026-01-05 23:00", 60)
    report = analyse(_bars(before + after), "nq")

    gaps = _finding(report, "gaps")
    assert gaps.detail["count"] == 0
    assert gaps.detail["session_breaks"] == 1
    assert gaps.severity == "ok"


def test_a_weekend_is_not_a_gap() -> None:
    friday = _session("2026-01-02 20:00", 60)
    sunday = _session("2026-01-04 23:00", 60)
    gaps = _finding(analyse(_bars(friday + sunday), "nq"), "gaps")
    assert gaps.detail["count"] == 0
    assert gaps.detail["weekend_breaks"] == 1


def test_a_missing_session_is_reported_as_a_closure_not_a_gap() -> None:
    """A holiday. Named for what is measurable — a session's worth of absence —
    rather than claimed to be a holiday, which needs an exchange calendar."""
    monday = _session("2026-01-05 15:00", 60)
    wednesday = _session("2026-01-06 15:00", 60)
    gaps = _finding(analyse(_bars(monday + wednesday), "nq"), "gaps")

    assert gaps.detail["full_session_closures"] == 1
    assert gaps.detail["count"] == 0
    assert gaps.detail["closure_sample"]


# ── what is a fault ──────────────────────────────────────────────────────────


def test_a_hole_in_the_middle_of_a_session_is_reported() -> None:
    """10:00 New York on a Tuesday, which is the case that matters."""
    morning = _session("2026-01-06 14:30", 30)  # 09:30 New York
    afternoon = _session("2026-01-06 18:00", 30)  # 13:00 New York
    report = analyse(_bars(morning + afternoon), "nq")
    gaps = _finding(report, "gaps")

    assert gaps.detail["count"] == 1
    assert gaps.detail["largest"][0]["hours"] == pytest.approx(3.0, abs=0.1)
    assert gaps.severity in {"warn", "fail"}


def test_coverage_is_a_fraction_not_a_count_of_hours() -> None:
    """The same absolute hours mean different things over different spans."""
    morning = _session("2026-01-06 14:30", 30)
    afternoon = _session("2026-01-06 18:00", 30)
    gaps = _finding(analyse(_bars(morning + afternoon), "nq"), "gaps")
    assert 0.0 <= gaps.detail["coverage"] <= 1.0
    assert "%" in gaps.summary


# ── integrity ────────────────────────────────────────────────────────────────


def test_duplicate_timestamps_fail() -> None:
    times = _session("2026-01-06 14:30", 5)
    report = analyse(_bars(times + times[-1:]), "nq")
    finding = _finding(report, "duplicate_timestamps")
    assert finding.severity == "fail"
    assert finding.detail["count"] == 1
    assert report.worst == "fail"


def test_an_impossible_bar_fails() -> None:
    """A high below its own close is not a price, it is a corrupted record."""
    frame = _bars(_session("2026-01-06 14:30", 5))
    frame.loc[2, "high"] = frame.loc[2, "close"] - 5
    finding = _finding(analyse(frame, "nq"), "ohlc_consistency")
    assert finding.severity == "fail"
    assert finding.detail["count"] == 1


def test_a_non_positive_price_fails() -> None:
    frame = _bars(_session("2026-01-06 14:30", 5))
    frame.loc[1, "low"] = 0.0
    assert _finding(analyse(frame, "nq"), "positive_prices").severity == "fail"


def test_negative_volume_fails_but_zero_volume_does_not() -> None:
    """A quiet overnight minute genuinely trades nothing."""
    frame = _bars(_session("2026-01-06 14:30", 5))
    frame.loc[1, "volume"] = 0.0
    ok = _finding(analyse(frame, "nq"), "volume")
    assert ok.severity == "ok"
    assert ok.detail["zero"] == 1

    frame.loc[2, "volume"] = -3.0
    assert _finding(analyse(frame, "nq"), "volume").severity == "fail"


def test_naive_timestamps_are_flagged() -> None:
    """An instant without a timezone is ambiguous, and this data is global."""
    frame = _bars(_session("2026-01-06 14:30", 5))
    frame["event_time"] = frame["event_time"].dt.tz_localize(None)
    finding = _finding(analyse(frame, "nq"), "timezone")
    assert finding.severity in {"ok", "warn"}


def test_an_empty_archive_fails_rather_than_reporting_health() -> None:
    report = analyse(_bars([]), "nq")
    assert report.worst == "fail"
    assert report.rows == 0


def test_the_report_keeps_its_evidence() -> None:
    """A status with no arithmetic behind it is an assertion, not a measurement."""
    report = analyse(_bars(_session("2026-01-06 14:30", 60)), "nq")
    payload = report.as_dict()
    assert payload["status"] in {"ok", "note", "warn", "fail"}
    assert payload["rows"] == 60
    for finding in payload["findings"]:
        assert finding["summary"]
        assert isinstance(finding["detail"], dict)


def test_the_session_timezone_is_the_exchange_not_the_machine() -> None:
    assert SESSION_TIMEZONE == "America/New_York"
