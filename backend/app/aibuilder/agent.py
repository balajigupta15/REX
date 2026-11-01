"""Autonomous research agent. Two published agent patterns are re-implemented natively here (no code from either repo is used):
  * Microsoft RD-Agent (github.com/microsoft/RD-Agent): an R&D loop of  hypothesis -> code -> evaluate -> feedback -> next hypothesis, repeated for N rounds,
    with an experiment log of every hypothesis and whether it was kept or dropped.
  * TradingAgents (github.com/TauricResearch/TradingAgents): specialist roles: Data/Technical Analyst, News Analyst, Bull and Bear researchers who debate,
    a Risk Manager with a veto, and a Portfolio Manager who makes the final call.
LLM calls are few (plan 1, one per round to write code, debate 1). Every role has a deterministic offline fallback. The veto is never an LLM decision.
Honesty rule: experiments are kept/dropped on TRAIN-period evidence only; the locked final period is shown but never used to choose or to give feedback."""
from __future__ import annotations
import logging, os, threading, time
from contextlib import closing
import numpy as np
from .. import crypto_memory, memory as legacy_memory, meko, news, strategy_memory
from . import codegen, engine, gemini, service, store, synth
from .models import AIError

log = logging.getLogger("rex.ai.agent")
MAX_STEPS = 16                 # exploration steps (data, news, memory, plan, 2 per round); the 3 final steps (debate, risk, decide) are always allowed
FINAL_STEPS = 3
STEP_TIMEOUT = dict(write_strategy=170.0, backtest=90.0, plan=90.0, debate=70.0, get_news=25.0)
DEFAULT_TIMEOUT = 60.0
MIN_TRAIN_TRADES = 8
ROLE = dict(get_data="Data / Technical Analyst", get_news="News Analyst", recall_memory="Memory", plan="Research Lead", write_strategy="Researcher", backtest="Evaluator",
            debate="Bull and Bear Researchers", risk_check="Risk Manager", decide="Portfolio Manager")
DISCLAIMER = "This is evidence from a historical simulation with stated assumptions, not investment advice. Past results do not predict future returns."
_alive: dict[str, threading.Thread] = {}
_alive_lock = threading.Lock()


class StepCap(Exception):
    pass


class Ctx:
    def __init__(self, rid: str, max_steps: int):
        self.rid, self.n, self.used, self.budget, self.capped = rid, 0, 0, max(max_steps - FINAL_STEPS, 1), False

    def step(self, key: str, tool: str, inputs: dict, fn, final: bool = False):
        """Run one tool step with persistence, replay (on resume), a per-step timeout and structured errors. -> output dict, or None when it failed."""
        if not final:
            if self.used >= self.budget:
                raise StepCap()
            self.used += 1
        done = store.done_step(self.rid, key)
        if done:
            self.n = max(self.n, done["idx"] + 1)
            return done.get("output") or {}
        idx = self.n
        self.n += 1
        store.begin_step(self.rid, idx, key, tool, inputs)
        box: dict = {}

        def work():
            try:
                box["r"] = fn()
            except BaseException as e:      # noqa: BLE001 - recorded, never crashes the loop
                box["e"] = e
        th = threading.Thread(target=work, daemon=True, name=f"ai-step-{tool}")
        th.start()
        th.join(STEP_TIMEOUT.get(tool, DEFAULT_TIMEOUT))
        if th.is_alive():
            err = dict(code="step_timeout", message=f"{tool} took longer than {STEP_TIMEOUT.get(tool, DEFAULT_TIMEOUT):g}s and was abandoned.", retryable=True)
            store.end_step(self.rid, idx, "error", "timed out", None, err)
            return None
        if "e" in box:
            e = box["e"]
            if isinstance(e, AIError):
                err = e.dump()
            elif isinstance(e, gemini.GeminiUnavailable):
                err = dict(code=f"gemini_{e.reason}", message=str(e), retryable=e.reason in ("quota", "unavailable", "timeout"))
            else:
                log.exception("step %s failed", tool)
                err = dict(code="step_failed", message=f"{type(e).__name__}: {str(e)[:200]}", retryable=False)
            store.end_step(self.rid, idx, "error", err["message"][:200], None, err)
            return None
        out, summary = box["r"]
        store.end_step(self.rid, idx, "ok", summary, out, None)
        return out


# ------------------------------------------------------------------ tools
def tool_data(setup: dict):
    df, k = setup["df"], setup["k"]
    c = df["Close"]
    ma = c.rolling(200).mean().iloc[-1]
    ret = c.pct_change().dropna()
    vol = float(ret.std() * np.sqrt(setup["periods"]))
    dd = float((c / c.cummax() - 1).min())
    n30 = min(30 * (24 if setup["interval"] == "1h" else 6 if setup["interval"] == "4h" else 1), len(c) - 1)
    out = dict(symbol=setup["symbol"], interval=setup["interval"], bars=len(df), start=setup["fmt"](df.index[0]), end=setup["fmt"](df.index[-1]), last_close=round(float(c.iloc[-1]), 4),
               locked_from=setup["fmt"](df.index[k]), vs_200bar_average="above" if np.isfinite(ma) and c.iloc[-1] > ma else "below" if np.isfinite(ma) else "n/a",
               annual_volatility_pct=round(vol * 100, 1), buy_hold_max_drawdown_pct=round(dd * 100, 1), change_30d_pct=round(float(c.iloc[-1] / c.iloc[-1 - n30] - 1) * 100, 1),
               cost=setup["cost_label"], shorting=setup["allow_short"])
    return out, (f"{out['symbol']} {out['interval']}: {out['bars']} real candles {out['start']} to {out['end']}; last {out['last_close']:g}, {out['vs_200bar_average']} its 200-bar average, "
                 f"annual volatility {out['annual_volatility_pct']}%, buy & hold worst drawdown {out['buy_hold_max_drawdown_pct']}%. Locked period starts {out['locked_from']}.")


def tool_news(setup: dict):
    r = news.latest(setup["symbol"], limit=6)
    items = [dict(title=i["title"], publisher=i.get("publisher"), published=i.get("published"), url=i.get("url")) for i in r.get("items", [])]
    if r.get("disabled"):
        return dict(items=[], disabled=True), "News is switched off in this environment; skipped."
    if not items and r.get("errors"):
        raise AIError("news_unavailable", "News sources did not respond: " + "; ".join(r["errors"])[:160], retryable=True)
    return dict(items=items, sources=r.get("sources")), f"{len(items)} recent headlines (listed as published; not scored)."


def tool_memory(setup: dict):
    """Recall earlier runs. Source is Meko (memory_search over MCP) when MEKO_TOKEN is set and answers; the local SQLite strategy-run memory is always read too."""
    mod = crypto_memory if setup["market"] == "crypto" else strategy_memory
    q = mod.query(ticker=setup["symbol"], limit=5)
    runs = [dict(headline=r["headline"], lesson=r["lesson"], verdict=r["verdict"], template=r["template"]) for r in q["runs"]]
    mk, mk_err = [], None
    if meko.enabled():
        try:
            mk = [dict(text=(m.get("text") or "")[:300], score=m.get("score")) for m in meko.search(f"{setup['symbol']} {setup['interval']} strategy lesson", 5)][:5]
        except Exception as e:      # noqa: BLE001
            mk_err = str(e)[:120]
        if not mk and mk_err is None and meko._state.get("last_error"):
            mk_err = meko._state["last_error"][:120]
    src = "Meko" if mk else "SQLite"
    out = dict(source=src, meko=dict(enabled=meko.enabled(), hits=len(mk), error=mk_err), meko_memories=mk, total=q["total"], runs=runs)
    bits = [f"{q['total']} earlier run(s) on {setup['symbol']} in local memory." + (f" Latest: {runs[0]['headline']}" if runs else " Nothing recorded yet.")]
    if mk:
        bits.insert(0, f"Recalled {len(mk)} memories from Meko (memory_search). Top: {mk[0]['text'][:140]}")
    elif meko.enabled():
        bits.insert(0, "Meko had nothing relevant" + (f" ({mk_err})" if mk_err else "") + "; used local SQLite memory.")
    else:
        bits.insert(0, "Memory source: SQLite (no MEKO_TOKEN set).")
    return out, " ".join(bits)


def write_meko(lesson: str, setup: dict, title: str, decision: str, timeout: float = 25.0) -> str:
    """Best-effort memory_add of the run's lesson to Meko. Never raises. Returns 'Meko', 'Meko failed' or 'SQLite only'."""
    if not meko.enabled():
        return "SQLite only"
    box: dict = {}
    def go():
        try:
            box["ok"] = meko.add_many([("ai_agent_lesson", lesson, dict(symbol=setup["symbol"], interval=setup["interval"], market=setup["market"], title=title[:80], decision=decision))])
        except Exception as e:      # noqa: BLE001
            log.warning("meko write failed: %s", e)
    th = threading.Thread(target=go, daemon=True); th.start(); th.join(timeout)
    return "Meko" if box.get("ok") else "Meko failed"


def _offline_plan(setup: dict, rounds: int, reason: str) -> dict:
    s = synth.search(setup, top=max(rounds, 2) + 2)
    cands = [dict(title=c["title"], family=c["family"], code=c["code"], exits=c["exits"], rules=c["rules"], train=c["train"]) for c in s["ranked"]]
    return dict(source="offline", reason=reason, thesis=f"OFFLINE MODE ({reason}). Composed {s['tried']} new signals() programs from indicator building blocks and searched them on the train period only.",
                ideas=[dict(name=c["title"], brief=" ".join(c["rules"])) for c in cands], candidates=cands, tried=s["tried"], trial_sharpes=[round(x, 5) for x in s["trial_sharpes"]])


def tool_plan(ctx_state: dict, setup: dict, goal: str, rounds: int, mem_note: str, news_note: str, offline: bool):
    if not offline and gemini.enabled():
        try:
            p, meta = codegen.plan(setup, goal, mem_note, news_note)
            return dict(source="gemini", model=meta["model"], cached=meta["cached"], thesis=p["thesis"], ideas=p["ideas"], tried=0, trial_sharpes=[], candidates=[]), \
                f"Gemini proposed {len(p['ideas'])} hypotheses. Thesis: {p['thesis']}"
        except gemini.GeminiUnavailable as e:
            reason = f"Gemini unavailable: {e.reason}"
    else:
        reason = "offline mode requested" if offline else "no Gemini key"
    out = _offline_plan(setup, rounds, reason)
    if not out["ideas"]:
        raise AIError("offline_no_candidate", "Offline search found no composed strategy with enough trades on the train period.")
    out["notice"] = out["thesis"]
    return out, out["thesis"] + f" Kept the top {len(out['ideas'])} families as hypotheses."


def _train_view(v: dict) -> dict:
    tr = v["result"]["train"]["metrics"]
    return dict(sharpe=tr["sharpe"], total_return=tr["total_return"], trades=tr["trades"], max_drawdown=tr["max_drawdown"])


def feedback_text(t: dict) -> str:
    """Train-period only (locked period is never used for feedback)."""
    if t["trades"] < MIN_TRAIN_TRADES:
        return f"Only {t['trades']} trades on train: loosen entry conditions or shorten lookbacks."
    if t["sharpe"] < 0:
        return f"Lost money on train (Sharpe {t['sharpe']:.2f}): this logic has no edge here; try a different family of idea."
    if t["max_drawdown"] < -0.35:
        return f"Train drawdown {t['max_drawdown']*100:.0f}% is deep: add a protective stop or a volatility filter."
    return f"Positive on train (Sharpe {t['sharpe']:.2f}, {t['trades']} trades): try a simpler variant that keeps the edge, to guard against overfitting."


def _facts(exps: list[dict], chosen: dict | None, v: dict | None, setup: dict) -> str:
    rows = "\n".join(f"- round {e['round']} '{e['title']}': train Sharpe {e['train_sharpe']:.2f}, train return {e['train_return']*100:+.1f}%, {e['train_trades']} train trades; locked return {e['locked_return']*100:+.1f}% "
                     f"(buy&hold {e['locked_bh']*100:+.1f}%), locked Sharpe {e['locked_sharpe']:.2f}, {e['locked_trades']} locked trades; verdict {e['verdict']}; {e['status']}" for e in exps if e.get("version_id"))
    return f"Market {setup['symbol']} {setup['interval']}. Experiments:\n{rows}\nChosen: {chosen['title'] if chosen else 'none'}."


def _offline_debate(exps: list[dict], chosen: dict, v: dict) -> dict:
    lk, tr = chosen, chosen
    fo = v["stats"].get("folds") or {}
    bull, bear = [], []
    if chosen["locked_return"] > 0:
        bull.append(f"Positive on the locked final period ({chosen['locked_return']*100:+.1f}%) with Sharpe {chosen['locked_sharpe']:.2f}, data it was not selected on.")
    if chosen["train_sharpe"] > 0.5:
        bull.append(f"Train Sharpe {chosen['train_sharpe']:.2f} over {chosen['train_trades']} trades is a meaningful sample.")
    if fo.get("n") and fo["positive_blocks"] > fo["n"] // 2:
        bull.append(f"{fo['positive_blocks']} of {fo['n']} equal time blocks were profitable, so it is not one lucky stretch.")
    if chosen["locked_return"] > chosen["locked_bh"]:
        bull.append(f"It beat buy & hold on the locked period ({chosen['locked_bh']*100:+.1f}%).")
    if not bull:
        bull.append("Costs and next-bar entries are already included, so the numbers are not flattered by free trading.")
    if chosen["locked_trades"] < 10:
        bear.append(f"Only {chosen['locked_trades']} trades on the locked period: too few to separate skill from luck.")
    if chosen["locked_return"] <= 0:
        bear.append(f"Locked-period return was {chosen['locked_return']*100:+.1f}%: it did not hold up out of sample.")
    if chosen["locked_return"] < chosen["locked_bh"]:
        bear.append(f"Buy & hold made {chosen['locked_bh']*100:+.1f}% on the same period versus {chosen['locked_return']*100:+.1f}%.")
    n = len([e for e in exps if e.get("version_id")])
    bear.append(f"{n} experiment(s) were tried; picking the best of several inflates how good the winner looks (selection bias).")
    d = (v["stats"].get("dsr") or {}).get("dsr")
    if d is not None and d < 0.95:
        bear.append(f"Deflated Sharpe is {d*100:.0f}%, below the usual 95% bar.")
    return dict(bull=bull[:4], bear=bear[:4])


DEBATE_SYS = ("You are two analysts. BULL argues the strongest honest case that the chosen backtested strategy has a real edge; BEAR argues the strongest honest case that it is luck, overfit or fragile. "
              "Use ONLY the numbers provided; do not invent data. Never give investment advice. Return ONLY JSON: {\"bull\": [up to 3 short sentences], \"bear\": [up to 3 short sentences]}")


def tool_debate(exps, chosen, v, setup, offline):
    det = _offline_debate(exps, chosen, v)
    if not offline and gemini.enabled():
        try:
            obj, meta = gemini.generate_json(DEBATE_SYS, _facts(exps, chosen, v, setup) + f"\nRisk-relevant: deflated Sharpe {((v['stats'].get('dsr') or {}).get('dsr'))}.", tag="debate", temperature=0.3)
            bull = [str(x)[:260] for x in (obj.get("bull") or [])][:3]
            bear = [str(x)[:260] for x in (obj.get("bear") or [])][:3]
            if bull and bear:
                return dict(source="gemini", model=meta["model"], bull=bull, bear=bear), "Bull and Bear researchers argued the case (Gemini, one call)."
        except gemini.GeminiUnavailable:
            pass
    return dict(source="rules", **det), "Bull and Bear cases built deterministically from the measured numbers (offline)."


# ------------------------------------------------------------------ driver
def _experiment_row(rnd: int, hyp: dict, v: dict, best_score: float | None) -> dict:
    res = v["result"]
    tr, lk, lb = _train_view(v), res["locked"]["metrics"], res["locked"]["benchmark"]
    eligible = tr["trades"] >= MIN_TRAIN_TRADES
    if eligible and (best_score is None or tr["sharpe"] > best_score):
        status, reason = "kept", ("first eligible experiment" if best_score is None else f"train Sharpe {tr['sharpe']:.2f} beats previous best {best_score:.2f}")
    elif not eligible:
        status, reason = "dropped", f"only {tr['trades']} train trades (need {MIN_TRAIN_TRADES})"
    else:
        status, reason = "dropped", f"train Sharpe {tr['sharpe']:.2f} does not beat best {best_score:.2f}"
    return dict(round=rnd, hypothesis=hyp["name"], hypothesis_text=hyp["brief"], version_id=v["id"], n=v["n"], title=v["title"], source=v["source"], train_sharpe=tr["sharpe"], train_return=tr["total_return"],
                train_trades=tr["trades"], locked_return=lk["total_return"], locked_sharpe=lk["sharpe"], locked_trades=lk["trades"], locked_bh=lb["total_return"], verdict=v["verdict"]["label"],
                status=status, reason=reason, feedback=feedback_text(tr))


def _fail_row(rnd: int, hyp: dict, why: str) -> dict:
    return dict(round=rnd, hypothesis=hyp["name"], hypothesis_text=hyp["brief"], version_id=None, title="(no valid code)", source=None, train_sharpe=0.0, train_return=0.0, train_trades=0, locked_return=0.0,
                locked_sharpe=0.0, locked_trades=0, locked_bh=0.0, verdict="FAILED", status="dropped", reason=why[:160], feedback="Code was rejected by the safety / look-ahead checks; the next round writes a different idea.")


def _fail(rid: str, err: dict):
    store.update_run(rid, status="failed", error=err)


def run(rid: str) -> None:
    """Drive (or resume) a run to completion. Safe to call again after a crash: finished steps are replayed from SQLite."""
    owner = f"{os.getpid()}-{threading.get_ident()}"
    if not store.claim_run(rid, owner, stale_s=120.0):
        return
    try:
        _drive(rid)
    except AIError as e:
        _fail(rid, e.dump())
    except Exception as e:      # noqa: BLE001
        log.exception("run %s crashed", rid)
        _fail(rid, dict(code="agent_crashed", message=f"{type(e).__name__}: {str(e)[:200]}", retryable=True))


def _drive(rid: str) -> None:
    run_ = store.get_run(rid)
    p = run_["params"]
    goal, target, rounds, max_steps = run_["goal"], p["target"], int(p.get("rounds", 3)), int(p.get("max_steps", MAX_STEPS))
    prefs = p.get("preferences") or {}
    if prefs:
        goal += f" Style constraint: {prefs.get('style', 'any')}. Maximum drawdown: {prefs.get('max_drawdown_pct', 50)}%."
    force_off = bool(p.get("offline"))
    with closing(store._db()) as con:                         # drop failed/half-done rows so they are retried on resume
        con.execute("DELETE FROM ai_steps WHERE run_id=? AND status!='ok'", (rid,))
        con.commit()
    setup = dict(service.get_setup(target), preferences=p.get("preferences") or {})
    sid = run_["session_id"]
    ctx = Ctx(rid, max_steps)
    exps: list[dict] = []
    plan = None
    state: dict = dict(offline=force_off or run_["mode"] == "offline")

    def sync_mode(out):
        if out and out.get("source") in ("offline", "offline_synth") and not state["offline"]:
            state["offline"] = True
            store.update_run(rid, mode="offline")
    try:
        d = ctx.step("data", "get_data", dict(symbol=setup["symbol"], interval=setup["interval"]), lambda: tool_data(setup))
        nw = ctx.step("news", "get_news", dict(symbol=setup["symbol"]), lambda: tool_news(setup))
        mem = ctx.step("memory", "recall_memory", dict(symbol=setup["symbol"]), lambda: tool_memory(setup))
        mem_note = " ".join(r["headline"] for r in (mem or {}).get("runs", [])[:2])
        news_note = "; ".join(i["title"] for i in (nw or {}).get("items", [])[:4])
        plan = ctx.step("plan", "plan", dict(goal=goal, rounds=rounds), lambda: tool_plan(state, setup, goal, rounds, mem_note, news_note, state["offline"]))
        if plan is None:
            raise AIError("plan_failed", "The research plan could not be made.", retryable=True)
        sync_mode(plan)
        ideas = plan["ideas"]
        base = dict(trials=int(plan.get("tried") or 0), sharpes=list(plan.get("trial_sharpes") or []))
        best, best_score, feedback = None, None, ""
        for r in range(1, rounds + 1):
            refine = r > len(ideas)
            if refine and (best is None or state["offline"]):
                break
            hyp = ideas[r - 1] if not refine else dict(name=f"Refine: {best['title']}"[:60], brief="Simplify the best strategy so far while keeping its edge; remove any parameter or condition that is not clearly needed.")
            inputs = dict(round=r, hypothesis=hyp["name"])
            cand_off = plan["candidates"][r - 1] if state["offline"] and r <= len(plan.get("candidates") or []) else None
            ver_id: dict = {}

            def write(r=r, hyp=hyp, refine=refine, cand_off=cand_off):
                other = list(base["sharpes"]) or store.trial_sharpes(sid)
                n_tr = base["trials"] or (len(other) + 1)
                prior = store.get_version(best["version_id"]) if refine and best else None
                brief = hyp["brief"] + (f"\nFeedback from the previous round (train data only): {feedback}" if feedback else "")
                hist = f"Memory: {mem_note}" if mem_note else ""
                notice = None
                if state["offline"] and cand_off is None:
                    raise AIError("offline_no_candidate", "No further composed candidates available in offline mode.")
                if not state["offline"]:
                    try:
                        res = service.author(setup, brief, prior, hist, n_tr, other)
                    except gemini.GeminiUnavailable as e:
                        notice = f"Gemini became unavailable ({e.reason}); switched to offline code synthesis for the remaining rounds."
                        state["offline"] = True
                        store.update_run(rid, mode="offline")
                        pool = _offline_plan(setup, rounds, f"Gemini unavailable: {e.reason}")
                        plan["candidates"] = pool["candidates"]
                        base.update(trials=pool["tried"], sharpes=list(pool["trial_sharpes"]))
                        if not pool["candidates"]:
                            raise AIError("offline_no_candidate", "Offline search found no usable candidate.")
                        res = service.author(setup, brief, None, "", pool["tried"], pool["trial_sharpes"], offline_candidate=pool["candidates"][min(r - 1, len(pool["candidates"]) - 1)])
                else:
                    res = service.author(setup, brief, None, "", n_tr, other, offline_candidate=cand_off)
                v = service.save_version(sid, setup, res, hyp["brief"], prior["id"] if prior else None, notes=dict(round=r, hypothesis=hyp["name"], run_id=rid), cost_bps=target.get("cost_bps"))
                out = dict(version_id=v["id"], n=v["n"], title=v["title"], source=res["source"], model=res["model"], attempts=res["attempts"], rules=v["rules"], explanation=v["explanation"], notice=notice)
                return out, f"Hypothesis '{hyp['name']}': wrote '{v['title']}' ({'Gemini ' + str(res['model']) if res['source'] == 'gemini' else 'offline synthesizer'}, {res['attempts']} attempt{'s' if res['attempts'] > 1 else ''}); passed the code allowlist and look-ahead check."

            w = ctx.step(f"write:{r}", "write_strategy", inputs, write)
            if w is None:
                err = (store.steps(rid)[-1].get("error") or {}).get("message", "write failed")
                exps.append(_fail_row(r, hyp, err))
                feedback = "The previous attempt could not produce valid code: " + err[:140]
                continue
            sync_mode(dict(source="offline" if w.get("source") == "offline_synth" else "gemini"))

            def bt(r=r, hyp=hyp, w=w):
                v = store.get_version(w["version_id"])
                row = _experiment_row(r, hyp, v, best_score)
                tv = _train_view(v)
                return dict(experiment=row), (f"Train: Sharpe {tv['sharpe']:.2f}, return {tv['total_return']*100:+.1f}%, {tv['trades']} trades. Locked final period (not used to choose): "
                                              f"{row['locked_return']*100:+.1f}% vs buy & hold {row['locked_bh']*100:+.1f}%, {row['locked_trades']} trades. "
                                              f"{row['status'].upper()}: {row['reason']}.")
            b = ctx.step(f"bt:{r}", "backtest", dict(round=r, version_id=w["version_id"]), bt)
            if b is None:
                exps.append(_fail_row(r, hyp, "backtest step failed"))
                continue
            row = b["experiment"]
            exps.append(row)
            feedback = row["feedback"]
            if row["status"] == "kept":
                best, best_score = row, row["train_sharpe"]
        # no final stage restrictions below
    except StepCap:
        log.info("run %s hit the exploration step cap", rid)
        ctx.capped = True
    _finish(rid, ctx, setup, sid, exps, plan, state, goal, target, max_steps)


def _finish(rid, ctx, setup, sid, exps, plan, state, goal, target, max_steps):
    kept = [e for e in exps if e["status"] == "kept"]
    chosen = kept[-1] if kept else None
    capped = ctx.capped
    store.update_run(rid)
    if chosen is None:
        out = _final_none(exps, state, capped, setup)
        ctx.step("decide", "decide", dict(chosen=None), lambda: (dict(final=out), out["headline"]), final=True)
        store.update_run(rid, status="done", final=out)
        return
    v = store.get_version(chosen["version_id"])
    deb = ctx.step("debate", "debate", dict(version_id=v["id"]), lambda: tool_debate(exps, chosen, v, setup, state["offline"]), final=True)
    total_trials = max(int((plan or {}).get("tried") or 0), len(store.trial_sharpes(sid)), len(exps))
    sharpes = list((plan or {}).get("trial_sharpes") or []) or store.trial_sharpes(sid)
    risk = ctx.step("risk_check", "risk_check", dict(version_id=v["id"], trials=total_trials), lambda: _risk_step(v, setup, total_trials, sharpes), final=True)

    def decide():
        out = _final_card(rid, v, setup, sid, chosen, exps, deb, risk, state, capped, total_trials, sharpes, goal)
        return dict(final=out), out["headline"]
    d = ctx.step("decide", "decide", dict(version_id=v["id"]), decide, final=True)
    if d is None:
        raise AIError("decide_failed", "The final decision step failed.", retryable=True)
    store.update_run(rid, status="done", final=d["final"])


def _risk_step(v, setup, total_trials, sharpes):
    r = service.risk_review(v, setup, total_trials, sharpes)
    msg = f"{r['verdict']}. " + " ".join((r["vetoes"] + r["cautions"] + r["passes"])[:3])
    return r, msg


def _final_none(exps, state, capped, setup):
    why = "every experiment failed the code checks" if exps and all(e["verdict"] == "FAILED" for e in exps) else f"no experiment reached {MIN_TRAIN_TRADES} trades on the train period"
    return dict(decision="NO RESULT", headline=f"No candidate to report: {why}.", version_id=None, mode="offline" if state["offline"] else "gemini", capped=capped, disclaimer=DISCLAIMER,
                lesson=f"{setup['symbol']} {setup['interval']}: agent run produced no usable strategy ({why}).")


def _final_card(rid, v, setup, sid, chosen, exps, deb, risk, state, capped, total_trials, sharpes, goal):
    # recompute the overfit statistics for the chosen version with the FINAL trial count (every experiment/combination tried), then persist
    pos = service.dec_pos(v["stats"]["positions"])
    sink: dict = {}
    res = engine.backtest_positions(setup["df"], pos, v["exits"], setup["k"], setup["cost"], setup["periods"], setup["fmt"], setup["allow_short"], ret_sink=sink)
    dsr = engine.dsr_for(sink["train_r"], sharpes + ([] if v["trial_sharpe"] is None else [v["trial_sharpe"]]), total_trials, setup["periods"])
    ver = engine.verdict(res, total_trials, dsr, v["stats"].get("folds"))
    stats = dict(v["stats"], dsr=dsr, n_trials=total_trials)
    store.update_version(v["id"], verdict=ver, stats=stats)
    v = store.get_version(v["id"])
    mem = service.remember(v, setup, sid, kind="ai_agent")
    store.update_version(v["id"], memory_run_id=mem.get("run_id"))
    full, tr, lk = res["full"], res["train"], res["locked"]
    thin = engine.thin(dict(full=dict(dates=full["dates"], equity=full["equity"], benchmark_equity=full["benchmark_equity"])))["full"]
    rv = risk["verdict"] if risk else "NOT RUN"
    if rv == "VETO":
        decision, line = "DO NOT TRADE", "The Risk Manager vetoed it: " + " ".join((risk["vetoes"] or [])[:2])
    elif rv == "CAUTION":
        decision, line = "WEAK EVIDENCE", "Mixed evidence; at most worth watching on paper. " + " ".join((risk["cautions"] or [])[:2])
    else:
        decision, line = "EVIDENCE HELD UP", "Held up on data it was not selected on and passed the risk checks. Still only worth watching on paper first."
    lesson = (f"AI research run on {setup['symbol']} {setup['interval']}: '{v['title']}' after {total_trials} trial(s): {ver['label']}, locked return {lk['metrics']['total_return']*100:+.1f}% "
              f"vs buy & hold {lk['benchmark']['total_return']*100:+.1f}%; risk {rv}. {ver['reasons'][0]}")
    try:
        with closing(legacy_memory._db()) as con:
            con.execute("INSERT INTO lessons(ts, run_id, ticker, template, kind, text, tags) VALUES(?,?,?,?,?,?,?)", (time.time(), mem.get("run_id") or rid, setup["symbol"], f"ai:{v['title'][:50]}", "ai_agent", lesson, "{}"))
            con.commit()
    except Exception as e:      # noqa: BLE001
        log.warning("lesson write failed: %s", e)
    mem_dest = write_meko(lesson, setup, v["title"], decision)
    return dict(decision=decision, headline=f"{decision}: {v['title']}. {line}", version_id=v["id"], title=v["title"], rules=v["rules"], explanation=v["explanation"], mode="offline" if state["offline"] else "gemini",
                source=v["source"], model=v["model"], verdict=ver, risk=risk, debate=deb, metrics=dict(train=tr["metrics"], locked=lk["metrics"], full=full["metrics"], benchmark_train=tr["benchmark"],
                benchmark_locked=lk["benchmark"], benchmark_full=full["benchmark"]), periods=dict(train=[tr["start"], tr["end"]], locked=[lk["start"], lk["end"]]), chart=thin, locked_from=lk["start"],
                dsr=dsr, trials=total_trials, capped=capped, lesson=lesson, memory_run_id=mem.get("run_id"), memory_written=["SQLite"] + (["Meko"] if mem_dest == "Meko" else []), meko_status=mem_dest, cost=setup["cost_label"], disclaimer=DISCLAIMER,
                limits=["Backtest on past data with assumed costs; live trading differs (slippage, outages, regime change).",
                        "Experiments were chosen on the train period only; the locked period was shown but not used to choose.",
                        "The 4-block stability check is not a clean out-of-sample test."])


# ------------------------------------------------------------------ public helpers
def create(goal: str, target: dict, rounds: int = 3, offline: bool = False, max_steps: int = MAX_STEPS, preferences: dict | None = None) -> str:
    mode = "gemini" if gemini.enabled() and not offline else "offline"
    sid = store.create_session(goal, target["market"], target["symbol"], target["interval"])
    store.add_message(sid, "user", goal)
    return store.create_run(goal, dict(target=target, rounds=rounds, offline=offline, max_steps=max_steps, preferences=preferences or {}), mode, sid)


def start(rid: str) -> bool:
    """Run in a background thread (one thread per run id in this process)."""
    with _alive_lock:
        th = _alive.get(rid)
        if th and th.is_alive():
            return False
        th = threading.Thread(target=run, args=(rid,), daemon=True, name=f"ai-run-{rid}")
        _alive[rid] = th
        th.start()
    return True


def is_alive(rid: str) -> bool:
    th = _alive.get(rid)
    return bool(th and th.is_alive())
