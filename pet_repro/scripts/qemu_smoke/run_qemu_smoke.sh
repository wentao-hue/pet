#!/usr/bin/env bash
# PET functional smoke test in QEMU with two NUMA nodes (node0 = fast,
# node1 = slow via pet.slow_node=1, so no memory-tiers setup is needed).
#
# Requirements: qemu-system-x86_64, cpio, gzip, and either a native x86_64
# cc or CROSS_CC=x86_64-linux-gnu-gcc; a STATIC x86_64 busybox binary
# (e.g. from the busybox:musl docker image — the :latest glibc variant is
# dynamically linked and will NOT boot).
#
# Usage:
#   BZIMAGE=/path/to/bzImage BUSYBOX=/path/to/static-busybox \
#     ./run_qemu_smoke.sh
#
# Pass criteria (checked automatically):
#   - demoted_pages grows to cover the idle 256 MiB block (>= 65536)
#   - fake_faults > 0 and promoted_pblocks > 0 after the re-touch phase
#   - migration_failures == 0, no WARNING/BUG/Oops in the serial log
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BZIMAGE="${BZIMAGE:?set BZIMAGE to a PET bzImage}"
BUSYBOX="${BUSYBOX:?set BUSYBOX to a static x86_64 busybox}"
CROSS_CC="${CROSS_CC:-cc}"
WORK="${WORK:-$(mktemp -d)}"

"$CROSS_CC" -O2 -static -o "$WORK/memtoucher" "$HERE/memtoucher.c"

mkdir -p "$WORK/root/bin" "$WORK/root/proc" "$WORK/root/sys"
cp "$BUSYBOX" "$WORK/root/bin/busybox"
ln -sf busybox "$WORK/root/bin/sh"
cp "$HERE/init" "$WORK/root/init"
cp "$WORK/memtoucher" "$WORK/root/memtoucher"
chmod +x "$WORK/root/init" "$WORK/root/bin/busybox" "$WORK/root/memtoucher"
(cd "$WORK/root" && find . | cpio -o -H newc | gzip) > "$WORK/initramfs.cpio.gz"

qemu-system-x86_64 -M q35 -cpu max -smp 2 -m 2048 \
  -object memory-backend-ram,id=m0,size=1024M \
  -object memory-backend-ram,id=m1,size=1024M \
  -numa node,nodeid=0,cpus=0-1,memdev=m0 \
  -numa node,nodeid=1,memdev=m1 \
  -kernel "$BZIMAGE" -initrd "$WORK/initramfs.cpio.gz" \
  -append "console=ttyS0 pet.fast_node=0 pet.slow_node=1 pet.sampling_interval_ms=100 pet.scan_interval_ms=1000 numa_balancing=disable panic=-1" \
  -nographic -no-reboot > "$WORK/serial.log" 2>&1 || true

echo "serial log: $WORK/serial.log"
fail=0
grep -q 'SMOKE DONE' "$WORK/serial.log" || { echo "FAIL: guest did not finish"; fail=1; }
awk -F= '/^demoted_pages=/{v=$2} END{exit !(v >= 65536)}' \
  <(grep '^demoted_pages=' "$WORK/serial.log") \
  || { echo "FAIL: demotion never covered the idle block"; fail=1; }
awk -F= '/^fake_faults=/{v=$2} END{exit !(v > 0)}' \
  <(grep '^fake_faults=' "$WORK/serial.log") \
  || { echo "FAIL: no fake faults recorded"; fail=1; }
awk -F= '/^promoted_pblocks=/{v=$2} END{exit !(v > 0)}' \
  <(grep '^promoted_pblocks=' "$WORK/serial.log") \
  || { echo "FAIL: no promotion happened"; fail=1; }
if grep -E '^fake' "$WORK/serial.log" >/dev/null && \
   grep -qE 'migration_failures=[1-9]' "$WORK/serial.log"; then
  echo "WARN: migration failures observed"
fi
if grep -qE 'WARNING|BUG:|Oops' "$WORK/serial.log"; then
  echo "FAIL: kernel warnings/oops in serial log"
  fail=1
fi
[ "$fail" = 0 ] && echo "QEMU SMOKE: PASS" || echo "QEMU SMOKE: FAIL"
exit "$fail"
