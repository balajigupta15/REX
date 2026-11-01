"""Incremental ingestion. One failing symbol never stops a run; every run and every symbol is written to the run log."""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
from . import config, store
from .providers import Providers, ProviderError, with_retry
from .validate import validate, expected_nse_session, IST, STEP

COLS = store.COLS


def _trim_open_bar(df: pd.DataFrame, market: str, interval: str, now: datetime) -> pd.DataFrame:
    """Never store a bar that is still forming."""
    if len(df) == 0:
        return df
    if market == "crypto":
        cut = (now.replace(tzinfo=None) - STEP[interval])
        return df[df.index <= pd.Timestamp(cut)]
    n = now.astimezone(IST)
    if n.weekday() < 5 and (n.hour, n.minute) < (16, 0):          # today's NSE daily bar is partial until the close has settled
        return df[df.index.normalize() < pd.Timestamp(n.date())]
    return df


def _start(market: str, interval: str, last_ts: str | None):
    if not last_ts:
        return None
    last = pd.Timestamp(last_ts)
    if market == "crypto":
        step = STEP[interval]
        return int((last - config.OVERLAP_BARS * step).tz_localize("UTC").timestamp() * 1000)
    return (last - pd.Timedelta(days=config.OVERLAP_BARS + 4)).date()


def ingest_one(market: str, symbol: str, interval: str, providers: Providers, now: datetime, sleep=time.sleep) -> dict:
    """Fetch (incrementally), validate, merge and store one partition. Returns an item dict (also logged by `run`)."""
    t0 = time.time()
    part = store.partition(market, symbol, interval)
    existing = store.read(market, symbol, interval) if part else None
    start = _start(market, interval, part["last_ts"] if part and existing is not None else None)
    item = dict(market=market, symbol=symbol, interval=interval, status="ok", rows_added=0, rows_total=len(existing) if existing is not None else 0,
                attempts=1, provider=None, error=None, issues=[])
    try:
        fetch = (lambda: providers.nse(symbol, start)) if market == "nse" else (lambda: providers.crypto(symbol, interval, start))
        (raw, provider), attempts = with_retry(fetch, sleep=sleep)
        item.update(attempts=attempts, provider=provider)
        if raw.attrs.get("fell_back"):
            item["error"] = "fell back: " + raw.attrs["fell_back"][:240]      # status stays ok; the log shows why a source was skipped
    except ProviderError as e:
        item.update(status="failed", error=str(e), attempts=config.RETRY_ATTEMPTS, duration_s=time.time() - t0)
        return item
    new = _trim_open_bar(raw, market, interval, now)
    new, rep_new = validate(new, market, interval, now, check_fresh=False)
    if len(new) == 0 and existing is None:
        item.update(status="failed", error="provider returned no usable rows", issues=rep_new["issues"], duration_s=time.time() - t0)
        return item
    merged = new if existing is None else pd.concat([existing, new])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged, rep = validate(merged, market, interval, now)
    dropped_issues = [i for i in rep_new["issues"] if i["level"] == "error"]       # rows rejected from the fresh batch stay visible in the catalog
    if dropped_issues:
        rep = dict(rep, issues=dropped_issues + rep["issues"], status="error")
    added = len(merged) - (len(existing) if existing is not None else 0)
    store.write(market, symbol, interval, merged, status=rep["status"], issues=rep["issues"])
    item.update(rows_added=max(added, 0), rows_total=len(merged), issues=rep["issues"], duration_s=time.time() - t0,
                status="ok" if rep["status"] != "error" else "ok_with_drops")
    return item


def seed_one(market: str, symbol: str, interval: str, now: datetime) -> dict:
    """Offline path: import the bundled cache CSV into the store (no network)."""
    from app import config as appcfg, crypto_data as cd, data
    t0 = time.time()
    p = appcfg.DATA_CACHE / f"{symbol}.csv" if market == "nse" else cd._path(symbol, interval)
    item = dict(market=market, symbol=symbol, interval=interval, status="seeded", rows_added=0, rows_total=0, attempts=0, provider="bundled cache", error=None, issues=[])
    if not p.exists():
        item.update(status="failed", error=f"no bundled cache file {p.name}")
        return item
    df = pd.read_csv(p, index_col=0, parse_dates=True)[COLS]
    df, rep = validate(df, market, interval, now, check_fresh=False)
    existing = store.read(market, symbol, interval)
    if existing is not None and len(existing) >= len(df) and existing.index[-1] >= df.index[-1]:
        item.update(status="up_to_date", rows_total=len(existing), duration_s=time.time() - t0)
        return item
    store.write(market, symbol, interval, df, status=rep["status"], issues=rep["issues"])
    item.update(rows_added=len(df) - (len(existing) if existing is not None else 0), rows_total=len(df), issues=rep["issues"], duration_s=time.time() - t0)
    return item


def run(market: str = "all", symbols: list[str] | None = None, intervals: list[str] | None = None, trigger: str = "manual",
        providers: Providers | None = None, now: datetime | None = None, sleep=time.sleep, force_offline: bool | None = None) -> dict:
    """One ingestion run over the selected targets. Returns the run summary (same data as the run log)."""
    providers = providers or Providers()
    now = now or datetime.now(timezone.utc)
    store.fail_stale_runs()
    rid = store.start_run(trigger, market)
    ok = failed = added = 0
    items = []
    for mk, sym, iv in config.targets(market, symbols, intervals):
        offline = config.offline(mk) if force_offline is None else force_offline
        try:
            item = seed_one(mk, sym, iv, now) if offline else ingest_one(mk, sym, iv, providers, now, sleep)
        except Exception as e:        # a bug or disk error in one partition must not kill the run
            item = dict(market=mk, symbol=sym, interval=iv, status="failed", error=f"{type(e).__name__}: {str(e)[:200]}", rows_added=0, rows_total=0, attempts=1, provider=None, issues=[])
        store.log_item(rid, mk, sym, iv, item["status"], item.get("rows_added", 0), item.get("rows_total", 0), item.get("attempts", 1),
                       item.get("provider"), item.get("error"), item.get("issues"), item.get("duration_s", 0.0))
        items.append(item)
        if item["status"] == "failed":
            failed += 1
        else:
            ok += 1; added += item.get("rows_added", 0)
    if getattr(providers, "bhav", None) and any(m == "nse" for m, _, _ in config.targets(market, symbols, intervals)) and not (config.offline("nse") if force_offline is None else force_offline):
        try:
            from . import crosscheck
            crosscheck.run([s for m, s, _ in config.targets(market, symbols, intervals) if m == "nse"], http=providers.bhav, now=now)
        except Exception:            # a cross-check problem must never fail an ingestion run
            pass
    seeded = any(i["status"] in ("seeded", "up_to_date") for i in items)
    status = "failed" if ok == 0 and failed else ("partial" if failed else ("seeded" if seeded and all(i["status"] in ("seeded", "up_to_date") for i in items) else "ok"))
    msg = "; ".join(f"{i['symbol']} {i['interval']}: {i['error']}" for i in items if i["status"] == "failed")[:300]
    store.finish_run(rid, status, ok, failed, added, msg)
    return dict(run_id=rid, status=status, market=market, symbols_ok=ok, symbols_failed=failed, rows_added=added, items=items)
