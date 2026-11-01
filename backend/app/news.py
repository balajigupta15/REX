"""Latest headlines per asset from two free sources: Google News RSS and yfinance news. Headlines, source, time and a link out.
No sentiment scoring is done here: Rex shows what was published, not what it thinks of it."""
from __future__ import annotations
import os, re, time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from . import assets, crypto_data as cd, config

TTL_S = 600
_cache: dict[str, tuple[float, dict]] = {}
QUERY = {"BTCUSDT": "Bitcoin BTC", "ETHUSDT": "Ethereum ETH", "SOLUSDT": "Solana SOL"}


_JUNK = re.compile(r"^[A-Z0-9]+(_[A-Z0-9]+)+$")


def _junk(title: str) -> bool:
    """Some feeds return template placeholders such as META_TITLE_QUOTE instead of a headline; those are not news."""
    return bool(_JUNK.match(title))


def _query(symbol: str, name: str) -> str:
    if symbol in QUERY:
        return QUERY[symbol] + " crypto"
    base = symbol.replace(".NS", "")
    return f'"{name}" OR {base} NSE share'


def fetch_rss(url: str) -> bytes:
    import httpx
    r = httpx.get(url, timeout=10.0, headers={"User-Agent": "Mozilla/5.0 (rex-lite)"}, follow_redirects=True)
    r.raise_for_status()
    return r.content


def parse_google(raw: bytes, limit: int = 12) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        src = it.find("source")
        pub = None
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
        publisher = (src.text or "").strip() if src is not None else ""
        if publisher and title.endswith(" - " + publisher):          # Google appends the publisher to the headline
            title = title[: -len(publisher) - 3].rstrip()
        if not title or _junk(title) or _junk(title.split(" - ")[0].strip()):                   # e.g. "META_TITLE_QUOTE - Yahoo Finance"
            continue
        out.append(dict(title=title, url=link, publisher=publisher or "Google News", published=pub.strftime("%Y-%m-%dT%H:%M:%SZ") if pub else None, via="google_news"))
        if len(out) >= limit:
            break
    return out


def yf_news(ticker: str) -> list[dict]:
    import yfinance as yf
    return yf.Ticker(ticker).news or []


def parse_yf(items: list[dict], limit: int = 12) -> list[dict]:
    out = []
    for it in items:
        c = it.get("content") if isinstance(it.get("content"), dict) else it          # yfinance >= 0.2.5x nests the payload under "content"
        title = (c.get("title") or "").strip()
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link") or "").strip()
        if not title or not url or _junk(title):
            continue
        pub = c.get("pubDate") or c.get("displayTime")
        if pub is None and c.get("providerPublishTime"):
            pub = datetime.fromtimestamp(int(c["providerPublishTime"]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pub = str(pub).replace(".000Z", "Z") if pub else None
        publisher = ((c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else None) or c.get("publisher") or "Yahoo Finance"
        out.append(dict(title=title, url=url, publisher=publisher, published=pub, via="yfinance"))
        if len(out) >= limit:
            break
    return out


def latest(symbol: str, limit: int = 8, rss=fetch_rss, yfn=yf_news, now: float | None = None) -> dict:
    """Merged, de-duplicated headlines newest first. A failing source is reported, never raised. Cached for 10 minutes."""
    info = next((a for a in assets.catalog() if a["symbol"] == symbol), None)
    if info is None:
        raise KeyError(symbol)
    if rss is fetch_rss and os.getenv("REX_NEWS_OFFLINE", "").strip().lower() not in ("", "0", "false", "no"):
        return dict(symbol=symbol, name=info["name"], items=[], sources=[], errors=[], disabled=True, fetched_at=None,
                    note="News is switched off (REX_NEWS_OFFLINE=1). Rex does not score or interpret headlines.")
    key = f"{symbol}:{limit}"
    t = time.time() if now is None else now
    if key in _cache and t - _cache[key][0] < TTL_S and rss is fetch_rss:
        return _cache[key][1]
    items, errors, used = [], [], []
    try:
        url = f"https://news.google.com/rss/search?q={quote_plus(_query(symbol, info['name']))}+when:7d&hl=en-IN&gl=IN&ceid=IN:en"
        g = parse_google(rss(url), limit * 2)
        items += g; used.append("google_news")
    except Exception as e:
        errors.append(f"google_news: {type(e).__name__} {str(e)[:80]}")
    try:
        tk = cd.SYMBOLS[symbol]["yf"] if info["asset_class"] == "crypto" else symbol
        y = parse_yf(yfn(tk), limit * 2)
        items += y; used.append("yfinance")
    except Exception as e:
        errors.append(f"yfinance: {type(e).__name__} {str(e)[:80]}")
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: x["published"] or "", reverse=True):
        k = " ".join(it["title"].lower().split())[:80]
        if k in seen:
            continue
        seen.add(k); uniq.append(it)
    out = dict(symbol=symbol, name=info["name"], items=uniq[:limit], sources=used, errors=errors, fetched_at=datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               note="Headlines are listed as published by their sources. Rex does not score or interpret them.")
    if uniq and rss is fetch_rss:
        _cache[key] = (t, out)
    return out
