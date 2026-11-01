"""Journal import + pre-trade check. Pairing/rule tests use HAND-BUILT fills and rows (labelled); fingerprints, the sample
journal and API tests use the bundled REAL yfinance cache."""
import json, sqlite3
from contextlib import closing
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import db, data, journal, config, meko
from app.main import app

client = TestClient(app)


def csv_(rows, header="symbol,trade_date,trade_type,quantity,price"):
    return header + "\n" + "\n".join(rows) + "\n"


# ------------------------------------------------------------ parsing
def test_tolerant_column_mapping_and_symbols():
    txt = "Scrip Name;Date;B/S;Qty;Avg Price;Exchange\nRELIANCE-EQ;02-03-2026;B;10;\"1,250.50\";NSE\nNSE:TCS;2026-03-03;S;5;3,100;BSE\n"
    fills, rej, cmap = journal.parse_tradebook(txt)
    assert not rej and cmap["symbol"] == "Scrip Name" and cmap["side"] == "B/S" and cmap["price"] == "Avg Price"
    assert fills[0]["ticker"] == "RELIANCE.NS" and fills[0]["price"] == 1250.5 and fills[0]["date"] == "2026-03-02"   # dd-mm-yyyy fallback
    assert fills[1]["ticker"] == "TCS.BO" and fills[1]["side"] == "sell"


def test_zerodha_style_headers_and_comment_lines():
    txt = ("# SAMPLE\n# comment\nsymbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time\n"
           "INFY,INE009A01021,2026-01-05,NSE,EQ,EQ,buy,false,3,1500.0,1,2,2026-01-05T09:20:00\n")
    fills, rej, cmap = journal.parse_tradebook(txt)
    assert len(fills) == 1 and cmap["date"] == "trade_date" and cmap["time"] == "order_execution_time" and fills[0]["tkey"] == "2026-01-05T09:20:00"


def test_bad_rows_are_rejected_with_reasons_not_dropped_silently():
    txt = csv_(["INFY,2026-01-05,hold,3,1500", "INFY,notadate,buy,3,1500", "INFY,2026-01-05,buy,-3,1500", "INFY,2026-01-05,buy,3,1500"])
    fills, rej, _ = journal.parse_tradebook(txt)
    assert len(fills) == 1 and len(rej) == 3 and all(r["reason"] for r in rej)
    fo = "symbol,trade_date,trade_type,quantity,price,segment\nNIFTY24500CE,2026-01-05,buy,50,100,FO\n"
    _, rej, _ = journal.parse_tradebook(fo)
    assert "derivatives" in rej[0]["reason"]


def test_missing_columns_raises_helpful_error():
    with pytest.raises(journal.ImportError_) as e:
        journal.parse_tradebook("foo,bar\n1,2\n")
    assert "symbol" in str(e.value) and "Headers seen" in str(e.value)


# ------------------------------------------------------------ pairing (hand-built fills)
def F(sym, d, side, q, px, i, t=""):
    return dict(row=i, symbol=sym, ticker=sym + ".NS", date=d, tkey=t, side=side, qty=q, price=px, idx=i)


def test_pairing_scale_in_partial_exit_and_pnl():
    fills = [F("AAA", "2026-01-01", "buy", 10, 100, 0), F("AAA", "2026-01-03", "buy", 10, 110, 1),
             F("AAA", "2026-01-10", "sell", 5, 130, 2), F("AAA", "2026-01-12", "sell", 15, 120, 3)]
    trips, openp = journal.pair(fills)
    assert len(trips) == 1 and not openp
    t = trips[0]
    assert t["qty"] == 20 and t["avg_entry"] == 105.0 and t["n_fills"] == 4 and t["hold_days"] == 11
    assert t["avg_exit"] == (5 * 130 + 15 * 120) / 20 == 122.5
    assert t["pnl"] == pytest.approx((122.5 - 105) * 20) and t["ret"] == pytest.approx(17.5 / 105)


def test_pairing_open_position_short_flip_and_costs():
    fills = [F("AAA", "2026-01-01", "buy", 10, 100, 0), F("AAA", "2026-01-05", "sell", 15, 90, 1),     # sells 5 more than held: closes long, opens short
             F("AAA", "2026-01-09", "buy", 5, 80, 2), F("BBB", "2026-01-02", "buy", 7, 50, 3)]
    trips, openp = journal.pair(fills)
    assert [(t["direction"], t["pnl"]) for t in trips] == [("long", -100.0), ("short", 50.0)]
    assert len(openp) == 1 and openp[0]["ticker"] == "BBB.NS" and openp[0]["qty_open"] == 7
    t2, _ = journal.pair([F("AAA", "2026-01-01", "buy", 10, 100, 0), F("AAA", "2026-01-02", "sell", 10, 100, 1)], cost_bps=20)
    assert t2[0]["pnl"] == pytest.approx(-0.002 * 1000)         # flat trade, 20 bp on the notional


def test_same_day_buy_before_sell_without_times_and_by_time_with_times():
    fills = [F("AAA", "2026-01-01", "sell", 10, 105, 0), F("AAA", "2026-01-01", "buy", 10, 100, 1)]
    trips, openp = journal.pair(fills)
    assert len(trips) == 1 and trips[0]["direction"] == "long" and trips[0]["pnl"] == 50 and not openp
    timed = [F("AAA", "2026-01-01", "sell", 10, 105, 0, "09:20:00"), F("AAA", "2026-01-01", "buy", 10, 100, 1, "10:00:00")]
    trips, _ = journal.pair(timed)
    assert trips[0]["direction"] == "short" and trips[0]["pnl"] == 50      # sold 105 first, covered at 100


# ------------------------------------------------------------ fingerprint on REAL data
def real():
    df, _ = data.load("RELIANCE.NS")
    return df


def test_fingerprint_has_no_lookahead():
    df = real()
    d = df.index[-60]
    base = journal.fingerprint(df, d)
    assert base["entry_bar_found"] and set(base["tags"]) == set(journal.JGROUPS) and base["as_of"] == str(df.index[-61].date())
    wrecked = df.astype(float).copy()
    wrecked.loc[d:, ["High", "Low", "Close", "Volume"]] *= 3         # everything on/after the entry day except the open
    again = journal.fingerprint(wrecked, d)
    assert again["tags"] == base["tags"] and again["metrics"]["gap_pct"] == base["metrics"]["gap_pct"]
    gapped = df.astype(float).copy(); gapped.loc[d, "Open"] *= 1.05
    g = journal.fingerprint(gapped, d)
    assert g["tags"]["gap"] == "gap_up" and {k: v for k, v in g["tags"].items() if k != "gap"} == {k: v for k, v in base["tags"].items() if k != "gap"}


def test_fingerprint_future_date_uses_last_close_and_unknown_gap():
    df = real()
    fp = journal.fingerprint(df, "2030-01-01")
    assert fp["as_of"] == str(df.index[-1].date()) and not fp["entry_bar_found"] and "gap" not in fp["tags"] and not fp["complete"]
    assert journal.fingerprint(df, df.index[0]) is None


def test_tag_values_follow_definitions():
    df = real()
    cf = journal.context_frame(df)
    r = cf.dropna().iloc[200]
    c = df["Close"].loc[cf.dropna().index[200]]
    s20, s50 = df["Close"].rolling(20).mean().loc[r.name], df["Close"].rolling(50).mean().loc[r.name]
    exp = "above_both" if c > s20 and c > s50 else "pullback_in_uptrend" if c > s50 else "bounce_in_downtrend" if c > s20 else "below_both"
    assert r["trend_dma"] == exp
    assert r["extension"] == ("stretched" if c / s20 - 1 > .05 else "depressed" if c / s20 - 1 < -.05 else "near")


# ------------------------------------------------------------ import end to end
def test_import_real_prices_roundtrip_and_idempotent():
    df = real()
    d0, d1 = df.index[-80], df.index[-70]
    txt = csv_([f"RELIANCE,{d0.date()},buy,10,{df['Open'].loc[d0]:.2f}", f"RELIANCE,{d1.date()},sell,10,{df['Close'].loc[d1]:.2f}"])
    r = journal.import_csv(txt, "mine.csv")
    assert r["round_trips"] == 1 and r["new_trades"] == 1 and not r["is_sample"]
    t = r["trades"][0]
    assert t["pnl"] == pytest.approx((float(f"{df['Close'].loc[d1]:.2f}") - float(f"{df['Open'].loc[d0]:.2f}")) * 10, abs=0.02)
    assert t["context"]["tags"] == journal.fingerprint(df, d0)["tags"]
    again = journal.import_csv(txt, "mine.csv")
    assert again["new_trades"] == 0 and again["duplicates_skipped"] == 1                 # no double counting
    assert len(journal.list_trades()["trades"]) == 1


def test_sample_journal_is_labelled_and_summarised():
    r = journal.import_sample()
    assert r["is_sample"] and "SAMPLE DATA" in r["label"] and r["rows_read"] > 200 and r["round_trips"] > 80 and not r["rows_rejected"]
    assert len(r["open_positions"]) == 3
    s = r["summary"]
    assert s["source_used"] == "sample" and any("SAMPLE" in n for n in s["notes"]) and s["overall"]["n"] == r["round_trips"]
    for p in s["bad_patterns"]:
        assert p["n"] >= 5 and p["win_rate"] <= p["base_win_rate"] - 0.15 and p["avg_return_pct"] < 0
    # every stored trade has a full 5-group fingerprint or is flagged incomplete
    for t in journal.list_trades(1000)["trades"]:
        assert t["is_sample"] == 1 and (len(t["tags"]) == 5) == bool(t["context_ok"])
    with closing(db.connect()) as con:
        assert con.execute("SELECT COUNT(*) FROM journal_trades WHERE is_sample=1").fetchone()[0] == r["round_trips"]


# ------------------------------------------------------------ pre-trade check
def test_check_neighbors_and_stats_are_consistent():
    journal.import_sample()
    c = journal.check("INFY", "2026-10-15", "buy", top_k=7)
    sim = c["similar"]
    assert sim["n"] == len(sim["neighbors"]) <= 7 and sim["candidates_above_threshold"] >= sim["n"]
    assert all(len(n["matching"]) >= journal.MIN_MATCH for n in sim["neighbors"])
    cos = [n["cosine"] for n in sim["neighbors"]]
    assert cos == sorted(cos, reverse=True)
    pn = [n["pnl"] for n in sim["neighbors"]]
    assert sim["stats"]["avg_pnl"] == pytest.approx(sum(pn) / len(pn), abs=0.01)
    assert sim["stats"]["win_rate"] == pytest.approx(sum(p > 0 for p in pn) / len(pn), abs=1e-3)
    assert c["recommendation"]["action"] in ("proceed", "wait", "reduce_size") and c["recommendation"]["reasons"]
    assert any("not a broker" in n for n in c["notes"])


def test_check_small_journal_is_low_confidence_proceed():
    df = real(); d0, d1 = df.index[-80], df.index[-70]
    journal.import_csv(csv_([f"RELIANCE,{d0.date()},buy,10,{df['Open'].loc[d0]:.2f}", f"RELIANCE,{d1.date()},sell,10,{df['Close'].loc[d1]:.2f}"]))
    c = journal.check("RELIANCE", None, "buy")
    assert c["recommendation"]["action"] == "proceed" and c["recommendation"]["confidence"] == "low" and c["similar"]["n"] == 0


def _insert(rows):
    """HAND-BUILT journal rows with chosen tags/PnL (synthetic fixtures for the rule tests)."""
    with closing(journal._db()) as con:
        for k, (tags, pnl, ret, sample) in enumerate(rows):
            con.execute("INSERT INTO journal_trades(import_id,uniq,is_sample,symbol,ticker,direction,qty,entry_date,exit_date,avg_entry,avg_exit,pnl,ret,hold_days,n_fills,tags,metrics,vec,context_ok) "
                        "VALUES('x',?,?,'AAA','AAA.NS','long',10,?,?,100,100,?,?,5,2,?,'{}',?,1)",
                        (f"u{k}", sample, f"2026-01-{1 + k % 28:02d}", f"2026-02-{1 + k % 28:02d}", pnl, ret, json.dumps(tags), json.dumps(journal.vec(tags))))
        con.commit()


def test_rules_wait_reduce_proceed_on_constructed_journals():
    df = real()
    when = df.index[-100]
    tags = journal.fingerprint(df, when)["tags"]
    other = {g: [v for v in vals if v != tags[g]][0] for g, vals in journal.JGROUPS.items()}
    # 12 losers in exactly this setup, 12 winners elsewhere  -> wait
    _insert([(tags, -500, -0.05, 0)] * 12 + [(other, 400, 0.04, 0)] * 12)
    c = journal.check("RELIANCE", str(when.date()), "buy")
    assert c["recommendation"]["action"] == "wait" and c["recommendation"]["size_factor"] == 0.0 and c["recommendation"]["wait_for"]
    assert c["similar"]["stats"]["win_rate"] == 0.0 and c["similar"]["n"] == 10
    journal.clear()
    # only 6 similar trades (2 wins, 4 losses): matched repeat-mistake pattern but n < 8, not severe -> reduce size
    _insert([(tags, 300, 0.03, 0)] * 2 + [(tags, -200, -0.02, 0)] * 4 + [(other, 300, 0.03, 0)] * 18)
    c = journal.check("RELIANCE", str(when.date()), "buy")
    assert c["recommendation"]["action"] == "reduce_size" and c["recommendation"]["size_factor"] == 0.5
    journal.clear()
    # similar trades clearly better than the rest -> proceed
    _insert([(tags, 300, 0.03, 0)] * 10 + [(other, -100, -0.01, 0)] * 14)
    c = journal.check("RELIANCE", str(when.date()), "buy")
    assert c["recommendation"]["action"] == "proceed" and c["recommendation"]["confidence"] in ("medium", "high")


def test_repeat_mistake_pattern_is_found_and_matched():
    df = real(); when = df.index[-100]; tags = journal.fingerprint(df, when)["tags"]
    other = {g: [v for v in vals if v != tags[g]][0] for g, vals in journal.JGROUPS.items()}
    g0 = "extension"
    bad = dict(other, **{g0: tags[g0]})            # differs from the proposed trade in 4 groups but shares the one bad tag
    _insert([(bad, -300, -0.04, 0)] * 8 + [(other, 400, 0.04, 0)] * 16)
    c = journal.check("RELIANCE", str(when.date()), "buy")
    m = c["mistake_pattern"]["matched"]
    assert m and {"group": g0, "value": tags[g0]} in m["conditions"] and m["n"] >= 5 and c["recommendation"]["action"] in ("reduce_size", "wait")


def test_real_journal_takes_precedence_over_sample():
    journal.import_sample()
    df = real(); d0, d1 = df.index[-80], df.index[-70]
    journal.import_csv(csv_([f"RELIANCE,{d0.date()},buy,10,{df['Open'].loc[d0]:.2f}", f"RELIANCE,{d1.date()},sell,10,{df['Close'].loc[d1]:.2f}"]))
    assert journal.summarize()["overall"]["n"] == 1 and journal.summarize("sample")["overall"]["n"] > 80
    assert journal.check("RELIANCE", None, "buy")["journal"]["source_used"] == "real"
    assert journal.check("RELIANCE", None, "buy", source="sample")["journal"]["source_used"] == "sample"


def test_short_side_uses_short_history_only():
    journal.import_sample()
    c = journal.check("TCS", None, "sell")
    assert c["direction"] == "short" and c["journal"]["n"] == 0 and c["recommendation"]["action"] == "proceed"


# ------------------------------------------------------------ API
def test_api_import_three_ways_and_check():
    df = real(); d0, d1 = df.index[-80], df.index[-70]
    txt = csv_([f"RELIANCE,{d0.date()},buy,10,{df['Open'].loc[d0]:.2f}", f"RELIANCE,{d1.date()},sell,10,{df['Close'].loc[d1]:.2f}"])
    r = client.post("/trades/import", files={"file": ("tb.csv", txt, "text/csv")}); assert r.status_code == 200 and r.json()["new_trades"] == 1
    r = client.post("/trades/import", content=txt, headers={"content-type": "text/csv"}); assert r.status_code == 200 and r.json()["duplicates_skipped"] == 1
    r = client.post("/trades/import", json={"csv_text": txt, "filename": "j.json"}); assert r.status_code == 200 and r.json()["filename"] == "j.json"
    assert client.get("/trades").json()["n"] == 1


def test_api_sample_flow_and_check_and_errors():
    assert "SAMPLE DATA" in client.get("/trades/sample").text
    r = client.post("/trades/import-sample"); assert r.status_code == 200 and r.json()["is_sample"]
    assert client.get("/trades/summary").json()["overall"]["n"] == r.json()["round_trips"]
    c = client.post("/trades/check", json={"symbol": "HDFCBANK.NS", "date": "2026-10-20", "side": "buy", "top_k": 5})
    assert c.status_code == 200 and c.json()["similar"]["n"] <= 5 and c.json()["recommendation"]["action"]
    assert client.post("/trades/check", json={"symbol": "RELIANCE", "side": "hold"}).status_code == 422
    assert client.post("/trades/check", json={"symbol": "NOTREAL!!"}).status_code == 422
    assert client.post("/trades/import", content="a,b\n1,2\n", headers={"content-type": "text/csv"}).status_code == 422
    assert client.post("/trades/import?cost_bps=9999", content="x", headers={"content-type": "text/csv"}).status_code == 422
    assert client.delete("/trades?sample_only=true").json()["deleted_trades"] == r.json()["round_trips"]
    assert client.get("/trades").json()["n"] == 0


def test_meko_bad_token_journal_still_stored_in_sqlite(monkeypatch):
    monkeypatch.setattr(config, "MEKO_TOKEN", "mko_tkn_FAKE")      # rejected / unreachable: SQLite stays the source of truth
    df = real(); d0, d1 = df.index[-80], df.index[-70]
    r = journal.import_csv(csv_([f"RELIANCE,{d0.date()},buy,10,{df['Open'].loc[d0]:.2f}", f"RELIANCE,{d1.date()},sell,10,{df['Close'].loc[d1]:.2f}"]))
    assert r["stored_in"] == "sqlite" and r["meko_mirror"] == "queued"
    meko.join(60)
    assert len(journal.list_trades()["trades"]) == 1 and meko.status()["writes_ok"] == 0


def test_memory_proof_walk_forward_is_consistent_and_has_no_lookahead():
    journal.import_sample()
    p = journal.proof()
    assert p["is_sample"] and p["n_trades"] == sum(p["decisions"].values()) and p["decisions"]["warmup"] >= 10
    assert p["off"]["total_pnl"] == pytest.approx(journal.summarize()["overall"]["total_pnl"], abs=0.5)
    assert p["on"]["total_pnl"] == pytest.approx(sum(s["pnl_on"] for s in p["steps"]), abs=0.5)
    assert all(s["size"] in (0.0, 0.5, 1.0) for s in p["steps"]) and p["series"][-1]["off"] == pytest.approx(p["off"]["total_pnl"], abs=0.5)
    # dropping the newest trade must not change any earlier decision (a decision only sees trades closed before its entry)
    last = max(p["steps"], key=lambda s: (s["entry_date"], s["exit_date"]))
    with closing(journal._db()) as con:
        con.execute("DELETE FROM journal_trades WHERE ticker=? AND entry_date=? AND exit_date=?", (last["ticker"], last["entry_date"], last["exit_date"])); con.commit()
    q = journal.proof()
    key = lambda s: (s["ticker"], s["entry_date"], s["exit_date"])
    qa = {key(s): s["action"] for s in q["steps"]}
    assert q["n_trades"] == p["n_trades"] - 1 and all(qa[key(s)] == s["action"] for s in p["steps"] if key(s) in qa)


def test_api_memory_proof_and_empty_error():
    assert client.get("/memory/proof").status_code == 422
    client.post("/trades/import-sample")
    r = client.get("/memory/proof"); assert r.status_code == 200 and r.json()["is_sample"] and len(r.json()["series"]) == r.json()["n_trades"]
