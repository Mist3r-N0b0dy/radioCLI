"""Contrat de données entre le moteur audio et le rendu terminal.

Ce module est le point de rencontre entre ``audio.py`` / ``analysis.py``
(qui produisent une :class:`Frame`) et ``render.py`` (qui la dessine).
Il ne dépend ni de curses ni de subprocess : uniquement des dataclasses.

Toutes les valeurs de spectre sont normalisées dans ``[0.0, 1.0]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Nombre de bandes de spectre produites par l'analyseur.
BAND_COUNT = 64

# Nombre de points du signal temporel (oscilloscope).
WAVE_POINTS = 256


@dataclass(slots=True)
class Frame:
    """Photo instantanée de ce que l'interface doit afficher.

    Attributes
    ----------
    bars:
        ``BAND_COUNT`` valeurs de spectre lissées, graves -> aigus.
    peaks:
        ``BAND_COUNT`` valeurs de crêtes (peak-hold), même ordre.
    wave:
        ``WAVE_POINTS`` échantillons dans ``[-1.0, 1.0]`` (oscilloscope).
    rms:
        Niveau RMS global normalisé (0 = silence, 1 = plein).
    peak:
        Crête instantanée normalisée, toutes voies confondues.
    bass, mid, treble:
        Énergie normalisée par plage de fréquences.
    beat:
        Impulsion de tempo qui décroît de 1.0 à 0.0 (0 = pas de beat frais).
    title:
        Titre en cours de diffusion, ex. ``"Sai Sai - Revolt Dub"``.
        Chaîne vide si l'info n'est pas (encore) disponible.
    station:
        Nom de la station webradio en cours de lecture.
    subtitle:
        Accroche courte, ex. ``"Dub · Dancehall · Roots · Paris"``.
    bitrate:
        Libellé du débit, ex. ``"192 kbps"``. Chaîne vide si inconnu.
    listeners:
        Auditeurs connectés si l'info est disponible, sinon ``None``.
    elapsed:
        Secondes écoulées depuis le lancement de l'application.
    fps:
        Fréquence de rafraîchissement mesurée de l'interface.
    volume:
        Volume mpv 0-100, ou ``None`` si non contrôlable.
    muted:
        Muet ou non.
    recording:
        Enregistrement en cours ou non.
    recording_seconds:
        Durée de l'enregistrement en cours, en secondes.
    connected:
        ``False`` pendant une coupure / reconnexion du flux.
    status:
        Message d'état court à afficher (ex. ``"Reconnexion…"``).
    demo:
        Mode démonstration (audio simulé, pas de réseau).
    theme:
        Nom du thème actif (le rendu peut s'en servir pour l'en-tête).
    ts:
        ``time.monotonic()`` au moment de la production de la frame,
        utile pour les animations (marquee, pulsations, horloge).
    """

    bars: tuple[float, ...] = ()
    peaks: tuple[float, ...] = ()
    wave: tuple[float, ...] = ()
    rms: float = 0.0
    peak: float = 0.0
    peak_l: float = 0.0
    peak_r: float = 0.0
    bass: float = 0.0
    mid: float = 0.0
    treble: float = 0.0
    beat: float = 0.0
    title: str = ""
    station: str = "radioCLI"
    subtitle: str = "Dub · Dancehall · Roots · Paris"
    bitrate: str = ""
    listeners: int | None = None
    elapsed: float = 0.0
    fps: float = 0.0
    volume: int | None = None
    muted: bool = False
    recording: bool = False
    recording_seconds: float = 0.0
    connected: bool = True
    status: str = ""
    demo: bool = False
    theme: str = "roots"
    ts: float = 0.0
    extra: dict[str, object] = field(default_factory=dict)

    def sanitized(self) -> "Frame":
        """Retourne une copie où toutes les listes font exactement la bonne taille.

        Protège le rendu contre une frame partielle (démarrage, reconnexion…).
        """
        bars = tuple(self.bars) + (0.0,) * (BAND_COUNT - len(self.bars))
        peaks = tuple(self.peaks) + (0.0,) * (BAND_COUNT - len(self.peaks))
        wave = tuple(self.wave) + (0.0,) * (WAVE_POINTS - len(self.wave))
        return Frame(
            bars=bars[:BAND_COUNT],
            peaks=peaks[:BAND_COUNT],
            wave=wave[:WAVE_POINTS],
            rms=self.rms,
            peak=self.peak,
            peak_l=self.peak_l,
            peak_r=self.peak_r,
            bass=self.bass,
            mid=self.mid,
            treble=self.treble,
            beat=self.beat,
            title=self.title,
            station=self.station,
            subtitle=self.subtitle,
            bitrate=self.bitrate,
            listeners=self.listeners,
            elapsed=self.elapsed,
            fps=self.fps,
            volume=self.volume,
            muted=self.muted,
            recording=self.recording,
            recording_seconds=self.recording_seconds,
            connected=self.connected,
            status=self.status,
            demo=self.demo,
            theme=self.theme,
            ts=self.ts,
            extra=dict(self.extra),
        )


@dataclass(slots=True)
class SpectrumState:
    """Sortie brute de l'analyseur, avant assemblage d'une :class:`Frame`."""

    bars: tuple[float, ...]
    peaks: tuple[float, ...]
    wave: tuple[float, ...]
    rms: float
    peak: float
    peak_l: float
    peak_r: float
    bass: float
    mid: float
    treble: float
    beat: float
