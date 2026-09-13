"""What makes a resume a continuation, and what makes it a different programme.

A campaign is durable. Its frontier, hypotheses, agent claims and skip ledger all
outlive the process, and `start()` on an existing campaign re-attaches to them.
That is the point — an unattended programme that forgot its state on every
restart would have to begin again.

The hazard is that re-attaching is keyed by campaign id alone. Nothing stops a
campaign whose temporal scope, dataset or capability set has been edited between
runs from picking up state that was built under the old one, and nothing about
the result *looks* wrong: the frontier still has entries, the agents still have
claims, the progress counter still climbs. It reports continuation. The evidence
underneath belongs to a different question.

That is the failure this module exists to prevent, and it is the dangerous shape
rather than the loud one — a plausible wrong answer, not a crash.

**Identity is a function of configuration, not of the job.** Two runs continue
one programme only when the things that decide what the research *means* are
unchanged. The comparison is a digest over those components, so a resume under a
changed configuration is not detected and refused — it simply is not the same
identity, and the caller can act on that before any state is touched. Detection
logic can be wrong; a digest that does not match cannot be talked into matching.

**What counts, and what deliberately does not.** Over-binding is its own bug: a
campaign that refuses to resume because its name was edited teaches operators to
work around the check. So identity carries only what changes the meaning of
accumulated evidence:

============================  ==========================================
Component                     Why a change invalidates prior state
============================  ==========================================
``dataset``                   Different bars. Every result was measured
                              against the old ones.
``instrument``                Symbol and timeframe. A finding about NQ 1m
                              is not a finding about ES 5m.
``universe``                  Which markets were in scope at all.
``scope``                     Start and end dates. This is the temporal
                              integrity boundary: widening it makes
                              earlier conclusions rest on bars the
                              experiment never saw.
``capabilities``              What a construction was allowed to use. A
                              narrower set retrospectively invalidates
                              nothing, but a *wider* one means the
                              frontier was explored under a constraint
                              that no longer holds.
``external_research``         Whether untrusted external evidence was
                              admissible. Findings gathered with it on
                              are not comparable to findings without.
``vocabulary``                The construction grammar itself. If the
                              observables, shapes or mechanisms changed,
                              "we already tried that" is a claim about a
                              vocabulary that no longer exists.
============================  ==========================================

Excluded, each for a stated reason:

``seed``
    Changes the sequence explored, not the validity of what was already
    found. `duplicate()` exists for deliberately re-running a programme
    under a new seed, and that makes a new campaign with its own id.
``allocation`` and ``stopping``
    Steering. Raising an experiment budget or shifting the
    exploration/exploitation balance mid-programme is a legitimate
    operator action, not a new question.
``priority``, ``agent_target``
    How much machine the campaign gets. No bearing on evidence.
``name``, ``description``, ``tags``
    Labels.
``model configuration``
    Deliberately absent. Doc 1 warns against over-binding nondeterministic
    information, and swapping the model behind a role does not invalidate
    a completed experiment — the experiment was run by the deterministic
    engine either way.

**Legacy campaigns resume.** A campaign that recorded no identity — every
campaign that existed before this module — compares as `UNKNOWN` and is allowed
through. The alternative would strand existing research programmes to close a
hazard they are not in, which is the wrong trade for a store whose whole premise
is that a research programme is not a cache.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from forge.research.grammar import OBSERVABLES, SHAPES
from forge.research.mechanisms import MECHANISMS

#: Length of the hex digests below. Full SHA-256 is 64 characters and none of
#: this is adversarial -- the digest distinguishes configurations, it does not
#: authenticate them -- so it is truncated to stay readable in a row and a log.
DIGEST_CHARS = 16


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def vocabulary_version() -> str:
    """A digest of the construction vocabulary as it currently stands.

    Derived from the grammar rather than declared beside it. A hand-maintained
    constant is a promise to remember, and the failure mode is silent: somebody
    adds an observable, forgets to bump the number, and every stale checkpoint
    in the installation is suddenly considered compatible.

    Keys only, not definitions. Retuning a parameter bound inside an existing
    observable does not make previously explored constructions unexplored, but
    adding or removing one changes what "we have already tried that" means.
    """
    parts = (
        ",".join(sorted(OBSERVABLES)),
        ",".join(sorted(SHAPES)),
        ",".join(sorted(MECHANISMS)),
    )
    return _digest("|".join(parts))


class Verdict(StrEnum):
    """Whether accumulated state may be treated as this run's own."""

    #: Same configuration. Resuming continues the programme.
    COMPATIBLE = "compatible"
    #: Something identity-bearing changed. Prior state belongs to a different
    #: question, and `Compatibility.changed` names which components moved.
    INCOMPATIBLE = "incompatible"
    #: No identity was recorded, so there is nothing to compare against. A
    #: campaign that predates identity, allowed through deliberately.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Identity:
    """The identity-bearing configuration of one campaign run."""

    #: Component name to its canonical value, ordered as declared.
    components: Mapping[str, str]
    #: Digest over the whole mapping. Equality of this is equality of identity.
    digest: str

    def as_json(self) -> str:
        """The stored form. Sorted so the same identity always serialises alike."""
        return json.dumps(
            {"components": dict(self.components), "digest": self.digest},
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class Compatibility:
    """The answer to "may this run adopt what is already there?"."""

    verdict: Verdict
    #: Components whose value differs, in declaration order. Empty unless
    #: INCOMPATIBLE.
    changed: tuple[str, ...] = ()

    @property
    def resumable(self) -> bool:
        """True when prior state may be adopted.

        UNKNOWN resumes: see the legacy note in the module docstring.
        """
        return self.verdict is not Verdict.INCOMPATIBLE

    def reason(self) -> str:
        """One sentence for the operator, naming what moved."""
        if self.verdict is Verdict.COMPATIBLE:
            return "the configuration is unchanged"
        if self.verdict is Verdict.UNKNOWN:
            return "this campaign recorded no configuration to compare against"
        named = ", ".join(self.changed)
        return (
            f"{named} changed since this campaign last ran, so the research already "
            "recorded was gathered under a different question"
        )


def _canonical(value: Any) -> str:
    """A stable string for one component value.

    Tuples are sorted before joining: a capability set is a set, and reordering
    it in a request must not read as a different configuration.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (tuple, list)):
        return ",".join(sorted(str(item) for item in value))
    return str(value)


def of(campaign: Any) -> Identity:
    """The identity of a campaign as currently configured.

    Takes anything carrying the campaign fields rather than the concrete class,
    so the identity can be computed from a request being validated as easily as
    from a stored row -- which is what lets a caller check compatibility
    *before* deciding to start.
    """
    components: dict[str, str] = {
        "dataset": _canonical(campaign.dataset),
        "instrument": f"{campaign.symbol}@{campaign.timeframe}",
        "universe": _canonical(campaign.universe),
        "scope": f"{campaign.start_date}..{campaign.end_date}",
        "capabilities": _canonical(campaign.allowed_capabilities),
        "external_research": _canonical(campaign.web_research),
        "vocabulary": vocabulary_version(),
    }
    joined = "|".join(f"{name}={value}" for name, value in components.items())
    return Identity(components=components, digest=_digest(joined))


def parse(raw: str) -> Identity | None:
    """Read a stored identity back, or None if there isn't a usable one.

    Returns None rather than raising on anything malformed. A row that cannot be
    read is indistinguishable, for our purposes, from a row written before
    identities existed, and both mean the same thing: nothing to compare
    against. Raising here would turn an unreadable column into a campaign that
    can never be started again.
    """
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(loaded, dict):
        return None
    components = loaded.get("components")
    digest = loaded.get("digest")
    if not isinstance(components, dict) or not isinstance(digest, str) or not digest:
        return None
    return Identity(
        components={str(k): str(v) for k, v in components.items()}, digest=digest
    )


def compare(stored: Identity | None, current: Identity) -> Compatibility:
    """Whether `current` may adopt state recorded under `stored`."""
    if stored is None:
        return Compatibility(Verdict.UNKNOWN)
    if stored.digest == current.digest:
        return Compatibility(Verdict.COMPATIBLE)
    changed = tuple(
        name
        for name, value in current.components.items()
        if stored.components.get(name) != value
    )
    # A digest mismatch with no named difference means the stored components are
    # a different *shape* from today's -- an older or newer set of names. That is
    # still incompatible, and saying so beats reporting an empty list.
    return Compatibility(Verdict.INCOMPATIBLE, changed or ("configuration",))
