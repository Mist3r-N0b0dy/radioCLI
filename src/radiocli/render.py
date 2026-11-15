"""Rendu terminal du visualiseur radioCLI (curses, stdlib uniquement).

Le module expose l'API utilisée par ``app.py`` ::

    from .render import Renderer, THEME_NAMES, PRIMARY_THEME
    renderer = Renderer(stdscr, theme=PRIMARY_THEME)
    renderer.draw(frame)          # dessine TOUT puis stdscr.refresh()
    renderer.set_theme("dub")     # change de thème
    nom = renderer.cycle_theme()  # thème suivant, renvoie son nom

``draw()`` ne lève jamais d'exception : toute erreur d'affichage est absorbée
(``Renderer.last_error`` garde la trace pour le débogage du harnais).

Le module sépare volontairement :
    * des helpers **purs** (sans curses) : découpage géométrique, ré-
      échantillonnage, braille, marquee, formatage... testables sans terminal ;
    * la classe :class:`Renderer` qui ne fait que du dessin.
"""

from __future__ import annotations

import colorsys
import curses
import locale
import math
import traceback
from dataclasses import dataclass, replace

from .frame import Frame
from .menu import MAIN_ITEMS, PAGE_STATIONS, MenuState, filter_stations
from .themes import (
    PRIMARY_THEME,
    THEMES,
    THEME_ALIASES,
    THEME_NAMES,
    ColorManager,
    Theme,
    gradient_level,
    gradient_steps,
    nearest_xterm,
)

# ---------------------------------------------------------------------------
# Constantes de dessin
# ---------------------------------------------------------------------------
#: Blocs partiels Unicode (1/8 -> 8/8), utilisés pour le haut des barres.
PARTIAL_BLOCKS = "▁▂▃▄▅▆▇█"
FULL_BLOCK = "█"
PEAK_CAP = "▔"
#: Blocs partiels horizontaux (1/8 -> 8/8) pour l'extrémité des barres.
LEFT_PARTIAL_BLOCKS = "▏▎▍▌▋▊▉█"
#: Marqueur de crête sur un spectre vertical.
PEAK_VERTICAL = "▏"

#: Modes de visualisation du spectre (ordre du cycle de la touche ``v``).
VISUAL_MODES = (
    "bars",
    "vertical",
    "radial",
    "tunnel",
    "sphere",
    "carpet",
    "geodesic",
    "rosace_live",
    "mandala",
    "rosace",
    "snowflake",
    "galaxy",
)
#: Labels shown in the header and status messages.
VISUAL_LABELS = {
    "bars": "bars",
    "vertical": "vertical",
    "radial": "radial",
    "tunnel": "tunnel",
    "sphere": "sphere",
    "carpet": "carpet",
    "geodesic": "geodesic sphere",
    "rosace_live": "reactive rosace",
    "mandala": "mandala",
    "rosace": "rosace",
    "snowflake": "snowflake",
    "galaxy": "galaxy",
}
#: Alias tolérants acceptés par ``set_visual`` / ``--visual``.
VISUAL_ALIASES = {
    "barres": "bars",
    "bar": "bars",
    "classique": "bars",
    "spectre": "bars",
    "verticale": "vertical",
    "colonnes": "vertical",
    "circulaire": "radial",
    "cercle": "radial",
    "polaire": "radial",
    "perspective": "tunnel",
    "wormhole": "tunnel",
    "sphère": "sphere",
    "boule": "sphere",
    "globe": "sphere",
    "tapis": "carpet",
    "tapis volant": "carpet",
    "tapis-volant": "carpet",
    "geodesique": "geodesic",
    "sphère géodésique": "geodesic",
    "flocon": "snowflake",
    "koch": "snowflake",
    "rosace-reactive": "rosace_live",
    "reactive-rosace": "rosace_live",
    "rosace2": "rosace_live",
    "fractale": "mandala",
    "mandala2": "rosace",
    "flat-fractal": "snowflake",
    "fractale-plane": "snowflake",
    "galaxie": "galaxy",
    "spirale": "galaxy",
}
#: Mode de visualisation par défaut.
PRIMARY_VISUAL = "bars"

#: Signature affichée en bas de l'écran.
CREDIT = "Build by mist3r_n0b0dy"

#: Logo ASCII art affiché en haut du menu principal.
_MENU_LOGO = (
    "█▀█ ▄▀█ █▀▄ █ █▀█   █▀▀ █   █",
    "█▀▄ █▀█ █▄▀ █ █▄█   █▄▄ █▄▄ █",
    "▀ ▀ ▀ ▀ ▀▀  ▀ ▀▀▀   ▀▀▀ ▀▀▀ ▀",
)

#: Dots braille : ``_BRAILLE_DOTS[dx][dy]`` pour une cellule 2x4 points.
_BRAILLE_DOTS = (
    (0x01, 0x02, 0x04, 0x40),
    (0x08, 0x10, 0x20, 0x80),
)

_LIVE_TEXT = "LIVE"
_OFFLINE_TEXT = "OFFLINE"

_HINTS = (
    "(q)uit m(u)te +/- vol (t)heme (v)isual (m)enu (s)tation (S)genre (r)ecord",
    "(q)uit m(u)te +/- vol (t)heme (v)isual (m)enu (s)tation (S)genre",
    "(q)uit m(u)te +/- vol (t)heme (v)isual (m)enu (s)tation",
    "(q)uit m(u)te (t)heme (v)isual (m)enu",
    "(q)uit (m)enu",
    "(q)uit",
)


@dataclass(frozen=True)
class Rect:
    """Rectangle terminal en cellules (x, y, largeur, hauteur)."""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass(frozen=True)
class Layout:
    """Découpage complet de l'écran pour une taille donnée."""

    width: int
    height: int
    framed: bool
    mode: str
    inner: Rect
    header: Rect | None
    spectrum: Rect
    osc: Rect | None
    vu: Rect | None
    title: Rect | None
    meta: Rect | None
    hints: Rect | None
    banner: Rect | None
    rules: tuple[tuple[int, str], ...]

    def rects(self) -> tuple[Rect, ...]:
        candidates = (
            self.header,
            self.spectrum,
            self.osc,
            self.vu,
            self.title,
            self.meta,
            self.hints,
            self.banner,
        )
        return tuple(rect for rect in candidates if rect is not None)


def normalize_visual(name: object) -> str:
    key = str(name or "").strip().lower()
    key = VISUAL_ALIASES.get(key, key)
    return key if key in VISUAL_MODES else ""


def compute_layout(width: int, height: int) -> Layout:
    w = max(1, int(width))
    h = max(1, int(height))
    framed = w >= 34 and h >= 10
    if framed:
        inner_x, inner_y = 1, 1
        inner_w, inner_h = max(1, w - 2), max(1, h - 2)
        hints_h = 0
    else:
        inner_x, inner_y = 0, 0
        hints_h = 1 if h >= 4 else 0
        inner_w, inner_h = w, max(1, h - hints_h)
    body = inner_h
    if body >= 20:
        mode = "full"
    elif body >= 14:
        mode = "medium"
    elif body >= 8:
        mode = "compact"
    else:
        mode = "tiny"

    inner = Rect(inner_x, inner_y, inner_w, inner_h)
    header: Rect | None = None
    spectrum: Rect
    osc: Rect | None = None
    vu: Rect | None = None
    title: Rect | None = None
    meta: Rect | None = None
    hints: Rect | None = None
    rules: list[tuple[int, str]] = []
    banner_rows = 2 if framed and body >= 18 else 0
    banner = Rect(inner_x, inner_y, inner_w, banner_rows) if banner_rows else None
    y = inner_y + banner_rows

    if mode == "full":
        osc_h = 3 if body >= 26 else 2
        header = Rect(inner_x, y, inner_w, 1)
        y += 1
        rules.append((y, "SPECTRUM"))
        y += 1
        spectrum = Rect(inner_x, y, inner_w, max(1, body - (10 + osc_h + banner_rows)))
        y += spectrum.h
        rules.append((y, "OSCILLOSCOPE"))
        y += 1
        osc = Rect(inner_x, y, inner_w, osc_h)
        y += osc_h
        rules.append((y, "LEVELS"))
        y += 1
        vu = Rect(inner_x, y, inner_w, 3)
        y += 3
        rules.append((y, "NOW PLAYING"))
        y += 1
        title = Rect(inner_x, y, inner_w, 1)
        y += 1
        meta = Rect(inner_x, y, inner_w, 1)
        y += 1
    elif mode == "medium":
        header = Rect(inner_x, y, inner_w, 1)
        y += 1
        rules.append((y, "SPECTRUM"))
        y += 1
        spectrum = Rect(inner_x, y, inner_w, max(1, body - (9 + banner_rows)))
        y += spectrum.h
        rules.append((y, "OSCILLOSCOPE"))
        y += 1
        osc = Rect(inner_x, y, inner_w, 1)
        y += 1
        rules.append((y, "LEVELS"))
        y += 1
        vu = Rect(inner_x, y, inner_w, 1)
        y += 1
        rules.append((y, "NOW PLAYING"))
        y += 1
        title = Rect(inner_x, y, inner_w, 1)
        y += 1
        meta = Rect(inner_x, y, inner_w, 1)
        y += 1
    elif mode == "compact":
        meta_h = 1 if body >= 10 else 0
        header = Rect(inner_x, y, inner_w, 1)
        y += 1
        rules.append((y, "SPECTRUM"))
        y += 1
        spectrum = Rect(inner_x, y, inner_w, max(1, body - (4 + meta_h + banner_rows)))
        y += spectrum.h
        vu = Rect(inner_x, y, inner_w, 1)
        y += 1
        title = Rect(inner_x, y, inner_w, 1)
        y += 1
        if meta_h:
            meta = Rect(inner_x, y, inner_w, 1)
            y += 1
    else:  # tiny
        if body >= 3:
            has_header, has_title = 1, 1
        elif body == 2:
            has_header, has_title = 1, 0
        else:
            has_header, has_title = 0, 0
        if has_header:
            header = Rect(inner_x, y, inner_w, 1)
            y += 1
        spectrum = Rect(inner_x, y, inner_w, max(1, body - has_header - has_title))
        y += spectrum.h
        if has_title:
            title = Rect(inner_x, y, inner_w, 1)
            y += 1

    if not framed and hints_h:
        hints = Rect(inner_x, inner_y + body, inner_w, 1)

    return Layout(
        width=w,
        height=h,
        framed=framed,
        mode=mode,
        inner=inner,
        header=header,
        spectrum=spectrum,
        osc=osc,
        vu=vu,
        title=title,
        meta=meta,
        hints=hints,
        banner=banner,
        rules=tuple(rules),
    )


def _iter_runs(text: str):
    """Regroupe les caractères identiques consécutifs : (début, motif)."""
    if not text:
        return
    start = 0
    for index in range(1, len(text) + 1):
        if index == len(text) or text[index] != text[start]:
            yield start, text[start:index]
            start = index


def clamp01(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return 0.0 if number <= 0.0 else 1.0 if number >= 1.0 else number


def resample(values: object, width: int) -> tuple[float, ...]:
    count = int(width)
    if count <= 0:
        return ()
    try:
        source = tuple(float(value) for value in values)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        source = ()
    if not source:
        return (0.0,) * count
    if len(source) == count:
        return tuple(clamp01(value) for value in source)
    result: list[float] = []
    length = len(source)
    for index in range(count):
        start = int(index * length / count)
        end = max(start + 1, int((index + 1) * length / count))
        result.append(max(clamp01(value) for value in source[start:end]))
    return tuple(result)


def truncate_text(text: object, width: int) -> str:
    value = str(text or "")
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return value[:1]
    return value[: width - 1] + "…"


def marquee_view(text: object, width: int, timestamp: object, speed: float = 6.0) -> str:
    value = str(text or "")
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    try:
        ts = float(timestamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        ts = 0.0
    padded = value + "   "
    offset = int(max(0.0, ts) * max(0.1, speed)) % len(padded)
    return (padded + value)[offset : offset + width]


def format_clock(seconds: object) -> str:
    try:
        total = max(0, int(float(seconds)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_rec(seconds: object, timestamp: object = 0.0) -> str:
    dot = "●" if _pulse_state(timestamp, 1.1, 0.55) else "○"
    return f"REC {dot} {format_clock(seconds)}"


def format_volume(volume: object, muted: bool = False) -> str:
    if muted:
        return "muted"
    if volume is None:
        return ""
    try:
        value = int(round(float(volume)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return f"vol {max(0, min(100, value))}%"


def format_listeners(value: object) -> str:
    if value is None:
        return ""
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if number < 0:
        return ""
    if number >= 10_000:
        return f"{number / 1000:.0f} k listeners"
    return f"{number} listeners"


def hints_text(width: int) -> str:
    for hint in _HINTS:
        if len(hint) <= width:
            return hint
    return ""


def bar_eighths(value: object, height: int) -> int:
    if height <= 0:
        return 0
    return int(round(clamp01(value) * height * 8))


def wave_to_braille(wave: object, width: int, height: int) -> tuple[str, ...]:
    columns = max(0, int(width))
    rows = max(0, int(height))
    if columns == 0 or rows == 0:
        return tuple("" for _ in range(rows))
    try:
        values = tuple(float(value) for value in wave)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        values = ()
    if not values:
        return tuple(" " * columns for _ in range(rows))
    samples = resample(tuple(abs(value) if value != value else 0.0 for value in values), columns)
    if max(samples) <= 1e-9:
        flat = [" " * columns for _ in range(rows)]
        if rows:
            flat[-1] = "⠉" * columns
        return tuple(flat)
    dots = [0] * (columns * rows)
    for index, sample in enumerate(samples):
        level = int(round((1.0 - clamp01(sample)) * (rows - 1)))
        level = max(0, min(rows - 1, level))
        dots[level * columns + index] = 0x40
    result: list[str] = []
    for row in range(rows):
        chars = []
        for column in range(columns):
            chars.append(chr(0x2800 + dots[row * columns + column]))
        result.append("".join(chars))
    return tuple(result)


def _pulse_state(timestamp: object, speed: float = 0.9, threshold: float = 0.68) -> bool:
    try:
        ts = float(timestamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        ts = 0.0
    return (ts * speed) % 1.0 < threshold


class BrailleCanvas:
    """Canevas de points braille (2x4 points par cellule) pour les visuels courbes.

    Chaque cellule ne peut avoir qu'une couleur : la dernière écriture gagne.
    ``render()`` regroupe les cellules consécutives de même couleur en une seule
    écriture curses (performance et pas de scintillement).
    """

    __slots__ = ("width", "height", "_dots", "_colors")

    def __init__(self, width: int, height: int) -> None:
        self.width = max(0, int(width))
        self.height = max(0, int(height))
        self._dots = [0] * (self.width * self.height)
        self._colors = [0] * (self.width * self.height)

    # ------------------------------------------------------------- primitives
    def plot(self, x: float, y: float, color: int) -> None:
        """Allume un point (coordonnées en points braille, pas en cellules)."""
        if self.width <= 0 or self.height <= 0:
            return
        dx = int(round(x))
        dy = int(round(y))
        if dx < 0 or dy < 0 or dx >= self.width * 2 or dy >= self.height * 4:
            return
        cell = (dy >> 2) * self.width + (dx >> 1)
        self._dots[cell] |= _BRAILLE_DOTS[dx & 1][dy & 3]
        self._colors[cell] = int(color)

    def fill_cell(self, cell_x: int, cell_y: int, color: int) -> None:
        """Remplit toute une cellule braille (face opaque texturée)."""
        if cell_x < 0 or cell_y < 0 or cell_x >= self.width or cell_y >= self.height:
            return
        cell = cell_y * self.width + cell_x
        self._dots[cell] = 0xFF
        self._colors[cell] = int(color)

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        color: int,
    ) -> None:
        """Trace un segment (échantillonnage au pas maximal, sans trou)."""
        delta_x = x1 - x0
        delta_y = y1 - y0
        steps = int(max(abs(delta_x), abs(delta_y))) + 1
        if steps <= 1:
            self.plot(x0, y0, color)
            return
        for index in range(steps + 1):
            t = index / steps
            self.plot(x0 + delta_x * t, y0 + delta_y * t, color)

    def ring(
        self,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        color: int,
        steps: int | None = None,
        values: object = None,
        wobble: float = 0.0,
    ) -> None:
        """Trace une ellipse (éventuellement déformée par un spectre)."""
        if rx <= 0 or ry <= 0:
            return
        if steps is None:
            steps = max(16, int(2.0 * math.pi * max(rx, ry) / 2.5))
        steps = max(8, int(steps))
        try:
            wave = tuple(values) if values is not None else ()
        except TypeError:
            wave = ()
        count = len(wave)
        previous: tuple[float, float] | None = None
        for index in range(steps + 1):
            angle = -math.pi / 2.0 + 2.0 * math.pi * index / steps
            scale = 1.0
            if count:
                amplitude = wave[int(index / steps * count) % count]
                scale = 1.0 + wobble * (float(amplitude) - 0.5)
            x = cx + math.cos(angle) * rx * scale
            y = cy + math.sin(angle) * ry * scale
            if previous is not None:
                self.line(previous[0], previous[1], x, y, color)
            previous = (x, y)

    # ---------------------------------------------------------------- rendu
    def render(self, renderer: "Renderer", rect: Rect) -> None:
        """Peint le canevas dans ``rect`` via les primitives du Renderer."""
        if self.width <= 0 or self.height <= 0:
            return
        for cell_y in range(self.height):
            row = cell_y * self.width
            cell_x = 0
            while cell_x < self.width:
                color = self._colors[row + cell_x]
                if self._dots[row + cell_x] == 0 and (
                    cell_x + 1 >= self.width or self._dots[row + cell_x + 1] == 0
                ):
                    cell_x += 1
                    continue
                run = cell_x
                chars: list[str] = []
                while cell_x < self.width and self._colors[row + cell_x] == color:
                    mask = self._dots[row + cell_x]
                    chars.append(chr(0x2800 + mask) if mask else " ")
                    cell_x += 1
                text = "".join(chars).rstrip()
                if text:
                    renderer._put(rect.y + cell_y, rect.x + run, text, renderer._p(color))


# ---------------------------------------------------------------------------
# Rendu curses
# ---------------------------------------------------------------------------
class Renderer:
    """Dessine une :class:`~radiocli.frame.Frame` en plein écran curses.

    L'API est volontairement minimaliste : ``draw``, ``set_theme``,
    ``cycle_theme``. Aucune touche ni gestion de resize ici (c'est le rôle de
    ``app.py``) ; :meth:`draw` recalcule la géométrie à chaque appel.
    """

    def __init__(
        self,
        stdscr,
        theme: str = PRIMARY_THEME,
        visual: str = PRIMARY_VISUAL,
    ) -> None:
        self.stdscr = stdscr
        # Important : curses a besoin de la locale UTF-8 pour nos blocs Unicode.
        try:
            locale.setlocale(locale.LC_ALL, "")
        except Exception:  # pragma: no cover - environnement exotique
            pass
        self.colors = ColorManager()
        self.colors.init()
        self._attr_cache: dict[int, int] = {}
        self._maxyx: tuple[int, int] = (0, 0)
        self._layout: Layout | None = None
        self._layout_key: tuple[int, int] | None = None
        self.last_error: str = ""
        self._base_theme: Theme = THEMES[PRIMARY_THEME]
        self._theme: Theme = self._base_theme
        self._theme_name: str = PRIMARY_THEME
        self._gradient: tuple[int, ...] = gradient_steps(self._theme)
        self._reactive: bool = False
        self.set_theme(theme)
        self._visual: str = PRIMARY_VISUAL
        self.set_visual(visual)

    # ------------------------------------------------------------- API thème
    @property
    def theme_name(self) -> str:
        """Nom du thème actif."""
        return self._theme_name

    @property
    def theme(self) -> Theme:
        """Palette de base active (utile au harnais / au débogage)."""
        return self._base_theme

    def set_theme(self, name: str) -> str:
        """Active le thème ``name`` (nom inconnu -> thème primaire)."""
        key = str(name or "").strip().lower()
        key = THEME_ALIASES.get(key, key)
        if key not in THEMES:
            key = PRIMARY_THEME
        self._theme_name = key
        self._base_theme = THEMES[key]
        self._theme = self._base_theme
        self._gradient = gradient_steps(self._theme)
        return key

    def cycle_theme(self) -> str:
        """Passe au thème suivant de THEME_NAMES et renvoie son nom."""
        names = THEME_NAMES
        try:
            index = names.index(self._theme_name)
        except ValueError:  # pragma: no cover - thème toujours valide
            index = -1
        return self.set_theme(names[(index + 1) % len(names)])

    # ----------------------------------------------------------- API visuelle
    @property
    def visual_name(self) -> str:
        """Nom du mode de visualisation actif (``bars``, ``radial``…)."""
        return self._visual

    def set_visual(self, name: str) -> str:
        """Active un mode de visualisation (nom inconnu -> mode primaire)."""
        key = normalize_visual(name) or PRIMARY_VISUAL
        self._visual = key
        return key

    def cycle_visual(self) -> str:
        """Passe au mode de visualisation suivant et renvoie son nom."""
        try:
            index = VISUAL_MODES.index(self._visual)
        except ValueError:  # pragma: no cover - mode toujours valide
            index = -1
        return self.set_visual(VISUAL_MODES[(index + 1) % len(VISUAL_MODES)])

    # ---------------------------------------------------------- API réactive
    @property
    def reactive(self) -> bool:
        """Couleurs réactives à la musique activées ou non."""
        return self._reactive

    def set_reactive(self, enabled: bool) -> bool:
        """Active/désactive la palette pilotée par l'audio du moment."""
        self._reactive = bool(enabled)
        return self._reactive

    def _reactive_variant(self, base: Theme, frame: Frame) -> Theme:
        """Décline le thème dans une palette qui parcourt toutes les teintes."""
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        bass = clamp01(getattr(frame, "bass", 0.0))
        mid = clamp01(getattr(frame, "mid", 0.0))
        treble = clamp01(getattr(frame, "treble", 0.0))
        beat = clamp01(getattr(frame, "beat", 0.0))
        energy = 0.50 * bass + 0.28 * mid + 0.22 * treble

        # Rotation lente sur la roue chromatique + inflexions chaudes/froides :
        # la palette ne reste jamais bloquée dans les mauves.
        hue = (
            0.02
            + timestamp * 0.035
            + 0.20 * bass
            + 0.14 * treble
            + 0.05 * math.sin(timestamp * 0.23)
        ) % 1.0
        saturation = 0.62 + 0.30 * energy
        value = 0.82 + 0.16 * beat

        def color(h: float, saturation_scale: float = 1.0, value_scale: float = 1.0) -> int:
            r, g, b = colorsys.hsv_to_rgb(
                h % 1.0,
                max(0.0, min(1.0, saturation * saturation_scale)),
                max(0.0, min(1.0, value * value_scale)),
            )
            return nearest_xterm((r * 255.0, g * 255.0, b * 255.0))

        return replace(
            base,
            header_bg=color(hue, 0.85, 0.20),
            header_fg=color(hue + 0.50, 0.12, 1.0),
            accent=color(hue + 0.50),
            border=color(hue, 0.60, 0.50),
            spectrum_low=color(hue, 1.0, 0.70),
            spectrum_mid=color(hue + 0.10, 1.0, 0.95),
            spectrum_high=color(hue + 0.22, 0.95, 1.0),
            wave=color(hue + 0.12),
            title=color(hue + 0.58, 0.85, 1.0),
            live_bg=color(hue + 0.50, 0.85, 0.90),
            demo_bg=color(hue + 0.66, 0.85, 0.95),
            overlay_bg=color(hue, 0.85, 0.18),
        )

    # --------------------------------------------------------------- entrées
    def draw(self, frame: Frame, menu: MenuState | None = None) -> None:
        """Dessine toute l'interface (+ menu modal optionnel). Ne lève jamais."""
        self.last_error = ""
        base = self._base_theme
        try:
            # Le réactif ne s'applique qu'à la zone spectre (voir
            # _draw_spectrum), jamais au reste de l'interface.
            self._theme = base
            self._gradient = gradient_steps(base)
            self._draw_frame(frame)
            if menu is not None and menu.open:
                self._draw_menu(menu)
        except Exception:
            self.last_error = traceback.format_exc()
            try:
                self._draw_fallback()
            except Exception:
                pass
        finally:
            try:
                self.stdscr.refresh()
            except Exception:
                pass
            self._theme = base
            self._gradient = gradient_steps(base)

    # ------------------------------------------------------------ outillage
    def _p(self, fg: int, bg: int | None = None) -> int:
        """Identifiant de paire de couleurs (0 = défaut)."""
        return self.colors.pair(fg, bg)

    def _attr(self, pair: int, attr: int = 0) -> int:
        """Combine un attribut curses et une paire, sans risquer d'exception."""
        if pair <= 0 or self.colors.mono:
            return attr
        cached = self._attr_cache.get(pair)
        if cached is None:
            try:
                cached = curses.color_pair(pair)
            except Exception:
                cached = 0
            self._attr_cache[pair] = cached
        return attr | cached

    def _dim_attr(self) -> int:
        """En mono, un attribut ``A_DIM`` remplace la couleur discrète."""
        return curses.A_DIM if self.colors.mono else 0

    def _text_dim_pair(self, bg: int | None = None) -> int:
        """Paire du texte secondaire : gris clair, volontairement contrasté."""
        return self._p(self._theme.text_dim, bg)

    def _gradient_color(self, position: float) -> int:
        """Couleur du dégradé du thème à une position 0->1."""
        return self._gradient[gradient_level(clamp01(position), len(self._gradient))]

    def _badge_attr(self) -> int:
        """En mono, les badges passent en vidéo inverse pour rester lisibles."""
        return curses.A_REVERSE if self.colors.mono else 0

    def _put(self, y: int, x: int, text: object, pair: int = 0, attr: int = 0) -> None:
        """Écrit ``text`` en ``(y, x)``, rogné aux bornes de l'écran."""
        if not text:
            return
        max_y, max_x = self._maxyx
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        value = str(text)
        available = max_x - x
        if available <= 0:
            return
        if len(value) > available:
            value = value[:available]
        if not value:
            return
        try:
            self.stdscr.addstr(y, x, value, self._attr(pair, attr))
        except Exception:
            # Terminal exotique, caractère non encodable... on ignore la cellule.
            pass

    def _clear_row(self, y: int, x: int, width: int) -> None:
        if width > 0:
            self._put(y, x, " " * width, 0)

    def _clear_rect(self, rect: Rect) -> None:
        for row in range(rect.h):
            self._clear_row(rect.y + row, rect.x, rect.w)

    def _sanitize(self, frame: object) -> Frame:
        """Ramène la frame à un contrat sûr, même si elle est incomplète."""
        try:
            if hasattr(frame, "sanitized"):
                candidate = frame.sanitized()  # type: ignore[union-attr]
                if isinstance(candidate, Frame):
                    return candidate
        except Exception:
            pass
        if isinstance(frame, Frame):
            return frame
        try:
            return Frame()
        except Exception:  # pragma: no cover - Frame est toujours constructible
            return frame  # type: ignore[return-value]

    @staticmethod
    def _pulse(ts: object, speed: float = 0.9) -> bool:
        return _pulse_state(ts, speed, 0.68)

    # ------------------------------------------------------------ orchestration
    def _draw_frame(self, frame: object) -> None:
        try:
            size = self.stdscr.getmaxyx()
        except Exception:
            return
        if not size or len(size) < 2:
            return
        height, width = int(size[0]), int(size[1])
        if height <= 0 or width <= 0:
            return
        self._maxyx = (height, width)
        if self._layout_key != (width, height):
            self._layout = compute_layout(width, height)
            self._layout_key = (width, height)
            try:
                self.stdscr.clear()
            except Exception:
                pass
        layout = self._layout
        if layout is None:  # pragma: no cover - défensif
            return
        safe = self._sanitize(frame)
        self.colors.begin_frame()

        self._draw_border(layout)
        self._draw_rules(layout)
        self._draw_header(layout, safe)
        self._draw_spectrum(layout, safe)
        if layout.osc is not None:
            self._draw_scope(layout, safe)
        if layout.vu is not None:
            self._draw_vu(layout, safe)
        if layout.title is not None:
            self._draw_title(layout, safe)
        if layout.meta is not None:
            self._draw_meta(layout, safe)
        if layout.hints is not None:
            self._draw_hints(layout, safe)

    # ------------------------------------------------------------------ menu
    def _draw_menu(self, menu: MenuState) -> None:
        """Voile + panneau modal (menu principal ou sélecteur de stations)."""
        height, width = self._maxyx
        if height <= 0 or width <= 0:
            return
        theme = self._theme
        if width < 22 or height < 6:
            self._put(
                max(0, height // 2),
                0,
                truncate_text("MENU · Esc to close", width),
                self._p(theme.title),
                curses.A_BOLD,
            )
            return
        if menu.page == PAGE_STATIONS:
            self._draw_stations_menu(menu)
        else:
            self._draw_main_menu(menu)

    def _menu_panel(self, top: int, left: int, width: int, height: int, title: str) -> tuple[int, int]:
        """Dessine le cadre du menu et renvoie (x, largeur) de sa zone interne."""
        theme = self._theme
        bg = theme.header_bg
        fill = self._p(theme.overlay_fg, bg)
        border = self._p(theme.accent, bg)
        title_pair = self._p(theme.title, bg)
        for row in range(top, top + height):
            self._put(row, left, " " * width, fill)
        self._put(top, left, "╭" + "─" * (width - 2) + "╮", border)
        visible_title = truncate_text(str(title), max(0, width - 6))
        if visible_title:
            self._put(top, left + 2, f" {visible_title} ", title_pair, curses.A_BOLD)
        for row in range(top + 1, top + height - 1):
            self._put(row, left, "│", border)
            self._put(row, left + width - 1, "│", border)
        self._put(top + height - 1, left, "╰" + "─" * (width - 2) + "╯", border)
        return left + 2, width - 4

    def _draw_main_menu(self, menu: MenuState) -> None:
        height, width = self._maxyx
        theme = self._theme
        box_w = min(width - 6, 66)
        show_logo = box_w >= 38
        box_h = min(height - 2, 14 if show_logo else 10)
        show_logo = show_logo and box_h >= 13
        if box_h < 6:
            self._put(
                max(0, height // 2),
                0,
                truncate_text("MENU · Esc to close", width),
                self._p(theme.title),
                curses.A_BOLD,
            )
            return
        left = (width - box_w) // 2
        top = (height - box_h) // 2
        inner_x, inner_w = self._menu_panel(top, left, box_w, box_h, "MENU")
        fg = self._p(theme.overlay_fg, theme.header_bg)
        accent = self._p(theme.accent, theme.header_bg)
        dim = self._p(theme.text_dim, theme.header_bg)
        item_y = top + 2
        if show_logo:
            for logo_index, logo_line in enumerate(_MENU_LOGO):
                logo_x = inner_x + max(0, (inner_w - len(logo_line)) // 2)
                self._put(
                    top + 1 + logo_index,
                    logo_x,
                    logo_line,
                    self._p(self._gradient_color(0.55 + 0.18 * logo_index)),
                    curses.A_BOLD,
                )
            caption = "radioCLI"
            caption_x = inner_x + max(0, (inner_w - len(caption)) // 2)
            self._put(
                top + 1 + len(_MENU_LOGO),
                caption_x,
                caption,
                accent,
                curses.A_BOLD,
            )
            item_y = top + 2 + len(_MENU_LOGO) + 1
        for key, label, detail in MAIN_ITEMS:
            if item_y >= top + box_h - 2:
                break
            state = ""
            if key == "a":
                state = " · ON" if menu.auto_visual else " · OFF"
            elif key == "c":
                state = " · ON" if menu.reactive else " · OFF"
            self._put(item_y, inner_x, key, accent, curses.A_BOLD)
            text = truncate_text(
                f"{label}   {detail}{state}",
                max(1, inner_w - 4),
            )
            self._put(item_y, inner_x + 3, text, fg)
            item_y += 1
        footer = truncate_text(
            "(Esc)/(m) close · (a)uto · (c)olors",
            max(1, inner_w),
        )
        self._put(top + box_h - 2, inner_x, footer, dim)

    def _draw_stations_menu(self, menu: MenuState) -> None:
        height, width = self._maxyx
        theme = self._theme
        stations = menu.filtered()
        box_w = min(width - 4, 82)
        box_h = min(height - 2, 20)
        if box_h < 6:
            self._put(
                max(0, height // 2),
                0,
                truncate_text(
                    "STATIONS · filter: " + (menu.query or "type…") + " · Esc",
                    width,
                ),
                self._p(theme.title),
                curses.A_BOLD,
            )
            return
        left = (width - box_w) // 2
        top = (height - box_h) // 2
        title = f"STATIONS · {len(stations)}/{len(filter_stations(''))}"
        inner_x, inner_w = self._menu_panel(top, left, box_w, box_h, title)
        fg = self._p(theme.overlay_fg, theme.header_bg)
        dim = self._p(theme.text_dim, theme.header_bg)
        accent = self._p(theme.accent, theme.header_bg)
        title_pair = self._p(theme.title, theme.header_bg)
        selected_pair = self._p(theme.live_fg, theme.live_bg)

        # Ligne de filtre.
        if box_h >= 7:
            if menu.query:
                prefix = "Filter: "
                self._put(top + 1, inner_x, truncate_text(prefix, inner_w), dim)
                room = max(0, inner_w - len(prefix))
                self._put(
                    top + 1,
                    inner_x + min(len(prefix), inner_w),
                    truncate_text(menu.query + "▏", room),
                    title_pair,
                    curses.A_BOLD,
                )
            else:
                self._put(
                    top + 1,
                    inner_x,
                    truncate_text("Filter: type to filter…▏", inner_w),
                    dim,
                )
            self._put(top + 2, left, "├" + "─" * (box_w - 2) + "┤", self._p(theme.accent, theme.header_bg))

        content_top = top + 3
        content_height = box_h - 5
        if content_height > 0:
            rows = menu.rows()
            if not rows:
                self._put(
                    content_top,
                    inner_x,
                    truncate_text("No station matches this filter", inner_w),
                    accent,
                    curses.A_BOLD,
                )
            else:
                selected = menu.selected_row_index()
                start = max(0, min(selected - content_height // 2, len(rows) - content_height))
                current = menu.current_station()
                for offset in range(content_height):
                    index = start + offset
                    if index >= len(rows):
                        break
                    row = rows[index]
                    y = content_top + offset
                    if row.kind == "header":
                        self._put(
                            y,
                            inner_x,
                            truncate_text(row.label, inner_w),
                            dim,
                            curses.A_BOLD,
                        )
                    else:
                        is_selected = row.station is current
                        text = truncate_text(("▶ " if is_selected else "  ") + row.label, inner_w)
                        if is_selected:
                            self._put(y, inner_x, " " * inner_w, selected_pair)
                            self._put(y, inner_x, text, selected_pair, curses.A_BOLD)
                        else:
                            self._put(y, inner_x, text, fg)

        footer = truncate_text(
            "↑↓ scroll · Enter play · Esc back · type to filter",
            max(1, inner_w),
        )
        if box_h >= 8:
            self._put(top + box_h - 2, inner_x, footer, dim)

    def _draw_fallback(self) -> None:
        """Dessin minimal de secours si une exception a interrompu le rendu."""
        height, width = self._maxyx
        if width <= 0 or height <= 0:
            return
        for row in range(height):
            self._clear_row(row, 0, width)
        pair = self._p(self._theme.accent)
        self._put(0, 0, truncate_text("radioCLI · webradio visualizer", width), pair, curses.A_BOLD)

    # ---------------------------------------------------------------- chrome
    def _draw_border(self, layout: Layout) -> None:
        if not layout.framed:
            return
        width, height = layout.width, layout.height
        if width < 2 or height < 2:
            return
        theme = self._theme
        border_pair = self._p(theme.border)
        # Cadre extérieur en double ligne.
        inner = width - 2
        self._put(0, 0, "╔" + "═" * inner + "╗", border_pair)
        for row in range(1, height - 1):
            self._put(row, 0, "║", border_pair)
            self._put(row, width - 1, "║", border_pair)
        # Bandeau titre dédié, entouré par la double ligne.
        banner_y = layout.inner.y
        title = "radioCLI"
        title_x = max(1, (width - len(title)) // 2)
        if layout.banner is not None and width > len(title) + 4:
            self._put(
                banner_y + 1,
                1,
                " " * max(0, inner),
                self._p(theme.border),
            )
            self._put(
                banner_y,
                title_x,
                title,
                self._p(theme.title),
                curses.A_BOLD,
            )
            self._put(
                banner_y + 1,
                0,
                "╠" + "═" * inner + "╣",
                border_pair,
            )
        self._draw_bottom_border(layout)

    def _draw_bottom_border(self, layout: Layout) -> None:
        width, height = layout.width, layout.height
        theme = self._theme
        border_pair = self._p(theme.border)
        credit = CREDIT
        if width < len(credit) + 6:  # pragma: no cover - cadre >= 34 colonnes
            hint = hints_text(max(0, width - 6))
            if hint:
                fill = max(0, width - len(hint) - 6)
                self._put(height - 1, 0, "╚══ " + hint + " " + "═" * fill + "╝", border_pair)
            else:
                self._put(height - 1, 0, "╚" + "═" * (width - 2) + "╝", border_pair)
            return

        # On garde la signature en bas à droite ; les aides se raccourcissent
        # pour lui laisser la place.
        hint = hints_text(max(0, width - len(credit) - 8)) if width >= len(credit) + 14 else ""
        if hint:
            fill = width - len(hint) - len(credit) - 8
            x = 0
            self._put(height - 1, x, "╚══", border_pair)
            x += 3
            self._put(height - 1, x, " ", border_pair)
            x += 1
            self._put(height - 1, x, hint, self._p(theme.text_dim), self._dim_attr())
            x += len(hint)
            self._put(height - 1, x, " ", border_pair)
            x += 1
            self._put(height - 1, x, "═" * fill, border_pair)
            x += fill
            self._put(height - 1, x, " ", border_pair)
            x += 1
            self._put(height - 1, x, credit, self._p(theme.title))
            x += len(credit)
            self._put(height - 1, x, " ╝", border_pair)
        else:
            fill = max(0, width - len(credit) - 6)
            x = 0
            self._put(height - 1, x, "╚══", border_pair)
            x += 3
            self._put(height - 1, x, " ", border_pair)
            x += 1
            self._put(height - 1, x, credit, self._p(theme.title))
            x += len(credit)
            self._put(height - 1, x, " ", border_pair)
            x += 1
            self._put(height - 1, x, "═" * fill + "╝", border_pair)

    def _draw_rules(self, layout: Layout) -> None:
        theme = self._theme
        line_pair = self._p(theme.border)
        label_pair = self._p(theme.accent)
        for y, label in layout.rules:
            if y < 0 or y >= layout.height:
                continue
            if label == "SPECTRUM" and self._visual != PRIMARY_VISUAL:
                label = f"SPECTRUM · {VISUAL_LABELS[self._visual].upper()}"
            if layout.framed:
                x = 0
                self._put(y, x, "╠", line_pair)
                x += 1
                self._put(y, x, "═ ", line_pair)
                x += 2
                room = layout.width - x - 2  # place restante avant le coin
                if room < 1:  # pragma: no cover - très étroit
                    self._put(y, x, "═" * max(0, layout.width - x - 1), line_pair)
                else:
                    text = truncate_text(label, room)
                    self._put(y, x, text, label_pair, curses.A_BOLD)
                    x += len(text)
                    self._put(y, x, " ", line_pair)
                    x += 1
                    rest = layout.width - x - 1
                    if rest > 0:
                        self._put(y, x, "═" * rest, line_pair)
                self._put(y, layout.width - 1, "╣", line_pair)
            else:
                x = layout.inner.x
                room = layout.inner.w - 4
                if room < 1:
                    self._put(y, x, "─" * layout.inner.w, line_pair)
                    continue
                text = truncate_text(label, room)
                self._put(y, x, "─ ", line_pair)
                x += 2
                self._put(y, x, text, label_pair, curses.A_BOLD)
                x += len(text)
                self._put(y, x, " ", line_pair)
                x += 1
                self._put(y, x, "─" * max(0, layout.inner.x + layout.inner.w - x), line_pair)

    # ----------------------------------------------------------------- header
    def _draw_header(self, layout: Layout, frame: Frame) -> None:
        rect = layout.header
        if rect is None or rect.w <= 0:
            return
        self._clear_row(rect.y, rect.x, rect.w)
        theme = self._theme
        width = rect.w
        x0 = rect.x
        y = rect.y
        accent_pair = self._p(theme.accent)
        dim_pair = self._p(theme.text_dim)
        dim_attr = self._dim_attr()
        badge_attr = self._badge_attr()

        pulse = self._pulse(frame.ts)
        if frame.connected:
            live_text = ("● " if pulse else "○ ") + _LIVE_TEXT
            live_pair = self._p(theme.live_fg, theme.live_bg)
            live_attr = badge_attr
        else:
            live_text = "✕ " + _OFFLINE_TEXT
            live_pair = self._p(theme.vu_hot)
            live_attr = curses.A_BOLD
        station = str(frame.station or "radioCLI").strip() or "radioCLI"
        demo_text = "[ DEMO ]" if frame.demo else ""

        # --- partie droite : rec, horloge, débit, thème (les auditeurs sont
        # affichés dans la ligne méta, pas ici).
        right_specs: list[tuple[str, str, int, int]] = []
        if frame.recording:
            right_specs.append(
                ("rec", format_rec(frame.recording_seconds, frame.ts),
                 self._p(theme.rec_fg, theme.rec_bg), badge_attr)
            )
        right_specs.append(("clock", format_clock(frame.elapsed), dim_pair, dim_attr))
        if frame.bitrate:
            right_specs.append(("bitrate", str(frame.bitrate), dim_pair, dim_attr))
        right_specs.append(("theme", f"theme {theme.name}", dim_pair, dim_attr))
        right_specs.append(
            ("visual", f"visual {VISUAL_LABELS[self._visual]}", dim_pair, dim_attr)
        )

        def right_width(specs: list[tuple[str, str, int, int]]) -> int:
            if not specs:
                return 0
            return sum(len(text) for _, text, _, _ in specs) + 2 * (len(specs) - 1)

        def left_width() -> int:
            demo = len(demo_text) + 2 if demo_text else 0
            return 2 + len(station) + 1 + 2 + len(live_text) + demo

        # Priorité : station et badge direct toujours entiers. On retire
        # d'abord la démo, le débit, l'horloge puis le thème, et on ne rogne
        # la station qu'en tout dernier recours.
        for key in ("listeners", "demo", "bitrate", "visual", "clock", "theme"):
            if left_width() + right_width(right_specs) + 1 <= width:
                break
            if key == "listeners":  # déjà absent de l'en-tête
                continue
            if key == "demo":
                demo_text = ""
            else:
                right_specs = [spec for spec in right_specs if spec[0] != key]

        right_w = right_width(right_specs)
        allowed_left = max(0, width - right_w - (1 if right_specs else 0))
        if left_width() > allowed_left:
            excess = left_width() - allowed_left
            station = truncate_text(station, max(1, len(station) - excess))
        if left_width() > allowed_left:
            over = left_width() - allowed_left
            live_text = truncate_text(live_text, max(0, len(live_text) - over))

        # --- partie gauche
        left_runs: list[tuple[str, int, int]] = [
            ("♪ ", accent_pair, 0),
            (station + " ", self._p(theme.header_fg, theme.header_bg), curses.A_BOLD),
            ("  ", 0, 0),
            (live_text, live_pair, live_attr),
        ]
        if demo_text:
            left_runs.append(("  ", 0, 0))
            left_runs.append((demo_text, self._p(theme.demo_fg, theme.demo_bg), badge_attr))

        # --- dessin
        cursor = x0
        limit = allowed_left
        for text, pair, attr in left_runs:
            if limit <= 0:
                break
            visible = truncate_text(text, limit) if len(text) > limit else text
            if visible:
                self._put(y, cursor, visible, pair, attr)
            cursor += len(visible)
            limit -= len(visible)

        if right_specs:
            cursor = x0 + width - right_w
            for index, (_key, text, pair, attr) in enumerate(right_specs):
                if index:
                    self._put(y, cursor, "  ", dim_pair, dim_attr)
                    cursor += 2
                self._put(y, cursor, text, pair, attr)
                cursor += len(text)

    # -------------------------------------------------------------- spectre
    def _draw_spectrum(self, layout: Layout, frame: Frame) -> None:
        """Dessine le spectre ; seule cette zone reçoit la palette réactive."""
        rect = layout.spectrum
        width, height = rect.w, rect.h
        if width <= 0 or height <= 0:
            return
        base_theme = self._base_theme
        base_gradient = gradient_steps(base_theme)
        if self._reactive:
            self._theme = self._reactive_variant(base_theme, frame)
            self._gradient = gradient_steps(self._theme)
        try:
            self._clear_rect(rect)
            visual = self._visual
            if visual == "vertical" and width >= 6 and height >= 2:
                self._draw_spectrum_vertical(rect, frame)
            elif visual == "radial" and width >= 18 and height >= 6:
                self._draw_spectrum_radial(rect, frame)
            elif visual == "tunnel" and width >= 18 and height >= 6:
                self._draw_spectrum_tunnel(rect, frame)
            elif visual == "sphere" and width >= 18 and height >= 4:
                self._draw_spectrum_sphere(rect, frame)
            elif visual == "carpet" and width >= 24 and height >= 4:
                self._draw_spectrum_carpet(rect, frame)
            elif visual == "geodesic" and width >= 18 and height >= 4:
                self._draw_spectrum_geodesic(rect, frame)
            elif visual == "rosace_live" and width >= 18 and height >= 4:
                self._draw_spectrum_rosace_live(rect, frame)
            elif visual == "mandala" and width >= 18 and height >= 4:
                self._draw_spectrum_mandala(rect, frame)
            elif visual == "rosace" and width >= 18 and height >= 4:
                self._draw_spectrum_rosace(rect, frame)
            elif visual == "snowflake" and width >= 18 and height >= 4:
                self._draw_spectrum_snowflake(rect, frame)
            elif visual == "galaxy" and width >= 18 and height >= 4:
                self._draw_spectrum_galaxy(rect, frame)
            else:
                self._draw_spectrum_bars(rect, frame)
        finally:
            self._theme = base_theme
            self._gradient = base_gradient
        if not frame.connected:
            self._draw_overlay(rect, frame)

    def _draw_spectrum_bars(self, rect: Rect, frame: Frame) -> None:
        width, height = rect.w, rect.h
        theme = self._theme
        dim_pair = self._p(theme.dim)
        dim_attr = self._dim_attr()
        # Quadrillage très discret : un point tous les 8 colonnes, une ligne sur deux.
        dotted = "".join("·" if column % 8 == 4 else " " for column in range(width))
        blank = " " * width
        for row in range(height):
            self._put(rect.y + row, rect.x, dotted if row % 2 == 0 else blank, dim_pair, dim_attr)

        bars = resample(frame.bars, width)
        peaks = resample(frame.peaks, width)
        cells = [[" "] * width for _ in range(height)]
        cap_columns: dict[int, list[int]] = {}

        for column in range(width):
            value = bars[column]
            total = bar_eighths(value, height)
            if total > 0:
                full, frac = divmod(total, 8)
                full = min(full, height)
                for level in range(full):
                    cells[height - 1 - level][column] = FULL_BLOCK
                if frac and full < height:
                    cells[height - 1 - full][column] = PARTIAL_BLOCKS[frac - 1]
            peak = peaks[column]
            if peak > value + max(0.03, 1.2 / height) and peak > 0.02:
                cap_total = bar_eighths(peak, height)
                cap_row = height - 1 - max(0, (cap_total - 1) // 8)
                cap_row = max(0, min(height - 1, cap_row))
                cap_columns.setdefault(cap_row, []).append(column)

        # Dégradé vertical : une couleur par ligne, dessinée en une passe.
        levels = len(self._gradient)
        for row in range(height):
            t = (height - 1 - row) / (height - 1) if height > 1 else 0.5
            pair = self._p(self._gradient[gradient_level(t, levels)])
            line = "".join(cells[row])
            for start, run in _iter_runs(line):
                self._put(rect.y + row, rect.x + start, run, pair)

        # Capuchons de crête (marqueur lumineux flottant au-dessus des barres).
        cap_pair = self._p(theme.title)
        for row, columns in cap_columns.items():
            for column in columns:
                self._put(rect.y + row, rect.x + column, PEAK_CAP, cap_pair, curses.A_BOLD)

    # ------------------------------------------------------ spectre vertical
    def _draw_spectrum_vertical(self, rect: Rect, frame: Frame) -> None:
        """Spectre vertical : une bande par ligne, barres horizontales."""
        width, height = rect.w, rect.h
        theme = self._theme
        dim_pair = self._p(theme.dim)
        dim_attr = self._dim_attr()
        max_len = max(1, width - 1)
        values = resample(frame.bars, height)
        peaks = resample(frame.peaks, height)
        levels = len(self._gradient)
        grid = "".join("·" if column % 8 == 4 else " " for column in range(max_len))
        peak_pair = self._p(theme.title)

        # Ligne verticale d'axe + quadrillage discret, puis barres ligne à ligne.
        for row in range(height):
            band = height - 1 - row  # 0 = graves, en bas
            value = values[band]
            peak = peaks[band]
            fraction = band / (height - 1) if height > 1 else 0.5
            color = self._gradient[gradient_level(fraction, levels)]
            self._put(rect.y + row, rect.x, "│", dim_pair, dim_attr)
            self._put(rect.y + row, rect.x + 1, grid, dim_pair, dim_attr)
            total = int(round(value * max_len * 8))
            full, frac = divmod(total, 8)
            full = min(full, max_len)
            text = FULL_BLOCK * full
            if frac and full < max_len:
                text += LEFT_PARTIAL_BLOCKS[frac - 1]
            if text:
                self._put(rect.y + row, rect.x + 1, text, self._p(color))
            if peak > value + 0.035 and peak > 0.02:
                position = min(max_len - 1, int(round(peak * (max_len - 1))))
                self._put(
                    rect.y + row,
                    rect.x + 1 + position,
                    PEAK_VERTICAL,
                    peak_pair,
                    curses.A_BOLD,
                )

    # ---------------------------------------------------- spectre circulaire
    def _draw_spectrum_radial(self, rect: Rect, frame: Frame) -> None:
        """Spectre polaire : 64 bandes rayonnant autour d'un cercle."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        levels = len(self._gradient)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        max_radius = max(1.0, min(cx, cy) - 1.0)
        inner = max(1.5, max_radius * 0.30)
        bands = max(48, min(128, width * 2))
        values = resample(frame.bars, bands)
        peaks = resample(frame.peaks, bands)

        # Cercles de référence discrets.
        canvas.ring(cx, cy, inner, inner, theme.dim, steps=48)
        canvas.ring(cx, cy, max_radius, max_radius, theme.border)

        for index in range(bands):
            angle = -math.pi / 2.0 + 2.0 * math.pi * index / bands
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            outer = inner + (max_radius - inner) * values[index]
            color = self._gradient[gradient_level(index / max(1, bands - 1), levels)]
            canvas.line(
                cx + cos_a * inner,
                cy + sin_a * inner,
                cx + cos_a * outer,
                cy + sin_a * outer,
                color,
            )
            peak = peaks[index]
            if peak > values[index] + 0.03:
                peak_radius = inner + (max_radius - inner) * peak
                for delta in (-0.045, 0.0, 0.045):
                    canvas.plot(
                        cx + math.cos(angle + delta) * peak_radius,
                        cy + math.sin(angle + delta) * peak_radius,
                        theme.title,
                    )

        # Pulsation du beat au centre.
        beat = clamp01(frame.beat)
        if beat > 0.08:
            pulse = inner * (0.70 + 0.32 * beat)
            canvas.ring(cx, cy, pulse, pulse, theme.accent, steps=40)
            canvas.plot(cx, cy, theme.accent)
        canvas.render(self, rect)

    # -------------------------------------------------------- spectre tunnel
    def _draw_spectrum_tunnel(self, rect: Rect, frame: Frame) -> None:
        """Tunnel de spectre : des anneaux déformés qui arrivent vers le spectateur."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        levels = len(self._gradient)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        max_rx = max(2.0, cx - 1.0)
        max_ry = max(2.0, cy - 1.0)
        points = max(32, min(120, width * 2))
        values = resample(frame.bars, points)
        rings = max(5, min(14, height))
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        phase = (timestamp * 0.42) % 1.0

        # Rayons de fuite (structure du tunnel), très discrets.
        for spoke in range(8):
            angle = -math.pi / 2.0 + 2.0 * math.pi * spoke / 8.0
            canvas.line(
                cx,
                cy,
                cx + math.cos(angle) * max_rx,
                cy + math.sin(angle) * max_ry,
                theme.border,
            )

        # Anneaux : du fond (petits) vers l'avant (grands), déformés par le spectre.
        for ring_index in range(rings):
            progress = (phase + ring_index / rings) % 1.0
            if progress < 0.06:
                continue
            scale = progress ** 1.7
            if progress < 0.22:
                color = theme.dim
            else:
                color = self._gradient[gradient_level(0.08 + 0.92 * progress, levels)]
            canvas.ring(
                cx,
                cy,
                max_rx * scale,
                max_ry * scale,
                color,
                steps=points,
                values=values,
                wobble=0.34,
            )

        # Point de fuite pulsé par le beat.
        beat = clamp01(frame.beat)
        pulse = max(1.2, max_rx * 0.045 * (0.8 + 0.9 * beat))
        canvas.ring(cx, cy, pulse, pulse, theme.accent, steps=20)
        canvas.plot(cx, cy, theme.title)
        canvas.render(self, rect)

    # ------------------------------------------------------ visuels 3D (braille)
    def _draw_spectrum_sphere(self, rect: Rect, frame: Frame) -> None:
        """Sphère flottante : globe filaire déformé par le spectre, en orbite."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        beat = clamp01(getattr(frame, "beat", 0.0))
        max_r = max(2.0, min(cx, cy) - 1.0)
        cy += math.sin(timestamp * 0.9) * max_r * 0.07
        tilt = 0.38 + 0.06 * math.sin(timestamp * 0.5) + 0.04 * beat
        rotation = timestamp * 0.55
        bands = resample(frame.bars, 24)
        lon_count = max(24, min(44, int(width * 1.5)))
        lat_count = max(5, min(9, int(max_r / 5)))

        def project(lat: float, lon: float, amplitude: float) -> tuple[float, float, float]:
            radius = max_r * (0.82 + 0.26 * clamp01(amplitude))
            cos_lat = math.cos(lat)
            sin_lat = math.sin(lat)
            x0 = cos_lat * math.sin(lon)
            y0 = sin_lat
            z0 = cos_lat * math.cos(lon)
            x1 = x0 * math.cos(rotation) + z0 * math.sin(rotation)
            z1 = -x0 * math.sin(rotation) + z0 * math.cos(rotation)
            y2 = y0 * math.cos(tilt) - z1 * math.sin(tilt)
            z2 = y0 * math.sin(tilt) + z1 * math.cos(tilt)
            return cx + x1 * radius, cy - y2 * radius, z2

        # Orbite et halo discrets.
        canvas.ring(cx, cy, max_r * 1.35, max_r * 0.42, theme.dim, steps=90)
        canvas.ring(cx, cy, max_r, max_r, theme.border, steps=96)

        # Nuage de points du globe (hémisphère avant coloré, arrière discret).
        for lat_index in range(-lat_count, lat_count + 1):
            lat = (lat_index / lat_count) * (math.pi / 2.0)
            for lon_index in range(lon_count):
                lon = 2.0 * math.pi * lon_index / lon_count
                amplitude = bands[int(lon_index / lon_count * len(bands)) % len(bands)]
                x, y, depth = project(lat, lon, amplitude)
                if depth >= -0.08:
                    shade = round((0.5 + 0.5 * depth) * 5.0) / 5.0
                    canvas.plot(x, y, self._gradient_color(0.28 + 0.52 * shade))

        # Trois parallèles jaunes (dont l'équateur).
        for latitude in (-math.pi * 0.34, 0.0, math.pi * 0.34):
            for dot in range(lon_count * 2):
                longitude = 2.0 * math.pi * dot / (lon_count * 2)
                amplitude = bands[int(dot / (lon_count * 2) * len(bands)) % len(bands)]
                x, y, depth = project(latitude, longitude, amplitude)
                if depth >= -0.04:
                    canvas.plot(x, y, theme.title)
                elif dot % 3 == 0:
                    canvas.plot(x, y, theme.dim)

        # Trois méridiens jaunes.
        for meridian in range(3):
            longitude = 2.0 * math.pi * meridian / 3.0
            for dot in range(44):
                latitude = -math.pi / 2.0 + math.pi * dot / 43.0
                amplitude = bands[(dot * 2 + meridian) % len(bands)]
                x, y, depth = project(latitude, longitude, amplitude)
                if depth >= -0.04:
                    canvas.plot(x, y, theme.title)
                elif dot % 3 == 0:
                    canvas.plot(x, y, theme.dim)

        # Points rouges : villes / balises (effectif doublé, 64 points).
        for index in range(64):
            latitude = math.asin(-1.0 + 2.0 * ((index * 37) % 64) / 63.0)
            longitude = 2.0 * math.pi * ((index * 53) % 64) / 64.0
            amplitude = bands[(index * 7) % len(bands)]
            x, y, depth = project(latitude, longitude, amplitude)
            if depth >= -0.02:
                canvas.plot(x, y, theme.vu_hot)

        # Ombre portée sous la sphère flottante.
        canvas.ring(cx, cy + max_r * 1.18, max_r * 0.55, max_r * 0.10, theme.dim, steps=44)
        canvas.render(self, rect)

    def _draw_spectrum_carpet(self, rect: Rect, frame: Frame) -> None:
        """Tapis volant : grille en perspective ondulant au rythme du spectre."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        cx = (width * 2 - 1) / 2.0
        horizon = max(2.0, height * 4 * 0.28)
        bottom = height * 4 - 1
        spread = width * 1.10
        bands = resample(frame.bars, max(32, width * 2))

        # Étoiles au-dessus de l'horizon (clignotement doux).
        for index in range(16):
            star_x = (index * 53 + 17) % max(1, width * 2)
            star_y = (index * 29 + 3) % max(1, int(horizon))
            twinkle = 0.5 + 0.5 * math.sin(timestamp * 1.4 + index * 1.7)
            canvas.plot(star_x, star_y, self._gradient_color(0.18 + 0.22 * twinkle))

        # Lignes de fuite du tapis.
        for lateral in (-1.0, -0.55, 0.0, 0.55, 1.0):
            color = theme.accent if abs(lateral) == 1.0 else theme.dim
            canvas.line(
                cx,
                horizon,
                cx + lateral * spread,
                bottom,
                color,
            )

        # Rangées qui défilent vers le bas, ondulées par le spectre.
        rows = 7
        phase = (timestamp * 0.30) % 1.0
        for row_index in range(rows + 1):
            progress = ((row_index / rows) + phase) % 1.0
            if progress < 0.04:
                continue
            depth = progress
            base_y = horizon + (bottom - horizon) * (progress ** 1.55)
            row_color = self._gradient_color(0.12 + 0.78 * depth)
            previous: tuple[float, float] | None = None
            for dot_x in range(0, width * 2, 3):
                normalized = dot_x / max(1, width * 2 - 1)
                amplitude = clamp01(bands[int(normalized * (len(bands) - 1))])
                wave = math.sin(normalized * math.pi * 2.0 - timestamp * 2.2 + progress * 5.0)
                x = cx + (dot_x - cx) * (0.30 + 0.70 * depth)
                y = base_y - (3.0 + 9.0 * depth) * wave - (4.0 + 10.0 * depth) * amplitude
                if previous is not None:
                    canvas.line(previous[0], previous[1], x, y, row_color)
                previous = (x, y)

        # Franges des bords avant.
        for lateral in (-1.0, 1.0):
            x = cx + lateral * spread
            for fringe in range(7):
                y = bottom - fringe * 2
                canvas.plot(x + lateral * fringe, y, theme.title)
        canvas.render(self, rect)

    def _draw_spectrum_geodesic(self, rect: Rect, frame: Frame) -> None:
        """Sphère géodésique : maillage icosaédrique subdivisé qui respire."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        bass = clamp01(getattr(frame, "bass", 0.0))
        beat = clamp01(getattr(frame, "beat", 0.0))
        max_r = max(2.0, min(cx, cy) - 1.0) * (0.88 + 0.07 * bass + 0.04 * beat)
        bands = resample(frame.bars, 24)
        rotation_y = timestamp * 0.55
        rotation_x = 0.42 + 0.20 * math.sin(timestamp * 0.45)

        # Icosaèdre de base.
        phi = (1.0 + math.sqrt(5.0)) / 2.0
        vertices: list[tuple[float, float, float]] = []

        def add_vertex(point: tuple[float, float, float]) -> int:
            length = math.sqrt(sum(component * component for component in point)) or 1.0
            vertices.append(
                (point[0] / length, point[1] / length, point[2] / length)
            )
            return len(vertices) - 1

        base = (
            (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
            (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
            (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1),
        )
        for point in base:
            add_vertex(point)
        faces = [
            (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
            (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
            (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
            (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
        ]
        midpoint_cache: dict[tuple[int, int], int] = {}

        def midpoint(first: int, second: int) -> int:
            key = (min(first, second), max(first, second))
            if key not in midpoint_cache:
                a = vertices[first]
                b = vertices[second]
                midpoint_cache[key] = add_vertex(
                    (a[0] + b[0], a[1] + b[1], a[2] + b[2])
                )
            return midpoint_cache[key]

        for _ in range(1):  # fréquence 2 (20 -> 80 facettes)
            next_faces = []
            for a, b, c in faces:
                ab = midpoint(a, b)
                bc = midpoint(b, c)
                ca = midpoint(c, a)
                next_faces.extend(
                    ((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca))
                )
            faces = next_faces

        projected: list[tuple[float, float, float]] = []
        for index, (x, y, z) in enumerate(vertices):
            amplitude = clamp01(bands[index % len(bands)])
            radius = max_r * (0.92 + 0.12 * amplitude)
            x1 = x * math.cos(rotation_y) + z * math.sin(rotation_y)
            z1 = -x * math.sin(rotation_y) + z * math.cos(rotation_y)
            y2 = y * math.cos(rotation_x) - z1 * math.sin(rotation_x)
            z2 = y * math.sin(rotation_x) + z1 * math.cos(rotation_x)
            projected.append((cx + x1 * radius, cy - y2 * radius, z2))

        edges: set[tuple[int, int]] = set()
        for a, b, c in faces:
            for first, second in ((a, b), (b, c), (c, a)):
                edges.add((min(first, second), max(first, second)))
        for a, b in sorted(
            edges, key=lambda edge: min(projected[edge[0]][2], projected[edge[1]][2])
        ):
            x1, y1, z1 = projected[a]
            x2, y2, z2 = projected[b]
            depth = (z1 + z2) / 2.0
            color = (
                theme.dim
                if depth < -0.12
                else self._gradient_color(0.30 + 0.55 * max(0.0, depth) + 0.10 * beat)
            )
            canvas.line(x1, y1, x2, y2, color)
        for x, y, depth in projected:
            if depth >= 0.0:
                canvas.plot(x, y, theme.title)

        canvas.ring(cx, cy, max_r * 1.18, max_r * 1.18 * 0.28, theme.dim, steps=90)
        canvas.render(self, rect)

    def _draw_spectrum_snowflake(self, rect: Rect, frame: Frame) -> None:
        """Flocon de Koch entier, plan et réactif : il gonfle et ondule avec le son."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        bass = clamp01(getattr(frame, "bass", 0.0))
        treble = clamp01(getattr(frame, "treble", 0.0))
        beat = clamp01(getattr(frame, "beat", 0.0))
        max_r = max(2.0, min(cx, cy) - 1.0)
        bands = resample(frame.bars, 36)
        scale = max_r * (0.60 + 0.30 * bass + 0.14 * beat + 0.04 * math.sin(timestamp * 1.1))
        scale = max(max_r * 0.30, min(max_r * 0.97, scale))
        rotation = timestamp * 0.18

        # Triangle de départ, puis subdivision de Koch (forme fermée conservée).
        points: list[tuple[float, float]] = []
        for corner in range(3):
            angle = rotation - math.pi / 2.0 + 2.0 * math.pi * corner / 3.0
            points.append((math.cos(angle), math.sin(angle)))
        for _ in range(3):
            refined: list[tuple[float, float]] = []
            count = len(points)
            for index in range(count):
                ax, ay = points[index]
                bx, by = points[(index + 1) % count]
                dx = bx - ax
                dy = by - ay
                third_x, third_y = ax + dx / 3.0, ay + dy / 3.0
                two_third_x, two_third_y = ax + 2.0 * dx / 3.0, ay + 2.0 * dy / 3.0
                mid_x, mid_y = (third_x + two_third_x) / 2.0, (third_y + two_third_y) / 2.0
                k = math.sqrt(3.0) / 6.0
                peak_x = mid_x + dy * k
                peak_y = mid_y - dx * k
                refined.extend(
                    ((ax, ay), (third_x, third_y), (peak_x, peak_y), (two_third_x, two_third_y))
                )
            points = refined

        # Réaction au spectre : rayon local modulé, contour entier recoloré.
        for layer, layer_factor in enumerate((1.0, 0.52, 0.27)):
            previous: tuple[float, float] | None = None
            first: tuple[float, float] | None = None
            color = self._gradient_color(0.24 + 0.18 * layer + 0.10 * beat)
            for index, (px, py) in enumerate(points + [points[0]]):
                angle = math.atan2(py, px)
                amplitude = clamp01(
                    bands[int((angle + math.pi) / (2.0 * math.pi) * (len(bands) - 1))]
                )
                radial = scale * layer_factor * (
                    1.0
                    + 0.11 * amplitude
                    + 0.05 * treble * math.sin(angle * 7.0 + timestamp * 2.0)
                )
                x = cx + px * radial
                y = cy + py * radial
                if previous is None:
                    first = (x, y)
                else:
                    canvas.line(previous[0], previous[1], x, y, color)
                previous = (x, y)
            if previous is not None and first is not None:
                canvas.line(previous[0], previous[1], first[0], first[1], color)

        for spoke in range(6):
            angle = rotation * 2.0 + 2.0 * math.pi * spoke / 6.0
            canvas.line(
                cx,
                cy,
                cx + math.cos(angle) * scale * 0.24,
                cy + math.sin(angle) * scale * 0.24,
                theme.dim,
            )
        canvas.ring(
            cx,
            cy,
            scale * (0.10 + 0.04 * beat),
            scale * (0.10 + 0.04 * beat),
            theme.accent,
            steps=30,
        )
        canvas.plot(cx, cy, theme.title)
        canvas.render(self, rect)

    def _draw_spectrum_rosace_live(self, rect: Rect, frame: Frame) -> None:
        """Rosace réactive : courbes de rose entières qui se déforment fortement."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        bass = clamp01(getattr(frame, "bass", 0.0))
        treble = clamp01(getattr(frame, "treble", 0.0))
        beat = clamp01(getattr(frame, "beat", 0.0))
        max_r = max(2.0, min(cx, cy) - 1.0)
        bands = resample(frame.bars, 36)
        scale = 0.52 + 0.38 * bass + 0.16 * beat + 0.06 * math.sin(timestamp * 1.1)
        scale = max(0.36, min(1.18, scale))

        for layer_index, (folds, radius_fraction, direction) in enumerate(
            ((5, 0.96, 1.0), (7, 0.73, -1.0), (11, 0.52, 1.0))
        ):
            steps = 260
            previous: tuple[float, float] | None = None
            first_point: tuple[float, float] | None = None
            color = self._gradient_color(0.22 + 0.16 * layer_index + 0.10 * beat)
            for index in range(steps + 1):
                angle = 2.0 * math.pi * index / steps
                amplitude = clamp01(bands[int(index / steps * (len(bands) - 1))])
                petal = 0.52 + 0.34 * math.cos(
                    folds * angle + direction * timestamp * 0.38 + layer_index * 0.45
                )
                wobble = (
                    0.30 * amplitude
                    + 0.14 * treble * math.sin((folds + 4) * angle - timestamp * 1.15)
                )
                radius = max_r * radius_fraction * scale * max(0.10, petal + wobble)
                radius *= 1.0 + 0.08 * beat * math.sin(angle * 5.0 + timestamp * 1.6)
                radius = min(radius, max_r * 0.98)
                x = cx + math.cos(angle) * radius
                y = cy + math.sin(angle) * radius
                if previous is None:
                    first_point = (x, y)
                else:
                    canvas.line(previous[0], previous[1], x, y, color)
                previous = (x, y)
            if previous is not None and first_point is not None:
                canvas.line(previous[0], previous[1], first_point[0], first_point[1], color)

        # Rosace centrale et pétales lumineux.
        for index in range(5):
            angle = timestamp * 0.5 + 2.0 * math.pi * index / 5.0
            radius = max_r * (0.10 + 0.08 * beat)
            canvas.plot(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius, theme.title)
        canvas.ring(cx, cy, max_r * (0.10 + 0.05 * beat), max_r * (0.10 + 0.05 * beat), theme.accent, steps=30)
        canvas.plot(cx, cy, theme.title)
        canvas.render(self, rect)

    def _draw_spectrum_mandala(self, rect: Rect, frame: Frame) -> None:
        """Mandala fractal : arbres auto-similaires répétés en couronne."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        beat = clamp01(getattr(frame, "beat", 0.0))
        max_r = max(2.0, min(cx, cy) - 1.0)
        bands = resample(frame.bars, 12)

        def branch(
            x: float,
            y: float,
            angle: float,
            length: float,
            depth: int,
            amplitude: float,
        ) -> None:
            if depth > 4 or length < 1.0:
                return
            x2 = x + math.cos(angle) * length
            y2 = y + math.sin(angle) * length
            canvas.line(
                x,
                y,
                x2,
                y2,
                self._gradient_color(0.20 + 0.14 * depth + 0.16 * amplitude),
            )
            if depth == 4:
                canvas.plot(x2, y2, theme.title)
                return
            spread = 0.62 - 0.05 * depth
            ratio = 0.66
            branch(x2, y2, angle - spread, length * ratio, depth + 1, amplitude)
            branch(x2, y2, angle + spread, length * ratio, depth + 1, amplitude)

        for sector in range(12):
            angle = -math.pi / 2.0 + 2.0 * math.pi * sector / 12.0 + timestamp * 0.10
            amplitude = clamp01(bands[sector])
            branch(
                cx,
                cy,
                angle,
                max_r * (0.26 + 0.22 * amplitude + 0.04 * beat),
                0,
                amplitude,
            )

        for fraction in (0.35, 0.62, 0.88):
            canvas.ring(cx, cy, max_r * fraction, max_r * fraction, theme.dim, steps=64)
        canvas.ring(cx, cy, max_r * (0.10 + 0.03 * beat), max_r * 0.10, theme.accent, steps=36)
        canvas.plot(cx, cy, theme.title)
        canvas.render(self, rect)

    def _draw_spectrum_rosace(self, rect: Rect, frame: Frame) -> None:
        """Rosace fractale : polygones étoilés récursifs et courbe de rose."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        beat = clamp01(getattr(frame, "beat", 0.0))
        max_r = max(2.0, min(cx, cy) - 1.0)
        bands = resample(frame.bars, 16)
        sides = 10
        layers = 6

        for layer in range(layers):
            radius = max_r * (0.92 - 0.13 * layer)
            rotation = timestamp * 0.18 + layer * math.pi / sides
            points: list[tuple[float, float]] = []
            for index in range(sides):
                angle = rotation + 2.0 * math.pi * index / sides
                amplitude = clamp01(bands[(index + layer) % len(bands)])
                r = radius * (0.72 + 0.38 * amplitude)
                points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
            color = self._gradient_color(0.22 + 0.09 * layer + 0.08 * beat)
            for index in range(sides):
                x1, y1 = points[index]
                x2, y2 = points[(index + sides // 2) % sides]
                canvas.line(x1, y1, x2, y2, color)
                if layer < 3:
                    x3, y3 = points[(index + 1) % sides]
                    canvas.line(x1, y1, x3, y3, theme.dim)
            for index in range(0, sides, 2):
                canvas.line(cx, cy, points[index][0], points[index][1], theme.dim)

        for step in range(220):
            angle = 2.0 * math.pi * step / 220.0
            amplitude = clamp01(bands[step % len(bands)])
            radius = max_r * (0.28 + 0.58 * abs(math.sin(5.0 * angle + timestamp * 0.7)))
            radius *= 0.82 + 0.25 * amplitude
            canvas.plot(
                cx + math.cos(angle) * radius,
                cy + math.sin(angle) * radius,
                self._gradient_color(0.30 + 0.45 * amplitude),
            )

        canvas.ring(cx, cy, max_r * (0.16 + 0.03 * beat), max_r * 0.16, theme.accent, steps=40)
        canvas.plot(cx, cy, theme.title)
        canvas.render(self, rect)

    def _draw_spectrum_galaxy(self, rect: Rect, frame: Frame) -> None:
        """Galaxie spirale : bras tournants, cœur pulsé et champ d'étoiles."""
        width, height = rect.w, rect.h
        theme = self._theme
        canvas = BrailleCanvas(width, height)
        cx = (width * 2 - 1) / 2.0
        cy = (height * 4 - 1) / 2.0
        timestamp = float(getattr(frame, "ts", 0.0) or 0.0)
        beat = clamp01(getattr(frame, "beat", 0.0))
        max_r = max(2.0, min(cx, cy) - 1.0)
        bands = resample(frame.bars, 32)

        # Champ d'étoiles lointaines.
        for index in range(70):
            angle = index * 2.399 + timestamp * 0.04
            radius = max_r * (0.20 + 0.80 * ((index * 37) % 100) / 100.0)
            canvas.plot(
                cx + math.cos(angle) * radius,
                cy + math.sin(angle) * radius,
                self._gradient_color(0.10 + 0.20 * ((index * 13) % 100) / 100.0),
            )

        # Six bras spiraux.
        for arm in range(6):
            offset = 2.0 * math.pi * arm / 6.0 + timestamp * 0.30
            previous: tuple[float, float] | None = None
            for step in range(70):
                fraction = step / 69.0
                radius = max_r * (0.05 + 0.95 * (fraction ** 1.25))
                amplitude = clamp01(bands[int(fraction * (len(bands) - 1))])
                angle = offset + fraction * 4.4 + 0.22 * math.sin(timestamp * 0.7 + arm)
                radius *= 0.86 + 0.20 * amplitude
                x = cx + math.cos(angle) * radius
                y = cy + math.sin(angle) * radius
                if previous is not None:
                    canvas.line(
                        previous[0],
                        previous[1],
                        x,
                        y,
                        self._gradient_color(0.20 + 0.55 * fraction + 0.15 * amplitude),
                    )
                previous = (x, y)
                if step % 4 == 0:
                    canvas.plot(
                        x + math.cos(angle + 1.2) * 2.0,
                        y + math.sin(angle + 1.2) * 2.0,
                        theme.title,
                    )

        # Noyau lumineux.
        for fraction, color in ((0.16, theme.title), (0.09, theme.accent)):
            pulse = max_r * fraction * (1.0 + 0.15 * beat)
            canvas.ring(cx, cy, pulse, pulse, color, steps=44)
        canvas.plot(cx, cy, self._theme.title)
        canvas.render(self, rect)

    def _draw_overlay(self, rect: Rect, frame: Frame) -> None:
        """Centered CONNECTING/RECONNECTING veil + status, always on screen."""
        width, height = rect.w, rect.h
        if width < 12 or height < 1:
            return
        theme = self._theme
        box_w = max(12, min(width - 2, 46))
        status = str(frame.status or "").strip() or "Stream will resume shortly"
        # First connection attempt versus a stream that was already playing.
        label = (
            "◌ CONNECTING…"
            if "reconnect" not in status.lower() and "connect" in status.lower()
            else "↻ RECONNECTING…"
        )
        fg = self._p(theme.overlay_fg, theme.header_bg)
        border = self._p(theme.accent, theme.header_bg)
        if height < 3:
            row = rect.y + height // 2
            self._clear_row(row, rect.x, width)
            text = truncate_text(label, max(1, width - 2))
            x = rect.x + max(0, (width - len(text)) // 2)
            self._put(row, rect.x, " " * width, fg)
            self._put(row, x, text, fg, curses.A_BOLD)
            return
        box_h = 3
        top = rect.y + max(0, (height - box_h) // 2)
        left = rect.x + max(0, (width - box_w) // 2)
        # Fond (voile) du cadre.
        for row in range(box_h):
            self._put(top + row, left, " " * box_w, fg)
        title = truncate_text(label, max(0, box_w - 5))
        top_line = "╭─ " + title + " " + "─" * max(0, box_w - len(title) - 5) + "╮"
        self._put(top, left, top_line, border, curses.A_BOLD)
        content = truncate_text(status, max(0, box_w - 4))
        pad = max(0, box_w - 2 - len(content))
        left_pad = pad // 2
        right_pad = pad - left_pad
        self._put(top + 1, left, "│", border)
        self._put(top + 1, left + 1 + left_pad, content, fg, curses.A_BOLD)
        self._put(top + 1, left + 1 + left_pad + len(content), " " * right_pad + "│", border)
        self._put(top + 2, left, "╰" + "─" * (box_w - 2) + "╯", border)

    # --------------------------------------------------------------- oscillo
    def _draw_scope(self, layout: Layout, frame: Frame) -> None:
        rect = layout.osc
        if rect is None or rect.w <= 0 or rect.h <= 0:
            return
        theme = self._theme
        width, height = rect.w, rect.h
        self._clear_rect(rect)
        # Ligne zéro discrète (pointillés).
        line_row = height // 2
        pattern = "".join("·" if column % 2 == 0 else " " for column in range(width))
        self._put(rect.y + line_row, rect.x, pattern, self._p(theme.dim), self._dim_attr())
        pair = self._p(theme.wave)
        for row, text in enumerate(wave_to_braille(frame.wave, width, height)):
            for start, run in _iter_runs(text):
                self._put(rect.y + row, rect.x + start, run, pair)

    # ------------------------------------------------------------------- VU
    def _zone_color(self, position: float) -> int:
        """Couleur VU selon la position sur l'échelle (0 -> 1)."""
        theme = self._theme
        if position >= 0.85:
            return theme.vu_hot
        if position >= 0.70:
            return theme.vu_warn
        return theme.vu_safe

    def _meter(self, y: int, x: int, width: int, level: object) -> None:
        """Jauge horizontale : piste discrète + remplissage coloré par zone."""
        width = int(width)
        if width <= 0:
            return
        theme = self._theme
        self._put(y, x, "░" * width, self._p(theme.dim), self._dim_attr())
        filled = int(round(clamp01(level) * width))
        if filled <= 0:
            return
        segments: list[tuple[int, int, int]] = []
        start = 0
        current = None
        for cell in range(filled):
            color = self._zone_color((cell + 0.5) / width)
            if current is None:
                current = color
                start = cell
            elif color != current:
                segments.append((start, cell, current))
                start = cell
                current = color
        if current is not None:
            segments.append((start, filled, current))
        for begin, end, color in segments:
            self._put(y, x + begin, FULL_BLOCK * (end - begin), self._p(color))

    def _draw_vu(self, layout: Layout, frame: Frame) -> None:
        rect = layout.vu
        if rect is None or rect.w <= 0 or rect.h <= 0:
            return
        self._clear_rect(rect)
        if rect.h >= 3:
            self._vu_stereo(rect, 0, frame, with_bands=False)
            self._draw_vu_separator(rect, 1)
            self._vu_bands(rect, 2, frame)
        elif rect.h == 2:
            self._vu_stereo(rect, 0, frame, with_bands=False)
            self._vu_bands(rect, 1, frame)
        else:
            self._vu_stereo(rect, 0, frame, with_bands=rect.w >= 64)

    def _draw_vu_separator(self, rect: Rect, row: int) -> None:
        """Pointillés verts entre les VU L/R et les jauges BASS/MID/TREB."""
        width = rect.w
        if width <= 0:
            return
        y = rect.y + row
        pattern = "".join("·" if column % 2 == 0 else " " for column in range(width))
        self._put(y, rect.x, pattern, self._p(self._theme.accent), self._dim_attr())

    def _vu_stereo(self, rect: Rect, row: int, frame: Frame, with_bands: bool = False) -> None:
        y = rect.y + row
        x = rect.x
        width = rect.w
        theme = self._theme
        if with_bands and width >= 64:
            stereo_width = width - 26
            bands_x = x + stereo_width
            bands_width = 26
        else:
            stereo_width = width
            bands_x = None
            bands_width = 0
        half = stereo_width // 2
        if half < 4:
            # Terminal minuscule : un seul indicateur, on ne déborde pas.
            if width >= 4:
                self._put(y, x, "L", self._p(theme.accent), curses.A_BOLD)
                self._meter(y, x + 2, max(1, width - 2), frame.peak_l)
            return
        bar_width = max(1, half - 6)
        if bar_width + 2 > half:
            bar_width = max(1, half - 2)
        self._put(y, x, "L", self._p(theme.accent), curses.A_BOLD)
        self._meter(y, x + 2, bar_width, frame.peak_l)
        self._put(y, x + half, "R", self._p(theme.accent), curses.A_BOLD)
        self._meter(y, x + half + 2, bar_width, frame.peak_r)
        if bands_x is not None:
            self._put(y, bands_x - 1, "·", self._p(theme.accent), self._dim_attr())
            self._bands_compact(y, bands_x, bands_width, frame)

    def _bands_compact(self, y: int, x: int, width: int, frame: Frame) -> None:
        """Trois mini-jauges (B/M/T) pour les modes sans seconde ligne VU."""
        theme = self._theme
        item_width = 6  # label + 5 cellules
        total = item_width * 3 + 2
        if width < total:
            return
        cursor = x + width - total
        accent_pair = self._p(theme.accent)
        for label, value in (
            ("B", frame.bass),
            ("M", frame.mid),
            ("T", frame.treble),
        ):
            self._put(y, cursor, label, accent_pair, curses.A_BOLD)
            self._meter(y, cursor + 1, 4, value)
            cursor += item_width
        self._put(y, cursor, "  ", 0, 0)

    def _vu_bands(self, rect: Rect, row: int, frame: Frame) -> None:
        y = rect.y + row
        x = rect.x
        width = rect.w
        theme = self._theme
        dim_pair = self._p(theme.text_dim)
        dim_attr = self._dim_attr()
        if width < 20:
            return
        bar_width = max(2, (width - 19) // 3)
        cursor = x
        for label, value in (
            ("BASS", frame.bass),
            ("MID", frame.mid),
            ("TREB", frame.treble),
        ):
            if cursor + len(label) + 1 + bar_width > x + width:
                break
            self._put(y, cursor, label, dim_pair, dim_attr)
            self._meter(y, cursor + len(label) + 1, bar_width, value)
            cursor += len(label) + 1 + bar_width + 2
        rms_text = f"RMS {int(round(clamp01(frame.rms) * 100)):3d}%"
        remaining = x + width - cursor
        if remaining >= len(rms_text) + 1:
            self._put(y, x + width - len(rms_text), rms_text, dim_pair, dim_attr)

    # ----------------------------------------------------------------- titre
    def _draw_title(self, layout: Layout, frame: Frame) -> None:
        rect = layout.title
        if rect is None or rect.w <= 0:
            return
        self._clear_row(rect.y, rect.x, rect.w)
        theme = self._theme
        x = rect.x
        y = rect.y
        width = rect.w
        accent_pair = self._p(theme.accent)
        dim_pair = self._p(theme.dim)
        self._put(y, x, "♪ ", accent_pair, curses.A_BOLD)
        x += 2
        if layout.mode != "full" and width - (x - rect.x) >= 22:
            label = "NOW PLAYING"
            self._put(y, x, label, accent_pair, curses.A_BOLD)
            x += len(label)
            self._put(y, x, " · ", dim_pair, self._dim_attr())
            x += 3
        available = rect.x + width - x
        if available <= 0:
            return
        if frame.title:
            view = marquee_view(frame.title, available, frame.ts, speed=7.0)
            self._put(y, x, view, self._p(theme.title), curses.A_BOLD)
        else:
            hint = (
                "Waiting for title…"
                if float(getattr(frame, "elapsed", 0.0) or 0.0) < 12.0
                else "No title provided by this station"
            )
            self._put(
                y,
                x,
                truncate_text(hint, available),
                dim_pair,
                self._dim_attr() | curses.A_BOLD,
            )

    # ------------------------------------------------------------ méta / aide
    def _draw_meta(self, layout: Layout, frame: Frame) -> None:
        rect = layout.meta
        if rect is None or rect.w <= 0:
            return
        self._clear_row(rect.y, rect.x, rect.w)
        theme = self._theme
        dim_pair = self._p(theme.text_dim)
        dim_attr = self._dim_attr()
        x, y, width = rect.x, rect.y, rect.w

        # Droite : auditeurs, fps, volume (le volume reste le plus à droite).
        right_runs: list[tuple[str, int, int]] = []
        listeners = format_listeners(frame.listeners)
        if listeners:
            right_runs.append((listeners, dim_pair, dim_attr))
        if frame.fps:
            right_runs.append((f"{frame.fps:.0f} fps", dim_pair, dim_attr))
        volume = format_volume(frame.volume, frame.muted)
        if volume:
            volume_pair = self._p(theme.vu_hot) if frame.muted else self._p(theme.accent)
            right_runs.append((volume, volume_pair, 0))

        def right_width() -> int:
            if not right_runs:
                return 0
            return sum(len(text) for text, _, _ in right_runs) + 2 * (len(right_runs) - 1)

        # Gauche : sous-titre puis statut.
        left_runs: list[tuple[str, int, int]] = []
        subtitle = str(frame.subtitle or "").strip()
        status = str(frame.status or "").strip()
        if subtitle:
            left_runs.append((subtitle, dim_pair, dim_attr))
        if status:
            separator = "  ·  " if left_runs else ""
            status_pair = self._p(theme.vu_hot) if not frame.connected else self._p(theme.accent)
            left_runs.append((separator + status, status_pair, 0))

        while right_width() and (
            sum(len(text) for text, _, _ in left_runs) + right_width() + 1 > width
        ):
            right_runs.pop(0)
        limit = max(0, width - right_width() - (1 if right_runs else 0))
        cursor = x
        for text, pair, attr in left_runs:
            if limit <= 0:
                break
            visible = truncate_text(text, limit) if len(text) > limit else text
            if visible:
                self._put(y, cursor, visible, pair, attr)
            cursor += len(visible)
            limit -= len(visible)
        if right_runs:
            cursor = x + width - right_width()
            for index, (text, pair, attr) in enumerate(right_runs):
                if index:
                    self._put(y, cursor, "  ", dim_pair, dim_attr)
                    cursor += 2
                self._put(y, cursor, text, pair, attr)
                cursor += len(text)

    def _draw_hints(self, layout: Layout, frame: Frame) -> None:
        rect = layout.hints
        if rect is None or rect.w <= 0:
            return
        self._clear_row(rect.y, rect.x, rect.w)
        theme = self._theme
        dim_pair = self._p(theme.text_dim)
        dim_attr = self._dim_attr()
        credit = CREDIT
        width = rect.w
        if width >= len(credit) + 2:
            hint = hints_text(max(0, width - len(credit) - 2))
            if hint:
                self._put(rect.y, rect.x, hint, dim_pair, dim_attr)
            self._put(rect.y, rect.x + width - len(credit), credit, self._p(theme.title))
        elif width >= 10:
            text = truncate_text(credit, width)
            self._put(rect.y, rect.x + width - len(text), text, self._p(theme.title))
