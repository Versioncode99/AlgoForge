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
from typing import Any

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
        )
        self.save(campaign)
        return campaign

    def save(self, campaign: Campaign) -> Campaign:
        campaign.updated_at = _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
        return campaign

    def get(self, campaign_id: str) -> Campaign | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
        return None if row is None else _to_campaign(row)

    def list(self, *, limit: int = 100) -> builtins.list[Campaign]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM campaigns ORDER BY updated_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [_to_campaign(row) for row in rows]

    def active(self) -> Campaign | None:
        """The running campaign, if there is one.

        At most one campaign runs at a time: they share the engine's workers,
        its data slice and its holdout ledger, and two of them running would
        consume each other's burn-once splits.
        """
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM campaigns WHERE status='running' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _to_campaign(row)

    def set_status(self, campaign_id: str, status: str, *, reason: str = "") -> Campaign:
        campaign = self.get(campaign_id)
        if campaign is None:
            raise CampaignError(f"No campaign '{campaign_id}'.")
        if status == "running":
            running = self.active()
            if running is not None and running.campaign_id != campaign_id:
                raise CampaignError(
                    f"'{running.name}' is already running. Two campaigns would share the "
                    "engine's data split and consume each other's burn-once holdout, so "
                    "only one runs at a time."
                )
        campaign.status = status
        campaign.stopped_reason = reason
        return self.save(campaign)

    def record(self, campaign_id: str, **counters: Any) -> Campaign:
        """Add to the campaign's counters. Unknown counters are refused.

        Refusing rather than ignoring is deliberate: a typo'd counter name that
        silently did nothing would show up as a campaign that ran hundreds of
        experiments and discovered nothing, which is indistinguishable from the
        failure this subsystem exists to detect.
        """
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
    )
