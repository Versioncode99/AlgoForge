"""Persistence: what round-trips, what is append-only, and what is never stored."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge.propdesk import (
    Allocation,
    AllocationChange,
    AllocationConstraints,
    AuthMethod,
    BrokerConnection,
    ConnectionState,
    CopyGroup,
    CredentialRecord,
    Environment,
    Follower,
    Impact,
    Leader,
    ManualCalendar,
    NewsPolicy,
    Permission,
    PropDeskStore,
    PropProgramPolicy,
    Provider,
    SizingPolicy,
)

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


@pytest.fixture
def store(tmp_path) -> PropDeskStore:
    return PropDeskStore(tmp_path / "desk.db")


def connection(connection_id: str = "c1") -> BrokerConnection:
    return BrokerConnection(
        connection_id=connection_id,
        provider=Provider.SIMULATED,
        environment=Environment.SIMULATION,
        label="Simulator",
        credential_ref="cred-1",
        state=ConnectionState.LIVE,
        created_at=NOW,
        updated_at=NOW,
    )


def group(group_id: str = "g1") -> CopyGroup:
    return CopyGroup(
        group_id=group_id,
        name="Group",
        leader=Leader(account_uid="leader"),
        followers=(Follower(account_uid="f1", sizing=SizingPolicy(value=1.0)),),
        active=True,
        owner_attested_by="tester",
        created_at=NOW,
        updated_at=NOW,
    )


# ── configuration round-trips ────────────────────────────────────────────────


def test_a_connection_round_trips(store) -> None:
    store.save_connection(connection())
    loaded = store.connection("c1")
    assert loaded is not None
    assert loaded.provider is Provider.SIMULATED
    assert loaded.state is ConnectionState.LIVE


def test_deleting_a_connection_removes_its_accounts(store, tmp_path) -> None:
    from forge.propdesk import Account, AccountKey

    store.save_connection(connection())
    store.save_account(
        Account(
            key=AccountKey(
                provider=Provider.SIMULATED,
                environment=Environment.SIMULATION,
                credential_ref="cred-1",
                account_id="A",
            ),
            connection_id="c1",
        )
    )
    assert len(store.accounts("c1")) == 1
    assert store.delete_connection("c1") is True
    assert store.accounts("c1") == ()


def test_a_policy_round_trips_with_its_permissions(store) -> None:
    policy = PropProgramPolicy(
        firm_label="mine", copy_in=Permission.ALLOWED, permitted_products=("MNQ",)
    )
    store.save_policy(policy)
    loaded = store.policy(policy.policy_id)
    assert loaded is not None
    assert loaded.copy_in is Permission.ALLOWED
    assert loaded.automation is Permission.UNKNOWN
    assert loaded.version == policy.version


def test_a_copy_group_round_trips(store) -> None:
    store.save_group(group())
    loaded = store.group("g1")
    assert loaded is not None
    assert loaded.leader.account_uid == "leader"
    assert len(loaded.followers) == 1
    assert loaded.policy.block_reversals is True


def test_a_group_can_be_deleted(store) -> None:
    store.save_group(group())
    assert store.delete_group("g1") is True
    assert store.group("g1") is None


def test_a_credential_record_round_trips_without_its_secret(store) -> None:
    from forge.propdesk import SecretSource

    record = CredentialRecord(
        credential_ref="cred-1",
        provider=Provider.RITHMIC,
        auth_method=AuthMethod.USERNAME_PASSWORD,
        label="Rithmic",
        public_fields={"username": "trader1"},
        sources={"password": SecretSource(kind="env", name="RITHMIC_PASSWORD")},
        created_at=NOW,
    )
    store.save_credential(record)
    loaded = store.credential("cred-1")
    assert loaded is not None
    assert loaded.public_fields == {"username": "trader1"}
    assert loaded.sources["password"].name == "RITHMIC_PASSWORD"


# ── allocations and their history ────────────────────────────────────────────


def test_an_allocation_is_replaced_while_its_history_accumulates(store) -> None:
    """The configuration is current state; the history is the record."""
    for contracts in (2, 4, 1):
        allocation = Allocation(
            account_uid="a1", strategy_id="s1", contracts=contracts, decided_at=NOW
        )
        store.save_allocation(allocation)
        store.record_change(
            AllocationChange(
                account_uid="a1",
                at=NOW,
                previous_strategy_id="s1",
                strategy_id="s1",
                contracts=contracts,
            )
        )
    assert store.allocation("a1").contracts == 1
    assert len(store.history("a1")) == 3


def test_history_has_no_update_or_delete(store) -> None:
    """A history that can be edited is not a history."""
    public = {name for name in dir(PropDeskStore) if not name.startswith("_")}
    assert "record_change" in public
    for forbidden in ("update_change", "delete_change", "clear_history", "edit_history"):
        assert forbidden not in public


def test_changes_today_counts_from_the_record_rather_than_from_memory(store) -> None:
    """A restart must not reset somebody's daily change budget."""
    for _ in range(3):
        store.record_change(
            AllocationChange(account_uid="a1", at=NOW, strategy_id="s1", contracts=1)
        )
    store.record_change(
        AllocationChange(
            account_uid="a1", at=NOW - timedelta(days=2), strategy_id="s1", contracts=1
        )
    )
    reopened = PropDeskStore(store.path)
    assert reopened.changes_today("a1", today=NOW) == 3
    assert reopened.changes_today("a2", today=NOW) == 0


def test_clearing_an_allocation_leaves_its_history(store) -> None:
    store.save_allocation(
        Allocation(account_uid="a1", strategy_id="s1", contracts=2, decided_at=NOW)
    )
    store.record_change(
        AllocationChange(account_uid="a1", at=NOW, strategy_id="s1", contracts=2)
    )
    assert store.clear_allocation("a1") is True
    assert store.allocation("a1") is None
    assert len(store.history("a1")) == 1


# ── the desk's own record ────────────────────────────────────────────────────


def test_decisions_can_be_filtered_by_account_and_outcome(
    store, desk, adapter, context_factory
) -> None:
    from tests.propdesk.test_desk import intent

    context = context_factory()
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1"), key="ok"), intent("nobody", key="no")],
        context,
    )
    for decision in decisions:
        store.record_decision(decision)

    assert len(store.decisions()) == 2
    assert len(store.decisions(cleared=True)) == 1
    assert len(store.decisions(cleared=False)) == 1
    assert len(store.decisions(account_uid="nobody")) == 1


def test_recording_the_same_decision_twice_stores_one_row(
    store, desk, adapter, context_factory
) -> None:
    from tests.propdesk.test_desk import intent

    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context_factory())
    store.record_decision(decisions[0])
    store.record_decision(decisions[0])
    assert len(store.decisions()) == 1


def test_a_reconciliation_report_is_appended(store) -> None:
    from forge.propdesk import (
        LocalView,
        ProviderSnapshot,
        ReconciliationEngine,
        Trigger,
    )

    engine = ReconciliationEngine(now=lambda: NOW)
    report = engine.reconcile(
        local=LocalView(account_uid="a1"),
        snapshot=ProviderSnapshot(account_uid="a1", taken_at=NOW),
        trigger=Trigger.STARTUP,
    )
    store.record_reconciliation(report)
    store.record_reconciliation(report)
    assert len(store.reconciliations("a1")) == 2


# ── the calendar ─────────────────────────────────────────────────────────────


def test_calendar_events_round_trip_within_a_window(store) -> None:
    calendar = ManualCalendar()
    inside = calendar.record(title="Consumer Price Index", at=NOW)
    outside = calendar.record(title="FOMC Statement", at=NOW + timedelta(days=30))
    for event in (inside, outside):
        store.save_event(event)

    found = store.events(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert [e.title for e in found] == ["Consumer Price Index"]
    assert found[0].impact is Impact.HIGH
    assert store.delete_event(inside.event_id) is True


# ── settings ─────────────────────────────────────────────────────────────────


def test_settings_default_rather_than_failing_when_never_saved(store) -> None:
    assert store.news_policy().enabled is False
    assert store.constraints().risk_fraction == 0.25
    assert store.owner_attestation() == ""


def test_settings_round_trip(store) -> None:
    store.save_news_policy(NewsPolicy(enabled=True, minutes_before=15))
    store.save_constraints(AllocationConstraints(risk_fraction=0.1, max_contracts=3))
    store.save_owner_attestation("the operator")

    reopened = PropDeskStore(store.path)
    assert reopened.news_policy().minutes_before == 15
    assert reopened.constraints().max_contracts == 3
    assert reopened.owner_attestation() == "the operator"


def test_counts_report_every_table(store) -> None:
    store.save_connection(connection())
    counts = store.counts()
    assert counts["connections"] == 1
    assert counts["copy_groups"] == 0
    assert set(counts) >= {"accounts", "policies", "allocation_history", "desk_decisions"}


def test_opening_an_existing_database_does_not_lose_anything(store) -> None:
    store.save_group(group())
    reopened = PropDeskStore(store.path)
    assert reopened.group("g1") is not None


class TestRiskAndAutonomyRecords:
    """The phase-2 tables: configuration that is edited, and a record that is not."""

    def store(self, tmp_path):
        from forge.propdesk.store import PropDeskStore

        return PropDeskStore(tmp_path / "desk.db")

    def settings(self):
        from forge.propdesk.risk import ManualRisk, RiskMode, RiskSettings

        return RiskSettings(
            account_uid="acct-1",
            mode=RiskMode.MANUAL,
            manual=ManualRisk(risk_fraction=0.1, max_contracts=2),
        )

    def test_risk_settings_round_trip(self, tmp_path) -> None:
        store = self.store(tmp_path)
        saved = store.save_risk_settings(self.settings())
        assert store.risk_settings("acct-1") == saved
        assert store.all_risk_settings() == (saved,)

    def test_an_account_with_no_settings_reads_as_none_rather_than_a_default(
        self, tmp_path
    ) -> None:
        # A default here would be a risk configuration nobody chose.
        assert self.store(tmp_path).risk_settings("nobody") is None

    def test_autonomy_defaults_to_off_for_an_unconfigured_account(self, tmp_path) -> None:
        from forge.propdesk.autonomy import AutonomyLevel

        assert self.store(tmp_path).autonomy("nobody") is AutonomyLevel.OFF

    def test_autonomy_round_trips(self, tmp_path) -> None:
        from forge.propdesk.autonomy import AutonomyLevel

        store = self.store(tmp_path)
        store.save_autonomy("acct-1", AutonomyLevel.APPROVAL_REQUIRED)
        assert store.autonomy("acct-1") is AutonomyLevel.APPROVAL_REQUIRED
        assert store.all_autonomy() == {"acct-1": AutonomyLevel.APPROVAL_REQUIRED}

    def test_proposals_that_changed_nothing_are_recorded_too(self, tmp_path) -> None:
        # "Nothing moved, and here is what was measured" is the answer to half
        # the questions this screen is asked.
        from datetime import UTC, datetime

        from forge.propdesk.risk import RiskMode
        from forge.propdesk.scaling import Direction, RiskProposal

        store = self.store(tmp_path)
        held = RiskProposal(
            account_uid="acct-1",
            mode=RiskMode.ADAPTIVE,
            at=datetime(2026, 9, 11, tzinfo=UTC),
            current_fraction=0.1,
            proposed_fraction=0.1,
            applied_fraction=0.1,
            direction=Direction.HOLD,
        )
        store.record_proposal(held)
        assert len(store.proposals("acct-1")) == 1
        assert store.proposals("acct-1", changed_only=True) == ()

    def test_the_risk_proposal_table_has_no_update_or_delete(self, tmp_path) -> None:
        store = self.store(tmp_path)
        assert not hasattr(store, "update_proposal")
        assert not hasattr(store, "delete_proposal")

    def test_an_audit_record_refuses_to_carry_anything_shaped_like_a_secret(
        self, tmp_path
    ) -> None:
        from forge.propdesk.audit import AuditAction, ConsequentialRecord
        from forge.propdesk.credentials import REDACTED

        store = self.store(tmp_path)
        store.record_audit(
            ConsequentialRecord(
                action=AuditAction.RISK_MODE_CHANGED,
                account_uid="acct-1",
                previous={"mode": "manual", "api_key": "sk-live-should-never-appear"},
                current={"mode": "adaptive", "nested": {"password": "hunter2"}},
            )
        )
        raw = (store.path).read_bytes()
        assert b"sk-live-should-never-appear" not in raw
        assert b"hunter2" not in raw
        recorded = store.audit("acct-1")[0]
        assert recorded["previous"]["api_key"] == REDACTED
        assert recorded["current"]["nested"]["password"] == REDACTED

    def test_acknowledgements_are_appended_and_read_back_by_key(self, tmp_path) -> None:
        from forge.propdesk.consent import DISCLOSURES, Acknowledgement, DisclosureKey

        store = self.store(tmp_path)
        disclosure = DISCLOSURES[DisclosureKey.COPY_TRADING]
        store.record_acknowledgement(
            Acknowledgement(
                key=DisclosureKey.COPY_TRADING,
                version=disclosure.version,
                acknowledged_by="operator",
                accepted=disclosure.acknowledgements,
            )
        )
        assert len(store.acknowledgements(DisclosureKey.COPY_TRADING)) == 1
        assert store.acknowledgements(DisclosureKey.AI_RISK_MANAGEMENT) == ()

    def test_deployment_decisions_are_appended_with_their_outcome(self, tmp_path) -> None:
        from forge.propdesk.autonomy import AutonomyLevel, DeploymentFacts, evaluate

        store = self.store(tmp_path)
        decision = evaluate(
            DeploymentFacts(strategy_id="s1", account_uid="acct-1"), level=AutonomyLevel.OFF
        )
        store.record_deployment(decision)
        recorded = store.deployments("acct-1")
        assert len(recorded) == 1
        assert recorded[0]["outcome"] == "blocked"

    def test_counts_include_every_phase_two_table(self, tmp_path) -> None:
        counts = self.store(tmp_path).counts()
        for table in (
            "risk_settings",
            "risk_proposals",
            "autonomy",
            "deployment_decisions",
            "acknowledgements",
            "consequential_audit",
        ):
            assert counts[table] == 0
