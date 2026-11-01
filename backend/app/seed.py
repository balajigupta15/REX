"""Seed the memory with REAL backtests/refinements so the demo has history. Uses cached or live yfinance data."""
from __future__ import annotations
from . import backtest, config, data, journal, memory, refine, rules, strategy_memory

PLAN = [("breakout", {}, {}), ("ma_cross", {}, {}), ("rsi", {}, {}), ("mean_reversion", {}, {}),
        ("rsi", {"level": 70, "mode": "overbought"}, {})]
REFINE_PLAN = [("RELIANCE.NS", "breakout"), ("TCS.NS", "mean_reversion"), ("INFY.NS", "ma_cross"), ("HDFCBANK.NS", "rsi")]


def run(reset: bool = False, tickers: list[str] | None = None) -> dict:
    """Seed demo memory. Meko mirroring is paused so bulk demo data never floods a real Meko store."""
    from . import meko
    meko.suspend(True)
    try:
        return _run(reset, tickers)
    finally:
        meko.suspend(False)


def _run(reset: bool = False, tickers: list[str] | None = None) -> dict:
    if reset and config.DB_PATH.exists():
        config.DB_PATH.unlink()
    n_bt = n_rf = 0
    skipped = []
    for tk in tickers or config.DEFAULT_TICKERS:
        try:
            df, info = data.load(tk)
        except data.DataError as e:
            skipped.append(f"{tk}: {e}"); continue
        for tpl, p0, e0 in PLAN:
            p, e = rules.validate(tpl, p0, e0)
            res = backtest.run(df, tpl, p, e)
            memory.record("backtest", info["ticker"], tpl, p, e, config.COST_BPS_DEFAULT, res); n_bt += 1
    for tk, tpl in REFINE_PLAN:
        if tickers and tk not in tickers:
            continue
        try:
            df, info = data.load(tk)
        except data.DataError:
            continue
        p, e = rules.validate(tpl, {}, {})
        r = refine.refine(df, tpl, p, e)
        extra = memory.refine_lesson(info["ticker"], tpl, r["verdict"], r["original"]["result"], r["refined"]["result"])
        memory.record("refine", info["ticker"], tpl, r["refined"]["params"], r["refined"]["exits"], config.COST_BPS_DEFAULT, r["refined"]["result"],
                      flag=r["verdict"]["flag"], extra_lessons=[extra] if extra else None); n_rf += 1
    replays = None
    try:
        replays = strategy_memory.seed_replays(tickers)
    except Exception as e:
        skipped.append(f"strategy replays: {e}")
    jr = None
    try:
        jr = journal.import_sample()
        jr = dict(round_trips=jr["round_trips"], new_trades=jr["new_trades"], is_sample=True)
    except Exception as e:      # the sample journal is optional for the strategy demo
        skipped.append(f"sample journal: {e}")
    return dict(backtests=n_bt, refinements=n_rf, strategy_replays=replays, sample_journal=jr, skipped=skipped, status=memory.status())
