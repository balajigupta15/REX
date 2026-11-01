"""AI Strategy Builder: Gemini writes a `signals(df)` function, Rex validates it (AST allowlist), runs it in an isolated subprocess,
checks it for look-ahead, then backtests it with the existing data loaders, costs, windows and metrics. See api.py for the routes."""
