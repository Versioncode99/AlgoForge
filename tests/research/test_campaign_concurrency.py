"""A campaign's row is written whole, so two writers must not interleave.

Found by a test that passed on its own and failed in the full suite: a campaign
resumed through the action registry was `paused` again a moment later.

`CampaignStore.save` writes every column. `record`, `set_progress` and the rest
read the campaign, mutate one field and save the whole thing back, so a research
worker that read the row while the campaign was paused and finished its cycle
after the operator resumed it wrote `status='paused'` over the resume. Nothing
raised. The operator pressed resume, saw it run, and watched it stop.

These drive the interleaving deliberately rather than hoping a race shows up.
"""

from __future__ import annotations

import threading
from pathlib import Path

from forge.research.campaign import CampaignStore

OBJECTIVE = (
    "Discover intraday momentum effects in NQ one-minute bars, stated so that a "
    "result could falsify them"
)


def _store(tmp_path: Path) -> CampaignStore:
    return CampaignStore(tmp_path / "campaigns.db")


def _campaign(store: CampaignStore) -> str:
    return store.create(
        name="NQ Momentum", objective=OBJECTIVE, dataset="synthetic"
    ).campaign_id


def test_a_counter_written_during_a_resume_does_not_undo_it(tmp_path: Path) -> None:
    """The exact sequence that was losing the resume.

    A worker reads the campaign, the operator resumes it, and the worker then
    writes its counter. Serialised by the store's lock, the worker's write
    carries the *current* status rather than the one it happened to read.
    """
    store = _store(tmp_path)
    campaign_id = _campaign(store)
    store.set_status(campaign_id, "running")
    store.set_status(campaign_id, "paused", reason="paused by the operator")

    started = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            started.wait(timeout=5)
            for _ in range(60):
                store.record(campaign_id, experiments=1)
        except BaseException as exc:
            errors.append(exc)

    def operator() -> None:
        try:
            started.wait(timeout=5)
            for _ in range(60):
                store.set_status(campaign_id, "running")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker), threading.Thread(target=operator)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    final = store.get(campaign_id)
    assert final is not None
    assert final.status == "running", "a counter write undid the resume"
    # And the work itself was not lost to the serialisation either.
    assert final.progress.experiments == 60


def test_concurrent_counters_do_not_lose_increments(tmp_path: Path) -> None:
    """The same lost update, seen from the counters rather than the status.

    Two workers each recording fifty experiments must produce a hundred. Without
    the lock they produce somewhere between fifty and a hundred, and the number
    of experiments a campaign claims to have run is exactly the sort of figure
    that must not quietly drift.
    """
    store = _store(tmp_path)
    campaign_id = _campaign(store)

    def worker() -> None:
        for _ in range(50):
            store.record(campaign_id, experiments=1)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    final = store.get(campaign_id)
    assert final is not None
    assert final.progress.experiments == 100


def test_renaming_during_a_status_change_keeps_both(tmp_path: Path) -> None:
    """Any two whole-row writers, not just the pair that was caught."""
    store = _store(tmp_path)
    campaign_id = _campaign(store)
    store.set_status(campaign_id, "running")

    def rename() -> None:
        for index in range(40):
            store.rename(campaign_id, f"NQ Momentum {index}")

    def prioritise() -> None:
        for index in range(40):
            store.prioritise(campaign_id, index % 100)

    threads = [threading.Thread(target=rename), threading.Thread(target=prioritise)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    final = store.get(campaign_id)
    assert final is not None
    # Neither writer reverted the other's field to what it read.
    assert final.name == "NQ Momentum 39"
    assert final.priority == 39
    assert final.status == "running"
