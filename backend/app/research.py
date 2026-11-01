"""Stock Research Brief + Scout. Rule-based, from the cached/live daily bars only. No LLM, no forecasts."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from . import config, data

DISCLAIMER = "Descriptive statistics of past prices. Not a forecast, not investment advice."


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def _weekly(s: pd.Series) -> list:
    w = s.resample("W-FRI").last().dropna()
    return w


def _pivots(df: pd.DataFrame, look: int = 5, bars: int = 500):
    d = df.tail(bars)
    hi, lo = d["High"].values, d["Low"].values
    ph, pl = [], []
    for i in range(look, len(d) - look):
        if hi[i] == hi[i - look:i + look + 1].max():
            ph.append((d.index[i], hi[i]))
        if lo[i] == lo[i - look:i + look + 1].min():
            pl.append((d.index[i], lo[i]))
    return ph, pl


def _cluster(points, tol=0.015):
    pts = sorted(points, key=lambda p: p[1])
    groups = []
    for dt, px in pts:
        if groups and abs(px / np.mean([g[1] for g in groups[-1]]) - 1) <= tol:
            groups[-1].append((dt, px))
        else:
            groups.append([(dt, px)])
    return [dict(level=_r(np.mean([g[1] for g in grp])), touches=len(grp), last_touch=str(max(g[0] for g in grp).date())) for grp in groups]


def levels(df: pd.DataFrame) -> dict:
    ph, pl = _pivots(df)
    cl = _cluster(ph + pl)
    px = float(df["Close"].iloc[-1])
    sup = sorted([c for c in cl if c["level"] < px and c["touches"] >= 2], key=lambda c: -c["level"])[:3]
    res = sorted([c for c in cl if c["level"] > px and c["touches"] >= 2], key=lambda c: c["level"])[:3]
    for c in sup + res:
        c["distance_pct"] = _r((c["level"] / px - 1) * 100)
    return dict(close=_r(px), support=sup, resistance=res,
                method="Swing highs/lows (5 bars each side) over the last ~2 years, merged when within 1.5%; only levels touched 2+ times are listed.")


def earnings(t: str, df: pd.DataFrame, offline: bool) -> dict:
    p = config.DATA_CACHE / f"earnings_{t}.json"
    raw = None
    if p.exists():
        raw = json.loads(p.read_text())
    elif not offline:
        try:
            import warnings, yfinance as yf
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                e = yf.Ticker(t).get_earnings_dates(limit=24)
            if e is not None and len(e):
                rows = [dict(date=str(ts.date()), eps_estimate=_r(r["EPS Estimate"], 4), eps_reported=_r(r["Reported EPS"], 4), surprise_pct=_r(r["Surprise(%)"]))
                        for ts, r in e.iterrows()]
                raw = dict(ticker=t, fetched=str(pd.Timestamp.today().date()), source="yfinance get_earnings_dates", rows=rows)
                p.write_text(json.dumps(raw))
        except Exception:
            raw = None
    if not raw or not raw.get("rows"):
        return dict(available=False, reactions=[], summary=None,
                    note=f"No earnings dates available for {t} from yfinance" + (" in the offline cache." if offline else "."))
    out = []
    for r in raw["rows"]:
        if r.get("eps_reported") is None:
            continue
        d = pd.Timestamp(r["date"]).normalize()
        i = int(df.index.searchsorted(d, side="left"))
        if i < 1 or i + 1 >= len(df):
            continue
        # NSE companies report before, during or after market hours and yfinance gives only a date,
        # so use a 2-day window: close before the announcement date -> close after the next session.
        react = df["Close"].iloc[i + 1] / df["Close"].iloc[i - 1] - 1
        out.append(dict(date=r["date"], eps_estimate=r.get("eps_estimate"), eps_reported=r.get("eps_reported"),
                        surprise_pct=r.get("surprise_pct"), reaction_pct=_r(react * 100)))
    out.sort(key=lambda x: x["date"], reverse=True)
    summ = None
    if out:
        mv = [abs(x["reaction_pct"]) for x in out]
        beats = [x["reaction_pct"] for x in out if (x["surprise_pct"] or 0) > 0]
        miss = [x["reaction_pct"] for x in out if (x["surprise_pct"] or 0) < 0]
        summ = dict(n=len(out), avg_abs_move_pct=_r(np.mean(mv)), max_abs_move_pct=_r(max(mv)),
                    up=sum(1 for x in out if x["reaction_pct"] > 0), down=sum(1 for x in out if x["reaction_pct"] < 0),
                    avg_after_beat_pct=_r(np.mean(beats)) if len(beats) >= 3 else None, n_beats=len(beats),
                    avg_after_miss_pct=_r(np.mean(miss)) if len(miss) >= 3 else None, n_misses=len(miss))
    return dict(available=bool(out), reactions=out, summary=summ, fetched=raw.get("fetched"), source=raw.get("source"),
                note="Dates and EPS come from yfinance and are unverified against exchange filings. Reaction = close the session before the announcement date to the close after the next session (2-day window), because the timing of results within the day is not in the data.")


def brief(ticker: str) -> dict:
    t = data.normalise(ticker)
    df, info = data.load(t)
    if len(df) < 260:
        raise data.DataError(f"{t}: only {len(df)} bars; need at least a year for a brief.")
    c = df["Close"]
    ret = c.pct_change().dropna()
    px = float(c.iloc[-1])
    s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    tot = px / float(c.iloc[0]) - 1
    def back(n):
        return _r((px / float(c.iloc[-n - 1]) - 1) * 100) if len(c) > n else None
    hi52, lo52 = float(c.tail(252).max()), float(c.tail(252).min())
    above200 = px > float(s200.iloc[-1]); above50 = px > float(s50.iloc[-1]); golden = float(s50.iloc[-1]) > float(s200.iloc[-1])
    label = "uptrend" if above200 and above50 and golden else ("downtrend" if (not above200) and (not above50) and (not golden) else "mixed")
    dd = c / c.cummax() - 1
    trough = dd.idxmin(); peak = c.loc[:trough].idxmax()
    vol_all = float(ret.std() * np.sqrt(252) * 100)
    r20 = ret.rolling(20).std() * np.sqrt(252) * 100
    cur20 = float(r20.iloc[-1]); pct_rank = float((r20.dropna() <= cur20).mean() * 100)
    # biggest moves
    vr = df["Volume"] / df["Volume"].shift(1).rolling(20, min_periods=10).mean()
    mv = pd.DataFrame({"ret": ret * 100, "vr": vr}).dropna()
    def rows(s):
        return [dict(date=str(i.date()), return_pct=_r(r.ret), volume_ratio=_r(r.vr), close=_r(c.loc[i])) for i, r in s.iterrows()]
    big_up, big_dn = rows(mv.nlargest(5, "ret")), rows(mv.nsmallest(5, "ret"))
    big_all = mv.reindex(mv["ret"].abs().sort_values(ascending=False).index).head(10)
    lv = levels(df)
    er = earnings(t, df, data.is_offline())
    w = _weekly(c); w50 = s50.reindex(w.index); w200 = s200.reindex(w.index)
    wdd = dd.reindex(w.index); wvol = r20.reindex(w.index)
    series = [dict(date=str(i.date()), close=_r(w.loc[i]), sma50=_r(w50.loc[i]), sma200=_r(w200.loc[i]), drawdown_pct=_r(wdd.loc[i] * 100), vol20=_r(wvol.loc[i], 1)) for i in w.index]
    tag = t.replace(".NS", "")
    para = []
    para.append(f"{tag} closed at {px:,.2f} on {df.index[-1].date()}. Over {yrs:.1f} years it moved {tot*100:+.0f}% ({((1+tot)**(1/yrs)-1)*100:+.1f}% a year). "
                f"Right now the trend is {label}: price is {'above' if above200 else 'below'} its 200-day average, {'above' if above50 else 'below'} its 50-day, and the 50-day is {'above' if golden else 'below'} the 200-day.")
    para.append(f"It sits {abs((px/hi52-1)*100):.1f}% below its 1-year high ({hi52:,.2f}) and {((px/lo52-1)*100):.1f}% above its 1-year low ({lo52:,.2f}). "
                f"Last 1 / 3 / 6 / 12 months: {back(21):+.1f}% / {back(63):+.1f}% / {back(126):+.1f}% / {back(252):+.1f}%." if len(c) > 252 else "")
    para.append(f"Typical volatility is {vol_all:.0f}% a year; the last 20 days ran at {cur20:.0f}% (higher than {pct_rank:.0f}% of its own history). "
                f"Its worst fall was {dd.min()*100:.0f}% (peak {peak.date()} to trough {trough.date()}), so a holder should expect drawdowns of that size, not just the average return.")
    ls, lr = lv["support"], lv["resistance"]
    para.append("Nearest support: " + (", ".join(f"{x['level']:,.0f} ({x['touches']} touches)" for x in ls[:2]) or "none found") + ". Nearest resistance: " +
                (", ".join(f"{x['level']:,.0f} ({x['touches']} touches)" for x in lr[:2]) or "none found (price is near its highs)") + ".")
    if er["available"] and er["summary"]:
        s = er["summary"]
        para.append(f"Across the last {s['n']} results it moved {s['avg_abs_move_pct']:.1f}% on average in the 2-day window around the date (largest {s['max_abs_move_pct']:.1f}%); {s['up']} up, {s['down']} down.")
    else:
        para.append("Earnings reactions are not available for this stock.")
    return dict(ticker=t, symbol=tag, as_of=str(df.index[-1].date()), data=info, close=_r(px), bars=len(df), years=_r(yrs, 1),
                trend=dict(label=label, total_return_pct=_r(tot * 100, 1), cagr_pct=_r(((1 + tot) ** (1 / yrs) - 1) * 100, 1),
                           ret_1m=back(21), ret_3m=back(63), ret_6m=back(126), ret_12m=back(252),
                           above_50dma=bool(above50), above_200dma=bool(above200), golden_cross=bool(golden),
                           sma50=_r(s50.iloc[-1]), sma200=_r(s200.iloc[-1]), high_52w=_r(hi52), low_52w=_r(lo52),
                           pct_from_high=_r((px / hi52 - 1) * 100), pct_from_low=_r((px / lo52 - 1) * 100)),
                volatility=dict(annualised_pct=_r(vol_all, 1), last_20d_pct=_r(cur20, 1), percentile_of_history=_r(pct_rank, 0),
                                max_drawdown_pct=_r(dd.min() * 100, 1), max_dd_peak=str(peak.date()), max_dd_trough=str(trough.date()),
                                current_drawdown_pct=_r(dd.iloc[-1] * 100, 1), avg_daily_range_pct=_r(((df["High"] - df["Low"]) / c).tail(60).mean() * 100),
                                worst_day_pct=_r(ret.min() * 100), best_day_pct=_r(ret.max() * 100)),
                biggest_moves=dict(up=big_up, down=big_dn), levels=lv, earnings=er,
                series=series, summary=[p for p in para if p], disclaimer=DISCLAIMER)


# ---------------------------------------------------------------- Scout
SCOUT_RULES = [
    "Trend (0-40): +15 close above the 200-day average, +10 above the 50-day, +10 50-day above 200-day, +5 200-day average rising over the last 20 bars",
    "Momentum (0-40): 3-month return scaled from -20% (0) to +20% (20) plus 6-month return scaled from -30% (0) to +30% (20)",
    "Volume (0-20): 5-day average volume vs the prior 60 days. Neutral = 10. A spike (>=1.3x) on a rising 5 days adds up to +10, on a falling 5 days takes away up to 10; very dry volume (<0.7x) takes 3 away",
    "Stretch penalty: -5 if the close is more than 12% above the 50-day average (chasing risk)",
    "Labels: 70+ strong, 50-69 constructive, 30-49 neutral, below 30 weak. A ranking of past behaviour, not a prediction.",
]


def _scout_one(t: str) -> dict:
    df, info = data.load(t)
    c, v = df["Close"], df["Volume"]
    px = float(c.iloc[-1])
    s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
    a50, a200 = float(s50.iloc[-1]), float(s200.iloc[-1])
    rising = a200 > float(s200.iloc[-21])
    trend = 15 * (px > a200) + 10 * (px > a50) + 10 * (a50 > a200) + 5 * bool(rising)
    r3, r6 = px / float(c.iloc[-64]) - 1, px / float(c.iloc[-127]) - 1
    mom = 20 * (np.clip(r3 / 0.20, -1, 1) + 1) / 2 + 20 * (np.clip(r6 / 0.30, -1, 1) + 1) / 2
    vr = float(v.tail(5).mean() / v.iloc[-65:-5].mean())
    r5 = px / float(c.iloc[-6]) - 1
    if vr >= 1.3:
        k = min((vr - 1) / 1.0, 1.0)
        vol = 10 + 10 * k if r5 > 0 else 10 - 10 * k
    else:
        vol = 7 if vr < 0.7 else 10
    ext = px / a50 - 1
    pen = 5 if ext > 0.12 else 0
    score = max(0.0, min(100.0, trend + mom + vol - pen))
    label = "strong" if score >= 70 else "constructive" if score >= 50 else "neutral" if score >= 30 else "weak"
    reasons = []
    reasons.append(f"Price is {'above' if px > a200 else 'below'} the 200-day and {'above' if px > a50 else 'below'} the 50-day average; the 50-day is {'above' if a50 > a200 else 'below'} the 200-day.")
    reasons.append(f"3-month return {r3*100:+.1f}%, 6-month {r6*100:+.1f}%.")
    if vr >= 1.3:
        reasons.append(f"Volume spike: the last 5 sessions averaged {vr:.1f}x the prior 60 days while price is {'up' if r5 > 0 else 'down'} {abs(r5)*100:.1f}% over those 5 days ({'accumulation-like' if r5 > 0 else 'distribution-like'}).")
    elif vr < 0.7:
        reasons.append(f"Volume is dry ({vr:.1f}x the prior 60 days): moves lack participation.")
    else:
        reasons.append(f"Volume is normal ({vr:.1f}x the prior 60 days).")
    if pen:
        reasons.append(f"Extended: {ext*100:.0f}% above the 50-day average, so chasing here carries pullback risk.")
    return dict(ticker=t, symbol=t.replace(".NS", ""), score=_r(score, 1), label=label, close=_r(px), as_of=str(df.index[-1].date()),
                components=dict(trend=_r(trend, 1), momentum=_r(mom, 1), volume=_r(vol, 1), stretch_penalty=pen),
                metrics=dict(ret_3m_pct=_r(r3 * 100, 1), ret_6m_pct=_r(r6 * 100, 1), ret_5d_pct=_r(r5 * 100, 1), volume_ratio_5d_60d=_r(vr),
                             pct_above_50dma=_r(ext * 100, 1), pct_vs_200dma=_r((px / a200 - 1) * 100, 1)),
                reasons=reasons, data_source=info.get("source"))


def scout(tickers: list[str] | None = None) -> dict:
    tickers = tickers or list(config.DEFAULT_TICKERS)
    rows, errors = [], []
    for t in tickers:
        try:
            rows.append(_scout_one(t))
        except Exception as e:  # one broken symbol must not hide the rest
            errors.append(dict(ticker=t, error=str(e)))
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return dict(as_of=max((r["as_of"] for r in rows), default=None), universe=len(tickers), ranked=rows, errors=errors, rules=SCOUT_RULES,
                disclaimer="A rule-based ranking of recent price behaviour for the cached watchlist. Not a prediction or advice.")
