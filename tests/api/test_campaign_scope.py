"""Choosing the research window, from the form to the bars the engine loads.

`Campaign.start_date` and `end_date` were on the model, stored in their own
columns, returned by the API — and sent by nothing. Every campaign therefore ran
on `tail(250_000)`: about nine months of one-minute bars, always the most recent
nine months, whatever the hypothesis was about. The default objective even said
"the full available history", which was the one window it never got.

These tests are about the half that was missing: the window is *chosen*, what
the chooser shows is what will run, and nothing silently substitutes full
history when a choice was made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app

#: A sixteen-year reservoir with a known end, so a preset's arithmetic can be
#: asserted rather than eyeballed.
END = datetime(2026, 1, 1, tzinfo=UTC)
START = END - timedelta(days=365.25 * 16)
DATASET = "nq_1m_16y"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    app = create_app(tmp_path / "scope.db")
    market = app.state.actions.market
    # The reservoir, stubbed so these assertions are about the *window
    # arithmetic* rather than about whichever archive happens to be on the
    # machine running them. Everything downstream of it is the real code.
    monkeypatch.setattr(market, "reservoir", lambda key: (START, END))
    monkeypatch.setattr(market, "bars_per_year", lambda key: 360_000.0)
    with TestClient(app) as running:
        running.app_state = app.state  # type: ignore[attr-defined]
        yield running


def scope(client: Any, **params: str) -> dict[str, Any]:
    response = client.get("/api/v1/campaigns/scope", params={"dataset": DATASET, **params})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def day(iso: str) -> str:
    return iso[:10]


# ── the presets ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("preset", "years"),
    [("0.0833", 0.0833), ("0.25", 0.25), ("0.5", 0.5), ("1", 1.0), ("2", 2.0),
     ("3", 3.0), ("5", 5.0), ("10", 10.0)],
)
def test_every_preset_resolves_to_that_many_years_back_from_the_archive_end(
    client: Any, preset: str, years: float
) -> None:
    """Back from the end, not forward from the start.

    "The last two years" is what somebody means by a two-year window, and
    anchoring it to the start of a sixteen-year archive would hand them 2009
    and 2010 without saying so.
    """
    payload = scope(client, preset=preset)
    assert payload["selected"] is True
    expected = (END - timedelta(days=years * 365.25)).date().isoformat()
    assert day(payload["selected_start"]) == expected
    assert day(payload["selected_end"]) == END.date().isoformat()
    assert payload["selected_years"] == pytest.approx(years, abs=0.02)


def test_the_full_dataset_preset_names_the_whole_reservoir_explicitly(client: Any) -> None:
    """Explicit dates, not "no window".

    The difference matters: a campaign with dates carries a scope fingerprint
    into every experiment's provenance, and a campaign without one runs on
    whatever the engine last loaded. "Full history" chosen deliberately and
    "full history" by default are not the same claim.
    """
    payload = scope(client, preset="full")
    assert day(payload["selected_start"]) == START.date().isoformat()
    assert day(payload["selected_end"]) == END.date().isoformat()
    assert payload["fingerprint"]


def test_a_preset_longer_than_the_archive_gives_the_archive(client: Any) -> None:
    """Fifty years of a sixteen-year archive is sixteen years, reported as sixteen.

    `clamped` is False here on purpose: the preset resolved *to* the archive
    start, so nothing was cut off what was asked for — the window it names is
    the window it gets. A custom range reaching past the archive is the case
    that sets the flag, and the test below is that one.
    """
    payload = scope(client, preset="50")
    assert day(payload["selected_start"]) == START.date().isoformat()
    assert payload["clamped"] is False
    assert payload["selected_years"] <= 16.1
    assert payload["selected_years"] >= 15.9


# ── custom ranges ────────────────────────────────────────────────────────────


def test_a_custom_range_is_used_verbatim(client: Any) -> None:
    payload = scope(client, start="2018-03-01", end="2020-09-30")
    assert day(payload["selected_start"]) == "2018-03-01"
    assert day(payload["selected_end"]) == "2020-09-30"
    assert payload["method"] == "FIXED_DATE_RANGE"


def test_a_range_reaching_before_the_archive_is_clamped_visibly(client: Any) -> None:
    payload = scope(client, start="1990-01-01", end="2012-01-01")
    assert day(payload["selected_start"]) == START.date().isoformat()
    assert payload["clamped"] is True, "a silently widened window is a changed claim"


def test_a_reversed_range_is_refused_rather_than_swapped(client: Any) -> None:
    response = client.get(
        "/api/v1/campaigns/scope",
        params={"dataset": DATASET, "start": "2020-01-01", "end": "2018-01-01"},
    )
    assert response.status_code >= 400
    assert "not a window this dataset holds" in response.text


def test_a_range_entirely_outside_the_archive_is_refused(client: Any) -> None:
    response = client.get(
        "/api/v1/campaigns/scope",
        params={"dataset": DATASET, "start": "2030-01-01", "end": "2031-01-01"},
    )
    assert response.status_code >= 400


def test_no_dates_at_all_says_what_will_happen_rather_than_implying_a_choice(
    client: Any,
) -> None:
    payload = scope(client)
    assert payload["selected"] is False
    assert "whatever the engine last loaded" in payload["reason"]


# ── what the chooser must show ───────────────────────────────────────────────


def test_the_readout_carries_every_fact_the_form_shows(client: Any) -> None:
    payload = scope(client, preset="2")
    for key in (
        "available_start", "available_end", "available_years",
        "selected_start", "selected_end", "selected_years",
        "approximate_bars", "warmup_bars", "required_bars", "sufficient",
        "fingerprint", "method", "timezone",
    ):
        assert key in payload, f"the chooser has no {key} to show"
    assert payload["warmup_bars"] > 0
    assert "UTC" in payload["timezone"]


def test_warmup_is_stated_as_outside_the_scored_window(client: Any) -> None:
    payload = scope(client, preset="2")
    assert "purged out of the scored partitions" in payload["note"]


def test_a_window_too_small_to_partition_warns_instead_of_running(client: Any) -> None:
    """Warned about, not silently widened.

    `chronological_split` refuses a window it cannot cut into three partitions
    with two purge gaps. Finding that out after starting a campaign is finding
    out that nothing it produced can reach validation.
    """
    payload = scope(client, start="2025-12-30", end="2025-12-31")
    assert payload["sufficient"] is False
    assert "cannot be split" in payload["warning"]
    assert str(payload["required_bars"]) in payload["warning"].replace(",", "")


def test_the_same_window_has_the_same_fingerprint_and_a_different_one_does_not(
    client: Any,
) -> None:
    """Identity, so a resume is the same question and an edit is a new one."""
    two = scope(client, preset="2")["fingerprint"]
    again = scope(client, preset="2")["fingerprint"]
    three = scope(client, preset="3")["fingerprint"]
    assert two == again
    assert two != three


# ── end to end: the form's dates reach the campaign ──────────────────────────


def test_the_window_is_stored_on_the_campaign_and_returned_with_it(client: Any) -> None:
    chosen = scope(client, preset="2")
    created = client.post(
        "/api/v1/campaigns",
        json={
            "name": "Windowed",
            "objective": "Whether opening-range breakouts carry on NQ over the chosen window.",
            "dataset": DATASET,
            "start_date": day(chosen["selected_start"]),
            "end_date": day(chosen["selected_end"]),
        },
    )
    assert created.status_code in (200, 201), created.text
    campaign = created.json()["data"]
    assert campaign["start_date"] == day(chosen["selected_start"])
    assert campaign["end_date"] == day(chosen["selected_end"])

    # And it survives a read, which is what a resume does.
    read = client.get(f"/api/v1/campaigns/{campaign['campaign_id']}")
    assert read.status_code == 200, read.text
    assert read.json()["data"]["campaign"]["start_date"] == campaign["start_date"]


def test_the_stored_window_becomes_the_scope_the_engine_loads(client: Any) -> None:
    """The join that was missing: dates on the model, bars in the engine.

    `Campaign.time_scope` is what `AutonomousEngine.scope_for` calls, and
    `MarketService.load_scope` is what turns its answer into bars. Asserting the
    scope here is asserting that the dates chosen in the form are the dates the
    engine will slice on — the step that used to be absent entirely.
    """
    from forge.research.campaign import Campaign

    created = client.post(
        "/api/v1/campaigns",
        json={
            "name": "Windowed",
            "objective": "Whether opening-range breakouts carry on NQ over the chosen window.",
            "dataset": DATASET,
            "start_date": "2020-01-01",
            "end_date": "2022-01-01",
        },
    )
    assert created.status_code in (200, 201), created.text
    stored: Campaign = client.app_state.actions.campaigns.campaigns.get(  # type: ignore[attr-defined]
        created.json()["data"]["campaign_id"]
    )
    built = stored.time_scope(START, END)
    assert built is not None, "the campaign's dates produced no scope"
    assert built.selected_start.date().isoformat() == "2020-01-01"
    assert built.selected_end.date().isoformat() == "2022-01-01"
    assert built.dataset == DATASET

    # And a campaign configured *without* dates still returns `None`, so every
    # campaign created before this change runs exactly as it always did rather
    # than acquiring a window nobody chose.
    unscoped = client.post(
        "/api/v1/campaigns",
        json={
            "name": "Unwindowed",
            "objective": "Whether opening-range breakouts carry on NQ, window unchosen.",
            "dataset": DATASET,
        },
    )
    assert unscoped.status_code in (200, 201), unscoped.text
    legacy: Campaign = client.app_state.actions.campaigns.campaigns.get(  # type: ignore[attr-defined]
        unscoped.json()["data"]["campaign_id"]
    )
    assert legacy.start_date == "" and legacy.end_date == ""
    assert legacy.time_scope(START, END) is None
