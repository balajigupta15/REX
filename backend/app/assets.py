"""Global asset search across asset classes (NSE stocks Rex has history for + BTC/ETH/SOL). Purely additive."""
from __future__ import annotations
import difflib
import re
from . import config, crypto_data

NSE_NAMES = {
    "RELIANCE.NS": ("Reliance Industries", "oil gas telecom retail jio"), "JIOFIN.NS": ("Jio Financial Services", "jio finance nbfc"),
    "TCS.NS": ("Tata Consultancy Services", "it software tata"), "INFY.NS": ("Infosys", "it software"),
    "HDFCBANK.NS": ("HDFC Bank", "bank hdfc"), "ICICIBANK.NS": ("ICICI Bank", "bank icici"), "SBIN.NS": ("State Bank of India", "sbi bank"),
    "ITC.NS": ("ITC Limited", "fmcg cigarettes hotels"), "LT.NS": ("Larsen & Toubro", "l&t engineering construction"),
    "BHARTIARTL.NS": ("Bharti Airtel", "airtel telecom"),
}


def catalog() -> list[dict]:
    out = []
    for t in config.DEFAULT_TICKERS:
        name, kw = NSE_NAMES.get(t, (t.split(".")[0], ""))
        out.append(dict(symbol=t, short=t.split(".")[0], name=name, asset_class="stock", market="NSE", keywords=kw))
    for s, v in crypto_data.SYMBOLS.items():
        out.append(dict(symbol=s, short=v["short"], name=v["name"], asset_class="crypto", market="Crypto (Binance USDT, 24/7)",
                        keywords=f"{v['short']}/USDT {v['yf']} coin cryptocurrency"))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9&]+", " ", s.lower()).strip()


def _score(q: str, a: dict) -> float:
    """Higher is better, 0 = no match. Exact symbol > symbol prefix > name-word prefix > substring > fuzzy."""
    short, sym, name = a["short"].lower(), a["symbol"].lower(), _norm(a["name"])
    words = name.split() + _norm(a["keywords"]).split()
    if q in (short, sym):
        return 100
    if short.startswith(q) or sym.startswith(q):
        return 90 - min(len(short) - len(q), 10)
    if name.startswith(q):
        return 85
    if any(w.startswith(q) for w in name.split()):
        return 75
    if any(w.startswith(q) for w in words):
        return 60
    if q in name or q in short:
        return 50
    if len(q) >= 3:        # fuzzy-ish: typos like "relience", "bitcon", "infosis"
        best = max(difflib.SequenceMatcher(None, q, w).ratio() for w in [short, *name.split(), name])
        if best >= 0.72:
            return 30 + 20 * best
    return 0


def search(q: str, limit: int = 12) -> list[dict]:
    q = _norm(q or "")
    items = catalog()
    if not q:
        return [{k: v for k, v in a.items() if k != "keywords"} for a in items[:limit]]
    scored = sorted(((s, a) for a in items if (s := _score(q, a)) > 0), key=lambda x: (-x[0], x[1]["asset_class"] != "stock", x[1]["symbol"]))
    return [{k: v for k, v in a.items() if k != "keywords"} for _, a in scored[:limit]]


def quote(symbol: str) -> dict:
    """Latest price for the asset view. Stocks: last daily close or ~15 min delayed intraday. Crypto: real-time REST price. Never raises for a known symbol."""
    sym = (symbol or "").strip().upper()
    a = next((x for x in catalog() if x["symbol"] == sym or x["short"] == sym), None)
    if a is None:
        raise KeyError(symbol)
    out = {k: v for k, v in a.items() if k != "keywords"}
    try:
        if a["asset_class"] == "crypto":
            q = crypto_data.quote(a["symbol"])
            out.update(price=q["price"], price_time=q.get("time_utc"), live=bool(q.get("live")) and not q.get("stale"), source=q.get("provider"),
                       state="open 24/7", currency="USD")
        else:
            from . import livedata
            s = livedata.snapshot(a["symbol"])
            q, mk = s["quote"], s["market"]
            out.update(price=q["price"], price_time=q["price_time"], live=not q["stale"], source=q["source"], state=mk["label"], currency="INR",
                       last_close=q["last_close"], last_close_date=q["last_close_date"], next_open=mk.get("next_open"))
    except Exception as e:
        out.update(price=None, error=f"{type(e).__name__}: {str(e)[:120]}")
    return out


def resolve(symbol: str) -> str:
    """Canonical catalog symbol for 'btc', 'BTCUSDT', 'tcs', 'TCS.NS'. Raises KeyError when unknown."""
    sym = (symbol or "").strip().upper()
    a = next((x for x in catalog() if x["symbol"] == sym or x["short"] == sym), None)
    if a is None:
        raise KeyError(symbol)
    return a["symbol"]
