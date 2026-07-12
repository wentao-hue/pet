#!/usr/bin/env bash
set -euo pipefail

# Generic PET experiment harness.
#
# Fill workload commands through environment variables on the target machine,
# for example:
#   GRAPH500_CMD='numactl --membind=0 ./graph500_reference_bfs ...' \
#   LIBLINEAR_CMD='./train -s 2 kdd2010 ...' \
#   ./pet_repro/scripts/run_pet_matrix.sh fits
#
# The script records timing, PET stats, numastat and vmstat. It never fills in
# paper result numbers by itself.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-fits}"
OUT_ROOT="${OUT_ROOT:-$ROOT/pet_repro/results/matrix/$MODE}"
REPEAT="${REPEAT:-3}"

case "$MODE" in
  fits|oversub|sensitivity|adversarial|thp) ;;
  *)
    echo "unknown mode: $MODE" >&2
    echo "modes: fits, oversub, sensitivity, adversarial, thp" >&2
    exit 2
    ;;
esac

declare -A COMMANDS=(
  [graph500]="${GRAPH500_CMD:-}"
  [imagick_s]="${IMAGICK_S_CMD:-}"
  [xz_s]="${XZ_S_CMD:-}"
  [gcc_s]="${GCC_S_CMD:-}"
  [roms_s]="${ROMS_S_CMD:-}"
  [liblinear]="${LIBLINEAR_CMD:-}"
  [gapbs_bc]="${GAPBS_BC_CMD:-}"
  [gapbs_bfs]="${GAPBS_BFS_CMD:-}"
  [gapbs_pr]="${GAPBS_PR_CMD:-}"
  [redis_ycsb_a]="${REDIS_YCSB_A_CMD:-}"
  [redis_ycsb_b]="${REDIS_YCSB_B_CMD:-}"
  [xsbench]="${XSBENCH_CMD:-}"
  [dacapo_h2]="${DACAPO_H2_CMD:-}"
)

capture_stats() {
  local out="$1"

  mkdir -p "$out"
  if [[ -r /proc/pet/stats ]]; then
    cat /proc/pet/stats > "$out/pet_stats"
  fi
  numastat -m > "$out/numastat" 2>/dev/null || true
  vmstat 1 3 > "$out/vmstat" 2>/dev/null || true
}

run_one() {
  local name="$1"
  local cmd="$2"
  local rep="$3"
  local out="$OUT_ROOT/$name/rep-$rep"

  mkdir -p "$out"
  capture_stats "$out/before"
  printf '%s\n' "$cmd" > "$out/command.txt"
  /usr/bin/time -f 'elapsed_sec=%e\nuser_sec=%U\nsys_sec=%S\nmaxrss_kb=%M' \
    -o "$out/time.txt" bash -lc "$cmd" \
    > "$out/stdout.txt" 2> "$out/stderr.txt"
  capture_stats "$out/after"
}

mkdir -p "$OUT_ROOT"
if [[ -w /proc/pet/enabled ]]; then
  echo "${PET_ENABLE:-1}" > /proc/pet/enabled
fi

for name in "${!COMMANDS[@]}"; do
  cmd="${COMMANDS[$name]}"
  [[ -n "$cmd" ]] || continue
  for rep in $(seq 1 "$REPEAT"); do
    run_one "$name" "$cmd" "$rep"
  done
done

cat > "$OUT_ROOT/README.txt" <<EOF2
Mode: $MODE
Repeat: $REPEAT

Fill paper tables only from these captured outputs or separately verified
baseline runs with matching workload scale, fast-memory capacity, kernel config,
THP setting, and PET parameters.
EOF2
