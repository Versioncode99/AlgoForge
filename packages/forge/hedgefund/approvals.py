"""The queue a person acts on, and the only way an AI-proposed action becomes done.

When `forge.modes.permissions` rules `REQUIRE_APPROVAL`, the call does not run.
It lands here, with the arguments it would have used, the reason the policy held
it, and everything a person needs to decide without reconstructing the request.

**Approving is not a flag flip.** It executes the action *as the operator*, at
approval time, through the same registry the interface uses. That matters for
two reasons: the action re-validates its own arguments against the state that
exists now rather than the state that existed when it was proposed, and the
resulting audit row honestly names a human as the actor — because a human is
what authorised it.

**A request holds no capability.** It is data: an action name and arguments. It
does not carry a token, a permission or a pre-computed result, so nothing is
gained by an agent creating one, and an agent cannot approve one — approval
arrives through a surface that only a person reaches.

**Expiry is real.** A proposed rebalance approved four hours later is a decision
made on prices that have moved. Requests carry a deadline and expire rather than
waiting indefinitely, and an expired request is rejected rather than silently
run late.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel

SCHEMA_VERSION = 1

#: How long a request stays actionable. Fifteen minutes is short enough that an
#: approved order is still priced on something recent, and long enough that a
#: person can read the proposal before deciding.
DEFAULT_TTL_SECONDS = 900


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    #: Approved, but the action failed when it ran. Distinct from REJECTED: the
    #: person said yes, and what went wrong afterwards is a different fact.
    FAILED = "failed"


class ApprovalRequest(FrozenModel):
    request_id: str
    created_at: datetime
    expires_at: datetime
    #: Who proposed it. Always an AI actor today; the field exists because a
    #: request from a second operator is the obvious next case.
    requested_by: str
    origin: str = ""
    mode: str = ""
    stance: str = ""
    action: str
    arguments: dict[str, Any]
    #: The permission policy's own words for why this needs a person.
    reason: str
    #: What the proposer expects the action to do, in a sentence. Written by the
    #: caller, not generated here — a summary invented by the queue would be a
    #: description of the action rather than of this instance of it.
    summary: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: datetime | None = None
    decided_by: str = ""
    note: str = ""
    result: str = ""
    error: str = ""

    def expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["expired"] = self.status is ApprovalStatus.PENDING and self.expired()
        return payload


class ApprovalError(Exception):
    """The request could not be acted on, with a reason to show verbatim."""


class ApprovalQueue:
    """Durable requests, plus the execution that happens when one is approved."""

    def __init__(self, path: Path, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS approvals ("
                "request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS approvals_by_status ON approvals(status, created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ── proposing ────────────────────────────────────────────────────────────
    def submit(
        self,
        *,
        action: str,
        arguments: dict[str, Any],
        reason: str,
        requested_by: str = "ai",
        origin: str = "",
        mode: str = "",
        stance: str = "",
        summary: str = "",
        ttl_seconds: int | None = None,
    ) -> ApprovalRequest:
        now = datetime.now(UTC)
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        request = ApprovalRequest(
            request_id=stable_id(
                "approval", {"action": action, "arguments": arguments, "at": now.isoformat()}
            ),
            created_at=now,
            expires_at=now + timedelta(seconds=max(30, ttl)),
            requested_by=requested_by,
            origin=origin,
            mode=mode,
            stance=stance,
            action=action,
            arguments=dict(arguments),
            reason=reason,
            summary=summary,
        )
        self._write(request)
        return request

    # ── deciding ─────────────────────────────────────────────────────────────
    def approve(
        self,
        request_id: str,
        run: Callable[[str, dict[str, Any]], Any],
        *,
        decided_by: str = "operator",
        note: str = "",
    ) -> ApprovalRequest:
        """Run the held action as the operator, and record what came of it.

        `run` is supplied by the caller rather than held by the queue, so this
        module has no dependency on the action registry and cannot become a
        second way to invoke one. It is called exactly once, inside the lock, so
        two approvals of the same request cannot both execute.
        """
        with self._lock:
            request = self._claim(request_id)
            try:
                result = run(request.action, dict(request.arguments))
            except Exception as exc:
                failed = request.model_copy(
                    update={
                        "status": ApprovalStatus.FAILED,
                        "decided_at": datetime.now(UTC),
                        "decided_by": decided_by,
                        "note": note,
                        "error": f"{type(exc).__name__}: {exc}"[:2000],
                    }
                )
                self._write(failed)
                return failed
            approved = request.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": decided_by,
                    "note": note,
                    "result": _render(result),
                }
            )
            self._write(approved)
            return approved

    def reject(
        self, request_id: str, *, decided_by: str = "operator", note: str = ""
    ) -> ApprovalRequest:
        with self._lock:
            request = self.get(request_id)
            if request is None:
                raise ApprovalError(f"no approval request '{request_id}'")
            if request.status is not ApprovalStatus.PENDING:
                raise ApprovalError(
                    f"'{request_id}' is already {request.status.value} and cannot be rejected"
                )
            rejected = request.model_copy(
                update={
                    "status": ApprovalStatus.REJECTED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": decided_by,
                    "note": note,
                }
            )
            self._write(rejected)
            return rejected

    def _claim(self, request_id: str) -> ApprovalRequest:
        request = self.get(request_id)
        if request is None:
            raise ApprovalError(f"no approval request '{request_id}'")
        if request.status is not ApprovalStatus.PENDING:
            raise ApprovalError(
                f"'{request_id}' is already {request.status.value}; an approval runs once"
            )
        if request.expired():
            expired = request.model_copy(
                update={"status": ApprovalStatus.EXPIRED, "decided_at": datetime.now(UTC)}
            )
            self._write(expired)
            raise ApprovalError(
                f"'{request_id}' expired at {request.expires_at.isoformat()}. It was not run: "
                "a decision approved late is made on inputs that have moved. Propose it again."
            )
        return request

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, request_id: str) -> ApprovalRequest | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT payload FROM approvals WHERE request_id=?", (request_id,)
            ).fetchone()
        return None if row is None else ApprovalRequest.model_validate(json.loads(row["payload"]))

    def pending(self) -> list[ApprovalRequest]:
        """Everything still awaiting a person, expiring anything that timed out.

        Expiry happens on read rather than on a timer: this application has no
        scheduler that outlives a request, and a queue that only expires when
        somebody looks is honest about that. What it must not do is *show* an
        expired request as actionable, and it does not.
        """
        now = datetime.now(UTC)
        live: list[ApprovalRequest] = []
        for request in self._by_status(ApprovalStatus.PENDING):
            if request.expired(now):
                self._write(
                    request.model_copy(
                        update={"status": ApprovalStatus.EXPIRED, "decided_at": now}
                    )
                )
                continue
            live.append(request)
        return live

    def history(self, limit: int = 100) -> list[ApprovalRequest]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT payload FROM approvals ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [ApprovalRequest.model_validate(json.loads(row["payload"])) for row in rows]

    def _by_status(self, status: ApprovalStatus) -> list[ApprovalRequest]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT payload FROM approvals WHERE status=? ORDER BY created_at ASC",
                (status.value,),
            ).fetchall()
        return [ApprovalRequest.model_validate(json.loads(row["payload"])) for row in rows]

    def _write(self, request: ApprovalRequest) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(request_id) DO UPDATE SET payload=excluded.payload, "
                "status=excluded.status",
                (
                    request.request_id,
                    json.dumps(request.model_dump(mode="json")),
                    request.status.value,
                    request.created_at.isoformat(),
                    SCHEMA_VERSION,
                ),
            )


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value[:4000]
    try:
        return json.dumps(value, default=str)[:4000]
    except (TypeError, ValueError):
        return str(value)[:4000]
