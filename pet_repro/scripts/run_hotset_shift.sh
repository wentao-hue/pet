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

PET_ENABLE="${PET_ENABLE:-1}"
PET_ENABLED_PREV=""
if [[ "$PET_ENABLE" == "1" ]]; then
  if [[ -w /proc/pet/enabled ]]; then
    PET_ENABLED_PREV="$(cat /proc/pet/enabled)"
    echo 1 > /proc/pet/enabled
    # Restore the previous setting even if the benchmark fails.
    trap '[[ -n "$PET_ENABLED_PREV" ]] && echo "$PET_ENABLED_PREV" > /proc/pet/enabled' EXIT
  else
    echo "ERROR: PET_ENABLE=1 but /proc/pet/enabled is not writable" >&2
    echo "       (PET kernel booted? running as root?)" >&2
    exit 1
  fi
fi

if [[ -r /proc/pet/stats ]]; then
  cat /proc/pet/stats > "$OUT_DIR/pet_stats.before"
fi
{
  echo "TOTAL_GB=$TOTAL_GB HOT_GB=$HOT_GB THREADS=$THREADS"
  echo "PHASE_SEC=$PHASE_SEC PHASES=$PHASES PET_ENABLE=$PET_ENABLE"
  for p in /sys/module/pet/parameters/*; do
    [[ -r "$p" ]] && echo "$(basename "$p")=$(cat "$p")"
  done
} > "$OUT_DIR/run_params.txt" 2>/dev/null || true

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
