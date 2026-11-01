import numpy as np
import pandas as pd
import pytest
from app import data, rules, config


@pytest.fixture(scope="module")
def df():
    return data.load("RELIANCE.NS")[0]


def test_real_data_shape(df):
    d, info = data.load("RELIANCE.NS")
    assert len(d) > 1000 and info["last"] > "2026-01-01"
    assert {"Open", "High", "Low", "Close", "Volume"} <= set(d.columns) and d["Close"].min() > 0


def test_ticker_validation():
    assert data.normalise("tcs") == "TCS.NS"
    for bad in ["", "../etc", "A B.NS", "X" * 30 + ".NS"]:
        with pytest.raises(data.DataError):
            data.normalise(bad)


def test_split_is_last_year(df):
    k = data.split_index(df)
    days = (df.index[-1] - df.index[k]).days
    assert 300 <= days <= 365 and df.index[k - 1] <= df.index[-1] - pd.Timedelta(days=365)


@pytest.mark.parametrize("tpl", list(rules.TEMPLATES))
def test_signals_have_no_lookahead(df, tpl):
    p = rules.default_params(tpl)
    s1 = rules.signals(df, tpl, p)
    cut = 800
    d2 = df.astype(float)
    d2.iloc[cut:, :] = d2.iloc[cut:, :] * 7.0                      # wreck the future
    s2 = rules.signals(d2, tpl, p)
    assert (s1[:cut] == s2[:cut]).all()
    assert s1.sum() > 0


def test_tags_have_no_lookahead(df):
    t1 = rules.tag_frame(df)
    d2 = df.astype(float); d2.iloc[900:, :] *= 5.0
    t2 = rules.tag_frame(d2)
    for g in rules.TAG_GROUPS:
        assert (t1[g].iloc[:900] == t2[g].iloc[:900]).all()


def test_rsi_range(df):
    r = rules.rsi(df["Close"], 14).dropna()
    assert r.between(0, 100).all()


def test_validation_errors():
    with pytest.raises(rules.SpecError): rules.validate("nope", {}, {})
    with pytest.raises(rules.SpecError): rules.validate("ma_cross", {"fast": 50, "slow": 20}, {})
    with pytest.raises(rules.SpecError): rules.validate("breakout", {"n": 3}, {})
    with pytest.raises(rules.SpecError): rules.validate("rsi", {"mode": "x"}, {})
    p, e = rules.validate("breakout", {"n": "30"}, {"stop_pct": None})
    assert p["n"] == 30 and e["stop_pct"] is None and e["hold_days"] == 10


def test_tag_vector_cosine():
    from app.memory import cosine
    a = {"trend": "up", "volume": "dry", "volatility": "calm", "momentum": "up"}
    b = dict(a, momentum="down")
    assert cosine(rules.tag_vector(a), rules.tag_vector(a)) == pytest.approx(1.0)
    assert cosine(rules.tag_vector(a), rules.tag_vector(b)) == pytest.approx(0.75)
