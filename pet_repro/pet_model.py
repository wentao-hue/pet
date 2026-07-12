"""Trace-driven model of PET's P-block demotion and promotion policy.

The model uses MiB-granularity address ranges.  It is intentionally not a
performance simulator: it reproduces PET's control-state transitions so the
paper policy can be tested before kernel implementation work starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import ceil
from typing import Iterable, List, Sequence, Tuple

MiBRange = Tuple[float, float]  # [start_mib, end_mib)
PAGES_PER_MIB = 256


class PBlockState(str, Enum):
    NORMAL = "NORMAL"
    PHASE1 = "PHASE1"
    PHASE2 = "PHASE2"
    DEMOTED = "DEMOTED"


@dataclass(frozen=True)
class PETConfig:
    """Default parameters reported in the PET paper."""

    sampling_interval_s: float = 0.5
    scan_interval_s: float = 10.0
    max_pblock_mib: float = 1024.0
    temporary_block_mib: float = 128.0
    canary_ratio: float = 0.10
    tolerable_slowdown: float = 0.03
    misdemotion_penalty_us: float = 8.3

    @property
    def samples_per_scan(self) -> int:
        return max(1, round(self.scan_interval_s / self.sampling_interval_s))

    @property
    def rpromo_faults_per_s(self) -> float:
        penalty_s = self.misdemotion_penalty_us * 1e-6
        return self.tolerable_slowdown * self.canary_ratio / penalty_s

    @property
    def promotion_fault_budget_per_sample(self) -> int:
        return max(1, ceil(self.rpromo_faults_per_s * self.sampling_interval_s))


@dataclass
class StepStats:
    time_s: float
    sampled_pages: int
    canary_faults: int
    promoted_mib: float
    demoted_mib: float
    fast_memory_mib: float


@dataclass
class PBlock:
    start_mib: float
    size_mib: float
    state: PBlockState = PBlockState.NORMAL
    sampled_accessed: bool = False
    temp_accessed: List[bool] | None = None

    @property
    def end_mib(self) -> float:
        return self.start_mib + self.size_mib

    def intersects(self, ranges: Sequence[MiBRange]) -> bool:
        return any(start < self.end_mib and end > self.start_mib for start, end in ranges)

    def accessed_mib(self, ranges: Sequence[MiBRange]) -> float:
        total = 0.0
        for start, end in ranges:
            total += max(0.0, min(end, self.end_mib) - max(start, self.start_mib))
        return min(total, self.size_mib)

    def temp_slices(self, cfg: PETConfig) -> List[MiBRange]:
        slices: List[MiBRange] = []
        cur = self.start_mib
        while cur < self.end_mib:
            nxt = min(cur + cfg.temporary_block_mib, self.end_mib)
            slices.append((cur, nxt))
            cur = nxt
        return slices

    def ensure_temp_tracking(self, cfg: PETConfig) -> None:
        if self.temp_accessed is None:
            self.temp_accessed = [False] * len(self.temp_slices(cfg))

    def reset_tracking(self, cfg: PETConfig) -> None:
        self.sampled_accessed = False
        if self.state in (PBlockState.PHASE1, PBlockState.PHASE2):
            self.ensure_temp_tracking(cfg)
            self.temp_accessed = [False] * len(self.temp_accessed or [])
        else:
            self.temp_accessed = None

    def record_tracking_sample(self, ranges: Sequence[MiBRange], cfg: PETConfig) -> int:
        """Return the number of PTE access-bit samples modeled for this block."""

        if self.state == PBlockState.NORMAL:
            self.sampled_accessed = self.sampled_accessed or self.intersects(ranges)
            return 1

        if self.state in (PBlockState.PHASE1, PBlockState.PHASE2):
            self.ensure_temp_tracking(cfg)
            sampled = 0
            for idx, (start, end) in enumerate(self.temp_slices(cfg)):
                sampled += 1
                if any(r_start < end and r_end > start for r_start, r_end in ranges):
                    self.temp_accessed[idx] = True
                    self.sampled_accessed = True
            return sampled

        return 0


@dataclass
class PETSimulator:
    cfg: PETConfig = field(default_factory=PETConfig)
    blocks: List[PBlock] = field(default_factory=list)
    time_s: float = 0.0
    _samples_in_scan: int = 0
    _next_alloc_mib: float = 0.0

    def allocate(self, size_mib: float, start_mib: float | None = None) -> List[PBlock]:
        """Allocate anonymous mmap-like memory and split it into initial P-blocks."""

        if size_mib <= 0:
            raise ValueError("size_mib must be positive")
        cur = self._next_alloc_mib if start_mib is None else start_mib
        remaining = size_mib
        made: List[PBlock] = []
        while remaining > 0:
            block_size = min(self.cfg.max_pblock_mib, remaining)
            block = PBlock(cur, block_size)
            self.blocks.append(block)
            made.append(block)
            cur += block_size
            remaining -= block_size
        self.blocks.sort(key=lambda b: b.start_mib)
        self._next_alloc_mib = max(self._next_alloc_mib, cur)
        return made

    def step(self, access_ranges: Iterable[MiBRange]) -> StepStats:
        """Advance one PET sampling interval."""

        ranges = _normalize_ranges(access_ranges)
        sampled_pages = 0
        faulted_blocks: List[Tuple[PBlock, int]] = []
        canary_faults = 0
        promoted_mib = 0.0
        demoted_mib = 0.0

        for block in self.blocks:
            if block.state == PBlockState.DEMOTED:
                accessed_mib = block.accessed_mib(ranges)
                if accessed_mib > 0:
                    faults = ceil(accessed_mib * PAGES_PER_MIB * self.cfg.canary_ratio)
                    canary_faults += faults
                    faulted_blocks.append((block, faults))
            else:
                sampled_pages += block.record_tracking_sample(ranges, self.cfg)

        for block in self._promotion_targets(faulted_blocks, canary_faults):
            promoted_mib += block.size_mib
            block.state = PBlockState.NORMAL
            block.reset_tracking(self.cfg)

        self._samples_in_scan += 1
        if self._samples_in_scan >= self.cfg.samples_per_scan:
            demoted_mib += self._finish_scan()
            self._samples_in_scan = 0

        self.time_s += self.cfg.sampling_interval_s
        return StepStats(
            time_s=self.time_s,
            sampled_pages=sampled_pages,
            canary_faults=canary_faults,
            promoted_mib=promoted_mib,
            demoted_mib=demoted_mib,
            fast_memory_mib=self.fast_memory_mib(),
        )

    def fast_memory_mib(self) -> float:
        """Approximate fast-tier footprint.

        Fully demoted P-blocks are counted as slow memory.  PHASE2 has canary
        pages already placed in slow memory, so we subtract that small fraction.
        """

        total = 0.0
        for block in self.blocks:
            if block.state == PBlockState.DEMOTED:
                continue
            if block.state == PBlockState.PHASE2:
                total += block.size_mib * (1.0 - self.cfg.canary_ratio)
            else:
                total += block.size_mib
        return total

    def demoted_memory_mib(self) -> float:
        return sum(block.size_mib for block in self.blocks if block.state == PBlockState.DEMOTED)

    def state_counts(self) -> dict[str, int]:
        counts = {state.value: 0 for state in PBlockState}
        for block in self.blocks:
            counts[block.state.value] += 1
        return counts

    def _promotion_targets(
        self, faulted_blocks: Sequence[Tuple[PBlock, int]], total_faults: int
    ) -> List[PBlock]:
        if not faulted_blocks:
            return []

        th_total = self.cfg.promotion_fault_budget_per_sample
        if total_faults >= th_total:
            return [block for block, _ in faulted_blocks]

        total_demoted = max(self.demoted_memory_mib(), 1.0)
        targets: List[PBlock] = []
        for block, faults in faulted_blocks:
            th_block = max(1, ceil(th_total * block.size_mib / total_demoted))
            if faults >= th_block:
                targets.append(block)
        return targets

    def _finish_scan(self) -> float:
        new_blocks: List[PBlock] = []
        demoted_mib = 0.0

        for block in self.blocks:
            if block.state == PBlockState.NORMAL:
                if not block.sampled_accessed:
                    block.state = PBlockState.PHASE1
                    block.ensure_temp_tracking(self.cfg)
                block.reset_tracking(self.cfg)
                new_blocks.append(block)
                continue

            if block.state == PBlockState.PHASE1:
                cold_mib, accessed_mib = self._temp_cold_accessed_mib(block)
                if cold_mib > accessed_mib:
                    block.state = PBlockState.PHASE2
                    block.ensure_temp_tracking(self.cfg)
                else:
                    block.state = PBlockState.NORMAL
                block.reset_tracking(self.cfg)
                new_blocks.append(block)
                continue

            if block.state == PBlockState.PHASE2:
                cold_mib, accessed_mib = self._temp_cold_accessed_mib(block)
                if cold_mib > accessed_mib:
                    split_blocks = self._split_phase2_block(block)
                    demoted_mib += sum(
                        b.size_mib for b in split_blocks if b.state == PBlockState.DEMOTED
                    )
                    new_blocks.extend(split_blocks)
                else:
                    block.state = PBlockState.NORMAL
                    block.reset_tracking(self.cfg)
                    new_blocks.append(block)
                continue

            new_blocks.append(block)

        # PET may merge adjacent temporary blocks after splitting one PHASE2
        # P-block, but independent P-blocks must remain independent.  Keeping
        # that boundary matters for promotion thresholds and migration volume.
        self.blocks = sorted(new_blocks, key=lambda b: b.start_mib)
        return demoted_mib

    def _temp_cold_accessed_mib(self, block: PBlock) -> Tuple[float, float]:
        block.ensure_temp_tracking(self.cfg)
        cold = 0.0
        accessed = 0.0
        for is_accessed, (start, end) in zip(block.temp_accessed or [], block.temp_slices(self.cfg)):
            if is_accessed:
                accessed += end - start
            else:
                cold += end - start
        return cold, accessed

    def _split_phase2_block(self, block: PBlock) -> List[PBlock]:
        block.ensure_temp_tracking(self.cfg)
        slices = block.temp_slices(self.cfg)
        if not slices:
            return []

        out: List[PBlock] = []
        run_start = slices[0][0]
        run_end = slices[0][1]
        run_accessed = bool((block.temp_accessed or [False])[0])

        for is_accessed, (start, end) in zip((block.temp_accessed or [False])[1:], slices[1:]):
            is_accessed = bool(is_accessed)
            if is_accessed == run_accessed and abs(start - run_end) < 1e-9:
                run_end = end
                continue
            out.append(_make_split_block(run_start, run_end, run_accessed))
            run_start, run_end, run_accessed = start, end, is_accessed
        out.append(_make_split_block(run_start, run_end, run_accessed))

        for item in out:
            item.reset_tracking(self.cfg)
        return out


def _make_split_block(start: float, end: float, accessed: bool) -> PBlock:
    return PBlock(
        start_mib=start,
        size_mib=end - start,
        state=PBlockState.NORMAL if accessed else PBlockState.DEMOTED,
    )


def _normalize_ranges(ranges: Iterable[MiBRange]) -> List[MiBRange]:
    normalized: List[MiBRange] = []
    for start, end in ranges:
        if end <= start:
            continue
        normalized.append((float(start), float(end)))
    return normalized


