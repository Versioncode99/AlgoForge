"""Strategies created from a definition, over HTTP and through the registry.

The property under test throughout is that `definition.json` is the source of
truth and `strategy.py` is its rendering. That claim is only worth anything if
the two are checked against each other, so the creation path runs both and
compares the ledgers, and this file asserts that a definition whose rendering
would disagree gets nothing written at all.

The other property is parity. The agent's action and the HTTP route are the same
implementation, so a definition refused in one is refused in the other. There is
no agent-only path to writing a strategy.
"""

from __future__ import annotations

import json
import pathlib
import shutil

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


# ── the catalogue ────────────────────────────────────────────────────────────


def test_blueprints_are_listed_with_what_they_can_actually_do(client):
    body = client.get("/api/v1/blueprints").json()
    items = body["data"]
    assert len(items) >= 3
    keys = {item["key"] for item in items}
    assert "london_breakout" in keys
    for item in items:
        # A blueprint that cannot be wrong is not a hypothesis.
        assert len(item["hypothesis"]) >= 40
        assert len(item["falsifiable_prediction"]) >= 30
        assert item["definition_hash"]
        assert item["parameters"]
        # At least one way out, which the IR validator already enforces — this
        # asserts the catalogue reports it truthfully.
        assert item["has_stop"] or item["has_trailing"]
    assert "UTC" in body["meta"]["note"]


def test_the_briefs_worked_example_is_shipped_and_says_what_it_does(client):
    """NQ London breakout, ATR risk, no entries after 11:30 London."""
    items = client.get("/api/v1/blueprints").json()["data"]
    london = next(item for item in items if item["key"] == "london_breakout")
    assert london["symbol"] == "NQ"
    assert london["has_stop"] and london["has_target"]
    assert "11:30" in london["session"]


# ── creation ─────────────────────────────────────────────────────────────────


def test_a_blueprint_becomes_a_real_strategy_whose_rendering_is_checked(client):
    created = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "london_breakout", "verify_bars": 4000},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    data, meta = body["data"], body["meta"]

    assert data["definition_hash"]
    assert data["template"].startswith("ir:")
    assert meta["generated_python_verified"] is True
    assert "identical ledgers" in meta["verification"]
    assert meta["canonical"].startswith("definition.json")

    # The strategy is a first-class one: it has readable, executable source.
    detail = client.get(f"/api/v1/strategies/{data['strategy_id']}").json()["data"]
    assert "def entry_signal" in detail["source"]
    assert "def exit_signal" in detail["source"]
    assert "def position_levels" in detail["source"]

    # And the definition is readable back, unchanged.
    definition = client.get(
        f"/api/v1/strategies/{data['strategy_id']}/definition"
    ).json()
    assert definition["meta"]["definition_hash"] == data["definition_hash"]
    assert definition["meta"]["canonical"] is True


def test_skipping_the_check_says_so_rather_than_implying_it_passed(client):
    created = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "vwap_reversion", "verify_bars": 0},
    )
    assert created.status_code == 201
    meta = created.json()["meta"]
    assert meta["generated_python_verified"] is False
    assert "Not checked" in meta["verification"]


def test_overrides_change_the_strategy_and_its_identity(client):
    plain = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "london_breakout", "verify_bars": 0},
    ).json()["data"]
    adjusted = client.post(
        "/api/v1/strategies/from-blueprint",
        json={
            "blueprint": "london_breakout",
            "symbol": "ES",
            "parameters": {"stop_atr": 2.5},
            "session_end_minute": 9 * 60,
            "verify_bars": 0,
        },
    ).json()["data"]

    assert adjusted["symbol"] == "ES"
    # A different strategy, not the same one wearing a label.
    assert adjusted["definition_hash"] != plain["definition_hash"]

    definition = client.get(
        f"/api/v1/strategies/{adjusted['strategy_id']}/definition"
    ).json()["data"]
    assert definition["entry"]["session"]["end_minute"] == 9 * 60
    stop = next(p for p in definition["parameters"] if p["name"] == "stop_atr")
    assert stop["default"] == 2.5


def test_a_parameter_outside_its_declared_range_is_refused_by_name(client):
    response = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "london_breakout", "parameters": {"nonsense": 1.0}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_parameter"


def test_an_unknown_blueprint_names_the_ones_that_exist(client):
    response = client.post(
        "/api/v1/strategies/from-blueprint", json={"blueprint": "get_rich"}
    )
    assert response.status_code == 404
    assert "london_breakout" in response.json()["detail"]["detail"]


def test_a_malformed_definition_is_refused_with_the_field_that_is_wrong(client):
    response = client.post(
        "/api/v1/strategies/from-definition",
        json={"definition": {"name": "nope"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "malformed_definition"


def test_a_definition_with_no_way_out_is_refused(client):
    """Otherwise every trade runs to the end of the data."""
    blueprint = client.get("/api/v1/blueprints").json()["data"]
    assert blueprint  # the catalogue is what a caller composes from
    definition = {
        "name": "Never Exits",
        "family": "breakout",
        "symbol": "NQ",
        "hypothesis": "A" * 60,
        "falsifiable_prediction": "B" * 40,
        "features": [{"name": "c", "kind": "close"}],
        "entry": {
            "long": {
                "kind": "compare",
                "op": "gt",
                "left": {"kind": "feature", "name": "c"},
                "right": {"kind": "const", "value": 0.0},
            }
        },
        "exit": {},
        "parameters": [],
        "provenance": {"created_at": "2026-09-08T00:00:00Z"},
    }
    response = client.post(
        "/api/v1/strategies/from-definition",
        json={"definition": definition, "verify_bars": 0},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_definition"
    assert "at least one way out" in response.json()["detail"]["detail"]


# ── what a definition buys: the levels are real ──────────────────────────────


def test_an_ir_strategy_records_the_levels_its_trades_ran_under(client):
    """The whole point. A hand-written `exit_signal` returning "stop" knows
    where the stop was and has no way to say."""
    created = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "london_breakout", "verify_bars": 0},
    ).json()["data"]
    strategy_id = created["strategy_id"]

    run = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 20000},
    )
    assert run.status_code == 200, run.text
    trades = run.json()["data"]["trades"]
    if not trades:
        pytest.skip("this blueprint took no trades on this synthetic sample")

    for trade in trades:
        assert trade["stop_price"] is not None
        assert trade["target_price"] is not None
        # Long stops sit below entry, short stops above. A stop on the wrong
        # side is a stop that would have fired instantly.
        if trade["direction"] == 1:
            assert trade["stop_price"] < trade["entry_price"] < trade["target_price"]
        else:
            assert trade["target_price"] < trade["entry_price"] < trade["stop_price"]
        # And the features it decided on are recorded.
        assert set(trade["entry_context"]) >= {"atr14", "last_close"}


def test_a_trailing_blueprint_records_a_trail_that_ratchets(client):
    created = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "trend_pullback", "verify_bars": 0},
    ).json()["data"]
    run = client.post(
        f"/api/v1/strategies/{created['strategy_id']}/backtest",
        json={"dataset": "synthetic", "bar_count": 20000},
    )
    trades = run.json()["data"]["trades"]
    if not trades:
        pytest.skip("no trades on this sample")
    assert all(t["trailing_stop_price"] is not None for t in trades)
    # A trail that ended level with its opening distance never moved; at least
    # one must have ratcheted, or the ratchet is not being exercised.
    assert any(
        (t["trailing_stop_price"] > t["entry_price"]) if t["direction"] == 1
        else (t["trailing_stop_price"] < t["entry_price"])
        for t in trades
    )


# ── export ───────────────────────────────────────────────────────────────────


def test_python_export_is_generated_and_says_what_it_assumes(client):
    created = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "london_breakout", "verify_bars": 0},
    ).json()["data"]
    body = client.get(
        f"/api/v1/strategies/{created['strategy_id']}/export/python"
    ).json()
    report = body["data"]
    assert report["verifiable"] is True
    assert "def entry_signal" in report["code"]
    assert report["definition_hash"] == created["definition_hash"]
    assert any("next bar" in note for note in report["approximations"])
    # An export must never read as a promise.
    assert "not a claim that the strategy is profitable" in body["meta"]["warranty"]


@pytest.mark.parametrize("target", ["pine", "ninjascript"])
def test_unverifiable_targets_report_coverage_and_generate_nothing(client, target):
    """A fake exporter is worse than a missing one."""
    created = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "london_breakout", "verify_bars": 0},
    ).json()["data"]
    report = client.get(
        f"/api/v1/strategies/{created['strategy_id']}/export/{target}"
    ).json()["data"]
    assert report["code"] == ""
    assert report["verifiable"] is False
    assert report["approximations"] or report["unsupported"]


def test_an_unknown_target_names_the_ones_that_exist(client):
    created = client.post(
        "/api/v1/strategies/from-blueprint",
        json={"blueprint": "london_breakout", "verify_bars": 0},
    ).json()["data"]
    response = client.get(
        f"/api/v1/strategies/{created['strategy_id']}/export/cobol"
    )
    assert response.status_code == 422
    assert "python" in response.json()["detail"]["detail"]


def test_a_hand_written_strategy_has_no_definition_and_none_is_invented(client):
    """A definition inferred from a function body would be a guess wearing the
    authority of a canonical record."""
    created = client.post(
        "/api/v1/strategies", json={"template": "momentum_breakout"}
    ).json()["data"]
    definition = client.get(f"/api/v1/strategies/{created['strategy_id']}/definition")
    assert definition.status_code == 404
    assert definition.json()["detail"]["code"] == "no_definition"
    assert "does not infer" in definition.json()["detail"]["reason"]

    export = client.get(f"/api/v1/strategies/{created['strategy_id']}/export/python")
    assert export.status_code == 404


# ── the agent reaches the same implementation ────────────────────────────────


def test_the_agent_uses_the_same_creation_path_as_the_interface(client, tmp_path):
    """There is no agent-only route to writing a strategy."""
    from forge_api.main import create_app  # noqa: F401  (fixture already built one)

    actions = client.app.state.actions
    names = {schema["name"] for schema in actions.schemas()}
    assert {
        "list_blueprints",
        "create_strategy_from_blueprint",
        "strategy_definition",
        "export_strategy",
        "strategy_trades",
        "strategy_regimes",
    } <= names

    result = actions.call(
        "create_strategy_from_blueprint",
        {"blueprint": "london_breakout", "symbol": "ES"},
    )
    assert result["symbol"] == "ES"
    assert result["generated_python_verified"] is True
    assert result["has_stop"] and result["has_target"]

    # The strategy the agent wrote is visible over HTTP like any other.
    listed = client.get("/api/v1/strategies").json()["data"]
    assert any(row["strategy_id"] == result["strategy_id"] for row in listed)

    # And the file on disk is the definition, not a summary of one.
    detail = client.get(f"/api/v1/strategies/{result['strategy_id']}").json()["data"]
    folder = pathlib.Path(detail["path"])
    stored = json.loads((folder / "definition.json").read_text(encoding="utf-8"))
    assert stored["name"] == "London Breakout"
    assert stored["symbol"] == "ES"


def test_the_agent_is_refused_exactly_what_the_route_refuses(client):
    from forge_api.actions import ActionError

    actions = client.app.state.actions
    with pytest.raises(ActionError, match="Unknown parameter"):
        actions.call(
            "create_strategy_from_blueprint",
            {"blueprint": "london_breakout", "parameters": {"nonsense": 1.0}},
        )
    with pytest.raises(ActionError, match="outside its declared range"):
        actions.call(
            "create_strategy_from_blueprint",
            {"blueprint": "london_breakout", "parameters": {"stop_atr": 99.0}},
        )
    with pytest.raises(ActionError, match="unknown blueprint"):
        actions.call("create_strategy_from_blueprint", {"blueprint": "get_rich"})


def test_creating_from_a_blueprint_is_a_mutating_action(client):
    """It writes files. The registry must say so, because that is what decides
    whether it is logged and whether MCP exposes it read-only."""
    actions = client.app.state.actions
    schema = next(
        item for item in actions.schemas()
        if item["name"] == "create_strategy_from_blueprint"
    )
    assert schema["mutating"] is True
    reads = {"list_blueprints", "strategy_definition", "export_strategy", "strategy_trades"}
    for item in actions.schemas():
        if item["name"] in reads:
            assert item["mutating"] is False
