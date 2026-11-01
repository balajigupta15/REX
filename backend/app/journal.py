"""Trade journal: import a broker tradebook CSV, pair fills into round trips, fingerprint the market context at entry
(from yfinance daily bars), store in SQLite (+ best-effort Meko mirror), and run a rule-based pre-trade check.
No LLM. Rex is a memory/strategy layer: it never places orders."""
from __future__ import annotations
import csv, hashlib, io, json, math, re, time, uuid
from collections import defaultdict
from contextlib import closing
from datetime import date as _date
import numpy as np
import pandas as pd
from . import challenger, config, data, meko, memory, rules

SAMPLE_PATH = config.BASE / "sample" / "SAMPLE_tradebook_synthetic_journal_real_prices.csv"

J_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_imports(id TEXT PRIMARY KEY, ts REAL, filename TEXT, is_sample INTEGER, rows_read INTEGER,
  rows_rejected INTEGER, round_trips INTEGER, new_trades INTEGER, duplicates INTEGER, open_positions TEXT);
CREATE TABLE IF NOT EXISTS journal_trades(id INTEGER PRIMARY KEY AUTOINCREMENT, import_id TEXT, uniq TEXT UNIQUE, is_sample INTEGER,
  symbol TEXT, ticker TEXT, direction TEXT, qty REAL, entry_date TEXT, exit_date TEXT, avg_entry REAL, avg_exit REAL, pnl REAL,
  ret REAL, hold_days INTEGER, n_fills INTEGER, tags TEXT, metrics TEXT, vec TEXT, context_ok INTEGER);
CREATE INDEX IF NOT EXISTS ix_jt_dir ON journal_trades(direction);
"""


def _db():
    con = memory._db()
    con.executescript(J_SCHEMA)
    return con


# ---------------------------------------------------------------- fingerprint
JGROUPS = {
    "trend_dma": ["above_both", "pullback_in_uptrend", "bounce_in_downtrend", "below_both"],
    "extension": ["stretched", "near", "depressed"],
    "volume": ["spike", "normal", "dry"],
    "volatility": ["calm", "normal", "stressed"],
    "gap": ["gap_up", "flat_open", "gap_down"],
}
LABEL = {
    ("trend_dma", "above_both"): "price above both the 20 and 50 DMA",
    ("trend_dma", "pullback_in_uptrend"): "price above the 50 DMA but below the 20 DMA (pullback)",
    ("trend_dma", "bounce_in_downtrend"): "price below the 50 DMA but above the 20 DMA (bounce in a downtrend)",
    ("trend_dma", "below_both"): "price below both the 20 and 50 DMA",
    ("extension", "stretched"): "price stretched more than 5% above its 20 DMA (extended)",
    ("extension", "near"): "price within 5% of its 20 DMA",
    ("extension", "depressed"): "price more than 5% below its 20 DMA (deep pullback)",
    ("volume", "spike"): "volume spike (prior session at least 1.5x its 20-day average)",
    ("volume", "normal"): "normal volume",
    ("volume", "dry"): "dry volume (prior session under 0.7x average)",
    ("volatility", "calm"): "calm volatility regime",
    ("volatility", "normal"): "normal volatility regime",
    ("volatility", "stressed"): "stressed volatility regime (20-day vol over 1.2x its 1-year level)",
    ("gap", "gap_up"): "gap up at the open (1% or more)",
    ("gap", "flat_open"): "flat open",
    ("gap", "gap_down"): "gap down at the open (1% or more)",
}
WAIT_FOR = {
    ("extension", "stretched"): "wait for a pullback toward the 20 DMA instead of buying strength",
    ("extension", "depressed"): "wait for a close back above the 20 DMA before buying",
    ("gap", "gap_up"): "do not buy the gap-up open; let the first session settle",
    ("gap", "gap_down"): "wait for the gap to hold for a session before buying",
    ("trend_dma", "below_both"): "wait for a close back above the 50 DMA",
    ("trend_dma", "bounce_in_downtrend"): "wait for the 50 DMA to be reclaimed",
    ("volume", "spike"): "wait for volume to normalise",
    ("volume", "dry"): "wait for volume to come back before committing",
    ("volatility", "stressed"): "wait for volatility to settle, or trade smaller with a wider stop",
}
MIN_MATCH = 3          # of 5 groups must agree for a past trade to count as "similar"


def vec(tags: dict) -> list[float]:
    return [1.0 if tags.get(g) == v else 0.0 for g, vals in JGROUPS.items() for v in vals]


def context_frame(df: pd.DataFrame) -> pd.DataFrame:
    """State as of each bar's CLOSE. The trade at bar i uses row i-1 (no look-ahead), plus bar i's gap."""
    c, v = df["Close"], df["Volume"]
    s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
    st = np.where((c > s20) & (c > s50), "above_both", np.where(c > s50, "pullback_in_uptrend", np.where(c > s20, "bounce_in_downtrend", "below_both")))
    st = pd.Series(st, index=df.index).where(s50.notna())
    ext_v = c / s20 - 1
    ext = pd.Series(np.where(ext_v > 0.05, "stretched", np.where(ext_v < -0.05, "depressed", "near")), index=df.index).where(s20.notna())
    vr = v / v.shift(1).rolling(20, min_periods=10).mean()
    vol = pd.Series(np.where(vr >= 1.5, "spike", np.where(vr < 0.7, "dry", "normal")), index=df.index).where(vr.notna())
    ret = c.pct_change()
    volr = ret.rolling(20, min_periods=15).std() / ret.rolling(250, min_periods=60).std()
    vola = pd.Series(np.where(volr < 0.8, "calm", np.where(volr > 1.2, "stressed", "normal")), index=df.index).where(volr.notna())
    return pd.DataFrame({"trend_dma": st, "extension": ext, "volume": vol, "volatility": vola,
                         "dma20_gap_pct": (ext_v * 100).round(2), "dma50_gap_pct": ((c / s50 - 1) * 100).round(2),
                         "volume_ratio": vr.round(2), "vol_ratio": volr.round(2)}, index=df.index)


def fingerprint(df: pd.DataFrame, when, frame: pd.DataFrame | None = None) -> dict | None:
    """Fingerprint for a trade at date `when`: indicators from the previous close, gap from the entry bar's open.
    If `when` is not a bar in the data (future / weekend), the latest prior close is used and the gap is unknown."""
    frame = context_frame(df) if frame is None else frame
    d = pd.Timestamp(when).normalize()
    i = int(df.index.searchsorted(d, side="left"))
    if i == 0:
        return None
    exact = i < len(df) and df.index[i] == d
    row = frame.iloc[i - 1]
    tags = {g: (row[g] if isinstance(row[g], str) else None) for g in ("trend_dma", "extension", "volume", "volatility")}
    gap_pct = None
    tags["gap"] = None
    if exact:
        gap_pct = (df["Open"].iloc[i] / df["Close"].iloc[i - 1] - 1) * 100
        tags["gap"] = "gap_up" if gap_pct >= 1 else ("gap_down" if gap_pct <= -1 else "flat_open")
    tags = {k: v for k, v in tags.items() if v}
    metrics = {k: (None if pd.isna(row[k]) else float(row[k])) for k in ("dma20_gap_pct", "dma50_gap_pct", "volume_ratio", "vol_ratio")}
    metrics["gap_pct"] = None if gap_pct is None else round(float(gap_pct), 2)
    return dict(tags=tags, metrics=metrics, as_of=str(df.index[i - 1].date()), entry_bar_found=bool(exact),
                complete=len(tags) == len(JGROUPS))


def describe(tags: dict) -> list[str]:
    return [LABEL[(g, v)] for g, v in tags.items() if (g, v) in LABEL]


# ---------------------------------------------------------------- CSV parsing (tolerant)
ALIASES = {
    "symbol": ["symbol", "tradingsymbol", "instrument", "scrip", "scripname", "stock", "security", "ticker", "name", "company", "securityname"],
    "date": ["tradedate", "date", "orderexecutiontime", "executiontime", "timestamp", "datetime", "tradetime", "exchangetimestamp", "executedat"],
    "side": ["tradetype", "side", "type", "buysell", "transactiontype", "action", "bs", "buyorsell", "trade"],
    "qty": ["quantity", "qty", "shares", "filledqty", "tradedqty", "tradedquantity", "units"],
    "price": ["price", "avgprice", "averageprice", "rate", "tradeprice", "executionprice", "fillprice", "tradedprice"],
    "exchange": ["exchange", "exch", "market"],
    "segment": ["segment", "series"],
    "time": ["orderexecutiontime", "executiontime", "tradetime", "time"],
}


class ImportError_(ValueError):
    pass


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower())


def map_columns(headers: list[str]) -> dict:
    normed = {h: _norm(h) for h in headers}
    out, used = {}, set()
    for field in ("symbol", "side", "qty", "price", "date", "exchange", "segment", "time"):
        for alias in ALIASES[field]:                     # earlier alias = higher priority
            hit = [h for h, n in normed.items() if n == alias and h not in used]
            if hit:
                out[field] = hit[0]
                if field != "time":
                    used.add(hit[0])
                break
    missing = [f for f in ("symbol", "date", "side", "qty", "price") if f not in out]
    if missing:
        raise ImportError_(f"Could not find column(s) for {', '.join(missing)}. Headers seen: {headers}. "
                           "Expected something like symbol, trade_date, trade_type (buy/sell), quantity, price.")
    return out


def _num(x) -> float:
    return float(str(x).replace(",", "").replace("₹", "").replace("Rs.", "").strip())


def _side(x: str) -> str | None:
    s = str(x).strip().lower()
    if s in ("buy", "b", "bought", "purchase", "bo", "long"):
        return "buy"
    if s in ("sell", "s", "sold", "sale", "so", "short"):
        return "sell"
    return None


def _symbol(sym: str, exchange: str | None) -> tuple[str, str]:
    s = str(sym).strip().upper()
    s = re.sub(r"^(NSE|BSE|NFO)[:\-]", "", s)
    s = re.sub(r"\.(NS|BO)$", "", s)
    s = re.sub(r"-(EQ|BE|BZ|SM)$", "", s)
    suffix = ".BO" if exchange and str(exchange).strip().upper().startswith("BSE") else ".NS"
    if str(sym).strip().upper().endswith(".BO"):
        suffix = ".BO"
    return s, s + suffix


def parse_tradebook(text: str) -> tuple[list[dict], list[dict], dict]:
    """-> (fills, rejected_rows, column_mapping). '#' comment lines are ignored (the bundled sample carries a label)."""
    text = text.lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < 2:
        raise ImportError_("The CSV has no data rows.")
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim))
    headers = [h for h in (rows[0].keys() if rows else []) if h]
    cmap = map_columns(headers)
    fills, rejected = [], []
    for k, r in enumerate(rows):
        rn = k + 2
        try:
            side = _side(r[cmap["side"]])
            if side is None:
                raise ValueError(f"unknown trade type '{r[cmap['side']]}'")
            seg = (r.get(cmap.get("segment", ""), "") or "").strip().upper()
            if seg in ("FO", "NFO", "BFO", "FUT", "OPT", "CDS", "MCX", "COM", "CD"):
                raise ValueError(f"segment {seg}: derivatives are not supported (equity only)")
            raw = str(r[cmap["date"]]).strip()
            # ISO (yyyy-mm-dd) is read as is; anything else is read day-first (Indian broker convention, e.g. 02-03-2026 = 2 March)
            ts = pd.to_datetime(raw, errors="coerce", dayfirst=not re.match(r"^\d{4}-\d{1,2}-\d{1,2}", raw))
            if pd.isna(ts):
                raise ValueError(f"cannot read date '{raw}'")
            if getattr(ts, "tzinfo", None) is not None:
                ts = ts.tz_localize(None)
            tcol = cmap.get("time")
            tkey = ""
            if tcol and r.get(tcol) and tcol != cmap["date"]:
                tkey = str(r[tcol]).strip()
            elif ts.hour or ts.minute or ts.second:
                tkey = ts.strftime("%H:%M:%S")
            qty, price = _num(r[cmap["qty"]]), _num(r[cmap["price"]])
            if qty <= 0 or price <= 0:
                raise ValueError("quantity and price must be positive")
            sym, ticker = _symbol(r[cmap["symbol"]], r.get(cmap.get("exchange", ""), None))
            if not sym:
                raise ValueError("empty symbol")
            fills.append(dict(row=rn, symbol=sym, ticker=ticker, date=str(ts.date()), tkey=tkey, side=side, qty=qty, price=price, idx=k))
        except (ValueError, KeyError, TypeError) as e:
            rejected.append(dict(row=rn, reason=str(e)))
    return fills, rejected, cmap


# ---------------------------------------------------------------- pairing into round trips
def pair(fills: list[dict], cost_bps: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Average-cost position tracking per symbol. A position opens from flat and closes when quantity returns to zero
    (scale-ins and partial exits stay inside one round trip). Overselling flips into a short. Returns (round_trips, open_positions).
    Same-day ordering: by execution time if the file has it, else buys before sells."""
    by = defaultdict(list)
    for f in fills:
        by[f["ticker"]].append(f)
    trips, open_pos = [], []
    for ticker, fs in by.items():
        timed = {f["date"] for f in fs if f["tkey"]}
        fs.sort(key=lambda f: (f["date"], f["tkey"], 0, f["idx"]) if f["date"] in timed else (f["date"], "", 0 if f["side"] == "buy" else 1, f["idx"]))
        pos = None   # dict(direction, qty, entry_val, entry_date, entry_qty, exit_val, exit_qty, exit_date, n)
        for f in fs:
            sgn = 1 if f["side"] == "buy" else -1
            q = f["qty"]
            while q > 1e-9:
                if pos is None:
                    pos = dict(direction="long" if sgn > 0 else "short", qty=0.0, in_val=0.0, in_qty=0.0, out_val=0.0, out_qty=0.0,
                               entry_date=f["date"], exit_date=None, n=0, symbol=f["symbol"], ticker=ticker)
                opening = (pos["direction"] == "long") == (sgn > 0)
                if opening:
                    pos["qty"] += q; pos["in_val"] += q * f["price"]; pos["in_qty"] += q; pos["n"] += 1
                    q = 0
                else:
                    take = min(q, pos["qty"])
                    pos["qty"] -= take; pos["out_val"] += take * f["price"]; pos["out_qty"] += take
                    pos["exit_date"] = f["date"]; pos["n"] += 1
                    q -= take
                    if pos["qty"] <= 1e-9:
                        trips.append(_close(pos, cost_bps)); pos = None
        if pos is not None:
            avg = pos["in_val"] / pos["in_qty"]
            open_pos.append(dict(symbol=pos["symbol"], ticker=ticker, direction=pos["direction"], qty_open=round(pos["qty"], 4),
                                 avg_entry=round(avg, 2), entry_date=pos["entry_date"], note="still open in this file: excluded from statistics"))
    trips.sort(key=lambda t: (t["entry_date"], t["ticker"]))
    return trips, open_pos


def _close(pos: dict, cost_bps: float) -> dict:
    avg_in, avg_out = pos["in_val"] / pos["in_qty"], pos["out_val"] / pos["out_qty"]
    q = pos["out_qty"]
    gross = (avg_out - avg_in) * q if pos["direction"] == "long" else (avg_in - avg_out) * q
    cost = (cost_bps / 1e4) * (avg_in * q + avg_out * q) / 2
    pnl = gross - cost
    hold = (pd.Timestamp(pos["exit_date"]) - pd.Timestamp(pos["entry_date"])).days
    return dict(symbol=pos["symbol"], ticker=pos["ticker"], direction=pos["direction"], qty=round(q, 4), entry_date=pos["entry_date"],
                exit_date=pos["exit_date"], avg_entry=round(avg_in, 4), avg_exit=round(avg_out, 4), pnl=round(pnl, 2),
                ret=pnl / (avg_in * q), hold_days=int(hold), n_fills=pos["n"])


# ---------------------------------------------------------------- import
def import_csv(text: str, filename: str = "tradebook.csv", is_sample: bool = False, cost_bps: float = 0.0) -> dict:
    fills, rejected, cmap = parse_tradebook(text)
    if not fills:
        raise ImportError_("No valid trade rows. First problems: " + "; ".join(f"row {r['row']}: {r['reason']}" for r in rejected[:5]))
    trips, open_pos = pair(fills, cost_bps)
    cache, ctx_errors = {}, {}
    for t in trips:
        tk = t["ticker"]
        if tk not in cache:
            try:
                df, _ = data.load(tk)
                cache[tk] = (df, context_frame(df))
            except data.DataError as e:
                cache[tk] = None; ctx_errors[tk] = str(e)[:160]
        t["context"] = None
        if cache[tk] is not None:
            t["context"] = fingerprint(cache[tk][0], t["entry_date"], cache[tk][1])
    import_id = uuid.uuid4().hex[:12]
    new, dup = [], 0
    with closing(_db()) as con:
        for t in trips:
            cx = t["context"]
            tags = cx["tags"] if cx else {}
            uniq = hashlib.sha1(json.dumps([t["ticker"], t["direction"], t["entry_date"], t["exit_date"], t["qty"], t["avg_entry"], t["avg_exit"], int(is_sample)]).encode()).hexdigest()
            try:
                con.execute("INSERT INTO journal_trades(import_id,uniq,is_sample,symbol,ticker,direction,qty,entry_date,exit_date,avg_entry,avg_exit,pnl,ret,hold_days,n_fills,tags,metrics,vec,context_ok) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (import_id, uniq, int(is_sample), t["symbol"], t["ticker"], t["direction"], t["qty"], t["entry_date"], t["exit_date"], t["avg_entry"],
                             t["avg_exit"], t["pnl"], t["ret"], t["hold_days"], t["n_fills"], json.dumps(tags), json.dumps(cx["metrics"] if cx else {}),
                             json.dumps(vec(tags)), 1 if cx and cx["complete"] else 0))
                new.append(t)
            except Exception as e:
                if "UNIQUE" in str(e):
                    dup += 1
                else:
                    raise
        con.execute("INSERT INTO journal_imports VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (import_id, time.time(), filename, int(is_sample), len(fills) + len(rejected), len(rejected), len(trips), len(new), dup, json.dumps(open_pos)))
        con.commit()
    summ = summarize(source="sample" if is_sample else "real")
    mirror = "off"
    if meko.enabled() and new and not is_sample:        # sample data is never mirrored into the user's real memory store
        items = [("journal_trade", f"{t['direction']} {t['ticker']} {t['entry_date']}->{t['exit_date']} pnl {t['pnl']:+.0f} ({t['ret']*100:+.1f}%) "
                                   f"tags={t['context']['tags'] if t['context'] else {}}",
                  dict(import_id=import_id, direction=t["direction"], ticker=t["ticker"], entry_date=t["entry_date"], pnl=t["pnl"], ret=t["ret"]))
                 for t in new[:40]]
        items += [("journal_pattern", p["text"], dict(n=p["n"], win_rate=p["win_rate"], avg_return_pct=p["avg_return_pct"])) for p in summ.get("bad_patterns", [])]
        meko.mirror_async(items); mirror = "queued"
    return dict(import_id=import_id, filename=filename, is_sample=is_sample,
                label=("SAMPLE DATA: synthetic trading behaviour on real yfinance prices. Not a real trader's record." if is_sample else "imported from your file"),
                column_mapping=cmap, rows_read=len(fills) + len(rejected), rows_rejected=rejected, round_trips=len(trips),
                new_trades=len(new), duplicates_skipped=dup, open_positions=open_pos,
                context_missing=[dict(ticker=k, reason=v) for k, v in ctx_errors.items()],
                trades=[dict(t, ret_pct=round(t["ret"] * 100, 2)) for t in trips], summary=summ,
                stored_in="sqlite", meko_mirror=mirror, cost_note=(f"PnL net of {cost_bps:g} bp assumed round-trip cost." if cost_bps else
                                                                    "PnL is gross: before brokerage, STT and other charges."))


def import_sample() -> dict:
    if not SAMPLE_PATH.exists():
        raise ImportError_("Bundled sample CSV is missing. Run scripts/make_sample_tradebook.py.")
    return import_csv(SAMPLE_PATH.read_text(), filename=SAMPLE_PATH.name, is_sample=True)


# ---------------------------------------------------------------- read back + statistics
def _rows(source: str = "auto", direction: str | None = None) -> list[dict]:
    with closing(_db()) as con:
        q, a = "SELECT * FROM journal_trades WHERE 1=1", []
        if direction:
            q += " AND direction=?"; a.append(direction)
        rows = [dict(r) for r in con.execute(q + " ORDER BY entry_date", a).fetchall()]
    real = [r for r in rows if not r["is_sample"]]
    if source == "real" or (source == "auto" and real):
        rows = real
    elif source == "sample":
        rows = [r for r in rows if r["is_sample"]]
    for r in rows:
        r["tags"] = json.loads(r["tags"]); r["metrics"] = json.loads(r["metrics"]); r["vec"] = json.loads(r["vec"])
    return rows


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return dict(n=0, win_rate=None, avg_pnl=None, avg_return_pct=None, total_pnl=0.0)
    w = [r for r in rows if r["pnl"] > 0]
    l = [r for r in rows if r["pnl"] <= 0]
    return dict(n=n, wins=len(w), win_rate=round(len(w) / n, 4), avg_pnl=round(sum(r["pnl"] for r in rows) / n, 2),
                avg_return_pct=round(sum(r["ret"] for r in rows) / n * 100, 2), total_pnl=round(sum(r["pnl"] for r in rows), 2),
                avg_win_pct=round(sum(r["ret"] for r in w) / len(w) * 100, 2) if w else None,
                avg_loss_pct=round(sum(r["ret"] for r in l) / len(l) * 100, 2) if l else None,
                avg_hold_days=round(sum(r["hold_days"] for r in rows) / n, 1))


def patterns(rows: list[dict]) -> dict:
    """Single tags and pairs of tags with a clearly worse (or better) record than the trader's own average."""
    rows = [r for r in rows if r["tags"]]
    n = len(rows)
    out = dict(bad=[], good=[], tested=0)
    if n < 15:
        return out
    base_wr = sum(r["pnl"] > 0 for r in rows) / n
    items = [(g, v) for g, vals in JGROUPS.items() for v in vals]
    combos = [((a,),) for a in items] + [((a, b),) for i, a in enumerate(items) for b in items[i + 1:] if a[0] != b[0]]
    cand = []
    for (conds,) in combos:
        sub = [r for r in rows if all(r["tags"].get(g) == v for g, v in conds)]
        if len(sub) < (5 if len(conds) == 1 else 6):
            continue
        out["tested"] += 1
        wr = sum(r["pnl"] > 0 for r in sub) / len(sub)
        avg = sum(r["ret"] for r in sub) / len(sub)
        score = abs(wr - base_wr) * math.sqrt(len(sub))
        if wr <= base_wr - 0.15 and avg < 0:
            cand.append(("bad", score, conds, sub, wr, avg))
        elif wr >= base_wr + 0.15 and avg > 0:
            cand.append(("good", score, conds, sub, wr, avg))
    for kind in ("bad", "good"):
        seen, picked = [], []
        for _, sc, conds, sub, wr, avg in sorted([c for c in cand if c[0] == kind], key=lambda c: -c[1]):
            key = frozenset(conds)
            if any(key <= s or s <= key for s in seen):     # skip near-duplicates (a pair that only restates a single tag)
                continue
            seen.append(key)
            desc = " and ".join(LABEL[c] for c in conds)
            picked.append(dict(kind=kind, conditions=[dict(group=g, value=v) for g, v in conds], n=len(sub), win_rate=round(wr, 3),
                               avg_return_pct=round(avg * 100, 2), avg_pnl=round(sum(r["pnl"] for r in sub) / len(sub), 2), base_win_rate=round(base_wr, 3),
                               text=(f"When you bought with {desc}: {len(sub)} trades, win rate {wr*100:.0f}% (your average is {base_wr*100:.0f}%), "
                                     f"avg return {avg*100:+.1f}%." + (" This is a repeat mistake pattern." if kind == "bad" else " This looks like your better setup.")
                                     + " Small sample: a hint, not proof."),
                               wait_for=(" / ".join(dict.fromkeys(WAIT_FOR[c] for c in conds if c in WAIT_FOR)) or None) if kind == "bad" else None))
            if len(picked) == 3:
                break
        out[kind] = picked
    return out


def hold_behaviour(rows: list[dict]) -> dict | None:
    w = [r["hold_days"] for r in rows if r["pnl"] > 0]
    l = [r["hold_days"] for r in rows if r["pnl"] <= 0]
    if len(w) < 5 or len(l) < 5:
        return None
    aw, al = sum(w) / len(w), sum(l) / len(l)
    if al >= 1.5 * aw and al - aw >= 2:
        return dict(kind="holds_losers", text=f"You hold losing trades about {al:.1f} calendar days vs {aw:.1f} for winners ({len(l)} losers, {len(w)} winners). "
                                               "Losers run longer than winners: a pre-set stop would address that.", avg_hold_losers=round(al, 1), avg_hold_winners=round(aw, 1))
    if aw >= 1.5 * al and aw - al >= 2:
        return dict(kind="cuts_winners", text=f"You exit winners after {aw:.1f} calendar days but hold losers only {al:.1f} ({len(w)} winners, {len(l)} losers): check whether winners are being cut short.", avg_hold_losers=round(al, 1), avg_hold_winners=round(aw, 1))
    return None


def summarize(source: str = "auto") -> dict:
    rows = _rows(source)
    pat = patterns(rows)
    return dict(source_used=("sample" if rows and all(r["is_sample"] for r in rows) else "real") if rows else "none",
                overall=_stats(rows), long=_stats([r for r in rows if r["direction"] == "long"]), short=_stats([r for r in rows if r["direction"] == "short"]),
                bad_patterns=pat["bad"], good_patterns=pat["good"], patterns_tested=pat["tested"], hold_behaviour=hold_behaviour(rows),
                notes=["Statistics use closed round trips only.", "Patterns come from tests of many tag combinations on a small sample: treat them as hints."]
                + (["Using the bundled SAMPLE journal (synthetic behaviour on real prices), not a real trader's data."] if rows and all(r["is_sample"] for r in rows) else []))


def list_trades(limit: int = 200, source: str = "auto", offset: int = 0) -> dict:
    rows = _rows(source)
    for r in rows:
        r.pop("vec", None)
    return dict(trades=rows[::-1][offset:offset + limit], n=len(rows))


def clear(sample_only: bool = False) -> dict:
    with closing(_db()) as con:
        n = con.execute("DELETE FROM journal_trades" + (" WHERE is_sample=1" if sample_only else "")).rowcount
        con.execute("DELETE FROM journal_imports" + (" WHERE is_sample=1" if sample_only else ""))
        con.commit()
    return dict(deleted_trades=n)


# ---------------------------------------------------------------- pre-trade check
def check(symbol: str, when: str | None, side: str = "buy", top_k: int = 10, source: str = "auto", price: float | None = None, qty: float | None = None, _rows_override: list[dict] | None = None) -> dict:
    side = side.lower()
    if side not in ("buy", "sell"):
        raise ImportError_("side must be 'buy' or 'sell'")
    _, ticker = _symbol(symbol, None)
    df, info = data.load(ticker)
    when = when or str(_date.today())
    try:
        pd.Timestamp(when)
    except Exception:
        raise ImportError_(f"cannot read date '{when}'")
    fp = fingerprint(df, when)
    if fp is None:
        raise ImportError_(f"{ticker}: not enough price history before {when} to build a market context.")
    direction = "long" if side == "buy" else "short"
    rows = _rows(source, direction) if _rows_override is None else _rows_override
    base = _stats(rows)
    out = dict(symbol=ticker, date=str(pd.Timestamp(when).date()), side=side, direction=direction, price=price, qty=qty,
               fingerprint=dict(tags=fp["tags"], metrics=fp["metrics"], as_of=fp["as_of"], descriptions=describe(fp["tags"]),
                                complete=fp["complete"], gap_known=fp["entry_bar_found"]),
               journal=dict(n=base["n"], source_used=("sample" if rows and all(r["is_sample"] for r in rows) else "real") if rows else "none", overall=base),
               data=info, notes=["Rex Lite is a memory and strategy layer, not a broker: it places no orders.",
                                 "Rule-based on your own past trades; small samples. Educational, not investment advice.",
                                 "Only entries are evaluated (a buy opens a long, a sell opens a short); exits are not."])
    if not fp["entry_bar_found"]:
        out["notes"].append(f"{out['date']} is not a trading day with a bar yet, so the gap is unknown; indicators use the close of {fp['as_of']}.")
    if base["n"] < 10:
        out.update(similar=dict(n=0, top_k=top_k, stats=None, neighbors=[]), mistake_pattern=None,
                   recommendation=dict(action="proceed", size_factor=1.0, confidence="low",
                                       reasons=[f"Your journal has only {base['n']} closed {direction} round trips; at least 10 are needed before similarity says anything. "
                                                "Use your normal rules."], wait_for=None))
        out["challenger"] = challenger.build(out, rows, ticker)
        return out
    pv = vec(fp["tags"])
    scored = []
    for r in rows:
        dot = sum(a * b for a, b in zip(pv, r["vec"]))
        if dot >= MIN_MATCH:
            na, nb = math.sqrt(sum(pv)), math.sqrt(sum(r["vec"]))
            scored.append((dot / (na * nb), r))
    n_cand = len(scored)
    scored.sort(key=lambda x: x[1]["entry_date"], reverse=True)                                  # newest first ...
    scored.sort(key=lambda x: (-x[0], 0 if x[1]["ticker"] == ticker else 1))                      # ... then best match, same symbol first (stable)
    top = scored[:top_k]
    nb_rows = [r for _, r in top]
    st = _stats(nb_rows)
    neighbors = [dict(cosine=round(c, 3), ticker=r["ticker"], entry_date=r["entry_date"], exit_date=r["exit_date"], pnl=r["pnl"], return_pct=round(r["ret"] * 100, 2),
                      hold_days=r["hold_days"], same_symbol=r["ticker"] == ticker, is_sample=bool(r["is_sample"]),
                      matching=[g for g in JGROUPS if fp["tags"].get(g) and fp["tags"].get(g) == r["tags"].get(g)], tags=r["tags"]) for c, r in top]
    out["similar"] = dict(n=len(nb_rows), candidates_above_threshold=n_cand, min_groups_matching=MIN_MATCH, top_k=top_k, stats=st,
                          same_symbol=sum(1 for r in nb_rows if r["ticker"] == ticker), neighbors=neighbors)
    # ---- mistake pattern matched by the proposed trade
    pat = patterns(rows)
    matched = [p for p in pat["bad"] if all(fp["tags"].get(c["group"]) == c["value"] for c in p["conditions"])]
    hb = hold_behaviour(rows)
    out["mistake_pattern"] = dict(matched=matched[0] if matched else None, also_matched=matched[1:],
                                  other_bad_patterns=[p for p in pat["bad"] if p not in matched], hold_behaviour=hb,
                                  matched_good=[p for p in pat["good"] if all(fp["tags"].get(c["group"]) == c["value"] for c in p["conditions"])][:1])
    # ---- recommendation (fixed rules)
    reasons, action, size, wait_for = [], "proceed", 1.0, None
    conf = "high" if st["n"] >= 10 and base["n"] >= 30 else ("medium" if st["n"] >= 5 else "low")
    sev_n = st["n"] >= 8 and st["win_rate"] <= 0.35 and st["avg_return_pct"] < 0
    mod_n = st["n"] >= 5 and (st["win_rate"] <= base["win_rate"] - 0.10 or st["avg_return_pct"] < 0)
    sev_p = bool(matched) and matched[0]["n"] >= 8 and matched[0]["win_rate"] <= matched[0]["base_win_rate"] - 0.20
    if st["n"] < 5:
        reasons.append(f"Only {st['n']} similar past trades (need 5): not enough to lean on. Use your normal rules.")
        conf = "low"
    else:
        reasons.append(f"Top {st['n']} similar past {direction} trades: win rate {st['win_rate']*100:.0f}% vs {base['win_rate']*100:.0f}% across all your {direction} trades, "
                       f"avg return {st['avg_return_pct']:+.1f}%, avg PnL {st['avg_pnl']:+,.0f}.")
        if st["avg_loss_pct"] is not None:
            reasons.append(f"Your losers in this kind of setup lost {abs(st['avg_loss_pct']):.1f}% on average: size so one such loss is acceptable.")
    if matched:
        reasons.append(matched[0]["text"])
        wait_for = matched[0]["wait_for"]
    if sev_n or sev_p:
        action, size = "wait", 0.0
    elif mod_n or matched:
        action, size = "reduce_size", 0.5
    elif st["n"] >= 5 and st["win_rate"] >= base["win_rate"] + 0.10 and st["avg_return_pct"] > 0:
        reasons.append("This setup has been better than your average, so there is no historical reason to hold back.")
    if hb and hb["kind"] == "holds_losers" and action != "proceed":
        reasons.append(hb["text"])
    if out["mistake_pattern"]["matched_good"] and action == "proceed":
        reasons.append("It also matches one of your better-performing setups: " + out["mistake_pattern"]["matched_good"][0]["text"])
    if fp["tags"].get("volatility") == "stressed" and action == "proceed":
        reasons.append("Volatility is in a stressed regime: consider a wider stop and smaller size even though history does not object.")
    if action == "wait" and not wait_for:
        wait_for = "wait for the setup to change (for example a pullback, a settled gap or normal volume) and re-check"
    out["recommendation"] = dict(action=action, size_factor=size, confidence=conf, reasons=reasons, wait_for=wait_for,
                                 rules=["wait: 8+ similar trades with win rate <= 35% and negative avg, or a matched repeat-mistake pattern (n>=8) 20 points below your average",
                                        "reduce_size: 5+ similar trades with win rate 10 points below your average or negative avg, or any matched repeat-mistake pattern",
                                        "proceed: otherwise (low confidence when fewer than 5 similar trades)"])
    out["challenger"] = challenger.build(out, rows, ticker)
    return out


def proof(source: str = "auto", top_k: int = 10) -> dict:
    """Walk-forward test of memory OFF vs ON on the journal itself. For each closed trade, in entry order, Rex sees ONLY trades that were
    already closed before that entry (no look-ahead) and issues its normal check. OFF takes every trade at full size. ON applies the
    recommendation: wait = skip, reduce_size = half size, proceed = full size. The first 10 trades per direction are warm-up (full size in both)."""
    rows = _rows(source)
    if not rows:
        raise ImportError_("No journal imported yet: import a tradebook or load the sample first.")
    rows = sorted(rows, key=lambda r: (r["entry_date"], r["exit_date"]))
    steps, counts = [], dict(proceed=0, reduce_size=0, wait=0, warmup=0)
    for r in rows:
        hist = [h for h in rows if h["direction"] == r["direction"] and h["exit_date"] < r["entry_date"]]
        if len(hist) < 10 or not r.get("context_ok", True):
            act, size = "warmup", 1.0
        else:
            try:
                c = check(r["ticker"], r["entry_date"], "buy" if r["direction"] == "long" else "sell", top_k, _rows_override=hist)
                act, size = c["recommendation"]["action"], c["recommendation"]["size_factor"]
            except (ImportError_, data.DataError):
                act, size = "warmup", 1.0
        counts[act] += 1
        steps.append(dict(ticker=r["ticker"], entry_date=r["entry_date"], exit_date=r["exit_date"], action=act, size=size,
                          pnl_off=r["pnl"], pnl_on=round(r["pnl"] * size, 2), ret_pct=round(r["ret"] * 100, 2)))
    def curve(key):
        cum, peak, mdd, pts = 0.0, 0.0, 0.0, []
        for st in sorted(steps, key=lambda x: (x["exit_date"], x["entry_date"])):
            cum += st[key]; peak = max(peak, cum); mdd = min(mdd, cum - peak)
            pts.append((st["exit_date"], round(cum, 2)))
        return pts, round(cum, 2), round(mdd, 2)
    off, off_t, off_dd = curve("pnl_off"); on, on_t, on_dd = curve("pnl_on")
    series = [dict(date=d, off=o, on=n) for (d, o), (_, n) in zip(off, on)]
    taken = [x for x in steps if x["size"] > 0]
    skipped = [x for x in steps if x["action"] == "wait"]
    reduced = [x for x in steps if x["action"] == "reduce_size"]
    wr = lambda xs, k: round(sum(1 for x in xs if x[k] > 0) / len(xs), 4) if xs else None
    return dict(source_used="sample" if all(r["is_sample"] for r in rows) else "real", is_sample=all(r["is_sample"] for r in rows),
                n_trades=len(steps), decisions=counts,
                off=dict(total_pnl=off_t, max_drawdown=off_dd, win_rate=wr(steps, "pnl_off"), trades_taken=len(steps)),
                on=dict(total_pnl=on_t, max_drawdown=on_dd, win_rate=wr(taken, "pnl_on"), trades_taken=len(taken)),
                skipped=dict(n=len(skipped), pnl_if_taken=round(sum(x["pnl_off"] for x in skipped), 2)),
                reduced=dict(n=len(reduced), pnl_full_size=round(sum(x["pnl_off"] for x in reduced), 2), pnl_half_size=round(sum(x["pnl_on"] for x in reduced), 2)),
                series=series, steps=steps,
                notes=["Walk-forward: each decision uses only trades already closed before that entry. No look-ahead, no tuning of the rules.",
                       "The rules are fixed (see /trades/check). Result is on ONE journal and a small sample: it can go either way and is not a promise.",
                       "PnL is gross (no costs). Sizes: wait = 0, reduce_size = 0.5."] +
                      (["SAMPLE DATA: synthetic trader behaviour on real prices. This shows the mechanics, not a real trader's edge."] if all(r["is_sample"] for r in rows) else []))


# ---------------------------------------------------------------- trades logged through the Live Strategy Tracker
def add_tracked_trade(session_id: str, ticker: str, direction: str, qty: float | None, entry_date: str, exit_date: str,
                      avg_entry: float, avg_exit: float, cost_bps: float = 0.0, net_return_pct: float | None = None) -> dict:
    """Store ONE closed round trip that the user took in their own broker and tracked in Rex. It is a real (non-sample) journal trade
    with the same context fingerprint as imported trades, so the pre-trade check can learn from it."""
    q = float(qty or 1)
    gross = (avg_exit - avg_entry) * q if direction == "long" else (avg_entry - avg_exit) * q
    pnl = gross - (cost_bps / 1e4) * avg_entry * q if net_return_pct is None else net_return_pct / 100 * avg_entry * q
    ret = pnl / (avg_entry * q)
    hold = (pd.Timestamp(exit_date) - pd.Timestamp(entry_date)).days
    cx = None
    try:
        df, _ = data.load(ticker)
        cx = fingerprint(df, entry_date)
    except data.DataError:
        pass
    tags = cx["tags"] if cx else {}
    uniq = hashlib.sha1(json.dumps(["tracker", session_id]).encode()).hexdigest()
    iid = f"trk_{session_id}"
    with closing(_db()) as con:
        try:
            con.execute("INSERT INTO journal_trades(import_id,uniq,is_sample,symbol,ticker,direction,qty,entry_date,exit_date,avg_entry,avg_exit,pnl,ret,hold_days,n_fills,tags,metrics,vec,context_ok) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (iid, uniq, 0, ticker.replace(".NS", ""), ticker, direction, q, entry_date, exit_date, round(avg_entry, 4), round(avg_exit, 4), round(pnl, 2),
                         ret, int(hold), 2, json.dumps(tags), json.dumps(cx["metrics"] if cx else {}), json.dumps(vec(tags)), 1 if cx and cx["complete"] else 0))
            con.execute("INSERT OR IGNORE INTO journal_imports VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (iid, time.time(), "Live Strategy Tracker (trade you took in your broker)", 0, 2, 0, 1, 1, 0, "[]"))
            con.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                return dict(added=False, reason="already in journal")
            raise
        n_real = con.execute("SELECT COUNT(*) FROM journal_trades WHERE is_sample=0").fetchone()[0]
    if meko.enabled():
        meko.mirror_async([("journal_trade", f"{direction} {ticker} {entry_date}->{exit_date} pnl {pnl:+.0f} ({ret*100:+.1f}%) tags={tags} (tracked in Rex)",
                            dict(import_id=iid, direction=direction, ticker=ticker, entry_date=entry_date, pnl=pnl, ret=ret))])
    return dict(added=True, pnl=round(pnl, 2), ret_pct=round(ret * 100, 2), tags=tags, real_trades_in_journal=n_real,
                note="Once your journal holds any real trade, the pre-trade check uses your real trades only (not the sample ones).")
