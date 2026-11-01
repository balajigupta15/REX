"""Persona D: 'Chaser Chintu', a repeat-mistake trader. Tradebook = synthetic BEHAVIOUR on REAL cached NSE prices (fills at real open/close).
Behaviour: (1) chases stocks after a >=6% 5-day run with a volume spike, takes profit fast (+3%), sits on losers up to 35 days;
(2) occasional calm pullback trades above the 20/50 DMA. Rex gets only trades that CLOSED before 2025-06-01 (uploaded through the real API),
then every later entry is run through POST /trades/check as of its entry date. We compare ignoring Rex vs following it (wait = skip, reduce_size = factor).
Usage: python journal_persona.py http://127.0.0.1:8001"""
import csv, io, json, math, os, random, sys
os.environ.setdefault("REX_OFFLINE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx, numpy as np, pandas as pd
from app import config, data

HERE = os.path.dirname(os.path.abspath(__file__))
CUT = pd.Timestamp("2025-06-01")
NOTIONAL, COST = 50000.0, 0.0015


def generate(seed=7):
    rng = random.Random(seed)
    trades = []
    for t in config.DEFAULT_TICKERS:
        df, _ = data.load(t)
        c, o, v = df.Close.to_numpy(), df.Open.to_numpy(), df.Volume.to_numpy()
        ma20, ma50 = df.Close.rolling(20).mean().to_numpy(), df.Close.rolling(50).mean().to_numpy()
        vr = (df.Volume / df.Volume.rolling(20).mean().shift(1)).to_numpy()
        i, n = 60, len(df)
        while i < n - 2:
            kind = None
            run5 = c[i - 1] / c[i - 6] - 1
            if run5 >= 0.06 and vr[i - 1] >= 1.5 and rng.random() < 0.6:
                kind = "chase"
            elif c[i - 1] > ma20[i - 1] > ma50[i - 1] and abs(c[i - 1] / ma20[i - 1] - 1) < 0.02 and rng.random() < 0.04:
                kind = "calm"
            if not kind:
                i += 1; continue
            entry = o[i]; j = i
            maxh = 35 if kind == "chase" else rng.randint(8, 15)
            while True:
                j += 1
                if j >= n: j = None; break
                ret = c[j] / entry - 1
                if kind == "chase" and ret >= 0.03: break          # takes profit quickly
                if kind == "calm" and ret <= -0.08: break          # calm trades do have a stop
                if j - i >= maxh: break
            if j is None: break
            trades.append(dict(sym=t.split(".")[0], kind=kind, ed=df.index[i], xd=df.index[j], entry=float(entry), exit=float(c[j]), qty=max(1, int(NOTIONAL // entry))))
            i = j + 1
    trades.sort(key=lambda r: (r["ed"], r["sym"]))
    return trades


def to_csv(trs):
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow("symbol,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time".split(","))
    rows = []
    for k, r in enumerate(trs):
        rows.append((r["ed"], k * 2, [r["sym"], r["ed"].date(), "NSE", "EQ", "EQ", "buy", "false", r["qty"], f"{r['entry']:.2f}", 20000000 + k * 2, 1700000000000 + k * 2, f"{r['ed'].date()}T09:31:00"]))
        rows.append((r["xd"], k * 2 + 1, [r["sym"], r["xd"].date(), "NSE", "EQ", "EQ", "sell", "false", r["qty"], f"{r['exit']:.2f}", 20000000 + k * 2 + 1, 1700000000000 + k * 2 + 1, f"{r['xd'].date()}T15:10:00"]))
    for _, _, row in sorted(rows, key=lambda x: (x[0], x[1])): w.writerow(row)
    return buf.getvalue()


def pnl(r, f=1.0):
    return f * r["qty"] * (r["exit"] * (1 - COST / 2) - r["entry"] * (1 + COST / 2))


def mdd(seq):
    eq = np.cumsum(seq); peak = np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    return float((eq - peak).min()) if len(eq) else 0.0


def main(api, seed=7, write=True):
    trs = generate(seed)
    train = [r for r in trs if r["xd"] < CUT]
    test = [r for r in trs if r["ed"] >= CUT]
    csv_all = to_csv(trs); csv_train = to_csv(train)
    if write:
        open(os.path.join(HERE, "chintu_tradebook_full.csv"), "w").write(csv_all)
        open(os.path.join(HERE, "chintu_tradebook_uploaded.csv"), "w").write(csv_train)
    cl = httpx.Client(base_url=api, timeout=120)
    cl.delete("/trades")
    imp = cl.post("/trades/import", params={"cost_bps": 15, "filename": "chintu_tradebook.csv"}, files={"file": ("chintu_tradebook.csv", csv_train, "text/csv")}).json()
    summ = cl.get("/trades/summary", params={"source": "real"}).json()
    rec = dict(generated=len(trs), uploaded_round_trips=len(train), test_trades=len(test), import_summary={k: imp.get(k) for k in ("import_id", "n_trades", "trades", "paired", "orphans", "rejected") if k in imp},
               summary_keys=list(summ.keys()))
    rows = []
    for r in test:
        chk = cl.post("/trades/check", json=dict(symbol=r["sym"] + ".NS", date=str(r["ed"].date()), side="buy", source="real")).json()
        if "recommendation" not in chk:
            rows.append(dict(sym=r["sym"], date=str(r["ed"].date()), error=str(chk)[:200])); continue
        rc = chk["recommendation"]
        pat = (chk.get("mistake_pattern") or {}).get("matched")
        rows.append(dict(sym=r["sym"], kind=r["kind"], date=str(r["ed"].date()), exit=str(r["xd"].date()), action=rc["action"], factor=rc.get("size_factor", 1.0),
                         conf=rc.get("confidence"), pnl=pnl(r), ret=r["exit"] / r["entry"] - 1, pattern=(pat or {}).get("text") if pat else None,
                         reason=(rc.get("reasons") or [""])[0], n_similar=chk["similar"]["n"]))
    ok = [x for x in rows if "error" not in x]
    ok.sort(key=lambda x: x["exit"])
    def stats(sel):
        s = [sel(x) for x in ok]
        return dict(trades=sum(1 for v in s if v != 0), total_pnl=round(sum(s)), max_drawdown=round(mdd(s)), win_rate=round(float(np.mean([v > 0 for v in s if v != 0])), 3) if any(v != 0 for v in s) else None)
    res = dict(ignore=stats(lambda x: x["pnl"]),
               follow=stats(lambda x: x["pnl"] * (0 if x["action"] == "wait" else x["factor"] if x["action"] == "reduce_size" else 1)),
               by_action={a: dict(n=len([x for x in ok if x["action"] == a]), total_pnl_if_taken=round(sum(x["pnl"] for x in ok if x["action"] == a)),
                                  win_rate=round(float(np.mean([x["pnl"] > 0 for x in ok if x["action"] == a])), 3)) for a in sorted({x["action"] for x in ok})},
               by_kind={k: dict(n=len([x for x in ok if x["kind"] == k]), total_pnl=round(sum(x["pnl"] for x in ok if x["kind"] == k)),
                                win_rate=round(float(np.mean([x["pnl"] > 0 for x in ok if x["kind"] == k])), 3)) for k in ("chase", "calm") if any(x["kind"] == k for x in ok)})
    # train-period behaviour for context
    tr_stats = dict(n=len(train), total_pnl=round(sum(pnl(r) for r in train)), win_rate=round(float(np.mean([pnl(r) > 0 for r in train])), 3),
                    chase_pnl=round(sum(pnl(r) for r in train if r["kind"] == "chase")), calm_pnl=round(sum(pnl(r) for r in train if r["kind"] == "calm")))
    patterns = sorted({x["pattern"] for x in ok if x["pattern"]})[:5]
    out = dict(seed=seed, setup=rec, train_stats=tr_stats, results=res, patterns_shown=patterns, rows=ok)
    if write:
        json.dump(out, open(os.path.join(HERE, "journal_persona_results.json"), "w"), indent=1, default=str)
        print(json.dumps(dict(setup=rec, train_stats=tr_stats, results=res, patterns=patterns), indent=1, default=str))
    return out


def sweep(api, seeds=range(1, 13)):
    rows = []
    for sd in seeds:
        o = main(api, sd, write=False); r = o["results"]
        rows.append(dict(seed=sd, train_n=o["setup"]["uploaded_round_trips"], test_n=r["ignore"]["trades"], ignore_pnl=r["ignore"]["total_pnl"], follow_pnl=r["follow"]["total_pnl"],
                         ignore_mdd=r["ignore"]["max_drawdown"], follow_mdd=r["follow"]["max_drawdown"],
                         skipped_pnl_if_taken=r["by_action"].get("wait", {}).get("total_pnl_if_taken", 0)))
    agg = dict(traders=len(rows), follow_better_pnl=sum(x["follow_pnl"] > x["ignore_pnl"] for x in rows), follow_better_mdd=sum(x["follow_mdd"] > x["ignore_mdd"] for x in rows),
               sum_ignore_pnl=sum(x["ignore_pnl"] for x in rows), sum_follow_pnl=sum(x["follow_pnl"] for x in rows),
               sum_ignore_mdd=sum(x["ignore_mdd"] for x in rows), sum_follow_mdd=sum(x["follow_mdd"] for x in rows),
               sum_wait_pnl_if_taken=sum(x["skipped_pnl_if_taken"] for x in rows))
    json.dump(dict(rows=rows, aggregate=agg), open(os.path.join(HERE, "journal_sweep_results.json"), "w"), indent=1)
    print(json.dumps(agg, indent=1))
    for x in rows: print(x)


if __name__ == "__main__":
    api = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
    (sweep if "--sweep" in sys.argv else main)(api)
