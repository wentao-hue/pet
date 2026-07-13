# PET Reproduction (EuroSys 2025)

Clean-room reproduction of:

> Doh et al., *PET: Proactive Demotion for Efficient Tiered Memory
> Management*, EuroSys 2025.

The paper does not provide public source code.  This repository contains a
kernel prototype (as a patch against vanilla Linux 6.1.44), a trace-driven
user-space model, reproduction documentation, and benchmark harnesses.

## Layout

| Path | Content |
| --- | --- |
| `pet-linux-6.1.44-prototype.patch` | Full kernel prototype as one patch against kernel.org Linux 6.1.44 |
| `pet_repro/README.md` | What is implemented, known limits, build/run instructions |
| `pet_repro/PET_DESIGN_COVERAGE.md` | Design-point coverage matrix vs. the paper |
| `pet_repro/PET_REPRODUCTION_PLAN.md` | Reproduction plan, verification status, residual risks |
| `pet_repro/pet_model.py`, `test_pet_model.py` | User-space model of the P-block policy + unit tests |
| `pet_repro/microbench/hotset_shift.c` | Hot-set shifting reactiveness microbenchmark |
| `pet_repro/scripts/` | Benchmark matrix harness |

## Applying the kernel patch

```bash
# base: vanilla Linux 6.1.44 from kernel.org
curl -LO https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.44.tar.xz
tar xf linux-6.1.44.tar.xz && cd linux-6.1.44
patch -p1 < ../pet-linux-6.1.44-prototype.patch

make ARCH=x86_64 O=build defconfig
scripts/config --file build/.config -e NUMA -e NUMA_BALANCING -e MIGRATION \
  -e TRANSPARENT_HUGEPAGE -e PET_TIERING
make ARCH=x86_64 O=build olddefconfig
make ARCH=x86_64 O=build -j"$(nproc)"
```

Runtime control: `/proc/pet/enabled`, `/proc/pet/stats`, and module
parameters under `/sys/module/pet/parameters/` (intervals, canary ratio,
block sizes, fast/slow node overrides).

`CONFIG_NUMA_BALANCING` is a hard build dependency (PET reuses the
PROT_NONE hint-fault encoding; without it canary faults would loop
forever).  Before experiments set `kernel.numa_balancing=0`, and see
`pet_repro/README.md` for THP/khugepaged caveats.

## Verification status

- Patch applies cleanly to vanilla 6.1.44 and reproduces the development
  tree byte-for-byte.
- All touched objects compile with 0 warnings under three configs
  (PET+THP, PET without THP, PET disabled/stub), Kconfig correctly gates
  PET off when `NUMA_BALANCING` is disabled, and a full `vmlinux` links
  with PET enabled (x86_64 cross build).
- `checkpatch.pl --strict`: 0 errors.
- User-space model tests pass (`python3 -m unittest pet_repro.test_pet_model`).
- End-to-end functional smoke test in QEMU with two NUMA nodes
  (`pet_repro/scripts/qemu_smoke/`): capture → two-phase cold detection →
  PHASE1/PHASE2 canary pre-demotion → full demotion to the slow node
  (with split/merge) → idle stability → fake-fault counting → threshold
  promotion back to the fast node → a second automatic demotion cycle
  after the workload goes idle again; zero migration failures, zero
  kernel warnings.
- Lifecycle/concurrency stress smoke (`init-stress` + `churn.c`): ~2400
  mmap/mremap/munmap/partial-unmap ops with periodic fork (children
  touch inherited canary PROT_NONE PTEs) and repeated runtime
  enable/disable toggles — captured == released (no P-block leaks) with
  demotion/canary/fake-fault/promotion active throughout; ext4 on
  virtio-blk exercises cold-file demotion of a closed 64 MiB file, and
  an immediate umount right after queueing a fresh inode drains cleanly
  (no "Busy inodes", validating the superblock-shutdown path at
  runtime).
- Bare-metal bring-up done: the monolithic PET kernel boots a two-socket
  AMD EPYC host (node 1 = emulated slow tier), and an 8 GiB anonymous
  workload reproduces the full bidirectional cycle end to end — demotion
  to the slow node when the block goes cold, then promotion back to the
  fast node (driven by fake faults on canary PTEs) when it is re-accessed,
  both matching the workload size byte-for-byte with zero migration
  failures. See `pet_repro/INSTALL-baremetal.md`.
- Paper-level performance numbers (fast-memory savings / slowdown vs. the
  paper's Optane tier) still require a full benchmark matrix on a genuine
  slow-tier host; nothing here fabricates results.

## License

The kernel patch is a derivative work of the Linux kernel and is licensed
GPL-2.0.  The user-space model and scripts in `pet_repro/` follow the same
license unless noted otherwise.
