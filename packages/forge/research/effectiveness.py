"""Is an agent doing research, or producing activity?

The agent registry counts experiments, findings and errors. The skip ledger
records every refusal with the agent that proposed it. Neither on its own can
answer the question anybody actually has, because the two halves are the same
story: an agent that proposed forty things and had thirty-eight refused has an
experiment count that looks like work.

So this module joins them, and the joining is the whole point. Three figures come
out of it and they are deliberately different from each other:

**Acceptance** is the share of an agent's proposals that became an experiment
rather than a refusal. It is the cheapest signal that something is wrong: an
agent proposing into a corner of the space that is already exhausted has a high
throughput and a collapsing acceptance rate.

**Efficiency** is useful output per unit of compute. A fast agent producing
refusals is not efficient however fast it is.

**Effectiveness** is downstream research progress — findings, and experiments
that reached a verdict rather than a refusal. This is the one that matters and
the one with the weakest instrument, so it is reported as what it is: a count of
durable outcomes, not a score.

**Nothing here decides anything.** It does not allocate compute, retire an agent,
or feed back into what gets proposed. A measurement that changed the thing it
measured would make the number unreadable, and an agent that could be retired for
a low score would make "propose nothing" the winning strategy.

**A number nobody can act on is not reported.** Every field here is derived from
rows that exist; where a figure would need attribution the stores do not record,
this module says so rather than estimating it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Skip kinds that mean "the proposal was declined". The agent did work and the
#: work was refused, which is the numerator of a low acceptance rate.
REFUSAL_KINDS: frozenset[str] = frozenset(
    {"NOT_NOVEL", "FAILURE_REGION", "PROPOSAL_ERROR", "BUDGET"}
)

#: Skip kinds that mean "there was nothing to propose". The agent did *not* do
#: work, so counting these as refusals would blame an agent for an empty
#: frontier — which is a fact about the campaign, not about the agent.
IDLE_KINDS: frozenset[str] = frozenset(
    {"NO_ELIGIBLE_WORK", "CAPABILITY_BLOCKED", "DATA_BLOCKED"}
)


@dataclass
class RoleScore:
    """One role's record, joined across the registry and the skip ledger."""

    role: str
    agents: int = 0
    experiments: int = 0
    findings: int = 0
    errors: int = 0
    refusals: int = 0
    idle: int = 0
    compute_spent: float = 0.0

    @property
    def proposals(self) -> int:
        """Everything the agent put forward, accepted or not."""
        return self.experiments + self.refusals

    @property
    def acceptance(self) -> float:
        """Share of proposals that became an experiment.

        Zero proposals gives 0.0 rather than 1.0. An agent that has proposed
        nothing has not earned a perfect score, and reporting one is how a
        dashboard tells somebody their idle crew is doing well.
        """
        total = self.proposals
        return round(self.experiments / total, 4) if total else 0.0

    @property
    def efficiency(self) -> float:
        """Experiments per unit of compute spent, or 0.0 when nothing was spent.

        Compute is unmetered by default on a local installation, so this is
        often 0.0 and means "not measured" rather than "inefficient". The
        interface must say which.
        """
        return round(self.experiments / self.compute_spent, 4) if self.compute_spent else 0.0

    @property
    def measured(self) -> bool:
        """Has this role done anything a score could be computed from?"""
        return bool(self.proposals or self.findings or self.idle)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "agents": self.agents,
            "proposals": self.proposals,
            "experiments": self.experiments,
            "refusals": self.refusals,
            "idle_cycles": self.idle,
            "findings": self.findings,
            "errors": self.errors,
            "compute_spent": round(self.compute_spent, 2),
            "acceptance": self.acceptance,
            "efficiency": self.efficiency,
            "efficiency_measured": bool(self.compute_spent),
            "measured": self.measured,
        }


@dataclass
class Effectiveness:
    """The whole crew, per role and in total."""

    roles: list[RoleScore] = field(default_factory=list)

    @property
    def experiments(self) -> int:
        return sum(role.experiments for role in self.roles)

    @property
    def refusals(self) -> int:
        return sum(role.refusals for role in self.roles)

    @property
    def idle(self) -> int:
        return sum(role.idle for role in self.roles)

    @property
    def findings(self) -> int:
        return sum(role.findings for role in self.roles)

    @property
    def acceptance(self) -> float:
        total = self.experiments + self.refusals
        return round(self.experiments / total, 4) if total else 0.0

    def weakest(self) -> RoleScore | None:
        """The role with the lowest acceptance among those that have proposed.

        Named rather than ranked: "these four are all fine and this one is
        proposing into an exhausted corner" is the sentence worth showing, and a
        league table of roles that have each proposed twice is not.
        """
        candidates = [role for role in self.roles if role.proposals >= 3]
        return min(candidates, key=lambda role: role.acceptance) if candidates else None

    def as_dict(self) -> dict[str, Any]:
        weak = self.weakest()
        return {
            "roles": [role.as_dict() for role in self.roles if role.measured],
            "totals": {
                "experiments": self.experiments,
                "refusals": self.refusals,
                "idle_cycles": self.idle,
                "findings": self.findings,
                "acceptance": self.acceptance,
            },
            "weakest_role": weak.role if weak else "",
            "weakest_acceptance": weak.acceptance if weak else 0.0,
            "note": (
                "Acceptance is the share of an agent's proposals that became an "
                "experiment rather than a refusal. A low one usually means the "
                "frontier that role draws from is exhausted, not that the agent is "
                "faulty. Nothing here allocates compute or retires an agent."
            ),
        }


def score(
    agents: Iterable[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]],
) -> Effectiveness:
    """Join the registry and the skip ledger into a per-role record.

    ``agents`` are rows as `AgentRegistry` reports them and ``skips`` are rows as
    `SkipLedger.list` reports them. Both are plain mappings on purpose: this
    function reads two stores it must not be able to write to, and taking their
    own types would make importing it a way to reach them.

    A skip whose ``agent_id`` matches no live agent is still counted against its
    role when the row carries one, and dropped otherwise. An agent that has been
    retired should not take its refusals out of the record with it.
    """
    by_role: dict[str, RoleScore] = {}

    def bucket(role: str) -> RoleScore:
        return by_role.setdefault(role, RoleScore(role=role))

    role_of: dict[str, str] = {}
    for row in agents:
        role = str(row.get("role") or "")
        if not role:
            continue
        agent_id = str(row.get("agent_id") or "")
        if agent_id:
            role_of[agent_id] = role
        entry = bucket(role)
        entry.agents += 1
        entry.experiments += int(row.get("experiments") or 0)
        entry.findings += int(row.get("findings") or 0)
        entry.errors += int(row.get("errors") or 0)
        entry.compute_spent += float(row.get("compute_spent") or 0.0)

    for row in skips:
        role = role_of.get(str(row.get("agent_id") or ""), "")
        if not role:
            continue
        kind = str(row.get("kind") or "")
        occurrences = max(1, int(row.get("occurrences") or 1))
        entry = bucket(role)
        if kind in REFUSAL_KINDS:
            entry.refusals += occurrences
        elif kind in IDLE_KINDS:
            entry.idle += occurrences

    return Effectiveness(roles=sorted(by_role.values(), key=lambda item: item.role))
