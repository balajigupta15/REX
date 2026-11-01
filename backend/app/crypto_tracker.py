"""Crypto Tracker: live P&L for BTC / ETH / SOL, 24/7. Rex NEVER places orders: you trade on your own exchange.
  * mode "logged": the trade you took on your exchange ("I took this trade"): live P&L vs your entry, distance to your strategy's stop and target,
    and "Strategy says exit now" when its exit rule fires (stop / target touched, or max hold reached).
  * mode "paper": no trade logged; Rex follows the strategy on real candles and simulates entries/exits with the backtest's rules.
Prices are Binance public REST polled every few seconds (labelled real-time, not a websocket). Candles are UTC. Separate tables from the NSE tracker
(crypto_sessions / crypto_fills / crypto_ticks); reuses the NSE tracker's exit logic (evaluate_exit), P&L and excursion helpers unchanged."""
from __future__ import annotations
import json, os, threading, time, uuid
from contextlib import closing
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from . import crypto_data as cd, crypto_memory as cm, memory, rules, strategy_memory as sm
from .tracker import TrackerError, _excursions, _net_pct, evaluate_exit

T_SCHEMA = """
CREATE TABLE IF NOT EXISTS crypto_sessions(id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT, mode TEXT, symbol TEXT, interval TEXT, template TEXT,
  params TEXT, exits TEXT, cost_bps REAL, label TEXT, trade TEXT, state TEXT, last_quote TEXT, last_error TEXT, closed_at REAL, close TEXT);
CREATE TABLE IF NOT EXISTS crypto_fills(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL, ts_label TEXT, kind TEXT, side TEXT,
  price REAL, qty REAL, reason TEXT, pnl_pct REAL, at TEXT);
CREATE INDEX IF NOT EXISTS ix_cf_s ON crypto_fills(session_id);
CREATE TABLE IF NOT EXISTS crypto_ticks(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL, ts_label TEXT, price REAL);
CREATE INDEX IF NOT EXISTS ix_ct_s ON crypto_ticks(session_id);
"""
INTERVAL_S = float(os.getenv("REX_CRYPTO_TRACK_INTERVAL", "10"))
REFRESH_S = 3
MAX_TICKS = 600
_LOCK = threading.RLock()
_loop = dict(thread=None, stop=threading.Event(), last_run=None, last_error=None, runs=0)
DISCLAIMER = "Rex tracks prices and your strategy's rules. It never places orders: you trade on your own exchange. Educational, not investment advice."


def _db():
    con = memory._db()
    con.executescript(T_SCHEMA)
    return con


def _now() -> pd.Timestamp:
    return pd.Timestamp(cd._now_ms(), unit="ms")          # naive UTC


def _lbl(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")


def _dirnum(d: str) -> int:
    return 1 if d == "long" else -1


def _loads(s):
    return json.loads(s) if s else None


def _get(sid: str) -> dict:
    with closing(_db()) as con:
        r = con.execute("SELECT * FROM crypto_sessions WHERE id=?", (sid,)).fetchone()
    if not r:
        raise TrackerError("No such crypto tracker session")
    d = dict(r)
    for k in ("params", "exits", "trade", "state", "last_quote", "close"):
        d[k] = _loads(d[k])
    return d


def _save(s: dict) -> None:
    s["updated"] = time.time()
    with closing(_db()) as con:
        con.execute("UPDATE crypto_sessions SET updated=?, status=?, state=?, last_quote=?, last_error=?, closed_at=?, close=? WHERE id=?",
                    (s["updated"], s["status"], json.dumps(s["state"]), json.dumps(s["last_quote"]), s["last_error"], s.get("closed_at"),
                     json.dumps(s["close"]) if s.get("close") else None, s["id"]))
        con.commit()


def _add_fill(sid: str, at: pd.Timestamp, kind: str, side: str, price: float, qty, reason: str, pnl_pct: float | None = None) -> None:
    with closing(_db()) as con:
        con.execute("INSERT INTO crypto_fills(session_id,ts,ts_label,kind,side,price,qty,reason,pnl_pct,at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (sid, time.time(), _lbl(at), kind, side, round(float(price), 4), qty, reason, None if pnl_pct is None else round(pnl_pct, 2), str(pd.Timestamp(at))))
        con.commit()


def _fills(sid: str) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT * FROM crypto_fills WHERE session_id=? ORDER BY ts, id", (sid,)).fetchall()]


def _add_tick(sid: str, price: float) -> None:
    with closing(_db()) as con:
        last = con.execute("SELECT ts FROM crypto_ticks WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        if last and time.time() - last["ts"] < 1.0:
            return
        con.execute("INSERT INTO crypto_ticks(session_id,ts,ts_label,price) VALUES(?,?,?,?)", (sid, time.time(), _now().strftime("%H:%M:%S"), float(price)))
        con.execute("DELETE FROM crypto_ticks WHERE session_id=? AND id NOT IN (SELECT id FROM crypto_ticks WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                    (sid, sid, MAX_TICKS))
        con.commit()


def _ticks(sid: str) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT ts, ts_label, price FROM crypto_ticks WHERE session_id=? ORDER BY id", (sid,)).fetchall()]


# ---------------------------------------------------------------- snapshot + engine
def snapshot(symbol: str, interval: str) -> dict:
    bars, info = cd.load(symbol, interval)
    q = cd.quote(symbol)
    fm = cd.forming(symbol, interval)
    allb = bars
    if fm is not None and fm["ts"] > bars.index[-1]:
        row = pd.DataFrame([dict(Open=fm["Open"], High=max(fm["High"], q["price"]), Low=min(fm["Low"], q["price"]), Close=q["price"], Volume=fm["Volume"])], index=[fm["ts"]])
        allb = pd.concat([bars, row])
    else:
        fm = None
    return dict(bars=bars, allb=allb, forming=fm, quote=q, info=info)


def _iv_ms(interval: str) -> int:
    return cd.INTERVALS[interval]["ms"]


def _entry_index(allb: pd.DataFrame, entry_ts: pd.Timestamp, interval: str) -> int | None:
    """Index of the candle that contains `entry_ts` (None when that candle is not available yet)."""
    i = int(allb.index.searchsorted(entry_ts, side="right")) - 1
    if i < 0:
        return 0
    if entry_ts >= allb.index[i] + pd.Timedelta(milliseconds=_iv_ms(interval)):
        return None
    return i


def _exit_text(res: dict, allb: pd.DataFrame, hold: int, stop_pct, tp_pct, direction: int, interval: str) -> str | None:
    if not res["fired"]:
        return None
    when = None
    if res.get("idx") is not None and res["idx"] < len(allb):
        when = pd.Timestamp(allb.index[res["idx"]]).strftime("%d %b %H:%M UTC")
    r = res["reason"]
    if r == "stop":
        return f"Stop-loss level {res['stop_px']:,.2f} ({stop_pct:g}% against your entry) was reached" + (f" in the candle of {when}." if when else ".")
    if r == "take_profit":
        return f"Target {res['tp_px']:,.2f} ({tp_pct:g}% in your favour) was reached" + (f" in the candle of {when}." if when else ".")
    return f"Max hold of {hold} {interval} candles reached" + (f" ({when})" if when else "") + (": the strategy exits at this candle's close." if res.get("at_close") else ".")


def _levels_hit(res: dict, st: dict, entry: float, direction: int, stop_pct, tp_pct) -> dict:
    """Candle H/L are not used on the entry candle of a logged trade (we do not know when inside it you entered), so also compare the live price
    and the lowest / highest live price Rex has SEEN since you entered with the stop / target levels. Never overrides an earlier-firing exit."""
    if res["fired"]:
        return res
    lo, hi = st.get("seen_min"), st.get("seen_max")
    if lo is None:
        return res
    sp, tp = res["stop_px"], res["tp_px"]
    stop_hit = sp is not None and ((lo <= sp) if direction > 0 else (hi >= sp))
    tp_hit = tp is not None and ((hi >= tp) if direction > 0 else (lo <= tp))
    if stop_hit:
        res.update(fired=True, reason="stop", px=float(sp))
    elif tp_hit:
        res.update(fired=True, reason="take_profit", px=float(tp))
    return res


def _advance(s: dict, snap: dict) -> None:
    st, ex, iv = s["state"], s["exits"], s["interval"]
    bars, allb, fm, q = snap["bars"], snap["allb"], snap["forming"], snap["quote"]
    price = q["price"]
    n_c = len(bars)
    hold, stop, tp = int(ex["hold_days"]), ex.get("stop_pct"), ex.get("tp_pct")
    st["pending_close"] = False
    st["pending_signal"] = None
    sig = rules.signals(bars, s["template"], s["params"]) if s["mode"] == "paper" else None
    now = _now()
    pos = st.get("pos")
    if pos:                                                    # lowest / highest live price seen while in the trade
        st["seen_min"] = price if st.get("seen_min") is None else min(st["seen_min"], price)
        st["seen_max"] = price if st.get("seen_max") is None else max(st["seen_max"], price)
    for _ in range(60):
        pos = st.get("pos")
        if pos is None:
            if s["mode"] != "paper":
                break
            start = int(bars.index.searchsorted(pd.Timestamp(st["scan_from"]), side="left"))
            ks = np.flatnonzero(sig[start:]) + start if start < n_c else []
            if not len(ks):
                break
            k = int(ks[0]); e = k + 1
            if e >= len(allb):
                st["pending_signal"] = pd.Timestamp(bars.index[k]).strftime("%Y-%m-%d %H:%M UTC")       # entry happens at the NEXT candle's open
                break
            entry = float(allb["Open"].iloc[e])
            at = pd.Timestamp(allb.index[e])
            st["pos"] = dict(entry_px=entry, entry_ts=str(at), qty=st.get("paper_qty"), direction="long", origin="signal", extremes=True,
                             signal_ts=str(bars.index[k]))
            st["seen_min"] = st["seen_max"] = None
            _add_fill(s["id"], at, "entry", "buy", entry, st.get("paper_qty"), f"strategy signal at the close of the {pd.Timestamp(bars.index[k]).strftime('%d %b %H:%M')} candle")
            continue
        entry_ts = pd.Timestamp(pos["entry_ts"])
        e = _entry_index(allb, entry_ts, iv) if pos["origin"] == "logged_trade" else int(allb.index.searchsorted(entry_ts, side="left"))
        e = None if (e is None or e >= len(allb)) else e
        direction = _dirnum(pos["direction"])
        res = evaluate_exit(allb, e, pos["entry_px"], hold, stop, tp, direction, pos.get("extremes", True), price, live_last=fm is not None)
        res = _levels_hit(res, st, pos["entry_px"], direction, stop, tp) if not pos.get("extremes", True) else res
        st["exit"] = dict(fired=res["fired"], reason=res["reason"], px=res["px"], at_close=res["at_close"], text=_exit_text(res, allb, hold, stop, tp, direction, iv))
        if res["fired"] and "first_exit" not in st:
            st["first_exit"] = dict(reason=res["reason"], px=res["px"], seen=_lbl(now), price_seen=price, text=st["exit"]["text"])
        if s["mode"] == "paper" and res["fired"]:
            if res["at_close"]:                                # hold limit on the forming candle: exits at ITS close, not before
                st["pending_close"] = True
                break
            x = res["idx"]
            xat = pd.Timestamp(allb.index[x]) if x is not None else pd.Timestamp(allb.index[-1])
            xpx = res["px"] if res["px"] is not None else price
            pnl = _net_pct(direction, pos["entry_px"], xpx, s["cost_bps"])
            _add_fill(s["id"], now if (x is None or x >= n_c) else xat, "exit", "sell", xpx, pos.get("qty"), res["reason"], pnl)
            st.setdefault("closed_trades", []).append(dict(entry_ts=pos["entry_ts"], exit_ts=str(xat), entry=pos["entry_px"], exit=float(xpx),
                                                           net_pct=round(pnl, 2), reason=res["reason"]))
            st["pos"] = None
            st.pop("first_exit", None); st["exit"] = None
            st["scan_from"] = str(xat)
            if x is None or x >= n_c:                          # exit on the forming candle: re-entry only after that candle completes
                break
            continue
        break


def tick(sid: str) -> dict:
    with _LOCK:
        s = _get(sid)
        if s["status"] != "active":
            return s
        try:
            snap = snapshot(s["symbol"], s["interval"])
        except cd.CryptoError as e:
            s["last_error"] = str(e)[:200]
            _save(s)
            return s
        s["last_error"] = None
        s["state"].setdefault("closed_trades", [])
        _advance(s, snap)
        q = dict(snap["quote"])
        q["fetched_at"] = _lbl(_now())
        s["last_quote"] = q
        s["_snap"] = snap
        _add_tick(sid, q["price"])
        _save(s)
        return s


# ---------------------------------------------------------------- views
def _live_metrics(s: dict) -> dict | None:
    st = s["state"]; pos = st.get("pos"); q = s.get("last_quote") or {}
    if not pos or not q:
        return None
    price, ex, entry = q["price"], s["exits"], pos["entry_px"]
    d = _dirnum(pos["direction"])
    stop_px = entry * (1 - d * ex["stop_pct"] / 100) if ex.get("stop_pct") else None
    tp_px = entry * (1 + d * ex["tp_pct"] / 100) if ex.get("tp_pct") else None
    gross = d * (price / entry - 1) * 100
    net = gross - s["cost_bps"] / 100
    qty = pos.get("qty")
    held_s = max((_now() - pd.Timestamp(pos["entry_ts"])).total_seconds(), 0)
    return dict(direction=pos["direction"], entry_px=round(entry, 4), entry_ts=pos["entry_ts"], qty=qty, price=price, gross_pct=round(gross, 2), net_pct=round(net, 2),
                pnl_abs=None if not qty else round(net / 100 * entry * qty, 2), per_unit=round(d * (price - entry), 4),
                stop_px=None if stop_px is None else round(stop_px, 4), tp_px=None if tp_px is None else round(tp_px, 4),
                dist_to_stop_pct=None if stop_px is None else round(d * (price - stop_px) / price * 100, 2),
                dist_to_tp_pct=None if tp_px is None else round(d * (tp_px - price) / price * 100, 2),
                origin=pos.get("origin"), hold_candles=ex["hold_days"], held_hours=round(held_s / 3600, 2), unit="USDT")


def _paper_summary(s: dict) -> dict:
    ct = s["state"].get("closed_trades", [])
    g = 1.0
    for t in ct:
        g *= 1 + t["net_pct"] / 100
    return dict(trades=len(ct), realized_pct=round((g - 1) * 100, 2) if ct else 0.0, wins=sum(1 for t in ct if t["net_pct"] > 0), closed_trades=ct)


def _label_of(ts, iv: str) -> str:
    return pd.Timestamp(ts).strftime("%m-%d") if iv == "1d" else pd.Timestamp(ts).strftime("%m-%d %H:%M")


def _chart(s: dict, fills: list[dict], ticks: list[dict]) -> dict:
    iv = s["interval"]
    try:
        snap = s.get("_snap") or snapshot(s["symbol"], iv)
    except cd.CryptoError:
        return dict(points=[], markers=[], ticks=[], stop_px=None, tp_px=None, entry_px=None)
    allb = snap["allb"]
    first = None
    if fills:
        first = pd.Timestamp(fills[0]["at"])
    elif (s["state"].get("pos") or {}).get("entry_ts"):
        first = pd.Timestamp(s["state"]["pos"]["entry_ts"])
    keep = 90 if iv != "1d" else 70
    lo = max(0, len(allb) - keep)
    if first is not None:
        i0 = int(allb.index.searchsorted(first, side="right")) - 1
        lo = max(0, min(i0 - 20, len(allb) - keep))
    tail = allb.iloc[lo:]
    pts = [dict(d=_label_of(t, iv), close=round(float(v), 4)) for t, v in tail["Close"].items()]
    have = {p["d"] for p in pts}
    markers = []
    for f in fills:
        x = _label_of(pd.Timestamp(f["at"]).floor(f"{_iv_ms(iv) // 60000}min") if iv != "1d" else pd.Timestamp(f["at"]).floor("D"), iv)
        markers.append(dict(x=x if x in have else pts[-1]["d"], y=f["price"], kind="entry" if f["kind"] in ("entry", "logged_entry") else "exit",
                            label=f"{f['side'].upper()} {f['price']:,.2f}"))
    lm = _live_metrics(s)
    return dict(points=pts, markers=markers, ticks=[dict(t=t["ts_label"], p=t["price"]) for t in ticks[-240:]], stop_px=lm and lm["stop_px"], tp_px=lm and lm["tp_px"],
                entry_px=lm and lm["entry_px"], live=snap["forming"] is not None, last_label=pts[-1]["d"] if pts else None)


def view(s: dict, full: bool = True) -> dict:
    st = s["state"]; q = s.get("last_quote") or {}
    ex_sig = st.get("exit")
    iv = s["interval"]
    fm = (s.get("_snap") or {}).get("forming")
    closes_in = None
    if fm is not None:
        closes_in = max(int((pd.Timestamp(fm["ts"]) + pd.Timedelta(milliseconds=_iv_ms(iv)) - _now()).total_seconds()), 0)
    out = dict(id=s["id"], market="crypto", status=s["status"], mode=s["mode"], symbol=s["symbol"], ticker=s["symbol"], coin=cd.SYMBOLS[s["symbol"]]["name"], interval=iv,
               template=s["template"], params=s["params"], exits=s["exits"], cost_bps=s["cost_bps"], label=s["label"],
               created=_lbl(pd.Timestamp(s["created"], unit="s")), updated=_lbl(pd.Timestamp(s["updated"], unit="s")),
               phase="open" if st.get("pos") else "watching", quote=q or None, position=_live_metrics(s), exit_signal=ex_sig,
               alert=bool(ex_sig and ex_sig["fired"] and s["status"] == "active" and st.get("pos")), first_exit_signal=st.get("first_exit"),
               pending_close=st.get("pending_close", False), candle_closes_in_s=closes_in, pending_signal=st.get("pending_signal"), summary=_paper_summary(s),
               last_error=s.get("last_error"), trade=s.get("trade"), close=s.get("close"), refresh_seconds=REFRESH_S, disclaimer=DISCLAIMER,
               data_labels=[f"Real-time: Binance public price polled about every {REFRESH_S} s (REST, not a websocket). Source: {q.get('provider', 'n/a')}.",
                            "Crypto trades 24/7: there is no market open or closed state. All times are UTC.",
                            f"Strategy candles: {cd.INTERVALS[iv]['label']} ({iv}), closed candles only plus the forming one."])
    if q and q.get("stale"):
        out["data_labels"].append("Price shown is NOT live (network unavailable): " + q["provider"] + ".")
    if full:
        fl = _fills(s["id"]); tk = _ticks(s["id"])
        out["fills"] = fl
        out["chart"] = _chart(s, fl, tk)
        out["ticks"] = tk[-60:]
        if s["status"] == "active" and s["mode"] == "paper" and not st.get("pos"):
            out["entry_watch"] = _entry_watch(s)
    return out


def _entry_watch(s: dict) -> dict | None:
    try:
        df, _ = cd.load(s["symbol"], s["interval"])
        sig = rules.signals(df, s["template"], s["params"])
        d = cd.fmt_ts(df.index[-1], s["interval"])
        return dict(candle=d, met=bool(sig[-1]), text=(f"Entry condition is TRUE at the close of the {d} candle: the strategy buys at the next candle's open." if sig[-1]
                                                        else f"Entry condition is not met at the close of the {d} candle. Rex is watching for it."))
    except Exception:
        return None


def summary_row(s: dict) -> dict:
    v = view(s, full=False)
    pos = v["position"]
    return dict(id=v["id"], status=v["status"], mode=v["mode"], symbol=v["symbol"], coin=v["coin"], interval=v["interval"], template=v["template"], label=v["label"],
                created=v["created"], updated=v["updated"], phase=v["phase"], price=(v["quote"] or {}).get("price"), open_pct=pos and pos["net_pct"],
                open_pnl=pos and pos["pnl_abs"], side=pos and pos["direction"], exit_now=bool(v["alert"]), realized_pct=v["summary"]["realized_pct"],
                paper_trades=v["summary"]["trades"], closed_net_pct=(v["close"] or {}).get("net_pct"), verdict=(v["close"] or {}).get("verdict"))


# ---------------------------------------------------------------- public API
def _parse_when(v, default: pd.Timestamp) -> pd.Timestamp:
    if not v:
        return default
    try:
        t = pd.Timestamp(v)
        return (t.tz_convert("UTC").tz_localize(None) if t.tzinfo is not None else t)
    except Exception:
        raise TrackerError(f"Could not read the time '{v}'. Use YYYY-MM-DD HH:MM (UTC).")


def start(symbol: str, interval: str, template: str, params: dict | None, exits: dict | None, cost_bps: float | None = None, label: str = "original",
          trade: dict | None = None, qty: float | None = None) -> dict:
    try:
        p, e = rules.validate(template, params, exits)
        sym, iv = cd.normalise(symbol), cd.check_interval(interval)
        snap = snapshot(sym, iv)
    except (rules.SpecError, cd.CryptoError) as ex:
        raise TrackerError(str(ex))
    cost = cd.COST_BPS_DEFAULT if cost_bps is None else float(cost_bps)
    q = dict(snap["quote"]); q["fetched_at"] = _lbl(_now())
    bars = snap["bars"]
    st: dict = dict(pos=None, scan_from=str(bars.index[-1]), closed_trades=[], paper_qty=qty)
    mode, tr, sid = "paper", None, uuid.uuid4().hex[:10]
    now = _now()
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
        when = _parse_when(trade.get("entered_at"), now)
        if when > now + pd.Timedelta(minutes=5):
            raise TrackerError("entered_at is in the future")
        notes = []
        if abs(px / q["price"] - 1) > 0.35:
            notes.append(f"Your entry {px:,.2f} is {abs(px / q['price'] - 1) * 100:.0f}% away from the live price {q['price']:,.2f}: double-check the price.")
        direction = "long" if side == "buy" else "short"
        tr = dict(side=side, direction=direction, entry_price=px, qty=qy, entered_at=_lbl(when), notes=notes)
        st["pos"] = dict(entry_px=px, entry_ts=str(when), qty=qy, direction=direction, origin="logged_trade", extremes=False)
        _add_fill(sid, when, "logged_entry", side, px, qy, "logged by you (trade taken on your own exchange)")
    ts = time.time()
    with closing(_db()) as con:
        con.execute("INSERT INTO crypto_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, ts, ts, "active", mode, sym, iv, template, json.dumps(p), json.dumps(e), cost, label, json.dumps(tr) if tr else None,
                     json.dumps(st), json.dumps(q), None, None, None))
        con.commit()
    tick(sid)
    try:
        cm.record_run("tracker", sym, template, p, e, cost, verdict="TRACKING", anchor_date=str(cd.load(sym, "1d")[0].index[-1].date()),
                      outcome=dict(mode=mode, label=label, side=(tr or {}).get("side"), interval=iv), session_id=sid, run_id=f"ctrk_{sid}", interval=iv)
    except Exception:
        pass
    return view(_get(sid))


def list_sessions(status: str | None = None, limit: int = 100) -> list[dict]:
    with closing(_db()) as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM crypto_sessions" + (" WHERE status=?" if status else "") + " ORDER BY created DESC LIMIT ?",
                                            ((status, limit) if status else (limit,))).fetchall()]
    return [summary_row(_get(i)) for i in ids]


def get(sid: str, refresh: bool = False) -> dict:
    s = tick(sid) if refresh else _get(sid)
    return view(s)


def close(sid: str, exit_price: float | None = None, note: str | None = None, exited_at: str | None = None) -> dict:
    """Mark the session closed. Logged trade: records the exit you got on your exchange (default: latest price). Paper: closes an open simulated
    position at the latest price. The outcome and what price did next are saved to the crypto strategy-run memory."""
    with _LOCK:
        s = tick(sid)
        if s["status"] != "active":
            raise TrackerError("This session is already closed.")
        st, ex = s["state"], s["exits"]
        q = s.get("last_quote") or {}
        if not q:
            raise TrackerError("No price available yet for this coin.")
        when = _parse_when(exited_at, _now())
        xpx = float(exit_price) if exit_price not in (None, "") else q["price"]
        if not (xpx > 0):
            raise TrackerError("exit_price must be positive")
        pos, outcome, net = st.get("pos"), dict(mode=s["mode"], label=s["label"], note=note, closed_at=_lbl(when), interval=s["interval"], market="crypto"), None
        if pos:
            d = _dirnum(pos["direction"])
            net = _net_pct(d, pos["entry_px"], xpx, s["cost_bps"])
            _add_fill(sid, when, "logged_exit" if s["mode"] == "logged" else "exit", "sell" if d > 0 else "buy", xpx, pos.get("qty"),
                      "you closed it on your exchange" if s["mode"] == "logged" else "session stopped: closed at latest price", net)
            snap = s.get("_snap")
            sig_info, exc = None, dict(mfe_pct=None, mae_pct=None)
            if snap is not None:
                allb = snap["allb"]
                et = pd.Timestamp(pos["entry_ts"])
                e = _entry_index(allb, et, s["interval"]) if pos["origin"] == "logged_trade" else int(allb.index.searchsorted(et, side="left"))
                e = None if (e is None or e >= len(allb)) else e
                res = evaluate_exit(allb, e, pos["entry_px"], int(ex["hold_days"]), ex.get("stop_pct"), ex.get("tp_pct"), d, pos.get("extremes", True), q["price"],
                                    live_last=snap["forming"] is not None)
                res = _levels_hit(res, st, pos["entry_px"], d, ex.get("stop_pct"), ex.get("tp_pct")) if not pos.get("extremes", True) else res
                if res["fired"]:
                    sig_info = dict(reason=res["reason"], date=_lbl(allb.index[res["idx"]]) if res["idx"] is not None else q["fetched_at"],
                                    px=round(res["px"], 4) if res["px"] else None,
                                    cost_of_waiting_pct=None if not res["px"] else round(d * (xpx - res["px"]) / pos["entry_px"] * 100, 2))
                exc = _excursions(allb, e, pos["entry_px"], d, pos.get("extremes", True), [t["price"] for t in _ticks(sid)] + [xpx])
            fe = st.get("first_exit")
            if sig_info is None and fe:
                sig_info = dict(reason=fe["reason"], date=fe["seen"], px=None, cost_of_waiting_pct=None)
            held_h = max((when - pd.Timestamp(pos["entry_ts"])).total_seconds() / 3600, 0)
            outcome.update(side="buy" if d > 0 else "sell", entry=round(pos["entry_px"], 4), exit=round(xpx, 4), qty=pos.get("qty"), net_pct=round(net, 2),
                           gross_pct=round(d * (xpx / pos["entry_px"] - 1) * 100, 2), days_held=round(held_h / 24, 1), hours_held=round(held_h, 1),
                           pnl_abs=None if not pos.get("qty") else round(net / 100 * pos["entry_px"] * pos["qty"], 2), exit_reason="manual", exit_signal=sig_info,
                           followed_strategy_exit=bool(sig_info) and (sig_info["cost_of_waiting_pct"] or 0) > -0.3, stop_pct=ex.get("stop_pct"), **exc)
            if s["mode"] == "paper":
                st.setdefault("closed_trades", []).append(dict(entry_ts=pos["entry_ts"], exit_ts=str(when), entry=pos["entry_px"], exit=xpx, net_pct=round(net, 2),
                                                               reason="session stopped"))
            st["pos"] = None
        ps = _paper_summary(s)
        if s["mode"] == "paper":
            outcome.update(trades=ps["trades"], realized_pct=ps["realized_pct"], closed_trades=ps["closed_trades"])
            net = ps["realized_pct"] if ps["trades"] else None
        verdict = "NO TRADE" if net is None else ("WON" if net > 0.05 else ("LOST" if net < -0.05 else "FLAT"))
        outcome["verdict"] = verdict
        outcome["net_pct"] = None if net is None else round(net, 2)
        kind = "tracked_trade" if s["mode"] == "logged" and pos else "tracker"
        saved = dict(strategy_memory=cm.record_run(kind, s["symbol"], s["template"], s["params"], ex, s["cost_bps"], verdict=verdict, outcome=outcome, session_id=sid,
                                                   run_id=f"ctrk_{sid}", run_date=str(when.date()), interval=s["interval"]),
                     journal=dict(added=False, reason="the trade journal is for NSE stocks; crypto outcomes are saved to crypto memory"))
        saved["lesson"] = sm.lesson(dict(kind=kind, verdict=verdict, stats={}, outcome=outcome, fwd20=None, fwd5=None)).replace("stock", "coin")
        outcome["saved"] = saved
        s["status"] = "closed"; s["closed_at"] = time.time(); s["close"] = outcome
        _save(s)
    return view(s)


# ---------------------------------------------------------------- background loop
def tick_all() -> int:
    with closing(_db()) as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM crypto_sessions WHERE status='active'").fetchall()]
    for i in ids:
        try:
            tick(i)
        except Exception as e:
            _loop["last_error"] = f"{i}: {str(e)[:120]}"
    _loop["last_run"] = _lbl(_now()); _loop["runs"] += 1
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
    th = threading.Thread(target=_run_loop, name="rex-crypto-tracker", daemon=True)
    th.start(); _loop["thread"] = th
    return True


def stop_loop() -> None:
    _loop["stop"].set()


def loop_status() -> dict:
    return dict(running=bool(_loop["thread"] and _loop["thread"].is_alive()), interval_s=INTERVAL_S, last_run=_loop["last_run"], last_error=_loop["last_error"], runs=_loop["runs"])
