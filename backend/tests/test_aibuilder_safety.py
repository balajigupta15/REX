"""AI Strategy Builder: code safety (AST allowlist, sandbox, look-ahead) and engine equivalence with the existing backtester."""
import os
import numpy as np
import pytest
from app import backtest, config, data, rules
from app.aibuilder import ast_guard, engine, sandbox, service

GOOD = """def signals(df):
    hi = df['high'].rolling(20).max().shift(1)
    lo = df['low'].rolling(10).min().shift(1)
    pos = np.where(df['close'] > hi, 1.0, np.where(df['close'] < lo, 0.0, np.nan))
    return pd.Series(pos, index=df.index).ffill().fillna(0)
"""


@pytest.fixture(scope="module")
def btc():
    return service.get_setup(dict(market="crypto", symbol="BTCUSDT", interval="1d"))


# ------------------------------------------------------------------ AST validator
@pytest.mark.parametrize("bad", [
    "import os\ndef signals(df):\n    return df['close']*0",
    "from os import system\ndef signals(df):\n    return df['close']*0",
    "def signals(df):\n    return df.__class__",
    "def signals(df):\n    return ().__class__.__bases__",
    "def signals(df):\n    open('/etc/passwd')\n    return df['close']*0",
    "def signals(df):\n    eval('1')\n    return df['close']*0",
    "def signals(df):\n    exec('x=1')\n    return df['close']*0",
    "def signals(df):\n    while True:\n        pass\n    return df['close']*0",
    "def signals(df):\n    return getattr(df, 'close')",
    "def signals(df):\n    return __import__('os')",
    "def helper(df):\n    return df",
])
def test_ast_rejects(bad):
    with pytest.raises(ast_guard.CodeRejected):
        ast_guard.validate(bad)


def test_ast_accepts_good_code():
    ast_guard.validate(GOOD)


def test_ast_rejects_oversized_and_syntax_errors():
    with pytest.raises(ast_guard.CodeRejected):
        ast_guard.validate("def signals(df):\n    return df\n" + "# x\n" * 4000)
    with pytest.raises(ast_guard.CodeRejected):
        ast_guard.validate("def signals(df:\n")


# ------------------------------------------------------------------ sandbox
def test_sandbox_runs_good_code(btc):
    out = sandbox.run_signals(GOOD, btc["df"])
    assert len(out["positions"]) == len(btc["df"]) and out["lookahead_free"] is True and set(out["positions"]) <= {-1, 0, 1}


def test_sandbox_timeout(btc):
    slow = "def signals(df):\n    t = 0\n    for i in range(10**12):\n        t += i\n    return df['close'] * 0\n"
    with pytest.raises(sandbox.SandboxError) as e:
        sandbox.run_signals(slow, btc["df"], timeout=2)
    assert e.value.kind == "timeout"


def test_sandbox_does_not_see_api_key(btc, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-TEST-KEY-123")
    probe = "def signals(df):\n    return df['close'] * 0\n"
    assert sandbox.run_signals(probe, btc["df"])["lookahead_free"]
    seen = {}
    real = sandbox.subprocess.run
    def spy(cmd, **kw):
        seen["env"], seen["cmd"] = kw.get("env"), cmd
        return real(cmd, **kw)
    monkeypatch.setattr(sandbox.subprocess, "run", spy)
    sandbox.run_signals(probe, btc["df"], lookahead=False)
    assert "SECRET-TEST-KEY-123" not in repr(seen["env"]) and "GEMINI" not in repr(seen["env"])


def test_sandbox_rejects_validator_failures_before_running(btc):
    with pytest.raises((ast_guard.CodeRejected, sandbox.SandboxError)):
        sandbox.run_signals("import os\ndef signals(df):\n    return df['close']*0", btc["df"])


# ------------------------------------------------------------------ look-ahead detector
def test_lookahead_catches_shift_minus_one(btc):
    peek = "def signals(df):\n    return (df['close'].shift(-1) > df['close']).astype(int)\n"
    with pytest.raises(sandbox.SandboxError) as e:
        sandbox.run_signals(peek, btc["df"])
    assert e.value.kind == "lookahead"


def test_lookahead_catches_whole_series_statistics(btc):
    leak = "def signals(df):\n    z = (df['close'] - df['close'].mean()) / df['close'].std()\n    return (z < 0).astype(int)\n"
    with pytest.raises(sandbox.SandboxError) as e:
        sandbox.run_signals(leak, btc["df"])
    assert e.value.kind == "lookahead"


# ------------------------------------------------------------------ equivalence with backtest.run
def test_engine_matches_existing_backtester_on_breakout():
    """Hand-written breakout signals() run through the sandbox + position engine must reproduce backtest.run('breakout') exactly."""
    df, _ = data.load("RELIANCE.NS")
    k = data.split_index(df)
    code = "def signals(df):\n    prior = df['high'].shift(1).rolling(20).max()\n    return (df['close'] > prior).astype(int)\n"
    pos = sandbox.run_signals(code, df)["positions"]
    exits = dict(hold_bars=7, stop_pct=4.0, tp_pct=9.0, exit_on_signal_off=False)
    mine = engine.backtest_positions(df, pos, exits, k, 0.002, backtest.TRADING_DAYS, lambda t: str(t.date()))
    ref = backtest.run(df, "breakout", {"n": 20}, dict(hold_days=7, stop_pct=4.0, tp_pct=9.0), cost_bps=20.0)
    for w in ("train", "locked", "full"):
        for key in ("total_return", "sharpe", "max_drawdown", "trades"):
            assert mine[w]["metrics"][key] == pytest.approx(ref[w]["metrics"][key], rel=1e-9, abs=1e-12), (w, key)
    assert mine["full"]["metrics"]["trades"] > 5
    assert [t["entry_date"] for t in mine["full"]["trades"]] == [t["entry_date"] for t in ref["full"]["trades"]]


def test_engine_enters_on_next_bar_open(btc):
    df = btc["df"]
    pos = np.zeros(len(df), dtype=int)
    pos[300:306] = 1
    res = engine.backtest_positions(df, pos, dict(), btc["k"], 0.0, 365, btc["fmt"], True)
    t = res["train"]["trades"][0]
    assert t["entry"] == pytest.approx(round(float(df["Open"].iloc[301]), 4))
