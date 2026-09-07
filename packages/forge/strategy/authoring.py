"""Templates the operator and the agents can add at run time.

The twelve shipped templates are version-controlled Python. This module lets a
new one be written without a redeploy, on strictly the same terms:

* the source passes :func:`forge.strategy.guard.assert_safe` — no filesystem,
  network, subprocess or dynamic execution, and both required functions present;
* the parameters declare a real grid, so the engine's neighbourhood search and
  the memory constraints keep working;
* the template is **executed on synthetic bars before it is registered**. A
  template that raises, or that cannot be imported, never enters the catalogue.
  This is the difference between "an agent may write a template" and "an agent
  may crash the engine".

A registered template is exactly as capable as a built-in one — the engine
cannot tell them apart — and no more capable. It still sees only a right-bounded
`Window`, so it still cannot look ahead.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.strategy.guard import GuardViolation, assert_safe
from forge.strategy.models import ParameterSpec
from forge.strategy.templates import Template

KEY = re.compile(r"^[a-z][a-z0-9_]{2,49}$")

MAX_SOURCE_BYTES = 24_000
MAX_PARAMETERS = 10
SMOKE_BARS = 1_200


class TemplateRejected(Exception):
    """The template was not registered. The reason is the message."""


def _check_parameters(raw: list[dict[str, Any]]) -> tuple[ParameterSpec, ...]:
    if not raw:
        raise TemplateRejected(
            "A template needs at least one parameter. With none, the engine has no "
            "neighbourhood to search and every candidate is identical."
        )
    if len(raw) > MAX_PARAMETERS:
        raise TemplateRejected(f"At most {MAX_PARAMETERS} parameters; got {len(raw)}.")
    specs: list[ParameterSpec] = []
    seen: set[str] = set()
    for item in raw:
        try:
            spec = ParameterSpec(**item)
        except Exception as exc:
            raise TemplateRejected(f"Bad parameter definition: {exc}") from exc
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", spec.name):
            raise TemplateRejected(f"Parameter name '{spec.name}' is not a lowercase identifier.")
        if spec.name in seen:
            raise TemplateRejected(f"Duplicate parameter '{spec.name}'.")
        seen.add(spec.name)
        if spec.low >= spec.high:
            raise TemplateRejected(f"Parameter '{spec.name}': low must be below high.")
        if spec.step <= 0:
            raise TemplateRejected(f"Parameter '{spec.name}': step must be positive.")
        if not spec.low <= spec.default <= spec.high:
            raise TemplateRejected(f"Parameter '{spec.name}': default is outside its range.")
        steps = (spec.high - spec.low) / spec.step
        if steps < 1:
            raise TemplateRejected(
                f"Parameter '{spec.name}': the step is larger than the range, so the grid "
                "has one point and the parameter is not tunable."
            )
        if steps > 2_000:
            raise TemplateRejected(
                f"Parameter '{spec.name}': {steps:,.0f} grid points. Narrow the range or "
                "widen the step — a grid this large is a multiple-testing problem, not a search."
            )
        specs.append(spec)
    return tuple(specs)


class TemplateStore:
    """Persisted, guarded, smoke-tested templates."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.rejected_on_load: list[dict[str, str]] = []
        # What this store put into the shared catalogue, so a reload can take it
        # back out again.
        self._registered: set[str] = set()

    # ── files ────────────────────────────────────────────────────────────────
    def _meta_path(self, key: str) -> Path:
        return self.path / f"{key}.json"

    def _source_path(self, key: str) -> Path:
        return self.path / f"{key}.py"

    def keys(self) -> list[str]:
        return sorted(p.stem for p in self.path.glob("*.json"))

    # ── load ─────────────────────────────────────────────────────────────────
    def load_all(self, into: dict[str, Template]) -> list[Template]:
        """Register every stored template. Anything invalid is reported, not raised.

        A template that stops passing the guard — because the guard got stricter,
        or because someone edited the file — must not stop the application
        starting. It is dropped from the catalogue and listed as rejected.
        """
        loaded: list[Template] = []
        self.rejected_on_load = []
        # Deleting a template file, or pointing the app at a different workspace,
        # must remove it from the catalogue rather than leaving a ghost the
        # engine can still draw candidates from.
        for stale in self._registered - set(self.keys()):
            into.pop(stale, None)
        self._registered = set()
        for key in self.keys():
            try:
                template = self._read(key)
                assert_safe(template.source)
            except Exception as exc:
                self.rejected_on_load.append({"key": key, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            into[template.key] = template
            self._registered.add(template.key)
            loaded.append(template)
        return loaded

    def _read(self, key: str) -> Template:
        meta = json.loads(self._meta_path(key).read_text(encoding="utf-8"))
        source = self._source_path(key).read_text(encoding="utf-8")
        return Template(
            key=str(meta["key"]),
            name=str(meta["name"]),
            family=str(meta["family"]),
            hypothesis=str(meta["hypothesis"]),
            falsifiable_prediction=str(meta["falsifiable_prediction"]),
            parameters=tuple(ParameterSpec(**p) for p in meta["parameters"]),
            warmup_bars=int(meta.get("warmup_bars", 60)),
            source=source,
            data_requirement=str(meta.get("data_requirement", "BARS")),
            minimum_timeframe=str(meta.get("minimum_timeframe", "1m")),
            research_status=str(meta.get("research_status", "RUNNABLE")),
        )

    def metadata(self, key: str) -> dict[str, Any]:
        return dict(json.loads(self._meta_path(key).read_text(encoding="utf-8")))

    # ── create ───────────────────────────────────────────────────────────────
    def create(
        self,
        *,
        key: str,
        name: str,
        family: str,
        hypothesis: str,
        falsifiable_prediction: str,
        parameters: list[dict[str, Any]],
        source: str,
        warmup_bars: int = 60,
        known_families: set[str],
        existing_templates: set[str],
        created_by: str = "operator",
        research_sources: tuple[str, ...] = (),
    ) -> Template:
        key = key.strip().lower()
        if not KEY.fullmatch(key):
            raise TemplateRejected(
                "A template key is 3-50 characters of lowercase letters, digits and "
                f"underscores, starting with a letter. Got '{key}'."
            )
        if key in existing_templates:
            raise TemplateRejected(f"Template '{key}' already exists.")
        if family not in known_families:
            raise TemplateRejected(
                f"Unknown family '{family}'. Create the family first, or pick one of: "
                f"{', '.join(sorted(known_families))}."
            )
        if len(hypothesis.strip()) < 40:
            raise TemplateRejected(
                "The hypothesis must be at least 40 characters and state a mechanism. "
                "The judge's mechanism gate has nothing to test otherwise."
            )
        if len(falsifiable_prediction.strip()) < 30:
            raise TemplateRejected(
                "State what result would abandon this hypothesis, in at least 30 characters. "
                "A prediction that cannot fail is not a prediction."
            )
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise TemplateRejected(f"Source exceeds {MAX_SOURCE_BYTES:,} bytes.")
        if not 5 <= int(warmup_bars) <= 5_000:
            raise TemplateRejected("warmup_bars must be between 5 and 5,000.")

        specs = _check_parameters(parameters)

        try:
            assert_safe(source)
        except GuardViolation as exc:
            raise TemplateRejected(f"The static guard refused this source: {exc}") from exc

        template = Template(
            key=key,
            name=name.strip()[:120] or key,
            family=family,
            hypothesis=hypothesis.strip()[:4000],
            falsifiable_prediction=falsifiable_prediction.strip()[:4000],
            parameters=specs,
            warmup_bars=int(warmup_bars),
            source=source,
            research_status="RUNNABLE",
        )

        report = self.smoke_test(template)
        if not report["ok"]:
            raise TemplateRejected(f"The template failed its smoke test: {report['error']}")

        meta = {
            "key": key,
            "name": template.name,
            "family": family,
            "hypothesis": template.hypothesis,
            "falsifiable_prediction": template.falsifiable_prediction,
            "parameters": [p.model_dump() for p in specs],
            "warmup_bars": template.warmup_bars,
            "data_requirement": "BARS",
            "minimum_timeframe": "1m",
            "research_status": "RUNNABLE",
            "origin": "custom",
            "created_by": created_by,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "research_sources": list(research_sources),
            "smoke_test": report,
        }
        with self._lock:
            self._source_path(key).write_text(source, encoding="utf-8")
            self._meta_path(key).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return template

    def delete(self, key: str) -> None:
        with self._lock:
            meta, source = self._meta_path(key), self._source_path(key)
            if not meta.exists():
                raise KeyError(key)
            meta.unlink()
            source.unlink(missing_ok=True)

    # ── smoke test ───────────────────────────────────────────────────────────
    def smoke_test(self, template: Template) -> dict[str, Any]:
        """Import the source and run it over synthetic bars.

        The synthetic series is deliberately edge-free, so the result says nothing
        about whether the idea works. It says only that the code runs, returns
        values the runtime accepts, and does not raise on real array shapes —
        which is the whole point of checking before registration.
        """
        # Imported here: authoring is not on the hot path, and importing the
        # runtime at module scope would make this a circular import.
        from forge.strategy.models import StrategySpec
        from forge.strategy.runtime import run_backtest
        from forge.strategy.synthetic import generate_bars

        probe_name = f"algoforge_probe_{template.key}"
        probe_path = self.path / f"_probe_{template.key}.py"
        try:
            probe_path.write_text(template.source, encoding="utf-8")
            spec = importlib.util.spec_from_file_location(probe_name, probe_path)
            if spec is None or spec.loader is None:
                return {"ok": False, "error": "the source could not be loaded as a module"}
            module = importlib.util.module_from_spec(spec)
            sys.modules[probe_name] = module
            spec.loader.exec_module(module)

            bars = generate_bars(symbol="MNQ.SYNTH", count=SMOKE_BARS, seed=20260901)
            trial = StrategySpec(
                strategy_id="smoke_probe",
                name=f"{template.name} (smoke test)",
                lineage="smoke",
                family=template.family,
                market="futures",
                symbol="MNQ.SYNTH",
                template=template.key,
                hypothesis=template.hypothesis,
                falsifiable_prediction=template.falsifiable_prediction,
                parameters=template.parameters,
                warmup_bars=min(template.warmup_bars, SMOKE_BARS // 4),
                created_at=datetime.now(UTC),
                created_by="smoke-test",
            )
            result = run_backtest(module, trial, bars, labels=("SYNTHETIC", "SMOKE_TEST"))
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            sys.modules.pop(probe_name, None)
            probe_path.unlink(missing_ok=True)
            importlib.invalidate_caches()

        return {
            "ok": True,
            "bars": SMOKE_BARS,
            "trades": len(result.trades),
            "lookahead_clean": result.lookahead_clean,
            "note": (
                "Ran on edge-free synthetic bars. This proves the code executes, not "
                "that the hypothesis holds."
            ),
        }
