"""Tests de l'analyseur spectral (FFT, bandes, VU, silence)."""

from __future__ import annotations

import math
import struct
import unittest

from radiocli.analysis import (
    SpectrumAnalyzer,
    _log_band_bins,
    hann_window,
)
from radiocli.frame import BAND_COUNT, WAVE_POINTS


def tone_pcm(freq: float, frames: int = 1024, amp: int = 16000, rate: int = 44100) -> bytes:
    out = bytearray()
    for i in range(frames):
        sample = int(amp * math.sin(2.0 * math.pi * freq * i / rate))
        out += struct.pack("<hh", sample, sample)
    return bytes(out)


def stereo_pcm(left: int, right: int, frames: int = 1024) -> bytes:
    out = bytearray()
    for _ in range(frames):
        out += struct.pack("<hh", left, right)
    return bytes(out)


class HannWindowTests(unittest.TestCase):
    def test_endpoints_and_unity(self) -> None:
        window = hann_window(8)
        self.assertEqual(len(window), 8)
        self.assertAlmostEqual(window[0], 0.0, places=9)
        self.assertLess(window[0], window[4])
        self.assertAlmostEqual(window[4], 1.0, places=9)

    def test_all_values_in_range(self) -> None:
        for value in hann_window(1024):
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)


class BandMappingTests(unittest.TestCase):
    def test_ranges_are_strict_and_bounded(self) -> None:
        ranges = _log_band_bins(BAND_COUNT, 1024, 44100)
        self.assertEqual(len(ranges), BAND_COUNT)
        previous_hi = 0
        for lo, hi in ranges:
            self.assertGreaterEqual(lo, 0)
            self.assertLess(lo, hi)
            self.assertLessEqual(hi, 513)
            self.assertGreaterEqual(lo, previous_hi - 1)
            previous_hi = hi

    def test_single_band_has_at_least_one_bin(self) -> None:
        ranges = _log_band_bins(1, 512, 44100)
        self.assertEqual(len(ranges), 1)
        lo, hi = ranges[0]
        self.assertLess(lo, hi)


class AnalyzerTests(unittest.TestCase):
    def test_snapshot_shapes_and_ranges(self) -> None:
        analyzer = SpectrumAnalyzer()
        analyzer.feed(tone_pcm(440.0))
        state = analyzer.snapshot()
        self.assertEqual(len(state.bars), BAND_COUNT)
        self.assertEqual(len(state.peaks), BAND_COUNT)
        self.assertEqual(len(state.wave), WAVE_POINTS)
        for value in state.bars + state.peaks + state.wave:
            self.assertGreaterEqual(value, -1.0)
            self.assertLessEqual(value, 1.0)

    def test_bass_tone_lights_low_bands(self) -> None:
        analyzer = SpectrumAnalyzer()
        for _ in range(25):
            analyzer.feed(tone_pcm(55.0))
        state = analyzer.snapshot()
        low = max(state.bars[:8])
        high = max(state.bars[40:])
        self.assertGreater(low, high)
        self.assertGreater(state.bass, state.treble)

    def test_treble_tone_lights_high_bands(self) -> None:
        analyzer = SpectrumAnalyzer()
        for _ in range(25):
            analyzer.feed(tone_pcm(9000.0))
        state = analyzer.snapshot()
        high = max(state.bars[45:])
        low = max(state.bars[:12])
        self.assertGreater(high, low)
        self.assertGreater(state.treble, state.bass)

    def test_silence_is_silent(self) -> None:
        analyzer = SpectrumAnalyzer()
        analyzer.feed(b"\x00\x00\x00\x00" * 1024)
        state = analyzer.snapshot()
        self.assertAlmostEqual(state.rms, 0.0)
        self.assertAlmostEqual(max(state.bars), 0.0)
        self.assertAlmostEqual(state.peak, 0.0)

    def test_stereo_peaks_are_independent(self) -> None:
        analyzer = SpectrumAnalyzer()
        analyzer.feed(stereo_pcm(32767, 0))
        state = analyzer.snapshot()
        self.assertGreater(state.peak_l, state.peak_r)
        self.assertGreater(state.peak_l, 0.5)

    def test_peak_hold_above_bars(self) -> None:
        analyzer = SpectrumAnalyzer()
        for _ in range(20):
            analyzer.feed(tone_pcm(220.0))
        state = analyzer.snapshot()
        for bar, peak in zip(state.bars, state.peaks):
            self.assertGreaterEqual(peak + 1e-6, bar)

    def test_silence_decays_previous_bars(self) -> None:
        analyzer = SpectrumAnalyzer()
        for _ in range(20):
            analyzer.feed(tone_pcm(440.0))
        loud = max(analyzer.snapshot().bars)
        for _ in range(120):
            analyzer.feed(b"\x00\x00\x00\x00" * 1024)
        quiet = max(analyzer.snapshot().bars)
        self.assertLess(quiet, loud * 0.5)

    def test_partial_block_does_not_crash(self) -> None:
        analyzer = SpectrumAnalyzer()
        analyzer.feed(tone_pcm(440.0)[:1234])
        analyzer.feed(b"")
        self.assertTrue(analyzer.has_data)

    def test_reset_clears_state(self) -> None:
        analyzer = SpectrumAnalyzer()
        for _ in range(10):
            analyzer.feed(tone_pcm(100.0))
        analyzer.reset()
        state = analyzer.snapshot()
        self.assertAlmostEqual(max(state.bars), 0.0)
        self.assertFalse(analyzer.has_data)

    def test_beat_is_bounded(self) -> None:
        analyzer = SpectrumAnalyzer()
        for _ in range(40):
            analyzer.feed(tone_pcm(60.0))
        state = analyzer.snapshot()
        self.assertGreaterEqual(state.beat, 0.0)
        self.assertLessEqual(state.beat, 1.0)


if __name__ == "__main__":
    unittest.main()
