"""Progressive disclosure, the front door and the explanation layer, over HTTP.

The property each of these asserts is the same one: the interface and an agent
asking the same question get the same answer, because both read the declaration
rather than a copy of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge.explain.metrics import METRICS
from forge.modes.expertise import ALWAYS_VISIBLE, ExpertiseLevel
from forge.modes.intents import INTENTS
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    app = create_app(tmp_path / "explain.db")
    with TestClient(app) as running:
        running.app_state = app.state  # type: ignore[attr-defined]
        yield running


def data(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


class TestExpertise:
    def test_all_three_levels_are_published_in_order(self, client) -> None:
        payload = data(client.get("/api/v1/modes/expertise"))
        assert [item["level"] for item in payload["levels"]] == [
            level.value for level in ExpertiseLevel
        ]

    def test_the_always_visible_set_is_published_separately(self, client) -> None:
        # So a surface can check "may I hide this?" without re-deriving the rule.
        payload = data(client.get("/api/v1/modes/expertise"))
        assert set(payload["always_visible"]) == {s.value for s in ALWAYS_VISIBLE}

    def test_guided_shows_fewer_surfaces_than_quant(self, client) -> None:
        levels = {i["level"]: i for i in data(client.get("/api/v1/modes/expertise"))["levels"]}
        assert len(levels["guided"]["surfaces"]) < len(levels["quant"]["surfaces"])

    def test_no_level_hides_a_refusal(self, client) -> None:
        for level in data(client.get("/api/v1/modes/expertise"))["levels"]:
            shown = {s["surface"] for s in level["surfaces"]}
            assert {"refusal_ladder", "limitations", "unmeasured"} <= shown


class TestTheFrontDoor:
    def test_every_intent_is_published(self, client) -> None:
        payload = data(client.get("/api/v1/modes/intents"))
        assert len(payload["intents"]) == len(INTENTS)

    def test_it_can_be_narrowed_to_one_mode(self, client) -> None:
        prop = data(client.get("/api/v1/modes/intents?mode=prop_firm"))["intents"]
        normal = data(client.get("/api/v1/modes/intents?mode=normal"))["intents"]
        assert {i["intent"] for i in prop} != {i["intent"] for i in normal}
        assert all("prop_firm" in i["modes"] for i in prop)

    def test_an_unknown_mode_is_refused_rather_than_ignored(self, client) -> None:
        response = client.get("/api/v1/modes/intents?mode=casino")
        assert response.status_code == 422

    def test_every_published_intent_names_actions_the_registry_has(self, client) -> None:
        registered = set(client.app_state.actions.names())
        for intent in data(client.get("/api/v1/modes/intents"))["intents"]:
            assert set(intent["actions"]) <= registered

    def test_the_execution_intents_carry_their_caveat(self, client) -> None:
        intents = {i["intent"]: i for i in data(client.get("/api/v1/modes/intents"))["intents"]}
        assert "simulat" in intents["run_a_strategy"]["caveat"].lower()


class TestMetricGlossary:
    def test_the_whole_glossary_is_published(self, client) -> None:
        payload = data(client.get("/api/v1/explain/metrics"))
        assert len(payload["metrics"]) == len(METRICS)

    def test_a_subset_can_be_asked_for(self, client) -> None:
        payload = data(client.get("/api/v1/explain/metrics?keys=pbo,sharpe"))
        assert [m["key"] for m in payload["metrics"]] == ["pbo", "sharpe"]

    def test_every_entry_answers_all_four_questions(self, client) -> None:
        for metric in data(client.get("/api/v1/explain/metrics"))["metrics"]:
            assert metric["what"] and metric["why"] and metric["definition"]
            assert metric["caveats"]

    def test_interpreting_a_value_names_the_bar_and_who_set_it(self, client) -> None:
        payload = data(client.get("/api/v1/explain/metrics/pbo?value=0.12"))
        assert payload["reading"] == "meets"
        assert "OVERFITTING_THRESHOLD" in payload["sentence"]

    def test_a_value_below_the_bar_reads_as_below(self, client) -> None:
        payload = data(client.get("/api/v1/explain/metrics/deflated_sharpe?value=0.2"))
        assert payload["reading"] == "below"

    def test_an_absent_value_is_not_comparable_rather_than_zero(self, client) -> None:
        payload = data(client.get("/api/v1/explain/metrics/calmar"))
        assert payload["reading"] == "not_comparable"
        assert "was not measured" in payload["sentence"]

    def test_an_unknown_metric_is_a_404_naming_the_known_set(self, client) -> None:
        response = client.get("/api/v1/explain/metrics/made_up")
        assert response.status_code == 404
        assert "sharpe" in response.json()["detail"]["reason"]


class TestQuestions:
    def test_every_question_is_published_with_what_it_needs(self, client) -> None:
        for item in data(client.get("/api/v1/explain/questions"))["questions"]:
            assert item["label"].endswith("?")
            assert item["requires"]


class TestPassport:
    def test_a_strategy_with_nothing_behind_it_still_has_every_section(
        self, client
    ) -> None:
        payload = data(client.get("/api/v1/explain/passport/nothing-here?depth=quant"))
        kinds = {section["kind"] for section in payload["sections"]}
        assert {"verdict", "walk_forward", "monte_carlo", "risk", "deployment"} <= kinds

    def test_every_unmeasured_section_says_what_would_measure_it(self, client) -> None:
        payload = data(client.get("/api/v1/explain/passport/nothing-here?depth=quant"))
        for section in payload["sections"]:
            if not section["measured"]:
                assert section["what_would_measure_it"]

    def test_guided_shows_fewer_sections_than_quant(self, client) -> None:
        guided = data(client.get("/api/v1/explain/passport/x?depth=guided"))
        quant = data(client.get("/api/v1/explain/passport/x?depth=quant"))
        assert len(guided["sections"]) < len(quant["sections"])

    def test_guided_still_shows_the_verdict_and_what_did_not_hold(self, client) -> None:
        guided = data(client.get("/api/v1/explain/passport/x?depth=guided"))
        kinds = {section["kind"] for section in guided["sections"]}
        assert {"verdict", "failures", "risk"} <= kinds

    def test_an_unknown_depth_is_refused_with_the_valid_set(self, client) -> None:
        response = client.get("/api/v1/explain/passport/x?depth=expert")
        assert response.status_code == 422
        assert "guided, advanced, quant" in response.json()["detail"]["reason"]

    def test_a_missing_strategy_produces_an_honest_passport_not_an_error(
        self, client
    ) -> None:
        # A strategy nobody has written has no evidence, which is a fact about
        # the strategy rather than a failure of the endpoint.
        payload = data(client.get("/api/v1/explain/passport/never-existed"))
        assert payload["strategy_id"] == "never-existed"
        assert "verdict" in payload["unmeasured"]
