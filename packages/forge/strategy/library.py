from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from forge.contracts.hashing import content_hash
from forge.strategy.guard import assert_safe
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
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ── paths ────────────────────────────────────────────────────────────────
    def dir_for(self, strategy_id: str) -> Path:
        return self.root / strategy_id

    def source_path(self, strategy_id: str) -> Path:
        return self.dir_for(strategy_id) / "strategy.py"

    def spec_path(self, strategy_id: str) -> Path:
        return self.dir_for(strategy_id) / "spec.json"

    def test_path(self, strategy_id: str) -> Path:
        return self.dir_for(strategy_id) / "test_strategy.py"

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
    ) -> StrategySpec:
        template = TEMPLATES.get(template_key)
        if template is None:
            raise KeyError(f"unknown template '{template_key}'")

        display = name or template.name
        base = slugify(display)
        strategy_id, n = base, 2
        while self.dir_for(strategy_id).exists():
            strategy_id, n = f"{base}_v{n}", n + 1

        spec = StrategySpec(
            strategy_id=strategy_id,
            name=display,
            lineage=base,
            family=template.family,  # type: ignore[arg-type]
            market=market,  # type: ignore[arg-type]
            symbol=symbol,
            bar_spec=bar_spec,
            template=template.key,
            hypothesis=template.hypothesis,
            falsifiable_prediction=template.falsifiable_prediction,
            parameters=template.parameters,
            warmup_bars=template.warmup_bars,
            created_at=datetime.now(UTC),
            created_by=created_by,
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
        for child in folder.iterdir():
            child.unlink()
        folder.rmdir()

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
