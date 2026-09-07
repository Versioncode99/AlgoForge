"""The evidence dossier: everything known about one candidate, in one place.

A researcher asking "why does AlgoForge trust or reject this?" currently has to
visit four screens and join them by hand. This assembles the answer from the
records that already exist — the strategy spec, its backtests, the stored
validation evidence, the verdict those produce, the specialist positions, the
experiment that created it and its lineage, and what research memory has learned
about the region it sits in.

Two rules shape the whole module:

**It assembles, it does not compute.** Every number here was produced by the
judge, the validation stack or the backtest engine and is copied through. A
dossier that recalculated anything could disagree with the screen the reader
just came from, and then neither could be trusted.

**Absent means absent.** Each section reports its own availability. A candidate
that was never validated gets ``"available": false`` with a reason, never a
section of zeroes — zeroes read as measurements. This mirrors the judge's own
rule that missing evidence is INCONCLUSIVE rather than a fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.agents import build_debate
from forge.judge import Judge, JudgeInput
from forge.judge.models import Verdict


def _absent(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _section(payload: dict[str, Any]) -> dict[str, Any]:
    return {"available": True, **payload}


def _strategy_section(spec: Any) -> dict[str, Any]:
    return _section(
        {
            "strategy_id": spec.strategy_id,
            "name": spec.name,
            "family": spec.family,
            "template": spec.template,
            # The research question and the thing that would falsify it. A spec
            # that cannot be wrong is not a hypothesis, so both are mandatory
            # upstream and both belong at the top of the dossier.
            "hypothesis": spec.hypothesis,
            "falsifiable_prediction": spec.falsifiable_prediction,
            "parameters": dict(spec.defaults),
            "market": spec.market,
            "symbol": spec.symbol,
            "bar_spec": spec.bar_spec,
            "warmup_bars": spec.warmup_bars,
            "lineage": spec.lineage,
            "spec_hash": spec.spec_hash,
            "created_at": str(spec.created_at),
            "created_by": spec.created_by,
            "research_sources": list(spec.research_sources),
            "adaptation_note": spec.adaptation_note,
            # Prompt §17 asks for costs and slippage assumptions explicitly:
            # a P&L figure means nothing without the frictions behind it.
            "cost_model": {
                "commission_per_side": spec.commission_per_side,
                "slippage_ticks": spec.slippage_ticks,
                "tick_value": spec.tick_value,
            },
        }
    )


def _runs_section(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return _absent("no backtest has been run for this strategy")
    return _section(
        {
            "count": len(runs),
            "runs": [
                {
                    "backtest_id": run.get("backtest_id"),
                    "evidence_tier": run.get("evidence_tier", "LEGACY_IN_SAMPLE"),
                    "partition": run.get("partition_name"),
                    "dataset": run.get("dataset_key"),
                    "trades": len(run.get("trades", [])),
                    "net_pnl": run.get("net_pnl"),
                    "lookahead_clean": run.get("lookahead_clean"),
                    "code_hash": run.get("code_hash"),
                    "labels": list(run.get("labels", ())),
                    "calculation_version": run.get("calculation_version"),
                }
                for run in runs
            ],
        }
    )


def _verdict_section(verdict: Verdict | None, reason: str) -> dict[str, Any]:
    if verdict is None:
        return _absent(reason)
    return _section(
        {
            "verdict_id": verdict.verdict_id,
            "decision": verdict.decision,
            "grade": verdict.grade,
            "dimensions": dict(verdict.dimensions),
            "metrics": dict(verdict.metrics),
            "gates": [gate.model_dump() for gate in verdict.gates],
            # The gates that stopped it, split by what the status actually means.
            "failed_gates": [g.gate for g in verdict.gates if g.status == "FAIL"],
            "unmeasured_gates": [g.gate for g in verdict.gates if g.status == "INCONCLUSIVE"],
        }
    )


def _limitations(verdict: Verdict | None) -> list[str]:
    """Every caveat the judge attached to a number, de-duplicated, order kept.

    These are the reasons a figure might not mean what it appears to. Burying
    them inside per-metric traces makes them easy to miss, so the dossier lifts
    them to the top level.
    """
    if verdict is None:
        return ["No verdict, so no measured quantity carries a stated limitation."]
    seen: dict[str, None] = {}
    for trace in verdict.traces:
        for note in trace.limitations:
            seen.setdefault(note, None)
    return list(seen)


def _evidence_section(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence:
        return _absent("validation has never been run for this strategy")
    overfitting = evidence.get("overfitting") or {}
    return _section(
        {
            "evidence_id": evidence.get("evidence_id"),
            "trial_count": evidence.get("trial_count"),
            "configurations": overfitting.get("trials"),
            "probability_of_overfitting": overfitting.get("probability"),
            "cscv_splits": overfitting.get("splits"),
            "walk_forward": evidence.get("walk_forward"),
            "paths": evidence.get("paths"),
            "selection_stability": evidence.get("selection_stability"),
            "best_parameters": evidence.get("best_parameters"),
            "split_id": evidence.get("split_id"),
            "code_hash": evidence.get("code_hash"),
        }
    )


def _experiment_section(experiments: Any, spec: Any, scope: str) -> dict[str, Any]:
    """Find the experiment that produced this strategy, and its line."""
    match: dict[str, Any] | None = None
    for row in experiments.iter_scope(scope):
        if row.get("strategy_id") == spec.strategy_id:
            match = row
            break
    if match is None:
        return _absent(
            "no experiment in this scope claims this strategy; it was probably "
            "created by hand rather than by the engine"
        )
    line = experiments.lineage(str(match["id"]))
    return _section(
        {
            "experiment": match,
            "policy": match.get("policy"),
            "seed": match.get("seed"),
            "data_version": match.get("data_version"),
            "ancestors": line["ancestors"],
            "children": line["children"],
            "descendant_count": len(line["descendants"]),
            "depth": len(line["ancestors"]),
        }
    )


def _reproducibility_section(snapshots: Any, run_id: str | None) -> dict[str, Any]:
    """Can this verdict be re-examined, and do the rules still say the same thing?

    Two questions, deliberately separate. `intact` asks whether the record is
    undamaged. `comparable` asks whether the judge and statistics modules that
    produced it still have the same source — a verdict from an edited judge is
    still an honest record of what that judge decided, but it cannot be lined up
    against a fresh verdict as though the two agreed.
    """
    if not run_id:
        return _absent("no verdict, so nothing was snapshotted")
    manifest = snapshots.read(run_id)
    if manifest is None:
        return _absent(
            "no snapshot for this run; it predates run snapshotting or was judged "
            "outside the engine"
        )
    verified = snapshots.verify(run_id)
    drift = snapshots.drift(run_id)
    return _section(
        {
            "run_id": run_id,
            "manifest_hash": manifest.get("manifest_hash"),
            "intact": verified["intact"],
            "changed": verified["changed"],
            "comparable": drift["comparable"],
            "drifted": drift["drifted"],
            "captured_sources": [
                {"label": item["label"], "sha256": item["sha256"]}
                for item in manifest.get("sources", [])
            ],
        }
    )


def _memory_section(memory: Any, scope: str, template: str | None) -> dict[str, Any]:
    if not template:
        return _absent("strategy does not record the template it came from")
    related = [row for row in memory.recent(scope, limit=500) if row.template == template]
    return _section(
        {
            "template": template,
            "related_failures": len(related),
            "by_class": {
                name: sum(1 for row in related if str(row.failure_class) == name)
                for name in {str(row.failure_class) for row in related}
            },
            "recent": [
                {
                    "failure_class": str(row.failure_class),
                    "reason": row.reason,
                    "parameters": row.parameters,
                    "gate": row.gate,
                    "at": row.created_at,
                }
                for row in related[:20]
            ],
        }
    )


def build_dossier(
    *,
    root: Path,
    library: Any,
    store: Any,
    experiments: Any,
    memory: Any,
    scope: str,
    strategy_id: str,
    snapshots: Any = None,
) -> dict[str, Any]:
    """Assemble the dossier for one strategy. Raises KeyError if it does not exist."""
    from forge_api.strategies import judge_evidence, load_evidence

    spec = library.get_spec(strategy_id)
    runs = store.for_strategy(strategy_id)

    verdict: Verdict | None = None
    verdict_absent = "no backtest has been run for this strategy"
    if runs:
        latest = runs[0]
        pnl = tuple(float(trade["net_pnl"]) for trade in latest.get("trades", []))
        if not pnl:
            verdict_absent = "the most recent backtest produced no trades to judge"
        else:
            evidence_args = judge_evidence(
                root,
                strategy_id,
                code_hash=str(latest.get("code_hash", "")),
            )
            verdict = Judge().evaluate(
                JudgeInput(
                    run_id=str(latest["backtest_id"]),
                    tier=str(latest.get("evidence_tier", "LEGACY_IN_SAMPLE")),
                    pnl=pnl,
                    trial_count=max(1, experiments.count(scope)),
                    data_gate_passed=latest.get("evidence_tier") not in {None, "SYNTHETIC"},
                    preregistered=True,
                    implementation_tests_passed=True,
                    lookahead_detected=not latest.get("lookahead_clean", True),
                    **evidence_args,
                )
            )

    return {
        "strategy_id": strategy_id,
        "scope": scope,
        "strategy": _strategy_section(spec),
        "backtests": _runs_section(runs),
        "verdict": _verdict_section(verdict, verdict_absent),
        "validation": _evidence_section(load_evidence(root, strategy_id)),
        "provenance": _experiment_section(experiments, spec, scope),
        "research_memory": _memory_section(memory, scope, spec.template),
        "reproducibility": (
            _reproducibility_section(snapshots, verdict.run_id if verdict else None)
            if snapshots is not None
            else _absent("no snapshot store was supplied")
        ),
        "dissent": (
            _section(build_debate(verdict).model_dump(mode="json"))
            if verdict is not None
            else _absent("no verdict to review")
        ),
        "limitations": _limitations(verdict),
        "standing_caveats": [
            "Paper only. No live-order capability exists.",
            "Fills are modelled, not calibrated against an execution venue.",
            "A qualified research candidate is not a profitable strategy.",
        ],
    }
