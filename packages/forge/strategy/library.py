from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import re
import shutil
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from forge.contracts.hashing import content_hash
from forge.strategy.guard import assert_safe
from forge.strategy.ir import StrategyDefinition
from forge.strategy.models import StrategySpec
from forge.strategy.templates import TEMPLATES, TEST_TEMPLATE

SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    return SLUG.sub("_", value.strip().lower()).strip("_") or "strategy"


class StrategyLibrary:
    """Strategies live on disk as readable, editable, version-controllable files.

    strategies/<strategy_id>/
        spec.json        the frozen declaration (hypothesis, params, costs)
        strategy.py      the code that actually runs
        test_strategy.py its own conformance suite, including a lookahead trap
        definition.json  the Strategy IR, when the strategy came from one

    **Where `definition.json` exists, it is the source of truth and
    `strategy.py` is its rendering.** That is not a bookkeeping distinction: the
    generated module is what gets executed, imported, hashed and guarded exactly
    like a hand-written one, so nothing downstream needs to know which kind it
    is holding. What the definition adds is that AlgoForge can also *answer
    questions* about the strategy — where its stop is, what its session window
    was, what it would look like in another language — which a function body
    cannot be asked.

    Creation checks the two agree. `create_from_definition` runs the compiled IR
    and the generated Python over the same bars and compares the ledgers, and
    refuses to write anything if they differ. A rendering that says something
    other than the definition would be the worst of both: an artifact whose
    provenance points at an IR that does not describe it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ── paths ────────────────────────────────────────────────────────────────
    def dir_for(self, strategy_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", strategy_id):
            raise KeyError("invalid strategy ID")
        folder = (self.root / strategy_id).resolve()
        if folder.parent != self.root.resolve():
            raise KeyError("strategy path leaves the library")
        return folder

    def source_path(self, strategy_id: str) -> Path:
        return self.dir_for(strategy_id) / "strategy.py"

    def spec_path(self, strategy_id: str) -> Path:
        return self.dir_for(strategy_id) / "spec.json"

    def test_path(self, strategy_id: str) -> Path:
        return self.dir_for(strategy_id) / "test_strategy.py"

    def definition_path(self, strategy_id: str) -> Path:
        return self.dir_for(strategy_id) / "definition.json"

    # ── the Strategy IR, where one exists ────────────────────────────────────
    def has_definition(self, strategy_id: str) -> bool:
        return self.definition_path(strategy_id).exists()

    def get_definition(self, strategy_id: str) -> StrategyDefinition:
        path = self.definition_path(strategy_id)
        if not path.exists():
            raise KeyError(f"strategy '{strategy_id}' was not built from a definition")
        return StrategyDefinition.model_validate_json(path.read_text(encoding="utf-8"))

    def create_from_definition(
        self,
        definition: StrategyDefinition,
        *,
        name: str | None = None,
        symbol: str | None = None,
        created_by: str = "operator",
        research_sources: tuple[str, ...] = (),
        verify_bars: Sequence[Any] | None = None,
    ) -> StrategySpec:
        """Write a strategy whose canonical form is the definition.

        `verify_bars` is the checking step and is not optional in spirit: when
        bars are supplied the compiled IR and the generated Python are both run
        over them and their ledgers compared, and a mismatch refuses the write.
        Callers that genuinely have no bars (a unit test constructing a spec)
        may omit it, and then the agreement is unchecked and stated as such.
        """
        from forge.strategy.export import to_python, verify_python
        from forge.strategy.ir import validate_definition

        definition = validate_definition(definition)
        display = name or definition.name
        base = slugify(display)
        strategy_id, n = f"{base}_{uuid.uuid4().hex[:10]}", 2
        while self.dir_for(strategy_id).exists():
            strategy_id, n = f"{base}_v{n}", n + 1

        spec = StrategySpec(
            strategy_id=strategy_id,
            name=display,
            lineage=base,
            family=definition.family,
            market=definition.market,
            symbol=symbol or definition.symbol,
            bar_spec=definition.timeframe,
            # Named for the definition it renders, so a reader of the spec alone
            # can tell that `strategy.py` was generated rather than written.
            template=f"ir:{definition.definition_id}",
            hypothesis=definition.hypothesis,
            falsifiable_prediction=definition.falsifiable_prediction,
            parameters=definition.parameters,
            warmup_bars=definition.required_warmup(),
            commission_per_side=definition.execution.commission_per_side,
            slippage_ticks=definition.execution.slippage_ticks,
            created_at=datetime.now(UTC),
            created_by=created_by,
            research_sources=research_sources,
        )

        report = to_python(definition)
        # The generated module passes the same static guard as anything else
        # that is executed here. Generated is not the same as trusted.
        assert_safe(report.code)

        if verify_bars is not None:
            check = verify_python(definition, list(verify_bars), spec)
            if not check["verified"]:
                raise ValueError(
                    "the generated Python does not reproduce the definition "
                    f"({check['reason']}). Nothing was written: an artifact whose "
                    "provenance points at a definition that does not describe it "
                    "would be worse than no strategy at all."
                )

        folder = self.dir_for(strategy_id)
        folder.mkdir(parents=True)
        self.definition_path(strategy_id).write_text(
            json.dumps(definition.model_dump(mode="json"), indent=2), encoding="utf-8"
        )
        self.spec_path(strategy_id).write_text(
            json.dumps(spec.model_dump(mode="json"), indent=2), encoding="utf-8"
        )
        self.source_path(strategy_id).write_text(report.code, encoding="utf-8")
        params_literal = json.dumps(spec.defaults, indent=4)
        self.test_path(strategy_id).write_text(
            TEST_TEMPLATE.format(name=display)
            + f"\n\nPARAMS = {params_literal}\n"
            + "\nfrom strategy import entry_signal, exit_signal  # noqa: E402,F401\n",
            encoding="utf-8",
        )
        return spec

    # ── create ───────────────────────────────────────────────────────────────
    def create_from_template(
        self,
        template_key: str,
        *,
        name: str | None = None,
        symbol: str = "MNQ.CME",
        market: str = "futures",
        bar_spec: str = "1m",
        created_by: str = "operator",
        parameters: dict[str, float] | None = None,
        research_sources: tuple[str, ...] = (),
        hypothesis: str | None = None,
    ) -> StrategySpec:
        template = TEMPLATES.get(template_key)
        if template is None:
            raise KeyError(f"unknown template '{template_key}'")

        display = name or template.name
        base = slugify(display)
        strategy_id, n = f"{base}_{uuid.uuid4().hex[:10]}", 2
        while self.dir_for(strategy_id).exists():
            strategy_id, n = f"{base}_v{n}", n + 1

        spec = StrategySpec(
            strategy_id=strategy_id,
            name=display,
            lineage=base,
            family=template.family,
            market=market,  # type: ignore[arg-type]
            symbol=symbol,
            bar_spec=bar_spec,
            template=template.key,
            hypothesis=hypothesis or template.hypothesis,
            falsifiable_prediction=template.falsifiable_prediction,
            parameters=tuple(
                p.model_copy(update={"default": parameters[p.name]})
                if parameters and p.name in parameters
                else p
                for p in template.parameters
            ),
            warmup_bars=template.warmup_bars,
            created_at=datetime.now(UTC),
            created_by=created_by,
            research_sources=research_sources,
            adaptation_note="Research adaptation; not a verified paper replication."
            if research_sources
            else "",
        )

        assert_safe(template.source)  # never write code that would be refused at run time

        folder = self.dir_for(strategy_id)
        folder.mkdir(parents=True)
        self.spec_path(strategy_id).write_text(
            json.dumps(spec.model_dump(mode="json"), indent=2), encoding="utf-8"
        )
        self.source_path(strategy_id).write_text(template.source, encoding="utf-8")
        params_literal = json.dumps(spec.defaults, indent=4)
        self.test_path(strategy_id).write_text(
            TEST_TEMPLATE.format(name=display)
            + f"\n\nPARAMS = {params_literal}\n"
            + "\nfrom strategy import entry_signal, exit_signal  # noqa: E402,F401\n",
            encoding="utf-8",
        )
        return spec

    # ── read ─────────────────────────────────────────────────────────────────
    def list_specs(self) -> list[StrategySpec]:
        specs: list[StrategySpec] = []
        for path in sorted(self.root.glob("*/spec.json")):
            try:
                specs.append(StrategySpec.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:  # a hand-edited spec should not break the whole listing
                continue
        return sorted(specs, key=lambda s: s.created_at, reverse=True)

    def get_spec(self, strategy_id: str) -> StrategySpec:
        path = self.spec_path(strategy_id)
        if not path.exists():
            raise KeyError(strategy_id)
        return StrategySpec.model_validate_json(path.read_text(encoding="utf-8"))

    def get_source(self, strategy_id: str) -> str:
        return self.source_path(strategy_id).read_text(encoding="utf-8")

    def get_tests(self, strategy_id: str) -> str:
        path = self.test_path(strategy_id)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def code_hash(self, strategy_id: str) -> str:
        return content_hash({"source": self.get_source(strategy_id)})

    def write_source(self, strategy_id: str, source: str) -> None:
        """Hand edits are allowed, but they pass the same guard as generated code."""
        assert_safe(source)
        self.source_path(strategy_id).write_text(source, encoding="utf-8")

    def delete(self, strategy_id: str) -> None:
        folder = self.dir_for(strategy_id)
        if not folder.exists():
            raise KeyError(strategy_id)
        # Drop the imported module first. On Windows the loader keeps a handle on
        # strategy.py, and unlinking it while imported raises "Access is denied".
        self.unload_module(strategy_id)
        # rmtree, not unlink: the loader leaves a __pycache__ directory behind.
        for attempt in range(4):
            try:
                shutil.rmtree(folder)
                return
            except PermissionError:
                if attempt == 3:
                    raise
                gc.collect()
                time.sleep(0.1)

    def unload_module(self, strategy_id: str) -> None:
        sys.modules.pop(f"algoforge_strategy_{strategy_id}", None)
        importlib.invalidate_caches()

    # ── load ─────────────────────────────────────────────────────────────────
    def load_module(self, strategy_id: str) -> ModuleType:
        source = self.get_source(strategy_id)
        assert_safe(source)
        module_name = f"algoforge_strategy_{strategy_id}"
        spec = importlib.util.spec_from_file_location(module_name, self.source_path(strategy_id))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load strategy '{strategy_id}'")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
