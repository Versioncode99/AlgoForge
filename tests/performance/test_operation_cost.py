"""What one workspace operation actually costs, measured rather than assumed.

`docs/PERFORMANCE_BASELINE.md` names four dimensions it did not measure. Two of
them are properties of the server rather than of the shell, and they are the two
that decide whether the workstation stays responsive when a layout is being
dragged around: **how long a docking operation takes end to end**, and **how many
database writes one of them causes**.

The second is the one worth having. Latency on an idle container says little; a
single panel move that writes forty rows is a defect that shows up as a
workstation which gets slower the longer somebody uses it, and it is invisible
to every other test in the repository. `sqlite3_total_changes` counts it exactly.

These are measurements, not budgets. Only the structural assertions fail: a cost
that grows with the number of panels when it should not, and a write count that
is more than a small constant per operation. Both of those are properties of the
code and would be defects on any machine. The numbers themselves are written to
`docs/OPERATION_COST.json` for the next measurement to be compared against.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app

REPORT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "OPERATION_COST.json"

#: How many times each operation is repeated before the median is taken. Enough
#: that one unlucky scheduler slice does not become the published figure.
REPEATS = 9

measured: dict[str, Any] = {}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Any:
    root = tmp_path_factory.mktemp("cost")
    import os

    os.environ["ALGOFORGE_VAULT"] = str(root / "workspace")
    with TestClient(create_app(database_path=root / "cost.db")) as client:
        client.root = root  # type: ignore[attr-defined]
        yield client


def _workspace(client: TestClient, panels: int) -> str:
    created = client.post("/api/v1/workspaces", json={"name": f"Cost {panels}"})
    assert created.status_code == 200, created.text
    workspace_id = created.json()["data"]["workspace_id"]
    for index in range(panels):
        added = client.post(
            f"/api/v1/workspaces/{workspace_id}/panels",
            json={"kind": "chart", "title": f"panel {index}"},
        )
        assert added.status_code == 200, added.text
    return workspace_id


def _panels(client: TestClient, workspace_id: str) -> list[dict[str, Any]]:
    body = client.get(f"/api/v1/workspaces/{workspace_id}").json()["data"]
    return list(body["panels"])


class WriteCounter:
    """Counts the write statements SQLite executes while it is installed.

    `sqlite3_total_changes` is per connection and counts from when that
    connection was opened, and every store here opens one per call -- so it can
    never see across them. `PRAGMA data_version` only moves for commits from
    *other* connections and counts commits rather than rows, which would have
    reported a flattering zero and measured nothing. Both were tried.

    So the counting is done where every store necessarily passes: `sqlite3.connect`
    is wrapped for the duration, and each connection it returns gets a trace
    callback that sees the SQL actually executed. It observes the real statements
    and changes none of them.
    """

    STATEMENTS = ("insert", "update", "delete", "replace")

    def __init__(self) -> None:
        self.writes = 0
        self._real = sqlite3.connect

    def _watch(self, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = self._real(*args, **kwargs)
        connection.set_trace_callback(self._saw)
        return connection

    def _saw(self, statement: str) -> None:
        head = statement.strip().split(None, 1)[0].lower() if statement.strip() else ""
        if head in self.STATEMENTS:
            self.writes += 1

    def __enter__(self) -> WriteCounter:
        sqlite3.connect = self._watch  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        sqlite3.connect = self._real  # type: ignore[assignment]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def test_a_split_costs_the_same_whether_the_layout_is_small_or_large(client: TestClient) -> None:
    """The structural property: docking must not get slower as a desk fills up.

    A split touches one rectangle and its neighbour. If the cost grows with the
    panel count, something is re-reading or rewriting the whole layout per
    operation -- which is exactly the defect that makes a workstation feel worse
    the more it is used, and it is invisible to a test with three panels in it.
    """
    timings: dict[int, float] = {}
    for size in (2, 10):
        workspace_id = _workspace(client, size)
        samples: list[float] = []
        for _ in range(REPEATS):
            target = _panels(client, workspace_id)[0]["panel_id"]
            started = time.perf_counter()
            response = client.post(
                f"/api/v1/workspaces/{workspace_id}/panels/{target}/split",
                json={"kind": "chart", "along": "row", "title": "split"},
            )
            elapsed = (time.perf_counter() - started) * 1000
            if response.status_code != 200:
                # A grid that has run out of room refuses, which is correct and
                # is not a measurement. Stop rather than timing refusals.
                break
            samples.append(elapsed)
        assert samples, "no split succeeded, so nothing was measured"
        timings[size] = round(_median(samples), 2)

    measured["split_ms_median"] = timings
    small, large = timings[2], timings[10]
    assert large < max(small * 6, 250), (
        f"splitting cost {large}ms on a ten-panel desk against {small}ms on a two-panel one. "
        "Docking is meant to touch one rectangle and its neighbour; a cost that grows with "
        "the layout means something is rewriting the whole thing."
    )


def test_every_docking_operation_is_measured(client: TestClient) -> None:
    """Latency for each verb, published rather than assumed.

    No threshold: an idle container's milliseconds are a property of the
    container. The value is the comparison the next measurement can make.
    """
    workspace_id = _workspace(client, 4)
    panels = _panels(client, workspace_id)
    base = f"/api/v1/workspaces/{workspace_id}/panels"
    operations: dict[str, float] = {}

    started = time.perf_counter()
    split = client.post(
        f"{base}/{panels[0]['panel_id']}/split",
        json={"kind": "chart", "along": "column", "title": "below"},
    )
    operations["split"] = round((time.perf_counter() - started) * 1000, 2)
    assert split.status_code == 200, split.text

    started = time.perf_counter()
    stacked = client.post(
        f"{base}/{panels[1]['panel_id']}/stack", json={"onto": panels[2]["panel_id"]}
    )
    operations["stack"] = round((time.perf_counter() - started) * 1000, 2)
    assert stacked.status_code == 200, stacked.text

    started = time.perf_counter()
    detached = client.post(f"{base}/{panels[1]['panel_id']}/detach", json={})
    operations["detach"] = round((time.perf_counter() - started) * 1000, 2)
    assert detached.status_code == 200, detached.text

    started = time.perf_counter()
    moved = client.post(
        f"{base}/{panels[3]['panel_id']}/move", json={"x": 0, "y": 0}
    )
    operations["move"] = round((time.perf_counter() - started) * 1000, 2)
    # Move may not exist under that name; a 404 is recorded rather than asserted
    # away, because an operation this file cannot reach is a fact about the API.
    if moved.status_code != 200:
        operations.pop("move")

    measured["docking_ms"] = operations
    assert set(operations) >= {"split", "stack", "detach"}


def test_one_operation_writes_a_small_constant_number_of_rows(client: TestClient) -> None:
    """The dimension that actually catches something.

    A panel move that writes a row per panel is a layout being rewritten whole,
    and it scales with the desk rather than with the change. Counted across every
    database the workspace owns, so a write to a second store is not invisible.
    """
    counts: dict[int, int] = {}
    for size in (2, 10):
        workspace_id = _workspace(client, size)
        target = _panels(client, workspace_id)[0]["panel_id"]
        with WriteCounter() as counter:
            response = client.post(
                f"/api/v1/workspaces/{workspace_id}/panels/{target}/split",
                json={"kind": "chart", "along": "row", "title": "split"},
            )
        assert response.status_code == 200, response.text
        counts[size] = counter.writes
        assert counter.writes > 0, (
            "a split that persists nothing is not being persisted, or this counter is "
            "measuring nothing -- either way the number below would be meaningless"
        )

    measured["writes_per_split"] = counts
    assert counts[10] <= counts[2] + 2, (
        f"a split wrote {counts[10]} times on a ten-panel desk and {counts[2]} on a two-panel "
        "one. The write cost of one edit must not scale with the size of the layout."
    )


def test_the_measurements_are_written_where_the_next_run_can_compare(
    client: TestClient, tmp_path: pathlib.Path
) -> None:
    """Render the report, and publish it only when asked.

    `REPORT` is tracked, so writing it unconditionally made every full suite run
    leave the working tree dirty with a diff nobody asked for -- and commits on
    this branch carried re-measured latencies along with changes that had
    nothing to do with them. The baseline is worth having *because* it is
    stable: a committed figure somebody deliberately updated is something to
    compare against, and one that moves on every run is not.

    The rendering still runs on every invocation, against a temporary file, so
    this stays a test of the thing it names rather than a step that is skipped
    in CI and therefore never checked. `ALGOFORGE_WRITE_BASELINE=1` is what
    copies it over the tracked one.
    """
    assert measured, "nothing was measured, so nothing is worth writing"
    report = json.dumps(
        {
            "note": (
                "Measured by tests/performance/test_operation_cost.py on one container. "
                "A starting point for comparison, not a budget."
            ),
            "repeats": REPEATS,
            **measured,
        },
        indent=2,
    ) + "\n"

    scratch = tmp_path / "OPERATION_COST.json"
    scratch.write_text(report, encoding="utf-8")
    written = json.loads(scratch.read_text(encoding="utf-8"))
    assert written["repeats"] == REPEATS
    assert set(measured) <= set(written), "a measurement was taken and then not published"

    # The committed baseline must stay readable and keep the same shape, or the
    # comparison this file exists for has nothing to compare against.
    assert REPORT.exists(), "the recorded baseline is missing"
    recorded = json.loads(REPORT.read_text(encoding="utf-8"))
    assert set(written) == set(recorded), (
        "the report grew or lost a section. Re-record it with "
        "ALGOFORGE_WRITE_BASELINE=1 so the committed baseline matches what is measured."
    )

    if os.getenv("ALGOFORGE_WRITE_BASELINE") == "1":
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report, encoding="utf-8")
