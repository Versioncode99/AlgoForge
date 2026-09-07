"""Families and templates as things that can be created, not just listed.

The console assistant used to answer "the families available are fixed" and it
was telling the truth: families were a `Literal` and templates were module-level
constants. Both are now registries with a create path, and this router is that
path.

Creating capability is still not the same as inventing it. A family that needs
order-book depth is registered `BLOCKED_DATA` and the strategy writer refuses it;
a template must pass the static guard and execute on synthetic bars before it
enters the catalogue. What changed is that the refusal is now a *measured* one
with a reason attached, instead of an architectural dead end.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.strategy import (
    TEMPLATES,
    FamilyRegistry,
    TemplateRejected,
    TemplateStore,
    strategy_capability_catalog,
)
from forge.vault import VaultMirror
from pydantic import BaseModel, Field

from forge_api.activity import ActivityLog

# A worked example, served to the interface and to any model asked to write one.
# Showing the shape is cheaper than describing it, and a template written against
# this skeleton passes the guard on the first attempt far more often.
EXAMPLE_SOURCE = '''"""Range Compression Reversal.

Enter against the move when the last closed bar extends far beyond a rolling
mean while realised range is contracting. Exit on reversion to the mean, an ATR
stop, or a time stop.

`w` ends at the last CLOSED bar and `p` holds the parameters. Fills happen on
the next bar's open, so this module cannot see the price it will trade at.
"""

import numpy as np


def entry_signal(w, p):
    lookback = int(p["lookback"])
    closes = w.closes[-lookback:]
    mean = closes.mean()
    sigma = closes.std()
    if sigma <= 0:
        return None
    deviation = (w.closes[-1] - mean) / sigma
    recent_range = (w.highs[-lookback:] - w.lows[-lookback:]).mean()
    older_range = (w.highs[-2 * lookback : -lookback] - w.lows[-2 * lookback : -lookback]).mean()
    if older_range <= 0 or recent_range / older_range > float(p["compression"]):
        return None
    if deviation <= -float(p["entry_sigma"]):
        return 1
    if deviation >= float(p["entry_sigma"]):
        return -1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr > 0:
        drawdown = (pos.entry_price - w.closes[-1]) * pos.direction
        if drawdown >= float(p["stop_atr"]) * atr:
            return "stop"
    closes = w.closes[-int(p["lookback"]) :]
    if (w.closes[-1] - closes.mean()) * pos.direction >= 0:
        return "signal"
    return None
'''


class FamilyRequest(BaseModel):
    key: str = Field(min_length=3, max_length=40)
    label: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=600)
    mechanism: str = Field(min_length=40, max_length=2000)
    data_requirements: list[str] = Field(default_factory=lambda: ["BARS"], max_length=8)


class TemplateRequest(BaseModel):
    key: str = Field(min_length=3, max_length=50)
    name: str = Field(min_length=2, max_length=120)
    family: str = Field(min_length=3, max_length=40)
    hypothesis: str = Field(min_length=40, max_length=4000)
    falsifiable_prediction: str = Field(min_length=30, max_length=4000)
    parameters: list[dict[str, Any]] = Field(min_length=1, max_length=10)
    source: str = Field(min_length=40, max_length=24_000)
    warmup_bars: int = Field(default=60, ge=5, le=5_000)
    research_sources: list[str] = Field(default_factory=list, max_length=8)


def build_catalog_router(
    families: FamilyRegistry,
    templates: TemplateStore,
    mirror: VaultMirror,
    log: ActivityLog,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["catalog"])

    @router.get("/families", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_families() -> ApiEnvelope[list[dict[str, Any]]]:
        rows = families.with_templates(TEMPLATES)
        return ApiEnvelope(
            data=rows,
            meta={
                "total": len(rows),
                "runnable": sum(1 for r in rows if r["runnable"]),
                "blocked": sum(1 for r in rows if not r["runnable"]),
                "note": (
                    "A blocked family is registered and documented but refused by the "
                    "strategy writer: the data it needs is not configured."
                ),
            },
        )

    @router.post("/families", response_model=ApiEnvelope[dict[str, Any]])
    def create_family(body: FamilyRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            family = families.create(
                key=body.key,
                label=body.label,
                description=body.description,
                mechanism=body.mechanism,
                data_requirements=tuple(body.data_requirements),
                created_by="operator",
            )
        except ValueError as exc:
            raise HTTPException(422, {"code": "family_refused", "detail": str(exc)}) from exc
        mirror.family(family.as_dict())
        log.record(
            "CATALOG",
            f"family '{family.key}' created"
            + ("" if family.runnable else f" — blocked on {', '.join(family.blocked_by)}"),
            "pass" if family.runnable else "warn",
        )
        return ApiEnvelope(data=family.as_dict())

    @router.delete("/families/{key}", response_model=ApiEnvelope[dict[str, Any]])
    def delete_family(key: str) -> ApiEnvelope[dict[str, Any]]:
        in_use = sorted(t.key for t in TEMPLATES.values() if t.family == key)
        if in_use:
            raise HTTPException(
                409,
                {
                    "code": "family_in_use",
                    "templates": in_use,
                    "detail": "Delete or re-file these templates first.",
                },
            )
        try:
            families.delete(key)
        except KeyError as exc:
            raise HTTPException(404, {"code": "family_not_found", "key": key}) from exc
        except ValueError as exc:
            raise HTTPException(422, {"code": "family_protected", "detail": str(exc)}) from exc
        log.record("CATALOG", f"family '{key}' deleted", "warn")
        return ApiEnvelope(data={"deleted": key})

    @router.get("/templates", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_templates() -> ApiEnvelope[list[dict[str, Any]]]:
        custom = set(templates.keys())
        rows = [
            {
                "key": t.key,
                "name": t.name,
                "family": t.family,
                "hypothesis": t.hypothesis,
                "falsifiable_prediction": t.falsifiable_prediction,
                "warmup_bars": t.warmup_bars,
                "data_requirement": t.data_requirement,
                "minimum_timeframe": t.minimum_timeframe,
                "research_status": t.research_status,
                "origin": "custom" if t.key in custom else "builtin",
                "parameters": [p.model_dump() for p in t.parameters],
                "grid_points": _grid_points(t),
                "line_count": len(t.source.splitlines()),
            }
            for t in sorted(TEMPLATES.values(), key=lambda t: (t.family, t.key))
        ]
        return ApiEnvelope(
            data=rows,
            meta={
                "total": len(rows),
                "custom": len(custom),
                "rejected_on_load": templates.rejected_on_load,
                "example_source": EXAMPLE_SOURCE,
            },
        )

    @router.get("/templates/{key}/source", response_model=ApiEnvelope[dict[str, Any]])
    def template_source(key: str) -> ApiEnvelope[dict[str, Any]]:
        template = TEMPLATES.get(key)
        if template is None:
            raise HTTPException(404, {"code": "template_not_found", "key": key})
        return ApiEnvelope(data={"key": key, "source": template.source, "family": template.family})

    @router.post("/templates", response_model=ApiEnvelope[dict[str, Any]])
    def create_template(body: TemplateRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            template = templates.create(
                key=body.key,
                name=body.name,
                family=body.family,
                hypothesis=body.hypothesis,
                falsifiable_prediction=body.falsifiable_prediction,
                parameters=body.parameters,
                source=body.source,
                warmup_bars=body.warmup_bars,
                known_families=families.runnable_keys(),
                existing_templates=set(TEMPLATES),
                created_by="operator",
                research_sources=tuple(body.research_sources),
            )
        except TemplateRejected as exc:
            raise HTTPException(422, {"code": "template_rejected", "detail": str(exc)}) from exc
        TEMPLATES[template.key] = template
        meta = templates.metadata(template.key)
        log.record(
            "CATALOG",
            f"template '{template.key}' registered in {template.family} "
            f"(smoke test: {meta['smoke_test'].get('trades', 0)} trades on synthetic bars)",
            "pass",
        )
        return ApiEnvelope(
            data={"key": template.key, "family": template.family, **meta},
            meta={"registered": True, "engine_can_use_immediately": True},
        )

    @router.delete("/templates/{key}", response_model=ApiEnvelope[dict[str, Any]])
    def delete_template(key: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            templates.delete(key)
        except KeyError as exc:
            raise HTTPException(
                404,
                {
                    "code": "template_not_custom",
                    "detail": (
                        "Built-in templates are version-controlled and cannot be deleted here."
                    ),
                },
            ) from exc
        TEMPLATES.pop(key, None)
        log.record("CATALOG", f"template '{key}' removed", "warn")
        return ApiEnvelope(data={"deleted": key})

    @router.get("/capabilities/strategies", response_model=ApiEnvelope[list[dict[str, Any]]])
    def capability_catalog() -> ApiEnvelope[list[dict[str, Any]]]:
        rows = [item.model_dump(mode="json") for item in strategy_capability_catalog()]
        return ApiEnvelope(
            data=rows,
            meta={"runnable": sum(1 for r in rows if r["runnable"]), "total": len(rows)},
        )

    return router


def _grid_points(template: Any) -> int:
    total = 1
    for p in template.parameters:
        span = (p.high - p.low) / p.step if p.step else 0
        total *= max(1, round(span) + 1)
        if total > 10**9:
            return 10**9
    return total
