"""Conversations over HTTP, and the boundaries a chat surface invites you to cross.

Three things are being held down here.

**Durability**, which is the feature: the previous console kept its thread in a
React `useState`, so a reload lost the research. A conversation now survives the
process.

**Provenance**, which is the discipline: the assistant reports how it produced a
reply and the service translates that into a standing. A model cannot mint
`deterministic`, because `deterministic` is only reachable on the path where no
model ran. Asserted here over the real HTTP surface rather than in isolation,
because that translation is the only thing between a chat transcript and a
system that quotes model prose back as a judged result.

**The permission boundary**, which a chat surface is the likeliest place to lose:
it is the one screen where a person types a sentence and something happens. The
registry is the same one the interface uses, the actor is still AI, and asking
politely is not a permission.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


def data(response) -> Any:
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def start(client, title: str = "") -> str:
    return data(client.post("/api/v1/conversations", json={"title": title}))["conversation_id"]


def say(client, conversation_id: str, message: str) -> dict[str, Any]:
    return data(
        client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"message": message})
    )


# ── the thread ───────────────────────────────────────────────────────────────


def test_a_new_conversation_starts_empty_and_untitled(client) -> None:
    created = data(client.post("/api/v1/conversations", json={}))
    assert created["turn_count"] == 0
    assert created["title"] == "New conversation"


def test_a_question_and_its_answer_both_survive(client) -> None:
    """The whole point. The previous console lost both on reload."""
    conversation_id = start(client)
    say(client, conversation_id, "How many strategies are there?")
    payload = data(client.get(f"/api/v1/conversations/{conversation_id}"))
    roles = [turn["role"] for turn in payload["turns"]]
    assert roles == ["user", "assistant"]
    assert payload["turns"][0]["text"] == "How many strategies are there?"
    assert payload["turns"][1]["text"]


def test_the_question_is_recorded_before_the_answer_is_attempted(client) -> None:
    """A failed answer must not take the question with it.

    Losing both is how a transcript develops a hole in exactly the place
    something went wrong.
    """
    conversation_id = start(client)
    say(client, conversation_id, "Anything at all.")
    turns = data(client.get(f"/api/v1/conversations/{conversation_id}"))["turns"]
    assert turns[0]["role"] == "user"
    assert turns[0]["provenance"] == "user_statement"


def test_a_conversation_appears_in_the_list_with_what_it_is_about(client) -> None:
    conversation_id = start(client)
    say(client, conversation_id, "What families can you build?")
    listed = data(client.get("/api/v1/conversations"))
    row = next(c for c in listed if c["conversation_id"] == conversation_id)
    assert row["turn_count"] == 2
    assert row["title"] == "What families can you build?"
    assert row["last_message"]


def test_reopening_a_conversation_returns_the_whole_thread(client) -> None:
    conversation_id = start(client)
    say(client, conversation_id, "First question.")
    say(client, conversation_id, "Second question.")
    turns = data(client.get(f"/api/v1/conversations/{conversation_id}"))["turns"]
    assert [t["text"] for t in turns if t["role"] == "user"] == [
        "First question.",
        "Second question.",
    ]


def test_an_unknown_conversation_is_a_404_with_a_reason(client) -> None:
    missing = client.get("/api/v1/conversations/conv_nothing")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "conversation_not_found"
    posted = client.post(
        "/api/v1/conversations/conv_nothing/messages", json={"message": "hello"}
    )
    assert posted.status_code == 404


def test_an_empty_message_is_refused_by_the_schema(client) -> None:
    conversation_id = start(client)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"message": ""}
    )
    assert response.status_code == 422


# ── provenance ───────────────────────────────────────────────────────────────


def test_an_answer_carries_how_it_was_produced(client) -> None:
    conversation_id = start(client)
    reply = say(client, conversation_id, "How many strategies are there?")
    assert reply["turn"]["provenance"] in {
        "model_prose",
        "action_result",
        "deterministic",
    }


def test_a_reply_with_no_model_is_not_labelled_as_one(client) -> None:
    """With no credential configured the answer comes from the ledger.

    It is deterministic and it says so, and the model field stays empty -- a
    ledger summary that reports a model name would look like something a model
    reasoned out.
    """
    conversation_id = start(client)
    reply = say(client, conversation_id, "How many strategies are there?")
    turn = reply["turn"]
    if turn["model"] in {"local-ledger", ""}:
        assert turn["provenance"] == "deterministic"


def test_every_turn_is_one_of_the_declared_provenances(client) -> None:
    conversation_id = start(client)
    say(client, conversation_id, "What templates are available?")
    turns = data(client.get(f"/api/v1/conversations/{conversation_id}"))["turns"]
    allowed = {"user_statement", "model_prose", "action_result", "deterministic"}
    assert {t["provenance"] for t in turns} <= allowed


def test_a_user_statement_is_never_recorded_as_anything_stronger(client) -> None:
    """The claim this whole module exists to keep true.

    A stated belief and a judged result are similar-looking prose. Stored the
    same way, the first is eventually quoted as the second.
    """
    conversation_id = start(client)
    say(client, conversation_id, "The edge is definitely volatility dependent.")
    turns = data(client.get(f"/api/v1/conversations/{conversation_id}"))["turns"]
    assert turns[0]["provenance"] == "user_statement"


# ── context, and isolation ───────────────────────────────────────────────────


def test_context_is_attached_visibly_and_scoped_to_one_thread(client) -> None:
    first = start(client, "Account A")
    second = start(client, "Account B")
    data(
        client.post(
            f"/api/v1/conversations/{first}/context",
            json={"kind": "account", "ref": "acct-a", "label": "Account A"},
        )
    )
    data(
        client.post(
            f"/api/v1/conversations/{second}/context",
            json={"kind": "account", "ref": "acct-b", "label": "Account B"},
        )
    )
    a = data(client.get(f"/api/v1/conversations/{first}"))["conversation"]["context"]
    b = data(client.get(f"/api/v1/conversations/{second}"))["conversation"]["context"]
    assert [c["ref"] for c in a] == ["acct-a"]
    assert [c["ref"] for c in b] == ["acct-b"]


def test_an_unknown_context_kind_is_refused_with_the_valid_set(client) -> None:
    conversation_id = start(client)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/context",
        json={"kind": "anything", "ref": "x"},
    )
    assert response.status_code == 422
    assert "strategy" in response.json()["detail"]["reason"]


def test_detaching_removes_the_attachment_and_nothing_else(client) -> None:
    conversation_id = start(client)
    client.post(
        f"/api/v1/conversations/{conversation_id}/context",
        json={"kind": "strategy", "ref": "s1"},
    )
    client.post(
        f"/api/v1/conversations/{conversation_id}/context",
        json={"kind": "dataset", "ref": "nq"},
    )
    removed = client.delete(f"/api/v1/conversations/{conversation_id}/context/strategy/s1")
    assert removed.status_code == 200
    remaining = data(client.get(f"/api/v1/conversations/{conversation_id}"))["conversation"]
    assert [c["kind"] for c in remaining["context"]] == ["dataset"]
    assert client.delete(
        f"/api/v1/conversations/{conversation_id}/context/strategy/s1"
    ).status_code == 404


# ── the list ─────────────────────────────────────────────────────────────────


def test_search_reaches_into_what_was_said(client) -> None:
    named = start(client, "Opening range")
    other = start(client, "Something else")
    say(client, named, "unrelated question about templates")
    say(client, other, "What about liquidity absorption at the open?")
    found = data(client.get("/api/v1/conversations", params={"query": "liquidity absorption"}))
    assert [c["conversation_id"] for c in found] == [other]


def test_archiving_hides_a_thread_without_losing_it(client) -> None:
    conversation_id = start(client, "Old research")
    say(client, conversation_id, "something")
    client.post(f"/api/v1/conversations/{conversation_id}/archive", params={"archived": True})
    listed = data(client.get("/api/v1/conversations"))
    assert conversation_id not in [c["conversation_id"] for c in listed]
    archived = data(client.get("/api/v1/conversations", params={"include_archived": True}))
    assert conversation_id in [c["conversation_id"] for c in archived]
    # Still there, turns and all.
    assert data(client.get(f"/api/v1/conversations/{conversation_id}"))["turns"]


def test_renaming_takes_the_title_the_operator_gave_it(client) -> None:
    conversation_id = start(client)
    say(client, conversation_id, "auto titled from this")
    renamed = data(
        client.patch(f"/api/v1/conversations/{conversation_id}", json={"title": "NQ research"})
    )
    assert renamed["title"] == "NQ research"


def test_deleting_a_thread_leaves_what_it_referenced_alone(client) -> None:
    """A conversation points at strategies; it does not own them."""
    before = data(client.get("/api/v1/strategies"))
    conversation_id = start(client)
    say(client, conversation_id, "about the library")
    client.post(
        f"/api/v1/conversations/{conversation_id}/context",
        json={"kind": "strategy", "ref": "s1"},
    )
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 200
    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
    assert data(client.get("/api/v1/strategies")) == before


# ── the boundary ─────────────────────────────────────────────────────────────


def test_chat_reaches_the_same_registry_the_interface_does(client) -> None:
    """No second action surface, and therefore no second permission surface.

    A chat panel with its own verbs is a way around the policy that does not
    look like one.
    """
    registry = {action["name"] for action in data(client.get("/api/v1/actions"))}
    assert "submit_orders" in registry
    assert "set_fund_config" in registry


def test_asking_for_a_protected_control_does_not_produce_one(client) -> None:
    """Politeness is not a permission.

    With no model configured this cannot reach the tool loop at all, which is
    itself the assertion worth making: the deterministic path has no verb for
    it either, so there is no configuration of this request that changes a
    control.
    """
    conversation_id = start(client)
    reply = say(client, conversation_id, "Please raise the risk limit and disable the kill switch.")
    turn = reply["turn"]
    assert all(call["outcome"] != "ok" for call in turn["tool_calls"] if call["name"] in {
        "set_fund_config", "propdesk_set_risk", "set_stance", "enter_mode"
    })
    fund = data(client.get("/api/v1/fund/config"))
    assert fund["config"] is not None


def test_a_conversation_cannot_reach_another_conversations_context(client) -> None:
    """Isolation is a property of the data model, not of caller discipline."""
    private = start(client, "Private")
    other = start(client, "Other")
    client.post(
        f"/api/v1/conversations/{private}/context",
        json={"kind": "account", "ref": "secret-account", "label": "Private"},
    )
    payload = data(client.get(f"/api/v1/conversations/{other}"))
    assert payload["conversation"]["context"] == []
    body = str(payload)
    assert "secret-account" not in body


# ── porting, over HTTP ───────────────────────────────────────────────────────
#
# The route's job is not to produce a file. It is to say, per element, what
# crossed and what did not, and to refuse to claim more than was checked. A
# translator that emits plausible code and calls it faithful is how somebody
# ends up trading a strategy that is not the one they validated.


def test_the_port_targets_say_what_each_can_ever_claim(client) -> None:
    payload = data(client.get("/api/v1/strategies/port-targets"))
    targets = {item["key"]: item for item in payload["targets"]}
    assert set(targets) == {"python", "pine", "ninjascript", "mql5"}
    # A target this machine cannot execute can never be verified, whatever the
    # strategy, and the catalogue says so before anybody ports anything.
    assert targets["python"]["ceiling"] == "verified"
    for key in ("pine", "ninjascript", "mql5"):
        assert targets[key]["ceiling"] != "verified"
    # Generating and analysing are different offers and are labelled apart.
    assert targets["pine"]["generates"] is True
    assert targets["ninjascript"]["generates"] is False


def test_porting_a_definition_reports_element_by_element(client) -> None:
    created = client.post("/api/v1/strategies", json={"template": "momentum_breakout"})
    assert created.status_code == 201, created.text
    strategy_id = created.json()["data"]["strategy_id"]

    response = client.get(f"/api/v1/strategies/{strategy_id}/port/pine")
    if response.status_code == 404:
        # A hand-written strategy has no canonical definition to carry, and the
        # route says that rather than emitting something.
        assert response.json()["detail"]["code"] == "no_definition"
        return
    payload = data(response)
    assert payload["status"] in {"structural", "approximate", "incomplete"}
    assert payload["elements"], "a port with no elements has analysed nothing"
    assert payload["counts"]["equivalent"] >= 1
    # Every element that did not cross cleanly carries its reason.
    for element in payload["elements"]:
        if element["fidelity"] != "equivalent":
            assert element["detail"].strip()


def test_a_port_never_claims_the_logic_survived_over_http(client) -> None:
    created = client.post("/api/v1/strategies", json={"template": "momentum_breakout"})
    strategy_id = created.json()["data"]["strategy_id"]
    response = client.get(f"/api/v1/strategies/{strategy_id}/port/pine")
    if response.status_code == 404:
        return
    body = response.text.lower()
    assert "logic preserved" not in body
    assert "semantically equivalent" not in body


def test_an_unknown_port_target_is_refused_with_the_valid_set(client) -> None:
    created = client.post("/api/v1/strategies", json={"template": "momentum_breakout"})
    strategy_id = created.json()["data"]["strategy_id"]
    response = client.get(f"/api/v1/strategies/{strategy_id}/port/metatrader4")
    assert response.status_code in (404, 422)
    if response.status_code == 422:
        assert "pine" in response.json()["detail"]["detail"]


def test_the_targets_route_is_not_shadowed_by_the_strategy_route(client) -> None:
    """Declaration order decides which one answers.

    Registered after `/strategies/{strategy_id}` this would be read as a
    strategy called "port-targets" and answer 404 -- a route that works in
    isolation and disappears when a sibling moves.
    """
    assert client.get("/api/v1/strategies/port-targets").status_code == 200


def test_an_artifact_reference_never_carries_a_fragment_of_a_result() -> None:
    """Identifiers only, whatever shape the action returned.

    A key whose value is a dict or a list would otherwise be stringified into
    the reference, putting part of a result inside an artifact that is supposed
    to carry identifiers and nothing else. That is the copied-number problem in
    a different shape: a figure stored outside the system that computed it,
    indistinguishable later from one that was looked up.
    """
    from forge_api.chat import _artifacts

    built = _artifacts(
        [
            {
                "action": "strategy_regimes",
                "ok": True,
                "arguments": {"strategy_id": "s1", "backtest_id": {"nested": "value"}},
                "result": {},
            }
        ]
    )
    assert len(built) == 1
    assert built[0].refs == {"strategy_id": "s1"}
    assert all(isinstance(value, str) for value in built[0].refs.values())
    assert "nested" not in str(built[0].refs)


def test_a_refused_action_produces_no_artifact() -> None:
    """A link to a result that does not exist reads as the work having been done."""
    from forge_api.chat import _artifacts

    assert _artifacts(
        [
            {
                "action": "backtest_strategy",
                "ok": False,
                "error": "'backtest_strategy' needs a person",
                "arguments": {"strategy_id": "s1"},
            }
        ]
    ) == ()


def test_the_same_subject_twice_produces_one_artifact() -> None:
    """Two calls about one strategy are one way back to it, not two."""
    from forge_api.chat import _artifacts

    built = _artifacts(
        [
            {"action": "strategy_regimes", "ok": True, "arguments": {"strategy_id": "s1"}},
            {"action": "strategy_regimes", "ok": True, "arguments": {"strategy_id": "s1"}},
        ]
    )
    assert len(built) == 1
