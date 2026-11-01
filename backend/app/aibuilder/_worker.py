"""Sandbox worker. Runs in a SEPARATE python process (started by sandbox.py with -I, empty environment, no network namespace when available).
Reads <workdir>/in.json + <workdir>/df.csv, re-validates the code, executes `signals(df)` with restricted builtins under a CPU alarm and a
memory cap, runs the look-ahead check (signals on truncated data must equal the full-data signals on the same bars), writes <workdir>/out.json.
Never imported by the app."""
import json, os, sys, time, signal, traceback, resource

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
WORK = sys.argv[1]


def write(obj):
    with open(os.path.join(WORK, "out.json"), "w") as f:
        json.dump(obj, f)


def block_network():
    import socket
    def _no(*a, **k):
        raise OSError("network access is disabled in the strategy sandbox")
    socket.socket = _no
    socket.create_connection = _no
    socket.getaddrinfo = _no
    socket.gethostbyname = _no


class Timeout(Exception):
    pass


def main():
    cfg = json.load(open(os.path.join(WORK, "in.json")))
    import numpy as np, pandas as pd
    import ast_guard
    block_network()
    try:    # memory cap: current address space + headroom (pandas/numpy are already imported)
        pages = int(open("/proc/self/statm").read().split()[0]) * resource.getpagesize()
        lim = pages + int(cfg.get("mem_mb", 768)) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
        resource.setrlimit(resource.RLIMIT_CPU, (int(cfg["timeout"]) + 5, int(cfg["timeout"]) + 6))
        resource.setrlimit(resource.RLIMIT_FSIZE, (48 << 20, 48 << 20))
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0)) if False else None
    except Exception:
        pass
    df = pd.read_csv(os.path.join(WORK, "df.csv"), index_col=0, parse_dates=True)
    tree = ast_guard.validate(cfg["code"]) if cfg.get("code") else None
    import builtins
    safe = {k: getattr(builtins, k) for k in ast_guard.SAFE_BUILTINS if hasattr(builtins, k)}
    g = {"__builtins__": safe, "pd": pd, "np": np}
    t0 = time.time()

    def on_alarm(*a):
        raise Timeout()
    signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, float(cfg["timeout"]))
    n = len(df)

    def call(frame):
        out = g["signals"](frame.copy())
        if isinstance(out, (pd.Series, pd.DataFrame)):
            if isinstance(out, pd.DataFrame):
                raise ValueError("signals must return a pandas Series, not a DataFrame")
            if len(out) != len(frame):
                raise ValueError(f"signals returned {len(out)} values for {len(frame)} bars")
            a = out.to_numpy()
        else:
            a = np.asarray(out)
            if a.ndim != 1 or len(a) != len(frame):
                raise ValueError("signals must return a 1-D Series with one value per bar")
        a = pd.to_numeric(pd.Series(a), errors="coerce").to_numpy(dtype=float)
        a = np.where(np.isfinite(a), a, 0.0)
        if not np.isin(a, (-1.0, 0.0, 1.0)).all():
            bad = sorted({float(x) for x in a if x not in (-1.0, 0.0, 1.0)})[:3]
            raise ValueError(f"positions must be 1, 0 or -1; got values like {bad}")
        return a.astype(int)

    if cfg.get("batch"):       # screening mode: many trusted/validated candidate codes, positions only, no look-ahead pass
        out = []
        for code in cfg["batch"]:
            try:
                signal.setitimer(signal.ITIMER_REAL, float(cfg["timeout"]))
                gg = {"__builtins__": safe, "pd": pd, "np": np}
                exec(compile(ast_guard.validate(code), "<strategy>", "exec"), gg)
                g = gg
                out.append(dict(ok=True, pos="".join("2" if x < 0 else str(int(x)) for x in call(df))))
            except Timeout:
                out.append(dict(ok=False, error="timeout"))
            except Exception as e:
                out.append(dict(ok=False, error=f"{type(e).__name__}: {str(e)[:120]}"))
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        write(dict(ok=True, batch=out))
        return
    res = dict(ok=False)
    try:
        exec(compile(tree, "<strategy>", "exec"), g)
        full = call(df)
        # ---- look-ahead check: positions on bars [0, k) must not change when only the first k bars exist
        rng = np.random.default_rng(7)
        lo = min(max(int(cfg.get("min_cut", 60)), 30), max(n - 2, 31))
        cuts = sorted({int(n * f) for f in (0.3, 0.45, 0.6, 0.75, 0.88, 0.96)} | {int(x) for x in rng.integers(lo, max(n - 1, lo + 1), 3)})
        cuts = [k for k in cuts if lo <= k < n] if not cfg.get("skip_lookahead") else []
        viol = []
        for k in cuts:
            try:
                part = call(df.iloc[:k])
            except Timeout:
                raise
            except Exception as e:
                viol.append(dict(cut=k, kind="error_on_truncated", detail=f"{type(e).__name__}: {str(e)[:120]}"))
                continue
            d = np.nonzero(part != full[:k])[0]
            if len(d):
                viol.append(dict(cut=k, kind="changed", first_bar=int(d[0]), first_date=str(df.index[d[0]])[:16], n_changed=int(len(d))))
        res = dict(ok=True, positions=[int(x) for x in full], cuts=cuts, violations=viol, lookahead_free=not viol, seconds=round(time.time() - t0, 3))
    except Timeout:
        res = dict(ok=False, error=f"Timed out after {cfg['timeout']} seconds. Use vectorised pandas instead of slow loops.", kind="timeout")
    except MemoryError:
        res = dict(ok=False, error="Ran out of memory (limit applies). Avoid building huge arrays.", kind="memory")
    except ast_guard.CodeRejected as e:
        res = dict(ok=False, error=str(e), kind="rejected")
    except Exception as e:
        tb = traceback.extract_tb(sys.exc_info()[2])
        line = next((f.lineno for f in reversed(tb) if f.filename == "<strategy>"), None)
        res = dict(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}" + (f" (strategy line {line})" if line else ""), kind="runtime")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    write(res)


try:
    main()
except BaseException as e:      # e.g. MemoryError while importing; the parent treats a missing/odd out.json as a failure
    try:
        write(dict(ok=False, error=f"Sandbox failed: {type(e).__name__}: {str(e)[:160]}", kind="sandbox"))
    except Exception:
        pass
