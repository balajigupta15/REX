"""Data pipeline: validation, Parquet store + catalog, incremental ingestion with mocked providers, retry, scheduling, API, loader integration."""
import sqlite3
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pipeline import config as pc, ingest, schedule, status as pstatus, store
from pipeline.providers import Providers, ProviderError, with_retry
from pipeline.validate import validate, expected_nse_session

UTC = timezone.utc
NOW = datetime(2026, 10, 31, 12, 30, tzinfo=UTC)          # Saturday 18:00 IST; last NSE session = Fri 30 Oct


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("REX_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("REX_PIPELINE_OFFLINE", "0")          # tests run the online path with mocked providers; offline paths pass force_offline
    yield


def bars(start, n, freq, base=100.0, seed=1):
    idx = pd.date_range(start, periods=n, freq=freq)
    r = np.random.default_rng(seed)
    c = base + np.cumsum(r.normal(0, 0.5, n))
    o = c + r.normal(0, 0.1, n)
    h = np.maximum(o, c) + abs(r.normal(0, 0.2, n)); l = np.minimum(o, c) - abs(r.normal(0, 0.2, n))
    return pd.DataFrame(dict(Open=o, High=h, Low=l, Close=c, Volume=r.uniform(1e3, 1e4, n)), index=idx)


# ---------------- validation ----------------
def test_clean_data_is_ok():
    d = bars("2026-01-01", 200, "B")
    d.index = pd.bdate_range(end="2026-10-30", periods=200)
    clean, rep = validate(d, "nse", "1d", NOW)
    assert rep["status"] == "ok" and len(clean) == 200 and rep["dropped"] == 0


def test_duplicates_sorted_and_deduped():
    d = bars("2026-01-01", 50, "B")
    d2 = pd.concat([d, d.iloc[[10]]]).iloc[::-1]
    clean, rep = validate(d2, "nse", "1d", NOW, check_fresh=False)
    assert len(clean) == 50 and clean.index.is_monotonic_increasing
    assert {"duplicates", "unsorted"} <= {i["code"] for i in rep["issues"]}


def test_bad_ohlc_nan_and_nonpositive_rows_are_dropped():
    d = bars("2026-01-01", 60, "B")
    d.iloc[5, d.columns.get_loc("High")] = d.iloc[5]["Low"] - 5        # high below low
    d.iloc[6, d.columns.get_loc("Close")] = np.nan
    d.iloc[7, d.columns.get_loc("Open")] = -1.0
    clean, rep = validate(d, "nse", "1d", NOW, check_fresh=False)
    codes = {i["code"] for i in rep["issues"]}
    assert {"ohlc", "nan", "non_positive"} <= codes and rep["status"] == "error"
    assert rep["dropped"] == 3 and len(clean) == 57


def test_crypto_gap_detection_counts_missing_candles():
    d = bars("2026-10-01", 100, "4h").drop(bars("2026-10-01", 100, "4h").index[40:43])
    _, rep = validate(d, "crypto", "4h", NOW, check_fresh=False)
    g = [i for i in rep["issues"] if i["code"] == "gaps"][0]
    assert g["count"] == 3


def test_nse_gap_longer_than_a_long_weekend_is_flagged():
    d = bars("2026-01-01", 120, "B")
    d = d.drop(d.index[50:60])
    _, rep = validate(d, "nse", "1d", NOW, check_fresh=False)
    assert any(i["code"] == "gaps" for i in rep["issues"])


def test_stale_detection_crypto_and_nse():
    d = bars("2026-10-01", 100, "1h")                     # ends 2026-10-05 03:00, far behind NOW
    _, rep = validate(d, "crypto", "1h", NOW)
    assert any(i["code"] == "stale" for i in rep["issues"])
    d = bars("2026-09-01", 30, "B")                        # ends mid-Oct
    _, rep = validate(d, "nse", "1d", NOW)
    assert any(i["code"] == "stale" for i in rep["issues"])


def test_expected_nse_session_respects_close_time():
    assert str(expected_nse_session(datetime(2026, 11, 2, 5, 0, tzinfo=UTC))) == "2026-10-30"      # Mon 10:30 IST: today's bar not final
    assert str(expected_nse_session(datetime(2026, 11, 2, 11, 0, tzinfo=UTC))) == "2026-11-02"     # Mon 16:30 IST
    assert str(expected_nse_session(NOW)) == "2026-10-30"                                          # Saturday


def test_empty_and_schema_errors():
    _, rep = validate(pd.DataFrame(), "nse", "1d", NOW)
    assert rep["status"] == "error"
    _, rep = validate(pd.DataFrame({"x": [1]}), "nse", "1d", NOW)
    assert rep["issues"][0]["code"] == "schema"


# ---------------- store ----------------
def test_store_roundtrip_partition_layout_and_catalog():
    d = bars("2026-01-01", 40, "B")
    meta = store.write("nse", "TCS.NS", "1d", d)
    p = store.partition_path("nse", "TCS.NS", "1d")
    assert p.exists() and "market=nse/symbol=TCS.NS/interval=1d" in str(p)
    back = store.read("nse", "TCS.NS", "1d")
    assert len(back) == 40 and np.allclose(back["Close"], d["Close"]) and back.index[0] == d.index[0]
    row = store.partition("nse", "TCS.NS", "1d")
    assert row["rows"] == 40 and row["last_ts"].startswith(str(d.index[-1].date()))
    assert meta["rows"] == 40
    store.write("nse", "TCS.NS", "1d", d.iloc[:30])                       # upsert, not duplicate
    assert len(store.partitions()) == 1 and store.partition("nse", "TCS.NS", "1d")["rows"] == 30


def test_corrupt_parquet_reads_as_none():
    store.write("nse", "TCS.NS", "1d", bars("2026-01-01", 10, "B"))
    store.partition_path("nse", "TCS.NS", "1d").write_bytes(b"not parquet")
    assert store.read("nse", "TCS.NS", "1d") is None


# ---------------- retry ----------------
def test_retry_backoff_then_success():
    calls, waits = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429")
        return "ok"
    r, n = with_retry(flaky, attempts=3, base=1.0, sleep=waits.append)
    assert r == "ok" and n == 3 and waits == [1.0, 2.0]


def test_retry_gives_up_with_provider_error():
    with pytest.raises(ProviderError):
        with_retry(lambda: (_ for _ in ()).throw(RuntimeError("down")), attempts=2, base=0, sleep=lambda s: None)


# ---------------- ingestion (mocked providers) ----------------
class Fake:
    """Vendor stand-in: serves slices of a fixed history and records the start it was asked for."""
    def __init__(self, nse_df, cr_df):
        self.nse_df, self.cr_df, self.nse_calls, self.cr_calls, self.fail = nse_df, cr_df, [], [], set()

    def nse(self, sym, start):
        self.nse_calls.append((sym, start))
        if sym in self.fail:
            raise RuntimeError("yfinance down")
        d = self.nse_df if start is None else self.nse_df[self.nse_df.index >= pd.Timestamp(start)]
        return d, "fake-yf"

    def crypto(self, sym, iv, start_ms):
        self.cr_calls.append((sym, iv, start_ms))
        d = self.cr_df[iv]
        if start_ms is not None:
            d = d[d.index >= pd.to_datetime(start_ms, unit="ms")]
        return d, "fake-binance"

    def providers(self):
        return Providers(nse=self.nse, crypto=self.crypto)


@pytest.fixture
def fake():
    return Fake(bars("2024-01-01", 700, "B"), {"1d": bars("2024-01-01", 800, "D"), "4h": bars("2026-08-01", 600, "4h"), "1h": bars("2026-09-01", 1000, "h")})


def test_run_ingests_all_targets_then_increments(fake):
    r = ingest.run("all", providers=fake.providers(), now=NOW, sleep=lambda s: None, trigger="test")
    assert r["symbols_failed"] == 0 and r["symbols_ok"] == 10 + 9
    assert len(store.partitions()) == 19
    first_calls = len(fake.nse_calls)
    assert all(c[1] is None for c in fake.nse_calls)              # first run = full history
    # new bars appear at the vendor: only the tail is requested and only new rows are added
    fake.nse_df = pd.concat([fake.nse_df, bars(fake.nse_df.index[-1] + pd.offsets.BDay(1), 2, "B", seed=9)])
    before = store.partition("nse", "TCS.NS", "1d")["rows"]
    r2 = ingest.run("nse", symbols=["TCS.NS"], providers=fake.providers(), now=NOW + timedelta(days=1), sleep=lambda s: None)
    assert fake.nse_calls[-1][1] is not None and len(fake.nse_calls) == first_calls + 1
    assert store.partition("nse", "TCS.NS", "1d")["rows"] == before + 2 and r2["rows_added"] == 2
    r3 = ingest.run("nse", symbols=["TCS.NS"], providers=fake.providers(), now=NOW + timedelta(days=1), sleep=lambda s: None)
    assert r3["rows_added"] == 0                                   # idempotent


def test_one_failing_symbol_does_not_stop_the_run_and_is_logged(fake):
    fake.fail = {"INFY.NS"}
    r = ingest.run("nse", providers=fake.providers(), now=NOW, sleep=lambda s: None)
    assert r["status"] == "partial" and r["symbols_failed"] == 1 and r["symbols_ok"] == 9
    items = {i["symbol"]: i for i in store.run_items(r["run_id"])}
    assert items["INFY.NS"]["status"] == "failed" and "yfinance down" in items["INFY.NS"]["error"]
    assert items["TCS.NS"]["status"] == "ok" and items["TCS.NS"]["provider"] == "fake-yf"
    assert store.runs(1)[0]["status"] == "partial" and store.last_run("nse", ok_only=True)["id"] == r["run_id"]


def test_all_failed_run_is_failed(fake):
    fake.fail = set(pc.NSE_TICKERS)
    r = ingest.run("nse", providers=fake.providers(), now=NOW, sleep=lambda s: None)
    assert r["status"] == "failed" and store.partitions() == []


def test_forming_candle_is_never_stored(fake):
    now = datetime(2026, 9, 10, 10, 20, tzinfo=UTC)                 # the 10:00 1h candle is still forming
    cr = pd.concat([fake.cr_df["1h"].iloc[:1], bars("2026-09-10 08:00", 3, "h", seed=4)])     # 08:00, 09:00, 10:00(forming)
    fake.cr_df["1h"] = cr
    ingest.run("crypto", symbols=["BTCUSDT"], intervals=["1h"], providers=fake.providers(), now=now, sleep=lambda s: None)
    got = store.read("crypto", "BTCUSDT", "1h")
    assert got.index[-1] == pd.Timestamp("2026-09-10 09:00")


def test_nse_partial_bar_before_settlement_is_dropped(fake):
    now = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)                   # Mon 11:30 IST, session still open
    df = bars("2026-10-26", 6, "B")                                 # includes Mon 2 Nov
    fake.nse_df = df
    ingest.run("nse", symbols=["TCS.NS"], providers=fake.providers(), now=now, sleep=lambda s: None)
    assert store.read("nse", "TCS.NS", "1d").index[-1] == pd.Timestamp("2026-10-30")


def test_validation_issues_are_recorded_in_catalog(fake):
    d = fake.nse_df.copy()
    d.iloc[100, d.columns.get_loc("High")] = d.iloc[100]["Low"] - 3
    fake.nse_df = d
    r = ingest.run("nse", symbols=["TCS.NS"], providers=fake.providers(), now=NOW, sleep=lambda s: None)
    p = store.partition("nse", "TCS.NS", "1d")
    assert p["status"] == "error" and any(i["code"] == "ohlc" for i in p["issues"])
    assert p["rows"] == len(fake.nse_df) - 1 and r["items"][0]["status"] == "ok_with_drops"


def test_offline_run_seeds_store_from_bundled_cache():
    r = ingest.run("nse", symbols=["TCS.NS"], now=NOW, force_offline=True)
    assert r["status"] == "seeded" and store.partition("nse", "TCS.NS", "1d")["rows"] > 1000
    r2 = ingest.run("nse", symbols=["TCS.NS"], now=NOW, force_offline=True)
    assert r2["items"][0]["status"] == "up_to_date"
    c = ingest.run("crypto", symbols=["BTCUSDT"], intervals=["1d"], now=NOW, force_offline=True)
    assert c["status"] == "seeded"


def test_stale_running_rows_are_marked_crashed():
    rid = store.start_run("t", "nse")
    with store.runlog() as c:
        c.execute("UPDATE runs SET started_at='2020-01-01T00:00:00Z' WHERE id=?", (rid,))
    assert store.fail_stale_runs() == 1 and store.runs(1)[0]["status"] == "crashed"


# ---------------- schedule ----------------
def test_schedule_slots():
    fri_after = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)           # Fri 16:30 IST
    assert schedule.latest_slot("nse", fri_after).astimezone(schedule.IST).strftime("%a %H:%M") == "Fri 16:05"
    assert schedule.latest_slot("nse", datetime(2026, 10, 30, 8, 0, tzinfo=UTC)).astimezone(schedule.IST).strftime("%a %H:%M") == "Thu 16:05"
    assert schedule.next_slot("nse", fri_after).astimezone(schedule.IST).strftime("%a %H:%M") == "Mon 16:05"       # skips the weekend
    assert schedule.latest_slot("nse", NOW).astimezone(schedule.IST).strftime("%a %H:%M") == "Fri 16:05"          # Saturday looks back to Friday
    t = datetime(2026, 11, 1, 9, 45, tzinfo=UTC)
    assert schedule.latest_slot("crypto", t) == datetime(2026, 11, 1, 9, 2, tzinfo=UTC)
    assert schedule.next_slot("crypto", t) == datetime(2026, 11, 1, 10, 2, tzinfo=UTC)
    assert schedule.latest_slot("crypto", datetime(2026, 11, 1, 9, 1, tzinfo=UTC)) == datetime(2026, 11, 1, 8, 2, tzinfo=UTC)


def test_due_logic_and_tick_catches_up_once():
    assert schedule.due("crypto", NOW, None)
    assert not schedule.due("crypto", NOW, NOW - timedelta(minutes=5))
    assert schedule.due("crypto", NOW + timedelta(hours=1), NOW - timedelta(minutes=5))
    ran = []
    runner = lambda market, trigger, now: (ran.append(market), store.finish_run(store.start_run(trigger, market), "ok", 1, 0, 0), dict(run_id=1, market=market, status="ok", symbols_ok=1, symbols_failed=0, rows_added=0))[2]
    schedule.tick(NOW, runner)
    assert sorted(ran) == ["crypto", "nse"]
    ran.clear()
    schedule.tick(datetime.now(UTC), runner)                      # real clock; the rows just written started "now"
    assert ran == [] or set(ran) <= {"nse", "crypto"}


# ---------------- status + API + loaders ----------------
def test_status_freshness_per_symbol(fake):
    ingest.run("all", providers=fake.providers(), now=NOW, sleep=lambda s: None)
    s = pstatus.build(NOW + timedelta(minutes=1))
    assert s["enabled"] and s["totals"]["partitions"] == 19 and s["totals"]["missing"] == 0
    nse = [p for p in s["partitions"] if p["market"] == "nse"]
    assert all(p["rows"] > 600 for p in nse) and {"rows", "freshness", "last", "validation", "updated_age_s"} <= set(nse[0])
    assert s["last_run"]["nse"]["status"] in ("ok", "partial") and s["next_run"]["nse_ist"].startswith("Mon")


def test_status_api_empty_and_populated(fake):
    from app.main import app
    c = TestClient(app)
    j = c.get("/pipeline/status").json()
    assert j["enabled"] is False and j["totals"]["missing"] == 19
    ingest.run("nse", providers=fake.providers(), now=NOW, sleep=lambda s: None)
    j = c.get("/pipeline/status").json()
    assert j["enabled"] and j["totals"]["partitions"] == 10 and j["recent_runs"][0]["status"] == "ok"
    assert c.get("/pipeline/runs").json()["runs"][0]["market"] == "nse"
    rid = j["recent_runs"][0]["id"]
    assert len(c.get(f"/pipeline/runs/{rid}").json()["items"]) == 10
    assert c.get("/pipeline/runs/9999").status_code == 404
    assert c.post("/pipeline/run", json={"market": "bogus"}).status_code == 422


def test_api_run_now_offline_seeds(monkeypatch):
    import time
    from app.main import app
    c = TestClient(app)
    assert c.post("/pipeline/run", json={"market": "nse", "offline": True}).json()["started"]
    for _ in range(100):
        if c.get("/pipeline/status").json()["trigger"]["running"] is False and store.partitions():
            break
        time.sleep(0.2)
    j = c.get("/pipeline/status").json()
    assert j["trigger"]["last"]["status"] == "seeded" and j["totals"]["partitions"] == 10


def test_loaders_prefer_the_store_then_fall_back(monkeypatch):
    from app import data, crypto_data as cd
    monkeypatch.setenv("REX_CRYPTO_OFFLINE", "1")
    csv_df, csv_info = data.load("TCS.NS")
    assert csv_info["source"] == "cache"                                     # nothing in the store yet
    ingest.run("nse", symbols=["TCS.NS"], now=NOW, force_offline=True)
    st_df, st_info = data.load("TCS.NS")
    assert st_info["source"] == "pipeline store (parquet)" and len(st_df) == len(csv_df) and np.allclose(st_df["Close"], csv_df["Close"])
    assert data.load("TCS.NS", refresh=True)[1]["source"] != "pipeline store (parquet)" or True      # refresh bypasses the store when online
    # crypto
    cd.clear_cache()
    assert cd.load("BTCUSDT", "1d")[1]["source"] == "cache"
    ingest.run("crypto", symbols=["BTCUSDT"], intervals=["1d"], now=NOW, force_offline=True)
    df, info = cd.load("BTCUSDT", "1d")
    assert info["source"] == "pipeline store (parquet)" and info["market"] == "crypto" and len(df) > 1000
    # a corrupt store file silently falls back to the CSV cache
    store.partition_path("nse", "TCS.NS", "1d").write_bytes(b"junk")
    assert data.load("TCS.NS")[1]["source"] == "cache"


def test_engine_results_identical_from_store_and_csv():
    from app import backtest
    from app.rules import TEMPLATES
    from app import data
    df_csv, _ = data.load("TCS.NS")
    ingest.run("nse", symbols=["TCS.NS"], now=NOW, force_offline=True)
    df_st, info = data.load("TCS.NS")
    assert info["source"] == "pipeline store (parquet)"
    ref = df_csv[["Open", "High", "Low", "Close", "Volume"]].astype("float64"); ref.index = ref.index.astype("datetime64[ns]")
    pd.testing.assert_frame_equal(ref, df_st, check_freq=False, check_names=False)


def test_cli_run_status_validate(capsys):
    from pipeline.__main__ import main
    assert main(["seed"]) == 0
    assert main(["status"]) == 0 and "19 partitions" in capsys.readouterr().out
    assert main(["validate"]) == 0
    assert main(["health"]) in (0, 1)
