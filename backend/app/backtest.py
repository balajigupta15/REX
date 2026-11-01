"""Long-only daily backtester. Signal at close of bar i -> entry at OPEN of bar i+1.
Exits (first to hit): stop-loss, take-profit, max hold. Stop is checked before take-profit when both are
inside the same bar (conservative). Gaps fill at the open. Costs: cost_bps round trip, half at entry, half at exit."""
from __future__ import annotations
import numpy as np
import pandas as pd
from . import config, rules

TRADING_DAYS = 252


def _run_window(o, h, l, c, sig, a: int, b: int, hold: int, stop, tp, cost):
    """Simulate over bars [a, b] inclusive. Returns (daily_returns[a..b], trades). Position force-closed at close of b.
    `cost` is a round-trip fraction (half at entry, half at exit) or an (entry_leg, exit_leg) tuple of fractions (asymmetric cost models)."""
    ce, cx = cost if isinstance(cost, tuple) else (cost / 2, cost / 2)
    n = b - a + 1
    r = np.zeros(n)
    trades = []
    i = a
    while i < b:                       # a signal on bar i needs bar i+1 to exist inside the window
        if not sig[i]:
            i += 1
            continue
        e = i + 1
        entry = o[e]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue
        x_last = min(e + hold, b)
        stop_px = entry * (1 - stop / 100) if stop else None
        tp_px = entry * (1 + tp / 100) if tp else None
        exit_px, reason, x = c[x_last], ("hold" if x_last == e + hold else "window_end"), x_last
        for j in range(e, x_last + 1):
            if j > e:   # gap handling at the open
                if stop_px is not None and o[j] <= stop_px:
                    exit_px, reason, x = o[j], "stop", j; break
                if tp_px is not None and o[j] >= tp_px:
                    exit_px, reason, x = o[j], "take_profit", j; break
            if stop_px is not None and l[j] <= stop_px:
                exit_px, reason, x = stop_px, "stop", j; break
            if tp_px is not None and h[j] >= tp_px:
                exit_px, reason, x = tp_px, "take_profit", j; break
        # daily returns for the days we held
        prev = entry
        growth = 1.0
        for d in range(e, x + 1):
            end = exit_px if d == x else c[d]
            f = end / prev
            if d == e:
                f *= (1 - ce)
            if d == x:
                f *= (1 - cx)
            r[d - a] = f - 1
            growth *= f
            prev = c[d]
        trades.append(dict(sig_idx=i, entry_idx=e, exit_idx=x, entry=float(entry), exit=float(exit_px),
                           ret=float(growth - 1), reason=reason, bars=int(x - e + 1)))
        i = x                           # flat after exit; a signal at the exit-bar close may re-enter next open
    return r, trades


def _metrics(r: np.ndarray, trades: list[dict], periods: float = TRADING_DAYS) -> dict:
    n = len(r)
    eq = np.cumprod(1 + r) if n else np.array([1.0])
    total = float(eq[-1] - 1)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
    dd = float(((np.concatenate([[1.0], eq]) / peak) - 1).min())
    sd = r.std(ddof=1) if n > 2 else 0.0
    sharpe = float(r.mean() / sd * np.sqrt(periods)) if sd > 1e-12 else 0.0
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    ci = None
    if len(rets) >= 5:
        rng = np.random.default_rng(0)
        bs = rng.choice(rets, size=(2000, len(rets))).mean(axis=1)
        ci = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
    exposure = float(np.mean(r != 0)) if n else 0.0
    return dict(
        days=int(n), trades=int(len(trades)), total_return=total,
        cagr=float(eq[-1] ** (periods / n) - 1) if n > 20 and eq[-1] > 0 else None,
        win_rate=float((rets > 0).mean()) if len(rets) else None,
        avg_trade=float(rets.mean()) if len(rets) else None,
        avg_trade_ci95=ci,
        profit_factor=float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        max_drawdown=dd, sharpe=sharpe, exposure=exposure,
    )


def _bh_returns(c, a, b):
    return c[a:b + 1] / c[a - 1:b] - 1


def run(df: pd.DataFrame, template: str, params: dict, exits: dict, cost_bps: float | None = None,
        windows: dict[str, tuple[int, int]] | None = None, keep_series: bool = True, periods: float = TRADING_DAYS, fmt=None,
        cost_legs: tuple[float, float] | None = None, ret_sink: dict | None = None) -> dict:
    """Run on named windows (default: train = [WARMUP, split-1], locked = [split, last]). Windows are simulated
    independently (a trade never crosses the train/locked boundary). `periods` = bars per year for Sharpe / CAGR (252 for NSE daily; the crypto
    market passes 365 / 2190 / 8760) and `fmt` formats a bar timestamp (default: the date); both default to the original NSE behaviour."""
    fmt = fmt or (lambda ts: str(ts.date()))
    cost = (config.COST_BPS_DEFAULT if cost_bps is None else float(cost_bps)) / 10000.0
    if cost_legs is not None:       # asymmetric (entry, exit) leg fractions from app.costs; overrides cost_bps
        cost = (float(cost_legs[0]), float(cost_legs[1]))
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    sig = rules.signals(df, template, params)
    if windows is None:
        s = max(config.WARMUP_BARS, 1)
        k = _split(df)
        windows = {"train": (s, k - 1), "locked": (k, len(df) - 1)}
    res, all_r, all_t, bh_all, raw_t = {}, [], [], [], []
    tf = rules.tag_frame(df) if keep_series else None
    dates = df.index
    for name, (a, b) in windows.items():
        r, tr = _run_window(o, h, l, c, sig, a, b, int(exits["hold_days"]), exits.get("stop_pct"), exits.get("tp_pct"), cost)
        bh = _bh_returns(c, a, b)
        raw_t += [dict(ret=t['ret']) for t in tr]
        m = _metrics(r, tr, periods)
        bm = _metrics(bh, [], periods)
        bm = {k2: bm[k2] for k2 in ("total_return", "max_drawdown", "sharpe", "cagr")}
        out = dict(start=fmt(dates[a]), end=fmt(dates[b]), metrics=m, benchmark=bm)
        if keep_series:
            tl = []
            for t in tr:
                tags = {g: str(tf[g].iloc[t["sig_idx"]]) for g in rules.TAG_GROUPS}
                tl.append(dict(window=name, entry_date=fmt(dates[t["entry_idx"]]), exit_date=fmt(dates[t["exit_idx"]]),
                               entry=round(t["entry"], 2), exit=round(t["exit"], 2), return_pct=round(t["ret"] * 100, 2),
                               reason=t["reason"], bars=t["bars"], tags=tags,
                               volume_ratio=float(tf["volume_ratio"].iloc[t["sig_idx"]]), vol_ratio=float(tf["vol_ratio"].iloc[t["sig_idx"]])))
            out["trades"] = tl
            all_t += tl
            out["equity"] = [round(float(x), 5) for x in np.cumprod(1 + r)]
            out["benchmark_equity"] = [round(float(x), 5) for x in np.cumprod(1 + bh)]
            out["dates"] = [fmt(d) for d in dates[a:b + 1]]
        res[name] = out
        all_r.append(r); bh_all.append(bh)
    full_r, full_bh = np.concatenate(all_r), np.concatenate(bh_all)
    if ret_sink is not None:        # optional: hand the daily strategy returns to the caller (used by the overfit statistics)
        ret_sink["r"] = full_r
    res["full"] = dict(metrics=_metrics(full_r, raw_t, periods),
                       benchmark={k2: v for k2, v in _metrics(full_bh, [], periods).items() if k2 in ("total_return", "max_drawdown", "sharpe", "cagr")})
    if keep_series:
        res["full"]["equity"] = [round(float(x), 5) for x in np.cumprod(1 + full_r)]
        res["full"]["benchmark_equity"] = [round(float(x), 5) for x in np.cumprod(1 + full_bh)]
        res["full"]["dates"] = sum((res[n_]["dates"] for n_ in windows), [])
        res["full"]["trades"] = all_t
        res["full"]["start"] = res[next(iter(windows))]["start"]; res["full"]["end"] = res[list(windows)[-1]]["end"]
    return res


def _split(df):
    from .data import split_index
    return split_index(df)
