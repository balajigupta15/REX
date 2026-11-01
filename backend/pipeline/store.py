"""Parquet store (one file per market/symbol/interval partition) + SQLite catalog and run log.

    <store>/market=nse/symbol=TCS.NS/interval=1d/data.parquet
    <store>/catalog.sqlite      tables: partitions, runs, run_items
"""
from __future__ import annotations
import json, os, sqlite3, threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from . import config

COLS = ["Open", "High", "Low", "Close", "Volume"]
_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS partitions(
  market TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, path TEXT NOT NULL,
  rows INTEGER NOT NULL, first_ts TEXT, last_ts TEXT, updated_at TEXT NOT NULL, status TEXT, issues TEXT,
  PRIMARY KEY(market, symbol, interval));
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT, market TEXT, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT, symbols_ok INTEGER DEFAULT 0, symbols_failed INTEGER DEFAULT 0, rows_added INTEGER DEFAULT 0, message TEXT);
CREATE TABLE IF NOT EXISTS run_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, market TEXT, symbol TEXT, interval TEXT, status TEXT,
  rows_added INTEGER DEFAULT 0, rows_total INTEGER DEFAULT 0, attempts INTEGER DEFAULT 1, provider TEXT, error TEXT, issues TEXT, duration_s REAL);
CREATE INDEX IF NOT EXISTS run_items_run ON run_items(run_id);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def partition_path(market: str, symbol: str, interval: str) -> Path:
    return config.store_dir() / f"market={market}" / f"symbol={symbol}" / f"interval={interval}" / "data.parquet"


@contextmanager
def catalog():
    d = config.store_dir()
    d.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        c = sqlite3.connect(d / "catalog.sqlite", timeout=20)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        try:
            yield c
            c.commit()
        finally:
            c.close()


def exists() -> bool:
    return (config.store_dir() / "catalog.sqlite").exists()


# ---------------- data ----------------
def read(market: str, symbol: str, interval: str) -> pd.DataFrame | None:
    """The stored frame, or None when the partition is missing / unreadable (callers then fall back to their own cache)."""
    p = partition_path(market, symbol, interval)
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if "ts" in df.columns:
            df = df.set_index("ts")
        df.index = pd.DatetimeIndex(df.index).astype("datetime64[ns]")
        df.index.name = "Date" if market == "nse" else None
        return df[COLS].astype("float64")
    except Exception:
        return None


def write(market: str, symbol: str, interval: str, df: pd.DataFrame, status: str = "ok", issues: list | None = None) -> dict:
    """Atomically replace the partition file and upsert its catalog row."""
    p = partition_path(market, symbol, interval)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = df[COLS].astype("float64").copy()
    out.index = pd.DatetimeIndex(out.index).astype("datetime64[ns]")
    out.index.name = "ts"
    tmp = p.with_suffix(".parquet.tmp")
    with _LOCK:
        out.reset_index().to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, p)
        meta = dict(market=market, symbol=symbol, interval=interval, path=str(p.relative_to(config.store_dir())), rows=int(len(out)),
                    first_ts=str(out.index[0]) if len(out) else None, last_ts=str(out.index[-1]) if len(out) else None,
                    updated_at=iso(utcnow()), status=status, issues=json.dumps(issues or []))
        with catalog() as c:
            c.execute("INSERT INTO partitions(market,symbol,interval,path,rows,first_ts,last_ts,updated_at,status,issues) "
                      "VALUES(:market,:symbol,:interval,:path,:rows,:first_ts,:last_ts,:updated_at,:status,:issues) "
                      "ON CONFLICT(market,symbol,interval) DO UPDATE SET path=:path, rows=:rows, first_ts=:first_ts, last_ts=:last_ts, "
                      "updated_at=:updated_at, status=:status, issues=:issues", meta)
    return meta


def partition(market: str, symbol: str, interval: str) -> dict | None:
    if not exists():
        return None
    with catalog() as c:
        r = c.execute("SELECT * FROM partitions WHERE market=? AND symbol=? AND interval=?", (market, symbol, interval)).fetchone()
    return _row(r)


def partitions() -> list[dict]:
    if not exists():
        return []
    with catalog() as c:
        return [_row(r) for r in c.execute("SELECT * FROM partitions ORDER BY market, symbol, interval")]


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    if "issues" in d:
        try:
            d["issues"] = json.loads(d["issues"] or "[]")
        except Exception:
            d["issues"] = []
    return d


RUNLOG_SCHEMA = "\n".join(s + ";" for s in SCHEMA.split(";") if "CREATE TABLE IF NOT EXISTS runs" in s or "CREATE TABLE IF NOT EXISTS run_items" in s or "run_items_run" in s)


def _pg_names(sql: str) -> str:
    """In the shared database `runs` is already the strategy-memory table, so the pipeline log lives in pipeline_runs / pipeline_run_items."""
    import re
    return re.sub(r"\bruns\b", "pipeline_runs", re.sub(r"\brun_items_run\b", "pipeline_run_items_run", re.sub(r"\brun_items\b", "pipeline_run_items", sql)))


class _Renamed:
    def __init__(self, c):
        self._c = c

    def execute(self, sql, params=()):
        return self._c.execute(_pg_names(sql), params)

    def __getattr__(self, k):
        return getattr(self._c, k)


@contextmanager
def runlog():
    """Connection for the run log (runs, run_items). SQLite: the catalog file next to the Parquet files (unchanged). With Yugabyte
    active (REX_DB_URL) the run log lives in the shared database so the api and pipeline containers see the same history."""
    from app import db
    if db.backend() == "sqlite":
        with catalog() as c:
            yield c
        return
    c = _Renamed(db.connect(_pg_names(RUNLOG_SCHEMA)))
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _has_log() -> bool:
    from app import db
    return exists() if db.backend() == "sqlite" else True


# ---------------- run log ----------------
def start_run(trigger: str, market: str) -> int:
    with runlog() as c:
        cur = c.execute("INSERT INTO runs(trigger, market, started_at, status) VALUES(?,?,?,?)", (trigger, market, iso(utcnow()), "running"))
        return int(cur.lastrowid)


def log_item(run_id: int, market: str, symbol: str, interval: str, status: str, rows_added: int = 0, rows_total: int = 0, attempts: int = 1,
             provider: str | None = None, error: str | None = None, issues: list | None = None, duration_s: float = 0.0) -> None:
    with runlog() as c:
        c.execute("INSERT INTO run_items(run_id,market,symbol,interval,status,rows_added,rows_total,attempts,provider,error,issues,duration_s) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, market, symbol, interval, status, rows_added, rows_total, attempts, provider, (error or "")[:300] or None, json.dumps(issues or []), round(duration_s, 2)))


def finish_run(run_id: int, status: str, ok: int, failed: int, rows_added: int, message: str = "") -> None:
    with runlog() as c:
        c.execute("UPDATE runs SET finished_at=?, status=?, symbols_ok=?, symbols_failed=?, rows_added=?, message=? WHERE id=?",
                  (iso(utcnow()), status, ok, failed, rows_added, message[:300], run_id))


def runs(limit: int = 15, market: str | None = None) -> list[dict]:
    if not _has_log():
        return []
    with runlog() as c:
        q, a = "SELECT * FROM runs", []
        if market:
            q += " WHERE market IN (?, 'all')"; a.append(market)
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*a, limit))]


def run_items(run_id: int) -> list[dict]:
    with runlog() as c:
        return [dict(r, issues=json.loads(r["issues"] or "[]")) for r in c.execute("SELECT * FROM run_items WHERE run_id=? ORDER BY id", (run_id,))]


def last_run(market: str, ok_only: bool = False) -> dict | None:
    if not _has_log():
        return None
    with runlog() as c:
        q = "SELECT * FROM runs WHERE market IN (?, 'all')" + (" AND status IN ('ok','partial','seeded')" if ok_only else "") + " ORDER BY id DESC LIMIT 1"
        r = c.execute(q, (market,)).fetchone()
    return dict(r) if r else None


def fail_stale_runs() -> int:
    """A crashed process leaves runs in 'running'. Mark them so the status page stays truthful."""
    if not _has_log():
        return 0
    with runlog() as c:
        return c.execute("UPDATE runs SET status='crashed', finished_at=? WHERE status='running' AND started_at < ?",
                         (iso(utcnow()), iso(utcnow() - pd.Timedelta(hours=1).to_pytimedelta()))).rowcount


def last_sources() -> dict[tuple[str, str, str], dict]:
    """Newest successful run item per partition: which provider actually supplied the data last, and any fallback note."""
    if not _has_log():
        return {}
    with runlog() as c:
        rows = c.execute("""SELECT i.market, i.symbol, i.interval, i.provider, i.error, i.status, r.started_at FROM run_items i JOIN runs r ON r.id = i.run_id
                            WHERE i.status IN ('ok','ok_with_drops','seeded','up_to_date') AND i.id IN
                              (SELECT MAX(id) FROM run_items WHERE status IN ('ok','ok_with_drops','seeded','up_to_date') GROUP BY market, symbol, interval)""").fetchall()
    return {(r["market"], r["symbol"], r["interval"]): dict(provider=r["provider"], note=r["error"], at=r["started_at"], status=r["status"]) for r in rows}
