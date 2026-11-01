"""Run model-written strategy code in an isolated subprocess.
Layers: AST allowlist (ast_guard) in this process -> separate python process (-I, EMPTY environment so no API keys, cwd = throwaway temp dir)
-> network namespace via `unshare -rn` when the kernel allows it (else socket calls are patched out) -> restricted builtins -> memory cap
(RLIMIT_AS), CPU cap (RLIMIT_CPU), a SIGALRM wall alarm (default 5 s) and a parent-side hard kill -> look-ahead check on truncated data."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
import pandas as pd
from . import ast_guard

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_worker.py")
DEFAULT_TIMEOUT_S = 5.0
_NETNS: bool | None = None


class SandboxError(Exception):
    def __init__(self, msg: str, kind: str = "runtime"):
        super().__init__(msg)
        self.kind = kind


def _netns_ok() -> bool:
    global _NETNS
    if _NETNS is None:
        u = shutil.which("unshare")
        try:
            _NETNS = bool(u) and subprocess.run([u, "-rn", "true"], capture_output=True, timeout=5).returncode == 0
        except Exception:
            _NETNS = False
    return _NETNS


def isolation() -> str:
    return "network namespace (unshare -rn) + patched sockets" if _netns_ok() else "patched sockets (no network namespace available on this host)"


def _lower(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    out.columns = ["open", "high", "low", "close", "volume"]
    return out


def run_batch(codes: list[str], df: pd.DataFrame, timeout: float = DEFAULT_TIMEOUT_S, mem_mb: int = 768) -> list[dict]:
    """Screen many already-validated candidate codes in ONE subprocess (positions only, no look-ahead pass). Each item: dict(ok, positions|error)."""
    for c in codes:
        ast_guard.validate(c)
    out = _exec(dict(batch=list(codes), timeout=float(timeout), mem_mb=int(mem_mb), min_cut=60), df, float(timeout) * len(codes) + 25)
    if "batch" not in out:
        raise SandboxError(out.get("error", "batch sandbox failed"), out.get("kind", "sandbox"))
    for o in out["batch"]:
        if o.get("ok"):
            o["positions"] = [{"0": 0, "1": 1, "2": -1}[ch] for ch in o.pop("pos")]
    return out["batch"]


def _exec(cfg: dict, df: pd.DataFrame, wall: float) -> dict:
    work = tempfile.mkdtemp(prefix="rexai_")
    try:
        _lower(df).to_csv(os.path.join(work, "df.csv"))
        with open(os.path.join(work, "in.json"), "w") as f:
            json.dump(cfg, f)
        cmd = [sys.executable, "-I", WORKER, work]
        if _netns_ok():
            cmd = [shutil.which("unshare"), "-rn"] + cmd
        env = {"PATH": "/usr/bin:/bin", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8"}
        try:
            subprocess.run(cmd, cwd=work, env=env, capture_output=True, timeout=wall, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise SandboxError(f"Timed out (killed after {wall:g} s). Use vectorised pandas instead of slow loops.", "timeout")
        try:
            return json.load(open(os.path.join(work, "out.json")))
        except Exception:
            raise SandboxError("The sandbox process crashed (probably out of memory or CPU).", "sandbox")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_signals(code: str, df: pd.DataFrame, timeout: float = DEFAULT_TIMEOUT_S, mem_mb: int = 768, lookahead: bool = True) -> dict:
    """df has the loader's capitalised columns (Open/High/Low/Close/Volume). Returns dict(positions=[...], lookahead_free, violations, cuts, seconds).
    Raises ast_guard.CodeRejected (code outside the allowlist) or SandboxError (timeout / runtime error / look-ahead)."""
    ast_guard.validate(code)
    cfg = dict(code=code, timeout=float(timeout), mem_mb=int(mem_mb), min_cut=60)
    if not lookahead:
        cfg["skip_lookahead"] = True
    out = _exec(cfg, df, float(timeout) + 25)
    if not out.get("ok"):
        if out.get("kind") == "rejected":
            raise ast_guard.CodeRejected(out["error"])
        raise SandboxError(out.get("error", "unknown sandbox error"), out.get("kind", "runtime"))
    if out["violations"]:
        v = out["violations"][0]
        if v["kind"] == "changed":
            raise SandboxError(f"Look-ahead detected: the position on bar {v['first_bar']} ({v['first_date']}) changes when later bars are removed "
                               f"({v['n_changed']} bars differ with {v['cut']} bars of data). The code uses future data (for example shift(-1), a centred window, "
                               f"or statistics of the whole series). Use only data up to the current bar.", "lookahead")
        raise SandboxError(f"The function fails on truncated data ({v['detail']}), so it cannot be evaluated bar by bar.", "lookahead")
    return out
