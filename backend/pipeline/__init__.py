"""Rex data pipeline: incremental ingestion of NSE daily bars (yfinance) and Binance klines into a Parquet store with a SQLite catalog.

    python -m pipeline run [--market nse|crypto|all]     one ingestion run
    python -m pipeline schedule                          NSE after 16:00 IST on weekdays, crypto hourly
    python -m pipeline seed | status | validate | health
"""
