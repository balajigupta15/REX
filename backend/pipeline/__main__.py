from __future__ import annotations
import argparse, json, sys
from . import config, ingest, schedule, status, store
from .validate import validate


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline", description="Rex data pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one ingestion run (incremental)")
    r.add_argument("--market", choices=["all", "nse", "crypto"], default="all")
    r.add_argument("--symbols", nargs="*"); r.add_argument("--intervals", nargs="*")
    r.add_argument("--offline", action="store_true", help="seed from the bundled cache, no network")
    r.add_argument("--json", action="store_true")
    s = sub.add_parser("schedule", help="long-running scheduler (NSE after 16:00 IST weekdays, crypto hourly)")
    s.add_argument("--poll", type=float, default=30.0)
    sub.add_parser("seed", help="import the bundled cache into the store (no network)")
    st = sub.add_parser("status", help="print freshness per symbol"); st.add_argument("--json", action="store_true")
    sub.add_parser("crosscheck", help="compare stored NSE closes with the official bhavcopy")
    sub.add_parser("validate", help="re-validate every stored partition")
    sub.add_parser("health", help="exit 0 when the scheduler is alive (Docker healthcheck)")
    a = ap.parse_args(argv)

    if a.cmd == "run" or a.cmd == "seed":
        res = ingest.run(market=getattr(a, "market", "all"), symbols=getattr(a, "symbols", None), intervals=getattr(a, "intervals", None),
                         trigger="cli", force_offline=True if (a.cmd == "seed" or a.offline) else None)
        if getattr(a, "json", False):
            print(json.dumps(res, default=str))
        else:
            print(f"run #{res['run_id']} {res['status']}: {res['symbols_ok']} ok, {res['symbols_failed']} failed, +{res['rows_added']} rows")
            for i in res["items"]:
                print(f"  {i['market']:6} {i['symbol']:12} {i['interval']:3} {i['status']:13} rows={i['rows_total']:<6} +{i['rows_added']:<5} {i.get('error') or ''}")
        return 0 if res["status"] != "failed" else 1
    if a.cmd == "schedule":
        store.fail_stale_runs()
        schedule.loop(a.poll)
        return 0
    if a.cmd == "status":
        s = status.build()
        if a.json:
            print(json.dumps(s, default=str, indent=1)); return 0
        t = s["totals"]
        print(f"store {s['store']}: {t['partitions']} partitions, {t['rows']} rows, {t['stale']} stale, {t['missing']} missing")
        for p in s["partitions"]:
            print(f"  {p['market']:6} {p['symbol']:12} {p['interval']:3} rows={p['rows']:<6} {p['freshness']:6} validation={p['validation']:5} {p['freshness_detail']}")
        return 0
    if a.cmd == "validate":
        bad = 0
        for p in store.partitions():
            df = store.read(p["market"], p["symbol"], p["interval"])
            _, rep = validate(df, p["market"], p["interval"])
            bad += rep["status"] == "error"
            print(f"  {p['market']:6} {p['symbol']:12} {p['interval']:3} {rep['status']:5} {[i['code'] for i in rep['issues']]}")
        return 1 if bad else 0
    if a.cmd == "crosscheck":
        from . import crosscheck
        r = crosscheck.run()
        print("NSE archives unreachable" if not r["reachable"] else f"{len(r['results'])} closes compared, {len(r['mismatches'])} mismatch(es) beyond {r['tolerance_pct']}%")
        for m in r["mismatches"]:
            print(f"  {m['symbol']} {m['date']} yfinance {m['yfinance']} vs NSE {m['nse']} ({m['diff_pct']}%)")
        return 1 if r["mismatches"] else 0
    if a.cmd == "health":
        ok, msg = status.healthy()
        print(msg)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
