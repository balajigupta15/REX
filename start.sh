#!/bin/bash
# Start Rex: API on :8000, web on :3000. Usage: ./start.sh [--build]
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ -z "${GEMINI_API_KEY:-}" ] && [ -f "$ROOT/.env" ]; then
  GEMINI_API_KEY="$(sed -n 's/^GEMINI_API_KEY=//p' "$ROOT/.env" | tail -n 1)"
  export GEMINI_API_KEY
fi
echo "Stopping old Rex servers..."
lsof -ti tcp:8000 | xargs kill 2>/dev/null
lsof -ti tcp:3000 | xargs kill 2>/dev/null
sleep 1
echo "Starting YugabyteDB on :5434..."
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$ROOT/docker-compose.yml" up -d yugabyte >/tmp/rexyb.log 2>&1 || echo "YugabyteDB did not start; API will fall back to SQLite if unreachable."
  YB_CID="$(docker compose -f "$ROOT/docker-compose.yml" ps -q yugabyte 2>/dev/null)"
  if [ -n "$YB_CID" ]; then
    for i in $(seq 1 120); do
      YB_STATE="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$YB_CID" 2>/dev/null)"
      [ "$YB_STATE" = "healthy" ] && break
      sleep 1
    done
  fi
fi
echo "Starting API on :8000..."
DB_URL="${REX_DB_URL-postgresql://yugabyte@127.0.0.1:5434/rexlite}"
cd "$ROOT/backend" && REX_DB_URL="$DB_URL" REX_OFFLINE=0 nohup python3 -m uvicorn app.main:app --port 8000 > /tmp/rexapi.log 2>&1 &
RUN_DIR="$ROOT/.run"
PIPELINE_PID_FILE="$RUN_DIR/pipeline.pid"
mkdir -p "$RUN_DIR"
if [ -f "$PIPELINE_PID_FILE" ] && kill -0 "$(cat "$PIPELINE_PID_FILE")" 2>/dev/null && ps -p "$(cat "$PIPELINE_PID_FILE")" -o command= | grep -q -- "-m pipeline schedule"; then
  echo "Market data scheduler already running."
else
  echo "Starting market data scheduler..."
  (
    cd "$ROOT/backend" || exit 1
    REX_OFFLINE=0 nohup python3 -m pipeline schedule >> /tmp/rexpipeline.log 2>&1 < /dev/null &
    echo "$!" > "$PIPELINE_PID_FILE"
  )
fi
cd "$ROOT/web"
if [ "$1" == "--build" ] || [ ! -d .next ]; then echo "Building web..."; npm run build || exit 1; fi
echo "Starting web on :3000..."
nohup npx next start -p 3000 > /tmp/rexweb.log 2>&1 &
for i in $(seq 1 30); do
  if curl -s localhost:8000/health >/dev/null && curl -s -o /dev/null localhost:3000/ai; then
    echo "Rex is up: http://localhost:3000  (logs: /tmp/rexapi.log, /tmp/rexweb.log)"
    open http://localhost:3000 2>/dev/null; exit 0
  fi; sleep 1
done
echo "Something didn't start. Check /tmp/rexapi.log and /tmp/rexweb.log"; exit 1
