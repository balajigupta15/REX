"""Strategy-run memory. Every backtest, refinement and tracker session is stored (SQLite is the source of truth, Meko mirror when
MEKO_TOKEN is set) together with what the REAL price did afterwards (5 and 20 trading days). Before the next run of a similar strategy
(same template or ticker) the app shows a "Last time" panel with a one-line, rule-based lesson. No LLM needed."""
from __future__ import annotations
import json, math, time, uuid
from contextlib import closing
from datetime import datetime
import pandas as pd
from . import config, data, memory, meko

S_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_runs(id TEXT PRIMARY KEY, ts REAL, run_date TEXT, kind TEXT, source TEXT, ticker TEXT, template TEXT,
  params TEXT, exits TEXT, cost_bps REAL, verdict TEXT, stats TEXT, anchor_date TEXT, fwd5 REAL, fwd20 REAL, outcome TEXT,
  session_id TEXT, headline TEXT);
CREATE INDEX IF NOT EXISTS ix_sr_tk ON strategy_runs(ticker, template);
"""
KIND_LABEL = dict(backtest="backtest", refine="refine", tracker="tracker session", tracked_trade="tracked trade")


def _db():
    con = memory._db()
    con.executescript(S_SCHEMA)
    return con


# ---------------------------------------------------------------- verdicts + stats
def verdict_for_result(result: dict, flag: str | None = None) -> str:
    if flag:
        return {"overfit": "OVERFIT", "inconclusive": "INCONCLUSIVE", "ok": "HELD UP", "no_change": "NO CHANGE"}.get(flag, flag.upper())
    tr, lk = result["train"]["metrics"], result["locked"]["metrics"]
    if lk["trades"] < 3:
        return "INCONCLUSIVE"
    if tr["sharpe"] > 0.5 and lk["sharpe"] < 0:
        return "FADED"
    return "WORKED" if lk["total_return"] > 0 else "LOST"


def stats_for_result(result: dict) -> dict:
    tr, lk = result["train"]["metrics"], result["locked"]["metrics"]
    return dict(train_return=round(tr["total_return"] * 100, 2), test_return=round(lk["total_return"] * 100, 2), test_sharpe=round(lk["sharpe"], 2),
                train_sharpe=round(tr["sharpe"], 2), test_trades=lk["trades"], test_win_rate=None if lk["win_rate"] is None else round(lk["win_rate"] * 100, 1),
                buy_hold_test=round(result["locked"]["benchmark"]["total_return"] * 100, 2), test_start=result["locked"]["start"], test_end=result["locked"]["end"])


# ---------------------------------------------------------------- forward returns from real data
def forward_returns(df: pd.DataFrame, anchor_date: str) -> dict:
    """Price move after the bar dated `anchor_date` (the last bar on or before it). fwd5/fwd20 are None until that many bars exist."""
    c = df["Close"].to_numpy(dtype=float)
    i = int(df.index.searchsorted(pd.Timestamp(anchor_date), side="right")) - 1
    if i < 0:
        return dict(fwd5=None, fwd20=None, days_since=0, since_pct=None, base_date=None)
    f = lambda k: round((c[i + k] / c[i] - 1) * 100, 2) if i + k < len(c) else None
    ds = len(c) - 1 - i
    return dict(fwd5=f(5), fwd20=f(20), days_since=ds, since_pct=round((c[-1] / c[i] - 1) * 100, 2) if ds > 0 else None, base_date=str(df.index[i].date()))


def _fmt_date(s: str | None) -> str:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")
    except Exception:
        return str(s)


def _short(t: str) -> str:
    return t.replace(".NS", "")


# ---------------------------------------------------------------- write
def record_run(kind: str, ticker: str, template: str, params: dict, exits: dict, cost_bps: float, result: dict | None = None, flag: str | None = None,
               df: pd.DataFrame | None = None, anchor_date: str | None = None, verdict: str | None = None, stats: dict | None = None,
               outcome: dict | None = None, session_id: str | None = None, source: str = "live", ts: float | None = None,
               run_date: str | None = None, run_id: str | None = None) -> dict:
    """Insert (or, when `run_id` exists, update) one strategy run. Never raises into the caller's request path for Meko problems."""
    verdict = verdict or (verdict_for_result(result, flag) if result else "TRACKING")
    stats = stats if stats is not None else (stats_for_result(result) if result else {})
    anchor = anchor_date or (str(df.index[-1].date()) if df is not None else None)
    fw = dict(fwd5=None, fwd20=None)
    if df is not None and anchor:
        fw = forward_returns(df, anchor)
    ts = ts or time.time()
    run_date = run_date or datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    rid = run_id or uuid.uuid4().hex[:12]
    row = dict(id=rid, ts=ts, run_date=run_date, kind=kind, source=source, ticker=ticker, template=template, params=params, exits=exits, cost_bps=cost_bps,
               verdict=verdict, stats=stats, anchor_date=anchor, fwd5=fw["fwd5"], fwd20=fw["fwd20"], outcome=outcome, session_id=session_id)
    head = headline(row, fw if df is not None else None)
    with closing(_db()) as con:
        con.execute("INSERT OR REPLACE INTO strategy_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, ts, run_date, kind, source, ticker, template, json.dumps(params), json.dumps(exits), cost_bps, verdict, json.dumps(stats), anchor,
                     fw["fwd5"], fw["fwd20"], json.dumps(outcome) if outcome else None, session_id, head))
        con.commit()
    mirror = "off"
    if source == "live" and meko.enabled():
        meko.mirror_async([("strategy_run", head, dict(id=rid, kind=kind, ticker=ticker, template=template, verdict=verdict, run_date=run_date, stats=stats,
                                                       anchor_date=anchor, outcome=outcome))])
        mirror = "queued"
    return dict(run_id=rid, verdict=verdict, headline=head, stored_in="sqlite", meko_mirror=mirror)


# ---------------------------------------------------------------- read
def _row(r) -> dict:
    d = dict(r)
    for k in ("params", "exits", "stats", "outcome"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def _refresh_forward(rows: list[dict]) -> None:
    """Fill forward returns that were unknown when the run was saved, as soon as the real data has enough bars. Updates SQLite."""
    cache: dict[str, pd.DataFrame | None] = {}
    for r in rows:
        r["days_since"] = None; r["since_pct"] = None
        if not r.get("anchor_date"):
            continue
        tk = r["ticker"]
        if tk not in cache:
            try:
                cache[tk] = data.load(tk)[0]
            except data.DataError:
                cache[tk] = None
        df = cache[tk]
        if df is None:
            continue
        fw = forward_returns(df, r["anchor_date"])
        r["days_since"], r["since_pct"] = fw["days_since"], fw["since_pct"]
        if (fw["fwd5"], fw["fwd20"]) != (r["fwd5"], r["fwd20"]) and (fw["fwd5"] is not None):
            r["fwd5"], r["fwd20"] = fw["fwd5"], fw["fwd20"]
            with closing(_db()) as con:
                con.execute("UPDATE strategy_runs SET fwd5=?, fwd20=? WHERE id=?", (r["fwd5"], r["fwd20"], r["id"])); con.commit()


def headline(r: dict, fw: dict | None = None) -> str:
    v, st = r["verdict"], r.get("stats") or {}
    when = _fmt_date(r["run_date"])
    if r["kind"] == "tracked_trade":
        o = r.get("outcome") or {}
        base = f"On {when} you took {o.get('side', 'buy').upper()} {_short(r['ticker'])} ({r['template']}) in your broker: {v}"
        if o.get("net_pct") is not None:
            base += f", {o['net_pct']:+.1f}% in {o.get('days_held', 0)}d"
    elif r["kind"] == "tracker":
        base = f"On {when} you tracked {r['template']} on {_short(r['ticker'])}: {v}"
        o = r.get("outcome") or {}
        if o.get("realized_pct") is not None and o.get("trades"):
            base += f", {o['realized_pct']:+.1f}% over {o['trades']} paper trade(s)"
    else:
        verb = "refined" if r["kind"] == "refine" else "ran"
        if r.get("source") == "replay":
            base = f"As of {when} (replayed from real data) {r['template']}{' refined' if r['kind'] == 'refine' else ''} on {_short(r['ticker'])}: {v}"
        else:
            base = f"On {when} you {verb} {r['template']} on {_short(r['ticker'])}: {v}"
        if st.get("test_return") is not None:
            base += f", test {st['test_return']:+.1f}%"
    fw = fw or {}
    f20, f5 = r.get("fwd20", fw.get("fwd20")), r.get("fwd5", fw.get("fwd5"))
    if f20 is not None:
        tail = f"price then moved {f20:+.1f}% in 20 days"
    elif f5 is not None:
        tail = f"price then moved {f5:+.1f}% in 5 days (20-day figure not available yet)"
    elif (fw.get("since_pct") is not None) and fw.get("days_since"):
        tail = f"price has moved {fw['since_pct']:+.1f}% since ({fw['days_since']} trading days so far)"
    else:
        tail = "not enough trading days have passed yet to see how price moved"
    return f"{base}; {tail}."


def lesson(r: dict) -> str:
    """One rule-based line from the stored verdict, stats and what price did next."""
    v, st, f20 = r["verdict"], r.get("stats") or {}, r.get("fwd20")
    f = f20 if f20 is not None else r.get("fwd5")
    if r["kind"] == "tracked_trade":
        o = r.get("outcome") or {}
        net, sig = o.get("net_pct"), o.get("exit_signal")
        if sig and sig.get("cost_of_waiting_pct") is not None and sig["cost_of_waiting_pct"] < -0.3:
            return f"The strategy's exit fired on {sig['date']} but you held longer; that cost about {abs(sig['cost_of_waiting_pct']):.1f}%. Next time act on the exit alert or set a rule for ignoring it."
        if o.get("exit_reason") == "manual" and o.get("mfe_pct") is not None and net is not None and o["mfe_pct"] >= 2.0 and o["mfe_pct"] - net > 1.5:
            return f"Price was up {o['mfe_pct']:+.1f}% at its best but you closed at {net:+.1f}%: consider taking profit at the strategy's target instead of waiting."
        if net is not None and net < 0 and o.get("mae_pct") is not None and o["mae_pct"] < -(o.get("stop_pct") or 99):
            return "Price went past your strategy's stop level before you closed. Honour the stop next time; it is the one rule that protects the account."
        if net is not None and net > 0:
            return "A winner that followed the plan is worth repeating; keep the same position size so the result stays comparable."
        return "A loss inside the plan is a cost of doing business; compare it with the strategy's average loss before changing the rules."
    out = None
    if v == "OVERFIT":
        out = "The tuned version fell apart on the locked year. Do not trust tuned parameters here: keep the original rules or simplify instead of tuning more."
    elif v == "FADED":
        out = "It looked good in training but lost in the last year, so the edge may have faded. Ask for more than a good training Sharpe."
    elif v == "LOST":
        out = "It lost money on the locked year. Change the rule or the stock before trusting it" + (
            "; price still rose afterwards, so the rule, not the stock, was the problem." if f is not None and f > 2 else ".")
    elif v == "INCONCLUSIVE":
        out = "Too few trades in the locked year to judge. Loosen the entry rule or test more stocks before drawing a conclusion."
    elif v in ("WORKED", "HELD UP"):
        out = "It held up on the locked year" + ("; price fell afterwards, so past strength was no guarantee." if f is not None and f < -2 else
                                                 "; re-run it on today's data before relying on that.")
    elif v == "NO CHANGE":
        out = "Your original parameters were already the best nearby; the edge, if any, comes from the rule itself."
    elif v == "TRACKING":
        out = "This was only tracked, not closed yet. Close the session with the outcome so the lesson is saved."
    return out or "No lesson recorded for this run."


def query(ticker: str | None = None, template: str | None = None, limit: int = 8, exclude_id: str | None = None, offset: int = 0) -> dict:
    t = data.normalise(ticker) if ticker else None
    with closing(_db()) as con:
        rows = [_row(r) for r in con.execute("SELECT * FROM strategy_runs ORDER BY ts DESC").fetchall()]
    if exclude_id:
        rows = [r for r in rows if r["id"] != exclude_id]

    def score(r):
        same_t, same_p = (t and r["ticker"] == t), (template and r["template"] == template)
        return 3 if same_t and same_p else 2 if same_t else 1 if same_p else 0
    if t or template:
        rows = [r for r in rows if score(r) > 0]
    rows.sort(key=lambda r: (-score(r), -r["ts"]))
    total = len(rows)
    rows = rows[offset:offset + limit]
    _refresh_forward(rows)
    for r in rows:
        m = score(r)
        r["match"] = {3: "same ticker and strategy", 2: "same ticker", 1: "same strategy"}.get(m, "recent")
        r["headline"] = headline(r, dict(days_since=r.get("days_since"), since_pct=r.get("since_pct")))
        r["lesson"] = lesson(r)
        r["replayed"] = r["source"] == "replay"
        r["run_date_label"] = _fmt_date(r["run_date"])
    return dict(count=len(rows), total=total, runs=rows, last_time=rows[0] if rows else None,
                backend=("meko+" if (meko.enabled() and meko._state.get("connected")) else "") + db.backend(),
                note="Forward returns come from real yfinance closes after each run's data date. 'replayed' rows are real past runs re-computed as of an earlier date "
                     "(only data up to that date) so history exists on a fresh install.")


def count() -> int:
    with closing(_db()) as con:
        return con.execute("SELECT COUNT(*) FROM strategy_runs").fetchone()[0]


# ---------------------------------------------------------------- seed: honest as-of replays
REPLAY_CUTS = (45, 20)          # trading days before the last bar at which the replayed run "happened"
REPLAY_PLAN = [("breakout", {}, {}), ("ma_cross", {}, {}), ("rsi", {}, {}), ("mean_reversion", {}, {})]


def seed_replays(tickers: list[str] | None = None, plan=None, cuts=REPLAY_CUTS, refine_too: bool = True) -> dict:
    """Re-run real backtests using ONLY data up to an earlier date and store them as dated runs (source='replay'). The forward returns are then
    the real prices that followed. Idempotent: skips ids already present."""
    from . import backtest, refine, rules
    n = 0
    skipped = []
    for tk in tickers or config.DEFAULT_TICKERS:
        try:
            df, info = data.load(tk)
        except data.DataError as e:
            skipped.append(f"{tk}: {e}"); continue
        for k in cuts:
            cut = len(df) - k
            if cut < config.MIN_BARS:
                continue
            sub = df.iloc[:cut]
            when = sub.index[-1]
            ts = pd.Timestamp(when).replace(hour=12).timestamp()
            for tpl, p0, e0 in (plan or REPLAY_PLAN):
                rid = f"rp_{info['ticker']}_{tpl}_{when.date()}"
                with closing(_db()) as con:
                    if con.execute("SELECT 1 FROM strategy_runs WHERE id=?", (rid,)).fetchone():
                        continue
                p, e = rules.validate(tpl, p0, e0)
                res = backtest.run(sub, tpl, p, e, keep_series=False)
                flag = None
                kind = "backtest"
                if refine_too and k == cuts[0] and tpl in ("breakout", "mean_reversion") and tk in config.DEFAULT_TICKERS[:5]:
                    rf = refine.refine(sub, tpl, p, e)
                    res, flag, kind, p, e = rf["refined"]["result"], rf["verdict"]["flag"], "refine", rf["refined"]["params"], rf["refined"]["exits"]
                record_run(kind, info["ticker"], tpl, p, e, config.COST_BPS_DEFAULT, res, flag=flag, df=df, anchor_date=str(when.date()), source="replay",
                           ts=ts, run_date=str(when.date()), run_id=rid); n += 1
    return dict(replayed_runs=n, skipped=skipped, total=count())
