from __future__ import annotations

import math

import pytest
from forge.contracts.hashing import canonical_json, content_hash


def test_hash_is_stable_across_mapping_order() -> None:
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})
    assert canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_non_finite_values_fail_closed() -> None:
    with pytest.raises(ValueError):
        content_hash({"invalid": math.nan})
