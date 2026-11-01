"""Compare Rex's /backtest against the independent reference (ref_backtest.py) on many runs. Usage: python audit_backtest.py http://127.0.0.1:8003"""
import json, sys, itertools, urllib.request
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import ref_backtest as ref

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8003"
CACHE = "/workspace/rexlite/backend/data_cache/"
TICKERS = ["RELIANCE", "JIOFIN", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT", "BHARTIARTL"]
PARAMS = {"breakout": dict(n=20), "ma_cross": dict(fast=10, slow=50), "rsi": dict(period=14, level=30, mode="oversold"), "mean_reversion": dict(n=20, z=2.0)}
ALT = {"breakout": dict(n=55), "ma_cross": dict(fast=5, slow=20), "rsi": dict(period=7, level=70, mode="overbought"), "mean_reversion": dict(n=10, z=1.0)}
EXITS = dict(hold_days=10, stop_pct=5.0, tp_pct=10.0)


def post(path, body):
    req = urllib.request.Request(API + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=300)), 200
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}"), e.code


runs = []
for t, tpl in itertools.product(TICKERS, PARAMS):                              # 40 default runs
    runs.append((t, tpl, PARAMS[tpl], EXITS, 15.0, "default"))
for i, (t, tpl) in enumerate(itertools.product(TICKERS, PARAMS)):              # 40 varied runs: alt params + exit variants + cost variants
    ex = [dict(hold_days=1, stop_pct=5.0, tp_pct=10.0), dict(hold_days=120, stop_pct=None, tp_pct=None), dict(hold_days=10, stop_pct=0.5, tp_pct=0.5),
          dict(hold_days=30, stop_pct=30, tp_pct=100), dict(hold_days=10, stop_pct=None, tp_pct=8), dict(hold_days=15, stop_pct=3, tp_pct=None)][i % 6]
    runs.append((t, tpl, ALT[tpl], ex, [0.0, 15.0, 100.0, 500.0][i % 4], "varied"))

TOL = dict(total_return=2e-4, max_drawdown=2e-4, sharpe=2e-3, win_rate=1e-9)
bad, n_trades, n_checked = [], 0, 0
rows = []
for t, tpl, p, ex, cost, kind in runs:
    body = dict(ticker=t, template=tpl, params=p, exits=ex, cost_bps=cost, remember=False)
    r, code = post("/backtest", body)
    if code != 200:
        bad.append((t, tpl, kind, "HTTP", code, r)); continue
    df = ref.load_csv(CACHE + t + ".NS.csv")
    R = ref.backtest(df, tpl, p, ex, cost)
    for w in ("train", "locked"):
        A, B = r["result"][w], R[w]
        # window dates
        if (A["start"], A["end"]) != (B["start"], B["end"]): bad.append((t, tpl, kind, w, "window", A["start"], A["end"], B["start"], B["end"]))
        # trade list: dates, prices, returns, reasons
        at = A["trades"]; bt = B["trades"]
        if len(at) != len(bt): bad.append((t, tpl, kind, w, "trade count", len(at), len(bt))); continue
        for x, y in zip(at, bt):
            n_trades += 1
            ed, xd = str(df.index[y["entry_idx"]].date()), str(df.index[y["exit_idx"]].date())
            if (x["entry_date"], x["exit_date"], x["reason"]) != (ed, xd, y["reason"]): bad.append((t, tpl, kind, w, "trade dates/reason", x["entry_date"], x["exit_date"], x["reason"], ed, xd, y["reason"]))
            if abs(x["entry"] - y["entry"]) > 0.006 or abs(x["exit"] - y["exit"]) > 0.006: bad.append((t, tpl, kind, w, "trade px", x["entry"], x["exit"], y["entry"], y["exit"]))
            if abs(x["return_pct"] - y["ret"] * 100) > 0.0051: bad.append((t, tpl, kind, w, "trade ret", x["return_pct"], y["ret"] * 100))
        for m, tol in TOL.items():
            a, b = A["metrics"][m], B["metrics"][m]
            n_checked += 1
            if (a is None) != (b is None) or (a is not None and abs(a - b) > tol): bad.append((t, tpl, kind, w, m, a, b))
        for m in ("total_return", "max_drawdown", "sharpe"):
            n_checked += 1
            if abs(A["benchmark"][m] - B["benchmark"][m]) > TOL[m]: bad.append((t, tpl, kind, w, "buy&hold " + m, A["benchmark"][m], B["benchmark"][m]))
        if A["metrics"]["trades"] != B["metrics"]["trades"]: bad.append((t, tpl, kind, w, "metrics.trades"))
    rows.append((t, tpl, kind, r["result"]["locked"]["metrics"]["trades"], round(r["result"]["locked"]["metrics"]["total_return"] * 100, 2), round(R["locked"]["metrics"]["total_return"] * 100, 2)))
summary = dict(runs=len(runs), trades_compared=n_trades, metric_comparisons=n_checked, mismatches=len(bad), mismatch_detail=bad[:30])
print(json.dumps(summary, indent=1, default=str))
json.dump(dict(summary=summary, rows=rows), open(__file__.rsplit("/", 1)[0] + "/audit_backtest_results.json", "w"), default=str)
