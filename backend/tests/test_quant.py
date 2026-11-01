import itertools
import math
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app import backtest, costs, data, quant, rules
from app.main import app

c = TestClient(app)


# ---------------------------------------------------------------- PBO
def _pbo_reference(M, S):
    """Straight-from-the-paper loops, no vectorisation."""
    T, N = M.shape
    L = T // S
    M = M[T - L * S:]
    blocks = [M[i * L:(i + 1) * L] for i in range(S)]
    lam = []
    for J in itertools.combinations(range(S), S // 2):
        Jc = [i for i in range(S) if i not in J]
        tr = np.vstack([blocks[i] for i in J]); te = np.vstack([blocks[i] for i in Jc])
        sr = lambda X: np.array([X[:, n].mean() / X[:, n].std(ddof=1) if X[:, n].std(ddof=1) > 1e-12 else 0.0 for n in range(N)])
        s_is, s_oos = sr(tr), sr(te)
        n_star = int(np.argmax(s_is))
        v = s_oos[n_star]
        rank = (s_oos < v).sum() + ((s_oos == v).sum() + 1) / 2
        w = rank / (N + 1)
        lam.append(math.log(w / (1 - w)))
    return float(np.mean(np.array(lam) <= 0)), len(lam)


def test_pbo_matches_reference_implementation():
    rng = np.random.default_rng(3)
    M = rng.normal(0.0005, 0.01, size=(96, 7)) + np.linspace(0, 0.002, 7)
    ref, n = _pbo_reference(M, 6)
    out = quant.pbo(M, S=6)
    assert out["splits"] == n == math.comb(6, 3)
    assert out["pbo"] == pytest.approx(ref, abs=1e-12)


def test_pbo_noise_is_about_half_and_real_edge_is_near_zero():
    # 40 skill-less strategies: choosing the best in-sample is luck. One dataset swings a lot (0.16..0.95), the average over datasets is 0.5.
    p_noise = np.mean([quant.pbo(np.random.default_rng(s).normal(0, 0.01, size=(800, 40)), S=16)["pbo"] for s in range(12)])
    assert 0.4 < p_noise < 0.6, p_noise
    rng = np.random.default_rng(1)
    edge = rng.normal(0, 0.01, size=(800, 20)) + np.linspace(0, 0.01, 20)   # strategy k has a persistently higher mean
    assert quant.pbo(edge, S=16)["pbo"] < 0.1


def test_pbo_perfectly_stable_winner_is_zero_and_validates_input():
    base = np.random.default_rng(5).normal(0, 0.01, size=(160, 1))
    M = np.hstack([base + 0.0005 * k for k in range(5)])         # same shape, strategy 4 always best
    out = quant.pbo(M, S=8)
    assert out["pbo"] == 0.0 and out["candidates"] == 5 and out["splits"] == 70
    with pytest.raises(ValueError):
        quant.pbo(M[:, :1])
    with pytest.raises(ValueError):
        quant.pbo(M, S=5)


# ---------------------------------------------------------------- DSR
def test_expected_max_sharpe_known_values():
    # unit-variance trials: E[max of N standard normals] ~ 2.53 for N=100 (Bailey & Lopez de Prado eq. with Euler-Mascheroni gamma)
    assert quant.expected_max_sharpe(100, 1.0) == pytest.approx(2.53, abs=0.02)
    assert quant.expected_max_sharpe(1, 1.0) == 0.0 and quant.expected_max_sharpe(50, 0.0) == 0.0
    sim = np.random.default_rng(0).normal(size=(4000, 100)).max(axis=1).mean()
    assert quant.expected_max_sharpe(100, 1.0) == pytest.approx(sim, abs=0.15)
    assert quant.expected_max_sharpe(500, 1.0) > quant.expected_max_sharpe(10, 1.0)


def test_dsr_closed_form_and_monotonicity():
    Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    sr, T, sk, ku, N, var = 0.12, 250, -0.3, 4.5, 20, 0.004
    sr0 = quant.expected_max_sharpe(N, var)
    want = Phi((sr - sr0) * math.sqrt(T - 1) / math.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2))
    assert quant.dsr(sr, T, sk, ku, N, var) == pytest.approx(want, abs=1e-12)
    # one trial = plain probabilistic Sharpe ratio against 0
    assert quant.dsr(0.1, 250, 0, 3, 1, 0.0) == pytest.approx(Phi(0.1 * math.sqrt(249) / math.sqrt(1 + 0.5 * 0.01)), abs=1e-12)
    assert quant.dsr(sr, T, sk, ku, 200, var) < quant.dsr(sr, T, sk, ku, 5, var)         # more trials -> lower
    assert quant.dsr(0.2, T, 0, 3, N, var) > quant.dsr(0.1, T, 0, 3, N, var)              # higher Sharpe -> higher
    assert quant.dsr(0.2, 1000, 0, 3, N, var) > quant.dsr(0.2, 100, 0, 3, N, var)         # longer track record -> higher


def test_dsr_of_best_of_noise_is_not_convincing():
    rng = np.random.default_rng(11)
    ps = []
    for _ in range(40):
        R = rng.normal(0, 0.01, size=(500, 50))
        sh = R.mean(0) / R.std(0, ddof=1)
        rep = quant.dsr_report(R[:, sh.argmax()], list(sh), 50, 252)
        ps.append(rep["dsr"])
    assert np.median(ps) < 0.8 and rep["verdict"] in ("likely_luck", "uncertain", "likely_real")
    assert "Deflated Sharpe" in rep["words"]


def test_refine_reports_pbo_and_dsr():
    r = c.post("/refine", json=dict(ticker="RELIANCE.NS", template="breakout", remember=False)).json()
    s = r["overfit_stats"]
    assert s["pbo"]["available"] and 0 <= s["pbo"]["pbo"] <= 1 and s["pbo"]["level"] in ("low", "moderate", "high") and s["pbo"]["candidates"] >= 2
    assert s["dsr"]["available"] and 0 <= s["dsr"]["dsr"] <= 1 and s["dsr"]["n_trials"] == r["search"]["trials"] and s["dsr"]["words"]


# ---------------------------------------------------------------- costs
def test_india_delivery_hand_calculation():
    d = costs.india("delivery", 100_000, 0)
    b, s = d["buy"]["parts"], d["sell"]["parts"]
    assert b["brokerage"] == 0 == s["brokerage"]
    assert b["stt"] == 100 == s["stt"]                                   # 0.1% both sides
    assert b["exchange_txn"] == pytest.approx(3.07) and b["sebi"] == pytest.approx(0.1)
    assert b["gst"] == pytest.approx(0.18 * (3.07 + 0.1), abs=1e-3)
    assert b["stamp_duty"] == 15 and s["stamp_duty"] == 0                # buy side only
    assert s["dp_charge"] == 15.34 and b["dp_charge"] == 0
    assert d["round_trip_bps"] == pytest.approx((100 + 3.07 + 0.1 + 0.5706 + 15) / 10 + (100 + 3.07 + 0.1 + 0.5706 + 15.34) / 10, abs=0.01)
    assert costs.india("delivery", 100_000, 5)["round_trip_bps"] == pytest.approx(d["round_trip_bps"] + 10, abs=0.01)


def test_india_intraday_brokerage_is_lower_of_flat_and_pct():
    assert costs.leg("intraday", "buy", 100_000)["parts"]["brokerage"] == 20          # 0.03% = 30 -> capped at 20
    assert costs.leg("intraday", "buy", 50_000)["parts"]["brokerage"] == 15           # 0.03% = 15 < 20
    s = costs.leg("intraday", "sell", 100_000)["parts"]
    assert s["stt"] == 25 and s["stamp_duty"] == 0                                    # 0.025% sell only
    assert costs.leg("intraday", "buy", 100_000)["parts"]["stamp_duty"] == 3
    with pytest.raises(ValueError):
        costs.leg("margin", "buy", 1)


def test_resolve_keeps_original_behaviour_and_crypto_stays_bps():
    r = costs.resolve(None, None)
    assert r["cost_bps"] == 15 and r["legs"] is None
    assert costs.resolve("bps", 20, 3)["cost_bps"] == 26                     # 20 + 2 x 3 bp slippage
    cr = costs.resolve("india_delivery", 10, 2, market="crypto")
    assert cr["legs"] is None and cr["cost_bps"] == 14
    with pytest.raises(ValueError):
        costs.resolve("nope", None)


def test_backtest_legs_equal_symmetric_bps_and_asymmetry_costs_more():
    df, _ = data.load("RELIANCE.NS")
    p, e = rules.validate("breakout", {}, {})
    base = backtest.run(df, "breakout", p, e, 20, keep_series=False)["full"]["metrics"]["total_return"]
    legs = backtest.run(df, "breakout", p, e, 20, keep_series=False, cost_legs=(0.001, 0.001))["full"]["metrics"]["total_return"]
    assert base == pytest.approx(legs, abs=1e-12)
    d = costs.india("delivery")
    india = backtest.run(df, "breakout", p, e, None, keep_series=False, cost_legs=d["legs"])["full"]["metrics"]["total_return"]
    free = backtest.run(df, "breakout", p, e, 0, keep_series=False)["full"]["metrics"]["total_return"]
    assert india < free


def test_backtest_api_cost_models():
    base = c.post("/backtest", json=dict(ticker="TCS.NS", remember=False)).json()
    assert base["spec"]["cost_bps"] == 15 and base["spec"]["cost_model"] == "bps"
    ind = c.post("/backtest", json=dict(ticker="TCS.NS", remember=False, cost_model="india_delivery", slippage_bps=5)).json()
    assert ind["spec"]["cost_model"] == "india_delivery" and ind["spec"]["cost_bps"] == pytest.approx(33.78, abs=0.01)
    assert any("Indian delivery" in n for n in ind["notes"])
    assert c.post("/backtest", json=dict(ticker="TCS.NS", remember=False, cost_model="x")).status_code == 422
    m = c.get("/costs/models", params=dict(kind="intraday", trade_value=50000)).json()
    assert m["example"]["buy"]["parts"]["brokerage"] == 15 and "zerodha.com/charges" in m["source"]


# ---------------------------------------------------------------- walk-forward
def _df():
    return data.load("RELIANCE.NS")[0]


def test_windows_are_contiguous_non_overlapping_and_anchored_vs_rolling():
    for mode in ("rolling", "anchored"):
        ws = quant.make_windows(1236, 504, 252, mode)
        assert len(ws) >= 3
        for w in ws:
            assert w["train"][1] + 1 == w["test"][0]                    # test starts right after train
            assert w["train"][0] < w["train"][1] < w["test"][0] <= w["test"][1] <= 1235
        for a, b in zip(ws, ws[1:]):
            assert a["test"][1] + 1 == b["test"][0]                    # tests tile the out-of-sample span, no gaps or overlaps
        if mode == "rolling":
            assert {w["train"][1] - w["train"][0] + 1 for w in ws} == {504}
        else:
            assert len({w["train"][0] for w in ws}) == 1


def test_no_leakage_future_data_cannot_change_earlier_choices():
    df = _df()
    p, e = rules.validate("ma_cross", {}, {})
    base = quant.walk_forward(df, "ma_cross", p, e)
    ws = base["windows"]
    assert len(ws) >= 3
    k = 1                                                                # corrupt EVERYTHING from the start of window 2's test onward
    cut = df.index.get_loc(df.index[df.index >= ws[k]["test_start"]][0])
    rng = np.random.default_rng(0)
    d2 = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        d2.iloc[cut:, d2.columns.get_loc(col)] = d2[col].iloc[cut:].to_numpy() * rng.uniform(0.3, 3.0, len(d2) - cut)
    d2["High"] = d2[["Open", "High", "Low", "Close"]].max(axis=1); d2["Low"] = d2[["Open", "High", "Low", "Close"]].min(axis=1)
    alt = quant.walk_forward(d2, "ma_cross", p, e)
    for a, b in zip(base["windows"][:k + 1], alt["windows"][:k + 1]):    # windows whose train+test lie before the corruption (or whose TRAIN does) are identical
        assert a["params"] == b["params"] and a["exits"] == b["exits"] and a["train_sharpe"] == b["train_sharpe"]
    assert base["windows"][0]["test_return"] == alt["windows"][0]["test_return"]


def test_test_window_never_influences_its_own_choice():
    df = _df()
    p, e = rules.validate("breakout", {}, {})
    base = quant.walk_forward(df, "breakout", p, e)
    w = base["windows"][0]
    lo, hi = df.index.get_loc(df.index[df.index >= w["test_start"]][0]), df.index.get_loc(df.index[df.index <= w["test_end"]][-1])
    d2 = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        d2.iloc[lo:hi + 1, d2.columns.get_loc(col)] = d2[col].iloc[lo:hi + 1].to_numpy()[::-1] * 1.7      # scramble ONLY the first test window
    d2["High"] = d2[["Open", "High", "Low", "Close"]].max(axis=1); d2["Low"] = d2[["Open", "High", "Low", "Close"]].min(axis=1)
    alt = quant.walk_forward(d2, "breakout", p, e)
    assert alt["windows"][0]["params"] == w["params"] and alt["windows"][0]["train_sharpe"] == w["train_sharpe"]
    assert alt["windows"][0]["test_return"] != w["test_return"]          # ... while its own test result does move


def test_walkforward_api_shape_and_stitching():
    r = c.post("/walkforward", json=dict(ticker="RELIANCE.NS", template="breakout", remember=False)).json()
    assert r["mode"] == "rolling" and len(r["windows"]) >= 3
    ch = r["chart"]
    assert len(ch["dates"]) == len(ch["equity"]) == len(ch["benchmark_equity"]) and ch["boundaries"][0] == r["windows"][0]["test_start"]
    assert ch["dates"][0] >= r["windows"][0]["test_start"] and r["oos"]["end"] == r["windows"][-1]["test_end"]
    # stitched equity = product of per-window growth
    prod = 1.0
    for w in r["windows"]:
        prod *= 1 + w["test_return"]
    assert prod - 1 == pytest.approx(r["summary"]["metrics"]["total_return"], abs=1e-6) and abs(ch["equity"][-1] - prod) < 1e-3
    assert r["verdict"]["flag"] in ("holds", "fails", "inconclusive")
    a = c.post("/walkforward", json=dict(ticker="RELIANCE.NS", mode="anchored", remember=False)).json()
    assert {w["train_start"] for w in a["windows"]} == {a["windows"][0]["train_start"]}
    assert c.post("/walkforward", json=dict(ticker="RELIANCE.NS", mode="sideways")).status_code == 422
    assert c.post("/walkforward", json=dict(ticker="RELIANCE.NS", train_bars=2900, test_bars=900)).status_code == 422


def test_crypto_walkforward(monkeypatch):
    r = c.post("/crypto/walkforward", json=dict(symbol="BTCUSDT", interval="1d", template="breakout")).json()
    assert len(r["windows"]) >= 2 and r["spec"]["market"] == "crypto" and r["summary"]["metrics"]["days"] > 300


# ---------------------------------------------------------------- strategy lab
def test_lab_runs_all_13_assets_with_verdicts():
    r = c.post("/lab/run", json=dict(template="breakout")).json()
    assert len(r["rows"]) == 13 and not r["errors"]
    assert {x["asset_class"] for x in r["rows"]} == {"stock", "crypto"} and sum(x["asset_class"] == "crypto" for x in r["rows"]) == 3
    for x in r["rows"]:
        assert x["verdict"] in ("holds", "fades", "loses", "few_trades") and x["verdict_text"]
        assert {"total_return", "sharpe", "max_drawdown", "trades", "buy_hold_return"} <= set(x)
        assert x["max_drawdown"] <= 0
    s = r["summary"]
    assert s["assets"] == 13 and s["holds"] + s["fades"] + s["loses"] + s["few_trades"] == 13
    # the Lab numbers equal a single backtest of the same asset (same engine, same split)
    one = c.post("/backtest", json=dict(ticker="TCS.NS", template="breakout", remember=False)).json()["result"]["full"]["metrics"]
    row = next(x for x in r["rows"] if x["symbol"] == "TCS.NS")
    assert row["total_return"] == pytest.approx(one["total_return"]) and row["trades"] == one["trades"]


def test_lab_isolates_failures_and_honours_cost_model(monkeypatch):
    real = quant.lab_one

    def boom(kind, symbol, *a, **k):
        if symbol == "ITC.NS":
            raise RuntimeError("provider down")
        return real(kind, symbol, *a, **k)
    monkeypatch.setattr(quant, "lab_one", boom)
    r = c.post("/lab/run", json=dict(template="ma_cross", cost_model="india_delivery")).json()
    assert len(r["rows"]) == 12 and r["errors"][0]["symbol"] == "ITC.NS" and "Indian delivery" in r["cost"]
    cheap = c.post("/lab/run", json=dict(template="ma_cross", cost_bps=0)).json()
    assert cheap["summary"]["median_sharpe"] >= r["summary"]["median_sharpe"] - 1e-9
    assert c.post("/lab/run", json=dict(template="nope")).status_code == 422
