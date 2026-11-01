from __future__ import annotations
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

NSE_TICKERS = ["RELIANCE.NS", "JIOFIN.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "ITC.NS", "LT.NS", "BHARTIARTL.NS"]
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CRYPTO_INTERVALS = ["1d", "4h", "1h"]
NSE_HISTORY_YEARS = 5

NSE_RUN_AT_IST = (16, 5)       # weekdays, after the 15:30 close and the 16:00 settlement of yfinance daily bars
CRYPTO_RUN_MINUTE = 2          # every hour at hh:02 UTC (the hourly candle has just closed)
OVERLAP_BARS = 3               # re-fetch the last few bars on every incremental run (vendors revise the newest bar)
RETRY_ATTEMPTS = int(os.getenv("REX_PIPELINE_RETRIES", "3"))
RETRY_BASE_S = float(os.getenv("REX_PIPELINE_BACKOFF_S", "1.5"))


def store_dir() -> Path:
    return Path(os.getenv("REX_STORE", str(BASE / "data_store")))


def offline(market: str) -> bool:
    """Offline = never touch the network; the run seeds the store from the bundled cache instead."""
    v = os.getenv("REX_PIPELINE_OFFLINE")
    if v is not None and v.strip() != "":
        return v.strip().lower() not in ("0", "false", "no")
    if market == "crypto":
        return os.getenv("REX_CRYPTO_OFFLINE", "").strip().lower() not in ("", "0", "false", "no")
    return os.getenv("REX_OFFLINE", "").strip().lower() not in ("", "0", "false", "no")


def targets(market: str | None = None, symbols: list[str] | None = None, intervals: list[str] | None = None) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if market in (None, "all", "nse"):
        out += [("nse", s, "1d") for s in NSE_TICKERS if not symbols or s in symbols]
    if market in (None, "all", "crypto"):
        out += [("crypto", s, iv) for s in CRYPTO_SYMBOLS if not symbols or s in symbols for iv in CRYPTO_INTERVALS if not intervals or iv in intervals]
    return out
