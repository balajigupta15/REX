# Rex Lite API (FastAPI) and data pipeline worker: one image, two commands.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Writable state lives on volumes: sqlite memory (REX_DB), Parquet store (REX_STORE), price cache (REX_DATA_CACHE).
ENV REX_DB=/data/sqlite/rexlite_memory.sqlite REX_STORE=/data/store REX_DATA_CACHE=/data/cache
RUN useradd -m rex && mkdir -p /data/sqlite /data/store /data/cache && chown -R rex /data /app
USER rex
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
