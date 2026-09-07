"""Missions and the raw action surface.

Two routers in one place because they are two views of the same thing: an action
is one verb, a mission is a planned sequence of them. Exposing both means nothing
the orchestrator can do is hidden from the operator, and nothing the operator can
do is unavailable to the orchestrator.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from pydantic import BaseModel, Field

from forge_api.actions import ActionError, Actions
from forge_api.orchestrator import Orchestrator


class MissionRequest(BaseModel):
    objective: str = Field(min_length=8, max_length=600)
    # Off by default: a failed research search should not cancel the backtest
    # that does not depend on it.
    stop_on_failure: bool = False
    # Plan and return without executing, so a plan can be read before it runs.
    dry_run: bool = False
    # A plan to run verbatim, skipping the planner. This is how a dry run
    # becomes an approved run rather than a second, different plan.
    steps: list[dict[str, Any]] | None = Field(default=None, max_length=12)


class ActionRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def build_mission_router(orchestrator: Orchestrator, actions: Actions) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["orchestration"])

    @router.get("/missions", response_model=ApiEnvelope[dict[str, Any]])
    def list_missions() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=orchestrator.snapshot(),
            meta={"sequential": True, "note": "One mission runs at a time."},
        )

    @router.get("/missions/{mission_id}", response_model=ApiEnvelope[dict[str, Any]])
    def mission_detail(mission_id: str) -> ApiEnvelope[dict[str, Any]]:
        mission = orchestrator.get(mission_id)
        if mission is None:
            raise HTTPException(404, {"code": "mission_not_found", "id": mission_id})
        return ApiEnvelope(data=mission)

    @router.post("/missions", response_model=ApiEnvelope[dict[str, Any]], status_code=201)
    def launch_mission(body: MissionRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            mission = orchestrator.launch(
                body.objective,
                stop_on_failure=body.stop_on_failure,
                dry_run=body.dry_run,
                steps=body.steps,
            )
        except ValueError as exc:
            raise HTTPException(409, {"code": "mission_refused", "detail": str(exc)}) from exc
        return ApiEnvelope(
            data=mission,
            meta={
                "planned_by": mission["plan_source"],
                "note": (
                    "Every step is a declared action or specialist assignment. The plan "
                    "cannot introduce a verb that does not already exist."
                ),
            },
        )

    @router.get("/actions", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_actions() -> ApiEnvelope[list[dict[str, Any]]]:
        schemas = actions.schemas()
        return ApiEnvelope(
            data=schemas,
            meta={
                "total": len(schemas),
                "mutating": sum(1 for s in schemas if s["mutating"]),
                "recent": actions.recent(20),
            },
        )

    @router.post("/actions/{name}", response_model=ApiEnvelope[dict[str, Any]])
    def call_action(name: str, body: ActionRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=actions.call(name, body.arguments))
        except ActionError as exc:
            raise HTTPException(422, {"code": "action_refused", "detail": str(exc)}) from exc

    return router
