"""A research campaign: an objective, a budget, and the record of what it did.

"Start the engine" is not a research act. It says what machinery to run and
nothing about what question is being asked, how much may be spent answering it,
or what would count as finished — so a run that had been going for six hours had
no way to report whether it was making progress or repeating itself.

A campaign supplies all three. It names an objective, fixes the dataset and the
universe, declares the budgets, carries the allocation across the five search
categories, states when to stop, and accumulates the counters that say what has
actually happened. Everything the interface shows about an autonomous run is a
projection of one campaign row and the frontier and hypothesis stores it points
at.

**Budgets are enforced, not decorative.** ``exhausted`` is checked at the top of
every cycle, and each stopping criterion reports itself by name so "why did it
stop" has an answer that is not "it stopped".

**Progress distinguishes trials from discoveries.** ``experiments`` and
``hypotheses`` are separate counters and are displayed as separate counters,
because a hundred parameter draws against one claim is one piece of research and
presenting it as a hundred is the specific dishonesty this whole subsystem was
built to remove.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from forge.contracts.hashing import stable_id
from forge.research.allocation import Bucket, ResearchAllocation

SCHEMA_VERSION = 1

#: Data capabilities a bar-based installation can actually serve. Mirrors
#: `forge.strategy.families.SUPPORTED_DATA`; a campaign may narrow this but
#: never widen it, because widening it would not conjure the data.
DEFAULT_CAPABILITIES: tuple[str, ...] = ("BARS", "VOLUME", "SESSION_CLOCK")


class CampaignError(ValueError):
    """A campaign operation was refused. The message says why."""


@dataclass(frozen=True)
class StoppingCriteria:
    """When the campaign is finished, and which condition finished it."""

    #: Hard ceiling on experiments. The primary budget.
    max_experiments: int = 500
    #: Ceiling on compute, in the backtest-equivalent units
    #: `forge.research.information` counts in.
    max_compute_units: float = 5_000.0
    #: Stop once this many candidates have cleared the judge out of sample.
    target_validated: int = 3
    #: Stop when this many consecutive proposals were refused as duplicates —
    #: the signal that the frontier around this objective is exhausted.
    max_consecutive_duplicates: int = 40
    #: Wall-clock ceiling. Zero means no limit.
    max_hours: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_experiments": self.max_experiments,
            "max_compute_units": self.max_compute_units,
            "target_validated": self.target_validated,
            "max_consecutive_duplicates": self.max_consecutive_duplicates,
            "max_hours": self.max_hours,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> StoppingCriteria:
        if not raw:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise CampaignError(
                f"Unknown stopping criteria: {', '.join(sorted(unknown))}. "
                f"Valid: {', '.join(sorted(known))}."
            )
        return cls(**{k: type(getattr(cls(), k))(v) for k, v in raw.items()})


@dataclass
class CampaignProgress:
    """What the campaign has done. Every counter is incremented at one site.

    The separation of ``experiments`` from ``hypotheses``, ``mechanisms`` and
    ``families`` is the whole point of the type. A reader looking at these five
    numbers can tell a broad search from a deep one without reading anything
    else.
    """

    experiments: int = 0
    hypotheses: int = 0
    families_created: int = 0
    templates_created: int = 0
    mechanisms: int = 0
    duplicates_rejected: int = 0
    blocked_proposals: int = 0
    consecutive_duplicates: int = 0
    failures: int = 0
    promising: int = 0
    validation_candidates: int = 0
    validated: int = 0
    inconclusive: int = 0
    sources_retrieved: int = 0
    followups_generated: int = 0
    compute_units: float = 0.0
    #: How much each allocation bucket actually consumed, so the realised split
    #: can be compared against the intended one.
    spend: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiments": self.experiments,
            "hypotheses": self.hypotheses,
            "families_created": self.families_created,
            "templates_created": self.templates_created,
            "mechanisms": self.mechanisms,
            "duplicates_rejected": self.duplicates_rejected,
            "blocked_proposals": self.blocked_proposals,
            "consecutive_duplicates": self.consecutive_duplicates,
            "failures": self.failures,
            "promising": self.promising,
            "validation_candidates": self.validation_candidates,
            "validated": self.validated,
            "inconclusive": self.inconclusive,
            "sources_retrieved": self.sources_retrieved,
            "followups_generated": self.followups_generated,
            "compute_units": round(self.compute_units, 2),
            "spend": dict(self.spend),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> CampaignProgress:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Campaign:
    """One research programme."""

    campaign_id: str
    name: str
    objective: str
    dataset: str
    symbol: str
    timeframe: str
    universe: tuple[str, ...]
    start_date: str
    end_date: str
    allocation: ResearchAllocation
    stopping: StoppingCriteria
    allowed_capabilities: tuple[str, ...]
    web_research: bool
    seed: int
    status: str
    progress: CampaignProgress
    stopped_reason: str
    created_at: str
    updated_at: str
    description: str = ""
    #: Higher runs first when the orchestrator cannot serve every campaign at
    #: once. It allocates workers; it never relaxes a gate.
    priority: int = 50
    #: How many research agents this campaign has asked for. Honoured subject to
    #: the installation's actual capacity — see `forge.research.agents`.
    agent_target: int = 1
    archived_at: str = ""
    parent_campaign_id: str = ""
    tags: tuple[str, ...] = ()

    @property
    def archived(self) -> bool:
        return bool(self.archived_at)

    @property
    def running(self) -> bool:
        return self.status == "running"

    def exhausted(self) -> tuple[bool, str]:
        """Has a stopping criterion been reached, and which one?

        Returns the reason as a sentence rather than a flag, because that
        sentence is what the interface shows and what the campaign row keeps.
        """
        s, p = self.stopping, self.progress
        if p.experiments >= s.max_experiments:
            return True, f"experiment budget reached ({s.max_experiments} experiments)"
        if p.compute_units >= s.max_compute_units:
            return True, f"compute budget reached ({s.max_compute_units:.0f} units)"
        if s.target_validated and p.validated >= s.target_validated:
            return True, f"{p.validated} candidates cleared out-of-sample validation"
        if p.consecutive_duplicates >= s.max_consecutive_duplicates:
            return (
                True,
                f"{p.consecutive_duplicates} consecutive proposals were duplicates — the "
                "frontier around this objective is exhausted",
            )
        if s.max_hours > 0:
            started = datetime.fromisoformat(self.created_at)
            elapsed = (datetime.now(UTC) - started).total_seconds() / 3600.0
            if elapsed >= s.max_hours:
                return True, f"time budget reached ({s.max_hours:g} hours)"
        return False, ""

    def serves(self, requirements: Sequence[str]) -> tuple[str, ...]:
        """Which of these data requirements this campaign cannot serve."""
        allowed = {c.upper() for c in self.allowed_capabilities}
        return tuple(sorted({r.upper() for r in requirements} - allowed))

    def as_dict(self) -> dict[str, Any]:
        exhausted, reason = self.exhausted()
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "objective": self.objective,
            "dataset": self.dataset,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "universe": list(self.universe),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "allocation": self.allocation.as_dict(),
            "stopping": self.stopping.as_dict(),
            "allowed_capabilities": list(self.allowed_capabilities),
            "web_research": self.web_research,
            "seed": self.seed,
            "status": self.status,
            "progress": self.progress.as_dict(),
            "exhausted": exhausted,
            "exhausted_reason": reason,
            "stopped_reason": self.stopped_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "priority": self.priority,
            "agent_target": self.agent_target,
            "archived": self.archived,
            "archived_at": self.archived_at,
            "parent_campaign_id": self.parent_campaign_id,
            "tags": list(self.tags),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CampaignStore:
    """Durable campaigns. One SQLite file in the workspace data root."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS campaigns ("
                "campaign_id TEXT PRIMARY KEY, name TEXT NOT NULL, objective TEXT NOT NULL, "
                "dataset TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
                "universe TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, "
                "allocation TEXT NOT NULL, stopping TEXT NOT NULL, capabilities TEXT NOT NULL, "
                "web_research INTEGER NOT NULL, seed INTEGER NOT NULL, status TEXT NOT NULL, "
                "progress TEXT NOT NULL, stopped_reason TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            self._migrate(db)

    #: Columns added after the first version. Applied by ALTER TABLE so an
    #: existing workspace keeps its campaigns rather than being asked to start
    #: over — a research programme is not a cache.
    _ADDED: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("description", "TEXT", "''"),
        # Higher runs first when the orchestrator cannot serve everything.
        ("priority", "INTEGER", "50"),
        # How many research agents this campaign has asked for.
        ("agent_target", "INTEGER", "1"),
        # Set when archived; archived campaigns keep their research and are
        # hidden from the default listing rather than deleted.
        ("archived_at", "TEXT", "''"),
        # The campaign this was duplicated from, for provenance.
        ("parent_campaign_id", "TEXT", "''"),
        ("tags", "TEXT", "'[]'"),
    )

    def _migrate(self, db: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in db.execute("PRAGMA table_info(campaigns)")}
        for name, kind, default in self._ADDED:
            if name not in existing:
                db.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {kind} DEFAULT {default}")
        db.execute("CREATE INDEX IF NOT EXISTS campaigns_status ON campaigns(status)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def create(
        self,
        *,
        name: str,
        objective: str,
        dataset: str,
        symbol: str = "NQ",
        timeframe: str = "1m",
        universe: Sequence[str] = (),
        start_date: str = "",
        end_date: str = "",
        allocation: Mapping[str, float] | ResearchAllocation | None = None,
        stopping: Mapping[str, Any] | StoppingCriteria | None = None,
        allowed_capabilities: Sequence[str] = DEFAULT_CAPABILITIES,
        web_research: bool = False,
        seed: int = 20260910,
        description: str = "",
        priority: int = 50,
        agent_target: int = 1,
        parent_campaign_id: str = "",
        tags: Sequence[str] = (),
    ) -> Campaign:
        """Register a campaign. It starts stopped: creating is not running.

        Capabilities are intersected with what the installation can serve rather
        than taken at face value. A campaign configured to allow ``L2_MBP``
        would otherwise schedule hypotheses that can never run, and the
        resulting `BLOCKED_BY_DATA` items would look like the campaign's fault
        rather than the configuration's.
        """
        objective = objective.strip()
        if len(objective) < 20:
            raise CampaignError(
                "A campaign objective needs at least 20 characters. 'find alpha' is not an "
                "objective — it does not say on what, over what, or what would count."
            )
        if not name.strip():
            raise CampaignError("A campaign needs a name.")
        requested = {c.strip().upper() for c in allowed_capabilities if c.strip()}
        capabilities = tuple(sorted(requested & set(DEFAULT_CAPABILITIES))) or ("BARS",)

        now = _now()
        campaign = Campaign(
            campaign_id=stable_id("camp", {"n": name.strip(), "o": objective, "at": now}),
            name=name.strip()[:120],
            objective=objective[:2000],
            dataset=dataset,
            symbol=symbol.upper()[:16],
            timeframe=timeframe,
            universe=tuple(u.upper() for u in universe) or (symbol.upper(),),
            start_date=start_date,
            end_date=end_date,
            allocation=(
                allocation
                if isinstance(allocation, ResearchAllocation)
                else ResearchAllocation.from_mapping(allocation)
            ),
            stopping=(
                stopping
                if isinstance(stopping, StoppingCriteria)
                else StoppingCriteria.from_mapping(stopping)
            ),
            allowed_capabilities=capabilities,
            web_research=bool(web_research),
            seed=int(seed),
            status="created",
            progress=CampaignProgress(),
            stopped_reason="",
            created_at=now,
            updated_at=now,
            description=description.strip()[:2000],
            priority=max(0, min(100, int(priority))),
            agent_target=max(1, int(agent_target)),
            parent_campaign_id=parent_campaign_id,
            tags=tuple(sorted({t.strip().lower()[:40] for t in tags if t.strip()})),
        )
        self.save(campaign)
        return campaign

    def save(self, campaign: Campaign) -> Campaign:
        campaign.updated_at = _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO campaigns ("
                "campaign_id, name, objective, dataset, symbol, timeframe, universe, "
                "start_date, end_date, allocation, stopping, capabilities, web_research, "
                "seed, status, progress, stopped_reason, schema_version, created_at, "
                "updated_at, description, priority, agent_target, archived_at, "
                "parent_campaign_id, tags) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    campaign.campaign_id,
                    campaign.name,
                    campaign.objective,
                    campaign.dataset,
                    campaign.symbol,
                    campaign.timeframe,
                    json.dumps(list(campaign.universe)),
                    campaign.start_date,
                    campaign.end_date,
                    json.dumps(campaign.allocation.as_dict()),
                    json.dumps(campaign.stopping.as_dict()),
                    json.dumps(list(campaign.allowed_capabilities)),
                    int(campaign.web_research),
                    campaign.seed,
                    campaign.status,
                    json.dumps(campaign.progress.as_dict()),
                    campaign.stopped_reason,
                    SCHEMA_VERSION,
                    campaign.created_at,
                    campaign.updated_at,
                    campaign.description,
                    campaign.priority,
                    campaign.agent_target,
                    campaign.archived_at,
                    campaign.parent_campaign_id,
                    json.dumps(list(campaign.tags)),
                ),
            )
        return campaign

    def get(self, campaign_id: str) -> Campaign | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
        return None if row is None else _to_campaign(row)

    def list(
        self, *, limit: int = 100, include_archived: bool = False
    ) -> builtins.list[Campaign]:
        """Campaigns, most recently touched first.

        Archived ones are excluded by default. Archiving keeps the research and
        removes the programme from the working set; a listing that showed both
        identically would make archiving pointless.
        """
        clause = "" if include_archived else " WHERE COALESCE(archived_at,'')=''"
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM campaigns{clause} ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [_to_campaign(row) for row in rows]

    def running(self, *, limit: int = 100) -> builtins.list[Campaign]:
        """Every running campaign, highest priority first.

        There used to be at most one, on the stated grounds that two would
        "consume each other's burn-once splits". That was not true of the code:
        the holdout ledger is keyed by *strategy lineage*, and two campaigns
        produce different strategies and therefore different lineages. The real
        shared resource was the duplicate-claim namespace in `Experiments`,
        which is now partitioned by campaign, and the trial count the judge
        deflates against — which is deliberately *not* partitioned, because
        every trial run against this data is a trial whoever ran it.

        What remains genuinely shared is compute, and that is an allocation
        problem for the orchestrator, not a reason to forbid a second campaign.
        """
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM campaigns WHERE status='running' "
                "ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [_to_campaign(row) for row in rows]

    def active(self) -> Campaign | None:
        """The highest-priority running campaign, if there is one.

        Retained for callers that predate multi-campaign support and only ever
        wanted "the" campaign. New code should use :meth:`running`.
        """
        found = self.running(limit=1)
        return found[0] if found else None

    def set_status(self, campaign_id: str, status: str, *, reason: str = "") -> Campaign:
        # Locked across the read *and* the write.
        #
        # `save` writes every column, so a get/mutate/save pair that is not
        # atomic is a lost update: a worker that read the campaign while it was
        # paused and called `record` after the operator resumed it wrote
        # `status='paused'` back over the resume. That was reproducible -- a
        # campaign resumed from the palette silently un-resumed itself a second
        # later, because a research worker mid-cycle still held the old row.
        # The lock is an RLock precisely so `save` can take it again.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            if status == "running" and campaign.archived:
                raise CampaignError(
                    f"'{campaign.name}' is archived. Restore it before running it again."
                )
            campaign.status = status
            campaign.stopped_reason = reason
            return self.save(campaign)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def duplicate(self, campaign_id: str, *, name: str = "", seed: int | None = None) -> Campaign:
        """Copy a campaign's *configuration*, never its findings.

        A duplicate starts at zero. Copying the progress counters would make the
        new programme claim experiments it has not run, and copying the frontier
        would make it claim to have settled questions it has not asked — which
        is the precise shape of the fabricated evidence this system exists to
        prevent.
        """
        source = self.get(campaign_id)
        if source is None:
            raise CampaignError(f"No campaign '{campaign_id}'.")
        return self.create(
            name=name.strip() or _copy_name(source.name),
            objective=source.objective,
            dataset=source.dataset,
            symbol=source.symbol,
            timeframe=source.timeframe,
            universe=source.universe,
            start_date=source.start_date,
            end_date=source.end_date,
            allocation=source.allocation,
            stopping=source.stopping,
            allowed_capabilities=source.allowed_capabilities,
            web_research=source.web_research,
            seed=source.seed if seed is None else int(seed),
            description=source.description,
            priority=source.priority,
            agent_target=source.agent_target,
            parent_campaign_id=source.campaign_id,
            tags=source.tags,
        )

    def archive(self, campaign_id: str) -> Campaign:
        # Locked across the read and the write; see `set_status`.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            if campaign.running:
                raise CampaignError("Stop the campaign before archiving it.")
            campaign.archived_at = _now()
            return self.save(campaign)

    def restore(self, campaign_id: str) -> Campaign:
        # Locked across the read and the write; see `set_status`.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            campaign.archived_at = ""
            return self.save(campaign)

    def prioritise(self, campaign_id: str, priority: int) -> Campaign:
        # Locked across the read and the write; see `set_status`.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            campaign.priority = max(0, min(100, int(priority)))
            return self.save(campaign)

    def set_agent_target(self, campaign_id: str, agents: int) -> Campaign:
        # Locked across the read and the write; see `set_status`.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            campaign.agent_target = max(1, int(agents))
            return self.save(campaign)

    def rename(self, campaign_id: str, name: str) -> Campaign:
        # Locked across the read and the write; see `set_status`.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            if not name.strip():
                raise CampaignError("A campaign needs a name.")
            campaign.name = name.strip()[:120]
            return self.save(campaign)

    def record(self, campaign_id: str, **counters: Any) -> Campaign:
        """Add to the campaign's counters. Unknown counters are refused.

        Refusing rather than ignoring is deliberate: a typo'd counter name that
        silently did nothing would show up as a campaign that ran hundreds of
        experiments and discovered nothing, which is indistinguishable from the
        failure this subsystem exists to detect.
        """
        # Locked across the read and the write; see `set_status`.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            progress = campaign.progress
            for key, value in counters.items():
                if key == "bucket":
                    bucket = str(Bucket(str(value)))
                    progress.spend[bucket] = progress.spend.get(bucket, 0) + 1
                    continue
                if key == "consecutive_duplicates" and value == 0:
                    progress.consecutive_duplicates = 0
                    continue
                if not hasattr(progress, key):
                    raise CampaignError(
                        f"Unknown campaign counter '{key}'. A counter that does not exist would "
                        "silently record nothing."
                    )
                setattr(progress, key, getattr(progress, key) + value)
            return self.save(campaign)

    def set_progress(self, campaign_id: str, **values: Any) -> Campaign:
        """Set counters to absolute values, for the ones derived from stores.

        ``mechanisms`` and ``hypotheses`` are counted by querying the hypothesis
        graph rather than incremented, because a proposal that deduplicated into
        an existing node must not increment anything.
        """
        # Locked across the read and the write; see `set_status`.
        with self._lock:
            campaign = self.get(campaign_id)
            if campaign is None:
                raise CampaignError(f"No campaign '{campaign_id}'.")
            for key, value in values.items():
                if not hasattr(campaign.progress, key):
                    raise CampaignError(f"Unknown campaign counter '{key}'.")
                setattr(campaign.progress, key, value)
            return self.save(campaign)

    def delete(self, campaign_id: str) -> bool:
        with self._lock, closing(self._connect()) as db, db:
            return bool(
                db.execute("DELETE FROM campaigns WHERE campaign_id=?", (campaign_id,)).rowcount
            )


def _copy_name(name: str) -> str:
    """`X` becomes `X (copy)`; `X (copy)` becomes `X (copy 2)`, and so on."""
    if not name.endswith(")"):
        return f"{name} (copy)"[:120]
    head, _, tail = name.rpartition(" (")
    if tail == "copy)":
        return f"{head} (copy 2)"[:120]
    if tail.startswith("copy ") and tail[5:-1].isdigit():
        return f"{head} (copy {int(tail[5:-1]) + 1})"[:120]
    return f"{name} (copy)"[:120]


def _to_campaign(row: sqlite3.Row) -> Campaign:
    return Campaign(
        campaign_id=row["campaign_id"],
        name=row["name"],
        objective=row["objective"],
        dataset=row["dataset"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        universe=tuple(json.loads(row["universe"])),
        start_date=row["start_date"],
        end_date=row["end_date"],
        allocation=ResearchAllocation.from_mapping(json.loads(row["allocation"])),
        stopping=StoppingCriteria.from_mapping(json.loads(row["stopping"])),
        allowed_capabilities=tuple(json.loads(row["capabilities"])),
        web_research=bool(row["web_research"]),
        seed=int(row["seed"]),
        status=row["status"],
        progress=CampaignProgress.from_mapping(json.loads(row["progress"])),
        stopped_reason=row["stopped_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        description=_column(row, "description", ""),
        priority=int(_column(row, "priority", 50) or 50),
        agent_target=int(_column(row, "agent_target", 1) or 1),
        archived_at=_column(row, "archived_at", "") or "",
        parent_campaign_id=_column(row, "parent_campaign_id", "") or "",
        tags=tuple(json.loads(_column(row, "tags", "[]") or "[]")),
    )


def _column(row: sqlite3.Row, name: str, default: Any) -> Any:
    """Read a column a migrated file may not have written yet."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return default if value is None else value
