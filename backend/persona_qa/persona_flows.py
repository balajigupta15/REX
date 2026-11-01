"""Persona journeys A (beginner), B (swing trader), C (overfitting optimizer) through the REAL HTTP API, then an as-of out-of-sample
measurement of each persona's own strategy. Real cached NSE prices only. Usage: python persona_flows.py http://127.0.0.1:8002"""
import json, os, sys
os.environ.setdefault("REX_OFFLINE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from app import config, data
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oos_refine_study import fwd, CUTS
from app import refine as refine_mod

HERE = os.path.dirname(os.path.abspath(__file__))
api = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002"
cl = httpx.Client(base_url=api, timeout=180)
LOG = {}


def pct(x): return None if x is None else round(x * 100, 2)
def met(res, w): m = res[w]["metrics"]; return dict(trades=m["trades"], ret=pct(m["total_return"]), mdd=pct(m["max_drawdown"]), win=pct(m["win_rate"]), sharpe=round(m["sharpe"], 2) if m["sharpe"] is not None else None)


def journey(name, who, texts, fallback_text=None, entry_date="2026-10-23", side="buy", qty=10, add_journal=False, follow_rex=True, similar_tweak=None, pick=None):
    L = LOG[name] = dict(persona=who, steps=[])
    def step(s, **kw): L["steps"].append(dict(step=s, **kw)); return kw
    spec = None
    for t in texts + ([fallback_text] if fallback_text else []):
        p = cl.post("/strategies/parse", json=dict(text=t)).json()
        step("parse", text=t, strategy=p["strategy"], confidence=p["confidence"], assumptions=p["assumptions"], unsupported=p["unsupported"])
        if p["strategy"]: spec = p["strategy"]; break
    assert spec, "no parseable strategy"
    if pick: spec = pick(spec)
    chk = cl.post("/memory/check", json=spec).json()
    step("pre_run_memory", warnings=[w if isinstance(w, str) else w.get("text", str(w)) for w in (chk.get("warnings") or chk.get("insights") or [])][:4], keys=list(chk.keys())[:8])
    lt0 = cl.get("/memory/strategy-runs", params=dict(ticker=spec["ticker"], template=spec["template"], limit=4)).json()
    step("last_time_before_first_run", headline=(lt0["last_time"] or {}).get("headline"), lesson=(lt0["last_time"] or {}).get("lesson"), replayed=(lt0["last_time"] or {}).get("replayed"), total=lt0["total"])
    bt = cl.post("/backtest", json=spec).json()
    r = bt["result"]
    step("backtest", spec=bt["spec"], train=met(r, "train"), locked=met(r, "locked"), bh_locked=pct(r["locked"]["benchmark"]["total_return"]), bh_train=pct(r["train"]["benchmark"]["total_return"]))
    rf = cl.post("/refine", json=spec).json()
    v = rf["verdict"]
    step("refine", flag=v["flag"], headline=v["headline"], refined_params=rf["refined"]["params"], refined_exits=rf["refined"]["exits"], changed=rf["refined"]["changed"],
         original=dict(train=met(rf["original"]["result"], "train"), locked=met(rf["original"]["result"], "locked")), refined=dict(train=met(rf["refined"]["result"], "train"), locked=met(rf["refined"]["result"], "locked")),
         trials=rf["search"]["trials"], notes=v["notes"])
    use_refined = (v["flag"] == "ok" and rf["refined"]["changed"]) if follow_rex else rf["refined"]["changed"]
    chosen = dict(spec, params=rf["refined"]["params"], exits=rf["refined"]["exits"]) if use_refined else spec
    step("persona_decision", follow_rex=follow_rex, uses="refined rules" if use_refined else "original rules", params=chosen["params"], exits=chosen["exits"])
    df, _ = data.load(spec["ticker"])
    entry = float(df.loc[entry_date, "Open"]) if entry_date in df.index.strftime("%Y-%m-%d") else float(df.Close.iloc[-5])
    sv = cl.post("/paper/start", json=dict(ticker=chosen["ticker"], template=chosen["template"], params=chosen["params"], exits=chosen["exits"], cost_bps=chosen.get("cost_bps") or 15.0,
                                           label="refined" if use_refined else "original",
                                           trade=dict(side=side, entry_price=round(entry, 2), qty=qty, entered_at=entry_date))).json()
    sid = sv["id"]
    v2 = cl.get(f"/paper/{sid}", params=dict(refresh=1)).json()
    pos = v2["position"] or {}
    step("logged_trade_tracking", session=sid, mode=v2["mode"], entry=round(entry, 2), qty=qty, live_price=v2["quote"].get("price"), market=v2["market"].get("label") if isinstance(v2["market"], dict) else v2["market"],
         net_pct=pos.get("net_pct"), pnl_abs=pos.get("pnl_abs"), dist_to_stop_pct=pos.get("dist_to_stop_pct"), dist_to_tp_pct=pos.get("dist_to_tp_pct"),
         exit_alert=(v2["exit_signal"] or {}).get("fired"), exit_text=(v2["exit_signal"] or {}).get("text"))
    paper = cl.post("/paper/start", json=dict(ticker=chosen["ticker"], template=chosen["template"], params=chosen["params"], exits=chosen["exits"], label="original")).json()
    pv = cl.get(f"/paper/{paper['id']}", params=dict(refresh=1)).json()
    step("paper_tracking_no_trade", session=paper["id"], phase=pv["phase"], watching=(pv.get("entry_watch") or {}).get("text"), position=pv["position"] is not None)
    cl.post(f"/paper/{paper['id']}/stop", json=dict(note="stopped paper follow"))
    cp = cl.post(f"/paper/{sid}/stop", json=dict(note=f"{who}: closed in my broker", add_to_journal=add_journal)).json()
    c = cp.get("close") or {}
    step("closed", status=cp["status"], verdict=c.get("verdict"), lesson=((c.get("saved") or {}).get("lesson")), net_pct=c.get("net_pct"), saved=c.get("saved"), close_keys=list(c.keys()))
    # a new, similar backtest: Rex shows 'Last time'
    sim = dict(chosen)
    if similar_tweak: sim = similar_tweak(sim)
    lt = cl.get("/memory/strategy-runs", params=dict(ticker=sim["ticker"], template=sim["template"], limit=4)).json()
    step("last_time_before_similar_backtest", headline=(lt["last_time"] or {}).get("headline"), lesson=(lt["last_time"] or {}).get("lesson"), match=(lt["last_time"] or {}).get("match"),
         kind=(lt["last_time"] or {}).get("kind"), fwd5=(lt["last_time"] or {}).get("fwd5"), fwd20=(lt["last_time"] or {}).get("fwd20"), since_pct=(lt["last_time"] or {}).get("since_pct"))
    chk2 = cl.post("/memory/check", json=sim).json()
    bt2 = cl.post("/backtest", json=sim).json()
    step("similar_backtest", params=bt2["spec"]["params"], locked=met(bt2["result"], "locked"), train=met(bt2["result"], "train"))
    # out-of-sample (as-of) check on this persona's exact strategy
    oos = []
    for cut in CUTS:
        idx0 = int(df.index.searchsorted(cut, side="right"))
        hist = df.iloc[:idx0]
        if len(hist) < config.MIN_BARS: oos.append(dict(cut=cut, skipped="not enough history")); continue
        rr = refine_mod.refine(hist, spec["template"], spec["params"], spec["exits"], config.COST_BPS_DEFAULT)
        o = fwd(df, spec["template"], spec["params"], spec["exits"], idx0)
        f = o if not rr["refined"]["changed"] else fwd(df, spec["template"], rr["refined"]["params"], rr["refined"]["exits"], idx0)
        fol = f if rr["verdict"]["flag"] == "ok" else o
        oos.append(dict(cut=cut, flag=rr["verdict"]["flag"], changed=rr["refined"]["changed"], refined_params=rr["refined"]["params"], refined_exits=rr["refined"]["exits"],
                        original=dict(ret=pct(o["ret"]), mdd=pct(o["mdd"]), trades=o["trades"]), refined=dict(ret=pct(f["ret"]), mdd=pct(f["mdd"]), trades=f["trades"]),
                        follow_rex=dict(ret=pct(fol["ret"]), mdd=pct(fol["mdd"])), buy_hold=dict(ret=pct(o["bh"]), mdd=pct(o["bh_mdd"])), forward_days=len(df) - idx0))
    step("out_of_sample_as_of", results=oos)
    return L


journey("A_beginner", "Beginner Bina (vague idea, first backtest)",
        ["I want to buy Reliance when it is going up a lot and sell when I make some profit or if I am losing too much"],
        fallback_text="Buy Reliance when it breaks above the 20 day high, hold 10 days, 5% stop loss, 10% profit target",
        entry_date="2026-10-27", qty=10, add_journal=False, similar_tweak=lambda s: dict(s, params={"n": 25}))
journey("B_swing", "Swing trader Sameer (MA crossover, defined risk)",
        ["Buy TCS when the 20 day average crosses above the 50 day average, 4% stop loss, 8% take profit, hold at most 15 days"],
        entry_date="2026-10-23", qty=5, add_journal=False, similar_tweak=lambda s: dict(s, params={"fast": 15, "slow": 50}))
journey("C_optimizer", "Optimizer Omkar (tunes until the backtest looks great, ignores warnings)",
        ["Buy JIOFIN when RSI goes below 30, hold 8 days, 4% stop loss, 8% take profit"], entry_date="2026-10-23", qty=50, follow_rex=False, add_journal=False,
        similar_tweak=lambda s: dict(s, params=dict(s["params"], level=35.0)))
json.dump(LOG, open(os.path.join(HERE, "persona_flows_results.json"), "w"), indent=1, default=str)
for k, L in LOG.items():
    print("=====", k, L["persona"])
    for s in L["steps"]:
        d = {a: b for a, b in s.items() if a != "step"}
        print("-", s["step"], json.dumps(d, default=str)[:700])
