"""Markdown mirrors of what the engine produces.

The store under ``.store`` is the truth: JSON specs, SQLite ledgers, real Python.
None of it is readable in Obsidian. This module writes the second copy — one note
per strategy, paper, backtest, verdict, family and mission, with YAML frontmatter
and wiki links, so the vault's graph shows which paper a strategy came from and
which verdict closed it.

Two rules hold throughout:

* **The mirror is never authoritative.** Nothing reads these notes back. Editing
  one changes a note and nothing else, which is what makes it safe to leave the
  vault open while the engine runs.
* **The mirror never breaks the engine.** Every write is guarded. A locked file,
  a full disk or a vault on a disconnected network share degrades to a skipped
  note, not a failed backtest.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.vault.location import Workspace

UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows caps a path at 260 characters by default and a paper title can be
# 300. The hash suffix carried by every id keeps truncated names distinct.
MAX_STEM = 90

# f-string expressions cannot contain a backslash before Python 3.12 and are
# hard to read with one after it, so joins inside the note bodies use this.
NEWLINE = "\n"

# A family with no stated mechanism is a naming exercise. The note says so
# rather than leaving the section blank.
NO_MECHANISM = "_Not stated. A family without a mechanism is a label, not a hypothesis._"


def safe_stem(value: str) -> str:
    cleaned = UNSAFE.sub("-", value).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:MAX_STEM].rstrip() or "untitled").rstrip(".")


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, int | float):
        return str(value)
    text = str(value).replace('"', "'")
    return f'"{text}"'


def frontmatter(fields: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list | tuple):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def links(names: Iterable[str]) -> str:
    items = [f"[[{safe_stem(name)}]]" for name in names if name]
    return ", ".join(items) if items else "_none_"


class VaultMirror:
    """Writes notes into the workspace. Silent, guarded, and idempotent."""

    def __init__(self, workspace: Workspace, *, enabled: bool = True) -> None:
        self.workspace = workspace
        self.enabled = enabled
        self._lock = threading.Lock()
        self.skipped = 0
        self.written = 0
        self.last_error: str | None = None

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _write(self, folder: Path, stem: str, body: str) -> Path | None:
        if not self.enabled:
            return None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{safe_stem(stem)}.md"
            path.write_text(body.rstrip() + "\n", encoding="utf-8")
        except OSError as exc:
            with self._lock:
                self.skipped += 1
                self.last_error = f"{type(exc).__name__}: {exc.strerror or exc}"
            return None
        with self._lock:
            self.written += 1
        return path

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "notes_written": self.written,
                "notes_skipped": self.skipped,
                "last_error": self.last_error,
                "notes_root": str(self.workspace.notes),
            }

    # ── notes ────────────────────────────────────────────────────────────────
    def strategy(self, spec: Any, *, source_titles: Iterable[str] = ()) -> Path | None:
        """One note per strategy, written the moment the code hits disk."""
        params = "\n".join(
            f"| `{p.name}` | {p.default} | {p.low} to {p.high} | {p.step} | {p.description} |"
            for p in spec.parameters
        )
        body = f"""{
            frontmatter(
                {
                    "type": "algoforge-strategy",
                    "id": spec.strategy_id,
                    "name": spec.name,
                    "family": spec.family,
                    "template": spec.template,
                    "lineage": spec.lineage,
                    "market": spec.market,
                    "symbol": spec.symbol,
                    "bar_spec": spec.bar_spec,
                    "created": spec.created_at.isoformat(),
                    "created_by": spec.created_by,
                    "tags": ["algoforge", "strategy", f"family/{spec.family}"],
                }
            )
        }

# {spec.name}

**Family** [[{safe_stem(spec.family)}]] · **Template** `{spec.template}` ·
**Instrument** {spec.symbol} @ {spec.bar_spec}

## Hypothesis

{spec.hypothesis}

## What would falsify it

{spec.falsifiable_prediction}

## Parameters

| Name | Default | Range | Step | Meaning |
| --- | --- | --- | --- | --- |
{params}

## Provenance

- **Sources**: {links(source_titles)}
- **Adaptation note**: {spec.adaptation_note or "_none recorded_"}
- **Costs modelled**: {spec.commission_per_side} per side, {spec.slippage_ticks} tick slippage
- **Code**: `.store/strategies/{spec.strategy_id}/strategy.py`

> [!warning] Paper only
> Fills are modelled, not calibrated. Nothing in this note is evidence of
> future profit, and no live-order path exists.
"""
        return self._write(self.workspace.strategy_notes, f"{spec.name} · {spec.strategy_id}", body)

    def paper(self, item: Mapping[str, Any]) -> Path | None:
        """One note per research reference, curated or discovered."""
        templates = list(item.get("templates") or [])
        origin = str(item.get("origin", "unknown"))
        body = f"""{
            frontmatter(
                {
                    "type": "algoforge-paper",
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "authors": item.get("authors"),
                    "year": item.get("year"),
                    "doi": item.get("doi", ""),
                    "url": item.get("url"),
                    "origin": origin,
                    "evidence": item.get("evidence", "UNREVIEWED"),
                    "content_level": item.get("content_level", "metadata"),
                    "retrieved": item.get("retrieved_at", ""),
                    "tags": [
                        "algoforge",
                        "research",
                        f"evidence/{item.get('evidence', 'UNREVIEWED')}",
                    ],
                }
            )
        }

# {item.get("title", "Untitled")}

{item.get("authors") or "_authors not recorded_"} · {item.get("year") or "undated"}

[Open the source]({item.get("url", "")})

## What it claims

{item.get("summary") or "_No abstract was supplied. Read the source before replication._"}

## Replication gap

> {item.get("replication_gap") or "_Not assessed._"}

## Linked templates

{links(templates)}

> [!info] Evidence status: {item.get("evidence", "UNREVIEWED")}
> {
            "A curated reference summarised by hand."
            if origin == "curated"
            else "Discovered through scholarly search. Metadata and any available abstract only — "
            "nobody has read the full paper."
        }
"""
        return self._write(self.workspace.paper_notes, f"{item.get('title', 'untitled')}", body)

    def backtest(self, result: Mapping[str, Any], *, strategy_name: str) -> Path | None:
        trades = result.get("trades") or []
        net = float(result.get("net_pnl") or 0.0)
        body = f"""{
            frontmatter(
                {
                    "type": "algoforge-backtest",
                    "id": result.get("backtest_id"),
                    "strategy_id": result.get("strategy_id"),
                    "strategy": strategy_name,
                    "dataset": result.get("dataset_key"),
                    "partition": result.get("partition_name"),
                    "evidence_tier": result.get("evidence_tier"),
                    "net_pnl": round(net, 2),
                    "trades": len(trades),
                    "win_rate": round(float(result.get("win_rate") or 0.0), 4),
                    "max_drawdown": round(float(result.get("max_drawdown") or 0.0), 2),
                    "bars": result.get("bar_count"),
                    "finished": str(result.get("finished_at", "")),
                    "tags": [
                        "algoforge",
                        "backtest",
                        f"tier/{result.get('evidence_tier', 'UNKNOWN')}",
                    ],
                }
            )
        }

# Backtest · {strategy_name}

Strategy [[{safe_stem(strategy_name)}]] · partition **{result.get("partition_name") or "—"}** ·
tier **{result.get("evidence_tier") or "—"}**

| Measure | Value |
| --- | --- |
| Net P&L | {net:+,.2f} |
| Gross P&L | {float(result.get("gross_pnl") or 0.0):+,.2f} |
| Costs | {float(result.get("total_costs") or 0.0):,.2f} |
| Trades | {len(trades)} |
| Win rate | {float(result.get("win_rate") or 0.0):.1%} |
| Max drawdown | {float(result.get("max_drawdown") or 0.0):,.2f} |
| Bars | {int(result.get("bar_count") or 0):,} |
| Lookahead clean | {result.get("lookahead_clean")} |

Labels: {", ".join(f"`{label}`" for label in result.get("labels", ())) or "_none_"}

> [!caution] In-sample until proven otherwise
> A development-partition result is a measurement of the past on modelled fills.
> Only a holdout run carries evidence weight, and holdout is burn-once.
"""
        return self._write(
            self.workspace.backtest_notes,
            f"{strategy_name} · {str(result.get('backtest_id', ''))[:12]}",
            body,
        )

    def verdict(
        self, verdict: Mapping[str, Any], *, strategy_name: str, strategy_id: str
    ) -> Path | None:
        gates = verdict.get("gates") or []
        rows = NEWLINE.join(
            f"| `{g.get('gate', '?')}` | {g.get('name', '')} | {g.get('status', '?')} | "
            f"{g.get('observed', '')!s} | {str(g.get('finding', '')).replace('|', '/')} |"
            for g in gates
        )
        decision = str(verdict.get("decision", "INCONCLUSIVE"))
        failed = [g for g in gates if g.get("status") != "PASS"]
        body = f"""{
            frontmatter(
                {
                    "type": "algoforge-verdict",
                    "id": verdict.get("verdict_id"),
                    "run_id": verdict.get("run_id"),
                    "strategy_id": strategy_id,
                    "strategy": strategy_name,
                    "decision": decision,
                    "grade": verdict.get("grade"),
                    "gates_failed": len(failed),
                    "tags": ["algoforge", "verdict", f"verdict/{decision}"],
                }
            )
        }

# Verdict · {strategy_name} — {decision} (grade {verdict.get("grade", "?")})

Strategy [[{safe_stem(strategy_name)}]]

| Gate | What it tests | Result | Observed | Finding |
| --- | --- | --- | --- | --- |
{rows or "| — | no gates recorded | — | — | — |"}

{"### Why it was refused" if failed else "### Every gate cleared"}

{
            chr(10).join(f"- **{g.get('gate')}** — {g.get('finding', '')}" for g in failed)
            or "No gate returned FAIL or INCONCLUSIVE on this run."
        }

> [!note] The judge is deterministic
> No narrative, agent or operator opinion can change this outcome. A FAIL here
> is the system working: most candidates should be rejected.

Limitations recorded with this verdict:
{chr(10).join(f"- {item}" for item in verdict.get("limitations", ())) or "- none stated"}
"""
        return self._write(self.workspace.verdict_notes, f"{strategy_name} · {decision}", body)

    def family(self, item: Mapping[str, Any]) -> Path | None:
        body = f"""{
            frontmatter(
                {
                    "type": "algoforge-family",
                    "key": item.get("key"),
                    "label": item.get("label"),
                    "origin": item.get("origin", "builtin"),
                    "created": item.get("created_at", ""),
                    "tags": ["algoforge", "family"],
                }
            )
        }

# {item.get("label", item.get("key"))}

{item.get("description") or "_No description recorded._"}

## Economic mechanism

{item.get("mechanism") or NO_MECHANISM}

## Templates in this family

{links(item.get("templates") or [])}

## Data this family needs

{", ".join(f"`{d}`" for d in item.get("data_requirements", ())) or "`BARS`"}
"""
        stem = str(item.get("label") or item.get("key"))
        return self._write(self.workspace.family_notes, stem, body)

    def mission(self, item: Mapping[str, Any]) -> Path | None:
        steps = item.get("steps") or []
        rows = "\n".join(
            f"| {i + 1} | {s.get('role', '?')} | {s.get('status', 'pending')} | "
            f"{str(s.get('summary', '') or '').replace(chr(10), ' ')[:300]} |"
            for i, s in enumerate(steps)
        )
        body = f"""{
            frontmatter(
                {
                    "type": "algoforge-mission",
                    "id": item.get("id"),
                    "objective": item.get("objective"),
                    "status": item.get("status"),
                    "started": datetime.fromtimestamp(
                        float(item.get("started_at") or 0), UTC
                    ).isoformat(timespec="seconds"),
                    "steps": len(steps),
                    "tags": ["algoforge", "mission", f"mission/{item.get('status', 'unknown')}"],
                }
            )
        }

# Mission · {item.get("objective", "untitled")}

**Status** {item.get("status")} · **Plan source** {item.get("plan_source", "unknown")}

## Plan

{item.get("plan_rationale") or "_No rationale recorded._"}

## Steps

| # | Specialist | Status | Outcome |
| --- | --- | --- | --- |
{rows or "| — | — | — | no steps |"}

## What it produced

{item.get("outcome") or "_Still running, or nothing was produced._"}
"""
        return self._write(
            self.workspace.mission_notes,
            f"{item.get('objective', 'mission')} · {str(item.get('id', ''))[:10]}",
            body,
        )

    def index(self, summary: Mapping[str, Any]) -> Path | None:
        """A single dashboard note so opening the vault lands somewhere useful."""
        body = f"""{
            frontmatter(
                {
                    "type": "algoforge-index",
                    "updated": datetime.now(UTC).isoformat(timespec="seconds"),
                    "tags": ["algoforge", "index"],
                }
            )
        }

# AlgoForge

Everything below was written by the application. The folders are mirrors: the
executable truth lives in `.store`, which Obsidian hides.

| Folder | Holds | Count |
| --- | --- | --- |
| `Strategies` | one note per strategy the engine wrote | {summary.get("strategy_notes", 0)} |
| `Research Papers` | curated and discovered references | {summary.get("paper_notes", 0)} |
| `Backtests` | one note per completed run | {summary.get("backtest_notes", 0)} |
| `Verdicts` | the deterministic judge's decisions | {summary.get("verdict_notes", 0)} |
| `Families` | strategy families and their mechanisms | {summary.get("family_notes", 0)} |
| `Missions` | orchestrated multi-agent work | {summary.get("mission_notes", 0)} |

## Useful queries

```dataview
TABLE family, template, created
FROM "10 AlgoForge/Strategies"
SORT created DESC
LIMIT 25
```

```dataview
TABLE verdict, strategy
FROM "10 AlgoForge/Verdicts"
WHERE verdict = "PASS"
```

> [!warning] Paper only
> AlgoForge models fills; it does not place orders. No note in this vault is
> financial advice or evidence of future profit.
"""
        return self._write(self.workspace.notes, "AlgoForge", body)
