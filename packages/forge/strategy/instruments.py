"""Exchange contract units, separate from broker-specific commission assumptions."""

import re

# (dollars per full price point, minimum price tick). CME specifications.
CONTRACTS = {
    "MNQ": (2.0, 0.25),
    "NQ": (20.0, 0.25),
    "MES": (5.0, 0.25),
    "ES": (50.0, 0.25),
    "MGC": (10.0, 0.1),
    "GC": (100.0, 0.1),
}


def contract_units(symbol: str) -> tuple[float, float]:
    root = symbol.upper().split(".")[0]
    if root in CONTRACTS:
        return CONTRACTS[root]
    for key, units in CONTRACTS.items():
        if re.fullmatch(key + r"[FGHJKMNQUVXZ]\d{1,4}", root):
            return units
    if root in {"BTCUSDT", "ETHUSDT"}:
        return 1.0, 0.01
    raise ValueError(f"Contract units are not configured for {symbol}")
