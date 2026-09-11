"""Where the desk's configuration and its record live.

Two kinds of thing, kept apart on purpose.

**Configuration** — connections, credential *metadata*, account policies, copy
groups, allocation constraints — is mutable and is what the operator edits. It
is versioned by being overwritten with an `updated_at`, because a copy group's
history is not itself evidence.

**The record** — allocation changes, desk decisions, reconciliation reports,
credential accesses — is append-only. Every one of them exists to answer a
question after the fact: why did that follower not take the trade, when did this
account switch strategy, what read the provider password. A row anybody can edit
answers none of them, so there is no update and no delete on these tables.

**No secret reaches this file.** `credentials` stores a `CredentialRecord`,
which is a provider, a label, some non-secret public fields and the *name of an
environment variable*. `CredentialRecord` validates that a public field is not
named like a secret, and `tests/propdesk/test_credentials.py` reads the database
back and asserts no stored value looks like one.

The provider snapshot cache that reconciliation compares against is deliberately
*not* here. The provider is the source of truth and a snapshot is a photograph
of a moment that has passed; persisting it would invite somebody to reconcile
against yesterday's.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.propdesk.allocation import Allocation, AllocationChange, AllocationConstraints
from forge.propdesk.audit import ConsequentialRecord
from forge.propdesk.autonomy import AutonomyLevel, DeploymentDecision
from forge.propdesk.consent import Acknowledgement, DisclosureKey
from forge.propdesk.copy import CopyGroup
from forge.propdesk.credentials import CredentialRecord
from forge.propdesk.desk import DeskDecision
from forge.propdesk.identity import Account, BrokerConnection
from forge.propdesk.news import EconomicEvent, NewsPolicy
from forge.propdesk.policy import PropProgramPolicy
from forge.propdesk.reconcile import ReconciliationReport
from forge.propdesk.risk import RiskSettings
from forge.propdesk.scaling import RiskProposal, ScalingState

#: Bumped when the schema gains a table. Every statement is `IF NOT EXISTS`, so
#: an older database gains the new tables on the next open rather than needing a
#: migration; the number is here so a reader can tell which shape they have.
SCHEMA_VERSION = 2


class PropDeskStore:
    """Connections, accounts, policies, groups, allocations — and the record."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS connections (
                    connection_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Metadata only. The secret lives where `sources` says it does.
                CREATE TABLE IF NOT EXISTS credentials (
                    credential_ref TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    account_uid TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS accounts_by_connection
                    ON accounts(connection_id);

                CREATE TABLE IF NOT EXISTS policies (
                    policy_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS copy_groups (
                    group_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS allocations (
                    account_uid TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Append-only from here down. No UPDATE, no DELETE.
                CREATE TABLE IF NOT EXISTS allocation_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_uid TEXT NOT NULL,
                    at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS allocation_history_by_account
                    ON allocation_history(account_uid, at DESC);

                CREATE TABLE IF NOT EXISTS desk_decisions (
                    decision_id TEXT PRIMARY KEY,
                    account_uid TEXT NOT NULL,
                    at TEXT NOT NULL,
                    cleared INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS desk_decisions_by_time
                    ON desk_decisions(at DESC);

                CREATE TABLE IF NOT EXISTS reconciliations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_uid TEXT NOT NULL,
                    at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS reconciliations_by_account
                    ON reconciliations(account_uid, at DESC);

                CREATE TABLE IF NOT EXISTS calendar_events (
                    event_id TEXT PRIMARY KEY,
                    at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS calendar_events_by_time
                    ON calendar_events(at);

                CREATE TABLE IF NOT EXISTS desk_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                -- Risk configuration. One row per account, and a separate
                -- append-only table for every proposal the scaler produced, so
                -- "why is my size different from yesterday" is answerable from
                -- the record rather than by re-deriving it against state that
                -- has since moved.
                CREATE TABLE IF NOT EXISTS risk_settings (
                    account_uid TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_state (
                    account_uid TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS autonomy (
                    account_uid TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Append-only from here down. No UPDATE, no DELETE.
                CREATE TABLE IF NOT EXISTS risk_proposals (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_uid TEXT NOT NULL,
                    at TEXT NOT NULL,
                    changed INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS risk_proposals_by_account
                    ON risk_proposals(account_uid, at DESC);

                CREATE TABLE IF NOT EXISTS deployment_decisions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_uid TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS deployment_decisions_by_time
                    ON deployment_decisions(at DESC);

                CREATE TABLE IF NOT EXISTS acknowledgements (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    disclosure_key TEXT NOT NULL,
                    version TEXT NOT NULL,
                    account_uid TEXT NOT NULL,
                    at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS acknowledgements_by_key
                    ON acknowledgements(disclosure_key, at DESC);

                CREATE TABLE IF NOT EXISTS consequential_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    account_uid TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS consequential_audit_by_time
                    ON consequential_audit(at DESC);
                """
            )
            db.execute(
                "INSERT INTO desk_settings VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(SCHEMA_VERSION),),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ── connections ──────────────────────────────────────────────────────────
    def save_connection(self, connection: BrokerConnection) -> BrokerConnection:
        self._upsert(
            "connections",
            "connection_id",
            connection.connection_id,
            connection.model_dump(mode="json"),
            extra={"provider": connection.provider.value},
            stamp="updated_at",
        )
        return connection

    def connection(self, connection_id: str) -> BrokerConnection | None:
        row = self._one("connections", "connection_id", connection_id)
        return None if row is None else BrokerConnection.model_validate(row)

    def connections(self) -> tuple[BrokerConnection, ...]:
        return tuple(
            BrokerConnection.model_validate(payload)
            for payload in self._all("connections", "updated_at DESC")
        )

    def delete_connection(self, connection_id: str) -> bool:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            removed = db.execute(
                "DELETE FROM connections WHERE connection_id=?", (connection_id,)
            ).rowcount
            if removed:
                db.execute("DELETE FROM accounts WHERE connection_id=?", (connection_id,))
        return bool(removed)

    # ── credentials (metadata only) ──────────────────────────────────────────
    def save_credential(self, record: CredentialRecord) -> CredentialRecord:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO credentials VALUES (?, ?, ?, ?) "
                "ON CONFLICT(credential_ref) DO UPDATE SET payload=excluded.payload",
                (
                    record.credential_ref,
                    record.provider.value,
                    json.dumps(record.model_dump(mode="json")),
                    record.created_at.isoformat(),
                ),
            )
        return record

    def credential(self, credential_ref: str) -> CredentialRecord | None:
        row = self._one("credentials", "credential_ref", credential_ref)
        return None if row is None else CredentialRecord.model_validate(row)

    def credentials(self) -> tuple[CredentialRecord, ...]:
        return tuple(
            CredentialRecord.model_validate(payload)
            for payload in self._all("credentials", "created_at DESC")
        )

    def delete_credential(self, credential_ref: str) -> bool:
        with closing(self._connect()) as db, db:
            removed = db.execute(
                "DELETE FROM credentials WHERE credential_ref=?", (credential_ref,)
            ).rowcount
        return bool(removed)

    # ── accounts ─────────────────────────────────────────────────────────────
    def save_account(self, account: Account) -> Account:
        self._upsert(
            "accounts",
            "account_uid",
            account.account_uid,
            account.model_dump(mode="json"),
            extra={"connection_id": account.connection_id},
            stamp="updated_at",
        )
        return account

    def account(self, account_uid: str) -> Account | None:
        row = self._one("accounts", "account_uid", account_uid)
        return None if row is None else Account.model_validate(row)

    def accounts(self, connection_id: str | None = None) -> tuple[Account, ...]:
        with closing(self._connect()) as db, db:
            if connection_id is None:
                rows = db.execute(
                    "SELECT payload FROM accounts ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM accounts WHERE connection_id=? "
                    "ORDER BY updated_at DESC",
                    (connection_id,),
                ).fetchall()
        return tuple(Account.model_validate(json.loads(row["payload"])) for row in rows)

    # ── programme policies ───────────────────────────────────────────────────
    def save_policy(self, policy: PropProgramPolicy) -> PropProgramPolicy:
        self._upsert(
            "policies",
            "policy_id",
            policy.policy_id,
            policy.model_dump(mode="json"),
            stamp="updated_at",
        )
        return policy

    def policy(self, policy_id: str) -> PropProgramPolicy | None:
        row = self._one("policies", "policy_id", policy_id)
        return None if row is None else PropProgramPolicy.model_validate(row)

    def policies(self) -> tuple[PropProgramPolicy, ...]:
        return tuple(
            PropProgramPolicy.model_validate(payload)
            for payload in self._all("policies", "updated_at DESC")
        )

    # ── copy groups ──────────────────────────────────────────────────────────
    def save_group(self, group: CopyGroup) -> CopyGroup:
        self._upsert(
            "copy_groups",
            "group_id",
            group.group_id,
            group.model_dump(mode="json"),
            stamp="updated_at",
        )
        return group

    def group(self, group_id: str) -> CopyGroup | None:
        row = self._one("copy_groups", "group_id", group_id)
        return None if row is None else CopyGroup.model_validate(row)

    def groups(self) -> tuple[CopyGroup, ...]:
        return tuple(
            CopyGroup.model_validate(payload)
            for payload in self._all("copy_groups", "updated_at DESC")
        )

    def delete_group(self, group_id: str) -> bool:
        with closing(self._connect()) as db, db:
            removed = db.execute(
                "DELETE FROM copy_groups WHERE group_id=?", (group_id,)
            ).rowcount
        return bool(removed)

    # ── allocations, and their history ───────────────────────────────────────
    def save_allocation(self, allocation: Allocation) -> Allocation:
        self._upsert(
            "allocations",
            "account_uid",
            allocation.account_uid,
            allocation.model_dump(mode="json"),
            stamp="updated_at",
        )
        return allocation

    def allocation(self, account_uid: str) -> Allocation | None:
        row = self._one("allocations", "account_uid", account_uid)
        return None if row is None else Allocation.model_validate(row)

    def allocations(self) -> tuple[Allocation, ...]:
        return tuple(
            Allocation.model_validate(payload)
            for payload in self._all("allocations", "updated_at DESC")
        )

    def clear_allocation(self, account_uid: str) -> bool:
        with closing(self._connect()) as db, db:
            removed = db.execute(
                "DELETE FROM allocations WHERE account_uid=?", (account_uid,)
            ).rowcount
        return bool(removed)

    def record_change(self, change: AllocationChange) -> AllocationChange:
        """Append one allocation change. There is no way to amend it."""
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO allocation_history (account_uid, at, payload) VALUES (?, ?, ?)",
                (
                    change.account_uid,
                    change.at.astimezone(UTC).isoformat(),
                    json.dumps(change.as_dict()),
                ),
            )
        return change

    def history(
        self, account_uid: str | None = None, limit: int = 200
    ) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as db, db:
            if account_uid is None:
                rows = db.execute(
                    "SELECT payload FROM allocation_history ORDER BY at DESC, "
                    "sequence DESC LIMIT ?",
                    (max(1, min(limit, 2000)),),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM allocation_history WHERE account_uid=? "
                    "ORDER BY at DESC, sequence DESC LIMIT ?",
                    (account_uid, max(1, min(limit, 2000))),
                ).fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    def changes_today(self, account_uid: str, *, today: datetime | None = None) -> int:
        """How many allocation changes this account has had today.

        Read by the allocator's churn constraint. Counted from the record rather
        than from a counter in memory, so a restart does not reset somebody's
        daily change budget.
        """
        day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM allocation_history "
                "WHERE account_uid=? AND at >= ?",
                (account_uid, f"{day}T00:00:00+00:00"),
            ).fetchone()
        return 0 if row is None else int(row["n"])

    # ── the desk's own record ────────────────────────────────────────────────
    def record_decision(self, decision: DeskDecision) -> DeskDecision:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO desk_decisions VALUES (?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.intent.account_uid,
                    decision.at.astimezone(UTC).isoformat(),
                    1 if decision.cleared else 0,
                    json.dumps(decision.as_dict()),
                ),
            )
        return decision

    def decisions(
        self, *, account_uid: str = "", cleared: bool | None = None, limit: int = 200
    ) -> tuple[dict[str, Any], ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if account_uid:
            clauses.append("account_uid=?")
            params.append(account_uid)
        if cleared is not None:
            clauses.append("cleared=?")
            params.append(1 if cleared else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 2000)))
        with closing(self._connect()) as db, db:
            rows = db.execute(
                f"SELECT payload FROM desk_decisions{where} ORDER BY at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    def record_reconciliation(self, report: ReconciliationReport) -> ReconciliationReport:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO reconciliations (account_uid, at, state, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    report.account_uid,
                    report.at.astimezone(UTC).isoformat(),
                    report.state.value,
                    json.dumps(report.as_dict()),
                ),
            )
        return report

    def reconciliations(
        self, account_uid: str | None = None, limit: int = 100
    ) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as db, db:
            if account_uid is None:
                rows = db.execute(
                    "SELECT payload FROM reconciliations ORDER BY at DESC, "
                    "sequence DESC LIMIT ?",
                    (max(1, min(limit, 1000)),),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM reconciliations WHERE account_uid=? "
                    "ORDER BY at DESC, sequence DESC LIMIT ?",
                    (account_uid, max(1, min(limit, 1000))),
                ).fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    # ── the calendar ─────────────────────────────────────────────────────────
    def save_event(self, event: EconomicEvent) -> EconomicEvent:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO calendar_events VALUES (?, ?, ?) "
                "ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload",
                (
                    event.event_id,
                    event.at.astimezone(UTC).isoformat(),
                    json.dumps(event.model_dump(mode="json")),
                ),
            )
        return event

    def events(self, start: datetime, end: datetime) -> tuple[EconomicEvent, ...]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT payload FROM calendar_events WHERE at BETWEEN ? AND ? "
                "ORDER BY at ASC",
                (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()),
            ).fetchall()
        return tuple(
            EconomicEvent.model_validate(json.loads(row["payload"])) for row in rows
        )

    def delete_event(self, event_id: str) -> bool:
        with closing(self._connect()) as db, db:
            removed = db.execute(
                "DELETE FROM calendar_events WHERE event_id=?", (event_id,)
            ).rowcount
        return bool(removed)

    # ── settings ─────────────────────────────────────────────────────────────
    def news_policy(self) -> NewsPolicy:
        raw = self._setting("news_policy")
        return NewsPolicy() if raw is None else NewsPolicy.model_validate(raw)

    def save_news_policy(self, policy: NewsPolicy) -> NewsPolicy:
        self._save_setting("news_policy", policy.model_dump(mode="json"))
        return policy

    def constraints(self) -> AllocationConstraints:
        raw = self._setting("allocation_constraints")
        return (
            AllocationConstraints()
            if raw is None
            else AllocationConstraints.model_validate(raw)
        )

    def save_constraints(self, constraints: AllocationConstraints) -> AllocationConstraints:
        self._save_setting("allocation_constraints", constraints.model_dump(mode="json"))
        return constraints

    def owner_attestation(self) -> str:
        raw = self._setting("owner_attestation")
        return "" if raw is None else str(raw)

    # ── risk configuration, and every proposal it produced ───────────────────
    def save_risk_settings(self, settings: RiskSettings) -> RiskSettings:
        self._upsert(
            "risk_settings",
            "account_uid",
            settings.account_uid,
            settings.model_dump(mode="json"),
            stamp="updated_at",
        )
        return settings

    def risk_settings(self, account_uid: str) -> RiskSettings | None:
        row = self._one("risk_settings", "account_uid", account_uid)
        return None if row is None else RiskSettings.model_validate(row)

    def all_risk_settings(self) -> tuple[RiskSettings, ...]:
        return tuple(
            RiskSettings.model_validate(payload)
            for payload in self._all("risk_settings", "updated_at DESC")
        )

    def save_scaling_state(self, state: ScalingState) -> ScalingState:
        self._upsert(
            "risk_state",
            "account_uid",
            state.account_uid,
            state.model_dump(mode="json"),
            stamp="updated_at",
        )
        return state

    def scaling_state(self, account_uid: str) -> ScalingState | None:
        row = self._one("risk_state", "account_uid", account_uid)
        return None if row is None else ScalingState.model_validate(row)

    def record_proposal(self, proposal: RiskProposal) -> RiskProposal:
        """Append one risk evaluation. There is no way to amend it.

        Proposals that changed nothing are recorded too. "Nothing moved, and
        here is what was measured" is the answer to half the questions an
        operator asks of this screen, and a log of only the changes cannot give
        it.

        The row holds `model_dump`, not `as_dict`: the derived keys `as_dict`
        adds are a rendering, and a row carrying them cannot be validated back
        into a `RiskProposal` — which is exactly what answering "why did my risk
        change" from the record requires. Rendering happens on the way out.
        """
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO risk_proposals (account_uid, at, changed, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    proposal.account_uid,
                    proposal.at.astimezone(UTC).isoformat(),
                    int(proposal.changed),
                    json.dumps(proposal.model_dump(mode="json")),
                ),
            )
        return proposal

    def proposals(
        self, account_uid: str | None = None, limit: int = 100, *, changed_only: bool = False
    ) -> tuple[dict[str, Any], ...]:
        clauses: list[str] = []
        params: list[object] = []
        if account_uid is not None:
            clauses.append("account_uid=?")
            params.append(account_uid)
        if changed_only:
            clauses.append("changed=1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 2000)))
        with closing(self._connect()) as db, db:
            rows = db.execute(
                f"SELECT payload FROM risk_proposals {where} "
                "ORDER BY at DESC, sequence DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    # ── autonomy ─────────────────────────────────────────────────────────────
    def save_autonomy(self, account_uid: str, level: AutonomyLevel) -> AutonomyLevel:
        self._upsert(
            "autonomy",
            "account_uid",
            account_uid,
            {"account_uid": account_uid, "level": level.value},
            stamp="updated_at",
        )
        return level

    def autonomy(self, account_uid: str) -> AutonomyLevel:
        """The level for one account, defaulting to OFF.

        A missing row is OFF rather than an error: an account nobody has
        configured has not opted into autonomous deployment, and the safe
        reading of silence is the one that does nothing.
        """
        row = self._one("autonomy", "account_uid", account_uid)
        return AutonomyLevel.OFF if row is None else AutonomyLevel(row["level"])

    def all_autonomy(self) -> dict[str, AutonomyLevel]:
        return {
            payload["account_uid"]: AutonomyLevel(payload["level"])
            for payload in self._all("autonomy", "updated_at DESC")
        }

    def record_deployment(self, decision: DeploymentDecision) -> DeploymentDecision:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO deployment_decisions "
                "(account_uid, strategy_id, at, outcome, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    decision.account_uid,
                    decision.strategy_id,
                    decision.at.astimezone(UTC).isoformat(),
                    decision.outcome.value,
                    json.dumps(decision.as_dict()),
                ),
            )
        return decision

    def deployments(
        self, account_uid: str | None = None, limit: int = 100
    ) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as db, db:
            if account_uid is None:
                rows = db.execute(
                    "SELECT payload FROM deployment_decisions "
                    "ORDER BY at DESC, sequence DESC LIMIT ?",
                    (max(1, min(limit, 2000)),),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM deployment_decisions WHERE account_uid=? "
                    "ORDER BY at DESC, sequence DESC LIMIT ?",
                    (account_uid, max(1, min(limit, 2000))),
                ).fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    # ── consent ──────────────────────────────────────────────────────────────
    def record_acknowledgement(self, acknowledgement: Acknowledgement) -> Acknowledgement:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO acknowledgements "
                "(disclosure_key, version, account_uid, at, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    acknowledgement.key.value,
                    acknowledgement.version,
                    acknowledgement.account_uid,
                    acknowledgement.acknowledged_at.astimezone(UTC).isoformat(),
                    json.dumps(acknowledgement.as_dict()),
                ),
            )
        return acknowledgement

    def acknowledgements(
        self, key: DisclosureKey | None = None
    ) -> tuple[Acknowledgement, ...]:
        with closing(self._connect()) as db, db:
            if key is None:
                rows = db.execute(
                    "SELECT payload FROM acknowledgements ORDER BY at DESC, sequence DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM acknowledgements WHERE disclosure_key=? "
                    "ORDER BY at DESC, sequence DESC",
                    (key.value,),
                ).fetchall()
        return tuple(Acknowledgement.model_validate(json.loads(row["payload"])) for row in rows)

    # ── the consequential audit trail ────────────────────────────────────────
    def record_audit(self, record: ConsequentialRecord) -> ConsequentialRecord:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO consequential_audit (at, account_uid, action, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    record.at.astimezone(UTC).isoformat(),
                    record.account_uid,
                    record.action.value,
                    json.dumps(record.as_dict()),
                ),
            )
        return record

    def audit(
        self, account_uid: str | None = None, limit: int = 200
    ) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as db, db:
            if account_uid is None:
                rows = db.execute(
                    "SELECT payload FROM consequential_audit "
                    "ORDER BY at DESC, sequence DESC LIMIT ?",
                    (max(1, min(limit, 2000)),),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM consequential_audit WHERE account_uid=? "
                    "ORDER BY at DESC, sequence DESC LIMIT ?",
                    (account_uid, max(1, min(limit, 2000))),
                ).fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    def save_owner_attestation(self, attested_by: str) -> str:
        self._save_setting("owner_attestation", attested_by)
        return attested_by

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _upsert(
        self,
        table: str,
        key_column: str,
        key: str,
        payload: dict[str, Any],
        *,
        extra: dict[str, str] | None = None,
        stamp: str,
    ) -> None:
        columns = [key_column, *sorted(extra or {}), "payload", stamp]
        values = [
            key,
            *[(extra or {})[name] for name in sorted(extra or {})],
            json.dumps(payload),
            datetime.now(UTC).isoformat(),
        ]
        assignments = ", ".join(
            f"{name}=excluded.{name}" for name in columns if name != key_column
        )
        with closing(self._connect()) as db, db:
            db.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)}) "
                f"ON CONFLICT({key_column}) DO UPDATE SET {assignments}",
                tuple(values),
            )

    def _one(self, table: str, key_column: str, key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                f"SELECT payload FROM {table} WHERE {key_column}=?", (key,)
            ).fetchone()
        if row is None:
            return None
        payload: dict[str, Any] = json.loads(row["payload"])
        return payload

    def _all(self, table: str, order: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as db, db:
            rows = db.execute(f"SELECT payload FROM {table} ORDER BY {order}").fetchall()
        return tuple(json.loads(row["payload"]) for row in rows)

    def _setting(self, key: str) -> Any:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT value FROM desk_settings WHERE key=?", (key,)
            ).fetchone()
        return None if row is None else json.loads(row["value"])

    def _save_setting(self, key: str, value: Any) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO desk_settings VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def counts(self) -> dict[str, int]:
        tables = (
            "connections",
            "credentials",
            "accounts",
            "policies",
            "copy_groups",
            "allocations",
            "allocation_history",
            "desk_decisions",
            "reconciliations",
            "calendar_events",
            "risk_settings",
            "risk_proposals",
            "autonomy",
            "deployment_decisions",
            "acknowledgements",
            "consequential_audit",
        )
        with closing(self._connect()) as db, db:
            return {
                table: int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
                for table in tables
            }
