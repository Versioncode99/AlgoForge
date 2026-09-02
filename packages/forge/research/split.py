from __future__ import annotations

from forge.contracts.hashing import content_hash, stable_id
from forge.data.models import Bar
from forge.research.models import PartitionReceipt, ResearchPartitions, ResearchSplitReceipt


def source_data_hash(bars: list[Bar]) -> str:
    return content_hash([bar.model_dump(mode="json") for bar in bars])


def _partition(name: str, tier: str, bars: list[Bar], start: int, end: int) -> PartitionReceipt:
    selected = bars[start:end]
    if not selected:
        raise ValueError(f"empty {name.lower()} partition")
    return PartitionReceipt(
        name=name,  # type: ignore[arg-type]
        evidence_tier=tier,  # type: ignore[arg-type]
        start_index=start,
        end_index=end,
        bar_count=len(selected),
        first_event_time=selected[0].event_time,
        last_event_time=selected[-1].event_time,
        data_hash=source_data_hash(selected),
    )


def chronological_split(
    bars: list[Bar],
    *,
    warmup_bars: int,
    development_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> ResearchPartitions:
    """Create chronological development, validation and burn-once holdout partitions.

    Two purge gaps, each at least the strategy warm-up, prevent adjacent feature
    windows from crossing an evidence boundary. The receipt is content-addressed
    and can be reconstructed without relying on filenames or mutable settings.
    """
    if not bars:
        raise ValueError("cannot split an empty dataset")
    if not 0 < development_fraction < 1:
        raise ValueError("development_fraction must be between 0 and 1")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    if development_fraction + validation_fraction >= 1:
        raise ValueError("split fractions leave no holdout")
    if any(bars[i].event_time >= bars[i + 1].event_time for i in range(len(bars) - 1)):
        raise ValueError("bars must be strictly chronological")

    purge = max(1, int(warmup_bars))
    usable = len(bars) - 2 * purge
    minimum_partition = max(purge + 5, 100)
    if usable < minimum_partition * 3:
        required = minimum_partition * 3 + 2 * purge
        raise ValueError(
            f"INSUFFICIENT_SPLIT_BARS: {len(bars)} available, at least {required} required "
            f"for three partitions and two {purge}-bar purge gaps"
        )

    development_count = int(usable * development_fraction)
    validation_count = int(usable * validation_fraction)
    holdout_count = usable - development_count - validation_count
    if min(development_count, validation_count, holdout_count) < minimum_partition:
        raise ValueError("split fractions produce an undersized partition")

    development_start = 0
    development_end = development_count
    validation_start = development_end + purge
    validation_end = validation_start + validation_count
    holdout_start = validation_end + purge
    holdout_end = holdout_start + holdout_count

    source_hash = source_data_hash(bars)
    development = _partition(
        "DEVELOPMENT", "DEVELOPMENT_IN_SAMPLE", bars, development_start, development_end
    )
    validation = _partition("VALIDATION", "VALIDATION_OOS", bars, validation_start, validation_end)
    holdout = _partition("HOLDOUT", "HOLDOUT", bars, holdout_start, holdout_end)
    payload = {
        "source_data_hash": source_hash,
        "source_bar_count": len(bars),
        "purge_bars": purge,
        "development_fraction": development_fraction,
        "validation_fraction": validation_fraction,
        "partitions": [
            development.model_dump(mode="json"),
            validation.model_dump(mode="json"),
            holdout.model_dump(mode="json"),
        ],
    }
    receipt = ResearchSplitReceipt(
        split_id=stable_id("split", payload),
        source_data_hash=source_hash,
        source_bar_count=len(bars),
        purge_bars=purge,
        development_fraction=development_fraction,
        validation_fraction=validation_fraction,
        development=development,
        validation=validation,
        holdout=holdout,
    )
    return ResearchPartitions(
        receipt=receipt,
        development=bars[development_start:development_end],
        validation=bars[validation_start:validation_end],
        holdout=bars[holdout_start:holdout_end],
    )
