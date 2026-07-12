# PET Design Coverage Matrix

This matrix tracks the clean-room Linux 6.1.44 reproduction against the PET
paper's design points.

## Kernel Mechanism Coverage

| Paper design point | Required implementation artifact | Status |
| --- | --- | --- |
| Capture anonymous mmap/brk/mremap VMAs as P-blocks | `pet_mmap_capture()` hooked in `mm/mmap.c` and `mm/mremap.c`; full-VMA gap filling handles small brk/mremap expansions | done |
| Ignore non-anonymous, stack, special, shared, tiny mappings | VMA eligibility filter, default 128KB minimum | done |
| Split oversized P-blocks | maximum P-block size parameter, default 1024MB | done |
| Track one sampled page per hot P-block per sampling interval | `kdemoted` two-phase sampling: interval N arms a random page by clearing its accessed bit, interval N+1 checks the same page (paper §4.2/Fig. 6); scan decisions require at least one completed check (`samples_seen`), so mmap_lock contention cannot fake a cold signal | done |
| Track temporary blocks for PHASE1 | temporary-block bitmap, default 128MB | done |
| Use four states: NORMAL, PHASE1, PHASE2, DEMOTED | P-block state machine | done |
| Use PROT_NONE canary pages in PHASE2 | canary page selection, demotion, PTE protection | done |
| Count fake page faults on canary pages | page-fault hook before NUMA hint faults | done |
| Use canary pages for DEMOTED P-block hotness | repeated canary poisoning in demoted P-blocks | done |
| Promote via kpromoted, not in fault handler | promotion request queue and kernel thread | done |
| Use `Rpromo`, `th_total`, and per-P-block `th_block` | threshold computation from sampling interval and demoted bytes | done |
| Reset promotion windows cleanly | atomic fault-window generation avoids stale per-P-block counts and data races | done |
| Merge adjacent temporary blocks with same hot/cold property | PHASE2 split/merge | done |
| Promote canary pages in hot split blocks | selective promotion after PHASE2 split | done |
| Demote cold file pages by inode `open_count` | inode counter, cold-file processing generation, reopen-safe migration, runtime disable drains queued inode refs; `generic_shutdown_super()` drains per-superblock queue/processing entries so umount never sees PET-held inode references | done |
| Avoid long global PET lock holds | P-block refcount, per-P-block mutex, lockless page walk/migration outside `pet_lock`; dedicated `pet_cold_lock` spinlock keeps open()/close() off `pet_lock`; fault path bails without `pet_lock` while no canaries exist (`pet_have_poisoned`) | done |
| Keep VMA/P-block lifecycle synchronized | successful-unmap release, partial trim/split, brk/mremap gap-fill capture, mremap state inheritance including canaries and `MREMAP_DONTUNMAP` | done |
| Notify secondary MMUs for canary protection changes | mmu-notifier ranges around PROT_NONE install/restore | done |
| Compare with MGLRU/DAMON design differences | reproduction notes and experiment scripts | pending |
| THP-enabled behavior | THP-aware sampling/migration or documented parity gap | partial: sampling reads/clears the accessed bit at PMD granularity without splitting; full-range migration isolates PMD-mapped THPs as whole folios without splitting; canary-only migration skips THPs (canaries are 4KB). The one deliberate split: installing canaries over a huge PMD splits that PMD once (Thermostat-style 4KB poisoning). Known residual: 6.1.44 khugepaged does not skip PROT_NONE PTEs, so with khugepaged active, collapse can absorb canaries and the next refresh re-splits — run THP experiments with khugepaged deferred/off or acknowledge the ping-pong |
| Prerequisite: NUMA balancing off | PET reuses the PROT_NONE encoding of NUMA hint faults; with `kernel.numa_balancing=1`, balancing-poisoned PTEs at canary addresses are miscounted as fake faults and every hint fault pays the PET fault-path cost. Enabling PET while balancing is on logs a warning | documented |
| Limitation: fork/exec | child processes inherit canary PROT_NONE PTEs but no P-blocks; such faults fall through to `do_numa_page()` which restores protections uncounted. PET tracks the parent mm only (paper is silent on fork) | documented |
| Deliberate deviation: brk heap tracking | paper §5.2.2 deliberately excludes small-object areas (heap); this reproduction tracks brk-grown VMAs ≥ `min_mmap_kb` so small brk extensions are not lost. Compare against paper numbers with this in mind | documented |
| Discussion: dynamic canary ratio | not part of evaluated PET prototype; optional future work | not required |
| Discussion: thrashing suspension | not part of evaluated PET prototype; optional future work | not required |

## Experiment Coverage

| Paper experiment | Required artifact | Status |
| --- | --- | --- |
| Larger-than-fast-memory workloads | benchmark runner template, metrics collection | pending |
| Fits-in-fast-memory RSS saving | benchmark runner template, metrics collection | pending |
| Phase-change workload with `mlock` limited fast memory | synthetic phase runner | partial: user-space model exists |
| Hot-set shifting microbenchmark | native benchmark source and runner | done |
| Java adversarial workload note | DaCapo H2 run recipe | pending |
| THP comparison note | THP run recipe | pending |
| Sensitivity: canary ratio | parameterized runner | done via module parameters and matrix harness |
| Sensitivity: sampling/scan intervals | parameterized runner | done via module parameters and matrix harness |
| Sensitivity: temp/max P-block size | parameterized runner | done via module parameters and matrix harness |

No result values are fabricated here. Every result cell must be filled from
experiments run on the target tiered-memory machine or from verified matching
baseline runs.
