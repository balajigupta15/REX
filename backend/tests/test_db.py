"""DB adapter: SQLite stays the default/fallback; the sqlite->PostgreSQL SQL translation; /health reports the live backend."""
import os, sqlite3
from contextlib import closing
import pytest
from fastapi.testclient import TestClient
from app import db, config
from app.main import app

SCHEMA = """CREATE TABLE IF NOT EXISTS t_items(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, score REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS t_kv(k TEXT PRIMARY KEY, v TEXT, w REAL);
CREATE TABLE IF NOT EXISTS t_pair(a TEXT, b INTEGER, c TEXT, PRIMARY KEY(a, b))"""
PG = os.environ.get("REX_TEST_PG")


@pytest.fixture
def forced_sqlite(monkeypatch):
    monkeypatch.delenv("REX_DB_URL", raising=False)
    db.init(force=True)
    yield
    db.init(force=True)


def test_sqlite_is_default_and_health_reports_it(forced_sqlite):
    assert db.backend() == "sqlite" and db.info()["error"] is None
    r = TestClient(app).get("/health").json()
    assert r["db"] == "sqlite" and r["ok"]


def test_unreachable_yugabyte_falls_back_to_sqlite(monkeypatch):
    monkeypatch.setenv("REX_DB_URL", "postgresql://yugabyte@127.0.0.1:1/yugabyte")
    monkeypatch.setenv("REX_DB_CONNECT_TIMEOUT", "1")
    try:
        assert db.init(force=True) == "sqlite"
        i = db.info()
        assert i["configured"] and i["error"] and "yugabyte@" in (db._state["url"] or "")
        assert TestClient(app).get("/health").json()["db"] == "sqlite"
        # and everything still works (memory goes to SQLite)
        with closing(db.connect(SCHEMA)) as c:
            c.execute("INSERT INTO t_items(name) VALUES('x')"); c.commit()
        assert config.DB_PATH.exists()
    finally:
        monkeypatch.delenv("REX_DB_URL", raising=False); db.init(force=True)


def test_url_password_is_masked_in_info(monkeypatch):
    monkeypatch.setenv("REX_DB_URL", "postgresql://u:secret123@127.0.0.1:1/x")
    monkeypatch.setenv("REX_DB_CONNECT_TIMEOUT", "1")
    try:
        db.init(force=True)
        assert "secret123" not in str(db.info()) and "secret123" not in str(db._state)
    finally:
        monkeypatch.delenv("REX_DB_URL", raising=False); db.init(force=True)


def test_sql_translation():
    db._learn(SCHEMA.split(";")[0]); db._learn(SCHEMA.split(";")[1]); db._learn(SCHEMA.split(";")[2])
    assert "BIGSERIAL PRIMARY KEY" in db._ddl(SCHEMA) and "DOUBLE PRECISION" in db._ddl(SCHEMA) and "BIGINT" in db._ddl(SCHEMA)
    q, ser = db._dml("INSERT INTO t_items(name,score) VALUES(?,?)")
    assert q.endswith("RETURNING id") and q.count("%s") == 2 and ser == "t_items"
    q, _ = db._dml("INSERT OR IGNORE INTO t_kv(k,v) VALUES(?,?)")
    assert q.endswith("ON CONFLICT DO NOTHING") and "OR IGNORE" not in q
    q, _ = db._dml("INSERT OR REPLACE INTO t_kv VALUES(?,?,?)")
    assert "ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v, w=EXCLUDED.w" in q
    q, _ = db._dml("INSERT OR REPLACE INTO t_pair(a,b,c) VALUES(?,?,?)")
    assert "ON CONFLICT(a,b) DO UPDATE SET c=EXCLUDED.c" in q
    q, _ = db._dml("SELECT * FROM t WHERE name LIKE '%x%' AND n=?")
    assert "'%%x%%'" in q and q.endswith("n=%s")


def test_sqlite_connection_semantics_unchanged(forced_sqlite):
    with closing(db.connect(SCHEMA)) as c:
        cur = c.execute("INSERT INTO t_items(name,score,n) VALUES(?,?,?)", ("a", 1.5, 2))
        assert cur.lastrowid == 1
        c.execute("INSERT OR REPLACE INTO t_kv VALUES('k','v',1.0)"); c.execute("INSERT OR REPLACE INTO t_kv VALUES('k','v2',2.0)"); c.commit()
        r = c.execute("SELECT * FROM t_kv").fetchone()
        assert r["v"] == "v2" and r[0] == "k" and dict(r)["w"] == 2.0 and r.keys() == ["k", "v", "w"]
    assert db.fetchone("SELECT COUNT(*) FROM t_items")[0] == 1 and db.execute("SELECT name FROM t_items")[0]["name"] == "a"


@pytest.mark.skipif(not PG, reason="set REX_TEST_PG=postgresql://... to run against a PostgreSQL-protocol server (Yugabyte YSQL / PG)")
def test_postgres_backend_behaves_like_sqlite():
    assert db.backend() == "yugabyte" and TestClient(app).get("/health").json()["db"] == "yugabyte"
    with closing(db.connect(SCHEMA)) as c:
        assert c.execute("INSERT INTO t_items(name,score,n) VALUES(?,?,?)", ("a", 1.5, 2)).lastrowid == 1
        assert c.execute("INSERT INTO t_items(name,score,n) VALUES(?,?,?)", ("b", 2, True)).lastrowid == 2
        c.execute("INSERT OR REPLACE INTO t_kv VALUES('k','v',1.0)"); c.execute("INSERT OR REPLACE INTO t_kv VALUES('k','v2',2.0)")
        c.execute("INSERT OR IGNORE INTO t_kv VALUES('k','zzz',9.0)"); c.commit()
        r = c.execute("SELECT * FROM t_kv").fetchone()
        assert r["v"] == "v2" and r[0] == "k" and dict(r)["w"] == 2.0
        assert c.execute("SELECT COUNT(*) FROM t_kv WHERE v LIKE '%2'").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO t_kv VALUES('k','dup',1)")
        c.rollback()
        with pytest.raises(sqlite3.OperationalError):
            c.execute("SELECT * FROM nope")
        assert c.execute("SELECT COUNT(*) FROM t_items").fetchone()[0] == 2     # connection still usable after errors
