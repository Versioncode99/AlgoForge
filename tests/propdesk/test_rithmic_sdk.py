"""The SDK is the operator's, not this repository's — asserted, not promised.

Both Rithmic archives state that unauthorised reproduction or distribution
"(including its documentation), or any portion of it" may be prosecuted. The
consequence is a rule with a shape a test can check: nothing from either archive
is in this repository, and nothing in the build can put it there by accident.

The rest is about the other half of that decision. If the SDK is not committed
then it has to be *found*, and the way a connector fails when it is absent is
the whole difference between a fixable message and an operator checking a
password that was never the problem.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from forge.propdesk.rithmic.sdk import (
    RECONCILED_TEMPLATE_VERSION,
    SDK_ENV,
    VENDOR_DIRECTORY,
    RithmicSdk,
    SdkMissing,
)

ROOT = Path(__file__).resolve().parents[2]

#: Files that would only exist here if part of the archive had been committed.
FORBIDDEN_GLOBS = ("**/*.proto", "**/*_pb2.py", "**/*_pb2.pyi", "**/rapiplus.dll")

#: Directories that are not ours to search and would make the check meaningless.
SKIP = {".git", "node_modules", ".venv", "dist", "build", "__pycache__", "vendor"}


def _tracked(pattern: str) -> list[str]:
    found: list[str] = []
    for path in ROOT.glob(pattern):
        if any(part in SKIP for part in path.parts):
            continue
        found.append(path.relative_to(ROOT).as_posix())
    return found


@pytest.mark.parametrize("pattern", FORBIDDEN_GLOBS)
def test_no_part_of_the_rithmic_sdk_is_in_this_repository(pattern: str) -> None:
    offenders = _tracked(pattern)
    assert not offenders, (
        f"{offenders} look like Rithmic SDK artefacts. The archives are licensed to the "
        "operator and are not redistributable; see docs/ADR-0001-rithmic-transport.md."
    )


def test_the_ignore_file_refuses_the_vendor_directory_and_generated_protobuf() -> None:
    """Belt and braces: a copy placed for convenience cannot be committed."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "vendor/rithmic" in ignore
    assert "_pb2.py" in ignore


def test_no_source_file_transcribes_a_rithmic_template_table() -> None:
    """A field number in this repository is a field number copied out of a `.proto`.

    It is also the *wrong* number sooner or later: 0.89.0.0 changed
    `data_bar_seq_num` from a scalar to a repeated field and deprecated
    `entitlement_flag`, so a table transcribed from the reference guide shipping
    inside that archive decodes it incorrectly.
    """
    # Rithmic's field numbers are six-figure tags. A line assigning one to a
    # name is a transcription.
    transcription = re.compile(r"=\s*1[0-9]{5}\s*;")
    for path in (ROOT / "packages").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        assert not transcription.search(path.read_text(encoding="utf-8")), path


# ── discovery ────────────────────────────────────────────────────────────────


def test_an_absent_sdk_says_where_it_looked_and_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SDK_ENV, raising=False)
    with pytest.raises(SdkMissing) as refusal:
        RithmicSdk.discover(tmp_path)
    message = str(refusal.value)
    assert "does not ship one" in message
    assert SDK_ENV in message
    assert VENDOR_DIRECTORY.as_posix() in message
    assert str(tmp_path) in message, "the directory it looked in was not named"


def test_available_is_false_rather_than_raising_when_it_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SDK_ENV, raising=False)
    assert RithmicSdk.available(tmp_path) is False


def _fake_sdk(root: Path, *, version: str = "0.89.0.0", template: str = "5.42") -> Path:
    """A directory shaped like the archive, with none of its content."""
    archive = root / version
    (archive / "proto").mkdir(parents=True)
    (archive / "etc").mkdir(parents=True)
    (archive / "proto" / "change_log").write_text(
        f"#;====\n$;\t\t    template version\t{template}\n#;   \t\t\trelease date    ...\n",
        encoding="utf-8",
    )
    for name in ("request_login", "response_login", "request_heartbeat"):
        (archive / "proto" / f"{name}.proto").write_text(
            f"// a stand-in for {name}\n", encoding="utf-8"
        )
    return archive


def test_the_environment_variable_is_looked_at_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _fake_sdk(tmp_path / "elsewhere")
    monkeypatch.setenv(SDK_ENV, str(archive))
    assert RithmicSdk.discover(tmp_path).root == archive


def test_a_directory_holding_one_version_resolves_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who unpacked one level high is not told "not found"."""
    holder = tmp_path / "RProtocolAPI"
    _fake_sdk(holder)
    monkeypatch.setenv(SDK_ENV, str(holder))
    assert RithmicSdk.discover(tmp_path).archive_version() == "0.89.0.0"


def test_two_versions_side_by_side_resolve_to_the_later_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder = tmp_path / "RProtocolAPI"
    _fake_sdk(holder, version="0.88.0.0", template="5.41")
    _fake_sdk(holder, version="0.89.0.0", template="5.42")
    monkeypatch.setenv(SDK_ENV, str(holder))
    assert RithmicSdk.discover(tmp_path).archive_version() == "0.89.0.0"


def test_the_workspace_vendor_directory_is_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SDK_ENV, raising=False)
    _fake_sdk(tmp_path / VENDOR_DIRECTORY)
    assert RithmicSdk.discover(tmp_path).archive_version() == "0.89.0.0"


# ── the compatibility manifest ───────────────────────────────────────────────


def test_the_manifest_describes_the_copy_without_reproducing_any_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _fake_sdk(tmp_path)
    monkeypatch.setenv(SDK_ENV, str(archive))
    manifest = RithmicSdk.discover(tmp_path).manifest()
    assert manifest.archive_version == "0.89.0.0"
    assert manifest.template_version == "5.42"
    assert manifest.proto_count == 3
    assert manifest.fingerprint
    # Names and digests, and nothing that is a portion of the work.
    payload = manifest.as_dict()
    for entry in payload["files"]:
        assert set(entry) == {"name", "bytes", "sha256"}
        assert len(entry["sha256"]) == 64


def test_the_template_version_comes_from_the_change_log_not_the_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guide shipping inside 0.89.0.0 is titled for 0.48.0.0.

    Reading a version out of it would report one forty-one releases stale, and
    the adapter would believe it had been reconciled against a shape it had not.
    """
    archive = _fake_sdk(tmp_path, template="5.42")
    (archive / "doc").mkdir()
    (archive / "doc" / "Reference_Guide.pdf").write_bytes(b"template version 0.48")
    monkeypatch.setenv(SDK_ENV, str(archive))
    assert RithmicSdk.discover(tmp_path).template_version() == "5.42"


def test_a_version_this_build_was_not_reconciled_against_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _fake_sdk(tmp_path, version="0.91.0.0", template="5.44")
    monkeypatch.setenv(SDK_ENV, str(archive))
    manifest = RithmicSdk.discover(tmp_path).manifest()
    assert manifest.reconciled is False
    assert "unverified" in manifest.note
    assert RECONCILED_TEMPLATE_VERSION in manifest.note


def test_the_reconciled_version_is_the_one_the_archive_ships(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _fake_sdk(tmp_path, template=RECONCILED_TEMPLATE_VERSION)
    monkeypatch.setenv(SDK_ENV, str(archive))
    manifest = RithmicSdk.discover(tmp_path).manifest()
    assert manifest.reconciled is True
    assert "reconciled against" in manifest.note


def test_the_fingerprint_changes_when_any_file_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _fake_sdk(tmp_path)
    monkeypatch.setenv(SDK_ENV, str(archive))
    before = RithmicSdk.discover(tmp_path).manifest().fingerprint
    (archive / "proto" / "request_login.proto").write_text("// changed\n", encoding="utf-8")
    assert RithmicSdk.discover(tmp_path).manifest().fingerprint != before


def test_a_missing_change_log_reports_no_version_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _fake_sdk(tmp_path)
    (archive / "proto" / "change_log").unlink()
    monkeypatch.setenv(SDK_ENV, str(archive))
    manifest = RithmicSdk.discover(tmp_path).manifest()
    assert manifest.template_version == ""
    assert manifest.reconciled is False
