"""Data providers. Each returns an OHLCV frame indexed by bar-open time (UTC-naive for crypto, session date for NSE)."""
from __future__ import annotations
import time, warnings
from datetime import date, datetime, timezone
import pandas as pd
from . import config

COLS = ["Open", "High", "Low", "Close", "Volume"]


class ProviderError(Exception):
    pass


def nse_daily(symbol: str, start: date | None) -> tuple[pd.DataFrame, str]:
    """NSE daily bars from yfinance (auto-adjusted). start=None means the full 5-year history."""
    import yfinance as yf
    kw = dict(start=str(start)) if start else dict(period=f"{config.NSE_HISTORY_YEARS}y")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(symbol, interval="1d", auto_adjust=True, progress=False, threads=False, **kw)
    if df is None or len(df) == 0:
        raise ProviderError(f"yfinance returned no rows for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[COLS].dropna(how="all")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df, "yfinance"


def _get(url: str, params: dict | None = None):
    from app import crypto_data as cd
    return cd.http_get(url, params)          # looked up at call time so tests can swap in a mock


def _to_frame(rows: list[list], t_i=0) -> pd.DataFrame:
    """rows: [open_time_ms, o, h, l, c, v, ...] in any order -> OHLCV frame indexed by bar-open time (UTC-naive)."""
    df = pd.DataFrame([[int(r[t_i]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows], columns=["t"] + COLS)
    df.index = pd.to_datetime(df.pop("t"), unit="ms")
    df.index.name = "Date"
    return df[~df.index.duplicated(keep="last")].sort_index()


def binance(symbol: str, interval: str, start_ms: int | None) -> pd.DataFrame:
    """Binance public klines (api.binance.com, then data-api.binance.vision), paged 1000 at a time."""
    from app import crypto_data as cd
    try:
        df, _ = cd._klines(symbol, interval, start_ms)
    except cd.CryptoError as e:
        raise ProviderError(str(e)) from e
    return df[COLS]


BYBIT_IV = {"1d": "D", "4h": "240", "1h": "60"}
OKX_IV = {"1d": "1Dutc", "4h": "4H", "1h": "1H"}
STEP_MS = {"1d": 86_400_000, "4h": 14_400_000, "1h": 3_600_000}


def bybit(symbol: str, interval: str, start_ms: int | None) -> pd.DataFrame:
    """Bybit v5 spot klines (public, no key). Pages backwards from now with `end`, 1000 per call, newest first."""
    rows, end = [], None
    for _ in range(80):
        params = dict(category="spot", symbol=symbol, interval=BYBIT_IV[interval], limit=1000)
        if end is not None:
            params["end"] = end
        if start_ms is not None:
            params["start"] = start_ms
        j = _get("https://api.bybit.com/v5/market/kline", params)
        if str(j.get("retCode")) != "0":
            raise ProviderError(f"Bybit error {j.get('retCode')}: {str(j.get('retMsg'))[:80]}")
        page = j["result"]["list"]
        if not page:
            break
        rows += page
        oldest = min(int(r[0]) for r in page)
        if len(page) < 1000 or (start_ms is not None and oldest <= start_ms):
            break
        end = oldest - 1
    if not rows:
        raise ProviderError(f"Bybit returned no candles for {symbol} {interval}")
    return _to_frame(rows)


def okx(symbol: str, interval: str, start_ms: int | None) -> pd.DataFrame:
    """OKX v5 history-candles (public, no key). 100 per call, newest first, paged backwards with `after`."""
    inst = symbol[:-4] + "-USDT" if symbol.endswith("USDT") else symbol
    rows, after = [], None
    for _ in range(260):
        params = dict(instId=inst, bar=OKX_IV[interval], limit=100)
        if after is not None:
            params["after"] = after
        j = _get("https://www.okx.com/api/v5/market/history-candles", params)
        if str(j.get("code")) != "0":
            raise ProviderError(f"OKX error {j.get('code')}: {str(j.get('msg'))[:80]}")
        page = [r for r in j["data"] if str(r[8] if len(r) > 8 else "1") == "1"]       # confirmed (closed) candles only
        if not j["data"]:
            break
        rows += page
        oldest = min(int(r[0]) for r in j["data"])
        if len(j["data"]) < 100 or (start_ms is not None and oldest <= start_ms):
            break
        after = oldest
    if start_ms is not None:
        rows = [r for r in rows if int(r[0]) >= start_ms]
    if not rows:
        raise ProviderError(f"OKX returned no candles for {symbol} {interval}")
    return _to_frame(rows)


def yfinance_crypto(symbol: str, interval: str, start_ms: int | None) -> pd.DataFrame:
    from app import crypto_data as cd
    try:
        df = cd._yf_klines(symbol, interval)
    except Exception as e:
        raise ProviderError(f"yfinance: {type(e).__name__} {str(e)[:100]}") from e
    if start_ms:
        df = df[df.index >= pd.to_datetime(start_ms, unit="ms")]
    if len(df) == 0:
        raise ProviderError(f"yfinance returned no candles for {symbol} {interval}")
    return df[COLS]


CRYPTO_CHAIN = [("binance", binance), ("bybit", bybit), ("okx", okx), ("yfinance", yfinance_crypto)]
CHAIN_TEXT = " -> ".join(n for n, _ in CRYPTO_CHAIN)


def crypto_klines(symbol: str, interval: str, start_ms: int | None, chain=None) -> tuple[pd.DataFrame, str]:
    """Crypto candles through the free fallback chain Binance -> Bybit -> OKX -> yfinance. The first source that returns usable rows wins;
    `df.attrs["fell_back"]` records why earlier ones were skipped. Returns (frame, source name)."""
    from app import crypto_data as cd
    if start_ms is None:
        start_ms = int(time.time() * 1000) - cd.INTERVALS[interval]["history_days"] * 86_400_000
    errs = []
    for name, fn in (chain or CRYPTO_CHAIN):
        try:
            df = fn(symbol, interval, start_ms)
            if df is None or len(df) == 0:
                raise ProviderError("no rows")
            df = df[COLS].dropna(how="all")
            if errs:
                df.attrs["fell_back"] = "; ".join(errs)
            return df, name
        except Exception as e:         # geo-block (HTTP 451), rate limit, timeout, schema change: try the next source
            errs.append(f"{name}: {type(e).__name__} {str(e)[:90]}")
    raise ProviderError("all crypto sources failed (" + "; ".join(errs) + ")")


class Providers:
    """Injectable bundle so tests can run the whole pipeline on mocked data."""
    def __init__(self, nse=nse_daily, crypto=crypto_klines, bhav="auto"):
        self.nse, self.crypto = nse, crypto
        from . import crosscheck
        # bhavcopy HTTP getter for the post-run cross-check; mocked providers get none unless a getter is passed explicitly
        self.bhav = crosscheck._http if bhav == "auto" and nse is nse_daily else (None if bhav == "auto" else bhav)


def with_retry(fn, attempts: int | None = None, base: float | None = None, factor: float = 2.0, sleep=time.sleep):
    """Call fn(); on any exception wait base * factor**k and try again. Returns (result, attempts_used). Raises the last error."""
    attempts = attempts or config.RETRY_ATTEMPTS
    base = config.RETRY_BASE_S if base is None else base
    last = None
    for k in range(attempts):
        try:
            return fn(), k + 1
        except Exception as e:      # network, rate limit, empty result
            last = e
            if k < attempts - 1:
                sleep(base * factor ** k)
    raise ProviderError(f"{type(last).__name__}: {str(last)[:200]} (after {attempts} attempts)") from last
