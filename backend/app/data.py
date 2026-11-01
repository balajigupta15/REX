"""Real NSE daily bars from yfinance (5y), cached to CSV. No synthetic data anywhere."""
from __future__ import annotations
import os, re, time, warnings
from datetime import datetime
import pandas as pd
from . import config

_TICKER_RE = re.compile(r"^[A-Z0-9&\-]{1,20}\.(NS|BO)$")


class DataError(Exception):
    pass


def is_offline() -> bool:
    return os.getenv("REX_OFFLINE", "").strip().lower() not in ("", "0", "false", "no")


def normalise(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if "." not in t and t:
        t += ".NS"
    if not _TICKER_RE.match(t):
        raise DataError(f"Invalid ticker '{ticker}'. Use an NSE/BSE symbol like RELIANCE.NS")
    return t


def _cache_path(t: str):
    return config.DATA_CACHE / f"{t}.csv"


def _fetch(t: str) -> pd.DataFrame:
    import yfinance as yf
    last = None
    for k in range(3):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.download(t, period="5y", interval="1d", auto_adjust=True, progress=False, threads=False)
            if df is not None and len(df):
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                df.index = pd.to_datetime(df.index).tz_localize(None)
                df.index.name = "Date"
                return df
        except Exception as e:  # network / rate limit
            last = e
        time.sleep(1.5 * (k + 1))
    raise DataError(f"No data for {t} from yfinance ({last or 'empty result'})")


def _from_store(t: str, refresh: bool):
    """Pipeline store first (Parquet, written by `python -m pipeline`). None => caller uses the CSV cache as before."""
    if refresh:
        return None
    try:
        from pipeline import store as ps
        part = ps.partition("nse", t, "1d")
        if not part or part["rows"] < config.MIN_BARS:
            return None
        from datetime import timezone
        age_h = (datetime.now(timezone.utc) - datetime.strptime(part["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if not (is_offline() or age_h < config.CACHE_MAX_AGE_HOURS):
            return None
        df = ps.read("nse", t, "1d")
        if df is None or len(df) < config.MIN_BARS:
            return None
        df = df[~df.index.duplicated()].sort_index()
        return df, {"ticker": t, "bars": int(len(df)), "first": str(df.index[0].date()), "last": str(df.index[-1].date()),
                    "source": "pipeline store (parquet)", "cache_file_mtime": part["updated_at"][:16]}
    except Exception:
        return None


def load(ticker: str, refresh: bool = False) -> tuple[pd.DataFrame, dict]:
    """Return (OHLCV frame, info). Uses fresh cache, else fetches, else falls back to a stale cache."""
    t = normalise(ticker)
    hit = _from_store(t, refresh)
    if hit is not None:
        return hit
    config.DATA_CACHE.mkdir(parents=True, exist_ok=True)
    p = _cache_path(t)
    fresh = p.exists() and (time.time() - p.stat().st_mtime) < config.CACHE_MAX_AGE_HOURS * 3600
    source = "cache"
    df = None
    offline = is_offline() and p.exists()     # REX_OFFLINE=1 serves the bundled real cache, no network
    if p.exists() and ((fresh and not refresh) or offline):
        df = pd.read_csv(p, index_col=0, parse_dates=True)
    else:
        try:
            df = _fetch(t)
            df.to_csv(p)
            source = "yfinance"
        except DataError:
            if p.exists():
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                source = "cache (stale, live fetch failed)"
            else:
                raise
    if len(df) < config.MIN_BARS:
        raise DataError(f"{t} has only {len(df)} daily bars; need at least {config.MIN_BARS}")
    df = df[~df.index.duplicated()].sort_index()
    info = {"ticker": t, "bars": int(len(df)), "first": str(df.index[0].date()), "last": str(df.index[-1].date()),
            "source": source, "cache_file_mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes")}
    return df, info


def split_index(df: pd.DataFrame) -> int:
    """First bar index of the locked final period (last LOCKED_DAYS calendar days)."""
    cutoff = df.index[-1] - pd.Timedelta(days=config.LOCKED_DAYS)
    return int(df.index.searchsorted(cutoff, side="right"))
