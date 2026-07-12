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

# A prebuilt static memtoucher in $WORK is reused (useful on hosts
# without an x86_64 cross compiler; build one via docker beforehand).
[ -x "$WORK/memtoucher" ] || \
  "$CROSS_CC" -O2 -static -o "$WORK/memtoucher" "$HERE/memtoucher.c"

mkdir -p "$WORK/root/bin" "$WORK/root/proc" "$WORK/root/sys"
cp "$BUSYBOX" "$WORK/root/bin/busybox"
ln -sf busybox "$WORK/root/bin/sh"
cp "$HERE/init" "$WORK/root/init"
cp "$WORK/memtoucher" "$WORK/root/memtoucher"
chmod +x "$WORK/root/init" "$WORK/root/bin/busybox" "$WORK/root/memtoucher"
(cd "$WORK/root" && find . | cpio -o -H newc | gzip) > "$WORK/initramfs.cpio.gz"

# Guard against a hung guest: panic=-1 handles panics, but a deadlock
# would otherwise block this harness forever.
QEMU_TIMEOUT="${QEMU_TIMEOUT:-240}"
QEMU_CMD=(qemu-system-x86_64 -M q35 -cpu max -smp 2 -m 2048
  -object memory-backend-ram,id=m0,size=1024M
  -object memory-backend-ram,id=m1,size=1024M
  -numa node,nodeid=0,cpus=0-1,memdev=m0
  -numa node,nodeid=1,memdev=m1
  -kernel "$BZIMAGE" -initrd "$WORK/initramfs.cpio.gz"
  -append "console=ttyS0 pet.fast_node=0 pet.slow_node=1 pet.sampling_interval_ms=100 pet.scan_interval_ms=1000 numa_balancing=disable panic=-1"
  -nographic -no-reboot)
command -v qemu-system-x86_64 >/dev/null || {
  echo "FAIL: qemu-system-x86_64 not found" >&2; exit 1; }
rc=0
if command -v timeout >/dev/null; then
  timeout "$QEMU_TIMEOUT" "${QEMU_CMD[@]}" > "$WORK/serial.log" 2>&1 || rc=$?
else
  "${QEMU_CMD[@]}" > "$WORK/serial.log" 2>&1 || rc=$?
fi
if [ "$rc" = 124 ]; then
  echo "FAIL: guest did not finish within ${QEMU_TIMEOUT}s (hang?)"
fi

# Serial consoles emit CRLF; strip CR so the numeric checks below stay
# portable across awk implementations (mawk/busybox-awk strnum rules).
tr -d '\r' < "$WORK/serial.log" > "$WORK/serial.clean"
echo "serial log: $WORK/serial.log"
fail=0
grep -q 'SMOKE DONE' "$WORK/serial.clean" || { echo "FAIL: guest did not finish"; fail=1; }
awk -F= '/^demoted_pages=/{v=$2+0} END{exit !(v >= 65536)}' "$WORK/serial.clean" \
  || { echo "FAIL: demotion never covered the idle block"; fail=1; }
awk -F= '/^fake_faults=/{v=$2+0} END{exit !(v > 0)}' "$WORK/serial.clean" \
  || { echo "FAIL: no fake faults recorded"; fail=1; }
awk -F= '/^promoted_pblocks=/{v=$2+0} END{exit !(v > 0)}' "$WORK/serial.clean" \
  || { echo "FAIL: no promotion happened"; fail=1; }
awk -F= '/^migration_failures=/{v=$2+0} END{exit !(v == 0)}' "$WORK/serial.clean" \
  || { echo "FAIL: migration failures observed"; fail=1; }
if grep -qE 'WARNING|BUG:|Oops' "$WORK/serial.clean"; then
  echo "FAIL: kernel warnings/oops in serial log"
  fail=1
fi
[ "$fail" = 0 ] && echo "QEMU SMOKE: PASS" || echo "QEMU SMOKE: FAIL"
exit "$fail"
