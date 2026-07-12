# PET Reproduction Notes

This directory contains the reproduction artifacts for:

> PET: Proactive Demotion for Efficient Tiered Memory Management, EuroSys 2025.

The original paper does not appear to provide source code publicly.  This
artifact therefore contains both a trace-driven model and a clean-room Linux
6.1.44 kernel prototype under `../linux-6.1.44-pet`.

Primary artifacts:

- `../linux-6.1.44-pet`: modified Linux 6.1.44 source tree.
- `../pet-linux-6.1.44-prototype.patch`: patch from clean Linux 6.1.44.
- `PET_DESIGN_COVERAGE.md`: design-point coverage matrix.
- `scripts/run_pet_matrix.sh`: benchmark matrix harness.
- `microbench/hotset_shift.c`: native hot-set shifting microbenchmark.

## What Is Implemented

- P-block allocation and initial splitting at 1 GiB.
- Four P-block states: `NORMAL`, `PHASE1`, `PHASE2`, `DEMOTED`.
- Scan/sample timing: 500 ms sampling interval and 10 s scan interval.
- Two-phase access sampling per paper §4.2/Fig. 6: interval N arms a random
  page by clearing its accessed bit, interval N+1 checks that same page, so a
  "hot" reading always refers to one sampling window.  Scan decisions require
  at least one completed check per scan (mmap_lock contention cannot fake a
  cold signal).
- Temporary blocks at 128 MiB.
- PHASE2 canary ratio at 10%.
- Promotion threshold from the paper's Thermostat-derived equation:
  `Rpromo = tolerable_slowdown * canary_ratio / misdemotion_penalty`.
- Split of mixed PHASE2 P-blocks into hot `NORMAL` and cold `DEMOTED` blocks.
- PROT_NONE canary fake faults and promotion via `kpromoted`.
- File-page demotion through inode open-count tracking.
- Successful-`munmap()` trim/split of affected P-blocks.
- `brk()` and `mremap()` lifecycle coverage for anonymous P-block capture,
  including full-VMA gap filling for small expansions.
- `mremap()` movement preserves PET P-block state, tracking bitmaps, fake-fault
  counters, and canary protection metadata before the old range is released.
- Reopen-safe cold-file demotion with processing generations.
- Runtime `file_demote_enabled=0` (and `enabled=0`, via proc or sysfs) drains
  queued cold-file inode references.
- `generic_shutdown_super()` drains per-superblock cold-file queue and waits
  for in-flight processing, so umount never races PET-held inode references.
- Atomic fault-window generations for promotion thresholds, including busy
  demoted P-block handling when the global promotion threshold fires.
- MMU notifier coverage around canary PTE protection changes.
- THP handling without implicit PMD splits: sampling reads/clears the accessed
  bit at PMD granularity; full-range migration isolates PMD-mapped THPs as
  whole folios; canary-only migration skips THPs (canaries are 4KB).  The one
  deliberate split is canary installation over a huge PMD (Thermostat-style
  4KB poisoning).
- A synthetic phase-shift workload resembling the paper's reactiveness test.

## Run

```bash
python3 -m unittest pet_repro.test_pet_model
python3 pet_repro/run_synthetic_phase_shift.py > /tmp/pet_phase_shift.csv
```

## Limits

- This is not a timing or bandwidth simulator.
- PTE access-bit sampling is deterministic at MiB range granularity here.
- Canary faults are estimated from accessed MiB and canary ratio.
- The kernel prototype now contains VMA/P-block hooks, `kdemoted`,
  `kpromoted`, PROT_NONE canary faults, promotion thresholds, and file-page
  demotion via inode open counts.
- P-block metadata now uses per-P-block locking and refcounts so background
  page walks/migration do not run under the global PET list mutex; file
  open/close accounting uses a dedicated spinlock and the fault path skips
  PET entirely while no canaries exist.
- Run experiments with `kernel.numa_balancing=0`: PET reuses the PROT_NONE
  encoding of NUMA hint faults, so active balancing miscounts canary faults
  and pays the PET fault-path cost on every hint fault.  Enabling PET while
  balancing is on logs a warning.
- With THP on, 6.1.44 khugepaged does not skip PROT_NONE PTEs: collapse can
  absorb canaries and the next refresh re-splits that PMD.  Defer or disable
  khugepaged for THP experiments, or acknowledge the split/collapse churn.
- fork/exec: children inherit canary PROT_NONE PTEs but no P-blocks; such
  faults are restored by `do_numa_page()` uncounted.  PET tracks the parent
  mm only (the paper is silent on fork).
- Deliberate deviation: paper §5.2.2 excludes heap-like small-object areas;
  this prototype tracks brk-grown VMAs at or above `min_mmap_kb` so small brk
  extensions are not lost.
- Paper-level performance numbers still require the target tiered-memory host.

## Kernel Build

```bash
cd ../linux-6.1.44-pet
make ARCH=x86_64 O=../linux-6.1.44-pet-build defconfig
scripts/config --file ../linux-6.1.44-pet-build/.config -e PET_TIERING
make ARCH=x86_64 O=../linux-6.1.44-pet-build olddefconfig
make ARCH=x86_64 O=../linux-6.1.44-pet-build -j"$(nproc)"
```

## Reactiveness Microbenchmark

Small smoke test:

```bash
cc -O2 -pthread pet_repro/microbench/hotset_shift.c -o /tmp/hotset_shift
/tmp/hotset_shift --total-gb 1 --hot-gb 1 --threads 1 --phase-sec 1 --phases 2
```

Paper-style run on the tiered-memory host:

```bash
TOTAL_GB=120 HOT_GB=1 THREADS=8 PHASE_SEC=60 PHASES=30 \
  pet_repro/scripts/run_hotset_shift.sh
```

Generic benchmark matrix:

```bash
GRAPH500_CMD='...' LIBLINEAR_CMD='...' XSBENCH_CMD='...' \
  pet_repro/scripts/run_pet_matrix.sh fits
```
