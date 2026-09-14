"""Conversations that persist, and the promotion that must never happen.

Most of this file is about one property. AlgoForge's entire discipline rests on
telling a claim apart from evidence, and a chat transcript is where that
distinction is easiest to lose: a stated belief, a model's paraphrase and a
judged result are three paragraphs of similar-looking prose. The store keeps
them apart by carrying provenance on every turn and by never offering an
operation that moves a turn up that scale — so the tests assert the absence of
that operation as much as the presence of the data.

The rest is ordinary durability: a thread survives a restart, search finds what
was said rather than what it was about, and deleting a conversation does not
reach into anything it merely referenced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from forge.conversation.models import (
    TITLE_LIMIT,
    UNTITLED,
    Artifact,
    ArtifactKind,
    ContextKind,
    Outcome,
    Provenance,
    Role,
    ToolCall,
    derive_title,
)
from forge.conversation.store import ConversationError, ConversationStore

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "conversations.db")


def ask(store: ConversationStore, conversation_id: str, text: str, *, at=NOW):
    return store.append(
        conversation_id, Role.USER, text, provenance=Provenance.USER_STATEMENT, at=at
    )


# ── the thread ───────────────────────────────────────────────────────────────


def test_a_new_conversation_is_untitled_rather_than_summarised(store) -> None:
    """A placeholder that admits it is one.

    A generated title on an empty thread is a summary of nothing, sitting in a
    list where it will be read as a summary of something.
    """
    created = store.create()
    assert created.title == UNTITLED
    assert created.turn_count == 0


def test_the_first_question_names_the_thread(store) -> None:
    created = store.create()
    ask(store, created.conversation_id, "Why does the NQ breakout fail in high volatility?")
    assert store.get(created.conversation_id).title == (
        "Why does the NQ breakout fail in high volatility?"
    )


def test_a_thread_the_operator_named_is_not_renamed_by_a_message(store) -> None:
    created = store.create("NQ volatility research")
    ask(store, created.conversation_id, "Start again from the opening range.")
    assert store.get(created.conversation_id).title == "NQ volatility research"


def test_turns_come_back_in_the_order_they_were_said(store) -> None:
    created = store.create()
    for index, text in enumerate(["first", "second", "third"]):
        ask(store, created.conversation_id, text, at=NOW + timedelta(minutes=index))
    assert [t.text for t in store.turns(created.conversation_id)] == ["first", "second", "third"]


def test_a_conversation_survives_a_restart(store, tmp_path) -> None:
    """The point of the store. A reload used to lose the thread entirely."""
    created = store.create()
    ask(store, created.conversation_id, "Investigate the opening-range mechanism.")
    reopened = ConversationStore(tmp_path / "conversations.db")
    assert reopened.get(created.conversation_id).title.startswith("Investigate the opening-range")
    assert len(reopened.turns(created.conversation_id)) == 1


def test_an_unknown_conversation_refuses_rather_than_inventing_one(store) -> None:
    with pytest.raises(ConversationError, match="no conversation"):
        store.get("conv_nothing")
    with pytest.raises(ConversationError, match="no conversation"):
        store.turns("conv_nothing")
    with pytest.raises(ConversationError, match="no conversation"):
        ask(store, "conv_nothing", "hello")


# ── provenance: the promotion that must not exist ────────────────────────────


def test_a_turn_records_what_its_text_is_not_merely_who_said_it(store) -> None:
    created = store.create()
    stated = ask(store, created.conversation_id, "I'm sure the edge is volatility dependent.")
    prose = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "The edge appears volatility dependent.",
        provenance=Provenance.MODEL_PROSE,
        model="test-model",
    )
    judged = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "G6 failed: the effect does not survive the high-volatility fold.",
        provenance=Provenance.DETERMINISTIC,
    )
    # Three sentences that read almost identically and mean entirely different
    # things. Stored identically, the first would eventually be quoted as the
    # third.
    assert stated.provenance is Provenance.USER_STATEMENT
    assert prose.provenance is Provenance.MODEL_PROSE
    assert judged.provenance is Provenance.DETERMINISTIC


def test_provenance_survives_a_reload(store, tmp_path) -> None:
    created = store.create()
    ask(store, created.conversation_id, "I think this works.")
    store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "It might.",
        provenance=Provenance.MODEL_PROSE,
        model="test-model",
    )
    reopened = ConversationStore(tmp_path / "conversations.db")
    assert [t.provenance for t in reopened.turns(created.conversation_id)] == [
        Provenance.USER_STATEMENT,
        Provenance.MODEL_PROSE,
    ]


def test_the_store_offers_no_way_to_promote_a_turn(store) -> None:
    """Asserted as an absence, because that is what the guarantee is.

    There is no `set_provenance`, no `mark_as_evidence`, no update to a turn's
    text at all. A transcript that can be edited after the fact is not a record
    of what somebody was told before they decided something.
    """
    public = {name for name in dir(store) if not name.startswith("_")}
    forbidden = {"set_provenance", "promote", "mark_evidence", "update_turn", "edit"}
    assert not (public & forbidden)


def test_a_model_answer_records_which_model_answered(store) -> None:
    """An answer produced with no credential must not look like a reasoned one."""
    created = store.create()
    with_model = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "Four strategies have a verdict.",
        provenance=Provenance.ACTION_RESULT,
        model="claude-x",
    )
    deterministic = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "Four strategies have a verdict.",
        provenance=Provenance.DETERMINISTIC,
    )
    assert with_model.model == "claude-x"
    assert deterministic.model == ""


# ── tool calls ───────────────────────────────────────────────────────────────


def test_a_refusal_is_kept_apart_from_a_failure(store) -> None:
    """Opposite meanings: one is the boundary working, one is something broken."""
    created = store.create()
    turn = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "I could not do that.",
        provenance=Provenance.ACTION_RESULT,
        tool_calls=(
            ToolCall(name="list_strategies", outcome=Outcome.OK, duration_ms=4),
            ToolCall(
                name="submit_orders",
                outcome=Outcome.REFUSED,
                reason="'submit_orders' reaches the book; a person applies it",
            ),
            ToolCall(name="backtest_strategy", outcome=Outcome.FAILED, reason="worker timed out"),
        ),
    )
    assert [c.name for c in turn.refused] == ["submit_orders"]
    reloaded = store.turns(created.conversation_id)[0]
    assert [c.outcome for c in reloaded.tool_calls] == [
        Outcome.OK,
        Outcome.REFUSED,
        Outcome.FAILED,
    ]
    # The reason is carried verbatim: paraphrasing a refusal makes the audit
    # unverifiable against the rule that produced it.
    assert reloaded.tool_calls[1].reason.startswith("'submit_orders' reaches the book")


def test_tool_calls_belong_to_the_turn_that_made_them(store) -> None:
    created = store.create()
    store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "one",
        provenance=Provenance.ACTION_RESULT,
        tool_calls=(ToolCall(name="a", outcome=Outcome.OK),),
    )
    store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "two",
        provenance=Provenance.ACTION_RESULT,
        tool_calls=(ToolCall(name="b", outcome=Outcome.OK),),
    )
    turns = store.turns(created.conversation_id)
    assert [c.name for c in turns[0].tool_calls] == ["a"]
    assert [c.name for c in turns[1].tool_calls] == ["b"]


# ── artifacts ────────────────────────────────────────────────────────────────


def test_an_artifact_carries_references_and_not_numbers(store) -> None:
    """The rule that stops a stored artifact from becoming fabricated evidence.

    A model that writes figures into an artifact has produced a number nobody
    computed, and a later reader cannot tell it from one that was. References
    either resolve to what the deterministic system says today, or they fail to
    resolve and say so.
    """
    created = store.create()
    turn = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "Here is where the P&L came from.",
        provenance=Provenance.DETERMINISTIC,
        artifacts=(
            Artifact(
                kind=ArtifactKind.REGIME,
                title="Regime attribution",
                refs={"strategy_id": "s1", "backtest_id": "b1"},
            ),
        ),
    )
    artifact = turn.artifacts[0]
    assert artifact.refs == {"strategy_id": "s1", "backtest_id": "b1"}
    assert all(isinstance(value, str) for value in artifact.refs.values())
    assert artifact.artifact_id, "an artifact needs an identity to be reopened"


def test_artifact_identity_is_stable_across_a_reload(store, tmp_path) -> None:
    created = store.create()
    turn = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "Two views.",
        provenance=Provenance.DETERMINISTIC,
        artifacts=(
            Artifact(kind=ArtifactKind.REGIME, title="Regimes", refs={"strategy_id": "s1"}),
            Artifact(kind=ArtifactKind.RESAMPLE, title="Resample", refs={"strategy_id": "s1"}),
        ),
    )
    reopened = ConversationStore(tmp_path / "conversations.db")
    stored = reopened.turns(created.conversation_id)[0]
    assert [a.artifact_id for a in stored.artifacts] == [a.artifact_id for a in turn.artifacts]
    assert len(set(a.artifact_id for a in stored.artifacts)) == 2


def test_an_artifact_a_model_produced_is_labelled_one(store) -> None:
    created = store.create()
    turn = store.append(
        created.conversation_id,
        Role.ASSISTANT,
        "A suggested layout.",
        provenance=Provenance.MODEL_PROSE,
        artifacts=(
            Artifact(
                kind=ArtifactKind.WORKSPACE,
                title="Proposed workspace",
                refs={"workspace_id": "w1"},
                provenance=Provenance.MODEL_PROSE,
            ),
        ),
    )
    assert turn.artifacts[0].provenance is Provenance.MODEL_PROSE


# ── context, and isolation between conversations ─────────────────────────────


def test_context_is_scoped_to_the_conversation_it_was_attached_to(store) -> None:
    """One account's state cannot appear in another thread by accident.

    Attachment is the only channel, so isolation is a property of the data model
    rather than of every caller remembering to filter.
    """
    first = store.create("Account A")
    second = store.create("Account B")
    store.attach(first.conversation_id, ContextKind.ACCOUNT, "acct-a", "Account A")
    store.attach(second.conversation_id, ContextKind.ACCOUNT, "acct-b", "Account B")
    assert [c.ref for c in store.context(first.conversation_id)] == ["acct-a"]
    assert [c.ref for c in store.context(second.conversation_id)] == ["acct-b"]


def test_attaching_the_same_thing_twice_is_one_attachment(store) -> None:
    created = store.create()
    store.attach(created.conversation_id, ContextKind.STRATEGY, "s1", "NQ ORB")
    store.attach(created.conversation_id, ContextKind.STRATEGY, "s1", "NQ ORB (renamed)")
    context = store.context(created.conversation_id)
    assert len(context) == 1
    assert context[0].label == "NQ ORB (renamed)"


def test_detaching_removes_only_what_was_named(store) -> None:
    created = store.create()
    store.attach(created.conversation_id, ContextKind.STRATEGY, "s1")
    store.attach(created.conversation_id, ContextKind.DATASET, "nq")
    assert store.detach(created.conversation_id, ContextKind.STRATEGY, "s1") is True
    assert [c.kind for c in store.context(created.conversation_id)] == [ContextKind.DATASET]
    assert store.detach(created.conversation_id, ContextKind.STRATEGY, "s1") is False


def test_an_attachment_without_an_identifier_is_refused(store) -> None:
    created = store.create()
    with pytest.raises(ConversationError, match="needs an identifier"):
        store.attach(created.conversation_id, ContextKind.STRATEGY, "   ")


def test_attaching_to_an_unknown_conversation_is_refused(store) -> None:
    with pytest.raises(ConversationError, match="no conversation"):
        store.attach("conv_nothing", ContextKind.STRATEGY, "s1")


# ── the list ─────────────────────────────────────────────────────────────────


def test_threads_are_listed_by_most_recent_activity(store) -> None:
    first = store.create("first")
    second = store.create("second")
    ask(store, first.conversation_id, "later", at=NOW + timedelta(hours=1))
    ask(store, second.conversation_id, "earlier", at=NOW)
    assert [c.title for c in store.list()] == ["first", "second"]


def test_the_list_carries_enough_to_recognise_a_thread(store) -> None:
    created = store.create("Research")
    ask(store, created.conversation_id, "Does volatility compression predict continuation?")
    listed = store.list()[0]
    assert listed.turn_count == 1
    assert "volatility compression" in listed.last_message


def test_search_finds_what_was_said_not_only_what_it_was_titled(store) -> None:
    """The threads that most need search are the ones nobody renamed."""
    named = store.create("Opening range")
    unnamed = store.create("Thread two")
    ask(store, named.conversation_id, "unrelated")
    ask(store, unnamed.conversation_id, "What about liquidity absorption at the open?")
    found = store.list(query="liquidity absorption")
    assert [c.conversation_id for c in found] == [unnamed.conversation_id]


def test_a_search_term_with_a_wildcard_is_taken_literally(store) -> None:
    """Otherwise typing `%` matches everything and reads as a broken search."""
    created = store.create("Percentages")
    ask(store, created.conversation_id, "the 75% volatility percentile")
    other = store.create("Nothing to do with it")
    ask(store, other.conversation_id, "opening range")
    assert [c.conversation_id for c in store.list(query="75%")] == [created.conversation_id]
    # A bare `%` is a character somebody typed, so it matches the text that
    # contains one -- and only that text. Unescaped it would be a wildcard and
    # would return every conversation in the store.
    assert [c.conversation_id for c in store.list(query="%")] == [created.conversation_id]


# ── archive and delete ───────────────────────────────────────────────────────


def test_archiving_hides_a_thread_without_losing_it(store) -> None:
    created = store.create("Old research")
    ask(store, created.conversation_id, "something")
    store.set_archived(created.conversation_id, True)
    assert store.list() == []
    assert [c.title for c in store.list(include_archived=True)] == ["Old research"]
    store.set_archived(created.conversation_id, False)
    assert [c.title for c in store.list()] == ["Old research"]
    assert len(store.turns(created.conversation_id)) == 1


def test_deleting_a_thread_takes_its_turns_and_nothing_else(store) -> None:
    """A conversation references strategies; it does not own them."""
    created = store.create()
    ask(store, created.conversation_id, "about strategy s1")
    store.attach(created.conversation_id, ContextKind.STRATEGY, "s1")
    assert store.delete(created.conversation_id) is True
    assert store.delete(created.conversation_id) is False
    with pytest.raises(ConversationError):
        store.get(created.conversation_id)
    # The attachment went with the thread; the strategy it named is untouched,
    # which this store has no way to reach in the first place.
    assert store.count() == 0


def test_renaming_refuses_an_empty_title(store) -> None:
    created = store.create("Named")
    with pytest.raises(ConversationError, match="cannot be empty"):
        store.rename(created.conversation_id, "   ")
    assert store.get(created.conversation_id).title == "Named"


# ── titles ───────────────────────────────────────────────────────────────────


def test_a_derived_title_stops_at_the_first_sentence() -> None:
    assert derive_title("Why did this fail? I ran it twice.") == "Why did this fail?"


def test_a_long_title_is_cut_at_a_word_boundary() -> None:
    long = "Investigate whether volatility compression followed by directional expansion " \
           "predicts continuation on the NQ opening range"
    title = derive_title(long)
    assert len(title) <= TITLE_LIMIT + 1  # the ellipsis
    assert title.endswith("…")
    assert not title.rstrip("…").endswith(" ")


def test_an_empty_message_leaves_the_placeholder() -> None:
    assert derive_title("   ") == UNTITLED
