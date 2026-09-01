import pytest
from forge.forgekeeper import ForgeKeeper, classify_candidate


def test_missing_licence_forces_clean_room_lane() -> None:
    candidate = classify_candidate("owner/repo", "abc123", None, "https://example.test", ("chart",))
    assert candidate.lane == "CLEAN_ROOM"


def test_failed_canary_cannot_activate_and_preserves_pointer(tmp_path) -> None:
    keeper = ForgeKeeper(tmp_path)
    candidate = classify_candidate(
        "owner/repo", "abc123", "MIT", "https://example.test", ("chart",)
    )
    canary = keeper.evaluate_canary(
        test_pass_rate=1,
        verdict_drift=0.01,
        latency_regression=0,
        changed_paths=("packages/forge/judge/engine.py",),
    )
    manifest = keeper.stage(candidate, canary)
    assert not canary.passed
    assert manifest.status == "ROLLED_BACK"
    with pytest.raises(ValueError, match="CANARY_FAILED"):
        keeper.activate(manifest, human_approved=True)
    assert keeper.overview()["active_release"] is None


def test_passing_canary_still_needs_human_approval(tmp_path) -> None:
    keeper = ForgeKeeper(tmp_path)
    candidate = classify_candidate("owner/repo", "def456", "MIT", "https://example.test", ("ui",))
    canary = keeper.evaluate_canary(
        test_pass_rate=1,
        verdict_drift=0,
        latency_regression=0.05,
        changed_paths=("apps/web/src/App.tsx",),
    )
    manifest = keeper.stage(candidate, canary)
    with pytest.raises(PermissionError, match="HUMAN_APPROVAL_REQUIRED"):
        keeper.activate(manifest, human_approved=False)
    active = keeper.activate(manifest, human_approved=True)
    assert active.status == "ACTIVE"
    assert keeper.overview()["active_release"] == active.release_id
