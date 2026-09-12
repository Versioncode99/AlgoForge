"""The layer above campaigns: who gets a worker, and why.

The engine used to be `Engine → Campaign → Loop`. One engine, one director, one
campaign, and a worker's research policy was a function of its **thread index**.
That shape has three consequences worth naming, because they are the ones this
module removes.

A second campaign could not run at all. The store refused it, and the engine had
nowhere to put it: the director held a single ``_campaign_id``.

Workers could not be allocated. Eight threads always drew from one pool at one
rate, so "give the NQ campaign more compute than the volatility one" was not a
sentence the system could represent.

And a stalled campaign could not be noticed, because there was only ever one and
its stall was the engine's stall.

:class:`ResearchOrchestrator` is a **scheduler and nothing else**. It decides
which campaign a worker serves on this cycle. It does not decide what to
research — the campaign's own director does that — and it cannot decide what a
result means, which remains the judge's job alone. The separation is deliberate
and load-bearing: a scheduler that could also weigh evidence would be a way to
give a favoured campaign an easier gate.

**Allocation.** Workers are dealt to running campaigns by weight, where weight is
priority scaled by a *health* factor the orchestrator observes rather than one
the campaign declares. A campaign returning nothing but duplicates loses share to
one that is producing; a campaign that recovers gets it back. Every campaign with
at least one worker keeps at least one, because a campaign starved to zero can
never demonstrate that it recovered.
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from forge.research.agents import AgentRegistry, AgentRole, AgentState
from forge.research.campaign import Campaign, CampaignStore
from forge.research.runtime import Outcome, RuntimeState

#: How many recent outcomes per campaign feed the health estimate. Small enough
#: to react within a couple of minutes of eight workers, large enough that three
#: unlucky duplicates in a row do not cost a campaign its workers.
HEALTH_WINDOW = 60

#: The floor on a campaign's share. A campaign at zero workers produces nothing,
#: and a campaign producing nothing never recovers its health — so starving one
#: completely is a decision that cannot be revisited, which is not a decision a
#: scheduler should be able to make.
MIN_HEALTH = 0.15

#: Consecutive barren cycles before the orchestrator calls a campaign stalled and
#: asks its director to widen. Higher than the runtime watchdog's threshold: the
#: watchdog describes the *engine*, this decides whether to intervene in one
#: campaign's research.
STALL_THRESHOLD = 40


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class CampaignRuntime:
    """What the orchestrator has observed about one campaign.

    Deliberately separate from ``CampaignProgress``, which is the campaign's own
    durable record of what it did. This is the scheduler's short-term view of
    how it is *going*, and it is allowed to be lossy.
    """

    campaign_id: str
    workers: int = 0
    outcomes: deque[Outcome] = field(default_factory=lambda: deque(maxlen=HEALTH_WINDOW))
    last_progress_at: str = ""
    barren_run: int = 0
    assigned: int = 0

    def health(self) -> float:
        """Share of recent cycles that produced something, floored.

        A campaign with no history scores 1.0: an unproven campaign is given the
        benefit of the doubt for its first window, because scoring it zero would
        deny it the workers it needs to earn a score.

        That 1.0 is a **scheduling weight, not a measurement**, and the two are
        not interchangeable. Read `observed` before showing this number to
        anybody: a campaign that has run no cycles has no health, and rendering
        its weight as "100%" tells an operator their campaign is doing
        perfectly at the exact moment it has done nothing at all.
        """
        if not self.outcomes:
            return 1.0
        progressed = sum(1 for outcome in self.outcomes if outcome is Outcome.PROGRESS)
        return max(MIN_HEALTH, progressed / len(self.outcomes))

    @property
    def observed(self) -> int:
        """Cycles behind `health`. Zero means the score is a prior, not a result."""
        return len(self.outcomes)

    def stalled(self) -> bool:
        return self.barren_run >= STALL_THRESHOLD

    def as_dict(self) -> dict[str, Any]:
        counts = Counter(str(outcome) for outcome in self.outcomes)
        return {
            "campaign_id": self.campaign_id,
            "workers": self.workers,
            "health": round(self.health(), 3),
            "health_observed": self.observed,
            "barren_run": self.barren_run,
            "stalled": self.stalled(),
            "last_progress_at": self.last_progress_at,
            "assigned": self.assigned,
            "recent_outcomes": {str(o): counts.get(str(o), 0) for o in Outcome},
        }


@dataclass(frozen=True)
class Assignment:
    """What one worker should do this cycle."""

    worker_id: str
    campaign_id: str | None
    agent_id: str | None = None
    role: AgentRole | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "campaign_id": self.campaign_id,
            "agent_id": self.agent_id,
            "role": str(self.role) if self.role else None,
            "reason": self.reason,
        }


class ResearchOrchestrator:
    """Deals workers to campaigns, and campaigns to agents.

    Thread-safe. The engine's workers call :meth:`assign` at the top of every
    cycle and :meth:`observe` at the bottom of one.
    """

    def __init__(
        self,
        *,
        campaigns: CampaignStore,
        agents: AgentRegistry,
        log: Any = None,
    ) -> None:
        self.campaigns = campaigns
        self.agents = agents
        self.log = log
        self._lock = threading.RLock()
        self._runtimes: dict[str, CampaignRuntime] = {}
        # Deterministic round-robin over the weighted plan, so eight workers
        # spread across campaigns instead of all landing on the heaviest.
        self._plan: list[str] = []
        self._cursor = 0
        self._plan_version = ""

    # ── allocation ───────────────────────────────────────────────────────────
    def running(self) -> list[Campaign]:
        return self.campaigns.running()

    def _rebuild_plan(self, campaigns: list[Campaign], workers: int) -> list[str]:
        """A worker-length list naming which campaign each slot serves.

        Weight is ``priority * health``. Priority is the operator's intent and
        health is the orchestrator's observation, and multiplying them means a
        high-priority campaign that has stopped producing still yields ground —
        slowly, and never all of it — to one that is producing.
        """
        if not campaigns:
            return []
        weights: list[tuple[str, float]] = []
        for campaign in campaigns:
            runtime = self._runtime(campaign.campaign_id)
            weights.append(
                (campaign.campaign_id, max(1.0, float(campaign.priority)) * runtime.health())
            )
        total = sum(weight for _, weight in weights) or 1.0
        slots = max(1, int(workers))

        # Every running campaign gets one slot before any campaign gets two.
        # Proportional allocation alone starves a new low-priority campaign to
        # zero, and a campaign with no workers can never earn a better score.
        plan: list[str] = [campaign_id for campaign_id, _ in weights][:slots]
        remaining = slots - len(plan)
        if remaining > 0:
            shares = sorted(
                ((campaign_id, weight / total * remaining) for campaign_id, weight in weights),
                key=lambda item: item[1],
                reverse=True,
            )
            for campaign_id, share in shares:
                whole = int(share)
                plan.extend([campaign_id] * min(whole, remaining))
                remaining -= min(whole, remaining)
                if remaining <= 0:
                    break
            # Whatever rounding left over goes to the heaviest campaign.
            if remaining > 0 and shares:
                plan.extend([shares[0][0]] * remaining)
        return plan

    def assign(self, worker_id: str, *, workers: int) -> Assignment:
        """Which campaign this worker serves on this cycle.

        Returns an assignment with ``campaign_id=None`` when nothing is running,
        which is how the engine keeps working with no campaign at all — the mode
        it had before campaigns existed, and still a legitimate one.
        """
        campaigns = self.running()
        with self._lock:
            version = "|".join(
                f"{c.campaign_id}:{c.priority}:{self._runtime(c.campaign_id).health():.2f}"
                for c in campaigns
            ) + f"#{workers}"
            if version != self._plan_version:
                self._plan = self._rebuild_plan(campaigns, workers)
                self._plan_version = version
                self._cursor = 0
                for runtime in self._runtimes.values():
                    runtime.workers = 0
                for campaign_id in self._plan:
                    self._runtime(campaign_id).workers += 1
            if not self._plan:
                return Assignment(
                    worker_id=worker_id, campaign_id=None, reason="no campaign running"
                )
            campaign_id = self._plan[self._cursor % len(self._plan)]
            self._cursor += 1
            runtime = self._runtime(campaign_id)
            runtime.assigned += 1

        agent = self._pick_agent(campaign_id)
        return Assignment(
            worker_id=worker_id,
            campaign_id=campaign_id,
            agent_id=agent.agent_id if agent else None,
            role=agent.role if agent else None,
            reason=(
                f"{runtime.workers} of {max(1, workers)} worker(s), health "
                f"{runtime.health():.0%}"
            ),
        )

    def _pick_agent(self, campaign_id: str) -> Any:
        """The least-loaded eligible agent on this campaign, or ``None``.

        Least-loaded by experiments run, so a new agent is given work before a
        busy one is given more. There is no attempt to be clever here: the role
        biases *what the director proposes*, and the director is where research
        judgement belongs.
        """
        eligible = self.agents.eligible(campaign_id)
        if not eligible:
            return None
        return min(eligible, key=lambda agent: (agent.experiments, agent.created_at))

    # ── observation ──────────────────────────────────────────────────────────
    def observe(self, campaign_id: str | None, outcome: Outcome, *, agent_id: str = "") -> None:
        """Record what one cycle produced, for scheduling purposes only.

        This never writes to the campaign's durable progress — the director owns
        that — and it never touches evidence.
        """
        if not campaign_id:
            return
        with self._lock:
            runtime = self._runtime(campaign_id)
            runtime.outcomes.append(outcome)
            if outcome is Outcome.PROGRESS:
                runtime.barren_run = 0
                runtime.last_progress_at = _now()
            else:
                runtime.barren_run += 1
        if agent_id:
            try:
                if outcome is Outcome.PROGRESS:
                    self.agents.record(agent_id, experiments=1, result="cycle produced a result")
                elif outcome is Outcome.ERROR:
                    self.agents.set_state(agent_id, AgentState.RUNNING, error="cycle raised")
                else:
                    self.agents.beat(agent_id, task=f"last cycle: {outcome}")
            except Exception:  # an agent bookkeeping failure must not stop research
                pass

    def _runtime(self, campaign_id: str) -> CampaignRuntime:
        runtime = self._runtimes.get(campaign_id)
        if runtime is None:
            runtime = CampaignRuntime(campaign_id=campaign_id)
            self._runtimes[campaign_id] = runtime
        return runtime

    # ── diagnosis ────────────────────────────────────────────────────────────
    def stalled_campaigns(self) -> list[str]:
        with self._lock:
            return sorted(r.campaign_id for r in self._runtimes.values() if r.stalled())

    def snapshot(self, *, engine_state: RuntimeState | None = None) -> dict[str, Any]:
        """Everything the Research Control Center needs about allocation."""
        campaigns = self.running()
        with self._lock:
            runtimes = {c.campaign_id: self._runtime(c.campaign_id).as_dict() for c in campaigns}
            plan = list(self._plan)
        agent_counts = {c.campaign_id: self.agents.counts(c.campaign_id) for c in campaigns}
        return {
            "engine_state": str(engine_state) if engine_state else None,
            "running_campaigns": [
                {
                    "campaign_id": c.campaign_id,
                    "name": c.name,
                    "priority": c.priority,
                    "dataset": c.dataset,
                    "symbol": c.symbol,
                    "agent_target": c.agent_target,
                    "runtime": runtimes.get(c.campaign_id, {}),
                    "agents": agent_counts.get(c.campaign_id, {}),
                    "progress": c.progress.as_dict(),
                }
                for c in campaigns
            ],
            "worker_plan": plan,
            "stalled": self.stalled_campaigns(),
        }

    # ── lifecycle ────────────────────────────────────────────────────────────
    def forget(self, campaign_id: str) -> None:
        """Drop the scheduler's view of a campaign that has stopped."""
        with self._lock:
            self._runtimes.pop(campaign_id, None)
            self._plan_version = ""

    def reset(self) -> None:
        with self._lock:
            self._runtimes.clear()
            self._plan.clear()
            self._plan_version = ""
            self._cursor = 0
