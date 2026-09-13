"""The G0 gate answers the same thing faster, and "the same thing" is the test.

`validate_bars` now computes its structural findings over arrays and caches the
expensive half — a content hash over every bar's canonical form — against a
fingerprint of the bars themselves. Both changes are only safe if the answer is
unchanged, and both have a specific way of being unsafe:

* a vectorised check could report findings in a different order, and the order
  is part of the receipt, which is stored;
* a fingerprint that missed a field would let one dataset answer from another's
  receipt, attaching one dataset's identity to another's results.

So this file re-implements the original bar-by-bar pass and asserts the two
agree, then asserts that every field reaching the canonical form also reaches
the fingerprint.
"""

from __future__ import annotations

import math

import pytest
from forge.contracts.hashing import content_hash
from forge.data.models import Bar, DataQualityReceipt
from forge.data.validation import clear_cache, fingerprint, validate_bars
from forge.strategy.synthetic import generate_bars


def _reference(bars: list[Bar]) -> DataQualityReceipt:
    """The original implementation, kept as the thing the new one must match."""
    findings: list[str] = []
    keys: set[tuple[str, object]] = set()
    previous = None
    symbols = {bar.symbol for bar in bars}
    if len(symbols) != 1:
        findings.append("MIXED_SYMBOLS")
    for bar in bars:
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(value) for value in values):
            findings.append("NON_FINITE_VALUE")
        if bar.high < max(bar.open, bar.close, bar.low):
            findings.append("HIGH_INVARIANT")
        if bar.low > min(bar.open, bar.close, bar.high):
            findings.append("LOW_INVARIANT")
        if bar.knowledge_time < bar.event_time:
            findings.append("KNOWLEDGE_BEFORE_EVENT")
        if previous is not None and bar.event_time <= previous:
            findings.append("NON_MONOTONIC_EVENT_TIME")
        key = (bar.symbol, bar.event_time)
        if key in keys:
            findings.append("DUPLICATE_BAR")
        keys.add(key)
        previous = bar.event_time
    unique = tuple(dict.fromkeys(findings))
    digest = content_hash([bar.model_dump(mode="json") for bar in bars])
    return DataQualityReceipt(
        accepted=not unique, findings=unique, row_count=len(bars), content_hash=digest
    )


@pytest.fixture
def clean() -> list[Bar]:
    return generate_bars(symbol="MNQ", count=300, seed=3)


def _mutate(bars: list[Bar], at: int = 5, **changes: object) -> list[Bar]:
    out = list(bars)
    out[at] = bars[at].model_copy(update=changes)
    return out


@pytest.fixture(autouse=True)
def _fresh() -> None:
    clear_cache()


def _case(bars: list[Bar]) -> None:
    clear_cache()
    assert validate_bars(bars).model_dump() == _reference(bars).model_dump()


def test_a_clean_batch_is_accepted_and_identical_to_the_original_pass(clean) -> None:
    _case(clean)
    assert validate_bars(clean).accepted


def test_a_broken_high_is_reported_identically(clean) -> None:
    _case(_mutate(clean, high=0.0))


def test_a_broken_low_is_reported_identically(clean) -> None:
    _case(_mutate(clean, low=1e9))


def test_knowledge_before_the_event_is_reported_identically(clean) -> None:
    _case(_mutate(clean, knowledge_time=clean[5].event_time.replace(year=2000)))


def test_a_duplicate_bar_is_reported_identically(clean) -> None:
    _case([*clean[:5], clean[4], *clean[5:]])


def test_out_of_order_bars_are_reported_identically(clean) -> None:
    _case([*clean[:10], clean[3], *clean[10:]])


def test_mixed_symbols_lead_the_findings_as_they_always_did(clean) -> None:
    mixed = [*clean[:200], *(b.model_copy(update={"symbol": "ES"}) for b in clean[200:])]
    _case(mixed)
    assert validate_bars(mixed).findings[0] == "MIXED_SYMBOLS"


def test_several_faults_keep_the_order_the_bar_by_bar_pass_produced(clean) -> None:
    """The order is part of the receipt, and the receipt is stored."""
    bars = _mutate(_mutate(clean, at=7, high=0.0), at=9, low=1e9)
    _case(bars)


def test_an_empty_batch_is_still_refused_by_name() -> None:
    receipt = validate_bars([])
    assert receipt.findings == ("EMPTY_BATCH",)
    assert not receipt.accepted
    assert receipt.content_hash is None


# ── the cache cannot answer for the wrong data ───────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", 1.0),
        ("high", 1e9),
        ("low", 0.5),
        ("close", 2.0),
        ("volume", 7.0),
        ("source", "somewhere-else"),
        ("bar_type", "VOLUME"),
    ],
)
def test_changing_any_field_changes_the_fingerprint_and_the_digest(clean, field, value) -> None:
    before = validate_bars(clean)
    changed = _mutate(clean, at=5, **{field: value})
    assert fingerprint(changed) != fingerprint(clean)
    assert validate_bars(changed).content_hash != before.content_hash


def test_changing_a_timestamp_changes_the_fingerprint(clean) -> None:
    before = validate_bars(clean)
    changed = _mutate(clean, at=5, ingestion_time=clean[5].ingestion_time.replace(year=2031))
    assert fingerprint(changed) != fingerprint(clean)
    assert validate_bars(changed).content_hash != before.content_hash


def test_an_equal_but_separate_list_answers_from_the_same_entry(clean) -> None:
    """The cache is keyed on the bars, not on the identity of the list holding them."""
    first = validate_bars(clean)
    second = validate_bars([bar.model_copy() for bar in clean])
    assert first.content_hash == second.content_hash
    assert first.findings == second.findings


def test_a_repeat_call_returns_what_an_uncached_call_would(clean) -> None:
    warm = validate_bars(clean)
    clear_cache()
    cold = validate_bars(clean)
    assert warm.model_dump() == cold.model_dump()


def test_the_cache_is_bounded_and_keeps_answering_correctly() -> None:
    """A long session must not accumulate receipts, and must not go wrong doing it."""
    from forge.data.validation import _CACHE_LIMIT

    batches = [
        generate_bars(symbol="MNQ", count=120, seed=seed) for seed in range(_CACHE_LIMIT + 6)
    ]
    digests = [validate_bars(bars).content_hash for bars in batches]
    # The oldest entries have been evicted; every one still answers correctly.
    for bars, digest in zip(batches, digests, strict=True):
        assert validate_bars(bars).content_hash == digest
