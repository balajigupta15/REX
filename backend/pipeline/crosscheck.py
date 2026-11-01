"""Cross-validate yfinance NSE closes against the official NSE bhavcopy (end-of-day file published by the exchange, free, no key).

Why: yfinance is a scraped third-party feed. The bhavcopy is the exchange's own record, so a disagreement is worth a look.
Caveat shown in the UI: yfinance daily bars are split/dividend adjusted, so only the most recent sessions are compared (older closes
legitimately differ after a corporate action). Tolerance: 0.5%. Skipped quietly when NSE archives are unreachable."""
from __future__ import annotations
import csv, io, json, zipfile
from datetime import date, datetime, timedelta, timezone
from . import config, store

TOLERANCE_PCT = 0.5
UA = {"User-Agent": "Mozilla/5.0 (rex-lite)"}
URL_CSV = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
URL_ZIP = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


class BhavError(Exception):
    pass


def _http(url: str) -> bytes:
    import httpx
    r = httpx.get(url, headers=UA, timeout=15.0, follow_redirects=True)
    r.raise_for_status()
    return r.content


def parse_full_csv(raw: bytes) -> dict[str, float]:
    out = {}
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore")), skipinitialspace=True):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if row.get("SERIES") == "EQ":
            try:
                out[row["SYMBOL"]] = float(row["CLOSE_PRICE"])
            except (KeyError, ValueError):
                pass
    return out


def parse_cm_zip(raw: bytes) -> dict[str, float]:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("SctySrs") == "EQ":
            try:
                out[row["TckrSymb"]] = float(row["ClsPric"])
            except (KeyError, ValueError):
                pass
    return out


def bhavcopy_closes(day: date, http=_http) -> dict[str, float]:
    """{NSE symbol: official close} for one session. Tries the classic CSV, then the new CM zip. Raises BhavError (no file = holiday or unreachable)."""
    errs = []
    for url, parse in ((URL_CSV.format(d=day), parse_full_csv), (URL_ZIP.format(d=day), parse_cm_zip)):
        try:
            got = parse(http(url))
            if got:
                return got
            errs.append("empty file")
        except Exception as e:
            errs.append(f"{type(e).__name__} {str(e)[:60]}")
    raise BhavError(f"no bhavcopy for {day} ({'; '.join(errs)})")


def compare(symbol: str, day: date, yf_close: float, official: float, tol: float = TOLERANCE_PCT) -> dict:
    diff = (yf_close / official - 1) * 100 if official else None
    return dict(symbol=symbol, date=str(day), yfinance=round(yf_close, 2), nse=round(official, 2), diff_pct=None if diff is None else round(diff, 3),
                flag="mismatch" if diff is None or abs(diff) > tol else "ok")


def run(symbols: list[str] | None = None, sessions: int = 3, http=_http, now: datetime | None = None, read=None) -> dict:
    """Compare the last `sessions` stored closes per symbol with the bhavcopy. Never raises; reports `reachable` and per-symbol results."""
    read = read or (lambda s: store.read("nse", s, "1d"))
    now = now or datetime.now(timezone.utc)
    syms = symbols or config.NSE_TICKERS
    frames = {s: read(s) for s in syms}
    days = sorted({d.date() for f in frames.values() if f is not None for d in f.index[-sessions:]})
    books, errors = {}, []
    for d in days:
        try:
            books[d] = bhavcopy_closes(d, http)
        except BhavError as e:
            errors.append(str(e))
    results = []
    for s in syms:
        f = frames[s]
        if f is None:
            continue
        for ts, close in f["Close"].iloc[-sessions:].items():
            d = ts.date()
            book = books.get(d)
            if book is None:
                continue
            official = book.get(s.replace(".NS", ""))
            if official is None:
                results.append(dict(symbol=s, date=str(d), yfinance=round(float(close), 2), nse=None, diff_pct=None, flag="not_in_file"))
            else:
                results.append(compare(s, d, float(close), official))
    out = dict(checked_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), reachable=bool(books), tolerance_pct=TOLERANCE_PCT, sessions=sessions, errors=errors[:4],
               results=results, mismatches=[r for r in results if r["flag"] == "mismatch"],
               note="yfinance closes are split/dividend adjusted; bhavcopy is the exchange's raw close. Only the latest sessions are compared.")
    save(out)
    return out


def _file():
    return config.store_dir() / "crosscheck.json"


def save(report: dict) -> None:
    try:
        config.store_dir().mkdir(parents=True, exist_ok=True)
        _file().write_text(json.dumps(report))
    except OSError:
        pass


def latest() -> dict | None:
    try:
        return json.loads(_file().read_text())
    except (OSError, ValueError):
        return None
