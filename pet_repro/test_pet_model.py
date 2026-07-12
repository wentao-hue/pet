import unittest

from .pet_model import PETConfig, PETSimulator, PBlockState


def run_scan(sim: PETSimulator, ranges=()):
    stats = None
    for _ in range(sim.cfg.samples_per_scan):
        stats = sim.step(ranges)
    return stats


class PETModelTests(unittest.TestCase):
    def test_allocation_splits_at_max_pblock_size(self):
        sim = PETSimulator(PETConfig(max_pblock_mib=1024))
        sim.allocate(2300)
        self.assertEqual([b.size_mib for b in sim.blocks], [1024, 1024, 252])

    def test_hot_normal_block_stays_normal(self):
        sim = PETSimulator()
        sim.allocate(1024)
        for _ in range(3):
            run_scan(sim, [(0, 1024)])
        self.assertEqual(sim.blocks[0].state, PBlockState.NORMAL)

    def test_cold_block_reaches_demoted_after_three_scans(self):
        sim = PETSimulator()
        sim.allocate(1024)
        run_scan(sim)
        self.assertEqual(sim.blocks[0].state, PBlockState.PHASE1)
        run_scan(sim)
        self.assertEqual(sim.blocks[0].state, PBlockState.PHASE2)
        run_scan(sim)
        self.assertEqual(sim.blocks[0].state, PBlockState.DEMOTED)
        self.assertEqual(sim.demoted_memory_mib(), 1024)

    def test_phase2_split_keeps_hot_temporary_block_in_fast_memory(self):
        sim = PETSimulator(PETConfig(temporary_block_mib=128))
        sim.allocate(512)
        run_scan(sim)
        run_scan(sim, [(0, 128)])
        run_scan(sim, [(0, 128)])
        self.assertEqual([(b.size_mib, b.state) for b in sim.blocks], [
            (128, PBlockState.NORMAL),
            (384, PBlockState.DEMOTED),
        ])

    def test_demoted_block_promotes_on_canary_fault_budget(self):
        sim = PETSimulator()
        sim.allocate(1024)
        for _ in range(3):
            run_scan(sim)
        self.assertEqual(sim.blocks[0].state, PBlockState.DEMOTED)
        stats = sim.step([(0, 1024)])
        self.assertGreater(stats.canary_faults, sim.cfg.promotion_fault_budget_per_sample)
        self.assertEqual(sim.blocks[0].state, PBlockState.NORMAL)

    def test_fast_memory_counts_demoted_blocks_as_slow(self):
        sim = PETSimulator()
        sim.allocate(1024)
        for _ in range(3):
            run_scan(sim)
        self.assertEqual(sim.fast_memory_mib(), 0)
        sim.step([(0, 1024)])
        self.assertEqual(sim.fast_memory_mib(), 1024)


if __name__ == "__main__":
    unittest.main()

