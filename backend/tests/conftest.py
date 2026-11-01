import os, sys
from pathlib import Path
os.environ.setdefault("REX_OFFLINE", "1")           # tests use the bundled REAL yfinance cache
os.environ.setdefault("REX_NEWS_OFFLINE", "1")        # no live headline / CoinGecko calls in tests
os.environ.setdefault("REX_TRACKER_LOOP", "0")      # no background price loop in tests
# Tests run against SQLite. To run the same suite against a PostgreSQL-protocol server (Yugabyte YSQL / PG): REX_TEST_PG=postgresql://...
os.environ.pop("REX_DB_URL", None)
if os.environ.get("REX_TEST_PG"):
    os.environ["REX_DB_URL"] = os.environ["REX_TEST_PG"]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from app import config


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mem.sqlite")
    monkeypatch.setattr(config, "MEKO_TOKEN", "")
    from app import livedata
    livedata.clear_cache(); monkeypatch.setattr(livedata, "PROVIDER", None)
    from app import db
    if db._state["backend"] and db._state["backend"] != ("yugabyte" if os.environ.get("REX_TEST_PG") else "sqlite"):
        db.init(force=True)                            # a test (test_db.py) switched the backend; restore the configured one
    if db.init() == "yugabyte":
        db.drop_all()                                  # fresh schema per test on the PostgreSQL-protocol backend
    yield
