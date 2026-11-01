"""Hand-built OHLC fixtures (labelled synthetic, unit tests only) to pin down exact fill rules."""
import numpy as np
import pandas as pd
import pytest
from app import backtest

COST = 0.0015


def frame(rows):
    idx = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def run(rows, sig_idx, hold=3, stop=None, tp=None, cost=0.0, win=None):
    df = frame(rows)
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    sig = np.zeros(len(df), bool)
    for i in sig_idx:
        sig[i] = True
    a, b = win or (1, len(df) - 1)
    return backtest._run_window(o, h, l, c, sig, a, b, hold, stop, tp, cost)


FLAT = [(100, 101, 99, 100)] * 10


def test_entry_at_next_open_and_hold_exit_at_close():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (102, 103, 101, 102), (103, 104, 102, 103), (104, 105, 103, 104), (105, 106, 104, 105), (105, 106, 104, 105)]
    r, tr = run(rows, [1], hold=2)
    t = tr[0]
    assert t["entry_idx"] == 2 and t["entry"] == 102          # signal bar 1 -> open of bar 2
    assert t["exit_idx"] == 4 and t["reason"] == "hold" and t["exit"] == 104
    assert t["ret"] == pytest.approx(104 / 102 - 1)


def test_stop_intrabar_fills_at_stop_price():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 94, 96), (96, 97, 95, 96), (96, 97, 95, 96)]
    r, tr = run(rows, [1], hold=3, stop=5)
    assert tr[0]["reason"] == "stop" and tr[0]["exit"] == pytest.approx(95.0) and tr[0]["exit_idx"] == 2
    assert tr[0]["ret"] == pytest.approx(-0.05)


def test_gap_down_fills_at_open_not_stop():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (90, 92, 88, 91), (91, 92, 90, 91)]
    r, tr = run(rows, [1], hold=3, stop=5)
    assert tr[0]["reason"] == "stop" and tr[0]["exit"] == 90 and tr[0]["exit_idx"] == 3


def test_take_profit_and_gap_up():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 111, 99, 108), (108, 109, 107, 108)]
    _, tr = run(rows, [1], hold=3, tp=10)
    assert tr[0]["reason"] == "take_profit" and tr[0]["exit"] == pytest.approx(110.0)
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (115, 116, 114, 115), (115, 116, 114, 115)]
    _, tr = run(rows, [1], hold=3, tp=10)
    assert tr[0]["exit"] == 115 and tr[0]["reason"] == "take_profit"


def test_stop_beats_take_profit_in_same_bar():
    rows = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 120, 80, 100), (100, 101, 99, 100)]
    _, tr = run(rows, [1], hold=3, stop=5, tp=10)
    assert tr[0]["reason"] == "stop"


def test_costs_on_flat_trade():
    _, tr = run(FLAT, [1], hold=3, cost=COST)
    assert tr[0]["ret"] == pytest.approx((1 - COST / 2) ** 2 - 1)      # 15 bp round trip, half each side
    r, tr = run(FLAT, [1], hold=3, cost=COST)
    assert np.prod(1 + r) - 1 == pytest.approx(tr[0]["ret"])           # daily returns reconcile with trade return


def test_no_overlap_and_no_entry_without_next_bar():
    rows = FLAT
    r, tr = run(rows, list(range(1, 10)), hold=2)
    for a, b in zip(tr, tr[1:]):
        assert b["entry_idx"] > a["exit_idx"] - 1 + 0  # next entry is after the previous exit bar
    # a signal on the final bar of the window cannot trade
    _, tr = run(rows, [9])
    assert tr == []


def test_window_end_forces_exit_at_close():
    rows = [(100, 101, 99, 100)] * 4 + [(100, 101, 99, 103)]
    _, tr = run(rows, [2], hold=50, win=(1, 4))
    assert tr[0]["reason"] == "window_end" and tr[0]["exit_idx"] == 4 and tr[0]["exit"] == 103


def test_metrics_basic():
    r = np.array([0.01, -0.01, 0.02, 0.0])
    m = backtest._metrics(r, [dict(ret=0.02), dict(ret=-0.01)])
    assert m["total_return"] == pytest.approx(np.prod(1 + r) - 1)
    assert m["win_rate"] == 0.5 and m["max_drawdown"] <= 0
