"""Status payload shared by the API (/pipeline/status), the UI Data page and `python -m pipeline status`."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pandas as pd
from . import config, store, schedule, crosscheck, providers
from .validate import STEP, expected_nse_session, IST

FRESH_CRYPTO_BARS = 2
UTC = timezone.utc


def _age_s(ts: str | None, now: datetime) -> int | None:
    if not ts:
        return None
    return int((now - datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)).total_seconds())


def freshness(p: dict, now: datetime) -> dict:
    """fresh | stale for one partition based on its last bar (not on when we last ran)."""
    last = pd.Timestamp(p["last_ts"]) if p.get("last_ts") else None
    if last is None:
        return dict(state="missing", detail="no rows")
    if p["market"] == "crypto":
        lag = (now.replace(tzinfo=None) - last.to_pydatetime()) / STEP[p["interval"]] - 1
        return dict(state="fresh" if lag <= FRESH_CRYPTO_BARS else "stale", lag_bars=max(int(lag), 0),
                    detail=f"last candle {last:%d %b %H:%M} UTC, {max(int(lag), 0)} closed candle(s) behind")
    exp = expected_nse_session(now)
    behind, x = 0, last.date()
    while x < exp:
        x += timedelta(days=1)
        if x.weekday() < 5:
            behind += 1
    return dict(state="fresh" if behind <= 1 else "stale", lag_bars=behind, detail=f"last bar {last.date()}, expected {exp}")


def build(now: datetime | None = None, run_limit: int = 10) -> dict:
    now = now or datetime.now(UTC)
    parts = store.partitions()
    srcs = store.last_sources()
    cc = crosscheck.latest()
    bad = {}
    for r in (cc or {}).get("results", []):
        if r["flag"] == "mismatch":
            bad[r["symbol"]] = bad.get(r["symbol"], 0) + 1
    rows = []
    for p in parts:
        f = freshness(p, now)
        rows.append(dict(market=p["market"], symbol=p["symbol"], interval=p["interval"], rows=p["rows"], first=p["first_ts"], last=p["last_ts"],
                         updated_at=p["updated_at"], updated_age_s=_age_s(p["updated_at"], now), validation=p["status"],
                         source=(srcs.get((p["market"], p["symbol"], p["interval"])) or {}).get("provider"), source_note=(srcs.get((p["market"], p["symbol"], p["interval"])) or {}).get("note"),
                         crosscheck=(None if p["market"] != "nse" or not cc or not cc.get("reachable") else ("mismatch" if bad.get(p["symbol"]) else "ok")),
                         issues=[dict(code=i["code"], level=i["level"], msg=i["msg"]) for i in p["issues"]], freshness=f["state"], freshness_detail=f["detail"]))
    have = {(r["market"], r["symbol"], r["interval"]) for r in rows}
    missing = [dict(market=m, symbol=s, interval=i) for m, s, i in config.targets("all") if (m, s, i) not in have]
    last_runs = {m: store.last_run(m) for m in ("nse", "crypto")}
    nxt = {m: schedule.next_slot(m, now).strftime("%Y-%m-%d %H:%M UTC") for m in ("nse", "crypto")}
    nxt["nse_ist"] = schedule.next_slot("nse", now).astimezone(IST).strftime("%a %d %b %H:%M IST")
    stale = sum(1 for r in rows if r["freshness"] != "fresh")
    return dict(enabled=bool(parts), store=str(config.store_dir()), format="Parquet, partitioned by market/symbol/interval; SQLite catalog (catalog.sqlite)",
                now_utc=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                totals=dict(partitions=len(rows), rows=sum(r["rows"] for r in rows), stale=stale, missing=len(missing),
                            with_issues=sum(1 for r in rows if r["issues"])),
                last_run={m: (None if r is None else dict(r, age_s=_age_s(r["started_at"], now))) for m, r in last_runs.items()},
                next_run=nxt, schedule="NSE: weekdays after 16:00 IST (16:05). Crypto: hourly at :02 UTC.",
                sources=dict(nse="yfinance (primary); NSE bhavcopy cross-check", crypto=providers.CHAIN_TEXT + " (first that answers wins)", crypto_meta="CoinGecko", news="Google News RSS + yfinance news"),
                crosscheck=cc, recent_runs=store.runs(run_limit), partitions=rows, missing=missing,
                modes=dict(nse="offline (seed from bundled cache)" if config.offline("nse") else "live (yfinance)",
                           crypto="offline (seed from bundled cache)" if config.offline("crypto") else "live (Binance public REST)"),
                readers="App loaders read this store first when it is present and fresh, then fall back to data_cache/ CSVs.")


def healthy(max_age_h: float = 3.0) -> tuple[bool, str]:
    """Container healthcheck: the store exists and the scheduler has produced a run recently (or the store is simply there in offline mode)."""
    if not store.exists():
        return False, "no catalog yet"
    r = store.last_run("crypto") or store.last_run("nse")
    if r is None:
        return False, "no runs yet"
    age = _age_s(r["started_at"], datetime.now(UTC)) or 0
    return (age < max_age_h * 3600 * (30 if config.offline("crypto") else 1), f"last run {age // 60} min ago ({r['status']})")
