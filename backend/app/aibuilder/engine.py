"""Backtest of a position series (1 long / 0 flat / -1 short) with the SAME conventions as app.backtest:
  * the position is decided at the CLOSE of bar i and entered at the OPEN of bar i+1 (never the same bar);
  * exits, first to hit: stop (checked before take-profit inside a bar, gaps fill at the open), take-profit, max hold, and (optional)
    the position flipping away from the entry side, which exits at the next bar's OPEN;
  * costs split half at entry and half at exit (or asymmetric legs from app.costs); a trade never crosses the train/locked boundary;
  * metrics, buy&hold benchmark and the train / locked-final-period windows come from the existing modules.
The simulation loop is an independent implementation (tests compare it with backtest.run on an equivalent rule)."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from .. import backtest, config, costs, crypto_data as cd, data, quant

DEFAULT_EXITS = dict(stop_pct=None, tp_pct=None, hold_bars=None, exit_on_signal_off=True)


def clean_exits(x: dict | None) -> dict:
    x = dict(x or {})
    out = dict(DEFAULT_EXITS)
    for k, lo, hi in (("stop_pct", 0.1, 50.0), ("tp_pct", 0.1, 200.0)):
        v = x.get(k)
        out[k] = None if v in (None, "", 0) else min(max(float(v), lo), hi)
    h = x.get("hold_bars", x.get("hold_days"))
    out["hold_bars"] = None if h in (None, "", 0) else int(min(max(int(round(float(h))), 1), 5000))
    out["exit_on_signal_off"] = bool(x.get("exit_on_signal_off", True))
    return out


def sim_window(o, h, l, c, pos, a: int, b: int, ex: dict, cost, allow_short: bool):
    ce, cx = cost if isinstance(cost, tuple) else (cost / 2, cost / 2)
    n = b - a + 1
    r = np.zeros(n)
    trades = []
    stop, tp, hold, soff = ex["stop_pct"], ex["tp_pct"], ex["hold_bars"], ex["exit_on_signal_off"]
    i = a
    while i < b:
        side = int(pos[i])
        if side == 0 or (side < 0 and not allow_short):
            i += 1
            continue
        e = i + 1
        entry = o[e]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue
        x_last = b if hold is None else min(e + hold, b)
        if side > 0:
            stop_px = entry * (1 - stop / 100) if stop else None
            tp_px = entry * (1 + tp / 100) if tp else None
        else:
            stop_px = entry * (1 + stop / 100) if stop else None
            tp_px = entry * (1 - tp / 100) if tp else None
        exit_px, reason, x = c[x_last], ("hold" if hold is not None and x_last == e + hold else "window_end"), x_last
        for j in range(e, x_last + 1):
            if j > e:
                if soff and int(pos[j - 1]) != side:
                    exit_px, reason, x = o[j], "signal", j; break
                if stop_px is not None and ((o[j] <= stop_px) if side > 0 else (o[j] >= stop_px)):
                    exit_px, reason, x = o[j], "stop", j; break
                if tp_px is not None and ((o[j] >= tp_px) if side > 0 else (o[j] <= tp_px)):
                    exit_px, reason, x = o[j], "take_profit", j; break
            if stop_px is not None and ((l[j] <= stop_px) if side > 0 else (h[j] >= stop_px)):
                exit_px, reason, x = stop_px, "stop", j; break
            if tp_px is not None and ((h[j] >= tp_px) if side > 0 else (l[j] <= tp_px)):
                exit_px, reason, x = tp_px, "take_profit", j; break
        prev, growth = entry, 1.0
        for d in range(e, x + 1):
            end = exit_px if d == x else c[d]
            f = end / prev if side > 0 else 2.0 - end / prev
            if d == e:
                f *= (1 - ce)
            if d == x:
                f *= (1 - cx)
            r[d - a] = f - 1
            growth *= f
            prev = c[d]
        trades.append(dict(sig_idx=i, entry_idx=e, exit_idx=x, entry=float(entry), exit=float(exit_px), ret=float(growth - 1), reason=reason,
                           bars=int(x - e + 1), side="long" if side > 0 else "short"))
        i = x
    return r, trades


def market_setup(market: str, symbol: str, interval: str = "1d", cost_bps: float | None = None, cost_model: str | None = None, refresh: bool = False) -> dict:
    if market == "crypto":
        df, info = cd.load(symbol, interval, refresh=refresh)
        iv = info["interval"]
        k = cd.split_k(df, iv)
        bps = cd.COST_BPS_DEFAULT if cost_bps is None else float(cost_bps)
        return dict(df=df, info=info, k=k, periods=cd.INTERVALS[iv]["periods"], fmt=(lambda t, iv=iv: cd.fmt_ts(t, iv)), cost=bps / 1e4,
                    cost_label=f"{bps:g} bp round trip (exchange fee + slippage assumption)", allow_short=True, interval=iv, symbol=info["symbol"], market="crypto")
    df, info = data.load(symbol, refresh=refresh)
    k = data.split_index(df)
    c = costs.resolve(cost_model, cost_bps, None, None, config.COST_BPS_DEFAULT, "nse")
    cost = c["legs"] if c["legs"] else c["cost_bps"] / 1e4
    return dict(df=df, info=info, k=k, periods=backtest.TRADING_DAYS, fmt=(lambda t: str(t.date())), cost=cost, cost_label=c["label"],
                allow_short=False, interval="1d", symbol=info["ticker"], market="nse")


def thin(res: dict, limit: int = 500) -> dict:
    for w in res.values():
        nn = len(w.get("dates", []))
        if nn > limit:
            step = math.ceil(nn / limit)
            idx = sorted(set(list(range(0, nn, step)) + [nn - 1]))
            for kx in ("dates", "equity", "benchmark_equity"):
                w[kx] = [w[kx][t] for t in idx]
    return res


def backtest_positions(df: pd.DataFrame, pos, exits: dict, k: int, cost, periods: float, fmt, allow_short: bool = False, windows=None,
                       keep_series: bool = True, ret_sink: dict | None = None) -> dict:
    ex = clean_exits(exits)
    o, h, l, c = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Low", "Close"))
    pos = np.asarray(pos, dtype=int)
    wins = windows or {"train": (max(config.WARMUP_BARS, 1), k - 1), "locked": (k, len(df) - 1)}
    dates = df.index
    res, all_r, all_t, bh_all, raw_t = {}, [], [], [], []
    for name, (a, b) in wins.items():
        r, tr = sim_window(o, h, l, c, pos, a, b, ex, cost, allow_short)
        bh = backtest._bh_returns(c, a, b)
        raw_t += [dict(ret=t["ret"]) for t in tr]
        m = backtest._metrics(r, tr, periods)
        bm = backtest._metrics(bh, [], periods)
        bm = {q: bm[q] for q in ("total_return", "max_drawdown", "sharpe", "cagr")}
        out = dict(start=fmt(dates[a]), end=fmt(dates[b]), metrics=m, benchmark=bm)
        if keep_series:
            out["trades"] = [dict(window=name, side=t["side"], entry_date=fmt(dates[t["entry_idx"]]), exit_date=fmt(dates[t["exit_idx"]]), entry=round(t["entry"], 4),
                                  exit=round(t["exit"], 4), return_pct=round(t["ret"] * 100, 2), reason=t["reason"], bars=t["bars"]) for t in tr]
            all_t += out["trades"]
            out["equity"] = [round(float(x), 5) for x in np.cumprod(1 + r)]
            out["benchmark_equity"] = [round(float(x), 5) for x in np.cumprod(1 + bh)]
            out["dates"] = [fmt(d) for d in dates[a:b + 1]]
        res[name] = out
        all_r.append(r); bh_all.append(bh)
    fr, fb = np.concatenate(all_r), np.concatenate(bh_all)
    if ret_sink is not None:
        ret_sink["r"] = fr; ret_sink["train_r"] = all_r[0]
    res["full"] = dict(metrics=backtest._metrics(fr, raw_t, periods),
                       benchmark={q: v for q, v in backtest._metrics(fb, [], periods).items() if q in ("total_return", "max_drawdown", "sharpe", "cagr")})
    if keep_series:
        res["full"]["equity"] = [round(float(x), 5) for x in np.cumprod(1 + fr)]
        res["full"]["benchmark_equity"] = [round(float(x), 5) for x in np.cumprod(1 + fb)]
        res["full"]["dates"] = sum((res[q]["dates"] for q in wins), [])
        res["full"]["trades"] = all_t
        res["full"]["start"] = res[next(iter(wins))]["start"]; res["full"]["end"] = res[list(wins)[-1]]["end"]
    return res


# ------------------------------------------------------------------ honesty checks
def folds(df, pos, exits, k, cost, periods, fmt, allow_short, n: int = 4) -> dict:
    """Fixed rules, no tuning: cut the history after warm-up into n consecutive blocks and run each on its own. A STABILITY check
    (the author may have looked at every block), not a clean out-of-sample test; the locked final period is the clean one."""
    s, e = max(config.WARMUP_BARS, 1), len(df) - 1
    edges = np.linspace(s, e + 1, n + 1).astype(int)
    rows = []
    for q in range(n):
        a, b = int(edges[q]), int(edges[q + 1]) - 1
        if b - a < 20:
            continue
        w = backtest_positions(df, pos, exits, k, cost, periods, fmt, allow_short, windows={"w": (a, b)}, keep_series=False)["w"]
        rows.append(dict(start=w["start"], end=w["end"], trades=w["metrics"]["trades"], strategy_return=w["metrics"]["total_return"],
                         buy_hold_return=w["benchmark"]["total_return"], sharpe=w["metrics"]["sharpe"], max_drawdown=w["metrics"]["max_drawdown"]))
    return dict(blocks=rows, positive_blocks=sum(1 for r in rows if r["strategy_return"] > 0),
                beat_buy_hold_blocks=sum(1 for r in rows if r["strategy_return"] > r["buy_hold_return"]), n=len(rows))


def verdict(res: dict, n_versions: int, dsr: dict | None, fold: dict | None = None) -> dict:
    """Plain-English overfit verdict from the train vs locked windows (+ deflated Sharpe and block stability when available)."""
    tr, lk = res["train"]["metrics"], res["locked"]["metrics"]
    lb = res["locked"]["benchmark"]
    reasons = []
    if lk["trades"] < 5:
        flag, label = "inconclusive", "INCONCLUSIVE"
        reasons.append(f"Only {lk['trades']} trades in the locked final period: too few to judge.")
    elif tr["sharpe"] > 0.5 and lk["sharpe"] < 0:
        flag, label = "overfit", "LIKELY OVERFIT"
        reasons.append(f"Train Sharpe {tr['sharpe']:.2f} but locked-period Sharpe {lk['sharpe']:.2f}: the edge disappeared on data it had not been shaped on.")
    elif lk["total_return"] <= 0:
        flag, label = "lost", "LOST ON LOCKED PERIOD"
        reasons.append(f"Locked-period return {lk['total_return']*100:+.1f}% (buy & hold {lb['total_return']*100:+.1f}%).")
    elif tr["sharpe"] > 0 and lk["sharpe"] < 0.5 * tr["sharpe"]:
        flag, label = "weakened", "WEAKENED"
        reasons.append(f"Locked Sharpe {lk['sharpe']:.2f} is under half the train Sharpe {tr['sharpe']:.2f}.")
    else:
        flag, label = "ok", "HELD UP"
        reasons.append(f"Locked-period return {lk['total_return']*100:+.1f}% with Sharpe {lk['sharpe']:.2f} (train {tr['sharpe']:.2f}).")
    if 0 < lk["total_return"] < lb["total_return"]:
        reasons.append(f"It made money but less than just holding ({lb['total_return']*100:+.1f}% over the same period).")
    if n_versions > 1:
        reasons.append(f"{n_versions} versions/candidates were tried: the more that are tried, the more a good-looking result can be luck.")
    if dsr and dsr.get("dsr") is not None:
        reasons.append(f"Deflated Sharpe (train window, {dsr['n_trials']} trial{'s' if dsr['n_trials'] != 1 else ''}): {dsr['dsr']*100:.0f}% probability the edge is real; 95%+ is the usual bar.")
        if flag == "ok" and dsr["dsr"] < 0.5:
            flag, label = "weakened", "WEAKENED"
    if fold and fold["n"]:
        reasons.append(f"Stability: {fold['positive_blocks']} of {fold['n']} equal time blocks were profitable and {fold['beat_buy_hold_blocks']} beat buy & hold.")
        if flag == "ok" and fold["positive_blocks"] <= fold["n"] // 2:
            flag, label = "weakened", "WEAKENED"
    return dict(flag=flag, label=label, reasons=reasons)


def dsr_for(train_r, trial_sharpes: list[float], n_trials: int, periods: float) -> dict | None:
    try:
        rep = quant.dsr_report(np.asarray(train_r), trial_sharpes, max(int(n_trials), 1), periods)
        return dict(dsr=rep["dsr"], verdict=rep["verdict"], sharpe_annual=rep["sharpe_annual"], n_trials=rep["n_trials"], words=rep["words"])
    except Exception:
        return None


def per_period_sharpe(r) -> float:
    r = np.asarray(r)
    sd = np.std(r, ddof=1) if len(r) > 2 else 0.0
    return float(np.mean(r) / sd) if sd > 1e-12 else 0.0
