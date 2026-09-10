"""The one place that decides what the engine tries next.

Before this existed, ``AutonomousEngine._cycle`` began with
``rng.choice(sorted(TEMPLATES))`` and a uniform parameter draw. That single line
was the whole of the search's creativity, and it is why the engine was a
parameter search: the set of templates was fixed, so the set of *questions* was
fixed too, and the only thing left to vary was the numbers.

The director replaces that line and nothing else. Everything downstream — the
memory prune, the pre-registration freeze, the experiment reservation, the
strategy write, conformance, determinism, the chronological split, the
validation grid, the judge, the burn-once holdout — is the existing code path,
unchanged. This is deliberate: the scientific infrastructure was not the
problem, and rebuilding it would have been the most expensive way to make the
system worse.

What the director adds is a decision. Each cycle it:

1. draws a **bucket** from the campaign's research allocation;
2. produces a **candidate** appropriate to that bucket — which for the discovery
   buckets means proposing a family or composing a new template, and for the
   refinement buckets means the parameter work the engine always did;
3. checks the proposal for **novelty** against everything already known, and
   refuses a restatement with the collision named;
4. records the question on the **frontier** and the claim in the **hypothesis
   graph**, so the next cycle knows this was asked;
5. hands back a candidate the engine can execute.

And after the cycle, ``observe`` reads the outcome, moves the frontier item,
updates the hypothesis, generates **follow-up questions** from what failed, and
queues anything that has earned validation.

**No arbitrary code is generated anywhere in here.** A new template is composed
as a :class:`~forge.strategy.ir.StrategyDefinition` — data — and rendered to
Python by the existing exporter, which is then put through the same static guard
and the same smoke test as an operator-written template. The director's
authority stops at choosing from a vocabulary; it never extends to writing
source.
"""

from __future__ import annotations

import contextlib
import random
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from forge.memory.research import FailureClass
from forge.research.allocation import Bucket, FrontierSignal, ResearchAllocation, adapt
from forge.research.campaign import Campaign, CampaignStore
from forge.research.followup import FollowUp, Observation, derive
from forge.research.frontier import FrontierState, ResearchFrontier, SearchKind
from forge.research.hypotheses import EdgeKind, HypothesisGraph, HypothesisStatus
from forge.research.information import estimate_cost, rank, value_of
from forge.research.journal import EventKind, ResearchJournal
from forge.research.literature import SourceStore, retrieve, topics_for
from forge.research.novelty import (
    Subject,
    assess,
    subjects_from_families,
    subjects_from_hypotheses,
    subjects_from_templates,
)
from forge.research.promotion import (
    Candidate as PromotionCandidate,
)
from forge.research.promotion import (
    Prerequisites,
    PromotionOutcome,
    PromotionQueue,
)
from forge.research.promotion import (
    assess as assess_promotion,
)
from forge.research.synthesis import ARCHETYPES, Archetype, archetypes_for, compose
from forge.strategy import TEMPLATES, TemplateRejected
from forge.strategy.export import to_python

#: How many literature results one retrieval keeps. Small on purpose: an
#: autonomous run that pulls fifty abstracts per cycle is spending its time
#: fetching rather than testing.
LITERATURE_RESULTS = 6

#: A generated template is piloted before it joins the catalogue. This is the
#: bar count for that pilot — enough to establish that it trades at a usable
#: rate on real data, far short of enough to say anything about edge.
PILOT_BARS = 20_000


@dataclass
class Candidate:
    """What the engine should run next, and why.

    Everything on here is something the engine already needed
    (``template_key``, ``parameters``) plus the research context that used not
    to exist. The context is what turns a row in the experiment table from "a
    thing that was tried" into "an attempt to answer this question".
    """

    template_key: str
    parameters: dict[str, float]
    bucket: Bucket
    search_kind: SearchKind
    hypothesis_id: str | None = None
    frontier_item_id: str | None = None
    research_sources: tuple[str, ...] = ()
    #: The prior attempt this was derived from, for the lineage edge.
    parent_experiment_id: str | None = None
    hypothesis_text: str | None = None
    novelty: float = 1.0
    #: Why this candidate exists, in a sentence. Shown in the campaign stream.
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template_key,
            "parameters": dict(self.parameters),
            "bucket": str(self.bucket),
            "search_kind": str(self.search_kind),
            "hypothesis_id": self.hypothesis_id,
            "frontier_item_id": self.frontier_item_id,
            "research_sources": list(self.research_sources),
            "novelty": round(self.novelty, 4),
            "rationale": self.rationale,
        }


@dataclass
class Refusal:
    """The director had nothing to offer this cycle, and why.

    A refusal is a real outcome and is recorded as one. A cycle that produced
    nothing because every proposal was a duplicate is information about the
    frontier; a cycle that silently fell back to a random draw would hide it.
    """

    reason: str
    bucket: Bucket
    duplicate: bool = False
    blocked: tuple[str, ...] = ()


@dataclass
class ObservationInput:
    """Everything the director needs to read one finished cycle.

    Assembled by the engine from objects it already holds. Deliberately plain
    data so the director can be tested without an engine.
    """

    candidate: Candidate
    strategy_id: str | None
    experiment_id: str
    status: str
    trades: int = 0
    net_pnl: float = 0.0
    grid_size: int = 1
    conformance_passed: bool | None = None
    determinism_reproduced: bool | None = None
    real_data: bool = True
    failure_class: FailureClass | None = None
    gate: str | None = None
    reason: str = ""
    verdict_id: str | None = None
    backtest_id: str | None = None
    decision: str = ""
    compute_units: float = 0.0
    #: A condition the effect was confined to, when the result carried one.
    condition: str = ""
    concentration: str = ""


class ResearchDirector:
    """Chooses what to research next, and reads what came of it.

    One instance per engine. Thread-safe: the engine runs several workers and
    all of them call ``next_candidate`` concurrently.
    """

    def __init__(
        self,
        *,
        campaigns: CampaignStore,
        frontier: ResearchFrontier,
        hypotheses: HypothesisGraph,
        journal: ResearchJournal,
        sources: SourceStore,
        promotion: PromotionQueue,
        families: Any,
        templates: Any,
        log: Any = None,
        library: Any = None,
        transport: Any = None,
    ) -> None:
        self.campaigns = campaigns
        self.frontier = frontier
        self.hypotheses = hypotheses
        self.journal = journal
        self.sources = sources
        self.promotion = promotion
        self.families = families
        self.templates = templates
        self.log = log
        self.library = library
        # Injected in tests so literature retrieval can be exercised without a
        # network. Production leaves it None and httpx uses its own transport.
        self.transport = transport
        self._lock = threading.RLock()
        self._campaign_id: str | None = None
        self._allocation: ResearchAllocation | None = None
        self._cycles_since_adapt = 0
        # Templates this director generated, so the campaign can attribute them
        # and the interface can tell a discovered template from a shipped one.
        self._generated: dict[str, dict[str, Any]] = {}
        self._topics: list[str] = []
        self._topic_index = 0

    # ── campaign lifecycle ───────────────────────────────────────────────────
    def attach(self, campaign: Campaign) -> None:
        """Bind the director to a campaign and prepare its research context."""
        with self._lock:
            self._campaign_id = campaign.campaign_id
            self._allocation = campaign.allocation
            self._cycles_since_adapt = 0
            self._topics = topics_for(campaign.objective)
            self._topic_index = 0
        released = self.promotion.release_running(campaign.campaign_id)
        if released:
            self._event(
                EventKind.CAMPAIGN_STARTED,
                f"released {released} validation claim(s) left by an interrupted run",
                level="warn",
            )
        self._event(
            EventKind.CAMPAIGN_STARTED,
            f"campaign '{campaign.name}' started on {campaign.dataset}",
            detail={
                "objective": campaign.objective,
                "allocation": campaign.allocation.as_dict(),
                "stopping": campaign.stopping.as_dict(),
                "capabilities": list(campaign.allowed_capabilities),
                "web_research": campaign.web_research,
            },
            level="pass",
        )

    def detach(self, reason: str) -> None:
        if self._campaign_id:
            self._event(EventKind.CAMPAIGN_STOPPED, reason, level="warn")
        with self._lock:
            self._campaign_id = None

    @property
    def campaign_id(self) -> str | None:
        return self._campaign_id

    def campaign(self) -> Campaign | None:
        return self.campaigns.get(self._campaign_id) if self._campaign_id else None

    # ── the decision ─────────────────────────────────────────────────────────
    def next_candidate(
        self, rng: random.Random, worker: int, policy: str
    ) -> Candidate | Refusal | None:
        """What to try next, or a refusal with its reason.

        Returns ``None`` only when no campaign is attached, which is how the
        engine keeps working exactly as before with no campaign running.
        """
        campaign = self.campaign()
        if campaign is None or not campaign.running:
            return None

        exhausted, why = campaign.exhausted()
        if exhausted:
            self.campaigns.set_status(campaign.campaign_id, "completed", reason=why)
            self._event(EventKind.CAMPAIGN_STOPPED, why, level="pass")
            self.detach(why)
            return Refusal(reason=why, bucket=Bucket.REFINE_PARAMETERS)

        allocation = self._current_allocation(campaign)
        bucket = allocation.draw(rng)
        self._event(
            EventKind.BUDGET_DRAWN,
            f"budget drew {bucket.value.replace('_', ' ').lower()}",
            detail={"bucket": str(bucket), "weights": allocation.as_dict()},
            worker=worker,
        )

        builders = {
            Bucket.DISCOVER_FAMILY: self._discover_family,
            Bucket.EXPLORE_HYPOTHESIS: self._explore_hypothesis,
            Bucket.ADVANCE_PROMISING: self._advance_promising,
            Bucket.REFINE_PARAMETERS: self._refine_parameters,
            Bucket.ROBUSTNESS: self._robustness,
        }
        try:
            outcome = builders[bucket](campaign, rng, worker)
        except Exception as exc:  # a bad proposal must never kill a worker
            self._event(
                EventKind.ERROR,
                f"proposing a {bucket.value.lower()} candidate failed: {type(exc).__name__}: {exc}",
                level="fail",
                worker=worker,
            )
            return Refusal(reason=str(exc), bucket=bucket)

        if isinstance(outcome, Refusal):
            self.campaigns.record(
                campaign.campaign_id,
                **(
                    {"duplicates_rejected": 1, "consecutive_duplicates": 1}
                    if outcome.duplicate
                    else {"blocked_proposals": 1}
                    if outcome.blocked
                    else {}
                ),
            )
            return outcome

        self.campaigns.record(campaign.campaign_id, bucket=str(bucket), consecutive_duplicates=0)
        return outcome

    def _current_allocation(self, campaign: Campaign) -> ResearchAllocation:
        """The campaign's allocation, re-tilted every so often from evidence.

        Re-computed on a cadence rather than every cycle: the frontier does not
        change fast enough for a per-cycle re-weight to mean anything, and eight
        workers each adapting on every draw would spend more time reading the
        frontier than testing it.
        """
        with self._lock:
            self._cycles_since_adapt += 1
            due = self._cycles_since_adapt >= 25 or self._allocation is None
            if not due:
                return self._allocation or campaign.allocation
            self._cycles_since_adapt = 0

        signal = FrontierSignal(
            state_counts=self.frontier.counts(campaign.campaign_id),
            duplicate_proposals=campaign.progress.duplicates_rejected,
            total_proposals=(campaign.progress.duplicates_rejected + campaign.progress.hypotheses),
            validation_backlog=self.promotion.pending(campaign.campaign_id),
            experiments=campaign.progress.experiments,
            mechanisms=self.hypotheses.distinct_mechanisms(campaign.campaign_id),
        )
        advice = adapt(campaign.allocation, signal)
        with self._lock:
            self._allocation = advice.allocation
        self._event(
            EventKind.ALLOCATION_ADAPTED,
            "; ".join(advice.reasons),
            detail={"weights": advice.allocation.as_dict(), "reasons": list(advice.reasons)},
        )
        return advice.allocation

    # ── the five builders ────────────────────────────────────────────────────
    def _discover_family(
        self, campaign: Campaign, rng: random.Random, worker: int
    ) -> Candidate | Refusal:
        """Propose a genuinely new research family, then a template for it.

        The novelty gate is what makes this worth having. A proposal that
        collides with an existing family is refused with the collision named,
        which is how ``mean_reversion_2`` never gets created.
        """
        archetype = self._pick_archetype(campaign, rng, prefer_unused=True)
        if archetype is None:
            return Refusal(
                reason="every archetype is already represented by a registered family",
                bucket=Bucket.DISCOVER_FAMILY,
                duplicate=True,
            )

        sources = self._maybe_retrieve(campaign, archetype.label, worker)
        proposal = Subject.of(
            f"proposed:{archetype.key}",
            statement=f"{archetype.label}. {archetype.claim.format(direction='directional')}",
            mechanism=archetype.mechanism,
            features=archetype.signature(),
            required_data=archetype.required_data,
        )
        verdict = assess(
            proposal, subjects_from_families(self.families.all()), claimed=SearchKind.FAMILY
        )
        self._event(
            EventKind.NOVELTY_CHECKED,
            f"family proposal '{archetype.key}': {verdict.reason}",
            detail=verdict.as_dict(),
            level="info" if verdict.admitted else "warn",
            worker=worker,
        )
        if not verdict.admitted:
            return Refusal(reason=verdict.reason, bucket=Bucket.DISCOVER_FAMILY, duplicate=True)

        missing = campaign.serves(archetype.required_data)
        if missing:
            self._admit_blocked(campaign, archetype, missing, worker)
            return Refusal(
                reason=f"requires {', '.join(missing)}, which this campaign cannot serve",
                bucket=Bucket.DISCOVER_FAMILY,
                blocked=missing,
            )

        family_key = f"discovered_{archetype.key}"[:40]
        if family_key not in self.families.all_keys():
            try:
                self.families.create(
                    key=family_key,
                    label=f"{archetype.label} (discovered)",
                    description=archetype.claim.format(direction="directional")[:600],
                    mechanism=archetype.mechanism,
                    data_requirements=archetype.required_data,
                    created_by="research-director",
                )
            except ValueError as exc:
                return Refusal(reason=str(exc), bucket=Bucket.DISCOVER_FAMILY, duplicate=True)
            self.campaigns.record(campaign.campaign_id, families_created=1)
            self._event(
                EventKind.FAMILY_CREATED,
                f"created family '{family_key}' — {archetype.mechanism[:120]}",
                subject=family_key,
                detail={
                    "key": family_key,
                    "mechanism": archetype.mechanism,
                    "required_data": list(archetype.required_data),
                    "novelty": verdict.novelty,
                    "nearest": verdict.nearest.as_dict() if verdict.nearest else None,
                },
                level="pass",
                worker=worker,
            )

        return self._candidate_from_archetype(
            campaign,
            archetype,
            rng,
            worker,
            bucket=Bucket.DISCOVER_FAMILY,
            search_kind=SearchKind.FAMILY,
            family=family_key,
            novelty=verdict.novelty,
            sources=sources,
            rationale=f"new family '{family_key}': {verdict.reason}",
        )

    def _explore_hypothesis(
        self, campaign: Campaign, rng: random.Random, worker: int
    ) -> Candidate | Refusal:
        """Test an open question on the frontier, or ask a new one.

        Open questions come first, and are ranked by information value rather
        than taken in order — a frontier with forty untested items should
        answer the cheap high-novelty ones before the expensive marginal ones.
        """
        open_items = [
            item
            for item in self.frontier.list(
                campaign.campaign_id,
                states=(FrontierState.UNTESTED, FrontierState.UNKNOWN, FrontierState.INCONCLUSIVE),
            )
            if not campaign.serves(item.required_data)
        ]
        if open_items:
            scored = [
                (
                    item,
                    value_of(
                        novelty=item.novelty,
                        state=item.state,
                        kind=item.search_kind,
                        prior_experiments=item.experiments,
                        data_available=True,
                        cost=estimate_cost(bars=250_000, grid_size=9, includes_validation=True),
                    ),
                )
                for item in open_items
            ]
            best, value = rank(scored)[0]
            archetype = self._archetype_for_item(best, rng)
            if archetype is not None:
                return self._candidate_from_archetype(
                    campaign,
                    archetype,
                    rng,
                    worker,
                    bucket=Bucket.EXPLORE_HYPOTHESIS,
                    search_kind=best.search_kind,
                    family=best.family
                    if best.family in self.families.all_keys()
                    else archetype.family,
                    novelty=best.novelty,
                    sources=best.sources,
                    frontier_item_id=best.item_id,
                    hypothesis_id=best.hypothesis_id,
                    rationale=(
                        f"open question, priority {value.priority:.4f}: {best.question[:140]}"
                    ),
                )

        archetype = self._pick_archetype(campaign, rng, prefer_unused=False)
        if archetype is None:
            return Refusal(
                reason="no archetype is runnable on this campaign's data capabilities",
                bucket=Bucket.EXPLORE_HYPOTHESIS,
                blocked=("BARS",),
            )
        sources = self._maybe_retrieve(campaign, archetype.label, worker)
        return self._candidate_from_archetype(
            campaign,
            archetype,
            rng,
            worker,
            bucket=Bucket.EXPLORE_HYPOTHESIS,
            search_kind=SearchKind.HYPOTHESIS,
            family=archetype.family,
            novelty=1.0,
            sources=sources,
            rationale=f"new hypothesis in {archetype.family} via {archetype.key}",
        )

    def _advance_promising(
        self, campaign: Campaign, rng: random.Random, worker: int
    ) -> Candidate | Refusal:
        """Build a structurally different test of a question already going well.

        The point of this bucket is that a promising result deserves a *second
        construction*, not a second parameter set — if the effect is real it
        should survive being measured a different way, and if it does not, that
        is the most informative thing the campaign could learn about it.
        """
        promising = [
            item
            for item in self.frontier.list(
                campaign.campaign_id,
                states=(FrontierState.PROMISING, FrontierState.PARTIALLY_EXPLORED),
            )
            if not campaign.serves(item.required_data)
        ]
        if not promising:
            return self._explore_hypothesis(campaign, rng, worker)

        item = promising[rng.randrange(len(promising))]
        archetype = self._archetype_for_item(item, rng, exclude_used=True)
        if archetype is None:
            return Refusal(
                reason=f"no unused construction remains for '{item.question[:80]}'",
                bucket=Bucket.ADVANCE_PROMISING,
                duplicate=True,
            )
        return self._candidate_from_archetype(
            campaign,
            archetype,
            rng,
            worker,
            bucket=Bucket.ADVANCE_PROMISING,
            search_kind=SearchKind.STRUCTURAL,
            family=item.family if item.family in self.families.all_keys() else archetype.family,
            novelty=item.novelty * 0.8,
            sources=item.sources,
            frontier_item_id=item.item_id,
            hypothesis_id=item.hypothesis_id,
            rationale=f"second construction for a promising result: {item.question[:140]}",
        )

    def _refine_parameters(
        self, campaign: Campaign, rng: random.Random, worker: int
    ) -> Candidate | Refusal:
        """The parameter draw the engine always did — now explicitly budgeted.

        This is not a lesser bucket. Refining a claim that already has evidence
        is real research; the failure was never that it happened, only that it
        was the *only* thing that happened.
        """
        pool = self._runnable_templates(campaign)
        if not pool:
            return Refusal(
                reason="no template is runnable on this campaign's data capabilities",
                bucket=Bucket.REFINE_PARAMETERS,
                blocked=("BARS",),
            )
        key = pool[rng.randrange(len(pool))]
        template = TEMPLATES[key]
        return Candidate(
            template_key=key,
            parameters=_draw(template, rng),
            bucket=Bucket.REFINE_PARAMETERS,
            search_kind=SearchKind.PARAMETER,
            hypothesis_text=template.hypothesis,
            novelty=0.2,
            rationale=f"parameter refinement of {key}",
        )

    def _robustness(
        self, campaign: Campaign, rng: random.Random, worker: int
    ) -> Candidate | Refusal:
        """Replication at declared defaults — the cheapest robustness check.

        A template run at the values its author committed to, with no search
        around it, is the one configuration a selection-integrity failure cannot
        explain. It costs one backtest and it is the control everything else in
        the campaign is measured against.
        """
        pool = self._runnable_templates(campaign)
        if not pool:
            return Refusal(
                reason="no template is runnable on this campaign's data capabilities",
                bucket=Bucket.ROBUSTNESS,
                blocked=("BARS",),
            )
        key = pool[rng.randrange(len(pool))]
        template = TEMPLATES[key]
        return Candidate(
            template_key=key,
            parameters={p.name: float(p.default) for p in template.parameters},
            bucket=Bucket.ROBUSTNESS,
            search_kind=SearchKind.PARAMETER,
            hypothesis_text=template.hypothesis,
            novelty=0.15,
            rationale=f"replication of {key} at its declared defaults, no neighbourhood searched",
        )

    # ── shared machinery ─────────────────────────────────────────────────────
    def _runnable_templates(self, campaign: Campaign) -> list[str]:
        return sorted(
            key
            for key, template in TEMPLATES.items()
            if not campaign.serves((template.data_requirement,))
        )

    def _pick_archetype(
        self, campaign: Campaign, rng: random.Random, *, prefer_unused: bool
    ) -> Archetype | None:
        """An archetype this campaign can actually run.

        ``prefer_unused`` biases family discovery towards constructions that
        have not already been turned into a family, so the discovery bucket does
        not spend its budget re-proposing the same one and being refused.
        """
        runnable = [a for a in ARCHETYPES.values() if not campaign.serves(a.required_data)]
        if not runnable:
            return None
        if prefer_unused:
            known = self.families.all_keys()
            fresh = [a for a in runnable if f"discovered_{a.key}"[:40] not in known]
            if fresh:
                runnable = fresh
        return runnable[rng.randrange(len(runnable))]

    def _archetype_for_item(
        self, item: Any, rng: random.Random, *, exclude_used: bool = False
    ) -> Archetype | None:
        """A construction suited to a frontier item's family.

        ``exclude_used`` drops constructions this item has already been tested
        with, which is what makes ``ADVANCE_PROMISING`` produce a genuinely
        different measurement rather than the same one again.
        """
        pool = archetypes_for(item.family)
        if exclude_used:
            used = {
                meta["archetype"]
                for meta in self._generated.values()
                if meta.get("frontier_item_id") == item.item_id
            }
            pool = [a for a in pool if a.key not in used] or []
        return pool[rng.randrange(len(pool))] if pool else None

    def _candidate_from_archetype(
        self,
        campaign: Campaign,
        archetype: Archetype,
        rng: random.Random,
        worker: int,
        *,
        bucket: Bucket,
        search_kind: SearchKind,
        family: str,
        novelty: float,
        sources: Sequence[str] = (),
        frontier_item_id: str | None = None,
        hypothesis_id: str | None = None,
        rationale: str = "",
    ) -> Candidate | Refusal:
        """Compose a definition, admit it as a template, and record the research.

        The order matters. The template is registered *before* the hypothesis is
        recorded, because a hypothesis whose implementation was rejected would
        be a claim the system cannot test and should not be on the frontier as
        if it could.
        """
        seed = rng.randrange(1_000_000)
        composition = compose(
            archetype=archetype,
            family=family,
            symbol=campaign.symbol,
            timeframe=campaign.timeframe,
            seed=seed,
            derived_from=f"campaign:{campaign.campaign_id}",
        )
        definition = composition.definition

        template_key = f"gen_{archetype.key}_{definition.definition_hash[:8]}"[:50]
        registered_here = template_key not in TEMPLATES
        if registered_here:
            registered = self._register_template(
                campaign, composition, template_key, family, worker, sources
            )
            if isinstance(registered, Refusal):
                return registered

        # The claim, checked against every claim already made. A structural
        # variant that turns out to restate an existing hypothesis is refused
        # here rather than becoming a near-duplicate node in the graph.
        proposal = Subject.of(
            template_key,
            statement=definition.hypothesis,
            mechanism=archetype.mechanism,
            family=family,
            features=archetype.signature(),
            required_data=archetype.required_data,
        )
        corpus = subjects_from_hypotheses(
            self.hypotheses.list(campaign.campaign_id, limit=400)
        ) + subjects_from_templates({k: v for k, v in TEMPLATES.items() if k != template_key})
        verdict = assess(proposal, corpus, claimed=search_kind)
        if not verdict.admitted and search_kind is not SearchKind.PARAMETER:
            # Take the template back out. It was registered a moment ago to
            # prove the claim was implementable, and the claim turned out to be
            # one already on the frontier — leaving it would put a template in
            # the catalogue that no hypothesis points at, and inflate the
            # "templates created" count with work that answered nothing.
            if registered_here:
                self._withdraw_template(template_key)
            self._event(
                EventKind.HYPOTHESIS_REJECTED,
                verdict.reason,
                detail={**verdict.as_dict(), "withdrew_template": template_key},
                level="warn",
                worker=worker,
            )
            return Refusal(reason=verdict.reason, bucket=bucket, duplicate=True)

        item_id = frontier_item_id
        if item_id is None:
            item = self.frontier.admit(
                campaign_id=campaign.campaign_id,
                question=definition.hypothesis,
                family=family,
                mechanism=archetype.mechanism,
                search_kind=verdict.kind,
                required_data=archetype.required_data,
                novelty=verdict.novelty,
                sources=sources,
                origin=f"director:{bucket.value.lower()}",
                reason=rationale or "proposed by the research director",
            )
            item_id = item.item_id

        node = self.hypotheses.propose(
            campaign_id=campaign.campaign_id,
            statement=definition.hypothesis,
            prediction=definition.falsifiable_prediction,
            mechanism=archetype.mechanism,
            family=family,
            search_kind=verdict.kind,
            parent_id=hypothesis_id,
            required_data=archetype.required_data,
            expected_horizon=campaign.timeframe,
            origin=f"director:{bucket.value.lower()}",
            novelty=verdict.novelty,
            nearest_id=verdict.nearest.key if verdict.nearest else None,
            similarity=verdict.nearest.combined if verdict.nearest else 0.0,
            sources=sources,
            frontier_item_id=item_id,
        )
        self.hypotheses.link(
            node.hypothesis_id, EdgeKind.IMPLEMENTED_BY, template_key, note=archetype.key
        )
        self.frontier.attach_hypothesis(item_id, node.hypothesis_id)
        self._refresh_counts(campaign)
        self._event(
            EventKind.HYPOTHESIS_PROPOSED,
            f"hypothesis: {definition.hypothesis[:160]}",
            subject=node.hypothesis_id,
            detail={
                "hypothesis_id": node.hypothesis_id,
                "frontier_item_id": item_id,
                "template": template_key,
                "family": family,
                "search_kind": str(verdict.kind),
                "novelty": verdict.novelty,
                "nearest": verdict.nearest.as_dict() if verdict.nearest else None,
                "required_data": list(archetype.required_data),
                "sources": list(sources),
            },
            level="pass",
            worker=worker,
        )

        template = TEMPLATES[template_key]
        return Candidate(
            template_key=template_key,
            parameters=_draw(template, rng),
            bucket=bucket,
            search_kind=verdict.kind,
            hypothesis_id=node.hypothesis_id,
            frontier_item_id=item_id,
            research_sources=tuple(sources),
            hypothesis_text=definition.hypothesis,
            novelty=verdict.novelty,
            rationale=rationale or verdict.reason,
        )

    def _register_template(
        self,
        campaign: Campaign,
        composition: Any,
        template_key: str,
        family: str,
        worker: int,
        sources: Sequence[str],
    ) -> Refusal | None:
        """Put a composed definition into the catalogue, or refuse it with a reason.

        Everything protective here is the existing machinery:
        ``to_python`` renders the definition, ``TemplateStore.create`` applies
        the static guard and runs the smoke test on synthetic bars, and a
        template that fails either never enters ``TEMPLATES``. The director adds
        no new trust.
        """
        definition = composition.definition
        report = to_python(definition)
        try:
            template = self.templates.create(
                key=template_key,
                name=definition.name,
                family=family,
                hypothesis=definition.hypothesis,
                falsifiable_prediction=definition.falsifiable_prediction,
                parameters=[p.model_dump() for p in definition.parameters],
                source=report.code,
                warmup_bars=min(5_000, definition.required_warmup()),
                known_families=self.families.all_keys(),
                existing_templates=set(TEMPLATES),
                created_by="research-director",
                research_sources=tuple(sources),
            )
        except TemplateRejected as exc:
            self._event(
                EventKind.TEMPLATE_REJECTED,
                f"generated template '{template_key}' refused: {exc}",
                subject=template_key,
                detail={"reason": str(exc), "archetype": composition.archetype},
                level="fail",
                worker=worker,
            )
            return Refusal(reason=str(exc), bucket=Bucket.DISCOVER_FAMILY)

        TEMPLATES[template_key] = template
        with self._lock:
            self._generated[template_key] = {
                **composition.as_dict(),
                "family": family,
                "campaign_id": campaign.campaign_id,
            }
        self.campaigns.record(campaign.campaign_id, templates_created=1)
        self._event(
            EventKind.TEMPLATE_CREATED,
            f"template '{template_key}' created from the {composition.archetype} construction "
            f"and passed the static guard and its smoke test",
            subject=template_key,
            detail=self._generated[template_key],
            level="pass",
            worker=worker,
        )
        return None

    def _withdraw_template(self, template_key: str) -> None:
        """Remove a generated template that no admitted hypothesis needs.

        Reverses `_register_template` exactly: out of the shared catalogue, off
        disk, and out of the attribution record and the campaign's count. A
        template left behind here would be reachable by the parameter-refinement
        bucket forever, which would quietly turn a refused duplicate into a
        thing the engine keeps testing.
        """
        TEMPLATES.pop(template_key, None)
        with self._lock:
            self._generated.pop(template_key, None)
        with contextlib.suppress(KeyError):
            self.templates.delete(template_key)
        if self._campaign_id:
            self.campaigns.record(self._campaign_id, templates_created=-1)

    def _admit_blocked(
        self, campaign: Campaign, archetype: Archetype, missing: Sequence[str], worker: int
    ) -> None:
        """Record a question this installation cannot answer, as blocked.

        Not as failed, and not silently discarded. The distinction is the whole
        reason ``BLOCKED_BY_DATA`` exists: the question may well be true, and a
        later installation with the data should find it waiting rather than
        having to think of it again.
        """
        self.frontier.admit(
            campaign_id=campaign.campaign_id,
            question=archetype.claim.format(direction="directional"),
            family=archetype.family,
            mechanism=archetype.mechanism,
            search_kind=SearchKind.MECHANISM,
            required_data=archetype.required_data,
            missing_data=missing,
            origin="director:discover_family",
        )
        self._event(
            EventKind.DATA_BLOCKED,
            f"'{archetype.label}' needs {', '.join(missing)}, which this campaign cannot "
            "serve; recorded as blocked rather than tested",
            detail={"archetype": archetype.key, "missing": list(missing)},
            level="warn",
            worker=worker,
        )

    def _maybe_retrieve(self, campaign: Campaign, hint: str, worker: int) -> tuple[str, ...]:
        """Search the literature, when the campaign allows it.

        Returns the ids of what was found, or an empty tuple. A retrieval that
        found nothing is still recorded, because "we looked and the literature
        had nothing" is a research fact and an absence of records is not.
        """
        if not campaign.web_research:
            return ()
        with self._lock:
            if not self._topics:
                self._topics = topics_for(campaign.objective, extra=[hint])
            topic = self._topics[self._topic_index % len(self._topics)]
            self._topic_index += 1

        report = retrieve(topic, limit=LITERATURE_RESULTS, transport=self.transport)
        stored = self.sources.record(campaign.campaign_id, report)
        self._event(
            EventKind.LITERATURE_SEARCHED,
            (
                f"searched '{topic}' — {len(stored)} source(s), "
                f"{sum(len(s.claims) for s in stored)} extracted claim(s)"
            )
            if report.ok
            else f"literature search for '{topic}' failed: {report.error}",
            detail=report.as_dict(),
            level="info" if report.ok else "warn",
            worker=worker,
        )
        if stored:
            self.campaigns.record(campaign.campaign_id, sources_retrieved=len(stored))
            for source in stored[:3]:
                self._event(
                    EventKind.SOURCE_FOUND,
                    f"{source.title[:140]} ({source.source})",
                    subject=source.source_id,
                    detail=source.as_dict(),
                    worker=worker,
                )
        return tuple(s.source_id for s in stored[:3])

    # ── reading the outcome ──────────────────────────────────────────────────
    def observe(self, outcome: ObservationInput) -> None:
        """Read one finished cycle and expand the frontier from it.

        This is the half of the loop that makes the search a research programme
        rather than a queue: the result moves a frontier item, updates a
        hypothesis, generates the questions the failure implies, and queues the
        candidate for validation when it has earned it.
        """
        campaign = self.campaign()
        if campaign is None:
            return
        candidate = outcome.candidate
        try:
            self._record_outcome(campaign, outcome, candidate)
        except Exception as exc:  # observation must never fail a research cycle
            self._event(
                EventKind.ERROR,
                f"recording the outcome of {outcome.experiment_id} failed: "
                f"{type(exc).__name__}: {exc}",
                level="fail",
            )

    def _record_outcome(
        self, campaign: Campaign, outcome: ObservationInput, candidate: Candidate
    ) -> None:
        self.campaigns.record(
            campaign.campaign_id,
            experiments=1,
            compute_units=outcome.compute_units,
        )
        if candidate.frontier_item_id:
            self.frontier.record_experiment(candidate.frontier_item_id)

        state, reason, status = _interpret(outcome)

        if candidate.frontier_item_id:
            self.frontier.transition(
                candidate.frontier_item_id,
                state,
                reason=reason,
                actor="engine",
                evidence=[e for e in (outcome.verdict_id, outcome.backtest_id) if e],
            )
            self._event(
                EventKind.FRONTIER_UPDATED,
                f"{state.value}: {reason}",
                subject=candidate.frontier_item_id,
                detail={"state": str(state), "reason": reason, "strategy": outcome.strategy_id},
                level="pass" if state is FrontierState.VALIDATED else "info",
            )
        if candidate.hypothesis_id:
            self.hypotheses.set_status(candidate.hypothesis_id, status)
            self.hypotheses.link(
                candidate.hypothesis_id,
                EdgeKind.TESTED_BY,
                outcome.experiment_id,
                note=outcome.status,
            )
            if outcome.verdict_id:
                self.hypotheses.link(
                    candidate.hypothesis_id, EdgeKind.RESULT, outcome.verdict_id, note=reason
                )

        counters: dict[str, int] = {}
        if state is FrontierState.FAILED:
            counters["failures"] = 1
        elif state is FrontierState.PROMISING:
            counters["promising"] = 1
        elif state is FrontierState.VALIDATED:
            counters["validated"] = 1
        elif state is FrontierState.INCONCLUSIVE:
            counters["inconclusive"] = 1
        if counters:
            self.campaigns.record(campaign.campaign_id, **counters)

        self._event(
            EventKind.EXPERIMENT_FINISHED,
            f"{outcome.strategy_id or candidate.template_key}: {reason}",
            subject=outcome.strategy_id,
            detail={
                **candidate.as_dict(),
                "status": outcome.status,
                "trades": outcome.trades,
                "net_pnl": round(outcome.net_pnl, 2),
                "decision": outcome.decision,
                "gate": outcome.gate,
            },
            level="pass" if outcome.decision == "PASS" else "info",
        )

        self._queue_validation(campaign, outcome, candidate)
        self._generate_followups(campaign, outcome, candidate)

    def _queue_validation(
        self, campaign: Campaign, outcome: ObservationInput, candidate: Candidate
    ) -> None:
        """Enqueue a candidate that has earned validation, or say what it missed."""
        if not outcome.strategy_id:
            return
        promotion_candidate = PromotionCandidate(
            strategy_id=outcome.strategy_id,
            hypothesis_id=candidate.hypothesis_id,
            frontier_item_id=candidate.frontier_item_id,
            experiment_id=outcome.experiment_id,
            trades=outcome.trades,
            net_pnl=outcome.net_pnl,
            grid_size=outcome.grid_size,
            conformance_passed=outcome.conformance_passed,
            determinism_reproduced=outcome.determinism_reproduced,
            real_data=outcome.real_data,
        )
        eligibility = assess_promotion(promotion_candidate, Prerequisites())
        if not eligibility.eligible:
            return
        entry = self.promotion.enqueue(
            campaign.campaign_id,
            promotion_candidate,
            eligibility,
            priority=candidate.novelty,
        )
        if entry is None:
            return
        self.campaigns.record(campaign.campaign_id, validation_candidates=1)
        self._event(
            EventKind.VALIDATION_QUEUED,
            f"{outcome.strategy_id} earned validation: {eligibility.reasons[0]}",
            subject=outcome.strategy_id,
            detail={"entry_id": entry, "reasons": list(eligibility.reasons)},
            level="pass",
        )

    def _generate_followups(
        self, campaign: Campaign, outcome: ObservationInput, candidate: Candidate
    ) -> None:
        """Turn what happened into the questions it implies.

        Every generated question goes through the same novelty gate and the same
        falsifiability checks as anything else. A follow-up that restates a
        question already on the frontier is dropped, which is what stops a
        repeated failure mode from filling the graph with the same three
        sentences.
        """
        if outcome.failure_class is None and not outcome.condition:
            return
        observation = Observation(
            family=TEMPLATES[candidate.template_key].family
            if candidate.template_key in TEMPLATES
            else "",
            mechanism=candidate.rationale,
            hypothesis=candidate.hypothesis_text or "",
            template=candidate.template_key,
            failure_class=outcome.failure_class,
            gate=outcome.gate,
            condition=outcome.condition,
            concentration=outcome.concentration,
            trades=outcome.trades,
            net_pnl=outcome.net_pnl,
            was_profitable=outcome.net_pnl > 0,
        )
        followups = derive(observation)
        if not followups:
            return

        corpus = subjects_from_hypotheses(self.hypotheses.list(campaign.campaign_id, limit=400))
        admitted = 0
        for followup in followups:
            if self._admit_followup(campaign, followup, candidate, corpus):
                admitted += 1
        if admitted:
            self.campaigns.record(campaign.campaign_id, followups_generated=admitted)
            self._refresh_counts(campaign)

    def _admit_followup(
        self,
        campaign: Campaign,
        followup: FollowUp,
        candidate: Candidate,
        corpus: list[Subject],
    ) -> bool:
        missing = campaign.serves(followup.required_data)
        proposal = Subject.of(
            "followup",
            statement=followup.statement,
            mechanism=followup.mechanism,
            family=followup.family,
            required_data=followup.required_data,
        )
        verdict = assess(proposal, corpus, claimed=followup.search_kind)
        if not verdict.admitted:
            return False

        item = self.frontier.admit(
            campaign_id=campaign.campaign_id,
            question=followup.statement,
            family=followup.family,
            mechanism=followup.mechanism,
            search_kind=verdict.kind,
            required_data=followup.required_data,
            missing_data=missing,
            novelty=verdict.novelty,
            origin="failure-derived",
            reason=followup.rationale,
        )
        node = self.hypotheses.propose(
            campaign_id=campaign.campaign_id,
            statement=followup.statement,
            prediction=followup.prediction,
            mechanism=followup.mechanism,
            family=followup.family,
            search_kind=verdict.kind,
            parent_id=candidate.hypothesis_id,
            required_data=followup.required_data,
            missing_data=missing,
            expected_horizon=campaign.timeframe,
            origin="failure-derived",
            novelty=verdict.novelty,
            nearest_id=verdict.nearest.key if verdict.nearest else None,
            similarity=verdict.nearest.combined if verdict.nearest else 0.0,
            frontier_item_id=item.item_id,
        )
        self.frontier.attach_hypothesis(item.item_id, node.hypothesis_id)
        self._event(
            EventKind.FOLLOWUP_GENERATED,
            f"from {followup.rationale}: {followup.statement[:150]}",
            subject=node.hypothesis_id,
            detail={
                **followup.as_dict(),
                "hypothesis_id": node.hypothesis_id,
                "frontier_item_id": item.item_id,
                "parent_hypothesis_id": candidate.hypothesis_id,
                "novelty": verdict.novelty,
                "blocked": list(missing),
            },
            level="pass",
        )
        return True

    # ── bookkeeping ──────────────────────────────────────────────────────────
    def _refresh_counts(self, campaign: Campaign) -> None:
        """Recount hypotheses and mechanisms from the graph rather than adding.

        A proposal that deduplicated into an existing node must not increment
        anything, and only a query can tell the difference.
        """
        self.campaigns.set_progress(
            campaign.campaign_id,
            hypotheses=len(self.hypotheses.list(campaign.campaign_id, limit=10_000)),
            mechanisms=self.hypotheses.distinct_mechanisms(campaign.campaign_id),
        )

    def _event(
        self,
        kind: EventKind,
        message: str,
        *,
        level: str = "info",
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
        worker: int | None = None,
    ) -> None:
        """Write to the campaign journal and mirror one line to the operator log."""
        campaign_id = self._campaign_id
        if campaign_id is None:
            return
        self.journal.record(
            campaign_id,
            kind,
            message,
            level=level,
            subject=subject,
            detail=detail,
            worker=worker,
        )
        if self.log is not None:
            self.log.record("RESEARCH", message, level, subject)

    def generated_templates(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._generated)


def _draw(template: Any, rng: random.Random) -> dict[str, float]:
    """One point from a template's declared parameter grid."""
    params: dict[str, float] = {}
    for spec in template.parameters:
        steps = max(1, round((spec.high - spec.low) / spec.step))
        value = spec.low + spec.step * rng.randint(0, steps)
        params[spec.name] = round(min(value, spec.high), 4)
    return params


def _interpret(
    outcome: ObservationInput,
) -> tuple[FrontierState, str, HypothesisStatus]:
    """What one cycle's result means for the frontier and the hypothesis.

    The mapping is deliberately conservative in one direction: nothing here
    produces ``FAILED`` unless a gate genuinely failed, and a candidate that was
    merely screened out or never measured lands on ``PARTIALLY_EXPLORED`` or
    ``INCONCLUSIVE``. Turning "not measured" into "refuted" is the single
    mistake that would undo the whole point of the frontier.
    """
    if outcome.decision == "PASS":
        return (
            FrontierState.VALIDATED,
            f"cleared the gate ladder on {outcome.trades} trades, net {outcome.net_pnl:+.2f}",
            HypothesisStatus.VALIDATED,
        )
    if outcome.status == "no_trades":
        return (
            FrontierState.EXHAUSTED,
            "the construction produced no trades at these settings",
            HypothesisStatus.INCONCLUSIVE,
        )
    if outcome.failure_class is not None:
        return (
            FrontierState.FAILED,
            outcome.reason or f"failed {outcome.gate or 'a gate'}",
            HypothesisStatus.REFUTED,
        )
    if outcome.status in {"inconclusive", "screened"}:
        if outcome.net_pnl > 0 and outcome.trades >= 30:
            return (
                FrontierState.PROMISING,
                f"development net {outcome.net_pnl:+.2f} over {outcome.trades} trades; "
                "not yet validated",
                HypothesisStatus.SUPPORTED,
            )
        return (
            FrontierState.PARTIALLY_EXPLORED,
            outcome.reason
            or f"screened out at {outcome.trades} trades, net {outcome.net_pnl:+.2f}",
            HypothesisStatus.INCONCLUSIVE,
        )
    if outcome.net_pnl > 0 and outcome.trades >= 30:
        return (
            FrontierState.PROMISING,
            f"development net {outcome.net_pnl:+.2f} over {outcome.trades} trades",
            HypothesisStatus.SUPPORTED,
        )
    return (
        FrontierState.INCONCLUSIVE,
        outcome.reason or "the evidence did not decide the question",
        HypothesisStatus.INCONCLUSIVE,
    )


__all__ = [
    "Candidate",
    "ObservationInput",
    "PromotionOutcome",
    "Refusal",
    "ResearchDirector",
]
