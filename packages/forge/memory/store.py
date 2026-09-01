from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel

MemoryPartition = Literal["EPISODIC", "SEMANTIC", "PROCEDURAL", "ADVERSARIAL"]


class MemoryEntry(FrozenModel):
    entry_id: str
    partition: MemoryPartition
    subject_id: str
    statement: str
    evidence_ids: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)
    created_at: datetime
    previous_hash: str | None
    entry_hash: str


class DecisionMemory:
    """Append-only, partitioned decision memory with an inspectable hash chain."""

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    def append(
        self,
        partition: MemoryPartition,
        subject_id: str,
        statement: str,
        evidence_ids: tuple[str, ...],
        confidence: float,
        created_at: datetime | None = None,
    ) -> MemoryEntry:
        timestamp = created_at or datetime.now(UTC)
        previous = self._entries[-1].entry_hash if self._entries else None
        payload = {
            "partition": partition,
            "subject_id": subject_id,
            "statement": statement,
            "evidence_ids": evidence_ids,
            "confidence": confidence,
            "created_at": timestamp.astimezone(UTC).isoformat(),
            "previous_hash": previous,
        }
        entry = MemoryEntry(
            entry_id=stable_id("mem", payload),
            partition=partition,
            subject_id=subject_id,
            statement=statement,
            evidence_ids=evidence_ids,
            confidence=confidence,
            created_at=timestamp,
            previous_hash=previous,
            entry_hash=content_hash(payload),
        )
        self._entries.append(entry)
        return entry

    def retrieve(
        self, subject_id: str, allowed_partitions: set[MemoryPartition]
    ) -> tuple[MemoryEntry, ...]:
        return tuple(
            entry
            for entry in self._entries
            if entry.subject_id == subject_id and entry.partition in allowed_partitions
        )

    def verify_chain(self) -> bool:
        previous: str | None = None
        for entry in self._entries:
            if entry.previous_hash != previous:
                return False
            previous = entry.entry_hash
        return True
