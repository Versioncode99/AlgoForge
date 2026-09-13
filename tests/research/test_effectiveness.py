"""An agent that proposes forty things and has thirty-eight refused.

Its experiment count looks like work. The registry counts experiments, findings
and errors; the skip ledger records every refusal with the agent that made it;
and until the two are joined, nothing in the system can tell that story.

These assert the join, and — more importantly — the three ways a score like this
usually lies:

* reporting a perfect acceptance rate for an agent that has proposed nothing;
* blaming an agent for an empty frontier, which is a fact about the campaign;
* reporting an efficiency of zero when compute is unmetered, which means "not
  measured" and reads as "inefficient".
"""

from __future__ import annotations

from forge.research.effectiveness import (
    IDLE_KINDS,
    REFUSAL_KINDS,
    Effectiveness,
    RoleScore,
    score,
)


def _agent(agent_id: str, role: str, **fields: object) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "role": role,
        "experiments": 0,
        "findings": 0,
        "errors": 0,
        "compute_spent": 0.0,
        **fields,
    }


def _skip(agent_id: str, kind: str, occurrences: int = 1) -> dict[str, object]:
    return {"agent_id": agent_id, "kind": kind, "occurrences": occurrences}


# ── the join ─────────────────────────────────────────────────────────────────


def test_an_agents_refusals_are_counted_against_its_role() -> None:
    result = score(
        [_agent("a1", "DISCOVERY", experiments=2)],
        [_skip("a1", "NOT_NOVEL", 38)],
    )
    entry = result.roles[0]
    assert entry.proposals == 40
    assert entry.experiments == 2
    assert entry.refusals == 38
    assert entry.acceptance == 0.05


def test_two_agents_in_one_role_are_summed() -> None:
    result = score(
        [_agent("a1", "REGIME", experiments=3), _agent("a2", "REGIME", experiments=1)],
        [_skip("a1", "NOT_NOVEL", 2), _skip("a2", "NOT_NOVEL")],
    )
    entry = result.roles[0]
    assert entry.agents == 2
    assert entry.experiments == 4
    assert entry.refusals == 3


def test_roles_come_back_in_a_stable_order() -> None:
    result = score(
        [_agent("a", "ROBUSTNESS"), _agent("b", "DISCOVERY"), _agent("c", "HYPOTHESIS")],
        [],
    )
    assert [entry.role for entry in result.roles] == ["DISCOVERY", "HYPOTHESIS", "ROBUSTNESS"]


# ── the three ways this would lie ────────────────────────────────────────────


def test_an_agent_that_proposed_nothing_does_not_score_a_perfect_acceptance() -> None:
    """Reporting 1.0 is how a dashboard says an idle crew is doing well."""
    result = score([_agent("a1", "DISCOVERY")], [])
    assert result.roles[0].acceptance == 0.0
    assert result.acceptance == 0.0


def test_an_empty_frontier_is_not_counted_against_the_agent() -> None:
    """Having nothing to propose is a fact about the campaign.

    An agent that was handed no eligible work did not do work badly; it did
    none. Counting those cycles as refusals would make a saturated campaign look
    like a faulty crew.
    """
    result = score(
        [_agent("a1", "DISCOVERY", experiments=5)],
        [_skip("a1", "NO_ELIGIBLE_WORK", 200)],
    )
    entry = result.roles[0]
    assert entry.refusals == 0
    assert entry.idle == 200
    assert entry.acceptance == 1.0


def test_unmetered_compute_reports_efficiency_as_unmeasured_rather_than_zero() -> None:
    result = score([_agent("a1", "DISCOVERY", experiments=9)], [])
    payload = result.roles[0].as_dict()
    assert payload["efficiency"] == 0.0
    assert payload["efficiency_measured"] is False


def test_metered_compute_reports_experiments_per_unit() -> None:
    result = score([_agent("a1", "DISCOVERY", experiments=9, compute_spent=3.0)], [])
    payload = result.roles[0].as_dict()
    assert payload["efficiency"] == 3.0
    assert payload["efficiency_measured"] is True


# ── what is and is not attributable ──────────────────────────────────────────


def test_a_skip_from_an_unknown_agent_is_dropped_rather_than_guessed() -> None:
    """A refusal nobody can attribute is not attributed to somebody."""
    result = score([_agent("a1", "DISCOVERY", experiments=1)], [_skip("ghost", "NOT_NOVEL", 9)])
    assert result.roles[0].refusals == 0


def test_a_skip_with_no_agent_at_all_is_dropped() -> None:
    result = score([_agent("a1", "DISCOVERY")], [{"kind": "NOT_NOVEL", "occurrences": 4}])
    assert result.refusals == 0


def test_the_refusal_and_idle_kinds_do_not_overlap() -> None:
    """A kind counted as both would be counted twice in the same total."""
    assert not (REFUSAL_KINDS & IDLE_KINDS)


def test_an_unrecognised_skip_kind_is_counted_as_neither() -> None:
    """A new kind must not silently land in the wrong column."""
    result = score([_agent("a1", "DISCOVERY", experiments=2)], [_skip("a1", "SOMETHING_NEW", 5)])
    entry = result.roles[0]
    assert entry.refusals == 0 and entry.idle == 0


# ── what gets shown ──────────────────────────────────────────────────────────


def test_only_roles_that_did_something_are_reported() -> None:
    """A row of zeroes for a role nobody ran is noise on a dashboard."""
    result = score(
        [_agent("a1", "DISCOVERY", experiments=3), _agent("a2", "REVIEWER")],
        [],
    )
    reported = {row["role"] for row in result.as_dict()["roles"]}
    assert reported == {"DISCOVERY"}


def test_the_weakest_role_is_named_only_when_it_has_proposed_enough_to_mean_it() -> None:
    """A league table of roles that proposed twice each is not a finding."""
    thin = score([_agent("a1", "DISCOVERY", experiments=1)], [_skip("a1", "NOT_NOVEL")])
    assert thin.weakest() is None

    real = score(
        [_agent("a1", "DISCOVERY", experiments=1), _agent("a2", "REGIME", experiments=8)],
        [_skip("a1", "NOT_NOVEL", 20), _skip("a2", "NOT_NOVEL", 2)],
    )
    weakest = real.weakest()
    assert weakest is not None and weakest.role == "DISCOVERY"


def test_the_payload_explains_what_a_low_acceptance_usually_means() -> None:
    payload = score([_agent("a1", "DISCOVERY", experiments=1)], []).as_dict()
    assert "exhausted" in payload["note"]
    assert "allocates compute" in payload["note"]


def test_totals_are_the_sum_of_the_roles() -> None:
    result = score(
        [
            _agent("a1", "DISCOVERY", experiments=4, findings=1),
            _agent("a2", "REGIME", experiments=6, findings=2),
        ],
        [_skip("a1", "NOT_NOVEL", 3), _skip("a2", "NO_ELIGIBLE_WORK", 7)],
    )
    totals = result.as_dict()["totals"]
    assert totals["experiments"] == 10
    assert totals["refusals"] == 3
    assert totals["idle_cycles"] == 7
    assert totals["findings"] == 3
    assert totals["acceptance"] == round(10 / 13, 4)


def test_nothing_here_can_reach_a_store() -> None:
    """It reads two stores it must not be able to write to.

    Taking their own types would make importing this module a way to reach them,
    so it takes plain mappings — and this asserts the module imports nothing
    that could.
    """
    import ast
    from pathlib import Path

    source = Path("packages/forge/research/effectiveness.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(("forge.research.agents", "forge.research.skips"))
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("forge.")


def test_an_empty_input_produces_an_empty_record_rather_than_raising() -> None:
    result = score([], [])
    assert isinstance(result, Effectiveness)
    assert result.as_dict()["roles"] == []
    assert result.acceptance == 0.0
    assert RoleScore(role="X").measured is False
