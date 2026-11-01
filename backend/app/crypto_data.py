"""Real crypto candles + live prices for the SEPARATE Crypto section (BTC, ETH, SOL against USDT).

Source order for every request: Binance public REST (api.binance.com, no key) -> data-api.binance.vision (Binance's public market-data mirror)
-> yfinance (BTC-USD, ETH-USD, SOL-USD) when both Binance hosts are blocked (some countries / clouds geo-block Binance). Candle history is cached to
data_cache/crypto/<SYMBOL>_<interval>.csv (closed candles only, UTC) and topped up incrementally. Nothing here is synthetic.
The crypto market is 24/7: there is no open/closed state. Timestamps are UTC (naive, bar OPEN time)."""
from __future__ import annotations
import os, threading, time, warnings
from datetime import datetime, timezone
import pandas as pd
from . import config, data

HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")
SYMBOLS = {"BTCUSDT": dict(name="Bitcoin", short="BTC", yf="BTC-USD"), "ETHUSDT": dict(name="Ethereum", short="ETH", yf="ETH-USD"),
           "SOLUSDT": dict(name="Solana", short="SOL", yf="SOL-USD")}
ALIASES = {"BITCOIN": "BTCUSDT", "BTC": "BTCUSDT", "XBT": "BTCUSDT", "ETHEREUM": "ETHUSDT", "ETHER": "ETHUSDT", "ETH": "ETHUSDT", "SOLANA": "SOLUSDT", "SOL": "SOLUSDT"}
# bars per year (24/7: 365 days) feed Sharpe / CAGR; history is how much we keep; locked = how the final test period is chosen
INTERVALS = {"1d": dict(label="daily", ms=86_400_000, periods=365, history_days=1830, locked="the last 365 days"),
             "4h": dict(label="4-hour", ms=14_400_000, periods=365 * 6, history_days=1100, locked="the last ~20% of candles"),
             "1h": dict(label="1-hour", ms=3_600_000, periods=365 * 24, history_days=365, locked="the last ~20% of candles")}
COST_BPS_DEFAULT = 10.0           # ASSUMPTION: round-trip exchange fee + slippage for liquid USDT pairs
QUOTE_TTL = 1.5
_LOCK = threading.RLock()
_quote_cache: dict[str, tuple[float, dict]] = {}
_forming_cache: dict[tuple, tuple[float, dict | None]] = {}
_fail_until = 0.0                 # after a failed top-up, do not retry the network for 30 s (the live tracker polls every few seconds)
state = dict(provider=None, last_error=None, last_ok=None, attempts=[])      # what the last network call did (shown in the UI)


class CryptoError(data.DataError):
    pass


def http_get(url: str, params: dict | None = None, timeout: float = 8.0):
    """JSON GET. Tests replace this with a mock."""
    import httpx
    r = httpx.get(url, params=params, timeout=timeout, headers={"User-Agent": "rex-lite/0.1"})
    r.raise_for_status()
    return r.json()


def network_allowed() -> bool:
    return os.getenv("REX_CRYPTO_OFFLINE", "").strip().lower() in ("", "0", "false", "no")


def normalise(sym: str) -> str:
    t = (sym or "").strip().upper().replace("/", "").replace("-", "").replace("_", "")
    if t.endswith("USD") and not t.endswith("USDT"):
        t += "T"                      # BTC-USD -> BTCUSDT
    if t in ALIASES:
        t = ALIASES[t]
    elif t + "USDT" in SYMBOLS:
        t += "USDT"
    if t not in SYMBOLS:
        raise CryptoError(f"Unsupported coin '{sym}'. Crypto section supports BTC, ETH and SOL (USDT pairs).")
    return t


def check_interval(interval: str) -> str:
    iv = (interval or "1d").strip().lower()
    iv = {"d": "1d", "daily": "1d", "day": "1d", "h": "1h", "hourly": "1h", "4hour": "4h", "1hour": "1h"}.get(iv, iv)
    if iv not in INTERVALS:
        raise CryptoError(f"Unsupported candle interval '{interval}'. Use 1d, 4h or 1h.")
    return iv


def _note(provider: str | None, err: str | None = None) -> None:
    state["attempts"] = (state["attempts"] + [(provider, err)])[-6:]
    if err is None:
        state.update(provider=provider, last_error=None, last_ok=time.time())
    else:
        state["last_error"] = err


def _binance(path: str, params: dict):
    errs = []
    for h in HOSTS:
        try:
            out = http_get(h + path, params)
            _note(h.split("//")[1])
            return out, h.split("//")[1]
        except Exception as e:      # 451 geo-block, timeouts, DNS ...
            errs.append(f"{h.split('//')[1]}: {type(e).__name__} {str(e)[:80]}")
            _note(h.split("//")[1], errs[-1])
    raise CryptoError("Binance unreachable (" + "; ".join(errs) + ")")


def _frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame([[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), int(r[6])] for r in rows],
                      columns=["t", "Open", "High", "Low", "Close", "Volume", "close_ms"])
    df.index = pd.to_datetime(df.pop("t"), unit="ms")
    df.index.name = "Date"
    return df


def _klines(symbol: str, interval: str, start_ms: int | None, end_ms: int | None = None) -> tuple[pd.DataFrame, str]:
    """Paged klines (1000 per request) from Binance. Returns (frame incl. close_ms, host)."""
    out, host, cur = [], None, start_ms
    for _ in range(60):
        params = dict(symbol=symbol, interval=interval, limit=1000)
        if cur is not None:
            params["startTime"] = cur
        if end_ms is not None:
            params["endTime"] = end_ms
        rows, host = _binance("/api/v3/klines", params)
        if not rows:
            break
        out += rows
        if len(rows) < 1000:
            break
        cur = int(rows[-1][0]) + INTERVALS[interval]["ms"]
    if not out:
        raise CryptoError(f"Binance returned no candles for {symbol} {interval}")
    return _frame(out), host


def _yf_klines(symbol: str, interval: str) -> pd.DataFrame:
    import yfinance as yf
    tk = SYMBOLS[symbol]["yf"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if interval == "1d":
            h = yf.download(tk, period="5y", interval="1d", auto_adjust=False, progress=False, threads=False)
        else:
            h = yf.download(tk, period="730d", interval="1h", auto_adjust=False, progress=False, threads=False)
    if h is None or not len(h):
        raise CryptoError(f"yfinance returned no data for {tk}")
    if isinstance(h.columns, pd.MultiIndex):
        h.columns = h.columns.get_level_values(0)
    h = h[["Open", "High", "Low", "Close", "Volume"]].dropna()
    idx = pd.to_datetime(h.index)
    h.index = (idx.tz_convert("UTC") if idx.tz is not None else idx).tz_localize(None)
    h.index.name = "Date"
    if interval == "4h":
        h = h.resample("4h").agg(dict(Open="first", High="max", Low="min", Close="last", Volume="sum")).dropna()
    h["close_ms"] = (h.index.astype("int64") // 10**6 + INTERVALS[interval]["ms"] - 1)
    return h


def _path(sym: str, iv: str):
    return config.DATA_CACHE / "crypto" / f"{sym}_{iv}.csv"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _closed(df: pd.DataFrame, interval: str, now_ms: int | None = None) -> pd.DataFrame:
    """Only candles that have finished (the forming candle is handled separately by `forming()`)."""
    ms = INTERVALS[interval]["ms"]
    cut = pd.to_datetime((now_ms or _now_ms()) - ms, unit="ms")
    return df[df.index <= cut]


def _read(p) -> pd.DataFrame:
    return pd.read_csv(p, index_col=0, parse_dates=True)[["Open", "High", "Low", "Close", "Volume"]]


def _from_store(sym: str, iv: str, spec: dict, refresh: bool):
    """Pipeline store first. Used when it is up to date (latest closed candle present) or when the network is disabled; otherwise the CSV flow tops up as before."""
    if refresh:
        return None
    try:
        from pipeline import store as ps
        part = ps.partition("crypto", sym, iv)
        if not part or part["rows"] < config.MIN_BARS:
            return None
        df = ps.read("crypto", sym, iv)
        if df is None:
            return None
        df = df[~df.index.duplicated()].sort_index()
        want_last = pd.to_datetime((_now_ms() // spec["ms"]) * spec["ms"] - spec["ms"], unit="ms")
        if network_allowed() and df.index[-1] < want_last:
            return None
        df = _closed(df, iv) if network_allowed() else df
        if len(df) < config.MIN_BARS:
            return None
        info = dict(symbol=sym, ticker=sym, coin=SYMBOLS[sym]["name"], interval=iv, interval_label=spec["label"], bars=int(len(df)),
                    first=fmt_ts(df.index[0], iv), last=fmt_ts(df.index[-1], iv), source="pipeline store (parquet)", timezone="UTC", market="crypto",
                    locked_rule=spec["locked"], periods_per_year=spec["periods"], cache_file_mtime=part["updated_at"][:16])
        return df, info
    except Exception:
        return None


def load(symbol: str, interval: str = "1d", refresh: bool = False) -> tuple[pd.DataFrame, dict]:
    """(closed-candle OHLCV frame, info). Cache first; tops up from the network when a closed candle is missing; stale cache if the network fails."""
    global _fail_until
    sym, iv = normalise(symbol), check_interval(interval)
    spec = INTERVALS[iv]
    hit = _from_store(sym, iv, spec, refresh)
    if hit is not None:
        return hit
    p = _path(sym, iv)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        cached = _read(p) if p.exists() else None
        source = "cache"
        if cached is not None:
            cached = cached[~cached.index.duplicated()].sort_index()
        want_last = pd.to_datetime((_now_ms() // spec["ms"]) * spec["ms"] - spec["ms"], unit="ms")      # open time of the latest CLOSED candle
        need = network_allowed() and time.time() >= _fail_until and (cached is None or refresh or cached.index[-1] < want_last)
        df = cached
        if need:
            try:
                if cached is None or len(cached) < 50:
                    start = _now_ms() - spec["history_days"] * 86_400_000
                    try:
                        new, host = _klines(sym, iv, start)
                        src = host
                    except CryptoError as be:
                        new, src = _yf_klines(sym, iv), "yfinance (Binance blocked)"
                        _note("yfinance")
                else:
                    start = int(cached.index[-1].value // 10**6) + spec["ms"]
                    try:
                        new, src = _klines(sym, iv, start)
                    except CryptoError:
                        new, src = _yf_klines(sym, iv), "yfinance (Binance blocked)"
                        _note("yfinance")
                    new = new[new.index > cached.index[-1]]
                new = _closed(new, iv)[["Open", "High", "Low", "Close", "Volume"]]
                df = new if cached is None or len(cached) < 50 else pd.concat([cached, new])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                df.to_csv(p)
                source = src
            except Exception as e:
                _fail_until = time.time() + 30
                state["last_error"] = f"{type(e).__name__}: {str(e)[:120]}"
                if cached is None:
                    raise CryptoError(f"No {sym} {iv} candles: Binance and yfinance both failed ({state['last_error']}) and nothing is cached.")
                source = "cache (stale, live fetch failed)"
                df = cached
        elif cached is None:
            raise CryptoError(f"No cached {sym} {iv} candles and the network is disabled (REX_CRYPTO_OFFLINE=1).")
    df = _closed(df, iv) if network_allowed() else df
    if len(df) < config.MIN_BARS:
        raise CryptoError(f"{sym} {iv} has only {len(df)} candles; need at least {config.MIN_BARS}")
    info = dict(symbol=sym, ticker=sym, coin=SYMBOLS[sym]["name"], interval=iv, interval_label=spec["label"], bars=int(len(df)),
                first=fmt_ts(df.index[0], iv), last=fmt_ts(df.index[-1], iv), source=source, timezone="UTC", market="crypto",
                locked_rule=spec["locked"], periods_per_year=spec["periods"],
                cache_file_mtime=datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes"))
    return df, info


def fmt_ts(ts, interval: str = "1d") -> str:
    ts = pd.Timestamp(ts)
    return str(ts.date()) if interval == "1d" else ts.strftime("%Y-%m-%d %H:%M")


def split_k(df: pd.DataFrame, interval: str) -> int:
    """First bar of the LOCKED final period: last 365 calendar days for daily candles, the last ~20% of candles for 1h / 4h."""
    if interval == "1d":
        return data.split_index(df)
    return max(int(len(df) * 0.8), config.WARMUP_BARS + 50)


# ---------------------------------------------------------------- live
def _quote_yf(symbol: str) -> dict:
    import yfinance as yf
    fi = yf.Ticker(SYMBOLS[symbol]["yf"]).fast_info
    return dict(price=float(fi["last_price"]), provider="yfinance (Binance blocked)")


def quote(symbol: str, now: float | None = None) -> dict:
    """Latest traded price. Never raises: falls back to the last cached close and says so. 24/7: always `market='open 24/7'`."""
    sym = normalise(symbol)
    t = time.time()
    if now is None and sym in _quote_cache and t - _quote_cache[sym][0] < QUOTE_TTL:
        return _quote_cache[sym][1]
    out = None
    if network_allowed():
        try:
            j, host = _binance("/api/v3/ticker/price", dict(symbol=sym))
            out = dict(price=float(j["price"]), provider=host, live=True)
        except Exception:
            try:
                q = _quote_yf(sym)
                _note("yfinance"); out = dict(price=q["price"], provider=q["provider"], live=True)
            except Exception as e:
                state["last_error"] = f"{type(e).__name__}: {str(e)[:100]}"
    if out is None:
        df = None
        for iv in ("1h", "1d", "4h"):
            try:
                df = _read(_path(sym, iv)); break
            except Exception:
                continue
        if df is None:
            raise CryptoError(f"No live price and no cache for {sym}")
        out = dict(price=float(df["Close"].iloc[-1]), provider="offline cache (last close)", live=False)
    out.update(symbol=sym, time_utc=datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), ts=t, market="24/7 (always open)",
               label="Real-time (REST poll about every 3 s)" if out["live"] else "Not live: last cached close", stale=not out["live"])
    if now is None:
        _quote_cache[sym] = (t, out)
    return out


def forming(symbol: str, interval: str) -> dict | None:
    """The candle that is still forming now (open/high/low so far/close = live price). None when the network is unavailable."""
    if not network_allowed():
        return None
    sym, iv = normalise(symbol), check_interval(interval)
    t = time.time()
    hit = _forming_cache.get((sym, iv))
    if hit and t - hit[0] < QUOTE_TTL:
        return hit[1]
    try:
        rows, _ = _binance("/api/v3/klines", dict(symbol=sym, interval=iv, limit=2))
    except CryptoError:
        return None
    r = rows[-1]
    if int(r[6]) < _now_ms() - 1000:       # newest row already closed (the new candle has not started): nothing forming
        return None
    out = dict(ts=pd.to_datetime(int(r[0]), unit="ms"), Open=float(r[1]), High=float(r[2]), Low=float(r[3]), Close=float(r[4]), Volume=float(r[5]))
    _forming_cache[(sym, iv)] = (t, out)
    return out


def clear_cache() -> None:
    global _fail_until
    _fail_until = 0.0
    _quote_cache.clear(); _forming_cache.clear()
    state.update(provider=None, last_error=None, last_ok=None, attempts=[])


def status() -> dict:
    return dict(market="crypto", symbols=list(SYMBOLS), intervals=list(INTERVALS), network=network_allowed(), **{k: state[k] for k in ("provider", "last_error")},
                last_ok_utc=None if not state["last_ok"] else datetime.fromtimestamp(state["last_ok"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                sources="Binance api.binance.com -> data-api.binance.vision -> yfinance (BTC-USD, ETH-USD, SOL-USD)")
