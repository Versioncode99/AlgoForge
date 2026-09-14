"""D1 §34's seven dimensions, asserted rather than described.

    "Track: coverage, freshness, source, entitlement, latency, timestamp
    quality, completeness."

All seven were already measured, under names that came from what each
measurement *is* rather than from this list -- `first`/`last`/`span_days` is
coverage, `findings` is completeness and timestamp quality. What was missing was
a statement that all seven are covered and something that fails when one quietly
stops being reported. This is that something.

The alternative -- a parallel data-fabric abstraction holding the same numbers a
second time -- is the complexity Doc 2 §26 names, and would have been two places
for the same measurement to disagree.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.actions import FABRIC_DIMENSIONS
from forge_api.main import create_app

#: The directive's list, verbatim and in its order.
NAMED = (
    "coverage",
    "freshness",
    "source",
    "entitlement",
    "latency",
    "timestamp_quality",
    "completeness",
)


def health(client: TestClient) -> dict[str, Any]:
    """The `data_health` payload, through the action route a caller would use."""
    response = client.post("/api/v1/actions/data_health", json={"arguments": {}})
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "fabric.db")) as client:
        yield client


def test_the_contract_names_exactly_the_seven_the_directive_names() -> None:
    assert tuple(FABRIC_DIMENSIONS) == NAMED


def test_every_dimension_says_where_it_is_reported() -> None:
    """A name with no location is a claim, not a contract."""
    for name, where in FABRIC_DIMENSIONS.items():
        assert len(where) > 10, f"{name} does not say where it is reported"


def test_data_health_carries_the_contract(client: TestClient) -> None:
    tracked = health(client)["tracked"]
    assert tuple(tracked) == NAMED
    for name in NAMED:
        assert tracked[name]["where"], f"{name} names no source"
        assert "reported" in tracked[name]


def test_reported_is_measured_rather_than_asserted(client: TestClient) -> None:
    """A dimension whose source went away must read false, not stay true.

    This vault has no imported archive, so the dataset-backed dimensions are
    honestly not reported here -- and that is the assertion. A contract that
    said "yes" on an empty installation would say "yes" on a broken one too.
    """
    tracked = health(client)["tracked"]
    dataset_backed = {"coverage", "freshness", "timestamp_quality", "completeness"}
    measurable = [row for row in health(client)["datasets"]["rows"] if row.get("measurable")]
    for name in dataset_backed:
        assert tracked[name]["reported"] is bool(measurable), (
            f"{name} claims to be reported independently of whether any dataset "
            "can actually be measured"
        )


def test_the_dimensions_that_do_not_need_an_archive_are_reported(client: TestClient) -> None:
    """Services and entitlement are declared at startup, so they are always there."""
    tracked = health(client)["tracked"]
    assert tracked["source"]["reported"] is True
    assert tracked["entitlement"]["reported"] is True
    assert tracked["latency"]["reported"] is True


def test_the_contract_is_not_a_second_fabric(client: TestClient) -> None:
    """It points at the existing sections; it does not restate their numbers.

    A contract carrying its own copy of the coverage figures would be a second
    record able to disagree with the first, which is the failure this whole
    repository is arranged against.
    """
    for entry in health(client)["tracked"].values():
        assert set(entry) == {"where", "reported"}, (
            "the contract has started carrying values of its own"
        )
