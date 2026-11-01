"""Challenger: a rule-based devil's advocate for a proposed trade. No LLM.

It only uses (a) the user's own closed round trips and (b) the market context of the proposed entry (prior-close fingerprint).
It collects arguments AGAINST the trade, each with a fixed weight, and can veto ("don't trade") when the total is high enough.
"""
from __future__ import annotations

W_STRONG, W_WEAK = 2, 1
VETO_SCORE = 5
RULES = [
    "matched repeat-mistake pattern (n>=8, win rate 20+ points below your average): strong (3)",
    "same-symbol history: 3+ closed trades with win rate <=40%: weak; 5+ trades with win rate <=30%: strong",
    "recent form: 4 of your last 5 closed trades in this direction lost: strong; 3 of 5: weak",
    "stressed volatility: weak; strong if 4+ past trades in that regime won 10+ points below your average",
    "stretched entry (>5% above the 20-day average) or gap-up chase: weak; strong if 4+ similar past trades won <=35%",
    "you hold losers much longer than winners: weak",
    "worst similar past loss of 8% or more: weak",
    "fewer than 5 similar past trades: weak (no evidence the setup works for you)",
    f"veto when the weights add up to {VETO_SCORE}+ (strong=2, weak=1, repeat-mistake pattern=3)",
]


def _wr(rows):
    return (sum(1 for r in rows if r["pnl"] > 0) / len(rows)) if rows else None


def build(out: dict, rows: list[dict], ticker: str) -> dict:
    tags = out["fingerprint"]["tags"]
    base_wr = _wr(rows)
    args: list[tuple[int, str]] = []   # (weight, text)
    n = len(rows)
    sim = (out.get("similar") or {}).get("stats")
    mp = out.get("mistake_pattern") or {}
    m = mp.get("matched")
    if m and m["n"] >= 8 and m["win_rate"] <= m["base_win_rate"] - 0.20:
        args.append((3, f"This entry matches a repeat mistake of yours: {m['text']}"))
    elif m:
        args.append((W_WEAK, f"This entry matches one of your weaker patterns ({m['n']} trades, win rate {m['win_rate']*100:.0f}% vs {m['base_win_rate']*100:.0f}%)."))
    same = [r for r in rows if r["ticker"] == ticker]
    if len(same) >= 3:
        wr = _wr(same)
        if wr <= 0.40:
            tot = sum(r["pnl"] for r in same)
            args.append((W_STRONG if len(same) >= 5 and wr <= 0.30 else W_WEAK,
                         f"Your record in {ticker.replace('.NS', '')}: {len(same)} closed trades, win rate {wr*100:.0f}%, total P&L {tot:+,.0f}."))
    recent = sorted(rows, key=lambda r: r["exit_date"])[-5:]
    if len(recent) == 5:
        lost = sum(1 for r in recent if r["pnl"] <= 0)
        if lost >= 3:
            args.append((W_STRONG if lost >= 4 else W_WEAK, f"{lost} of your last 5 closed trades lost money: recent form is poor, a loss streak is when size and discipline slip."))
    if tags.get("volatility") == "stressed":
        reg = [r for r in rows if r["tags"].get("volatility") == "stressed"]
        if len(reg) >= 4 and base_wr is not None and _wr(reg) <= base_wr - 0.10:
            args.append((W_STRONG, f"Volatility is stressed and your {len(reg)} past trades in that regime won {_wr(reg)*100:.0f}% vs {base_wr*100:.0f}% overall."))
        else:
            args.append((W_WEAK, "Volatility is in a stressed regime (20-day volatility well above its 1-year norm): stops get hit more often."))
    chase = tags.get("extension") == "stretched" or tags.get("gap") == "gap_up"
    if chase:
        what = "stretched more than 5% above its 20-day average" if tags.get("extension") == "stretched" else "gapping up at the open"
        sub = [r for r in rows if (r["tags"].get("extension") == "stretched" and tags.get("extension") == "stretched") or (r["tags"].get("gap") == "gap_up" and tags.get("gap") == "gap_up")]
        if len(sub) >= 4 and _wr(sub) <= 0.35:
            args.append((W_STRONG, f"Chasing: the stock is {what}, and your {len(sub)} similar chases won only {_wr(sub)*100:.0f}%."))
        else:
            args.append((W_WEAK, f"Possible chase: the stock is {what}; late entries leave less room before a pullback."))
    hb = mp.get("hold_behaviour")
    if hb and hb["kind"] == "holds_losers":
        args.append((W_WEAK, f"You hold losers {hb['avg_hold_losers']} days vs {hb['avg_hold_winners']} for winners: without a pre-set stop, a wrong entry tends to get worse."))
    if sim and sim["n"] >= 3 and sim.get("avg_loss_pct") is not None:
        worst = min((x["return_pct"] for x in (out["similar"]["neighbors"] or [])), default=0)
        if worst <= -8:
            args.append((W_WEAK, f"The worst similar past trade lost {abs(worst):.1f}%: size so that a loss like that is survivable."))
    if sim is None or sim["n"] < 5:
        args.append((W_WEAK, "Fewer than 5 similar past trades: there is no evidence in your history that this setup works for you."))
    args.sort(key=lambda a: -a[0])
    score = sum(w for w, _ in args)
    severity = "strong" if score >= VETO_SCORE else ("weak" if score >= 1 else "none")
    veto = score >= VETO_SCORE
    action = out["recommendation"]["action"]
    final = "wait" if veto else action
    if veto:
        verdict = f"Veto: the case against adds up to {score} (veto threshold {VETO_SCORE}). Don't take this trade now; revisit when the setup changes."
    elif score:
        verdict = f"Objections add up to {score} (below the veto threshold of {VETO_SCORE}); they do not change the recommendation, but size accordingly."
    else:
        verdict = "No data-backed objection found. That is not a reason to skip your stop."
    return dict(veto=veto, severity=severity, score=score, veto_threshold=VETO_SCORE,
                arguments=[dict(weight="strong" if w >= 2 else "weak", points=w, text=t) for w, t in args],
                verdict=verdict, final_action=final, overrides_recommendation=final != action, rules=RULES)
