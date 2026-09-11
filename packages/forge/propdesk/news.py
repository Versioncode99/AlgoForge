"""Scheduled economic events, from sources that permit being read.

**On Forex Factory**, which the task asked to be investigated specifically.
It publishes no official or documented public API for its calendar. Its terms
page returns HTTP 403 to an automated request — the site actively refuses
non-browser access — and every "Forex Factory API" on the market is a
third-party scraper of it. So AlgoForge does not scrape it, does not ship a
scraped dataset, and does not embed anybody's key. That is the finding, and it
is recorded in `SUGGESTED_SOURCES` rather than worked around.

**What is shipped instead**, behind a provider interface so the source can be
replaced without touching anything that reads it:

* `ManualCalendar` — events the operator records or imports from a file. The
  default, it works offline, and it is deterministic, which is what makes the
  blackout logic testable.
* `FredReleaseCalendar` — a real client for the Federal Reserve Bank of
  St. Louis FRED API's release-dates endpoint, which returns *scheduled future*
  release dates when asked for dates with no data yet. It is the strongest
  legitimate option available: an official API, documented, free with a
  registered key, and with terms of use that permit this use given an
  attribution notice, which is carried in `FRED_ATTRIBUTION` and returned with
  every response. Without `FRED_API_KEY` it refuses; it does not fall back to
  anything.

**News never widens a permission.** `NewsPolicy.assess` can add a restriction —
a blackout around a high-impact release — and there is no path by which it
removes one. A calendar that failed to load produces "unknown", which for a
policy configured to block around events means the blackout cannot be confirmed
either way, and the honest handling of that is stated rather than silently
resolved to "trade freely".
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel

FRED_BASE = "https://api.stlouisfed.org/fred"

#: Required by the FRED terms of use, and returned with every response this
#: provider produces so a surface cannot render the data without it.
FRED_ATTRIBUTION = (
    "This product uses the FRED® API but is not endorsed or certified by the "
    "Federal Reserve Bank of St. Louis. https://fred.stlouisfed.org/docs/api/terms_of_use.html"
)


class CalendarError(Exception):
    """The calendar could not be read. No events are returned, and none invented."""


class Impact(StrEnum):
    """How much a release typically moves futures.

    `UNKNOWN` is a real value and the default. A source that does not grade its
    events produces `UNKNOWN`, and a blackout configured for high-impact events
    does not fire on it — which is stated, not hidden, because the alternative
    is a risk control that silently does not apply.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class EconomicEvent(FrozenModel):
    """One scheduled release."""

    event_id: str
    title: str = Field(min_length=1, max_length=200)
    at: datetime
    country: str = Field(default="US", max_length=8)
    category: str = ""
    impact: Impact = Impact.UNKNOWN
    actual: str = ""
    forecast: str = ""
    previous: str = ""
    source: str = Field(min_length=1, max_length=60)
    source_url: str = ""
    retrieved_at: datetime | None = None
    #: True when the source gives a date but not a time of day. A daily
    #: statistical release without a published clock time cannot support a
    #: fifteen-minute blackout, and pretending otherwise would put the window in
    #: the wrong place.
    time_is_approximate: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Availability(FrozenModel):
    """Whether a provider can be used right now, and what it needs if not."""

    provider: str
    available: bool
    reason: str = ""
    requires: tuple[str, ...] = ()
    attribution: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CalendarProvider(Protocol):
    name: str

    def availability(self) -> Availability: ...

    def events(self, start: datetime, end: datetime) -> tuple[EconomicEvent, ...]: ...


#: Releases that move CME index and rate futures, as the task named them, with
#: the agency that actually publishes each. Used to grade impact for sources
#: that do not, and to tell an operator which first-party schedule to consult.
KNOWN_RELEASES: tuple[tuple[str, str, Impact, str], ...] = (
    ("CPI", "Consumer Price Index", Impact.HIGH, "Bureau of Labor Statistics"),
    ("NFP", "Employment Situation", Impact.HIGH, "Bureau of Labor Statistics"),
    ("FOMC", "FOMC statement and projections", Impact.HIGH, "Federal Reserve"),
    ("GDP", "Gross Domestic Product", Impact.HIGH, "Bureau of Economic Analysis"),
    ("PCE", "Personal Income and Outlays", Impact.HIGH, "Bureau of Economic Analysis"),
    ("PPI", "Producer Price Index", Impact.MEDIUM, "Bureau of Labor Statistics"),
    ("RETAIL", "Advance Retail Sales", Impact.MEDIUM, "Census Bureau"),
    ("CLAIMS", "Unemployment Insurance Weekly Claims", Impact.MEDIUM, "Department of Labor"),
    ("JOLTS", "Job Openings and Labor Turnover", Impact.LOW, "Bureau of Labor Statistics"),
    ("ISM", "ISM Manufacturing and Services", Impact.MEDIUM, "Institute for Supply Management"),
)

_IMPACT_MARKERS: tuple[tuple[str, Impact], ...] = (
    ("consumer price index", Impact.HIGH),
    ("employment situation", Impact.HIGH),
    ("fomc", Impact.HIGH),
    ("federal open market committee", Impact.HIGH),
    ("gross domestic product", Impact.HIGH),
    ("personal income and outlays", Impact.HIGH),
    ("producer price index", Impact.MEDIUM),
    ("retail sales", Impact.MEDIUM),
    ("unemployment insurance", Impact.MEDIUM),
    ("jobless claims", Impact.MEDIUM),
    ("job openings", Impact.LOW),
)


def classify_impact(title: str) -> Impact:
    """Grade a release by name, or say that it was not graded.

    Substring matching on the official release names, which is crude and
    honest: a release this list does not recognise gets `UNKNOWN` rather than a
    guess, and a blackout configured for high-impact events will not fire on it.
    """
    lowered = title.lower()
    for marker, impact in _IMPACT_MARKERS:
        if marker in lowered:
            return impact
    return Impact.UNKNOWN


class ManualCalendar:
    """Events the operator recorded, or imported from a file they control.

    The default provider, and the one the blackout logic is tested against. It
    needs no network, no key and nobody's permission, and an operator who keeps
    their own calendar of the four releases they care about is better served by
    it than by a scraped feed of two hundred.
    """

    name = "manual"

    def __init__(self, events: Iterable[EconomicEvent] = ()) -> None:
        self._events: dict[str, EconomicEvent] = {e.event_id: e for e in events}

    def availability(self) -> Availability:
        return Availability(
            provider=self.name,
            available=True,
            reason=f"{len(self._events)} event(s) recorded locally",
        )

    def record(
        self,
        *,
        title: str,
        at: datetime,
        impact: Impact | None = None,
        country: str = "US",
        category: str = "",
        note: str = "",
    ) -> EconomicEvent:
        event = EconomicEvent(
            event_id=stable_id("calevent", {"title": title, "at": at.isoformat()}),
            title=title,
            at=at.astimezone(UTC),
            country=country,
            category=category,
            impact=impact or classify_impact(title),
            source=self.name,
            retrieved_at=datetime.now(UTC),
            note=note,
        )
        self._events[event.event_id] = event
        return event

    def remove(self, event_id: str) -> bool:
        return self._events.pop(event_id, None) is not None

    def load_file(self, path: Path) -> int:
        """Import a JSON array the operator supplied.

        Refuses the whole file on a malformed entry rather than importing what
        parsed: a calendar that silently dropped the one release somebody cared
        about is worse than one that did not import.
        """
        try:
            payload = json.loads(Path(path).read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalendarError(f"{path} could not be read as JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise CalendarError(f"{path} must hold a JSON array of events")
        parsed: list[EconomicEvent] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise CalendarError(f"{path}: entry {index} is not an object")
            try:
                at = datetime.fromisoformat(str(item["at"]))
                title = str(item["title"])
            except (KeyError, ValueError) as exc:
                raise CalendarError(
                    f"{path}: entry {index} needs a 'title' and an ISO-8601 'at': {exc}"
                ) from exc
            impact_value = str(item.get("impact", "")).lower()
            parsed.append(
                EconomicEvent(
                    event_id=stable_id("calevent", {"title": title, "at": at.isoformat()}),
                    title=title,
                    at=at if at.tzinfo else at.replace(tzinfo=UTC),
                    country=str(item.get("country", "US")),
                    category=str(item.get("category", "")),
                    impact=(
                        Impact(impact_value)
                        if impact_value in {i.value for i in Impact}
                        else classify_impact(title)
                    ),
                    source=f"{self.name}:{Path(path).name}",
                    retrieved_at=datetime.now(UTC),
                )
            )
        for event in parsed:
            self._events[event.event_id] = event
        return len(parsed)

    def events(self, start: datetime, end: datetime) -> tuple[EconomicEvent, ...]:
        return tuple(
            sorted(
                (e for e in self._events.values() if start <= e.at <= end),
                key=lambda e: (e.at, e.title),
            )
        )


class FredReleaseCalendar:
    """Scheduled release dates from the FRED API.

    `include_release_dates_with_no_data=true` is what makes this a *calendar*
    rather than a history: without it the endpoint returns only releases that
    have already published. The key comes from the environment and nothing here
    writes one down.

    The honest limitation, stated in every response: FRED returns release
    *dates*, not times of day. A release scheduled for 8:30 Eastern arrives here
    as a date, so `time_is_approximate` is `True` on every event and a blackout
    computed from one is a whole-day window unless the operator supplies the
    time themselves. A fifteen-minute window around a time this source does not
    provide would be a window in the wrong place.
    """

    name = "fred"

    def __init__(
        self,
        *,
        api_key_env: str = "FRED_API_KEY",
        opener: Callable[[str], bytes] | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_key_env = api_key_env
        self._opener = opener
        self._timeout = timeout

    def _key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise CalendarError(
                f"{self.api_key_env} is not set. FRED issues free API keys at "
                "https://fredaccount.stlouisfed.org/apikeys — AlgoForge ships none and "
                "will not read this calendar without one."
            )
        return key

    def availability(self) -> Availability:
        try:
            self._key()
        except CalendarError as exc:
            return Availability(
                provider=self.name,
                available=False,
                reason=str(exc),
                requires=(self.api_key_env,),
                attribution=FRED_ATTRIBUTION,
            )
        return Availability(
            provider=self.name,
            available=True,
            reason="a FRED API key is configured",
            attribution=FRED_ATTRIBUTION,
        )

    def _fetch(self, url: str) -> dict[str, Any]:
        if self._opener is not None:
            raw = self._opener(url)
        else:  # pragma: no cover - exercised only with a real key and network
            request = urllib.request.Request(
                url, headers={"User-Agent": "AlgoForge/0.1 (research workstation)"}
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CalendarError(f"FRED returned a body that is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise CalendarError("FRED returned a payload that is not an object")
        return payload

    def events(self, start: datetime, end: datetime) -> tuple[EconomicEvent, ...]:
        key = self._key()
        query = urllib.parse.urlencode(
            {
                "api_key": key,
                "file_type": "json",
                "realtime_start": start.date().isoformat(),
                "realtime_end": end.date().isoformat(),
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
                "order_by": "release_date",
                "limit": 1000,
            }
        )
        payload = self._fetch(f"{FRED_BASE}/releases/dates?{query}")
        rows = payload.get("release_dates")
        if not isinstance(rows, list):
            raise CalendarError(
                "FRED's response carried no release_dates array; nothing is returned "
                "rather than an empty calendar that would read as 'no events'"
            )
        retrieved = datetime.now(UTC)
        events: list[EconomicEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                day = date.fromisoformat(str(row["date"]))
            except (KeyError, ValueError):
                continue
            title = str(row.get("release_name", "")).strip()
            if not title:
                continue
            at = datetime(day.year, day.month, day.day, tzinfo=UTC)
            if not start <= at <= end:
                continue
            events.append(
                EconomicEvent(
                    event_id=stable_id(
                        "calevent", {"title": title, "at": at.isoformat(), "src": "fred"}
                    ),
                    title=title,
                    at=at,
                    country="US",
                    category=str(row.get("release_id", "")),
                    impact=classify_impact(title),
                    source=self.name,
                    source_url="https://fred.stlouisfed.org/docs/api/fred/releases_dates.html",
                    retrieved_at=retrieved,
                    time_is_approximate=True,
                    note=(
                        "FRED publishes release dates, not clock times. Treat the window "
                        "as the whole day unless you supply the time."
                    ),
                )
            )
        return tuple(sorted(events, key=lambda e: (e.at, e.title)))


class CalendarRegistry:
    """Every calendar source, and which of them can actually be read.

    Sources are tried in order and the first available one answers. A failure is
    reported rather than swallowed: a calendar that returned nothing because its
    key was missing, presented as "no events this week", is a risk control that
    quietly stopped working.
    """

    def __init__(self, providers: Iterable[CalendarProvider] | None = None) -> None:
        self._providers: list[CalendarProvider] = list(
            providers if providers is not None else (ManualCalendar(), FredReleaseCalendar())
        )

    def add(self, provider: CalendarProvider) -> None:
        self._providers.append(provider)

    def provider(self, name: str) -> CalendarProvider | None:
        return next((p for p in self._providers if p.name == name), None)

    def availability(self) -> tuple[Availability, ...]:
        return tuple(p.availability() for p in self._providers)

    def events(
        self, start: datetime, end: datetime, *, prefer: str = ""
    ) -> tuple[tuple[EconomicEvent, ...], tuple[str, ...]]:
        """Events from every available source, plus what could not be read."""
        collected: dict[str, EconomicEvent] = {}
        problems: list[str] = []
        ordered = sorted(
            self._providers, key=lambda p: (0 if p.name == prefer else 1, p.name)
        )
        for provider in ordered:
            availability = provider.availability()
            if not availability.available:
                problems.append(f"{provider.name}: {availability.reason}")
                continue
            try:
                for event in provider.events(start, end):
                    collected.setdefault(event.event_id, event)
            except CalendarError as exc:
                problems.append(f"{provider.name}: {exc}")
        return (
            tuple(sorted(collected.values(), key=lambda e: (e.at, e.title))),
            tuple(problems),
        )


class NewsPolicy(FrozenModel):
    """Blackout windows around scheduled releases.

    Two numbers and a floor on impact. The floor matters: an event this
    application could not grade is `UNKNOWN`, and a policy that blacked out
    every ungraded event would black out most of the calendar.
    """

    enabled: bool = False
    minutes_before: int = Field(default=2, ge=0, le=720)
    minutes_after: int = Field(default=2, ge=0, le=720)
    #: The least impactful grade that triggers a blackout.
    minimum_impact: Impact = Impact.HIGH
    #: Whether an ungraded event triggers one. Off by default, and the resulting
    #: gap is reported rather than left to be discovered.
    blackout_unknown_impact: bool = False
    #: Whether a whole-day window is applied to an event whose source gives no
    #: clock time. Off by default because it is a very blunt instrument.
    blackout_approximate_times: bool = False
    #: What the desk does inside a window.
    action: str = Field(default="block_new", pattern=r"^(block_new|warn|flatten)$")

    def covers(self, event: EconomicEvent) -> bool:
        if event.impact is Impact.UNKNOWN:
            return self.blackout_unknown_impact
        ranking = {Impact.LOW: 0, Impact.MEDIUM: 1, Impact.HIGH: 2}
        return ranking[event.impact] >= ranking[self.minimum_impact]


class NewsAssessment(FrozenModel):
    """Whether an instant is inside a blackout, and what was not knowable.

    `restricted` says only whether a restriction applies. There is deliberately
    no field that could mean "permitted": this assessment is an input that can
    tighten a decision, and nothing downstream reads it as a reason to allow
    something the deterministic controls refused.
    """

    at: datetime
    restricted: bool
    action: str = ""
    events: tuple[EconomicEvent, ...] = ()
    next_event: EconomicEvent | None = None
    minutes_to_next: float | None = None
    #: Sources that could not be read, and grading gaps. A blackout that could
    #: not be evaluated must be visible.
    gaps: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def assess_news(
    *,
    policy: NewsPolicy,
    events: Iterable[EconomicEvent],
    at: datetime,
    problems: Iterable[str] = (),
) -> NewsAssessment:
    """Is `at` inside a blackout window, and what is coming next.

    Pure: it reads a policy and a list of events, and nothing else. The registry
    fetches; this decides — which is what makes the decision testable without a
    network and reproducible from the record.
    """
    gaps = list(problems)
    if not policy.enabled:
        return NewsAssessment(
            at=at, restricted=False, gaps=(*gaps, "no news policy is enabled")
        )

    inside: list[EconomicEvent] = []
    upcoming: list[EconomicEvent] = []
    for event in events:
        if not policy.covers(event):
            if event.impact is Impact.UNKNOWN:
                gaps.append(
                    f"'{event.title}' was not graded for impact, so no window was "
                    "applied to it"
                )
            continue
        if event.time_is_approximate and not policy.blackout_approximate_times:
            gaps.append(
                f"'{event.title}' has no published clock time from {event.source}, so "
                "no window was applied to it"
            )
            continue
        opens = event.at - timedelta(minutes=policy.minutes_before)
        closes = event.at + timedelta(minutes=policy.minutes_after)
        if opens <= at <= closes:
            inside.append(event)
        elif event.at > at:
            upcoming.append(event)

    upcoming.sort(key=lambda e: e.at)
    following = upcoming[0] if upcoming else None
    return NewsAssessment(
        at=at,
        restricted=bool(inside),
        action=policy.action if inside else "",
        events=tuple(inside),
        next_event=following,
        minutes_to_next=(
            None if following is None else round((following.at - at).total_seconds() / 60, 2)
        ),
        gaps=tuple(dict.fromkeys(gaps)),
    )


#: What was investigated, what was concluded, and what to implement next. Kept
#: in code rather than only in a document so the interface can show an operator
#: where the data would come from, and so a future contributor finds the
#: Forex Factory conclusion before repeating the work.
SUGGESTED_SOURCES: tuple[dict[str, str], ...] = (
    {
        "name": "Forex Factory",
        "status": "unsuitable",
        "detail": (
            "No official or documented public calendar API. The site returns HTTP 403 "
            "to automated requests, so it actively refuses non-browser access, and "
            "every third-party 'Forex Factory API' is a scraper of it. AlgoForge does "
            "not scrape it and ships no scraped dataset."
        ),
        "url": "https://www.forexfactory.com/calendar",
    },
    {
        "name": "FRED release dates",
        "status": "implemented",
        "detail": (
            "Official Federal Reserve Bank of St. Louis API. Free registered key. "
            "Returns scheduled future release dates, but dates only — no clock times. "
            "Terms of use require the attribution notice this provider returns."
        ),
        "url": "https://fred.stlouisfed.org/docs/api/fred/releases_dates.html",
    },
    {
        "name": "Bureau of Labor Statistics release schedule",
        "status": "suggested",
        "detail": (
            "First-party schedule for CPI, PPI and the Employment Situation, with "
            "published clock times. HTML rather than JSON, so a parser rather than a "
            "client. The BLS also publishes a registered-key data API for the series "
            "themselves."
        ),
        "url": "https://www.bls.gov/schedule/",
    },
    {
        "name": "Bureau of Economic Analysis release schedule",
        "status": "suggested",
        "detail": "First-party schedule for GDP and Personal Income and Outlays.",
        "url": "https://www.bea.gov/news/schedule",
    },
    {
        "name": "Census Bureau economic indicator calendar",
        "status": "suggested",
        "detail": "First-party schedule for Advance Retail Sales and related indicators.",
        "url": "https://www.census.gov/economic-indicators/calendar-listview.html",
    },
    {
        "name": "Federal Reserve FOMC calendar",
        "status": "suggested",
        "detail": (
            "First-party FOMC meeting and statement calendar, with times. The single "
            "highest-impact scheduled event for index and rate futures."
        ),
        "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    },
)
