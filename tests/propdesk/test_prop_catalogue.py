"""The rule-set catalogue: what it reads, what it refuses, and what it expires.

The failure this file exists to prevent is not a crash. It is a rule set that
loads *slightly* wrong — a trailing mode read as static, a consistency rule read
as 40 times its intended size, a limit silently dropped because its name changed —
and then sits behind a drawdown panel that looks authoritative. Every refusal
below is there because the alternative is a number an operator would act on.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from forge.prop.account import TrailMode
from forge.prop.catalogue import (
    SCHEMA_VERSION,
    Catalogue,
    RuleLoadError,
    RuleProvenance,
    Verification,
    load_directory,
    load_file,
    parse,
    review_warnings,
)

REPOSITORY = Path(__file__).resolve().parents[2]

#: The shape the shipped files use, as a starting point each test bends.
BASE = {
    "rule_id": "desk-50k-funded",
    "display_name": "50K Funded",
    "provider": "A prop firm",
    "phase": "FUNDED",
    "starting_balance": 50000,
    "maximum_loss": 2000,
    "trail_mode": "EOD",
    "floor_cap": 50000,
    "minimum_days": 5,
    "consistency_percent": 40,
    "timezone": "America/Chicago",
}


def build(**overrides: object) -> dict[str, object]:
    payload = dict(BASE)
    payload.update(overrides)
    return {key: value for key, value in payload.items() if value is not ...}


# ── the files that ship ──────────────────────────────────────────────────────
def test_every_rule_file_in_the_repository_loads() -> None:
    """The four in `rules/` shipped unreadable for as long as they existed.

    There was no loader, and their field names had drifted from the model they
    were meant to produce. This is the test that would have said so.
    """
    catalogue = load_directory(REPOSITORY / "rules")
    assert catalogue.rejected == (), [row.as_dict() for row in catalogue.rejected]
    assert len(catalogue.rule_sets) == 4


def test_the_shipped_files_are_reported_as_unverified() -> None:
    """They are samples. Loading them must not make them look checked."""
    catalogue = load_directory(REPOSITORY / "rules")
    for row in catalogue.rule_sets:
        assert row.status() is Verification.UNVERIFIED
        assert row.needs_review()


def test_a_shipped_file_keeps_its_trailing_mode() -> None:
    catalogue = load_directory(REPOSITORY / "rules")
    funded = catalogue.get("topstep-50k-funded-sample-v1")
    assert funded is not None
    assert funded.rules.trail_mode is TrailMode.END_OF_DAY
    assert funded.rules.floor_cap == 50000


# ── field names are translated, never guessed ────────────────────────────────
def test_the_files_field_names_reach_the_model() -> None:
    row = parse(build())
    assert row.rules.minimum_trading_days == 5
    assert row.rules.starting_balance == 50000
    assert row.rules.maximum_loss == 2000


def test_a_percentage_is_converted_rather_than_accepted_as_a_fraction() -> None:
    """40 and 0.4 are the same rule written two ways, and only one validates."""
    row = parse(build(consistency_percent=40))
    assert row.rules.consistency_share == pytest.approx(0.4)


def test_a_percentage_that_is_not_a_number_is_refused_with_its_units_named() -> None:
    with pytest.raises(RuleLoadError, match="consistency_percent is not a number"):
        parse(build(consistency_percent="forty"))


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("EOD", TrailMode.END_OF_DAY),
        ("end_of_day", TrailMode.END_OF_DAY),
        ("INTRADAY", TrailMode.INTRADAY),
        ("static", TrailMode.STATIC),
        ("none", TrailMode.STATIC),
    ],
)
def test_every_trailing_spelling_the_files_use_is_read(spelling, expected) -> None:
    assert parse(build(trail_mode=spelling)).rules.trail_mode is expected


def test_an_unknown_trailing_mode_is_refused_rather_than_defaulted() -> None:
    """Defaulting to STATIC would put the loss floor in the wrong place."""
    with pytest.raises(RuleLoadError, match="trail_mode 'RATCHET' is not one this build knows"):
        parse(build(trail_mode="RATCHET"))


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    """A dropped field is a limit the operator believes is being enforced."""
    with pytest.raises(RuleLoadError, match="unknown field"):
        parse(build(weekend_holding_limit=3))


def test_a_zero_target_means_no_target_rather_than_a_validation_error() -> None:
    """`0` is how the files say "there isn't one"; the model wants `None`."""
    row = parse(build(profit_target=0, daily_loss_limit=0))
    assert row.rules.profit_target is None
    assert row.rules.daily_loss_limit is None


def test_payout_terms_are_recorded_and_never_enforced_as_limits() -> None:
    """A payout threshold is not a trading limit, and enforcing it would refuse
    trades no contract refuses."""
    row = parse(build(payout_threshold=3000, payout_amount=1500, timeout_days=120))
    assert "payout_threshold=3000" in row.rules.source_note
    assert row.rules.profit_target is None
    # And nothing became a limit on the model.
    assert row.rules.max_risk_per_trade is None


# ── the schema version is checked ────────────────────────────────────────────
def test_an_unknown_schema_version_is_refused_by_name() -> None:
    with pytest.raises(RuleLoadError, match="schema_version '2' is not one this build reads"):
        parse(build(schema_version="2"))


def test_the_refusal_says_why_reading_it_anyway_would_be_worse() -> None:
    """The message has to carry the reasoning, or somebody will 'fix' it by
    loosening the check."""
    with pytest.raises(RuleLoadError) as caught:
        parse(build(schema_version="2"))
    assert "would load as no limit at all" in str(caught.value)


def test_the_current_version_is_the_default_when_a_file_omits_it() -> None:
    row = parse(build())
    assert row.schema_version == SCHEMA_VERSION


# ── provenance ───────────────────────────────────────────────────────────────
def test_an_unverified_rule_set_needs_no_expiry() -> None:
    provenance = RuleProvenance()
    assert provenance.status() is Verification.UNVERIFIED


def test_a_verification_without_an_expiry_is_refused() -> None:
    """A claim that never lapses is one that decays silently while showing green."""
    with pytest.raises(ValueError, match="must carry review_expires_at"):
        RuleProvenance(verified=True, source_url="https://example.test/terms")


def test_a_verification_must_name_what_it_was_checked_against() -> None:
    with pytest.raises(ValueError, match="must name what it was verified against"):
        RuleProvenance(verified=True, review_expires_at=date(2026, 12, 31))


def test_a_verification_lapses_on_its_own_once_the_window_passes() -> None:
    provenance = RuleProvenance(
        verified=True,
        source_url="https://example.test/terms",
        review_expires_at=date(2026, 6, 30),
    )
    assert provenance.status(date(2026, 6, 30)) is Verification.VERIFIED
    assert provenance.status(date(2026, 7, 1)) is Verification.EXPIRED


def test_an_expired_rule_set_still_loads_but_never_reads_as_verified() -> None:
    """Hiding the account would be its own kind of unhelpful. Calling it checked
    would be a lie. It loads, and it says it needs review."""
    row = parse(
        build(
            verified=True,
            source_url="https://example.test/terms",
            review_expires_at="2026-06-30",
        )
    )
    assert row.status(date(2026, 7, 1)) is Verification.EXPIRED
    assert row.needs_review(date(2026, 7, 1))
    assert not row.needs_review(date(2026, 6, 1))
    assert row.as_dict(date(2026, 7, 1))["status"] == "EXPIRED"


def test_terms_that_have_not_started_are_reported_as_not_yet_effective() -> None:
    row = parse(build(effective_from="2026-09-01"))
    assert row.provenance.effective(date(2026, 8, 31)) is False
    assert row.provenance.effective(date(2026, 9, 1)) is True


def test_a_malformed_date_is_refused_with_the_field_named() -> None:
    with pytest.raises(RuleLoadError, match="review_expires_at is not a date"):
        parse(build(review_expires_at="the end of June"))


# ── the directory ────────────────────────────────────────────────────────────
def test_one_bad_file_does_not_discard_the_rest(tmp_path: Path) -> None:
    (tmp_path / "good.json").write_text(json.dumps(build()))
    (tmp_path / "bad.json").write_text("{not json")
    catalogue = load_directory(tmp_path)
    assert [row.rule_id for row in catalogue.rule_sets] == ["desk-50k-funded"]
    assert len(catalogue.rejected) == 1
    assert catalogue.rejected[0].origin == "bad.json"


def test_a_rejected_file_says_which_and_why(tmp_path: Path) -> None:
    """A shorter list with no explanation reads as though the file was never there."""
    (tmp_path / "broken.json").write_text(json.dumps(build(trail_mode="RATCHET")))
    catalogue = load_directory(tmp_path)
    assert catalogue.rule_sets == ()
    assert "RATCHET" in catalogue.rejected[0].reason


def test_two_files_under_one_rule_id_are_refused_rather_than_ordered(tmp_path: Path) -> None:
    """Otherwise which contract an account is held to depends on file order."""
    (tmp_path / "a.json").write_text(json.dumps(build()))
    (tmp_path / "b.json").write_text(json.dumps(build(display_name="Another 50K")))
    catalogue = load_directory(tmp_path)
    assert len(catalogue.rule_sets) == 1
    assert len(catalogue.rejected) == 1
    assert "already defined by a.json" in catalogue.rejected[0].reason


def test_a_missing_directory_is_an_empty_catalogue_not_an_error(tmp_path: Path) -> None:
    assert load_directory(tmp_path / "nothing") == Catalogue()


def test_a_file_without_a_rule_id_is_refused(tmp_path: Path) -> None:
    payload = build()
    payload.pop("rule_id")
    (tmp_path / "anonymous.json").write_text(json.dumps(payload))
    catalogue = load_directory(tmp_path)
    assert "must carry a rule_id" in catalogue.rejected[0].reason


def test_the_catalogue_counts_what_needs_review(tmp_path: Path) -> None:
    """An interface must not draw "2 rule sets" while both are unchecked."""
    (tmp_path / "a.json").write_text(json.dumps(build()))
    (tmp_path / "b.json").write_text(
        json.dumps(
            build(
                rule_id="desk-50k-challenge",
                verified=True,
                source_url="https://example.test/terms",
                review_expires_at="2030-01-01",
            )
        )
    )
    counts = load_directory(tmp_path).as_dict(date(2026, 1, 1))["counts"]
    assert counts == {"loaded": 2, "rejected": 0, "needing_review": 1}


def test_yaml_and_json_both_load(tmp_path: Path) -> None:
    (tmp_path / "one.json").write_text(json.dumps(build()))
    (tmp_path / "two.yaml").write_text(
        "\n".join(f"{key}: {value!r}" for key, value in build(rule_id="desk-2").items())
    )
    catalogue = load_directory(tmp_path)
    assert {row.rule_id for row in catalogue.rule_sets} == {"desk-50k-funded", "desk-2"}


def test_a_file_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(RuleLoadError, match="must contain one mapping"):
        load_file(path)


# ── review warnings ──────────────────────────────────────────────────────────
def test_expired_is_reported_before_unverified() -> None:
    """A lapsed review is the more surprising of the two: it looked checked."""
    unverified = parse(build(rule_id="never-checked", display_name="Never checked"))
    expired = parse(
        build(
            rule_id="lapsed",
            display_name="Lapsed",
            verified=True,
            source_url="https://example.test/terms",
            review_expires_at="2026-01-01",
        )
    )
    warnings = review_warnings([unverified, expired], date(2026, 7, 1))
    assert len(warnings) == 2
    assert "Lapsed" in warnings[0]
    assert "lapsed on 2026-01-01" in warnings[0]
    assert "Never checked" in warnings[1]


def test_a_verified_rule_set_inside_its_window_produces_no_warning() -> None:
    row = parse(
        build(
            verified=True,
            source_url="https://example.test/terms",
            review_expires_at="2030-01-01",
        )
    )
    assert review_warnings([row], date(2026, 7, 1)) == ()


# ── the generic boundary ─────────────────────────────────────────────────────
def test_no_firm_name_changes_any_behaviour() -> None:
    """`provider` is the operator's label. Nothing branches on it, and the
    catalogue must produce the same rule set without it."""
    named = parse(build(provider="Topstep"))
    anonymous = parse(build(provider=""))
    assert named.rules.model_dump(exclude={"provider"}) == anonymous.rules.model_dump(
        exclude={"provider"}
    )
    assert named.rules.rules_id != anonymous.rules.rules_id  # the label is in the hash
    assert named.status() is anonymous.status()


def test_the_module_names_no_prop_firm() -> None:
    """A loader that special-cased a firm would be a loader that stops working
    when that firm changes its terms."""
    source = (
        REPOSITORY / "packages" / "forge" / "prop" / "catalogue.py"
    ).read_text(encoding="utf-8")
    for firm in ("topstep", "apex", "lucid", "ftmo", "tradeify", "myfundedfutures"):
        assert firm not in source.lower(), f"catalogue.py names {firm}"
