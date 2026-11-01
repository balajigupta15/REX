"""Meko memory mirror over MCP (https://mcp.mekodata.ai/mcp). VERIFIED against the live server (1 Nov 2026) with a real token.

Real tools used (of 42 exposed): conversation_create, memory_add, memory_search (also available: memory_get_all, memory_get_by_id,
memory_update, memory_delete_by_id, context_search, knowledgebase_*, datapack_*, amp_*, artifact_*).
Real argument names: memory_add(text, conversation_id [required], agent_id, metadata = JSON STRING, datapack_id);
memory_search(query, conversation_id [required], agent_id, limit, datapack_id); conversation_create(agent_id, title, metadata, datapack_id).
memory_add stores `text` verbatim and returns the metadata dict on search. memory_search is cross-agent, so results are filtered to agent_id 'rexlite'.
Every call is best-effort: any failure is reported in status() and never breaks the app. SQLite stays the source of truth."""
from __future__ import annotations
import asyncio, json, threading, time
from . import config

AGENT_ID = "rexlite"
HEADERS = lambda: {"Authorization": f"Bearer {config.MEKO_TOKEN}", "User-Agent": "meko-mcp-installer/1.0"}
_state = {"last_error": None, "tools": None, "writes_ok": 0, "writes_failed": 0, "connected": None, "checked_at": 0.0, "conversation_id": None,
          "last_recall_ms": None}
_suspended = {"on": False}


def enabled() -> bool:
    return bool(config.MEKO_TOKEN)


def suspend(on: bool) -> None:
    """Pause mirroring (used while bulk-seeding demo data so we never flood the user's real memory store)."""
    _suspended["on"] = on


def status() -> dict:
    ts = _state["tools"]
    return {"enabled": enabled(), "connected": _state["connected"] if enabled() else False, "endpoint": config.MEKO_URL,
            "agent_id": AGENT_ID, "tool_count": len(ts) if ts else 0, "memory_tools": [t for t in (ts or []) if t.startswith("memory_") or t == "conversation_create"],
            "last_error": _state["last_error"], "writes_ok": _state["writes_ok"], "writes_failed": _state["writes_failed"],
            "last_recall_ms": _state["last_recall_ms"],
            "note": ("live: lessons and journal patterns are mirrored to Meko; SQLite remains the source of truth" if _state["connected"]
                     else ("MEKO_TOKEN set but not connected yet / last attempt failed" if enabled() else "no MEKO_TOKEN set: using SQLite only"))}


async def _session_call(fn):
    try:
        import httpx2 as httpx_lib
    except ImportError:
        import httpx as httpx_lib
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    hc = httpx_lib.AsyncClient(headers=HEADERS(), timeout=25)
    async with streamable_http_client(config.MEKO_URL, http_client=hc) as (r, w, *_):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def _run(coro_fn):
    """Run an async MCP call from sync code (works inside or outside a running event loop)."""
    box = {}
    def target():
        try:
            box["v"] = asyncio.run(asyncio.wait_for(_session_call(coro_fn), 40))
        except BaseException as e:   # includes ExceptionGroup from anyio task groups
            subs = getattr(e, "exceptions", None) or [e]
            box["e"] = "; ".join(repr(x)[:160] for x in subs)
    t = threading.Thread(target=target); t.start(); t.join(50)
    if "e" in box or "v" not in box:
        raise RuntimeError(box.get("e", "timeout"))
    return box["v"]


def _txt(res) -> str:
    return "".join(getattr(x, "text", "") for x in res.content)


def _ok(res):
    if getattr(res, "is_error", False) or getattr(res, "isError", False):
        raise RuntimeError(f"tool error: {_txt(res)[:200]}")
    return res


async def _conversation(s, title: str) -> str:
    if _state["conversation_id"]:
        return _state["conversation_id"]
    kw = {"agent_id": AGENT_ID, "title": title}
    if config.MEKO_DATAPACK_ID:
        kw["datapack_id"] = config.MEKO_DATAPACK_ID
    cid = json.loads(_txt(_ok(await s.call_tool("conversation_create", kw))))["id"]
    _state["conversation_id"] = cid
    return cid


def verify(force: bool = False) -> bool:
    """list_tools on the live server; caches the tool names and the connected flag (2 min)."""
    if not enabled():
        return False
    if not force and _state["connected"] is not None and time.time() - _state["checked_at"] < 120:
        return bool(_state["connected"])
    async def fn(s):
        return [t.name for t in (await s.list_tools()).tools]
    try:
        _state["tools"] = _run(fn); _state["connected"] = True; _state["last_error"] = None
    except Exception as e:
        _state["connected"] = False; _state["last_error"] = str(e)[:300]
    _state["checked_at"] = time.time()
    return bool(_state["connected"])


def verify_async() -> None:
    if enabled() and (_state["connected"] is None or time.time() - _state["checked_at"] > 120):
        _state["checked_at"] = time.time()
        threading.Thread(target=verify, kwargs={"force": True}, daemon=True).start()


_threads: list[threading.Thread] = []
_breaker = {"until": 0.0}


def add_many(items: list[tuple[str, str, dict]]) -> bool:
    """Write memories in ONE MCP session via memory_add(text='[kind] text', metadata=JSON string).
    A failure opens a 2-minute circuit breaker so a bad token never slows the app."""
    if not enabled() or _suspended["on"] or time.time() < _breaker["until"] or not items:
        return False
    async def fn(s):
        cid = await _conversation(s, "Rex Lite memory")
        for kind, text, record in items:
            kw = {"text": f"[{kind}] {text}"[:1800], "conversation_id": cid, "agent_id": AGENT_ID,
                  "metadata": json.dumps({"kind": kind, **{k: v for k, v in record.items() if isinstance(v, (str, int, float, bool))}}, default=str)[:3000]}
            if config.MEKO_DATAPACK_ID:
                kw["datapack_id"] = config.MEKO_DATAPACK_ID
            _ok(await s.call_tool("memory_add", kw))
    try:
        _run(fn); _state["writes_ok"] += len(items); _state["last_error"] = None; _state["connected"] = True
        return True
    except Exception as e:
        _state["conversation_id"] = None
        _state["writes_failed"] += len(items); _state["last_error"] = str(e)[:300]
        _breaker["until"] = time.time() + 120
        return False


def mirror_async(items: list[tuple[str, str, dict]]) -> None:
    if _suspended["on"]:
        return
    th = threading.Thread(target=add_many, args=(items,), daemon=True)
    th.start(); _threads.append(th)


def join(timeout: float = 60) -> None:
    for th in list(_threads):
        th.join(timeout)
    _threads.clear()


def search(query: str, limit: int = 10) -> list[dict]:
    """memory_search on Meko, filtered to this app's agent_id. Returns [{id, text, metadata, score, created_at}] ([] on any failure)."""
    if not enabled():
        return []
    async def fn(s):
        cid = await _conversation(s, "Rex Lite memory")
        kw = {"query": query, "conversation_id": cid, "agent_id": AGENT_ID, "limit": limit}
        if config.MEKO_DATAPACK_ID:
            kw["datapack_id"] = config.MEKO_DATAPACK_ID
        return json.loads(_txt(_ok(await s.call_tool("memory_search", kw))) or "{}")
    t0 = time.time()
    try:
        res = _run(fn)
        _state["connected"] = True; _state["last_error"] = None; _state["last_recall_ms"] = int((time.time() - t0) * 1000)
    except Exception as e:
        _state["conversation_id"] = None; _state["last_error"] = str(e)[:300]
        return []
    return [dict(id=r.get("id"), text=r.get("memory"), metadata=r.get("metadata") or {}, score=round(r.get("score") or 0, 3), created_at=r.get("created_at"))
            for r in res.get("results", []) if r.get("agent_id", AGENT_ID) == AGENT_ID]


def delete(memory_id: str) -> bool:
    async def fn(s):
        cid = await _conversation(s, "Rex Lite memory")
        kw = {"memory_id": memory_id, "conversation_id": cid, "agent_id": AGENT_ID}
        _ok(await s.call_tool("memory_delete_by_id", kw)); return True
    try:
        return _run(fn)
    except Exception as e:
        _state["last_error"] = str(e)[:300]; return False
