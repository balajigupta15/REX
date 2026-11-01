"""HTTP routes of the SEPARATE Crypto section, all under /crypto. The NSE routes are untouched."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import crypto, crypto_data as cd, crypto_memory as cm, crypto_tracker as ct, data, nlparse, rules
from .tracker import TrackerError
from . import saved_results
from typing import Literal

router = APIRouter(prefix="/crypto", tags=["crypto"])
ERRS = (cd.CryptoError, data.DataError, rules.SpecError, TrackerError, nlparse.ParseError)


class CSpec(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "1d"
    template: str = "breakout"
    params: dict = Field(default_factory=dict)
    exits: dict = Field(default_factory=dict)
    cost_bps: float | None = Field(default=None, ge=0, le=500)
    remember: bool = True


class ParseReq(BaseModel):
    text: str = Field(min_length=1, max_length=nlparse.MAX_CHARS)


class CryptoTrade(BaseModel):
    side: Literal["buy", "sell"] = "buy"
    entry_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    qty: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    entered_at: str | None = None


class TrackReq(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "1d"
    template: str
    params: dict = Field(default_factory=dict)
    exits: dict = Field(default_factory=dict)
    cost_bps: float | None = Field(default=None, ge=0, le=500)
    label: str = "original"
    qty: float | None = Field(default=None, gt=0)
    trade: CryptoTrade | None = None


class CloseReq(BaseModel):
    exit_price: float | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=300)
    exited_at: str | None = None


def _e(ex):
    return HTTPException(422, str(ex))


@router.get("/status")
def status():
    return dict(cd.status(), tracker_loop=ct.loop_status(), memory_rows=cm.count(), cost_bps_default=cd.COST_BPS_DEFAULT,
                note="Rex never places orders. Prices: Binance public REST. 24/7 market.")


@router.get("/symbols")
def symbols():
    return dict(symbols=[dict(symbol=k, **v) for k, v in cd.SYMBOLS.items()],
                intervals=[dict(interval=k, label=v["label"], periods_per_year=v["periods"], locked=v["locked"]) for k, v in cd.INTERVALS.items()],
                cost_bps_default=cd.COST_BPS_DEFAULT, templates=rules.TEMPLATES, exits=rules.EXIT_PARAMS)


@router.get("/price")
def price(symbol: str = "BTCUSDT"):
    try:
        return cd.quote(symbol)
    except ERRS as ex:
        raise _e(ex)


@router.get("/prices")
def prices():
    from concurrent.futures import ThreadPoolExecutor

    def one(sym):
        try:
            return cd.quote(sym)
        except ERRS as ex:
            return dict(symbol=sym, error=str(ex))
    with ThreadPoolExecutor(max_workers=len(cd.SYMBOLS)) as ex:  # the three quotes in parallel: one round trip instead of three
        out = list(ex.map(one, list(cd.SYMBOLS)))
    return dict(prices=out, label="Real-time (REST poll about every 3 s). Crypto is 24/7.")


@router.post("/strategies/parse")
def parse(req: ParseReq):
    try:
        return crypto.parse(req.text)
    except nlparse.ParseError as ex:
        raise _e(ex)


@router.post("/backtest")
def backtest(s: CSpec):
    try:
        out = crypto.run_backtest(s.symbol, s.interval, s.template, s.params, s.exits, s.cost_bps)
    except ERRS as ex:
        raise _e(ex)
    df = out.pop("df")
    if s.remember:
        sp = out["spec"]
        out["memory"] = cm.record_run("backtest", sp["symbol"], s.template, sp["params"], sp["exits"], sp["cost_bps"], out["result"], interval=sp["interval"])
    return saved_results.save("crypto", "backtest", out) if s.remember else out


class CWFSpec(CSpec):
    mode: str = "rolling"
    train_bars: int | None = None
    test_bars: int | None = None


@router.post("/walkforward")
def walkforward(s: CWFSpec):
    """Walk-forward on crypto candles (24/7: periods per year follow the interval)."""
    from . import quant, crypto_data as cd
    try:
        df, info = cd.load(s.symbol, s.interval)
        p, e = rules.validate(s.template, s.params, s.exits)
        iv = info["interval"]
        per = cd.INTERVALS[iv]["periods"]
        cost = cd.COST_BPS_DEFAULT if s.cost_bps is None else s.cost_bps
        r = quant.walk_forward(df, s.template, p, e, cost, mode=s.mode, train_bars=s.train_bars or (2 * per if iv == "1d" else None), test_bars=s.test_bars or (per if iv == "1d" else None),
                               periods=per, fmt=lambda t: cd.fmt_ts(t, iv))
    except (cd.CryptoError, ValueError, rules.SpecError) as ex:
        raise HTTPException(422, str(ex))
    return dict(spec=dict(symbol=info["symbol"], interval=iv, template=s.template, params=p, exits=e, cost_bps=cost, market="crypto"), data=info, **r)


@router.post("/refine")
def refine(s: CSpec):
    try:
        out = crypto.run_refine(s.symbol, s.interval, s.template, s.params, s.exits, s.cost_bps)
    except ERRS as ex:
        raise _e(ex)
    out.pop("df")
    if s.remember:
        sp, r = out["spec"], out
        out["memory"] = cm.record_run("refine", sp["symbol"], s.template, r["refined"]["params"], r["refined"]["exits"], sp["cost_bps"], r["refined"]["result"],
                                      flag=r["verdict"]["flag"], interval=sp["interval"])
    return saved_results.save("crypto", "refine", out) if s.remember else out


@router.get("/memory/strategy-runs")
def memory_runs(symbol: str | None = None, template: str | None = None, limit: int = 8, offset: int = 0):
    try:
        return cm.query(symbol, template, max(1, min(limit, 50)), offset=max(0, offset))
    except ERRS as ex:
        raise _e(ex)


@router.post("/tracker/start")
def tracker_start(r: TrackReq):
    try:
        return ct.start(r.symbol, r.interval, r.template, r.params, r.exits, r.cost_bps, r.label, r.trade.model_dump() if r.trade else None, r.qty)
    except ERRS as ex:
        raise _e(ex)


@router.get("/tracker/sessions")
def tracker_sessions(status: str | None = None, limit: int = 100):
    return dict(sessions=ct.list_sessions(status, max(1, min(limit, 500))), loop=ct.loop_status())


@router.get("/tracker/sessions/{sid}")
def tracker_get(sid: str, refresh: bool = True):
    try:
        return ct.get(sid, refresh)
    except ERRS as ex:
        raise HTTPException(404, str(ex))


@router.post("/tracker/sessions/{sid}/close")
def tracker_close(sid: str, r: CloseReq):
    try:
        return ct.close(sid, r.exit_price, r.note, r.exited_at)
    except ERRS as ex:
        raise _e(ex)
