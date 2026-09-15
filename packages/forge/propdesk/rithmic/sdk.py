"""Where the Rithmic SDK is, what version it is, and why none of it is here.

**Nothing from the SDK is committed, and this module is the reason it does not
have to be.** Both archives state that the software is protected by copyright,
that unauthorised reproduction or distribution "(including its documentation),
or any portion of it" may be prosecuted, and that it is licensed under a
separate agreement carrying restrictions on use, reverse engineering,
disclosure and confidentiality. So AlgoForge redistributes no `.proto` file, no
generated `*_pb2.py` — which is a derivative of one — no `rapiplus.dll`, and no
documentation text, including into comments.

What it does instead is *locate* the operator's own licensed copy and describe
it. Version strings, file names, counts and hashes are facts about an artefact
rather than a portion of it, and they are what makes "the adapter has been
reconciled against this version" a checkable statement.

**Absent, everything refuses.** There is no partial mode, no simulation and no
fallback: `SdkMissing` names what is missing and where to put it. An adapter
that half-worked without the SDK would be the specific lie
`forge.propdesk.adapters.declared` is written against — an interface that offers
a connection, takes a password, and fails in a way that looks like the
operator's mistake.

**The reference guide is not the source.** `Reference_Guide.pdf` inside the
0.89.0.0 archive carries the PDF title of the 0.48.0.0 guide, forty-one
releases earlier, and its template table predates changes that alter decoding —
`data_bar_seq_num` became a repeated field, and `entitlement_flag` was
deprecated in favour of two new fields. The `.proto` files and `change_log` are
authoritative, which is why the vocabulary is read from them rather than
transcribed. See `docs/ADR-0001-rithmic-transport.md`.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The environment variable an operator points at their unpacked SDK.
SDK_ENV = "ALGOFORGE_RITHMIC_SDK"

#: Where the workspace looks when the variable is unset. Ignored by git — see
#: the entry in `.gitignore` — so a copy placed here cannot be committed.
VENDOR_DIRECTORY = Path("vendor") / "rithmic"

#: The template version this adapter's normalisation has been reconciled
#: against, from `proto/change_log`. A different version is reported rather than
#: assumed compatible: 0.89.0.0 alone changed a scalar field into a repeated one
#: and deprecated an entitlement flag, either of which decodes wrongly against
#: an older reading.
RECONCILED_TEMPLATE_VERSION = "5.42"

#: Matches the template version line in `proto/change_log`, which is the file
#: that states it. Deliberately not the PDF.
_TEMPLATE_VERSION = re.compile(r"template\s+version\s+([0-9]+\.[0-9]+)", re.IGNORECASE)

#: Matches the archive version in a directory name such as `0.89.0.0`.
_ARCHIVE_VERSION = re.compile(r"^(\d+\.\d+\.\d+\.\d+)$")


class SdkMissing(RuntimeError):
    """The SDK is not where it was looked for, with where to put it."""


@dataclass(frozen=True)
class SdkFile:
    """One file of the operator's copy, described rather than reproduced."""

    name: str
    bytes: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "bytes": self.bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class CompatibilityManifest:
    """What was found, and whether this adapter has been reconciled against it.

    Recorded with every connection and every capability claim, so "verified
    against Rithmic Test" is a statement about a *version* rather than about
    Rithmic in general.
    """

    root: str
    archive_version: str
    template_version: str
    reconciled_template_version: str
    proto_count: int
    #: Name, size and digest per `.proto`. No content, and no field numbers.
    files: tuple[SdkFile, ...]
    #: A digest over the file digests: one value that changes when any does.
    fingerprint: str

    @property
    def reconciled(self) -> bool:
        return self.template_version == self.reconciled_template_version

    @property
    def note(self) -> str:
        if self.reconciled:
            return (
                f"Template version {self.template_version}, which this adapter has been "
                "reconciled against."
            )
        return (
            f"Template version {self.template_version}; this adapter was reconciled "
            f"against {self.reconciled_template_version}. Message shapes may have "
            "changed — 0.89.0.0 alone made a scalar field repeated and deprecated an "
            "entitlement flag. Treat every capability as unverified until reconciled."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "archive_version": self.archive_version,
            "template_version": self.template_version,
            "reconciled_template_version": self.reconciled_template_version,
            "reconciled": self.reconciled,
            "proto_count": self.proto_count,
            "fingerprint": self.fingerprint,
            "note": self.note,
            # Names and digests only. The manifest describes the artefact; it is
            # not a copy of any part of it.
            "files": [f.as_dict() for f in self.files],
        }


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()


@dataclass(frozen=True)
class RithmicSdk:
    """The operator's unpacked R | Protocol archive."""

    root: Path

    @property
    def proto_directory(self) -> Path:
        return self.root / "proto"

    @property
    def certificate(self) -> Path:
        """The TLS CA parameter file the archive ships.

        Used as the trust anchor for the WebSocket connection. Verification is
        never disabled: a connector that fell back to an unverified context on a
        certificate problem would turn a trust failure into a silent one.
        """
        return self.root / "etc" / "rithmic_ssl_cert_auth_params"

    # ── discovery ────────────────────────────────────────────────────────────
    @classmethod
    def discover(cls, workspace: Path | None = None) -> RithmicSdk:
        """Find the SDK, or say exactly where it was looked for.

        Two places, in order: the environment variable, then the workspace's
        vendor directory. An operator who unpacked the archive one level higher
        or lower gets the directory they pointed at named back, which is the
        difference between a fixable message and "not found".
        """
        candidates: list[Path] = []
        configured = os.environ.get(SDK_ENV, "").strip()
        if configured:
            candidates.append(Path(configured))
        if workspace is not None:
            candidates.append(workspace / VENDOR_DIRECTORY)

        tried: list[str] = []
        for candidate in candidates:
            tried.append(str(candidate))
            found = cls._resolve(candidate)
            if found is not None:
                return found

        where = "; ".join(tried) if tried else "nowhere — no location is configured"
        raise SdkMissing(
            "No Rithmic R | Protocol SDK was found. AlgoForge does not ship one: the "
            "archive is licensed to you, not redistributable, and nothing here "
            "reproduces any part of it. Unpack RProtocolAPI (the directory containing "
            f"`proto/` and `etc/`) and set {SDK_ENV} to it, or place it at "
            f"`<workspace>/{VENDOR_DIRECTORY.as_posix()}`. Looked in: {where}."
        )

    @classmethod
    def _resolve(cls, candidate: Path) -> RithmicSdk | None:
        """Accept the archive root, or a directory holding one version of it."""
        if (candidate / "proto").is_dir():
            return cls(root=candidate)
        if candidate.is_dir():
            # `.../RProtocolAPI/0.89.0.0/proto`. Newest first, so an operator who
            # unpacked two versions side by side gets the later one.
            versions = sorted(
                (child for child in candidate.iterdir() if (child / "proto").is_dir()),
                key=lambda child: child.name,
                reverse=True,
            )
            if versions:
                return cls(root=versions[0])
        return None

    @classmethod
    def available(cls, workspace: Path | None = None) -> bool:
        try:
            cls.discover(workspace)
        except SdkMissing:
            return False
        return True

    # ── description ──────────────────────────────────────────────────────────
    def archive_version(self) -> str:
        """The archive version, from the directory name. Empty when unnamed."""
        match = _ARCHIVE_VERSION.match(self.root.name)
        return match.group(1) if match else ""

    def template_version(self) -> str:
        """The template version, from `proto/change_log` — never from the PDF.

        The guide shipping inside 0.89.0.0 is titled for 0.48.0.0, so reading a
        version out of it would report one forty-one releases stale.
        """
        log = self.proto_directory / "change_log"
        if not log.is_file():
            return ""
        # Bounded read: the file is small, and a caller should not be able to
        # make this load an arbitrary amount because a path was wrong.
        head = log.read_text(encoding="utf-8", errors="replace")[:8192]
        match = _TEMPLATE_VERSION.search(head)
        return match.group(1) if match else ""

    def manifest(self) -> CompatibilityManifest:
        """Everything recorded about this copy, computed from it."""
        protos = sorted(self.proto_directory.glob("*.proto"))
        files = tuple(
            SdkFile(name=path.name, bytes=path.stat().st_size, sha256=_digest(path))
            for path in protos
        )
        rollup = hashlib.sha256()
        for entry in files:
            rollup.update(entry.name.encode("utf-8"))
            rollup.update(entry.sha256.encode("ascii"))
        return CompatibilityManifest(
            root=str(self.root),
            archive_version=self.archive_version(),
            template_version=self.template_version(),
            reconciled_template_version=RECONCILED_TEMPLATE_VERSION,
            proto_count=len(files),
            files=files,
            fingerprint=rollup.hexdigest(),
        )
