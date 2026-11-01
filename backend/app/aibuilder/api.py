"""HTTP routes of the AI Strategy Builder, all under /ai. Errors are structured: {"detail": {"error": {code, message, detail, retryable}}}."""
from __future__ import annotations
import difflib, logging, time
from contextlib import closing
from fastapi import APIRouter, HTTPException
from .. import crypto_data as cd, config
from . import agent, gemini, sandbox, service, store
from .models import AIError, ChatReq, RunOut, RunReq, StepOut

log = logging.getLogger("rex.ai.api")
router = APIRouter(prefix="/ai", tags=["ai"])
EXAMPLES = [dict(text="Find a BTC strategy on 1h candles that won't overfit", market="crypto"),
            dict(text="Find an ETH strategy on 4h candles that beats buy and hold after costs", market="crypto"),
            dict(text="Find a SOL strategy on 1h candles that survives double the trading costs", market="crypto"),
            dict(text="Find me a strategy for RELIANCE that won't overfit", market="nse")]


def _http(e: AIError) -> HTTPException:
    return HTTPException(e.http, detail=dict(error=e.dump()))


def _nf(what: str) -> HTTPException:
    return HTTPException(404, detail=dict(error=dict(code="not_found", message=f"{what} not found", detail=None, retryable=False)))


@router.get("/status")
def status():
    return dict(gemini=gemini.stats(), sandbox=sandbox.isolation(), examples=EXAMPLES,
                symbols=[dict(symbol=k, name=v.get("name", k)) for k, v in cd.SYMBOLS.items()], nse=config.DEFAULT_TICKERS, intervals=["1h", "4h", "1d"],
                mode="gemini" if gemini.enabled() else "offline", round_options=[1, 2, 3, 4])


# ---------------------------------------------------------------- chat builder
@router.post("/chat")
def chat(req: ChatReq):
    try:
        return service.chat_turn(req.message, req.session_id, req.parent_version_id, req.target.model_dump() if req.target else None)
    except AIError as e:
        raise _http(e)


@router.get("/sessions")
def sessions():
    return dict(sessions=store.list_sessions())


@router.get("/sessions/{sid}")
def session(sid: str):
    s = store.get_session(sid)
    if not s:
        raise _nf("Session")
    return dict(session=s, messages=store.messages(sid), versions=store.versions(sid))


@router.get("/versions/{vid}")
def version(vid: str):
    v = store.get_version(vid)
    if not v:
        raise _nf("Version")
    return v


@router.get("/versions/{a}/diff/{b}")
def diff(a: str, b: str):
    va, vb = store.get_version(a), store.get_version(b)
    if not va or not vb:
        raise _nf("Version")
    d = "".join(difflib.unified_diff(va["code"].splitlines(True), vb["code"].splitlines(True), f"v{va['n']}", f"v{vb['n']}"))

    def m(v):
        r = v["result"]
        return dict(id=v["id"], n=v["n"], title=v["title"], train=r["train"]["metrics"], locked=r["locked"]["metrics"], benchmark_locked=r["locked"]["benchmark"], verdict=v["verdict"]["label"], exits=v["exits"])
    return dict(diff=d, a=m(va), b=m(vb))


# ---------------------------------------------------------------- research agent
def run_view(rid: str) -> dict:
    r = store.get_run(rid)
    if not r:
        raise _nf("Run")
    st = r["status"]
    if st == "running" and not agent.is_alive(rid) and time.time() - r["updated"] > 15:
        st = "interrupted"
    steps, exps, debate, risk, notices = [], [], None, None, []
    for s in store.steps(rid, with_output=True):
        out = s.get("output") or {}
        if s["tool"] == "backtest" and out.get("experiment"):
            exps.append(out["experiment"])
        if s["tool"] == "write_strategy" and s["status"] == "error":
            inp = s.get("inputs") or {}
            exps.append(agent._fail_row(int(inp.get("round") or 0), dict(name=inp.get("hypothesis") or "hypothesis", brief=""), (s.get("error") or {}).get("message", "code could not be produced")))
        if s["tool"] == "debate" and out:
            debate = out
        if s["tool"] == "risk_check" and out:
            risk = out
        if out.get("notice"):
            notices.append(out["notice"])
        if s["tool"] == "plan":
            out = {k: v for k, v in out.items() if k not in ("candidates", "trial_sharpes")}
        elif s["tool"] in ("decide", "risk_check", "debate"):
            out = {}
        steps.append(StepOut(idx=s["idx"], role=agent.ROLE.get(s["tool"], s["tool"]), tool=s["tool"], status=s["status"], summary=s.get("summary"), inputs=s.get("inputs"), output=out,
                             error=s.get("error"), started=s.get("started"), ended=s.get("ended")))
    p = r["params"]
    if (r.get("final") or {}).get("capped"):
        notices.append(f"Step cap reached ({p.get('max_steps', agent.MAX_STEPS)} steps): exploration stopped early and the best experiment so far was reviewed.")
    return RunOut(id=rid, goal=r["goal"], status=st, mode=r["mode"], session_id=r["session_id"], target=p.get("target", {}), created=r["ts"], updated=r["updated"], steps=steps,
                  experiments=exps, debate=debate, risk=risk, final=r.get("final"), error=r.get("error"), max_steps=int(p.get("max_steps", agent.MAX_STEPS)), notices=notices).model_dump()


@router.post("/runs")
def start_run(req: RunReq):
    try:
        tgt = req.target.model_dump() if req.target else service.parse_goal(req.goal)
        service.get_setup(tgt)                          # fail fast (structured error) when the data cannot be loaded
        rid = agent.create(req.goal, tgt, req.rounds, req.offline, preferences=dict(style=req.style, max_drawdown_pct=req.max_drawdown_pct))
        agent.start(rid)
        return run_view(rid)
    except AIError as e:
        raise _http(e)


@router.get("/runs")
def runs():
    return dict(runs=store.list_runs())


@router.get("/runs/{rid}")
def get_run(rid: str):
    return run_view(rid)


@router.post("/runs/{rid}/resume")
def resume(rid: str):
    r = store.get_run(rid)
    if not r:
        raise _nf("Run")
    if r["status"] != "done":
        agent.start(rid)
    return run_view(rid)


# ---------------------------------------------------------------- "track this live" (paper signal watch of AI-written code)
@router.post("/versions/{vid}/track")
def track(vid: str):
    v = store.get_version(vid)
    if not v:
        raise _nf("Version")
    try:
        now = time.time()
        wid = store.create_run(f"watch {v['symbol']} {v['title']}", dict(version_id=vid, started_at=now), "gemini", v["session_id"], kind="watch")
        store.update_run(wid, status="running")
        live = service.live_signal(v, now)
        store.update_run(wid, final=dict(live=live))
        return dict(watch_id=wid, status="running", live=live)
    except AIError as e:
        raise _http(e)


@router.get("/watches")
def watches(version_id: str | None = None):
    with closing(store._db()) as con:
        rows = con.execute("SELECT id FROM ai_runs WHERE kind='watch' ORDER BY ts DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        w = store.get_run(r["id"])
        if version_id is None or w["params"]["version_id"] == version_id:
            out.append(dict(id=r["id"], status=w["status"], version_id=w["params"]["version_id"]))
    return dict(watches=out)


@router.get("/watches/{wid}")
def watch(wid: str):
    w = store.get_run(wid)
    if not w or w["kind"] != "watch":
        raise _nf("Watch")
    v = store.get_version(w["params"]["version_id"])
    try:
        if w["status"] == "stopped" and w.get("final"):
            live = w["final"]["live"]
        else:
            live = service.live_signal(v, w["params"]["started_at"])
            store.update_run(wid, final=dict(live=live))
        return dict(watch_id=wid, status=w["status"], live=live)
    except AIError as e:
        raise _http(e)


@router.post("/watches/{wid}/stop")
def stop_watch(wid: str):
    """Stop a paper deployment. The watch row is kept (so its history stays readable) and is marked stopped; nothing was ever ordered."""
    w = store.get_run(wid)
    if not w or w["kind"] != "watch":
        raise _nf("Watch")
    if w["status"] != "stopped":
        store.update_run(wid, status="stopped")
    return dict(watch_id=wid, status="stopped")
