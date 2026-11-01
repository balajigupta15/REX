import json, os, numpy as np
H = os.path.dirname(os.path.abspath(__file__))
rows = [r for r in json.load(open(os.path.join(H, "oos_refine_results.json"))) if "skipped" not in r]
def agg(sel, name):
    if not sel: return None
    f = lambda k, who: np.array([r[who][k] for r in sel])
    ign = [r["refined"]["ret"] for r in sel]; orig = [r["orig"]["ret"] for r in sel]
    fol = [(r["refined"] if r["flag"] == "ok" else r["orig"])["ret"] for r in sel]
    ign_d = [r["refined"]["mdd"] for r in sel]; orig_d = [r["orig"]["mdd"] for r in sel]; fol_d = [(r["refined"] if r["flag"] == "ok" else r["orig"])["mdd"] for r in sel]
    return dict(group=name, cases=len(sel), original_avg_ret=round(float(np.mean(orig)) * 100, 2), refined_always_avg_ret=round(float(np.mean(ign)) * 100, 2),
                follow_rex_avg_ret=round(float(np.mean(fol)) * 100, 2), buy_hold_avg_ret=round(float(np.mean([r["orig"]["bh"] for r in sel])) * 100, 2),
                original_avg_mdd=round(float(np.mean(orig_d)) * 100, 2), refined_always_avg_mdd=round(float(np.mean(ign_d)) * 100, 2), follow_rex_avg_mdd=round(float(np.mean(fol_d)) * 100, 2),
                refined_beats_original=int(sum(a > b for a, b in zip(ign, orig))), refined_worse_than_original=int(sum(a < b for a, b in zip(ign, orig))))
chg = [r for r in rows if r["changed"]]
out = [agg(rows, "all cases"), agg(chg, "refine changed the rules"), agg([r for r in chg if r["flag"] == "overfit"], "flagged OVERFIT"),
       agg([r for r in chg if r["flag"] == "ok"], "flagged held-up (ok)"), agg([r for r in chg if r["flag"] == "inconclusive"], "flagged inconclusive"),
       agg([r for r in rows if r["cut"] == "2024-04-30"], "cut 2024-04-30 (30 mo forward)"), agg([r for r in rows if r["cut"] == "2025-04-30"], "cut 2025-04-30 (18 mo forward)")]
json.dump(out, open(os.path.join(H, "oos_refine_summary.json"), "w"), indent=1)
print(len(rows), "cases;", len(chg), "changed; flags among changed:", {f: sum(r["flag"] == f for r in chg) for f in ("overfit", "ok", "inconclusive")})
for o in out:
    if o: print(o)
# overfit: did locked-year verdict agree with forward?  (flag overfit => refined worse fwd than original?)
for fl in ("overfit", "ok", "inconclusive"):
    s = [r for r in chg if r["flag"] == fl]
    if s: print(fl, "refined fwd < orig fwd in", sum(r["refined"]["ret"] < r["orig"]["ret"] for r in s), "of", len(s), "| refined fwd return negative in", sum(r["refined"]["ret"] < 0 for r in s))
