"""OFFLINE MODE strategy-code synthesizer. Used only when Gemini is unavailable (no key, quota, outage). It is not a menu of ready-made
strategies: it COMPOSES small indicator building blocks (trend filter, breakout channel, RSI, z-score, volatility regime, volume
confirmation, exit rule) into new `signals(df)` source code, then SEARCHES the combinations on the TRAIN window only. The locked final
period is never used to rank. The number of combinations tried is reported and used as the trial count for the deflated Sharpe.
Everything it writes goes through exactly the same AST allowlist + sandbox + look-ahead checks as model-written code."""
from __future__ import annotations
import itertools, logging
import numpy as np
from . import ast_guard, engine, sandbox

log = logging.getLogger("rex.ai.synth")
BPD = {"1d": 1, "4h": 6, "1h": 24}                      # bars per day
HEAD = "def signals(df):\n    close = df['close']\n    high = df['high']\n    low = df['low']\n    volume = df['volume']\n"
RSI = ("    d = close.diff()\n    up = d.clip(lower=0).ewm(alpha=1 / {n}, adjust=False).mean()\n    dn = (-d.clip(upper=0)).ewm(alpha=1 / {n}, adjust=False).mean()\n"
       "    rsi = 100 - 100 / (1 + up / (dn + 1e-12))\n")
TAIL_LONG = "    raw = pd.Series(np.where(enter, 1.0, np.where(leave, 0.0, np.nan)), index=df.index)\n    return raw.ffill().fillna(0).astype(int)\n"
TAIL_LS = ("    raw = pd.Series(np.where(enter, 1.0, np.where(leave, 0.0, np.nan)), index=df.index)\n    pos = raw.ffill().fillna(0)\n"
           "    raw_s = pd.Series(np.where(enter_s, 1.0, np.where(leave_s, 0.0, np.nan)), index=df.index)\n    pos_s = raw_s.ffill().fillna(0)\n    return (pos - pos_s).astype(int)\n")


def _b(days: float, iv: str, lo: int = 3) -> int:
    return max(lo, int(round(days * BPD.get(iv, 1))))


def _donchian(iv, p, short):
    n, t = _b(p["n"], iv), _b(p["trend"], iv)
    x = _b(p["n"] / 2, iv)
    code = HEAD + f"    trend = close.rolling({t}).mean()\n    top = high.rolling({n}).max().shift(1)\n    floor = low.rolling({x}).min().shift(1)\n"
    code += "    enter = (close > top) & (close > trend)\n    leave = close < floor\n"
    if short:
        code += f"    bottom = low.rolling({n}).min().shift(1)\n    ceil = high.rolling({x}).max().shift(1)\n    enter_s = (close < bottom) & (close < trend)\n    leave_s = close > ceil\n"
    rules = [f"Go long when the close breaks above the highest high of the last {n} bars and the close is above its {t}-bar average.",
             f"Exit when the close falls below the lowest low of the last {x} bars."]
    if short:
        rules.append(f"Go short on the mirror image: close below the {n}-bar low and below the {t}-bar average; cover above the {x}-bar high.")
    return code + (TAIL_LS if short else TAIL_LONG), f"Channel breakout {n}/{x} with {t}-bar trend filter", rules


def _ema_cross(iv, p, short):
    f, s, t = _b(p["fast"], iv), _b(p["slow"], iv), _b(p["trend"], iv)
    code = HEAD + f"    fast = close.ewm(span={f}, adjust=False).mean()\n    slow = close.ewm(span={s}, adjust=False).mean()\n    trend = close.rolling({t}).mean()\n"
    code += "    enter = (fast > slow) & (close > trend)\n    leave = fast < slow\n"
    if short:
        code += "    enter_s = (fast < slow) & (close < trend)\n    leave_s = fast > slow\n"
    rules = [f"Go long when the {f}-bar EMA is above the {s}-bar EMA and the close is above the {t}-bar average.", f"Exit when the {f}-bar EMA drops back under the {s}-bar EMA."]
    if short:
        rules.append("Go short on the mirror image (fast under slow and close under the trend average).")
    return code + (TAIL_LS if short else TAIL_LONG), f"EMA {f}/{s} crossover gated by {t}-bar trend", rules


def _rsi_pullback(iv, p, short):
    n, t = _b(p["rsi"], iv, 5), _b(p["trend"], iv)
    lo, hi = p["lo"], p["hi"]
    code = HEAD + RSI.format(n=n) + f"    trend = close.rolling({t}).mean()\n    enter = (rsi < {lo}) & (close > trend)\n    leave = rsi > {hi}\n"
    if short:
        code += f"    enter_s = (rsi > {100 - lo}) & (close < trend)\n    leave_s = rsi < {100 - hi}\n"
    rules = [f"Buy dips inside an uptrend: go long when RSI({n}) falls below {lo} while the close is above its {t}-bar average.", f"Exit when RSI({n}) recovers above {hi}."]
    if short:
        rules.append(f"Short rallies inside a downtrend: RSI above {100 - lo} with the close under the trend average; cover under {100 - hi}.")
    return code + (TAIL_LS if short else TAIL_LONG), f"RSI({n}) dip-buy in {t}-bar uptrend", rules


def _zscore(iv, p, short):
    n, z = _b(p["n"], iv, 5), p["z"]
    code = HEAD + f"    mid = close.rolling({n}).mean()\n    sd = close.rolling({n}).std()\n    zs = (close - mid) / (sd + 1e-12)\n    enter = zs < -{z}\n    leave = zs > 0\n"
    if short:
        code += f"    enter_s = zs > {z}\n    leave_s = zs < 0\n"
    rules = [f"Mean reversion: go long when the close is more than {z} standard deviations below its {n}-bar mean.", "Exit when it returns to the mean."]
    if short:
        rules.append(f"Short when more than {z} standard deviations above the mean; cover at the mean.")
    return code + (TAIL_LS if short else TAIL_LONG), f"Z-score reversion ({n} bars, {z} sd)", rules


def _mom_vol(iv, p, short):
    l, v = _b(p["look"], iv), _b(p["vol"], iv, 10)
    code = HEAD + (f"    ret = close.pct_change()\n    vol = ret.rolling({v}).std()\n    calm = vol < vol.rolling({v * 4}).median()\n    mom = close.pct_change({l})\n"
                   "    enter = (mom > 0) & calm\n    leave = mom < 0\n")
    if short:
        code += "    enter_s = (mom < 0) & calm\n    leave_s = mom > 0\n"
    rules = [f"Momentum in calm markets: go long when the {l}-bar return is positive and recent volatility ({v} bars) is below its own long-run median.", "Exit when the momentum turns negative."]
    if short:
        rules.append("Short when momentum is negative in calm conditions; cover when it turns positive.")
    return code + (TAIL_LS if short else TAIL_LONG), f"{l}-bar momentum with a calm-volatility filter", rules


def _vol_break(iv, p, short):
    n, x = _b(p["n"], iv), _b(p["n"] / 2, iv)
    k = p["k"]
    code = HEAD + (f"    top = high.rolling({n}).max().shift(1)\n    busy = volume > {k} * volume.rolling({n}).mean()\n    slow = close.ewm(span={x}, adjust=False).mean()\n"
                   "    enter = (close > top) & busy\n    leave = close < slow\n")
    if short:
        code += f"    bottom = low.rolling({n}).min().shift(1)\n    enter_s = (close < bottom) & busy\n    leave_s = close > slow\n"
    rules = [f"Breakout on real participation: go long when the close exceeds the {n}-bar high and volume is above {k}x its {n}-bar average.", f"Exit when the close falls under its {x}-bar EMA."]
    if short:
        rules.append("Short the mirror image: close below the lowest low with heavy volume; cover above the EMA.")
    return code + (TAIL_LS if short else TAIL_LONG), f"Volume-confirmed {n}-bar breakout", rules


ARCH = {
    "channel_breakout": (_donchian, [dict(n=n, trend=t) for n, t in itertools.product((10, 20, 40), (50, 100))]),
    "ema_cross": (_ema_cross, [dict(fast=f, slow=s, trend=t) for f, s, t in ((5, 20, 50), (10, 30, 100), (10, 50, 100), (20, 60, 150), (8, 21, 60))]),
    "rsi_pullback": (_rsi_pullback, [dict(rsi=14, trend=t, lo=lo, hi=hi) for t, lo, hi in itertools.product((50, 100), (30, 40), (55, 65))]),
    "zscore_reversion": (_zscore, [dict(n=n, z=z) for n, z in itertools.product((10, 20, 40), (1.5, 2.0))]),
    "momentum_calm": (_mom_vol, [dict(look=l, vol=v) for l, v in itertools.product((10, 20, 60), (10, 20))]),
    "volume_breakout": (_vol_break, [dict(n=n, k=k) for n, k in itertools.product((10, 20, 40), (1.2, 1.8))]),
}
STOPS = (None, 3.0)                                       # percent; None = exit by rule only
EXPLAIN = {"channel_breakout": "trend-following", "ema_cross": "trend-following", "rsi_pullback": "pullback in a trend", "zscore_reversion": "mean reversion",
           "momentum_calm": "momentum with a risk filter", "volume_breakout": "breakout with volume confirmation"}


def build_candidates(interval: str, allow_short: bool, only: set[str] | None = None) -> list[dict]:
    out = []
    for name, (fn, grid) in ARCH.items():
        if only and name not in only:
            continue
        for p in grid:
            for stop in STOPS:
                code, title, rules = fn(interval, p, allow_short)
                out.append(dict(arch=name, family=EXPLAIN[name], params=p, code=code, title=title, rules=rules + ([f"Hard stop {stop:g}% from the entry price."] if stop else []),
                                exits=engine.clean_exits(dict(stop_pct=stop))))
    for c in out:
        ast_guard.validate(c["code"])
    return out


def search(setup: dict, top: int = 4, only: set[str] | None = None, timeout_each: float = 5.0) -> dict:
    """Backtest every composed candidate on the TRAIN window only and rank. -> dict(ranked=[candidate+train stats], tried=int, failed=int, trial_sharpes=[...])"""
    df, k = setup["df"], setup["k"]
    prefs = setup.get("preferences") or {}
    style = prefs.get("style", "any")
    families = {"momentum": {"channel_breakout", "ema_cross", "momentum_calm", "volume_breakout"},
                "mean-reversion": {"rsi_pullback", "zscore_reversion"}}.get(style)
    if families:
        only = families if only is None else families & only
        if not only:
            return dict(ranked=[], tried=0, failed=0, usable=0, trial_sharpes=[])
    cands = build_candidates(setup["interval"], setup["allow_short"], only)
    outs = sandbox.run_batch([c["code"] for c in cands], df, timeout=timeout_each)
    rows, sharpes, failed = [], [], 0
    wins = {"train": (max(100, 1), k - 1)}
    for c, o in zip(cands, outs):
        if not o.get("ok"):
            failed += 1
            continue
        sink: dict = {}
        res = engine.backtest_positions(df, o["positions"], c["exits"], k, setup["cost"], setup["periods"], setup["fmt"], setup["allow_short"], windows=wins, keep_series=False, ret_sink=sink)
        m = res["train"]["metrics"]
        sh = engine.per_period_sharpe(sink["train_r"])
        sharpes.append(sh)
        rows.append(dict(c, train=dict(sharpe=m["sharpe"], total_return=m["total_return"], trades=m["trades"], max_drawdown=m["max_drawdown"]), per_period_sharpe=sh))
    ok = [r for r in rows if r["train"]["trades"] >= 8 and r["train"]["max_drawdown"] >= -float(prefs.get("max_drawdown_pct", 100)) / 100]
    ok.sort(key=lambda r: (r["train"]["sharpe"], r["train"]["total_return"]), reverse=True)
    picked, seen = [], set()
    for r in ok:                                         # diversity: best of each family first, then the next best overall
        if r["arch"] not in seen:
            picked.append(r); seen.add(r["arch"])
    picked += [r for r in ok if r not in picked]
    log.info("offline synth: tried=%d failed=%d usable=%d", len(cands), failed, len(ok))
    return dict(ranked=picked[:top], tried=len(cands), failed=failed, usable=len(ok), trial_sharpes=sharpes)
