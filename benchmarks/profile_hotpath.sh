#!/usr/bin/env bash
# Flame-graph the pounce hot path under load using py-spy.
#
# Usage:
#   ./benchmarks/profile_hotpath.sh [--workers N] [--duration S] [--output FILE]
#
# Prerequisites:
#   pip install py-spy
#   brew install wrk
#
# Output: flame.svg (or specified --output file)

set -euo pipefail

WORKERS=1
DURATION=20
OUTPUT="flame.svg"
PORT=8199
APP="benchmarks.apps.hello:app"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workers) WORKERS="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --app) APP="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== Pounce Hot-Path Profiler ==="
echo "  Workers:  $WORKERS"
echo "  Duration: ${DURATION}s"
echo "  Output:   $OUTPUT"
echo "  App:      $APP"
echo ""

# Check prerequisites
command -v py-spy >/dev/null 2>&1 || { echo "Error: py-spy not found. Install with: pip install py-spy"; exit 1; }
command -v wrk >/dev/null 2>&1 || { echo "Error: wrk not found. Install with: brew install wrk"; exit 1; }

# Start pounce in background
echo "Starting pounce on port $PORT..."
python -m pounce "$APP" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --workers "$WORKERS" \
    --no-access-log \
    --no-compression &
POUNCE_PID=$!

# Wait for server to be ready
sleep 1
if ! kill -0 "$POUNCE_PID" 2>/dev/null; then
    echo "Error: pounce failed to start"
    exit 1
fi

echo "Pounce started (PID: $POUNCE_PID)"

# Start load generator in background
echo "Starting wrk load generator..."
wrk -t4 -c100 -d"${DURATION}s" "http://127.0.0.1:${PORT}/" &
WRK_PID=$!

# Attach py-spy (needs ~2s to warm up)
sleep 1
echo "Attaching py-spy for $((DURATION - 2))s..."
py-spy record \
    --output "$OUTPUT" \
    --pid "$POUNCE_PID" \
    --duration "$((DURATION - 2))" \
    --format flamegraph \
    --subprocesses \
    2>/dev/null || true

# Wait for wrk to finish
wait "$WRK_PID" 2>/dev/null || true

# Stop pounce
echo "Stopping pounce..."
kill "$POUNCE_PID" 2>/dev/null || true
wait "$POUNCE_PID" 2>/dev/null || true

echo ""
echo "Done. Flame graph saved to: $OUTPUT"
echo "Open with: open $OUTPUT"
