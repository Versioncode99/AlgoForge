"""What a research conversation is made of, and what none of it may become.

A conversation is a durable record of a dialogue. It is emphatically *not* a
place evidence comes from, and the whole shape of this module exists to keep
those two apart.

**The failure this prevents.** A user says "I'm fairly sure the edge is
volatility-dependent." A model says "the edge appears volatility-dependent."
The judge, over an experiment, establishes that the edge is volatility-dependent.
Rendered as three paragraphs of chat those look identical, and a system that
stores them identically will eventually quote the first one back as though it
were the third. So every piece of text here carries a `Provenance` saying which
of them it is, and there is no operation anywhere that promotes one to another.

**Artifacts are references, never copies.** When the assistant shows a regime
grid it stores the strategy and backtest it came from, not the numbers. Two
reasons, and the second matters more: a copied number goes stale silently, and a
copied number can be *wrong in the first place* — a model that writes figures
into a stored artifact has fabricated evidence that a later reader cannot
distinguish from a computed one. A reference either resolves, and shows what the
deterministic system says today, or it does not resolve and says so.

**Context is explicit and scoped to one conversation.** What a conversation can
see is what was attached to it. There is no ambient "everything the user has";
a conversation about one prop account cannot reach another account's state
because the attachment is the only channel, and it is visible in the interface
rather than assembled behind it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel


class Role(StrEnum):
    """Who produced a turn. Two values, because there is no third speaker.

    Tool output is not a role: it belongs to the assistant turn that called it,
    recorded as a `ToolCall`, so a result can never be read as something the
    assistant asserted on its own.
    """

    USER = "user"
    ASSISTANT = "assistant"


class Provenance(StrEnum):
    """What a piece of text *is*, which is not the same as who said it.

    Ordered from least to most load-bearing. Nothing in this package moves a
    value up this list; only the deterministic systems that produce
    `DETERMINISTIC` can mint it.
    """

    #: The operator said it. It is a fact about the conversation and about
    #: nothing else -- a stated belief is not an observation.
    USER_STATEMENT = "user_statement"
    #: A model wrote it. It may be correct, and it is not evidence.
    MODEL_PROSE = "model_prose"
    #: The literal result of a registered action, recorded as it came back.
    ACTION_RESULT = "action_result"
    #: Produced by a deterministic AlgoForge subsystem -- the judge, the regime
    #: reader, the prop simulator. Authoritative, and reproducible from its own
    #: inputs.
    DETERMINISTIC = "deterministic"


class Outcome(StrEnum):
    """How a tool call ended.

    `REFUSED` is separate from `FAILED` because they mean opposite things about
    the system: a refusal is the permission boundary working, a failure is
    something broken. Collapsing them would make an audit of refusals impossible
    and would let "the action is denied to you" read as a transient error worth
    retrying.
    """

    OK = "ok"
    REFUSED = "refused"
    FAILED = "failed"


class ContextKind(StrEnum):
    """What can be attached to a conversation.

    A closed set, for the same reason the action registry is closed: "attach
    anything" is not a bounded capability, and every kind here has a resolver
    that can say whether the thing still exists.
    """

    STRATEGY = "strategy"
    EXPERIMENT = "experiment"
    BACKTEST = "backtest"
    DATASET = "dataset"
    ACCOUNT = "account"
    WORKSPACE = "workspace"
    CHART = "chart"
    FINDING = "finding"
    CAMPAIGN = "campaign"


class ArtifactKind(StrEnum):
    """What a stored artifact points at.

    Each names a surface that already exists in AlgoForge. There is no
    `custom` or `html` kind: an artifact the interface cannot resolve to a real
    panel is a picture of an answer rather than a way back to one.
    """

    STRATEGY = "strategy"
    BACKTEST = "backtest"
    REGIME = "regime"
    RESAMPLE = "resample"
    PARAMETER_SURFACE = "parameter_surface"
    PROP_SIMULATION = "prop_simulation"
    VALIDATION = "validation"
    EVIDENCE = "evidence"
    ANALYSIS = "analysis"
    WORKSPACE = "workspace"
    PORT = "port"
    #: A research campaign. Added when Campaigns became a destination: an
    #: assistant that opens a campaign and then cannot hand the operator a way
    #: into it has done the work and kept it.
    CAMPAIGN = "campaign"


class AttachedContext(FrozenModel):
    """One thing a conversation is about.

    `ref` is the identifier in whichever subsystem owns the thing -- a strategy
    id, a dataset key, an account id. It is resolved when the conversation is
    read, so a strategy deleted since the attachment shows as unavailable rather
    than as a name the assistant will happily reason about.
    """

    kind: ContextKind
    ref: str = Field(min_length=1, max_length=200)
    #: What to show the operator. Held so the attachment stays readable after
    #: the thing it names is gone -- "NQ ORB (deleted)" beats a bare id.
    label: str = ""
    attached_at: datetime


class ToolCall(FrozenModel):
    """One registered action an assistant turn invoked, and how it went.

    Recorded per turn rather than globally because the question a reader asks is
    "what did it do to answer *this*" -- and because a call list detached from
    the answer it produced is exactly how a transcript comes to imply work that
    belonged to a different question.
    """

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    outcome: Outcome
    #: Why, when the outcome is not OK. Carried verbatim from the registry: a
    #: refusal's reason is the rule that fired, and paraphrasing it would make
    #: the audit unverifiable.
    reason: str = ""
    duration_ms: int = 0
    #: Where the result lives, when it produced something durable. Never the
    #: result itself: see the module docstring.
    result_ref: str = ""


class Artifact(FrozenModel):
    """A pointer from a turn back into the deterministic system.

    The contract is that this carries *no numbers*. `title` may name a subject;
    it may not state a finding. Everything quantitative is resolved from `refs`
    at read time by whatever owns it, so an artifact is always either current or
    honestly missing.
    """

    artifact_id: str = ""
    kind: ArtifactKind
    title: str = Field(min_length=1, max_length=200)
    #: Identifiers the owning subsystem needs to reconstitute the view, e.g.
    #: {"strategy_id": "...", "backtest_id": "..."}. Values are ids, not data.
    refs: dict[str, str] = Field(default_factory=dict)
    #: Where the numbers behind this came from. An artifact produced by a model
    #: rather than by a computation is a claim, and is labelled one.
    provenance: Provenance = Provenance.DETERMINISTIC

    def identified(self, turn_id: str, index: int) -> Artifact:
        """The same artifact with a stable id derived from where it sits."""
        return self.model_copy(
            update={"artifact_id": stable_id("art", f"{turn_id}:{index}:{self.kind.value}")}
        )


class Turn(FrozenModel):
    """One message, and everything that produced it."""

    turn_id: str = ""
    conversation_id: str
    role: Role
    text: str
    created_at: datetime
    provenance: Provenance
    #: Which model answered, when one did. Empty for a user turn and for a
    #: deterministic reply, and the difference is shown: an answer produced with
    #: no credential configured must not look like one a model reasoned out.
    model: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    artifacts: tuple[Artifact, ...] = ()

    @property
    def refused(self) -> tuple[ToolCall, ...]:
        """The calls the permission boundary held or denied."""
        return tuple(call for call in self.tool_calls if call.outcome is Outcome.REFUSED)


class Conversation(FrozenModel):
    """A durable research thread."""

    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    archived: bool = False
    turn_count: int = 0
    context: tuple[AttachedContext, ...] = ()
    #: Restated from the turns so a list can be rendered without loading them.
    last_message: str = ""


#: Conversations start here. A generated title that says nothing is worse than
#: an honest placeholder, because it looks like a summary somebody wrote.
UNTITLED = "New conversation"

#: How long a derived title may be before it stops being a title.
TITLE_LIMIT = 72


def new_conversation_id(created_at: datetime, seed: str = "") -> str:
    return stable_id("conv", f"{created_at.isoformat()}:{seed}")


def new_turn_id(conversation_id: str, index: int) -> str:
    return stable_id("turn", f"{conversation_id}:{index}")


def derive_title(first_message: str) -> str:
    """A title from the first thing the operator asked.

    Deterministic, and taken from the operator's own words rather than from a
    model. Two reasons: a title is chrome, and spending a model call plus a
    second of latency on chrome is the wrong trade; and a model-written title is
    a summary nobody checked, sitting in a list where it will be trusted.

    The first sentence, trimmed. If it is too long it is cut at a word boundary
    and elided, so a title is never a truncated word.
    """
    cleaned = " ".join(first_message.split())
    if not cleaned:
        return UNTITLED
    for stop in (". ", "? ", "! ", "\n"):
        head, _, _ = cleaned.partition(stop)
        if head != cleaned:
            cleaned = head.strip() + stop.strip()
            break
    cleaned = cleaned.rstrip(".")
    if len(cleaned) <= TITLE_LIMIT:
        return cleaned
    cut = cleaned[:TITLE_LIMIT].rsplit(" ", 1)[0].rstrip(",;:")
    return f"{cut}…" if cut else cleaned[:TITLE_LIMIT]


def now() -> datetime:
    return datetime.now(UTC)
