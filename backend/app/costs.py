"""Realistic Indian equity cost model (optional). Round-trip costs are turned into per-leg fractions of trade value that backtest.run
applies at the entry open and the exit.  Crypto keeps a plain bps fee (+ optional slippage).

Rates (Zerodha schedule, https://zerodha.com/charges/ , checked 2026-11-01; NSE equity; rates change, edit RATES to update):
  brokerage  delivery: 0 | intraday: min(Rs 20, 0.03% of order value) per executed order
  STT        delivery: 0.1% on buy AND sell | intraday: 0.025% on sell only
  exchange transaction charge (NSE): 0.00307% of value, both legs
  SEBI turnover fee: Rs 10 per crore = 0.0001% of value, both legs
  GST: 18% of (brokerage + SEBI fee + exchange charge)
  stamp duty (buy only): delivery 0.015%, intraday 0.003%
  DP charge (delivery, on the SELL of each scrip, flat): Rs 15.34 incl. GST
  slippage: `slippage_bps` per leg (your assumption: market impact + spread; default 5 bp per leg)
Per-leg formula:  leg_cost = brokerage + STT + txn + SEBI + GST + stamp (+ DP on a delivery sell) + value * slippage_bps/1e4 ;  fraction = leg_cost / value.
The flat parts (Rs 20 brokerage, Rs 15.34 DP) depend on order size, so the model uses `trade_value` (default Rs 1,00,000 per trade).
"""
from __future__ import annotations

RATES = dict(
    brokerage_pct_intraday=0.03 / 100, brokerage_cap_intraday=20.0,
    stt_delivery_buy=0.1 / 100, stt_delivery_sell=0.1 / 100, stt_intraday_sell=0.025 / 100,
    txn_nse=0.00307 / 100, sebi=10.0 / 1e7, gst=0.18,
    stamp_delivery=0.015 / 100, stamp_intraday=0.003 / 100, dp_sell_delivery=15.34,
)
MODELS = {"bps": "Flat round-trip bps (the original assumption)",
          "india_delivery": "Indian cash equity, delivery (holds overnight)",
          "india_intraday": "Indian cash equity, intraday"}
SOURCE = "https://zerodha.com/charges/"


def leg(kind: str, side: str, value: float, slippage_bps: float = 0.0) -> dict:
    """Rupee breakdown of one executed order. kind: delivery | intraday; side: buy | sell."""
    if kind not in ("delivery", "intraday") or side not in ("buy", "sell"):
        raise ValueError("kind must be delivery|intraday and side buy|sell")
    R = RATES
    brok = 0.0 if kind == "delivery" else min(R["brokerage_cap_intraday"], R["brokerage_pct_intraday"] * value)
    if kind == "delivery":
        stt = value * (R["stt_delivery_buy"] if side == "buy" else R["stt_delivery_sell"])
    else:
        stt = value * R["stt_intraday_sell"] if side == "sell" else 0.0
    txn = value * R["txn_nse"]
    sebi = value * R["sebi"]
    gst = R["gst"] * (brok + txn + sebi)
    stamp = value * (R["stamp_delivery"] if kind == "delivery" else R["stamp_intraday"]) if side == "buy" else 0.0
    dp = R["dp_sell_delivery"] if (kind == "delivery" and side == "sell") else 0.0
    slip = value * slippage_bps / 1e4
    parts = dict(brokerage=brok, stt=stt, exchange_txn=txn, sebi=sebi, gst=gst, stamp_duty=stamp, dp_charge=dp, slippage=slip)
    parts = {k: round(v, 4) for k, v in parts.items()}
    return dict(parts=parts, total=round(sum(parts.values()), 4), fraction=sum(parts.values()) / value)


def india(kind: str = "delivery", trade_value: float = 100_000.0, slippage_bps: float = 5.0) -> dict:
    if trade_value <= 0:
        raise ValueError("trade_value must be positive")
    b, s = leg(kind, "buy", trade_value, slippage_bps), leg(kind, "sell", trade_value, slippage_bps)
    legs = (b["fraction"], s["fraction"])
    return dict(model=f"india_{kind}", kind=kind, trade_value=trade_value, slippage_bps=slippage_bps, legs=legs,
                round_trip_bps=round((legs[0] + legs[1]) * 1e4, 2), buy=b, sell=s, source=SOURCE,
                note="Per-leg costs are fractions of trade value. Flat charges assume the trade value above.")


def resolve(cost_model: str | None, cost_bps: float | None, slippage_bps: float | None = None, trade_value: float | None = None,
            default_bps: float = 15.0, market: str = "nse") -> dict:
    """-> dict(cost_bps, legs|None, label, detail|None). `bps` (or None) keeps the original behaviour exactly, plus optional slippage on both legs."""
    cm = cost_model or "bps"
    if cm not in MODELS:
        raise ValueError(f"cost_model must be one of {sorted(MODELS)}")
    slip = 0.0 if slippage_bps is None else float(slippage_bps)
    if cm == "bps" or market != "nse":
        base = default_bps if cost_bps is None else float(cost_bps)
        tot = base + 2 * slip
        return dict(cost_bps=tot, legs=None, detail=None,
                    label=f"{base:g} bp round trip" + (f" + {slip:g} bp slippage per leg = {tot:g} bp" if slip else ""))
    d = india(cm.split("_", 1)[1], 100_000.0 if trade_value is None else float(trade_value), 5.0 if slippage_bps is None else slip)
    return dict(cost_bps=d["round_trip_bps"], legs=d["legs"], detail=d,
                label=f"Indian {d['kind']} cost model: about {d['round_trip_bps']:g} bp round trip on Rs {d['trade_value']:,.0f} (incl. {d['slippage_bps']:g} bp slippage per leg)")
