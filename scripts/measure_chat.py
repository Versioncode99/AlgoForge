"""Where a chat turn's local time goes, measured rather than guessed.

Run: ``.venv/bin/python scripts/measure_chat.py [--strategies 400] [--samples 30]``

**Why it measures these things and not "chat latency".** A single number for how
long a reply takes is dominated by the provider and tells nobody what to fix.
The provider's time is not AlgoForge's to improve; the work done before the
provider is called *is*, and that is what this separates out:

* building the model's context, which re-read and re-validated every
  ``spec.json`` in the library on every single message;
* the conversation store's own reads and writes;
* accepting a run, which is what the interface waits on before it can show the
  question.

Nothing here calls a provider. A measurement that included one would vary by
more than everything it was trying to measure.

Each figure is reported as p50 and p95 over a stated sample, on a library of a
stated size, with the before/after taken **on the same machine in the same run**
— the cache is disabled for the "before" pass rather than comparing against a
number recorded on other hardware.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages"), str(ROOT / "apps" / "api")]


def percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
        "samples": len(ordered),
    }


def timed(fn: Callable[[], Any], samples: int) -> dict[str, float]:
    out: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return percentiles(out)


def build_library(root: Path, count: int) -> Any:
    """A library of `count` strategies, written the way the product writes them."""
    from forge.strategy import TEMPLATES, StrategyLibrary

    library = StrategyLibrary(root / "strategies")
    template = TEMPLATES["momentum_breakout"]
    for index in range(count):
        library.create_from_template(
            template.key,
            name=f"measured_{index:04d}",
            parameters={p.name: p.default for p in template.parameters},
        )
    return library


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategies", type=int, default=400)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "HORIZON_CHAT_PERF.json")
    args = parser.parse_args()

    from forge.conversation import ConversationStore, Provenance, Role
    from forge_api.activity import ActivityLog, BacktestStore
    from forge_api.assistant import Assistant
    from forge_api.chat import ChatService
    from forge_api.chat_runs import ChatRunner, RunStore
    from forge_api.settings_store import SettingsStore

    workspace = Path(tempfile.mkdtemp(prefix="algoforge-perf-"))
    (workspace / "data").mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    library = build_library(workspace, args.strategies)
    written_ms = (time.perf_counter() - started) * 1000.0

    assistant = Assistant(
        root=workspace,
        library=library,
        store=BacktestStore(workspace / "data"),
        log=ActivityLog(workspace / "data" / "activity.db"),
        settings=SettingsStore(workspace / "data" / "settings.json"),
    )

    report: dict[str, Any] = {
        "note": (
            "Local work only. No provider is called: provider time is not this "
            "application's to improve, and including it would swamp what is."
        ),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "strategies": args.strategies,
        "samples": args.samples,
        "library_write_ms": round(written_ms, 1),
    }

    # ── context: the N+1 that ran on every message ───────────────────────────
    # "Before" is this build with the cache defeated, so the comparison is on
    # one machine in one run rather than against a figure from another.
    def uncached() -> None:
        assistant._context_cache = None
        assistant.context()

    report["context_before"] = timed(uncached, args.samples)
    assistant._context_cache = None
    assistant.context()  # warm it once, as a real second message would find it
    report["context_after"] = timed(assistant.context, args.samples)
    report["context_cold_after"] = timed(uncached, 3)

    # ── the conversation store ───────────────────────────────────────────────
    store = ConversationStore(workspace / "data" / "conversations.db")
    report["conversation_create"] = timed(lambda: store.create(""), args.samples)
    thread = store.create("measured")
    report["conversation_append"] = timed(
        lambda: store.append(
            thread.conversation_id, Role.USER, "a question", provenance=Provenance.USER_STATEMENT
        ),
        args.samples,
    )
    report["conversation_read"] = timed(
        lambda: store.turns(thread.conversation_id), args.samples
    )
    report["conversation_list"] = timed(lambda: store.list(), args.samples)

    # ── accepting a run ──────────────────────────────────────────────────────
    # What the interface waits on before it can show the question. The work
    # itself happens on a worker; this is the acknowledgement.
    class Silent:
        def send(self, conversation_id: str, message: str, **kwargs: Any) -> dict[str, Any]:
            return {"turn": {"text": "", "artifacts": []}, "note": None}

    runner = ChatRunner(Silent(), RunStore(workspace / "data" / "chat-runs.db"))
    report["run_accept"] = timed(
        lambda: runner.start(thread.conversation_id, "measured"), args.samples
    )
    runner.shutdown()

    # ── a whole local turn, provider excluded ────────────────────────────────
    chat = ChatService(store, assistant)
    report["local_turn_total"] = timed(
        lambda: chat.send(thread.conversation_id, "how many strategies are there?"),
        max(5, args.samples // 3),
    )

    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
