"""Live Strategy Tracker.

Rex NEVER places orders. You trade in your own broker app (Groww, Zerodha, ...). Rex tracks:
  * mode "logged": the real trade you tell it about ("I took this trade"): live P&L vs your entry, distance to your strategy's stop and target,
    and an alert when your strategy's exit rule fires ("Strategy says exit now").
  * mode "paper": no trade logged. Rex follows the strategy itself on real prices and simulates entries/exits (same engine rules as the backtest,
    15 bps cost) so you can see what it WOULD have done.
Closing a session saves the outcome and how price moved to memory (SQLite, Meko mirror) and, for a logged trade, to your journal.
Prices: yfinance (NSE data is delayed about 15 minutes). Offline / market closed: the last close, clearly labelled."""
from __future__ import annotations
import json, threading, time, uuid
from contextlib import closing
from datetime import datetime
import numpy as np
import pandas as pd
from . import costs, config, data, journal, livedata, memory, rules, strategy_memory as smem

T_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracker_sessions(id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT, mode TEXT, ticker TEXT, template TEXT,
  params TEXT, exits TEXT, cost_bps REAL, label TEXT, trade TEXT, state TEXT, last_quote TEXT, last_error TEXT, closed_at REAL, close TEXT);
CREATE TABLE IF NOT EXISTS tracker_fills(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL, date TEXT, ts_label TEXT, kind TEXT, side TEXT,
  price REAL, qty REAL, reason TEXT, pnl_pct REAL);
CREATE INDEX IF NOT EXISTS ix_tf_s ON tracker_fills(session_id);
CREATE TABLE IF NOT EXISTS tracker_ticks(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL, ts_label TEXT, price REAL);
CREATE INDEX IF NOT EXISTS ix_tt_s ON tracker_ticks(session_id);
"""
INTERVAL_S = float(__import__("os").getenv("REX_TRACK_INTERVAL", "60"))
MAX_TICKS = 400
_LOCK = threading.RLock()
_loop = dict(thread=None, stop=threading.Event(), last_run=None, last_error=None, runs=0)


class TrackerError(ValueError):
    pass


def _db():
    con = memory._db()
    con.executescript(T_SCHEMA)
    return con


# ---------------------------------------------------------------- exit logic (mirrors backtest._run_window)
def evaluate_exit(bars: pd.DataFrame, e: int | None, entry_px: float, hold: int, stop_pct, tp_pct, direction: int = 1,
                  entry_extremes: bool = True, price_now: float | None = None, live_last: bool = False) -> dict:
    """Would the strategy's exit rules have closed a position entered at bar index `e` (price `entry_px`)? Same order of checks as the backtest:
    gap at the open (stop first, then target), then intraday stop before target, then max-hold at the close of bar e+hold.
    `direction` +1 long, -1 short (levels mirrored). On the entry bar only the close is used unless `entry_extremes` (entry at that bar's open).
    e None or beyond the last bar means the entry bar does not exist yet: only `price_now` is compared with the levels."""
    sd = (stop_pct or 0) / 100.0
    td = (tp_pct or 0) / 100.0
    stop_px = entry_px * (1 - direction * sd) if stop_pct else None
    tp_px = entry_px * (1 + direction * td) if tp_pct else None
    n = len(bars)
    base = dict(stop_px=stop_px, tp_px=tp_px, fired=False, reason=None, px=None, idx=None, at_close=False)
    hit_stop = (lambda o, h, l: (l <= stop_px) if direction > 0 else (h >= stop_px))
    hit_tp = (lambda o, h, l: (h >= tp_px) if direction > 0 else (l <= tp_px))
    if e is None or e >= n:
        p = price_now
        base.update(days_held=0, hold_left=hold, last_idx=None)
        if p is not None:
            if stop_px is not None and hit_stop(p, p, p):
                base.update(fired=True, reason="stop", px=stop_px)
            elif tp_px is not None and hit_tp(p, p, p):
                base.update(fired=True, reason="take_profit", px=tp_px)
        return base
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    x_last = min(e + hold, n - 1)
    for j in range(e, x_last + 1):
        oj, hj, lj = (o[j], h[j], l[j]) if (j > e or entry_extremes) else (c[j], c[j], c[j])
        if j > e:
            if stop_px is not None and ((oj <= stop_px) if direction > 0 else (oj >= stop_px)):
                base.update(fired=True, reason="stop", px=float(oj), idx=j); break
            if tp_px is not None and ((oj >= tp_px) if direction > 0 else (oj <= tp_px)):
                base.update(fired=True, reason="take_profit", px=float(oj), idx=j); break
        if stop_px is not None and hit_stop(oj, hj, lj):
            base.update(fired=True, reason="stop", px=float(stop_px), idx=j); break
        if tp_px is not None and hit_tp(oj, hj, lj):
            base.update(fired=True, reason="take_profit", px=float(tp_px), idx=j); break
    if not base["fired"] and n - 1 >= e + hold:
        x = e + hold
        base.update(fired=True, reason="hold", px=float(c[x]), idx=x, at_close=bool(live_last and x == n - 1))
    base["days_held"] = n - 1 - e
    base["hold_left"] = max(hold - (n - 1 - e), 0)
    base["last_idx"] = n - 1
    return base


def _excursions(bars: pd.DataFrame, e: int | None, entry_px: float, direction: int, extremes: bool, prices: list[float]) -> dict:
    """Best / worst the price got versus the entry (percent, in the direction of the trade)."""
    hi = [p for p in prices if p]
    lo = list(hi)
    if e is not None and e < len(bars):
        for j in range(e, len(bars)):
            if j == e and not extremes:
                hi.append(float(bars["Close"].iloc[j])); lo.append(float(bars["Close"].iloc[j]))
            else:
                hi.append(float(bars["High"].iloc[j])); lo.append(float(bars["Low"].iloc[j]))
    if not hi:
        return dict(mfe_pct=None, mae_pct=None)
    up, dn = (max(hi) / entry_px - 1) * 100, (min(lo) / entry_px - 1) * 100
    return dict(mfe_pct=round(max(up, 0) if direction > 0 else max(-dn, 0), 2), mae_pct=round(min(dn, 0) if direction > 0 else min(-up, 0), 2))


def _net_pct(direction: int, entry: float, price: float, cost_bps: float, legs=None) -> float:
    if legs:
        return ((1 + direction * (price / entry - 1)) * (1 - legs[0]) * (1 - legs[1]) - 1) * 100
    return direction * (price / entry - 1) * 100 - cost_bps / 100.0


def _exit_text(res: dict, bars: pd.DataFrame, hold: int, stop_pct, tp_pct, direction: int, price: float) -> str | None:
    if not res["fired"]:
        return None
    when = None
    if res.get("idx") is not None and res["idx"] < len(bars):
        when = bars.index[res["idx"]].strftime("%d %b")
    r = res["reason"]
    if r == "stop":
        return f"Stop-loss level Rs {res['stop_px']:,.2f} ({stop_pct:g}% against your entry) was reached" + (f" on {when}." if when else ".")
    if r == "take_profit":
        return f"Target Rs {res['tp_px']:,.2f} ({tp_pct:g}% in your favour) was reached" + (f" on {when}." if when else ".")
    return (f"Max hold of {hold} trading days reached" + (f" ({when})" if when else "")
            + (": the strategy exits at today's close." if res.get("at_close") else "."))


# ---------------------------------------------------------------- persistence helpers
def _loads(s):
    return json.loads(s) if s else None


def _get(sid: str) -> dict:
    with closing(_db()) as con:
        r = con.execute("SELECT * FROM tracker_sessions WHERE id=?", (sid,)).fetchone()
    if not r:
        raise KeyError(sid)
    d = dict(r)
    for k in ("params", "exits", "trade", "state", "last_quote", "close"):
        d[k] = _loads(d[k])
    return d


def _save(s: dict) -> None:
    s["updated"] = time.time()
    with closing(_db()) as con:
        con.execute("UPDATE tracker_sessions SET updated=?, status=?, state=?, last_quote=?, last_error=?, closed_at=?, close=? WHERE id=?",
                    (s["updated"], s["status"], json.dumps(s["state"]), json.dumps(s["last_quote"]), s["last_error"], s.get("closed_at"),
                     json.dumps(s["close"]) if s.get("close") else None, s["id"]))
        con.commit()


def _fills(sid: str) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT * FROM tracker_fills WHERE session_id=? ORDER BY ts, id", (sid,)).fetchall()]


def _add_fill(sid: str, date: str, ts_label: str, kind: str, side: str, price: float, qty: float | None, reason: str, pnl_pct: float | None = None) -> None:
    with closing(_db()) as con:
        con.execute("INSERT INTO tracker_fills(session_id,ts,date,ts_label,kind,side,price,qty,reason,pnl_pct) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (sid, time.time(), date, ts_label, kind, side, round(float(price), 2), qty, reason, None if pnl_pct is None else round(pnl_pct, 2)))
        con.commit()


def _add_tick(sid: str, price: float, label: str) -> None:
    with closing(_db()) as con:
        last = con.execute("SELECT price, ts FROM tracker_ticks WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        if last and abs(last["price"] - price) < 1e-9 and time.time() - last["ts"] < 600:
            return
        con.execute("INSERT INTO tracker_ticks(session_id,ts,ts_label,price) VALUES(?,?,?,?)", (sid, time.time(), label, round(float(price), 2)))
        con.execute("DELETE FROM tracker_ticks WHERE session_id=? AND id NOT IN (SELECT id FROM tracker_ticks WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                    (sid, sid, MAX_TICKS))
        con.commit()


def _ticks(sid: str) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT ts, ts_label, price FROM tracker_ticks WHERE session_id=? ORDER BY id", (sid,)).fetchall()]


# ---------------------------------------------------------------- the engine step
def _dirnum(d: str) -> int:
    return 1 if d == "long" else -1


def _advance(s: dict, snap: dict) -> None:
    """Move the session forward using the completed bars (+ today's forming bar when live). Mutates s['state'] and writes fills."""
    st, ex = s["state"], s["exits"]
    bars_c: pd.DataFrame = snap["bars"]
    live = snap["live_bar"]
    q = snap["quote"]
    price = q["price"]
    allb = bars_c
    if live:
        allb = pd.concat([bars_c, pd.DataFrame([dict(Open=live["open"], High=live["high"], Low=live["low"], Close=live["close"], Volume=np.nan)],
                                               index=[pd.Timestamp(live["date"])])])
    n_c = len(bars_c)
    hold, stop, tp = int(ex["hold_days"]), ex.get("stop_pct"), ex.get("tp_pct")
    st["pending_close"] = False
    st["pending_signal"] = None
    sig = rules.signals(bars_c, s["template"], s["params"]) if s["mode"] == "paper" else None
    label_now = q["price_time"]
    for _ in range(60):
        pos = st.get("pos")
        if pos is None:
            if s["mode"] != "paper":
                break
            start = int(bars_c.index.searchsorted(pd.Timestamp(st["scan_from"]), side="left"))
            ks = np.flatnonzero(sig[start:]) + start if start < n_c else []
            if not len(ks):
                break
            k = int(ks[0]); e = k + 1
            if e >= len(allb):
                st["pending_signal"] = str(bars_c.index[k].date())     # entry happens at the NEXT open
                break
            entry = float(allb["Open"].iloc[e])
            d = str(allb.index[e].date())
            st["pos"] = dict(entry_px=entry, entry_date=d, qty=st.get("paper_qty"), direction="long", origin="signal", extremes=True,
                             signal_date=str(bars_c.index[k].date()))
            _add_fill(s["id"], d, f"{d} 09:15 IST (open)", "entry", "buy", entry, st.get("paper_qty"), f"strategy signal on {bars_c.index[k].date()} close")
            continue
        e = int(allb.index.searchsorted(pd.Timestamp(pos["entry_date"]), side="left"))
        e = None if e >= len(allb) else e
        direction = _dirnum(pos["direction"])
        res = evaluate_exit(allb, e, pos["entry_px"], hold, stop, tp, direction, pos.get("extremes", True), price, live_last=bool(live))
        st["exit"] = dict(fired=res["fired"], reason=res["reason"], px=res["px"], at_close=res["at_close"],
                          text=_exit_text(res, allb, hold, stop, tp, direction, price))
        if res["fired"] and "first_exit" not in st:
            st["first_exit"] = dict(reason=res["reason"], px=res["px"], seen=label_now, price_seen=price,
                                    date=str(allb.index[res["idx"]].date()) if res["idx"] is not None else q["last_close_date"],
                                    text=st["exit"]["text"])
        if s["mode"] == "paper" and res["fired"]:
            if res["at_close"]:                          # hold limit on the forming bar: exits at today's close, not before
                st["pending_close"] = True
                break
            x = res["idx"]
            xd = str(allb.index[x].date()) if x is not None else livedata.market_state()["ist_date"]
            xpx = res["px"] if res["px"] is not None else price
            pnl = _net_pct(direction, pos["entry_px"], xpx, s["cost_bps"], s["state"].get("cost_legs"))
            _add_fill(s["id"], xd, f"{xd} ({res['reason']})", "exit", "sell", xpx, pos.get("qty"), res["reason"], pnl)
            st.setdefault("closed_trades", []).append(dict(entry_date=pos["entry_date"], exit_date=xd, entry=pos["entry_px"], exit=float(xpx),
                                                           net_pct=round(pnl, 2), reason=res["reason"]))
            st["pos"] = None
            st.pop("first_exit", None); st["exit"] = None
            st["scan_from"] = xd
            if x is None or x >= n_c:                    # exit on the forming bar: re-entry only after that bar completes
                break
            continue
        break
    return


def _ensure_loop_state(s: dict) -> None:
    s["state"].setdefault("closed_trades", [])


def tick(sid: str, now: datetime | None = None) -> dict:
    """Refresh one active session: fetch the latest price (or last close), advance the engine, persist. Never raises for data problems."""
    with _LOCK:
        s = _get(sid)
        if s["status"] != "active":
            return s
        try:
            snap = livedata.snapshot(s["ticker"], now)
        except data.DataError as e:
            s["last_error"] = str(e)[:200]
            _save(s)
            return s
        s["last_error"] = None
        _ensure_loop_state(s)
        _advance(s, snap)
        q = dict(snap["quote"]); q["market"] = snap["market"]; q["fetched_at"] = livedata.now_ist(now).strftime("%Y-%m-%d %H:%M:%S IST")
        s["last_quote"] = q
        _add_tick(sid, q["price"], q["fetched_at"])
        _save(s)
        return s


# ---------------------------------------------------------------- views
def _parse_when(v, default: datetime) -> datetime:
    if not v:
        return default
    try:
        t = pd.Timestamp(v)
        t = t.tz_localize(livedata.IST) if t.tzinfo is None else t.tz_convert(livedata.IST)
        return t.to_pydatetime()
    except Exception:
        raise TrackerError(f"Could not read the time '{v}'. Use YYYY-MM-DD or YYYY-MM-DD HH:MM (IST).")


def _live_metrics(s: dict, snap: dict | None) -> dict | None:
    st = s["state"]; pos = st.get("pos")
    q = s.get("last_quote") or {}
    if not pos or not q:
        return None
    price = q["price"]
    direction = _dirnum(pos["direction"])
    ex = s["exits"]
    entry = pos["entry_px"]
    stop_px = entry * (1 - direction * ex["stop_pct"] / 100) if ex.get("stop_pct") else None
    tp_px = entry * (1 + direction * ex["tp_pct"] / 100) if ex.get("tp_pct") else None
    gross = direction * (price / entry - 1) * 100
    net = _net_pct(direction, pos["entry_px"], price, s["cost_bps"], st.get("cost_legs"))
    qty = pos.get("qty")
    return dict(direction=pos["direction"], entry_px=round(entry, 2), entry_date=pos["entry_date"], qty=qty, price=price,
                gross_pct=round(gross, 2), net_pct=round(net, 2), pnl_abs=None if not qty else round(net / 100 * entry * qty, 2),
                per_share=round(direction * (price - entry), 2), stop_px=None if stop_px is None else round(stop_px, 2),
                tp_px=None if tp_px is None else round(tp_px, 2),
                dist_to_stop_pct=None if stop_px is None else round(direction * (price - stop_px) / price * 100, 2),
                dist_to_tp_pct=None if tp_px is None else round(direction * (tp_px - price) / price * 100, 2),
                origin=pos.get("origin"), hold_days=ex["hold_days"])


def _chart(s: dict, fills: list[dict], ticks: list[dict]) -> dict:
    try:
        df, _ = data.load(s["ticker"])
    except data.DataError:
        return dict(points=[], markers=[], stop_px=None, tp_px=None)
    st = s["state"]; q = s.get("last_quote") or {}
    pos = st.get("pos")
    first = None
    for f in fills:
        first = f["date"]; break
    if pos and not first:
        first = pos["entry_date"]
    trade = s.get("trade") or {}
    first = first or trade.get("entry_date")
    tail = df.iloc[-70:]
    if first:
        i0 = int(df.index.searchsorted(pd.Timestamp(first)))
        tail = df.iloc[max(0, min(i0 - 25, len(df) - 70)):]
    pts = [dict(d=str(i.date()), close=round(float(v), 2)) for i, v in tail["Close"].items()]
    have = {p["d"] for p in pts}
    if q:                                           # the latest price is always drawn as the final "Now" point
        pts.append(dict(d="live", close=q["price"]))
        have.add("live")
    markers = []
    for f in fills:
        x = f["date"] if f["date"] in have else "live"
        markers.append(dict(x=x, y=f["price"], kind="entry" if f["kind"] in ("entry", "logged_entry") else "exit", label=f"{f['side'].upper()} {f['price']:,.2f}"))
    lm = _live_metrics(s, None)
    return dict(points=pts, markers=markers, stop_px=lm and lm["stop_px"], tp_px=lm and lm["tp_px"])


def _paper_summary(s: dict) -> dict:
    ct = s["state"].get("closed_trades", [])
    g = 1.0
    for t in ct:
        g *= 1 + t["net_pct"] / 100
    return dict(trades=len(ct), realized_pct=round((g - 1) * 100, 2) if ct else 0.0,
                wins=sum(1 for t in ct if t["net_pct"] > 0), closed_trades=ct)


def view(s: dict, full: bool = True) -> dict:
    st = s["state"]; q = s.get("last_quote") or {}
    live = _live_metrics(s, None)
    mk = (q.get("market") if q else None) or livedata.market_state()
    ex_sig = st.get("exit")
    out = dict(id=s["id"], status=s["status"], mode=s["mode"], ticker=s["ticker"], template=s["template"], params=s["params"], exits=s["exits"],
               cost_bps=s["cost_bps"], cost_model=st.get("cost_model", "bps"), slippage_bps=st.get("slippage_bps"), trade_value=st.get("trade_value"), cost_label=st.get("cost_label"), label=s["label"], created=datetime.fromtimestamp(s["created"], livedata.IST).strftime("%Y-%m-%d %H:%M IST"),
               updated=datetime.fromtimestamp(s["updated"], livedata.IST).strftime("%Y-%m-%d %H:%M:%S IST"),
               phase="open" if st.get("pos") else "watching", quote=q or None, market=mk, position=live,
               exit_signal=ex_sig, first_exit_signal=st.get("first_exit"), pending_close=st.get("pending_close", False),
               pending_signal=st.get("pending_signal"), summary=_paper_summary(s), last_error=s.get("last_error"),
               trade=s.get("trade"), close=s.get("close"), refresh_seconds=INTERVAL_S,
               disclaimer="Rex tracks prices and your strategy's rules. It never places orders: you trade in your own broker app. Not investment advice.",
               data_labels=[livedata.DELAY_NOTE, f"Market: {mk['label']} (IST now {mk['ist_now']})." + (f" Next open {mk['next_open']}." if mk.get("next_open") else "")])
    if q and q.get("stale"):
        out["data_labels"].append(f"Price shown is the {q['source']} ({q['price_time']}); it updates when the market opens.")
    if q and q.get("note"):
        out["data_labels"].append(q["note"].capitalize() + ".")
    if full:
        fl = _fills(s["id"])
        out["fills"] = fl
        out["chart"] = _chart(s, fl, _ticks(s["id"]))
        out["ticks"] = _ticks(s["id"])[-60:]
        if s["status"] == "active" and s["mode"] == "paper" and not st.get("pos"):
            out["entry_watch"] = _entry_watch(s)
    return out


def _entry_watch(s: dict) -> dict | None:
    try:
        df, _ = data.load(s["ticker"])
        sig = rules.signals(df, s["template"], s["params"])
        d = str(df.index[-1].date())
        return dict(date=d, met=bool(sig[-1]), text=(f"Entry condition is TRUE at the {d} close: the strategy would buy at the next open." if sig[-1]
                                                       else f"Entry condition is not met at the {d} close. Rex is watching for it."))
    except Exception:
        return None


def summary_row(s: dict) -> dict:
    v = view(s, full=False)
    pos = v["position"]
    return dict(id=v["id"], status=v["status"], mode=v["mode"], ticker=v["ticker"], template=v["template"], label=v["label"], created=v["created"],
                updated=v["updated"], phase=v["phase"], price=(v["quote"] or {}).get("price"), price_time=(v["quote"] or {}).get("price_time"),
                market_label=v["market"]["label"], open_pct=pos and pos["net_pct"], open_pnl=pos and pos["pnl_abs"], side=pos and pos["direction"],
                exit_now=bool(v["exit_signal"] and v["exit_signal"]["fired"]) and v["status"] == "active",
                realized_pct=v["summary"]["realized_pct"], paper_trades=v["summary"]["trades"],
                closed_net_pct=(v["close"] or {}).get("net_pct"), verdict=(v["close"] or {}).get("verdict"))


# ---------------------------------------------------------------- public API
def start(ticker: str, template: str, params: dict | None, exits: dict | None, cost_bps: float | None = None, label: str = "original",
          trade: dict | None = None, simulate_entry_now: bool = False, qty: float | None = None, now: datetime | None = None, cost_model: str | None = None, slippage_bps: float | None = None, trade_value: float | None = None) -> dict:
    try:
        p, e = rules.validate(template, params, exits)
        tk = data.normalise(ticker)
        snap = livedata.snapshot(tk, now)
    except (rules.SpecError, data.DataError) as ex:
        raise TrackerError(str(ex))
    try:
        cs = costs.resolve(cost_model, cost_bps, slippage_bps, trade_value, default_bps=config.COST_BPS_DEFAULT)
    except ValueError as ex:
        raise TrackerError(str(ex))
    cost = cs["cost_bps"]
    q = dict(snap["quote"]); q["market"] = snap["market"]; q["fetched_at"] = livedata.now_ist(now).strftime("%Y-%m-%d %H:%M:%S IST")
    bars = snap["bars"]
    st: dict = dict(pos=None, scan_from=str(bars.index[-1].date()), closed_trades=[], paper_qty=qty)
    st.update(cost_model=cost_model or "bps", slippage_bps=slippage_bps, trade_value=trade_value, cost_legs=cs["legs"], cost_label=cs["label"])
    nowi = livedata.now_ist(now)
    mode = "paper"
    tr = None
    notes = []
    if trade:
        mode = "logged"
        side = str(trade.get("side", "buy")).lower()
        if side not in ("buy", "sell"):
            raise TrackerError("side must be 'buy' or 'sell'")
        px = trade.get("entry_price")
        px = float(px) if px not in (None, "") else q["price"]
        if not (px > 0):
            raise TrackerError("entry_price must be positive")
        qy = trade.get("qty")
        qy = float(qy) if qy not in (None, "", 0) else None
        if qy is not None and qy <= 0:
            raise TrackerError("qty must be positive")
        when = _parse_when(trade.get("entered_at"), nowi)
        if when > nowi + pd.Timedelta(minutes=5):
            raise TrackerError("entered_at is in the future")
        direction = "long" if side == "buy" else "short"
        if abs(px / q["last_close"] - 1) > 0.35:
            notes.append(f"Your entry Rs {px:,.2f} is {abs(px / q['last_close'] - 1) * 100:.0f}% away from the last close Rs {q['last_close']:,.2f}: double-check the price.")
        tr = dict(side=side, direction=direction, entry_price=px, qty=qy, entered_at=when.strftime("%Y-%m-%d %H:%M IST"), entry_date=str(when.date()),
                  notes=notes)
        st["pos"] = dict(entry_px=px, entry_date=str(when.date()), qty=qy, direction=direction, origin="logged_trade", extremes=False)
        sid = uuid.uuid4().hex[:10]
        _add_fill(sid, str(when.date()), tr["entered_at"], "logged_entry", side, px, qy, "logged by you (trade taken in your own broker)")
    elif simulate_entry_now:
        st["pos"] = dict(entry_px=q["price"], entry_date=livedata.market_state(now)["ist_date"], qty=qty, direction="long", origin="manual_paper", extremes=False)
        sid = uuid.uuid4().hex[:10]
        _add_fill(sid, st["pos"]["entry_date"], f"{q['price_time']}", "entry", "buy", q["price"], qty, "paper entry at the latest price (you asked to start in position)")
    else:
        sid = uuid.uuid4().hex[:10]
    now_ts = time.time()
    with closing(_db()) as con:
        con.execute("INSERT INTO tracker_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, now_ts, now_ts, "active", mode, tk, template, json.dumps(p), json.dumps(e), cost, label, json.dumps(tr) if tr else None,
                     json.dumps(st), json.dumps(q), None, None, None))
        con.commit()
    with _LOCK:
        s = tick(sid, now)
    try:
        smem.record_run("tracker", tk, template, p, e, cost, verdict="TRACKING", df=None, anchor_date=str(bars.index[-1].date()),
                        outcome=dict(mode=mode, label=label, side=(tr or {}).get("side")), session_id=sid, run_id=f"trk_{sid}")
    except Exception:
        pass
    return view(_get(sid))


def list_sessions(status: str | None = None, limit: int = 100) -> list[dict]:
    with closing(_db()) as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM tracker_sessions" + (" WHERE status=?" if status else "") + " ORDER BY created DESC LIMIT ?",
                                            ((status, limit) if status else (limit,))).fetchall()]
    return [summary_row(_get(i)) for i in ids]


def get(sid: str, refresh: bool = False, now: datetime | None = None) -> dict:
    if refresh:
        tick(sid, now)
    return view(_get(sid))


def close(sid: str, exit_price: float | None = None, note: str | None = None, add_to_journal: bool = True, exited_at: str | None = None,
          now: datetime | None = None) -> dict:
    """Mark the session closed. Logged trade: records your exit (price you got in your broker, default latest price). Paper: closes any open
    simulated position at the latest price. Saves the outcome to strategy-run memory (+ journal for a logged trade)."""
    with _LOCK:
        s = tick(sid, now)
        if s["status"] != "active":
            raise TrackerError("This session is already closed.")
        st, ex = s["state"], s["exits"]
        q = s.get("last_quote") or {}
        if not q:
            raise TrackerError("No price available yet for this ticker.")
        nowi = livedata.now_ist(now)
        when = _parse_when(exited_at, nowi)
        xpx = float(exit_price) if exit_price not in (None, "") else q["price"]
        if not (xpx > 0):
            raise TrackerError("exit_price must be positive")
        pos = st.get("pos")
        trade = s.get("trade")
        outcome: dict = dict(mode=s["mode"], label=s["label"], note=note, closed_at=when.strftime("%Y-%m-%d %H:%M IST"))
        net = None
        d_exit = str(when.date())
        snap = None
        try:
            snap = livedata.snapshot(s["ticker"], now)
        except data.DataError:
            pass
        if pos:
            direction = _dirnum(pos["direction"])
            net = _net_pct(direction, pos["entry_px"], xpx, s["cost_bps"], s["state"].get("cost_legs"))
            kind_ = "logged_exit" if s["mode"] == "logged" else "exit"
            _add_fill(sid, d_exit, when.strftime("%Y-%m-%d %H:%M IST"), kind_, "sell" if direction > 0 else "buy", xpx, pos.get("qty"),
                      "you closed it in your broker" if s["mode"] == "logged" else "session stopped: closed at latest price", net)
            # what the strategy said, and how price moved while in the trade
            allb = snap["bars"] if snap else None
            sig_info = None
            exc = dict(mfe_pct=None, mae_pct=None)
            if allb is not None:
                if snap["live_bar"]:
                    lb = snap["live_bar"]
                    allb = pd.concat([allb, pd.DataFrame([dict(Open=lb["open"], High=lb["high"], Low=lb["low"], Close=lb["close"], Volume=np.nan)],
                                                         index=[pd.Timestamp(lb["date"])])])
                e = int(allb.index.searchsorted(pd.Timestamp(pos["entry_date"]), side="left"))
                e = None if e >= len(allb) else e
                res = evaluate_exit(allb, e, pos["entry_px"], int(ex["hold_days"]), ex.get("stop_pct"), ex.get("tp_pct"), direction,
                                    pos.get("extremes", True), q["price"], live_last=bool(snap["live_bar"]))
                if res["fired"]:
                    sd = str(allb.index[res["idx"]].date()) if res["idx"] is not None else q["last_close_date"]
                    sig_info = dict(reason=res["reason"], date=sd, px=round(res["px"], 2) if res["px"] else None,
                                    cost_of_waiting_pct=None if not res["px"] else round(direction * (xpx - res["px"]) / pos["entry_px"] * 100, 2))
                exc = _excursions(allb, e, pos["entry_px"], direction, pos.get("extremes", True), [t["price"] for t in _ticks(sid)] + [xpx])
            fe = st.get("first_exit")
            if sig_info is None and fe:
                sig_info = dict(reason=fe["reason"], date=fe["date"], px=None, cost_of_waiting_pct=None)
            hd = max((pd.Timestamp(d_exit) - pd.Timestamp(pos["entry_date"])).days, 0)
            outcome.update(side="buy" if direction > 0 else "sell", entry=round(pos["entry_px"], 2), exit=round(xpx, 2), qty=pos.get("qty"),
                           net_pct=round(net, 2), gross_pct=round(direction * (xpx / pos["entry_px"] - 1) * 100, 2), days_held=hd,
                           pnl_abs=None if not pos.get("qty") else round(net / 100 * pos["entry_px"] * pos["qty"], 2),
                           exit_reason="manual", exit_signal=sig_info, followed_strategy_exit=bool(sig_info) and (sig_info["cost_of_waiting_pct"] or 0) > -0.3,
                           stop_pct=ex.get("stop_pct"), **exc)
            if s["mode"] == "paper":
                st.setdefault("closed_trades", []).append(dict(entry_date=pos["entry_date"], exit_date=d_exit, entry=pos["entry_px"], exit=xpx,
                                                               net_pct=round(net, 2), reason="session stopped"))
            st["pos"] = None
        ps = _paper_summary(s)
        if s["mode"] == "paper":
            outcome.update(trades=ps["trades"], realized_pct=ps["realized_pct"], closed_trades=ps["closed_trades"])
            net = ps["realized_pct"] if ps["trades"] else None
        if net is None:
            verdict = "NO TRADE"
        else:
            verdict = "WON" if net > 0.05 else ("LOST" if net < -0.05 else "FLAT")
        outcome["verdict"] = verdict
        outcome["net_pct"] = None if net is None else round(net, 2)
        # memory: strategy-run row (updates the row created at start) + journal
        saved = dict(strategy_memory=None, journal=None)
        try:
            df = data.load(s["ticker"])[0]
        except data.DataError:
            df = None
        kind = "tracked_trade" if s["mode"] == "logged" and pos else "tracker"
        side_o = outcome.get("side", "buy")
        saved["strategy_memory"] = smem.record_run(kind, s["ticker"], s["template"], s["params"], ex, s["cost_bps"], verdict=verdict, df=df,
                                                   anchor_date=d_exit, outcome=outcome, session_id=sid, run_id=f"trk_{sid}", run_date=d_exit)
        lesson_row = dict(kind=kind, verdict=verdict, stats={}, outcome=outcome, fwd20=None, fwd5=None)
        saved["lesson"] = smem.lesson(lesson_row)
        if s["mode"] == "logged" and pos and add_to_journal:
            try:
                saved["journal"] = journal.add_tracked_trade(sid, s["ticker"], "long" if side_o == "buy" else "short", pos.get("qty"), pos["entry_date"], d_exit,
                                                             pos["entry_px"], xpx, s["cost_bps"], net_return_pct=net)
            except Exception as ex_:
                saved["journal"] = dict(added=False, reason=str(ex_)[:160])
        elif s["mode"] == "logged":
            saved["journal"] = dict(added=False, reason="you chose not to add it to your journal")
        outcome["saved"] = saved
        s["status"] = "closed"; s["closed_at"] = time.time(); s["close"] = outcome
        _save(s)
    return view(_get(sid))


# ---------------------------------------------------------------- background loop
def tick_all(now: datetime | None = None) -> int:
    with closing(_db()) as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM tracker_sessions WHERE status='active'").fetchall()]
    for i in ids:
        try:
            tick(i, now)
        except Exception as e:                     # one bad session must not stop the others
            _loop["last_error"] = f"{i}: {str(e)[:120]}"
    _loop["last_run"] = livedata.now_ist().strftime("%Y-%m-%d %H:%M:%S IST"); _loop["runs"] += 1
    return len(ids)


def _run_loop() -> None:
    while not _loop["stop"].is_set():
        try:
            tick_all()
        except Exception as e:
            _loop["last_error"] = str(e)[:160]
        _loop["stop"].wait(INTERVAL_S)


def start_loop() -> bool:
    if _loop["thread"] and _loop["thread"].is_alive():
        return False
    _loop["stop"].clear()
    th = threading.Thread(target=_run_loop, name="rex-tracker", daemon=True)
    th.start(); _loop["thread"] = th
    return True


def stop_loop() -> None:
    _loop["stop"].set()


def loop_status() -> dict:
    return dict(running=bool(_loop["thread"] and _loop["thread"].is_alive()), interval_s=INTERVAL_S, last_run=_loop["last_run"],
                last_error=_loop["last_error"], runs=_loop["runs"])
