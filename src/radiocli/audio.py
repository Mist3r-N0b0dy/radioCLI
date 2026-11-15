"""Moteur audio : lecture du flux (mpv/ffplay), capture PCM (ffmpeg), ICY, REC.

Trois connexions indépendantes au flux, comme dans un lecteur de bureau :

* **mpv** (ou ffplay) joue le son sur le périphérique audio du système ;
* **ffmpeg** décode le même flux en PCM brut pour l'analyseur (visualiseur) ;
* un socket ICY récupère le titre du morceau en cours.

Avantage : couper le son (muet) ne fige pas le visualiseur, et les coupures
réseau sont rattrapées automatiquement par des superviseurs dédiés.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass

from .analysis import SpectrumAnalyzer
from .config import (
    RECORD_DIR,
    SAMPLE_RATE,
    STALL_TIMEOUT,
    USER_AGENT,
    Station,
)
from .icy import IcyMetadata

# 1024 trames stéréo 16 bits = 4096 octets (analyse toutes les ~23 ms,
# soit ~43 mises à jour/s : base d'un rendu fluide).
_BLOCK_BYTES = 4096

#: Cadence nominale de l'analyse : un bloc toutes les ~23 ms (43 Hz).
_ANALYSIS_PERIOD = (_BLOCK_BYTES // 4) / SAMPLE_RATE

#: Arriéré PCM conservé (≈ 0,28 s). En cas de burst Icecast (le serveur envoie
#: parfois plusieurs secondes d'audio d'un coup), on jette le surplus pour
#: rester en direct ; le reste est analysé à cadence régulière.
_MAX_PENDING_BYTES = _BLOCK_BYTES * 43


class _MpvIPC:
    """Petit client pour le socket IPC JSON de mpv (volume, muet, quit)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(self.path)
        self._sock = sock

    def send(self, command: list[object]) -> bool:
        payload = (json.dumps({"command": command}) + "\n").encode("utf-8")
        with self._lock:
            try:
                if self._sock is None:
                    self._connect()
                assert self._sock is not None
                self._sock.sendall(payload)
                return True
            except (OSError, AssertionError):
                self._close_locked()
                return False

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()


@dataclass(slots=True)
class _Recording:
    path: str
    process: subprocess.Popen[bytes]
    started_at: float


class AudioEngine:
    """Coordonne lecture, capture, métadonnées et enregistrement d'une station."""

    def __init__(
        self,
        station: Station,
        analyzer: SpectrumAnalyzer,
        volume: int = 80,
        play_audio: bool = True,
    ) -> None:
        self.station = station
        self.analyzer = analyzer
        self.metadata = IcyMetadata(station.url)
        self._volume = max(0, min(100, volume))
        self._muted = False
        self._play_audio = play_audio
        self._running = False
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self._player_name = ""
        self._player: subprocess.Popen[bytes] | None = None
        self._player_restarts = 0
        self._ipc_path: str | None = None
        self._ipc: _MpvIPC | None = None

        self._capture_proc: subprocess.Popen[bytes] | None = None
        self._capture_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._last_audio_ts = 0.0
        self._stream_started_ts = 0.0
        self._had_audio = False
        self._last_error = ""
        self._notes: deque[str] = deque(maxlen=8)

        self._recording: _Recording | None = None
        self._recording_interrupted = ""
        self._last_recording_path = ""
        self._started_at = time.monotonic()

    # --------------------------------------------------------------- cycle
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._stop.clear()
        self._started_at = time.monotonic()
        self._had_audio = False
        self._last_audio_ts = 0.0
        self._volume = min(100, max(0, self._volume))
        self._muted = False
        self._spawn_player()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="audio-capture", daemon=True
        )
        self._capture_thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._player_watchdog, name="player-watchdog", daemon=True
        )
        self._watchdog_thread.start()
        self.metadata.start()

    def stop(self) -> None:
        with self._lock:
            running = self._running
            self._running = False
        threads = (self._capture_thread, self._watchdog_thread)
        if not running and all(t is None or not t.is_alive() for t in threads):
            # Arrêt déjà effectué (ou jamais démarré) : on s'assure juste
            # qu'aucun enfant / socket ne traîne.
            self._terminate(self._player, timeout=1.0)
            self._player = None
            self._terminate(self._capture_proc, timeout=1.0)
            self._capture_proc = None
            self._cleanup_ipc_socket()
            return
        self._stop.set()

        # Chaque étape est protégée : le nettoyage doit aboutir même si un
        # signal ou une erreur survient pendant l'arrêt (fermeture du terminal).
        try:
            self.stop_recording()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.metadata.stop()
        except Exception:  # noqa: BLE001
            pass

        # Lecture : quitter proprement via IPC puis terminer.
        ipc = self._ipc
        if ipc is not None:
            try:
                ipc.send(["quit"])
            except Exception:  # noqa: BLE001
                pass
        self._terminate(self._player, timeout=2.0)
        self._player = None
        if ipc is not None:
            ipc.close()
        self._ipc = None

        self._terminate(self._capture_proc, timeout=2.0)
        self._capture_proc = None

        try:
            for thread in threads:
                if thread is not None and thread.is_alive():
                    thread.join(timeout=3.0)
        finally:
            self._cleanup_ipc_socket()
            self._capture_thread = None
            self._watchdog_thread = None
            with self._lock:
                self._last_audio_ts = 0.0

    def set_station(self, station: Station) -> None:
        """Change de station en redémarrant proprement tout le pipeline."""
        was_running = self._running
        self.stop()
        self.station = station
        self.metadata = IcyMetadata(station.url)
        self.analyzer.reset()
        self._had_audio = False
        self._last_error = ""
        self._notes.clear()
        if was_running:
            self.start()

    def toggle_pause_like(self) -> None:
        """Bascule muet (le flux continue, le visualiseur reste vivant)."""
        self.toggle_mute()

    # -------------------------------------------------------------- lecture
    def _spawn_player(self) -> None:
        self._terminate(self._player, timeout=1.0)
        if self._ipc is not None:
            self._ipc.close()
            self._ipc = None
        self._cleanup_ipc_socket()

        if not self._play_audio:
            self._player_name = ""
            self._player = None
            return

        if shutil.which("mpv"):
            self._player_name = "mpv"
            self._ipc_path = os.path.join(
                tempfile.gettempdir(), f"radiocli-{os.getpid()}.sock"
            )
            args = [
                "mpv",
                "--no-config",
                "--no-video",
                "--no-terminal",
                "--really-quiet",
                "--force-window=no",
                "--audio-display=no",
                "--no-resume-playback",
                "--cache=yes",
                "--cache-secs=8",
                "--network-timeout=20",
                f"--user-agent={USER_AGENT}",
                f"--volume={self._volume}",
                f"--mute={'yes' if self._muted else 'no'}",
                f"--input-ipc-server={self._ipc_path}",
                self.station.url,
            ]
            self._player = self._popen(args)
            self._ipc = _MpvIPC(self._ipc_path)
            self._start_player_state_sync()
        elif shutil.which("ffplay"):
            self._player_name = "ffplay"
            args = [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-volume",
                "0" if self._muted else str(self._volume),
                "-user_agent",
                USER_AGENT,
                self.station.url,
            ]
            self._player = self._popen(args)
        else:
            self._player_name = ""
            self._player = None

    @staticmethod
    def _popen(args: list[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _player_watchdog(self) -> None:
        """Relance mpv/ffplay s'il meurt (coupure réseau, erreur audio…)."""
        if not self._play_audio:
            return
        backoff = 1.0
        while not self._stop.wait(2.0):
            player = self._player
            if player is not None and player.poll() is not None:
                self._player_restarts += 1
                with self._lock:
                    self._notes.append("Audio player restarted")
                if self._stop.wait(backoff):
                    return
                self._spawn_player()
                backoff = min(backoff * 2.0, 15.0)
            else:
                backoff = 1.0

    def _start_player_state_sync(self) -> None:
        """Réapplique volume/muet dès que le socket IPC du nouveau mpv existe.

        Au redémarrage, mpv met quelques dizaines de ms à créer son socket :
        un envoi immédiat échouerait et l'utilisateur retrouverait le son
        alors que l'UI le croit muet. On réessaie donc brièvement en tâche de
        fond (et ``--mute``/``--volume`` couvrent déjà le cas nominal).
        """
        ipc = self._ipc
        if ipc is None:
            return

        def run() -> None:
            deadline = time.monotonic() + 3.0
            while not self._stop.is_set() and time.monotonic() < deadline:
                if os.path.exists(ipc.path):
                    volume_ok = ipc.send(["set_property", "volume", self._volume])
                    mute_ok = ipc.send(["set_property", "mute", self._muted])
                    if volume_ok and mute_ok:
                        return
                self._stop.wait(0.05)

        threading.Thread(target=run, name="mpv-state-sync", daemon=True).start()

    def volume_up(self, step: int = 5) -> int:
        self._volume = min(100, self._volume + step)
        if self._ipc is not None:
            self._ipc.send(["set_property", "volume", self._volume])
        return self._volume

    def volume_down(self, step: int = 5) -> int:
        self._volume = max(0, self._volume - step)
        if self._ipc is not None:
            self._ipc.send(["set_property", "volume", self._volume])
        return self._volume

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        if self._ipc is not None:
            self._ipc.send(["set_property", "mute", self._muted])
        return self._muted

    # -------------------------------------------------------------- capture
    def _capture_loop(self) -> None:
        backoff = 0.6
        while not self._stop.is_set():
            try:
                proc = self._spawn_capture()
            except OSError as exc:
                with self._lock:
                    self._last_error = f"ffmpeg not found: {exc}"
                    self._notes.append("ffmpeg not found · visualizer stopped")
                return
            started = time.monotonic()
            self._capture_proc = proc
            try:
                self._consume(proc)
            except Exception as exc:  # noqa: BLE001 - le thread doit survivre
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._terminate(proc, timeout=2.0)
                self._capture_proc = None
            if self._stop.is_set():
                return
            with self._lock:
                if self._had_audio:
                    self._notes.append("Stream dropped · reconnecting")
            if time.monotonic() - started > 20.0:
                backoff = 0.6
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 1.8, 8.0)

    def _spawn_capture(self) -> subprocess.Popen[bytes]:
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-user_agent",
            USER_AGENT,
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-rw_timeout",
            "15000000",
            "-i",
            self.station.url,
            "-vn",
            "-ac",
            "2",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        return subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )

    def _consume(self, proc: subprocess.Popen[bytes]) -> None:
        """Lit le PCM et alimente l'analyseur à cadence régulière (~43 Hz).

        Les paquets réseau/ffmpeg arrivent par rafales ; on les met en tampon
        puis on analyse **un bloc à la fois**, à intervalle fixe. On évite ainsi
        les gros à-coups de GIL (visualiseur figé) et on garde un spectre qui
        avance régulièrement, même si le flux arrive par bouffées.
        """
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        buffer = bytearray()
        last_data = time.monotonic()
        next_feed = time.monotonic()
        last_trim_note = 0.0

        def trim_backlog(now: float) -> None:
            nonlocal last_trim_note
            if len(buffer) <= _MAX_PENDING_BYTES:
                return
            excess = len(buffer) - _MAX_PENDING_BYTES
            drop = (excess + _BLOCK_BYTES - 1) // _BLOCK_BYTES * _BLOCK_BYTES
            drop = min(drop, len(buffer) - _BLOCK_BYTES)  # garde au moins un bloc
            if drop > 0:
                del buffer[:drop]
            if now - last_trim_note > 5.0:
                last_trim_note = now
                with self._lock:
                    self._notes.append("Audio backlog skipped (resync)")

        while not self._stop.is_set():
            now = time.monotonic()
            # Débit d'analyse régulier : un bloc toutes les ~23 ms.
            while len(buffer) >= _BLOCK_BYTES and now >= next_feed:
                trim_backlog(now)
                block = bytes(buffer[:_BLOCK_BYTES])
                del buffer[:_BLOCK_BYTES]
                self.analyzer.feed(block)
                next_feed += _ANALYSIS_PERIOD
                now = time.monotonic()
                if now - next_feed > 0.5:
                    # Grosse pause : ne pas tenter de rattraper l'arriéré.
                    next_feed = now
                    trim_backlog(now)
            trim_backlog(now)

            # Lecture rapide du tuyau (le PCM est mis en tampon, pas analysé ici).
            timeout = 0.02 if len(buffer) < _BLOCK_BYTES else max(
                0.0, min(0.02, next_feed - time.monotonic())
            )
            try:
                ready, _, _ = select.select([fd], [], [], timeout)
            except (OSError, ValueError):
                return
            if not ready:
                if time.monotonic() - last_data > STALL_TIMEOUT:
                    raise TimeoutError("no audio data for too long")
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            last_data = time.monotonic()
            buffer += data
            with self._lock:
                self._last_audio_ts = last_data
                if not self._had_audio:
                    self._had_audio = True
                    self._stream_started_ts = last_data
                    self._notes.append("Stream received · analyzing")
                    self._last_error = ""

    # ---------------------------------------------------------- enregistrer
    def take_recording_error(self) -> str:
        """Renvoie (et efface) un message d'interruption à afficher une fois."""
        message = self._recording_interrupted
        self._recording_interrupted = ""
        return message

    def toggle_recording(self) -> str:
        if self._recording is not None:
            return self.stop_recording() or ""
        return self.start_recording()

    def start_recording(self) -> str:
        os.makedirs(RECORD_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(
            RECORD_DIR, f"radiocli-{self.station.key}-{stamp}.mp3"
        )
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-nostdin",
            "-user_agent",
            USER_AGENT,
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            self.station.url,
            "-vn",
            "-c:a",
            "copy",
            "-f",
            "mp3",
            path,
        ]
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.3)
        if proc.poll() is not None:
            raise RuntimeError("ffmpeg could not start recording")
        self._recording = _Recording(path=path, process=proc, started_at=time.monotonic())
        self._last_recording_path = path
        return path

    def stop_recording(self) -> str | None:
        recording = self._recording
        if recording is None:
            return None
        self._recording = None
        try:
            recording.process.send_signal(signal.SIGINT)
            recording.process.wait(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            self._terminate(recording.process, timeout=1.0)
        return recording.path

    # ------------------------------------------------------------- propriétés
    @property
    def running(self) -> bool:
        return self._running

    @property
    def connected(self) -> bool:
        with self._lock:
            return (
                self._running
                and self._had_audio
                and time.monotonic() - self._last_audio_ts < 4.0
            )

    @property
    def had_audio(self) -> bool:
        with self._lock:
            return self._had_audio

    @property
    def title(self) -> str:
        return self.metadata.title

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def playback_backend(self) -> str:
        return self._player_name

    @property
    def recording(self) -> bool:
        recording = self._recording
        if recording is None:
            return False
        if recording.process.poll() is not None:
            # ffmpeg s'est arrêté tout seul : flux coupé, disque plein…
            self._recording = None
            # Un fichier vide laissé par un ffmpeg tué brutalement ne sert à rien.
            try:
                if os.path.getsize(recording.path) == 0:
                    os.unlink(recording.path)
            except OSError:
                pass
            self._recording_interrupted = "Recording interrupted (stream or disk?)"
            with self._lock:
                self._last_error = self._recording_interrupted
                self._notes.append("Recording interrupted")
            return False
        return True

    @property
    def recording_seconds(self) -> float:
        recording = self._recording
        if recording is None:
            return 0.0
        return time.monotonic() - recording.started_at

    @property
    def last_recording_path(self) -> str:
        return self._last_recording_path

    @property
    def last_error(self) -> str:
        with self._lock:
            if self._last_error:
                return self._last_error
            return self.metadata.last_error

    @property
    def status(self) -> str:
        if not self._running:
            return "Stopped"
        if not self._had_audio:
            return "Connecting to stream…"
        if not self.connected:
            return "Reconnecting to stream…"
        if not self._play_audio:
            return "Live · analysis only"
        if not self._player_name:
            return "Playback unavailable (mpv/ffplay missing)"
        return "Live"

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at

    @property
    def notes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._notes)

    # -------------------------------------------------------------- interne
    def _cleanup_ipc_socket(self) -> None:
        path = self._ipc_path
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    def _terminate(
        proc: subprocess.Popen[bytes] | None, timeout: float = 2.0
    ) -> None:
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
