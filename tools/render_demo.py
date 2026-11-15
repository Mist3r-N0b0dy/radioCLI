#!/usr/bin/env python3
"""radioCLI visualizer preview harness.

Runs curses with synthetic frames (no network, no audio) for visual QA:
moving bars, periodic beat, long-title marquee, volume changes, REC badge
and the reconnection overlay.

Examples::

    PYTHONPATH=src python3 tools/render_demo.py
    PYTHONPATH=src python3 tools/render_demo.py --seconds 20 --theme dub
    PYTHONPATH=src python3 tools/render_demo.py --disconnected --recording
    PYTHONPATH=src python3 tools/render_demo.py --size 80x24 --seconds 5
    PYTHONPATH=src python3 tools/render_demo.py --visual radial --seconds 20

Keys: ``q`` quit, ``t`` next theme, ``v`` next spectrum style,
``d`` toggle connected, ``r`` toggle recording, ``space`` mute,
``+``/``-`` volume.

At the end of the session a summary (frames, average draw time) is printed.
"""

from __future__ import annotations

import argparse
import curses
import math
import os
import sys
import time
from pathlib import Path

# Permet de lancer le harnais sans avoir à installer le package.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from radiocli.frame import BAND_COUNT, WAVE_POINTS, Frame  # noqa: E402
from radiocli.render import (  # noqa: E402
    PRIMARY_VISUAL,
    VISUAL_LABELS,
    VISUAL_MODES,
    Renderer,
    normalize_visual,
)
from radiocli.themes import PRIMARY_THEME, THEME_NAMES  # noqa: E402

LONG_TITLE = "Sai Sai — Revolt Dub (Extended Rasta Mix) · Roots & Culture Selection"
SHORT_TITLE = "Sai Sai - Revolt Dub"


# ---------------------------------------------------------------------------
# Génération de frames synthétiques
# ---------------------------------------------------------------------------
def beat_envelope(t: float, period: float) -> float:
    """Impulsion de beat qui retombe, période en secondes."""
    phase = (t % period) / period
    return math.exp(-phase * 7.0)


class SynthState:
    """Petit état interne du générateur (peak-hold, volume, bascules)."""

    def __init__(self, title: str = LONG_TITLE) -> None:
        self.title = title
        self.peaks = [0.0] * BAND_COUNT
        self.connected = True
        self.recording = False
        self.muted = False
        self.volume = 80
        self.demo = True


def synthesize(t: float, state: SynthState, theme_name: str, fps: float) -> Frame:
    """Construit une Frame cohérente et vivante à l'instant ``t``."""
    beat = beat_envelope(t, 0.62) if state.connected else 0.0

    bars: list[float] = []
    for index in range(BAND_COUNT):
        x = index / max(1, BAND_COUNT - 1)
        if state.connected:
            base = 0.52 - 0.30 * x
            wobble = 0.5 + 0.5 * math.sin(t * 2.1 + x * 7.5)
            ripple = 0.12 * math.sin(t * 5.3 - x * 18.0)
            clusters = 0.10 * math.sin(x * 19.0 - t * 0.9)
            low_boost = 0.30 * math.exp(-x * 5.5) * beat
            value = base * (0.45 + 0.55 * wobble) + ripple + clusters + low_boost
        else:
            value = 0.0
        bars.append(max(0.0, min(1.0, value)))

    for index, value in enumerate(bars):
        peak = state.peaks[index]
        if value >= peak:
            state.peaks[index] = value
        else:
            state.peaks[index] = max(value, peak - 0.012)
    peaks = list(state.peaks)

    amplitude = 0.12 + 0.72 * (sum(bars) / BAND_COUNT) if state.connected else 0.0
    wave = []
    for index in range(WAVE_POINTS):
        x = index / max(1, WAVE_POINTS - 1)
        sample = (
            0.62 * math.sin(2.0 * math.pi * (x * 3.0 + t * 1.7))
            + 0.24 * math.sin(2.0 * math.pi * (x * 7.0 - t * 2.4))
            + 0.10 * math.sin(2.0 * math.pi * (x * 11.0 + t * 3.3))
        )
        wave.append(sample * amplitude)

    bass = (0.55 + 0.40 * beat) if state.connected else 0.0
    mid = (sum(bars[BAND_COUNT // 4 : (3 * BAND_COUNT) // 4]) / (BAND_COUNT // 2)) if state.connected else 0.0
    treble = (sum(bars[(3 * BAND_COUNT) // 4 :]) / (BAND_COUNT // 4)) if state.connected else 0.0
    rms = (sum(bars) / BAND_COUNT) if state.connected else 0.0
    peak_l = (0.55 + 0.28 * math.sin(t * 1.1) + 0.15 * beat) if state.connected else 0.0
    peak_r = (0.52 + 0.30 * math.sin(t * 1.35 + 1.0) + 0.15 * beat) if state.connected else 0.0

    elapsed_seconds = t
    title = state.title
    status = "" if state.connected else "Serveur injoignable · nouvelle tentative…"

    return Frame(
        bars=tuple(bars),
        peaks=tuple(peaks),
        wave=tuple(wave),
        rms=max(0.0, min(1.0, rms)),
        peak=max(peak_l, peak_r),
        peak_l=max(0.0, min(1.0, peak_l)),
        peak_r=max(0.0, min(1.0, peak_r)),
        bass=max(0.0, min(1.0, bass)),
        mid=max(0.0, min(1.0, mid)),
        treble=max(0.0, min(1.0, treble)),
        beat=beat,
        title=title,
        station="radioCLI demo",
        subtitle="Dub · Dancehall · Roots · Paris",
        bitrate="192 kbps",
        listeners=1234 + int(40 * math.sin(t * 0.15)),
        elapsed=elapsed_seconds,
        fps=fps,
        volume=state.volume,
        muted=state.muted,
        recording=state.recording,
        recording_seconds=(t - 0.0) if state.recording else 0.0,
        connected=state.connected,
        status=status,
        demo=state.demo,
        theme=theme_name,
        ts=time.monotonic(),
    )


# ---------------------------------------------------------------------------
# Boucle curses
# ---------------------------------------------------------------------------
def handle_key(key: int, renderer: Renderer, state: SynthState) -> bool:
    """Traite une touche ; renvoie ``False`` si l'utilisateur veut quitter."""
    if key in (ord("q"), 27):  # q / Échap
        return False
    if key == ord("t"):
        renderer.cycle_theme()
    elif key == ord("v"):
        renderer.cycle_visual()
    elif key == ord("d"):
        state.connected = not state.connected
    elif key == ord("r"):
        state.recording = not state.recording
    elif key == ord(" "):
        state.muted = not state.muted
    elif key in (ord("+"), ord("=")):
        state.volume = min(100, state.volume + 5)
    elif key in (ord("-"), ord("_")):
        state.volume = max(0, state.volume - 5)
    return True


def handle_resize(stdscr) -> None:
    """Resynchronise curses après un redimensionnement du terminal."""
    try:
        curses.update_lines_cols()
    except Exception:
        pass
    try:
        curses.resize_term(curses.LINES, curses.COLS)
    except Exception:
        pass
    try:
        stdscr.clear()
    except Exception:
        pass


def run(stdscr, args: argparse.Namespace) -> dict[str, float]:
    """Boucle principale du harnais, exécutée par ``curses.wrapper``."""
    stdscr.nodelay(True)
    stdscr.keypad(True)
    try:
        curses.curs_set(0)
    except Exception:
        pass

    renderer = Renderer(
        stdscr,
        theme=args.theme,
        visual=normalize_visual(args.visual) or PRIMARY_VISUAL,
    )
    state = SynthState(title=args.title)
    state.connected = args.connected
    state.recording = args.recording
    state.muted = args.muted

    start = time.monotonic()
    frames = 0
    total_draw = 0.0
    fps = max(1.0, args.fps)  # valeur de départ réaliste, lissée ensuite
    last_frame = start
    running = True
    while running:
        now = time.monotonic()
        if now - start >= args.seconds:
            break
        key = stdscr.getch()
        while key != -1:
            if key == curses.KEY_RESIZE:
                handle_resize(stdscr)
            elif not handle_key(key, renderer, state):
                running = False
                break
            key = stdscr.getch()
        if not running:
            break

        delta = now - last_frame
        last_frame = now
        if delta > 0.0:
            instant = 1.0 / delta
            fps = instant if fps <= 0.0 else fps * 0.85 + instant * 0.15

        elapsed = now - start
        frame = synthesize(elapsed, state, renderer.theme_name, fps)
        draw_start = time.perf_counter()
        renderer.draw(frame)
        total_draw += time.perf_counter() - draw_start
        frames += 1

        # Cadence cible : évite de saturer un cœur inutilement.
        due = start + frames / max(1.0, args.fps)
        sleep_time = due - time.monotonic()
        if sleep_time > 0:
            time.sleep(sleep_time)

    return {
        "frames": float(frames),
        "seconds": time.monotonic() - start,
        "avg_draw_ms": (total_draw / frames * 1000.0) if frames else 0.0,
        "theme": renderer.theme_name,
        "visual": renderer.visual_name,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="radioCLI visualizer preview harness (curses).",
    )
    parser.add_argument("--seconds", type=float, default=8.0, help="auto-stop duration in seconds (0.5 = short)")
    parser.add_argument("--fps", type=float, default=30.0, help="target frame rate (frames/second)")
    parser.add_argument(
        "--size",
        default="",
        metavar="LxH",
        help="forced size (e.g. 80x24) via LINES/COLUMNS; defaults to terminal size",
    )
    parser.add_argument(
        "--theme",
        default=PRIMARY_THEME,
        choices=list(THEME_NAMES),
        help="initial color theme",
    )
    parser.add_argument(
        "--visual",
        default=PRIMARY_VISUAL,
        choices=list(VISUAL_MODES),
        help="initial spectrum style",
    )
    parser.add_argument("--title", default=LONG_TITLE, help="displayed title (long titles scroll)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--connected", dest="connected", action="store_true", default=True, help="connected stream (default)")
    group.add_argument("--disconnected", dest="connected", action="store_false", help="simulate reconnection")
    parser.add_argument("--recording", action="store_true", help="show the REC badge")
    parser.add_argument("--muted", action="store_true", help="start muted")
    args = parser.parse_args(argv)
    if args.size:
        try:
            width_text, height_text = args.size.lower().split("x", 1)
            width, height = int(width_text), int(height_text)
        except ValueError:
            parser.error("--size must look like 80x24")
        if width < 4 or height < 3:
            parser.error("minimum --size is 4x3")
        os.environ["COLUMNS"] = str(width)
        os.environ["LINES"] = str(height)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        stats = curses.wrapper(run, args)
    except KeyboardInterrupt:
        return 130
    print(
        "harness: {frames:.0f} frames in {seconds:.1f}s · avg draw {avg:.2f} ms · "
        "theme {theme} · visual {visual}".format(
            frames=stats["frames"],
            seconds=stats["seconds"],
            avg=stats["avg_draw_ms"],
            theme=stats["theme"],
            visual=VISUAL_LABELS.get(str(stats["visual"]), stats["visual"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
