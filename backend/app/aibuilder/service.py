"""Core pipeline shared by the chat builder and the research agent:
write code (Gemini, or the offline synthesizer) -> AST allowlist -> sandbox + look-ahead check -> backtest -> honesty checks -> persist."""
from __future__ import annotations
import logging, re, threading, time
import numpy as np
from .. import config, crypto_data as cd, crypto_memory, data, strategy_memory
from . import ast_guard, codegen, engine, gemini, sandbox, store, synth
from .models import AIError

log = logging.getLogger("rex.ai")
_setup_cache: dict = {}
_setup_lock = threading.Lock()
SYMS = {"BTCUSDT": ("btc", "bitcoin"), "ETHUSDT": ("eth", "ethereum", "ether"), "SOLUSDT": ("sol", "solana")}


# ------------------------------------------------------------------ goal parsing / market setup
def parse_goal(text: str) -> dict:
    """Crypto-first: BTC/ETH/SOL words pick crypto (+ candle size); a known NSE name picks NSE; otherwise BTC 1h."""
    t = " " + re.sub(r"[^a-z0-9. ]", " ", text.lower()) + " "
    for sym, words in SYMS.items():
        if any(f" {w} " in t or f" {w}usdt " in t or f" {w}usd " in t for w in words):
            iv = "4h" if re.search(r"\b(4 ?h|4 ?hr|4 hour|four hour)\b", t) else "1d" if re.search(r"\b(1d|daily|day candles|1 day)\b", t) else "1h"
            return dict(market="crypto", symbol=sym, interval=iv, cost_bps=None)
    for tk in config.DEFAULT_TICKERS:
        if f" {tk.replace('.NS', '').lower()} " in t:
            return dict(market="nse", symbol=tk, interval="1d", cost_bps=None)
    return dict(market="crypto", symbol="BTCUSDT", interval="1h", cost_bps=None)


def get_setup(target: dict, refresh: bool = False) -> dict:
    key = (target["market"], target["symbol"], target.get("interval", "1d"), target.get("cost_bps"))
    with _setup_lock:
        hit = _setup_cache.get(key)
        if hit and time.time() - hit[0] < 20 and not refresh:
            return hit[1]
    try:
        s = engine.market_setup(target["market"], target["symbol"], target.get("interval", "1d"), target.get("cost_bps"), refresh=refresh)
    except (cd.CryptoError, data.DataError) as e:
        raise AIError("data_unavailable", f"Could not load {target['symbol']} data: {e}", retryable=True, http=502)
    except Exception as e:
        raise AIError("data_unavailable", f"Could not load {target['symbol']} data ({type(e).__name__}).", retryable=True, http=502)
    with _setup_lock:
        if len(_setup_cache) > 12:
            _setup_cache.clear()
        _setup_cache[key] = (time.time(), s)
    return s


def target_of(setup: dict, cost_bps=None) -> dict:
    return dict(market=setup["market"], symbol=setup["symbol"], interval=setup["interval"], cost_bps=cost_bps)


def _double(cost):
    return (cost[0] * 2, cost[1] * 2) if isinstance(cost, tuple) else cost * 2


def enc_pos(pos) -> str:
    return "".join("2" if x < 0 else str(int(x)) for x in pos)


def dec_pos(s: str) -> list[int]:
    return [{"0": 0, "1": 1, "2": -1}[c] for c in s]


# ------------------------------------------------------------------ evaluate one piece of code
def evaluate(setup: dict, code: str, exits: dict, n_trials: int, other_sharpes: list[float]) -> dict:
    """Raises ast_guard.CodeRejected / sandbox.SandboxError. Everything else is computed from the sandbox's position series."""
    out = sandbox.run_signals(code, setup["df"])
    pos = out["positions"]
    ex = engine.clean_exits(exits)
    sink: dict = {}
    res = engine.backtest_positions(setup["df"], pos, ex, setup["k"], setup["cost"], setup["periods"], setup["fmt"], setup["allow_short"], ret_sink=sink)
    sh = engine.per_period_sharpe(sink["train_r"])
    dsr = engine.dsr_for(sink["train_r"], list(other_sharpes) + [sh], max(n_trials, 1), setup["periods"])
    fo = engine.folds(setup["df"], pos, ex, setup["k"], setup["cost"], setup["periods"], setup["fmt"], setup["allow_short"])
    ver = engine.verdict(res, n_trials, dsr, fo)
    engine.thin(res)
    p = np.asarray(pos)
    stats = dict(positions=enc_pos(pos), exposure=round(float((p != 0).mean()), 3), lookahead_free=out["lookahead_free"], lookahead_cuts=len(out["cuts"]), sandbox_seconds=out["seconds"],
                 isolation=sandbox.isolation(), dsr=dsr, folds=fo, n_trials=n_trials, shorts_ignored=bool((p < 0).any() and not setup["allow_short"]), exits=ex)
    return dict(result=res, verdict=ver, stats=stats, trial_sharpe=sh, exits=ex)


# ------------------------------------------------------------------ write + evaluate with one automatic retry
def author(setup: dict, brief: str, prior: dict | None, history: str, n_trials: int, other: list[float], offline_candidate: dict | None = None) -> dict:
    """-> dict(cand, ev, source, model, attempts). Gemini path: write -> check -> (on failure) send the error back once. Offline path: use the synthesized candidate."""
    if offline_candidate is not None:
        ev = evaluate(setup, offline_candidate["code"], offline_candidate["exits"], n_trials, other)
        c = dict(title=offline_candidate["title"], explanation=f"Offline mode: composed from indicator building blocks ({offline_candidate['family']}) and selected by searching combinations on the train period only.",
                 rules=offline_candidate["rules"], exits=ev["exits"], code=offline_candidate["code"])
        return dict(cand=c, ev=ev, source="offline_synth", model=None, attempts=1)
    err, last = None, None
    for attempt in (1, 2):
        cand, meta = codegen.write(setup, brief, prior=prior, error=err, history=history)      # may raise GeminiUnavailable
        try:
            ast_guard.validate(cand["code"])
            ev = evaluate(setup, cand["code"], cand["exits"], n_trials, other)
            return dict(cand=cand, ev=ev, source="gemini", model=meta["model"], attempts=attempt)
        except (ast_guard.CodeRejected, sandbox.SandboxError) as e:
            err = f"{type(e).__name__}: {e}"
            last = e
            log.info("strategy attempt %d failed: %s", attempt, err)
    raise AIError("strategy_failed", "The strategy code could not be made to pass the safety / look-ahead checks after one automatic retry.", detail=str(last), retryable=True, http=422)


def save_version(session_id: str, setup: dict, res: dict, message: str, parent_id: str | None, notes: dict | None = None, cost_bps=None) -> dict:
    cand, ev = res["cand"], res["ev"]
    return store.save_version(dict(session_id=session_id, parent_id=parent_id, message=message, title=cand["title"], explanation=cand["explanation"], rules=cand["rules"], code=cand["code"],
                                   exits=ev["exits"], source=res["source"], model=res["model"], market=setup["market"], symbol=setup["symbol"], interval=setup["interval"],
                                   cost_bps=cost_bps, status="ok", result=ev["result"], verdict=ev["verdict"], stats=ev["stats"], notes=dict(notes or {}, attempts=res["attempts"]),
                                   trial_sharpe=ev["trial_sharpe"]))


# ------------------------------------------------------------------ chat builder (one message -> one version)
EDIT_RE = [("stop_pct", re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:stop|sl|stoploss|stop[- ]loss)|(?:stop|sl|stop[- ]loss)\D{0,12}(\d+(?:\.\d+)?)\s*%", re.I)),
           ("tp_pct", re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:target|take[- ]profit|tp)|(?:target|take[- ]profit|tp)\D{0,12}(\d+(?:\.\d+)?)\s*%", re.I)),
           ("hold_bars", re.compile(r"(?:hold|exit after|max(?:imum)? hold)\D{0,12}(\d+)\s*(?:bars?|candles?|days?)", re.I))]


def chat_turn(message: str, session_id: str | None, parent_version_id: str | None, target: dict | None) -> dict:
    sess = store.get_session(session_id) if session_id else None
    parent = store.get_version(parent_version_id) if parent_version_id else (store.versions(session_id, light=False)[-1] if sess and store.versions(session_id) else None)
    if sess is None:
        tgt = target or parse_goal(message)
        sid = store.create_session(message, tgt["market"], tgt["symbol"], tgt.get("interval", "1d"))
    else:
        sid = sess["id"]
        tgt = dict(market=sess["market"], symbol=sess["symbol"], interval=sess["interval"], cost_bps=(target or {}).get("cost_bps"))
    setup = get_setup(tgt)
    store.add_message(sid, "user", message)
    other = store.trial_sharpes(sid)
    n_trials = len(other) + 1
    history = "\n".join(f"v{v['n']}: {v['title']} -> train Sharpe {v['result']['train']['metrics']['sharpe']:.2f}, verdict {v['verdict']['label']}" for v in store.versions(sid)[-4:])
    res, note = None, None
    if gemini.enabled():
        try:
            res = author(setup, message, parent, history, n_trials, other)
        except gemini.GeminiUnavailable as e:
            note = f"Gemini unavailable ({e.reason}): {e}"
        except AIError as e:
            store.add_message(sid, "assistant", f"I could not build that: {e.message} {e.detail or ''}")
            raise AIError(e.code, e.message, e.detail, e.retryable, e.http)
    else:
        note = "GEMINI_API_KEY is not set"
    if res is None:                      # offline mode
        res = _offline_chat(setup, message, parent, n_trials, other, sid, note)
    v = save_version(sid, setup, res, message, parent["id"] if parent else None, notes=dict(offline_reason=note), cost_bps=tgt.get("cost_bps"))
    mem = remember(v, setup, sid)
    store.update_version(v["id"], memory_run_id=mem.get("run_id"))
    reply = res["cand"]["explanation"] + f"\n\nVerdict: {v['verdict']['label']}. " + " ".join(v["verdict"]["reasons"][:2])
    if res["source"] == "offline_synth":
        reply = "OFFLINE MODE (Gemini unavailable: " + (note or "") + "). " + reply
    store.add_message(sid, "assistant", reply, v["id"])
    return dict(session_id=sid, version=store.get_version(v["id"]), reply=reply, offline=res["source"] == "offline_synth", notice=note)


def _offline_chat(setup, message, parent, n_trials, other, sid, note) -> dict:
    edits = {}
    for k, rx in EDIT_RE:
        m = rx.search(message)
        if m:
            edits[k] = float(next(g for g in m.groups() if g))
    if parent and edits:                       # exit rules live outside the code, so exit edits work without a model
        ex = dict(parent["exits"] or {}, **edits)
        ev = evaluate(setup, parent["code"], ex, n_trials, other)
        return dict(cand=dict(title=parent["title"], explanation=f"Offline mode: kept the entry logic and changed only the exit rules ({', '.join(f'{k}={v:g}' for k, v in edits.items())}). Rewriting entry logic needs Gemini.",
                              rules=list(parent["rules"] or []) + [f"Exit rule changed: {k} = {v:g}." for k, v in edits.items()], exits=ev["exits"], code=parent["code"]), ev=ev, source="offline_synth", model=None, attempts=1)
    if parent:
        store.add_message(sid, "assistant", "Offline mode can only change exit rules (stop, target, hold time) without Gemini. Try again when Gemini is back, or say e.g. 'add a 5% stop'.")
        raise AIError("offline_cannot_edit", "Gemini is unavailable and offline mode can only change exit rules (stop, take-profit, max hold) on an existing strategy.", detail=note, retryable=True, http=503)
    s = synth.search(setup, top=1)
    if not s["ranked"]:
        raise AIError("offline_no_candidate", "Offline search found no composed strategy with enough trades on the train period.", http=422)
    out = author(setup, message, None, "", s["tried"], s["trial_sharpes"], offline_candidate=s["ranked"][0])
    out["cand"]["explanation"] = "Offline discovery: your exact entry instructions were not implemented. This is a searched alternative; review its rules before using it. " + out["cand"]["explanation"]
    return out


# ------------------------------------------------------------------ memory
def remember(v: dict, setup: dict, session_id: str | None, kind: str = "ai") -> dict:
    """Save the run into the existing strategy-run memory (NSE) or crypto memory (market tag 'crypto')."""
    try:
        mod = crypto_memory if v["market"] == "crypto" else strategy_memory
        res = v["result"]
        kw = dict(interval=v["interval"]) if v["market"] == "crypto" else {}
        full = dict(res, train=res["train"], locked=res["locked"])
        return mod.record_run(kind, v["symbol"], f"ai:{v['title'][:50]}", dict(version=v["n"], source=v["source"], market=v["market"]), v["exits"] or {}, float(setup["cost"] * 1e4) if not isinstance(setup["cost"], tuple) else float(sum(setup["cost"]) * 1e4),
                              result=full, verdict=v["verdict"]["label"], df=None, anchor_date=str(setup["df"].index[-1].date()), session_id=session_id, **kw)
    except Exception as e:
        log.warning("memory write failed: %s", e)
        return dict(error=str(e))


# ------------------------------------------------------------------ risk + challenge (deterministic; the veto is never an LLM decision)
def risk_review(v: dict, setup: dict, total_trials: int, trial_sharpes: list[float]) -> dict:
    pos = dec_pos(v["stats"]["positions"])
    ex = v["exits"]
    sink: dict = {}
    base = engine.backtest_positions(setup["df"], pos, ex, setup["k"], setup["cost"], setup["periods"], setup["fmt"], setup["allow_short"], keep_series=False, ret_sink=sink)
    dbl = engine.backtest_positions(setup["df"], pos, ex, setup["k"], _double(setup["cost"]), setup["periods"], setup["fmt"], setup["allow_short"], keep_series=False)
    dsr = engine.dsr_for(sink["train_r"], trial_sharpes, total_trials, setup["periods"])
    tr, lk, full = base["train"]["metrics"], base["locked"]["metrics"], base["full"]["metrics"]
    veto, caution, ok = [], [], []
    if v["verdict"]["flag"] in ("overfit", "lost"):
        veto.append(f"Locked final period: {v['verdict']['label'].lower()} (return {lk['total_return']*100:+.1f}%, Sharpe {lk['sharpe']:.2f}).")
    if tr["trades"] < 8:
        veto.append(f"Only {tr['trades']} trades on the train period: nothing to learn from.")
    limit = float((setup.get("preferences") or {}).get("max_drawdown_pct", 50))
    if full["max_drawdown"] < -limit / 100:
        veto.append(f"Max drawdown {full['max_drawdown']*100:.1f}% is beyond your {limit:g}% limit.")
    if 0 < lk["trades"] < 10 and v["verdict"]["flag"] != "inconclusive":
        caution.append(f"Only {lk['trades']} trades on the locked period: thin evidence.")
    if lk["trades"] == 0:
        caution.append("No trades on the locked period: it was not tested out of sample.")
    if dsr and dsr["dsr"] is not None and dsr["dsr"] < 0.95:
        caution.append(f"Deflated Sharpe {dsr['dsr']*100:.0f}% after {total_trials} trials is below the 95% bar.")
    if full["total_return"] > 0 and dbl["full"]["metrics"]["total_return"] <= 0:
        caution.append("Edge disappears if trading costs are doubled.")
    elif full["total_return"] > 0:
        ok.append(f"Still positive with doubled costs ({dbl['full']['metrics']['total_return']*100:+.1f}%).")
    if lk["total_return"] > 0 and lk["total_return"] < base["locked"]["benchmark"]["total_return"]:
        caution.append(f"Trailed buy & hold on the locked period: buy & hold made {base['locked']['benchmark']['total_return']*100:+.1f}% vs {lk['total_return']*100:+.1f}%.")
    fo = v["stats"].get("folds") or {}
    if fo.get("n") and fo["positive_blocks"] <= fo["n"] // 2:
        caution.append(f"Only {fo['positive_blocks']} of {fo['n']} time blocks were profitable: regime dependent.")
    elif fo.get("n"):
        ok.append(f"{fo['positive_blocks']} of {fo['n']} time blocks profitable.")
    if v["verdict"]["flag"] == "ok":
        ok.append("Held up on the locked final period.")
    verdict = "VETO" if veto else "CAUTION" if caution else "APPROVE"
    return dict(verdict=verdict, vetoes=veto, cautions=caution, passes=ok, cost_2x_return=dbl["full"]["metrics"]["total_return"], cost_1x_return=full["total_return"],
                dsr=dsr, total_trials=total_trials,
                rules="Veto: locked period overfit/lost, <8 train trades, drawdown beyond the configured limit. Caution: thin locked sample, deflated Sharpe <95%, edge dies at 2x costs, trails buy & hold, <=half the time blocks profitable.")


# ------------------------------------------------------------------ live signal watch ("track this live")
def live_signal(v: dict, started_at: float | None = None) -> dict:
    tgt = dict(market=v["market"], symbol=v["symbol"], interval=v["interval"], cost_bps=v.get("cost_bps"))
    setup = get_setup(tgt, refresh=(v["market"] == "crypto"))
    df = setup["df"]
    try:
        out = sandbox.run_signals(v["code"], df, lookahead=False)
    except (ast_guard.CodeRejected, sandbox.SandboxError) as e:
        raise AIError("live_failed", f"Could not run the strategy on the latest candles: {e}", retryable=True, http=502)
    pos = np.asarray(out["positions"])
    chg = np.nonzero(np.diff(pos) != 0)[0]
    last_chg = int(chg[-1]) + 1 if len(chg) else 0
    q = None
    if v["market"] == "crypto":
        try:
            q = cd.quote(v["symbol"])
        except Exception:
            q = None
    name = {1: "LONG", 0: "FLAT", -1: "SHORT"}[int(pos[-1])]
    paper = None
    if started_at:
        idx = df.index
        after = int(np.searchsorted(np.asarray(idx.astype("int64") // 10**9), started_at))
        a = max(after - 1, 0)
        if a < len(df) - 1:
            r, trades = engine.sim_window(df["Open"].to_numpy(float), df["High"].to_numpy(float), df["Low"].to_numpy(float), df["Close"].to_numpy(float), pos, a, len(df) - 1,
                                         engine.clean_exits(v["exits"]), setup["cost"], setup["allow_short"])
            paper = dict(trades=len(trades), return_pct=round(float((np.prod(1 + r) - 1) * 100), 2), closed=[dict(side=t["side"], entry=t["entry"], exit=t["exit"], return_pct=round(t["ret"] * 100, 2)) for t in trades[-5:]])
        else:
            paper = dict(trades=0, return_pct=0.0, closed=[])
    return dict(version_id=v["id"], symbol=v["symbol"], interval=v["interval"], position=name, last_closed_bar=setup["fmt"](df.index[-1]), last_change_bar=setup["fmt"](df.index[last_chg]),
                bars_in_position=int(len(pos) - last_chg), price=q, paper=paper, started_at=started_at,
                note="Paper watch only: the strategy is re-run on the latest CLOSED candles. Nothing is ordered. A new position would be entered at the next candle's open.")
