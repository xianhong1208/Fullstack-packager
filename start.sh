#!/bin/bash
set -e

# Ensure uv is in PATH when running via sudo
export PATH="$HOME/.local/bin:$PATH"

cd "$(dirname "$0")"

# Default values
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5018}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: ./start.sh [options]"
            echo ""
            echo "Options:"
            echo "  --host HOST    Server host (default: 0.0.0.0)"
            echo "  --port PORT    Server port (default: 5018)"
            echo ""
            echo "Environment variables:"
            echo "  HOST, PORT     Same as above"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=== Build Center ==="

# Build frontend. Rebuild by default so code changes actually take effect —
# the old "skip if dist/ exists" behavior silently served a stale build
# after every frontend change. Set SKIP_FRONTEND=1 to reuse the existing
# dist (only honored when a dist already exists).
if [ "$SKIP_FRONTEND" = "1" ] && [ -d "frontend/dist" ]; then
    echo "[1/2] SKIP_FRONTEND=1 — reusing existing frontend/dist"
else
    echo "[1/2] Building frontend..."
    cd frontend
    # npm install only when deps are missing — keeps rebuilds fast
    if [ ! -d "node_modules" ]; then
        npm install
    fi
    npm run build
    cd ..
fi

# Sync dependencies with uv
echo "[2/2] Starting backend..."
echo "Syncing dependencies..."
uv sync

# Start server
echo ""
echo "=== Server starting at http://${HOST}:${PORT} ==="
echo ""
HOST="$HOST" PORT="$PORT" uv run python main.py
