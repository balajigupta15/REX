"""Coin metadata from CoinGecko's free public API (no key): name, rank, market cap, supply, homepage, short description.
Cached 6 h in memory. Metadata only: prices and candles come from the exchange chain, never from here."""
from __future__ import annotations
import os, re, time
from . import crypto_data as cd

TTL_S = 6 * 3600
IDS = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}
_cache: dict[str, tuple[float, dict]] = {}


def fetch(coin_id: str) -> dict:
    return cd.http_get(f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                       dict(localization="false", tickers="false", market_data="true", community_data="false", developer_data="false", sparkline="false"), timeout=10.0)


def _plain(html: str, n: int = 320) -> str:
    s = re.sub(r"<[^>]+>", "", html or "")
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def shape(j: dict) -> dict:
    md = j.get("market_data") or {}
    usd = lambda k: (md.get(k) or {}).get("usd")
    return dict(id=j.get("id"), name=j.get("name"), symbol=(j.get("symbol") or "").upper(), rank=j.get("market_cap_rank"),
                description=_plain((j.get("description") or {}).get("en", "")), categories=(j.get("categories") or [])[:4],
                homepage=next((u for u in (j.get("links") or {}).get("homepage", []) if u), None), genesis_date=j.get("genesis_date"),
                market_cap_usd=usd("market_cap"), volume_24h_usd=usd("total_volume"), circulating_supply=md.get("circulating_supply"),
                max_supply=md.get("max_supply"), ath_usd=usd("ath"), ath_date=((md.get("ath_date") or {}).get("usd") or "")[:10] or None)


def meta(symbol: str, get=fetch) -> dict:
    if symbol not in IDS:
        raise KeyError(symbol)
    live = get is fetch
    if live and os.getenv("REX_NEWS_OFFLINE", "").strip().lower() not in ("", "0", "false", "no"):
        return dict(symbol=symbol, available=False, reason="external lookups switched off (REX_NEWS_OFFLINE=1)", source="CoinGecko")
    hit = _cache.get(symbol)
    if live and hit and time.time() - hit[0] < TTL_S:
        return hit[1]
    try:
        out = dict(shape(get(IDS[symbol])), symbol=symbol, available=True, source="CoinGecko (free public API)", url=f"https://www.coingecko.com/en/coins/{IDS[symbol]}")
    except Exception as e:
        return dict(symbol=symbol, available=False, reason=f"{type(e).__name__}: {str(e)[:80]}", source="CoinGecko")
    if live:
        _cache[symbol] = (time.time(), out)
    return out
