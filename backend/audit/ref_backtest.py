"""INDEPENDENT reference backtester for the accuracy audit. Deliberately imports NOTHING from `app`.
Written from the documented spec only: signal at the close of bar i, entry at the open of bar i+1, long only, one position at a time,
stop checked before take-profit inside a bar, gaps fill at the open, exit at close of bar (entry bar + hold_days) if nothing else hit,
cost = round-trip bps split half at entry and half at exit, windows simulated independently:
train = bars [100, k-1], locked = bars [k, last], k = first bar later than (last date - 365 calendar days)."""
import math
import numpy as np
import pandas as pd

WARMUP, LOCKED_DAYS = 100, 365


def load_csv(path):
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df[~df.index.duplicated()].sort_index()


def split_k(df):
    cutoff = df.index[-1] - pd.Timedelta(days=LOCKED_DAYS)
    return next(i for i, d in enumerate(df.index) if d > cutoff)


def wilder_rsi(close, period):
    c = close.to_numpy(float)
    n = len(c)
    up = np.full(n, np.nan); dn = np.full(n, np.nan)
    a = 1.0 / period
    u = d = None
    for t in range(1, n):
        ch = c[t] - c[t - 1]
        gu, gd = max(ch, 0.0), max(-ch, 0.0)
        u = gu if u is None else (1 - a) * u + a * gu
        d = gd if d is None else (1 - a) * d + a * gd
        if t >= period:          # `period` differences observed
            up[t], dn[t] = u, d
    out = np.full(n, np.nan)
    for t in range(n):
        if np.isnan(up[t]):
            continue
        out[t] = 100.0 if dn[t] == 0 else 100 - 100 / (1 + up[t] / dn[t])
    return pd.Series(out, index=close.index)


def signals(df, template, p):
    c, h = df["Close"], df["High"]
    if template == "breakout":
        prev_high = pd.Series([h.iloc[max(0, i - p["n"]):i].max() if i >= p["n"] else np.nan for i in range(len(h))], index=h.index)
        s = c > prev_high
    elif template == "ma_cross":
        f, sl = c.rolling(p["fast"]).mean(), c.rolling(p["slow"]).mean()
        s = (f > sl) & (f.shift(1) <= sl.shift(1))
    elif template == "rsi":
        r = wilder_rsi(c, p["period"])
        s = (r < p["level"]) if p["mode"] == "oversold" else ((r > p["level"]) & (r.shift(1) <= p["level"]))
    elif template == "mean_reversion":
        m, sd = c.rolling(p["n"]).mean(), c.rolling(p["n"]).std(ddof=1)
        s = ((c - m) / sd) < -p["z"]
    else:
        raise ValueError(template)
    return s.fillna(False).to_numpy(bool)


def simulate(df, sig, a, b, hold, stop, tp, cost):
    O, H, L, C = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    daily = {}          # bar index -> return
    trades = []
    i = a
    while i < b:
        if not sig[i]:
            i += 1
            continue
        e = i + 1
        entry = O[e]
        stop_px = entry * (1 - stop / 100.0) if stop else None
        tp_px = entry * (1 + tp / 100.0) if tp else None
        last = min(e + hold, b)
        xi, xp, why = last, C[last], ("hold" if e + hold <= b else "window_end")
        for j in range(e, last + 1):
            if j > e:
                if stop_px is not None and O[j] <= stop_px: xi, xp, why = j, O[j], "stop"; break
                if tp_px is not None and O[j] >= tp_px: xi, xp, why = j, O[j], "take_profit"; break
            if stop_px is not None and L[j] <= stop_px: xi, xp, why = j, stop_px, "stop"; break
            if tp_px is not None and H[j] >= tp_px: xi, xp, why = j, tp_px, "take_profit"; break
        g = 1.0
        for d in range(e, xi + 1):
            base = entry if d == e else C[d - 1]
            end = xp if d == xi else C[d]
            f = end / base
            if d == e: f *= 1 - cost / 2
            if d == xi: f *= 1 - cost / 2
            daily[d] = f - 1
            g *= f
        trades.append(dict(entry_idx=e, exit_idx=xi, entry=entry, exit=xp, ret=g - 1, reason=why))
        i = xi
    series = np.array([daily.get(d, 0.0) for d in range(a, b + 1)])
    return series, trades


def metrics(series, trades=()):
    eq = np.concatenate([[1.0], np.cumprod(1 + series)])
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    sd = series.std(ddof=1) if len(series) > 2 else 0.0
    sharpe = float(series.mean() / sd * math.sqrt(252)) if sd > 1e-12 else 0.0
    rets = [t["ret"] for t in trades]
    return dict(total_return=float(eq[-1] - 1), max_drawdown=dd, sharpe=sharpe, trades=len(rets),
                win_rate=(sum(r > 0 for r in rets) / len(rets)) if rets else None)


def buy_hold(df, a, b):
    C = df["Close"].to_numpy(float)
    return metrics(C[a:b + 1] / C[a - 1:b] - 1)


def backtest(df, template, params, exits, cost_bps=15.0):
    sig = signals(df, template, params)
    k = split_k(df)
    out = {}
    for name, (a, b) in {"train": (WARMUP, k - 1), "locked": (k, len(df) - 1)}.items():
        s, tr = simulate(df, sig, a, b, int(exits["hold_days"]), exits.get("stop_pct"), exits.get("tp_pct"), cost_bps / 1e4)
        out[name] = dict(metrics=metrics(s, tr), trades=tr, benchmark=buy_hold(df, a, b), start=str(df.index[a].date()), end=str(df.index[b].date()))
    return out
