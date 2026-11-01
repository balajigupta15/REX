"""Crypto section services (BTC / ETH / SOL vs USDT): plain-English parser, backtest and refine on real Binance candles.
Reuses the SAME engine (backtest.run), parser (nlparse), refinement (refine.refine) and metrics as the NSE section; only the data provider,
bars-per-year, the locked-period rule and the default cost differ. Rex never places orders: this is research and tracking."""
from __future__ import annotations
import math, re
from . import backtest, config, crypto_data as cd, nlparse, refine, rules

_COIN = re.compile(r"\b(bitcoin|btc|xbt|ethereum|ether|eth|solana|sol)(?:\s*/?\s*usdt|-usd)?\b", re.I)
_IV_1H = re.compile(r"\b(?:1\s?-?\s?h|1\s?-?\s?hours?|hourly|60\s?-?\s?min\w*)\b(?:\s*(?:candles?|bars?|chart|timeframe))?", re.I)
_IV_4H = re.compile(r"\b(?:4\s?-?\s?h|4\s?-?\s?hours?|four\s?-?\s?hours?)\b(?:\s*(?:candles?|bars?|chart|timeframe))?", re.I)
_IV_1D = re.compile(r"\b(?:daily|1\s?-?\s?d|1\s?-?\s?day|day)\s+(?:candles?|bars?|chart|timeframe)\b|\bdaily\b|\b1d\b", re.I)
_HOURS = re.compile(r"\b(\d+)\s*(?:hours?|hrs?|h)\b", re.I)
_DROP = ("No supported stock named", "cached NSE stocks", "Intraday timeframes")


def parse(text: str) -> dict:
    """Same rules parser as the stock section, with coin + candle interval pulled out first. Never guesses an entry rule."""
    text = (text or "").strip()
    if not text:
        raise nlparse.ParseError("Write your strategy first.")
    if len(text) > nlparse.MAX_CHARS:
        raise nlparse.ParseError(f"Keep it under {nlparse.MAX_CHARS} characters.")
    coins = []
    for m in _COIN.finditer(text):
        s = cd.normalise(m.group(1))
        if s not in coins:
            coins.append(s)
    iv, notes = None, []
    cleaned = _COIN.sub(" ", text)
    for rx, v in ((_IV_4H, "4h"), (_IV_1H, "1h"), (_IV_1D, "1d")):
        if rx.search(cleaned):
            iv = iv or v
            cleaned = rx.sub(" ", cleaned)
    hours = [int(x) for x in _HOURS.findall(cleaned)]
    if hours:
        cleaned = _HOURS.sub(lambda m: f"{m.group(1)} days", cleaned)
        iv = iv or "1h"
    out = nlparse.parse_rules(cleaned)
    out["parser"] = "rules (crypto)"
    st = out.get("strategy")
    sym = coins[0] if coins else "BTCUSDT"
    interval = iv or "1d"
    if st is None:
        out["unsupported"] = [u.replace("RELIANCE", "BTC").replace("stock", "coin") for u in out["unsupported"] if not u.startswith(_DROP)]
        out.update(symbol=sym, interval=interval, market="crypto")
        return out
    if hours:     # an "N hours" hold: convert to candles of the chosen interval
        h = int(st["exits"]["hold_days"])
        if h in hours:
            bars = h if interval == "1h" else max(1, round(h / 4)) if interval == "4h" else max(1, round(h / 24))
            st["exits"]["hold_days"] = bars
            notes.append(f"Hold of {h} hours = {bars} {interval} candle{'s' if bars != 1 else ''}")
    st["ticker"] = sym
    st["interval"] = interval
    fr = out["fields_from_text"]
    if coins:
        fr.append("ticker")
    else:
        out["assumptions"].insert(0, "No coin named: using BTCUSDT (pick another in the form)")
    if iv:
        fr.append("interval")
    else:
        out["assumptions"].append("Candle size not stated: using daily (1d) candles")
    if len(coins) > 1:
        out["unsupported"].append("One coin per backtest: using " + sym + ", ignored " + ", ".join(coins[1:]))
    if interval != "1d":
        notes.append(f"On {interval} candles every 'day' in your rule means one {interval} candle (an N-day high is the N-candle high)")
    out["assumptions"] = [a.replace("(trading days)", "(candles)") for a in out["assumptions"] + notes if not a.startswith(_DROP)]
    out["unsupported"] = [u for u in out["unsupported"] if not u.startswith(_DROP) and "cached NSE" not in u]
    s = nlparse.summarise(dict(st, ticker=sym))
    s = s.replace("trading days", "candles" if interval != "1d" else "days")
    if interval != "1d":
        s = re.sub(r"-day\b", "-candle", s)
    out["summary"] = f"{s[:-1]} on {interval} candles." if s.endswith(".") else s
    out.update(symbol=sym, interval=interval, market="crypto", cost_bps_default=cd.COST_BPS_DEFAULT)
    return out


def _thin(res: dict, limit: int = 700) -> dict:
    """Charts do not need 8,000 points: keep at most `limit` per window (metrics and trades are untouched)."""
    for w in res.values():
        n = len(w.get("dates", []))
        if n > limit:
            step = math.ceil(n / limit)
            idx = sorted(set(list(range(0, n, step)) + [n - 1]))
            for k in ("dates", "equity", "benchmark_equity"):
                w[k] = [w[k][i] for i in idx]
    return res


def _prep(symbol: str, interval: str, template: str, params: dict | None, exits: dict | None, refresh: bool = False):
    df, info = cd.load(symbol, interval, refresh=refresh)
    p, e = rules.validate(template, params or {}, exits or {})
    return df, info, p, e


def _windows(df, k: int):
    return {"train": (max(config.WARMUP_BARS, 1), k - 1), "locked": (k, len(df) - 1)}


def run_backtest(symbol: str, interval: str, template: str, params: dict | None, exits: dict | None, cost_bps: float | None = None) -> dict:
    df, info, p, e = _prep(symbol, interval, template, params, exits)
    cost = cd.COST_BPS_DEFAULT if cost_bps is None else float(cost_bps)
    iv = info["interval"]
    k = cd.split_k(df, iv)
    res = backtest.run(df, template, p, e, cost, windows=_windows(df, k), periods=cd.INTERVALS[iv]["periods"], fmt=lambda t: cd.fmt_ts(t, iv))
    info = dict(info, locked_from=cd.fmt_ts(df.index[k], iv), train_bars=k - config.WARMUP_BARS, locked_bars=len(df) - k)
    spec = dict(ticker=info["symbol"], symbol=info["symbol"], interval=iv, template=template, params=p, exits=e, cost_bps=cost, market="crypto")
    notes = ["Signals at the candle close, entries at the next candle's open, long only, one position at a time. Binance candles, UTC.",
             f"Costs: {cost:g} bp per round trip (assumption: exchange fee plus slippage). Stop is checked before take-profit inside a candle.",
             f"Locked final period ({info['locked_rule']}): {res['locked']['start']} to {res['locked']['end']}. Refinement never tunes on it.",
             "Crypto trades 24/7, so Sharpe and CAGR use " + f"{cd.INTERVALS[iv]['periods']} {iv} candles per year, not 252.",
             "Hold, stop and take-profit counts are in candles of the chosen interval." if iv != "1d" else "Hold counts are in daily candles (calendar days).",
             "Educational research on historical data, not investment advice. Rex never places orders."]
    return dict(spec=spec, data=info, result=_thin(res), notes=notes, df=df)


def run_refine(symbol: str, interval: str, template: str, params: dict | None, exits: dict | None, cost_bps: float | None = None) -> dict:
    df, info, p, e = _prep(symbol, interval, template, params, exits)
    cost = cd.COST_BPS_DEFAULT if cost_bps is None else float(cost_bps)
    iv = info["interval"]
    k = cd.split_k(df, iv)
    r = refine.refine(df, template, p, e, cost, split_k=k, periods=cd.INTERVALS[iv]["periods"], fmt=lambda t: cd.fmt_ts(t, iv))
    _thin(r["original"]["result"]); _thin(r["refined"]["result"])
    info = dict(info, locked_from=cd.fmt_ts(df.index[k], iv))
    def wording(s):
        if isinstance(s, list):
            return [wording(x) for x in s]
        if not isinstance(s, str) or iv == "1d":
            return s
        return s.replace("locked final year", "locked final period").replace("final year", "final period").replace("locked year", "locked final period").replace("Locked year", "Locked final period")
    r["verdict"] = {a: wording(b) for a, b in r["verdict"].items()}
    spec = dict(ticker=info["symbol"], symbol=info["symbol"], interval=iv, template=template, cost_bps=cost, market="crypto")
    return dict(spec=spec, data=info, df=df, **r)
