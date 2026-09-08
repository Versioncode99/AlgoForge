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

import pytest


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the workspace resolver at this test's own temporary directory."""
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    # A stray keys file on the developer's machine must not leak into a test's
    # environment and make a "no credential" path behave as though there were
    # one.
    monkeypatch.delenv("ALGOFORGE_KEYS_FILE", raising=False)
