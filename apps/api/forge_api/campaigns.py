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

from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.research.campaign import CampaignError, CampaignStore
from forge.research.frontier import FrontierState, ResearchFrontier
from forge.research.hypotheses import HypothesisError, HypothesisGraph
from forge.research.journal import ResearchJournal
from forge.research.literature import SourceStore
from forge.research.promotion import PromotionQueue
from forge.research.synthesis import ARCHETYPES
from pydantic import BaseModel, Field

from forge_api.director import ResearchDirector


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
        self.log = log
        self.director: ResearchDirector | None = None

    def overview(self, campaign_id: str) -> dict[str, Any]:
        """Everything one campaign screen needs, in one payload."""
        campaign = self.campaigns.get(campaign_id)
        if campaign is None:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": campaign_id})
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
            "sources": self.sources.count(campaign_id),
            "journal_head": self.journal.latest_id(campaign_id),
            "generated_templates": (
                self.director.generated_templates() if self.director else {}
            ),
        }


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
        campaign = service.campaigns.active()
        return ApiEnvelope(data=service.overview(campaign.campaign_id) if campaign else None)

    @router.post("")
    def create_campaign(body: CreateCampaignRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            campaign = service.campaigns.create(**body.model_dump())
        except (CampaignError, ValueError) as exc:
            raise HTTPException(400, {"code": "campaign_refused", "reason": str(exc)}) from exc
        return ApiEnvelope(data=campaign.as_dict())

    @router.get("/{campaign_id}")
    def get_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=service.overview(campaign_id))

    @router.post("/{campaign_id}/start")
    def start_campaign(
        campaign_id: str, body: StartCampaignRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        campaign = service.campaigns.get(campaign_id)
        if campaign is None:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": campaign_id})
        try:
            campaign = service.campaigns.set_status(campaign_id, "running")
        except CampaignError as exc:
            raise HTTPException(409, {"code": "campaign_conflict", "reason": str(exc)}) from exc
        if service.director is not None:
            service.director.attach(campaign)
        start_engine(campaign, body)
        return ApiEnvelope(data=service.overview(campaign_id))

    @router.post("/{campaign_id}/stop")
    def stop_campaign(campaign_id: str) -> ApiEnvelope[dict[str, Any]]:
        campaign = service.campaigns.get(campaign_id)
        if campaign is None:
            raise HTTPException(404, {"code": "unknown_campaign", "reason": campaign_id})
        service.campaigns.set_status(campaign_id, "stopped", reason="stopped by the operator")
        if service.director is not None:
            service.director.detach("stopped by the operator")
        stop_engine()
        return ApiEnvelope(data=service.overview(campaign_id))

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
