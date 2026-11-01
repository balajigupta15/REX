"""Trade memory: SQLite (source of truth) + best-effort Meko MCP mirror. Similarity is plain-code cosine on setup tags.
Lessons are produced from fixed templates over measured statistics. No LLM involved."""
from __future__ import annotations
import json, math, sqlite3, time, uuid
from contextlib import closing
from . import config, db, meko, rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, ts REAL, kind TEXT, ticker TEXT, template TEXT, params TEXT, exits TEXT,
  cost_bps REAL, train TEXT, locked TEXT, bench_train REAL, bench_locked REAL, flag TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ticker TEXT, template TEXT, win TEXT,
  entry_date TEXT, ret REAL, reason TEXT, tags TEXT, vec TEXT, loser INTEGER);
CREATE INDEX IF NOT EXISTS ix_trades_tpl ON trades(template);
CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, run_id TEXT, ticker TEXT, template TEXT, kind TEXT, text TEXT, tags TEXT);
"""


def _db():
    """Operational DB connection (Yugabyte when REX_DB_URL is set and reachable, else SQLite). See app/db.py."""
    return db.connect(SCHEMA)


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def status() -> dict:
    with closing(_db()) as con:
        n = lambda t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        out = {"runs": n("runs"), "trades": n("trades"), "lessons": n("lessons")}
        try:
            out["journal_trades"] = n("journal_trades")
        except sqlite3.OperationalError:
            out["journal_trades"] = 0
    meko.verify_async()
    store_backend = db.backend()
    out["backend"] = f"meko+{store_backend}" if (meko.enabled() and meko._state["connected"]) else store_backend
    out["meko"] = meko.status()
    out["db_path"] = str(config.DB_PATH) if store_backend == "sqlite" else db.info()["target"]
    out["db"] = store_backend
    return out


# ---------------- lessons (templates over measured stats) ----------------
def make_lessons(ticker: str, template: str, result: dict, kind: str = "backtest") -> list[dict]:
    full = result["full"]
    trades = full["trades"]
    out = []
    n = len(trades)
    if n >= 10:
        wins = [t["return_pct"] > 0 for t in trades]
        base = sum(wins) / n
        cand = []
        for g, vals in rules.TAG_GROUPS.items():
            for v in vals:
                sub = [t for t in trades if t["tags"][g] == v]
                if len(sub) >= 5:
                    wr = sum(t["return_pct"] > 0 for t in sub) / len(sub)
                    avg = sum(t["return_pct"] for t in sub) / len(sub)
                    if abs(wr - base) >= 0.15:
                        cand.append((abs(wr - base) * math.sqrt(len(sub)), g, v, len(sub), wr, avg, base))
        for _, g, v, k, wr, avg, base in sorted(cand, reverse=True)[:3]:
            weak = wr < base
            out.append(dict(kind="setup_weak" if weak else "setup_strong", tags={g: v},
                            text=(f"{template} on {ticker}: trades taken when {g} was '{v}' won {wr*100:.0f}% of {k} (avg {avg:+.1f}%) vs {base*100:.0f}% overall. "
                                  + ("Consider skipping that setup or requiring the opposite condition." if weak else "That setup looked better; test requiring it.")
                                  + " Small sample, a hint not proof.")))
        stops = sum(1 for t in trades if t["reason"] == "stop")
        if n >= 8 and stops / n >= 0.5:
            out.append(dict(kind="stop_heavy", tags={}, text=f"{template} on {ticker}: {stops} of {n} trades ended at the stop-loss. The stop may be too tight for this stock's volatility, or the entry rule is weak."))
    else:
        out.append(dict(kind="few_trades", tags={}, text=f"{template} on {ticker}: only {n} trades in the whole history. Treat the results as noise, not evidence."))
    tr, lk = result["train"]["metrics"], result["locked"]["metrics"]
    if tr["sharpe"] > 0.5 and lk["sharpe"] < 0:
        out.append(dict(kind="regime_change", tags={}, text=f"{template} on {ticker}: worked in training (Sharpe {tr['sharpe']:.2f}) but lost in the last year (Sharpe {lk['sharpe']:.2f}). Edges can fade."))
    bl = result["locked"]["benchmark"]["total_return"]
    if lk["total_return"] < bl:
        out.append(dict(kind="lagged_bh", tags={}, text=f"{template} on {ticker}: last-year return {lk['total_return']*100:+.1f}% vs buy and hold {bl*100:+.1f}%. Being in the market only {lk['exposure']*100:.0f}% of days did not pay here."))
    return out


def refine_lesson(ticker: str, template: str, verdict: dict, orig: dict, ref: dict) -> dict | None:
    if verdict["flag"] == "no_change":
        return None
    t, l = ref["train"]["metrics"], ref["locked"]["metrics"]
    txt = (f"Refining {template} on {ticker}: train Sharpe {t['sharpe']:.2f} -> locked-year Sharpe {l['sharpe']:.2f} "
           f"(locked return {l['total_return']*100:+.1f}% vs original {orig['locked']['metrics']['total_return']*100:+.1f}%). "
           + {"overfit": "Flagged OVERFIT: tuned parameters did not hold up.", "inconclusive": "Inconclusive: too few locked trades.", "ok": "Held up on the locked year."}[verdict["flag"]])
    return dict(kind="refine_" + verdict["flag"], tags={}, text=txt)


# ---------------- persistence ----------------
def record(kind: str, ticker: str, template: str, params: dict, exits: dict, cost_bps: float, result: dict,
           flag: str | None = None, extra_lessons: list[dict] | None = None) -> dict:
    run_id = uuid.uuid4().hex[:12]
    lessons = make_lessons(ticker, template, result, kind) + (extra_lessons or [])
    tr, lk = result["train"], result["locked"]
    summary = (f"{kind} {template} {ticker} params={params} exits={exits}: train {tr['metrics']['total_return']*100:+.1f}% "
               f"(Sharpe {tr['metrics']['sharpe']:.2f}), locked {lk['metrics']['total_return']*100:+.1f}% (Sharpe {lk['metrics']['sharpe']:.2f}), "
               f"buy&hold locked {lk['benchmark']['total_return']*100:+.1f}%" + (f", verdict {flag}" if flag else ""))
    ts = time.time()
    with closing(_db()) as con:
        con.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, ts, kind, ticker, template, json.dumps(params), json.dumps(exits), cost_bps,
                     json.dumps(tr["metrics"]), json.dumps(lk["metrics"]), tr["benchmark"]["total_return"], lk["benchmark"]["total_return"], flag, summary))
        for t in result["full"]["trades"]:
            con.execute("INSERT INTO trades(run_id,ticker,template,win,entry_date,ret,reason,tags,vec,loser) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (run_id, ticker, template, t["window"], t["entry_date"], t["return_pct"] / 100, t["reason"], json.dumps(t["tags"]),
                         json.dumps(rules.tag_vector(t["tags"])), 1 if t["return_pct"] <= 0 else 0))
        for ls in lessons:
            con.execute("INSERT INTO lessons(ts,run_id,ticker,template,kind,text,tags) VALUES(?,?,?,?,?,?,?)",
                        (ts, run_id, ticker, template, ls["kind"], ls["text"], json.dumps(ls["tags"])))
        con.commit()
    # Meko mirror (background thread, one MCP session): summary + lessons + the 15 worst losing setups
    queued = False
    if meko.enabled():
        items = [("backtest_result", summary, dict(id=run_id, kind=kind, ticker=ticker, template=template, params=params, exits=exits,
                                                  train=tr["metrics"], locked=lk["metrics"], flag=flag))]
        items += [("lesson", ls["text"], dict(run_id=run_id, ticker=ticker, template=template, **ls)) for ls in lessons]
        losers = sorted(result["full"]["trades"], key=lambda t: t["return_pct"])[:15]
        meko.mirror_async(items)
        queued = True
    return dict(run_id=run_id, lessons=lessons, stored_in="sqlite", meko_mirror="queued" if queued else "off")


def list_runs(limit: int = 50, ticker: str | None = None, template: str | None = None, offset: int = 0) -> list[dict]:
    q, a = "SELECT * FROM runs WHERE 1=1", []
    if ticker: q += " AND ticker=?"; a.append(ticker)
    if template: q += " AND template=?"; a.append(template)
    q += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"; a.extend([limit, offset])
    with closing(_db()) as con:
        rows = con.execute(q, a).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("params", "exits", "train", "locked"):
            d[k] = json.loads(d[k])
        out.append(d)
    return out


def list_lessons(limit: int = 100, template: str | None = None, offset: int = 0, kind: str | None = None) -> dict:
    with closing(_db()) as con:
        q, a = " FROM lessons WHERE 1=1", []
        if template: q += " AND template=?"; a.append(template)
        if kind: q += " AND kind=?"; a.append(kind)
        total = con.execute("SELECT COUNT(*)" + q, a).fetchone()[0]
        rows = [dict(r) for r in con.execute("SELECT *" + q + " ORDER BY id DESC LIMIT ? OFFSET ?", a + [limit, offset]).fetchall()]
        agg = [dict(r) for r in con.execute(
            "SELECT template, kind, COUNT(*) n FROM lessons GROUP BY template, kind ORDER BY n DESC").fetchall()]
        ref = [dict(r) for r in con.execute(
            "SELECT template, SUM(CASE WHEN flag='overfit' THEN 1 ELSE 0 END) overfit, SUM(CASE WHEN flag='ok' THEN 1 ELSE 0 END) ok, COUNT(*) n FROM runs WHERE kind='refine' GROUP BY template").fetchall()]
    for r in rows:
        r["tags"] = json.loads(r["tags"])
    return dict(lessons=rows, total=total, counts=agg, refine_outcomes=ref)


def check(ticker: str, template: str, params: dict, exits: dict, current_tags: dict, as_of: str) -> dict:
    """'Before you run': compare this idea and today's setup with stored history."""
    warnings = []
    with closing(_db()) as con:
        runs = [dict(r) for r in con.execute("SELECT * FROM runs WHERE template=? ORDER BY ts DESC", (template,)).fetchall()]
        total_runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        trades = [dict(r) for r in con.execute("SELECT ticker, entry_date, ret, tags, vec, loser FROM trades WHERE template=?", (template,)).fetchall()]
        relevant = [dict(r) for r in con.execute("SELECT text, kind, ticker FROM lessons WHERE template=? ORDER BY id DESC LIMIT 5", (template,)).fetchall()]
    if total_runs == 0:
        return dict(warnings=[dict(level="info", text="Memory is empty: run a backtest and it will remember this idea.")], current_setup=dict(tags=current_tags, as_of=as_of),
                    similar=None, memory_runs=0, recent_lessons=[])
    same = [r for r in runs if r["ticker"] == ticker]
    exact = [r for r in same if json.loads(r["params"]) == params and json.loads(r["exits"]) == exits]
    if exact:
        r = exact[0]; lk = json.loads(r["locked"]); tr = json.loads(r["train"])
        warnings.append(dict(level="info", text=f"You already ran this exact setup on {ticker} ({time.strftime('%d %b', time.localtime(r['ts']))}): train {tr['total_return']*100:+.1f}%, locked year {lk['total_return']*100:+.1f}%."))
    ref_same = [r for r in same if r["kind"] == "refine"]
    if ref_same:
        ov = sum(1 for r in ref_same if r["flag"] == "overfit")
        if ov:
            warnings.append(dict(level="warn", text=f"{ov} of {len(ref_same)} earlier refinements of {template} on {ticker} were flagged OVERFIT. Tuned parameters have not held up here before."))
    bt = [r for r in runs if r["kind"] == "backtest"]
    if len(bt) >= 3:
        lost = sum(1 for r in bt if json.loads(r["locked"])["total_return"] < 0)
        beat = sum(1 for r in bt if json.loads(r["locked"])["total_return"] > r["bench_locked"])
        if lost / len(bt) >= 0.5:
            warnings.append(dict(level="warn", text=f"{lost} of {len(bt)} past {template} backtests lost money in their locked year; only {beat} beat buy and hold."))
        else:
            warnings.append(dict(level="info", text=f"Past {template} backtests: {lost} of {len(bt)} lost money in the locked year; {beat} beat buy and hold."))
    sim = None
    cv = rules.tag_vector(current_tags)
    if len(trades) >= 20:
        base_wr = sum(1 for t in trades if t["ret"] > 0) / len(trades)
        scored = [(cosine(cv, json.loads(t["vec"])), t) for t in trades]
        near = [(s, t) for s, t in scored if s >= 0.75]          # >= 3 of 4 tag groups match
        if near:
            wr = sum(1 for _, t in near if t["ret"] > 0) / len(near)
            avg = sum(t["ret"] for _, t in near) / len(near)
            losers = sorted([(s, t) for s, t in near if t["ret"] <= 0], key=lambda x: (-x[0], x[1]["ret"]))[:5]
            sim = dict(n=len(near), win_rate=wr, avg_return=avg, base_win_rate=base_wr, min_cosine=0.75,
                       examples=[dict(cosine=round(s, 2), ticker=t["ticker"], date=t["entry_date"], return_pct=round(t["ret"] * 100, 2), tags=json.loads(t["tags"])) for s, t in losers])
            desc = ", ".join(f"{g}={v}" for g, v in current_tags.items())
            if len(near) >= 8 and wr <= base_wr - 0.10:
                warnings.append(dict(level="warn", text=f"Today's setup on {ticker} ({desc}) is similar to {len(near)} past {template} trades that won only {wr*100:.0f}% (avg {avg*100:+.1f}%) vs {base_wr*100:.0f}% for the template overall."))
            elif len(near) >= 8 and wr >= base_wr + 0.10:
                warnings.append(dict(level="info", text=f"Today's setup on {ticker} ({desc}) resembles {len(near)} past {template} trades that won {wr*100:.0f}% vs {base_wr*100:.0f}% overall."))
            else:
                warnings.append(dict(level="info", text=f"Today's setup on {ticker} ({desc}) resembles {len(near)} past {template} trades; win rate {wr*100:.0f}% vs {base_wr*100:.0f}% overall (no clear signal)."))
    return dict(warnings=warnings, current_setup=dict(tags=current_tags, as_of=as_of), similar=sim, memory_runs=total_runs, recent_lessons=relevant)
