"""Point d'entrée : boucle curses, clavier, assemblage des frames.

Lancement :
    radiocli                    # webradio + visualiseur + son
    radiocli --demo             # visualiseur simulé, sans réseau
    radiocli --stats --duration 10   # test headless (sans interface)
"""

from __future__ import annotations

import argparse
import curses
import gc
import locale
import random
import shutil
import signal
import sys
import time
import traceback
from urllib.parse import urlsplit

from . import __version__
from .analysis import SpectrumAnalyzer
from .audio import AudioEngine
from .config import (
    DEFAULT_STATION,
    GENRES,
    STATIONS,
    UI_FPS,
    Station,
    genre_label,
    next_station,
    station_by_key,
)
from .demo import DemoSpectrum
from .menu import PAGE_STATIONS, MenuState
from .frame import Frame

_KEYS_QUIT = {ord("q"), ord("Q"), 27}
_KEYS_MUTE = {ord(" ")}
_KEYS_VOL_UP = {ord("+"), ord("="), ord("k")}
_KEYS_VOL_DOWN = {ord("-"), ord("_"), ord("j")}
_KEYS_THEME = {ord("t"), ord("T")}
_KEYS_VISUAL = {ord("v"), ord("V")}
_KEYS_AUTO = {ord("a"), ord("A")}
_KEYS_REACTIVE = {ord("c"), ord("C")}
_KEYS_STATION = {ord("s")}
_KEYS_GENRE = {ord("S")}
_KEYS_RECORD = {ord("r"), ord("R")}

#: Visuels braille 3D plus coûteux : on plafonne leur cadence à 30 i/s pour
#: garder un mouvement régulier (40 i/s reste la cible des styles plus légers).
_HEAVY_VISUALS = {"tunnel", "sphere", "carpet"}


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radiocli",
        description=(
            "Listen to web radio in your terminal, with an animated spectrum "
            "visualizer, now-playing title and recorder."
        ),
        epilog=(
            "Keys: (q)uit · m(u)te · +/- vol · (t)heme · (v)isual · (m)enu · "
            "(s)tation · (S)genre · (r)ecord.\n"
            "Examples:\n"
            "  radiocli\n"
            "  radiocli --demo --theme dub --visual radial\n"
            "  radiocli --station mouv            # French rap\n"
            "  radiocli --list-stations           # the 39 stations\n"
            "  radiocli --auto-visual --reactive  # random visual + music colors\n"
            "  radiocli --stats --no-audio --duration 10\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--station",
        "-s",
        default=DEFAULT_STATION,
        metavar="KEY",
        help=f"station key (see --list-stations; default: {DEFAULT_STATION})",
    )
    parser.add_argument(
        "--url",
        "-u",
        metavar="URL",
        help="custom Icecast/MP3 stream (overrides --station)",
    )
    parser.add_argument(
        "--theme",
        "-t",
        metavar="NAME",
        help="color theme (default: roots)",
    )
    parser.add_argument(
        "--visual",
        "-V",
        metavar="MODE",
        default=None,
        help=(
            "spectrum style: bars, vertical, radial, tunnel, sphere, carpet, "
            "geodesic, rosace_live, mandala, rosace, snowflake, galaxy "
            "(default: bars; press v to change live)"
        ),
    )
    parser.add_argument(
        "--auto-visual",
        action="store_true",
        help="cycle visuals randomly every 10 to 30 seconds",
    )
    parser.add_argument(
        "--reactive",
        action="store_true",
        help="music-reactive color palette",
    )
    parser.add_argument(
        "--volume",
        type=int,
        default=80,
        metavar="0-100",
        help="initial volume (default: 80)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="demo mode: simulated spectrum, no network or audio",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="analyze the stream without playing sound",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="headless mode: print analyzer status, no UI",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="quit automatically after N seconds (0 = forever)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=UI_FPS,
        metavar="N",
        help=f"UI frames per second (default: {UI_FPS:.0f})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show tracebacks on errors",
    )
    parser.add_argument(
        "--list-stations",
        action="store_true",
        help="list available stations and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _resolve_station(args: argparse.Namespace) -> Station:
    if args.url:
        parts = urlsplit(args.url)
        name = "Custom stream"
        subtitle = args.url
        return Station(
            key="custom",
            name=name,
            subtitle=subtitle,
            url=args.url,
            bitrate=0,
        )
    return station_by_key(args.station)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_stations:
        print("Available stations (s = next in genre, S = next genre):")
        for genre, label in GENRES:
            stations = [station for station in STATIONS if station.genre == genre]
            if not stations:
                continue
            print(f"\n  {label}")
            for station in stations:
                print(f"    {station.key:<12} {station.label():<38} {station.url}")
        print("\nCustom stream: radiocli --url http://…")
        return 0

    try:
        station = _resolve_station(args)
    except KeyError as exc:
        print(f"Error: {exc.args[0]}", file=sys.stderr)
        return 2

    if args.demo:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print(
                "Demo mode needs an interactive terminal.\n"
                "Tip: run it in your usual terminal.",
                file=sys.stderr,
            )
            return 1
        return _wrap_ui(args, station)

    if not shutil.which("ffmpeg"):
        print(
            "Error: ffmpeg not found. It is required to decode the stream.\n"
            "Install it (e.g. sudo pacman -S ffmpeg) or try --demo.",
            file=sys.stderr,
        )
        return 1

    if args.stats:
        return _run_stats(args, station)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "This command needs an interactive terminal.\n"
            "To test without UI: radiocli --stats --no-audio --duration 10",
            file=sys.stderr,
        )
        return 1
    return _wrap_ui(args, station)


def _wrap_ui(args: argparse.Namespace, station: Station) -> int:
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    # SIGTERM/SIGHUP (fermeture du terminal, arrêt de session…) doivent passer
    # par la même sortie propre que Ctrl+C : arrêt de mpv/ffmpeg et restauration
    # du terminal, plutôt qu'un orphelin qui continue de jouer.
    #
    # Le handler est volontairement idempotent : quand le terminal se ferme,
    # plusieurs SIGHUP arrivent (pty + session), et un KeyboardInterrupt levé
    # pendant le nettoyage interromprait la finalisation de l'enregistrement et
    # la suppression du socket IPC. Seul le premier signal déclenche l'arrêt.
    state = {"shutting_down": False}

    def _interrupt(signum: int, frame: object) -> None:
        if state["shutting_down"]:
            return
        state["shutting_down"] = True
        raise KeyboardInterrupt

    previous: dict[int, object] = {}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[signum] = signal.signal(signum, _interrupt)
        except (ValueError, OSError):
            pass

    exit_code = 0
    try:
        curses.wrapper(_run_ui, args, station)
    except KeyboardInterrupt:
        exit_code = 0
    except curses.error as exc:
        if state["shutting_down"]:
            # Terminal détruit pendant l'arrêt : comportement normal.
            exit_code = 0
        else:
            print(
                f"Terminal error: {exc}. Try a UTF-8 terminal (and correct TERM).",
                file=sys.stderr,
            )
            exit_code = 1
    except Exception:  # noqa: BLE001 - on veut toujours rendre le terminal
        if args.debug:
            traceback.print_exc()
        else:
            print(
                "Unexpected error: run with --debug for details.",
                file=sys.stderr,
            )
        exit_code = 1
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass
    return exit_code


# ------------------------------------------------------------------------ UI
def _run_ui(stdscr: "curses.window", args: argparse.Namespace, station: Station) -> None:
    from .render import (
        PRIMARY_THEME,
        PRIMARY_VISUAL,
        THEME_NAMES,
        VISUAL_LABELS,
        VISUAL_MODES,
        Renderer,
        normalize_visual,
    )

    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    stdscr.timeout(0)
    # Sans cela, Échap attend ~1 s (ESCDELAY ncurses) avant d'être livré :
    # gênant pour fermer le menu. 25 ms suffit à distinguer les flèches.
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass

    # Rendu temps réel : on réduit les pauses du GC (le moteur alloue beaucoup
    # de listes par analyse) et on rend le GIL plus équitable entre le thread
    # d'analyse et celui de l'UI.
    gc.freeze()
    gc.set_threshold(50_000, 50, 50)
    try:
        sys.setswitchinterval(0.001)
    except (AttributeError, ValueError):
        pass

    theme = args.theme if args.theme in THEME_NAMES else PRIMARY_THEME
    visual = normalize_visual(args.visual) or PRIMARY_VISUAL
    analyzer = SpectrumAnalyzer()
    demo = DemoSpectrum() if args.demo else None
    engine: AudioEngine | None = None
    if not args.demo:
        engine = AudioEngine(
            station,
            analyzer,
            volume=args.volume,
            play_audio=not args.no_audio,
        )

    renderer = Renderer(stdscr, theme=theme, visual=visual)
    menu = MenuState()
    auto_visual = bool(args.auto_visual)
    reactive = bool(args.reactive)
    renderer.set_reactive(reactive)
    menu.auto_visual = auto_visual
    menu.reactive = reactive
    next_auto_switch = time.monotonic() + random.uniform(10.0, 30.0)
    if engine is not None:
        engine.start()

    session_start = time.monotonic()
    last_draw = session_start
    fps = float(args.fps)
    transient = ""
    transient_until = 0.0
    if args.theme and args.theme not in THEME_NAMES:
        transient = f"Unknown theme: {args.theme} (themes: {', '.join(THEME_NAMES)})"
        transient_until = time.monotonic() + 4.0
    if args.visual and not normalize_visual(args.visual):
        transient = (
            f"Unknown visual: {args.visual} "
            "(bars, vertical, radial, tunnel, sphere, carpet, geodesic, rosace_live, "
            "mandala, rosace, snowflake, galaxy)"
        )
        transient_until = time.monotonic() + 4.0

    next_frame = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if args.duration and now - session_start >= args.duration:
                break

            # --- clavier (drain des touches en attente) -------------------
            while True:
                key = stdscr.getch()
                if key == -1:
                    break
                if key == curses.KEY_RESIZE:
                    # ncurses ne redimensionne pas toujours stdscr tout seul :
                    # sans ceci, addstr() sur les nouvelles lignes/colonnes
                    # échoue et les zones redimensionnées restent vides.
                    try:
                        curses.update_lines_cols()
                        curses.resize_term(curses.LINES, curses.COLS)
                    except curses.error:
                        pass
                    stdscr.clear()
                    continue
                if menu.open:
                    if menu.page == PAGE_STATIONS:
                        if key == 27:  # Échap : retour au menu principal
                            menu.back()
                        elif key == curses.KEY_UP:
                            menu.move(-1)
                        elif key == curses.KEY_DOWN:
                            menu.move(1)
                        elif key == curses.KEY_PPAGE:
                            menu.move(-6)
                        elif key == curses.KEY_NPAGE:
                            menu.move(6)
                        elif key == curses.KEY_HOME:
                            menu.selected = 0
                        elif key == curses.KEY_END:
                            menu.selected = max(0, len(menu.filtered()) - 1)
                        elif key in (curses.KEY_ENTER, 10, 13):
                            target = menu.current_station()
                            if target is not None:
                                if engine is None:
                                    transient = "Selection unavailable in demo mode"
                                    transient_until = now + 3.0
                                else:
                                    engine.set_station(target)
                                    transient = (
                                        f"Station: {target.name} · "
                                        f"{genre_label(target.genre)}"
                                    )
                                    transient_until = now + 3.0
                                menu.close()
                        elif key in (curses.KEY_BACKSPACE, 8, 127):
                            menu.backspace()
                        elif 32 <= key < 127:
                            menu.append_char(chr(key))
                    else:
                        if key in (27, ord("m")):
                            menu.close()
                        elif key == ord("s"):
                            menu.open_stations()
                        elif key == ord("t"):
                            theme = renderer.cycle_theme()
                            transient = f"Theme: {theme}"
                            transient_until = now + 2.0
                        elif key == ord("v"):
                            visual = renderer.cycle_visual()
                            transient = f"Visual: {VISUAL_LABELS[visual]}"
                            transient_until = now + 2.0
                        elif key == ord("a"):
                            auto_visual = not auto_visual
                            menu.auto_visual = auto_visual
                            if auto_visual:
                                next_auto_switch = now + random.uniform(10.0, 30.0)
                            transient = (
                                "Auto visual: ON (10–30 s)"
                                if auto_visual
                                else "Auto visual: OFF"
                            )
                            transient_until = now + 3.0
                        elif key == ord("c"):
                            reactive = not reactive
                            renderer.set_reactive(reactive)
                            menu.reactive = reactive
                            transient = (
                                "Reactive colors: ON"
                                if reactive
                                else "Reactive colors: OFF"
                            )
                            transient_until = now + 3.0
                    continue
                if key in _KEYS_QUIT:
                    return
                if key == ord("m"):
                    menu.open_main()
                    continue
                if key in _KEYS_AUTO:
                    auto_visual = not auto_visual
                    menu.auto_visual = auto_visual
                    if auto_visual:
                        next_auto_switch = now + random.uniform(10.0, 30.0)
                    transient = (
                        "Auto visual: ON (10–30 s)"
                        if auto_visual
                        else "Auto visual: OFF"
                    )
                    transient_until = now + 3.0
                    continue
                if key in _KEYS_REACTIVE:
                    reactive = not reactive
                    renderer.set_reactive(reactive)
                    menu.reactive = reactive
                    transient = (
                        "Reactive colors: ON"
                        if reactive
                        else "Reactive colors: OFF"
                    )
                    transient_until = now + 3.0
                    continue
                if engine is not None and args.no_audio and (
                    key in _KEYS_MUTE or key in _KEYS_VOL_UP or key in _KEYS_VOL_DOWN
                ):
                    transient = "Audio disabled (--no-audio)"
                    transient_until = now + 2.0
                elif engine is not None and key in _KEYS_MUTE:
                    muted = engine.toggle_mute()
                    transient = "Audio muted" if muted else "Audio on"
                    transient_until = now + 2.0
                elif engine is not None and key in _KEYS_VOL_UP:
                    volume = engine.volume_up()
                    transient = f"Volume {volume}%"
                    transient_until = now + 1.5
                elif engine is not None and key in _KEYS_VOL_DOWN:
                    volume = engine.volume_down()
                    transient = f"Volume {volume}%"
                    transient_until = now + 1.5
                elif key in _KEYS_THEME:
                    theme = renderer.cycle_theme()
                    transient = f"Theme: {theme}"
                    transient_until = now + 2.0
                elif key in _KEYS_VISUAL:
                    visual = renderer.cycle_visual()
                    transient = f"Visual: {VISUAL_LABELS[visual]}"
                    transient_until = now + 2.0
                elif key in _KEYS_STATION or key in _KEYS_GENRE:
                    if engine is None or args.url:
                        transient = "Station change unavailable here"
                        transient_until = now + 2.5
                    else:
                        target = next_station(
                            engine.station, same_genre=key in _KEYS_STATION
                        )
                        engine.set_station(target)
                        transient = (
                            f"Station: {target.name} · {genre_label(target.genre)}"
                        )
                        transient_until = now + 3.0
                elif key in _KEYS_RECORD:
                    if engine is None:
                        transient = "Recording unavailable in demo mode"
                        transient_until = now + 2.5
                    else:
                        try:
                            path = engine.toggle_recording()
                        except RuntimeError as exc:
                            transient = f"Recording failed: {exc}"
                        else:
                            if engine.recording:
                                transient = f"REC → {path}"
                            else:
                                transient = f"Recording stopped: {path}"
                        transient_until = now + 4.0

            # --- changement automatique de visuel (10 à 30 s) -------------
            if auto_visual and not menu.open:
                now = time.monotonic()
                if now >= next_auto_switch:
                    options = [mode for mode in VISUAL_MODES if mode != renderer.visual_name]
                    if options:
                        chosen = random.choice(options)
                        renderer.set_visual(chosen)
                        transient = (
                            f"Visual: {VISUAL_LABELS[chosen]} · auto"
                        )
                        transient_until = now + 2.5
                    next_auto_switch = now + random.uniform(10.0, 30.0)

            # --- données de la frame --------------------------------------
            if demo is not None:
                state = demo.snapshot()
                title = demo.title
                connected = True
                base_status = "Demo mode · simulated spectrum, no audio"
                volume = None
                muted = False
                recording = False
                recording_seconds = 0.0
                bitrate = ""
                subtitle = "Offline demo"
                demo_mode = True
                display_station = station
            else:
                assert engine is not None
                state = analyzer.snapshot()
                title = engine.title
                connected = engine.connected
                base_status = engine.status
                volume = engine.volume if not args.no_audio else None
                muted = engine.muted
                recording = engine.recording
                recording_seconds = engine.recording_seconds
                kbps = engine.metadata.bitrate_kbps or engine.station.bitrate
                bitrate = f"{kbps} kbps" if kbps else ""
                subtitle = engine.station.subtitle
                display_station = engine.station
                demo_mode = False
                if engine.last_error and not connected:
                    base_status = f"{base_status} · {engine.last_error}"
                recording_error = engine.take_recording_error()
                if recording_error:
                    transient = recording_error
                    transient_until = now + 4.0

            status = transient if now < transient_until else base_status

            frame = Frame(
                bars=state.bars,
                peaks=state.peaks,
                wave=state.wave,
                rms=state.rms,
                peak=state.peak,
                peak_l=state.peak_l,
                peak_r=state.peak_r,
                bass=state.bass,
                mid=state.mid,
                treble=state.treble,
                beat=state.beat,
                title=title,
                station=display_station.name,
                subtitle=subtitle,
                bitrate=bitrate,
                elapsed=now - session_start,
                fps=fps,
                volume=volume,
                muted=muted,
                recording=recording,
                recording_seconds=recording_seconds,
                connected=connected,
                status=status,
                demo=demo_mode,
                theme=theme,
                ts=now,
            )

            renderer.draw(frame, menu if menu.open else None)

            # --- cadence de l'interface ----------------------------------
            draw_done = time.monotonic()
            elapsed = draw_done - last_draw
            if elapsed > 1e-6:
                instant = 1.0 / elapsed
                fps = fps * 0.9 + instant * 0.1
            last_draw = draw_done
            target_fps = float(args.fps)
            if renderer.visual_name in _HEAVY_VISUALS:
                target_fps = min(target_fps, 30.0)
            next_frame += 1.0 / max(1.0, target_fps)
            remaining = next_frame - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            elif remaining < -(1.0 / max(1.0, target_fps)):
                # Retard de plus d'une frame (machine chargée, resize…) :
                # on recale l'échéancier au lieu de courir après le temps.
                next_frame = time.monotonic()
    finally:
        if engine is not None:
            engine.stop()


# --------------------------------------------------------------------- stats
def _run_stats(args: argparse.Namespace, station: Station) -> int:
    analyzer = SpectrumAnalyzer()
    engine = AudioEngine(
        station,
        analyzer,
        volume=args.volume,
        play_audio=not args.no_audio,
    )

    def _interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous: dict[int, object] = {}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[signum] = signal.signal(signum, _interrupt)
        except (ValueError, OSError):
            pass

    start = time.monotonic()
    last_title = ""
    engine.start()
    try:
        while True:
            elapsed = time.monotonic() - start
            if args.duration and elapsed >= args.duration:
                break
            time.sleep(0.5)
            state = analyzer.snapshot()
            title = engine.title
            marker = "  " if title == last_title else "♪ "
            last_title = title
            print(
                f"[{elapsed:6.1f}s] {engine.status:<28} "
                f"rms={state.rms:4.2f} max={max(state.bars, default=0.0):4.2f} "
                f"bass={state.bass:4.2f} beat={state.beat:3.2f} "
                f"connected={str(engine.connected):<5} "
                f"backend={engine.playback_backend or '—'} "
                f"{marker}{title or '—'}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)
    finally:
        engine.stop()
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
