"""Tests du générateur de spectre du mode démo."""

from __future__ import annotations

import time
import unittest

from radiocli.demo import DemoSpectrum
from radiocli.frame import BAND_COUNT, WAVE_POINTS


class DemoSpectrumTests(unittest.TestCase):
    def test_snapshot_shapes(self) -> None:
        demo = DemoSpectrum()
        state = demo.snapshot()
        self.assertEqual(len(state.bars), BAND_COUNT)
        self.assertEqual(len(state.peaks), BAND_COUNT)
        self.assertEqual(len(state.wave), WAVE_POINTS)
        self.assertEqual(len(state.bars), BAND_COUNT)

    def test_values_are_normalized(self) -> None:
        demo = DemoSpectrum()
        for _ in range(5):
            state = demo.snapshot()
            for value in state.bars + state.peaks:
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)
            for value in state.wave:
                self.assertGreaterEqual(value, -1.0)
                self.assertLessEqual(value, 1.0)
            time.sleep(0.01)

    def test_bass_is_present(self) -> None:
        demo = DemoSpectrum()
        state = demo.snapshot()
        self.assertGreater(max(state.bars), 0.3)
        self.assertGreater(state.bass, 0.2)

    def test_title_is_available(self) -> None:
        demo = DemoSpectrum()
        self.assertTrue(demo.title)


if __name__ == "__main__":
    unittest.main()
