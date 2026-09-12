"""What a refused proposal tells the proposer.

These refusals are not internal. `AgentService.run` catches them and hands the
text back as `proposal_rejected`, with a note the operator reads -- and the
proposer is expected to correct against them. A refusal that says "Parameter
outside declared range" without naming the parameter or the bound leaves that
reader to diff their proposal against the catalogue by hand, which is the one
thing the message existed to save them.

So the assertions here are about the *facts in the sentence*, not about the
fact that it refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge.strategy.templates import TEMPLATES


@pytest.fixture
def service(tmp_path: Path):
    from forge_api.activity import ActivityLog
    from forge_api.agent_service import AgentService
    from forge_api.settings_store import SettingsStore

    root = tmp_path / "workspace"
    (root / "data").mkdir(parents=True)
    return AgentService(
        root=root,
        log=ActivityLog(root / "data" / "activity.db"),
        settings=SettingsStore(root / "data" / "settings.db"),
    )


TEMPLATE = "momentum_breakout"
SOURCES = {"src_1"}


def _proposal(**overrides) -> dict:
    base = {
        "template": TEMPLATE,
        "parameters": {},
        "source_ids": ["src_1"],
        "hypothesis": "Momentum continues after a range break on expanding volume.",
    }
    base.update(overrides)
    return base


def test_a_good_proposal_is_accepted(service) -> None:
    assert service.propose(_proposal(), SOURCES)["id"]


def test_an_unknown_template_names_the_ones_that_exist(service) -> None:
    with pytest.raises(ValueError) as refusal:
        service.propose(_proposal(template="wishful_thinking"), SOURCES)
    message = str(refusal.value)
    assert "wishful_thinking" in message, "the refusal did not repeat what was asked for"
    assert TEMPLATE in message, "the refusal did not name a single template that exists"


def test_an_unknown_parameter_names_it_and_the_declared_ones(service) -> None:
    with pytest.raises(ValueError) as refusal:
        service.propose(_proposal(parameters={"moon_phase": 3}), SOURCES)
    message = str(refusal.value)
    assert "moon_phase" in message
    declared = {p.name for p in TEMPLATES[TEMPLATE].parameters}
    assert any(name in message for name in declared), "the declared parameters were not listed"


def test_a_value_outside_the_range_names_the_value_and_the_bounds(service) -> None:
    spec = TEMPLATES[TEMPLATE].parameters[0]
    with pytest.raises(ValueError) as refusal:
        service.propose(_proposal(parameters={spec.name: spec.high + spec.step * 10}), SOURCES)
    message = str(refusal.value)
    assert spec.name in message
    assert f"{spec.low:g}" in message and f"{spec.high:g}" in message, (
        f"the bounds were not in the refusal: {message}"
    )


def test_a_value_off_the_grid_offers_the_nearest_one(service) -> None:
    spec = next(
        (p for p in TEMPLATES[TEMPLATE].parameters if p.step >= 1),
        TEMPLATES[TEMPLATE].parameters[0],
    )
    off = spec.low + spec.step * 1.5
    if off > spec.high:
        pytest.skip("this template has no parameter with room for an off-grid value")
    with pytest.raises(ValueError) as refusal:
        service.propose(_proposal(parameters={spec.name: off}), SOURCES)
    message = str(refusal.value)
    assert spec.name in message
    assert "nearest" in message.lower(), f"no valid value was offered: {message}"


def test_citing_no_source_is_told_apart_from_citing_a_bad_one(service) -> None:
    """Two different mistakes that used to arrive as one sentence."""
    with pytest.raises(ValueError) as none_cited:
        service.propose(_proposal(source_ids=[]), SOURCES)
    assert "no sources" in str(none_cited.value).lower()

    with pytest.raises(ValueError) as wrong_cited:
        service.propose(_proposal(source_ids=["src_ghost"]), SOURCES)
    assert "src_ghost" in str(wrong_cited.value), "the offending id was not named"


def test_parameters_of_the_wrong_shape_says_what_was_given(service) -> None:
    with pytest.raises(ValueError) as refusal:
        service.propose(_proposal(parameters=["fast", "slow"]), SOURCES)
    assert "list" in str(refusal.value), "the refusal did not say what was passed"
