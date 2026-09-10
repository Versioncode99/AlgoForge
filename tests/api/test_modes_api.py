"""The four modes, end to end through the API the interface actually calls.

Everything below goes through HTTP or through the shared action registry, so a
passing assertion here says the same thing about the interface and about an
agent: they call one implementation.

The security tests are the reason this file exists. Each one describes a way an
AI actor could reach something it must not, and asserts that it cannot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge.modes.permissions import Actor
from forge_api.actions import ActionError, ApprovalRequired
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    app = create_app(tmp_path / "modes.db")
    with TestClient(app) as running:
        running.app_state = app.state  # type: ignore[attr-defined]
        yield running


def data(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def enter(client: Any, mode: str, stance: str | None = None) -> Any:
    return data(client.post(f"/api/v1/modes/{mode}/enter", json={"stance": stance}))


# ── the manifest and the session ─────────────────────────────────────────────


def test_the_api_serves_four_modes_and_the_loop(client: Any) -> None:
    payload = data(client.get("/api/v1/modes"))
    assert [mode["mode"] for mode in payload["modes"]] == [
        "normal", "prop_firm", "ai", "hedge_fund"
    ]
    assert len(payload["loop"]) == 11
    assert payload["loop"][0]["stage"] == "data"


def test_nothing_is_open_until_a_mode_is_entered(client: Any) -> None:
    session = data(client.get("/api/v1/modes/session"))
    assert session["session"]["mode"] is None
    # The descriptor is a fallback for the policy, not the open mode. Saying so
    # is what stops the chooser quietly rendering as AI mode.
    assert session["policy_applies"] is False


@pytest.mark.parametrize("mode", ["normal", "prop_firm", "ai", "hedge_fund"])
def test_every_mode_opens_and_seeds_its_own_workspace(client: Any, mode: str) -> None:
    opened = enter(client, mode)
    assert opened["session"]["mode"] == mode
    assert opened["workspace"] is not None
    assert opened["workspace"]["panels"], f"{mode} opened on an empty layout"
    assert opened["descriptor"]["sections"]


def test_entering_the_fund_on_a_stance_records_it(client: Any) -> None:
    opened = enter(client, "hedge_fund", "autonomous")
    assert opened["session"]["stance"] == "autonomous"
    assert "unattended" in opened["policy"]["summary"]


def test_a_stance_on_a_mode_that_has_none_is_refused(client: Any) -> None:
    response = client.post("/api/v1/modes/normal/enter", json={"stance": "autonomous"})
    assert response.status_code == 409
    assert "no operating stances" in response.json()["detail"]["reason"]


def test_an_unknown_mode_is_refused_with_the_valid_set(client: Any) -> None:
    response = client.post("/api/v1/modes/day_trading/enter", json={})
    assert response.status_code == 409
    assert "hedge_fund" in response.json()["detail"]["reason"]


# ── switching does not corrupt anything ──────────────────────────────────────


def test_each_mode_keeps_its_own_layout_across_a_switch(client: Any) -> None:
    normal = enter(client, "normal")["session"]["workspace_id"]
    fund = enter(client, "hedge_fund")["session"]["workspace_id"]
    assert normal and fund and normal != fund

    back = enter(client, "normal")
    assert back["session"]["workspace_id"] == normal
    # Returning opens the layout that mode was left on, not whichever was active.
    assert data(client.get("/api/v1/workspaces/active"))["workspace_id"] == normal


def test_leaving_returns_to_the_chooser_without_forgetting_a_layout(client: Any) -> None:
    seeded = enter(client, "prop_firm")["session"]["workspace_id"]
    assert data(client.post("/api/v1/modes/leave"))["session"]["mode"] is None
    assert enter(client, "prop_firm")["session"]["workspace_id"] == seeded


def test_a_seeded_workspace_holds_the_panels_the_mode_declares(client: Any) -> None:
    opened = enter(client, "hedge_fund")
    kinds = {panel["kind"] for panel in opened["workspace"]["panels"]}
    assert {"fund_summary", "portfolio", "pretrade_gate"} <= kinds


# ── the permission surface ───────────────────────────────────────────────────


def test_the_permissions_endpoint_rules_on_every_registered_action(client: Any) -> None:
    enter(client, "hedge_fund", "human_in_the_loop")
    payload = data(client.get("/api/v1/modes/permissions"))
    rulings = {row["action"]: row["ruling"] for row in payload["actions"]}
    assert set(rulings) == set(client.app_state.actions.names())
    assert rulings["set_fund_config"] == "deny"
    assert rulings["submit_orders"] == "require_approval"
    assert rulings["backtest_strategy"] == "allow"


def test_the_permissions_endpoint_follows_the_stance(client: Any) -> None:
    enter(client, "hedge_fund", "human_in_the_loop")
    supervised = {
        row["action"]: row["ruling"]
        for row in data(client.get("/api/v1/modes/permissions"))["actions"]
    }
    enter(client, "hedge_fund", "autonomous")
    autonomous = {
        row["action"]: row["ruling"]
        for row in data(client.get("/api/v1/modes/permissions"))["actions"]
    }
    assert supervised["submit_orders"] == "require_approval"
    assert autonomous["submit_orders"] == "allow"
    # And the thing that must not move.
    assert supervised["set_fund_config"] == autonomous["set_fund_config"] == "deny"


# ── what an AI actor cannot do ───────────────────────────────────────────────


def test_an_agent_cannot_change_a_protected_control_in_any_mode(client: Any) -> None:
    actions = client.app_state.actions
    for mode in ("normal", "prop_firm", "ai", "hedge_fund"):
        enter(client, mode, "autonomous" if mode == "hedge_fund" else None)
        with pytest.raises(ActionError, match="protected control"):
            actions.call("set_fund_config", {"config": {}}, actor=Actor.AI)


def test_an_agent_cannot_widen_its_own_permissions(client: Any) -> None:
    """The escape route: switch to autonomous, then do what autonomous permits."""
    actions = client.app_state.actions
    enter(client, "hedge_fund", "human_in_the_loop")
    with pytest.raises(ActionError, match="protected control"):
        actions.call("set_stance", {"stance": "autonomous"}, actor=Actor.AI)
    with pytest.raises(ActionError, match="protected control"):
        actions.call("enter_mode", {"mode": "hedge_fund", "stance": "autonomous"},
                     actor=Actor.AI)
    assert data(client.get("/api/v1/modes/session"))["session"]["stance"] == "human_in_the_loop"


def test_an_agent_cannot_write_a_prop_account_balance(client: Any) -> None:
    """A balance an agent could write is a rule engine an agent could satisfy."""
    actions = client.app_state.actions
    enter(client, "prop_firm")
    for name in ("create_prop_account", "update_prop_rules", "record_prop_state"):
        with pytest.raises(ActionError, match="protected control"):
            actions.call(name, {}, actor=Actor.AI)


def test_a_consequential_action_is_held_rather_than_run_in_the_loop(client: Any) -> None:
    actions = client.app_state.actions
    enter(client, "hedge_fund", "human_in_the_loop")
    with pytest.raises(ApprovalRequired) as held:
        actions.call("submit_orders", {"order_ids": ["o1"]}, actor=Actor.AI,
                     origin="test agent")
    request = held.value.request
    assert request.action == "submit_orders"
    assert request.arguments == {"order_ids": ["o1"]}

    pending = data(client.get("/api/v1/approvals"))["pending"]
    assert [row["request_id"] for row in pending] == [request.request_id]


def test_approving_runs_the_action_as_the_operator_and_records_the_chain(
    client: Any,
) -> None:
    actions = client.app_state.actions
    enter(client, "hedge_fund", "human_in_the_loop")
    with pytest.raises(ApprovalRequired) as held:
        actions.call("submit_orders", {"order_ids": ["never_screened"]}, actor=Actor.AI)
    request_id = held.value.request.request_id

    decided = data(client.post(f"/api/v1/approvals/{request_id}/approve", json={"note": "ok"}))
    assert decided["status"] == "approved"
    # It ran, and it refused honestly: the order was never screened by the gate.
    assert "not been screened" in decided["result"]

    entries = data(client.get("/api/v1/audit"))["entries"]
    linked = [row for row in entries if row["approval_id"] == request_id]
    assert {row["actor"] for row in linked} == {"ai", "human"}


def test_rejecting_leaves_nothing_run(client: Any) -> None:
    actions = client.app_state.actions
    enter(client, "hedge_fund", "human_in_the_loop")
    with pytest.raises(ApprovalRequired) as held:
        actions.call("submit_orders", {"order_ids": ["o1"]}, actor=Actor.AI)
    decided = data(
        client.post(f"/api/v1/approvals/{held.value.request.request_id}/reject", json={})
    )
    assert decided["status"] == "rejected"
    assert data(client.get("/api/v1/approvals"))["pending"] == []


def test_an_agent_may_run_the_preparatory_half_of_the_loop(client: Any) -> None:
    """The permission that has to work, or the assistant is decorative."""
    actions = client.app_state.actions
    enter(client, "hedge_fund", "human_in_the_loop")
    for name in ("fund_state", "calculate_risk", "list_strategies"):
        assert actions.call(name, {}, actor=Actor.AI) is not None


# ── the audit trail ──────────────────────────────────────────────────────────


def test_every_call_is_audited_including_the_refusals(client: Any) -> None:
    actions = client.app_state.actions
    enter(client, "hedge_fund", "human_in_the_loop")
    actions.call("fund_state", {}, actor=Actor.AI, origin="test agent")
    with pytest.raises(ActionError):
        actions.call("set_fund_config", {"config": {}}, actor=Actor.AI, origin="test agent")

    payload = data(client.get("/api/v1/audit"))
    by_action = {row["action"]: row for row in payload["entries"]}
    assert by_action["fund_state"]["outcome"] == "ok"
    assert by_action["set_fund_config"]["outcome"] == "denied"
    assert "protected" in by_action["set_fund_config"]["ruling_reason"]
    assert payload["summary"]["denied"] >= 1


def test_the_audit_records_which_mode_and_stance_a_call_was_made_under(
    client: Any,
) -> None:
    actions = client.app_state.actions
    enter(client, "hedge_fund", "autonomous")
    actions.call("fund_state", {}, actor=Actor.AI)
    entry = data(client.get("/api/v1/audit"))["entries"][0]
    assert entry["mode"] == "hedge_fund"
    assert entry["stance"] == "autonomous"


# ── the fund, through HTTP ───────────────────────────────────────────────────


def test_a_fund_with_no_universe_refuses_to_construct(client: Any) -> None:
    enter(client, "hedge_fund")
    response = client.post("/api/v1/fund/portfolio", json={})
    assert response.status_code == 409
    assert "no universe" in response.json()["detail"]["reason"]


def test_the_fund_reports_every_loop_stage_with_a_state(client: Any) -> None:
    enter(client, "hedge_fund")
    state = data(client.get("/api/v1/fund/state"))
    assert len(state["stages"]) == 11
    assert all(stage["status"] for stage in state["stages"])
    # A fresh installation looks like one rather than like a flat fund.
    assert state["stages"][0]["summary"] == "no universe configured"
    assert state["execution_mode"] == "PAPER"
    assert state["limitations"]


def test_the_fund_admits_it_cannot_mark_open_positions(client: Any) -> None:
    enter(client, "hedge_fund")
    limitations = " ".join(data(client.get("/api/v1/fund/state"))["limitations"])
    assert "mark-to-market" in limitations
    assert "MTD" in limitations


def test_configuring_the_fund_is_recorded_with_who_changed_it(client: Any) -> None:
    enter(client, "hedge_fund")
    config = data(client.get("/api/v1/fund/config"))["config"]
    config["universe"] = [
        {"symbol": "NQ", "sector": "Equity Index", "region": "US", "multiplier": 20,
         "price": 20_000, "adv_notional": 5e10, "shortable": True},
    ]
    saved = data(client.put("/api/v1/fund/config", json={"config": config, "note": "seed"}))
    assert len(saved["config"]["universe"]) == 1
    history = data(client.get("/api/v1/fund/config"))["history"]
    assert history[0]["changed_by"] == "operator"
    assert history[0]["note"] == "seed"


def test_an_unscreened_order_cannot_be_submitted(client: Any) -> None:
    """The gate is not advisory, and this is the path that proves it over HTTP."""
    enter(client, "hedge_fund")
    payload = data(client.post("/api/v1/fund/orders/submit", json={"order_ids": ["made_up"]}))
    assert payload["accepted"] == 0
    assert "not been screened" in payload["results"][0]["reason"]


def test_the_operations_view_reports_a_simulated_book(client: Any) -> None:
    enter(client, "hedge_fund")
    operations = data(client.get("/api/v1/fund/operations"))
    assert operations["execution_mode"] == "PAPER"
    assert operations["book"]["simulated"] is True
    assert operations["reconciliation"]["reconciled"] is True
    assert any("simulated" in note for note in operations["limitations"])


# ── prop firm, through HTTP ──────────────────────────────────────────────────


def test_a_prop_account_can_be_configured_and_assessed(client: Any) -> None:
    enter(client, "prop_firm")
    assert data(client.get("/api/v1/prop/accounts"))["accounts"] == []

    created = data(client.post("/api/v1/prop/accounts", json={"rules": {
        "name": "50k Evaluation", "starting_balance": 50_000, "maximum_loss": 2_000,
        "daily_loss_limit": 1_000, "profit_target": 3_000, "trail_mode": "end_of_day",
        "floor_cap": 50_000,
    }}))["account"]

    # Nothing recorded yet: the engine says so rather than drawing a flat account.
    status = data(client.get("/api/v1/prop/accounts/status"))
    assert status["assessment"] is None
    assert "no state has been recorded" in status["reason"]

    recorded = data(client.post(
        f"/api/v1/prop/accounts/{created['account_id']}/state",
        json={
            "state": {
                "as_of": "2026-03-04T15:00:00Z", "balance": 50_800, "equity": 50_600,
                "high_water_balance": 51_000, "high_water_equity": 51_000,
                "realised_today": -200, "unrealised": -200,
            },
            "source": "entered by the operator",
        },
    ))
    assessment = recorded["assessment"]
    assert assessment["loss_floor"] == 49_000
    daily = next(row for row in assessment["statuses"] if row["key"] == "daily_loss")
    assert daily["buffer"] == 600.0
    assert assessment["can_trade"] is True


def test_a_breaching_account_is_reported_as_unable_to_trade(client: Any) -> None:
    enter(client, "prop_firm")
    created = data(client.post("/api/v1/prop/accounts", json={"rules": {
        "name": "50k", "starting_balance": 50_000, "maximum_loss": 2_000,
        "daily_loss_limit": 1_000,
    }}))["account"]
    recorded = data(client.post(
        f"/api/v1/prop/accounts/{created['account_id']}/state",
        json={
            "state": {
                "as_of": "2026-03-04T15:00:00Z", "balance": 47_900, "equity": 47_900,
                "high_water_balance": 50_000, "high_water_equity": 50_000,
            },
            "source": "entered by the operator",
        },
    ))
    assert recorded["assessment"]["can_trade"] is False
    assert "max_drawdown" in recorded["assessment"]["breaches"]


def test_a_snapshot_must_say_where_its_numbers_came_from(client: Any) -> None:
    enter(client, "prop_firm")
    created = data(client.post("/api/v1/prop/accounts", json={"rules": {
        "name": "50k", "starting_balance": 50_000, "maximum_loss": 2_000,
    }}))["account"]
    response = client.post(
        f"/api/v1/prop/accounts/{created['account_id']}/state",
        json={"state": {"as_of": "2026-03-04T15:00:00Z", "balance": 50_000, "equity": 50_000,
                        "high_water_balance": 50_000, "high_water_equity": 50_000},
              "source": "  "},
    )
    assert response.status_code == 409
    # The refusal names the field rather than saying the request was invalid.
    assert "'source'" in response.json()["detail"]["reason"]


def test_deleting_a_layout_clears_every_mode_pointing_at_it(client: Any) -> None:
    """A mode left holding a deleted layout opens on nothing, with no way to tell why."""
    seeded = enter(client, "normal")["session"]["workspace_id"]
    assert client.delete(f"/api/v1/workspaces/{seeded}").status_code == 200
    reopened = enter(client, "normal")
    assert reopened["session"]["workspace_id"] != seeded
    assert reopened["workspace"]["panels"], "the mode did not re-seed its layout"
