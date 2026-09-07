"""Run snapshots: enough to reproduce a verdict, not merely to detect tampering.

AlgoForge already stored a ``code_hash`` on every backtest, so it could tell you
that a strategy had changed since it was judged. What it could not do was
*reconstruct* the computation: the judge's own source, the statistics module
that produced the numbers, the template the candidate came from and the identity
of the data were all left implicit. A hash that says "this differs from what ran"
is useful; it is not the same as being able to run it again.

This module writes, for one judged run, a directory holding:

* ``manifest.json`` — a SHA-256 for every file the snapshot references, so any
  later edit to any of them is detectable individually rather than in aggregate.
* ``identity.json`` — what the run *was*: dataset key and version, bar count,
  split receipt, seed, parameters, catalogue version, code hash.
* ``verdict.json`` and, when validation ran, ``evidence.json``.

Sources are **content-addressed**, not copied per run. Every captured file is
stored once under ``_sources/<sha256>.py`` and referenced from the manifest by
hash. Auto-Quant copies its judge sources into each run directory, which is
correct but grows without bound; addressing them by content keeps the same
guarantee — the exact bytes that ran are still on disk and still reachable — at
the cost of one copy per distinct version rather than one per run. A search that
judges ten thousand candidates against an unchanged judge stores that judge once.

What this does **not** claim: it does not pin the Python version, the installed
package versions, or the market data itself. The data is identified by key,
version and bar count rather than copied, because vendor archives are large and
paid for. So a snapshot makes a run *auditable and re-runnable against the same
inputs*; it is not a hermetic build. `verify()` says which of those inputs still
match, and names the ones that do not.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.contracts.hashing import content_hash

SNAPSHOT_VERSION = "1"

# The modules whose source decides what a verdict means. If any of these change,
# an old verdict was produced by different rules and should not be compared with
# a new one as though they agreed.
CAPTURED_MODULES: tuple[str, ...] = (
    "forge.judge.engine",
    "forge.judge.statistics",
    "forge.judge.metrics",
    "forge.research.validation",
    "forge.research.walkforward",
    "forge.research.cpcv",
    "forge.strategy.runtime",
)


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class CapturedSource:
    """One file whose exact bytes are part of what a verdict means."""

    label: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class SnapshotResult:
    run_id: str
    path: Path
    manifest_hash: str
    sources: tuple[CapturedSource, ...]


class RunSnapshots:
    """Content-addressed snapshot store under one workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.sources = root / "_sources"

    def _store_text(self, text: str, suffix: str = ".py") -> str:
        """Put these bytes in the content store if they are not already there."""
        digest = _sha256_text(text)
        self.sources.mkdir(parents=True, exist_ok=True)
        target = self.sources / f"{digest}{suffix}"
        if not target.exists():
            # Written once. A path that exists already holds these exact bytes,
            # because the name *is* the hash of the bytes.
            # write_bytes, not write_text: the text writer translates line
            # endings on Windows, so the file would not hash to the name it
            # was stored under and every verify would report corruption.
            target.write_bytes(text.encode("utf-8"))
        return digest

    def _capture_modules(self) -> list[CapturedSource]:
        captured: list[CapturedSource] = []
        for name in CAPTURED_MODULES:
            module = sys.modules.get(name)
            source_file = getattr(module, "__file__", None) if module else None
            if not source_file:
                continue
            path = Path(source_file)
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            captured.append(
                CapturedSource(
                    label=name,
                    sha256=self._store_text(text),
                    bytes=len(text.encode("utf-8")),
                )
            )
        return captured

    def write(
        self,
        *,
        run_id: str,
        verdict: dict[str, Any],
        identity: dict[str, Any],
        strategy_source: str | None = None,
        template_source: str | None = None,
        preregistration: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> SnapshotResult:
        """Snapshot one judged run. Existing snapshots are never overwritten.

        A run is immutable evidence: re-judging the same id would mean the first
        verdict was wrong, and the honest record of that is a second run, not a
        silent edit of the first.
        """
        directory = self.root / run_id
        if (directory / "manifest.json").exists():
            existing = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            return SnapshotResult(
                run_id=run_id,
                path=directory,
                manifest_hash=str(existing.get("manifest_hash", "")),
                sources=tuple(
                    CapturedSource(label=item["label"], sha256=item["sha256"], bytes=item["bytes"])
                    for item in existing.get("sources", [])
                ),
            )

        directory.mkdir(parents=True, exist_ok=True)
        captured = self._capture_modules()
        if strategy_source:
            captured.append(
                CapturedSource(
                    label="strategy",
                    sha256=self._store_text(strategy_source),
                    bytes=len(strategy_source.encode("utf-8")),
                )
            )
        if template_source:
            captured.append(
                CapturedSource(
                    label="template",
                    sha256=self._store_text(template_source),
                    bytes=len(template_source.encode("utf-8")),
                )
            )

        files: dict[str, dict[str, Any]] = {}

        def emit(name: str, payload: dict[str, Any]) -> None:
            target = directory / name
            text = json.dumps(payload, indent=2, sort_keys=True, default=str)
            # See _store_text on why this is write_bytes.
            target.write_bytes(text.encode("utf-8"))
            files[name] = {"sha256": _sha256_text(text), "bytes": len(text.encode("utf-8"))}

        emit("verdict.json", verdict)
        emit(
            "identity.json",
            {
                **identity,
                "snapshot_version": SNAPSHOT_VERSION,
                "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        if preregistration:
            emit("preregistration.json", preregistration)
        if evidence:
            emit("evidence.json", evidence)

        body = {
            "snapshot_version": SNAPSHOT_VERSION,
            "run_id": run_id,
            "files": files,
            "sources": [
                {"label": item.label, "sha256": item.sha256, "bytes": item.bytes}
                for item in captured
            ],
        }
        manifest = {**body, "manifest_hash": content_hash(body)}
        (directory / "manifest.json").write_bytes(
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        )
        return SnapshotResult(
            run_id=run_id,
            path=directory,
            manifest_hash=str(manifest["manifest_hash"]),
            sources=tuple(captured),
        )

    # ── reading back ─────────────────────────────────────────────────────────
    def read(self, run_id: str) -> dict[str, Any] | None:
        path = self.root / run_id / "manifest.json"
        if not path.is_file():
            return None
        try:
            loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # An unreadable manifest is a missing one; it must never read as intact.
            return None
        return loaded

    def source_text(self, sha256: str) -> str | None:
        path = self.sources / f"{sha256}.py"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def verify(self, run_id: str) -> dict[str, Any]:
        """Re-hash everything the snapshot claims and report what no longer matches.

        Returns a report rather than a bool, because "the judge source changed"
        and "the verdict file was edited" are different findings and a caller
        needs to know which happened.
        """
        manifest = self.read(run_id)
        if manifest is None:
            return {"run_id": run_id, "intact": False, "reason": "no snapshot", "changed": []}

        changed: list[dict[str, str]] = []
        directory = self.root / run_id
        for name, meta in dict(manifest.get("files", {})).items():
            target = directory / name
            if not target.is_file():
                changed.append({"kind": "file", "name": name, "problem": "missing"})
            elif _sha256_file(target) != meta.get("sha256"):
                changed.append({"kind": "file", "name": name, "problem": "modified"})

        for item in manifest.get("sources", []):
            stored = self.sources / f"{item['sha256']}.py"
            if not stored.is_file():
                changed.append(
                    {"kind": "source", "name": item["label"], "problem": "source no longer stored"}
                )
            elif _sha256_file(stored) != item["sha256"]:
                # The name is the hash, so this means the content store itself
                # was tampered with.
                changed.append(
                    {"kind": "source", "name": item["label"], "problem": "content store corrupt"}
                )

        return {
            "run_id": run_id,
            "intact": not changed,
            "manifest_hash": manifest.get("manifest_hash"),
            "changed": changed,
        }

    def drift(self, run_id: str) -> dict[str, Any]:
        """Do the modules that decided this verdict still have the same source?

        Distinct from :meth:`verify`. Verify asks whether the *record* is
        undamaged; drift asks whether the *rules have moved since*. A verdict
        produced by a judge that has since been edited is still an honest record
        of what that judge decided — it just cannot be compared with a fresh
        verdict as though the two agreed.
        """
        manifest = self.read(run_id)
        if manifest is None:
            return {"run_id": run_id, "comparable": False, "reason": "no snapshot", "drifted": []}

        current = {item.label: item.sha256 for item in self._capture_modules()}
        drifted = [
            {"module": item["label"], "was": item["sha256"], "now": current.get(item["label"], "")}
            for item in manifest.get("sources", [])
            if item["label"] in current and current[item["label"]] != item["sha256"]
        ]
        return {"run_id": run_id, "comparable": not drifted, "drifted": drifted}
