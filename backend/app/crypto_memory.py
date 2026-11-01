"""'Last time' memory for the Crypto section. Same idea and helper functions as strategy_memory, but a SEPARATE SQLite table (`crypto_runs`,
every row tagged market='crypto') so crypto rows never appear in the NSE strategy memory and vice versa. Meko mirror (when MEKO_TOKEN is set)
carries market='crypto' in the payload. Forward returns (5 / 20 days) come from real Binance DAILY closes after the run."""
from __future__ import annotations
import json, time, uuid
from contextlib import closing
from datetime import datetime
import pandas as pd
from . import crypto_data as cd, memory, meko, strategy_memory as sm

C_SCHEMA = """
CREATE TABLE IF NOT EXISTS crypto_runs(id TEXT PRIMARY KEY, ts REAL, run_date TEXT, kind TEXT, source TEXT, ticker TEXT, template TEXT,
  params TEXT, exits TEXT, cost_bps REAL, verdict TEXT, stats TEXT, anchor_date TEXT, fwd5 REAL, fwd20 REAL, outcome TEXT,
  session_id TEXT, headline TEXT, market TEXT DEFAULT 'crypto', interval TEXT);
CREATE INDEX IF NOT EXISTS ix_cr_tk ON crypto_runs(ticker, template);
"""


def _db():
    con = memory._db()
    con.executescript(C_SCHEMA)
    return con


def _fix(s: str) -> str:
    return s.replace("trading days", "days").replace("locked year", "locked final period").replace("stock", "coin").replace("USDT", "").replace("in your broker", "on your exchange")



def record_run(kind: str, ticker: str, template: str, params: dict, exits: dict, cost_bps: float, result: dict | None = None, flag: str | None = None,
               df: pd.DataFrame | None = None, anchor_date: str | None = None, verdict: str | None = None, stats: dict | None = None,
               outcome: dict | None = None, session_id: str | None = None, source: str = "live", interval: str = "1d", run_id: str | None = None, run_date: str | None = None) -> dict:
    verdict = verdict or (sm.verdict_for_result(result, flag) if result else "TRACKING")
    stats = stats if stats is not None else (sm.stats_for_result(result) if result else {})
    daily = None
    try:
        daily = cd.load(ticker, "1d")[0]
    except Exception:
        pass
    anchor = anchor_date or (str(daily.index[-1].date()) if daily is not None else None)
    fw = sm.forward_returns(daily, anchor) if daily is not None and anchor else dict(fwd5=None, fwd20=None)
    ts = time.time()
    run_date = run_date or datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    rid = run_id or uuid.uuid4().hex[:12]
    row = dict(id=rid, ts=ts, run_date=run_date, kind=kind, source=source, ticker=ticker, template=template, params=params, exits=exits, cost_bps=cost_bps,
               verdict=verdict, stats=stats, anchor_date=anchor, fwd5=fw["fwd5"], fwd20=fw["fwd20"], outcome=outcome, session_id=session_id)
    head = _fix(sm.headline(row, fw if daily is not None else None)) + (f" ({interval} candles)" if interval != "1d" else "")
    with closing(_db()) as con:
        con.execute("INSERT OR REPLACE INTO crypto_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, ts, run_date, kind, source, ticker, template, json.dumps(params), json.dumps(exits), cost_bps, verdict, json.dumps(stats), anchor,
                     fw["fwd5"], fw["fwd20"], json.dumps(outcome) if outcome else None, session_id, head, "crypto", interval))
        con.commit()
    mirror = "off"
    if source == "live" and meko.enabled():
        meko.mirror_async([("strategy_run", head, dict(id=rid, market="crypto", interval=interval, kind=kind, ticker=ticker, template=template, verdict=verdict,
                                                       run_date=run_date, stats=stats, anchor_date=anchor, outcome=outcome))])
        mirror = "queued"
    return dict(run_id=rid, verdict=verdict, headline=head, stored_in="sqlite (crypto_runs)", market="crypto", meko_mirror=mirror)


def _refresh_forward(rows: list[dict]) -> None:
    cache: dict = {}
    for r in rows:
        r["days_since"] = None; r["since_pct"] = None
        if not r.get("anchor_date"):
            continue
        if r["ticker"] not in cache:
            try:
                cache[r["ticker"]] = cd.load(r["ticker"], "1d")[0]
            except Exception:
                cache[r["ticker"]] = None
        df = cache[r["ticker"]]
        if df is None:
            continue
        fw = sm.forward_returns(df, r["anchor_date"])
        r["days_since"], r["since_pct"] = fw["days_since"], fw["since_pct"]
        if (fw["fwd5"], fw["fwd20"]) != (r["fwd5"], r["fwd20"]) and fw["fwd5"] is not None:
            r["fwd5"], r["fwd20"] = fw["fwd5"], fw["fwd20"]
            with closing(_db()) as con:
                con.execute("UPDATE crypto_runs SET fwd5=?, fwd20=? WHERE id=?", (r["fwd5"], r["fwd20"], r["id"])); con.commit()


def query(ticker: str | None = None, template: str | None = None, limit: int = 8, offset: int = 0) -> dict:
    t = cd.normalise(ticker) if ticker else None
    with closing(_db()) as con:
        rows = [sm._row(r) for r in con.execute("SELECT * FROM crypto_runs ORDER BY ts DESC").fetchall()]

    def score(r):
        a, b = (t and r["ticker"] == t), (template and r["template"] == template)
        return 3 if a and b else 2 if a else 1 if b else 0
    if t or template:
        rows = [r for r in rows if score(r) > 0]
    rows.sort(key=lambda r: (-score(r), -r["ts"]))
    total = len(rows)
    rows = rows[offset:offset + limit]
    _refresh_forward(rows)
    for r in rows:
        r["match"] = {3: "same coin and strategy", 2: "same coin", 1: "same strategy"}.get(score(r), "recent")
        r["headline"] = _fix(sm.headline(r, dict(days_since=r.get("days_since"), since_pct=r.get("since_pct"))))
        r["lesson"] = _fix(sm.lesson(r))
        r["replayed"] = False
        r["market"] = "crypto"
        r["run_date_label"] = sm._fmt_date(r["run_date"])
    return dict(count=len(rows), total=total, runs=rows, last_time=rows[0] if rows else None, market="crypto",
                backend=("meko+" if (meko.enabled() and meko._state.get("connected")) else "") + db.backend(),
                note="Crypto memory is kept apart from the NSE stock memory (table crypto_runs, market='crypto'). Forward returns come from real Binance daily closes.")


def count() -> int:
    with closing(_db()) as con:
        return con.execute("SELECT COUNT(*) FROM crypto_runs").fetchone()[0]
