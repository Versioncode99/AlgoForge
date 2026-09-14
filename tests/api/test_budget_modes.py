"""§12's three modes, and the one that could not be a boolean.

    ENFORCED · UNLIMITED_WITH_SAFETY_LIMITS · ADAPTIVE

`ADAPTIVE` is the reason the switch changed shape: an operator wants to spend
freely while it is cheap and be stopped when it is not, and a two-state switch
cannot express that. Everything below is about when a ceiling bites, never about
what a ceiling is -- the numbers are the operator's.
"""

from __future__ import annotations

import pytest
from forge_api.settings_store import BUDGET_MODES, SAFETY_LIMITS, BudgetMode, BudgetSettings


def settings(mode: BudgetMode, **over: object) -> BudgetSettings:
    fields: dict[str, object] = {
        "mode": mode,
        "daily_usd_soft": 9.0,
        "model_calls_per_day": 100,
        "campaign_experiments": 9,
    }
    fields.update(over)
    return BudgetSettings(**fields)  # type: ignore[arg-type]


def test_the_three_modes_are_the_three_the_directive_names() -> None:
    assert {str(mode) for mode in BudgetMode} == {
        "ENFORCED",
        "UNLIMITED_WITH_SAFETY_LIMITS",
        "ADAPTIVE",
    }
    assert {row["key"] for row in BUDGET_MODES} == {str(mode) for mode in BudgetMode}


def test_enforced_applies_the_ceiling_whatever_has_been_spent() -> None:
    budget = settings(BudgetMode.ENFORCED)
    assert budget.limit("model_calls_per_day") == 100
    assert budget.limit("model_calls_per_day", spent_usd=0.0) == 100
    assert budget.limit("model_calls_per_day", spent_usd=500.0) == 100


def test_unlimited_applies_none_of_them() -> None:
    budget = settings(BudgetMode.UNLIMITED_WITH_SAFETY_LIMITS)
    assert budget.limit("model_calls_per_day") == 0
    assert budget.limit("campaign_experiments", spent_usd=1000.0) == 0
    # The configured numbers survive, so turning it back on restores the
    # ceilings rather than making the operator type them again.
    assert budget.model_calls_per_day == 100


def test_adaptive_does_not_interrupt_while_the_spending_is_small() -> None:
    budget = settings(BudgetMode.ADAPTIVE)
    assert budget.limit("model_calls_per_day", spent_usd=0.0) == 0
    assert budget.limit("campaign_experiments", spent_usd=8.99) == 0


def test_adaptive_applies_everything_once_the_soft_threshold_is_crossed() -> None:
    budget = settings(BudgetMode.ADAPTIVE)
    assert budget.limit("model_calls_per_day", spent_usd=9.0) == 100
    assert budget.limit("campaign_experiments", spent_usd=40.0) == 9


def test_adaptive_enforces_when_the_spend_is_not_known() -> None:
    """The fail-safe direction, and the reason it is the only one available.

    A caller that cannot say what has been spent has not established that
    spending is low. Treating unknown as "under the threshold" would make the
    ceilings stop applying exactly when the accounting broke, which is when they
    matter most.
    """
    budget = settings(BudgetMode.ADAPTIVE)
    assert budget.limit("model_calls_per_day") == 100
    assert budget.limit("model_calls_per_day", spent_usd=None) == 100


def test_a_mode_that_survived_a_round_trip_still_behaves_like_one() -> None:
    """StrEnum members equal their string and are not identical to it.

    Settings round-trip through JSON, every decision in `limit` is an `is`
    check, and without coercion a mode loaded from disk silently behaved as
    ENFORCED whatever it said -- which looks exactly like the setting not
    saving.
    """
    stored = BudgetSettings(mode="UNLIMITED_WITH_SAFETY_LIMITS", model_calls_per_day=100)  # type: ignore[arg-type]
    assert stored.mode is BudgetMode.UNLIMITED_WITH_SAFETY_LIMITS
    assert stored.limit("model_calls_per_day") == 0


def test_an_unreadable_mode_enforces_rather_than_lifting_every_ceiling() -> None:
    """A settings file this build does not understand is not permission."""
    unknown = BudgetSettings(mode="SOMETHING_A_LATER_BUILD_ADDED", model_calls_per_day=100)  # type: ignore[arg-type]
    assert unknown.mode is BudgetMode.ENFORCED
    assert unknown.limit("model_calls_per_day") == 100


def test_only_unlimited_reports_itself_as_unenforced() -> None:
    """ADAPTIVE's ceilings are configured and can stop a campaign, so it is
    enforced -- *when* they bite is `limit`'s business, not this flag's."""
    assert settings(BudgetMode.ENFORCED).enforced is True
    assert settings(BudgetMode.ADAPTIVE).enforced is True
    assert settings(BudgetMode.UNLIMITED_WITH_SAFETY_LIMITS).enforced is False


def test_no_mode_touches_a_safety_limit() -> None:
    """Safety limits are about what this process can survive, not what the
    research is worth. No budget mode is an input to any of them."""
    keys = {row["key"] for row in SAFETY_LIMITS}
    for mode in BudgetMode:
        assert not (keys & set(vars(settings(mode)))), (
            f"{mode} shares a field name with a safety limit, which is how the two "
            "start being confused for one another"
        )


# ── over HTTP ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    from fastapi.testclient import TestClient
    from forge_api.main import create_app

    with TestClient(create_app(database_path=tmp_path / "budget.db")) as client:
        yield client


def _patch(client, body):
    response = client.patch("/api/v1/settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_the_modes_travel_with_the_settings(client) -> None:
    """So the screen cannot offer a mode the code no longer implements."""
    body = client.get("/api/v1/settings").json()["data"]
    assert {row["key"] for row in body["budget_modes"]} == {str(mode) for mode in BudgetMode}
    assert body["ai"]["budget"]["mode"] == str(BudgetMode.ENFORCED)


def test_the_mode_can_be_set_and_survives_a_reload(client) -> None:
    after = _patch(client, {"budget_mode": "ADAPTIVE"})
    assert after["ai"]["budget"]["mode"] == "ADAPTIVE"
    # Read back from the store rather than from the response to the write.
    assert client.get("/api/v1/settings").json()["data"]["ai"]["budget"]["mode"] == "ADAPTIVE"


def test_the_old_boolean_still_works_and_is_translated(client) -> None:
    """A settings screen from before this change must not silently stop working."""
    off = _patch(client, {"budget_enforced": False})
    assert off["ai"]["budget"]["mode"] == "UNLIMITED_WITH_SAFETY_LIMITS"
    assert off["ai"]["budget"]["enforced"] is False

    on = _patch(client, {"budget_enforced": True})
    assert on["ai"]["budget"]["mode"] == "ENFORCED"
    assert on["ai"]["budget"]["enforced"] is True


def test_the_mode_wins_when_both_arrive(client) -> None:
    after = _patch(client, {"budget_enforced": False, "budget_mode": "ADAPTIVE"})
    assert after["ai"]["budget"]["mode"] == "ADAPTIVE"


def test_an_unknown_mode_is_refused_with_the_ones_that_exist(client) -> None:
    response = client.patch("/api/v1/settings", json={"budget_mode": "WHATEVER"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "unknown_budget_mode"
    assert set(detail["known"]) == {str(mode) for mode in BudgetMode}
