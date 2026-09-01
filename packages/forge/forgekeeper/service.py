from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Literal

from forge.contracts.hashing import stable_id
from forge.forgekeeper.models import CanaryResult, ReleaseManifest, RepoCandidate

PROTECTED_PREFIXES = ("packages/forge/judge/", "config/capabilities.json", "rules/")
PERMISSIVE_LICENCES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}


def classify_candidate(
    repository: str,
    commit_sha: str,
    licence_spdx: str | None,
    provenance_url: str,
    proposed_features: tuple[str, ...],
) -> RepoCandidate:
    lane: Literal["REUSE", "REVIEW", "CLEAN_ROOM", "REJECT"]
    if licence_spdx in PERMISSIVE_LICENCES:
        lane = "REUSE"
    elif licence_spdx is None:
        lane = "CLEAN_ROOM"
    elif licence_spdx in {"GPL-3.0", "AGPL-3.0"}:
        lane = "REVIEW"
    else:
        lane = "REJECT"
    return RepoCandidate(
        repository=repository,
        commit_sha=commit_sha,
        licence_spdx=licence_spdx,
        provenance_url=provenance_url,
        lane=lane,
        proposed_features=proposed_features,
    )


class ForgeKeeper:
    """Local release supervisor; it never edits the active release in place."""

    def __init__(self, state_directory: Path) -> None:
        self.state_directory = state_directory
        self.releases_directory = state_directory / "releases"
        self.releases_directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def evaluate_canary(
        *,
        test_pass_rate: float,
        verdict_drift: float,
        latency_regression: float,
        changed_paths: tuple[str, ...],
    ) -> CanaryResult:
        normalised = tuple(
            PurePosixPath(item.replace("\\", "/")).as_posix() for item in changed_paths
        )
        protected = tuple(
            path
            for path in normalised
            if any(path == prefix or path.startswith(prefix) for prefix in PROTECTED_PREFIXES)
        )
        reasons: list[str] = []
        if test_pass_rate < 1:
            reasons.append("TEST_FAILURE")
        if abs(verdict_drift) > 0:
            reasons.append("NUMERIC_VERDICT_DRIFT")
        if latency_regression > 0.2:
            reasons.append("LATENCY_REGRESSION")
        if protected:
            reasons.append("PROTECTED_PATH_CHANGE")
        return CanaryResult(
            passed=not reasons,
            test_pass_rate=test_pass_rate,
            verdict_drift=verdict_drift,
            latency_regression=latency_regression,
            protected_path_changes=protected,
            reasons=tuple(reasons),
        )

    def stage(self, candidate: RepoCandidate, canary: CanaryResult) -> ReleaseManifest:
        current = self._read_pointer("active")
        release_id = stable_id(
            "release",
            {
                "repo": candidate.repository,
                "sha": candidate.commit_sha,
                "canary": canary.model_dump(),
            },
        )
        manifest = ReleaseManifest(
            release_id=release_id,
            candidate=candidate,
            canary=canary,
            previous_release_id=current,
            status="STAGED" if canary.passed else "ROLLED_BACK",
        )
        release_dir = self.releases_directory / release_id
        release_dir.mkdir(exist_ok=True)
        (release_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        if not canary.passed:
            self._write_pointer("rollback", current)
        return manifest

    def activate(self, manifest: ReleaseManifest, *, human_approved: bool) -> ReleaseManifest:
        if not manifest.canary.passed:
            raise ValueError("CANARY_FAILED")
        if not human_approved:
            raise PermissionError("HUMAN_APPROVAL_REQUIRED")
        previous = self._read_pointer("active")
        self._write_pointer("previous", previous)
        self._write_pointer("active", manifest.release_id)
        active = manifest.model_copy(update={"status": "ACTIVE", "previous_release_id": previous})
        manifest_path = self.releases_directory / manifest.release_id / "manifest.json"
        manifest_path.write_text(active.model_dump_json(indent=2), encoding="utf-8")
        return active

    def overview(self) -> dict[str, object]:
        manifests = sorted(self.releases_directory.glob("*/manifest.json"))
        return {
            "active_release": self._read_pointer("active"),
            "previous_release": self._read_pointer("previous"),
            "release_count": len(manifests),
            "automatic_live_changes": False,
            "paper_only": True,
        }

    def _read_pointer(self, name: str) -> str | None:
        path = self.state_directory / f"{name}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text("utf-8"))
        result = value.get("release_id")
        return str(result) if result else None

    def _write_pointer(self, name: str, release_id: str | None) -> None:
        path = self.state_directory / f"{name}.json"
        path.write_text(json.dumps({"release_id": release_id}, indent=2), encoding="utf-8")
