"""Data-quality checks. `validate()` returns (clean frame, report). Rows that cannot be real market data are dropped and counted."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pandas as pd

STEP = {"1d": timedelta(days=1), "4h": timedelta(hours=4), "1h": timedelta(hours=1)}
IST = timezone(timedelta(hours=5, minutes=30))
COLS = ["Open", "High", "Low", "Close", "Volume"]
TOL = 1e-6


def _issue(level: str, code: str, count: int, msg: str, examples=None) -> dict:
    d = dict(level=level, code=code, count=int(count), msg=msg)
    if examples is not None and len(examples):
        d["examples"] = [str(x) for x in list(examples)[:3]]
    return d


def _utcnow(now):
    return now if now is not None else datetime.now(timezone.utc)


def expected_nse_session(now: datetime):
    """Date of the most recent NSE session whose daily bar should exist (weekday; today only after 16:00 IST). Holidays are not modelled."""
    n = now.astimezone(IST)
    d = n.date()
    if n.weekday() >= 5 or (n.hour, n.minute) < (16, 0):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def validate(df: pd.DataFrame, market: str, interval: str, now: datetime | None = None, check_fresh: bool = True) -> tuple[pd.DataFrame, dict]:
    issues: list[dict] = []
    dropped = 0
    now = _utcnow(now)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLS), dict(status="error", rows_in=0, rows_out=0, dropped=0, issues=[_issue("error", "empty", 1, "no rows")])
    d = df[COLS].copy() if all(c in df.columns for c in COLS) else None
    if d is None:
        return pd.DataFrame(columns=COLS), dict(status="error", rows_in=len(df), rows_out=0, dropped=len(df),
                                                issues=[_issue("error", "schema", 1, f"missing columns, need {COLS}")])
    rows_in = len(d)
    d.index = pd.to_datetime(d.index)
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_convert("UTC").tz_localize(None)
    for c in COLS:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    # duplicates / ordering
    dup = d.index.duplicated(keep="last")
    if dup.any():
        issues.append(_issue("warn", "duplicates", dup.sum(), f"{int(dup.sum())} duplicate timestamps (kept the last)", d.index[dup]))
        d = d[~dup]
    if not d.index.is_monotonic_increasing:
        issues.append(_issue("warn", "unsorted", 1, "timestamps were not sorted (sorted)"))
        d = d.sort_index()
    # NaN
    bad = d[COLS].isna().any(axis=1)
    if bad.any():
        issues.append(_issue("error", "nan", bad.sum(), f"{int(bad.sum())} rows with missing OHLCV (dropped)", d.index[bad]))
        dropped += int(bad.sum()); d = d[~bad]
    # non-positive prices, negative volume
    bad = (d[["Open", "High", "Low", "Close"]] <= 0).any(axis=1) | (d["Volume"] < 0)
    if bad.any():
        issues.append(_issue("error", "non_positive", bad.sum(), f"{int(bad.sum())} rows with price <= 0 or negative volume (dropped)", d.index[bad]))
        dropped += int(bad.sum()); d = d[~bad]
    # OHLC sanity
    hi_ok = d["High"] >= d[["Open", "Close", "Low"]].max(axis=1) * (1 - TOL)
    lo_ok = d["Low"] <= d[["Open", "Close", "High"]].min(axis=1) * (1 + TOL)
    bad = ~(hi_ok & lo_ok)
    if bad.any():
        issues.append(_issue("error", "ohlc", bad.sum(), f"{int(bad.sum())} rows break High >= max(O,C,L) / Low <= min(O,C,H) (dropped)", d.index[bad]))
        dropped += int(bad.sum()); d = d[~bad]
    # gaps
    if len(d) > 1:
        delta = d.index.to_series().diff().dropna()
        if market == "crypto":
            step = STEP[interval]
            miss = ((delta / step) - 1).clip(lower=0)
            n_missing = int(round(miss.sum()))
            if n_missing:
                where = delta[delta > step].index
                issues.append(_issue("warn", "gaps", n_missing, f"{n_missing} missing {interval} candles in {int((delta > step).sum())} gap(s)", where))
        else:
            big = delta[delta > timedelta(days=5)]          # weekend + up to 3 holidays is normal; more is a hole in the data
            if len(big):
                issues.append(_issue("warn", "gaps", len(big), f"{len(big)} gap(s) longer than 5 calendar days", big.index))
    # staleness
    if check_fresh and len(d):
        last = d.index[-1]
        if market == "crypto":
            step = STEP[interval]
            lag = (now.replace(tzinfo=None) - last.to_pydatetime()) / step - 1      # closed candles missing at the end
            if lag > 2:
                issues.append(_issue("warn", "stale", int(lag), f"last candle {last:%Y-%m-%d %H:%M} UTC is about {int(lag)} candles behind"))
        else:
            exp = expected_nse_session(now)
            behind = 0
            x = last.date()
            while x < exp:
                x += timedelta(days=1)
                if x.weekday() < 5:
                    behind += 1
            if behind > 1:
                issues.append(_issue("warn", "stale", behind, f"last bar {last.date()} is {behind} trading days behind (expected {exp})"))
    status = "error" if any(i["level"] == "error" for i in issues) else ("warn" if issues else "ok")
    return d, dict(status=status, rows_in=rows_in, rows_out=len(d), dropped=dropped, issues=issues)
