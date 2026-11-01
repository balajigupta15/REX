"""Database adapter: YugabyteDB (PostgreSQL wire protocol, YSQL) as the primary operational store, SQLite as the always-available fallback.

Everything in Rex that persists operational data (backtest / strategy-run memory, lessons, tracker sessions, journal, crypto memory,
pipeline run log, AI agent runs) opens its connection through `connect()` here and keeps writing plain sqlite-flavoured SQL
(`?` placeholders, INSERT OR REPLACE / OR IGNORE, INTEGER PRIMARY KEY AUTOINCREMENT, REAL).  On SQLite that is passed straight through, unchanged.
On Yugabyte / PostgreSQL a thin translation layer rewrites it (placeholders, upserts, serial ids, types) and the rows still behave like
sqlite3.Row (row["col"], row[0], dict(row), row.keys()).

Choosing the backend (once per process, at startup via `init()`, or lazily on first use):
    REX_DB_URL unset                          -> sqlite (config.DB_PATH / REX_DB)
    REX_DB_URL=postgresql://yugabyte@yugabyte:5433/yugabyte and reachable -> "yugabyte"
    REX_DB_URL set but unreachable            -> logs the reason, falls back to sqlite, `info()["error"]` says why
`/health` reports `"db": "yugabyte" | "sqlite"`.  A fresh Yugabyte database is filled once from the SQLite file (the shipped demo memory),
so the demo data is there on first start (set REX_DB_IMPORT=0 to skip).

Public API (import `from app import db`):
    db.connect(schema=None, timeout=30)   -> connection with .execute/.executescript/.commit/.rollback/.close, rows indexable by name or position
                                             use as `with closing(db.connect(SCHEMA)) as con:` ; `schema` (CREATE TABLE IF NOT EXISTS ...) is applied once
    db.execute(sql, params=(), schema=None) -> list of rows, commits (one-shot write/read helper);  db.fetchone(...), db.fetchall(...)
    db.backend() -> "yugabyte" | "sqlite";  db.info() -> {backend, target, error, ...};  db.init() -> backend name
    db.IntegrityError / db.OperationalError (sqlite3's; raised by BOTH backends: a PG unique violation raises IntegrityError("UNIQUE constraint failed ..."))
"""
from __future__ import annotations
import logging, os, re, sqlite3, threading, time
from contextlib import closing
from . import config

log = logging.getLogger("rex.db")
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError

_state: dict = {"backend": None, "error": None, "url": None, "imported": None}
_lock = threading.RLock()
_schema_done: set = set()
_PK: dict[str, list[str]] = {}          # table -> primary key columns (parsed from the DDL we are given)
_SERIAL: set[str] = set()               # tables with an auto-increment `id`
_COLS: dict[str, list[str]] = {}
_pool: list = []
_POOL_MAX = 8


def url() -> str:
    return os.getenv("REX_DB_URL", "").strip()


def _mask(u: str) -> str:
    return re.sub(r"://([^:/@]*):[^@]*@", r"://\1:***@", u)


# ------------------------------------------------------------------ backend selection
def init(force: bool = False) -> str:
    """Decide the backend. Safe to call many times; the first call (or force=True) probes Yugabyte."""
    with _lock:
        if _state["backend"] and not force:
            return _state["backend"]
        u = url()
        _state.update(url=_mask(u) if u else None, error=None, imported=None)
        _schema_done.clear(); _pool.clear()
        if not u:
            _state["backend"] = "sqlite"
            return "sqlite"
        try:
            c = _pg_connect(u, timeout=float(os.getenv("REX_DB_CONNECT_TIMEOUT", "4")))
            c.execute("SELECT 1"); c.rollback(); c.close()
        except Exception as e:                                    # unreachable, bad credentials, psycopg missing ...
            _state.update(backend="sqlite", error=f"{type(e).__name__}: {str(e).strip()[:200]}")
            log.warning("REX_DB_URL is set but Yugabyte is not reachable (%s); using SQLite at %s", _state["error"], config.DB_PATH)
            return "sqlite"
        _state["backend"] = "yugabyte"
    try:
        _maybe_import()
    except Exception as e:                                        # an import problem must never take the API down
        _state["imported"] = f"failed: {type(e).__name__}: {str(e)[:150]}"
        log.warning("one-time SQLite import into Yugabyte failed: %s", _state["imported"])
    return "yugabyte"


def backend() -> str:
    return _state["backend"] or init()


def info() -> dict:
    b = backend()
    out = {"backend": b, "target": _state["url"] if b == "yugabyte" else str(config.DB_PATH), "configured": bool(url()), "error": _state["error"]}
    if _state["imported"]:
        out["imported_from_sqlite"] = _state["imported"]
    return out


# ------------------------------------------------------------------ public connection API
def connect(schema: str | None = None, timeout: float = 30):
    if backend() == "sqlite":
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(config.DB_PATH, timeout=timeout)
        con.row_factory = sqlite3.Row
        if schema:
            con.executescript(schema)
        return con
    con = PgConn(_checkout())
    if schema:
        con.executescript(schema)
    return con


def execute(sql: str, params=(), schema: str | None = None) -> list:
    with closing(connect(schema)) as con:
        cur = con.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        con.commit()
        return rows


def fetchall(sql: str, params=(), schema: str | None = None) -> list:
    return execute(sql, params, schema)


def fetchone(sql: str, params=(), schema: str | None = None):
    rows = execute(sql, params, schema)
    return rows[0] if rows else None


# ------------------------------------------------------------------ PostgreSQL / Yugabyte side
class Row(tuple):
    """sqlite3.Row look-alike: row[0], row["col"], dict(row), row.keys(), iteration over values."""
    def __new__(cls, cols, vals):
        r = super().__new__(cls, vals)
        r._cols = cols
        return r

    def __getitem__(self, k):
        if isinstance(k, str):
            try:
                return tuple.__getitem__(self, self._cols.index(k))
            except ValueError:
                raise IndexError("No item with that key") from None
        return tuple.__getitem__(self, k)

    def keys(self):
        return list(self._cols)


def _row_factory(cur):
    cols = [d.name for d in cur.description] if cur.description else []
    return lambda vals: Row(cols, vals)


def _pg_connect(u: str, timeout: float = 10):
    import psycopg
    from psycopg.adapt import Dumper

    class _Loose(Dumper):                 # send numbers as untyped literals so PG infers the column type, like SQLite's loose typing
        def dump(self, obj):
            return str(int(obj) if isinstance(obj, bool) else obj).encode()
    c = psycopg.connect(u, connect_timeout=max(1, int(timeout)), row_factory=_row_factory, autocommit=False)
    for t in (int, float, bool):
        c.adapters.register_dumper(t, _Loose)
    return c


def _checkout():
    with _lock:
        while _pool:
            c = _pool.pop()
            if not c.closed and not c.broken:
                return c
    return _pg_connect(url())


def _checkin(c) -> None:
    try:
        if c.closed or c.broken:
            return
        c.rollback()
    except Exception:
        return
    with _lock:
        if len(_pool) < _POOL_MAX:
            _pool.append(c); return
    c.close()


_T_RE = re.compile(r"(?is)^\s*insert\s+or\s+(replace|ignore)\s+into\s+([A-Za-z_]\w*)\s*(\(([^)]*)\))?\s*(.*)$")
_INS_RE = re.compile(r"(?is)^\s*insert\s+into\s+([A-Za-z_]\w*)")


def _ddl(sql: str) -> str:
    s = re.sub(r"(?i)\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "BIGSERIAL PRIMARY KEY", sql)
    s = re.sub(r"(?i)\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"(?i)\bINTEGER\b", "BIGINT", s)
    return s


def _learn(stmt: str) -> None:
    m = re.match(r"(?is)^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_]\w*)\s*\((.*)\)\s*$", stmt)
    if not m:
        return
    t, body = m.group(1), m.group(2)
    if re.search(r"(?i)\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", body):
        _SERIAL.add(t); _PK[t] = ["id"]
    parts, depth, cur = [], 0, ""
    for ch in body:                                              # split the column list on top-level commas
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    parts.append(cur)
    cols = []
    for p in parts:
        p = p.strip()
        tp = re.match(r"(?is)^primary\s+key\s*\(([^)]*)\)", p)
        if tp:
            _PK[t] = [x.strip() for x in tp.group(1).split(",")]
        elif p and not re.match(r"(?i)^(unique|check|foreign|constraint)\b", p):
            name = p.split()[0]
            cols.append(name)
            if re.search(r"(?i)\bPRIMARY\s+KEY\b", p) and t not in _PK:
                _PK[t] = [name]
    _COLS[t] = cols


def _dml(sql: str) -> tuple[str, str | None]:
    """sqlite flavoured DML -> PostgreSQL.  Returns (sql, serial_table_for_RETURNING_or_None)."""
    m = _T_RE.match(sql)
    serial = None
    if m:
        kind, table, _, cols, rest = m.groups()
        collist = [c.strip() for c in cols.split(",")] if cols else list(_COLS.get(table, []))
        head = f"INSERT INTO {table}" + (f"({cols})" if cols else "") + " " + rest.rstrip().rstrip(";")
        if kind.lower() == "ignore":
            sql = head + " ON CONFLICT DO NOTHING"
        else:
            pk = _PK.get(table)
            if not pk or not collist:
                raise OperationalError(f"cannot translate INSERT OR REPLACE into {table}: schema unknown")
            sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in collist if c not in pk)
            sql = head + f" ON CONFLICT({','.join(pk)}) " + (f"DO UPDATE SET {sets}" if sets else "DO NOTHING")
    mi = _INS_RE.match(sql)
    if mi and mi.group(1) in _SERIAL and "returning" not in sql.lower():
        sql = sql.rstrip().rstrip(";") + " RETURNING id"
        serial = mi.group(1)
    return sql.replace("%", "%%").replace("?", "%s"), serial


class _Cur:
    def __init__(self, cur, serial=None):
        self._c = cur; self.lastrowid = None
        if serial and cur.description:
            r = cur.fetchone(); self.lastrowid = r[0] if r else None
            self._c = None; self.description = None; self.rowcount = 1
        else:
            self.description = cur.description; self.rowcount = cur.rowcount

    def fetchone(self):
        return self._c.fetchone() if self._c is not None and self.description else None

    def fetchall(self):
        return self._c.fetchall() if self._c is not None and self.description else []

    def __iter__(self):
        return iter(self.fetchall())


class PgConn:
    """sqlite3.Connection look-alike over a psycopg connection (YSQL). Retries serialization conflicts (Yugabyte's optimistic concurrency)."""

    def __init__(self, raw):
        self._raw = raw; self._log: list = []; self.row_factory = None

    # -- helpers
    def _run(self, sql, params):
        import psycopg
        try:
            return self._raw.execute(sql, tuple(params))
        except psycopg.errors.UniqueViolation as e:
            self._raw.rollback(); self._log.clear()
            raise IntegrityError("UNIQUE constraint failed: " + str(e).splitlines()[0]) from e
        except psycopg.errors.IntegrityError as e:
            self._raw.rollback(); self._log.clear()
            raise IntegrityError(str(e).splitlines()[0]) from e
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as e:
            self._raw.rollback(); self._log.clear()
            raise OperationalError("no such table/column: " + str(e).splitlines()[0]) from e

    def execute(self, sql, params=()):
        import psycopg
        q, serial = _dml(sql)
        for attempt in range(4):
            try:
                cur = self._run(q, params)
                self._log.append((q, params))
                return _Cur(cur, serial)
            except psycopg.errors.SerializationFailure:
                self._raw.rollback(); self._replay(attempt)

    def _replay(self, attempt: int):
        if attempt >= 3:
            raise OperationalError("could not serialize access (gave up after retries)")
        time.sleep(0.05 * (attempt + 1) ** 2)
        log, self._log = self._log, []
        for q, p in log:
            self._raw.execute(q, tuple(p)); self._log.append((q, p))

    def executemany(self, sql, seq):
        for p in seq:
            self.execute(sql, p)

    def executescript(self, script: str):
        key = (url(), hash(script))
        with _lock:
            done = key in _schema_done
        statements = [s.strip() for s in script.split(";") if s.strip()]
        for s in statements:
            _learn(s)
        if done:
            return
        with _lock:
            if key in _schema_done:
                return
            self._raw.rollback()
            ac = self._raw.autocommit
            self._raw.autocommit = True
            try:
                for s in statements:
                    self._raw.execute(_ddl(s).replace("%", "%%"))
            finally:
                self._raw.autocommit = ac
            _schema_done.add(key)

    def commit(self):
        import psycopg
        for attempt in range(4):
            try:
                self._raw.commit(); self._log.clear(); return
            except psycopg.errors.SerializationFailure:
                self._raw.rollback(); self._replay(attempt)

    def rollback(self):
        self._raw.rollback(); self._log.clear()

    def close(self):
        raw, self._raw = self._raw, None
        if raw is not None:
            _checkin(raw)

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.commit() if et is None else self.rollback()


# ------------------------------------------------------------------ one-time import of the shipped SQLite memory + test helpers
def _maybe_import() -> None:
    if os.getenv("REX_DB_IMPORT", "1") == "0" or not config.DB_PATH.exists():
        return
    c = _pg_connect(url())
    try:
        n = c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
        if n:
            return
    finally:
        c.close()
    _state["imported"] = f"{import_sqlite(config.DB_PATH)} rows"


def import_sqlite(path) -> int:
    """Copy every table of a SQLite file into the (empty) Yugabyte DB: tables are created from the original DDL. Returns rows copied."""
    src = sqlite3.connect(path); src.row_factory = sqlite3.Row
    total = 0
    try:
        objs = src.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC").fetchall()
        tables = [o for o in objs if o["type"] == "table"]
        dst = PgConn(_pg_connect(url()))
        try:
            for o in tables:
                ddl = re.sub(r"(?i)^create\s+table\s+(?!if)", "CREATE TABLE IF NOT EXISTS ", o["sql"])
                dst.executescript(ddl)
            for o in objs:
                if o["type"] == "index":
                    dst.executescript(re.sub(r"(?i)^create\s+(unique\s+)?index\s+(?!if)", lambda m: f"CREATE {m.group(1) or ''}INDEX IF NOT EXISTS ", o["sql"]))
            for o in tables:
                t = o["name"]
                rows = src.execute(f"SELECT * FROM {t}").fetchall()
                if not rows:
                    continue
                cols = rows[0].keys()
                q = f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))}) ON CONFLICT DO NOTHING"
                for r in rows:
                    dst.execute(q, [x if not isinstance(x, bytes) else x.decode("utf-8", "replace") for x in tuple(r)])
                total += len(rows)
                if t in _SERIAL:
                    dst.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), (SELECT COALESCE(MAX(id),1) FROM {t}), (SELECT MAX(id) IS NOT NULL FROM {t}))")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return total


def drop_all() -> None:
    """Remove every table (tests and `seed --reset` on Yugabyte). No-op on SQLite."""
    if backend() != "yugabyte":
        return
    c = _pg_connect(url())
    try:
        c.autocommit = True
        for (t,) in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall():
            c.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
    finally:
        c.close()
    with _lock:
        _schema_done.clear()
