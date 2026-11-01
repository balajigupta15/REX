"""Live Strategy Tracker: exit logic equivalence with the backtest, logged-trade P&L + exit alert, paper mode, offline / market-closed behaviour,
persistence, memory + journal feed. Rex never places orders: nothing here (or in the API) can."""
from __future__ import annotations
import json
from datetime import datetime
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import backtest, data, livedata, rules, tracker
from app.main import app
from app.livedata import IST

client = TestClient(app)


def _spec(**kw):
    return {**dict(ticker="RELIANCE.NS", template="breakout"), **kw}


def _bars(rows):
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=pd.date_range("2026-01-01", periods=len(rows)))


# ---------------------------------------------------------------- exit rules == backtest rules
@pytest.mark.parametrize("ticker,template", [("RELIANCE.NS", "rsi"), ("TCS.NS", "mean_reversion"), ("INFY.NS", "breakout"), ("HDFCBANK.NS", "ma_cross")])
def test_evaluate_exit_matches_backtest_trades(ticker, template):
    df, _ = data.load(ticker)
    p, e = rules.validate(template, {}, {"stop_pct": 3, "tp_pct": 4, "hold_days": 6})
    res = backtest.run(df, template, p, e, 15)
    idx = {str(d.date()): i for i, d in enumerate(df.index)}
    checked = reasons = 0
    seen = set()
    for t in res["full"]["trades"]:
        if t["reason"] == "window_end":
            continue
        ei, xi = idx[t["entry_date"]], idx[t["exit_date"]]
        out = tracker.evaluate_exit(df.iloc[:xi + 1], ei, float(df["Open"].iloc[ei]), e["hold_days"], e["stop_pct"], e["tp_pct"], 1, True)
        assert out["fired"] and out["idx"] == xi and out["reason"] == t["reason"], (t, out)
        assert abs(out["px"] - t["exit"]) < 0.011
        if xi > ei:        # one bar earlier the exit must not have fired
            assert not tracker.evaluate_exit(df.iloc[:xi], ei, float(df["Open"].iloc[ei]), e["hold_days"], e["stop_pct"], e["tp_pct"], 1, True)["fired"]
        checked += 1; seen.add(t["reason"])
    assert checked >= 8 and len(seen) >= 2


def test_evaluate_exit_rules_on_synthetic_bars():
    # entry bar 0 at open 100; stop 5% = 95, target 10% = 110
    b = _bars([(100, 101, 99, 100), (100, 112, 94, 100)])           # both levels inside one bar -> stop wins (conservative, same as backtest)
    r = tracker.evaluate_exit(b, 0, 100, 10, 5, 10)
    assert (r["fired"], r["reason"], r["idx"], round(r["px"], 2)) == (True, "stop", 1, 95.0)
    b = _bars([(100, 101, 99, 100), (93, 94, 92, 93)])               # gap down through the stop fills at the open
    r = tracker.evaluate_exit(b, 0, 100, 10, 5, 10)
    assert (r["reason"], r["px"]) == ("stop", 93.0)
    b = _bars([(100, 120, 90, 100)])                                  # entry bar, entry NOT at its open: only the close counts
    assert not tracker.evaluate_exit(b, 0, 100, 10, 5, 10, entry_extremes=False)["fired"]
    assert tracker.evaluate_exit(b, 0, 100, 10, 5, 10, entry_extremes=True)["reason"] == "stop"
    b = _bars([(100, 101, 99, 100)] + [(100, 101, 99, 100)] * 3)      # hold limit
    r = tracker.evaluate_exit(b, 0, 100, 3, 5, 10)
    assert (r["reason"], r["idx"], r["days_held"]) == ("hold", 3, 3)
    assert not tracker.evaluate_exit(b.iloc[:3], 0, 100, 3, 5, 10)["fired"]
    # short: levels mirrored (stop above, target below)
    b = _bars([(100, 101, 99, 100), (100, 106, 99, 105)])
    r = tracker.evaluate_exit(b, 0, 100, 10, 5, 10, direction=-1)
    assert (r["reason"], r["px"]) == ("stop", 105.0)
    r = tracker.evaluate_exit(_bars([(100, 101, 99, 100), (100, 100, 89, 90)]), 0, 100, 10, 5, 10, direction=-1)
    assert (r["reason"], r["px"]) == ("take_profit", 90.0)
    # entry bar does not exist yet: only the live price is compared with the levels
    assert tracker.evaluate_exit(b.iloc[:1], 5, 100, 10, 5, 10, price_now=94)["reason"] == "stop"
    assert not tracker.evaluate_exit(b.iloc[:1], 5, 100, 10, 5, 10, price_now=99)["fired"]


# ---------------------------------------------------------------- market state / offline
def test_market_state_ist():
    ms = lambda y, m, d, h, mi: livedata.market_state(datetime(y, m, d, h, mi, tzinfo=IST))
    assert ms(2026, 11, 1, 12, 0)["state"] == "weekend" and not ms(2026, 11, 1, 12, 0)["open"]
    assert ms(2026, 10, 30, 10, 0)["open"]
    assert ms(2026, 10, 30, 15, 31)["state"] == "after_close"
    assert ms(2026, 11, 2, 8, 0)["state"] == "pre_open"
    assert ms(2026, 11, 1, 12, 0)["next_open"].startswith("Mon 02 Nov 09:15")
    utc = datetime(2026, 10, 30, 4, 30, tzinfo=__import__("datetime").timezone.utc)      # 10:00 IST
    assert livedata.market_state(utc)["open"]


def test_offline_market_closed_shows_last_close_without_crash():
    r = client.post("/paper/start", json=_spec())
    assert r.status_code == 200
    j = r.json()
    q = j["quote"]
    assert q["stale"] and "offline" in q["source"] and q["price"] == q["last_close"]
    assert j["phase"] in ("watching", "open") and j["last_error"] is None
    assert any("delayed about 15 minutes" in s for s in j["data_labels"]) and any("Market:" in s for s in j["data_labels"])
    assert j["market"]["state"] in ("weekend", "pre_open", "after_close", "open")
    assert "never places orders" in j["disclaimer"]


def test_unknown_ticker_and_bad_input_are_422_not_500():
    assert client.post("/paper/start", json=_spec(ticker="ZZZNOTREAL.NS")).status_code == 422
    assert client.post("/paper/start", json=_spec(template="nope")).status_code == 422
    assert client.post("/paper/start", json=_spec(trade=dict(side="hold"))).status_code == 422
    assert client.post("/paper/start", json=_spec(trade=dict(side="buy", entered_at="2999-01-01"))).status_code == 422
    assert client.post("/paper/start", json=_spec(trade=dict(side="buy", entry_price=-5))).status_code == 422
    assert client.get("/paper/doesnotexist").status_code == 404
    assert client.post("/paper/doesnotexist/stop").status_code == 404


# ---------------------------------------------------------------- logged trade ("I took this trade")
def _last_close(t="RELIANCE.NS"):
    return float(data.load(t)[0]["Close"].iloc[-1])


def test_logged_trade_live_pnl_and_distance_to_levels():
    lc = _last_close()
    j = client.post("/paper/start", json=_spec(exits=dict(stop_pct=5, tp_pct=10, hold_days=10),
                                                 trade=dict(side="buy", entry_price=round(lc * 0.98, 2), qty=10))).json()
    assert j["mode"] == "logged" and j["phase"] == "open"
    p = j["position"]
    assert p["direction"] == "long" and p["qty"] == 10
    exp_gross = (lc / p["entry_px"] - 1) * 100
    assert abs(p["gross_pct"] - exp_gross) < 0.02 and abs(p["net_pct"] - (exp_gross - 0.15)) < 0.02          # 15 bps cost
    assert abs(p["pnl_abs"] - (p["net_pct"] / 100 * p["entry_px"] * 10)) < 1.0
    assert abs(p["stop_px"] - p["entry_px"] * 0.95) < 0.01 and abs(p["tp_px"] - p["entry_px"] * 1.10) < 0.01
    assert abs(p["dist_to_stop_pct"] - (lc - p["stop_px"]) / lc * 100) < 0.02
    assert abs(p["dist_to_tp_pct"] - (p["tp_px"] - lc) / lc * 100) < 0.02
    assert j["exit_signal"] is None or not j["exit_signal"]["fired"]
    assert j["fills"][0]["kind"] == "logged_entry"
    assert j["chart"]["points"][-1]["d"] == "live" and j["chart"]["markers"][0]["kind"] == "entry"


def test_strategy_says_exit_now_when_stop_level_is_hit():
    lc = _last_close()
    j = client.post("/paper/start", json=_spec(exits=dict(stop_pct=5, tp_pct=10, hold_days=10),
                                                 trade=dict(side="buy", entry_price=round(lc * 1.08, 2), qty=1))).json()
    s = j["exit_signal"]
    assert s["fired"] and s["reason"] == "stop" and "Stop-loss" in s["text"]
    assert j["first_exit_signal"]["reason"] == "stop"
    row = [x for x in client.get("/paper/sessions").json()["sessions"] if x["id"] == j["id"]][0]
    assert row["exit_now"] is True and row["open_pct"] < -5


def test_logged_short_mirrors_levels_and_pnl():
    lc = _last_close()
    j = client.post("/paper/start", json=_spec(exits=dict(stop_pct=5, tp_pct=10, hold_days=10),
                                                 trade=dict(side="sell", entry_price=round(lc * 1.02, 2), qty=5))).json()
    p = j["position"]
    assert p["direction"] == "short" and p["gross_pct"] > 0                      # price below the short entry: in profit
    assert p["stop_px"] > p["entry_px"] > p["tp_px"]


def test_hold_limit_alert_for_old_logged_trade():
    df, _ = data.load("RELIANCE.NS")
    d = str(df.index[-8].date())
    j = client.post("/paper/start", json=_spec(exits=dict(stop_pct=30, tp_pct=60, hold_days=3),
                                                 trade=dict(side="buy", entry_price=float(df["Open"].iloc[-8]), qty=1, entered_at=d))).json()
    assert j["exit_signal"]["fired"] and j["exit_signal"]["reason"] == "hold"


def test_close_logged_trade_saves_outcome_to_memory_and_journal():
    lc = _last_close()
    sid = client.post("/paper/start", json=_spec(trade=dict(side="buy", entry_price=round(lc * 0.99, 2), qty=20))).json()["id"]
    r = client.post(f"/paper/{sid}/stop", json=dict(exit_price=round(lc * 1.01, 2), note="took profit"))
    assert r.status_code == 200
    j = r.json()
    c = j["close"]
    assert j["status"] == "closed" and c["verdict"] == "WON" and c["net_pct"] > 1.5 and c["side"] == "buy"
    assert c["mfe_pct"] is not None and c["mae_pct"] is not None and c["note"] == "took profit"
    assert c["saved"]["strategy_memory"]["run_id"] == f"trk_{sid}" and c["saved"]["lesson"]
    assert c["saved"]["journal"]["added"] is True
    # strategy-run memory has ONE row for the session (created at start, updated at close)
    runs = client.get("/memory/strategy-runs", params=dict(ticker="RELIANCE.NS", template="breakout")).json()["runs"]
    mine = [x for x in runs if x["session_id"] == sid]
    assert len(mine) == 1 and mine[0]["kind"] == "tracked_trade" and mine[0]["verdict"] == "WON" and mine[0]["outcome"]["net_pct"] == c["net_pct"]
    # the real journal now holds the trade, with the same context fingerprint as imported trades
    jt = client.get("/trades", params=dict(source="real")).json() if client.get("/trades").status_code == 200 else None
    if jt is not None:
        assert jt["n"] == 1 and jt["trades"][0]["ticker"] == "RELIANCE.NS" and jt["trades"][0]["tags"]
    # closing twice is refused
    assert client.post(f"/paper/{sid}/stop").status_code == 422


def test_close_without_journal_and_loss_lesson():
    lc = _last_close()
    sid = client.post("/paper/start", json=_spec(trade=dict(side="buy", entry_price=round(lc * 1.03, 2)))).json()["id"]
    j = client.post(f"/paper/{sid}/stop", json=dict(add_to_journal=False)).json()
    assert j["close"]["verdict"] == "LOST" and j["close"]["saved"]["journal"]["added"] is False
    assert j["close"]["pnl_abs"] is None            # no qty given: percent only, never invented


def test_exit_signal_ignored_is_reported_in_the_lesson():
    lc = _last_close()
    sid = client.post("/paper/start", json=_spec(exits=dict(stop_pct=5, tp_pct=10, hold_days=10),
                                                   trade=dict(side="buy", entry_price=round(lc * 1.08, 2), qty=1))).json()["id"]
    # the stop level is 1.026*lc; the user sells far below it
    j = client.post(f"/paper/{sid}/stop", json=dict(exit_price=round(lc * 0.95, 2))).json()
    sig = j["close"]["exit_signal"]
    assert sig["reason"] == "stop" and sig["cost_of_waiting_pct"] < -3
    assert "exit fired" in j["close"]["saved"]["lesson"] and "cost about" in j["close"]["saved"]["lesson"]


# ---------------------------------------------------------------- live feed (simulated intraday) while the market is open
def test_live_intraday_price_when_market_open(monkeypatch):
    lc = _last_close()
    seen = {}

    def provider(t, now):
        seen["called"] = True
        return dict(price=lc * 0.96, price_time="2026-11-02 11:00 IST", day_open=lc * 1.0, day_high=lc * 1.01, day_low=lc * 0.955, interval="1m")
    monkeypatch.setattr(livedata, "PROVIDER", provider)
    now = datetime(2026, 11, 2, 11, 15, tzinfo=IST)                               # Monday, market open
    j = tracker.start("RELIANCE.NS", "breakout", {}, {"stop_pct": 3, "tp_pct": 10, "hold_days": 10}, None, "original",
                      dict(side="buy", entry_price=lc, qty=10, entered_at="2026-10-30"), False, None, now)
    assert seen.get("called")
    q = j["quote"]
    assert not q["stale"] and "intraday" in q["source"] and "delayed" in q["source"] and q["day_low"] < lc
    assert abs(j["position"]["gross_pct"] - (-4.0)) < 0.05
    assert j["exit_signal"]["fired"] and j["exit_signal"]["reason"] == "stop"      # today's low went through the 3% stop
    assert j["market"]["state"] == "open"


def test_live_feed_failure_falls_back_to_last_close(monkeypatch):
    monkeypatch.setattr(livedata, "PROVIDER", lambda t, now: None)
    now = datetime(2026, 11, 2, 11, 15, tzinfo=IST)
    j = tracker.start("TCS.NS", "rsi", {}, {}, None, "original", None, False, None, now)
    assert j["quote"]["stale"] and "unavailable" in (j["quote"]["note"] or "") and j["last_error"] is None
    assert any("unavailable" in s for s in j["data_labels"])


# ---------------------------------------------------------------- paper mode (no logged trade)
def test_track_strategy_without_a_trade_and_list():
    j = client.post("/paper/start", json=_spec()).json()
    assert j["mode"] == "paper" and j["position"] is None and j["entry_watch"]["date"]
    ls = client.get("/paper/sessions").json()
    assert ls["sessions"][0]["id"] == j["id"] and ls["market"]["label"] and "loop" in ls
    assert client.post(f"/paper/{j['id']}/refresh").status_code == 200
    c = client.post(f"/paper/{j['id']}/stop").json()["close"]
    assert c["verdict"] == "NO TRADE"


def test_simulate_entry_now_is_labelled_and_tracked():
    j = client.post("/paper/start", json=_spec(simulate_entry_now=True, qty=3)).json()
    assert j["phase"] == "open" and j["position"]["origin"] == "manual_paper"
    assert "paper entry" in j["fills"][0]["reason"]
    c = client.post(f"/paper/{j['id']}/stop").json()["close"]
    assert c["mode"] == "paper" and c["trades"] == 1 and c["verdict"] in ("FLAT", "WON", "LOST")


@pytest.mark.parametrize("ticker,template,params", [("RELIANCE.NS", "rsi", {}), ("TCS.NS", "mean_reversion", {}), ("SBIN.NS", "breakout", {"n": 10})])
def test_paper_session_replays_backtest_trades_bar_by_bar(monkeypatch, ticker, template, params):
    """Feed real bars one at a time: the paper engine must open/close exactly the trades the backtest engine produces."""
    df, info = data.load(ticker)
    p, e = rules.validate(template, params, {"stop_pct": 3, "tp_pct": 4, "hold_days": 5})
    a, b = len(df) - 260, len(df) - 1
    clock = {"n": a}

    def fake_snapshot(t, now=None):
        bars = df.iloc[:clock["n"] + 1]
        px = float(bars["Close"].iloc[-1])
        return dict(quote=dict(price=px, price_time=str(bars.index[-1].date()), source="replay", stale=True, delay_note="", note=None, last_close=px,
                               last_close_date=str(bars.index[-1].date()), day_open=None, day_high=None, day_low=None),
                    bars=bars, live_bar=None, market=livedata.market_state(), info=info)
    monkeypatch.setattr(livedata, "snapshot", fake_snapshot)
    j = tracker.start(ticker, template, p, e, 15)
    sid = j["id"]
    for n in range(a + 1, b + 1):
        clock["n"] = n
        tracker.tick(sid)
    got = tracker.get(sid)["summary"]["closed_trades"]
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    sig = rules.signals(df, template, p)
    _, trs = backtest._run_window(o, h, l, c, sig, a, b, e["hold_days"], e["stop_pct"], e["tp_pct"], 0.0015)
    want = [t for t in trs if t["reason"] != "window_end"]
    assert len(want) >= 3, "pick a strategy that trades inside the window"
    assert [(g["entry_date"], g["exit_date"], g["reason"]) for g in got][:len(want)] == \
           [(str(df.index[t["entry_idx"]].date()), str(df.index[t["exit_idx"]].date()), "hold" if t["reason"] == "hold" else t["reason"]) for t in want]
    for g, t in zip(got, want):
        assert abs(g["entry"] - t["entry"]) < 1e-6 and abs(g["exit"] - t["exit"]) < 1e-6
        assert abs(g["net_pct"] - round(((t["exit"] / t["entry"] - 1) - 0.0015) * 100, 2)) < 0.1      # 15 bps round-trip cost


# ---------------------------------------------------------------- persistence
def test_sessions_fills_and_state_survive_restart():
    lc = _last_close()
    sid = client.post("/paper/start", json=_spec(trade=dict(side="buy", entry_price=round(lc * 0.97, 2), qty=7))).json()["id"]
    before = tracker.get(sid)
    tracker.stop_loop(); livedata.clear_cache()                 # "restart": nothing is held in memory, everything is in SQLite
    after = TestClient(app).get(f"/paper/{sid}").json()
    for k in ("id", "mode", "ticker", "template", "exits", "status", "phase", "fills", "position", "trade"):
        assert after[k] == before[k], k
    assert tracker.tick_all() == 1                              # the background loop step picks the session up again
    assert any(s["id"] == sid for s in client.get("/paper/sessions", params=dict(status="active")).json()["sessions"])


def test_tick_all_survives_a_broken_session(monkeypatch):
    client.post("/paper/start", json=_spec())
    client.post("/paper/start", json=_spec(ticker="TCS.NS"))
    orig = tracker.tick

    def flaky(sid, now=None):
        if flaky.n == 0:
            flaky.n += 1
            raise RuntimeError("boom")
        return orig(sid, now)
    flaky.n = 0
    monkeypatch.setattr(tracker, "tick", flaky)
    assert tracker.tick_all() == 2 and tracker.loop_status()["last_error"]


def test_tracker_alias_and_no_order_endpoints():
    assert client.get("/tracker/paper/sessions").status_code == 200
    paths = " ".join(app.openapi()["paths"])
    assert "order" not in paths.lower()
