"""AST allowlist for model-written strategy code. Standalone on purpose (stdlib only): the sandbox worker imports this file too, so the
code is checked twice, once in the API process and again inside the isolated subprocess.

The contract: the code defines `signals(df)` (plus optional helper functions / constants) and uses only pandas (`pd`), numpy (`np`) and a
handful of pure builtins. Everything not on the allowlist is rejected with a plain-English reason that is sent back to the model.
"""
from __future__ import annotations
import ast

MAX_CODE_CHARS = 6000
MAX_NODES = 2500
MAX_STR = 40

class CodeRejected(ValueError):
    """The code is outside the allowlist. str(e) is a one-line reason."""

SAFE_BUILTINS = {"abs", "min", "max", "len", "range", "sum", "round", "float", "int", "bool", "enumerate", "zip", "list", "tuple", "dict",
                 "set", "sorted", "reversed", "any", "all", "True", "False", "None"}
# names that must never appear, even as a local variable (they are the usual escape hatches)
DENY_NAMES = {"open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "hasattr", "globals", "locals", "vars", "dir", "type", "object",
              "input", "print", "breakpoint", "help", "memoryview", "bytearray", "bytes", "super", "classmethod", "staticmethod", "property",
              "id", "hash", "iter", "next", "callable", "isinstance", "issubclass", "format", "str", "repr", "ascii", "map", "filter", "slice",
              "os", "sys", "subprocess", "socket", "importlib", "builtins", "ctypes", "pickle", "shutil", "pathlib", "requests", "httpx", "urllib"}

ALLOWED_PD = {"Series", "DataFrame", "concat", "isna", "notna", "to_numeric", "NA", "NaT"}
ALLOWED_NP = {"nan", "inf", "pi", "where", "select", "maximum", "minimum", "abs", "sign", "log", "log1p", "exp", "sqrt", "mean", "std", "sum", "max", "min",
              "nanmean", "nanstd", "nanmax", "nanmin", "isnan", "isfinite", "clip", "arange", "zeros", "ones", "full", "zeros_like", "ones_like",
              "full_like", "array", "diff", "cumsum", "cumprod", "floor", "ceil", "round", "power", "minimum", "float64", "int64", "bool_", "sin", "cos",
              "tanh", "median", "percentile", "quantile", "corrcoef", "logical_and", "logical_or", "logical_not", "isin", "append", "concatenate", "argmax", "argmin"}
# methods allowed on Series / DataFrame / rolling / ewm / expanding / dt objects. Anything else (eval, query, to_csv, read_*, pipe, __x__ ...) is refused.
ALLOWED_METHODS = {
    "shift", "rolling", "ewm", "expanding", "mean", "std", "var", "sum", "min", "max", "median", "abs", "diff", "pct_change", "clip", "where", "mask",
    "fillna", "astype", "rank", "cummax", "cummin", "cumsum", "cumprod", "apply", "iloc", "loc", "values", "to_numpy", "index", "isna", "notna", "isnull",
    "notnull", "replace", "corr", "quantile", "skew", "kurt", "count", "idxmax", "idxmin", "copy", "ffill", "bfill", "dropna", "round", "reindex", "sign",
    "dt", "dayofweek", "month", "hour", "day", "weekday", "year", "dayofyear", "minute", "rolling_mean", "between", "isin", "nlargest", "nsmallest",
    "log", "exp", "sqrt", "shape", "size", "empty", "any", "all", "prod", "sem", "cov", "gt", "lt", "ge", "le", "eq", "ne", "add", "sub", "mul", "div",
    "truediv", "rsub", "rdiv", "combine_first", "interpolate", "lt", "ngroup", "is_monotonic_increasing", "map", "agg", "name", "dtype", "diff",
    "ewm", "std", "max", "iat", "at", "T", "head", "tail", "to_list", "tolist", "get", "update", "bool", "squeeze", "rename", "idx",
}
BANNED_METHODS = {"eval", "query", "pipe", "to_csv", "to_pickle", "to_sql", "to_json", "to_excel", "to_parquet", "read_csv", "format", "format_map",
                  "system", "popen", "exec", "load", "loads", "save", "dump"}
FORBIDDEN_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef, ast.While, ast.Try, ast.With, ast.Raise, ast.Delete,
                   ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr, ast.JoinedStr, ast.FormattedValue,
                   ast.Assert, ast.Match, ast.TypeAlias)
_TRY_STAR = getattr(ast, "TryStar", None)


def _fail(node, why):
    line = getattr(node, "lineno", None)
    raise CodeRejected(f"{why}" + (f" (line {line})" if line else ""))


def validate(code: str) -> ast.Module:
    """Return the parsed module when the code is inside the allowlist, else raise CodeRejected."""
    if not isinstance(code, str) or not code.strip():
        raise CodeRejected("No code was provided")
    if len(code) > MAX_CODE_CHARS:
        raise CodeRejected(f"Code is longer than {MAX_CODE_CHARS} characters; make it shorter")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise CodeRejected(f"Syntax error: {e.msg} (line {e.lineno})")
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CodeRejected("Code is too large or too complex; simplify it")
    # --- top level: functions and simple constant assignments only
    sig = None
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            if n.name == "signals":
                sig = n
        elif isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) for t in n.targets) and isinstance(n.value, (ast.Constant, ast.Tuple, ast.List, ast.UnaryOp, ast.BinOp)):
            pass
        elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            pass
        else:
            _fail(n, "Only function definitions and constant assignments are allowed at the top level")
    if sig is None:
        raise CodeRejected("The code must define a function named signals(df)")
    a = sig.args
    if len(a.args) != 1 or a.vararg or a.kwarg or a.kwonlyargs or a.defaults:
        raise CodeRejected("signals must take exactly one argument: df")
    # --- names that exist (assigned / args / loop vars / comprehension vars / helper functions)
    defined = {"df", "pd", "np"} | SAFE_BUILTINS
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            defined.add(n.name)
            for x in n.args.args:
                defined.add(x.arg)
        elif isinstance(n, ast.Lambda):
            for x in n.args.args:
                defined.add(x.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            defined.add(n.id)
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.Module)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)}
    for n in ast.walk(tree):
        if isinstance(n, FORBIDDEN_NODES) or (_TRY_STAR and isinstance(n, _TRY_STAR)):
            what = {ast.Import: "imports", ast.ImportFrom: "imports", ast.While: "while loops (use vectorised pandas, or a for loop over range())",
                    ast.JoinedStr: "f-strings", ast.Try: "try/except", ast.ClassDef: "classes", ast.With: "with blocks", ast.Raise: "raise"}.get(type(n), type(n).__name__)
            _fail(n, f"Not allowed: {what}")
        if isinstance(n, ast.Name):
            if n.id.startswith("__") or n.id in DENY_NAMES:
                _fail(n, f"Name '{n.id}' is not allowed")
            if isinstance(n.ctx, ast.Load) and n.id not in defined:
                _fail(n, f"Unknown name '{n.id}'. Only df, pd, np, your own variables and a few pure builtins are available")
        elif isinstance(n, ast.arg):
            if n.arg.startswith("__") or n.arg in DENY_NAMES:
                _fail(n, f"Argument name '{n.arg}' is not allowed")
        elif isinstance(n, ast.Attribute):
            at = n.attr
            if at.startswith("_"):
                _fail(n, f"Attribute '{at}' is not allowed (no underscore / dunder access)")
            if at in BANNED_METHODS:
                _fail(n, f"Method '{at}' is not allowed")
            if isinstance(n.value, ast.Name) and n.value.id in ("pd", "np"):
                ok = ALLOWED_PD if n.value.id == "pd" else ALLOWED_NP
                if at not in ok:
                    _fail(n, f"{n.value.id}.{at} is not on the allowlist")
            elif at not in ALLOWED_METHODS:
                _fail(n, f"Attribute/method '.{at}' is not on the allowlist")
        elif isinstance(n, ast.Constant):
            if isinstance(n.value, bytes):
                _fail(n, "Bytes literals are not allowed")
            if isinstance(n.value, str) and len(n.value) > MAX_STR and id(n) not in docs:
                _fail(n, f"String longer than {MAX_STR} characters")
            if isinstance(n.value, int) and not isinstance(n.value, bool) and abs(n.value) > 10**7:
                _fail(n, "Integer constant is too large")
        elif isinstance(n, ast.For):
            it = n.iter
            if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id in ("range", "enumerate", "zip")):
                _fail(n, "for loops may only iterate over range(...), enumerate(...) or zip(...)")
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in DENY_NAMES:
                _fail(n, f"Call to '{f.id}' is not allowed")
    # no function may reach for the module via call-through-subscript tricks: calling a subscript result / call result is refused
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Call, ast.Subscript)):
            _fail(n, "Calling the result of a call or subscript is not allowed")
    return tree
