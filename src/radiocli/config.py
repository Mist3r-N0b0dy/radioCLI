"""Configuration des webradios et des paramètres de radioCLI.

radioCLI est un lecteur de webradios pour le terminal. Les stations sont
classées par genre ; la touche ``s`` change de station dans le même genre,
la touche ``S`` passe au genre suivant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Délai (s) sans données audio avant de considérer le flux en panne.
STALL_TIMEOUT = 8.0

# Cadence cible de l'interface (images/seconde) : 40 i/s donne un
# mouvement nettement plus fluide qu'à 30 sur les spectres animés.
UI_FPS = 40.0

# Taille d'un bloc PCM analysé (échantillons par voie).
ANALYSIS_BLOCK = 1024

# Fréquence d'échantillonnage de capture (Hz), stéréo 16 bits signé.
SAMPLE_RATE = 44100

# Dossier des enregistrements (créé au besoin).
RECORD_DIR = os.environ.get(
    "RADIOCLI_RECORD_DIR", os.path.expanduser("~/Musique/radioCLI")
)

#: Genres dans l'ordre de présentation / de cycle.
GENRES: tuple[tuple[str, str], ...] = (
    ("reggae", "Reggae"),
    ("rock", "Rock / Metal"),
    ("rap", "Rap FR"),
    ("variete", "French Variety"),
    ("public", "Public Radio"),
    ("electro", "Electro"),
    ("house", "House / Vocal"),
    ("techno", "Techno / Minimal"),
    ("dnb", "Drum & Bass"),
    ("eclectique", "Eclectic"),
)

GENRE_LABELS: dict[str, str] = dict(GENRES)


@dataclass(frozen=True, slots=True)
class Station:
    key: str
    name: str
    subtitle: str
    url: str
    bitrate: int
    genre: str = "autre"

    def label(self) -> str:
        return f"{self.name} ({self.bitrate} kbps)"


STATIONS: tuple[Station, ...] = (
    # ------------------------------------------------------------- Reggae
    Station(
        key="reggae",
        name="La Grosse Radio Reggae",
        subtitle="Dub · Dancehall · Roots · Paris",
        url="http://hd.lagrosseradio.info:8000/lagrosseradio-reggae-192.mp3",
        bitrate=192,
        genre="reggae",
    ),
    Station(
        key="reggae-64",
        name="La Grosse Radio Reggae",
        subtitle="Low bitrate · Dub · Roots",
        url="http://hd.lagrosseradio.info:8000/lagrosseradio-reggae-064.mp3",
        bitrate=64,
        genre="reggae",
    ),
    Station(
        key="fipreggae",
        name="FIP Reggae",
        subtitle="Public radio · Dub, roots, ska",
        url="https://icecast.radiofrance.fr/fipreggae-midfi.mp3",
        bitrate=128,
        genre="reggae",
    ),
    # -------------------------------------------------------- Rock / Metal
    Station(
        key="rock",
        name="La Grosse Radio Rock",
        subtitle="Alternative rock · Pop hits · Indie legends",
        url="http://hd.lagrosseradio.info:8000/lagrosseradio-rock-192.mp3",
        bitrate=192,
        genre="rock",
    ),
    Station(
        key="metal",
        name="La Grosse Radio Metal",
        subtitle="Metal · Heavy · Extreme",
        url="http://hd.lagrosseradio.info:8000/lagrosseradio-metal-192.mp3",
        bitrate=192,
        genre="rock",
    ),
    Station(
        key="fiprock",
        name="FIP Rock",
        subtitle="Public radio · Rock, garage, indie",
        url="https://icecast.radiofrance.fr/fiprock-midfi.mp3",
        bitrate=128,
        genre="rock",
    ),
    Station(
        key="fipmetal",
        name="FIP Metal",
        subtitle="Public radio · Metal, stoner, hardcore",
        url="https://icecast.radiofrance.fr/fipmetal-midfi.mp3",
        bitrate=128,
        genre="rock",
    ),
    # ------------------------------------------------------------ Rap FR
    Station(
        key="mouv",
        name="Mouv'",
        subtitle="Public radio · French rap, R&B, urban",
        url="https://icecast.radiofrance.fr/mouv-midfi.mp3",
        bitrate=128,
        genre="rap",
    ),
    Station(
        key="skyrock",
        name="Skyrock",
        subtitle="French rap · R&B · urban hits",
        url="https://icecast.skyrock.net/s/natio_mp3_128k",
        bitrate=128,
        genre="rap",
    ),
    Station(
        key="generations",
        name="Générations",
        subtitle="French rap · Hip-hop · R&B · 88.2",
        url="http://generationfm.ice.infomaniak.ch/generationfm-high.mp3",
        bitrate=128,
        genre="rap",
    ),
    # ------------------------------------------------------ Variété FR
    Station(
        key="mradio",
        name="M Radio",
        subtitle="French variety · French songs",
        url="https://mfm.ice.infomaniak.ch/mfm-128.mp3",
        bitrate=128,
        genre="variete",
    ),
    Station(
        key="cheriefm",
        name="Chérie FM",
        subtitle="French variety · Pop & love songs",
        url="https://cdnradio.streamakaci.com/cheriefm1.mp3",
        bitrate=128,
        genre="variete",
    ),
    Station(
        key="rfm",
        name="RFM",
        subtitle="French variety · Hits & legends",
        url="https://rfm.lmn.fm/rfm.mp3",
        bitrate=128,
        genre="variete",
    ),
    Station(
        key="nostalgie",
        name="Nostalgie",
        subtitle="French variety · 80s to 2000s",
        url="https://streaming.nrjaudio.fm/oug7girb92oc?origine=fluxradios",
        bitrate=128,
        genre="variete",
    ),
    Station(
        key="icichanson",
        name="ICI Chanson française",
        subtitle="Public radio · 100% French songs",
        url="https://icecast.radiofrance.fr/fbchansonfrancaise-midfi.mp3",
        bitrate=128,
        genre="variete",
    ),
    # ---------------------------------------------------- Radio publique
    Station(
        key="franceinter",
        name="France Inter",
        subtitle="Public radio · News, culture, music",
        url="https://icecast.radiofrance.fr/franceinter-midfi.mp3",
        bitrate=128,
        genre="public",
    ),
    Station(
        key="franceinfo",
        name="France Info",
        subtitle="Public radio · News 24/7",
        url="https://icecast.radiofrance.fr/franceinfo-midfi.mp3",
        bitrate=128,
        genre="public",
    ),
    Station(
        key="francemusique",
        name="France Musique",
        subtitle="Public radio · Classical, jazz, discoveries",
        url="https://icecast.radiofrance.fr/francemusique-midfi.mp3",
        bitrate=128,
        genre="public",
    ),
    Station(
        key="franceculture",
        name="France Culture",
        subtitle="Public radio · Culture, debate, knowledge",
        url="https://icecast.radiofrance.fr/franceculture-midfi.mp3",
        bitrate=128,
        genre="public",
    ),
    # ------------------------------------------------------------- Électro
    Station(
        key="fipelectro",
        name="FIP Electro",
        subtitle="Public radio · Electro, house, ambient",
        url="https://icecast.radiofrance.fr/fipelectro-midfi.mp3",
        bitrate=128,
        genre="electro",
    ),
    Station(
        key="radiofg",
        name="Radio FG",
        subtitle="Electro · house · Paris club",
        url="http://radiofg.impek.com/fg.mp3",
        bitrate=128,
        genre="electro",
    ),
    Station(
        key="meuh",
        name="Radio Meuh",
        subtitle="Eclectic electro · Haute-Savoie",
        url="http://radiomeuh.ice.infomaniak.ch/radiomeuh-128.mp3",
        bitrate=128,
        genre="electro",
    ),
    # --------------------------------------------------------- House / Vocal
    Station(
        key="fgdeep",
        name="FG Deep Dance",
        subtitle="Deep house · clubbing",
        url="https://radiofg.impek.com/fgd.mp3",
        bitrate=128,
        genre="house",
    ),
    Station(
        key="fgchic",
        name="FG Chic",
        subtitle="Vocal house · electro chic",
        url="http://radiofg.impek.com/fgc.mp3",
        bitrate=128,
        genre="house",
    ),
    Station(
        key="tldeep",
        name="Technolovers Deep House",
        subtitle="Deep house · selection",
        url="https://stream.technolovers.fm/deep-house?ref=radiobrowser",
        bitrate=192,
        genre="house",
    ),
    Station(
        key="pulsclub",
        name="PulsRadio Club",
        subtitle="House / dance · club",
        url="https://icecast.pulsradio.com/mxHD.mp3",
        bitrate=192,
        genre="house",
    ),
    # ------------------------------------------------------- Techno / Minimal
    Station(
        key="fgunderground",
        name="FG Underground",
        subtitle="Techno · underground",
        url="https://radiofg.impek.com/ufg.mp3",
        bitrate=128,
        genre="techno",
    ),
    Station(
        key="fgtechno",
        name="FG Techno",
        subtitle="Techno · club",
        url="http://stream.rcs.revma.com/1z19ak79n98uv",
        bitrate=128,
        genre="techno",
    ),
    Station(
        key="tlminimal",
        name="Technolovers Minimal",
        subtitle="Minimal · techno",
        url="https://stream.technolovers.fm/minimal?ref=radiobrowser",
        bitrate=192,
        genre="techno",
    ),
    # ------------------------------------------------------------ Drum & Bass
    Station(
        key="bassdrive",
        name="Bassdrive",
        subtitle="Drum & Bass · worldwide",
        url="http://ice.bassdrive.net/stream",
        bitrate=128,
        genre="dnb",
    ),
    Station(
        key="dnbradio",
        name="DNBRadio",
        subtitle="Drum & Bass · 24/7",
        url="https://azura.drmnbss.org/listen/dnbradio/radio.mp3",
        bitrate=128,
        genre="dnb",
    ),
    Station(
        key="edmdnb",
        name="DnB&EDM",
        subtitle="Drum & Bass / EDM",
        url="https://edmdnb.com:448/radio/8000/radio.mp3",
        bitrate=128,
        genre="dnb",
    ),
    # ------------------------------------------------------- Éclectique
    Station(
        key="fip",
        name="FIP",
        subtitle="Public radio · All styles, eclectic",
        url="https://icecast.radiofrance.fr/fip-midfi.mp3",
        bitrate=128,
        genre="eclectique",
    ),
    Station(
        key="fipjazz",
        name="FIP Jazz",
        subtitle="Radio publique · Jazz, soul, improvisation",
        url="https://icecast.radiofrance.fr/fipjazz-midfi.mp3",
        bitrate=128,
        genre="eclectique",
    ),
    Station(
        key="fiphiphop",
        name="FIP Hip-Hop",
        subtitle="Radio publique · Hip-hop, soul, beatmaking",
        url="https://icecast.radiofrance.fr/fiphiphop-midfi.mp3",
        bitrate=128,
        genre="eclectique",
    ),
    Station(
        key="fipworld",
        name="FIP World",
        subtitle="Radio publique · Musiques du monde",
        url="https://icecast.radiofrance.fr/fipworld-midfi.mp3",
        bitrate=128,
        genre="eclectique",
    ),
    Station(
        key="fipgroove",
        name="FIP Groove",
        subtitle="Radio publique · Funk, soul, groove",
        url="https://icecast.radiofrance.fr/fipgroove-midfi.mp3",
        bitrate=128,
        genre="eclectique",
    ),
    Station(
        key="fippop",
        name="FIP Pop",
        subtitle="Radio publique · Pop, indie, chanson",
        url="https://icecast.radiofrance.fr/fippop-midfi.mp3",
        bitrate=128,
        genre="eclectique",
    ),
    Station(
        key="nova",
        name="Radio Nova",
        subtitle="Eclectic · Groove, discoveries, new scene",
        url="https://novazz.ice.infomaniak.ch/novazz-128.mp3",
        bitrate=128,
        genre="eclectique",
    ),
)

DEFAULT_STATION = "fip"

USER_AGENT = "radiocli/1.0 (+terminal player)"


def genre_label(genre: str) -> str:
    """Libellé français d'un genre (retourne la clé si inconnu)."""
    return GENRE_LABELS.get(genre, genre)


def stations_in_genre(genre: str) -> tuple[Station, ...]:
    """Stations d'un genre, dans l'ordre de STATIONS."""
    return tuple(station for station in STATIONS if station.genre == genre)


def next_station(current: Station, *, same_genre: bool = True) -> Station:
    """Station suivante du même genre, ou première station du genre suivant."""
    if same_genre:
        group = stations_in_genre(current.genre)
        if len(group) > 1:
            try:
                index = group.index(current)
            except ValueError:
                index = -1
            return group[(index + 1) % len(group)]
    order = [key for key, _ in GENRES if stations_in_genre(key)]
    if not order or current.genre not in order:
        return STATIONS[0]
    index = order.index(current.genre)
    for step in range(1, len(order) + 1):
        genre = order[(index + step) % len(order)]
        for station in STATIONS:
            if station.genre == genre:
                return station
    return STATIONS[0]  # pragma: no cover - défensif


def station_by_key(key: str) -> Station:
    """Retourne la station demandée, avec correspondance tolérante."""
    wanted = key.strip().lower()
    for station in STATIONS:
        if station.key == wanted:
            return station
    # Alias historiques.
    if wanted in {"default", "defaut", "défaut"}:
        return station_by_key(DEFAULT_STATION)
    raise KeyError(
        f"Unknown station: {key!r}. Available: "
        + ", ".join(s.key for s in STATIONS)
    )


__all__ = [
    "ANALYSIS_BLOCK",
    "DEFAULT_STATION",
    "GENRES",
    "GENRE_LABELS",
    "RECORD_DIR",
    "SAMPLE_RATE",
    "STALL_TIMEOUT",
    "STATIONS",
    "Station",
    "UI_FPS",
    "USER_AGENT",
    "genre_label",
    "next_station",
    "station_by_key",
    "stations_in_genre",
]
