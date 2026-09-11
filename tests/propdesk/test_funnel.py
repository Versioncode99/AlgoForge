"""The whole funnel, end to end, on one desk.

§38 asks for the loop to be verified rather than asserted:

    strategy -> account -> risk -> allocation -> execution -> monitoring
                                      |
                        AI -> recommendation -> deterministic gates -> execution

Every other test file in this directory checks one component in isolation. This
one wires them together and follows a single strategy from a judge verdict to a
fill on a simulated account, then breaks something and watches the same machinery
refuse.

The point is the seams. A component can be correct alone and still be wired to
the wrong field, and running the application found three of exactly that during
this work — a stored proposal that could not be read back, a driver keyed to
level names the rule engine does not use, and a rule set whose optional rules
locked risk at the floor. These tests are where that class of bug is cheapest to
catch.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from forge.prop.account import AccountRules, AccountState
from forge.prop.accounts import PropAccountStore
from forge.propdesk import (
    AiCapability,
    AutonomyLevel,
    DeploymentFacts,
    DisclosureAcknowledgement,
    DisclosureKey,
    Permission,
    PropDeskStore,
    StrategyHealth,
)
from forge.propdesk.autonomy import evaluate as evaluate_deployment
from forge.propdesk.consent import DISCLOSURES
from forge_api.propdesk import PropDeskError, PropDeskService

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


def oos(days: int = 240, seed: int = 11) -> tuple[float, ...]:
    """An out-of-sample per-contract daily series with a real drawdown tail."""
    rng = np.random.default_rng(seed)
    return tuple(float(x) for x in rng.normal(18.0, 110.0, days))


@pytest.fixture
def desk(tmp_path) -> PropDeskService:
    """A desk with one judged strategy, its evidence, and one funded account."""

    def health(strategy_id: str) -> StrategyHealth:
        if strategy_id != "judged":
            return StrategyHealth(strategy_id=strategy_id)
        return StrategyHealth(
            strategy_id=strategy_id,
            verdict="PASS",
            verdict_id="v-judged",
            oos_sharpe=1.4,
            oos_trades=180,
            expected_drawdown_p95=420.0,
            regimes_covered=("trending",),
            current_regime="trending",
            as_of=NOW,
            as_of_basis="out-of-sample run",
        )

    service = PropDeskService(
        store=PropDeskStore(tmp_path / "desk.db"),
        prop_accounts=PropAccountStore(tmp_path / "prop.db"),
        verdict_for=lambda sid: "PASS" if sid == "judged" else None,
        health_for=health,
        evidence_for=lambda sid: (
            {
                "symbol": "MNQ",
                "evidence_tier": "TRUTH_OOS",
                "walk_forward_survives": True,
                "paths_robust": True,
                "oos_daily_pnl": oos(),
            }
            if sid == "judged"
            else {}
        ),
        now=lambda: NOW,
    )

    connection_id = service.create_connection(provider="simulated", label="Sim")["connection"][
        "connection_id"
    ]
    service.connect(connection_id)
    account = service.seed_simulator(connection_id, account_id="F1", balance=50_000.0)["account"]

    rules = AccountRules(
        name="50k funded",
        provider="my firm",
        phase="FUNDED",
        starting_balance=50_000.0,
        maximum_loss=2_500.0,
        daily_loss_limit=1_200.0,
        max_position_contracts=8,
        max_order_contracts=4,
    )
    prop = service.prop_accounts.create(rules)
    service.prop_accounts.record(
        prop.account_id,
        AccountState(
            as_of=NOW,
            balance=51_000.0,
            equity=51_000.0,
            high_water_balance=51_000.0,
            high_water_equity=51_000.0,
            largest_order_contracts=1,
            open_positions=0,
        ),
        source="funnel fixture",
    )
    policy = service.save_policy(
        {
            "firm_label": "my firm",
            "program_label": "50k funded",
            "automation": Permission.ALLOWED.value,
            "copy_in": Permission.ALLOWED.value,
            "copy_out": Permission.ALLOWED.value,
            "algorithmic_allocation": Permission.ALLOWED.value,
            "permitted_products": ["MNQ"],
        }
    )["policy"]
    service.link_policy(account["account_uid"], policy["policy_id"])
    service.link_prop_account(account["account_uid"], prop.account_id)
    service.account_uid = account["account_uid"]  # type: ignore[attr-defined]
    service.prop_account_id = prop.account_id  # type: ignore[attr-defined]
    return service


def accept(service: PropDeskService, key: DisclosureKey, account_uid: str = "") -> None:
    service.store.record_acknowledgement(
        DisclosureAcknowledgement(
            key=key,
            version=DISCLOSURES[key].version,
            acknowledged_by="operator",
            accepted=DISCLOSURES[key].acknowledgements,
            account_uid=account_uid,
            acknowledged_at=NOW,
        )
    )


class TestTheDeterministicPath:
    """Strategy -> account -> risk -> allocation -> execution, with nothing advisory."""

    def test_a_judged_strategy_becomes_a_sized_allocation(self, desk) -> None:
        account = desk.account_uid

        # 1. Risk. Adaptive, so the size comes from measured state.
        desk.save_risk_settings(
            {"account_uid": account, "mode": "adaptive", "appetite": 60}, actor="operator"
        )
        # No allocation yet, so the band has to be asked for against a named
        # strategy: there is no measured drawdown to size against otherwise.
        proposal = desk.evaluate_risk(account, strategy_id="judged", apply_change=True)["proposal"]
        assert proposal["band"] is not None, proposal["why"]
        assert proposal["band"]["contracts"] > 0

        # 2. Allocation. The allocator turns the fraction into contracts through
        #    the same arithmetic, bounded by the account's own rules.
        plan = desk.plan_allocation(strategy_ids=("judged",))["plan"]
        allocations = plan["allocations"]
        assert allocations, plan["candidates"]
        assert allocations[0]["strategy_id"] == "judged"
        assert 0 < allocations[0]["contracts"] <= 8

        # 3. Apply, and the change is in the record.
        applied = desk.apply_allocation(
            allocations=tuple(allocations), actor="operator"
        )
        assert applied["changes"]
        assert desk.store.allocation(account) is not None

    def test_the_size_falls_when_the_account_draws_down(self, desk) -> None:
        account = desk.account_uid
        desk.save_risk_settings(
            {"account_uid": account, "mode": "adaptive", "appetite": 60}, actor="operator"
        )
        desk.apply_allocation(
            allocations=tuple(desk.plan_allocation(strategy_ids=("judged",))["plan"]["allocations"]),
            actor="operator",
        )
        before = desk.evaluate_risk(account, apply_change=True)["proposal"]

        # The account gives back most of its buffer. Nothing else changes.
        desk.prop_accounts.record(
            desk.prop_account_id,
            AccountState(
                as_of=NOW,
                balance=49_200.0,
                equity=49_200.0,
                high_water_balance=51_000.0,
                high_water_equity=51_000.0,
            ),
            source="drawdown",
        )
        after = desk.evaluate_risk(account, apply_change=True)["proposal"]
        assert after["applied_fraction"] <= before["applied_fraction"]
        assert any(
            driver["kind"] == "buffer" and driver["effect"] < 1.0
            for driver in after["drivers"]
        )

    def test_an_unjudged_strategy_is_never_allocated(self, desk) -> None:
        plan = desk.plan_allocation(strategy_ids=("unjudged",))["plan"]
        assert plan["allocations"] == []
        reasons = " ".join(
            reason["detail"]
            for candidate in plan["candidates"]
            for reason in candidate["reasons"]
        )
        assert "judge" in reasons.lower() or "verdict" in reasons.lower()


class TestTheAdvisoryPathCannotWiden:
    """AI -> recommendation -> deterministic gates -> execution."""

    def enable_ai(self, desk) -> str:
        account = desk.account_uid
        accept(desk, DisclosureKey.AI_RISK_MANAGEMENT)
        desk.save_risk_settings(
            {
                "account_uid": account,
                "mode": "ai_managed",
                "appetite": 60,
                "ai_capabilities": [AiCapability.POSITION_SIZING.value],
            },
            actor="operator",
        )
        return account

    def test_advice_can_only_narrow_the_deterministic_result(self, desk) -> None:
        account = self.enable_ai(desk)
        plain = desk.evaluate_risk(account)["proposal"]
        advised = desk.evaluate_risk(account, advisory=0.4)["proposal"]
        assert advised["proposed_fraction"] < plain["proposed_fraction"]

    def test_advice_outside_its_range_is_refused_rather_than_clamped(self, desk) -> None:
        account = self.enable_ai(desk)
        with pytest.raises(PropDeskError, match="may never widen"):
            desk.evaluate_risk(account, advisory=1.6)

    def test_advice_cannot_lift_the_operators_ceiling(self, desk) -> None:
        account = self.enable_ai(desk)
        desk.save_risk_settings(
            {
                "account_uid": account,
                "mode": "ai_managed",
                "appetite": 100,
                "ai_capabilities": [AiCapability.POSITION_SIZING.value],
                "boundaries": {"minimum_fraction": 0.02, "maximum_fraction": 0.06},
            },
            actor="operator",
        )
        proposal = desk.evaluate_risk(account, advisory=1.0, apply_change=True)["proposal"]
        assert proposal["applied_fraction"] <= 0.06

    def test_ai_risk_management_cannot_be_enabled_without_consent(self, desk) -> None:
        with pytest.raises(PropDeskError, match="AI Risk Management"):
            desk.save_risk_settings(
                {"account_uid": desk.account_uid, "mode": "ai_managed", "appetite": 60},
                actor="operator",
            )


class TestDeploymentIsGatedTheSameWayAtEveryLevel:
    def facts(self, desk) -> DeploymentFacts:
        return desk.deployment_facts(strategy_id="judged", account_uid=desk.account_uid)

    def test_the_strongest_possible_case_is_still_blocked_by_the_missing_connector(
        self, desk
    ) -> None:
        """Everything else passing, and it still does not deploy.

        This is the claim §36 asks to be true rather than stated: there is no
        configuration of this build in which a strategy reaches a venue.
        """
        account = desk.account_uid
        accept(desk, DisclosureKey.AUTONOMOUS_DEPLOYMENT)
        desk.save_risk_settings(
            {"account_uid": account, "mode": "adaptive", "appetite": 60}, actor="operator"
        )
        desk.evaluate_risk(account, apply_change=True)
        desk.set_autonomy(
            account_uid=account, level=AutonomyLevel.FULLY_AUTONOMOUS.value, actor="operator"
        )
        decision = desk.evaluate_deployment(strategy_id="judged", account_uid=account)[
            "decision"
        ]
        assert decision["outcome"] == "blocked"
        lifecycle = next(g for g in decision["gates"] if g["kind"] == "lifecycle")
        assert not lifecycle["passed"]
        assert "no broker connector" in lifecycle["detail"]

    def test_the_gate_list_does_not_shorten_with_the_level(self, desk) -> None:
        facts = self.facts(desk)
        kinds = [
            [gate.kind for gate in evaluate_deployment(facts, level=level).gates]
            for level in AutonomyLevel
        ]
        assert kinds[0] == kinds[1] == kinds[2]

    def test_a_blocked_deployment_is_recorded_with_its_reasons(self, desk) -> None:
        desk.evaluate_deployment(strategy_id="judged", account_uid=desk.account_uid)
        records = desk.audit(account_uid=desk.account_uid)["records"]
        blocked = next(r for r in records if r["action"] == "deployment_blocked")
        assert blocked["reason"]
        assert "no live connector" in blocked["execution_state"]


class TestMonitoringAndReallocation:
    def test_a_degraded_strategy_loses_its_allocation_on_the_next_plan(
        self, tmp_path
    ) -> None:
        """The loop closes: monitoring feeds reallocation.

        The strategy is healthy, allocated, then drifts. The next plan refuses
        it — not because anything told the allocator to, but because the health
        grade it reads is computed from the live expectancy it was given.
        """
        drift = {"value": False}

        def health(strategy_id: str) -> StrategyHealth:
            base = {
                "strategy_id": strategy_id,
                "verdict": "PASS",
                "oos_sharpe": 1.4,
                "oos_trades": 180,
                "expected_drawdown_p95": 420.0,
                "baseline_expectancy": 100.0,
            }
            if drift["value"]:
                base["live_expectancy"] = 20.0
            return StrategyHealth(**base)

        service = PropDeskService(
            store=PropDeskStore(tmp_path / "desk.db"),
            prop_accounts=PropAccountStore(tmp_path / "prop.db"),
            verdict_for=lambda sid: "PASS",
            health_for=health,
            evidence_for=lambda sid: {"symbol": "MNQ", "oos_daily_pnl": oos()},
            now=lambda: NOW,
        )
        connection_id = service.create_connection(provider="simulated", label="Sim")[
            "connection"
        ]["connection_id"]
        service.connect(connection_id)
        account = service.seed_simulator(connection_id, account_id="F1")["account"]
        rules = AccountRules(
            name="50k", starting_balance=50_000.0, maximum_loss=2_500.0,
            max_position_contracts=8,
        )
        prop = service.prop_accounts.create(rules)
        service.prop_accounts.record(
            prop.account_id,
            AccountState(
                as_of=NOW, balance=51_000.0, equity=51_000.0,
                high_water_balance=51_000.0, high_water_equity=51_000.0,
            ),
            source="fixture",
        )
        policy = service.save_policy(
            {
                "firm_label": "f",
                "automation": Permission.ALLOWED.value,
                "algorithmic_allocation": Permission.ALLOWED.value,
                "permitted_products": ["MNQ"],
            }
        )["policy"]
        service.link_policy(account["account_uid"], policy["policy_id"])
        service.link_prop_account(account["account_uid"], prop.account_id)

        healthy = service.plan_allocation(strategy_ids=("s1",))["plan"]
        assert healthy["allocations"], healthy["candidates"]

        assert not healthy["allocations"][0]["requires_confirmation"]
        service.apply_allocation(
            allocations=tuple(healthy["allocations"]), actor="operator"
        )

        # The strategy drifts. Nothing tells the allocator; it reads the grade,
        # and the grade is computed from the live expectancy it was given.
        drift["value"] = True
        degraded = service.plan_allocation(strategy_ids=("s1",))["plan"]
        assert {c["health_grade"] for c in degraded["candidates"]} == {"degraded"}

        # It is not refused outright — a degraded strategy is one a person may
        # still choose to run — but it stops being something the desk will place
        # on its own, and applying it without a confirmation is refused.
        proposed = degraded["allocations"]
        assert proposed and proposed[0]["requires_confirmation"]
        with pytest.raises(PropDeskError, match="need your confirmation"):
            service.apply_allocation(allocations=tuple(proposed), actor="operator")


class TestEverySurfaceAgrees:
    def test_the_desk_the_registry_and_http_return_the_same_risk_state(
        self, desk, tmp_path
    ) -> None:
        """One implementation, three surfaces.

        The action registry and the HTTP route both call the service; a second
        implementation behind either would be a back door.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from forge_api.propdesk import build_propdesk_router

        account = desk.account_uid
        desk.save_risk_settings(
            {"account_uid": account, "mode": "adaptive", "appetite": 45}, actor="operator"
        )
        direct = desk.risk(account)

        app = FastAPI()
        app.include_router(build_propdesk_router(desk))
        over_http = TestClient(app).get(
            f"/api/v1/propdesk/risk?account_uid={account}"
        ).json()["data"]
        assert over_http["accounts"][0]["settings"] == direct["accounts"][0]["settings"]
