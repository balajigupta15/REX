"""Refinement = grid search of NEARBY parameters on the TRAIN window only; the locked final window is
evaluated exactly once for the original and once for the chosen candidate. Never used to choose."""
from __future__ import annotations
import itertools
import numpy as np
import pandas as pd
from . import backtest, config, rules
from .data import split_index

MULTS = [0.6, 0.8, 1.0, 1.25, 1.5]
EXIT_MULTS = {"hold_days": [0.5, 1.0, 1.5, 2.0], "stop_pct": [0.5, 0.75, 1.0, 1.5, 2.0], "tp_pct": [0.5, 0.75, 1.0, 1.5, 2.0]}
MAX_TRIALS = 400
MIN_TRAIN_TRADES = 10


def _near(p: dict, v, mults):
    vals = set()
    for m in mults:
        x = v * m
        x = int(round(x)) if p["type"] == "int" else round(x, 2)
        x = min(max(x, p["min"]), p["max"])
        vals.add(x)
    return sorted(vals)


def candidates(template: str, params: dict, exits: dict, seed: int = 0) -> list[tuple[dict, dict]]:
    axes = []  # (kind, name, values)
    for p in rules.TEMPLATES[template]["params"]:
        if p["type"] in ("int", "float"):
            axes.append(("p", p["name"], _near(p, params[p["name"]], MULTS)))
        else:
            axes.append(("p", p["name"], [params[p["name"]]]))
    for p in rules.EXIT_PARAMS:
        v = exits.get(p["name"])
        axes.append(("e", p["name"], [None] if v is None else _near(p, v, EXIT_MULTS[p["name"]])))
    combos = []
    for vals in itertools.product(*[a[2] for a in axes]):
        pp, ee = dict(params), dict(exits)
        for (kind, name, _), v in zip(axes, vals):
            (pp if kind == "p" else ee)[name] = v
        if template == "ma_cross" and pp["fast"] >= pp["slow"]:
            continue
        combos.append((pp, ee))
    orig = (dict(params), dict(exits))
    combos = [c for c in combos if c != orig]
    if len(combos) > MAX_TRIALS - 1:
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(combos), MAX_TRIALS - 1, replace=False)
        combos = [combos[i] for i in sorted(idx)]
    return [orig] + combos


def _verdict(orig_train, orig_lock, ref_train, ref_lock, same: bool, n_trials: int, n_eligible: int) -> dict:
    notes, flag = [], "ok"
    if n_eligible == 0:      # nothing to choose from: do NOT claim the original was "the best of the grid"
        return dict(flag="inconclusive", overfit=False,
                    headline=f"Not enough trades to tune: none of the {n_trials} nearby combinations had {MIN_TRAIN_TRADES}+ training trades, so your rules were kept as they are.",
                    notes=[f"0 of {n_trials} candidates had at least {MIN_TRAIN_TRADES} training trades; with so few trades any 'best' setting would be luck.",
                           "Try a different stock or a looser entry rule (for example a shorter lookback) so the strategy trades more often."])
    if same:
        return dict(flag="no_change", overfit=False, headline="Your original parameters were already the best of the nearby grid on training data.",
                    notes=[f"{n_eligible} of {n_trials} candidates had at least {MIN_TRAIN_TRADES} training trades."])
    ts, vs = ref_train["sharpe"], ref_lock["sharpe"]
    vt = ref_lock["trades"]
    if vt < 3:
        flag = "inconclusive"
        notes.append(f"Only {vt} trade(s) in the locked year, so the validation tells you very little.")
    collapsed = ts > 0 and (ref_lock["total_return"] < 0 or vs < 0.5 * ts)
    if collapsed:
        flag = "overfit"
        notes.append(f"Refined Sharpe fell from {ts:.2f} (train) to {vs:.2f} (locked year): the tuned parameters did not hold up.")
    worse = ref_lock["total_return"] < orig_lock["total_return"]
    if worse:
        notes.append(f"On the locked year the refined version returned {ref_lock['total_return']*100:+.1f}% vs {orig_lock['total_return']*100:+.1f}% for your original: refinement did not help out of sample.")
    else:
        notes.append(f"On the locked year the refined version returned {ref_lock['total_return']*100:+.1f}% vs {orig_lock['total_return']*100:+.1f}% for your original (small samples can flatter either version).")
    if flag == "ok":
        notes.append("Holding up on one locked year is encouraging, not proof: in Rex's own out-of-sample tests (docs/PERSONA_QA.md) 'held up' refinements were not reliably better than the original.")
    notes.append(f"Best of {n_eligible} eligible candidates ({n_trials} combinations tried; eligible = at least {MIN_TRAIN_TRADES} training trades); picking the best of many looks better than it is (multiple testing).")
    head = {"overfit": "Overfit warning: the refinement looked good in training but collapsed on the locked final year.",
            "inconclusive": "Inconclusive: too few trades in the locked year to judge the refinement.",
            "ok": ("Refinement held up on the locked year." if not worse else "Refinement did not beat your original out of sample.")}[flag]
    return dict(flag=flag, overfit=flag == "overfit", headline=head, notes=notes)


def refine(df: pd.DataFrame, template: str, params: dict, exits: dict, cost_bps: float | None = None, seed: int = 0,
           split_k: int | None = None, periods: float = backtest.TRADING_DAYS, fmt=None, cost_legs=None) -> dict:
    """`split_k` (first bar of the locked period), `periods` and `fmt` default to the NSE daily behaviour; the crypto market passes its own."""
    k = split_index(df) if split_k is None else int(split_k)
    s = config.WARMUP_BARS
    train_w = {"train": (s, k - 1)}
    wins = {"train": (s, k - 1), "locked": (k, len(df) - 1)}      # identical to backtest.run's default windows
    cands = candidates(template, params, exits, seed)
    rows, rets = [], []
    for pp, ee in cands:
        sink = {}
        r = backtest.run(df, template, pp, ee, cost_bps, windows=train_w, keep_series=False, periods=periods, fmt=fmt, cost_legs=cost_legs, ret_sink=sink)["train"]["metrics"]
        rets.append(sink["r"])
        rows.append(dict(params=pp, exits=ee, train_sharpe=r["sharpe"], train_return=r["total_return"], train_trades=r["trades"],
                         train_win_rate=r["win_rate"], train_max_drawdown=r["max_drawdown"]))
    eligible = [r for r in rows if r["train_trades"] >= MIN_TRAIN_TRADES]
    chosen_row = max(eligible, key=lambda r: (r["train_sharpe"], r["train_return"])) if eligible else rows[0]
    same = (chosen_row["params"], chosen_row["exits"]) == (params, exits)
    full_orig = backtest.run(df, template, params, exits, cost_bps, windows=wins, periods=periods, fmt=fmt, cost_legs=cost_legs)           # locked year evaluated ONCE per version
    full_ref = full_orig if same else backtest.run(df, template, chosen_row["params"], chosen_row["exits"], cost_bps, windows=wins, periods=periods, fmt=fmt, cost_legs=cost_legs)
    top = sorted(eligible, key=lambda r: -r["train_sharpe"])[:10]
    pos = float(np.mean([r["train_sharpe"] > 0 for r in eligible])) if eligible else 0.0
    v = _verdict(full_orig["train"]["metrics"], full_orig["locked"]["metrics"], full_ref["train"]["metrics"], full_ref["locked"]["metrics"],
                 same, len(rows), len(eligible))
    stats = _overfit_stats(rows, rets, eligible, chosen_row, periods)
    return dict(
        overfit_stats=stats,
        original=dict(params=params, exits=exits, result=full_orig),
        refined=dict(params=chosen_row["params"], exits=chosen_row["exits"], result=full_ref, changed=not same),
        verdict=v,
        search=dict(trials=len(rows), eligible=len(eligible), min_train_trades=MIN_TRAIN_TRADES, objective="train Sharpe",
                    tuned_on=full_orig["train"]["start"] + " to " + full_orig["train"]["end"],
                    locked=full_orig["locked"]["start"] + " to " + full_orig["locked"]["end"] + " (never used for tuning)",
                    share_of_candidates_with_positive_train_sharpe=pos, top_candidates=top),
    )


def _overfit_stats(rows, rets, eligible, chosen_row, periods) -> dict:
    """PBO (CSCV) and Deflated Sharpe over the candidate grid, on the TRAIN window only. Never raises: a failure is reported as unavailable."""
    from . import quant
    out = {}
    el_idx = [i for i, r in enumerate(rows) if r["train_trades"] >= MIN_TRAIN_TRADES]
    try:
        if len(el_idx) < 2:
            raise ValueError(f"needs at least 2 candidates with {MIN_TRAIN_TRADES}+ training trades (had {len(el_idx)})")
        M = np.column_stack([rets[i] for i in el_idx])
        p = quant.pbo(M)
        lvl, words = quant.pbo_words(p["pbo"], len(el_idx))
        out["pbo"] = dict(p, level=lvl, words=words, available=True)
    except Exception as e:
        out["pbo"] = dict(available=False, reason=str(e))
    try:
        ci = next(i for i, r in enumerate(rows) if r["params"] == chosen_row["params"] and r["exits"] == chosen_row["exits"])
        sh = []
        for i in el_idx:
            x = rets[i]; sd = x.std(ddof=1)
            sh.append(float(x.mean() / sd) if sd > 1e-12 else 0.0)
        out["dsr"] = dict(quant.dsr_report(rets[ci], sh, len(rows), periods), available=True)
    except Exception as e:
        out["dsr"] = dict(available=False, reason=str(e))
    return out
