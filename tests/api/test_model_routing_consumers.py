"""Every caller reaches the same resolver, and none reaches around it.

This is the test the consolidation rests on. `model_routing` was already rich,
and the settings screen already wrote into it — but `Assistant.ask` read
`settings.ai.routing.get("chat", "")` and the orchestrator read
`settings.ai.routing.get("orchestrator", "")`, which is a *flat copy* of the
table that `control.py` kept in step by hand. An operator could set the chat
model, watch the rich table update, and have the conversation answered by
whatever the copy still said.

Source inspection is not enough to know that is fixed, so these tests drive the
real objects: they set a feature override, call the production path, and assert
the model that was actually sent.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from forge_api.model_choice import choose
from forge_api.settings_store import KNOWN_MODELS, SettingsStore

#: Where the flat copy may still be *read*. Empty, and asserted empty: the
#: payload derives it on the way out, which is a write, not a read.
ALLOWED_FLAT_READS: frozenset[str] = frozenset()

SOURCE = Path(__file__).resolve().parents[2]


def _reads_flat_routing(tree: ast.AST) -> bool:
    """`x.routing.get(...)` or `x.routing[...]`, as code rather than as prose.

    Parsed rather than grepped. A regex over the file text matches the sentences
    in this module's own docstring describing the behaviour being removed, which
    would make the test fail for explaining itself.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr == "routing":
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                node.func.attr == "get"
                and isinstance(owner, ast.Attribute)
                and owner.attr == "routing"
            ):
                return True
    return False


def test_no_module_reads_the_flat_routing_map_to_choose_a_model() -> None:
    """The copy is deprecated. Reading it is how the two tables disagreed."""
    offenders: list[str] = []
    for path in sorted((SOURCE / "apps" / "api" / "forge_api").glob("*.py")):
        if _reads_flat_routing(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(path.name)
    assert set(offenders) <= ALLOWED_FLAT_READS, (
        f"{offenders} still read the flat routing map instead of `model_choice.choose`"
    )


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.db")


def _catalogue_model(store: SettingsStore, tier: str) -> str:
    """A model id the configured provider actually serves, at a given tier."""
    from forge_api.providers import catalog_for

    current = store.load()
    for entry in catalog_for(current.ai.provider):
        if str(entry.get("tier")) == tier:
            return str(entry["id"])
    return str(KNOWN_MODELS[0]["id"])


def test_choose_is_the_path_from_a_feature_override_to_a_model(store: SettingsStore) -> None:
    wanted = _catalogue_model(store, "frontier")
    current = store.load()
    current.ai.model_routing.features["chat"] = wanted
    store.save(current)

    decision = choose(store.load(), "chat")
    assert decision.model == wanted
    assert decision.source == "feature"


def test_the_chat_turn_calls_the_model_the_chat_feature_names(
    store: SettingsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production path, driven end to end, with the provider call captured.

    Not `choose` again: `Assistant.ask` is what a message goes through, and the
    bug this replaces lived in `Assistant.ask` rather than in the resolver.
    """
    from forge.strategy import StrategyLibrary
    from forge_api.activity import ActivityLog, BacktestStore
    from forge_api.assistant import Assistant

    wanted = _catalogue_model(store, "frontier")
    current = store.load()
    current.ai.enabled = True
    current.ai.model_routing.features["chat"] = wanted
    # The flat copy says something else entirely. Before the consolidation this
    # is the value that would have been sent.
    current.ai.routing["chat"] = "a-model-nobody-chose"
    store.save(current)

    sent: dict[str, Any] = {}

    class FakeClient:
        def chat(self, *, model: str, system: str, prompt: str, **kwargs: Any) -> str:
            sent["model"] = model
            return '{"answer": "ok"}'

        def status(self) -> dict[str, Any]:
            return {"connected": True}

    class FakeSelection:
        provider = current.ai.provider
        client = FakeClient()
        status = {"connected": True}

    monkeypatch.setattr(
        "forge_api.assistant.resolve", lambda provider, base_url: FakeSelection()
    )

    root = tmp_path / "workspace"
    (root / "data").mkdir(parents=True, exist_ok=True)
    assistant = Assistant(
        root=root,
        library=StrategyLibrary(root / "strategies"),
        store=BacktestStore(root / "data"),
        log=ActivityLog(root / "data" / "activity.db"),
        settings=store,
    )
    assistant.ask("what is here?")

    assert sent.get("model") == wanted, (
        f"the chat turn called '{sent.get('model')}' where the Chat feature names '{wanted}'"
    )
    assert sent["model"] != "a-model-nobody-chose"


def test_the_orchestrator_plans_with_the_model_its_feature_names(
    store: SettingsStore,
) -> None:
    """The orchestrator role belongs to Chat, and reads through the same resolver."""
    from forge_api.model_routing import FEATURE_FOR_ROLE

    assert FEATURE_FOR_ROLE["orchestrator"] == "chat"
    wanted = _catalogue_model(store, "frontier")
    current = store.load()
    current.ai.model_routing.features["chat"] = wanted
    current.ai.routing["orchestrator"] = "a-model-nobody-chose"
    store.save(current)

    assert choose(store.load(), "orchestrator").model == wanted


def test_no_credential_is_ever_part_of_a_routing_decision(store: SettingsStore) -> None:
    """A decision carries a model, a source and a sentence. Never a secret."""
    decision = choose(store.load(), "chat")
    rendered = repr(decision.as_dict())
    for banned in ("sk-", "api_key", "apikey", "token", "secret", "password", "Bearer"):
        assert banned.lower() not in rendered.lower(), f"'{banned}' leaked into a decision"
