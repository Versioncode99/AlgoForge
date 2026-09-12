from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forge.contracts.hashing import content_hash, stable_id


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Preregistration(FrozenModel):
    schema_version: Literal["1"] = "1"
    hypothesis: str = Field(min_length=20)
    mechanism: str = Field(min_length=10)
    falsification: str = Field(min_length=10)
    frozen_at: datetime
    preregistration_id: str = ""
    content_hash: str = ""
    #: The fingerprint of the `TimeScope` this claim was frozen against.
    #:
    #: The window an experiment runs on is part of its claim. Two years and ten
    #: years are different assertions about the same mechanism, and a programme
    #: that tries one, dislikes the answer and reports the other has selected
    #: rather than tested. Carrying the fingerprint here means G1 -- which
    #: already re-derives this hash at judge time and fails ``CLAIM_MOVED`` when
    #: anything shifted -- catches a moved window with no new gate and no change
    #: to the ladder.
    scope_fingerprint: str = ""

    @classmethod
    def freeze(
        cls,
        hypothesis: str,
        mechanism: str,
        falsification: str,
        frozen_at: datetime,
        scope_fingerprint: str = "",
    ) -> Preregistration:
        payload = {
            "schema_version": "1",
            "hypothesis": hypothesis,
            "mechanism": mechanism,
            "falsification": falsification,
            "frozen_at": frozen_at.astimezone(UTC).isoformat(),
        }
        # Folded in only when present. Adding it unconditionally would change
        # the hash of every claim frozen before time scopes existed, and G1
        # would then report CLAIM_MOVED for every strategy in the library --
        # invalidating evidence that was never touched. The same conditional
        # identity keeps experiment keys stable across the campaign change.
        if scope_fingerprint:
            payload["time_scope"] = scope_fingerprint
        digest = content_hash(payload)
        return cls(
            hypothesis=hypothesis,
            mechanism=mechanism,
            falsification=falsification,
            frozen_at=frozen_at,
            scope_fingerprint=scope_fingerprint,
            preregistration_id=stable_id("pre", payload),
            content_hash=digest,
        )


class RunRecord(FrozenModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    preregistration_id: str
    preregistration_hash: str
    tier: Literal["SWEEP", "TRUTH_OOS", "HOLDOUT", "FORWARD"]
    source_hash: str
    data_hash: str
    cost_hash: str
    engine_version: str
    created_at: datetime
    status: Literal["FINAL"] = "FINAL"
    labels: tuple[str, ...] = ("SAMPLE_DATA", "UNCALIBRATED")

    @classmethod
    def create(
        cls,
        preregistration: Preregistration,
        tier: Literal["SWEEP", "TRUTH_OOS", "HOLDOUT", "FORWARD"],
        source_hash: str,
        data_hash: str,
        cost_hash: str,
        engine_version: str,
        created_at: datetime,
    ) -> RunRecord:
        if created_at <= preregistration.frozen_at:
            raise ValueError("run must be created after preregistration is frozen")
        payload = {
            "preregistration_id": preregistration.preregistration_id,
            "preregistration_hash": preregistration.content_hash,
            "tier": tier,
            "source_hash": source_hash,
            "data_hash": data_hash,
            "cost_hash": cost_hash,
            "engine_version": engine_version,
            "created_at": created_at.astimezone(UTC).isoformat(),
        }
        return cls(
            run_id=stable_id("run", payload),
            preregistration_id=preregistration.preregistration_id,
            preregistration_hash=preregistration.content_hash,
            tier=tier,
            source_hash=source_hash,
            data_hash=data_hash,
            cost_hash=cost_hash,
            engine_version=engine_version,
            created_at=created_at,
        )


class CalculationTrace(FrozenModel):
    trace_id: str
    metric: str
    formula: str
    inputs: dict[str, float | int | str]
    value: float | int | str
    source_tier: str
    limitations: tuple[str, ...] = ()


class DecisionRecord(FrozenModel):
    decision_id: str
    kind: str
    subject_id: str
    payload_hash: str
    previous_hash: str | None
    recorded_at: datetime
    record_hash: str

    @classmethod
    def append(
        cls,
        kind: str,
        subject_id: str,
        payload: dict[str, Any],
        previous_hash: str | None,
        recorded_at: datetime,
    ) -> DecisionRecord:
        base = {
            "kind": kind,
            "subject_id": subject_id,
            "payload_hash": content_hash(payload),
            "previous_hash": previous_hash,
            "recorded_at": recorded_at.astimezone(UTC).isoformat(),
        }
        digest = content_hash(base)
        return cls(
            decision_id=stable_id("dec", base),
            kind=kind,
            subject_id=subject_id,
            payload_hash=content_hash(payload),
            previous_hash=previous_hash,
            recorded_at=recorded_at,
            record_hash=digest,
        )


class ApiEnvelope[T](FrozenModel):
    data: T
    meta: dict[str, Any] = Field(default_factory=dict)
