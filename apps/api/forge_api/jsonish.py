"""Reading JSON out of a model reply that was asked for JSON.

Models that reason before answering put prose, a fenced block, or both around
the object. Taking the first `{` and the last `}` breaks the moment the preamble
contains a brace — which is exactly what a sentence like "I'll return {steps}"
does, and it was making a capable planner look unreachable.

This scans for balanced objects and returns the first one that parses and looks
like what the caller asked for. It is deliberately forgiving about the wrapper
and strict about the content: a reply that has no valid object raises.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def _candidates(text: str) -> Iterator[str]:
    """Every balanced `{...}` span, longest-first from each opening brace.

    String literals are tracked so a brace inside `"a } b"` does not close the
    span early.
    """
    opens: list[int] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            opens.append(index)
        elif char == "}" and opens:
            start = opens.pop()
            yield text[start : index + 1]


def loads(answer: str, *, require: tuple[str, ...] = ()) -> dict[str, Any]:
    """Parse the model's object.

    `require` names keys that identify the object the caller wants, so a nested
    fragment is not mistaken for the whole reply. An empty tuple accepts the
    first object that parses.
    """
    text = answer.strip()
    if text.startswith("```"):
        # Keep the fenced body; the language tag and closing fence are noise.
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

    best: dict[str, Any] | None = None
    for span in sorted(set(_candidates(text)), key=len, reverse=True):
        try:
            value = json.loads(span)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        if not require or any(key in value for key in require):
            return value
        best = best or value

    if best is not None:
        return best
    raise ValueError("the reply contained no JSON object")
