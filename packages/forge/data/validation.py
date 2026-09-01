from __future__ import annotations

import math

from forge.contracts.hashing import content_hash
from forge.data.models import Bar, DataQualityReceipt


def validate_bars(bars: list[Bar]) -> DataQualityReceipt:
    findings: list[str] = []
    if not bars:
        return DataQualityReceipt(
            accepted=False, findings=("EMPTY_BATCH",), row_count=0, content_hash=None
        )
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
