"""Crypto section tests. The network is mocked (cd.http_get / cd._now_ms / cd._yf_klines); the only real-network test is the live smoke test,
which skips itself when Binance is unreachable (e.g. geo-blocked)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audit"))
import ref_backtest as ref                                   # noqa: E402
from app import config, crypto, crypto_data as cd, crypto_memory as cm, crypto_tracker as ct, refine, rules, strategy_memory   # noqa: E402
from app.main import app                                     # noqa: E402

client = TestClient(app)
NOW = int(pd.Timestamp("2026-11-01 02:30:00").value // 10**6)      # fixed "now" (UTC) for mocked-network tests
MS = {"1d": 86_400_000, "4h": 14_400_000, "1h": 3_600_000}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Default: crypto network OFF (bundled real Binance cache). Tests that mock the network switch it on explicitly."""
    monkeypatch.setenv("REX_CRYPTO_OFFLINE", "1")
    cd.clear_cache()
    yield
    cd.clear_cache()


class FakeBinance:
    """Deterministic fake of api.binance.com: klines on a grid (random walk) + ticker price. `down` hosts raise like a 451 geo-block."""
    def __init__(self, price=100.0, down=()):
        self.price, self.down, self.calls = price, set(down), []

    def row(self, symbol, iv, t):
        rng = np.random.default_rng(int(t // MS[iv]) % 100000)
        base = 100 * (1 + 0.2 * np.sin((t // MS[iv]) / 50)) * np.exp(rng.normal(0, 0.01))
        o, c = base, base * (1 + rng.normal(0, 0.005))
        return [t, str(o), str(max(o, c) * 1.002), str(min(o, c) * 0.998), str(c), "10", t + MS[iv] - 1]

    def __call__(self, url, params=None, timeout=8.0):
        host = url.split("//")[1].split("/")[0]
        self.calls.append(host)
        if host in self.down:
            raise RuntimeError("451 geo-blocked")
        if url.endswith("/ticker/price"):
            return {"symbol": params["symbol"], "price": str(self.price)}
        iv, lim = params["interval"], params.get("limit", 1000)
        now_open = (NOW // MS[iv]) * MS[iv]
        start = params.get("startTime", now_open - (lim - 1) * MS[iv])
        out, t = [], (start // MS[iv]) * MS[iv]
        while t <= now_open and len(out) < lim:
            out.append(self.row(params["symbol"], iv, t)); t += MS[iv]
        if "startTime" not in params:
            out = out[-lim:]
        return out


@pytest.fixture
def net(monkeypatch, tmp_path):
    monkeypatch.setenv("REX_CRYPTO_OFFLINE", "0")
    monkeypatch.setattr(config, "DATA_CACHE", tmp_path / "cache")
    monkeypatch.setattr(cd, "_now_ms", lambda: NOW)
    fb = FakeBinance()
    monkeypatch.setattr(cd, "http_get", fb)
    return fb


# ------------------------------------------------------------------ provider
def test_normalise_and_interval():
    assert cd.normalise("btc") == "BTCUSDT" and cd.normalise("Ethereum") == "ETHUSDT" and cd.normalise("SOL-USD") == "SOLUSDT"
    for bad in ("DOGE", "RELIANCE.NS", ""):
        with pytest.raises(cd.CryptoError):
            cd.normalise(bad)
    with pytest.raises(cd.CryptoError):
        cd.check_interval("5m")


def test_bundled_cache_offline_is_real_and_closed():
    for iv, minbars in (("1d", 1500), ("4h", 5000), ("1h", 8000)):
        df, info = cd.load("BTCUSDT", iv)
        assert len(df) >= minbars and df.index.is_monotonic_increasing and not df.index.has_duplicates
        assert (df["High"] >= df["Low"]).all() and (df["Close"] > 0).all()
        assert info["market"] == "crypto" and info["timezone"] == "UTC"


def test_offline_without_cache_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_CACHE", tmp_path / "empty")
    with pytest.raises(cd.CryptoError):
        cd.load("BTCUSDT", "1d")


def test_fetch_binance_primary_and_cache_written(net):
    df, info = cd.load("BTCUSDT", "4h")
    assert info["source"] == "api.binance.com" and "api.binance.com" in net.calls
    assert cd._path("BTCUSDT", "4h").exists()
    last_closed_open = pd.Timestamp("2026-11-01 00:00")          # 02:30 now: the 4h candle opened 00:00 is still forming
    assert df.index[-1] == last_closed_open - pd.Timedelta(hours=4)       # closed candles only
    assert (df.index[1:] - df.index[:-1]).max() == pd.Timedelta(hours=4)


def test_fallback_to_vision_then_yfinance(net, monkeypatch):
    net.down = {"api.binance.com"}
    df, info = cd.load("ETHUSDT", "1h")
    assert info["source"] == "data-api.binance.vision" and cd.status()["provider"] == "data-api.binance.vision"
    cd.clear_cache(); monkeypatch.setattr(config, "DATA_CACHE", config.DATA_CACHE / "second")
    net.down = {"api.binance.com", "data-api.binance.vision"}
    yf = pd.DataFrame(dict(Open=1.0, High=2.0, Low=0.5, Close=1.5, Volume=3.0, close_ms=0), index=pd.date_range("2026-08-01", "2026-11-01 01:00", freq="h", name="Date"))
    monkeypatch.setattr(cd, "_yf_klines", lambda s, i: yf)
    df, info = cd.load("SOLUSDT", "1h")
    assert info["source"].startswith("yfinance") and len(df) > 1000
    assert df.index[-1] <= pd.Timestamp("2026-11-01 01:00")


def test_all_sources_down_uses_stale_cache_and_backoff(net, monkeypatch):
    cd.load("BTCUSDT", "1h")
    monkeypatch.setattr(cd, "_now_ms", lambda: NOW + 6 * MS["1h"])       # cache is now stale -> top-up needed
    net.down = {"api.binance.com", "data-api.binance.vision"}
    monkeypatch.setattr(cd, "_yf_klines", lambda s, i: (_ for _ in ()).throw(cd.CryptoError("yf down")))
    df, info = cd.load("BTCUSDT", "1h")
    assert "stale" in info["source"] and len(df) > 100
    n = len(net.calls)
    cd.load("BTCUSDT", "1h")
    assert len(net.calls) == n                                      # 30 s back-off: no hammering


def test_quote_live_and_fallback_to_cache(net, monkeypatch):
    net.price = 123.45
    q = cd.quote("BTCUSDT")
    assert q["price"] == 123.45 and q["live"] and q["market"] == "24/7 (always open)" and "Real-time" in q["label"] and not q["stale"]
    cd.clear_cache(); net.down = {"api.binance.com", "data-api.binance.vision"}
    monkeypatch.setattr(cd, "_quote_yf", lambda s: (_ for _ in ()).throw(RuntimeError("no yf")))
    monkeypatch.setattr(config, "DATA_CACHE", Path(__file__).resolve().parent.parent / "data_cache")      # bundled real cache
    q = cd.quote("BTCUSDT")
    assert q["stale"] and "Not live" in q["label"] and q["provider"].startswith("offline cache")


def test_quote_offline_is_marked_stale():
    q = cd.quote("ETHUSDT")
    assert q["stale"] and not q["live"] and "Not live" in q["label"] and q["price"] > 0


# ------------------------------------------------------------------ parser
def test_parser_coin_interval_hours():
    p = crypto.parse("buy ETH on 4h when price breaks 20-day high, hold 6 hours, stop 3%")
    s = p["strategy"]
    assert p["symbol"] == "ETHUSDT" and p["interval"] == "4h" and s["template"] == "breakout" and s["params"]["n"] == 20
    assert s["exits"]["hold_days"] == 2 and s["exits"]["stop_pct"] == 3.0 and p["market"] == "crypto" and p["parser"].startswith("rules")
    q = crypto.parse("buy bitcoin when RSI below 30")
    assert q["symbol"] == "BTCUSDT" and q["interval"] == "1d" and q["strategy"]["template"] == "rsi"


# ------------------------------------------------------------------ backtest vs independent reference
@pytest.mark.parametrize("sym,iv,tpl,params,ex,cost", [
    ("BTCUSDT", "1d", "breakout", dict(n=20), dict(hold_days=10, stop_pct=5.0, tp_pct=10.0), 10.0),
    ("ETHUSDT", "4h", "ma_cross", dict(fast=10, slow=50), dict(hold_days=12, stop_pct=3.0, tp_pct=None), 10.0),
    ("SOLUSDT", "1h", "rsi", dict(period=14, level=30, mode="oversold"), dict(hold_days=24, stop_pct=2.0, tp_pct=4.0), 0.0),
    ("BTCUSDT", "1h", "mean_reversion", dict(n=20, z=2.0), dict(hold_days=8, stop_pct=None, tp_pct=None), 25.0),
])
def test_crypto_engine_equals_reference(sym, iv, tpl, params, ex, cost):
    out = crypto.run_backtest(sym, iv, tpl, params, ex, cost)
    df = out.pop("df")
    sig = ref.signals(df, tpl, params)
    k = cd.split_k(df, iv)
    for name, (a, b) in {"train": (ref.WARMUP, k - 1), "locked": (k, len(df) - 1)}.items():
        s, tr = ref.simulate(df, sig, a, b, ex["hold_days"], ex["stop_pct"], ex["tp_pct"], cost / 1e4)
        m = out["result"][name]["metrics"]
        assert m["trades"] == len(tr)
        assert abs(m["total_return"] - (np.prod(1 + s) - 1)) < 2e-4
    assert out["spec"]["market"] == "crypto"


def test_intraday_locks_last_20_percent_daily_locks_365_days():
    for iv in ("1h", "4h"):
        df, info = cd.load("BTCUSDT", iv)
        k = cd.split_k(df, iv)
        assert abs((len(df) - k) / len(df) - 0.2) < 0.01
    df, _ = cd.load("BTCUSDT", "1d")
    k = cd.split_k(df, "1d")
    assert (df.index[-1] - df.index[k]).days in (364, 365)


def test_default_cost_10_bps_and_periods():
    out = crypto.run_backtest("BTCUSDT", "1d", "breakout", {}, {}, None)
    assert out["spec"]["cost_bps"] == 10.0 and cd.INTERVALS["1h"]["periods"] == 8760 and cd.INTERVALS["4h"]["periods"] == 2190
    out2 = crypto.run_backtest("BTCUSDT", "1d", "breakout", {}, {}, 100.0)
    assert out2["result"]["train"]["metrics"]["total_return"] < out["result"]["train"]["metrics"]["total_return"]


# ------------------------------------------------------------------ refine
@pytest.mark.parametrize("iv", ["1d", "4h"])
def test_refine_never_looks_at_locked_period(iv):
    df, _ = cd.load("ETHUSDT", iv)
    k = cd.split_k(df, iv)
    p, e = rules.validate("breakout", {}, {})
    per = cd.INTERVALS[iv]["periods"]
    a = refine.refine(df, "breakout", p, e, 10.0, seed=7, split_k=k, periods=per)
    d2 = df.copy()
    rng = np.random.default_rng(1)
    noise = 1 + rng.normal(0, 0.05, len(df) - k)
    for c in ("Open", "High", "Low", "Close"):
        d2.iloc[k:, d2.columns.get_loc(c)] = d2[c].iloc[k:].to_numpy() * noise      # scramble ONLY the locked period
    b = refine.refine(d2, "breakout", p, e, 10.0, seed=7, split_k=k, periods=per)
    assert a["refined"]["params"] == b["refined"]["params"] and a["refined"]["exits"] == b["refined"]["exits"]
    assert a["refined"]["result"]["train"]["metrics"] == b["refined"]["result"]["train"]["metrics"]


def test_crypto_refine_wording_intraday_vs_daily():
    h = crypto.run_refine("BTCUSDT", "1h", "breakout", {}, {}, None)
    txt = " ".join([h["verdict"]["headline"]] + h["verdict"]["notes"])
    assert "locked year" not in txt and "final year" not in txt and "locked final period" in txt
    assert h["data"]["locked_from"] and h["spec"]["market"] == "crypto"
    d = crypto.run_refine("BTCUSDT", "1d", "breakout", {}, {}, None)
    assert "locked" in " ".join([d["verdict"]["headline"]] + d["verdict"]["notes"]).lower()


# ------------------------------------------------------------------ API + memory separation
def test_api_backtest_refine_memory_separate_from_nse():
    r = client.post("/crypto/backtest", json=dict(symbol="BTC", interval="4h", template="breakout")).json()
    assert r["spec"]["symbol"] == "BTCUSDT" and "df" not in r and r["memory"]["market"] == "crypto"
    r2 = client.post("/crypto/refine", json=dict(symbol="ETH", interval="1d", template="rsi")).json()
    assert "df" not in r2 and r2["verdict"]["flag"] in ("ok", "overfit", "inconclusive")
    m = client.get("/crypto/memory/strategy-runs?symbol=BTCUSDT").json()
    assert m["total"] == 1 and m["last_time"]["market"] == "crypto" and m["runs"][0]["interval"] == "4h"
    assert client.get("/memory/strategy-runs").json()["total"] == 0          # NSE memory untouched
    assert strategy_memory.count() == 0 and cm.count() == 2
    client.post("/backtest", json=dict(ticker="RELIANCE.NS", template="breakout"))
    assert client.get("/crypto/memory/strategy-runs").json()["total"] == 2   # NSE run did not leak into crypto
    assert client.post("/crypto/backtest", json=dict(symbol="BTC", interval="1d", template="breakout", remember=False)).status_code == 200
    assert cm.count() == 2


def test_api_errors_are_422():
    assert client.post("/crypto/backtest", json=dict(symbol="DOGE")).status_code == 422
    assert client.post("/crypto/backtest", json=dict(symbol="BTC", interval="5m")).status_code == 422
    assert client.post("/crypto/strategies/parse", json=dict(text="buy ETH on 4h when price breaks 20-day high")).json()["interval"] == "4h"
    s = client.get("/crypto/symbols").json()
    assert [x["symbol"] for x in s["symbols"]] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"] and s["cost_bps_default"] == 10.0
    assert "never places orders" in client.get("/crypto/status").json()["note"]


# ------------------------------------------------------------------ tracker (mocked live market)
@pytest.fixture
def live(net):
    cd.load("BTCUSDT", "1h"); cd.load("BTCUSDT", "1d")
    df, _ = cd.load("BTCUSDT", "1h")
    net.price = float(df["Close"].iloc[-1])
    return net


def _set_price(net, px):
    net.price = px
    cd.clear_cache()


def test_tracker_logged_trade_stop_alert_and_distances(live):
    p0 = live.price
    s = client.post("/crypto/tracker/start", json=dict(symbol="BTC", interval="1h", template="breakout", params={"n": 20},
                                                        exits=dict(hold_days=48, stop_pct=5.0, tp_pct=10.0),
                                                        trade=dict(side="buy", entry_price=p0, qty=2))).json()
    assert s["mode"] == "logged" and s["phase"] == "open" and not s["alert"] and "never places orders" in s["disclaimer"]
    pos = s["position"]
    assert abs(pos["dist_to_stop_pct"] - 5.0) < 0.05 and abs(pos["dist_to_tp_pct"] - 10.0) < 0.05
    assert abs(pos["stop_px"] - p0 * 0.95) < 0.01 and abs(pos["tp_px"] - p0 * 1.10) < 0.01
    assert any("Real-time" in x for x in s["data_labels"]) and any("24/7" in x for x in s["data_labels"])
    _set_price(live, p0 * 0.97)
    g = client.get(f"/crypto/tracker/sessions/{s['id']}").json()
    assert not g["alert"] and g["position"]["pnl_abs"] < 0 and abs(g["position"]["net_pct"] - (-3.0 - 0.1)) < 0.05
    assert abs(g["position"]["dist_to_stop_pct"] - 2.06) < 0.1
    _set_price(live, p0 * 0.94)
    g = client.get(f"/crypto/tracker/sessions/{s['id']}").json()
    assert g["alert"] and g["exit_signal"]["reason"] == "stop" and "stop-loss" in g["exit_signal"]["text"].lower()
    assert len(g["chart"]["points"]) > 10 and g["chart"]["markers"] and len(g["chart"]["ticks"]) >= 1
    c = client.post(f"/crypto/tracker/sessions/{s['id']}/close", json={}).json()
    assert c["status"] == "closed" and c["close"]["verdict"] == "LOST"
    assert client.get("/crypto/tracker/sessions?status=active").json()["sessions"] == []
    m = client.get("/crypto/memory/strategy-runs?symbol=BTC").json()
    assert m["total"] >= 1 and m["last_time"]["market"] == "crypto"
    assert client.get("/memory/strategy-runs").json()["total"] == 0


def test_tracker_logged_take_profit_is_seen_between_polls(live):
    p0 = live.price
    s = ct.start("BTC", "1h", "breakout", {"n": 20}, dict(hold_days=48, stop_pct=5.0, tp_pct=2.0), None, "original", dict(side="buy", entry_price=p0), None)
    _set_price(live, p0 * 1.03)
    ct.get(s["id"], True)
    _set_price(live, p0 * 1.005)                      # price falls back below the target before you look again
    g = ct.get(s["id"], True)
    assert g["alert"] and g["exit_signal"]["reason"] == "take_profit"


def test_tracker_short_logged_trade(live):
    p0 = live.price
    s = ct.start("BTC", "1h", "breakout", {"n": 20}, dict(hold_days=48, stop_pct=5.0, tp_pct=10.0), None, "original", dict(side="sell", entry_price=p0), None)
    _set_price(live, p0 * 0.98)
    g = ct.get(s["id"], True)
    assert g["position"]["direction"] == "short" and g["position"]["net_pct"] > 1.5


def test_tracker_paper_enters_at_next_open_exits_on_stop_and_hold(live, monkeypatch):
    def sig(df, template, p):
        a = np.zeros(len(df), bool); a[-1] = True
        return a
    monkeypatch.setattr(ct.rules, "signals", sig)
    s = ct.start("BTC", "1h", "breakout", {"n": 20}, dict(hold_days=3, stop_pct=5.0, tp_pct=None), None, "original", None, 0.5)
    assert s["mode"] == "paper" and s["phase"] == "open" and s["position"]["origin"] == "signal"
    fm = cd.forming("BTCUSDT", "1h")
    assert fm is not None and abs(s["position"]["entry_px"] - fm["Open"]) < 1e-3        # next candle's OPEN, not the signal close
    assert not s["alert"]
    _set_price(live, s["position"]["entry_px"] * 0.90)
    g = ct.get(s["id"], True)
    st = g["summary"]
    assert g["phase"] == "watching" and st["trades"] == 1 and st["closed_trades"][0]["reason"] == "stop"


def test_tracker_closed_session_is_frozen_and_errors(live):
    s = ct.start("BTC", "1h", "breakout", {}, {}, None, "original", dict(side="buy"), None)
    ct.close(s["id"], None, "done")
    with pytest.raises(Exception):
        ct.close(s["id"])
    with pytest.raises(ct.TrackerError):
        ct.start("DOGE", "1h", "breakout", {}, {}, None)
    with pytest.raises(ct.TrackerError):
        ct.start("BTC", "1h", "breakout", {}, {}, None, "original", dict(side="buy", entry_price=-1), None)


def test_tracker_survives_network_loss(live):
    s = ct.start("BTC", "1h", "breakout", {}, {}, None, "original", dict(side="buy"), None)
    live.down = {"api.binance.com", "data-api.binance.vision"}
    cd.clear_cache()
    g = ct.get(s["id"], True)
    assert g["status"] == "active" and g["quote"]["price"] > 0


# ------------------------------------------------------------------ live smoke (real Binance; skipped when unreachable)
def test_live_binance_smoke(monkeypatch):
    monkeypatch.setenv("REX_CRYPTO_OFFLINE", "0")
    try:
        rows, host = cd._binance("/api/v3/klines", dict(symbol="BTCUSDT", interval="1h", limit=5))
    except cd.CryptoError as e:
        pytest.skip(f"Binance unreachable from this machine (geo-block or no network): {e}")
    assert len(rows) == 5 and host in ("api.binance.com", "data-api.binance.vision")
    q = cd.quote("BTCUSDT")
    assert q["live"] and q["price"] > 1000 and q["market"].startswith("24/7")
    for s in ("ETHUSDT", "SOLUSDT"):
        assert cd.quote(s)["price"] > 1
