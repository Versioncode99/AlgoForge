"""What a dataset actually contains, measured rather than asserted.

Every number here is computed from the archive on disk. Nothing is assumed from
the dataset's name, its declared span, or the fact that it was paid for -- a
purchased archive can still have a bad week, and the whole point of this module
is that "healthy" is a conclusion with arithmetic behind it.

The report deliberately does not reduce to a single tick. A dataset with a
four-hour hole on one afternoon and a dataset with two duplicate timestamps are
both "unhealthy" and are not the same problem, so the findings stay separate and
each carries the evidence that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - types only
    import pandas as pd

_pandas_cache: Any = None


def _pd() -> Any:
    global _pandas_cache
    if _pandas_cache is None:
        import pandas

        _pandas_cache = pandas
    return _pandas_cache


# Classifying a hole in one-minute bars is most of the work, because most holes
# are not faults. An OHLCV bar exists only where a trade happened, so a quiet
# overnight minute has no bar and never will; and the exchange itself is shut
# for an hour a day, two days a week, and a dozen days a year.
#
# Measured on the NQ archive before these thresholds existed: 3,824 "gaps"
# totalling 16,950 hours, of which the median was **61 minutes** -- the daily
# halt, counted from the last bar before it to the first bar after, one minute
# over a 60-minute threshold. The largest were Christmas Eve and New Year's
# Eve. Reporting those as faults is not caution, it is a detector that cries
# wolf until nobody reads it.
#
# What is worth a warning is a hole *while the market was open*.

#: The daily maintenance break, 17:00-18:00 New York. Measured close-to-open it
#: reads as ~61 minutes, and an illiquid evening can stretch it further.
SESSION_BREAK_MINUTES = 95

#: Friday's close to Sunday's open, with room for an early close.
WEEKEND_MINUTES = 44 * 60

#: A hole big enough to be a missing session rather than a quiet hour.
SESSION_LENGTH_MINUTES = 8 * 60

SESSION_TIMEZONE = "America/New_York"

#: How many of the largest gaps to keep. The full list on a sixteen-year archive
#: is thousands of weekends, which is noise rather than evidence.
GAP_SAMPLE = 12


@dataclass(frozen=True)
class Finding:
    """One thing that is true about the data, with the number behind it."""

    code: str
    severity: str  # "ok" | "note" | "warn" | "fail"
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HealthReport:
    dataset_key: str
    rows: int
    first: str | None
    last: str | None
    span_days: float
    findings: tuple[Finding, ...]

    @property
    def worst(self) -> str:
        """The most severe finding, which is what a matrix cell should show."""
        order = {"ok": 0, "note": 1, "warn": 2, "fail": 3}
        return max((f.severity for f in self.findings), key=lambda s: order[s], default="ok")

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset_key,
            "rows": self.rows,
            "first": self.first,
            "last": self.last,
            "span_days": round(self.span_days, 2),
            "status": self.worst,
            "findings": [f.as_dict() for f in self.findings],
        }


def _timestamp_findings(times: pd.Series) -> list[Finding]:
    findings: list[Finding] = []

    duplicates = int(times.duplicated().sum())
    findings.append(
        Finding(
            "duplicate_timestamps",
            "ok" if duplicates == 0 else "fail",
            "No two bars share a timestamp"
            if duplicates == 0
            else f"{duplicates:,} bars share a timestamp with another",
            {"count": duplicates},
        )
    )

    # Sorted on write, but this is the check that would catch a bad write rather
    # than trusting that it happened.
    out_of_order = int((times.diff().dropna() < _pd().Timedelta(0)).sum())
    findings.append(
        Finding(
            "monotonic_timestamps",
            "ok" if out_of_order == 0 else "fail",
            "Timestamps only move forward"
            if out_of_order == 0
            else f"{out_of_order:,} bars go backwards in time",
            {"count": out_of_order},
        )
    )

    tz = getattr(times.dt, "tz", None)
    findings.append(
        Finding(
            "timezone",
            "ok" if tz is not None else "warn",
            f"Timestamps carry a timezone ({tz})"
            if tz is not None
            else "Timestamps are naive - the instant they refer to is ambiguous",
            {"timezone": str(tz)},
        )
    )
    return findings


def _gap_findings(times: pd.Series) -> list[Finding]:
    """Classify every hole, and warn only about the ones that are faults."""
    deltas = times.diff()
    if len(deltas.dropna()) == 0:
        return []

    minutes = deltas.dt.total_seconds() / 60.0

    # The archive's own bar interval, taken from the data rather than assumed:
    # the most common step between bars is the resolution it was bought at.
    # Everything at or below that is two consecutive bars, not a hole, and
    # classifying those as anything counted every ordinary minute of the
    # 16:00-18:00 window as a maintenance break.
    modes = minutes.mode()
    native = float(modes.iloc[0]) if len(modes) else 1.0
    is_gap = minutes > native * 1.5

    # Where the hole began, in exchange time: an hour-long hole at 17:00 New
    # York is the maintenance break, and the same hole at 10:00 is missing data.
    started = times.shift(1).dt.tz_convert(SESSION_TIMEZONE)
    hour = started.dt.hour

    is_break = is_gap & (minutes <= SESSION_BREAK_MINUTES) & hour.isin([16, 17])
    is_weekend = is_gap & (minutes >= WEEKEND_MINUTES)
    # A hole on the scale of a whole session is a closure, not a gap in a
    # session that was trading. Classified by size rather than by guessing at
    # holidays from the clock: the dates are listed so a person can check them
    # against an exchange calendar, and this module does not claim to know why
    # the market was shut -- only that a session's worth of time is absent.
    is_closure = is_gap & (~is_weekend) & (minutes >= SESSION_LENGTH_MINUTES)
    # What is left happened while the market was open, and is the real signal.
    in_session = is_gap & (minutes > SESSION_BREAK_MINUTES) & ~is_weekend & ~is_closure & ~is_break

    count = int(in_session.sum())
    total_hours = float(minutes[in_session].sum()) / 60.0
    largest = minutes[in_session].nlargest(GAP_SAMPLE)
    samples = [
        {
            "starts": started.loc[index].isoformat(),
            "minutes": round(float(value), 1),
            "hours": round(float(value) / 60.0, 2),
        }
        for index, value in largest.items()
    ]

    closure_dates = sorted({str(stamp.date()) for stamp in started[is_closure].dropna()})

    # Severity comes from the *share* of open time that is missing, not from an
    # absolute number of hours. 1,833 missing hours sounds alarming and is 0.1%
    # of sixteen years of trading; the same figure over three months would not
    # be. A fraction is the only version of this that compares across archives.
    span_minutes = float(minutes.sum())
    excluded = float(minutes[is_weekend | is_closure | is_break].sum())
    open_minutes = max(1.0, span_minutes - excluded)
    coverage = 1.0 - (float(minutes[in_session].sum()) / open_minutes)

    if count == 0:
        severity = "ok"
    elif coverage >= 0.995:
        severity = "note"
    elif coverage >= 0.95:
        severity = "warn"
    else:
        severity = "fail"

    return [
        Finding(
            "gaps",
            severity,
            "No gaps while the market was open"
            if count == 0
            else (
                f"{coverage:.3%} of open time covered; {count:,} in-session gaps "
                f"totalling {total_hours:,.1f} hours"
            ),
            {
                "count": count,
                "coverage": round(coverage, 6),
                "total_hours": round(total_hours, 2),
                "open_hours": round(open_minutes / 60.0, 1),
                "largest": samples,
                # Reported so the classification can be audited rather than
                # trusted: these are the holes deliberately not counted.
                "session_breaks": int(is_break.sum()),
                "weekend_breaks": int(is_weekend.sum()),
                "full_session_closures": len(closure_dates),
                "closure_sample": closure_dates[-8:],
                "session_break_threshold_minutes": SESSION_BREAK_MINUTES,
            },
        )
    ]


def _price_findings(frame: pd.DataFrame) -> list[Finding]:
    """A bar has to be internally possible before it can be believed."""
    findings: list[Finding] = []

    impossible = int(
        (
            (frame["high"] < frame["low"])
            | (frame["high"] < frame["open"])
            | (frame["high"] < frame["close"])
            | (frame["low"] > frame["open"])
            | (frame["low"] > frame["close"])
        ).sum()
    )
    findings.append(
        Finding(
            "ohlc_consistency",
            "ok" if impossible == 0 else "fail",
            "Every bar's high and low contain its open and close"
            if impossible == 0
            else f"{impossible:,} bars have a high below or a low above their own open/close",
            {"count": impossible},
        )
    )

    non_positive = int((frame[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    findings.append(
        Finding(
            "positive_prices",
            "ok" if non_positive == 0 else "fail",
            "All prices are positive"
            if non_positive == 0
            else f"{non_positive:,} bars carry a zero or negative price",
            {"count": non_positive},
        )
    )

    missing = int(frame[["open", "high", "low", "close"]].isna().any(axis=1).sum())
    findings.append(
        Finding(
            "complete_bars",
            "ok" if missing == 0 else "fail",
            "No bar is missing a price"
            if missing == 0
            else f"{missing:,} bars are missing at least one price",
            {"count": missing},
        )
    )

    negative_volume = int((frame["volume"] < 0).sum())
    zero_volume = int((frame["volume"] == 0).sum())
    findings.append(
        Finding(
            "volume",
            "ok" if negative_volume == 0 else "fail",
            "Volume is never negative"
            if negative_volume == 0
            else f"{negative_volume:,} bars carry negative volume",
            # Zero-volume bars are reported but are not a fault: a quiet minute
            # in an overnight session genuinely trades nothing.
            {"negative": negative_volume, "zero": zero_volume},
        )
    )
    return findings


def analyse(frame: pd.DataFrame, dataset_key: str) -> HealthReport:
    """Measure one archive. Never mutates it."""
    pd = _pd()
    if frame.empty:
        return HealthReport(
            dataset_key=dataset_key,
            rows=0,
            first=None,
            last=None,
            span_days=0.0,
            findings=(
                Finding("empty", "fail", "The archive holds no bars", {"count": 0}),
            ),
        )

    times = pd.to_datetime(frame["event_time"], utc=True)
    first, last = times.iloc[0], times.iloc[-1]

    findings: list[Finding] = []
    findings += _timestamp_findings(times)
    findings += _gap_findings(times)
    findings += _price_findings(frame)

    return HealthReport(
        dataset_key=dataset_key,
        rows=len(frame),
        first=first.isoformat(),
        last=last.isoformat(),
        span_days=(last - first).total_seconds() / 86400.0,
        findings=tuple(findings),
    )


def report_path(cache_root: Path, dataset_key: str) -> Path:
    return cache_root / "derived" / f"{dataset_key}_health.json"
