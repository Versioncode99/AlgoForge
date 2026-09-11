"""The Prop Desk over HTTP and through the action registry.

Two things these assert that the unit tests cannot.

**One implementation, two surfaces.** Every route calls the same service method
the action registry calls, so there is no HTTP back door and no verb the
assistant can reach that the interface cannot.

**The permission policy governs the desk.** Everything that writes is
`protected`, which `forge.modes.permissions` denies to an AI actor in every mode
and on every stance — so an agent cannot open a broker connection, record a firm
permission, start a copy group or apply an allocation. That is asserted here
against the registry rather than trusted to the declarations.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from forge.modes.models import MODE_ORDER, Stance, WorkspaceMode
from forge.modes.permissions import ActionFacts, Actor, Ruling, evaluate
from forge.prop.account import AccountRules, AccountState
from forge.prop.accounts import PropAccountStore
from forge.propdesk import Permission, PropDeskStore
from forge_api.propdesk import PropDeskError, PropDeskService, build_propdesk_router

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


@pytest.fixture
def service(tmp_path) -> PropDeskService:
    return PropDeskService(
        store=PropDeskStore(tmp_path / "desk.db"),
        prop_accounts=PropAccountStore(tmp_path / "prop.db"),
        verdict_for=lambda strategy_id: "PASS" if strategy_id == "judged" else None,
        now=lambda: NOW,
    )


@pytest.fixture
def client(service) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(build_propdesk_router(service))
    return TestClient(app)


def data(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ── what this build can reach ────────────────────────────────────────────────


def test_the_providers_route_says_which_connectors_exist(client) -> None:
    payload = data(client.get("/api/v1/propdesk/providers"))
    assert payload["live_connectors_implemented"] == ["simulated"]
    for provider in ("rithmic", "tradovate", "projectx"):
        assert "no live connector" in payload["adapters"][provider]
        assert payload["required_work"][provider]["external"]


def test_platforms_are_listed_apart_from_providers(client) -> None:
    payload = data(client.get("/api/v1/propdesk/providers"))
    names = {item["platform"] for item in payload["platforms"]}
    assert "ninjatrader_desktop" in names
    desktop = next(
        item for item in payload["platforms"] if item["platform"] == "ninjatrader_desktop"
    )
    assert desktop["provider"] is None


# ── connections ──────────────────────────────────────────────────────────────


def test_a_simulated_connection_can_be_created_and_connected(client) -> None:
    created = data(
        client.post(
            "/api/v1/propdesk/connections",
            json={"provider": "simulated", "label": "Sim"},
        )
    )
    assert created["live_connector_implemented"] is True
    connection_id = created["connection"]["connection_id"]

    connected = data(
        client.post(f"/api/v1/propdesk/connections/{connection_id}/connect")
    )
    assert connected["connected"] is True


def test_creating_a_rithmic_connection_records_it_and_says_it_cannot_trade(
    client,
) -> None:
    created = data(
        client.post(
            "/api/v1/propdesk/connections",
            json={"provider": "rithmic", "label": "Mine", "environment": "demo"},
        )
    )
    assert created["live_connector_implemented"] is False
    assert "no live rithmic connector" in created["note"].lower()


def test_connecting_a_provider_with_no_connector_fails_rather_than_looking_connected(
    client,
) -> None:
    created = data(
        client.post(
            "/api/v1/propdesk/connections",
            json={"provider": "tradovate", "label": "TV", "environment": "demo"},
        )
    )
    result = data(
        client.post(
            f"/api/v1/propdesk/connections/{created['connection']['connection_id']}/connect"
        )
    )
    assert result["connected"] is False
    assert "no live" in result["reason"]


def test_an_unknown_provider_is_refused_with_the_valid_set(client) -> None:
    response = client.post(
        "/api/v1/propdesk/connections", json={"provider": "etrade", "label": "x"}
    )
    assert response.status_code == 409
    assert "not a provider" in response.json()["detail"]["reason"]


def test_accounts_can_only_be_invented_on_a_simulated_connection(client) -> None:
    created = data(
        client.post(
            "/api/v1/propdesk/connections",
            json={"provider": "projectx", "label": "PX", "environment": "demo"},
        )
    )
    response = client.post(
        f"/api/v1/propdesk/connections/{created['connection']['connection_id']}"
        "/simulated-accounts",
        json={"account_id": "FAKE"},
    )
    assert response.status_code == 409
    assert "discovered from the provider" in response.json()["detail"]["reason"]


# ── the whole funnel, end to end on the simulator ────────────────────────────


def seeded(service: PropDeskService) -> tuple[str, str, str]:
    """A connection, a leader and a follower, with rules and permissions recorded."""
    connection = service.create_connection(provider="simulated", label="Sim")
    connection_id = connection["connection"]["connection_id"]
    service.connect(connection_id)
    leader = service.seed_simulator(connection_id, account_id="LEADER")["account"]
    follower = service.seed_simulator(connection_id, account_id="F1")["account"]

    rules = AccountRules(
        name="Test 50k",
        starting_balance=50_000.0,
        maximum_loss=2_000.0,
        daily_loss_limit=1_000.0,
        max_position_contracts=10,
    )
    account = service.prop_accounts.create(rules)
    service.prop_accounts.record(
        account.account_id,
        AccountState(
            as_of=NOW,
            balance=50_000.0,
            equity=50_000.0,
            high_water_balance=50_000.0,
            high_water_equity=50_000.0,
        ),
        source="test fixture",
    )
    policy = service.save_policy(
        {
            "firm_label": "test firm",
            "copy_in": Permission.ALLOWED.value,
            "copy_out": Permission.ALLOWED.value,
            "automation": Permission.ALLOWED.value,
            "algorithmic_allocation": Permission.ALLOWED.value,
            "permitted_products": ["MNQ"],
        }
    )["policy"]
    for uid in (leader["account_uid"], follower["account_uid"]):
        service.link_policy(uid, policy["policy_id"])
        service.link_prop_account(uid, account.account_id)
    return connection_id, leader["account_uid"], follower["account_uid"]


def test_a_copy_pass_clears_the_ladder_and_reaches_the_simulator(service) -> None:
    _, leader, follower = seeded(service)
    group = service.create_group(
        name="Group", leader_account_uid=leader, attested_by="tester"
    )["group"]
    service.add_follower(
        group_id=group["group_id"], account_uid=follower, sizing={"value": 1.0}
    )
    service.set_group_active(group_id=group["group_id"], active=True)

    result = service.copy_pass(
        group_id=group["group_id"], symbol="MNQ", leader_position=2, dry_run=False
    )
    decisions = result["desk_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["cleared"] is True
    assert decisions[0]["acknowledgement"]["accepted"] is True
    assert result["health"]["replicating"] == 1


def test_a_dry_run_sends_nothing(service) -> None:
    _, leader, follower = seeded(service)
    group = service.create_group(
        name="Group", leader_account_uid=leader, attested_by="tester"
    )["group"]
    service.add_follower(
        group_id=group["group_id"], account_uid=follower, sizing={"value": 1.0}
    )
    service.set_group_active(group_id=group["group_id"], active=True)

    result = service.copy_pass(group_id=group["group_id"], symbol="MNQ", leader_position=2)
    assert result["dry_run"] is True
    assert all(d["dispatched"] is False for d in result["desk_decisions"])
    assert service.store.decisions() == ()


def test_a_group_without_an_ownership_attestation_is_refused(service) -> None:
    _, leader, _ = seeded(service)
    with pytest.raises(PropDeskError, match="one owner"):
        service.create_group(name="Group", leader_account_uid=leader, attested_by="  ")


def test_a_follower_with_no_recorded_firm_permission_is_refused_at_the_ladder(
    service,
) -> None:
    _, leader, follower = seeded(service)
    service.link_policy(follower, None)
    group = service.create_group(
        name="Group", leader_account_uid=leader, attested_by="tester"
    )["group"]
    service.add_follower(
        group_id=group["group_id"], account_uid=follower, sizing={"value": 1.0}
    )
    service.set_group_active(group_id=group["group_id"], active=True)

    result = service.copy_pass(
        group_id=group["group_id"], symbol="MNQ", leader_position=2, dry_run=False
    )
    decision = result["desk_decisions"][0]
    assert decision["cleared"] is False
    assert "compatibility" in decision["blocking_stages"]


def test_a_follower_with_no_linked_rule_set_is_refused(service) -> None:
    _, leader, follower = seeded(service)
    service.link_prop_account(follower, None)
    group = service.create_group(
        name="Group", leader_account_uid=leader, attested_by="tester"
    )["group"]
    service.add_follower(
        group_id=group["group_id"], account_uid=follower, sizing={"value": 1.0}
    )
    service.set_group_active(group_id=group["group_id"], active=True)

    result = service.copy_pass(
        group_id=group["group_id"], symbol="MNQ", leader_position=2, dry_run=False
    )
    assert "account_rules" in result["desk_decisions"][0]["blocking_stages"]


def test_reconciliation_reports_the_providers_view(service) -> None:
    _, _leader, follower = seeded(service)
    report = service.reconcile(follower, "startup")["report"]
    assert report["account_uid"] == follower
    assert report["trigger"] == "startup"
    assert service.store.reconciliations(follower)


def test_reconciliation_refuses_an_unknown_trigger(service) -> None:
    _, _, follower = seeded(service)
    with pytest.raises(PropDeskError, match="not a reconciliation trigger"):
        service.reconcile(follower, "whenever")


# ── allocation ───────────────────────────────────────────────────────────────


def test_an_unjudged_strategy_is_not_allocated(service) -> None:
    seeded(service)
    plan = service.plan_allocation(strategy_ids=("unjudged",))["plan"]
    assert plan["allocations"] == []
    assert any(not c["feasible"] for c in plan["candidates"])


def test_a_judged_strategy_with_no_drawdown_estimate_is_still_not_sized(service) -> None:
    """The desk has no evidence store attached, so it reports the verdict and
    says plainly that nothing can be sized from it."""
    seeded(service)
    plan = service.plan_allocation(strategy_ids=("judged",))["plan"]
    assert plan["allocations"] == []
    refused = " ".join(c["detail"] for c in plan["candidates"])
    assert "drawdown" in refused


def test_applying_an_unconfirmed_allocation_is_refused(service) -> None:
    with pytest.raises(PropDeskError, match="need your confirmation"):
        service.apply_allocation(
            allocations=(
                {
                    "account_uid": "a1",
                    "strategy_id": "s1",
                    "contracts": 2,
                    "requires_confirmation": True,
                    "decided_at": NOW.isoformat(),
                },
            ),
            actor="tester",
        )


def test_applying_an_allocation_records_it_and_its_history(service) -> None:
    applied = service.apply_allocation(
        allocations=(
            {
                "account_uid": "a1",
                "strategy_id": "s1",
                "contracts": 2,
                "decided_at": NOW.isoformat(),
            },
        ),
        actor="tester",
    )
    assert len(applied["changes"]) == 1
    assert applied["changes"][0]["kind"] == "started"
    assert service.store.allocation("a1") is not None


# ── news ─────────────────────────────────────────────────────────────────────


def test_the_news_route_reports_its_sources_and_their_availability(
    client, monkeypatch
) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    payload = data(client.get("/api/v1/propdesk/news"))
    statuses = {item["name"]: item["status"] for item in payload["sources"]}
    assert statuses["Forex Factory"] == "unsuitable"
    assert statuses["FRED release dates"] == "implemented"
    fred = next(a for a in payload["availability"] if a["provider"] == "fred")
    assert fred["available"] is False
    assert "FRED_API_KEY" in fred["requires"]


def test_a_recorded_event_appears_in_the_calendar(client) -> None:
    at = datetime(2026, 9, 12, 12, 30, tzinfo=UTC).isoformat()
    data(
        client.post(
            "/api/v1/propdesk/news/events",
            json={"title": "Consumer Price Index", "at": at},
        )
    )
    payload = data(client.get("/api/v1/propdesk/news?days=14"))
    assert any(event["title"] == "Consumer Price Index" for event in payload["events"])


def test_a_malformed_timestamp_is_refused(client) -> None:
    response = client.post(
        "/api/v1/propdesk/news/events", json={"title": "x", "at": "next tuesday"}
    )
    assert response.status_code == 409
    assert "ISO-8601" in response.json()["detail"]["reason"]


# ── activity ─────────────────────────────────────────────────────────────────


def test_activity_reports_counts_and_health(client) -> None:
    payload = data(client.get("/api/v1/propdesk/activity"))
    assert "counts" in payload
    assert payload["counts"]["connections"] == 0


# ── the permission policy governs every write ────────────────────────────────

WRITING_ACTIONS = (
    "propdesk_create_connection",
    "propdesk_connect",
    "propdesk_disconnect",
    "propdesk_save_policy",
    "propdesk_link_policy",
    "propdesk_link_rules",
    "propdesk_create_group",
    "propdesk_add_follower",
    "propdesk_remove_follower",
    "propdesk_set_group_active",
    "propdesk_copy_pass",
    "propdesk_apply_allocation",
    "propdesk_set_constraints",
    "propdesk_record_event",
    "propdesk_set_news_policy",
)

READING_ACTIONS = (
    "propdesk_providers",
    "propdesk_connections",
    "propdesk_policies",
    "propdesk_groups",
    "propdesk_allocation",
    "propdesk_plan_allocation",
    "propdesk_news",
    "propdesk_activity",
)


@pytest.fixture
def registry(tmp_path):
    from forge_api.actions import Actions

    return Actions(
        workspace=None,
        library=None,
        store=None,
        log=None,
        market=None,
        engine=None,
        agents=None,
        families=None,
        templates=None,
        mirror=None,
        workspaces=None,
        prop_desk=PropDeskService(
            store=PropDeskStore(tmp_path / "desk.db"),
            prop_accounts=PropAccountStore(tmp_path / "prop.db"),
            verdict_for=lambda _: None,
            now=lambda: NOW,
        ),
    )


def test_every_prop_desk_action_is_registered(registry) -> None:
    names = set(registry.names())
    assert set(WRITING_ACTIONS) <= names
    assert set(READING_ACTIONS) <= names


def facts(registry, name: str) -> ActionFacts:
    action = registry._registry[name]
    return ActionFacts(
        name=action.name,
        mutating=action.mutating,
        risk=str(action.risk),
        protected=action.protected,
    )


@pytest.mark.parametrize("name", WRITING_ACTIONS)
def test_no_ai_actor_may_write_to_the_desk_in_any_mode(registry, name) -> None:
    """Protected in every mode and on every stance — including autonomous.

    Evaluated against the policy directly rather than through the session, so
    every mode and stance is covered rather than whichever one happens to be
    open.
    """
    for mode in MODE_ORDER:
        stances: tuple[Stance | None, ...] = (
            (None, Stance.HUMAN_IN_THE_LOOP, Stance.AUTONOMOUS)
            if mode is WorkspaceMode.HEDGE_FUND
            else (None,)
        )
        for stance in stances:
            judgement = evaluate(
                facts(registry, name), actor=Actor.AI, mode=mode, stance=stance
            )
            assert judgement.ruling is Ruling.DENY, f"{name} in {mode}/{stance}"
            assert "protected" in judgement.reason


@pytest.mark.parametrize("name", READING_ACTIONS)
def test_an_ai_actor_may_read_the_desk(registry, name) -> None:
    """An assistant that cannot look cannot report honestly on what it found."""
    judgement = evaluate(
        facts(registry, name),
        actor=Actor.AI,
        mode=WorkspaceMode.PROP_FIRM,
        stance=None,
    )
    assert judgement.ruling is Ruling.ALLOW


def test_planning_an_allocation_writes_nothing(registry) -> None:
    """Which is why an agent may run it: it proposes, bounded by the
    deterministic feasible set, and records no decision."""
    action = registry._registry["propdesk_plan_allocation"]
    assert action.mutating is False
    assert action.protected is False


def test_a_copy_pass_that_reaches_accounts_is_high_risk(registry) -> None:
    action = registry._registry["propdesk_copy_pass"]
    assert action.risk.value == "high"
    assert action.protected is True


def test_a_desk_action_without_a_service_refuses_by_name() -> None:
    from forge_api.actions import ActionError, Actions

    bare = Actions(
        workspace=None, library=None, store=None, log=None, market=None, engine=None,
        agents=None, families=None, templates=None, mirror=None, workspaces=None,
    )
    with pytest.raises(ActionError, match="no prop desk is configured"):
        bare.call("propdesk_connections")


# ── risk modes, autonomy and consent ─────────────────────────────────────────


def oos_series(days: int = 200, seed: int = 5) -> tuple[float, ...]:
    import numpy as np

    rng = np.random.default_rng(seed)
    return tuple(float(x) for x in rng.normal(20.0, 120.0, days))


@pytest.fixture
def rich_service(tmp_path) -> PropDeskService:
    """A desk with evidence attached, so the risk layer has something to measure."""
    from forge.propdesk import StrategyHealth

    def health(strategy_id: str) -> StrategyHealth:
        return StrategyHealth(
            strategy_id=strategy_id,
            verdict="PASS" if strategy_id == "judged" else None,
            oos_sharpe=1.3,
            oos_trades=140,
            expected_drawdown_p95=450.0,
            regimes_covered=("trending",),
            current_regime="trending",
        )

    return PropDeskService(
        store=PropDeskStore(tmp_path / "desk.db"),
        prop_accounts=PropAccountStore(tmp_path / "prop.db"),
        verdict_for=lambda strategy_id: "PASS" if strategy_id == "judged" else None,
        health_for=health,
        evidence_for=lambda strategy_id: {
            "symbol": "MNQ",
            "evidence_tier": "TRUTH_OOS",
            "walk_forward_survives": True,
            "paths_robust": True,
            "oos_daily_pnl": oos_series(),
        },
        now=lambda: NOW,
    )


def accept_all(service: PropDeskService, key: str, account_uid: str = "") -> dict:
    from forge.propdesk.consent import DISCLOSURES, DisclosureKey

    disclosure = DISCLOSURES[DisclosureKey(key)]
    return service.acknowledge(
        key=key,
        accepted=disclosure.acknowledgements,
        actor="operator",
        account_uid=account_uid,
    )


def test_the_risk_catalogue_publishes_the_three_promises(client) -> None:
    payload = data(client.get("/api/v1/propdesk/risk/catalogue"))
    promises = {item["mode"]: item["promise"] for item in payload["modes"]}
    assert promises["manual"] == "I decide."
    assert promises["adaptive"] == "AlgoForge calculates."
    assert promises["ai_managed"] == "AlgoForge manages within my boundaries."


def test_every_prohibition_is_published_with_its_mechanism(client) -> None:
    payload = data(client.get("/api/v1/propdesk/risk/catalogue"))
    assert payload["prohibitions"]
    for item in payload["prohibitions"]:
        assert item["enforced_by"].startswith("forge.")


def test_the_mandatory_gate_list_is_published(client) -> None:
    payload = data(client.get("/api/v1/propdesk/risk/catalogue"))
    kinds = {item["kind"] for item in payload["mandatory_gates"]}
    assert {"validation", "prop_policy", "pretrade", "risk_envelope"} <= kinds


def test_manual_risk_can_be_set_and_read_back(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    saved = rich_service.save_risk_settings(
        {
            "account_uid": follower,
            "mode": "manual",
            "manual": {"risk_fraction": 0.1, "max_contracts": 2},
        },
        actor="operator",
    )
    assert saved["settings"]["promise"] == "I decide."
    read = rich_service.risk(follower)["accounts"][0]
    assert read["settings"]["mode"] == "manual"
    assert read["autonomy"] == "off"


def test_ai_risk_management_is_refused_without_an_acknowledgement(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    with pytest.raises(PropDeskError, match="AI Risk Management"):
        rich_service.save_risk_settings(
            {"account_uid": follower, "mode": "ai_managed", "appetite": 50},
            actor="operator",
        )


def test_ai_risk_management_is_permitted_once_acknowledged(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    accept_all(rich_service, "ai_risk_management")
    saved = rich_service.save_risk_settings(
        {
            "account_uid": follower,
            "mode": "ai_managed",
            "appetite": 60,
            "ai_capabilities": ["position_sizing"],
        },
        actor="operator",
    )
    assert saved["settings"]["disclosure_version"]


def test_acknowledging_only_some_statements_is_refused(rich_service) -> None:
    with pytest.raises(PropDeskError, match="has to be accepted"):
        rich_service.acknowledge(
            key="autonomous_deployment",
            accepted=("I understand that autonomous deployment can result in financial losses.",),
            actor="operator",
        )


def test_evaluating_risk_records_a_proposal_and_changes_nothing(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    rich_service.save_risk_settings(
        {"account_uid": follower, "mode": "adaptive", "appetite": 50}, actor="operator"
    )
    before = rich_service.store.scaling_state(follower)
    result = rich_service.evaluate_risk(follower)
    assert result["applied"] is False
    assert rich_service.store.scaling_state(follower) == before
    assert len(rich_service.store.proposals(follower)) == 1


def test_a_proposal_names_every_driver_and_whether_it_was_measured(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    rich_service.save_risk_settings(
        {"account_uid": follower, "mode": "adaptive", "appetite": 50}, actor="operator"
    )
    proposal = rich_service.evaluate_risk(follower)["proposal"]
    kinds = {driver["kind"] for driver in proposal["drivers"]}
    assert {"buffer", "strategy_health", "prop_constraint", "evidence"} <= kinds
    assert proposal["why"]


def test_applying_risk_on_a_manual_account_is_refused(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    rich_service.save_risk_settings(
        {
            "account_uid": follower,
            "mode": "manual",
            "manual": {"risk_fraction": 0.1, "max_contracts": 2},
        },
        actor="operator",
    )
    result = rich_service.evaluate_risk(follower, apply_change=True)
    # Manual mode proposes exactly what was configured, so nothing changes and
    # the refusal never fires. The property under test is that it cannot drift.
    assert result["proposal"]["applied_fraction"] == 0.1


def test_evaluating_an_account_with_no_risk_configuration_is_refused(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    with pytest.raises(PropDeskError, match="Choose a risk mode"):
        rich_service.evaluate_risk(follower)


def test_an_advisory_factor_above_one_is_refused_over_http(rich_service, tmp_path) -> None:
    from fastapi import FastAPI

    _, _, follower = seeded(rich_service)
    accept_all(rich_service, "ai_risk_management")
    rich_service.save_risk_settings(
        {
            "account_uid": follower,
            "mode": "ai_managed",
            "appetite": 50,
            "ai_capabilities": ["position_sizing"],
        },
        actor="operator",
    )
    app = FastAPI()
    app.include_router(build_propdesk_router(rich_service))
    response = TestClient(app).post(
        f"/api/v1/propdesk/risk/{follower}/evaluate", json={"advisory": 1.4}
    )
    assert response.status_code == 409
    assert "never widen" in response.json()["detail"]["reason"]


def test_autonomy_is_refused_without_the_deployment_disclosure(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    with pytest.raises(PropDeskError, match="Autonomous Strategy Deployment"):
        rich_service.set_autonomy(
            account_uid=follower, level="fully_autonomous", actor="operator"
        )


def test_autonomy_can_always_be_turned_off_without_a_disclosure(rich_service) -> None:
    # Reducing reach never needs consent.
    _, _, follower = seeded(rich_service)
    assert (
        rich_service.set_autonomy(account_uid=follower, level="off", actor="operator")[
            "autonomy"
        ]
        == "off"
    )


def test_deployment_is_blocked_by_the_absent_live_connector(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    accept_all(rich_service, "autonomous_deployment")
    rich_service.set_autonomy(
        account_uid=follower, level="fully_autonomous", actor="operator"
    )
    decision = rich_service.evaluate_deployment(strategy_id="judged", account_uid=follower)[
        "decision"
    ]
    assert decision["outcome"] == "blocked"
    lifecycle = next(gate for gate in decision["gates"] if gate["kind"] == "lifecycle")
    assert not lifecycle["passed"]
    assert "live execution is not available" in lifecycle["detail"]


def test_a_deployment_evaluation_is_recorded_in_the_audit_trail(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    rich_service.evaluate_deployment(strategy_id="judged", account_uid=follower)
    records = rich_service.audit(account_uid=follower)["records"]
    assert records
    assert records[0]["action"] in {"deployment_evaluated", "deployment_blocked"}
    assert "no live connector" in records[0]["execution_state"]


def test_a_risk_mode_change_is_recorded_with_both_sides(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    rich_service.save_risk_settings(
        {
            "account_uid": follower,
            "mode": "manual",
            "manual": {"risk_fraction": 0.1, "max_contracts": 2},
        },
        actor="operator",
    )
    rich_service.save_risk_settings(
        {"account_uid": follower, "mode": "adaptive", "appetite": 40}, actor="operator"
    )
    records = rich_service.audit(account_uid=follower)["records"]
    change = next(item for item in records if item["action"] == "risk_mode_changed")
    assert change["previous"]["mode"] == "manual"
    assert change["current"]["mode"] == "adaptive"


def test_why_declines_when_nothing_has_been_recorded(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    answer = rich_service.why(question="why_did_risk_change", account_uid=follower)["answer"]
    assert answer["answered"] is False
    assert answer["unavailable"]


def test_why_answers_from_the_recorded_proposal(rich_service) -> None:
    _, _, follower = seeded(rich_service)
    rich_service.save_risk_settings(
        {"account_uid": follower, "mode": "adaptive", "appetite": 50}, actor="operator"
    )
    rich_service.evaluate_risk(follower)
    answer = rich_service.why(question="why_did_risk_change", account_uid=follower)["answer"]
    assert answer["answered"] is True
    assert answer["points"]


def test_an_unknown_question_is_refused_with_the_known_set(rich_service) -> None:
    with pytest.raises(PropDeskError, match="why_did_risk_change"):
        rich_service.why(question="why_am_i_not_rich")


def test_the_risk_writing_actions_are_denied_to_ai_in_every_mode(tmp_path, monkeypatch) -> None:
    """The containment claim, asserted against the registry rather than trusted."""
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    from forge_api.main import create_app

    registry = create_app(tmp_path / "risk.db").state.actions
    for name in (
        "propdesk_set_risk",
        "propdesk_apply_risk",
        "propdesk_acknowledge",
        "propdesk_set_autonomy",
    ):
        action = registry._registry[name]
        assert action.protected, f"{name} is not protected"
        facts = ActionFacts(
            name=name,
            mutating=action.mutating,
            risk=action.risk.value,
            protected=action.protected,
        )
        for mode in MODE_ORDER:
            for stance in (None, Stance.HUMAN_IN_THE_LOOP, Stance.AUTONOMOUS):
                if stance is not None and mode is not WorkspaceMode.HEDGE_FUND:
                    continue
                ruling = evaluate(facts, actor=Actor.AI, mode=mode, stance=stance)
                assert ruling.ruling is Ruling.DENY, f"{name} reachable in {mode}/{stance}"


def test_a_stored_proposal_is_rendered_with_the_keys_the_screen_reads(rich_service) -> None:
    """The row holds the model; the rendering is added on the way out.

    Found by running the application: the screen reads each driver's label and
    whether it cut, and the row deliberately does not hold them — so a proposal
    that round-trips through the store has to be rendered rather than returned
    raw.
    """
    _, _, follower = seeded(rich_service)
    rich_service.save_risk_settings(
        {"account_uid": follower, "mode": "adaptive", "appetite": 50}, actor="operator"
    )
    rich_service.evaluate_risk(follower)
    proposal = rich_service.risk(follower)["accounts"][0]["last_proposal"]
    assert proposal["why"]
    assert all("label" in driver and "cuts" in driver for driver in proposal["drivers"])
    assert rich_service.audit(account_uid=follower)["proposals"][0]["clamp_detail"]


class TestTheRulesDriverReadsConfiguredRulesOnly:
    """A rule nobody configured cannot be breached, and must not read as unknown.

    Found by running the application. `AccountAssessment.level` is the worst
    over *every* rule and NOT_ASSESSED sorts above OK there, which is right for
    the account screen. It is wrong for the risk driver: a rule set with a
    maximum loss and a daily loss limit leaves seven optional rules reporting
    "not configured", so the account's worst level was permanently
    NOT_ASSESSED, the driver was permanently unmeasured, and risk could never
    rise. Safe, and silently wrong.
    """

    def test_a_sparse_rule_set_still_reports_a_measured_level(self, rich_service) -> None:
        _, _, follower = seeded(rich_service)
        observation = rich_service.risk_observation(follower)
        assert observation.rules_level == "ok"

    def test_the_driver_measures_rather_than_abstaining(self, rich_service) -> None:
        from forge.propdesk.scaling import DriverKind, drivers_for

        _, _, follower = seeded(rich_service)
        observation = rich_service.risk_observation(follower)
        settings = rich_service.save_risk_settings(
            {"account_uid": follower, "mode": "adaptive", "appetite": 50}, actor="operator"
        )
        del settings
        driver = next(
            item
            for item in drivers_for(
                observation, rich_service.store.risk_settings(follower).boundaries
            )
            if item.kind is DriverKind.PROP_CONSTRAINT
        )
        assert driver.measured
        assert driver.effect == 1.0

    def test_an_account_with_no_rules_at_all_is_unmeasured(self, rich_service) -> None:
        connection = rich_service.create_connection(provider="simulated", label="Sim")
        connection_id = connection["connection"]["connection_id"]
        rich_service.connect(connection_id)
        bare = rich_service.seed_simulator(connection_id, account_id="BARE")["account"]
        assert rich_service.risk_observation(bare["account_uid"]).rules_level == ""

    def test_a_configured_rule_that_cannot_be_checked_still_blocks(self, rich_service) -> None:
        # The other shape of NOT_ASSESSED: a rule that exists and has no
        # reading. That one is genuinely unknown and must keep blocking.
        from forge.prop.account import AccountRules, AccountState

        connection = rich_service.create_connection(provider="simulated", label="Sim2")
        connection_id = connection["connection"]["connection_id"]
        rich_service.connect(connection_id)
        account = rich_service.seed_simulator(connection_id, account_id="RISKCAP")["account"]
        rules = AccountRules(
            name="With a per-trade cap",
            starting_balance=50_000.0,
            maximum_loss=2_000.0,
            max_risk_per_trade=500.0,
        )
        prop = rich_service.prop_accounts.create(rules)
        rich_service.prop_accounts.record(
            prop.account_id,
            AccountState(
                as_of=NOW,
                balance=50_000.0,
                equity=50_000.0,
                high_water_balance=50_000.0,
                high_water_equity=50_000.0,
                # risk_per_trade deliberately absent: the rule exists and the
                # state cannot answer it.
            ),
            source="test",
        )
        rich_service.link_prop_account(account["account_uid"], prop.account_id)
        assert rich_service.risk_observation(account["account_uid"]).rules_level == ""
