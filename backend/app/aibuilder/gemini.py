"""Gemini REST client for the AI Strategy Builder (free tier: responses are cached in SQLite, calls are kept few, quota errors cool down).
Key from GEMINI_API_KEY (never logged, never returned). Models: AI_MODEL (default gemini-3.6-flash) then gemini-3.1-flash-lite."""
from __future__ import annotations
import json, logging, os, re, threading, time
import httpx
from . import store

log = logging.getLogger("rex.ai.gemini")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PRIMARY = "gemini-3.6-flash"
FALLBACK = ("gemini-3.1-flash-lite",)
TIMEOUT_S = 60.0
_lock = threading.Lock()
_down_until = 0.0
_stats = dict(calls=0, cache_hits=0, errors=0, last_error=None, last_model=None)


class GeminiUnavailable(Exception):
    """Gemini cannot be used right now. reason: no_key | quota | unavailable | timeout | bad_response."""
    def __init__(self, reason: str, msg: str):
        super().__init__(msg)
        self.reason = reason


def key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def enabled() -> bool:
    return bool(key())


def models() -> list[str]:
    first = os.getenv("AI_MODEL", "").strip() or PRIMARY
    return [first] + [m for m in FALLBACK if m != first]


def stats() -> dict:
    return dict(_stats, enabled=enabled(), models=models(), cooldown_s=max(0, int(_down_until - time.time())), cached_responses=store.cache_count())


def reset_cooldown() -> None:
    global _down_until
    _down_until = 0.0


def _scrub(s: str) -> str:
    k = key()
    return s.replace(k, "***") if k else s


def _parse(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        obj = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise GeminiUnavailable("bad_response", "Gemini did not return JSON")
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            raise GeminiUnavailable("bad_response", "Gemini returned malformed JSON")
    if not isinstance(obj, dict):
        raise GeminiUnavailable("bad_response", "Gemini returned a non-object")
    return obj


def generate_json(system: str, user: str, tag: str = "", temperature: float = 0.2, use_cache: bool = True) -> tuple[dict, dict]:
    """-> (object, meta{model, cached}). Raises GeminiUnavailable. Identical (system, user, model list) calls are served from the SQLite cache."""
    global _down_until
    if not enabled():
        raise GeminiUnavailable("no_key", "GEMINI_API_KEY is not set")
    ck = store.cache_key("gen", system, user, models(), temperature)
    if use_cache:
        hit = store.cache_get(ck)
        if hit:
            _stats["cache_hits"] += 1
            return hit["response"], dict(model=hit["model"], cached=True)
    if time.time() < _down_until:
        raise GeminiUnavailable("quota", f"Gemini is cooling down after a quota error ({int(_down_until - time.time())} s left)")
    last = None
    for m in models():
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}}
        for attempt in range(2):
            try:
                _stats["calls"] += 1
                r = httpx.post(URL.format(model=m), headers={"x-goog-api-key": key(), "Content-Type": "application/json"}, json=body, timeout=TIMEOUT_S)
            except httpx.TimeoutException:
                last = GeminiUnavailable("timeout", f"{m}: timed out"); break
            except httpx.HTTPError as e:
                last = GeminiUnavailable("unavailable", f"{m}: network error {type(e).__name__}"); break
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    obj = _parse("".join(p.get("text", "") for p in parts if not p.get("thought")))
                except (KeyError, IndexError, ValueError, TypeError):
                    last = GeminiUnavailable("bad_response", f"{m}: unreadable response"); break
                except GeminiUnavailable as e:
                    last = e; break
                _stats["last_model"] = m
                if use_cache:
                    store.cache_put(ck, m, obj)
                return obj, dict(model=m, cached=False)
            msg = _scrub(r.text[:160]).replace("\n", " ")
            if r.status_code == 429:           # each model has its own free-tier quota: try the next one before giving up
                _stats.update(errors=_stats["errors"] + 1, last_error=f"{m}: quota (429)")
                last = GeminiUnavailable("quota", f"Gemini free-tier quota exhausted ({m}). Try again in about a minute.")
                break
            if r.status_code in (500, 502, 503, 504) and attempt == 0:
                time.sleep(1.0); continue
            last = GeminiUnavailable("unavailable", f"{m}: HTTP {r.status_code} {msg}"); break
    if last is not None and last.reason == "quota":
        _down_until = time.time() + 45
    _stats.update(errors=_stats["errors"] + 1, last_error=str(last))
    log.warning("gemini failed: %s", last)
    raise last or GeminiUnavailable("unavailable", "Gemini unavailable")
