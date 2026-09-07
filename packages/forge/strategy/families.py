"""Strategy families, as data rather than as a type annotation.

`StrategySpec.family` used to be a four-value `Literal`. That made the answer to
"can you add a family?" a code change and a redeploy, which is why the console
assistant correctly reported it could not. A family is a research classification,
not a language construct: it belongs in a registry that the operator and the
agents can extend at run time.

What a family is *not* is a licence to invent capability. Adding
``order_book_imbalance`` as a family does not conjure L2 depth data. Every family
declares the data it needs, and a family whose requirement the installed data
providers cannot satisfy is registered as ``blocked`` — visible, documented, and
refused by the strategy writer until the data exists.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KEY = re.compile(r"^[a-z][a-z0-9_]{2,39}$")

# What the bar-based engine can actually serve. A family requiring anything else
# is registered blocked rather than silently accepted.
SUPPORTED_DATA = frozenset({"BARS", "VOLUME", "SESSION_CLOCK"})


@dataclass
class Family:
    key: str
    label: str
    description: str
    mechanism: str
    data_requirements: tuple[str, ...] = ("BARS",)
    origin: str = "builtin"
    created_at: str = ""
    created_by: str = "system"
    templates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked_by(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.data_requirements) - SUPPORTED_DATA))

    @property
    def runnable(self) -> bool:
        return not self.blocked_by

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "data_requirements": list(self.data_requirements),
            "templates": list(self.templates),
            "runnable": self.runnable,
            "blocked_by": list(self.blocked_by),
            "status": "RUNNABLE" if self.runnable else "BLOCKED_DATA",
        }


def _f(
    key: str,
    label: str,
    description: str,
    mechanism: str,
    data: tuple[str, ...] = ("BARS",),
) -> Family:
    return Family(
        key=key,
        label=label,
        description=description,
        mechanism=mechanism,
        data_requirements=data,
        origin="builtin",
        created_at="2026-01-01T00:00:00+00:00",
    )


# The four original families, plus the ones the existing templates already
# implement in spirit but had to be filed under a neighbour because the Literal
# had no room for them. Each carries a mechanism, because a family without one is
# a bucket name and the judge's mechanism gate has nothing to test.
BUILTIN_FAMILIES: tuple[Family, ...] = (
    _f(
        "momentum",
        "Momentum",
        "Continuation of a directional move over a short horizon.",
        "Order flow arrives in pieces; large participants cannot execute instantly, so "
        "a move that has begun tends to continue while the parent order is worked.",
    ),
    _f(
        "mean_reversion",
        "Mean reversion",
        "Return toward a reference level after a deviation.",
        "Liquidity provision is paid for absorbing imbalance. When the imbalance clears, "
        "the compensating move back is the provider's profit.",
    ),
    _f(
        "volatility",
        "Volatility regime",
        "Behaviour conditioned on the current variance state.",
        "Variance is persistent and forecastable in a way that direction is not, so "
        "sizing and gating on estimated variance is a different bet from predicting return.",
    ),
    _f(
        "breakout",
        "Breakout",
        "Directional resolution of a compressed range.",
        "Compression concentrates resting orders at range edges; clearing them removes "
        "the liquidity that was holding price in, so the resolution is fast.",
    ),
    _f(
        "session_structure",
        "Session structure",
        "Effects tied to the clock: opens, closes, and the transitions between them.",
        "Participation is not uniform across the session. Auction opens and closes "
        "concentrate hedging and rebalancing flow that is absent mid-session.",
        ("BARS", "SESSION_CLOCK"),
    ),
    _f(
        "liquidity",
        "Liquidity events",
        "Reaction to visible liquidity being taken or replenished.",
        "A sweep through resting orders is information about urgency. Whether it "
        "continues or reclaims separates informed flow from a liquidation.",
        ("BARS", "VOLUME"),
    ),
    _f(
        "statistical",
        "Statistical structure",
        "Estimated time-series properties: half-life, variance ratio, autocorrelation.",
        "Serial dependence measured over an estimation window is a testable property "
        "of the series, independent of any story about why it exists.",
    ),
    _f(
        "carry",
        "Carry and roll",
        "Return earned from holding rather than from price direction.",
        "A futures curve in contango or backwardation pays or charges the holder as "
        "the contract converges to spot.",
        ("BARS", "CONTINUOUS_CONTRACT_ROLL"),
    ),
    _f(
        "seasonality",
        "Calendar seasonality",
        "Recurring effects tied to time of day, week or month.",
        "Institutional calendars — settlement, index rebalancing, option expiry — "
        "repeat, and the flow they generate repeats with them.",
        ("BARS", "SESSION_CLOCK"),
    ),
    _f(
        "cross_asset",
        "Cross-asset lead-lag",
        "One instrument's move informing another's.",
        "Related instruments price the same risk with different latency and liquidity, "
        "so the faster one leads.",
        ("SYNCHRONIZED_MULTI_ASSET",),
    ),
    _f(
        "microstructure",
        "Microstructure",
        "Order-book state and the mechanics of matching.",
        "Queue position, depth imbalance and adverse selection determine whether a "
        "passive fill was a gift or a mistake.",
        ("L2_MBP", "TRADE_TICKS"),
    ),
    _f(
        "event_driven",
        "Event driven",
        "Response to scheduled releases and their volatility footprint.",
        "A scheduled release resolves uncertainty at a known instant; the repricing "
        "and the volatility decay that follows are both structural.",
        ("BARS", "ECONOMIC_CALENDAR"),
    ),
)


class FamilyRegistry:
    """Built-in families plus whatever the operator and agents have added.

    Custom families persist as one JSON file each, so a family is inspectable and
    deletable with a text editor, the same way a strategy is.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._custom: dict[str, Family] = {}
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, Family] = {}
        for file in sorted(self.path.glob("*.json")):
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
                raw["data_requirements"] = tuple(raw.get("data_requirements") or ("BARS",))
                raw["templates"] = tuple(raw.get("templates") or ())
                loaded[str(raw["key"])] = Family(
                    **{k: v for k, v in raw.items() if k in Family.__dataclass_fields__}
                )
            except Exception:  # a hand-edited family must not break startup
                continue
        with self._lock:
            self._custom = loaded

    def all(self) -> list[Family]:
        with self._lock:
            merged = {family.key: family for family in BUILTIN_FAMILIES}
            merged.update(self._custom)
        return sorted(merged.values(), key=lambda f: (f.origin != "builtin", f.key))

    def all_keys(self) -> set[str]:
        return {family.key for family in self.all()}

    def get(self, key: str) -> Family | None:
        return next((f for f in self.all() if f.key == key), None)

    def runnable_keys(self) -> set[str]:
        return {f.key for f in self.all() if f.runnable}

    def create(
        self,
        *,
        key: str,
        label: str,
        description: str,
        mechanism: str,
        data_requirements: tuple[str, ...] = ("BARS",),
        created_by: str = "operator",
    ) -> Family:
        key = key.strip().lower()
        if not KEY.fullmatch(key):
            raise ValueError(
                "A family key is 3-40 characters, lowercase letters, digits and underscores, "
                f"starting with a letter. Got '{key}'."
            )
        if key in self.all_keys():
            raise ValueError(f"Family '{key}' already exists.")
        if len(mechanism.strip()) < 40:
            raise ValueError(
                "A family needs a stated economic mechanism of at least 40 characters. "
                "A name with no mechanism is a bucket, and nothing in it can be falsified."
            )
        if not label.strip():
            raise ValueError("A family needs a display label.")
        family = Family(
            key=key,
            label=label.strip()[:80],
            description=description.strip()[:600],
            mechanism=mechanism.strip()[:2000],
            data_requirements=tuple(d.strip().upper() for d in data_requirements if d.strip())
            or ("BARS",),
            origin="custom",
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            created_by=created_by,
        )
        (self.path / f"{key}.json").write_text(
            json.dumps(family.as_dict(), indent=2), encoding="utf-8"
        )
        with self._lock:
            self._custom[key] = family
        return family

    def delete(self, key: str) -> None:
        if key in {f.key for f in BUILTIN_FAMILIES}:
            raise ValueError("Built-in families cannot be deleted.")
        file = self.path / f"{key}.json"
        if not file.exists():
            raise KeyError(key)
        file.unlink()
        with self._lock:
            self._custom.pop(key, None)

    def with_templates(self, templates: dict[str, Any]) -> list[dict[str, Any]]:
        """Families annotated with the templates that currently implement them."""
        by_family: dict[str, list[str]] = {}
        for template in templates.values():
            by_family.setdefault(template.family, []).append(template.key)
        rows = []
        for family in self.all():
            keys = sorted(by_family.get(family.key, ()))
            rows.append({**family.as_dict(), "templates": keys, "template_count": len(keys)})
        return rows
