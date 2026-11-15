"""Lecture des métadonnées ICY (titre du morceau) du flux Icecast.

Le flux est ouvert une seconde fois en HTTP brut avec l'en-tête
``Icy-MetaData: 1`` : le serveur insère alors, tous les ``icy-metaint`` octets
audio, un bloc de métadonnées contenant ``StreamTitle='Artiste - Titre';``.
On lit et jette l'audio, on ne garde que les titres. Reconnexion automatique.
"""

from __future__ import annotations

import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

from .config import USER_AGENT

_TITLE_RE = re.compile(rb"StreamTitle='(.*?)';", re.S)
_NAME_RE = re.compile(rb"icy-name:\s*(.+?)\r?\n", re.I)
_BR_RE = re.compile(rb"icy-br:\s*(\d+)", re.I)
_METAINT_RE = re.compile(rb"icy-metaint:\s*(\d+)", re.I)
_STATUS_RE = re.compile(rb"^(?:HTTP/\d(?:\.\d)?|ICY)\s+(\d{3})")
_LOCATION_RE = re.compile(rb"^location:\s*(.+?)\r?$", re.I | re.M)


class IcyMetadata:
    """Suit le ``StreamTitle`` d'un flux Icecast/Shoutcast en arrière-plan."""

    def __init__(self, url: str, user_agent: str = USER_AGENT) -> None:
        self.url = url
        self._user_agent = user_agent
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._title = ""
        self._title_at = 0.0
        self._icy_name = ""
        self._bitrate: int | None = None
        self._connected = False
        self._last_error = ""
        self._generation = 0

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        """Démarre (ou redémarre) le thread de suivi."""
        self.stop()
        self._stop = threading.Event()
        self._generation += 1
        generation = self._generation
        self._thread = threading.Thread(
            target=self._run, args=(generation,), name="icy-metadata", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Arrête proprement le thread et ferme la connexion."""
        self._stop.set()
        self._generation += 1
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        with self._lock:
            self._connected = False

    @property
    def title(self) -> str:
        with self._lock:
            return self._title

    @title.setter
    def title(self, value: str) -> None:
        with self._lock:
            self._title = value
            self._title_at = time.monotonic()

    @property
    def icy_name(self) -> str:
        with self._lock:
            return self._icy_name

    @property
    def bitrate_kbps(self) -> int | None:
        with self._lock:
            return self._bitrate

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # -------------------------------------------------------------- interne
    def _run(self, generation: int) -> None:
        backoff = 1.0
        while not self._stop.is_set() and generation == self._generation:
            started = time.monotonic()
            try:
                self._stream_once()
            except Exception as exc:  # réseau, DNS, timeout…
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._connected = False
            if self._stop.wait(backoff) or generation != self._generation:
                return
            # Si la connexion a tenu longtemps, on repart vite.
            if time.monotonic() - started > 30.0:
                backoff = 1.0
            else:
                backoff = min(backoff * 2.0, 10.0)

    def _stream_once(self, depth: int = 0) -> None:
        parts = urlsplit(self.url)
        host = parts.hostname or ""
        secure = parts.scheme == "https"
        port = parts.port or (443 if secure else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        sock = socket.create_connection((host, port), timeout=8.0)
        if secure:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        self._sock = sock
        request = (
            f"GET {path} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: {self._user_agent}\r\n"
            "Accept: */*\r\n"
            "Icy-MetaData: 1\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("latin-1"))
        sock.settimeout(8.0)

        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed before headers")
            head += chunk
            if len(head) > 65536:
                raise ConnectionError("ICY headers too large")
        head, buffered = head.split(b"\r\n\r\n", 1)

        status = _STATUS_RE.search(head)
        code = int(status.group(1)) if status else 0
        if code in (301, 302, 303, 307, 308):
            location = _LOCATION_RE.search(head)
            if location is not None and depth < 3:
                target = urljoin(
                    self.url, location.group(1).decode("latin-1").strip()
                )
                try:
                    sock.close()
                except OSError:
                    pass
                self._sock = None
                self.url = target
                return self._stream_once(depth + 1)
        if status is None or code >= 400:
            raise ConnectionError(
                f"HTTP {status.group(1).decode() if status else '?'} for {self.url}"
            )

        name_match = _NAME_RE.search(head)
        br_match = _BR_RE.search(head)
        with self._lock:
            if name_match:
                self._icy_name = name_match.group(1).decode("latin-1").strip()
            if br_match:
                self._bitrate = int(br_match.group(1))
            self._connected = True
            self._last_error = ""

        metaint_match = _METAINT_RE.search(head)
        metaint = int(metaint_match.group(1)) if metaint_match else 0
        if metaint <= 0:
            # Pas de métadonnées : on garde juste la connexion en vie.
            while not self._stop.is_set():
                if not sock.recv(8192):
                    return
            return

        data = buffered
        while not self._stop.is_set():
            # Jette ``metaint`` octets audio.
            need = metaint - len(data)
            while need > 0:
                chunk = sock.recv(min(65536, need))
                if not chunk:
                    return
                need -= len(chunk)
            data = b""

            length_byte = self._read_exact(sock, 1)
            if length_byte is None:
                return
            meta_len = length_byte[0] * 16
            if meta_len:
                meta = self._read_exact(sock, meta_len)
                if meta is None:
                    return
                self._parse_meta(meta)
            sock.settimeout(15.0)
        sock.settimeout(15.0)

    @staticmethod
    def _read_exact(sock: socket.socket, size: int) -> bytes | None:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _parse_meta(self, meta: bytes) -> None:
        match = _TITLE_RE.search(meta)
        if not match:
            return
        raw = match.group(1).strip(b"\x00 ")
        if not raw:
            return
        title = _decode_text(raw)
        with self._lock:
            if title and title != self._title:
                self._title = title
                self._title_at = time.monotonic()


def _decode_text(raw: bytes) -> str:
    """Décode un titre ICY (souvent UTF-8, parfois latin-1).

    Les caractères de contrôle sont neutralisés : un titre malformé ne doit
    pas pouvoir casser le rendu curses.
    """
    text = ""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1", "replace")
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(cleaned.split()).strip()
