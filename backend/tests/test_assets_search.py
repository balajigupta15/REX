from fastapi.testclient import TestClient
from app.main import app
from app import assets

c = TestClient(app)


def s(q, **kw):
    r = c.get("/assets/search", params=dict(q=q, **kw))
    assert r.status_code == 200
    return r.json()["results"]


def test_symbol_and_name_matches_case_insensitive():
    assert s("reliance")[0]["symbol"] == "RELIANCE.NS" and s("RELIANCE")[0]["symbol"] == "RELIANCE.NS"
    assert s("tcs")[0]["symbol"] == "TCS.NS" and s("tata")[0]["symbol"] == "TCS.NS"
    assert s("btc")[0]["symbol"] == "BTCUSDT" and s("Bitcoin")[0]["symbol"] == "BTCUSDT"
    assert s("sol")[0]["symbol"] == "SOLUSDT" and s("solana")[0]["symbol"] == "SOLUSDT"
    assert s("eth")[0]["symbol"] == "ETHUSDT"


def test_shape_and_asset_classes():
    r = s("btc")[0]
    assert {"symbol", "name", "asset_class", "market"} <= set(r) and r["asset_class"] == "crypto" and "keywords" not in r
    assert s("infy")[0]["asset_class"] == "stock" and s("infy")[0]["market"] == "NSE"
    both = {x["asset_class"] for x in s("", limit=50)}
    assert both == {"stock", "crypto"}


def test_prefix_partial_and_fuzzy():
    assert s("rel")[0]["symbol"] == "RELIANCE.NS"
    assert s("hdfc")[0]["symbol"] == "HDFCBANK.NS"
    assert s("state bank")[0]["symbol"] == "SBIN.NS" and s("sbi")[0]["symbol"] == "SBIN.NS"
    assert s("l&t")[0]["symbol"] == "LT.NS"
    assert s("relience")[0]["symbol"] == "RELIANCE.NS"      # typo
    assert s("bitcon")[0]["symbol"] == "BTCUSDT"
    assert s("airtel")[0]["symbol"] == "BHARTIARTL.NS"


def test_no_match_and_limit():
    assert s("zzzzqq") == []
    assert len(s("", limit=3)) == 3
    assert len(assets.catalog()) == 13
    assert s("  btc ")[0]["symbol"] == "BTCUSDT"


def test_quote_endpoint_stock_and_crypto(monkeypatch):
    monkeypatch.setenv("REX_CRYPTO_OFFLINE", "1")
    r = c.get("/assets/quote", params=dict(symbol="TCS.NS")).json()
    assert r["asset_class"] == "stock" and r["price"] > 0 and r["currency"] == "INR" and r["state"]
    r = c.get("/assets/quote", params=dict(symbol="BTCUSDT")).json()
    assert r["asset_class"] == "crypto" and r["currency"] == "USD" and (r["price"] is None or r["price"] > 0)
    assert c.get("/assets/quote", params=dict(symbol="NOPE")).status_code == 404
