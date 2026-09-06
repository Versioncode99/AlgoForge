"""How a secret is located, without ever carrying it across the API.

Lived in ``model_gateway`` until the OmniRoute and NIM providers were removed.
It is provider-agnostic and always was, so it belongs on its own.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialMaterial:
    """A resolved secret and where it came from. Only ``present`` is ever public."""

    value: str
    source: str

    @property
    def present(self) -> bool:
        return bool(self.value)
