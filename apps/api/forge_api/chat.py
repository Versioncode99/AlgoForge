"""The conversation the operator actually has, bound to the systems that answer it.

`forge.conversation` stores a dialogue. `forge_api.assistant` runs a bounded
tool loop over the action registry. This joins them, and almost everything it
does is a mapping — from what happened to what is recorded — chosen so the
record cannot overstate the work.

**Provenance is derived, never asserted.** The assistant reports a `source` for
each reply, and this module translates it:

    model prose            -> MODEL_PROSE
    rendered action result -> ACTION_RESULT
    ledger summary         -> DETERMINISTIC

A model can produce the first. It cannot produce the third, because the third is
only minted on the path where no model was involved at all. That asymmetry is
the point: "deterministic" has to be a fact about how the sentence was made, or
it is decoration on a sentence that wanted authority.

Note what this means for a grounded model answer. The assistant may have run six
actions, and every figure in the reply may have come back from one of them — the
reply is still `MODEL_PROSE`, because the *sentence* is the model's. The action
results are recorded separately as `ToolCall`s, and the artifacts point at the
subjects they touched. A reader who wants the number goes to the artifact.

**Artifacts are derived from calls that succeeded, by table.** Not by asking the
model what to attach: a model that names its own artifacts will name one for
work it did not do. The table maps an action to the surface its result can be
reopened in, and takes the identifiers out of the *arguments the registry
validated*, so an artifact cannot reference something that was never touched.
"""

from __future__ import annotations

from typing import Any

from forge.conversation import (
    Artifact,
    ArtifactKind,
    AttachedContext,
    ContextKind,
    Conversation,
    ConversationError,
    ConversationStore,
    Outcome,
    Provenance,
    Role,
    ToolCall,
)

from forge_api.assistant import Assistant

#: How the assistant's reported source becomes a turn's provenance.
#:
#: Anything unrecognised falls to MODEL_PROSE rather than to something stronger.
#: A new source arriving here should be treated as a model's words until
#: somebody decides otherwise, because the failure of guessing upward is a claim
#: with unearned standing and the failure of guessing downward is a footnote.
_PROVENANCE: dict[str, Provenance] = {
    "model_prose": Provenance.MODEL_PROSE,
    "action_result": Provenance.ACTION_RESULT,
    "deterministic": Provenance.DETERMINISTIC,
}

#: Which actions produce something worth reopening, and where.
#:
#: `(kind, argument names to carry)`. The arguments are read from the validated
#: call rather than from the result, so the reference is to what was asked for
#: and actually ran -- there is no path here for a reference to be invented.
_ARTIFACTS: dict[str, tuple[ArtifactKind, tuple[str, ...]]] = {
    "strategy_regimes": (ArtifactKind.REGIME, ("strategy_id", "backtest_id")),
    "strategy_trades": (ArtifactKind.BACKTEST, ("strategy_id", "backtest_id")),
    "strategy_dossier": (ArtifactKind.EVIDENCE, ("strategy_id",)),
    "strategy_definition": (ArtifactKind.STRATEGY, ("strategy_id",)),
    "backtest_strategy": (ArtifactKind.BACKTEST, ("strategy_id",)),
    "validate_strategy": (ArtifactKind.VALIDATION, ("strategy_id",)),
    "create_strategy": (ArtifactKind.STRATEGY, ("strategy_id", "template")),
    "create_strategy_from_blueprint": (ArtifactKind.STRATEGY, ("blueprint_id",)),
    "run_analysis": (ArtifactKind.ANALYSIS, ("strategy_id", "analysis")),
    "parameter_surface": (
        ArtifactKind.PARAMETER_SURFACE,
        ("strategy_id", "x_parameter", "y_parameter"),
    ),
    "analysis_trades": (ArtifactKind.ANALYSIS, ("strategy_id",)),
    "assess_prop_account": (ArtifactKind.PROP_SIMULATION, ("account_id",)),
    "export_strategy": (ArtifactKind.PORT, ("strategy_id", "target")),
    "build_workspace": (ArtifactKind.WORKSPACE, ("name",)),
    "create_workspace": (ArtifactKind.WORKSPACE, ("name",)),
    "open_workspace": (ArtifactKind.WORKSPACE, ("workspace_id",)),
}

#: How an artifact is titled. The action's own noun, and the subject it names --
#: never a finding. A title that states a result is a number stored outside the
#: system that computed it.
_TITLES: dict[ArtifactKind, str] = {
    ArtifactKind.REGIME: "Regime attribution",
    ArtifactKind.BACKTEST: "Backtest",
    ArtifactKind.EVIDENCE: "Evidence dossier",
    ArtifactKind.STRATEGY: "Strategy",
    ArtifactKind.VALIDATION: "Validation",
    ArtifactKind.ANALYSIS: "Analysis",
    ArtifactKind.PROP_SIMULATION: "Account simulation",
    ArtifactKind.PORT: "Ported strategy",
    ArtifactKind.WORKSPACE: "Workspace",
    ArtifactKind.RESAMPLE: "Resampled distribution",
    ArtifactKind.PARAMETER_SURFACE: "Parameter surface",
}


class ChatService:
    """Conversations, and the assistant that answers into them."""

    def __init__(self, store: ConversationStore, assistant: Assistant) -> None:
        self.store = store
        self.assistant = assistant

    # ── threads ──────────────────────────────────────────────────────────────

    def create(self, title: str = "") -> Conversation:
        return self.store.create(title)

    def list(self, *, query: str = "", include_archived: bool = False) -> list[Conversation]:
        return self.store.list(query=query, include_archived=include_archived)

    def get(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.store.get(conversation_id)
        turns = self.store.turns(conversation_id)
        return {
            "conversation": conversation.model_dump(mode="json"),
            "turns": [turn.model_dump(mode="json") for turn in turns],
        }

    def rename(self, conversation_id: str, title: str) -> Conversation:
        return self.store.rename(conversation_id, title)

    def set_archived(self, conversation_id: str, archived: bool) -> Conversation:
        return self.store.set_archived(conversation_id, archived)

    def delete(self, conversation_id: str) -> bool:
        return self.store.delete(conversation_id)

    def attach(
        self, conversation_id: str, kind: ContextKind, ref: str, label: str = ""
    ) -> AttachedContext:
        return self.store.attach(conversation_id, kind, ref, label)

    def detach(self, conversation_id: str, kind: ContextKind, ref: str) -> bool:
        return self.store.detach(conversation_id, kind, ref)

    # ── the exchange ─────────────────────────────────────────────────────────

    def send(self, conversation_id: str, message: str) -> dict[str, Any]:
        """One question, answered and recorded.

        The user's turn is written *before* the assistant runs. If the model call
        fails, times out or the process dies mid-answer, what the operator asked
        is still in the thread — losing the question along with the answer is how
        a research transcript develops holes exactly where something went wrong.
        """
        text = message.strip()
        if not text:
            raise ConversationError("a message cannot be empty")
        self.store.append(
            conversation_id, Role.USER, text, provenance=Provenance.USER_STATEMENT
        )

        attached = self._subject(conversation_id)
        subject = {"attached": list(attached)} if attached else None
        reply = self.assistant.ask(text, attached=subject)

        calls = tuple(_tool_call(entry) for entry in reply.get("calls", []))
        answer = self.store.append(
            conversation_id,
            Role.ASSISTANT,
            str(reply.get("answer", "")),
            provenance=_PROVENANCE.get(str(reply.get("source", "")), Provenance.MODEL_PROSE),
            model=str(reply.get("model", "")),
            tool_calls=calls,
            artifacts=_artifacts(reply.get("calls", [])),
        )
        return {
            "turn": answer.model_dump(mode="json"),
            "conversation": self.store.get(conversation_id).model_dump(mode="json"),
            "note": reply.get("note"),
            "provider": reply.get("provider"),
        }

    def _subject(self, conversation_id: str) -> tuple[dict[str, str], ...]:
        """What this conversation is about, as names rather than as content.

        Deliberately shallow: kind, identifier and label. Resolving each
        attachment into its full state here would put an account's balances into
        every prompt whether or not the question needed them, and would make one
        conversation's context a function of everything it had ever named.
        """
        return tuple(
            {"kind": item.kind.value, "ref": item.ref, "label": item.label}
            for item in self.store.context(conversation_id)
        )


def _tool_call(entry: dict[str, Any]) -> ToolCall:
    """One registry call, recorded with the distinction that matters.

    A refusal and a failure are opposite facts about the system -- the boundary
    working, against something broken -- and they arrive here indistinguishable,
    both as `ok: False` with a message. They are told apart by the message,
    because that message is produced by `forge.modes.permissions` and names the
    rule that fired.
    """
    raw = entry.get("arguments")
    arguments: dict[str, Any] = raw if isinstance(raw, dict) else {}
    failed = not entry.get("ok", False)
    reason = str(entry.get("error", ""))
    refused = failed and _is_refusal(reason)
    return ToolCall(
        name=str(entry.get("action", "")),
        arguments=arguments,
        outcome=Outcome.REFUSED if refused else Outcome.FAILED if failed else Outcome.OK,
        reason=reason,
        duration_ms=int(entry.get("ms", 0) or 0),
    )


#: Phrases the permission layer uses when it declines. Matched rather than
#: guessed at: these are the words `forge.modes.permissions` and the action
#: registry actually produce, and a message that matches none of them is treated
#: as a failure — which is the safer mistake, because a failure invites a look
#: and a refusal invites a shrug.
_REFUSAL_MARKERS = (
    "protected control",
    "may never call it",
    "needs a person",
    "a person applies it",
    "is not in any permitted set",
    "awaiting approval",
    "requires approval",
    "not permitted",
    "denied",
    "refused",
)


def _is_refusal(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def _artifacts(entries: list[dict[str, Any]]) -> tuple[Artifact, ...]:
    """Reopenable views for the calls that succeeded.

    Only successful calls. An artifact for a refused action would be a link to a
    result that does not exist, which reads as the work having been done.
    """
    built: list[Artifact] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not entry.get("ok"):
            continue
        mapping = _ARTIFACTS.get(str(entry.get("action", "")))
        if mapping is None:
            continue
        kind, wanted = mapping
        raw_args = entry.get("arguments")
        arguments: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        raw_result = entry.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        refs = {
            key: str(value)
            for key in wanted
            # The result is consulted only for identifiers the caller could not
            # have supplied -- a strategy id minted by `create_strategy`, say.
            # Never for values.
            #
            # Scalars only. A key whose value is a dict or a list would be
            # stringified into the reference, putting a fragment of a result
            # inside an artifact that is supposed to carry identifiers and
            # nothing else -- which is the copied-number problem wearing a
            # different shape.
            if isinstance(
                value := arguments.get(key) or result.get(key), str | int | float
            )
            and str(value) != ""
        }
        if not refs:
            continue
        signature = (kind.value, "|".join(f"{k}={v}" for k, v in sorted(refs.items())))
        if signature in seen:
            continue
        seen.add(signature)
        subject = next(iter(refs.values()))
        built.append(
            Artifact(
                kind=kind,
                title=f"{_TITLES.get(kind, kind.value)} · {subject}",
                refs=refs,
            )
        )
    return tuple(built)
