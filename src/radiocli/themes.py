"""Palettes de couleurs et gestion des paires curses du visualiseur.

Ce module est volontairement indépendant de l'état curses : il reste
importable (et testable) sans terminal. Seule :class:`ColorManager` touche
réellement à curses, et uniquement après ``initscr()``.

Fallback en cascade :
    * 256 couleurs ou plus : indices xterm utilisés tels quels ;
    * 8/16/88 couleurs     : conversion vers la couleur ANSI la plus proche ;
    * aucune couleur       : tout retombe sur la paire 0 + attributs
      (gras / dim / reverse) pour garder du contraste.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

try:  # pragma: no cover - curses est toujours présent sous Linux
    import curses
except Exception:  # pragma: no cover
    curses = None  # type: ignore[assignment]

#: Nombre de paliers du dégradé vertical (assez pour être joli, assez peu
#: pour ne pas épuiser les paires de couleurs : 12 paires par thème).
GRADIENT_LEVELS = 12

#: Thème par défaut, aux couleurs rasta (vert / jaune / rouge).
PRIMARY_THEME = "roots"

#: Alias tolérants acceptés par ``set_theme`` (jamais listés dans THEME_NAMES).
THEME_ALIASES = {
    "rasta": "roots",
    "reggae": "roots",
    "mint": "menthe",
    "monochrome": "mono",
}


@dataclass(frozen=True, slots=True)
class Theme:
    """Palette complète d'un thème du visualiseur (indices xterm 256)."""

    name: str
    label: str
    # -- en-tête / chrome ---------------------------------------------------
    header_fg: int
    header_bg: int
    accent: int
    dim: int
    border: int
    # -- spectre : trois arrêts du dégradé vertical (bas -> haut) -----------
    spectrum_low: int
    spectrum_mid: int
    spectrum_high: int
    # -- divers -------------------------------------------------------------
    wave: int
    title: int
    vu_safe: int
    vu_warn: int
    vu_hot: int
    live_fg: int
    live_bg: int
    rec_fg: int
    rec_bg: int
    demo_fg: int
    demo_bg: int
    overlay_fg: int
    overlay_bg: int
    #: Gris clair dédié aux textes secondaires (hints, sous-titres, menu…),
    #: plus lisible que ``dim`` qui reste réservé aux éléments décoratifs.
    text_dim: int = 250

    @property
    def stops(self) -> tuple[int, int, int]:
        """Arrêts du dégradé, du bas (graves) vers le haut (aigus)."""
        return (self.spectrum_low, self.spectrum_mid, self.spectrum_high)


# ---------------------------------------------------------------------------
# Définition des thèmes
# ---------------------------------------------------------------------------
_THEME_LIST: tuple[Theme, ...] = (
    # Roots rasta : vert profond en bas, or au milieu, rouge en haut.
    Theme(
        name="roots",
        label="Roots",
        header_fg=226,
        header_bg=22,
        accent=46,
        dim=240,
        border=28,
        spectrum_low=40,
        spectrum_mid=226,
        spectrum_high=196,
        wave=46,
        title=226,
        vu_safe=46,
        vu_warn=226,
        vu_hot=196,
        live_fg=16,
        live_bg=46,
        rec_fg=231,
        rec_bg=196,
        demo_fg=16,
        demo_bg=226,
        overlay_fg=231,
        overlay_bg=58,
    ),
    # Sunset : violet crépusculaire, orange chaud, rose corail.
    Theme(
        name="sunset",
        label="Sunset",
        header_fg=231,
        header_bg=88,
        accent=214,
        dim=240,
        border=53,
        spectrum_low=91,
        spectrum_mid=208,
        spectrum_high=205,
        wave=214,
        title=205,
        vu_safe=214,
        vu_warn=208,
        vu_hot=196,
        live_fg=16,
        live_bg=214,
        rec_fg=16,
        rec_bg=196,
        demo_fg=16,
        demo_bg=205,
        overlay_fg=231,
        overlay_bg=88,
    ),
    # Dub : bleu nuit, cyan électrique, magenta sound-system.
    Theme(
        name="dub",
        label="Dub",
        header_fg=51,
        header_bg=17,
        accent=45,
        dim=240,
        border=18,
        spectrum_low=18,
        spectrum_mid=39,
        spectrum_high=201,
        wave=51,
        title=51,
        vu_safe=45,
        vu_warn=201,
        vu_hot=196,
        live_fg=16,
        live_bg=45,
        rec_fg=16,
        rec_bg=196,
        demo_fg=16,
        demo_bg=51,
        overlay_fg=231,
        overlay_bg=17,
    ),
    # Menthe : vert d'eau profond vers menthe lumineuse.
    Theme(
        name="menthe",
        label="Menthe",
        header_fg=231,
        header_bg=29,
        accent=121,
        dim=242,
        border=29,
        spectrum_low=23,
        spectrum_mid=79,
        spectrum_high=195,
        wave=121,
        title=121,
        vu_safe=121,
        vu_warn=229,
        vu_hot=203,
        live_fg=16,
        live_bg=121,
        rec_fg=16,
        rec_bg=203,
        demo_fg=16,
        demo_bg=121,
        overlay_fg=231,
        overlay_bg=23,
    ),
    # Mono : gris échelle, contraste garanti sur tout terminal.
    Theme(
        name="mono",
        label="Mono",
        header_fg=231,
        header_bg=236,
        accent=250,
        dim=240,
        border=239,
        spectrum_low=240,
        spectrum_mid=250,
        spectrum_high=255,
        wave=252,
        title=255,
        vu_safe=250,
        vu_warn=255,
        vu_hot=196,
        live_fg=16,
        live_bg=250,
        rec_fg=16,
        rec_bg=196,
        demo_fg=16,
        demo_bg=250,
        overlay_fg=231,
        overlay_bg=236,
    ),
)

#: Thèmes indexés par nom (ordre de cycle préservé par THEME_NAMES).
THEMES: dict[str, Theme] = {theme.name: theme for theme in _THEME_LIST}

#: Noms de thèmes dans l'ordre de cycle (``roots`` -> ``sunset`` -> ...).
THEME_NAMES: tuple[str, ...] = tuple(theme.name for theme in _THEME_LIST)


# ---------------------------------------------------------------------------
# Palette xterm -> RGB (pour interpolation et fallback ANSI)
# ---------------------------------------------------------------------------
#: Les 16 premières couleurs xterm (ordre officiel xterm).
_XTERM_BASE16: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (128, 0, 0),
    (0, 128, 0),
    (128, 128, 0),
    (0, 0, 128),
    (128, 0, 128),
    (0, 128, 128),
    (192, 192, 192),
    (128, 128, 128),
    (255, 0, 0),
    (0, 255, 0),
    (255, 255, 0),
    (0, 0, 255),
    (255, 0, 255),
    (0, 255, 255),
    (255, 255, 255),
)

#: Les 6 niveaux du cube 6x6x6 xterm (16..231).
_XTERM_CUBE: tuple[int, ...] = (0, 95, 135, 175, 215, 255)


def xterm_rgb(index: int) -> tuple[int, int, int]:
    """Convertit un indice de palette xterm 256 en RGB (borné 0..255)."""
    i = int(index)
    if i < 0:
        i = 0
    elif i > 255:
        i = 255
    if i < 16:
        return _XTERM_BASE16[i]
    if i < 232:
        i -= 16
        return (
            _XTERM_CUBE[i // 36],
            _XTERM_CUBE[(i // 6) % 6],
            _XTERM_CUBE[i % 6],
        )
    level = 8 + (i - 232) * 10
    return (level, level, level)


@lru_cache(maxsize=1024)
def nearest_xterm(rgb: tuple[float, float, float]) -> int:
    """Indice xterm 256 le plus proche d'une couleur RGB (distance euclidienne)."""
    r, g, b = rgb
    best_index = 0
    best_distance = None
    for i in range(256):
        cr, cg, cb = xterm_rgb(i)
        distance = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = i
    return best_index


@lru_cache(maxsize=1024)
def nearest_basic(index: int, color_count: int = 8) -> int:
    """Convertit un indice xterm en couleur ANSI de base (0..7 ou 0..15).

    ``color_count`` est le nombre de couleurs annoncé par le terminal
    (``curses.COLORS``). Les 16 premières restent dans l'ordre xterm, ce qui
    correspond exactement aux couleurs curses ``COLOR_*``.
    """
    count = 16 if int(color_count) >= 16 else 8
    i = max(0, min(255, int(index)))
    if i < count:
        return i
    r, g, b = xterm_rgb(i)
    best_index = 0
    best_distance = None
    for candidate in range(count):
        cr, cg, cb = _XTERM_BASE16[candidate]
        distance = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = candidate
    return best_index


def adapt_color(index: int, color_count: int) -> int:
    """Adapte un indice xterm au nombre de couleurs disponibles.

    Renvoie ``-1`` tel quel (fond par défaut) ; sinon un indice valide pour
    ``curses.init_pair``.
    """
    if index is None:  # type: ignore[comparison-overlap]
        return -1
    i = int(index)
    if i < 0:
        return -1
    if int(color_count) >= 256:
        return max(0, min(255, i))
    if int(color_count) < 8:
        # Pas assez de couleurs : la paire 0 / les attributs prendront le relais.
        return 0
    return nearest_basic(i, int(color_count))


# ---------------------------------------------------------------------------
# Dégradé vertical du spectre
# ---------------------------------------------------------------------------
def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def theme_gradient_rgb(theme: Theme, t: float) -> tuple[float, float, float]:
    """Couleur RGB interpolée du dégradé du thème, ``t`` dans ``[0, 1]``.

    ``0`` = graves (bas du spectre), ``0.5`` = arrêt médian, ``1`` = aigus.
    """
    if t <= 0.0:
        t = 0.0
    elif t >= 1.0:
        t = 1.0
    else:
        t = float(t)
    low, mid, high = theme.stops
    if t <= 0.5:
        a, b = low, mid
        local = t * 2.0
    else:
        a, b = mid, high
        local = (t - 0.5) * 2.0
    ra, ga, ba = xterm_rgb(a)
    rb, gb, bb = xterm_rgb(b)
    return (
        _lerp(ra, rb, local),
        _lerp(ga, gb, local),
        _lerp(ba, bb, local),
    )


@lru_cache(maxsize=512)
def _rounded_rgb(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    # Petit cache d'arrondi : évite de recalculer un indice pour la même teinte.
    return (round(rgb[0], 2), round(rgb[1], 2), round(rgb[2], 2))


def theme_gradient(theme: Theme, t: float) -> int:
    """Indice xterm 256 du dégradé du thème à la position ``t``.

    Le résultat est interpolé puis projeté sur la palette ; les bornes
    ``t=0`` et ``t=1`` redonnent exactement les arrêts du thème.
    """
    if t <= 0.0:
        return theme.spectrum_low
    if t >= 1.0:
        return theme.spectrum_high
    if abs(t - 0.5) < 1e-12:
        return theme.spectrum_mid
    return nearest_xterm(_rounded_rgb(theme_gradient_rgb(theme, t)))


@lru_cache(maxsize=64)
def gradient_steps(theme: Theme, levels: int = GRADIENT_LEVELS) -> tuple[int, ...]:
    """``levels`` couleurs xterm régulièrement espacées le long du dégradé."""
    if levels <= 1:
        return (theme.spectrum_low,)
    return tuple(
        theme_gradient(theme, i / (levels - 1)) for i in range(levels)
    )


def gradient_level(t: float, levels: int = GRADIENT_LEVELS) -> int:
    """Palier du dégradé (0 .. levels-1) pour une position verticale."""
    if levels <= 1:
        return 0
    t = 0.0 if t <= 0.0 else (1.0 if t >= 1.0 else float(t))
    return int(round(t * (levels - 1)))


# ---------------------------------------------------------------------------
# Gestionnaire de paires curses
# ---------------------------------------------------------------------------
class ColorManager:
    """Alloue et mémorise les paires de couleurs de façon tolérante.

    * Aucune exception ne sort d'ici (terminal exotique, pas de couleur,
      ``use_default_colors`` indisponible, paires épuisées...).
    * En mode mono (ou avant ``initscr``), toutes les paires valent ``0``.
    """

    __slots__ = (
        "colors",
        "pair_count",
        "default_bg",
        "_cache",
        "_next_pair",
        "_init_done",
        "_mono",
    )

    def __init__(self) -> None:
        self.colors = 0
        self.pair_count = 0
        self.default_bg = False
        self._cache: dict[tuple[int, int], int] = {}
        self._next_pair = 1
        self._init_done = False
        self._mono = True

    # ------------------------------------------------------------------ init
    def init(self) -> None:
        """Initialise curses couleur ; totalement tolérant aux erreurs."""
        if self._init_done:
            return
        self._init_done = True
        if curses is None:  # pragma: no cover
            return
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            self.colors = int(getattr(curses, "COLORS", 0) or 0)
            self.pair_count = int(getattr(curses, "COLOR_PAIRS", 0) or 0)
        except Exception:
            self.colors = 0
            self.pair_count = 0
            return
        if self.colors < 8:
            self.colors = 0
            return
        self._mono = False
        try:
            # Fond transparent : le terminal garde son propre fond.
            curses.use_default_colors()
            self.default_bg = True
        except Exception:
            self.default_bg = False

    @property
    def mono(self) -> bool:
        """``True`` si le rendu doit se rabattre sur la paire 0."""
        return self._mono

    # ------------------------------------------------------------- frame
    def begin_frame(self) -> None:
        """Libère les paires si le terminal en offre peu.

        Le rendu repeint **toute** la surface à chaque frame : réattribuer des
        identifiants de paires entre deux frames est donc sans risque. Cela
        évite l'épuisement des 64 paires d'un terminal 8 couleurs après
        plusieurs changements de thème.
        """
        if self._mono or self.colors <= 0 or self.pair_count <= 0:
            return
        budget = max(8, int(self.pair_count * 0.6))
        if self._next_pair > budget:
            self._cache.clear()
            self._next_pair = 1

    # ------------------------------------------------------------------ pair
    def pair(self, fg: int, bg: int | None = None) -> int:
        """Retourne un identifiant de paire (fg sur bg), ou 0 en secours."""
        if self._mono or self.colors <= 0:
            return 0
        fg_index = adapt_color(fg, self.colors)
        if bg is None:
            bg_index = -1 if self.default_bg else 0
        elif bg < 0:
            bg_index = -1
        else:
            bg_index = adapt_color(bg, self.colors)
        key = (fg_index, bg_index)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        # Paire 0 = couleurs par défaut ; on n'y touche jamais.
        limit = max(0, self.pair_count - 1)
        if self._next_pair > limit:
            return 0
        pair_id = self._next_pair
        self._next_pair += 1
        try:
            curses.init_pair(pair_id, fg_index, bg_index)
        except Exception:
            # Paire invalide : on continue sans couleur plutôt que planter.
            return 0
        self._cache[key] = pair_id
        return pair_id


__all__ = [
    "GRADIENT_LEVELS",
    "PRIMARY_THEME",
    "THEMES",
    "THEME_ALIASES",
    "THEME_NAMES",
    "ColorManager",
    "Theme",
    "adapt_color",
    "gradient_level",
    "gradient_steps",
    "nearest_basic",
    "nearest_xterm",
    "theme_gradient",
    "theme_gradient_rgb",
    "xterm_rgb",
]
