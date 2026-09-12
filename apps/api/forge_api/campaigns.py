"""HTTP surface for research campaigns, the frontier and the hypothesis graph.

Everything the campaign interface renders is served from here, and every route
is a read or a write against the stores the director already uses. There is no
computation in this module: a route that derived a number the director does not
have would be a second source of truth about the same research.

The one thing this module owns is **assembly** — the campaign overview joins the
campaign row, the frontier counts, the hypothesis counts, the promotion queue
and the journal into the single payload the screen needs, so the interface makes
one request rather than six and cannot show five of them refreshed and one
stale.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.research.agents import (
    MAX_AGENTS,
    ROLE_PURPOSE,
    AgentRegistry,
    AgentRole,
    AgentState,
    capacity_for,
)
from forge.research.campaign import CampaignError, CampaignStore
from forge.research.frontier import FrontierState, ResearchFrontier
from forge.research.hypotheses import HypothesisError, HypothesisGraph
from forge.research.journal import ResearchJournal
from forge.research.literature import SourceStore
from forge.research.orchestration import ResearchOrchestrator
from forge.research.promotion import PromotionQueue
from forge.research.skips import SkipKind, SkipLedger
from forge.research.synthesis import ARCHETYPES
from pydantic import BaseModel, Field

from forge_api.director import ResearchDirector


class UnknownCampaign(CampaignError):
    """No campaign carries that id.

    A subclass of `CampaignError` so every existing `except CampaignError`
    keeps working, and a distinct type so a caller that needs to answer 404
    rather than 409 can tell the two apart without matching on the message.
    """



class CreateCampaignRequest(BaseModel):
    name: str
    objective: str
    dataset: str
    symbol: str = "NQ"
    timeframe: str = "1m"
    universe: list[str] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    allocation: dict[str, float] | None = None
    stopping: dict[str, Any] | None = None
    allowed_capabilities: list[str] = Field(
        default_factory=lambda: ["BARS", "VOLUME", "SESSION_CLOCK"]
    )
    web_research: bool = False
    seed: int = 20260910
    description: str = ""
    priority: int = 50
    agent_target: int = 1
    tags: list[str] = Field(default_factory=list)


class DeployAgentsRequest(BaseModel):
    count: int = 4
    #: Explicit roles, or omitted for a default crew. Named rather than inferred
    #: because "four discovery agents" and "four agents" are different research
    #: decisions and the system should not guess which one was meant.
    roles: list[str] = Field(default_factory=list)
    compute_budget: float = 0.0


class PrioritiseRequest(BaseModel):
    priority: int = Field(ge=0, le=100)


class RenameRequest(BaseModel):
    name: str


class DuplicateRequest(BaseModel):
    name: str = ""
    seed: int | None = None


class StartCampaignRequest(BaseModel):
    # The engine settings a campaign runs under. Separate from the campaign's
    # own research configuration: the same campaign can be resumed with more
    # workers without becoming a different piece of research.
    workers: int = 4
    cycle_seconds: float = 4.0
    max_strategies: int = 400
    max_bars: int = 250_000


class CampaignService:
    """The stores a campaign needs, constructed once and shared.

    Held together in one object because they are always used together and
    because the director, the engine and the HTTP layer must all be looking at
    the same files — three separate constructions of `ResearchFrontier` would be
    three separate connections to one database and, worse, three places to
    remember to point at the same path.
    """

    def __init__(self, data_root: Any, *, log: Any = None) -> None:
        self.campaigns = CampaignStore(data_root / "campaigns.db")
        self.frontier = ResearchFrontier(data_root / "frontier.db")
        self.hypotheses = HypothesisGraph(data_root / "hypotheses.db")
        self.journal = ResearchJournal(data_root / "research-journal.db")
        self.sources = SourceStore(data_root / "research-sources.db")
        self.promotion = PromotionQueue(data_root / "promotion.db")
        # Research agents and their claims. Durable for the same reason the
        # campaigns are: an unattended run that forgets which agent held which
        # hypothesis has to start the whole programme again.
        self.agents = AgentRegistry(data_root / "research-agents.db")
        self.skips = SkipLedger(data_root / "research_skips.db")
        self.log = log
        self.director: ResearchDirector | None = None
        # The scheduler. It decides which campaign a worker serves; it never
        # decides what to research and cannot touch a verdict.
        self.orchestrator = ResearchOrchestrator(
            campaigns=self.campaigns, agents=self.agents, log=log
        )
        # Starting a campaign is *two* things — marking the row running and
        # starting the search machinery — and only the control layer knows how
        # to do the second. Injected rather than imported so this module never
        # reaches into the engine, and optional so a test can exercise the
        # bookkeeping half on its own.
        self.start_engine: Callable[[Any, Any], None] | None = None
        self.stop_engine: Callable[[], Any] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    # These live on the service rather than inside the route closures because
    # three surfaces have to be able to run them and get the same result: the
    # HTTP routes below, the command palette, and an assistant calling the
    # action registry. A second implementation for the agent — which is what
    # existed while the registry had no campaign verbs at all — is how the AI
    # and the UI come to disagree about what "start" means.

    def require(self, campaign_id: str) -> Any:
        """The campaign, or `UnknownCampaign`. Never `None`."""
        campaign = self.campaigns.get(campaign_id)
        if campaign is None:
            raise UnknownCampaign(campaign_id)
        return campaign

    def start(self, campaign_id: str, settings: Any) -> dict[str, Any]:
        """Run this campaign, alongside any others already running.

        Starting a second campaign does not stop the first. The engine's
        workers are dealt across every running campaign by the orchestrator,
        and the engine itself is started once — a second start is a no-op,
        which is what lets a campaign join a run already in progress.
        """
        self.require(campaign_id)
        campaign = self.campaigns.set_status(campaign_id, "running")
        if self.director is not None:
            self.director.prepare(campaign)
            # The first campaign to start is also the attached one, so an
            # engine running with no orchestrator still has a campaign to serve.
            if self.director.attached_campaign_id is None:
                self.director.attach(campaign)
        # A crew, if this campaign asked for one and has none yet.
        note = ""
        if campaign.agent_target > 0 and not self.agents.list(campaign_id):
            _, note = self.agents.deploy(
                campaign_id=campaign_id, count=campaign.agent_target
            )
        for agent in self.agents.list(campaign_id):
            if agent.state in {AgentState.CREATED, AgentState.STOPPED}:
                self.agents.set_state(
                    agent.agent_id, AgentState.RUNNING, task="waiting for an assignment"
                )
        if self.start_engine is not None:
            self.start_engine(campaign, settings)
        payload = self.overview(campaign_id)
        if note:
            payload["capacity_note"] = note
        return payload

    def stop(self, campaign_id: str) -> dict[str, Any]:
        """Stop one campaign. The engine keeps running for the others.

        Stopping used to stop the engine, because there was only ever one
        campaign and the two were the same act. With several running, stopping
        the engine because one campaign finished would silently end the rest.
        """
        self.require(campaign_id)
        self.campaigns.set_status(campaign_id, "stopped", reason="stopped by the operator")
        self.agents.stop_all(campaign_id, reason="campaign stopped by the operator")
        self.orchestrator.forget(campaign_id)
        if self.director is not None:
            self.director.forget(campaign_id)
            if self.director.attached_campaign_id == campaign_id:
                self.director.detach("stopped by the operator")
        if not self.campaigns.running() and self.stop_engine is not None:
            # Nothing left to research. Only then does the engine stop.
            self.stop_engine()
        return self.overview(campaign_id)

    def pause(self, campaign_id: str) -> dict[str, Any]:
        """Hold a campaign without ending it.

        Distinct from stopping: a paused campaign keeps its agents, so resuming
        it does not reset the crew or lose what each agent was working on.
        """
        self.require(campaign_id)
        self.campaigns.set_status(campaign_id, "paused", reason="paused by the operator")
        for agent in self.agents.list(campaign_id):
            if agent.state in {AgentState.RUNNING, AgentState.IDLE, AgentState.WAITING}:
                self.agents.set_state(agent.agent_id, AgentState.WAITING, task="campaign paused")
        self.orchestrator.forget(campaign_id)
        return self.overview(campaign_id)

    def overview(self, campaign_id: str) -> dict[str, Any]:
        """Everything one campaign screen needs, in one payload."""
        campaign = self.require(campaign_id)
        counts = self.frontier.counts(campaign_id)
        return {
            "campaign": campaign.as_dict(),
            "frontier": {
                "counts": counts,
                "kinds": self.frontier.kind_counts(campaign_id),
                # The three numbers that separate a broad search from a deep one.
                "open": counts[str(FrontierState.UNTESTED)] + counts[str(FrontierState.UNKNOWN)],
                "blocked": counts[str(FrontierState.BLOCKED_BY_DATA)],
                "settled": (
                    counts[str(FrontierState.VALIDATED)]
                    + counts[str(FrontierState.FAILED)]
                    + counts[str(FrontierState.EXHAUSTED)]
                ),
            },
            "hypotheses": {
                "counts": self.hypotheses.counts(campaign_id),
                "mechanisms": self.hypotheses.distinct_mechanisms(campaign_id),
            },
            "validation": {
                "pending": self.promotion.pending(campaign_id),
                "outcomes": self.promotion.outcomes(campaign_id),
            },
            "agents": {
                "counts": self.agents.counts(campaign_id),
                "roster": [a.as_dict() for a in self.agents.list(campaign_id)],
                "claims": self.agents.claims(campaign_id),
            },
            # The accounting that replaces "Skipped by memory". Every number is
            # backed by rows naming what the proposal collided with.
            "skips": self.skips.counts(campaign_id),
            "sources": self.sources.count(campaign_id),
            "journal_head": self.journal.latest_id(campaign_id),
            "generated_templates": (
                self.director.generated_templates() if self.director else {}
            ),
        }


def _frontier_totals(service: CampaignService, campaigns: Any) -> dict[str, int]:
    """Frontier states summed across campaigns, with every state present.

    Zeroes are reported. A state missing from the payload renders as an absent
    category, and "no blocked questions" and "we did not look" are different.
    """
    totals = {str(state): 0 for state in FrontierState}
    for campaign in campaigns:
        for state, count in service.frontier.counts(campaign.campaign_id).items():
            totals[state] = totals.get(state, 0) + count
    return totals


#: Campaign cards rendered in the control centre. Beyond this the page stops
#: being readable, and the cards are ordered by priority so the cut falls on the
#: campaigns the operator cared least about.
CARD_LIMIT = 200

#: Campaigns counted for the totals. Separate from `CARD_LIMIT` because a page
#: is allowed to be a page, and a total is not allowed to be a page.
TOTALS_LIMIT = 10_000


def _capacity() -> dict[str, Any]:
    """What this installation can actually run, so nothing claims otherwise."""
    servable, _ = capacity_for(requested=MAX_AGENTS)
    return {"max_agents": servable, "ceiling": MAX_AGENTS}


def build_campaign_router(
    service: CampaignService,
    *,
    start_engine: Any,
    stop_engine: Any,
) -> APIRouter:
    """Routes for campaigns. ``start_engine`` and ``stop_engine`` are injected.

    Injected rather than imported so this module never reaches into the engine:
    starting a campaign is *two* things — marking the campaign running and
    starting the search machinery — and only the control layer knows how to do
    the second.
    """
    service.start_engine = start_engine
    service.stop_engine = stop_engine
    router = APIRouter(prefix="/campaigns", tags=["research"])

    @router.get("")
    def list_campaigns() -> ApiEnvelope[list[dict[str, Any]]]:
        return ApiEnvelope(data=[c.as_dict() for c in service.campaigns.list()])

    @router.get("/archetypes")
    def list_archetypes() -> ApiEnvelope[list[dict[str, Any]]]:
        """The signal constructions the director can compose from.

        Exposed because "what is it able to invent?" is a fair question with a
        finite answer, and an interface that cannot show the vocabulary makes
        the generation look like magic rather than composition.
        """
        return ApiEnvelope(
            data=[
                {
                    "key": a.key,
                    "label": a.label,
                    "family": a.family,
                    "mechanism": a.mechanism,
                    "prediction": a.prediction,
                    "required_data": list(a.required_data),
                    "features": sorted({f.kind for f in a.features}),
                    "continuation": a.continuation,
                }
                for a in ARCHETYPES.values()
            ]
        )

    @router.get("/active")
    def active_campaign() -> ApiEnvelope[dict[str, Any] | None]:
        """The highest-priority running campaign.

        Kept for callers that predate multi-campaign support. `/running` is the
        one to use: "the" campaign is no longer a well-formed question.
        """
        campaign = service.campaigns.active()
        return ApiEnvelope(data=service.overview(campaign.campaign_id) if campaign else None)

    @router.get("/running")
    def running_campaigns() -> ApiEnvelope[list[dict[str, Any]]]:
        return ApiEnvelope(data=[c.as_dict() for c in service.campaigns.running()])

    @router.get("/control-center")
    def control_center() -> ApiEnvelope[dict[str, Any]]:
        """Everything the Research Control Center renders, in one payload.

        Assembled rather than computed: every number here comes from the store
        that owns it. A route that derived its own would be a second source of
        truth about the same research, and the two would diverge on the day it
        mattered.
        """
        campaigns = service.campaigns.list(limit=CARD_LIMIT)
        # Totals are over every campaign, not over the page of cards. A sum
        # labelled "Experiments" that quietly omitted the campaigns below the
        # cut would be wrong in the direction nobody checks.
        counted = (
            campaigns
            if len(campaigns) < CARD_LIMIT
            else service.campaigns.list(limit=TOTALS_LIMIT)
        )
        running = [c for c in campaigns if c.running]
        totals = {
            "campaigns": len(counted),
            "running": sum(1 for c in counted if c.running),
            "experiments": sum(c.progress.experiments for c in counted),
            "hypotheses": sum(c.progress.hypotheses for c in counted),
            # Asked of the store that owns it, not summed across campaigns. A
            # mechanism is a *string*, deduplicated by the hypothesis graph, so
            # per-campaign counts overlap: three campaigns exploring one idea
            # summed to "Mechanisms: 3". This is the number the interface leans
            # on to separate a hundred discoveries from one idea a hundred
            # times, and inflating it with campaign count breaks exactly that.
            "mechanisms": service.hypotheses.distinct_mechanisms(),
            "families_created": sum(c.progress.families_created for c in counted),
            "templates_created": sum(c.progress.templates_created for c in counted),
            "followups": sum(c.progress.followups_generated for c in counted),
            "compute_units": round(sum(c.progress.compute_units for c in counted), 2),
        }
        # Validation is reported as attempts / passed / failed / blocked rather
        # than as one number. "Validation: 0" cannot distinguish "nothing was
        # eligible" from "everything was tried and everything failed", and those
        # are opposite facts about a campaign.
        outcomes = service.promotion.outcomes()
        blocked = sum(
            1
            for row in service.promotion.list(limit=2000)
            if str(row.get("state")) == "ABANDONED"
        )
        attempts = sum(outcomes.values()) + blocked
        return ApiEnvelope(
            data={
                "totals": totals,
                "campaigns": [c.as_dict() for c in campaigns],
                "allocation": service.orchestrator.snapshot(),
                "agents": service.agents.counts(),
                "skips": service.skips.counts(),
                "validation": {
                    "attempts": attempts,
                    "passed": outcomes.get("PASS", 0),
                    "failed": outcomes.get("FAIL", 0),
                    "inconclusive": outcomes.get("INCONCLUSIVE", 0),
                    "blocked": blocked,
                    "pending": sum(service.promotion.pending(c.campaign_id) for c in running),
                },
                "frontier": _frontier_totals(service, campaigns),
                "capacity": _capacity(),
            }
        )

    @router.post("")
    def create_campaign(body: CreateCampaignRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            campaign = service.campaigns.create(**body.model_dump())
        except (CampaignError, ValueError) as exc:
            raise HTTPException(400, {"code": "campaign_refused", "reason": str(exc)}) from exc
        return ApiEnvelope(data=campaign.as_dict())

    @router.get("/{campaign_id}")
    def get_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.overview(campaign_id))
        except UnknownCampaign as exc:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": str(exc)}) from exc

    @router.post("/{campaign_id}/start")
    def start_campaign(
        campaign_id: str, body: StartCampaignRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.start(campaign_id, body))
        except UnknownCampaign as exc:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": str(exc)}) from exc
        except CampaignError as exc:
            raise HTTPException(409, {"code": "campaign_conflict", "reason": str(exc)}) from exc

    @router.post("/{campaign_id}/stop")
    def stop_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.stop(campaign_id))
        except UnknownCampaign as exc:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": str(exc)}) from exc

    @router.post("/{campaign_id}/pause")
    def pause_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.pause(campaign_id))
        except UnknownCampaign as exc:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": str(exc)}) from exc

    @router.post("/{campaign_id}/resume")
    def resume_campaign(
        campaign_id: str, body: StartCampaignRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return start_campaign(campaign_id, body)

    @router.post("/{campaign_id}/duplicate")
    def duplicate_campaign(
        campaign_id: str, body: DuplicateRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        try:
            copy = service.campaigns.duplicate(campaign_id, name=body.name, seed=body.seed)
        except CampaignError as exc:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": str(exc)}) from exc
        return ApiEnvelope(data=copy.as_dict())

    @router.post("/{campaign_id}/archive")
    def archive_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.campaigns.archive(campaign_id).as_dict())
        except CampaignError as exc:
            raise HTTPException(409, {"code": "archive_refused", "reason": str(exc)}) from exc

    @router.post("/{campaign_id}/restore")
    def restore_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.campaigns.restore(campaign_id).as_dict())
        except CampaignError as exc:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": str(exc)}) from exc

    @router.post("/{campaign_id}/priority")
    def prioritise_campaign(
        campaign_id: str, body: PrioritiseRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        """Set how much of the engine this campaign gets.

        Priority allocates *workers*. It cannot relax a gate, lower a threshold
        or change what counts as evidence, and nothing downstream reads it.
        """
        try:
            return ApiEnvelope(
                data=service.campaigns.prioritise(campaign_id, body.priority).as_dict()
            )
        except CampaignError as exc:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": str(exc)}) from exc

    @router.post("/{campaign_id}/rename")
    def rename_campaign(campaign_id: str, body: RenameRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.campaigns.rename(campaign_id, body.name).as_dict())
        except CampaignError as exc:
            raise HTTPException(400, {"code": "rename_refused", "reason": str(exc)}) from exc

    @router.get("/{campaign_id}/export")
    def export_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Everything this campaign is and found, as one document.

        The research map travels; the *evidence* does not. Verdicts, artifacts
        and holdout consumptions stay where they were produced, because a
        portable verdict is a verdict that can be edited in transit.
        """
        campaign = service.campaigns.get(campaign_id)
        if campaign is None:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": campaign_id})
        return ApiEnvelope(
            data={
                "campaign": campaign.as_dict(),
                "frontier": [i.as_dict() for i in service.frontier.list(campaign_id, limit=2000)],
                "hypotheses": [
                    h.as_dict() for h in service.hypotheses.list(campaign_id, limit=2000)
                ],
                "validation": service.promotion.list(campaign_id, limit=2000),
                "skips": service.skips.counts(campaign_id),
                "agents": [a.as_dict() for a in service.agents.list(campaign_id)],
                "sources": service.sources.list(campaign_id, limit=1000),
                "exported_at": campaign.updated_at,
                "evidence_included": False,
                "note": (
                    "Research map only. Verdicts, backtests and holdout consumptions are not "
                    "exported: evidence stays where it was produced."
                ),
            }
        )

    # ── agents ───────────────────────────────────────────────────────────────
    @router.get("/{campaign_id}/agents")
    def list_agents(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data={
                "agents": [a.as_dict() for a in service.agents.list(campaign_id)],
                "counts": service.agents.counts(campaign_id),
                "claims": service.agents.claims(campaign_id),
                "roles": [
                    {"role": str(role), "purpose": purpose}
                    for role, purpose in ROLE_PURPOSE.items()
                ],
            }
        )

    @router.post("/{campaign_id}/agents")
    def deploy_agents(campaign_id: str, body: DeployAgentsRequest) -> ApiEnvelope[dict[str, Any]]:
        """Add agents, up to what this installation can actually run.

        The response carries a note when fewer were created than asked for. An
        interface that rendered thirty-two rows on a machine that can serve
        eight would be claiming compute that does not exist.
        """
        if service.campaigns.get(campaign_id) is None:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": campaign_id})
        try:
            roles = [AgentRole(r.strip().upper()) for r in body.roles if r.strip()]
        except ValueError as exc:
            raise HTTPException(
                400,
                {
                    "code": "unknown_role",
                    "reason": f"{exc}. Valid roles: "
                    + ", ".join(str(r) for r in AgentRole),
                },
            ) from exc
        existing = len(service.agents.list(campaign_id))
        granted, note = capacity_for(requested=existing + max(0, body.count))
        created, _ = service.agents.deploy(
            campaign_id=campaign_id,
            count=max(0, granted - existing),
            roles=roles or None,
            compute_budget=body.compute_budget,
        )
        return ApiEnvelope(
            data={
                "created": [a.as_dict() for a in created],
                "counts": service.agents.counts(campaign_id),
                "capacity_note": note,
            }
        )

    @router.delete("/{campaign_id}/agents/{agent_id}")
    def remove_agent(campaign_id: str, agent_id: str) -> ApiEnvelope[dict[str, Any]]:
        agent = service.agents.get(agent_id)
        if agent is None or agent.campaign_id != campaign_id:
            raise HTTPException(404, {"code": "unknown_agent", "reason": agent_id})
        service.agents.remove(agent_id)
        return ApiEnvelope(
            data={"removed": agent_id, "counts": service.agents.counts(campaign_id)}
        )

    @router.get("/{campaign_id}/agents/{agent_id}")
    def agent_detail(campaign_id: str, agent_id: str) -> ApiEnvelope[dict[str, Any]]:
        agent = service.agents.get(agent_id)
        if agent is None or agent.campaign_id != campaign_id:
            raise HTTPException(404, {"code": "unknown_agent", "reason": agent_id})
        return ApiEnvelope(
            data={
                "agent": agent.as_dict(),
                "claims": [
                    c for c in service.agents.claims(campaign_id) if c["agent_id"] == agent_id
                ],
                "skips": service.skips.list(campaign_id, limit=50),
            }
        )

    # ── skips ────────────────────────────────────────────────────────────────
    @router.get("/{campaign_id}/skips")
    def campaign_skips(
        campaign_id: str, kind: str | None = None, limit: int = 200
    ) -> ApiEnvelope[dict[str, Any]]:
        """What this campaign refused, and why.

        The replacement for a single number called "Skipped by memory" that
        summed five unrelated situations. Every row names what the proposal
        collided with and whether a retry is permitted.
        """
        try:
            chosen = SkipKind(kind.upper()) if kind else None
        except ValueError as exc:
            raise HTTPException(
                400,
                {
                    "code": "unknown_skip_kind",
                    "reason": f"'{kind}' is not a skip kind. Valid: "
                    + ", ".join(str(k) for k in SkipKind),
                },
            ) from exc
        return ApiEnvelope(
            data={
                "counts": service.skips.counts(campaign_id),
                "skips": service.skips.list(campaign_id, kind=chosen, limit=limit),
                "retryable": service.skips.retryable(campaign_id, limit=50),
            }
        )

    @router.delete("/{campaign_id}")
    def delete_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        campaign = service.campaigns.get(campaign_id)
        if campaign is None:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": campaign_id})
        if campaign.running:
            raise HTTPException(
                409,
                {"code": "campaign_running", "reason": "stop the campaign before deleting it"},
            )
        # The frontier, the hypotheses and the journal are deliberately left in
        # place. They are the research this campaign produced, and deleting the
        # campaign row is deleting the programme, not its findings.
        service.campaigns.delete(campaign_id)
        return ApiEnvelope(data={"deleted": campaign_id, "research_retained": True})

    @router.get("/{campaign_id}/frontier")
    def frontier(
        campaign_id: str, state: str | None = None, limit: int = 300
    ) -> ApiEnvelope[list[dict[str, Any]]]:
        states = None
        if state:
            try:
                states = (FrontierState(state.upper()),)
            except ValueError as exc:
                raise HTTPException(
                    400,
                    {
                        "code": "unknown_state",
                        "reason": f"'{state}' is not a frontier state. Valid: "
                        + ", ".join(s.value for s in FrontierState),
                    },
                ) from exc
        items = service.frontier.list(campaign_id, states=states, limit=limit)
        return ApiEnvelope(data=[item.as_dict() for item in items])

    @router.get("/{campaign_id}/frontier/{item_id}")
    def frontier_item(campaign_id: str, item_id: str) -> ApiEnvelope[dict[str, Any]]:
        item = service.frontier.get(item_id)
        if item is None or item.campaign_id != campaign_id:
            raise HTTPException(404, {"code": "unknown_item", "reason": item_id})
        return ApiEnvelope(
            data={"item": item.as_dict(), "history": service.frontier.history(item_id)}
        )

    @router.get("/{campaign_id}/hypotheses")
    def hypotheses(
        campaign_id: str, family: str | None = None, limit: int = 300
    ) -> ApiEnvelope[list[dict[str, Any]]]:
        nodes = service.hypotheses.list(campaign_id, family=family, limit=limit)
        return ApiEnvelope(data=[node.as_dict() for node in nodes])

    @router.get("/{campaign_id}/hypotheses/{hypothesis_id}")
    def hypothesis(campaign_id: str, hypothesis_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=service.hypotheses.lineage(hypothesis_id))
        except HypothesisError as exc:
            raise HTTPException(404, {"code": "unknown_hypothesis", "reason": str(exc)}) from exc

    @router.get("/{campaign_id}/events")
    def events(
        campaign_id: str, since: int = 0, limit: int = 200
    ) -> ApiEnvelope[dict[str, Any]]:
        """The live research stream.

        ``since`` is the last event id the caller has seen, so a polling
        interface fetches only what is new. Oldest first, because these read as
        a narrative and a narrative in reverse is not one.
        """
        new = service.journal.since(campaign_id, since, limit=limit)
        return ApiEnvelope(
            data={
                "events": new,
                "head": service.journal.latest_id(campaign_id),
                "counts": service.journal.counts(campaign_id),
            }
        )

    @router.get("/{campaign_id}/validation")
    def validation(campaign_id: str, limit: int = 100) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data={
                "queue": service.promotion.list(campaign_id, limit=limit),
                "pending": service.promotion.pending(campaign_id),
                "outcomes": service.promotion.outcomes(campaign_id),
            }
        )

    @router.get("/{campaign_id}/sources")
    def sources(campaign_id: str, limit: int = 100) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data={
                "sources": service.sources.list(campaign_id, limit=limit),
                "queries": service.sources.queries(campaign_id, limit=limit),
            }
        )

    return router
