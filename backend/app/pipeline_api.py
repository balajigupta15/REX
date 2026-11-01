"""/pipeline/... : read-only status of the data pipeline plus an on-demand run trigger. Purely additive."""
from __future__ import annotations
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
_state = dict(running=False, last=None, error=None)
_lock = threading.Lock()


class RunReq(BaseModel):
    market: str = "all"
    offline: bool | None = None       # True = seed from the bundled cache (no network)


@router.get("/status")
def status():
    from pipeline import status as st
    out = st.build()
    out["trigger"] = dict(running=_state["running"], last=_state["last"], error=_state["error"])
    return out


@router.get("/runs")
def runs(limit: int = 20, market: str | None = None):
    from pipeline import store
    return dict(runs=store.runs(max(1, min(limit, 200)), market))


@router.get("/runs/{run_id}")
def run_detail(run_id: int):
    from pipeline import store
    items = store.run_items(run_id) if store.exists() else []
    if not items:
        raise HTTPException(404, "run not found")
    return dict(run_id=run_id, items=items)


@router.get("/crosscheck")
def crosscheck_latest():
    from pipeline import crosscheck
    return crosscheck.latest() or dict(checked_at=None, reachable=None, results=[], mismatches=[], note="not run yet")


@router.post("/crosscheck")
def crosscheck_now():
    """Compare the stored NSE closes with the official bhavcopy now (uses the network; never in offline mode)."""
    from pipeline import config, crosscheck, store
    if config.offline("nse"):
        raise HTTPException(409, "offline mode: the cross-check needs the network (set REX_OFFLINE=0)")
    if not store.exists():
        raise HTTPException(409, "the store is empty: run the pipeline first")
    return crosscheck.run()


@router.post("/run")
def run_now(req: RunReq):
    """Start one ingestion run in the background (the scheduler container does this automatically; this is the manual button)."""
    if req.market not in ("all", "nse", "crypto"):
        raise HTTPException(422, "market must be all, nse or crypto")
    from pipeline import ingest
    with _lock:
        if _state["running"]:
            raise HTTPException(409, "a run is already in progress")
        _state.update(running=True, error=None)

    def work():
        try:
            r = ingest.run(market=req.market, trigger="api", force_offline=req.offline)
            _state["last"] = dict(run_id=r["run_id"], status=r["status"], symbols_ok=r["symbols_ok"], symbols_failed=r["symbols_failed"], rows_added=r["rows_added"])
        except Exception as e:
            _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            _state["running"] = False
    threading.Thread(target=work, daemon=True, name="pipeline-run").start()
    return dict(started=True, market=req.market)
