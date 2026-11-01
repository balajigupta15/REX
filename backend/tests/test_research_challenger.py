import os
os.environ["REX_OFFLINE"] = "1"
import pytest
from fastapi.testclient import TestClient
from app import challenger, data, journal, research
from app.main import app

client = TestClient(app)


def test_offline_flag_zero_is_online(monkeypatch):
    monkeypatch.setenv("REX_OFFLINE", "0"); assert not data.is_offline()
    monkeypatch.setenv("REX_OFFLINE", "1"); assert data.is_offline()
    monkeypatch.delenv("REX_OFFLINE"); assert not data.is_offline()


def test_brief_shape_and_values():
    b = client.get("/research/INFY").json()
    assert b["ticker"] == "INFY.NS" and b["bars"] > 1000 and b["trend"]["label"] in ("uptrend", "downtrend", "mixed")
    assert len(b["biggest_moves"]["up"]) == 5 and b["biggest_moves"]["up"][0]["return_pct"] > 0 > b["biggest_moves"]["down"][0]["return_pct"]
    assert abs(b["biggest_moves"]["down"][0]["return_pct"]) <= 25
    assert b["volatility"]["max_drawdown_pct"] < 0 and 5 < b["volatility"]["annualised_pct"] < 80
    assert 100 < len(b["series"]) < 400 and b["summary"] and "forecast" in b["disclaimer"]
    for x in b["levels"]["support"]:
        assert x["level"] < b["close"] and x["touches"] >= 2
    for x in b["levels"]["resistance"]:
        assert x["level"] > b["close"]


def test_brief_earnings_available_and_unavailable_honest():
    e = client.get("/research/TCS.NS").json()["earnings"]
    assert e["available"] and e["summary"]["n"] >= 8 and all(abs(r["reaction_pct"]) < 30 for r in e["reactions"])
    j = client.get("/research/JIOFIN.NS").json()["earnings"]
    assert j["available"] is False and j["reactions"] == [] and "No earnings" in j["note"]


def test_brief_bad_ticker():
    assert client.get("/research/%24%24").status_code == 422


def test_scout_ranks_all_cached():
    s = client.get("/scout").json()
    assert s["universe"] == 10 and len(s["ranked"]) == 10 and not s["errors"]
    sc = [r["score"] for r in s["ranked"]]
    assert sc == sorted(sc, reverse=True) and all(0 <= x <= 100 for x in sc)
    assert [r["rank"] for r in s["ranked"]] == list(range(1, 11))
    for r in s["ranked"]:
        c = r["components"]
        assert abs(c["trend"] + c["momentum"] + c["volume"] - c["stretch_penalty"] - r["score"]) < 0.3 or r["score"] in (0, 100)
        assert r["reasons"] and r["label"] in ("strong", "constructive", "neutral", "weak")


def _row(ticker, pnl, tags=None, exit_date="2026-01-01", ret=None):
    return dict(ticker=ticker, pnl=pnl, ret=(ret if ret is not None else pnl / 1000), exit_date=exit_date, tags=tags or {}, is_sample=1, hold_days=5)


def _out(tags, action="proceed", sim=None, mp=None):
    return dict(fingerprint=dict(tags=tags), recommendation=dict(action=action), similar=sim, mistake_pattern=mp)


def test_challenger_no_objection():
    rows = [_row("INFY.NS", 100, exit_date=f"2026-01-{i+1:02d}") for i in range(10)]
    sim = dict(n=8, stats=dict(n=8, avg_loss_pct=-1), neighbors=[dict(return_pct=2)])
    c = challenger.build(_out({"trend_dma": "above_both"}, sim=sim, mp={}), rows, "INFY.NS")
    assert c["severity"] == "none" and not c["veto"] and c["final_action"] == "proceed" and c["arguments"] == []


def test_challenger_veto_overrides_proceed():
    rows = [_row("SBIN.NS", -500, {"volatility": "stressed", "extension": "stretched"}, f"2026-02-{i+1:02d}") for i in range(8)] + \
          [_row("TCS.NS", 300, exit_date="2026-03-01") for _ in range(4)]
    sim = dict(n=3, stats=dict(n=3, avg_loss_pct=-9), neighbors=[dict(return_pct=-9.5)])
    c = challenger.build(_out({"volatility": "stressed", "extension": "stretched"}, sim=sim, mp={}), rows, "SBIN.NS")
    assert c["veto"] and c["severity"] == "strong" and c["final_action"] == "wait" and c["overrides_recommendation"] and c["score"] >= challenger.VETO_SCORE
    assert any(a["weight"] == "strong" for a in c["arguments"])


def test_check_returns_challenger_and_keeps_recommendation():
    journal.import_sample()
    r = client.post("/trades/check", json=dict(symbol="INFY", date="2026-10-15", side="buy")).json()
    ch = r["challenger"]
    assert set(ch) >= {"veto", "severity", "arguments", "verdict", "final_action", "score", "rules"}
    assert r["recommendation"]["action"] in ("proceed", "reduce_size", "wait")
    assert ch["veto"] == (ch["score"] >= ch["veto_threshold"])
