"""Campaigns: budgets that are enforced, counters that separate depth from breadth."""

from __future__ import annotations

import pytest
from forge.research.allocation import Bucket
from forge.research.campaign import (
    CampaignError,
    CampaignStore,
    StoppingCriteria,
)

OBJECTIVE = "Discover intraday NQ alpha on one-minute bars between 2008 and 2026."


def store(tmp_path) -> CampaignStore:
    return CampaignStore(tmp_path / "campaigns.db")


def make(campaigns: CampaignStore, name: str = "NQ Intraday", **kwargs):
    return campaigns.create(
        name=name, objective=kwargs.pop("objective", OBJECTIVE), dataset="nq_1m_16y", **kwargs
    )


def test_a_campaign_starts_stopped(tmp_path) -> None:
    """Creating is not running."""
    assert make(store(tmp_path)).status == "created"


def test_an_objective_that_says_nothing_is_refused(tmp_path) -> None:
    with pytest.raises(CampaignError, match="objective"):
        make(store(tmp_path), objective="find alpha")


def test_capabilities_are_intersected_with_what_can_be_served(tmp_path) -> None:
    """Allowing L2 would schedule hypotheses that can never run."""
    campaign = make(store(tmp_path), allowed_capabilities=["BARS", "L2_MBP", "OPTIONS_CHAIN"])
    assert "L2_MBP" not in campaign.allowed_capabilities
    assert "BARS" in campaign.allowed_capabilities


def test_serves_names_exactly_what_is_missing(tmp_path) -> None:
    campaign = make(store(tmp_path), allowed_capabilities=["BARS"])
    assert campaign.serves(("BARS",)) == ()
    assert campaign.serves(("BARS", "VOLUME")) == ("VOLUME",)
    assert campaign.serves(("L2_MBP", "TRADE_TICKS")) == ("L2_MBP", "TRADE_TICKS")


def test_several_campaigns_run_at_once(tmp_path) -> None:
    """The old rule was stronger than the constraint it claimed to enforce.

    It refused a second running campaign on the grounds that two would "consume
    each other's burn-once holdout". They would not: the holdout ledger is keyed
    by strategy lineage, and two campaigns produce different strategies. What
    they genuinely shared was the duplicate-claim namespace, which is now
    partitioned per campaign in `Experiments`.
    """
    campaigns = store(tmp_path)
    first = make(campaigns, "NQ Intraday Alpha")
    second = make(campaigns, "ES Intraday Alpha")
    third = make(campaigns, "Volatility Research")
    campaigns.set_status(first.campaign_id, "running")
    campaigns.set_status(second.campaign_id, "running")
    campaigns.set_status(third.campaign_id, "running")
    assert {c.name for c in campaigns.running()} == {
        "NQ Intraday Alpha",
        "ES Intraday Alpha",
        "Volatility Research",
    }


def test_running_is_ordered_by_priority(tmp_path) -> None:
    campaigns = store(tmp_path)
    low = make(campaigns, "Low")
    high = make(campaigns, "High")
    campaigns.prioritise(high.campaign_id, 90)
    campaigns.prioritise(low.campaign_id, 10)
    campaigns.set_status(low.campaign_id, "running")
    campaigns.set_status(high.campaign_id, "running")
    assert [c.name for c in campaigns.running()] == ["High", "Low"]
    # `active()` still answers for callers that only ever wanted one.
    assert campaigns.active().name == "High"


def test_duplicating_copies_configuration_and_not_findings(tmp_path) -> None:
    campaigns = store(tmp_path)
    source = make(campaigns, "NQ Alpha")
    campaigns.record(source.campaign_id, experiments=40, validated=2, hypotheses=9)
    copy = campaigns.duplicate(source.campaign_id)
    assert copy.name == "NQ Alpha (copy)"
    assert copy.objective == source.objective
    assert copy.parent_campaign_id == source.campaign_id
    # Nothing about what the original established carries over. A duplicate that
    # inherited the counters would claim experiments it has not run.
    assert copy.progress.experiments == 0
    assert copy.progress.validated == 0
    assert copy.progress.hypotheses == 0
    assert copy.status == "created"
    again = campaigns.duplicate(copy.campaign_id)
    assert again.name == "NQ Alpha (copy 2)"


def test_archiving_hides_a_campaign_without_deleting_its_research(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, "Old work")
    campaigns.archive(campaign.campaign_id)
    assert [c.name for c in campaigns.list()] == []
    assert [c.name for c in campaigns.list(include_archived=True)] == ["Old work"]
    assert campaigns.get(campaign.campaign_id).archived is True
    with pytest.raises(CampaignError, match="archived"):
        campaigns.set_status(campaign.campaign_id, "running")
    campaigns.restore(campaign.campaign_id)
    assert [c.name for c in campaigns.list()] == ["Old work"]


def test_a_running_campaign_cannot_be_archived(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, "Live")
    campaigns.set_status(campaign.campaign_id, "running")
    with pytest.raises(CampaignError, match="Stop the campaign"):
        campaigns.archive(campaign.campaign_id)


def test_the_new_fields_survive_a_reopen(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, "Tagged")
    campaigns.prioritise(campaign.campaign_id, 77)
    campaigns.set_agent_target(campaign.campaign_id, 8)
    campaigns.rename(campaign.campaign_id, "Renamed")
    reopened = store(tmp_path).get(campaign.campaign_id)
    assert reopened.priority == 77
    assert reopened.agent_target == 8
    assert reopened.name == "Renamed"


def test_the_experiment_budget_stops_the_campaign(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, stopping={"max_experiments": 10})
    campaigns.record(campaign.campaign_id, experiments=10)
    exhausted, reason = campaigns.get(campaign.campaign_id).exhausted()
    assert exhausted
    assert "experiment budget" in reason


def test_consecutive_duplicates_stop_the_campaign_by_name(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, stopping={"max_consecutive_duplicates": 3})
    for _ in range(3):
        campaigns.record(campaign.campaign_id, consecutive_duplicates=1)
    exhausted, reason = campaigns.get(campaign.campaign_id).exhausted()
    assert exhausted
    assert "frontier around this objective is exhausted" in reason


def test_a_non_duplicate_resets_the_consecutive_counter(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, stopping={"max_consecutive_duplicates": 3})
    campaigns.record(campaign.campaign_id, consecutive_duplicates=1)
    campaigns.record(campaign.campaign_id, consecutive_duplicates=1)
    campaigns.record(campaign.campaign_id, consecutive_duplicates=0)
    assert campaigns.get(campaign.campaign_id).progress.consecutive_duplicates == 0
    assert not campaigns.get(campaign.campaign_id).exhausted()[0]


def test_reaching_the_validation_target_stops_it(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, stopping={"target_validated": 2})
    campaigns.record(campaign.campaign_id, validated=2)
    exhausted, reason = campaigns.get(campaign.campaign_id).exhausted()
    assert exhausted
    assert "out-of-sample validation" in reason


def test_an_unknown_counter_is_refused_rather_than_ignored(tmp_path) -> None:
    """A typo that silently recorded nothing would look exactly like the bug."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    with pytest.raises(CampaignError, match="Unknown campaign counter"):
        campaigns.record(campaign.campaign_id, experments=1)


def test_an_unknown_stopping_criterion_is_refused(tmp_path) -> None:
    with pytest.raises(CampaignError, match="Unknown stopping criteria"):
        make(store(tmp_path), stopping={"max_expriments": 10})


def test_spend_is_recorded_per_bucket(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    for _ in range(3):
        campaigns.record(campaign.campaign_id, bucket=str(Bucket.REFINE_PARAMETERS))
    campaigns.record(campaign.campaign_id, bucket=str(Bucket.DISCOVER_FAMILY))
    spend = campaigns.get(campaign.campaign_id).progress.spend
    assert spend["REFINE_PARAMETERS"] == 3
    assert spend["DISCOVER_FAMILY"] == 1


def test_experiments_and_hypotheses_are_separate_counters(tmp_path) -> None:
    """A hundred trials of one claim is one piece of research."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.record(campaign.campaign_id, experiments=100)
    campaigns.set_progress(campaign.campaign_id, hypotheses=1, mechanisms=1)
    progress = campaigns.get(campaign.campaign_id).progress
    assert progress.experiments == 100
    assert progress.hypotheses == 1
    assert progress.mechanisms == 1


def test_set_progress_is_absolute_not_additive(tmp_path) -> None:
    """A deduplicated proposal must not increment anything."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.set_progress(campaign.campaign_id, hypotheses=4)
    campaigns.set_progress(campaign.campaign_id, hypotheses=4)
    assert campaigns.get(campaign.campaign_id).progress.hypotheses == 4


def test_a_campaign_survives_a_reopen(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns, allocation={"DISCOVER_FAMILY": 0.4})
    reopened = CampaignStore(tmp_path / "campaigns.db").get(campaign.campaign_id)
    assert reopened is not None
    assert reopened.objective == OBJECTIVE
    assert reopened.allocation.as_dict()["DISCOVER_FAMILY"] > 0.3


def test_default_stopping_criteria_are_all_present() -> None:
    payload = StoppingCriteria().as_dict()
    assert set(payload) == {
        "max_experiments",
        "max_compute_units",
        "target_validated",
        "max_consecutive_duplicates",
        "max_hours",
    }
