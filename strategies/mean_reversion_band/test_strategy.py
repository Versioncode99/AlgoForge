"""Auto-generated conformance tests for Mean Reversion Band.

Every strategy ships a lookahead trap. A suite without one is rejected by the
harness, because the cheapest way to fake an edge is to read the future.
"""

import numpy as np

from forge.strategy.runtime import Window


def _window(n=300, seed=7):
    rng = np.random.default_rng(seed)
    close = 20000 + np.cumsum(rng.normal(0, 4, n))
    high, low = close + 3, close - 3
    return Window(close, high, low, close, np.full(n, 1000.0), [None] * n, n - 1)


def test_entry_signal_returns_valid_direction():
    w = _window()
    result = entry_signal(w, PARAMS)
    assert result in (1, -1, None)


def test_window_cannot_see_the_future():
    """The window must expose exactly index+1 bars and nothing beyond."""
    w = _window(n=300)
    assert w.closes.size == 300
    w2 = Window(w.opens, w.highs, w.lows, w.closes, w.volumes, [None] * 300, 99)
    assert w2.closes.size == 100, "window leaked bars past its end index"


def test_signal_is_deterministic():
    a, b = _window(), _window()
    assert entry_signal(a, PARAMS) == entry_signal(b, PARAMS)


PARAMS = {
    "sma_period": 30,
    "entry_atr": 1.5,
    "max_bars": 25
}

from strategy import entry_signal, exit_signal  # noqa: E402,F401
