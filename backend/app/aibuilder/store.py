"""Persistence for the AI Strategy Builder, through app/db.py (YugabyteDB if REX_DB_URL is reachable, otherwise the SQLite file config.DB_PATH). Every function opens its own short-lived
connection with a 30 s busy timeout, so the API threads and the agent worker threads can write at the same time safely."""
from __future__ import annotations
import hashlib, json, time, uuid
from contextlib import closing
from .. import config, db

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_sessions(id TEXT PRIMARY KEY, ts REAL, title TEXT, market TEXT, symbol TEXT, interval TEXT);
CREATE TABLE IF NOT EXISTS ai_versions(id TEXT PRIMARY KEY, session_id TEXT, n INTEGER, parent_id TEXT, ts REAL, message TEXT, title TEXT, explanation TEXT,
  rules TEXT, code TEXT, exits TEXT, source TEXT, model TEXT, market TEXT, symbol TEXT, interval TEXT, cost_bps REAL, status TEXT, error TEXT,
  result TEXT, verdict TEXT, stats TEXT, notes TEXT, memory_run_id TEXT, trial_sharpe REAL);
CREATE INDEX IF NOT EXISTS ix_aiv_sess ON ai_versions(session_id, n);
CREATE TABLE IF NOT EXISTS ai_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL, role TEXT, text TEXT, version_id TEXT);
CREATE TABLE IF NOT EXISTS ai_cache(key TEXT PRIMARY KEY, ts REAL, model TEXT, response TEXT);
CREATE TABLE IF NOT EXISTS ai_runs(id TEXT PRIMARY KEY, ts REAL, updated REAL, kind TEXT, goal TEXT, params TEXT, status TEXT, mode TEXT, session_id TEXT,
  final TEXT, error TEXT, owner TEXT);
CREATE TABLE IF NOT EXISTS ai_steps(run_id TEXT, idx INTEGER, key TEXT, tool TEXT, status TEXT, inputs TEXT, summary TEXT, output TEXT, error TEXT,
  started REAL, ended REAL, PRIMARY KEY(run_id, idx));
"""
JSON_COLS = {"rules", "exits", "result", "verdict", "stats", "notes", "error", "params", "final", "inputs", "output"}


def _db():
    return db.connect(SCHEMA, timeout=30)      # app/db.py: YugabyteDB when REX_DB_URL is reachable, else SQLite (same DB file as the rest of Rex)


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def _enc(v):
    return json.dumps(v, default=str) if isinstance(v, (dict, list)) else v


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]


# ---------------------------------------------------------------- cache
def cache_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def cache_get(key: str) -> dict | None:
    with closing(_db()) as con:
        r = con.execute("SELECT response, model FROM ai_cache WHERE key=?", (key,)).fetchone()
    return dict(response=json.loads(r["response"]), model=r["model"]) if r else None


def cache_put(key: str, model: str, response: dict) -> None:
    with closing(_db()) as con:
        con.execute("INSERT OR REPLACE INTO ai_cache VALUES(?,?,?,?)", (key, time.time(), model, json.dumps(response)))
        con.commit()


def cache_count() -> int:
    with closing(_db()) as con:
        return con.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0]


# ---------------------------------------------------------------- sessions / messages / versions
def create_session(title: str, market: str, symbol: str, interval: str, sid: str | None = None) -> str:
    sid = sid or new_id("s_")
    with closing(_db()) as con:
        con.execute("INSERT OR IGNORE INTO ai_sessions VALUES(?,?,?,?,?,?)", (sid, time.time(), title[:80], market, symbol, interval))
        con.commit()
    return sid


def get_session(sid: str) -> dict | None:
    with closing(_db()) as con:
        return _row(con.execute("SELECT * FROM ai_sessions WHERE id=?", (sid,)).fetchone())


def list_sessions(limit: int = 30) -> list[dict]:
    with closing(_db()) as con:
        rows = con.execute("""SELECT s.*, (SELECT COUNT(*) FROM ai_versions v WHERE v.session_id=s.id) AS versions FROM ai_sessions s ORDER BY ts DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def add_message(sid: str, role: str, text: str, version_id: str | None = None) -> None:
    with closing(_db()) as con:
        con.execute("INSERT INTO ai_messages(session_id, ts, role, text, version_id) VALUES(?,?,?,?,?)", (sid, time.time(), role, text, version_id))
        con.commit()


def messages(sid: str) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT * FROM ai_messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()]


def next_n(sid: str) -> int:
    with closing(_db()) as con:
        return (con.execute("SELECT COALESCE(MAX(n),0) FROM ai_versions WHERE session_id=?", (sid,)).fetchone()[0]) + 1


def save_version(v: dict) -> dict:
    v = dict(v)
    vid = v.setdefault("id", new_id("v_"))
    v.setdefault("ts", time.time())
    cols = ["id", "session_id", "n", "parent_id", "ts", "message", "title", "explanation", "rules", "code", "exits", "source", "model", "market", "symbol", "interval",
            "cost_bps", "status", "error", "result", "verdict", "stats", "notes", "memory_run_id", "trial_sharpe"]
    with closing(_db()) as con:
        if v.get("n") is None:
            v["n"] = (con.execute("SELECT COALESCE(MAX(n),0) FROM ai_versions WHERE session_id=?", (v["session_id"],)).fetchone()[0]) + 1
        con.execute(f"INSERT OR REPLACE INTO ai_versions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [_enc(v.get(c)) for c in cols])
        con.commit()
    return get_version(vid)


def update_version(vid: str, **kw) -> None:
    with closing(_db()) as con:
        con.execute(f"UPDATE ai_versions SET {','.join(k + '=?' for k in kw)} WHERE id=?", [_enc(x) for x in kw.values()] + [vid])
        con.commit()


def get_version(vid: str) -> dict | None:
    with closing(_db()) as con:
        return _row(con.execute("SELECT * FROM ai_versions WHERE id=?", (vid,)).fetchone())


def versions(sid: str, light: bool = True) -> list[dict]:
    with closing(_db()) as con:
        rows = [_row(r) for r in con.execute("SELECT * FROM ai_versions WHERE session_id=? ORDER BY n", (sid,)).fetchall()]
    if light:
        for r in rows:
            r["result"] = _light(r.get("result"))
    return rows


def _light(res):
    """Drop equity series and trade lists (keep metrics) for list views."""
    if not isinstance(res, dict):
        return res
    return {w: {k: v for k, v in d.items() if k in ("start", "end", "metrics", "benchmark")} for w, d in res.items() if isinstance(d, dict)}


def trial_sharpes(sid: str) -> list[float]:
    with closing(_db()) as con:
        return [float(r[0]) for r in con.execute("SELECT trial_sharpe FROM ai_versions WHERE session_id=? AND trial_sharpe IS NOT NULL", (sid,)).fetchall()]


# ---------------------------------------------------------------- runs / steps
def create_run(goal: str, params: dict, mode: str, session_id: str | None, rid: str | None = None, kind: str = "agent") -> str:
    rid = rid or new_id("r_")
    now = time.time()
    with closing(_db()) as con:
        con.execute("INSERT INTO ai_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (rid, now, now, kind, goal, json.dumps(params), "queued", mode, session_id, None, None, None))
        con.commit()
    return rid


def update_run(rid: str, **kw) -> None:
    kw["updated"] = time.time()
    with closing(_db()) as con:
        con.execute(f"UPDATE ai_runs SET {','.join(k + '=?' for k in kw)} WHERE id=?", [_enc(x) for x in kw.values()] + [rid])
        con.commit()


def claim_run(rid: str, owner: str, stale_s: float = 90.0) -> bool:
    """Atomically take ownership of a run (so two workers never drive the same run). A running run whose heartbeat is older than stale_s can be taken over."""
    now = time.time()
    with closing(_db()) as con:
        cur = con.execute("UPDATE ai_runs SET status='running', owner=?, updated=? WHERE id=? AND (status IN ('queued','failed','interrupted') OR (status='running' AND updated < ?))",
                          (owner, now, rid, now - stale_s))
        con.commit()
        return cur.rowcount == 1


def get_run(rid: str) -> dict | None:
    with closing(_db()) as con:
        return _row(con.execute("SELECT * FROM ai_runs WHERE id=?", (rid,)).fetchone())


def list_runs(limit: int = 20) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT id, ts, updated, goal, status, mode, session_id FROM ai_runs ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()]


def begin_step(rid: str, idx: int, key: str, tool: str, inputs: dict) -> None:
    with closing(_db()) as con:
        con.execute("INSERT OR REPLACE INTO ai_steps(run_id, idx, key, tool, status, inputs, started) VALUES(?,?,?,?,?,?,?)", (rid, idx, key, tool, "running", json.dumps(inputs, default=str), time.time()))
        con.commit()
    update_run(rid)


def end_step(rid: str, idx: int, status: str, summary: str, output: dict | None, error: dict | None) -> None:
    with closing(_db()) as con:
        con.execute("UPDATE ai_steps SET status=?, summary=?, output=?, error=?, ended=? WHERE run_id=? AND idx=?",
                    (status, summary, json.dumps(output, default=str) if output is not None else None, json.dumps(error) if error else None, time.time(), rid, idx))
        con.commit()
    update_run(rid)


def steps(rid: str, with_output: bool = False) -> list[dict]:
    with closing(_db()) as con:
        rows = [_row(r) for r in con.execute("SELECT * FROM ai_steps WHERE run_id=? ORDER BY idx", (rid,)).fetchall()]
    if not with_output:
        for r in rows:
            r.pop("output", None)
    return rows


def done_step(rid: str, key: str) -> dict | None:
    with closing(_db()) as con:
        return _row(con.execute("SELECT * FROM ai_steps WHERE run_id=? AND key=? AND status='ok' ORDER BY idx DESC LIMIT 1", (rid, key)).fetchone())
