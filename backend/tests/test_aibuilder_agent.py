"""AI Strategy Builder: Gemini client, offline synthesizer, research-agent loop (RD-Agent rounds + TradingAgents roles), persistence, API."""
import os, time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.aibuilder import agent, api, gemini, service, store, synth, ast_guard, sandbox
from app.aibuilder.models import AIError

BTC = dict(market="crypto", symbol="BTCUSDT", interval="1h", cost_bps=None)
GOAL = "Find a BTC strategy on 1h candles that won't overfit"
CODE_A = """def signals(df):
    hi = df['high'].rolling(240).max().shift(1)
    lo = df['low'].rolling(120).min().shift(1)
    pos = np.where(df['close'] > hi, 1.0, np.where(df['close'] < lo, 0.0, np.nan))
    return pd.Series(pos, index=df.index).ffill().fillna(0)
"""
CODE_B = """def signals(df):
    f = df['close'].ewm(span=96, adjust=False).mean()
    s = df['close'].ewm(span=480, adjust=False).mean()
    return (f > s).astype(int)
"""


@pytest.fixture
def app_client():
    a = FastAPI()
    a.include_router(api.router)
    return TestClient(a)


class FakeGemini:
    """Stands in for gemini.generate_json: plan -> 2 ideas, write -> scripted code, debate -> bull/bear."""
    def __init__(self, codes=(CODE_A, CODE_B), fail_writes=0, bad_first=False):
        self.codes, self.calls, self.fail_writes, self.bad_first, self.n_write = list(codes), [], fail_writes, bad_first, 0

    def __call__(self, system, user, tag="x", temperature=0.3, use_cache=True):
        self.calls.append(tag)
        meta = dict(model="fake-gemini", cached=False)
        if tag == "plan":
            return dict(thesis="Trend persistence is plausible; main risk is overfitting window lengths.",
                        ideas=[dict(name="Channel breakout", brief="Buy a 240-bar high break, exit under the 120-bar low."),
                               dict(name="EMA trend", brief="Long when the 96 EMA is above the 480 EMA.")]), meta
        if tag == "write":
            self.n_write += 1
            if self.n_write <= self.fail_writes:
                raise gemini.GeminiUnavailable("unavailable", "fake outage")
            code = self.codes[min((self.n_write - 1) // (2 if self.bad_first else 1), len(self.codes) - 1)] if not self.bad_first or self.n_write % 2 == 0 else "import os\ndef signals(df):\n    return df['close']*0\n"
            return dict(title=f"Fake strategy {self.n_write}", explanation="Scripted.", rules=["rule one", "rule two", "rule three"], exits=dict(stop_pct=3, tp_pct=None, hold_bars=None), code=code), meta
        if tag == "debate":
            return dict(bull=["Held up on the locked period."], bear=["Few trades; selection bias."]), meta
        raise AssertionError(tag)


@pytest.fixture
def fake(monkeypatch):
    f = FakeGemini()
    monkeypatch.setattr(gemini, "enabled", lambda: True)
    monkeypatch.setattr(gemini, "generate_json", f)
    return f


def _run(goal=GOAL, rounds=2, offline=False, max_steps=agent.MAX_STEPS, target=BTC):
    rid = agent.create(goal, target, rounds, offline, max_steps)
    agent.run(rid)
    return rid


# ------------------------------------------------------------------ synthesizer / offline mode
def test_synth_candidates_are_valid_and_lookahead_free():
    s = service.get_setup(BTC)
    cands = synth.build_candidates("1h", True)
    assert len(cands) >= 50
    for c in cands:
        ast_guard.validate(c["code"])
    out = sandbox.run_signals(cands[0]["code"], s["df"])
    assert out["lookahead_free"]


def test_offline_mode_runs_full_loop_for_crypto_1h():
    rid = _run(offline=True, rounds=3)
    v = api.run_view(rid)
    assert v["status"] == "done" and v["mode"] == "offline"
    tools = [s["tool"] for s in v["steps"]]
    assert tools[:4] == ["get_data", "get_news", "recall_memory", "plan"]
    assert tools.count("write_strategy") == 3 and tools.count("backtest") == 3
    assert tools[-3:] == ["debate", "risk_check", "decide"]
    assert len(v["experiments"]) == 3 and {e["status"] for e in v["experiments"]} <= {"kept", "dropped"}
    assert any(e["status"] == "kept" for e in v["experiments"])
    f = v["final"]
    assert "not investment advice" in f["disclaimer"].lower() and f["decision"] in ("DO NOT TRADE", "WEAK EVIDENCE", "EVIDENCE HELD UP", "NO RESULT")
    assert f["metrics"]["locked"] and f["chart"]["equity"] and f["debate"]["bull"] and f["debate"]["bear"] and f["risk"]["verdict"] in ("VETO", "CAUTION", "APPROVE")
    assert v["notices"] and "OFFLINE MODE" in v["notices"][0]
    assert v["final"]["source"] == "offline_synth" and v["final"]["mode"] == "offline"


def test_offline_mode_nse_example():
    rid = _run("Find me a strategy for RELIANCE that won't overfit", rounds=2, offline=True, target=service.parse_goal("RELIANCE"))
    v = api.run_view(rid)
    assert v["status"] == "done" and v["target"]["market"] == "nse" and v["final"]["decision"]


def test_parse_goal_is_crypto_first():
    assert service.parse_goal(GOAL) == BTC
    assert service.parse_goal("ETH on 4h candles")["interval"] == "4h"
    assert service.parse_goal("something vague")["symbol"] == "BTCUSDT"
    assert service.parse_goal("Find me a strategy for RELIANCE")["market"] == "nse"


# ------------------------------------------------------------------ agent with a mocked LLM
def test_full_loop_with_mocked_gemini(fake):
    rid = _run(rounds=2)
    v = api.run_view(rid)
    assert v["status"] == "done" and v["mode"] == "gemini"
    assert fake.calls.count("plan") == 1 and fake.calls.count("write") == 2 and fake.calls.count("debate") == 1     # few LLM calls: 1 + rounds + 1
    assert [e["hypothesis"] for e in v["experiments"]][0] == "Channel breakout"
    assert v["debate"]["bull"] == ["Held up on the locked period."]
    assert v["final"]["source"] == "gemini" and store.get_version(v["final"]["version_id"])["code"].startswith("def signals")
    assert v["final"]["model"] == "fake-gemini"
    # lesson written back to memory
    assert any("BTCUSDT" in l["text"] and l["kind"] == "ai_agent" for l in _lessons())


def _lessons():
    from app import db
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM lessons")]
    finally:
        con.close()


def test_bad_code_is_sent_back_once_and_recovers(monkeypatch):
    f = FakeGemini(bad_first=True)
    monkeypatch.setattr(gemini, "enabled", lambda: True); monkeypatch.setattr(gemini, "generate_json", f)
    rid = _run(rounds=1)
    v = api.run_view(rid)
    assert v["status"] == "done" and f.calls.count("write") == 2            # rejected `import os`, then the retry passed
    assert v["experiments"][0]["status"] in ("kept", "dropped")
    ws = [s for s in v["steps"] if s["tool"] == "write_strategy"][0]
    assert ws["status"] == "ok" and "2 attempts" in (ws["summary"] or "")


def test_gemini_outage_switches_to_offline_synth_and_says_so(monkeypatch):
    f = FakeGemini(fail_writes=99)
    monkeypatch.setattr(gemini, "enabled", lambda: True); monkeypatch.setattr(gemini, "generate_json", f)
    rid = _run(rounds=2)
    v = api.run_view(rid)
    assert v["status"] == "done" and len(v["experiments"]) == 2
    assert any("offline" in n.lower() for n in v["notices"])
    assert v["final"]["source"] == "offline_synth"


def test_tool_error_recovery_run_continues(monkeypatch, fake):
    def boom(setup):
        raise RuntimeError("news provider exploded")
    monkeypatch.setattr(agent, "tool_news", boom)
    rid = _run(rounds=1)
    v = api.run_view(rid)
    news = [s for s in v["steps"] if s["tool"] == "get_news"][0]
    assert news["status"] == "error" and news["error"]["message"]
    assert v["status"] == "done" and v["final"]["decision"]


def test_strategy_that_never_passes_gives_no_result_not_a_crash(monkeypatch):
    f = FakeGemini(codes=("import os\ndef signals(df):\n    return df['close']*0\n",))
    monkeypatch.setattr(gemini, "enabled", lambda: True); monkeypatch.setattr(gemini, "generate_json", f)
    monkeypatch.setattr(synth, "search", lambda *a, **k: dict(ranked=[], tried=0, failed=0, usable=0, trial_sharpes=[]))
    rid = _run(rounds=1)
    v = api.run_view(rid)
    assert v["status"] == "done" and v["final"]["decision"] == "NO RESULT"
    assert v["experiments"][0]["verdict"] == "FAILED" and v["experiments"][0]["status"] == "dropped"


def test_step_cap_is_enforced(fake):
    rid = _run(rounds=4, max_steps=9)
    v = api.run_view(rid)
    assert v["status"] == "done" and len(v["steps"]) <= 9
    assert v["final"]["decision"] and any("cap" in (n or "").lower() or "step" in (n or "").lower() for n in v["notices"] + [v["final"].get("headline", "")])


def test_resume_replays_finished_steps_without_new_llm_calls(fake):
    rid = _run(rounds=2)
    n = len(fake.calls)
    store.update_run(rid, status="interrupted")
    agent.run(rid)
    assert len(fake.calls) == n and store.get_run(rid)["status"] == "done"


def test_claim_run_is_exclusive():
    rid = store.create_run("g", dict(target=BTC), "offline", None)
    assert store.claim_run(rid, "a") is True
    assert store.claim_run(rid, "b") is False
    assert store.claim_run(rid, "b", stale_s=0) is True


def test_risk_manager_can_veto():
    s = service.get_setup(BTC)
    ev = service.evaluate(s, CODE_B, dict(stop_pct=None), 1, [])
    v = dict(result=ev["result"], verdict=dict(ev["verdict"], flag="overfit"), stats=ev["stats"], exits=ev["exits"])
    r = service.risk_review(v, s, 50, [0.0] * 50)
    assert r["verdict"] == "VETO" and r["vetoes"]


# ------------------------------------------------------------------ gemini client
def test_gemini_never_leaks_key_and_reports_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gemini.enabled() is False
    with pytest.raises(gemini.GeminiUnavailable) as e:
        gemini.generate_json("s", "u", tag="t")
    assert e.value.reason == "no_key"
    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-TEST-KEY-123")
    assert "SECRET-TEST-KEY-123" not in repr(gemini.stats())


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="no GEMINI_API_KEY")
def test_live_gemini_smoke():
    gemini.reset_cooldown()
    try:
        obj, meta = gemini.generate_json("Return ONLY JSON.", 'Return {"ok": true}', tag="smoke", use_cache=False)
    except gemini.GeminiUnavailable as e:
        if e.reason in ("quota", "unavailable", "timeout"):
            pytest.skip(f"Gemini temporarily unavailable ({e.reason})")
        raise
    assert obj.get("ok") is True and meta["model"]


# ------------------------------------------------------------------ chat builder + versions
def test_chat_builder_offline_versions_diff_and_compare(app_client, monkeypatch):
    monkeypatch.setattr(gemini, "enabled", lambda: False)
    r = app_client.post("/ai/chat", json=dict(message="Build a BTC trend strategy", target=BTC))
    assert r.status_code == 200, r.text
    d = r.json(); sid, v1 = d["session_id"], d["version"]
    assert v1["code"].startswith("def signals") and v1["result"]["locked"]["metrics"]
    r2 = app_client.post("/ai/chat", json=dict(message="use a 5% stop loss", session_id=sid, parent_version_id=v1["id"]))
    assert r2.status_code == 200, r2.text
    v2 = r2.json()["version"]
    assert v2["n"] == 2 and v2["exits"]["stop_pct"] == 5.0
    cmp = app_client.get(f"/ai/versions/{v1['id']}/diff/{v2['id']}").json()
    assert "a" in cmp and "b" in cmp and cmp["b"]["exits"]["stop_pct"] == 5.0
    sess = app_client.get(f"/ai/sessions/{sid}").json()
    assert len(sess["versions"]) == 2 and len(sess["messages"]) >= 4


def test_chat_builder_with_mocked_gemini(app_client, fake):
    r = app_client.post("/ai/chat", json=dict(message="Breakout with a trend filter", target=BTC))
    assert r.status_code == 200, r.text
    assert r.json()["version"]["source"] == "gemini"


# ------------------------------------------------------------------ HTTP API
def test_api_status_and_errors(app_client):
    s = app_client.get("/ai/status").json()
    assert s["examples"][0]["market"] == "crypto" and "BTC" in s["examples"][0]["text"]
    assert any(e["market"] == "nse" for e in s["examples"][1:])
    r = app_client.get("/ai/runs/nope")
    assert r.status_code == 404 and r.json()["detail"]["error"]["code"] == "not_found"
    assert app_client.post("/ai/runs", json=dict(goal="x")).status_code == 422
    assert app_client.get("/ai/versions/zzz").status_code == 404


def test_api_run_is_pollable_and_survives_refresh(app_client):
    r = app_client.post("/ai/runs", json=dict(goal=GOAL, rounds=2, offline=True))
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    seen = set()
    for _ in range(300):
        v = app_client.get(f"/ai/runs/{rid}").json()
        seen.add(len(v["steps"]))
        if v["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert v["status"] == "done" and v["final"]["decision"]
    assert len(seen) > 1 or max(seen) > 5
    again = app_client.get(f"/ai/runs/{rid}").json()                      # "refresh by run id"
    assert again["final"]["decision"] == v["final"]["decision"] and len(again["steps"]) == len(v["steps"])
    assert any(x["id"] == rid for x in app_client.get("/ai/runs").json()["runs"])
    assert app_client.post(f"/ai/runs/{rid}/resume").json()["status"] == "done"


def test_api_stale_running_run_reports_interrupted(app_client):
    rid = agent.create(GOAL, BTC, 1, True)
    store.update_run(rid, status="running")
    from contextlib import closing
    with closing(store._db()) as con:
        con.execute("UPDATE ai_runs SET updated=? WHERE id=?", (time.time() - 60, rid)); con.commit()
    assert app_client.get(f"/ai/runs/{rid}").json()["status"] == "interrupted"


def test_api_track_live_paper_watch(app_client, monkeypatch):
    monkeypatch.setattr(gemini, "enabled", lambda: False)
    d = app_client.post("/ai/chat", json=dict(message="BTC trend", target=BTC)).json()
    t = app_client.post(f"/ai/versions/{d['version']['id']}/track")
    assert t.status_code == 200, t.text
    w = t.json()
    assert w["live"]["symbol"] == "BTCUSDT" or "signal" in w["live"] or w["live"]
    again = app_client.get(f"/ai/watches/{w['watch_id']}")
    assert again.status_code == 200


def test_api_watch_status_and_stop(app_client, monkeypatch):
    monkeypatch.setattr(gemini, "enabled", lambda: False)
    d = app_client.post("/ai/chat", json=dict(message="BTC trend", target=BTC)).json()
    wid = app_client.post(f"/ai/versions/{d['version']['id']}/track").json()["watch_id"]
    assert app_client.get(f"/ai/watches/{wid}").json()["status"] == "running"
    assert app_client.post(f"/ai/watches/{wid}/stop").json() == dict(watch_id=wid, status="stopped")
    assert app_client.post(f"/ai/watches/{wid}/stop").status_code == 200            # idempotent
    assert app_client.get(f"/ai/watches/{wid}").json()["status"] == "stopped"       # still readable after stopping
    assert app_client.post("/ai/watches/nope/stop").status_code == 404
    rid = agent.create(GOAL, BTC, 1, True)
    assert app_client.post(f"/ai/watches/{rid}/stop").status_code == 404            # only watches can be stopped, never research runs


def test_concurrent_runs_do_not_collide(app_client):
    ids = [app_client.post("/ai/runs", json=dict(goal=g, rounds=1, offline=True)).json()["id"] for g in (GOAL, "Find an ETH strategy on 1h candles")]
    for rid in ids:
        for _ in range(300):
            if app_client.get(f"/ai/runs/{rid}").json()["status"] in ("done", "error"):
                break
            time.sleep(0.2)
    vs = [app_client.get(f"/ai/runs/{rid}").json() for rid in ids]
    assert [v["status"] for v in vs] == ["done", "done"] and vs[0]["target"]["symbol"] == "BTCUSDT" and vs[1]["target"]["symbol"] == "ETHUSDT"


def test_meko_is_recalled_first_and_lesson_written_when_token_set(monkeypatch):
    from app import meko
    calls = dict(search=[], add=[])
    monkeypatch.setattr(meko, "enabled", lambda: True)
    monkeypatch.setattr(meko, "search", lambda q, limit=10: calls["search"].append(q) or [dict(text="BTC 1h breakouts failed the locked period before", score=0.9, metadata={})])
    monkeypatch.setattr(meko, "add_many", lambda items: calls["add"].append(items) or True)
    run = api.run_view(_run(offline=True))
    steps = {s["tool"]: s for s in run["steps"]}
    assert steps["recall_memory"]["output"]["source"] == "Meko" and "Meko" in steps["recall_memory"]["summary"]
    assert calls["search"] and "BTCUSDT" in calls["search"][0]
    f = run["final"]
    assert f["memory_written"] == ["SQLite", "Meko"] and any(it[0] == "ai_agent_lesson" and it[1] == f["lesson"] for c in calls["add"] for it in c)


def test_meko_failure_falls_back_to_sqlite(monkeypatch):
    from app import meko
    monkeypatch.setattr(meko, "enabled", lambda: True)
    monkeypatch.setattr(meko, "search", lambda q, limit=10: [])
    monkeypatch.setattr(meko, "add_many", lambda items: False)
    run = api.run_view(_run(offline=True))
    assert run["status"] == "done" and run["final"]["memory_written"] == ["SQLite"] and run["final"]["meko_status"] == "Meko failed"
    rec = [s for s in run["steps"] if s["tool"] == "recall_memory"][0]
    assert rec["output"]["source"] == "SQLite"
