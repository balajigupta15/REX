"""Rule templates -> boolean entry signals (decided at the CLOSE of bar i, entered at the OPEN of bar i+1)."""
from __future__ import annotations
import numpy as np
import pandas as pd

# Each param: name, label, type, default, min, max, step. Exits are shared by every template.
TEMPLATES: dict[str, dict] = {
    "breakout": {
        "label": "Breakout: N-day high",
        "description": "Buy when the close breaks above the highest high of the previous N days.",
        "params": [dict(name="n", label="Lookback N (days)", type="int", default=20, min=5, max=120, step=1)],
    },
    "ma_cross": {
        "label": "Moving-average crossover",
        "description": "Buy when the fast moving average crosses above the slow one.",
        "params": [dict(name="fast", label="Fast MA (days)", type="int", default=10, min=3, max=60, step=1),
                   dict(name="slow", label="Slow MA (days)", type="int", default=50, min=10, max=250, step=1)],
    },
    "rsi": {
        "label": "RSI oversold / overbought",
        "description": "oversold: buy when RSI falls below the level (bounce). overbought: buy when RSI rises above the level (momentum).",
        "params": [dict(name="period", label="RSI period", type="int", default=14, min=2, max=40, step=1),
                   dict(name="level", label="RSI level", type="float", default=30.0, min=5, max=95, step=1),
                   dict(name="mode", label="Mode", type="choice", default="oversold", choices=["oversold", "overbought"])],
    },
    "mean_reversion": {
        "label": "Mean reversion (z-score)",
        "description": "Buy when the close is more than z standard deviations below its N-day mean.",
        "params": [dict(name="n", label="Window N (days)", type="int", default=20, min=5, max=120, step=1),
                   dict(name="z", label="Entry z-score (below mean)", type="float", default=2.0, min=0.5, max=4.0, step=0.1)],
    },
}

EXIT_PARAMS = [
    dict(name="hold_days", label="Max hold (trading days)", type="int", default=10, min=1, max=120, step=1),
    dict(name="stop_pct", label="Stop-loss %", type="float", default=5.0, min=0.5, max=30.0, step=0.5, optional=True),
    dict(name="tp_pct", label="Take-profit %", type="float", default=10.0, min=0.5, max=100.0, step=0.5, optional=True),
]


class SpecError(ValueError):
    pass


def default_params(template: str) -> dict:
    return {p["name"]: p["default"] for p in TEMPLATES[template]["params"]}


def validate(template: str, params: dict | None, exits: dict | None) -> tuple[dict, dict]:
    if template not in TEMPLATES:
        raise SpecError(f"Unknown template '{template}'. Choose one of {sorted(TEMPLATES)}")
    out = default_params(template)
    for p in TEMPLATES[template]["params"]:
        if params and p["name"] in params and params[p["name"]] is not None:
            v = params[p["name"]]
            if p["type"] == "choice":
                if v not in p["choices"]:
                    raise SpecError(f"{p['name']} must be one of {p['choices']}")
            else:
                try:
                    v = int(round(float(v))) if p["type"] == "int" else float(v)
                except (TypeError, ValueError):
                    raise SpecError(f"{p['name']} must be a number")
                if not (p["min"] <= v <= p["max"]):
                    raise SpecError(f"{p['name']} must be between {p['min']} and {p['max']}")
            out[p["name"]] = v
    if template == "ma_cross" and out["fast"] >= out["slow"]:
        raise SpecError("fast MA must be shorter than slow MA")
    ex = {"hold_days": 10, "stop_pct": 5.0, "tp_pct": 10.0}
    for p in EXIT_PARAMS:
        if exits and p["name"] in exits:
            v = exits[p["name"]]
            if v is None and p.get("optional"):
                ex[p["name"]] = None
                continue
            try:
                v = int(round(float(v))) if p["type"] == "int" else float(v)
            except (TypeError, ValueError):
                raise SpecError(f"{p['name']} must be a number")
            if not (p["min"] <= v <= p["max"]):
                raise SpecError(f"{p['name']} must be between {p['min']} and {p['max']}")
            ex[p["name"]] = v
    return out, ex


def rsi(close: pd.Series, period: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna() & dn.notna())


def signals(df: pd.DataFrame, template: str, p: dict) -> np.ndarray:
    """Boolean array, True where the entry condition holds at the close of that bar. Uses only past/current data."""
    c, h = df["Close"], df["High"]
    if template == "breakout":
        prior_high = h.shift(1).rolling(p["n"]).max()
        s = c > prior_high
    elif template == "ma_cross":
        f, sl = c.rolling(p["fast"]).mean(), c.rolling(p["slow"]).mean()
        s = (f > sl) & (f.shift(1) <= sl.shift(1))
    elif template == "rsi":
        r = rsi(c, p["period"])
        if p["mode"] == "oversold":
            s = r < p["level"]
        else:
            s = (r > p["level"]) & (r.shift(1) <= p["level"])
    elif template == "mean_reversion":
        m, sd = c.rolling(p["n"]).mean(), c.rolling(p["n"]).std()
        s = (c - m) / sd < -p["z"]
    else:
        raise SpecError(template)
    return s.fillna(False).to_numpy(dtype=bool)


# ---- setup tags (known at the signal bar close; used for memory similarity) ----
TAG_GROUPS = {
    "trend": ["up", "flat", "down"],
    "volume": ["spike", "normal", "dry"],
    "volatility": ["calm", "normal", "stressed"],
    "momentum": ["up", "flat", "down"],
}


def tag_frame(df: pd.DataFrame) -> pd.DataFrame:
    c, v = df["Close"], df["Volume"]
    sma50 = c.rolling(50, min_periods=30).mean()
    slope = sma50 - sma50.shift(10)
    trend = np.where((c > sma50) & (slope > 0), "up", np.where((c < sma50) & (slope < 0), "down", "flat"))
    vr = v / v.shift(1).rolling(20, min_periods=10).mean()
    volume = np.where(vr >= 1.5, "spike", np.where(vr < 0.7, "dry", "normal"))
    ret = c.pct_change()
    vol_ratio = ret.rolling(20, min_periods=15).std() / ret.rolling(250, min_periods=60).std()
    vola = np.where(vol_ratio < 0.8, "calm", np.where(vol_ratio > 1.2, "stressed", "normal"))
    m5 = c.pct_change(5)
    mom = np.where(m5 > 0.02, "up", np.where(m5 < -0.02, "down", "flat"))
    return pd.DataFrame({"trend": trend, "volume": volume, "volatility": vola, "momentum": mom,
                         "volume_ratio": vr.round(2).fillna(1.0), "vol_ratio": vol_ratio.round(2).fillna(1.0)}, index=df.index)


def tag_vector(tags: dict) -> list[float]:
    """One-hot over every (group, value): 12 dims. Cosine of two vectors = (#matching groups) / 4."""
    return [1.0 if tags.get(g) == val else 0.0 for g, vals in TAG_GROUPS.items() for val in vals]
