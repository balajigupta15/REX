import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_CACHE = Path(os.getenv("REX_DATA_CACHE", BASE / "data_cache"))
DB_PATH = Path(os.getenv("REX_DB", BASE / "rexlite_memory.sqlite"))
MEKO_URL = os.getenv("MEKO_URL", "https://mcp.mekodata.ai/mcp")
MEKO_TOKEN = os.getenv("MEKO_TOKEN", "").strip()
MEKO_DATAPACK_ID = os.getenv("MEKO_DATAPACK_ID", "").strip()
COST_BPS_DEFAULT = 15.0          # ASSUMPTION: round-trip cost in basis points (brokerage + STT + slippage)
LOCKED_DAYS = 365                # locked final period = last 365 calendar days, never used for tuning
WARMUP_BARS = 100                # bars skipped at the start so indicators are valid
MIN_BARS = 400                   # need at least this much history
CACHE_MAX_AGE_HOURS = 20
DEFAULT_TICKERS = ["RELIANCE.NS", "JIOFIN.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
                   "ICICIBANK.NS", "SBIN.NS", "ITC.NS", "LT.NS", "BHARTIARTL.NS"]
