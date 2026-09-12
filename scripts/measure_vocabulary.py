"""How large is the construction vocabulary, and does the engine reach it?

The previous phase's finding was a number, not an impression: one bounded
120-cycle campaign exhausted the available construction space, and 78 of its 81
refusals were `SAME_CONSTRUCTION`. This script is what produced the number, and
it is in the repository rather than in a report so the claim can be re-run
rather than believed.

    uv run python scripts/measure_vocabulary.py
    uv run python scripts/measure_vocabulary.py --cycles 400 --bars 12000 --json

Three measurements, and they answer different questions.

**Reach** is how many structurally distinct constructions exist to be found. It
is counted from the vocabulary itself, so it is an upper bound on what any
campaign could do rather than a claim about what one did.

**Draw** is what a bounded campaign actually proposes: how many draws land on
something new, how many collide, and how fast the collision rate climbs. This is
the measurement that showed the old engine saturating, and it is the one that
has to change for the expansion to mean anything.

**Execution** is whether the drawn constructions are strategies. A vocabulary
that reaches a million signals none of which compile, export, or take a trade is
a larger number and not a larger vocabulary, so every draw here is composed,
compiled, passed through the static guard and backtested.

Nothing here promotes anything, writes to a store, or calls a model. It measures.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from forge.research.grammar import (
    DrawConstraints,
    draw,
    vocabulary_summary,
)
from forge.research.mechanisms import MECHANISMS
from forge.research.synthesis import (
    ARCHETYPES,
    EXIT_STYLES,
    SESSIONS,
    compose,
    compose_construction,
)
from forge.strategy.export import to_python
from forge.strategy.guard import GuardViolation, check_source
from forge.strategy.ir import compile_definition
from forge.strategy.models import StrategySpec
from forge.strategy.primitives import CATALOGUE, SERIES_KINDS
from forge.strategy.runtime import run_backtest
from forge.strategy.synthetic import generate_bars

#: The most warm-up bars a measured construction may need, matching the
#: director's own budget. A construction needing more is one a campaign would
#: refuse to draw, so counting it here would measure something nobody runs.
MAX_WARMUP = 800


@dataclass
class Tally:
    """What one bounded run of draws produced."""

    drawn: int = 0
    unique_signatures: int = 0
    collisions: int = 0
    exhausted_at: int | None = None
    composed: int = 0
    compiled: int = 0
    guard_failures: int = 0
    compose_failures: int = 0
    over_warmup: int = 0
    backtested: int = 0
    traded: int = 0
    silent: int = 0
    trades: list[int] = field(default_factory=list)
    shapes: Counter[str] = field(default_factory=Counter)
    mechanisms: Counter[str] = field(default_factory=Counter)
    feature_kinds: Counter[str] = field(default_factory=Counter)
    families: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        ordered = sorted(self.trades)
        return {
            "drawn": self.drawn,
            "unique_signatures": self.unique_signatures,
            "collisions": self.collisions,
            "collision_rate": round(self.collisions / self.drawn, 4) if self.drawn else 0.0,
            "exhausted_at": self.exhausted_at,
            "composed": self.composed,
            "compiled": self.compiled,
            "compose_failures": self.compose_failures,
            "guard_failures": self.guard_failures,
            "over_warmup": self.over_warmup,
            "backtested": self.backtested,
            "traded": self.traded,
            "silent": self.silent,
            "median_trades": ordered[len(ordered) // 2] if ordered else 0,
            "distinct_shapes": len(self.shapes),
            "distinct_mechanisms": len(self.mechanisms),
            "distinct_feature_kinds": len(self.feature_kinds),
            "distinct_families": len(self.families),
            "shapes": dict(self.shapes),
            "mechanisms": dict(self.mechanisms),
            "families": dict(self.families),
            "seconds": round(self.seconds, 2),
        }


def archetype_reach() -> dict[str, Any]:
    """What the written archetypes alone can reach.

    This is the *before*. Every free choice the old composer had is enumerated:
    ten archetypes, three directions, five session options including none, four
    exit styles. The entry condition is a constant per archetype, so the last
    row is the one that mattered.
    """
    hashes: set[str] = set()
    entry_signatures: set[frozenset[str]] = set()
    for archetype in ARCHETYPES.values():
        entry_signatures.add(archetype.signature())
        for direction in ("both", "long", "short"):
            for session in ("", *SESSIONS):
                for style in EXIT_STYLES:
                    composition = compose(
                        archetype=archetype,
                        seed=0,
                        direction=direction,
                        session=session,
                        exit_style=style,
                    )
                    hashes.add(composition.definition.definition_hash)
    return {
        "archetypes": len(ARCHETYPES),
        "directions": 3,
        "sessions": len(SESSIONS) + 1,
        "exit_styles": len(EXIT_STYLES),
        "enumerated_definition_hashes": len(hashes),
        "distinct_entry_signatures": len(entry_signatures),
    }


def _spec_for(definition: Any, index: int) -> StrategySpec:
    return StrategySpec(
        strategy_id=f"measure_{index}",
        name=definition.name[:120],
        lineage="measure",
        family=definition.family,
        market=definition.market,
        symbol=definition.symbol,
        bar_spec=definition.timeframe,
        template="ir:measure",
        hypothesis=definition.hypothesis,
        falsifiable_prediction=definition.falsifiable_prediction,
        parameters=definition.parameters,
        warmup_bars=definition.required_warmup(),
        commission_per_side=definition.execution.commission_per_side,
        slippage_ticks=definition.execution.slippage_ticks,
        created_at=datetime.now(UTC),
        created_by="measure",
    )


def run_draws(cycles: int, bars_count: int, seed: int, *, execute: bool) -> Tally:
    """Draw `cycles` constructions the way a campaign would, and see what happens.

    Each draw excludes every structural signature already produced, which is
    exactly what the director does. A campaign that saturates therefore shows up
    here as a rising collision rate and eventually as an exhausted draw, rather
    than as a suspicion.
    """
    tally = Tally()
    rng = random.Random(seed)
    bars = generate_bars(symbol="MNQ", count=bars_count, seed=seed + 1) if execute else []
    seen: set[str] = set()
    started = time.perf_counter()

    for index in range(cycles):
        spec = draw(rng, DrawConstraints(exclude_signatures=frozenset(seen)))
        tally.drawn += 1
        if spec is None:
            tally.exhausted_at = index
            break
        if spec.signature in seen:
            tally.collisions += 1
            continue
        seen.add(spec.signature)
        tally.shapes[spec.shape] += 1

        try:
            composition = compose_construction(spec, seed=index, symbol="MNQ")
        except Exception:
            tally.compose_failures += 1
            continue
        tally.composed += 1
        definition = composition.definition
        tally.mechanisms[composition.mechanism] += 1
        tally.families[definition.family] += 1
        for item in definition.features:
            tally.feature_kinds[item.kind] += 1

        try:
            module = compile_definition(definition)
            check_source(to_python(definition).code)
        except (GuardViolation, ValueError):
            tally.guard_failures += 1
            continue
        tally.compiled += 1

        if not execute:
            continue
        warmup = definition.required_warmup()
        if warmup > MAX_WARMUP or warmup + 200 > len(bars):
            tally.over_warmup += 1
            continue
        result = run_backtest(module, _spec_for(definition, index), bars, code_hash="measure")
        tally.backtested += 1
        tally.trades.append(len(result.trades))
        if result.trades:
            tally.traded += 1
        else:
            tally.silent += 1

    tally.unique_signatures = len(seen)
    tally.seconds = time.perf_counter() - started
    return tally


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=120, help="draws to make")
    parser.add_argument("--bars", type=int, default=9000, help="bars each backtest runs over")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--no-execute",
        action="store_true",
        help="count constructions without composing or backtesting them",
    )
    parser.add_argument("--json", action="store_true", help="emit the measurements as JSON")
    args = parser.parse_args(argv)

    before = archetype_reach()
    after = vocabulary_summary()
    tally = run_draws(args.cycles, args.bars, args.seed, execute=not args.no_execute)

    payload = {
        "primitives": {
            "feature_kinds": len(CATALOGUE),
            "observations": len(CATALOGUE) - len(SERIES_KINDS),
            "transformations": len(SERIES_KINDS),
        },
        "mechanisms": len(MECHANISMS),
        "before_written_archetypes_only": before,
        "after_assembled_grammar": after,
        "draws": tally.as_dict(),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("CONSTRUCTION VOCABULARY")
    print("=" * 72)
    print(
        f"feature primitives   {payload['primitives']['feature_kinds']:>8} "
        f"({payload['primitives']['observations']} observations, "
        f"{payload['primitives']['transformations']} transformations)"
    )
    print(f"mechanisms           {payload['mechanisms']:>8}")
    print()
    print("BEFORE — the ten written archetypes, every free choice enumerated")
    for key, value in before.items():
        print(f"  {key:<34} {value:>10,}")
    print()
    print("AFTER — the assembled grammar")
    for key, value in after.items():
        if isinstance(value, dict | list):
            continue
        print(f"  {key:<34} {value:>10,}")
    print(f"  {'triggers per shape':<34} {after['triggers_per_shape']}")
    print()
    print(f"DRAWS — {args.cycles} cycles, each excluding every signature already produced")
    for key, value in tally.as_dict().items():
        if isinstance(value, dict):
            continue
        shown = "not reached" if value is None else f"{value:>10}"
        print(f"  {key:<34} {shown:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
