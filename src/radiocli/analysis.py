"""Analyse audio temps réel sans dépendance externe (FFT pure Python).

Le flux MP3 est décodé par ffmpeg en PCM stéréo 16 bits ; ce module reçoit des
blocs PCM, en extrait un spectre log, un oscilloscope, des VU-mètres et une
détection de beat. Tout est normalisé dans ``[0, 1]`` pour le rendu.

Aucune bibliothèque tierce : la FFT radix-2 est implémentée ici en Python pur.
Un bloc de 1024 échantillons coûte ~4 ms, soit une petite fraction d'un cœur
à la cadence de 43 blocs/seconde.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from array import array

from .frame import BAND_COUNT, WAVE_POINTS, SpectrumState

#: Nombre de bandes de fréquences calculées (doit rester >= BAND_COUNT).
_FFT_SIZE = 1024
_SAMPLE_RATE = 44100
_DYNAMIC_RANGE_DB = 54.0
_SILENCE_DB = -52.0


def hann_window(n: int) -> list[float]:
    """Fenêtre de Hann périodique, normalisée pour une amplitude crête à 1."""
    if n <= 1:
        return [1.0] * max(n, 1)
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / n) for i in range(n)]


class _Radix2FFT:
    """FFT complexe itérative, tables précalculées pour une taille fixe."""

    __slots__ = ("n", "rev", "tw_re", "tw_im")

    def __init__(self, n: int) -> None:
        if n & (n - 1):
            raise ValueError("la taille de FFT doit être une puissance de 2")
        self.n = n
        levels = n.bit_length() - 1
        rev = [0] * n
        for i in range(1, n):
            rev[i] = rev[i >> 1] >> 1 | ((i & 1) << (levels - 1))
        self.rev = rev
        self.tw_re = [math.cos(-2.0 * math.pi * k / n) for k in range(n // 2)]
        self.tw_im = [math.sin(-2.0 * math.pi * k / n) for k in range(n // 2)]

    def magnitude(self, re: list[float], im: list[float], out: list[float]) -> None:
        """Transforme ``re``/``im`` en place et écrit les magnitudes dans ``out``.

        Seul le spectre positif (``n // 2 + 1`` raies) est écrit.
        """
        n = self.n
        rev = self.rev
        for i in range(n):
            j = rev[i]
            if i < j:
                re[i], re[j] = re[j], re[i]
                im[i], im[j] = im[j], im[i]

        tw_re = self.tw_re
        tw_im = self.tw_im
        length = 2
        while length <= n:
            half = length >> 1
            step = n // length
            for start in range(0, n, length):
                k = 0
                end = start + half
                for i in range(start, end):
                    j = i + half
                    wr = tw_re[k]
                    wi = tw_im[k]
                    xr = re[j]
                    xi = im[j]
                    vr = xr * wr - xi * wi
                    vi = xr * wi + xi * wr
                    ur = re[i]
                    ui = im[i]
                    re[i] = ur + vr
                    im[i] = ui + vi
                    re[j] = ur - vr
                    im[j] = ui - vi
                    k += step
            length <<= 1

        limit = n // 2 + 1
        for k in range(limit):
            out[k] = math.hypot(re[k], im[k])


def _db_from_amplitude(amp: float) -> float:
    return 20.0 * math.log10(amp + 1e-12)


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _smooth(current: float, target: float, attack: float, release: float) -> float:
    coeff = attack if target > current else release
    return current + (target - current) * coeff


def _log_band_bins(
    bands: int, fft_size: int, sample_rate: int, f_min: float = 32.0, f_max: float = 16500.0
) -> list[tuple[int, int]]:
    """Découpe le spectre en bandes log, en indices de bins (fin exclusive)."""
    bin_hz = sample_rate / fft_size
    nyquist_bin = fft_size // 2
    edges: list[int] = []
    ratio = (f_max / f_min) ** (1.0 / bands)
    f = f_min
    for _ in range(bands + 1):
        edges.append(min(nyquist_bin, max(1, int(round(f / bin_hz)))))
        f *= ratio
    # Garantit une progression stricte, même dans les graves.
    for i in range(1, len(edges)):
        if edges[i] <= edges[i - 1]:
            edges[i] = edges[i - 1] + 1
    ranges = []
    for i in range(bands):
        lo = min(edges[i], nyquist_bin - 1)
        hi = max(lo + 1, min(edges[i + 1], nyquist_bin))
        ranges.append((lo, hi))
    return ranges


class SpectrumAnalyzer:
    """Transforme un flux PCM stéréo en état de visualisation lissé.

    Utilisable depuis un thread de capture : :meth:`feed` écrit l'état,
    :meth:`snapshot` en renvoie une copie cohérente.
    """

    def __init__(
        self,
        sample_rate: int = _SAMPLE_RATE,
        fft_size: int = _FFT_SIZE,
        bands: int = BAND_COUNT,
    ) -> None:
        if bands > BAND_COUNT:
            raise ValueError(f"bands ({bands}) > BAND_COUNT ({BAND_COUNT})")
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.bands = bands
        self._fft = _Radix2FFT(fft_size)
        self._window = hann_window(fft_size)
        self._window_sum = math.fsum(self._window) or 1.0
        self._ranges = _log_band_bins(bands, fft_size, sample_rate)
        self._lock = threading.Lock()

        # État lissé exposé au rendu.
        self._bars = [0.0] * bands
        self._peaks = [0.0] * bands
        self._peak_age = [0.0] * bands
        self._wave = [0.0] * WAVE_POINTS
        self._rms = 0.0
        self._peak = 0.0
        self._peak_l = 0.0
        self._peak_r = 0.0
        self._bass = 0.0
        self._mid = 0.0
        self._treble = 0.0
        self._beat = 0.0

        # État interne de normalisation / détection.
        self._top_db = -30.0
        self._rms_db = -120.0
        self._bass_fast = 0.0
        self._bass_slow = 0.0
        self._last_beat = 0.0
        self._last_feed = 0.0
        self._silent_since = 0.0
        self._has_data = False
        self._db_buf: list[float] = [0.0] * bands

    # ------------------------------------------------------------------ feed
    def feed(self, pcm: bytes) -> None:
        """Analyse un bloc PCM stéréo 16 bits little-endian (1 ou plusieurs trames)."""
        if not pcm:
            return
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) - (len(pcm) % 4)])
        if sys.byteorder != "little":  # pragma: no cover - plateformes exotic
            samples.byteswap()
        n_frames = len(samples) // 2
        if n_frames == 0:
            return

        mono = [0.0] * n_frames
        peak_l = 0
        peak_r = 0
        for i in range(n_frames):
            left = samples[2 * i]
            right = samples[2 * i + 1]
            if left < 0:
                if -left > peak_l:
                    peak_l = -left
            elif left > peak_l:
                peak_l = left
            if right < 0:
                if -right > peak_r:
                    peak_r = -right
            elif right > peak_r:
                peak_r = right
            mono[i] = (left + right) * (0.5 / 32768.0)

        self._analyse(mono, peak_l / 32768.0, peak_r / 32768.0)

    # --------------------------------------------------------------- analyse
    def _analyse(self, mono: list[float], peak_l: float, peak_r: float) -> None:
        n = self.fft_size
        if len(mono) < n:
            # Complète par des zéros en tête pour garder une fenêtre pleine.
            mono = [0.0] * (n - len(mono)) + mono
        else:
            mono = mono[-n:]

        window = self._window
        re = [mono[i] * window[i] for i in range(n)]
        im = [0.0] * n
        mags = [0.0] * (n // 2 + 1)
        self._fft.magnitude(re, im, mags)

        # Spectre log en dB, avec une légère compensation des aigus.
        band_db = self._db_buf
        scale = 2.0 / self._window_sum
        top = -120.0
        for b, (lo, hi) in enumerate(self._ranges):
            peak = 0.0
            for k in range(lo, hi):
                value = mags[k]
                if value > peak:
                    peak = value
            db = _db_from_amplitude(peak * scale)
            # +4.5 dB à 8 kHz : les aigus sont physiquement plus faibles.
            freq = 0.5 * (lo + hi) * self.sample_rate / n
            db += 4.5 * math.log2(max(freq, 200.0) / 1000.0)
            band_db[b] = db
            if db > top:
                top = db

        # Toit glissant : attaque immédiate, relâchement lent (~6 dB/s).
        if top > self._top_db:
            self._top_db = top
        else:
            self._top_db = max(-90.0, self._top_db - 0.14)
        floor = self._top_db - _DYNAMIC_RANGE_DB

        # Niveau global (RMS) et porte de silence.
        energy = math.fsum(v * v for v in mono) / n
        rms_amp = math.sqrt(energy)
        rms_db = _db_from_amplitude(rms_amp)
        silent = rms_db < _SILENCE_DB
        gain = _clamp01((rms_db + 48.0) / 40.0) if not silent else 0.0

        bars = self._bars
        peaks = self._peaks
        ages = self._peak_age
        for b in range(self.bands):
            target = _clamp01((band_db[b] - floor) / _DYNAMIC_RANGE_DB) ** 1.5
            target *= gain
            bars[b] = _smooth(bars[b], target, 0.62, 0.11)
            if bars[b] >= peaks[b]:
                peaks[b] = bars[b]
                ages[b] = 0.0
            else:
                ages[b] += 1.0
                if ages[b] > 14:  # maintien ~0.35 s puis chute
                    peaks[b] = max(bars[b], peaks[b] - 0.022)

        # Oscilloscope : décimation avec moyenne (anti-repliement simple).
        step = max(1, n // WAVE_POINTS)
        wave = self._wave
        for w in range(WAVE_POINTS):
            start = w * step
            end = min(n, start + step)
            acc = 0.0
            for i in range(start, end):
                acc += mono[i]
            wave[w] = acc / max(1, end - start)

        # VU-mètres : RMS global + crêtes par canal avec décroissance douce.
        self._rms = _smooth(self._rms, _clamp01((rms_db + 50.0) / 50.0), 0.7, 0.10)
        self._peak = max(peak_l, peak_r)
        self._peak_l = _smooth(self._peak_l, _clamp01(peak_l), 0.9, 0.08)
        self._peak_r = _smooth(self._peak_r, _clamp01(peak_r), 0.9, 0.08)

        # Énergies par plage (basses / mediums / aigus).
        bass_db = max(band_db[0], band_db[1], band_db[2], band_db[3]) if self.bands > 4 else top
        mid_lo, mid_hi = self.bands // 4, (3 * self.bands) // 4
        mid_db = max(band_db[mid_lo:mid_hi]) if mid_hi > mid_lo else top
        treble_db = max(band_db[(3 * self.bands) // 4 :])
        norm = _DYNAMIC_RANGE_DB
        self._bass = _smooth(self._bass, _clamp01((bass_db - floor) / norm) * gain, 0.7, 0.12)
        self._mid = _smooth(self._mid, _clamp01((mid_db - floor) / norm) * gain, 0.7, 0.12)
        self._treble = _smooth(
            self._treble, _clamp01((treble_db - floor) / norm) * gain, 0.7, 0.12
        )

        # Détection de beat : montée d'énergie dans les basses.
        bass_energy = 10.0 ** (bass_db / 10.0)
        if self._bass_slow <= 0.0:
            self._bass_slow = bass_energy
        self._bass_fast += (bass_energy - self._bass_fast) * 0.5
        self._bass_slow += (bass_energy - self._bass_slow) * 0.03
        now = time.monotonic()
        flux = self._bass_fast - self._bass_slow * 1.25
        if (
            not silent
            and flux > max(self._bass_slow * 0.35, 1e-5)
            and now - self._last_beat > 0.20
        ):
            self._beat = 1.0
            self._last_beat = now
        else:
            self._beat = max(0.0, self._beat - 0.085)

        with self._lock:
            self._rms_db = rms_db
            self._last_feed = now
            self._has_data = True
            self._silent_since = 0.0 if not silent else self._silent_since + 1.0 / 43.0

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> SpectrumState:
        """État immuable, sûr à lire depuis le thread d'affichage."""
        with self._lock:
            bars = tuple(round(v, 4) for v in self._bars)
            peaks = tuple(round(v, 4) for v in self._peaks)
            wave = tuple(round(v, 4) for v in self._wave)
            return SpectrumState(
                bars=bars,
                peaks=peaks,
                wave=wave,
                rms=round(self._rms, 4),
                peak=round(self._peak, 4),
                peak_l=round(self._peak_l, 4),
                peak_r=round(self._peak_r, 4),
                bass=round(self._bass, 4),
                mid=round(self._mid, 4),
                treble=round(self._treble, 4),
                beat=round(self._beat, 4),
            )

    def reset(self) -> None:
        """Remet le spectre à zéro (changement de station / reconnexion)."""
        with self._lock:
            self._bars = [0.0] * self.bands
            self._peaks = [0.0] * self.bands
            self._peak_age = [0.0] * self.bands
            self._wave = [0.0] * WAVE_POINTS
            self._rms = 0.0
            self._peak = 0.0
            self._peak_l = 0.0
            self._peak_r = 0.0
            self._bass = 0.0
            self._mid = 0.0
            self._treble = 0.0
            self._beat = 0.0
            self._top_db = -30.0
            self._bass_fast = 0.0
            self._bass_slow = 0.0
            self._last_beat = 0.0
            self._last_feed = 0.0
            self._silent_since = 0.0
            self._has_data = False

    @property
    def has_data(self) -> bool:
        with self._lock:
            return self._has_data
