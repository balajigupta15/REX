"""Free data sources with mocked providers: crypto fallback chain, bhavcopy cross-check, news, CoinGecko metadata, active source on the Data page."""
import io, zipfile
from datetime import date, datetime, timezone
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import coinmeta, crypto_data as cd, news
from app.main import app
from pipeline import config as pcfg, crosscheck, ingest, providers, status, store

c = TestClient(app)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
T0 = 1_790_000_000_000 // 86_400_000 * 86_400_000          # a UTC midnight, ms


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("REX_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("REX_PIPELINE_OFFLINE", "0")
    monkeypatch.setenv("REX_PIPELINE_BACKOFF_S", "0")


def _rows(n=30, step=86_400_000, t0=T0):
    return [[t0 + i * step, 100 + i, 101 + i, 99 + i, 100.5 + i, 10 + i, 1000, 0, 0] for i in range(n)]


def _bybit_payload(rows):
    return {"retCode": 0, "retMsg": "OK", "result": {"list": [[str(r[0])] + [str(x) for x in r[1:6]] + ["1"] for r in reversed(rows)]}}


def _okx_payload(rows):
    return {"code": "0", "data": [[str(r[0])] + [str(x) for x in r[1:6]] + ["1", "1", "1"] for r in reversed(rows)]}


def router(binance_ok=True, bybit_ok=True, okx_ok=True, log=None):
    def get(url, params=None, timeout=8.0):
        log is not None and log.append(url.split("/")[2])
        if "binance" in url:
            if not binance_ok:
                raise RuntimeError("451 Unavailable For Legal Reasons")
            return [[r[0], str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5]), r[0] + 86_399_999] for r in _rows()]
        if "bybit" in url:
            if not bybit_ok:
                raise RuntimeError("403 Forbidden")
            return _bybit_payload(_rows())
        if "okx" in url:
            if not okx_ok:
                raise RuntimeError("timeout")
            return _okx_payload(_rows())
        raise AssertionError(url)
    return get


def test_chain_uses_binance_first(monkeypatch):
    log = []
    monkeypatch.setattr(cd, "http_get", router(log=log))
    df, src = providers.crypto_klines("BTCUSDT", "1d", T0)
    assert src == "binance" and len(df) == 30 and not df.attrs.get("fell_back") and set(log) <= {"api.binance.com"}


@pytest.mark.parametrize("flags,want", [(dict(binance_ok=False), "bybit"), (dict(binance_ok=False, bybit_ok=False), "okx")])
def test_chain_falls_through_in_order(monkeypatch, flags, want):
    monkeypatch.setattr(cd, "http_get", router(**flags))
    df, src = providers.crypto_klines("BTCUSDT", "1d", T0)
    assert src == want and len(df) == 30 and "binance" in df.attrs["fell_back"]
    assert list(df.columns) == providers.COLS and df.index.is_monotonic_increasing and df.index[0] == pd.Timestamp(T0, unit="ms")
    assert df["Close"].iloc[0] == 100.5


def test_chain_ends_in_yfinance_then_fails_cleanly(monkeypatch):
    monkeypatch.setattr(cd, "http_get", router(binance_ok=False, bybit_ok=False, okx_ok=False))
    yf = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [3.0], "close_ms": [1]}, index=pd.to_datetime([T0], unit="ms"))
    monkeypatch.setattr(cd, "_yf_klines", lambda s, i: yf)
    df, src = providers.crypto_klines("BTCUSDT", "1d", None)
    assert src == "yfinance" and "okx" in df.attrs["fell_back"]
    monkeypatch.setattr(cd, "_yf_klines", lambda s, i: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(providers.ProviderError) as e:
        providers.crypto_klines("BTCUSDT", "1d", None)
    assert all(n in str(e.value) for n in ("binance", "bybit", "okx", "yfinance"))


def test_bybit_and_okx_paging_and_unconfirmed_candles(monkeypatch):
    allrows = _rows(250)
    calls = []

    def get(url, params=None, timeout=8.0):
        calls.append(params)
        if "bybit" in url:
            pool = [r for r in allrows if "end" not in params or r[0] <= params["end"]][-params["limit"]:]
            return _bybit_payload(pool)
        pool = [r for r in allrows if "after" not in params or r[0] < int(params["after"])][-params["limit"]:]
        out = _okx_payload(pool)
        if "after" not in params:
            out["data"][0][8] = "0"                               # the newest candle is still forming
        return out
    monkeypatch.setattr(cd, "http_get", get)
    b = providers.bybit("BTCUSDT", "1d", None)
    assert len(b) == 250 and b.index.is_unique and len(calls) == 1
    calls.clear()
    o = providers.okx("BTCUSDT", "1d", None)
    assert len(o) == 249 and o.index.max() < pd.Timestamp(allrows[-1][0], unit="ms") and len(calls) == 3      # 100 + 100 + 50, forming candle dropped


def test_ingest_records_active_source_and_fallback_note(monkeypatch):
    monkeypatch.setattr(cd, "http_get", router(binance_ok=False))
    pv = providers.Providers(nse=lambda s, st: (_ for _ in ()).throw(AssertionError("no nse")))
    res = ingest.run("crypto", symbols=["BTCUSDT"], intervals=["1d"], providers=pv, now=NOW, sleep=lambda s: None)
    it = res["items"][0]
    assert it["status"] == "ok" and it["provider"] == "bybit" and it["error"].startswith("fell back: binance")
    s = status.build(NOW)
    row = next(r for r in s["partitions"] if r["symbol"] == "BTCUSDT" and r["interval"] == "1d")
    assert row["source"] == "bybit" and "binance" in row["source_note"]
    assert "binance" in s["sources"]["crypto"] and s["sources"]["crypto"].index("bybit") < s["sources"]["crypto"].index("okx") < s["sources"]["crypto"].index("yfinance")
    # next run: binance recovers -> the active source flips back
    monkeypatch.setattr(cd, "http_get", router())
    ingest.run("crypto", symbols=["BTCUSDT"], intervals=["1d"], providers=pv, now=NOW, sleep=lambda s: None)
    assert next(r for r in status.build(NOW)["partitions"] if r["symbol"] == "BTCUSDT")["source"] == "binance"


# ------------------------------------------------------------------ bhavcopy
def _csv(rows):
    head = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE\n"
    return (head + "\n".join(f"{s}, {ser}, 30-Oct-2026, 1, 1, 1, 1, 1, {cl}, 1" for s, ser, cl in rows)).encode()


def _zip(rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "TradDt,TckrSymb,SctySrs,ClsPric\n" + "\n".join(f"2026-10-30,{s},{ser},{cl}" for s, ser, cl in rows))
    return buf.getvalue()


def test_bhavcopy_parsers_keep_equity_series_only():
    assert crosscheck.parse_full_csv(_csv([("TCS", "EQ", 2502.6), ("TCS", "BE", 1.0), ("X", "GB", 9)])) == {"TCS": 2502.6}
    assert crosscheck.parse_cm_zip(_zip([("TCS", "EQ", 2502.6), ("SGB", "GB", 9)])) == {"TCS": 2502.6}


def test_bhavcopy_falls_back_to_zip_and_reports_missing():
    def http(url):
        if url.endswith(".csv"):
            raise RuntimeError("404")
        return _zip([("TCS", "EQ", 10.0)])
    assert crosscheck.bhavcopy_closes(date(2026, 10, 30), http) == {"TCS": 10.0}
    with pytest.raises(crosscheck.BhavError):
        crosscheck.bhavcopy_closes(date(2026, 10, 30), lambda u: (_ for _ in ()).throw(RuntimeError("blocked")))


def test_crosscheck_flags_mismatch_beyond_tolerance():
    idx = pd.to_datetime(["2026-10-29", "2026-10-30"])
    frames = {"TCS.NS": pd.DataFrame({"Close": [100.0, 102.0]}, index=idx), "INFY.NS": pd.DataFrame({"Close": [50.0, 50.1]}, index=idx), "ITC.NS": pd.DataFrame({"Close": [9.0, 9.0]}, index=idx)}
    book = {"TCS": 100.0, "INFY": 50.0}               # TCS 30th: 102 vs 100 = 2% off; ITC absent from the file

    def http(url):
        return _csv([(k, "EQ", v) for k, v in book.items()])
    r = crosscheck.run(list(frames), sessions=2, http=http, read=frames.get, now=NOW)
    flags = {(x["symbol"], x["date"]): x["flag"] for x in r["results"]}
    assert r["reachable"] and flags[("TCS.NS", "2026-10-30")] == "mismatch" and flags[("TCS.NS", "2026-10-29")] == "ok"
    assert flags[("INFY.NS", "2026-10-30")] == "ok" and flags[("ITC.NS", "2026-10-30")] == "not_in_file"
    assert [m["symbol"] for m in r["mismatches"]] == ["TCS.NS"] and r["mismatches"][0]["diff_pct"] == 2.0
    assert crosscheck.latest()["mismatches"][0]["symbol"] == "TCS.NS"       # persisted for the Data page


def test_crosscheck_unreachable_is_quiet():
    idx = pd.to_datetime(["2026-10-30"])
    r = crosscheck.run(["TCS.NS"], http=lambda u: (_ for _ in ()).throw(OSError("no route")), read=lambda s: pd.DataFrame({"Close": [1.0]}, index=idx), now=NOW)
    assert r["reachable"] is False and r["results"] == [] and r["errors"]


def test_nse_ingest_runs_crosscheck_and_shows_it_on_status(monkeypatch):
    idx = pd.bdate_range(end="2026-10-30", periods=40)
    df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000.0}, index=idx)
    pv = providers.Providers(nse=lambda s, st: (df, "yfinance"), crypto=None, bhav=lambda url: _csv([("TCS", "EQ", 103.0)]))
    ingest.run("nse", symbols=["TCS.NS"], providers=pv, now=NOW, sleep=lambda s: None)
    row = next(r for r in status.build(NOW)["partitions"] if r["symbol"] == "TCS.NS")
    assert row["source"] == "yfinance" and row["crosscheck"] == "mismatch"
    st = c.get("/pipeline/status").json()
    assert st["crosscheck"]["mismatches"][0]["diff_pct"] == pytest.approx(-2.913, abs=0.01)
    # mocked providers without a bhav getter never touch the network
    assert providers.Providers(nse=lambda s, st: (df, "x")).bhav is None


# ------------------------------------------------------------------ news + coin metadata
GOOGLE = b"""<?xml version="1.0"?><rss><channel>
<item><title>Reliance results beat estimates - Mint</title><link>https://n.example/1</link><pubDate>Fri, 30 Oct 2026 10:00:00 GMT</pubDate><source url="https://livemint.com">Mint</source></item>
<item><title>Reliance results beat estimates - Mint</title><link>https://n.example/1b</link><pubDate>Fri, 30 Oct 2026 10:00:00 GMT</pubDate><source url="https://livemint.com">Mint</source></item>
<item><title>Older story - The Hindu</title><link>https://n.example/2</link><pubDate>Wed, 28 Oct 2026 08:30:00 GMT</pubDate><source url="https://thehindu.com">The Hindu</source></item>
</channel></rss>"""
YF_NEW = [{"content": {"title": "Reliance Jio plans listing", "canonicalUrl": {"url": "https://y.example/3"}, "pubDate": "2026-10-31T04:00:00Z", "provider": {"displayName": "Reuters"}}},
          {"title": "legacy shape", "link": "https://y.example/4", "publisher": "ET", "providerPublishTime": 1793000000}]


def test_news_merges_dedupes_and_labels_sources():
    r = news.latest("RELIANCE.NS", 8, rss=lambda url: GOOGLE, yfn=lambda t: YF_NEW)
    titles = [i["title"] for i in r["items"]]
    assert titles[0] == "Reliance Jio plans listing" and titles.count("Reliance results beat estimates") == 1 and "Older story" in titles
    g = next(i for i in r["items"] if i["title"] == "Older story")
    assert g["publisher"] == "The Hindu" and g["published"] == "2026-10-28T08:30:00Z" and g["url"].startswith("https://") and g["via"] == "google_news"
    assert [i["published"] for i in r["items"]] == sorted((i["published"] for i in r["items"]), reverse=True)
    assert r["sources"] == ["google_news", "yfinance"] and "does not score" in r["note"]
    assert not any(k in r for k in ("sentiment", "score"))


def test_news_one_source_down_still_returns_the_other():
    r = news.latest("BTCUSDT", 5, rss=lambda u: (_ for _ in ()).throw(OSError("dns")), yfn=lambda t: YF_NEW)
    assert r["sources"] == ["yfinance"] and r["errors"][0].startswith("google_news") and len(r["items"]) == 2
    with pytest.raises(KeyError):
        news.latest("NOPE", 5)


def test_news_endpoint_is_offline_in_tests_and_validates():
    r = c.get("/news", params=dict(symbol="btc")).json()
    assert r["symbol"] == "BTCUSDT" and r["disabled"] and r["items"] == []
    assert c.get("/news", params=dict(symbol="zzz")).status_code == 404


def test_coinmeta_shapes_coingecko_payload():
    j = {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1, "description": {"en": "<a href='x'>Bitcoin</a> is a  peer-to-peer system. " * 20},
         "links": {"homepage": ["", "https://bitcoin.org"]}, "genesis_date": "2009-01-03", "categories": ["Layer 1", "PoW", "a", "b", "c"],
         "market_data": {"market_cap": {"usd": 1.5e12}, "total_volume": {"usd": 3e10}, "circulating_supply": 19.9e6, "max_supply": 21e6, "ath": {"usd": 126000}, "ath_date": {"usd": "2025-10-06T00:00:00.000Z"}}}
    m = coinmeta.meta("BTCUSDT", get=lambda cid: j)
    assert m["available"] and m["name"] == "Bitcoin" and m["rank"] == 1 and m["homepage"] == "https://bitcoin.org" and m["ath_date"] == "2025-10-06"
    assert "<" not in m["description"] and len(m["description"]) <= 321 and len(m["categories"]) == 4 and m["market_cap_usd"] == 1.5e12
    bad = coinmeta.meta("ETHUSDT", get=lambda cid: (_ for _ in ()).throw(OSError("429")))
    assert bad["available"] is False and "429" in bad["reason"]
    assert c.get("/assets/meta", params=dict(symbol="TCS.NS")).status_code == 404
    assert c.get("/assets/meta", params=dict(symbol="eth")).json()["available"] is False         # offline in tests


def test_news_drops_template_placeholder_titles():
    from app import news
    assert news._junk("META_TITLE_QUOTE") and news._junk("PAGE_TITLE_X1")
    assert not news._junk("Bitcoin rises above $78,000") and not news._junk("NSE: RELIANCE shares fall") and not news._junk("BTC")


def test_news_google_feed_drops_placeholder_with_publisher_suffix():
    from app import news
    raw = (b"<rss><channel>"
           b"<item><title>META_TITLE_QUOTE - Yahoo Finance</title><link>https://x/1</link><source>Yahoo Finance</source></item>"
           b"<item><title>Bitcoin rises - CoinDesk</title><link>https://x/2</link><source>CoinDesk</source></item>"
           b"</channel></rss>")
    out = news.parse_google(raw)
    assert [i["title"] for i in out] == ["Bitcoin rises"]
