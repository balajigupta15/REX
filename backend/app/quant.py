"""Quant-grade honesty checks, all additive:
  * pbo()           Probability of Backtest Overfitting via CSCV (Bailey, Borwein, Lopez de Prado, Zhu 2014, https://ssrn.com/abstract=2326253)
  * dsr()           Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014, https://ssrn.com/abstract=2460551)
  * walk_forward()  rolling / anchored train -> test windows, re-tuned every window, stitched out-of-sample equity
  * lab()           one strategy across all 10 NSE stocks and BTC/ETH/SOL
"""
from __future__ import annotations
import itertools
import math
from statistics import NormalDist
import numpy as np
import pandas as pd
from . import backtest, config, refine, rules

_N = NormalDist()
EULER = 0.5772156649015329


# ------------------------------------------------------------------ PBO (CSCV)
def _sharpe_from_blocks(n, s, ss):
    """Per-period Sharpe from block aggregates (count, sum, sum of squares): mean / sample std."""
    mean = s / n
    var = (ss - n * mean ** 2) / max(n - 1, 1)
    sd = np.sqrt(np.maximum(var, 0.0))
    return np.where(sd > 1e-12, mean / np.where(sd > 1e-12, sd, 1.0), 0.0)


def pbo(returns: np.ndarray, S: int = 16, max_combos: int | None = None, seed: int = 0) -> dict:
    """returns: T x N matrix (rows = periods, columns = candidate strategies tried). S even number of equal blocks.
    For every way of choosing S/2 blocks as in-sample (the rest out-of-sample): pick the in-sample best strategy by Sharpe, find its
    out-of-sample rank w = rank/(N+1) among all N, logit = ln(w/(1-w)). PBO = share of splits where the in-sample winner ends up
    at or below the out-of-sample median (logit <= 0)."""
    M = np.asarray(returns, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2:
        raise ValueError("need at least 2 candidate strategies")
    T, N = M.shape
    S = int(S)
    if S % 2 or S < 4:
        raise ValueError("S must be an even number >= 4")
    if T < S * 2:
        S = max(4, (T // 2) // 2 * 2)
    if T < S * 2:
        raise ValueError("not enough observations for CSCV")
    L = T // S
    M = M[T - L * S:]                                 # drop the oldest leftovers so blocks are equal-length
    B = M.reshape(S, L, N)
    bs, bss = B.sum(axis=1), (B ** 2).sum(axis=1)     # S x N
    combos = np.array(list(itertools.combinations(range(S), S // 2)))
    if max_combos and len(combos) > max_combos:
        combos = combos[np.random.default_rng(seed).choice(len(combos), max_combos, replace=False)]
    mask = np.zeros((len(combos), S), dtype=bool)
    mask[np.arange(len(combos))[:, None], combos] = True
    n_in = L * (S // 2)
    is_s, is_ss = mask.astype(float) @ bs, mask.astype(float) @ bss
    oos_s, oos_ss = bs.sum(0) - is_s, bss.sum(0) - is_ss
    sr_is, sr_oos = _sharpe_from_blocks(n_in, is_s, is_ss), _sharpe_from_blocks(n_in, oos_s, oos_ss)
    best = sr_is.argmax(axis=1)
    v = sr_oos[np.arange(len(combos)), best]
    below = (sr_oos < v[:, None]).sum(axis=1)
    ties = (sr_oos == v[:, None]).sum(axis=1)
    rank = below + (ties + 1) / 2.0                    # 1..N, average rank for ties
    w = rank / (N + 1)
    logit = np.log(w / (1 - w))
    p = float((logit <= 0).mean())
    return dict(pbo=p, splits=int(len(combos)), blocks=S, block_len=int(L), candidates=int(N),
                logit_mean=float(logit.mean()), logit_hist=[int(x) for x in np.histogram(logit, bins=12, range=(-4, 4))[0]],
                oos_sharpe_of_is_best_mean=float(v.mean()), is_sharpe_best_mean=float(sr_is.max(axis=1).mean()),
                degradation=float(np.polyfit(sr_is.max(axis=1), v, 1)[0]) if np.ptp(sr_is.max(axis=1)) > 1e-12 else None)


def pbo_words(p: float, n: int) -> tuple[str, str]:
    if p < 0.2:
        lvl, t = "low", "Low risk of overfitting."
    elif p <= 0.5:
        lvl, t = "moderate", "Moderate risk of overfitting."
    else:
        lvl, t = "high", "High risk: the tuning process mostly picks luck."
    return lvl, (f"{t} Rex split the history into pieces many ways; each time it picked the best of {n} nearby settings on half the pieces and checked that pick on the other half. "
                 f"The pick landed in the bottom half of the other pieces {p*100:.0f}% of the time. Around 50% would mean the tuning is no better than chance, near 0% means the winner keeps winning.")


# ------------------------------------------------------------------ DSR
def _moments(r: np.ndarray) -> tuple[float, float]:
    r = np.asarray(r, float)
    sd = r.std()
    if sd < 1e-12 or len(r) < 4:
        return 0.0, 3.0
    z = (r - r.mean()) / sd
    return float((z ** 3).mean()), float((z ** 4).mean())


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """SR0: expected maximum Sharpe among n independent trials with zero true skill (per-period units)."""
    if n_trials < 2 or sr_var <= 0:
        return 0.0
    return math.sqrt(sr_var) * ((1 - EULER) * _N.inv_cdf(1 - 1 / n_trials) + EULER * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def dsr(sr: float, T: int, skew: float, kurt: float, n_trials: int, sr_var: float) -> float:
    """Probability that the true Sharpe exceeds the best-of-N-noise benchmark. sr, sr_var per period; kurt non-excess."""
    sr0 = expected_max_sharpe(n_trials, sr_var)
    den = 1 - skew * sr + (kurt - 1) / 4.0 * sr ** 2
    if T < 3 or den <= 0:
        return float("nan")
    return float(_N.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(den)))


def dsr_report(chosen_r: np.ndarray, trial_sharpes: list[float], n_trials: int, periods: float) -> dict:
    """chosen_r: daily returns of the strategy that was picked. trial_sharpes: per-period Sharpe of every eligible candidate."""
    r = np.asarray(chosen_r, float)
    sd = r.std(ddof=1) if len(r) > 2 else 0.0
    sr = float(r.mean() / sd) if sd > 1e-12 else 0.0
    skew, kurt = _moments(r)
    var = float(np.var(trial_sharpes, ddof=1)) if len(trial_sharpes) > 1 else 0.0
    sr0 = expected_max_sharpe(n_trials, var)
    p = dsr(sr, len(r), skew, kurt, n_trials, var)
    k = math.sqrt(periods)
    if p != p:
        verdict, words = "unknown", "Not enough data to compute the Deflated Sharpe Ratio."
    else:
        verdict = "likely_real" if p >= 0.95 else ("uncertain" if p >= 0.5 else "likely_luck")
        words = (f"The chosen settings have a Sharpe of {sr*k:.2f} (per year) on the tuning period. After trying {n_trials} combinations, pure luck alone would be expected to produce a best Sharpe of about "
                 f"{sr0*k:.2f}. The Deflated Sharpe Ratio is the probability that the true Sharpe is above that luck level: {p*100:.0f}%. "
                 + ("95% or more is the usual bar for calling it likely real." if verdict == "likely_real" else
                    "Between 50% and 95% is not convincing." if verdict == "uncertain" else "Below 50% means the result is what luck would be expected to give."))
    return dict(dsr=None if p != p else p, verdict=verdict, sharpe_annual=sr * k, sharpe_per_period=sr, expected_max_sharpe_annual=sr0 * k,
                n_trials=int(n_trials), sr_trials_std_annual=math.sqrt(var) * k, observations=int(len(r)), skew=skew, kurtosis=kurt, words=words,
                note="N counts every combination tried. Nearby settings are correlated, so the truly independent number of trials is smaller; this is the conservative choice.")


# ------------------------------------------------------------------ walk-forward
def _grid(template, params, exits, max_trials, seed):
    cands = refine.candidates(template, params, exits, seed)
    if len(cands) > max_trials:
        rng = np.random.default_rng(seed)
        keep = sorted(rng.choice(np.arange(1, len(cands)), max_trials - 1, replace=False))
        cands = [cands[0]] + [cands[i] for i in keep]
    return cands


def _tune(df, template, params, exits, a, b, cost, legs, periods, max_trials, seed, min_trades):
    """Best of the nearby grid on bars [a, b] only (train Sharpe, ties by return). Returns (params, exits, row, n_eligible)."""
    best, n_el = None, 0
    for pp, ee in _grid(template, params, exits, max_trials, seed):
        m = backtest.run(df, template, pp, ee, cost, windows={"w": (a, b)}, keep_series=False, periods=periods, cost_legs=legs)["w"]["metrics"]
        if m["trades"] >= min_trades:
            n_el += 1
            key = (m["sharpe"], m["total_return"])
            if best is None or key > best[0]:
                best = (key, pp, ee, m)
    if best is None:
        m = backtest.run(df, template, params, exits, cost, windows={"w": (a, b)}, keep_series=False, periods=periods, cost_legs=legs)["w"]["metrics"]
        return params, exits, m, 0
    return best[1], best[2], best[3], n_el


def make_windows(n: int, train_bars: int, test_bars: int, mode: str, start: int | None = None) -> list[dict]:
    """Bar-index windows. train=[a, b], test=[b+1, c], inclusive. rolling: the train window slides by `test_bars`; anchored: train always starts at `start`."""
    s = config.WARMUP_BARS if start is None else start
    out, t0 = [], s + train_bars
    while t0 < n and (t0 + test_bars <= n or n - t0 >= test_bars // 2):
        t1 = min(t0 + test_bars, n) - 1
        a = s if mode == "anchored" else t0 - train_bars
        out.append(dict(train=(a, t0 - 1), test=(t0, t1)))
        if t1 >= n - 1:
            break
        t0 += test_bars
    return out


def walk_forward(df: pd.DataFrame, template: str, params: dict, exits: dict, cost_bps: float | None = None, cost_legs=None, mode: str = "rolling",
                 train_bars: int | None = None, test_bars: int | None = None, periods: float = backtest.TRADING_DAYS, fmt=None,
                 max_trials: int = 120, min_trades: int = 5, seed: int = 0) -> dict:
    """Every window: tune on the TRAIN bars only, then run the chosen settings on the next unseen TEST bars. The stitched test returns are
    the honest out-of-sample record. Test bars never influence which settings were chosen (see tests/test_quant.py::test_no_leakage)."""
    if mode not in ("rolling", "anchored"):
        raise ValueError("mode must be rolling or anchored")
    fmt = fmt or (lambda ts: str(ts.date()))
    test_bars = int(test_bars or round(periods))
    train_bars = int(train_bars or 2 * round(periods))
    wins = make_windows(len(df), train_bars, test_bars, mode)
    if not wins:
        raise ValueError(f"Not enough history for walk-forward: {len(df)} bars, need more than warm-up {config.WARMUP_BARS} + train {train_bars} + test {test_bars}.")
    cost = config.COST_BPS_DEFAULT if cost_bps is None else float(cost_bps)
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    cl = (cost / 1e4) if cost_legs is None else (float(cost_legs[0]), float(cost_legs[1]))
    rows, oos_r, orig_r, bh_r, dates = [], [], [], [], []
    sig_cache = {}

    def sim(pp, ee, a, b):
        key = repr(sorted(pp.items()))
        if key not in sig_cache:
            sig_cache[key] = rules.signals(df, template, pp)
        return backtest._run_window(o, h, l, c, sig_cache[key], a, b, int(ee["hold_days"]), ee.get("stop_pct"), ee.get("tp_pct"), cl)

    for i, w in enumerate(wins):
        a, b = w["train"]; t0, t1 = w["test"]
        pp, ee, tm, n_el = _tune(df, template, params, exits, a, b, cost, cost_legs, periods, max_trials, seed, min_trades)
        r, trades = sim(pp, ee, t0, t1)
        ro, trades_o = sim(params, exits, t0, t1)
        bh = backtest._bh_returns(c, t0, t1)
        tm_ = backtest._metrics(r, trades, periods)
        rows.append(dict(window=i + 1, train_start=fmt(df.index[a]), train_end=fmt(df.index[b]), test_start=fmt(df.index[t0]), test_end=fmt(df.index[t1]),
                         params=pp, exits=ee, changed=(pp, ee) != (params, exits), eligible=n_el,
                         train_sharpe=tm["sharpe"], train_return=tm["total_return"], train_trades=tm["trades"],
                         test_return=tm_["total_return"], test_sharpe=tm_["sharpe"], test_trades=tm_["trades"], test_max_drawdown=tm_["max_drawdown"],
                         original_test_return=float(np.prod(1 + ro) - 1), buy_hold_test_return=float(np.prod(1 + bh) - 1)))
        oos_r.append(r); orig_r.append(ro); bh_r.append(bh); dates += [fmt(d) for d in df.index[t0:t1 + 1]]
        w["_tr"] = trades
    R, RO, BH = np.concatenate(oos_r), np.concatenate(orig_r), np.concatenate(bh_r)
    tr_all = [dict(ret=t["ret"]) for w in wins for t in w["_tr"]]
    m = backtest._metrics(R, tr_all, periods)
    bm = backtest._metrics(BH, [], periods)
    mo = backtest._metrics(RO, [], periods)
    is_sh = [r["train_sharpe"] for r in rows if r["train_trades"]]
    eff = (m["sharpe"] / float(np.mean(is_sh))) if is_sh and np.mean(is_sh) > 1e-9 else None
    pos = sum(r["test_return"] > 0 for r in rows)
    if m["trades"] < 5:
        flag, head = "inconclusive", f"Inconclusive: only {m['trades']} out-of-sample trade(s) across {len(rows)} windows."
    elif m["total_return"] > 0 and m["sharpe"] > 0.3 and pos >= len(rows) / 2:
        flag, head = "holds", f"Held up out of sample: re-tuning each window and trading only unseen data returned {m['total_return']*100:+.1f}% (Sharpe {m['sharpe']:.2f}), profitable in {pos} of {len(rows)} windows."
    else:
        flag, head = "fails", f"Did not hold up out of sample: re-tuned each window and tested on unseen data, the strategy returned {m['total_return']*100:+.1f}% (Sharpe {m['sharpe']:.2f}), profitable in {pos} of {len(rows)} windows."
    notes = [f"{mode.capitalize()} windows: tune on {train_bars} bars, then trade the next {test_bars} bars the tuning never saw; every window is re-tuned from scratch.",
             f"Buy and hold over the same out-of-sample span: {bm['total_return']*100:+.1f}%. Your untuned original rules: {mo['total_return']*100:+.1f}%.",
             "Walk-forward efficiency (out-of-sample Sharpe divided by the average in-sample Sharpe): " + (f"{eff:.2f}. Near or above 1 is good; well below 0.5 suggests the tuning was fitting noise." if eff is not None else "n/a (no in-sample trades).")]
    step = max(1, math.ceil(len(R) / 700))
    idx = sorted(set(list(range(0, len(R), step)) + [len(R) - 1]))
    eq, eqo, eqb = (np.cumprod(1 + x) for x in (R, RO, BH))
    return dict(mode=mode, train_bars=train_bars, test_bars=test_bars, windows=rows, summary=dict(metrics=m, benchmark={k: bm[k] for k in ("total_return", "max_drawdown", "sharpe", "cagr")},
                original=dict(total_return=mo["total_return"], sharpe=mo["sharpe"]), efficiency=eff, windows_profitable=int(pos), windows_total=len(rows)),
                verdict=dict(flag=flag, headline=head, notes=notes),
                chart=dict(dates=[dates[i] for i in idx], equity=[round(float(eq[i]), 5) for i in idx], original_equity=[round(float(eqo[i]), 5) for i in idx],
                           benchmark_equity=[round(float(eqb[i]), 5) for i in idx],
                           boundaries=[r["test_start"] for r in rows]),
                oos=dict(start=rows[0]["test_start"], end=rows[-1]["test_end"], bars=int(len(R))))


# ------------------------------------------------------------------ strategy lab
def lab_verdict(train, locked, full) -> tuple[str, str]:
    if full["trades"] < 10:
        return "few_trades", "Too few trades to judge"
    if train["sharpe"] > 0 and locked["sharpe"] > 0 and locked["total_return"] > 0:
        return "holds", "Holds up"
    if train["sharpe"] > 0:
        return "fades", "Worked in training, faded in the locked year"
    return "loses", "Loses money after costs"


def lab_one(kind: str, symbol: str, template: str, params: dict, exits: dict, cost_bps: float | None, legs=None, interval: str = "1d") -> dict:
    from . import data, crypto_data as cd
    if kind == "crypto":
        df, info = cd.load(symbol, interval)
        k = cd.split_k(df, info["interval"]); periods = cd.INTERVALS[info["interval"]]["periods"]; name = cd.SYMBOLS[info["symbol"]]["name"]; short = cd.SYMBOLS[info["symbol"]]["short"]
        cost = cd.COST_BPS_DEFAULT if cost_bps is None else cost_bps
    else:
        df, info = data.load(symbol)
        k = data.split_index(df); periods = backtest.TRADING_DAYS
        from .assets import NSE_NAMES
        name = NSE_NAMES.get(symbol, (symbol, ""))[0]; short = symbol.split(".")[0]
        cost = config.COST_BPS_DEFAULT if cost_bps is None else cost_bps
    wins = {"train": (config.WARMUP_BARS, k - 1), "locked": (k, len(df) - 1)}
    res = backtest.run(df, template, params, exits, cost, windows=wins, keep_series=False, periods=periods, cost_legs=legs if kind == "stock" else None)
    f, tr, lk = res["full"]["metrics"], res["train"]["metrics"], res["locked"]["metrics"]
    flag, text = lab_verdict(tr, lk, f)
    return dict(symbol=info.get("symbol", symbol) if kind == "crypto" else symbol, short=short, name=name, asset_class=kind, total_return=f["total_return"], cagr=f["cagr"], sharpe=f["sharpe"],
                max_drawdown=f["max_drawdown"], trades=f["trades"], win_rate=f["win_rate"], buy_hold_return=res["full"]["benchmark"]["total_return"],
                train_sharpe=tr["sharpe"], locked_return=lk["total_return"], locked_sharpe=lk["sharpe"], locked_trades=lk["trades"], verdict=flag, verdict_text=text,
                bars=len(df), data_source=info.get("source") or info.get("provider"))


def lab(template: str, params: dict, exits: dict, cost_bps: float | None = None, crypto_cost_bps: float | None = None, legs=None,
        tickers: list[str] | None = None, coins: list[str] | None = None) -> dict:
    from . import crypto_data as cd
    rows, errors = [], []
    for t in (tickers if tickers is not None else config.DEFAULT_TICKERS):
        try:
            rows.append(lab_one("stock", t, template, params, exits, cost_bps, legs))
        except Exception as e:
            errors.append(dict(symbol=t, error=f"{type(e).__name__}: {str(e)[:140]}"))
    for s in (coins if coins is not None else list(cd.SYMBOLS)):
        try:
            rows.append(lab_one("crypto", s, template, params, exits, crypto_cost_bps if crypto_cost_bps is not None else cost_bps))
        except Exception as e:
            errors.append(dict(symbol=s, error=f"{type(e).__name__}: {str(e)[:140]}"))
    n = len(rows)
    held = sum(r["verdict"] == "holds" for r in rows)
    return dict(template=template, params=params, exits=exits, rows=rows, errors=errors,
                summary=dict(assets=n, holds=held, fades=sum(r["verdict"] == "fades" for r in rows), loses=sum(r["verdict"] == "loses" for r in rows),
                             few_trades=sum(r["verdict"] == "few_trades" for r in rows),
                             median_sharpe=float(np.median([r["sharpe"] for r in rows])) if rows else None,
                             beats_buy_hold=sum(r["total_return"] > r["buy_hold_return"] for r in rows)),
                notes=["Same rules and exits on every asset, daily bars, signals at the close and entries at the next open, long only. Holds up = positive Sharpe in training AND a positive locked final year.",
                       "Running one idea on 13 assets is a multiple test: one asset 'holding up' by chance is likely. Treat a result as strong only when many assets agree.",
                       "Stocks use the chosen cost model; crypto uses a bps fee (default 10 bp round trip)."])
