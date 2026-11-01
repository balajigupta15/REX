"""Live price + NSE market state for the Live Strategy Tracker. Real yfinance data only.
Yahoo's NSE feed is delayed about 15 minutes. With REX_OFFLINE=1 (the default) nothing is fetched: the last cached close is used and
labelled as such. NSE holidays are not modelled: on a holiday yfinance simply has no new bar and the last close stays on screen."""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from . import data

try:
    IST = ZoneInfo("Asia/Kolkata")
except Exception:                      # tzdata missing (Windows): India has no DST, so a fixed offset is exact
    IST = timezone(timedelta(hours=5, minutes=30))
OPEN_T, CLOSE_T = (9, 15), (15, 30)
DELAY_NOTE = "Yahoo Finance NSE prices are delayed about 15 minutes."
QUOTE_TTL = 20.0
_cache: dict[str, tuple[float, dict]] = {}
PROVIDER = None            # tests / demos can set a callable (ticker, now) -> intraday dict to simulate a live feed


def now_ist(now: datetime | None = None) -> datetime:
    n = now or datetime.now(IST)
    return n.astimezone(IST) if n.tzinfo else n.replace(tzinfo=IST)


def market_state(now: datetime | None = None) -> dict:
    n = now_ist(now)
    o = n.replace(hour=OPEN_T[0], minute=OPEN_T[1], second=0, microsecond=0)
    c = n.replace(hour=CLOSE_T[0], minute=CLOSE_T[1], second=0, microsecond=0)
    if n.weekday() >= 5:
        state, label = "weekend", "Closed (weekend)"
    elif n < o:
        state, label = "pre_open", "Closed (opens 09:15 IST)"
    elif n > c:
        state, label = "after_close", "Closed (closed at 15:30 IST)"
    else:
        state, label = "open", "Open (09:15 to 15:30 IST)"
    d = n + timedelta(days=1) if state in ("after_close", "weekend") else n
    while d.weekday() >= 5:
        d += timedelta(days=1)
    nxt = d.replace(hour=OPEN_T[0], minute=OPEN_T[1], second=0, microsecond=0)
    return dict(open=state == "open", state=state, label=label, ist_now=n.strftime("%Y-%m-%d %H:%M IST"), ist_date=str(n.date()),
                next_open=None if state == "open" else nxt.strftime("%a %d %b %H:%M IST"),
                holidays_note="NSE holidays are not modelled.")


def _intraday(t: str, now: datetime | None) -> dict | None:
    """Latest 1m (else 5m) bar of TODAY from yfinance. None on any failure."""
    if PROVIDER is not None:
        return PROVIDER(t, now)
    import warnings, yfinance as yf
    today = now_ist(now).date()
    for period, interval in (("1d", "1m"), ("5d", "5m")):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                h = yf.Ticker(t).history(period=period, interval=interval, auto_adjust=False)
            if h is None or not len(h):
                continue
            idx = pd.to_datetime(h.index)
            idx = idx.tz_convert(IST) if idx.tz is not None else idx.tz_localize(IST)
            h = h.copy(); h.index = idx
            h = h[[d.date() == today for d in h.index]].dropna(subset=["Close"])
            if not len(h):
                continue
            return dict(price=float(h["Close"].iloc[-1]), price_time=h.index[-1].strftime("%Y-%m-%d %H:%M IST"), day_open=float(h["Open"].iloc[0]),
                        day_high=float(h["High"].max()), day_low=float(h["Low"].min()), interval=interval)
        except Exception:
            continue
    return None


def snapshot(ticker: str, now: datetime | None = None) -> dict:
    """{quote, bars (COMPLETED daily bars only), live_bar (today's forming bar or None), market, info}. Never raises for a missing live feed:
    it falls back to the last close and says so. Raises data.DataError only if there is no price history at all."""
    t = data.normalise(ticker)
    if now is None and t in _cache and time.time() - _cache[t][0] < QUOTE_TTL:
        return _cache[t][1]
    mk = market_state(now)
    df, info = data.load(t)
    today = pd.Timestamp(mk["ist_date"])
    if (not data.is_offline()) and not mk["open"] and df.index[-1] == today and info.get("source") == "cache":
        # after the close, a cache saved mid-session holds a PARTIAL bar for today: re-fetch once
        try:
            df, info = data.load(t, refresh=True)
        except data.DataError:
            pass
    live = None
    note = None
    if mk["open"] and (PROVIDER is not None or not data.is_offline()):
        live = _intraday(t, now)
        if live is None:
            note = "live intraday price unavailable right now (yfinance); showing the last daily close"
    partial_today = bool(len(df) and df.index[-1] == today and mk["open"])
    bars = df.iloc[:-1] if partial_today else df
    last_close = float(bars["Close"].iloc[-1])
    last_date = bars.index[-1]
    live_bar = None
    if live:
        price, ptime = live["price"], live["price_time"]
        source = f"yfinance {live.get('interval', '1m')} intraday (delayed ~15 min)"
        live_bar = dict(date=mk["ist_date"], open=live["day_open"], high=max(live["day_high"], price), low=min(live["day_low"], price), close=price)
        stale = False
    else:
        price, ptime = last_close, f"{last_date.date()} 15:30 IST (close)"
        if data.is_offline():
            source = "last close in the bundled offline cache (REX_OFFLINE=1)"
        elif mk["open"]:
            source = "last daily close (live intraday feed unavailable)"
        else:
            source = "last daily close"
        stale = True
    q = dict(price=round(price, 2), price_time=ptime, source=source, stale=stale, delay_note=DELAY_NOTE, note=note, last_close=round(last_close, 2),
             last_close_date=str(last_date.date()), day_open=None if not live_bar else round(live_bar["open"], 2),
             day_high=None if not live_bar else round(live_bar["high"], 2), day_low=None if not live_bar else round(live_bar["low"], 2))
    out = dict(quote=q, bars=bars, live_bar=live_bar, market=mk, info=info)
    if now is None:
        _cache[t] = (time.time(), out)
    return out


def clear_cache() -> None:
    _cache.clear()
