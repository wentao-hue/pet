"""Run a PET-style synthetic phase-shift experiment.

This mirrors the paper's reactiveness microbenchmark at the policy-model level:
120 GiB total allocation, eight 1 GiB hot sets, hot sets relocate every minute.
The script writes CSV to stdout.
"""

from __future__ import annotations

import argparse
import csv
import sys

from pet_model import PETConfig, PETSimulator


def hot_ranges(minute: int, hot_sets: int, set_mib: int, total_sets: int):
    base = (minute % (total_sets // hot_sets)) * hot_sets
    for offset in range(hot_sets):
        idx = (base + offset) % total_sets
        start = idx * set_mib
        yield (start, start + set_mib)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--total-gib", type=int, default=120)
    parser.add_argument("--hot-sets", type=int, default=8)
    parser.add_argument("--hot-set-mib", type=int, default=1024)
    args = parser.parse_args()

    cfg = PETConfig()
    sim = PETSimulator(cfg)
    sim.allocate(args.total_gib * 1024)

    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            "time_s",
            "minute",
            "fast_memory_mib",
            "demoted_memory_mib",
            "sampled_pages",
            "canary_faults",
            "promoted_mib",
            "demoted_mib",
            "normal_blocks",
            "phase1_blocks",
            "phase2_blocks",
            "demoted_blocks",
        ],
    )
    writer.writeheader()

    total_steps = int(args.minutes * 60 / cfg.sampling_interval_s)
    total_sets = args.total_gib * 1024 // args.hot_set_mib
    agg_sampled_pages = 0
    agg_canary_faults = 0
    agg_promoted_mib = 0.0
    agg_demoted_mib = 0.0
    for _ in range(total_steps):
        minute = int(sim.time_s // 60)
        stats = sim.step(hot_ranges(minute, args.hot_sets, args.hot_set_mib, total_sets))
        agg_sampled_pages += stats.sampled_pages
        agg_canary_faults += stats.canary_faults
        agg_promoted_mib += stats.promoted_mib
        agg_demoted_mib += stats.demoted_mib
        if int(stats.time_s * 10) % int(cfg.scan_interval_s * 10) == 0:
            counts = sim.state_counts()
            writer.writerow(
                {
                    "time_s": f"{stats.time_s:.1f}",
                    "minute": minute,
                    "fast_memory_mib": f"{stats.fast_memory_mib:.1f}",
                    "demoted_memory_mib": f"{sim.demoted_memory_mib():.1f}",
                    "sampled_pages": agg_sampled_pages,
                    "canary_faults": agg_canary_faults,
                    "promoted_mib": f"{agg_promoted_mib:.1f}",
                    "demoted_mib": f"{agg_demoted_mib:.1f}",
                    "normal_blocks": counts["NORMAL"],
                    "phase1_blocks": counts["PHASE1"],
                    "phase2_blocks": counts["PHASE2"],
                    "demoted_blocks": counts["DEMOTED"],
                }
            )
            agg_sampled_pages = 0
            agg_canary_faults = 0
            agg_promoted_mib = 0.0
            agg_demoted_mib = 0.0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
