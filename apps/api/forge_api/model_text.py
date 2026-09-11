"""Turning a model's raw reply into the answer, for every provider.

Both wired providers serve reasoning models, and both hit the same two traps:
a ``<think>`` block inlined into the content, and a leaked "here's my thinking
process" preamble ahead of the real reply. The rule is identical either side,
so it is stated once. Two copies would drift, and the half that drifted would
quietly start handing the operator a model's private working as the answer.
"""

from __future__ import annotations

import re

_REASONING_MARKERS = (
    "here's a thinking process",
    "here is a thinking process",
    "let me think through",
    "thinking process:",
)


def clean_answer(text: str) -> str:
    """Drop a leaked reasoning preamble and any ``<think>`` block."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    lowered = cleaned.lower()
    if any(lowered.startswith(marker) for marker in _REASONING_MARKERS):
        _, separator, tail = cleaned.rpartition("\n\n")
        if separator and tail.strip():
            return tail.strip()
    return cleaned
