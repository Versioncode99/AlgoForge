"""Rule sets read from disk, with where each one came from and when it expires.

`rules/` has shipped four rule files since the Prop Desk was written. Nothing
read them. `forge.prop.account` said a rule set "can be written by hand, loaded
from `rules/`, or built in the interface", and two of those three were true: no
loader existed, and the files' field names had drifted from the model they were
supposed to produce — `minimum_days` against `minimum_trading_days`,
`consistency_percent` against `consistency_share`, `trail_mode: EOD` against
`end_of_day`. A file that cannot be loaded is not configuration; it is a
document that looks like configuration.

This is the loader, and it carries three things the model deliberately does not.

**A schema version, checked rather than assumed.** A file whose
`schema_version` this build does not know is *refused by name*, not read
best-effort. The failure mode being avoided is specific: a later schema that
renames `maximum_loss` would otherwise load as a rule set with no loss limit,
and a drawdown panel computed against it would show an account with room it does
not have.

**Provenance, separately from the rule.** `AccountRules` is what the contract
says; `RuleProvenance` is who says so and when they last checked. Keeping them
apart is what lets the desk distinguish "this account has a $2,000 trailing
drawdown" from "somebody typed $2,000 in March and nobody has looked since",
which are different facts about the same number.

**A review that expires.** A rule set carries `review_expires_at`, and once that
date passes its verification lapses to `EXPIRED` on its own. Prop firms change
their contracts and do not send anybody a diff, so a verification with no expiry
is a claim that gets less true every month while continuing to look green. An
expired rule set is still *usable* — refusing to show an account because a review
lapsed would be its own kind of unhelpful — but it is never reported as verified,
and `needs_review` says so everywhere the desk shows it.

**It names no firm.** The sample files mention providers because operators
recognise them, and `provider` is free text the operator wrote. Nothing in this
module branches on it, and `tests/propdesk/test_prop_catalogue.py` asserts that
removing every provider string changes no behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, model_validator

from forge.contracts.models import FrozenModel
from forge.prop.account import AccountRules, TrailMode

#: The only schema this build reads. Bump it when a field changes meaning, not
#: when one is added: a reader that ignores an unknown *extra* field is fine,
#: and one that misreads a renamed field is not.
SCHEMA_VERSION = "1"

#: Where the shipped rule files live, relative to the repository root.
RULES_DIRECTORY = Path("rules")


class RuleLoadError(Exception):
    """A rule file could not be read, and the message says which and why."""


class Verification(StrEnum):
    """What is known about whether a rule set matches a real contract.

    Three states rather than a boolean, because "nobody has checked" and "it was
    checked and the check has lapsed" are different things to tell an operator.
    Neither is `VERIFIED`, and neither is a reason to hide the account.
    """

    #: Entered by somebody and never checked against the contract it claims.
    UNVERIFIED = "UNVERIFIED"
    #: Checked against a named source, inside its review window.
    VERIFIED = "VERIFIED"
    #: Was verified; the review window has passed and nobody has looked again.
    EXPIRED = "EXPIRED"


class RuleProvenance(FrozenModel):
    """Where a rule set came from, and how long that claim is good for.

    Every field defaults to empty or `None`. A provenance record that invented a
    plausible URL would be worse than one that says nothing, because the empty
    one is obviously a gap and the plausible one looks like evidence.
    """

    #: Where the operator read the contract. Free text; never fetched.
    source_url: str = ""
    #: A digest of whatever the operator captured, so a later capture can be
    #: compared. Opaque here: this module never computes or verifies it, it only
    #: carries it, and `"unverified-sample"` in the shipped files says exactly
    #: that.
    source_hash: str = ""
    #: Whether a person checked this against the source. Claimed by whoever
    #: entered it — this module cannot check a contract.
    verified: bool = False
    #: Who checked. Empty when nobody did, and empty is the honest default.
    reviewed_by: str = ""
    #: When these terms start applying. A rule set that is not yet effective is
    #: still loaded; the desk decides what to do about it.
    effective_from: date | None = None
    #: When the verification lapses. `None` on an unverified rule set is
    #: coherent — there is nothing to expire. `None` on a *verified* one is
    #: refused: a verification that never expires is the claim this module
    #: exists to stop.
    review_expires_at: date | None = None

    @model_validator(mode="after")
    def _a_verification_expires(self) -> RuleProvenance:
        if self.verified and self.review_expires_at is None:
            raise ValueError(
                "a verified rule set must carry review_expires_at. Prop firms change "
                "their contracts without telling anybody, so a verification with no "
                "expiry is a claim that decays silently while still showing green."
            )
        if self.verified and not self.source_url and not self.source_hash:
            raise ValueError(
                "a verified rule set must name what it was verified against: set "
                "source_url, source_hash, or both."
            )
        return self

    def status(self, today: date | None = None) -> Verification:
        """The verification state as of `today`, expiry applied."""
        if not self.verified:
            return Verification.UNVERIFIED
        when = today or datetime.now(UTC).date()
        assert self.review_expires_at is not None  # the validator guarantees it
        return Verification.EXPIRED if when > self.review_expires_at else Verification.VERIFIED

    def effective(self, today: date | None = None) -> bool:
        """Whether these terms have started applying."""
        if self.effective_from is None:
            return True
        return (today or datetime.now(UTC).date()) >= self.effective_from

    def as_dict(self, today: date | None = None) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["status"] = self.status(today).value
        payload["effective"] = self.effective(today)
        return payload


class RuleSet(FrozenModel):
    """One loadable rule set: the contract, and the claim about the contract."""

    schema_version: str = SCHEMA_VERSION
    rule_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    rules: AccountRules
    provenance: RuleProvenance = RuleProvenance()
    #: The file this came from, for an operator who has to go and fix one.
    origin: str = ""

    def status(self, today: date | None = None) -> Verification:
        return self.provenance.status(today)

    def needs_review(self, today: date | None = None) -> bool:
        """Whether an operator should look at this before trusting it.

        True for unverified *and* expired, which is the conservative reading and
        the one the desk wants: both mean nobody can currently say these numbers
        match a contract.
        """
        return self.status(today) is not Verification.VERIFIED

    def as_dict(self, today: date | None = None) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rule_id": self.rule_id,
            "display_name": self.display_name,
            "origin": self.origin,
            "rules": self.rules.model_dump(mode="json"),
            "rules_id": self.rules.rules_id,
            "provenance": self.provenance.as_dict(today),
            "status": self.status(today).value,
            "needs_review": self.needs_review(today),
        }


class Rejected(FrozenModel):
    """A file that could not be loaded, and why.

    Carried rather than logged and dropped. An operator whose catalogue is
    missing one of five rule sets needs to know which and what is wrong with it;
    a shorter list with no explanation reads as though the file was never there.
    """

    origin: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Catalogue(FrozenModel):
    """Everything in `rules/`, loaded and refused."""

    rule_sets: tuple[RuleSet, ...] = ()
    rejected: tuple[Rejected, ...] = ()

    def get(self, rule_id: str) -> RuleSet | None:
        return next((row for row in self.rule_sets if row.rule_id == rule_id), None)

    def needing_review(self, today: date | None = None) -> tuple[RuleSet, ...]:
        return tuple(row for row in self.rule_sets if row.needs_review(today))

    def as_dict(self, today: date | None = None) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "rule_sets": [row.as_dict(today) for row in self.rule_sets],
            "rejected": [row.as_dict() for row in self.rejected],
            # Counted here so an interface cannot draw "4 rule sets" while two of
            # them are unreviewed and one failed to load.
            "counts": {
                "loaded": len(self.rule_sets),
                "rejected": len(self.rejected),
                "needing_review": len(self.needing_review(today)),
            },
        }


#: Field names in the shipped files, mapped to the model's.
#:
#: Written out rather than inferred. A loader that guessed — stripping
#: underscores, matching prefixes — would silently accept a future file whose
#: `max_loss` meant something else, and the whole point of this table is that a
#: name it does not know is refused rather than approximated.
_FIELD_NAMES: Mapping[str, str] = {
    "minimum_days": "minimum_trading_days",
    "max_position": "max_position_contracts",
    "max_order": "max_order_contracts",
}

#: `trail_mode` spellings the files use, mapped to the enum.
_TRAIL_MODES: Mapping[str, TrailMode] = {
    "EOD": TrailMode.END_OF_DAY,
    "END_OF_DAY": TrailMode.END_OF_DAY,
    "INTRADAY": TrailMode.INTRADAY,
    "STATIC": TrailMode.STATIC,
    "NONE": TrailMode.STATIC,
}

#: Keys a rule file may carry that describe the *payout*, not the contract's
#: limits. They are read into provenance-adjacent metadata rather than silently
#: discarded, because an operator who wrote them expects them to mean something.
_PAYOUT_KEYS = ("payout_threshold", "payout_amount", "timeout_days")

#: Keys that belong to provenance rather than to the rules.
_PROVENANCE_KEYS = (
    "source_url",
    "source_hash",
    "verified",
    "reviewed_by",
    "effective_from",
    "review_expires_at",
)


def _as_date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RuleLoadError(f"{field} is not a date: {value!r}") from exc


def parse(payload: Mapping[str, Any], *, origin: str = "") -> RuleSet:
    """One rule set from one mapping, or a `RuleLoadError` naming the problem."""
    if not isinstance(payload, Mapping):  # pragma: no cover - defensive
        raise RuleLoadError("a rule file must be a mapping")

    version = str(payload.get("schema_version", SCHEMA_VERSION))
    if version != SCHEMA_VERSION:
        raise RuleLoadError(
            f"schema_version {version!r} is not one this build reads (it reads "
            f"{SCHEMA_VERSION!r}). Refusing rather than reading it best-effort: a "
            "renamed limit would load as no limit at all."
        )

    data = {key: value for key, value in payload.items() if key != "schema_version"}

    rule_id = str(data.pop("rule_id", "") or "").strip()
    if not rule_id:
        raise RuleLoadError("a rule file must carry a rule_id")
    display_name = str(data.pop("display_name", "") or "").strip() or rule_id

    provenance_data: dict[str, Any] = {}
    for key in _PROVENANCE_KEYS:
        if key in data:
            provenance_data[key] = data.pop(key)
    for key in ("effective_from", "review_expires_at"):
        if key in provenance_data:
            provenance_data[key] = _as_date(provenance_data[key], key)

    payouts = {key: data.pop(key) for key in _PAYOUT_KEYS if key in data}

    # `consistency_percent` is 0-100 and the model wants 0-1. Converted here
    # rather than accepted as-is: 40 would validate as a fraction nowhere near
    # what the file meant, and `le=1` would reject it with a message about
    # bounds rather than about units.
    if "consistency_percent" in data:
        percent = data.pop("consistency_percent")
        if percent not in (None, ""):
            try:
                data["consistency_share"] = float(percent) / 100.0
            except (TypeError, ValueError) as exc:
                raise RuleLoadError(f"consistency_percent is not a number: {percent!r}") from exc

    if "trail_mode" in data and data["trail_mode"] is not None:
        spelling = str(data["trail_mode"]).strip().upper()
        if spelling not in _TRAIL_MODES:
            raise RuleLoadError(
                f"trail_mode {data['trail_mode']!r} is not one this build knows "
                f"({', '.join(sorted(_TRAIL_MODES))}). A trailing mode read wrongly "
                "moves the loss floor to the wrong place."
            )
        data["trail_mode"] = _TRAIL_MODES[spelling].value

    for old, new in _FIELD_NAMES.items():
        if old in data:
            data[new] = data.pop(old)

    # `0` means "no target" in the files and `gt=0` in the model. An explicit
    # zero is the operator saying there is none, so it becomes `None` rather
    # than failing validation on a number they meant.
    for key in ("profit_target", "daily_loss_limit", "max_risk_per_trade"):
        if data.get(key) in (0, 0.0):
            data[key] = None

    data.setdefault("name", display_name)

    known = set(AccountRules.model_fields)
    unknown = sorted(set(data) - known)
    if unknown:
        raise RuleLoadError(
            f"unknown field(s) {', '.join(unknown)}. Refusing rather than ignoring "
            "them: a field this build drops is a limit the operator believes is "
            "being enforced."
        )

    try:
        rules = AccountRules(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "the rule set"
        raise RuleLoadError(f"{where}: {first.get('msg', 'is not valid')}") from exc

    try:
        provenance = RuleProvenance(**provenance_data)
    except ValidationError as exc:
        raise RuleLoadError(str(exc.errors()[0].get("msg", "provenance is not valid"))) from exc

    if payouts:
        # Recorded on the rule set's own note rather than invented as limits.
        # Payout terms are not trading limits and enforcing them as though they
        # were would refuse trades no contract refuses.
        detail = ", ".join(f"{key}={value}" for key, value in sorted(payouts.items()))
        note = f"{rules.source_note} ({detail})".strip() if rules.source_note else detail
        rules = rules.model_copy(update={"source_note": note})

    return RuleSet(
        rule_id=rule_id,
        display_name=display_name,
        rules=rules,
        provenance=provenance,
        origin=origin,
    )


def load_file(path: Path) -> RuleSet:
    """One rule set from one file. YAML or JSON, by extension."""
    import json

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - PyYAML is a dependency
            raise RuleLoadError(
                "PyYAML is not installed, so YAML rule files cannot be read. "
                "Convert the file to JSON or install it."
            ) from exc
        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RuleLoadError(f"not valid YAML: {exc}") from exc
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuleLoadError(f"not valid JSON: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise RuleLoadError("a rule file must contain one mapping")
    return parse(payload, origin=path.name)


def load_directory(directory: Path | None = None) -> Catalogue:
    """Every rule file in a directory, with the ones that failed carried along.

    One bad file does not discard the rest, and it does not vanish either: it
    lands in `rejected` with the reason, so a catalogue that is short by one says
    which one and what is wrong with it.
    """
    root = directory or RULES_DIRECTORY
    if not root.is_dir():
        return Catalogue()

    loaded: list[RuleSet] = []
    rejected: list[Rejected] = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        try:
            loaded.append(load_file(path))
        except (RuleLoadError, OSError) as exc:
            rejected.append(Rejected(origin=path.name, reason=str(exc)))

    seen: dict[str, str] = {}
    unique: list[RuleSet] = []
    for row in loaded:
        if row.rule_id in seen:
            rejected.append(
                Rejected(
                    origin=row.origin,
                    reason=(
                        f"rule_id {row.rule_id!r} is already defined by {seen[row.rule_id]}. "
                        "Two rule sets under one id would make which contract an account "
                        "is held to depend on file order."
                    ),
                )
            )
            continue
        seen[row.rule_id] = row.origin
        unique.append(row)

    return Catalogue(rule_sets=tuple(unique), rejected=tuple(rejected))


def review_warnings(
    rule_sets: Iterable[RuleSet], today: date | None = None
) -> tuple[str, ...]:
    """One sentence per rule set an operator should look at, worst first.

    Expired before unverified: a lapsed review is the more surprising of the two,
    because it looked checked the last time anybody opened the screen.
    """
    when = today or datetime.now(UTC).date()
    expired: list[str] = []
    unverified: list[str] = []
    for row in rule_sets:
        status = row.status(when)
        if status is Verification.EXPIRED:
            expired.append(
                f"{row.display_name}: verified against {row.provenance.source_url or 'a source'} "
                f"but the review lapsed on {row.provenance.review_expires_at}. "
                "Check the contract again before trading to these numbers."
            )
        elif status is Verification.UNVERIFIED:
            unverified.append(
                f"{row.display_name}: nobody has checked these numbers against a contract."
            )
    return tuple(expired + unverified)


__all__ = [
    "RULES_DIRECTORY",
    "SCHEMA_VERSION",
    "Catalogue",
    "Rejected",
    "RuleLoadError",
    "RuleProvenance",
    "RuleSet",
    "Verification",
    "load_directory",
    "load_file",
    "parse",
    "review_warnings",
]
