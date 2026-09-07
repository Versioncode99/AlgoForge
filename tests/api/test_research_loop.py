from __future__ import annotations

from types import SimpleNamespace

from forge_api.activity import ActivityLog
from forge_api.jobs import Job
from forge_api.research_loop import ResearchLoop
from forge_api.settings_store import SettingsStore


class RecordingActions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, name: str, arguments: dict[str, object]):
        self.calls.append((name, arguments))
        return {"created": arguments["key"], "runnable": False}


def test_cycle_hands_research_to_specialists_and_registers_supported_family(tmp_path):
    actions = RecordingActions()
    loop = ResearchLoop(
        SimpleNamespace(),
        SettingsStore(tmp_path / "settings.json"),
        ActivityLog(tmp_path / "activity.ndjson"),
        actions,
    )
    results = iter(
        [
            Job("research", "agent", "research", status="DONE", result={"source_ids": ["p1"]}),
            Job(
                "hypothesis",
                "agent",
                "hypothesis",
                status="DONE",
                result={
                    "family_candidate": {
                        "key": "inventory_pressure",
                        "label": "Inventory pressure",
                        "description": "A source-linked research classification.",
                        "mechanism": (
                            "Dealers managing inventory can create temporary, measurable "
                            "pressure in prices."
                        ),
                        "data_requirements": ["L2_MBP"],
                        "source_ids": ["p1"],
                    }
                },
            ),
            Job("engineering", "agent", "strategy", status="DONE", result={}),
        ]
    )
    loop._submit_and_wait = lambda role, task: next(results)  # type: ignore[method-assign]

    loop._run_cycle(["dealer inventory futures"])

    status = loop.status()
    assert status["cycles"] == 1
    assert status["sources_found"] == 1
    assert status["downstream_tasks"] == 2
    assert actions.calls[0][0] == "create_family"
    assert actions.calls[0][1]["key"] == "inventory_pressure"
