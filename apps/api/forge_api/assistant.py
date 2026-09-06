"""The console assistant.

Answers questions about what this instance has actually done — strategies,
backtests, verdicts, engine state — rather than about markets in general. When no
model credential is configured it still works, using deterministic answers over
the local ledger, and says plainly that it is doing so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.strategy import TEMPLATES, StrategyLibrary

from forge_api.activity import ActivityLog, BacktestStore
from forge_api.providers import model_for, resolve
from forge_api.settings_store import SettingsStore

SYSTEM_PROMPT = """You are the console assistant inside AlgoForge, a local paper-only
quantitative research application. You answer questions about THIS instance: the
strategies it has written, the backtests it has run, the verdicts the deterministic
judge returned, and what the autonomous engine is doing.

Ground every claim in the context provided. If the context does not contain the
answer, say so rather than guessing. Never predict market direction, never suggest a
trade, and never present a backtest as evidence of future profit. Be brief and
concrete; the operator is technical."""


class Assistant:
    def __init__(
        self,
        root: Path,
        library: StrategyLibrary,
        store: BacktestStore,
        log: ActivityLog,
        settings: SettingsStore,
    ) -> None:
        self.root = root
        self.library = library
        self.store = store
        self.log = log
        self.settings = settings

    # ── local context ────────────────────────────────────────────────────────
    def context(self) -> dict[str, Any]:
        specs = self.library.list_specs()
        rows = []
        for spec in specs:
            latest = self.store.latest(spec.strategy_id)
            if latest and latest.get("calculation_version") != "contract-units-v2":
                latest = None
            rows.append(
                {
                    "id": spec.strategy_id,
                    "family": spec.family,
                    "template": spec.template,
                    "net_pnl": latest["net_pnl"] if latest else None,
                    "trades": len(latest["trades"]) if latest else 0,
                    "real_data": bool(latest and "REAL_DATA" in latest.get("labels", [])),
                }
            )
        return {
            "strategy_count": len(specs),
            "backtest_count": self.store.count(),
            "families": sorted({s.family for s in specs}),
            "templates_available": sorted(TEMPLATES),
            "strategies": rows,
            "recent_activity": [e.model_dump() for e in self.log.recent(25)],
        }

    # ── deterministic fallback ───────────────────────────────────────────────
    def _local_answer(self, question: str, ctx: dict[str, Any]) -> str:
        q = question.lower()
        tested = [s for s in ctx["strategies"] if s["net_pnl"] is not None]
        winners = [s for s in tested if s["net_pnl"] > 0]

        if any(word in q for word in ("how many", "count", "status", "summary")):
            return (
                f"{ctx['strategy_count']} strategies on disk, {ctx['backtest_count']} backtest "
                f"artifacts, {len(ctx['templates_available'])} families available "
                f"({', '.join(ctx['templates_available'])}). "
                f"{len(tested)} have been backtested; {len(winners)} are net positive."
            )
        if "best" in q or "winner" in q or "profitable" in q:
            if not winners:
                return (
                    "No current-calculation backtest in this library is net positive after costs. "
                    "Untested strategies and older results requiring a units rerun are excluded."
                )
            best = max(winners, key=lambda s: s["net_pnl"])
            return (
                f"Best is {best['id']} ({best['family']}) at {best['net_pnl']:+.2f} over "
                f"{best['trades']} trades. Treat that as in-sample until it clears a holdout."
            )
        if "fail" in q or "reject" in q or "why" in q:
            fails = [e for e in ctx["recent_activity"] if e["level"] == "fail"][:5]
            if fails:
                return "Most recent rejections:\n" + "\n".join(f"· {e['message']}" for e in fails)
            return "No failures recorded in the recent activity window."

        return (
            "No model credential is configured, so I can only answer from the local ledger. "
            f"Right now: {ctx['strategy_count']} strategies, {ctx['backtest_count']} backtests, "
            f"families {', '.join(ctx['families']) or 'none yet'}. "
            "Configure the model provider in Settings for open-ended questions."
        )

    # ── model-backed answer ──────────────────────────────────────────────────
    def ask(self, question: str) -> dict[str, Any]:
        ctx = self.context()
        current = self.settings.load()
        model = current.ai.routing.get("chat", "auto")

        if not current.ai.enabled or model in {"none", ""}:
            reason = (
                "AI is switched off in Settings"
                if not current.ai.enabled
                else "the chat role is disabled"
            )
            return {
                "answer": self._local_answer(question, ctx),
                "model": "local-ledger",
                "grounded": True,
                "note": f"Answered locally because {reason}.",
            }

        selection = resolve(current.ai.provider, current.ai.base_url)
        if not selection.status.get("connected"):
            return {
                "answer": self._local_answer(question, ctx),
                "model": "local-ledger",
                "provider": selection.provider,
                "grounded": True,
                "note": (
                    f"No model provider is reachable "
                    f"({selection.status.get('error') or 'offline'}); "
                    "answered from the local ledger instead."
                ),
            }

        try:
            answer: dict[str, Any] = selection.client.chat(
                model=model_for(selection.provider, model),
                system=SYSTEM_PROMPT,
                prompt=f"Instance context (JSON):\n{ctx}\n\nQuestion: {question}",
            )
            self.log.record(
                "ASSISTANT",
                f"answered via {selection.provider} {answer['model']} "
                f"({answer['input_tokens']}in/{answer['output_tokens']}out)",
                "info",
            )
            answer["provider"] = selection.provider
            if selection.fell_back:
                answer["note"] = "OmniRoute was not listening, so NVIDIA NIM answered."
            return answer
        except Exception as exc:
            self.log.record("ASSISTANT", f"{selection.provider} call failed: {exc}", "fail")
            return {
                "answer": self._local_answer(question, ctx),
                "model": "local-ledger",
                "provider": selection.provider,
                "grounded": True,
                "note": (f"The {selection.provider} call failed; answered from the local ledger."),
            }
