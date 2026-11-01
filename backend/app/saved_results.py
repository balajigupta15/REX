"""Immutable result snapshots so a remembered run can be reopened without rerunning it."""
import json
import time
import uuid
from contextlib import closing
from . import db

SCHEMA = """CREATE TABLE IF NOT EXISTS saved_results(
 id TEXT PRIMARY KEY, ts REAL, market TEXT, kind TEXT, symbol TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS ix_saved_results_market_ts ON saved_results(market, ts DESC, id DESC);"""


def save(market: str, kind: str, payload: dict) -> dict:
    rid = (payload.get("memory") or {}).get("run_id") or uuid.uuid4().hex
    payload["saved_result_id"] = rid
    spec = payload["spec"]
    with closing(db.connect(SCHEMA)) as con:
        con.execute("INSERT INTO saved_results VALUES(?,?,?,?,?,?)", (rid, time.time(), market, kind,
                    spec.get("symbol", spec.get("ticker")), json.dumps(payload)))
        con.commit()
    return payload


def get(rid: str) -> dict | None:
    with closing(db.connect(SCHEMA)) as con:
        row = con.execute("SELECT payload FROM saved_results WHERE id=?", (rid,)).fetchone()
    return json.loads(row[0]) if row else None


def history(market: str, limit: int, offset: int) -> dict:
    with closing(db.connect(SCHEMA)) as con:
        total = con.execute("SELECT COUNT(*) FROM saved_results WHERE market=?", (market,)).fetchone()[0]
        rows = con.execute("SELECT id, ts, market, kind, symbol FROM saved_results WHERE market=? ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                           (market, limit, offset)).fetchall()
    return dict(results=[dict(r) for r in rows], total=total)
