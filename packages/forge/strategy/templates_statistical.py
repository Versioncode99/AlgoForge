"""Statistical research adaptations. None claims replication or a verified edge."""

from forge.strategy.models import ParameterSpec as P
from forge.strategy.templates import Template

EXIT = """
def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(20)
    if atr == atr:
        adverse = (w.closes[-1] - pos.entry_price) * pos.direction
        if adverse < -float(p["stop_atr"]) * atr:
            return "stop"
    signal = entry_signal(w, p)
    if signal is not None and signal != pos.direction:
        return "signal"
    return None
"""

COMMON = (
    P(name="max_bars", default=60, low=20, high=180, step=20),
    P(name="stop_atr", default=2, low=1, high=4, step=0.5),
)

VOL_MOMENTUM = Template(
    key="vol_normalized_momentum",
    name="Volatility-normalized momentum",
    family="momentum",
    hypothesis=(
        "Signed multi-horizon returns normalized by realized volatility may "
        "isolate persistent order flow from isolated high-volatility shocks."
    ),
    falsifiable_prediction=(
        "Continuation must survive transaction costs and a no-volatility-gate"
        " ablation on untouched validation data; otherwise reject the "
        "mechanism."
    ),
    parameters=(
        P(name="lookback", default=120, low=40, high=480, step=40),
        P(name="threshold", default=1, low=0.5, high=2.5, step=0.25),
        P(name="vol_cap", default=1.5, low=1, high=3, step=0.25),
        *COMMON,
    ),
    warmup_bars=520,
    source='''"""TSMOM-inspired intraday adaptation; not monthly portfolio replication."""
import numpy as np

def entry_signal(w, p):
    n = int(p["lookback"])
    c = w.closes[-n-1:]
    if len(c) < n + 1 or np.any(c <= 0):
        return None
    r = np.diff(np.log(c))
    sigma = float(np.std(r))
    if sigma <= 1e-12:
        return None
    recent = float(np.std(r[-20:]))
    if recent > float(p["vol_cap"]) * sigma:
        return None
    score = float(np.sum(r)) / (sigma * np.sqrt(n))
    fast = float(np.sum(r[-max(10, n//4):]))
    if score > float(p["threshold"]) and fast > 0:
        return 1
    if score < -float(p["threshold"]) and fast < 0:
        return -1
    return None
'''
    + EXIT,
)

VARIANCE_REVERSION = Template(
    key="variance_ratio_reversion",
    name="Variance-ratio regime reversion",
    family="mean_reversion",
    hypothesis=(
        "Negative serial dependence identified by a multi-period variance "
        "ratio may distinguish reversible price dislocations from persistent "
        "directional regimes."
    ),
    falsifiable_prediction=(
        "A low variance-ratio gate must improve cost-adjusted reversion "
        "expectancy against the identical ungated z-score model across "
        "chronological folds."
    ),
    parameters=(
        P(name="lookback", default=120, low=60, high=300, step=30),
        P(name="vr_max", default=0.8, low=0.5, high=1, step=0.1),
        P(name="entry_z", default=2, low=1.25, high=3, step=0.25),
        *COMMON,
    ),
    warmup_bars=340,
    source='''"""Lo-MacKinlay-inspired regime filter; not a formal hypothesis-test p-value."""
import numpy as np

def entry_signal(w, p):
    n = int(p["lookback"])
    c = w.closes[-n-1:]
    if len(c) < n+1 or np.any(c <= 0):
        return None
    x = np.log(c)
    r = np.diff(x)
    variance = float(np.var(r))
    if variance <= 1e-16:
        return None
    vr = float(np.var(x[4:] - x[:-4])) / (4 * variance)
    scale = float(np.std(x[:-1]))
    if vr >= float(p["vr_max"]) or scale <= 1e-12:
        return None
    z = (x[-1] - float(np.mean(x[:-1]))) / scale
    if z > float(p["entry_z"]):
        return -1
    if z < -float(p["entry_z"]):
        return 1
    return None
'''
    + EXIT,
)

OU_REVERSION = Template(
    key="ou_half_life_reversion",
    name="OU half-life reversion",
    family="mean_reversion",
    hypothesis=(
        "A locally mean-reverting AR(1) price process with a finite half-life"
        " may support dislocation fades when estimated equilibrium distance "
        "exceeds residual noise."
    ),
    falsifiable_prediction=(
        "Reject if the fitted coefficient implies a unit root, half-life "
        "exceeds the holding horizon, or chronological net returns fail to "
        "beat the ungated baseline."
    ),
    parameters=(
        P(name="lookback", default=160, low=80, high=400, step=40),
        P(name="entry_z", default=2, low=1.25, high=3, step=0.25),
        P(name="half_life_max", default=40, low=10, high=80, step=10),
        *COMMON,
    ),
    warmup_bars=440,
    source='''"""Single-series OU adaptation. No factor hedge or cross-asset stat-arb claim."""
import numpy as np

def entry_signal(w, p):
    n = int(p["lookback"])
    c = w.closes[-n-2:]
    if len(c) < n+2 or np.any(c <= 0):
        return None
    prices = np.log(c)
    # Estimate entirely before the signal observation.
    x, y = prices[:-2], prices[1:-1]
    xc, yc = x - x.mean(), y - y.mean()
    denom = float(np.dot(xc, xc))
    if denom <= 1e-16:
        return None
    phi = float(np.dot(xc, yc)) / denom
    if not 0.01 < phi < 0.995:
        return None
    half_life = -np.log(2) / np.log(phi)
    if half_life > min(float(p["half_life_max"]), float(p["max_bars"])):
        return None
    intercept = float(y.mean() - phi*x.mean())
    mean = intercept / (1-phi)
    residual = y - (intercept + phi*x)
    scale = float(np.std(residual)) / np.sqrt(1-phi*phi)
    if scale <= 1e-12:
        return None
    z = (prices[-1] - mean) / scale
    if z > float(p["entry_z"]):
        return -1
    if z < -float(p["entry_z"]):
        return 1
    return None
'''
    + EXIT,
)

STATISTICAL_TEMPLATES = {t.key: t for t in (VOL_MOMENTUM, VARIANCE_REVERSION, OU_REVERSION)}
