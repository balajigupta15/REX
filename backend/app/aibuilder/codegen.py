"""Prompts and parsing for model-written strategy code (Gemini). The model writes `signals(df)`; nothing it writes is trusted: the caller runs
ast_guard -> sandbox -> look-ahead check on it. This module only builds prompts, calls gemini.generate_json and shapes the answer."""
from __future__ import annotations
from . import ast_guard, gemini

CONTRACT = """You write trading-strategy code as ONE python function for a backtester. Rules (a checker rejects anything else and you will be asked to fix it):
- Define `def signals(df):` returning a pandas Series (same index as df) of POSITIONS: 1 = long, 0 = flat, -1 = short. {short_rule}
- df has lowercase columns: open, high, low, close, volume. The index is time, oldest first. `pd` (pandas) and `np` (numpy) are already available. NO imports.
- A position on bar t is decided from data up to and including bar t's CLOSE only; the backtester enters at the NEXT bar's open. NEVER look ahead: no shift(-n), no centred rolling windows, no whole-series statistics (df.mean(), global max, quantile of all data), no reading future bars. A checker re-runs your function on truncated data and rejects any difference.
- Allowed: arithmetic, comparisons, & | ~, rolling(...).mean/std/min/max/sum/median, ewm(...).mean/std, shift(positive), diff, pct_change, clip, where, fillna, cummax, cumsum, rank, np.where/np.select/np.sign/np.log/np.sqrt/np.maximum/np.minimum/np.clip, abs/min/max/len/range. For loops only over range/enumerate/zip. No while, try, class, lambda-heavy tricks, f-strings, print, open, getattr, dunder names, string-heavy code.
- Compute indicators yourself (RSI, ATR, MACD, Bollinger, Donchian...) from OHLCV with pandas. Keep code under ~60 lines and vectorised (5 second limit).
- To hold a position between an entry and an exit signal, build `np.where(enter, 1.0, np.where(leave, 0.0, np.nan))`, wrap in pd.Series(..., index=df.index), then .ffill().fillna(0).
- Stops / take-profit / maximum holding time are NOT written in code: put them in the JSON field `exits` (percent numbers; hold_bars is a bar count).
Return ONLY JSON: {{"title": short name, "explanation": 2-4 plain-English sentences of the idea and its main risk, "rules": [3-6 short plain-English rule bullets], "exits": {{"stop_pct": number|null, "tp_pct": number|null, "hold_bars": integer|null}}, "code": "def signals(df): ..."}}"""


def system_prompt(allow_short: bool) -> str:
    return CONTRACT.format(short_rule="Shorting is allowed on this market." if allow_short else "This market is long-only: return only 1 or 0 (never -1).")


def context_text(setup: dict, extra: str = "") -> str:
    df = setup["df"]
    return (f"Market: {setup['market']}  Symbol: {setup['symbol']}  Candles: {setup['interval']}  Bars: {len(df)} ({df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}).\n"
            f"The last part of the history is a LOCKED final period you never see results from while designing. Costs: {setup['cost_label']}. {extra}")


def _shape(obj: dict) -> dict:
    code = obj.get("code")
    if not isinstance(code, str) or "def signals" not in code:
        raise ValueError("The reply had no `code` containing def signals(df)")
    ex = obj.get("exits") if isinstance(obj.get("exits"), dict) else {}
    rules = obj.get("rules") if isinstance(obj.get("rules"), list) else []
    return dict(title=str(obj.get("title") or "Strategy")[:80], explanation=str(obj.get("explanation") or "")[:900], rules=[str(r)[:200] for r in rules][:8],
                exits=dict(stop_pct=ex.get("stop_pct"), tp_pct=ex.get("tp_pct"), hold_bars=ex.get("hold_bars")), code=code.strip("\n") + "\n")


def write(setup: dict, brief: str, prior: dict | None = None, error: str | None = None, history: str = "", temperature: float = 0.3) -> tuple[dict, dict]:
    """One Gemini call that writes (or edits) a strategy. prior = dict(code, exits, title). error = what went wrong with the previous attempt."""
    parts = [context_text(setup), f"Request: {brief}"]
    if history:
        parts.append(f"Context from earlier work:\n{history}")
    if prior:
        parts.append(f"EDIT this existing strategy (keep what the request does not touch). Current exits: {prior.get('exits')}.\n```python\n{prior['code']}\n```")
    if error:
        parts.append(f"Your previous attempt FAILED with this error. Fix it and return the full JSON again:\n{error}")
    obj, meta = gemini.generate_json(system_prompt(setup["allow_short"]), "\n\n".join(parts), tag="write", temperature=temperature, use_cache=error is None)
    return _shape(obj), meta


PLAN_SYS = """You are the planner of an autonomous quant research agent. Given a goal and market facts, propose 2 or 3 genuinely different strategy IDEAS to write and test (e.g. a trend idea, a mean-reversion idea, a volatility or volume idea), tuned to the market and candle size.
The agent will then write code for each idea, backtest it on a train period, check it on a locked final period, compare honestly and pick one, or report that nothing held up.
Return ONLY JSON: {"thesis": one sentence on what kind of edge is plausible here and what the main overfit risk is, "ideas": [{"name": short, "brief": 2-3 sentences telling a coder exactly what entry/exit logic to write, in plain English}]}"""


def plan(setup: dict, goal: str, memory_note: str = "", news_note: str = "") -> tuple[dict, dict]:
    user = f"{context_text(setup)}\nGoal: {goal}\n"
    if memory_note:
        user += f"What earlier runs on this market found: {memory_note}\n"
    if news_note:
        user += f"Recent headlines (context only; do not trade on them): {news_note}\n"
    obj, meta = gemini.generate_json(PLAN_SYS, user, tag="plan", temperature=0.4)
    ideas = [i for i in (obj.get("ideas") or []) if isinstance(i, dict) and i.get("brief")][:3]
    if not ideas:
        raise gemini.GeminiUnavailable("bad_response", "The plan contained no ideas")
    return dict(thesis=str(obj.get("thesis") or "")[:300], ideas=[dict(name=str(i.get("name") or f"Idea {n + 1}")[:60], brief=str(i["brief"])[:600]) for n, i in enumerate(ideas)]), meta
