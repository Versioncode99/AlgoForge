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


def test_only_one_campaign_runs_at_a_time(tmp_path) -> None:
    """Two would share the data split and consume each other's burn-once holdout."""
    campaigns = store(tmp_path)
    first = make(campaigns, "First")
    second = make(campaigns, "Second")
    campaigns.set_status(first.campaign_id, "running")
    with pytest.raises(CampaignError, match="already running"):
        campaigns.set_status(second.campaign_id, "running")


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
