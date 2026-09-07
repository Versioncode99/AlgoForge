"""G0 and G1: the last two gates that were literals on the operator's path.

The engine supplied real values for both. The judge route a person drives from
the interface passed `True` for both. So the *same gate* was a measurement in
one path and a rubber stamp in the other, and the verdict does not record which
path produced it — which is worse than either alternative on its own.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

from forge.contracts.models import Preregistration
from forge.judge import Judge, JudgeInput
from forge.strategy import StrategyLibrary, generate_bars, run_backtest
from forge_api.preregistration_store import (
    FROZEN_AT,
    claim_holds,
    claims_for,
    freeze_claim,
    record_claim,
)


def library_with_one(tmp_path: Path) -> tuple[StrategyLibrary, Any]:
    library = StrategyLibrary(tmp_path / "strategies")
    return library, library.create_from_template("momentum_breakout", symbol="MNQ.CME")


# ── G0: the receipt run_backtest already computes ────────────────────────────


def test_a_run_carries_the_receipt_it_was_gated_on(tmp_path: Path) -> None:
    """It was computed and thrown away, so the gate read a literal instead."""
    library, spec = library_with_one(tmp_path)
    module = library.load_module(spec.strategy_id)
    result = run_backtest(module, spec, generate_bars(count=1_500, seed=3))

    assert result.data_quality is not None
    assert result.data_quality.accepted is True
    assert result.data_quality.row_count == 1_500
    assert result.data_quality.findings == ()
    library.unload_module(spec.strategy_id)


def test_the_receipt_survives_a_round_trip_through_json(tmp_path: Path) -> None:
    library, spec = library_with_one(tmp_path)
    module = library.load_module(spec.strategy_id)
    result = run_backtest(module, spec, generate_bars(count=1_500, seed=3))
    payload = result.model_dump(mode="json")
    assert payload["data_quality"]["accepted"] is True
    library.unload_module(spec.strategy_id)


def judge_with(**overrides: object) -> Any:
    base: dict[str, Any] = {
        "run_id": "run-1",
        "tier": "TRUTH_OOS",
        "pnl": tuple(float(v) for v in (80, -25, 95, -30, 70, -20, 110, -35, 60, 45) * 4),
        "trial_count": 1,
        "data_gate_passed": True,
        "preregistered": True,
        "implementation_tests_passed": True,
        "engine_consistent": True,
        "mechanism_aligned": True,
    }
    return Judge().evaluate(JudgeInput(**{**base, **overrides}))


def gate(verdict: Any, name: str) -> Any:
    return next(item for item in verdict.gates if item.gate == name)


def test_g0_is_inconclusive_when_no_receipt_was_recorded() -> None:
    """Artifacts written before the receipt existed do not get a free pass."""
    verdict = judge_with(data_gate_passed=None)
    assert gate(verdict, "G0").status == "INCONCLUSIVE"
    assert gate(verdict, "G0").observed == "NOT_MEASURED"
    assert verdict.decision == "INCONCLUSIVE"


def test_g0_fails_on_a_rejected_receipt() -> None:
    verdict = judge_with(data_gate_passed=False)
    assert gate(verdict, "G0").status == "FAIL"
    assert gate(verdict, "G0").observed == "RECEIPT_REJECTED"
    assert verdict.decision == "FAIL"


def test_g0_passes_on_an_accepted_receipt() -> None:
    assert gate(judge_with(data_gate_passed=True), "G0").observed == "RECEIPT_ACCEPTED"


def test_g0_no_longer_claims_a_receipt_it_did_not_read() -> None:
    rule = gate(judge_with(), "G0").rule
    assert "data-quality receipt" in rule


# ── G1: preregistration on the operator's path ───────────────────────────────


def test_a_claim_that_was_never_frozen_is_absent_not_failed(tmp_path: Path) -> None:
    """Never preregistered and preregistered-then-moved are different findings."""
    _, spec = library_with_one(tmp_path)
    assert claim_holds(tmp_path, spec.strategy_id, spec, dict(spec.defaults)) is None


def test_a_frozen_claim_re_derives_to_the_same_hash(tmp_path: Path) -> None:
    _, spec = library_with_one(tmp_path)
    params = dict(spec.defaults)
    record_claim(tmp_path, spec.strategy_id, freeze_claim(spec, params))
    assert claim_holds(tmp_path, spec.strategy_id, spec, params) is True


def test_moving_the_parameters_after_the_freeze_is_caught(tmp_path: Path) -> None:
    """The move preregistration exists to prevent."""
    _, spec = library_with_one(tmp_path)
    params = dict(spec.defaults)
    record_claim(tmp_path, spec.strategy_id, freeze_claim(spec, params))

    moved = dict(params)
    first = next(iter(moved))
    moved[first] = float(moved[first]) + 1.0
    assert claim_holds(tmp_path, spec.strategy_id, spec, moved) is False


def test_moving_the_hypothesis_after_the_freeze_is_caught(tmp_path: Path) -> None:
    _, spec = library_with_one(tmp_path)
    params = dict(spec.defaults)
    record_claim(tmp_path, spec.strategy_id, freeze_claim(spec, params))

    rewritten = spec.model_copy(update={"hypothesis": "Something else entirely."})
    assert claim_holds(tmp_path, spec.strategy_id, rewritten, params) is False


def test_a_second_freeze_is_recorded_beside_the_first_never_over_it(tmp_path: Path) -> None:
    """Overwriting would let a re-freeze launder a moved claim into a held one."""
    _, spec = library_with_one(tmp_path)
    params = dict(spec.defaults)
    record_claim(tmp_path, spec.strategy_id, freeze_claim(spec, params))

    moved = dict(params)
    first = next(iter(moved))
    moved[first] = float(moved[first]) + 1.0
    record_claim(tmp_path, spec.strategy_id, freeze_claim(spec, moved))

    rows = claims_for(tmp_path, spec.strategy_id)
    assert len(rows) == 2
    # Both claims now hold, because both were genuinely frozen before a run.
    assert claim_holds(tmp_path, spec.strategy_id, spec, params) is True
    assert claim_holds(tmp_path, spec.strategy_id, spec, moved) is True


def test_freezing_the_same_claim_twice_records_it_once(tmp_path: Path) -> None:
    _, spec = library_with_one(tmp_path)
    params = dict(spec.defaults)
    assert record_claim(tmp_path, spec.strategy_id, freeze_claim(spec, params)) is True
    assert record_claim(tmp_path, spec.strategy_id, freeze_claim(spec, params)) is False
    assert len(claims_for(tmp_path, spec.strategy_id)) == 1


def test_the_frozen_hash_does_not_depend_on_the_wall_clock(tmp_path: Path) -> None:
    """Otherwise every candidate fails G1 for the crime of time having passed."""
    _, spec = library_with_one(tmp_path)
    params = dict(spec.defaults)
    assert freeze_claim(spec, params).content_hash == freeze_claim(spec, params).content_hash
    assert freeze_claim(spec, params).frozen_at == FROZEN_AT


def test_the_claim_names_the_parameters_it_is_about(tmp_path: Path) -> None:
    """'This has an edge' and 'this has an edge here' are different claims."""
    _, spec = library_with_one(tmp_path)
    claim = freeze_claim(spec, dict(spec.defaults))
    assert isinstance(claim, Preregistration)
    assert "Evaluated at" in claim.falsification
    for name in spec.defaults:
        assert name in claim.falsification


def test_unreadable_claims_read_as_absent(tmp_path: Path) -> None:
    _, spec = library_with_one(tmp_path)
    path = tmp_path / "data" / "preregistrations" / f"{spec.strategy_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ truncated", encoding="utf-8")
    assert claim_holds(tmp_path, spec.strategy_id, spec, dict(spec.defaults)) is None


def test_a_traversing_strategy_id_is_refused(tmp_path: Path) -> None:
    assert claims_for(tmp_path, "../../etc/passwd") == []


def test_g1_distinguishes_a_moved_claim_from_no_claim() -> None:
    assert gate(judge_with(preregistered=None), "G1").status == "INCONCLUSIVE"
    assert gate(judge_with(preregistered=False), "G1").observed == "CLAIM_MOVED"
    assert gate(judge_with(preregistered=True), "G1").observed == "CLAIM_HELD"


# ── the whole class ──────────────────────────────────────────────────────────


def test_no_gate_input_defaults_to_a_pass() -> None:
    """The regression guard for every gate that used to be unfailable.

    G0, G1, G2, G7 and G9 all reached the judge as booleans that were either
    defaulted or literalled to True. A bool cannot express "nobody measured
    this", which is why four gates could not fail and one could not fail on the
    operator's path.
    """
    defaults = {field.name: field.default for field in fields(JudgeInput)}
    for name in (
        "implementation_tests_passed",
        "engine_consistent",
        "mechanism_aligned",
    ):
        assert defaults[name] is None, f"{name} must default to absent, not to a pass"

    annotations = {field.name: field.type for field in fields(JudgeInput)}
    for name in ("data_gate_passed", "preregistered"):
        assert "None" in str(annotations[name]), f"{name} must be able to express absence"


def test_every_gate_can_be_inconclusive_except_the_purely_computed_ones() -> None:
    """G3, G4, G6, G8 and G10 are computed from the P&L; they cannot be absent."""
    verdict = Judge().evaluate(
        JudgeInput(
            run_id="bare",
            tier="TRUTH_OOS",
            pnl=tuple(float(v) for v in (80, -25, 95, -30, 70, -20, 110, -35, 60, 45) * 4),
            trial_count=1,
            data_gate_passed=None,
            preregistered=None,
        )
    )
    inconclusive = {g.gate for g in verdict.gates if g.status == "INCONCLUSIVE"}
    assert {"G0", "G1", "G2", "G7", "G9", "G12", "G13"} <= inconclusive
    assert verdict.decision == "INCONCLUSIVE"
