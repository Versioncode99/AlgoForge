from __future__ import annotations

from datetime import datetime
from typing import Literal

from forge.contracts.models import FrozenModel


class Incident(FrozenModel):
    incident_id: str
    kind: Literal["RUNTIME", "DATA", "MODEL", "RELEASE"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    summary: str
    evidence_ids: tuple[str, ...]
    detected_at: datetime
    status: Literal["OPEN", "PROPOSED", "RECOVERED"] = "OPEN"


class RepoCandidate(FrozenModel):
    repository: str
    commit_sha: str
    licence_spdx: str | None
    provenance_url: str
    lane: Literal["REUSE", "REVIEW", "CLEAN_ROOM", "REJECT"]
    proposed_features: tuple[str, ...]


class CanaryResult(FrozenModel):
    passed: bool
    test_pass_rate: float
    verdict_drift: float
    latency_regression: float
    protected_path_changes: tuple[str, ...]
    reasons: tuple[str, ...]


class ReleaseManifest(FrozenModel):
    release_id: str
    candidate: RepoCandidate
    canary: CanaryResult
    previous_release_id: str | None
    status: Literal["STAGED", "ACTIVE", "ROLLED_BACK"]
    labels: tuple[str, ...] = ("PAPER_ONLY", "HUMAN_REVIEW_REQUIRED")
