from __future__ import annotations
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import os
from contextlib import asynccontextmanager
from . import saved_results, assets, coinmeta, costs, db, news, quant, crypto_api, pipeline_api, crypto_tracker, backtest, config, data, journal, livedata, memory, meko, nlparse, refine, research, rules, seed, strategy_memory, tracker

@asynccontextmanager
async def _lifespan(_app):
    db.init()                                            # pick Yugabyte (REX_DB_URL, reachable) or fall back to SQLite; logged
    if os.getenv("REX_TRACKER_LOOP", "1") != "0":       # background price loop for the Live Strategy Tracker (default every 60 s)
        tracker.start_loop()
        if os.getenv("REX_CRYPTO_LOOP", "1") != "0":     # Crypto tracker loop (24/7); off together with the NSE loop in tests
            crypto_tracker.start_loop()
    yield
    tracker.stop_loop()
    crypto_tracker.stop_loop()


app = FastAPI(title="Rex Lite API", version="0.1.0", lifespan=_lifespan,
              description="Define a rule-based NSE strategy, backtest it with costs, refine it honestly, and let memory warn you. Research tool, not investment advice.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Spec(BaseModel):
    ticker: str = "RELIANCE.NS"
    template: str = "breakout"
    params: dict = Field(default_factory=dict)
    exits: dict = Field(default_factory=dict)
    cost_bps: float | None = Field(default=None, ge=0, le=500)
    remember: bool = True
    cost_model: str | None = Field(default=None, description="bps (default, unchanged) | india_delivery | india_intraday")
    slippage_bps: float | None = Field(default=None, ge=0, le=200, description="extra slippage per leg, in bp")
    trade_value: float | None = Field(default=None, gt=0, description="rupees per trade for the flat charges of the Indian model")


def _costspec(s: Spec) -> dict:
    try:
        return costs.resolve(s.cost_model, s.cost_bps, s.slippage_bps, s.trade_value, default_bps=config.COST_BPS_DEFAULT)
    except ValueError as ex:
        raise HTTPException(422, str(ex))


def _prep(s: Spec):
    try:
        df, info = data.load(s.ticker)
        p, e = rules.validate(s.template, s.params, s.exits)
    except (data.DataError, rules.SpecError) as ex:
        raise HTTPException(422, str(ex))
    return df, info, p, e


def _cost(s: Spec) -> float:
    return config.COST_BPS_DEFAULT if s.cost_bps is None else s.cost_bps


class WFSpec(Spec):
    mode: str = Field(default="rolling", description="rolling | anchored")
    train_bars: int | None = Field(default=None, ge=120, le=3000)
    test_bars: int | None = Field(default=None, ge=20, le=1000)


@app.post("/walkforward")
def run_walkforward(s: WFSpec):
    """Walk-forward analysis: tune on each train window, trade the next unseen test window, stitch the out-of-sample results."""
    df, info, p, e = _prep(s)
    cs = _costspec(s)
    try:
        r = quant.walk_forward(df, s.template, p, e, cs["cost_bps"], cost_legs=cs["legs"], mode=s.mode, train_bars=s.train_bars, test_bars=s.test_bars)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return dict(spec=dict(ticker=info["ticker"], template=s.template, params=p, exits=e, cost_bps=cs["cost_bps"], cost_model=s.cost_model or "bps", slippage_bps=s.slippage_bps, trade_value=s.trade_value), data=info, cost=cs["label"], **r)


class LabSpec(BaseModel):
    template: str = "breakout"
    params: dict = Field(default_factory=dict)
    exits: dict = Field(default_factory=dict)
    cost_bps: float | None = Field(default=None, ge=0, le=500)
    crypto_cost_bps: float | None = Field(default=None, ge=0, le=500)
    cost_model: str | None = None
    slippage_bps: float | None = Field(default=None, ge=0, le=200)
    trade_value: float | None = Field(default=None, gt=0)


@app.post("/lab/run")
def run_lab(s: LabSpec):
    """Strategy Lab: one strategy on all 10 NSE stocks + BTC/ETH/SOL (daily), compared side by side."""
    try:
        p, e = rules.validate(s.template, s.params, s.exits)
        cs = costs.resolve(s.cost_model, s.cost_bps, s.slippage_bps, s.trade_value, default_bps=config.COST_BPS_DEFAULT)
    except (rules.SpecError, ValueError) as ex:
        raise HTTPException(422, str(ex))
    out = quant.lab(s.template, p, e, cs["cost_bps"], s.crypto_cost_bps, legs=cs["legs"])
    return dict(out, cost=cs["label"], cost_model=s.cost_model or "bps", slippage_bps=s.slippage_bps, trade_value=s.trade_value)


@app.get("/costs/models")
def cost_models(kind: str = "delivery", trade_value: float = 100000, slippage_bps: float = 5):
    """The Indian cost model with its rupee breakdown per leg, the rates used and the source."""
    if kind not in ("delivery", "intraday") or trade_value <= 0:
        raise HTTPException(422, "kind must be delivery or intraday and trade_value positive")
    return dict(models=costs.MODELS, rates=costs.RATES, source=costs.SOURCE, formulas=costs.__doc__, example=costs.india(kind, trade_value, slippage_bps))


@app.get("/assets/search")
def assets_search(q: str = "", limit: int = 12):
    """Global search across asset classes: NSE stocks and BTC/ETH/SOL. Case-insensitive; symbol, company/coin name, prefix and typo tolerant."""
    return dict(query=q, results=assets.search(q, max(1, min(limit, 50))))


@app.get("/assets/meta")
def asset_meta(symbol: str):
    """CoinGecko coin metadata (crypto only). Metadata, never prices."""
    try:
        return coinmeta.meta(assets.resolve(symbol))
    except KeyError:
        raise HTTPException(404, "no CoinGecko metadata for that symbol (crypto only)")


@app.get("/news")
def asset_news(symbol: str, limit: int = 8):
    """Latest headlines (Google News RSS + yfinance news) for one catalog asset. Headlines only: no sentiment is computed."""
    try:
        return news.latest(assets.resolve(symbol), max(1, min(limit, 20)))
    except KeyError:
        raise HTTPException(404, "unknown symbol")


@app.get("/assets/quote")
def assets_quote(symbol: str):
    try:
        return assets.quote(symbol)
    except KeyError:
        raise HTTPException(404, f"Unknown asset '{symbol}'. Search for a stock Rex has history for, or BTC / ETH / SOL.")


@app.get("/health")
def health():
    return {"ok": True, "paper_only": True, "db": db.backend(), "memory": memory.status()["backend"],
            "strategy_parser": "gemini" if nlparse.gemini_enabled() else "rules"}


@app.get("/strategies/templates")
def templates():
    return dict(templates=rules.TEMPLATES, exits=rules.EXIT_PARAMS, tickers=config.DEFAULT_TICKERS,
                cost_bps_default=config.COST_BPS_DEFAULT, cost_note="ASSUMPTION: round-trip cost in basis points, editable",
                locked_days=config.LOCKED_DAYS, tag_groups=rules.TAG_GROUPS)


class ParseReq(BaseModel):
    text: str = Field(min_length=1, max_length=nlparse.MAX_CHARS)


@app.post("/strategies/parse")
def parse_strategy(req: ParseReq):
    """Plain English -> the spec /backtest accepts (`strategy`), plus confidence, assumptions and unsupported parts.
    Uses Gemini when GEMINI_API_KEY is set (every field re-validated), else/on failure the deterministic rules parser.
    `strategy` is null when no supported entry rule was found: Rex never guesses."""
    try:
        return nlparse.parse(req.text)
    except nlparse.ParseError as ex:
        raise HTTPException(422, str(ex))


@app.post("/backtest")
def run_backtest(s: Spec):
    df, info, p, e = _prep(s)
    cs = _costspec(s)
    cost = cs["cost_bps"]
    res = backtest.run(df, s.template, p, e, cost, cost_legs=cs["legs"])
    out = dict(spec=dict(ticker=info["ticker"], template=s.template, params=p, exits=e, cost_bps=cost, cost_model=s.cost_model or "bps", slippage_bps=s.slippage_bps, trade_value=s.trade_value), data=info, result=res,
               notes=["Signals at the close, entries at the next open, long only, one position at a time.",
                      f"Costs: {cs['label']} (assumption). Stop is checked before take-profit inside a bar.",
                      f"Locked final period: {res['locked']['start']} to {res['locked']['end']}; refinement never tunes on it.",
                      "Educational research on historical data, not investment advice."])
    if s.remember:
        out["memory"] = memory.record("backtest", info["ticker"], s.template, p, e, cost, res)
        out["memory"]["strategy_run"] = strategy_memory.record_run("backtest", info["ticker"], s.template, p, e, cost, res, df=df)
    return saved_results.save("nse", "backtest", out) if s.remember else out


@app.post("/refine")
def run_refine(s: Spec):
    df, info, p, e = _prep(s)
    cs = _costspec(s)
    cost = cs["cost_bps"]
    r = refine.refine(df, s.template, p, e, cost, cost_legs=cs["legs"])
    out = dict(spec=dict(ticker=info["ticker"], template=s.template, cost_bps=cost, cost_model=s.cost_model or "bps", slippage_bps=s.slippage_bps, trade_value=s.trade_value), data=info, **r)
    if s.remember:
        extra = memory.refine_lesson(info["ticker"], s.template, r["verdict"], r["original"]["result"], r["refined"]["result"])
        out["memory"] = memory.record("refine", info["ticker"], s.template, r["refined"]["params"], r["refined"]["exits"], cost,
                                      r["refined"]["result"], flag=r["verdict"]["flag"], extra_lessons=[extra] if extra else None)
        out["memory"]["strategy_run"] = strategy_memory.record_run("refine", info["ticker"], s.template, r["refined"]["params"], r["refined"]["exits"], cost,
                                                                   r["refined"]["result"], flag=r["verdict"]["flag"], df=df)
    return saved_results.save("nse", "refine", out) if s.remember else out


@app.get("/results")
def results_history(market: str = "nse", limit: int = 20, offset: int = 0):
    if market not in ("nse", "crypto"):
        raise HTTPException(422, "market must be nse or crypto")
    return saved_results.history(market, max(1, min(limit, 100)), max(0, offset))


@app.get("/results/{rid}")
def saved_result(rid: str):
    result = saved_results.get(rid)
    if result is None:
        raise HTTPException(404, "No saved result. Older runs have summaries only; run them again to save a full result.")
    return result


@app.post("/memory/check")
def memory_check(s: Spec):
    df, info, p, e = _prep(s)
    tf = rules.tag_frame(df)
    cur = {g: str(tf[g].iloc[-1]) for g in rules.TAG_GROUPS}
    return dict(ticker=info["ticker"], template=s.template, **memory.check(info["ticker"], s.template, p, e, cur, info["last"]))


@app.get("/memory/status")
def memory_status():
    return memory.status()


@app.get("/memory/runs")
def memory_runs(limit: int = 50, ticker: str | None = None, template: str | None = None, offset: int = 0):
    return memory.list_runs(max(1, min(limit, 500)), ticker, template, max(0, offset))


@app.get("/memory/strategy-runs")
def memory_strategy_runs(ticker: str | None = None, template: str | None = None, limit: int = 8, offset: int = 0):
    """Past backtests / refinements / tracker sessions of a similar strategy (same template or ticker), each with the real price move that
    followed (5 / 20 trading days) and a one-line rule-based lesson. `last_time` is the closest match."""
    try:
        return strategy_memory.query(ticker, template, max(1, min(limit, 50)), offset=max(0, offset))
    except data.DataError as ex:
        raise HTTPException(422, str(ex))


@app.get("/memory/lessons")
def memory_lessons(limit: int = 100, template: str | None = None, offset: int = 0, kind: str | None = None):
    return memory.list_lessons(max(1, min(limit, 500)), template, max(0, offset), kind)


class Recall(BaseModel):
    query: str


@app.post("/memory/recall")
def memory_recall(r: Recall):
    """Search Meko (if a token is set). Local similarity lives in /memory/check."""
    recs = meko.search(r.query)
    return dict(backend=("meko+" if meko.enabled() and meko._state.get("connected") else "") + db.backend(), records=recs, meko=meko.status())


@app.post("/memory/seed")
def memory_seed(reset: bool = False):
    return seed.run(reset=reset)


# ---------------------------------------------------------------- Live Strategy Tracker (Rex never places orders: you trade in your own broker)
class LoggedTrade(BaseModel):
    side: str = "buy"                      # buy = you bought (long), sell = you sold (short) in your broker app
    entry_price: float | None = Field(default=None, gt=0)      # default: the latest price
    qty: float | None = Field(default=None, gt=0)
    entered_at: str | None = None          # YYYY-MM-DD or YYYY-MM-DD HH:MM (IST); default now


class PaperStart(BaseModel):
    ticker: str = "RELIANCE.NS"
    template: str = "breakout"
    params: dict = Field(default_factory=dict)
    exits: dict = Field(default_factory=dict)
    cost_bps: float | None = Field(default=None, ge=0, le=500)
    cost_model: str | None = None
    slippage_bps: float | None = Field(default=None, ge=0, le=200)
    trade_value: float | None = Field(default=None, gt=0)
    label: str = "original"                # original | refined (which version of the strategy is tracked)
    trade: LoggedTrade | None = None       # "I took this trade": omit to just track the strategy
    simulate_entry_now: bool = False       # paper only: start already in position at the latest price
    qty: float | None = Field(default=None, gt=0)


class PaperStop(BaseModel):
    exit_price: float | None = Field(default=None, gt=0)       # price you got in your broker; default latest price
    exited_at: str | None = None
    note: str | None = Field(default=None, max_length=500)
    add_to_journal: bool = True


tracker_router = APIRouter()


@tracker_router.post("/paper/start")
def paper_start(r: PaperStart):
    """Start tracking a strategy (optionally with the real trade you took in your broker). Works offline and with the market closed."""
    try:
        return tracker.start(r.ticker, r.template, r.params, r.exits, r.cost_bps, r.label, r.trade.model_dump() if r.trade else None, r.simulate_entry_now, r.qty, cost_model=r.cost_model, slippage_bps=r.slippage_bps, trade_value=r.trade_value)
    except tracker.TrackerError as ex:
        raise HTTPException(422, str(ex))


@tracker_router.get("/paper/sessions")
def paper_sessions(status: str | None = None, limit: int = 100):
    return dict(sessions=tracker.list_sessions(status, max(1, min(limit, 500))), loop=tracker.loop_status(), market=livedata.market_state(),
                labels=[livedata.DELAY_NOTE])


@tracker_router.get("/paper/{sid}")
def paper_get(sid: str, refresh: bool = False):
    try:
        return tracker.get(sid, refresh)
    except KeyError:
        raise HTTPException(404, "No such tracker session")


@tracker_router.post("/paper/{sid}/refresh")
def paper_refresh(sid: str):
    try:
        return tracker.get(sid, True)
    except KeyError:
        raise HTTPException(404, "No such tracker session")


@tracker_router.post("/paper/{sid}/stop")
def paper_stop(sid: str, r: PaperStop | None = None):
    """Mark the session closed ("I closed it"). Saves the outcome and how price moved to memory (and your journal for a logged trade)."""
    r = r or PaperStop()
    try:
        return tracker.close(sid, r.exit_price, r.note, r.add_to_journal, r.exited_at)
    except KeyError:
        raise HTTPException(404, "No such tracker session")
    except tracker.TrackerError as ex:
        raise HTTPException(422, str(ex))


app.include_router(tracker_router)
app.include_router(tracker_router, prefix="/tracker", include_in_schema=False)
app.include_router(pipeline_api.router)        # data pipeline status (/pipeline/...), purely additive
app.include_router(crypto_api.router)          # SEPARATE Crypto section (/crypto/...), purely additive
from .aibuilder import api as ai_api; app.include_router(ai_api.router)   # AI research agent + chat builder (/ai/...), purely additive


# ---------------------------------------------------------------- trade journal (broker tradebook import + pre-trade check)
class TradeCheck(BaseModel):
    symbol: str
    date: str | None = None          # YYYY-MM-DD; default today (uses the latest close)
    side: str = "buy"                # buy = open a long, sell = open a short
    price: float | None = None
    qty: float | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    source: str = "auto"             # auto (real if any imported, else sample) | real | sample


def _import_cost(value):
    try:
        cost = float(value)
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(422, "cost_bps must be a number between 0 and 500")
    if not 0 <= cost <= 500:
        raise HTTPException(422, "cost_bps must be between 0 and 500")
    return cost


@app.post("/trades/import")
async def trades_import(request: Request, cost_bps: float = 0.0, filename: str = "tradebook.csv"):
    """Broker tradebook CSV. Accepts multipart (field 'file'), raw text/csv body, or JSON {csv_text, filename, cost_bps}."""
    ctype = request.headers.get("content-type", "")
    try:
        if ctype.startswith("multipart/"):
            form = await request.form()
            up = form.get("file")
            if up is None or not hasattr(up, "read"):
                raise HTTPException(422, "multipart upload needs a 'file' field")
            text, filename = (await up.read()).decode("utf-8-sig", errors="replace"), getattr(up, "filename", None) or filename
            if form.get("cost_bps"):
                cost_bps = _import_cost(form.get("cost_bps"))
        elif ctype.startswith("application/json"):
            try:
                body = await request.json()
            except ValueError:
                raise HTTPException(422, "Malformed JSON")
            if not isinstance(body, dict) or not isinstance(body.get("csv_text", ""), str):
                raise HTTPException(422, "Expected an object with csv_text as a string")
            text = body.get("csv_text") or ""
            filename, cost_bps = body.get("filename", filename), _import_cost(body.get("cost_bps", cost_bps))
        else:
            text = (await request.body()).decode("utf-8-sig", errors="replace")
        if not (0 <= cost_bps <= 500):
            raise HTTPException(422, "cost_bps must be between 0 and 500")
        return journal.import_csv(text, filename=filename, is_sample=False, cost_bps=cost_bps)
    except journal.ImportError_ as e:
        raise HTTPException(422, str(e))


@app.get("/trades/sample")
def trades_sample_csv():
    """The bundled SAMPLE tradebook text (synthetic behaviour on real yfinance prices)."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(journal.SAMPLE_PATH.read_text(), media_type="text/csv")


@app.post("/trades/import-sample")
def trades_import_sample():
    try:
        return journal.import_sample()
    except journal.ImportError_ as e:
        raise HTTPException(422, str(e))


@app.post("/trades/check")
def trades_check(c: TradeCheck):
    try:
        return journal.check(c.symbol, c.date, c.side, c.top_k, c.source, c.price, c.qty)
    except (journal.ImportError_, data.DataError) as e:
        raise HTTPException(422, str(e))


@app.get("/trades")
def trades_list(limit: int = 200, source: str = "auto", offset: int = 0):
    return journal.list_trades(max(1, min(limit, 500)), source, max(0, offset))


@app.get("/trades/summary")
def trades_summary(source: str = "auto"):
    return journal.summarize(source)


@app.delete("/trades")
def trades_clear(sample_only: bool = False):
    return journal.clear(sample_only)


@app.get("/memory/proof")
def memory_proof(source: str = "auto", top_k: int = 10):
    """Memory OFF vs ON walk-forward on the imported journal (no look-ahead)."""
    try:
        return journal.proof(source, top_k)
    except journal.ImportError_ as e:
        raise HTTPException(422, str(e))


@app.get("/research/{ticker}")
def research_brief(ticker: str):
    try:
        return research.brief(ticker)
    except data.DataError as e:
        raise HTTPException(422, str(e))


@app.get("/scout")
def scout_rank():
    return research.scout()
