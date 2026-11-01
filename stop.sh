#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
lsof -ti tcp:8000 | xargs kill 2>/dev/null
lsof -ti tcp:3000 | xargs kill 2>/dev/null
PIPELINE_PID_FILE="$ROOT/.run/pipeline.pid"
if [ -f "$PIPELINE_PID_FILE" ]; then
  PIPELINE_PID="$(cat "$PIPELINE_PID_FILE")"
  PIPELINE_COMMAND="$(ps -p "$PIPELINE_PID" -o command= 2>/dev/null)"
  case "$PIPELINE_COMMAND" in
    *"-m pipeline schedule"*) kill "$PIPELINE_PID" 2>/dev/null; echo "Market data scheduler stopped." ;;
    *) echo "Ignoring stale pipeline PID file." ;;
  esac
  rm -f "$PIPELINE_PID_FILE"
fi
echo "Rex stopped."
