import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import backtest, config, data, memory, meko, refine, rules
from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module")
def df():
    return data.load("RELIANCE.NS")[0]


# ---------- refine ----------
def test_candidates_include_original_and_are_nearby_and_capped():
    p, e = rules.validate("ma_cross", {}, {})
    c = refine.candidates("ma_cross", p, e)
    assert c[0] == (p, e) and len(c) <= refine.MAX_TRIALS
    assert all(pp["fast"] < pp["slow"] for pp, _ in c)
    assert all(0.6 * 10 - 1 <= pp["fast"] <= 1.5 * 10 + 1 for pp, _ in c)


def test_refine_never_looks_at_locked_year(df):
    """Wreck every bar of the locked year: the chosen parameters must not change."""
    p, e = rules.validate("breakout", {}, {})
    r1 = refine.refine(df, "breakout", p, e)
    k = data.split_index(df)
    d2 = df.astype(float)
    d2.iloc[k:, :] = d2.iloc[k:, :] * np.linspace(1, 0.3, len(d2) - k)[:, None]
    r2 = refine.refine(d2, "breakout", p, e)
    assert r1["refined"]["params"] == r2["refined"]["params"] and r1["refined"]["exits"] == r2["refined"]["exits"]
    assert r1["original"]["result"]["train"]["metrics"] == r2["original"]["result"]["train"]["metrics"]
    assert r1["original"]["result"]["locked"]["metrics"] != r2["original"]["result"]["locked"]["metrics"]


def test_train_and_locked_windows_do_not_overlap(df):
    p, e = rules.validate("rsi", {}, {})
    res = backtest.run(df, "rsi", p, e)
    assert res["train"]["end"] < res["locked"]["start"]
    assert max(t["exit_date"] for t in res["train"]["trades"]) < res["locked"]["start"]
    assert len(res["full"]["equity"]) == len(res["train"]["equity"]) + len(res["locked"]["equity"])
    assert res["full"]["equity"][-1] == pytest.approx(
        res["train"]["equity"][-1] * res["locked"]["equity"][-1], rel=1e-4)


def test_verdict_flags_overfit_and_no_change():
    ok = dict(sharpe=1.0, total_return=0.2, trades=15)
    bad = dict(sharpe=-0.3, total_return=-0.1, trades=12)
    orig = dict(sharpe=0.0, total_return=0.0, trades=12)
    assert refine._verdict(orig, orig, ok, bad, False, 400, 300)["overfit"] is True
    assert refine._verdict(orig, orig, ok, ok, False, 400, 300)["flag"] == "ok"
    assert refine._verdict(orig, orig, ok, dict(ok, trades=1), False, 400, 300)["flag"] != "ok"
    assert refine._verdict(orig, orig, ok, ok, True, 400, 300)["flag"] == "no_change"


def test_benchmark_matches_close_to_close(df):
    p, e = rules.validate("breakout", {}, {})
    res = backtest.run(df, "breakout", p, e)
    a = df.index.get_loc(pd.Timestamp(res["locked"]["start"]))
    expect = df["Close"].iloc[-1] / df["Close"].iloc[a - 1] - 1
    assert res["locked"]["benchmark"]["total_return"] == pytest.approx(expect)


# ---------- memory ----------
def test_memory_roundtrip_lessons_and_check(df):
    assert memory.status()["backend"] == "sqlite"
    for tk in ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS"]:
        d, info = data.load(tk)
        for tpl in ("breakout", "mean_reversion"):
            p, e = rules.validate(tpl, {}, {})
            memory.record("backtest", tk, tpl, p, e, 15, backtest.run(d, tpl, p, e))
    st = memory.status()
    assert st["runs"] == 8 and st["trades"] > 50 and st["lessons"] >= 8
    tf = rules.tag_frame(df)
    cur = {g: str(tf[g].iloc[-1]) for g in rules.TAG_GROUPS}
    chk = memory.check("RELIANCE.NS", "breakout", *rules.validate("breakout", {}, {}), cur, "2026-10-30")
    assert chk["memory_runs"] == 8 and chk["warnings"]
    assert any("exact setup" in w["text"] for w in chk["warnings"])
    if chk["similar"]:
        assert chk["similar"]["min_cosine"] == 0.75 and 0 <= chk["similar"]["win_rate"] <= 1


def test_empty_memory_check():
    chk = memory.check("TCS.NS", "rsi", *rules.validate("rsi", {}, {}), {"trend": "up", "volume": "normal", "volatility": "calm", "momentum": "flat"}, "x")
    assert chk["memory_runs"] == 0 and chk["warnings"][0]["level"] == "info"


def test_meko_bad_token_falls_back_to_sqlite(monkeypatch, df):
    monkeypatch.setattr(config, "MEKO_TOKEN", "mko_tkn_FAKE")      # real server rejects it (network); offline also fails: both must fall back
    p, e = rules.validate("breakout", {}, {})
    out = memory.record("backtest", "RELIANCE.NS", "breakout", p, e, 15, backtest.run(df, "breakout", p, e))
    assert out["meko_mirror"] == "queued" and out["stored_in"] == "sqlite"
    meko.join(60)
    assert memory.status()["runs"] == 1 and meko.status()["last_error"] and meko.status()["writes_ok"] == 0


# ---------- API ----------
def test_api_templates():
    j = client.get("/strategies/templates").json()
    assert set(j["templates"]) == {"breakout", "ma_cross", "rsi", "mean_reversion"} and j["cost_bps_default"] == 15


def test_api_backtest_and_memory():
    r = client.post("/backtest", json={"ticker": "TCS.NS", "template": "breakout", "params": {"n": 30}, "exits": {"hold_days": 15, "stop_pct": 6, "tp_pct": None}})
    assert r.status_code == 200, r.text
    j = r.json()
    full = j["result"]["full"]
    assert j["spec"]["params"] == {"n": 30} and j["spec"]["exits"]["tp_pct"] is None
    assert len(full["dates"]) == len(full["equity"]) == len(full["benchmark_equity"]) and full["trades"]
    assert {"total_return", "win_rate", "max_drawdown", "sharpe"} <= set(full["metrics"])
    assert j["memory"]["run_id"] and len(client.get("/memory/runs").json()) == 1
    assert client.get("/memory/lessons").json()["lessons"] is not None
    chk = client.post("/memory/check", json={"ticker": "TCS.NS", "template": "breakout", "params": {"n": 30}, "exits": {"hold_days": 15, "stop_pct": 6, "tp_pct": None}}).json()
    assert chk["memory_runs"] == 1


def test_api_refine_structure():
    j = client.post("/refine", json={"ticker": "INFY.NS", "template": "mean_reversion"}).json()
    assert j["search"]["trials"] > 50 and "(never used for tuning)" in j["search"]["locked"]
    assert j["verdict"]["flag"] in ("ok", "overfit", "inconclusive", "no_change") and j["verdict"]["headline"]
    for side in ("original", "refined"):
        assert {"train", "locked", "full"} <= set(j[side]["result"])
    assert j["memory"]["run_id"]


def test_api_errors():
    assert client.post("/backtest", json={"template": "nope"}).status_code == 422
    assert client.post("/backtest", json={"ticker": "bad ticker"}).status_code == 422
    assert client.post("/backtest", json={"template": "breakout", "params": {"n": 2}}).status_code == 422
    assert client.post("/backtest", json={"cost_bps": -1}).status_code == 422


def test_api_remember_false_stores_nothing():
    client.post("/backtest", json={"ticker": "TCS.NS", "template": "rsi", "remember": False})
    assert client.get("/memory/status").json()["runs"] == 0


def test_candidate_counts_are_consistent_everywhere(df):
    """The verdict bullet and the search block must quote the same true counts: eligible of tried."""
    r = refine.refine(df, "rsi", dict(period=14, level=29, mode="oversold"), dict(hold_days=10, stop_pct=7, tp_pct=13))
    s = r["search"]
    assert s["trials"] >= s["eligible"] > 0
    bullet = [n for n in r["verdict"]["notes"] if n.startswith("Best of")]
    assert len(bullet) == 1
    assert f"Best of {s['eligible']} eligible candidates ({s['trials']} combinations tried" in bullet[0]


def test_no_eligible_candidates_is_not_called_best_of_grid():
    """Audit finding: JIOFIN ma_cross 10/50 has no combination with >= 10 training trades; Rex used to say the original was 'already the best'."""
    v = refine._verdict({"sharpe": 0}, {"sharpe": 0, "total_return": 0}, {"sharpe": 0}, {"sharpe": 0, "total_return": 0, "trades": 0}, True, 400, 0)
    assert v["flag"] == "inconclusive" and "already the best" not in v["headline"] and "Not enough trades" in v["headline"]
    df, _ = data.load("JIOFIN.NS")
    r = refine.refine(df, "ma_cross", dict(fast=10, slow=50), dict(hold_days=10, stop_pct=5.0, tp_pct=10.0))
    if r["search"]["eligible"] == 0:
        assert r["verdict"]["flag"] == "inconclusive" and r["refined"]["changed"] is False
