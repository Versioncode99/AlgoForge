"""Every test gets its own workspace, whether it asks for one or not.

Several fixtures used to construct `create_app()` without setting a workspace
location, and relied on the resolver falling back to the repository's own
`data/` directory. That made them share state with each other *and* with
whatever real workspace existed on the machine running them — the previous
audit recorded it as a known hazard and left it standing.

It stopped being merely a hazard once the resolver's last resort became the OS
application-data directory, which is shared by every test in the run rather
than merely by every test in the checkout. `test_strategy_listing_does_not
_rescan_every_artifact` promptly saw sixteen strategies where it created four.

So isolation is now the default and not something each fixture has to remember.
`ALGOFORGE_VAULT` rather than `ALGOFORGE_HOME` is deliberate: it is the lower
precedence of the two, so a test that sets either name explicitly still wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the workspace resolver at this test's own temporary directory."""
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    # A stray keys file on the developer's machine must not leak into a test's
    # environment and make a "no credential" path behave as though there were
    # one.
    monkeypatch.delenv("ALGOFORGE_KEYS_FILE", raising=False)


# The pointer that says where the real installation's data lives. It is not
# tracked by git, so nothing restores it if a test overwrites it.
_REAL_POINTER = Path(__file__).resolve().parents[1] / "config" / "storage.json"


@pytest.fixture(autouse=True)
def protect_the_real_storage_pointer() -> Any:
    """Refuse to let a test repoint the developer's own installation.

    Isolating the workspace is not enough. The pointer lives in the
    *repository*, so anything that writes it — `write_pointer`, `switch`,
    `migrate-layout` — targets `<repo>/config/storage.json`, and a fixture that
    leaves `main.ROOT` on the real checkout writes to the real one.

    This is a guard rather than a style rule because it already happened. A
    migration test repointed the live installation at a pytest temporary
    directory; the next launch came up with an empty library while 413
    strategies sat untouched in a vault the application no longer knew about.
    No data was lost, and it was still the worst possible failure mode: it
    looks exactly like data loss.

    Snapshot, run, restore-if-changed, and say so loudly.
    """
    before = _REAL_POINTER.read_text(encoding="utf-8") if _REAL_POINTER.exists() else None
    yield
    after = _REAL_POINTER.read_text(encoding="utf-8") if _REAL_POINTER.exists() else None
    if after == before:
        return
    if before is None:
        _REAL_POINTER.unlink(missing_ok=True)
    else:
        _REAL_POINTER.write_text(before, encoding="utf-8")
    raise AssertionError(
        "A test rewrote the repository's real config/storage.json, which is the "
        "pointer to the developer's own research data. It has been restored, but "
        "the test must isolate `forge_api.main.ROOT` to a temporary repository "
        "instead of leaving it on the real checkout."
    )
