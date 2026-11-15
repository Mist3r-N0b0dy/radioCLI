"""État du menu modal et filtre de stations (logique pure, sans curses).

Le menu est volontairement séparé du rendu : il ne contient que des données et
des règles de navigation/filtrage, ce qui le rend testable sans terminal.
``app.py`` gère les touches et ``render.py`` dessine l'overlay.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .config import GENRES, STATIONS, Station, genre_label

PAGE_MAIN = "main"
PAGE_STATIONS = "stations"

#: Options du menu principal : (touche, libellé, aide).
MAIN_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("s", "Stations", "choose a station (with filter)"),
    ("t", "Next theme", "visualizer colors"),
    ("v", "Next visual", "12 animated styles"),
    ("a", "Auto change", "random visual every 10–30 s"),
    ("c", "Reactive colors", "palette driven by the music"),
)

_MAX_QUERY = 48


def normalize(text: object) -> str:
    """Minuscule + suppression des accents (Générations -> generations)."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()


def station_matches(station: Station, query: str) -> bool:
    """Vrai si tous les mots de ``query`` apparaissent dans la station."""
    tokens = normalize(query).split()
    if not tokens:
        return True
    haystack = normalize(
        " ".join(
            (
                station.key,
                station.name,
                station.genre,
                genre_label(station.genre),
                station.subtitle,
            )
        )
    )
    return all(token in haystack for token in tokens)


def filter_stations(query: str) -> tuple[Station, ...]:
    """Stations correspondant au filtre, dans l'ordre de STATIONS."""
    if not normalize(query).strip():
        return STATIONS
    return tuple(station for station in STATIONS if station_matches(station, query))


@dataclass(slots=True)
class MenuRow:
    """Ligne affichable dans le sélecteur (en-tête de genre ou station)."""

    kind: str  # "header" | "station"
    label: str
    station: Station | None = None


@dataclass
class MenuState:
    """État du menu : ouvert/fermé, page courante, filtre et sélection."""

    open: bool = False
    page: str = PAGE_MAIN
    query: str = ""
    selected: int = 0
    auto_visual: bool = False
    reactive: bool = False

    # ------------------------------------------------------------- ouverture
    def open_main(self) -> None:
        self.open = True
        self.page = PAGE_MAIN
        self.query = ""
        self.selected = 0

    def open_stations(self) -> None:
        self.open = True
        self.page = PAGE_STATIONS
        self.query = ""
        self.selected = 0

    def close(self) -> None:
        self.open = False
        self.page = PAGE_MAIN
        self.query = ""
        self.selected = 0

    def back(self) -> None:
        """Échap : retour au menu principal depuis le sélecteur, sinon ferme."""
        if self.page == PAGE_STATIONS:
            self.page = PAGE_MAIN
            self.query = ""
            self.selected = 0
        else:
            self.close()

    # --------------------------------------------------------------- filtre
    def filtered(self) -> tuple[Station, ...]:
        return filter_stations(self.query)

    def append_char(self, char: str) -> None:
        if not char or not char.isprintable() or len(self.query) >= _MAX_QUERY:
            return
        self.query += char
        self.selected = 0

    def backspace(self) -> None:
        if self.query:
            self.query = self.query[:-1]
            self.selected = 0

    # ------------------------------------------------------------- sélection
    def current_station(self) -> Station | None:
        stations = self.filtered()
        if not stations:
            return None
        return stations[max(0, min(self.selected, len(stations) - 1))]

    def ensure_selection(self) -> None:
        total = len(self.filtered())
        self.selected = 0 if total <= 0 else max(0, min(self.selected, total - 1))

    def move(self, delta: int) -> None:
        total = len(self.filtered())
        if total <= 0:
            self.selected = 0
            return
        self.selected = (self.selected + int(delta)) % total

    # ----------------------------------------------------------------- lignes
    def rows(self) -> list[MenuRow]:
        """Lignes groupées par genre (en-têtes omis si le groupe est vide)."""
        stations = self.filtered()
        rows: list[MenuRow] = []
        for genre, label in GENRES:
            group = [station for station in stations if station.genre == genre]
            if not group:
                continue
            rows.append(MenuRow(kind="header", label=label.upper()))
            for station in group:
                rows.append(
                    MenuRow(
                        kind="station",
                        label=f"{station.key:<12} {station.name}  {station.bitrate} kbps",
                        station=station,
                    )
                )
        return rows

    def selected_row_index(self) -> int:
        """Index de la ligne sélectionnée dans ``rows()`` (0 par défaut)."""
        current = self.current_station()
        if current is None:
            return 0
        for index, row in enumerate(self.rows()):
            if row.station is current:
                return index
        return 0


__all__ = [
    "MAIN_ITEMS",
    "PAGE_MAIN",
    "PAGE_STATIONS",
    "MenuRow",
    "MenuState",
    "filter_stations",
    "normalize",
    "station_matches",
]
