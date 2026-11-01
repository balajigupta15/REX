"""Out-of-sample study: does following Rex's refine verdict beat ignoring it?  Real cached NSE prices only.

For each (ticker, template, cut date) Rex is run *as of the cut date* (data truncated, so tuning and its locked year both end at the cut).
The original and refined rules are then run on the following period, which Rex never saw.
  ignore Rex : always switch to the refined (best-on-training) rules
  follow Rex : switch only if the verdict is 'ok' (held up on the locked year), otherwise keep your original rules
Run: cd backend && REX_OFFLINE=1 python persona_qa/oos_refine_study.py   -> persona_qa/oos_refine_results.json
"""
import json, os, sys, itertools
from concurrent.futures import ProcessPoolExecutor
os.environ.setdefault("REX_OFFLINE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import backtest, config, data, refine, rules

CUTS = ["2024-04-30", "2025-04-30"]
TEMPLATES = ["breakout", "ma_cross", "rsi", "mean_reversion"]


def default_spec(template):
    p = {x["name"]: x["default"] for x in rules.TEMPLATES[template]["params"]}
    e = {x["name"]: x["default"] for x in rules.EXIT_PARAMS}
    return p, e


def fwd(df, template, p, e, idx0):
    r = backtest.run(df, template, p, e, config.COST_BPS_DEFAULT, windows={"fwd": (idx0, len(df) - 1)}, keep_series=False)["fwd"]
    m = r["metrics"]
    return dict(ret=m["total_return"], mdd=m["max_drawdown"], trades=m["trades"], bh=r["benchmark"]["total_return"], bh_mdd=r["benchmark"]["max_drawdown"])


def case(args):
    ticker, template, cut = args
    try:
        df, _ = data.load(ticker)
        idx0 = int(df.index.searchsorted(cut, side="right"))
        hist = df.iloc[:idx0]
        if len(hist) < config.MIN_BARS:
            return dict(ticker=ticker, template=template, cut=cut, skipped="not enough history before the cut")
        p, e = default_spec(template)
        r = refine.refine(hist, template, p, e, config.COST_BPS_DEFAULT)
        o = fwd(df, template, p, e, idx0)
        same = not r["refined"]["changed"]
        f = o if same else fwd(df, template, r["refined"]["params"], r["refined"]["exits"], idx0)
        flag = r["verdict"]["flag"]
        return dict(ticker=ticker, template=template, cut=cut, flag=flag, changed=not same,
                    locked_orig=r["original"]["result"]["locked"]["metrics"]["total_return"],
                    locked_ref=r["refined"]["result"]["locked"]["metrics"]["total_return"],
                    train_sharpe_ref=r["refined"]["result"]["train"]["metrics"]["sharpe"],
                    locked_sharpe_ref=r["refined"]["result"]["locked"]["metrics"]["sharpe"],
                    orig=o, refined=f, fwd_days=len(df) - idx0)
    except Exception as ex:  # noqa
        return dict(ticker=ticker, template=template, cut=cut, skipped=f"{type(ex).__name__}: {ex}")


if __name__ == "__main__":
    jobs = list(itertools.product(config.DEFAULT_TICKERS, TEMPLATES, CUTS))
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(case, jobs))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oos_refine_results.json")
    json.dump(rows, open(out, "w"), indent=1)
    print(len(rows), "cases,", sum(1 for r in rows if "skipped" in r), "skipped ->", out)
