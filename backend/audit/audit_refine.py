"""Refine audit across 10 tickers x 4 templates through /refine: top candidates re-scored by the independent reference, chosen = best train Sharpe,
refined/original locked results match the reference, counts consistent, locked year invariance (prices in the locked year perturbed in-process)."""
import json, sys, itertools, urllib.request
sys.path.insert(0, __file__.rsplit("/", 1)[0]); sys.path.insert(0, __file__.rsplit("/", 2)[0])
import numpy as np, ref_backtest as ref
API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8003"
CACHE = "/workspace/rexlite/backend/data_cache/"
TICKERS = ["RELIANCE", "JIOFIN", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT", "BHARTIARTL"]
PARAMS = {"breakout": dict(n=20), "ma_cross": dict(fast=10, slow=50), "rsi": dict(period=14, level=30, mode="oversold"), "mean_reversion": dict(n=20, z=2.0)}
EX = dict(hold_days=10, stop_pct=5.0, tp_pct=10.0)

def post(path, body):
    req = urllib.request.Request(API + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=600))

bad, flags, rows = [], {}, []
for t, tpl in itertools.product(TICKERS, PARAMS):
    r = post("/refine", dict(ticker=t, template=tpl, params=PARAMS[tpl], exits=EX, remember=False))
    df = ref.load_csv(CACHE + t + ".NS.csv"); k = ref.split_k(df)
    s = r["search"]; v = r["verdict"]
    flags[v["flag"]] = flags.get(v["flag"], 0) + 1
    # counts
    if not (s["trials"] >= s["eligible"] >= 0 and s["trials"] <= 400): bad.append((t, tpl, "counts", s["trials"], s["eligible"]))
    bl = [n for n in v["notes"] if n.startswith("Best of")]
    if s["eligible"] > 0 and v["flag"] != "no_change" and (len(bl) != 1 or f"Best of {s['eligible']} eligible" not in bl[0] or f"({s['trials']} combinations" not in bl[0]): bad.append((t, tpl, "bullet", bl))
    # train window must end before locked start
    tr_end, lk_start = r["original"]["result"]["train"]["end"], r["original"]["result"]["locked"]["start"]
    if not tr_end < lk_start or lk_start != str(df.index[k].date()): bad.append((t, tpl, "windows", tr_end, lk_start))
    # top candidates re-scored independently
    top = s["top_candidates"]
    for c in top:
        R = ref.backtest(df, tpl, c["params"], c["exits"], 15.0)["train"]["metrics"]
        if abs(R["sharpe"] - c["train_sharpe"]) > 2e-3 or abs(R["total_return"] - c["train_return"]) > 2e-4 or R["trades"] != c["train_trades"]:
            bad.append((t, tpl, "top candidate train metrics", c["params"], c["exits"], c["train_sharpe"], R["sharpe"]))
        if c["train_trades"] < s["min_train_trades"]: bad.append((t, tpl, "ineligible in top"))
    ch = r["refined"]
    if s["eligible"] == 0:
        if v["flag"] != "inconclusive" or "already the best" in v["headline"]: bad.append((t, tpl, "no eligible but verdict", v["flag"], v["headline"]))
        rows.append((t, tpl, v["flag"], s["trials"], 0, None, None)); continue
    best = max(top, key=lambda c: (c["train_sharpe"], c["train_return"]))
    if not r["refined"]["changed"]:
        if v["flag"] != "no_change": bad.append((t, tpl, "unchanged but flag", v["flag"]))
    else:
        if (best["params"], best["exits"]) != (ch["params"], ch["exits"]): bad.append((t, tpl, "chosen != best train sharpe", best["params"], ch["params"]))
    # refined and original full results vs reference
    for name, node in (("original", r["original"]), ("refined", r["refined"])):
        R = ref.backtest(df, tpl, node["params"], node["exits"], 15.0)
        for w in ("train", "locked"):
            a, b = node["result"][w]["metrics"], R[w]["metrics"]
            if abs(a["total_return"] - b["total_return"]) > 2e-4 or abs(a["sharpe"] - b["sharpe"]) > 2e-3 or a["trades"] != b["trades"]:
                bad.append((t, tpl, name, w, "metrics vs ref", a["total_return"], b["total_return"]))
    # verdict logic recomputed from stated numbers
    ot, ol = r["original"]["result"]["locked"]["metrics"], r["original"]["result"]["train"]["metrics"]
    rt, rl = ch["result"]["train"]["metrics"], ch["result"]["locked"]["metrics"]
    if ch["changed"]:
        exp = "inconclusive" if rl["trades"] < 3 else "ok"
        if rt["sharpe"] > 0 and (rl["total_return"] < 0 or rl["sharpe"] < 0.5 * rt["sharpe"]): exp = "overfit"
        if exp != v["flag"]: bad.append((t, tpl, "flag logic", v["flag"], exp))
    rows.append((t, tpl, v["flag"], s["trials"], s["eligible"], round(rt["sharpe"], 2), round(rl["sharpe"], 2)))

# locked-year invariance: wreck the locked year prices (x3 and noise) in-process, refine must pick identical rules with identical train metrics
from app import refine as app_refine, data as app_data
inv = []
for t, tpl in [("RELIANCE", "breakout"), ("TCS", "ma_cross"), ("SBIN", "rsi"), ("ITC", "mean_reversion"), ("LT", "breakout")]:
    df = ref.load_csv(CACHE + t + ".NS.csv"); k = app_data.split_index(df)
    a = app_refine.refine(df, tpl, PARAMS[tpl], EX)
    d2 = df.copy(); rng = np.random.default_rng(1)
    for col in ("Open", "High", "Low", "Close"):
        d2.iloc[k:, d2.columns.get_loc(col)] = d2[col].iloc[k:].to_numpy() * 3 * (1 + rng.normal(0, 0.05, len(d2) - k)).clip(0.5, 2)
    d2["High"] = d2[["Open", "High", "Low", "Close"]].max(axis=1); d2["Low"] = d2[["Open", "High", "Low", "Close"]].min(axis=1)
    b = app_refine.refine(d2, tpl, PARAMS[tpl], EX)
    same = (a["refined"]["params"], a["refined"]["exits"]) == (b["refined"]["params"], b["refined"]["exits"]) and a["search"]["trials"] == b["search"]["trials"] \
        and a["refined"]["result"]["train"]["metrics"] == b["refined"]["result"]["train"]["metrics"] and a["search"]["top_candidates"] == b["search"]["top_candidates"]
    inv.append((t, tpl, same))
    if not same: bad.append((t, tpl, "locked year changed the tuning"))
summary = dict(combos=len(rows), flags=flags, mismatches=len(bad), locked_invariance=inv, detail=bad[:20])
print(json.dumps(summary, indent=1, default=str))
json.dump(dict(summary=summary, rows=rows), open(__file__.rsplit("/", 1)[0] + "/audit_refine_results.json", "w"), default=str)
