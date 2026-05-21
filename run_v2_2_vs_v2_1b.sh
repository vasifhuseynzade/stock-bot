#!/usr/bin/env bash
set -euo pipefail

# Run from the folder that contains backtest_engine_v2_2.py.
# Requires FMP_API_KEY in your environment.
# Example:
#   export FMP_API_KEY="your_key"
#   export DATA_DIR="/data"
#   bash run_v2_2_vs_v2_1b.sh

START_DATE="${START_DATE:-2022-01-01}"
END_DATE="${END_DATE:-2026-05-20}"
CAPITAL="${CAPITAL:-4000}"
DATA_DIR="${DATA_DIR:-./data}"
export DATA_DIR

if [ -z "${FMP_API_KEY:-}" ]; then
  echo "ERROR: FMP_API_KEY is not set."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== V2.2 VARIANT COMPARISON, normal slippage 10 bps ==="
python backtest_engine_v2_2.py \
  --mode variants \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --capital "$CAPITAL" \
  --slippage-bps 10

echo "=== V2.2 VARIANT COMPARISON, stress slippage 50 bps ==="
python backtest_engine_v2_2.py \
  --mode variants \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --capital "$CAPITAL" \
  --slippage-bps 50

echo "=== V2.2 WALK-FORWARD, 12-month warmup / 3-month windows ==="
python backtest_engine_v2_2.py \
  --mode walkforward \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --capital "$CAPITAL" \
  --slippage-bps 10 \
  --train-months 12 \
  --test-months 3

echo "DONE. Zip files are in: $DATA_DIR/backtests"
