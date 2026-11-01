"""Schedule: NSE once on weekdays after 16:00 IST (16:05), crypto hourly (hh:02 UTC). Stateless: a run is due when none started since the latest slot."""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone
from . import config, store
from .validate import IST


def latest_slot(market: str, now: datetime) -> datetime:
    """The most recent scheduled instant <= now (UTC)."""
    now = now.astimezone(timezone.utc)
    if market == "crypto":
        s = now.replace(minute=config.CRYPTO_RUN_MINUTE, second=0, microsecond=0)
        return s if s <= now else s - timedelta(hours=1)
    n = now.astimezone(IST)
    s = n.replace(hour=config.NSE_RUN_AT_IST[0], minute=config.NSE_RUN_AT_IST[1], second=0, microsecond=0)
    if s > n:
        s -= timedelta(days=1)
    while s.weekday() >= 5:
        s -= timedelta(days=1)
    return s.astimezone(timezone.utc)


def next_slot(market: str, now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    if market == "crypto":
        return latest_slot(market, now) + timedelta(hours=1)
    s = latest_slot(market, now).astimezone(IST) + timedelta(days=1)
    while s.weekday() >= 5:
        s += timedelta(days=1)
    return s.astimezone(timezone.utc)


def due(market: str, now: datetime, last_started: datetime | None) -> bool:
    return last_started is None or last_started < latest_slot(market, now)


def _last_started(market: str) -> datetime | None:
    r = store.last_run(market)
    if not r:
        return None
    return datetime.strptime(r["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def tick(now: datetime | None = None, runner=None) -> list[dict]:
    """Run whatever is due right now (also catches up a slot missed while the container was down). Returns the run summaries."""
    from . import ingest
    runner = runner or ingest.run
    now = now or datetime.now(timezone.utc)
    out = []
    for m in ("nse", "crypto"):
        if due(m, now, _last_started(m)):
            out.append(runner(market=m, trigger="schedule", now=now))
    return out


def loop(poll_s: float = 30.0, log=print) -> None:
    log(f"[pipeline] scheduler started: NSE weekdays {config.NSE_RUN_AT_IST[0]}:{config.NSE_RUN_AT_IST[1]:02d} IST, crypto hourly at :{config.CRYPTO_RUN_MINUTE:02d} UTC")
    while True:
        try:
            for r in tick():
                log(f"[pipeline] {r['market']} run #{r['run_id']} {r['status']}: ok={r['symbols_ok']} failed={r['symbols_failed']} +{r['rows_added']} rows")
        except Exception as e:
            log(f"[pipeline] scheduler error: {type(e).__name__}: {e}")
        time.sleep(poll_s)
