#!/usr/bin/env bash
set -euo pipefail

echo "=== BuzzBot Development Bootstrap ==="

# 1. Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Install Python 3.11+."
    exit 1
fi

# 2. Create venv if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate

# 3. Install Python deps
echo "Installing Python dependencies..."
pip install -e ".[dev]"

# 4. Copy .env if missing
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo ">>> IMPORTANT: Edit .env and add your API keys <<<"
fi

# 5. Start DB
echo "Starting Postgres (docker compose)..."
docker compose up -d db

echo "Waiting for database to be ready..."
sleep 3

# 6. Run migrations
echo "Running Alembic migrations..."
alembic upgrade head

# 7. Frontend deps
if command -v npm &> /dev/null; then
    echo "Installing frontend dependencies..."
    cd frontend && npm install && cd ..
else
    echo "WARNING: npm not found. Skip frontend install. Install Node.js to use the UI."
fi

echo ""
echo "=== Bootstrap complete! ==="
echo "Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. make ingest       # ingest seed data"
echo "  3. make run-backend  # start API server"
echo "  4. make run-frontend # start UI"
