"""Memory audit on the SHIPPED database: every replayed strategy-run row (real past run re-computed as of an earlier date) is re-derived with the
independent reference backtester using ONLY data up to its date, and the stored test return / train return / trades / Sharpe / verdict / forward 5- and
20-day price moves / headline numbers are compared. Read-only."""
import sqlite3, json, sys, re
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import ref_backtest as ref
DB = __file__.rsplit("/", 2)[0] + "/rexlite_memory.sqlite"; CACHE = __file__.rsplit("/", 2)[0] + "/data_cache/"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("select * from strategy_runs where source='replay'")]
bad, n, nf = [], 0, 0
for r in rows:
    df = ref.load_csv(CACHE + r["ticker"] + ".csv"); anchor = df.index.searchsorted(__import__("pandas").Timestamp(r["anchor_date"]), side="right") - 1
    sub = df.iloc[:anchor + 1]; p, e, st = json.loads(r["params"]), json.loads(r["exits"]), json.loads(r["stats"])
    R = ref.backtest(sub, r["template"], p, e, r["cost_bps"]); tr, lk = R["train"]["metrics"], R["locked"]["metrics"]
    n += 1
    chk = {"train_return": round(tr["total_return"] * 100, 2), "test_return": round(lk["total_return"] * 100, 2), "test_sharpe": round(lk["sharpe"], 2),
           "train_sharpe": round(tr["sharpe"], 2), "test_trades": lk["trades"], "buy_hold_test": round(R["locked"]["benchmark"]["total_return"] * 100, 2),
           "test_start": R["locked"]["start"], "test_end": R["locked"]["end"]}
    for k, v in chk.items():
        a = st[k]
        if (isinstance(v, str) and a != v) or (not isinstance(v, str) and abs(a - v) > (0.011 if "return" in k or "hold" in k else 0.011 if "sharpe" in k else 0)):
            bad.append((r["id"], k, a, v))
    if r["kind"] == "backtest":
        exp = "INCONCLUSIVE" if lk["trades"] < 3 else ("FADED" if tr["sharpe"] > 0.5 and lk["sharpe"] < 0 else ("WORKED" if lk["total_return"] > 0 else "LOST"))
        if exp != r["verdict"]: bad.append((r["id"], "verdict", r["verdict"], exp))
    c = df["Close"].to_numpy(float)
    for k, f in ((5, "fwd5"), (20, "fwd20")):
        want = round((c[anchor + k] / c[anchor] - 1) * 100, 2) if anchor + k < len(c) else None; nf += 1
        if (r[f] is None) != (want is None) or (want is not None and abs(r[f] - want) > 0.011): bad.append((r["id"], f, r[f], want))
    m = re.search(r"price then moved ([+-][\d.]+)% in 20 days", r["headline"] or "")
    if r["fwd20"] is not None and (not m or abs(float(m.group(1)) - r["fwd20"]) > 0.051): bad.append((r["id"], "headline fwd20", r["headline"]))
    m = re.search(r"test ([+-][\d.]+)%", r["headline"] or "")
    if st["test_return"] is not None and (not m or abs(float(m.group(1)) - st["test_return"]) > 0.051): bad.append((r["id"], "headline test", r["headline"]))
out = dict(replay_rows=n, fields_compared=n * 8 + nf, forward_values_compared=nf, mismatches=len(bad), detail=bad[:12])
print(json.dumps(out, indent=1, default=str)); json.dump(out, open(__file__.rsplit("/", 1)[0] + "/audit_memory_results.json", "w"), default=str)
