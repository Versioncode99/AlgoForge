"""How a secret is located, without ever carrying it across the API.

Lived in ``model_gateway`` until the OmniRoute and NIM providers were removed.
It is provider-agnostic and always was, so it belongs on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CredentialMaterial:
    """A resolved secret and where it came from. Only ``present`` is ever public."""

    value: str
    source: str

    @property
    def present(self) -> bool:
        return bool(self.value)


def resolve_credential(name: str) -> CredentialMaterial:
    """Environment first, then the repository ``.env``. Never logged or returned.

    This lookup used to be a private helper inside ``opencode``. A second
    provider needs exactly the same order and exactly the same
    ``utf-8-sig``/quote-stripping tolerance — a ``.env`` saved by Notepad carries
    a BOM, and a BOM on the first line makes the key silently unfindable. Two
    copies of that rule would drift, so it lives here with the type it returns.
    """
    direct = os.environ.get(name, "").strip()
    if direct:
        return CredentialMaterial(direct, "environment")

    env_file = Path(__file__).resolve().parents[3] / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                if line.strip().startswith(f"{name}="):
                    value = line.partition("=")[2].strip().strip("\"'")
                    if value:
                        return CredentialMaterial(value, "env_file")
        except (OSError, UnicodeError):
            pass
    return CredentialMaterial("", "none")


#: Which environment variable each data source's credential lives in. Used only
#: to answer "is one configured", never to read one out.
DATA_CREDENTIALS: dict[str, str] = {
    "databento": "DATABENTO_API_KEY",
    "fred": "FRED_API_KEY",
}


def credential_present(provider_id: str) -> bool:
    """Whether a credential exists for this source. Never returns the secret.

    A health surface has to distinguish "the source is down" from "you have not
    given it a key", because the remedies are completely different. Answering
    that question needs one bit, and one bit is all that leaves this function.
    """
    name = DATA_CREDENTIALS.get(provider_id)
    return resolve_credential(name).present if name else False
