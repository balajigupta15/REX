"""Build the bundled SAMPLE tradebook: a simulated discretionary trader's fills priced from REAL yfinance bars.
The trader behaviour (seeded) is made up: it chases gaps/strength about half the time, buys at the open, takes quick profits
and holds losers longer. Entry/exit prices are the real open/close of those days, so outcomes are whatever real prices gave.
It is NOT a real person's record."""
import sys, random
sys.path.insert(0, ".")
import pandas as pd
from app import config, data, journal

random.seed(42)
TICKERS = config.DEFAULT_TICKERS
FROM, TO = pd.Timestamp("2024-07-01"), pd.Timestamp("2026-10-30")
frames = {t: data.load(t)[0] for t in TICKERS}
ctx = {t: journal.context_frame(frames[t]) for t in TICKERS}
tick = lambda x: round(round(x / 0.05) * 0.05, 2)
fills, tid = [], 0
busy = {t: [] for t in TICKERS}   # (start, end) of each simulated position, to avoid overlap


def add(sym, d, side, q, px, hh, mm):
    global tid
    tid += 1
    fills.append(dict(symbol=sym.replace(".NS", ""), trade_date=str(d.date()), exchange="NSE", segment="EQ", series="EQ", trade_type=side, auction="false",
                      quantity=int(q), price=f"{tick(px):.2f}", trade_id=10_000_000 + tid, order_id=1_300_000_000_000 + tid * 7,
                      order_execution_time=f"{d.date()}T{hh:02d}:{mm:02d}:{random.randint(0, 59):02d}"))


tries = 0
n_trips = 0
while n_trips < 118 and tries < 5000:
    tries += 1
    t = random.choice(TICKERS)
    df, cf = frames[t], ctx[t]
    idx = df.index[(df.index >= FROM) & (df.index <= TO - pd.Timedelta(days=40))]
    if random.random() < 0.5:     # behaviour: chase strength / gaps
        chase = ((df["Open"] / df["Close"].shift(1) - 1 >= 0.01) | (cf["dma20_gap_pct"].shift(1) > 5)).reindex(idx)
        cands = list(idx[chase.to_numpy()])
        if not cands:
            continue
        d0 = random.choice(cands)
    else:
        d0 = random.choice(list(idx))
    i0 = df.index.get_loc(d0)
    if i0 + 1 >= len(df):
        continue
    entry = df["Open"].iloc[i0]
    qty = max(1, round(random.choice([0.5, 1, 1, 1, 1.5, 2]) * 50000 / entry))
    exit_i = None
    for k in range(1, 26):
        j = i0 + k
        if j >= len(df):
            break
        r = df["Close"].iloc[j] / entry - 1
        if (r >= 0.03 and k >= 2) or (k >= random.randint(6, 10) and r > 0) or r <= -0.12 or k >= 25:
            exit_i = j
            break
    if exit_i is None:
        continue
    dx_chk = df.index[min(exit_i + 1, len(df) - 1)]
    if any(not (dx_chk < a or d0 > b) for a, b in busy[t]):
        continue
    busy[t].append((d0, dx_chk))
    add(t, d0, "buy", qty, entry, 9, random.randint(16, 45))
    scaled = random.random() < 0.15 and i0 + 3 < len(df)
    if scaled:
        add(t, df.index[i0 + 2], "buy", max(1, qty // 2), df["Open"].iloc[i0 + 2], 10, random.randint(0, 59))
        tot = qty + max(1, qty // 2)
    else:
        tot = qty
    dx = df.index[exit_i]
    if random.random() < 0.15 and exit_i + 1 < len(df) and tot >= 2:       # split exit
        h = tot // 2
        add(t, dx, "sell", h, df["Close"].iloc[exit_i], 15, random.randint(0, 29))
        add(t, df.index[exit_i + 1], "sell", tot - h, df["Close"].iloc[exit_i + 1], 15, random.randint(0, 29))
    else:
        add(t, dx, "sell", tot, df["Close"].iloc[exit_i], 15, random.randint(0, 29))
    n_trips += 1
# three positions deliberately left open at the end of the file (tests open-position handling)
for t in ("ITC.NS", "SBIN.NS", "LT.NS"):
    df = frames[t]; d = df.index[-random.randint(2, 6)]
    add(t, d, "buy", max(1, round(50000 / df["Open"].loc[d])), df["Open"].loc[d], 9, random.randint(16, 45))
fills.sort(key=lambda f: f["order_execution_time"])
cols = list(fills[0].keys())
out = config.BASE / "sample" / "SAMPLE_tradebook_synthetic_journal_real_prices.csv"
with open(out, "w") as fh:
    fh.write("# SAMPLE DATA: synthetic trader behaviour (seeded random rules) priced from REAL yfinance daily bars (auto-adjusted).\n")
    fh.write("# NOT a real person's tradebook. Zerodha-style columns for testing the import. Fills are at the real open (buys) or close (sells) of each day.\n")
    fh.write(",".join(cols) + "\n")
    for f in fills:
        fh.write(",".join(str(f[c]) for c in cols) + "\n")
print(out, len(fills), "fills,", n_trips, "closed round trips attempted")
