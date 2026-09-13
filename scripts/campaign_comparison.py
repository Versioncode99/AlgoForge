"""Three bounded campaigns through the real engine, compared.

`scripts/measure_vocabulary.py` measures the vocabulary. This measures what a
campaign does with it, which is a different question: a larger reachable set is
worth nothing if the engine still proposes the same handful of things and the
novelty gate still refuses them.

So this drives the **real** director, the real novelty gate, the real template
store with its static guard and smoke test, and the real backtest, over the
seeded edge-free synthetic series — and reports the numbers the previous phase's
finding was stated in.

    uv run python scripts/campaign_comparison.py
    uv run python scripts/campaign_comparison.py --cycles 120 --json

Three configurations, and the comparison between them is the point:

**A — baseline.** The ten written archetypes only, which is what the engine had
before this phase. This is the arm that saturated.

**B — expanded.** The assembled grammar at its configured share.

**C — throughput.** The same vocabulary with the assembled share at one, which
is the fastest the construction space can be explored.

What is measured, per arm: unique structural constructions, the share of
proposals refused as duplicates and at which novelty band, hypotheses and
mechanisms admitted, frontier movement, and wall clock.

**On the dataset.** Synthetic bars cannot clear the judge's G0 data gate, so no
candidate in any arm can be validated and none is expected to be. This measures
whether the engine *does research*, not whether it finds an edge — and a run
here that reported a promotion would be evidence of a bug.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.research import ResearchLedger
from forge.research.frontier import FrontierState
from forge.strategy import TEMPLATES, FamilyRegistry, StrategyLibrary, TemplateStore
from forge.vault import Workspace
from forge_api import director as director_module
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.campaigns import CampaignService
from forge_api.director import ResearchDirector
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import MarketService

OBJECTIVE = (
    "Discover intraday alpha on NQ one-minute bars across the full available history, "
    "preferring mechanisms that can be stated and falsified."
)


@dataclass
class Arm:
    """One configuration, and what it is meant to answer."""

    key: str
    label: str
    detail: str
    assembled_share: float


ARMS: tuple[Arm, ...] = (
    Arm(
        "A_baseline",
        "Baseline",
        "The ten written archetypes only — the engine as it was before this phase.",
        0.0,
    ),
    Arm(
        "B_expanded",
        "Expanded",
        "The assembled grammar at the director's configured share.",
        director_module.ASSEMBLED_SHARE,
    ),
    Arm(
        "C_throughput",
        "Throughput",
        "Assembled constructions only — the fastest the space can be explored.",
        1.0,
    ),
)


@dataclass
class Result:
    arm: str
    cycles: int
    seconds: float = 0.0
    experiments: int = 0
    hypotheses: int = 0
    mechanisms: int = 0
    families_created: int = 0
    templates_created: int = 0
    duplicates_rejected: int = 0
    blocked: int = 0
    followups: int = 0
    sources: int = 0
    unique_constructions: int = 0
    assembled_constructions: int = 0
    written_constructions: int = 0
    frontier: dict[str, int] = field(default_factory=dict)
    skip_bands: dict[str, int] = field(default_factory=dict)
    skip_kinds: dict[str, int] = field(default_factory=dict)
    errors: int = 0

    @property
    def novelty_rate(self) -> float:
        """Share of proposals that were admitted rather than refused as duplicates."""
        total = self.experiments + self.duplicates_rejected
        return round(self.experiments / total, 4) if total else 0.0

    @property
    def same_construction_rate(self) -> float:
        """The number the previous phase's finding was stated in: 78 of 81."""
        total = sum(self.skip_bands.values())
        return (
            round(self.skip_bands.get("SAME_CONSTRUCTION", 0) / total, 4) if total else 0.0
        )

    @property
    def constructions_per_cycle(self) -> float:
        return round(self.unique_constructions / self.cycles, 4) if self.cycles else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "cycles": self.cycles,
            "seconds": round(self.seconds, 2),
            "seconds_per_cycle": round(self.seconds / self.cycles, 3) if self.cycles else 0.0,
            "experiments": self.experiments,
            "hypotheses": self.hypotheses,
            "distinct_mechanisms": self.mechanisms,
            "families_created": self.families_created,
            "templates_created": self.templates_created,
            "duplicates_rejected": self.duplicates_rejected,
            "blocked_proposals": self.blocked,
            "followups_generated": self.followups,
            "sources_retrieved": self.sources,
            "unique_constructions": self.unique_constructions,
            "assembled_constructions": self.assembled_constructions,
            "written_constructions": self.written_constructions,
            "constructions_per_cycle": self.constructions_per_cycle,
            "novelty_rate": self.novelty_rate,
            "same_construction_rate": self.same_construction_rate,
            "frontier": self.frontier,
            "skip_bands": self.skip_bands,
            "skip_kinds": self.skip_kinds,
            "cycle_errors": self.errors,
        }


def _build(root: Path, bars: int) -> tuple[AutonomousEngine, CampaignService]:
    shutil.copytree(Path("rules"), root / "rules", dirs_exist_ok=True)
    workspace = Workspace(repo=root, root=root, vault_mode=False).ensure()
    log = ActivityLog(root / "activity.ndjson")
    engine = AutonomousEngine(
        StrategyLibrary(root / "strategies"),
        BacktestStore(root / "backtests"),
        log,
        MarketService(Path(".")),
        workspace,
        ResearchLedger(root / "research.db"),
    )
    service = CampaignService(workspace.data, log=log)
    service.director = ResearchDirector(
        campaigns=service.campaigns,
        frontier=service.frontier,
        hypotheses=service.hypotheses,
        journal=service.journal,
        sources=service.sources,
        promotion=service.promotion,
        families=FamilyRegistry(root / "families"),
        templates=TemplateStore(root / "templates"),
        log=log,
    )
    engine.director = service.director
    engine.state.config = EngineConfig(dataset="synthetic", max_bars=bars, workers=1)
    return engine, service


def run_arm(arm: Arm, *, cycles: int, bars: int, seed: int) -> Result:
    """One arm, in its own temporary installation, with its own template catalogue."""
    result = Result(arm=arm.key, cycles=cycles)
    root = Path(tempfile.mkdtemp(prefix=f"algoforge-{arm.key}-"))
    shipped = dict(TEMPLATES)
    original_share = director_module.ASSEMBLED_SHARE
    director_module.ASSEMBLED_SHARE = arm.assembled_share
    try:
        engine, service = _build(root, bars)
        campaign = service.campaigns.create(
            name=f"Comparison {arm.label}",
            objective=OBJECTIVE,
            dataset="synthetic",
            symbol="MNQ",
            stopping={"max_experiments": cycles + 50},
        )
        service.campaigns.set_status(campaign.campaign_id, "running")
        campaign = service.campaigns.get(campaign.campaign_id)
        assert campaign is not None
        service.director.attach(campaign)

        loaded, dataset = engine.market.load("synthetic", limit=bars)
        rng = random.Random(seed)
        started = time.perf_counter()
        for _ in range(cycles):
            try:
                engine._cycle(rng, list(loaded), dataset.is_real, worker=0)
            except Exception as exc:
                result.errors += 1
                print(f"  [{arm.key}] cycle error: {type(exc).__name__}: {exc}", file=sys.stderr)
        result.seconds = time.perf_counter() - started

        final = service.campaigns.get(campaign.campaign_id)
        assert final is not None
        progress = final.progress
        result.experiments = progress.experiments
        result.hypotheses = progress.hypotheses
        result.families_created = progress.families_created
        result.templates_created = progress.templates_created
        result.duplicates_rejected = progress.duplicates_rejected
        result.blocked = progress.blocked_proposals
        result.followups = progress.followups_generated
        result.sources = progress.sources_retrieved
        result.mechanisms = service.hypotheses.distinct_mechanisms(campaign.campaign_id)

        signatures: set[str] = set()
        assembled = 0
        for record in service.director.generated_templates().values():
            signature = str(record.get("structural_signature") or record.get("archetype") or "")
            if signature:
                signatures.add(signature)
            if record.get("construction"):
                assembled += 1
        result.unique_constructions = len(signatures)
        result.assembled_constructions = assembled
        result.written_constructions = len(signatures) - assembled

        result.frontier = {
            str(state): count
            for state, count in service.frontier.counts(campaign.campaign_id).items()
            if count
        }
        bands: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        for row in service.skips.list(campaign.campaign_id, limit=5000):
            bands[str(row.get("level") or "")] += int(row.get("occurrences") or 1)
            kinds[str(row.get("kind") or "")] += int(row.get("occurrences") or 1)
        result.skip_bands = {k: v for k, v in bands.items() if k}
        result.skip_kinds = {k: v for k, v in kinds.items() if k}
        return result
    finally:
        director_module.ASSEMBLED_SHARE = original_share
        TEMPLATES.clear()
        TEMPLATES.update(shipped)
        shutil.rmtree(root, ignore_errors=True)


def _table(results: list[Result]) -> str:
    rows = [
        ("unique constructions", lambda r: r.unique_constructions),
        ("  of which assembled", lambda r: r.assembled_constructions),
        ("constructions / cycle", lambda r: r.constructions_per_cycle),
        ("experiments", lambda r: r.experiments),
        ("hypotheses", lambda r: r.hypotheses),
        ("distinct mechanisms", lambda r: r.mechanisms),
        ("templates created", lambda r: r.templates_created),
        ("duplicates rejected", lambda r: r.duplicates_rejected),
        ("novelty rate", lambda r: r.novelty_rate),
        ("SAME_CONSTRUCTION rate", lambda r: r.same_construction_rate),
        ("follow-ups generated", lambda r: r.followups),
        ("frontier items", lambda r: sum(r.frontier.values())),
        ("cycle errors", lambda r: r.errors),
        ("seconds", lambda r: round(r.seconds, 1)),
        ("seconds / cycle", lambda r: round(r.seconds / r.cycles, 2) if r.cycles else 0),
    ]
    width = max(len(name) for name, _ in rows) + 2
    header = "".ljust(width) + "".join(r.arm.ljust(16) for r in results)
    lines = [header, "-" * len(header)]
    for name, read in rows:
        lines.append(name.ljust(width) + "".join(str(read(r)).ljust(16) for r in results))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=40, help="research cycles per arm")
    parser.add_argument("--bars", type=int, default=30_000)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--arms", default="", help="comma-separated arm keys; default all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    wanted = {key.strip() for key in args.arms.split(",") if key.strip()}
    arms = [arm for arm in ARMS if not wanted or arm.key in wanted]
    results: list[Result] = []
    for arm in arms:
        print(f"running {arm.key}: {arm.detail}", file=sys.stderr)
        results.append(run_arm(arm, cycles=args.cycles, bars=args.bars, seed=args.seed))

    if args.json:
        print(
            json.dumps(
                {
                    "cycles": args.cycles,
                    "bars": args.bars,
                    "seed": args.seed,
                    "arms": [
                        {"key": a.key, "label": a.label, "detail": a.detail} for a in arms
                    ],
                    "results": [r.as_dict() for r in results],
                },
                indent=2,
            )
        )
        return 0

    print(f"\nBOUNDED CAMPAIGN COMPARISON — {args.cycles} cycles, {args.bars:,} bars\n")
    for arm in arms:
        print(f"  {arm.key:<14} {arm.detail}")
    print()
    print(_table(results))
    print()
    for result in results:
        if result.skip_bands:
            print(f"{result.arm} refusals by band: {result.skip_bands}")
    for result in results:
        if result.frontier:
            print(f"{result.arm} frontier: {result.frontier}")
    unreached = [r.arm for r in results if r.errors]
    if unreached:
        print(f"\ncycle errors in: {', '.join(unreached)} — see stderr", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["ARMS", "Arm", "FrontierState", "Result", "main", "run_arm"]
