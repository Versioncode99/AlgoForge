from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_capability_manifest_forbids_external_execution() -> None:
    manifest = json.loads((ROOT / "config" / "capabilities.json").read_text("utf-8"))
    capabilities = manifest["capabilities"]
    assert manifest["paper_only"] is True
    assert capabilities["live_order"] is False
    assert capabilities["broker_account_creation"] is False
    assert capabilities["prop_account_signup"] is False
    assert capabilities["payment"] is False
    assert capabilities["kyc"] is False


def test_reference_ledger_fails_closed_for_missing_licences() -> None:
    ledger = json.loads((ROOT / "config" / "reference-ledger.json").read_text("utf-8"))
    for entry in ledger:
        if entry["licence"] is None:
            assert entry["lane"] in {"CLEAN_ROOM", "REJECT"}


def test_no_live_order_route_declared() -> None:
    route_sources = list((ROOT / "apps").rglob("*.py")) if (ROOT / "apps").exists() else []
    combined = "\n".join(path.read_text("utf-8") for path in route_sources)
    assert "/orders/live" not in combined
    assert "/broker/connect" not in combined
