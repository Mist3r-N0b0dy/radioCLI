"""Tests du rendu terminal radioCLI (sans initialiser curses).

Aucun test ne fait ``initscr()`` : les helpers purs sont testés directement et
le :class:`Renderer` est exercé avec une fausse fenêtre curses qui enregistre
chaque appel ``addstr`` et vérifie qu'aucune écriture ne sort de l'écran.

Exécution :
    PYTHONPATH=src python3 -m unittest tests.test_render -v
"""

from __future__ import annotations

import time
import unittest

from radiocli import render as render_module
from radiocli.frame import BAND_COUNT, WAVE_POINTS, Frame
from radiocli.menu import MenuState
from radiocli.render import (
    CREDIT,
    PRIMARY_THEME,
    PRIMARY_VISUAL,
    THEME_NAMES,
    VISUAL_LABELS,
    VISUAL_MODES,
    Layout,
    Rect,
    Renderer,
    bar_eighths,
    clamp01,
    compute_layout,
    format_clock,
    format_listeners,
    format_rec,
    format_volume,
    hints_text,
    marquee_view,
    normalize_visual,
    resample,
    truncate_text,
    wave_to_braille,
)
from radiocli.themes import (
    GRADIENT_LEVELS,
    THEMES,
    ColorManager,
    adapt_color,
    gradient_level,
    gradient_steps,
    nearest_basic,
    nearest_xterm,
    theme_gradient,
    theme_gradient_rgb,
    xterm_rgb,
)

TARGET_SIZES = ((40, 12), (60, 20), (80, 24), (120, 40), (200, 60))


# ---------------------------------------------------------------------------
# Fausse fenêtre curses
# ---------------------------------------------------------------------------
class FakeWindow:
    """Imite le strict minimum de ``stdscr`` et relève tout débordement."""

    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.calls: list[tuple[int, int, str, int]] = []
        self.violations: list[tuple[int, int, str]] = []
        self.grid: dict[tuple[int, int], str] = {}
        self.refreshed = 0
        self.cleared = 0

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if not isinstance(text, str):
            raise TypeError("addstr attend un str")
        if y < 0 or y >= self.height or x < 0 or x >= self.width:
            self.violations.append((y, x, text))
            return
        if x + len(text) > self.width:
            self.violations.append((y, x, text))
            return
        self.calls.append((y, x, text, attr))
        for offset, char in enumerate(text):
            self.grid[(y, x + offset)] = char

    def refresh(self) -> None:
        self.refreshed += 1

    def clear(self) -> None:
        self.cleared += 1
        self.grid.clear()

    def resize(self, height: int, width: int) -> None:
        self.height, self.width = height, width
        self.clear()

    def row_text(self, y: int) -> str:
        if y < 0 or y >= self.height:
            return ""
        return "".join(self.grid.get((y, x), " ") for x in range(self.width))

    def all_text(self) -> str:
        return "\n".join(self.row_text(y) for y in range(self.height))


def make_frame(**overrides) -> Frame:
    """Frame pleine et réaliste pour les tests de rendu."""
    bars = tuple(
        0.15 + 0.75 * (0.5 + 0.5 * ((index * 37) % 64 - 32) / 32) for index in range(BAND_COUNT)
    )
    peaks = tuple(min(1.0, value + 0.08) for value in bars)
    wave = tuple(0.6 * ((index * 13 % 100) / 100.0 - 0.5) * 2.0 for index in range(WAVE_POINTS))
    base = dict(
        bars=bars,
        peaks=peaks,
        wave=wave,
        rms=0.55,
        peak=0.82,
        peak_l=0.78,
        peak_r=0.64,
        bass=0.72,
        mid=0.5,
        treble=0.34,
        beat=0.7,
        title="Sai Sai — Revolt Dub (Extended Rasta Mix) · Sélection Roots & Culture",
        bitrate="192 kbps",
        elapsed=754.0,
        fps=30.0,
        volume=80,
        station="FIP",
        demo=True,
        ts=12.5,
    )
    base.update(overrides)
    return Frame(**base)


# ---------------------------------------------------------------------------
# Helpers purs
# ---------------------------------------------------------------------------
class TestClamp(unittest.TestCase):
    def test_bornes(self) -> None:
        self.assertEqual(clamp01(-3), 0.0)
        self.assertEqual(clamp01(0.0), 0.0)
        self.assertEqual(clamp01(0.42), 0.42)
        self.assertEqual(clamp01(1.0), 1.0)
        self.assertEqual(clamp01(9), 1.0)

    def test_entrees_exotiques(self) -> None:
        self.assertEqual(clamp01(None), 0.0)
        self.assertEqual(clamp01("abc"), 0.0)
        self.assertEqual(clamp01(float("nan")), 0.0)
        self.assertEqual(clamp01("0.25"), 0.25)


class TestResample(unittest.TestCase):
    def test_longueurs(self) -> None:
        source = tuple(index / (BAND_COUNT - 1) for index in range(BAND_COUNT))
        for width in (1, 2, 10, 63, 64, 65, 120, 200):
            result = resample(source, width)
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), width, f"largeur {width}")

    def test_valeurs_bornees(self) -> None:
        source = (-1.0, 0.5, 2.0, 0.0, 1.0) * 12
        for width in (1, 7, 64, 200):
            for value in resample(source, width):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_constante_preservee(self) -> None:
        for width in (1, 5, 64, 65, 200):
            values = resample((0.5,) * BAND_COUNT, width)
            self.assertTrue(all(abs(value - 0.5) < 1e-9 for value in values))

    def test_crete_preservee_sous_echantillonnage(self) -> None:
        source = [0.1] * BAND_COUNT
        source[33] = 1.0
        result = resample(source, 10)
        self.assertAlmostEqual(max(result), 1.0)

    def test_entrees_vides_ou_invalides(self) -> None:
        self.assertEqual(resample((), 8), (0.0,) * 8)
        self.assertEqual(resample(None, 4), (0.0,) * 4)
        self.assertEqual(resample((0.3,) * 10, 0), ())
        self.assertEqual(len(resample((0.3,) * 10, -5)), 0)

    def test_agrandissement_regulier(self) -> None:
        result = resample((0.0,) * 10 + (1.0,) * 10, 40)
        self.assertEqual(len(result), 40)
        self.assertAlmostEqual(result[0], 0.0)
        self.assertAlmostEqual(result[-1], 1.0)
        self.assertTrue(all(a <= b + 1e-9 for a, b in zip(result, result[1:])))


class TestBarHeights(unittest.TestCase):
    def test_bornes(self) -> None:
        self.assertEqual(bar_eighths(0.0, 10), 0)
        self.assertEqual(bar_eighths(1.0, 10), 80)
        self.assertEqual(bar_eighths(-2.0, 10), 0)
        self.assertEqual(bar_eighths(2.0, 10), 80)
        self.assertEqual(bar_eighths(0.5, 10), 40)
        self.assertEqual(bar_eighths(0.5, 0), 0)

    def test_croissance(self) -> None:
        heights = [bar_eighths(value / 20.0, 11) for value in range(21)]
        self.assertEqual(heights, sorted(heights))
        self.assertEqual(len(set(heights)), len(heights))


class TestWaveBraille(unittest.TestCase):
    def test_dimensions(self) -> None:
        wave = tuple(0.5 * ((i % 50) / 50.0 - 0.5) for i in range(WAVE_POINTS))
        for width, height in ((1, 1), (10, 1), (40, 2), (120, 3), (200, 3)):
            rows = wave_to_braille(wave, width, height)
            self.assertEqual(len(rows), height)
            for row in rows:
                self.assertEqual(len(row), width)

    def test_silence_ligne_plate(self) -> None:
        rows = wave_to_braille((0.0,) * WAVE_POINTS, 20, 2)
        joined = "".join(rows)
        self.assertIn("⠉", joined)
        self.assertEqual(rows[0], " " * 20)

    def test_valeurs_extremes_et_entrees_vides(self) -> None:
        wave = (-5.0, 5.0, float("nan")) * 20
        rows = wave_to_braille(wave, 16, 3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(row) == 16 for row in rows))
        wave_to_braille((), 8, 2)  # ne doit pas lever


class TestTextHelpers(unittest.TestCase):
    def test_truncate(self) -> None:
        self.assertEqual(truncate_text("abc", 0), "")
        self.assertEqual(truncate_text("abc", 3), "abc")
        self.assertEqual(truncate_text("abc", 10), "abc")
        self.assertEqual(truncate_text("abcdef", 5), "abcd…")
        self.assertEqual(truncate_text("abcdef", 1), "a")
        self.assertEqual(truncate_text(None, 4), "")

    def test_marquee(self) -> None:
        title = "Sai Sai - Revolt Dub (Extended Mix)"
        self.assertEqual(marquee_view(title, 80, 3.0), title)
        view = marquee_view(title, 12, 1.0)
        self.assertEqual(len(view), 12)
        # Le défilement dépend du temps : deux instants éloignés diffèrent.
        self.assertNotEqual(marquee_view(title, 12, 1.0), marquee_view(title, 12, 3.0))
        self.assertEqual(len(marquee_view(title, 0, 1.0)), 0)
        # ts=0 -> début du titre.
        self.assertTrue(marquee_view(title, 8, 0.0).startswith("Sai Sai"))

    def test_formats(self) -> None:
        self.assertEqual(format_clock(0), "00:00")
        self.assertEqual(format_clock(75), "01:15")
        self.assertEqual(format_clock(3675), "01:01:15")
        self.assertEqual(format_clock(-9), "00:00")
        self.assertIn("REC", format_rec(12))
        self.assertIn("00:12", format_rec(12))
        self.assertEqual(format_volume(None), "")
        self.assertEqual(format_volume(80), "vol 80%")
        self.assertEqual(format_volume(80, muted=True), "muted")
        self.assertEqual(format_volume(300), "vol 100%")
        self.assertEqual(format_listeners(None), "")
        self.assertEqual(format_listeners(1234), "1234 listeners")

    def test_hints_tiennent_toujours(self) -> None:
        for width in range(0, 201):
            hint = hints_text(width)
            self.assertLessEqual(len(hint), width)
        self.assertIn("(q)uit", hints_text(120))
        self.assertEqual(hints_text(0), "")
        self.assertIn("(q)uit", hints_text(20))


# ---------------------------------------------------------------------------
# Géométrie
# ---------------------------------------------------------------------------
class TestLayout(unittest.TestCase):
    def assert_layout_sane(self, layout: Layout) -> None:
        for rect in layout.rects():
            self.assertGreater(rect.w, 0, f"largeur nulle: {rect}")
            self.assertGreater(rect.h, 0, f"hauteur nulle: {rect}")
            self.assertGreaterEqual(rect.x, 0)
            self.assertGreaterEqual(rect.y, 0)
            self.assertLessEqual(rect.right, layout.width, f"débordement droite: {rect}")
            self.assertLessEqual(rect.bottom, layout.height, f"débordement bas: {rect}")
        for y, label in layout.rules:
            self.assertTrue(0 <= y < layout.height, f"règle hors écran: {y} {label}")
            for rect in layout.rects():
                self.assertFalse(
                    rect.y <= y < rect.bottom,
                    f"la règle {label!r} (y={y}) chevauche {rect}",
                )

    def test_tailles_cibles(self) -> None:
        expected_modes = {
            (40, 12): "compact",
            (60, 20): "medium",
            (80, 24): "full",
            (120, 40): "full",
            (200, 60): "full",
        }
        for width, height in TARGET_SIZES:
            layout = compute_layout(width, height)
            self.assert_layout_sane(layout)
            self.assertEqual(layout.width, width)
            self.assertEqual(layout.height, height)
            self.assertEqual(layout.mode, expected_modes[(width, height)])
            self.assertIsNotNone(layout.header)
            self.assertIsNotNone(layout.title)
            self.assertIsNotNone(layout.vu)
            self.assertIsNotNone(layout.spectrum)
            self.assertEqual(layout.framed, True)

    def test_partition_verticale(self) -> None:
        """Chaque ligne utile est occupée une et une seule fois."""
        for width, height in (*TARGET_SIZES, (34, 10), (50, 14), (20, 6)):
            layout = compute_layout(width, height)
            rows: list[int] = []
            for rect in layout.rects():
                rows.extend(range(rect.y, rect.bottom))
            rows.extend(y for y, _ in layout.rules)
            self.assertEqual(sorted(rows), sorted(set(rows)), f"chevauchement {width}x{height}")
            covered = set(rows)
            if layout.framed:
                self.assertEqual(covered, set(range(1, height - 1)))
            else:
                self.assertEqual(covered, set(range(0, height)))

    def test_tailles_extremes(self) -> None:
        for width, height in (
            (1, 1),
            (2, 2),
            (4, 3),
            (10, 3),
            (12, 4),
            (20, 6),
            (33, 9),
            (34, 10),
            (40, 9),
            (60, 5),
            (400, 3),
            (3, 400),
            (200, 12),
            (200, 24),
        ):
            layout = compute_layout(width, height)
            self.assert_layout_sane(layout)
            self.assertGreaterEqual(layout.spectrum.w, 1)
            self.assertGreaterEqual(layout.spectrum.h, 1)
            if layout.hints is not None:
                self.assertEqual(layout.hints.w, layout.inner.w)

    def test_tres_petit_reste_utilisable(self) -> None:
        layout = compute_layout(40, 12)
        self.assertEqual(layout.mode, "compact")
        self.assertGreaterEqual(layout.spectrum.h, 4)
        self.assertEqual(layout.hints, None)  # aides dans la bordure basse
        l2 = compute_layout(20, 6)
        self.assertEqual(l2.mode, "tiny")
        self.assertIsNotNone(l2.hints)


# ---------------------------------------------------------------------------
# Thèmes et couleurs
# ---------------------------------------------------------------------------
class TestThemes(unittest.TestCase):
    def test_noms_et_ordre(self) -> None:
        self.assertIsInstance(THEME_NAMES, tuple)
        self.assertGreaterEqual(len(THEME_NAMES), 5)
        self.assertEqual(THEME_NAMES[0], "roots")
        self.assertEqual(PRIMARY_THEME, "roots")
        self.assertEqual(set(THEME_NAMES), set(THEMES))
        self.assertEqual(render_module.THEME_NAMES, THEME_NAMES)

    def test_indices_xterm_valides(self) -> None:
        for name, theme in THEMES.items():
            for value in (
                theme.header_fg,
                theme.header_bg,
                theme.accent,
                theme.dim,
                theme.border,
                theme.spectrum_low,
                theme.spectrum_mid,
                theme.spectrum_high,
                theme.wave,
                theme.title,
                theme.vu_safe,
                theme.vu_warn,
                theme.vu_hot,
                theme.live_fg,
                theme.live_bg,
                theme.rec_fg,
                theme.rec_bg,
                theme.demo_fg,
                theme.demo_bg,
                theme.overlay_fg,
                theme.overlay_bg,
            ):
                self.assertIsInstance(value, int, name)
                self.assertTrue(0 <= value <= 255, f"{name}: {value}")

    def test_xterm_rgb(self) -> None:
        self.assertEqual(xterm_rgb(0), (0, 0, 0))
        self.assertEqual(xterm_rgb(15), (255, 255, 255))
        self.assertEqual(xterm_rgb(196), (255, 0, 0))
        self.assertEqual(xterm_rgb(232), (8, 8, 8))
        for index in (-5, 0, 100, 255, 300):
            rgb = xterm_rgb(index)
            self.assertTrue(all(0 <= channel <= 255 for channel in rgb))

    def test_degrade_bornes_et_paliers(self) -> None:
        for theme in THEMES.values():
            self.assertEqual(theme_gradient(theme, -1.0), theme.spectrum_low)
            self.assertEqual(theme_gradient(theme, 0.0), theme.spectrum_low)
            self.assertEqual(theme_gradient(theme, 0.5), theme.spectrum_mid)
            self.assertEqual(theme_gradient(theme, 1.0), theme.spectrum_high)
            for step in range(21):
                value = theme_gradient(theme, step / 20.0)
                self.assertTrue(0 <= value <= 255)
            steps = gradient_steps(theme)
            self.assertEqual(len(steps), GRADIENT_LEVELS)
            self.assertEqual(steps[0], theme.spectrum_low)
            self.assertEqual(steps[-1], theme.spectrum_high)
            self.assertTrue(all(0 <= index <= 255 for index in steps))
            self.assertEqual(gradient_level(0.0), 0)
            self.assertEqual(gradient_level(1.0), GRADIENT_LEVELS - 1)
            self.assertEqual(gradient_level(0.5, 5), 2)

    def test_degrade_monotone_dans_chaque_segment(self) -> None:
        for theme in THEMES.values():
            for start, stop in ((0.0, 0.5), (0.5, 1.0)):
                samples = [
                    theme_gradient_rgb(theme, start + (stop - start) * step / 10.0)
                    for step in range(11)
                ]
                for channel in range(3):
                    values = [sample[channel] for sample in samples]
                    variations = [b - a for a, b in zip(values, values[1:])]
                    self.assertTrue(
                        all(delta >= -1e-9 for delta in variations)
                        or all(delta <= 1e-9 for delta in variations),
                        f"{theme.name}: canal {channel} non monotone",
                    )

    def test_theme_gradient_rgb_bornes(self) -> None:
        for theme in THEMES.values():
            for step in range(11):
                rgb = theme_gradient_rgb(theme, step / 10.0)
                self.assertTrue(all(0.0 <= channel <= 255.0 for channel in rgb))

    def test_repli_couleurs(self) -> None:
        self.assertEqual(nearest_xterm((255.0, 0.0, 0.0)), 9)
        for count in (8, 16, 88, 256):
            for source in (0, 1, 7, 8, 15, 46, 196, 226, 255):
                adapted = adapt_color(source, count)
                if count >= 256:
                    self.assertEqual(adapted, max(0, min(255, source)))
                elif count >= 8:
                    self.assertTrue(0 <= adapted < min(count, 16) or adapted < count)
                    self.assertGreaterEqual(adapted, 0)
        self.assertEqual(adapt_color(-1, 8), -1)
        self.assertEqual(adapt_color(-1, 256), -1)

    def test_nearest_basic_couleurs_connues(self) -> None:
        self.assertEqual(nearest_basic(15, 16), 15)  # blanc
        self.assertEqual(nearest_basic(9, 16), 9)  # rouge vif
        self.assertEqual(nearest_basic(9, 8), 1)  # rouge ANSI
        self.assertEqual(nearest_basic(10, 8), 2)  # vert ANSI
        self.assertEqual(nearest_basic(46, 8), 2)  # vert ANSI

    def test_gestionnaire_liberation_paires(self) -> None:
        manager = ColorManager()
        manager._mono = False
        manager.colors = 8
        manager.pair_count = 64
        manager._cache = {(1, -1): 20}
        manager._next_pair = 60
        manager.begin_frame()
        self.assertEqual(manager._next_pair, 1)
        self.assertEqual(manager._cache, {})

    def test_texte_secondaire_lisible(self) -> None:
        for name, theme in THEMES.items():
            self.assertGreaterEqual(theme.text_dim, 245, name)
            self.assertGreater(
                sum(xterm_rgb(theme.text_dim)),
                sum(xterm_rgb(theme.dim)),
                f"{name}: text_dim doit être plus clair que dim",
            )

    def test_gestionnaire_avant_init(self) -> None:
        manager = ColorManager()
        self.assertTrue(manager.mono)
        self.assertEqual(manager.pair(46), 0)
        self.assertEqual(manager.pair(231, 22), 0)
        manager.init()  # sans initscr : doit rester silencieux et sûr
        self.assertEqual(manager.pair(196), 0)


# ---------------------------------------------------------------------------
# Renderer avec fausse fenêtre
# ---------------------------------------------------------------------------
class TestRenderer(unittest.TestCase):
    def draw(self, window: FakeWindow, frame: object, renderer: Renderer | None = None) -> Renderer:
        renderer = renderer or Renderer(window)
        before = window.refreshed
        renderer.draw(frame)  # type: ignore[arg-type]
        self.assertEqual(window.refreshed, before + 1, "draw() doit finir par refresh()")
        self.assertEqual(window.violations, [], "aucune écriture hors écran tolérée")
        self.assertEqual(renderer.last_error, "", renderer.last_error)
        return renderer

    def test_tailles_cibles_sans_exception(self) -> None:
        for width, height in TARGET_SIZES:
            for frame in (
                make_frame(),
                make_frame(connected=False, status="Reconnexion…"),
                make_frame(recording=True, recording_seconds=12.0),
                make_frame(title=""),
                Frame(),
            ):
                window = FakeWindow(height, width)
                self.draw(window, frame)
                self.assertGreaterEqual(len(window.calls), 10)

    def test_tailles_minuscules_sans_exception(self) -> None:
        for width, height in ((1, 1), (2, 2), (4, 3), (10, 3), (16, 5), (20, 6), (33, 9)):
            window = FakeWindow(height, width)
            self.draw(window, make_frame())
            self.draw(window, Frame())
            self.assertEqual(window.violations, [])

    def test_frame_invalide_ne_plante_pas(self) -> None:
        window = FakeWindow(24, 80)
        renderer = Renderer(window)
        before = window.refreshed
        for bogus in (None, object(), Frame(bars=(0.5,), wave=(1.0,)), {"bars": ()}):
            renderer.draw(bogus)  # type: ignore[arg-type]
            before += 1
            self.assertEqual(window.refreshed, before)
            self.assertEqual(window.violations, [])
        # La frame incomplète a été complétée par sanitized() : pas d'erreur.
        self.assertEqual(renderer.last_error, "")

    def test_entete_live_rec_demo(self) -> None:
        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        renderer.draw(make_frame(ts=0.0))
        header = window.row_text(compute_layout(120, 40).header.y)
        self.assertIn("FIP", header)
        self.assertIn("LIVE", header)
        self.assertIn("DEMO", header)
        self.assertIn("192 kbps", header)
        self.assertIn("12:34", header)
        self.assertIn("theme roots", header)

        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        renderer.draw(make_frame(connected=False, status="Tentative 2/5", ts=0.0))
        header = window.row_text(compute_layout(120, 40).header.y)
        self.assertIn("OFFLINE", header)
        self.assertIn("RECONNECTING", window.all_text())
        self.assertIn("Tentative 2/5", window.all_text())

        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        renderer.draw(make_frame(recording=True, recording_seconds=12.0, ts=0.0))
        header_y = compute_layout(120, 40).header.y
        self.assertIn("REC", window.row_text(header_y))
        self.assertIn("00:12", window.row_text(header_y))

    def test_marquee_defile(self) -> None:
        layout = compute_layout(60, 20)
        title_rect = layout.title
        self.assertIsNotNone(title_rect)
        assert title_rect is not None

        window = FakeWindow(20, 60)
        renderer = Renderer(window)
        renderer.draw(make_frame(ts=0.0))
        first = window.row_text(title_rect.y)
        renderer.draw(make_frame(ts=2.0))
        second = window.row_text(title_rect.y)
        self.assertIn("Sai Sai", first)
        self.assertNotEqual(first, second, "le marquee doit défiler avec frame.ts")

    def test_bandeau_titre_double(self) -> None:
        layout = compute_layout(120, 40)
        self.assertIsNotNone(layout.banner)
        window = FakeWindow(40, 120)
        Renderer(window).draw(make_frame())
        assert layout.banner is not None
        self.assertIn("radioCLI", window.row_text(layout.banner.y))
        self.assertIn("╠", window.row_text(layout.banner.y + 1))
        self.assertEqual(window.violations, [])

    def test_separateur_vu_pointille(self) -> None:
        layout = compute_layout(120, 40)
        self.assertEqual(layout.vu.h, 3)
        window = FakeWindow(40, 120)
        Renderer(window).draw(make_frame())
        separator = window.row_text(layout.vu.y + 1)
        self.assertIn("·", separator)
        self.assertEqual(window.violations, [])

    def test_aides_et_themes(self) -> None:
        window = FakeWindow(24, 80)
        renderer = Renderer(window)
        renderer.draw(make_frame())
        bottom = window.row_text(23)
        self.assertIn("(q)uit", bottom)
        self.assertIn("(t)heme", bottom)

        self.assertEqual(renderer.theme_name, PRIMARY_THEME)
        self.assertEqual(renderer.cycle_theme(), THEME_NAMES[1])
        self.assertEqual(renderer.cycle_theme(), THEME_NAMES[2])
        current = renderer.theme_name
        for _ in range(len(THEME_NAMES)):
            renderer.cycle_theme()
        self.assertEqual(renderer.theme_name, current)  # un tour complet revient au départ
        self.assertEqual(renderer.set_theme("dub"), "dub")
        self.assertEqual(renderer.theme_name, "dub")
        self.assertEqual(renderer.set_theme("rasta"), "roots")  # alias
        self.assertEqual(renderer.set_theme("inconnu"), PRIMARY_THEME)

    def test_reflux_sans_couleur_256(self) -> None:
        """Mono : aucune paire, mais le rendu reste complet et sans erreur."""
        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        renderer.colors._mono = True  # force le repli mono (pas de 256 couleurs)
        renderer.draw(make_frame())
        self.assertEqual(window.refreshed, 1)
        self.assertEqual(window.violations, [])
        self.assertTrue(all(attr == 0 or isinstance(attr, int) for _, _, _, attr in window.calls))
        self.assertIn("LIVE", window.row_text(compute_layout(120, 40).header.y))

    def test_resize_entre_deux_frames(self) -> None:
        window = FakeWindow(24, 80)
        renderer = Renderer(window)
        renderer.draw(make_frame())
        for width, height in ((40, 12), (120, 40), (200, 60), (60, 20), (80, 24)):
            window.resize(height, width)
            renderer.draw(make_frame())
            self.assertEqual(window.violations, [])
            self.assertEqual(window.height, height)
            self.assertGreater(window.cleared, 0)

    def test_draw_rapide(self) -> None:
        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        frame = make_frame()
        renderer.draw(frame)  # échauffement
        count = 30
        start = time.perf_counter()
        for _ in range(count):
            renderer.draw(frame)
        per_draw_ms = (time.perf_counter() - start) / count * 1000.0
        # Garde-fou large (la machine réelle doit rester sous ~8 ms).
        self.assertLess(per_draw_ms, 50.0, f"draw() trop lent : {per_draw_ms:.1f} ms")

    def test_aucun_depassement_apres_plusieurs_themes(self) -> None:
        window = FakeWindow(40, 140)
        renderer = Renderer(window)
        for name in THEME_NAMES:
            renderer.set_theme(name)
            renderer.draw(make_frame(beat=0.9))
            self.assertEqual(window.violations, [])
        self.assertIn("theme mono", window.row_text(compute_layout(140, 40).header.y))


# ---------------------------------------------------------------------------
# Modes de visualisation du spectre
# ---------------------------------------------------------------------------
class TestVisualModes(unittest.TestCase):
    def test_contrat_des_modes(self) -> None:
        self.assertEqual(
            VISUAL_MODES,
            (
                "bars", "vertical", "radial", "tunnel",
                "sphere", "carpet", "geodesic", "rosace_live",
                "mandala", "rosace", "snowflake", "galaxy",
            ),
        )
        self.assertEqual(PRIMARY_VISUAL, "bars")
        self.assertEqual(set(VISUAL_LABELS), set(VISUAL_MODES))
        for label in VISUAL_LABELS.values():
            self.assertIn(
                label,
                (
                    "bars", "vertical", "radial", "tunnel",
                    "sphere", "carpet", "geodesic sphere", "reactive rosace",
                    "mandala", "rosace", "snowflake", "galaxy",
                ),
            )

    def test_alias_et_normalisation(self) -> None:
        self.assertEqual(normalize_visual("branding"), "")
        self.assertEqual(normalize_visual("logo"), "")
        self.assertEqual(normalize_visual("barres"), "bars")
        self.assertEqual(normalize_visual("CIRCULAIRE"), "radial")
        self.assertEqual(normalize_visual("verticale"), "vertical")
        self.assertEqual(normalize_visual("tunnel"), "tunnel")
        self.assertEqual(normalize_visual("sphère"), "sphere")
        self.assertEqual(normalize_visual("tapis"), "carpet")
        self.assertEqual(normalize_visual("rosace-reactive"), "rosace_live")
        self.assertEqual(normalize_visual("reactive-rosace"), "rosace_live")
        self.assertEqual(normalize_visual("fractale"), "mandala")
        self.assertEqual(normalize_visual("mandala2"), "rosace")
        self.assertEqual(normalize_visual("flocon"), "snowflake")
        self.assertEqual(normalize_visual("geodesique"), "geodesic")
        self.assertEqual(normalize_visual("galaxie"), "galaxy")
        self.assertEqual(normalize_visual(""), "")
        self.assertEqual(normalize_visual(None), "")
        self.assertEqual(normalize_visual("inconnu"), "")

    def test_cycle_et_repli(self) -> None:
        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        self.assertEqual(renderer.visual_name, PRIMARY_VISUAL)
        cycle = tuple(renderer.cycle_visual() for _ in range(len(VISUAL_MODES)))
        self.assertEqual(cycle, VISUAL_MODES[1:] + VISUAL_MODES[:1])
        self.assertEqual(renderer.set_visual("radial"), "radial")
        self.assertEqual(renderer.visual_name, "radial")
        self.assertEqual(renderer.set_visual("circulaire"), "radial")
        self.assertEqual(renderer.set_visual("inconnu"), PRIMARY_VISUAL)

    def test_chaque_mode_sans_depassement(self) -> None:
        for mode in VISUAL_MODES:
            for width, height in TARGET_SIZES + ((34, 10), (40, 12), (16, 5)):
                window = FakeWindow(height, width)
                renderer = Renderer(window, visual=mode)
                renderer.draw(make_frame(ts=2.0))
                self.assertEqual(
                    window.violations,
                    [],
                    f"{mode} en {width}x{height} : {window.violations[:3]}",
                )
                self.assertEqual(renderer.last_error, "", f"{mode} {width}x{height}")

    def test_visuels_produisent_leur_contenu(self) -> None:
        cases = {
            "vertical": "SPECTRUM · VERTICAL",
            "radial": "SPECTRUM · RADIAL",
            "tunnel": "SPECTRUM · TUNNEL",
        }
        for mode, label in cases.items():
            window = FakeWindow(40, 120)
            Renderer(window, visual=mode).draw(make_frame(ts=1.0))
            text = window.all_text()
            self.assertIn(label, text)
            self.assertTrue(
                any(0x2800 <= ord(char) <= 0x28FF for char in text)
                or any(char in "█▏▎▍▌▋▊▉" for char in text),
                f"{mode} ne dessine aucun caractère graphique",
            )

    def test_bordure_haute_ne_clignote_pas_avec_le_beat(self) -> None:
        """La bordure supérieure doit garder le même attribut quel que soit le beat."""
        attributes = []
        for beat in (0.0, 0.5, 1.0):
            window = FakeWindow(24, 80)
            Renderer(window).draw(make_frame(beat=beat, ts=0.0))
            top = [call for call in window.calls if call[0] == 0 and call[1] == 0]
            self.assertEqual(len(top), 1)
            attributes.append((top[0][2], top[0][3]))
        self.assertEqual(attributes[0], attributes[1])
        self.assertEqual(attributes[1], attributes[2])

    def test_pas_de_remanence_entre_frames_et_modes(self) -> None:
        """Le spectre doit être repeint intégralement à chaque frame/mode."""
        layout = compute_layout(120, 40)
        rect = layout.spectrum
        window = FakeWindow(40, 120)
        renderer = Renderer(window, visual="vertical")
        renderer.draw(make_frame(ts=1.0))  # vue verticale pleine de blocs
        renderer.set_visual("radial")
        renderer.draw(make_frame(ts=1.0))

        # La même frame rendue sur un écran vierge doit donner exactement
        # le même résultat : si des barres verticales traînaient, ça différerait.
        reference = FakeWindow(40, 120)
        Renderer(reference, visual="radial").draw(make_frame(ts=1.0))
        for row in range(rect.y, rect.bottom):
            self.assertEqual(
                window.row_text(row),
                reference.row_text(row),
                f"rémanence détectée ligne {row}",
            )
        # Et en particulier plus aucun bloc de barre verticale dans la zone.
        spectrum_text = "".join(
            window.row_text(row)[rect.x : rect.right] for row in range(rect.y, rect.bottom)
        )
        for stale in "█▏▎▍▌▋▊▉│":
            self.assertNotIn(stale, spectrum_text, f"caractère résiduel {stale!r}")

        # Même vérification en passant de radial à tunnel.
        renderer.set_visual("tunnel")
        renderer.draw(make_frame(ts=1.0))
        reference = FakeWindow(40, 120)
        Renderer(reference, visual="tunnel").draw(make_frame(ts=1.0))
        for row in range(rect.y, rect.bottom):
            self.assertEqual(window.row_text(row), reference.row_text(row), f"rémanence tunnel ligne {row}")

    def test_credit_en_bas(self) -> None:
        for width, height in ((120, 40), (80, 24), (40, 12)):
            window = FakeWindow(height, width)
            Renderer(window).draw(make_frame())
            self.assertIn(CREDIT, window.row_text(height - 1), f"{width}x{height}")
            self.assertEqual(window.violations, [])
        # Hors cadre (terminal minuscule), la signature est tronquée mais visible.
        window = FakeWindow(6, 20)
        Renderer(window).draw(make_frame())
        self.assertIn("mist3r", window.row_text(5))
        self.assertEqual(window.violations, [])


# ---------------------------------------------------------------------------
# Import / contrat publics
# ---------------------------------------------------------------------------
class TestReactive(unittest.TestCase):
    def test_palette_reactive_sans_depassement(self) -> None:
        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        base_gradient = renderer._gradient
        self.assertFalse(renderer.reactive)
        self.assertTrue(renderer.set_reactive(True))
        for mode in VISUAL_MODES:
            renderer.set_visual(mode)
            renderer.draw(make_frame(bass=0.9, mid=0.6, treble=0.4, beat=0.8, ts=2.0))
            self.assertEqual(renderer.last_error, "", mode)
            self.assertEqual(window.violations, [], mode)
        # Le rendu réactif ne doit pas altérer la palette de base persistante.
        self.assertEqual(renderer.theme_name, PRIMARY_THEME)
        self.assertEqual(renderer._gradient, base_gradient)
        self.assertFalse(renderer.set_reactive(False))

    def test_palette_reactive_varie_dans_le_temps(self) -> None:
        window = FakeWindow(24, 80)
        renderer = Renderer(window)
        base = renderer.theme
        early = renderer._reactive_variant(base, make_frame(bass=0.5, mid=0.4, treble=0.3, ts=0.0))
        later = renderer._reactive_variant(base, make_frame(bass=0.5, mid=0.4, treble=0.3, ts=22.0))
        self.assertNotEqual(early.accent, later.accent)
        self.assertNotEqual(early.spectrum_mid, later.spectrum_mid)


class TestMenuRendu(unittest.TestCase):
    def test_menu_principal(self) -> None:
        window = FakeWindow(24, 80)
        renderer = Renderer(window)
        menu = MenuState()
        menu.open_main()
        renderer.draw(make_frame(), menu)
        text = window.all_text()
        self.assertIn("MENU", text)
        self.assertIn("Stations", text)
        self.assertIn("Esc", text)
        self.assertEqual(window.violations, [])
        self.assertEqual(renderer.last_error, "")

    def test_selecteur_stations_avec_filtre(self) -> None:
        window = FakeWindow(40, 120)
        renderer = Renderer(window)
        menu = MenuState()
        menu.open_stations()
        for char in "fip":
            menu.append_char(char)
        renderer.draw(make_frame(), menu)
        text = window.all_text()
        self.assertIn("STATIONS", text)
        self.assertIn("FIP", text)
        self.assertIn("Filter", text)
        self.assertEqual(window.violations, [])

    def test_selecteur_et_menu_petits_ecrans(self) -> None:
        for width, height in ((40, 12), (80, 24), (200, 60), (24, 7)):
            window = FakeWindow(height, width)
            renderer = Renderer(window)
            menu = MenuState()
            menu.open_stations()
            menu.append_char("f")
            renderer.draw(make_frame(), menu)
            self.assertEqual(window.violations, [], f"{width}x{height}")
            renderer2 = Renderer(window)
            main = MenuState()
            main.open_main()
            renderer2.draw(make_frame(), main)
            self.assertEqual(window.violations, [], f"main {width}x{height}")

    def test_logo_ascii_dans_le_menu(self) -> None:
        window = FakeWindow(30, 110)
        renderer = Renderer(window)
        menu = MenuState()
        menu.open_main()
        renderer.draw(make_frame(), menu)
        text = window.all_text()
        self.assertIn("█▀█", text)
        self.assertIn("RADIO", text.upper())
        self.assertEqual(window.violations, [])

    def test_menu_affiche_etats_auto_reactif(self) -> None:
        window = FakeWindow(24, 90)
        renderer = Renderer(window)
        menu = MenuState()
        menu.open_main()
        renderer.draw(make_frame(), menu)
        text = window.all_text()
        self.assertIn("Auto change", text)
        self.assertIn("Reactive colors", text)
        self.assertIn("OFF", text)
        menu.auto_visual = True
        menu.reactive = True
        renderer.draw(make_frame(), menu)
        self.assertIn("ON", window.all_text())

    def test_selection_mise_en_evidence(self) -> None:
        window = FakeWindow(24, 80)
        renderer = Renderer(window)
        menu = MenuState()
        menu.open_stations()
        renderer.draw(make_frame(), menu)
        self.assertIn("▶", window.all_text())


class TestContratPublic(unittest.TestCase):
    def test_api_imposee(self) -> None:
        self.assertTrue(callable(Renderer))
        self.assertTrue(callable(Renderer.draw))
        self.assertTrue(callable(Renderer.set_theme))
        self.assertTrue(callable(Renderer.cycle_theme))
        self.assertIsInstance(THEME_NAMES, tuple)
        self.assertIsInstance(PRIMARY_THEME, str)

    def test_imports_sans_curses_initialise(self) -> None:
        # Aucun test n'a appelé initscr : ces appels ne doivent jamais lever.
        layout = compute_layout(80, 24)
        self.assertIsInstance(layout, Layout)
        self.assertIsInstance(layout.spectrum, Rect)
        self.assertEqual(len(resample((0.5,) * BAND_COUNT, 80)), 80)
        ColorManager().init()

    def test_taille_frame_respectee(self) -> None:
        frame = make_frame().sanitized()
        self.assertEqual(len(frame.bars), BAND_COUNT)
        self.assertEqual(len(frame.peaks), BAND_COUNT)
        self.assertEqual(len(frame.wave), WAVE_POINTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
