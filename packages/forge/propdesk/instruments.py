"""Instruments, contracts, provider symbols, and the maths of mapping between them.

The products studied treat NQ→MNQ as a *count substitution*: one E-mini becomes
one Micro, and a multiplier is applied on top. That is a legitimate policy — it
is deliberate de-risking — but it is not the only one, and shipping it as the
only one silently decides a risk question on the operator's behalf. A follower
running "1:1 micro" carries a tenth of the leader's exposure; a follower who
wanted the same exposure in micros needs ten.

So a mapping here is a policy with a name:

* `SAME` — the same product. No conversion.
* `COUNT_CROSS` — one contract of the source becomes one of the target. What the
  commercial copiers call Cross Order or Micro mode.
* `NOTIONAL_EQUIVALENT` — the target quantity that carries the same notional
  exposure, computed from the two multipliers.
* `RISK_BUDGET` — as many contracts as the follower's own risk allowance buys,
  given a stop distance in ticks.

Three things this module refuses to guess.

**Contract specifications it has not verified.** Every entry carries `verified`,
and a mapping that would size a position from an unverified specification
refuses rather than returning a number. The multipliers below that are marked
unverified are exactly the ones the research flagged as not individually
re-checked, and a tick value that is wrong by a factor of ten is a position that
is wrong by a factor of ten.

**Rounding direction.** One product studied always rounds *up*, which means a
follower on a 0.5 multiplier takes a whole contract where half was intended, and
can therefore never be smaller than the leader by less than one contract. That
is a real, defensible choice and it is a choice: `Rounding` makes it explicit and
`MappedQuantity` reports the resulting exposure error rather than hiding it.

**Expiry alignment.** Contracts are mapped by *product and roll policy*, not by
literal symbol, because two providers routinely sit on different front months
for a day or two around a roll and an order for the wrong month is rejected.
`RollWindow` blocks during the disagreement instead of discovering it from a
rejection.
"""

from __future__ import annotations

import math
import re
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.models import FrozenModel
from forge.propdesk.identity import Provider


class MappingError(Exception):
    """The mapping could not be computed. No quantity is returned."""


class MappingPolicy(StrEnum):
    SAME = "same"
    COUNT_CROSS = "count_cross"
    NOTIONAL_EQUIVALENT = "notional_equivalent"
    RISK_BUDGET = "risk_budget"


class Rounding(StrEnum):
    CEIL = "ceil"
    FLOOR = "floor"
    NEAREST = "nearest"


class Instrument(FrozenModel):
    """A product, with the numbers a position is actually sized from.

    `multiplier` is currency per one point of price. `tick_value` is currency per
    one tick and is derivable from the other two, so the validator checks they
    agree rather than trusting whoever typed them.
    """

    root: str = Field(min_length=1, max_length=12)
    exchange: str = Field(min_length=1, max_length=12)
    description: str = ""
    multiplier: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    tick_value: float = Field(gt=0)
    currency: str = "USD"
    #: The standard-size product this is a micro of, when it is one.
    micro_of: str | None = None
    #: Products that a firm's hedging rule would treat as the same exposure.
    #: NQ and MNQ are one group; a rule against cross-account hedging applies
    #: across the group, not merely across the symbol.
    product_group: str = ""
    #: Month codes this product lists. Used by the roll logic.
    listing_cycle: str = ""
    #: False when the specification has not been checked against the exchange.
    #: A mapping that needs the multiplier refuses on an unverified instrument.
    verified: bool = False
    source_note: str = ""

    @model_validator(mode="after")
    def _tick_value_agrees(self) -> Instrument:
        implied = self.multiplier * self.tick_size
        if abs(implied - self.tick_value) > max(1e-6, implied * 1e-6):
            raise ValueError(
                f"{self.root}: tick_value {self.tick_value} disagrees with "
                f"multiplier x tick_size ({implied}); one of the three is wrong"
            )
        return self

    @property
    def group(self) -> str:
        return self.product_group or self.micro_of or self.root


class Contract(FrozenModel):
    """A dated instance of a product."""

    root: str = Field(min_length=1, max_length=12)
    #: The exchange's dated symbol, e.g. `NQU6`.
    exchange_symbol: str = Field(min_length=2, max_length=24)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2100)
    last_trade_date: date | None = None
    is_front: bool = False

    @property
    def month_code(self) -> str:
        return MONTH_CODES[self.expiry_month]


class ProviderSymbol(FrozenModel):
    """How one provider names one contract.

    Provider ids are *discovered*, not constructed: one provider's identifier
    for the September 2026 E-mini Nasdaq bears no useful relation to the
    exchange symbol, and building it from a template would produce a
    plausible-looking string that the provider rejects.
    """

    provider: Provider
    exchange_symbol: str
    provider_id: str = Field(min_length=1, max_length=80)
    discovered_at: str = ""


class RollWindow(FrozenModel):
    """A period in which providers may disagree about the front month.

    Orders on the product are blocked while it is open. Being unable to copy for
    two days around a roll is an inconvenience; sending an order for a month the
    follower's provider has already retired is a rejection at best and a
    position on the wrong contract at worst.
    """

    root: str
    opens: date
    closes: date
    reason: str = "providers may disagree about the front month during a roll"

    def contains(self, day: date) -> bool:
        return self.opens <= day <= self.closes


MONTH_CODES: dict[int, str] = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
CODE_MONTHS: dict[str, int] = {code: month for month, code in MONTH_CODES.items()}

_SYMBOL = re.compile(r"^([A-Z0-9]{1,6}?)([FGHJKMNQUVXZ])(\d{1,2})$")


def parse_exchange_symbol(symbol: str, *, century: int = 2020) -> Contract:
    """Split `MNQU6` into its product, month and year.

    A one-digit year is ambiguous forever and unambiguous for ten years, which
    is what `century` is for: the caller states the decade rather than this
    function assuming one and being quietly wrong in 2030.
    """
    match = _SYMBOL.fullmatch(symbol.strip().upper())
    if match is None:
        raise MappingError(
            f"'{symbol}' is not a dated exchange symbol like 'NQU6' or 'MNQU26'"
        )
    root, code, digits = match.groups()
    year = int(digits)
    year = 2000 + year if len(digits) == 2 else century + year
    return Contract(
        root=root,
        exchange_symbol=match.group(0),
        expiry_month=CODE_MONTHS[code],
        expiry_year=year,
    )


class MappedQuantity(FrozenModel):
    """The result of a mapping, with its error stated.

    `exposure_error` is the difference between the exposure the policy asked for
    and the exposure the whole number of contracts delivers, as a fraction. A
    follower that ends up 100% oversized because a 0.5 multiplier rounded up
    should say so in the record, not only in a footnote about rounding.
    """

    source_root: str
    target_root: str
    policy: MappingPolicy
    rounding: Rounding
    source_quantity: int
    #: Before rounding.
    exact_quantity: float
    quantity: int
    #: Currency notional the source position carries, when both specs are known.
    source_notional: float | None = None
    target_notional: float | None = None
    exposure_error: float | None = None
    capped_by: str = ""
    detail: str = ""

    @property
    def deliverable(self) -> bool:
        return self.quantity > 0

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class InstrumentCatalogue:
    """The specifications, and the refusal to size from one that is not verified."""

    def __init__(self, instruments: tuple[Instrument, ...] = ()) -> None:
        self._by_root: dict[str, Instrument] = {i.root: i for i in instruments}
        self._symbols: dict[tuple[Provider, str], ProviderSymbol] = {}
        self._rolls: list[RollWindow] = []

    # ── instruments ──────────────────────────────────────────────────────────
    def add(self, instrument: Instrument) -> None:
        self._by_root[instrument.root] = instrument

    def get(self, root: str) -> Instrument | None:
        return self._by_root.get(root.upper())

    def require(self, root: str) -> Instrument:
        found = self.get(root)
        if found is None:
            known = ", ".join(sorted(self._by_root)) or "nothing"
            raise MappingError(f"no instrument '{root}' in the catalogue. Known: {known}.")
        return found

    def all(self) -> tuple[Instrument, ...]:
        return tuple(self._by_root[root] for root in sorted(self._by_root))

    def counterpart(self, root: str) -> Instrument | None:
        """The micro of a standard, or the standard of a micro."""
        instrument = self.require(root)
        if instrument.micro_of:
            return self.get(instrument.micro_of)
        return next(
            (i for i in self._by_root.values() if i.micro_of == instrument.root), None
        )

    # ── provider symbols ─────────────────────────────────────────────────────
    def record_symbol(self, symbol: ProviderSymbol) -> None:
        self._symbols[(symbol.provider, symbol.exchange_symbol)] = symbol

    def provider_symbol(self, provider: Provider, exchange_symbol: str) -> ProviderSymbol | None:
        return self._symbols.get((provider, exchange_symbol.upper()))

    def resolve(self, provider: Provider, exchange_symbol: str) -> str:
        """The provider's own identifier, or a refusal naming what to discover.

        There is no fallback to the exchange symbol. A provider that wants
        `CON.F.US.ENQ.U26` and is handed `NQU6` rejects the order, and a mapper
        that returns the exchange symbol "just in case" has turned a clear
        configuration gap into an order rejection at the worst moment.
        """
        found = self.provider_symbol(provider, exchange_symbol)
        if found is None:
            raise MappingError(
                f"{provider.value} has no discovered identifier for {exchange_symbol}. "
                "Provider symbols are discovered from the provider, not constructed."
            )
        return found.provider_id

    # ── rolls ────────────────────────────────────────────────────────────────
    def add_roll_window(self, window: RollWindow) -> None:
        self._rolls.append(window)

    def roll_block(self, root: str, day: date) -> RollWindow | None:
        return next(
            (w for w in self._rolls if w.root == root.upper() and w.contains(day)), None
        )

    # ── the mapping ──────────────────────────────────────────────────────────
    def map_quantity(
        self,
        *,
        source_root: str,
        target_root: str,
        source_quantity: int,
        policy: MappingPolicy,
        multiplier: float = 1.0,
        rounding: Rounding = Rounding.NEAREST,
        max_contracts: int | None = None,
        risk_budget: float | None = None,
        stop_ticks: float | None = None,
    ) -> MappedQuantity:
        """How many target contracts this policy asks for.

        `multiplier` is the follower's own ratio and applies on top of the
        policy, which is the order the commercial products use and the order an
        operator expects: "half of whatever the leader does, in micros".
        """
        if source_quantity < 0:
            raise MappingError("source quantity cannot be negative")
        if multiplier < 0:
            raise MappingError("a sizing multiplier cannot be negative")
        source = self.require(source_root)
        target = self.require(target_root)

        if policy is MappingPolicy.SAME and source.root != target.root:
            raise MappingError(
                f"the SAME policy cannot map {source.root} to {target.root}; choose "
                "COUNT_CROSS, NOTIONAL_EQUIVALENT or RISK_BUDGET"
            )

        exact, detail = self._exact(
            source=source,
            target=target,
            source_quantity=source_quantity,
            policy=policy,
            multiplier=multiplier,
            risk_budget=risk_budget,
            stop_ticks=stop_ticks,
        )
        quantity = _round(exact, rounding)
        capped_by = ""
        if max_contracts is not None and quantity > max_contracts:
            quantity = max(0, int(max_contracts))
            capped_by = f"capped at {max_contracts} contract(s) by the account's limit"

        source_notional: float | None = None
        target_notional: float | None = None
        error: float | None = None
        if source.verified and target.verified:
            source_notional = source_quantity * source.multiplier
            target_notional = quantity * target.multiplier
            wanted = exact * target.multiplier
            error = 0.0 if wanted == 0 else round((target_notional - wanted) / wanted, 6)

        return MappedQuantity(
            source_root=source.root,
            target_root=target.root,
            policy=policy,
            rounding=rounding,
            source_quantity=source_quantity,
            exact_quantity=round(exact, 6),
            quantity=quantity,
            source_notional=source_notional,
            target_notional=target_notional,
            exposure_error=error,
            capped_by=capped_by,
            detail=detail,
        )

    def _exact(
        self,
        *,
        source: Instrument,
        target: Instrument,
        source_quantity: int,
        policy: MappingPolicy,
        multiplier: float,
        risk_budget: float | None,
        stop_ticks: float | None,
    ) -> tuple[float, str]:
        if policy in {MappingPolicy.SAME, MappingPolicy.COUNT_CROSS}:
            # No specification is read, so an unverified multiplier cannot make
            # this wrong: one contract becomes one contract by definition.
            note = (
                "one contract of the leader becomes one of the follower's product"
                if policy is MappingPolicy.COUNT_CROSS
                else "the same product"
            )
            return source_quantity * multiplier, note

        self._require_verified(source, target, policy)

        if policy is MappingPolicy.NOTIONAL_EQUIVALENT:
            ratio = source.multiplier / target.multiplier
            return (
                source_quantity * ratio * multiplier,
                f"{source.root} carries {ratio:g}x the notional of {target.root}",
            )

        if risk_budget is None or stop_ticks is None or stop_ticks <= 0:
            raise MappingError(
                "the risk-budget policy needs a currency budget and a stop distance in "
                "ticks. Without a stop the worst case is unbounded and no size follows "
                "from it."
            )
        per_contract = stop_ticks * target.tick_value
        if per_contract <= 0:
            raise MappingError("the stop distance prices to zero risk per contract")
        return (
            (risk_budget / per_contract) * multiplier,
            f"{risk_budget:,.2f} budget / {per_contract:,.2f} risk per contract",
        )

    @staticmethod
    def _require_verified(
        source: Instrument, target: Instrument, policy: MappingPolicy
    ) -> None:
        unverified = [i.root for i in (source, target) if not i.verified]
        if unverified:
            raise MappingError(
                f"the {policy.value} policy sizes from contract specifications, and "
                f"{', '.join(unverified)} has not been verified against the exchange. "
                "A multiplier that is wrong by a factor of ten is a position that is "
                "wrong by a factor of ten, so no quantity is returned."
            )

    def same_group(self, first_root: str, second_root: str) -> bool:
        """Whether two products are the same exposure for a hedging rule.

        NQ and MNQ are. So are ES and MES. A firm rule against holding opposite
        directions across accounts applies at this granularity, not at the
        symbol, and getting it wrong is how a copier produces the one thing
        nearly every firm prohibits.
        """
        return self.require(first_root).group == self.require(second_root).group


def _round(value: float, rounding: Rounding) -> int:
    if value <= 0:
        return 0
    if rounding is Rounding.CEIL:
        return math.ceil(value - 1e-9)
    if rounding is Rounding.FLOOR:
        return math.floor(value + 1e-9)
    # `round` is banker's rounding, which would send 0.5 to 0 and 1.5 to 2. A
    # trader reading "nearest" means half goes up.
    return math.floor(value + 0.5)


# ── the shipped catalogue ────────────────────────────────────────────────────
# `verified=True` marks a specification the research confirmed against the
# exchange's own contract specs. The rest are present because an operator
# recognises them and would otherwise add them by hand, and they are marked
# unverified so that nothing sizes a position from them until somebody checks.

CME_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument(
        root="NQ", exchange="CME", description="E-mini Nasdaq-100",
        multiplier=20.0, tick_size=0.25, tick_value=5.0,
        product_group="NASDAQ100", listing_cycle="HMUZ", verified=True,
        source_note="CME contract specifications (dossier 18, HIGH)",
    ),
    Instrument(
        root="MNQ", exchange="CME", description="Micro E-mini Nasdaq-100",
        multiplier=2.0, tick_size=0.25, tick_value=0.5,
        micro_of="NQ", product_group="NASDAQ100", listing_cycle="HMUZ", verified=True,
        source_note="CME contract specifications (dossier 18, HIGH)",
    ),
    Instrument(
        root="ES", exchange="CME", description="E-mini S&P 500",
        multiplier=50.0, tick_size=0.25, tick_value=12.5,
        product_group="SP500", listing_cycle="HMUZ", verified=True,
        source_note="CME contract specifications (dossier 18, HIGH)",
    ),
    Instrument(
        root="MES", exchange="CME", description="Micro E-mini S&P 500",
        multiplier=5.0, tick_size=0.25, tick_value=1.25,
        micro_of="ES", product_group="SP500", listing_cycle="HMUZ", verified=False,
        source_note="industry standard; not individually re-verified (dossier 18, MEDIUM)",
    ),
    Instrument(
        root="GC", exchange="COMEX", description="Gold",
        multiplier=100.0, tick_size=0.1, tick_value=10.0,
        product_group="GOLD", verified=True,
        source_note="CME contract specifications (dossier 18, HIGH)",
    ),
    Instrument(
        root="MGC", exchange="COMEX", description="Micro Gold",
        multiplier=10.0, tick_size=0.1, tick_value=1.0,
        micro_of="GC", product_group="GOLD", verified=True,
        source_note="CME contract specifications (dossier 18, HIGH)",
    ),
    Instrument(
        root="CL", exchange="NYMEX", description="WTI Crude Oil",
        multiplier=1000.0, tick_size=0.01, tick_value=10.0,
        product_group="WTI", verified=True,
        source_note="CME contract specifications (dossier 18, HIGH)",
    ),
    Instrument(
        root="MCL", exchange="NYMEX", description="Micro WTI Crude Oil",
        multiplier=100.0, tick_size=0.01, tick_value=1.0,
        micro_of="CL", product_group="WTI", verified=True,
        source_note="CME contract specifications (dossier 18, HIGH)",
    ),
    Instrument(
        root="YM", exchange="CBOT", description="E-mini Dow",
        multiplier=5.0, tick_size=1.0, tick_value=5.0,
        product_group="DOW", listing_cycle="HMUZ", verified=False,
        source_note="common knowledge; flagged for verification (dossier 18, LOW)",
    ),
    Instrument(
        root="MYM", exchange="CBOT", description="Micro E-mini Dow",
        multiplier=0.5, tick_size=1.0, tick_value=0.5,
        micro_of="YM", product_group="DOW", listing_cycle="HMUZ", verified=False,
        source_note="common knowledge; flagged for verification (dossier 18, LOW)",
    ),
    Instrument(
        root="RTY", exchange="CME", description="E-mini Russell 2000",
        multiplier=50.0, tick_size=0.1, tick_value=5.0,
        product_group="RUSSELL2000", listing_cycle="HMUZ", verified=False,
        source_note="common knowledge; flagged for verification (dossier 18, LOW)",
    ),
    Instrument(
        root="M2K", exchange="CME", description="Micro E-mini Russell 2000",
        multiplier=5.0, tick_size=0.1, tick_value=0.5,
        micro_of="RTY", product_group="RUSSELL2000", listing_cycle="HMUZ", verified=False,
        source_note="common knowledge; flagged for verification (dossier 18, LOW)",
    ),
)


def default_catalogue() -> InstrumentCatalogue:
    return InstrumentCatalogue(CME_INSTRUMENTS)
