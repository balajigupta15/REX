"""Accuracy audit as regression tests (full versions live in backend/audit/). The reference backtester in audit/ref_backtest.py is written
independently and imports nothing from `app`; these tests compare the app with it, check no-lookahead by truncation, locked-year isolation,
tracker exit/P&L math, memory forward returns, and parser ground truth + fuzz."""
import itertools, random, sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

AUDIT = Path(__file__).resolve().parent.parent / "audit"
sys.path.insert(0, str(AUDIT))
import ref_backtest as ref                      # noqa: E402
from app import backtest, data, nlparse, refine, rules, strategy_memory, tracker   # noqa: E402
from app.main import app                        # noqa: E402

client = TestClient(app)
CACHE = AUDIT.parent / "data_cache"
TICKERS = ["RELIANCE", "JIOFIN", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT", "BHARTIARTL"]
P = {"breakout": dict(n=20), "ma_cross": dict(fast=10, slow=50), "rsi": dict(period=14, level=30, mode="oversold"), "mean_reversion": dict(n=20, z=2.0)}
RUNS = [(t, tpl, ex, c) for (t, tpl), (ex, c) in zip(itertools.product(TICKERS, P), itertools.cycle([
    (dict(hold_days=10, stop_pct=5.0, tp_pct=10.0), 15.0), (dict(hold_days=3, stop_pct=1.0, tp_pct=2.0), 0.0),
    (dict(hold_days=30, stop_pct=None, tp_pct=None), 100.0), (dict(hold_days=1, stop_pct=0.5, tp_pct=None), 500.0), (dict(hold_days=20, stop_pct=None, tp_pct=15.0), 15.0)]))]


def _df(t):
    return ref.load_csv(str(CACHE / f"{t}.NS.csv"))


@pytest.mark.parametrize("t,tpl,ex,cost", RUNS)
def test_engine_equals_independent_reference(t, tpl, ex, cost):
    df = _df(t)
    A = backtest.run(df, tpl, P[tpl], ex, cost)
    R = ref.backtest(df, tpl, P[tpl], ex, cost)
    for w in ("train", "locked"):
        a, r = A[w]["metrics"], R[w]["metrics"]
        assert a["trades"] == r["trades"]
        assert abs(a["total_return"] - r["total_return"]) < 2e-4 and abs(a["sharpe"] - r["sharpe"]) < 2e-3 and abs(a["max_drawdown"] - r["max_drawdown"]) < 2e-4
        if r["trades"]:
            assert abs(a["win_rate"] - r["win_rate"]) < 1e-9
        assert abs(A[w]["benchmark"]["total_return"] - R[w]["benchmark"]["total_return"]) < 2e-4          # buy & hold
        at = [x for x in A[w]["trades"]]
        assert [(x["entry_date"], x["exit_date"], x["reason"]) for x in at] == [(str(df.index[x["entry_idx"]].date()), str(df.index[x["exit_idx"]].date()), x["reason"]) for x in R[w]["trades"]]
        for x, y in zip(at, R[w]["trades"]):
            assert abs(x["return_pct"] - y["ret"] * 100) < 0.011


@pytest.mark.parametrize("t,tpl", [("RELIANCE", "breakout"), ("TCS", "ma_cross"), ("SBIN", "rsi"), ("ITC", "mean_reversion"), ("LT", "rsi")])
def test_no_lookahead_by_truncation_and_future_perturbation(t, tpl):
    df, p, ex = _df(t), P[tpl], dict(hold_days=10, stop_pct=5.0, tp_pct=10.0)
    n, rng = len(df), np.random.default_rng(5)
    full_sig = rules.signals(df, tpl, p)
    ft = backtest.run(df, tpl, p, ex, windows={"w": (100, n - 1)})["w"]["trades"]
    idx = {str(d.date()): i for i, d in enumerate(df.index)}
    for m in rng.integers(200, n - 30, 4):
        m = int(m)
        assert np.array_equal(rules.signals(df.iloc[:m + 1], tpl, p), full_sig[:m + 1])
        d2 = df.copy()
        for col in ("Open", "High", "Low", "Close"):
            d2.iloc[m + 1:, d2.columns.get_loc(col)] = d2[col].iloc[m + 1:].to_numpy() * rng.uniform(0.3, 3, n - m - 1)
        assert np.array_equal(rules.signals(d2, tpl, p)[:m + 1], full_sig[:m + 1])
        ct = [x for x in backtest.run(df.iloc[:m + 1], tpl, p, ex, windows={"w": (100, m)})["w"]["trades"] if x["reason"] != "window_end"]
        fx = [x for x in ft if idx[x["exit_date"]] <= m and x["reason"] != "window_end"]
        k = lambda x: (x["entry_date"], x["exit_date"], x["entry"], x["exit"], x["return_pct"], x["reason"])
        assert [k(x) for x in ct] == [k(x) for x in fx]
    for x in ft:                                        # entry = next bar's open after a signal bar
        e = idx[x["entry_date"]]
        assert full_sig[e - 1] and abs(df["Open"].iloc[e] - x["entry"]) < 0.006


@pytest.mark.parametrize("t,tpl", [("RELIANCE", "breakout"), ("SBIN", "rsi")])
def test_locked_year_never_touches_tuning(t, tpl):
    df, ex = _df(t), dict(hold_days=10, stop_pct=5.0, tp_pct=10.0)
    k = data.split_index(df)
    a = refine.refine(df, tpl, P[tpl], ex)
    d2 = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        d2.iloc[k:, d2.columns.get_loc(col)] = d2[col].iloc[k:].to_numpy() * 3
    b = refine.refine(d2, tpl, P[tpl], ex)
    assert (a["refined"]["params"], a["refined"]["exits"]) == (b["refined"]["params"], b["refined"]["exits"])
    assert a["search"]["top_candidates"] == b["search"]["top_candidates"] and a["search"]["trials"] == b["search"]["trials"]


@pytest.mark.parametrize("t,tpl", [("RELIANCE", "breakout"), ("INFY", "rsi"), ("ITC", "mean_reversion")])
def test_refine_top_candidates_rescored_by_reference_and_chosen_is_best(t, tpl):
    df, ex = _df(t), dict(hold_days=10, stop_pct=5.0, tp_pct=10.0)
    r = refine.refine(df, tpl, P[tpl], ex)
    s = r["search"]
    top = s["top_candidates"]
    if s["eligible"] == 0:
        assert r["verdict"]["flag"] == "inconclusive"
        return
    for c in top[:5]:
        m = ref.backtest(df, tpl, c["params"], c["exits"], 15.0)["train"]["metrics"]
        assert m["trades"] == c["train_trades"] >= s["min_train_trades"] and abs(m["sharpe"] - c["train_sharpe"]) < 2e-3
    if r["refined"]["changed"]:
        best = max(top, key=lambda c: (c["train_sharpe"], c["train_return"]))
        assert (best["params"], best["exits"]) == (r["refined"]["params"], r["refined"]["exits"])


# ---------------------------------------------------------------- tracker
@pytest.mark.parametrize("t,tpl", [("RELIANCE", "rsi"), ("TCS", "breakout"), ("HDFCBANK", "mean_reversion"), ("SBIN", "ma_cross")])
def test_tracker_exit_alerts_match_reference_trades(t, tpl):
    df = _df(t)
    ex = dict(hold_days=6, stop_pct=3.0, tp_pct=4.0)
    sig = ref.signals(df, tpl, P[tpl])
    k = ref.split_k(df)
    _, tr = ref.simulate(df, sig, 100, len(df) - 1, 6, 3.0, 4.0, 0.0015)
    checked = 0
    for x in tr:
        if x["reason"] == "window_end":
            continue
        e, xi = x["entry_idx"], x["exit_idx"]
        out = tracker.evaluate_exit(df.iloc[:xi + 1], e, float(df["Open"].iloc[e]), 6, 3.0, 4.0, 1, True)
        assert out["fired"] and out["idx"] == xi and out["reason"] == x["reason"] and abs(out["px"] - x["exit"]) < 1e-6
        if xi > e:
            assert not tracker.evaluate_exit(df.iloc[:xi], e, float(df["Open"].iloc[e]), 6, 3.0, 4.0, 1, True)["fired"]
        checked += 1
    assert checked >= 5


@pytest.mark.parametrize("side,entry,exit_,cost,qty", [("buy", 1200.0, 1260.0, 15, 10), ("buy", 1200.0, 1100.0, 40, 3), ("sell", 1200.0, 1100.0, 15, 5),
                                                      ("sell", 1200.0, 1290.0, 0, 7), ("buy", 1200.0, 1200.0, 15, 1)])
def test_tracker_pnl_math_long_and_short(side, entry, exit_, cost, qty):
    lc = float(data.load("RELIANCE.NS")[0]["Close"].iloc[-1])
    entry = round(lc * entry / 1200.0, 2); exit_ = round(lc * exit_ / 1200.0, 2)
    j = client.post("/paper/start", json=dict(ticker="RELIANCE.NS", template="breakout", cost_bps=cost, exits=dict(stop_pct=None, tp_pct=None, hold_days=20),
                                              trade=dict(side=side, entry_price=entry, qty=qty))).json()
    c = client.post(f"/paper/{j['id']}/stop", json=dict(exit_price=exit_, add_to_journal=True)).json()["close"]
    d = 1 if side == "buy" else -1
    gross = d * (exit_ / entry - 1) * 100
    assert abs(c["gross_pct"] - gross) < 0.006 and abs(c["net_pct"] - (gross - cost / 100)) < 0.006
    assert abs(c["pnl_abs"] - (gross - cost / 100) / 100 * entry * qty) < 1.0     # abs P&L is rounded to whole rupees in the API
    assert c["verdict"] == ("WON" if c["net_pct"] > 0.05 else "LOST" if c["net_pct"] < -0.05 else "FLAT")


# ---------------------------------------------------------------- memory
@pytest.mark.parametrize("t", TICKERS[:5])
def test_memory_forward_returns_recomputed(t):
    df = _df(t)
    c = df["Close"].to_numpy(float)
    for back in (3, 10, 25, 60, 200):
        i = len(df) - 1 - back
        f = strategy_memory.forward_returns(df, str(df.index[i].date()))
        assert f["base_date"] == str(df.index[i].date())
        for k, key in ((5, "fwd5"), (20, "fwd20")):
            want = round((c[i + k] / c[i] - 1) * 100, 2) if i + k < len(c) else None
            assert f[key] == want
        assert f["days_since"] == back
    f = strategy_memory.forward_returns(df, str(df.index[-1].date()))
    assert f["fwd5"] is None and f["fwd20"] is None and f["since_pct"] is None      # nothing has happened after the last bar


# ---------------------------------------------------------------- parser
def test_parser_ground_truth_english_and_hinglish():
    rnd = random.Random(2)
    names = {"RELIANCE": "Reliance Industries", "JIOFIN": "Jio Financial", "TCS": "TCS", "INFY": "Infosys", "HDFCBANK": "HDFC Bank", "ICICIBANK": "ICICI Bank",
             "SBIN": "State Bank of India", "ITC": "ITC", "LT": "L&T", "BHARTIARTL": "Bharti Airtel"}
    for tk, nm in names.items():
        for _ in range(4):
            h, sp, tp = rnd.choice([5, 10, 20]), rnd.choice([2, 3.5, 6]), rnd.choice([4, 9, 15])
            tail = f", hold {h} days, {sp}% stop loss, {tp}% take profit"
            for text, want in ((f"Buy {nm} when RSI(14) drops below 28{tail}", ("rsi", dict(level=28.0))),
                               (f"{nm} 20 day high breakout{tail}", ("breakout", dict(n=20))),
                               (f"{nm} ko kharido jab RSI 28 se neeche ho, {h} din baad becho, stop loss {sp}%, target {tp}%", ("rsi", dict(level=28.0))),
                               (f"{nm} 20 din ka high breakout par kharido, {h} din tak rakho, stop loss {sp}%, target {tp}%", ("breakout", dict(n=20)))):
                s = nlparse.parse_rules(text)["strategy"]
                assert s["ticker"] == tk + ".NS" and s["template"] == want[0] and all(s["params"][k] == v for k, v in want[1].items()), text
                assert s["exits"] == dict(hold_days=h, stop_pct=float(sp), tp_pct=float(tp)), text


def test_parser_edge_cases():
    r = nlparse.parse_rules("Buy RELIANCE when RSI(14) drops below 290, sell after 10 days")
    assert r["strategy"]["params"]["level"] == 30.0 and any("290" in u for u in r["unsupported"])      # impossible value never used
    assert nlparse.parse_rules("short INFY when 10 day ma crosses below 50 day ma")["unsupported"]
    assert nlparse.parse_rules("Buy SBIN 20 day breakout, no stop loss")["strategy"]["exits"]["stop_pct"] is None
    assert nlparse.parse_rules("Buy ITC 20 day breakout hold 9999 days")["strategy"]["exits"]["hold_days"] != 9999
    assert nlparse.parse_rules("make me rich")["strategy"] is None
    s = nlparse.parse_rules("Buy TCS RSI below 30 stop loss 5% take profit 3%")["strategy"]["exits"]      # audit bug: tp used to read 5
    assert (s["stop_pct"], s["tp_pct"]) == (5.0, 3.0)
    s = nlparse.parse_rules("Buy TCS RSI below 30 take profit 12% stop loss 4%")["strategy"]["exits"]
    assert (s["stop_pct"], s["tp_pct"]) == (4.0, 12.0)
    s = nlparse.parse_rules("TCS ko kharido jab RSI 25 se neeche ho")["strategy"]                         # audit bug: used to raise (HTTP 500)
    assert s["params"]["level"] == 25.0 and s["params"]["mode"] == "oversold"
    s = nlparse.parse_rules("SBIN 70 se upar RSI jaye to kharido, stop loss nahi")["strategy"]
    assert s["params"]["mode"] == "overbought" and s["params"]["level"] == 70.0 and s["exits"]["stop_pct"] is None
    assert nlparse.parse_rules("10 din ka moving average 50 din ke moving average ko upar cross kare, INFY")["strategy"]["params"] == dict(fast=10, slow=50)
    assert nlparse.parse_rules("kharido") ["strategy"] is None


def test_parser_never_crashes_and_always_returns_valid_rules():
    frag = ["buy", "TCS", "reliance", "rsi", "rsi(14)", "below", "above", "30", "290", "-5", "0", "0.001", "9999999", "din", "neeche", "upar", "kharido", "becho",
            "stop loss", "target", "no stop", "hold", "days", "week", "%", "5%", "20 day high", "breakout", "moving average", "crosses", "golden cross", "short",
            "sell", "when", "and", "sigma", "2.5", "z-score", "ema", "trailing", "volume", "se", "nahi", "1e9", "()", "(", ")", "nan", "inf", "ko", "jab", "50", "200", "/"]
    rnd = random.Random(3)
    for _ in range(3000):
        t = " ".join(rnd.choice(frag) for _ in range(rnd.randint(1, 14)))
        s = nlparse.parse_rules(t)["strategy"]
        if s:
            rules.validate(s["template"], s["params"], s["exits"])


def test_parse_endpoint_hinglish_is_200_not_500():
    r = client.post("/strategies/parse", json=dict(text="TCS ko kharido jab RSI 25 se neeche ho, 5 din baad becho, stop loss 3%"))
    assert r.status_code == 200 and r.json()["strategy"]["exits"]["hold_days"] == 5
