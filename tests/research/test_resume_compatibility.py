"""Resume through the real store: a continuation is allowed, a different question isn't.

`tests/research/test_identity.py` pins the comparison itself. This pins the part
that actually protects a research programme -- that the guard sits on the path a
resume takes, survives a round trip through SQLite, and does not strand the
campaigns that existed before it.
"""

from __future__ import annotations

import pytest
from forge.research.campaign import CampaignError, CampaignStore
from forge.research.identity import Verdict, compare, of, parse

OBJECTIVE = "Discover intraday NQ alpha on one-minute bars between 2008 and 2026."


def store(tmp_path) -> CampaignStore:
    return CampaignStore(tmp_path / "campaigns.db")


def make(campaigns: CampaignStore, **kwargs):
    return campaigns.create(
        name=kwargs.pop("name", "NQ Intraday"),
        objective=kwargs.pop("objective", OBJECTIVE),
        dataset=kwargs.pop("dataset", "nq_1m_16y"),
        **kwargs,
    )


def test_a_campaign_records_its_configuration_when_it_first_runs(tmp_path) -> None:
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    assert campaign.run_identity == "", "nothing has run yet"

    running = campaigns.set_status(campaign.campaign_id, "running")
    assert running.run_identity, "starting records what it ran under"
    assert parse(running.run_identity) is not None


def test_the_recorded_identity_survives_the_database(tmp_path) -> None:
    """A column that does not round trip would make every resume look legacy."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.set_status(campaign.campaign_id, "running")

    reopened = CampaignStore(tmp_path / "campaigns.db").get(campaign.campaign_id)
    assert reopened is not None
    stored = parse(reopened.run_identity)
    assert stored is not None
    assert compare(stored, of(reopened)).verdict is Verdict.COMPATIBLE


def test_stop_and_resume_under_the_same_configuration_is_allowed(tmp_path) -> None:
    """The ordinary case, and the one a guard most easily breaks."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.set_status(campaign.campaign_id, "running")
    campaigns.set_status(campaign.campaign_id, "stopped", reason="operator")

    resumed = campaigns.set_status(campaign.campaign_id, "running")
    assert resumed.status == "running"


def test_resuming_after_the_temporal_scope_moved_is_refused(tmp_path) -> None:
    """The dangerous case: the frontier would answer a different question."""
    campaigns = store(tmp_path)
    campaign = make(campaigns, start_date="2018-01-01", end_date="2024-01-01")
    campaigns.set_status(campaign.campaign_id, "running")
    campaigns.set_status(campaign.campaign_id, "stopped", reason="operator")

    moved = campaigns.get(campaign.campaign_id)
    assert moved is not None
    moved.start_date = "2010-01-01"
    campaigns.save(moved)

    with pytest.raises(CampaignError) as caught:
        campaigns.set_status(campaign.campaign_id, "running")
    message = str(caught.value)
    assert "scope" in message
    assert "Duplicate it" in message, "the refusal names the way forward"


def test_a_refused_resume_leaves_the_campaign_alone(tmp_path) -> None:
    """Refusing must not half-apply: no status change, no identity rewrite."""
    campaigns = store(tmp_path)
    campaign = make(campaigns, dataset="nq_1m_16y")
    campaigns.set_status(campaign.campaign_id, "running")
    before = campaigns.get(campaign.campaign_id)
    assert before is not None
    campaigns.set_status(campaign.campaign_id, "stopped", reason="operator")

    changed = campaigns.get(campaign.campaign_id)
    assert changed is not None
    changed.symbol = "ES"
    campaigns.save(changed)

    with pytest.raises(CampaignError):
        campaigns.set_status(campaign.campaign_id, "running")

    after = campaigns.get(campaign.campaign_id)
    assert after is not None
    assert after.status == "stopped", "the refused start did not take"
    assert after.run_identity == before.run_identity, "the old identity is intact"


def test_a_campaign_that_never_recorded_an_identity_still_runs(tmp_path) -> None:
    """Every campaign created before this existed. Stranding them is the wrong trade."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.set_status(campaign.campaign_id, "running")

    legacy = campaigns.get(campaign.campaign_id)
    assert legacy is not None
    legacy.run_identity = ""  # as an older row reads
    legacy.status = "stopped"
    campaigns.save(legacy)

    resumed = campaigns.set_status(campaign.campaign_id, "running")
    assert resumed.status == "running"
    assert resumed.run_identity, "and it records one going forward"


def test_an_unreadable_identity_does_not_brick_the_campaign(tmp_path) -> None:
    """A column that cannot be parsed must not mean 'never startable again'."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.set_status(campaign.campaign_id, "running")

    corrupt = campaigns.get(campaign.campaign_id)
    assert corrupt is not None
    corrupt.run_identity = "{not json"
    corrupt.status = "stopped"
    campaigns.save(corrupt)

    assert campaigns.set_status(campaign.campaign_id, "running").status == "running"


def test_duplicating_is_the_way_to_research_a_changed_configuration(tmp_path) -> None:
    """The refusal points here, so this has to actually work."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.set_status(campaign.campaign_id, "running")

    copy = campaigns.duplicate(campaign.campaign_id)
    assert copy.campaign_id != campaign.campaign_id
    assert copy.run_identity == "", "a duplicate has not run, so it carries no identity"
    moved = campaigns.get(copy.campaign_id)
    assert moved is not None
    moved.start_date = "2010-01-01"
    campaigns.save(moved)

    assert campaigns.set_status(copy.campaign_id, "running").status == "running"


def test_pausing_and_stopping_are_not_gated(tmp_path) -> None:
    """Only adopting state is guarded. Stopping a campaign must always work."""
    campaigns = store(tmp_path)
    campaign = make(campaigns)
    campaigns.set_status(campaign.campaign_id, "running")

    drifted = campaigns.get(campaign.campaign_id)
    assert drifted is not None
    drifted.symbol = "ES"
    campaigns.save(drifted)

    assert campaigns.set_status(campaign.campaign_id, "paused").status == "paused"
    assert campaigns.set_status(campaign.campaign_id, "stopped").status == "stopped"
