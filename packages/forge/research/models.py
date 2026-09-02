from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from forge.contracts.models import FrozenModel
from forge.data.models import Bar

EvidenceTier = Literal[
    "DEVELOPMENT_IN_SAMPLE",
    "VALIDATION_OOS",
    "HOLDOUT",
    "FORWARD",
    "LEGACY_IN_SAMPLE",
    "SYNTHETIC",
]


class PartitionReceipt(FrozenModel):
    name: Literal["DEVELOPMENT", "VALIDATION", "HOLDOUT"]
    evidence_tier: EvidenceTier
    start_index: int
    end_index: int
    bar_count: int
    first_event_time: datetime
    last_event_time: datetime
    data_hash: str


class ResearchSplitReceipt(FrozenModel):
    split_id: str
    source_data_hash: str
    source_bar_count: int
    purge_bars: int
    development_fraction: float
    validation_fraction: float
    development: PartitionReceipt
    validation: PartitionReceipt
    holdout: PartitionReceipt


@dataclass(frozen=True)
class ResearchPartitions:
    receipt: ResearchSplitReceipt
    development: list[Bar]
    validation: list[Bar]
    holdout: list[Bar]
