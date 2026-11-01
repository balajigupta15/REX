"""Strategy-run memory: every backtest / refine / tracker session is saved with the real forward price move, and a "Last time" panel
with a rule-based lesson comes back for similar strategies."""
from __future__ import annotations
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import data, meko, strategy_memory as sm
from app.main import app

client = TestClient(app)
BT = dict(ticker="RELIANCE.NS", template="breakout")


def _row(**kw):
    base = dict(kind="backtest", source="live", verdict="LOST", stats=dict(test_return=-3.1), fwd5=None, fwd20=None, ticker="RELIANCE.NS", template="breakout",
                run_date="2026-08-12", outcome=None)
    return {**base, **kw}


def test_forward_returns_use_real_closes():
    df, _ = data.load("RELIANCE.NS")
    d = df.index[-40]
    f = sm.forward_returns(df, str(d.date()))
    c = df["Close"]
    assert f["fwd5"] == round((c.iloc[-35] / c.iloc[-40] - 1) * 100, 2) and f["fwd20"] == round((c.iloc[-20] / c.iloc[-40] - 1) * 100, 2)
    assert f["days_since"] == 39 and f["base_date"] == str(d.date())
    # an anchor on a non-trading day uses the previous bar
    sat = (df.index[-40] + pd.Timedelta(days=1)).date() if (df.index[-39] - df.index[-40]).days > 1 else d.date()
    assert sm.forward_returns(df, str(sat))["base_date"] == str(d.date())
    # not enough bars yet: None, never a guess
    g = sm.forward_returns(df, str(df.index[-3].date()))
    assert g["fwd5"] is None and g["fwd20"] is None and g["days_since"] == 2 and g["since_pct"] is not None
    h = sm.forward_returns(df, str(df.index[-10].date()))
    assert h["fwd5"] is not None and h["fwd20"] is None


def test_backtest_is_saved_and_shown_as_last_time():
    assert client.get("/memory/strategy-runs", params=BT).json()["count"] == 0
    j = client.post("/backtest", json=BT).json()
    sr = j["memory"]["strategy_run"]
    assert sr["stored_in"] == "sqlite" and sr["verdict"] in ("WORKED", "LOST", "FADED", "INCONCLUSIVE")
    q = client.get("/memory/strategy-runs", params=BT).json()
    lt = q["last_time"]
    assert q["count"] == 1 and lt["id"] == sr["run_id"] and lt["match"] == "same ticker and strategy" and lt["kind"] == "backtest"
    assert lt["headline"].startswith("On ") and "you ran breakout on RELIANCE" in lt["headline"] and lt["verdict"] in lt["headline"]
    assert "test " in lt["headline"] and lt["lesson"]
    assert lt["stats"]["test_return"] is not None and lt["stats"]["test_trades"] is not None
    # the run happened on the last data bar, so there is no forward data yet: said honestly
    assert lt["fwd20"] is None and "not enough trading days" in lt["headline"]


def test_remember_false_saves_nothing():
    client.post("/backtest", json={**BT, "remember": False})
    assert sm.count() == 0


def test_refine_is_saved_with_its_verdict():
    r = client.post("/refine", json=BT).json()
    sr = r["memory"]["strategy_run"]
    assert sr["verdict"] == {"overfit": "OVERFIT", "inconclusive": "INCONCLUSIVE", "ok": "HELD UP", "no_change": "NO CHANGE"}[r["verdict"]["flag"]]
    row = client.get("/memory/strategy-runs", params=BT).json()["last_time"]
    assert row["kind"] == "refine" and f"refined breakout on RELIANCE: {sr['verdict']}" in row["headline"]


def test_matching_prefers_same_ticker_and_strategy_then_ticker_then_template():
    df, _ = data.load("RELIANCE.NS")
    mk = lambda tk, tpl, anchor: sm.record_run("backtest", tk, tpl, {}, {}, 15, verdict="LOST", stats=dict(test_return=-1.0), df=df, anchor_date=anchor)
    a = mk("RELIANCE.NS", "rsi", str(df.index[-60].date()))["run_id"]
    b = mk("TCS.NS", "breakout", str(df.index[-60].date()))["run_id"]
    c = mk("RELIANCE.NS", "breakout", str(df.index[-60].date()))["run_id"]
    mk("INFY.NS", "ma_cross", str(df.index[-60].date()))
    ids = [r["id"] for r in sm.query("RELIANCE.NS", "breakout")["runs"]]
    assert ids[:3] == [c, a, b]            # exact, same ticker, same template; unrelated INFY/ma_cross excluded
    assert len(ids) == 3
    assert {r["id"] for r in sm.query(None, "breakout")["runs"]} == {b, c}
    assert sm.query("RELIANCE.NS", "breakout", exclude_id=c)["runs"][0]["id"] == a


def test_forward_return_filled_in_when_data_exists_and_example_headline():
    df, _ = data.load("RELIANCE.NS")
    anchor = str(df.index[-30].date())
    sm.record_run("refine", "RELIANCE.NS", "breakout", {}, {}, 15, verdict="OVERFIT", stats=dict(test_return=-3.1), anchor_date=anchor, df=None,
                  run_date="2026-09-01", run_id="x1")
    r = sm.query("RELIANCE.NS", "breakout")["runs"][0]
    want = round((df["Close"].iloc[-10] / df["Close"].iloc[-30] - 1) * 100, 2)
    assert r["fwd20"] == want and r["verdict"] == "OVERFIT"
    assert r["headline"] == f"On 1 Sep 2026 you refined breakout on RELIANCE: OVERFIT, test -3.1%; price then moved {want:+.1f}% in 20 days."
    assert "OVERFIT" in r["lesson"] or "tuned" in r["lesson"]
    # persisted: a second query does not need to recompute
    assert sm.query("RELIANCE.NS")["runs"][0]["fwd20"] == want


@pytest.mark.parametrize("verdict,fwd,needle", [("OVERFIT", None, "tuned"), ("FADED", None, "faded"), ("LOST", 5.0, "rule, not the stock"),
                                                 ("LOST", -5.0, "locked year"), ("INCONCLUSIVE", None, "Too few"), ("WORKED", -4.0, "price fell afterwards"),
                                                 ("WORKED", 1.0, "held up"), ("HELD UP", None, "held up")])
def test_rule_based_lessons(verdict, fwd, needle):
    assert needle in sm.lesson(_row(verdict=verdict, fwd20=fwd))


def test_tracked_trade_lessons_use_the_outcome():
    o = dict(net_pct=-4.0, side="buy", mfe_pct=0.5, mae_pct=-6.0, stop_pct=5.0, exit_reason="stop", exit_signal=None)
    assert "stop level" in sm.lesson(_row(kind="tracked_trade", verdict="LOST", outcome=o))
    o = dict(net_pct=1.0, side="buy", mfe_pct=4.0, mae_pct=-0.5, exit_reason="manual", exit_signal=None)
    assert "best" in sm.lesson(_row(kind="tracked_trade", verdict="WON", outcome=o))
    o = dict(net_pct=-2.0, exit_signal=dict(date="2026-10-28", cost_of_waiting_pct=-2.5))
    assert "exit fired on 2026-10-28" in sm.lesson(_row(kind="tracked_trade", verdict="LOST", outcome=o))


def test_seed_replays_are_real_asof_runs_with_real_forward_returns():
    out = sm.seed_replays(tickers=["RELIANCE.NS", "TCS.NS"])
    assert out["replayed_runs"] >= 12
    q = sm.query("RELIANCE.NS", "breakout")
    assert q["runs"] and all(r["source"] == "replay" and r["replayed"] for r in q["runs"])
    r = q["runs"][0]
    assert r["fwd20"] is not None and "replayed from real data" in r["headline"] and r["run_date"] < "2026-10-30"
    df, _ = data.load("RELIANCE.NS")
    assert r["fwd20"] == sm.forward_returns(df, r["anchor_date"])["fwd20"]
    assert sm.seed_replays(tickers=["RELIANCE.NS", "TCS.NS"])["replayed_runs"] == 0          # idempotent
    # replays are NOT mirrored into a user's Meko store
    sent = []
    meko_enabled = meko.enabled
    meko.enabled = lambda: True; meko.mirror_async = lambda items: sent.extend(items)
    try:
        sm.seed_replays(tickers=["INFY.NS"])
    finally:
        meko.enabled = meko_enabled
    assert not sent


def test_seed_endpoint_includes_replays():
    s = client.post("/memory/seed").json()
    assert s["strategy_replays"]["replayed_runs"] > 50
    q = client.get("/memory/strategy-runs", params=BT).json()
    assert q["count"] >= 2 and q["last_time"]["fwd20"] is not None


def test_tracker_session_start_creates_run_and_close_updates_it():
    sid = client.post("/paper/start", json=BT).json()["id"]
    rows = [r for r in client.get("/memory/strategy-runs", params=BT).json()["runs"] if r["session_id"] == sid]
    assert len(rows) == 1 and rows[0]["kind"] == "tracker" and rows[0]["verdict"] == "TRACKING" and "you tracked breakout on RELIANCE" in rows[0]["headline"]
    client.post(f"/paper/{sid}/stop")
    rows = [r for r in client.get("/memory/strategy-runs", params=BT).json()["runs"] if r["session_id"] == sid]
    assert len(rows) == 1 and rows[0]["verdict"] == "NO TRADE"


def test_meko_mirror_gets_strategy_runs_when_enabled(monkeypatch):
    sent = []
    monkeypatch.setattr(meko, "enabled", lambda: True)
    monkeypatch.setattr(meko, "mirror_async", lambda items: sent.extend(items))
    r = client.post("/backtest", json=BT).json()
    kinds = [k for k, _, _ in sent]
    assert "strategy_run" in kinds and "backtest_result" in kinds
    text = [t for k, t, _ in sent if k == "strategy_run"][0]
    assert "breakout on RELIANCE" in text
    assert r["memory"]["strategy_run"]["meko_mirror"] == "queued"


def test_query_with_unknown_ticker_is_clean_error():
    r = client.get("/memory/strategy-runs", params=dict(ticker="ZZZNOTREAL.NS"))
    assert r.status_code in (200, 422)
    assert client.get("/memory/strategy-runs").json()["count"] == 0


def test_query_reports_total_beyond_limit(tmp_path):
    from app import strategy_memory as sm
    out = sm.query(None, None, 1)
    assert out["total"] >= out["count"] == min(1, out["total"])
