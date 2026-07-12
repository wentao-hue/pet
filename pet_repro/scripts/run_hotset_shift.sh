#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT/pet_repro/results/hotset_shift}"
BIN="$OUT_DIR/hotset_shift"
SRC="$ROOT/pet_repro/microbench/hotset_shift.c"

TOTAL_GB="${TOTAL_GB:-120}"
HOT_GB="${HOT_GB:-1}"
THREADS="${THREADS:-8}"
PHASE_SEC="${PHASE_SEC:-60}"
PHASES="${PHASES:-30}"

mkdir -p "$OUT_DIR"
cc -O2 -pthread "$SRC" -o "$BIN"

if [[ -w /proc/pet/enabled ]]; then
  echo 1 > /proc/pet/enabled
fi

if [[ -r /proc/pet/stats ]]; then
  cat /proc/pet/stats > "$OUT_DIR/pet_stats.before"
fi

"$BIN" \
  --total-gb "$TOTAL_GB" \
  --hot-gb "$HOT_GB" \
  --threads "$THREADS" \
  --phase-sec "$PHASE_SEC" \
  --phases "$PHASES" \
  | tee "$OUT_DIR/throughput.csv"

if [[ -r /proc/pet/stats ]]; then
  cat /proc/pet/stats > "$OUT_DIR/pet_stats.after"
fi
numastat -m > "$OUT_DIR/numastat.after" 2>/dev/null || true
vmstat 1 5 > "$OUT_DIR/vmstat.tail" 2>/dev/null || true
