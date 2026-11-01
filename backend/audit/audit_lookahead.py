"""No-lookahead audit (truncation / future-perturbation invariance) over 10 tickers x 4 templates x several cut points.
For a cut m: (1) entry signals on bars <= m computed from df[:m+1] must equal those computed from the full frame;
(2) the same after the bars AFTER m are replaced with wild random prices; (3) every trade that has fully exited by bar m in the full run
must be identical (dates, prices, return, reason) in a run that only ever saw bars <= m (apart from a trade clipped by the window end);
(4) every trade entry is at the Open of the bar right after a signal bar."""
import sys, json, itertools
sys.path.insert(0, __file__.rsplit("/", 2)[0])
import numpy as np
from app import backtest, rules
import ref_backtest as ref
CACHE = "/workspace/rexlite/backend/data_cache/"
TICKERS = ["RELIANCE", "JIOFIN", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT", "BHARTIARTL"]
PARAMS = {"breakout": dict(n=20), "ma_cross": dict(fast=10, slow=50), "rsi": dict(period=14, level=30, mode="oversold"), "mean_reversion": dict(n=20, z=2.0)}
EX = dict(hold_days=10, stop_pct=5.0, tp_pct=10.0)
rng = np.random.default_rng(7)
checks = dict(signal_prefix=0, signal_future_wrecked=0, trade_prefix=0, entry_next_open=0); bad = []
for t, tpl in itertools.product(TICKERS, PARAMS):
    df = ref.load_csv(CACHE + t + ".NS.csv"); n = len(df); p = PARAMS[tpl]
    full_sig = rules.signals(df, tpl, p)
    full = backtest.run(df, tpl, p, EX, windows={"w": (100, n - 1)}, keep_series=False)
    ft = backtest.run(df, tpl, p, EX, windows={"w": (100, n - 1)}, keep_series=True)["w"]["trades"]
    for m in sorted(rng.integers(150, n - 30, 6)):
        m = int(m)
        # (1) prefix
        s_cut = rules.signals(df.iloc[:m + 1], tpl, p)
        checks["signal_prefix"] += 1
        if not np.array_equal(s_cut, full_sig[:m + 1]): bad.append((t, tpl, m, "signal prefix"))
        # (2) wreck the future
        d2 = df.copy()
        for col in ("Open", "High", "Low", "Close"):
            d2.iloc[m + 1:, d2.columns.get_loc(col)] = d2[col].iloc[m + 1:].to_numpy() * rng.uniform(0.3, 3, n - m - 1)
        s2 = rules.signals(d2, tpl, p)
        checks["signal_future_wrecked"] += 1
        if not np.array_equal(s2[:m + 1], full_sig[:m + 1]): bad.append((t, tpl, m, "signal changed by future prices"))
        # (3) trades fully exited by m are identical when the run never saw later bars
        cut = df.iloc[:m + 1]
        ct = backtest.run(cut, tpl, p, EX, windows={"w": (100, m)}, keep_series=True)["w"]["trades"]
        ct = [x for x in ct if x["reason"] != "window_end"]
        idx = {str(d.date()): i for i, d in enumerate(df.index)}
        fx = [x for x in ft if idx[x["exit_date"]] <= m]
        checks["trade_prefix"] += 1
        key = lambda x: (x["entry_date"], x["exit_date"], x["entry"], x["exit"], x["return_pct"], x["reason"])
        if [key(x) for x in ct] != [key(x) for x in fx if x["reason"] != "window_end"]: bad.append((t, tpl, m, "trade prefix", len(ct), len(fx)))
    # (4) entry is next bar's open after a signal bar
    for x in ft:
        e = idx[x["entry_date"]]; checks["entry_next_open"] += 1
        if not full_sig[e - 1] or abs(df["Open"].iloc[e] - x["entry"]) > 0.006: bad.append((t, tpl, x["entry_date"], "entry not next open"))
print(json.dumps(dict(checks=checks, mismatches=len(bad), detail=bad[:10]), indent=1, default=str))
json.dump(dict(checks=checks, mismatches=len(bad)), open(__file__.rsplit("/", 1)[0] + "/audit_lookahead_results.json", "w"))
