"""The economic calendar, its providers, and the rule that news only tightens.

No test here reaches a network. The FRED client is exercised through an injected
opener so its parsing, its refusal without a key and its honesty about missing
clock times are all tested without a credential.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from forge.propdesk import (
    FRED_ATTRIBUTION,
    SUGGESTED_SOURCES,
    CalendarError,
    CalendarRegistry,
    EconomicEvent,
    FredReleaseCalendar,
    Impact,
    ManualCalendar,
    NewsPolicy,
    assess_news,
    classify_impact,
)

NOW = datetime(2026, 9, 11, 13, 30, tzinfo=UTC)


def event(title: str, *, minutes: int = 0, impact: Impact | None = None, **overrides):
    base: dict = {
        "event_id": f"e-{title}-{minutes}",
        "title": title,
        "at": NOW + timedelta(minutes=minutes),
        "impact": impact or classify_impact(title),
        "source": "test",
    }
    return EconomicEvent(**{**base, **overrides})


# ── impact grading ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Consumer Price Index", Impact.HIGH),
        ("Employment Situation", Impact.HIGH),
        ("FOMC Statement", Impact.HIGH),
        ("Gross Domestic Product", Impact.HIGH),
        ("Producer Price Index", Impact.MEDIUM),
        ("Advance Retail Sales", Impact.MEDIUM),
        ("Something Nobody Listed", Impact.UNKNOWN),
    ],
)
def test_releases_are_graded_or_reported_ungraded(title, expected) -> None:
    assert classify_impact(title) is expected


# ── the manual calendar ──────────────────────────────────────────────────────


def test_the_manual_calendar_is_always_available() -> None:
    assert ManualCalendar().availability().available is True


def test_a_recorded_event_is_returned_within_its_window() -> None:
    calendar = ManualCalendar()
    recorded = calendar.record(title="Consumer Price Index", at=NOW)
    assert recorded.impact is Impact.HIGH
    assert calendar.events(NOW - timedelta(hours=1), NOW + timedelta(hours=1)) == (
        recorded,
    )
    assert calendar.events(NOW + timedelta(days=1), NOW + timedelta(days=2)) == ()


def test_importing_a_file_refuses_the_whole_file_on_a_bad_entry(tmp_path) -> None:
    """A calendar that silently dropped the one release somebody cared about is
    worse than one that did not import."""
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            [
                {"title": "Consumer Price Index", "at": NOW.isoformat()},
                {"title": "Broken"},
            ]
        )
    )
    calendar = ManualCalendar()
    with pytest.raises(CalendarError, match="entry 1"):
        calendar.load_file(path)
    assert calendar.events(NOW - timedelta(days=1), NOW + timedelta(days=1)) == ()


def test_importing_a_good_file_works(tmp_path) -> None:
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            [
                {"title": "FOMC Statement", "at": NOW.isoformat(), "impact": "high"},
                {"title": "Jobless Claims", "at": NOW.isoformat()},
            ]
        )
    )
    calendar = ManualCalendar()
    assert calendar.load_file(path) == 2
    found = calendar.events(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert {e.impact for e in found} == {Impact.HIGH, Impact.MEDIUM}


def test_a_file_that_is_not_a_json_array_is_refused(tmp_path) -> None:
    path = tmp_path / "events.json"
    path.write_text('{"title": "x"}')
    with pytest.raises(CalendarError, match="JSON array"):
        ManualCalendar().load_file(path)


# ── FRED ─────────────────────────────────────────────────────────────────────


def test_fred_refuses_without_a_key_and_says_where_to_get_one(monkeypatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    calendar = FredReleaseCalendar()
    availability = calendar.availability()
    assert availability.available is False
    assert "FRED_API_KEY" in availability.requires
    assert "fredaccount.stlouisfed.org" in availability.reason
    with pytest.raises(CalendarError, match="ships none"):
        calendar.events(NOW, NOW + timedelta(days=7))


def test_fred_carries_the_attribution_its_terms_require(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "x" * 32)
    availability = FredReleaseCalendar().availability()
    assert availability.available is True
    assert "not endorsed or certified" in availability.attribution
    assert availability.attribution == FRED_ATTRIBUTION


def test_fred_parses_scheduled_release_dates(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "x" * 32)
    captured: list[str] = []

    def opener(url: str) -> bytes:
        captured.append(url)
        return json.dumps(
            {
                "release_dates": [
                    {"release_id": 10, "release_name": "Consumer Price Index",
                     "date": "2026-09-15"},
                    {"release_id": 50, "release_name": "Employment Situation",
                     "date": "2026-09-12"},
                    {"release_id": 99, "date": "2026-09-13"},  # no name; skipped
                ]
            }
        ).encode()

    calendar = FredReleaseCalendar(opener=opener)
    events = calendar.events(NOW, NOW + timedelta(days=10))
    assert [e.title for e in events] == [
        "Employment Situation",
        "Consumer Price Index",
    ]
    assert all(e.impact is Impact.HIGH for e in events)
    # The parameter without which this is a history rather than a calendar.
    assert "include_release_dates_with_no_data=true" in captured[0]


def test_fred_events_admit_they_carry_no_clock_time(monkeypatch) -> None:
    """A fifteen-minute window around a time the source does not give would be
    a window in the wrong place."""
    monkeypatch.setenv("FRED_API_KEY", "x" * 32)
    calendar = FredReleaseCalendar(
        opener=lambda url: json.dumps(
            {"release_dates": [{"release_name": "Consumer Price Index",
                                "date": "2026-09-15"}]}
        ).encode()
    )
    found = calendar.events(NOW, NOW + timedelta(days=10))[0]
    assert found.time_is_approximate is True
    assert "release dates, not clock times" in found.note


def test_fred_refuses_a_response_with_no_release_dates(monkeypatch) -> None:
    """An empty calendar reads as 'no events', which is a risk control that quietly
    stopped working."""
    monkeypatch.setenv("FRED_API_KEY", "x" * 32)
    calendar = FredReleaseCalendar(opener=lambda url: b'{"error": "nope"}')
    with pytest.raises(CalendarError, match="no release_dates array"):
        calendar.events(NOW, NOW + timedelta(days=7))


def test_fred_refuses_a_body_that_is_not_json(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "x" * 32)
    calendar = FredReleaseCalendar(opener=lambda url: b"<html>blocked</html>")
    with pytest.raises(CalendarError, match="not JSON"):
        calendar.events(NOW, NOW + timedelta(days=7))


# ── the registry ─────────────────────────────────────────────────────────────


def test_the_registry_reports_a_source_it_could_not_read(monkeypatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    manual = ManualCalendar()
    manual.record(title="FOMC Statement", at=NOW)
    registry = CalendarRegistry([manual, FredReleaseCalendar()])
    events, problems = registry.events(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert len(events) == 1
    assert any("FRED_API_KEY" in problem for problem in problems)


def test_the_registry_merges_without_duplicating() -> None:
    first, second = ManualCalendar(), ManualCalendar()
    for calendar in (first, second):
        calendar.record(title="FOMC Statement", at=NOW)
    registry = CalendarRegistry([first, second])
    events, _ = registry.events(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert len(events) == 1


# ── the blackout policy ──────────────────────────────────────────────────────


def test_a_disabled_policy_restricts_nothing_and_says_so() -> None:
    assessment = assess_news(policy=NewsPolicy(), events=[event("CPI")], at=NOW)
    assert assessment.restricted is False
    assert "no news policy is enabled" in assessment.gaps


def test_a_high_impact_event_inside_the_window_restricts() -> None:
    policy = NewsPolicy(enabled=True, minutes_before=5, minutes_after=5)
    assessment = assess_news(
        policy=policy, events=[event("Consumer Price Index", minutes=2)], at=NOW
    )
    assert assessment.restricted is True
    assert assessment.action == "block_new"


def test_an_event_outside_the_window_does_not_restrict_but_is_counted_down() -> None:
    policy = NewsPolicy(enabled=True, minutes_before=5, minutes_after=5)
    assessment = assess_news(
        policy=policy, events=[event("Consumer Price Index", minutes=30)], at=NOW
    )
    assert assessment.restricted is False
    assert assessment.minutes_to_next == pytest.approx(30.0)
    assert assessment.next_event is not None


def test_a_medium_event_does_not_trigger_a_high_only_policy() -> None:
    policy = NewsPolicy(enabled=True, minimum_impact=Impact.HIGH)
    assessment = assess_news(
        policy=policy, events=[event("Producer Price Index", minutes=1)], at=NOW
    )
    assert assessment.restricted is False


def test_an_ungraded_event_is_reported_as_a_gap_rather_than_ignored() -> None:
    """A risk control that silently does not apply is the failure to avoid."""
    policy = NewsPolicy(enabled=True)
    assessment = assess_news(
        policy=policy, events=[event("Something Nobody Listed", minutes=1)], at=NOW
    )
    assert assessment.restricted is False
    assert any("not graded for impact" in gap for gap in assessment.gaps)


def test_an_ungraded_event_can_be_opted_into() -> None:
    policy = NewsPolicy(enabled=True, blackout_unknown_impact=True)
    assessment = assess_news(
        policy=policy, events=[event("Something Nobody Listed", minutes=1)], at=NOW
    )
    assert assessment.restricted is True


def test_an_event_without_a_clock_time_is_a_gap_by_default() -> None:
    policy = NewsPolicy(enabled=True)
    assessment = assess_news(
        policy=policy,
        events=[event("Consumer Price Index", minutes=1, time_is_approximate=True)],
        at=NOW,
    )
    assert assessment.restricted is False
    assert any("no published clock time" in gap for gap in assessment.gaps)


def test_a_whole_day_window_can_be_opted_into() -> None:
    policy = NewsPolicy(
        enabled=True, blackout_approximate_times=True, minutes_before=60, minutes_after=60
    )
    assessment = assess_news(
        policy=policy,
        events=[event("Consumer Price Index", minutes=10, time_is_approximate=True)],
        at=NOW,
    )
    assert assessment.restricted is True


def test_a_source_that_could_not_be_read_appears_in_the_gaps() -> None:
    assessment = assess_news(
        policy=NewsPolicy(enabled=True),
        events=[],
        at=NOW,
        problems=("fred: FRED_API_KEY is not set",),
    )
    assert any("FRED_API_KEY" in gap for gap in assessment.gaps)


def test_an_assessment_has_no_field_that_could_mean_permitted() -> None:
    """News is an input that tightens. Nothing downstream may read it as a yes."""
    from forge.propdesk import NewsAssessment

    fields = set(NewsAssessment.model_fields)
    assert "restricted" in fields
    assert not fields & {"permitted", "allowed", "clear_to_trade"}


# ── the research record ──────────────────────────────────────────────────────


def test_forex_factory_is_recorded_as_unsuitable_with_the_reason() -> None:
    entry = next(s for s in SUGGESTED_SOURCES if s["name"] == "Forex Factory")
    assert entry["status"] == "unsuitable"
    assert "403" in entry["detail"]
    assert "does not scrape" in entry["detail"]


def test_the_implemented_source_is_named_and_the_rest_are_marked_suggested() -> None:
    statuses = {s["name"]: s["status"] for s in SUGGESTED_SOURCES}
    assert statuses["FRED release dates"] == "implemented"
    assert statuses["Bureau of Labor Statistics release schedule"] == "suggested"
    assert "Federal Reserve FOMC calendar" in statuses
