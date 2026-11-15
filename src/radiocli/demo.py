"""Mode démonstration : spectre et titre simulés, sans réseau ni son.

Utilisé par ``radiocli --demo`` (et pour tester le rendu hors ligne).
Le signal imite une boucle reggae/dub : grosse caisse, skank, nappe de basse,
hi-hats et une ligne de basses fréquences, à 74 BPM.
"""

from __future__ import annotations

import math
import random
import time

from .frame import BAND_COUNT, WAVE_POINTS, SpectrumState

_TRACKS = (
    "Sai Sai — Revolt Dub",
    "Lee « Scratch » Perry — Dub Revolution",
    "The Abyssinians — Satta Massagana",
    "Max Romeo — Chase the Devil",
    "Augustus Pablo — King Tubby Meets Rockers Uptown",
    "Black Uhuru — Guess Who's Coming to Dinner",
    "Junior Murvin — Police & Thieves",
    "Groundation — Hebron",
    "Inna de Yard — Reggae Vibes",
    "L'Entourloop — Madiba",
)

_BPM = 74.0
_BEAT = _BPM / 60.0


class DemoSpectrum:
    """Générateur de :class:`SpectrumState` animé pour le mode démo."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._rng = random.Random(20261107)
        self._peaks = [0.0] * BAND_COUNT
        self._wave = [0.0] * WAVE_POINTS
        self._track_index = -1
        self._title = ""

    # ------------------------------------------------------------------ API
    @property
    def title(self) -> str:
        elapsed = time.monotonic() - self._t0
        index = int(elapsed // 16.0) % len(_TRACKS)
        if index != self._track_index:
            self._track_index = index
            self._title = _TRACKS[index]
        return self._title

    def snapshot(self) -> SpectrumState:
        t = time.monotonic() - self._t0
        beat_pos = (t * _BEAT) % 1.0
        off_pos = (t * _BEAT + 0.5) % 1.0
        kick = math.exp(-7.0 * beat_pos)
        snare = math.exp(-10.0 * ((beat_pos - 0.5) % 1.0))
        skank = math.exp(-11.0 * off_pos)
        hat = 0.55 + 0.45 * math.sin(2.0 * math.pi * (t * _BEAT * 2.0 + 0.25))
        bass_lfo = 0.5 + 0.5 * math.sin(2.0 * math.pi * 0.19 * t)

        bars: list[float] = []
        for i in range(BAND_COUNT):
            pos = i / (BAND_COUNT - 1)
            if pos < 0.16:  # sub / basse
                value = 0.42 + 0.40 * kick * bass_lfo + 0.16 * bass_lfo
            elif pos < 0.34:  # bas-médium : skank
                value = 0.18 + 0.46 * skank + 0.10 * kick
            elif pos < 0.58:  # médiums : nappe
                value = 0.22 + 0.20 * math.sin(2 * math.pi * (0.4 * t + pos * 3.0)) ** 2
                value += 0.10 * skank
            elif pos < 0.78:  # haut-médium : cuivres / melodica
                value = 0.12 + 0.18 * snare + 0.12 * skank
            else:  # aigus : hi-hats
                value = 0.06 + 0.30 * hat + 0.12 * self._rng.random()
            value *= 0.75 + 0.25 * math.sin(2 * math.pi * (0.07 * t + pos))
            bars.append(min(1.0, max(0.0, value)))

        # Capuchons de crête avec retombée.
        peaks = self._peaks
        for i, value in enumerate(bars):
            if value >= peaks[i]:
                peaks[i] = value
            else:
                peaks[i] = max(value, peaks[i] - 0.012)

        # Oscilloscope : basse + médiums + un peu de bruit.
        wave = self._wave
        for i in range(WAVE_POINTS):
            tau = t - (WAVE_POINTS - i) / 44100.0
            sample = 0.55 * math.sin(2 * math.pi * 55.0 * tau + 0.4 * math.sin(2 * math.pi * 0.2 * tau))
            sample += 0.25 * math.sin(2 * math.pi * 220.0 * tau)
            sample += 0.10 * math.sin(2 * math.pi * 660.0 * tau + 1.2 * kick)
            wave[i] = max(-1.0, min(1.0, sample * (0.55 + 0.45 * kick)))

        rms = min(1.0, 0.42 + 0.38 * kick + 0.12 * skank)
        bass = min(1.0, 0.35 + 0.55 * kick * bass_lfo)
        mid = min(1.0, 0.30 + 0.40 * skank + 0.12 * snare)
        treble = min(1.0, 0.18 + 0.45 * hat)
        return SpectrumState(
            bars=tuple(round(v, 3) for v in bars),
            peaks=tuple(round(v, 3) for v in peaks),
            wave=tuple(round(v, 3) for v in wave),
            rms=round(rms, 3),
            peak=round(min(1.0, 0.55 + 0.4 * kick), 3),
            peak_l=round(min(1.0, 0.6 + 0.35 * kick), 3),
            peak_r=round(min(1.0, 0.55 + 0.38 * snare), 3),
            bass=round(bass, 3),
            mid=round(mid, 3),
            treble=round(treble, 3),
            beat=round(kick, 3),
        )
