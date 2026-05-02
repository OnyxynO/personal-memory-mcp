"""Helpers partagés pour les tests UI (serveur HTTP + navigation Playwright)."""

import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

import sqlite_vec

from personal_memory_mcp.memory.storage import Storage, SCHEMA_SQL_BASE
from personal_memory_mcp.ui import serveur as module_serveur


def creer_storage_memoire() -> Storage:
    """Crée un Storage en mémoire — même pattern que test_deduplication.py."""
    with patch.object(Path, "mkdir"):
        storage = Storage.__new__(Storage)
    storage._chemin = Path(":memory:")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(SCHEMA_SQL_BASE)
    conn.commit()
    storage._conn = conn
    storage._dim = 0
    return storage


def port_libre() -> int:
    """Trouve un port TCP libre sur localhost."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def inserer_fait(
    storage: Storage,
    contenu: str,
    categorie: str,
    source: str = "test",
    date: str = "2026-01-15T10:00:00+00:00",
) -> int:
    """Insère un fait directement en SQL sans embedding."""
    curseur = storage._conn.execute(
        "INSERT INTO faits (contenu, categorie, source, date_creation) VALUES (?, ?, ?, ?)",
        (contenu, categorie, source, date),
    )
    storage._conn.commit()
    return curseur.lastrowid  # type: ignore[return-value]


class ServeurContexte:
    """Démarre le serveur HTTP dans un thread avec un Storage en mémoire.

    S'utilise comme gestionnaire de contexte :
        with ServeurContexte(storage) as ctx:
            ctx.get("/api/faits")
    """

    def __init__(self, storage: Storage):
        self._storage = storage
        self._port = port_libre()
        self._httpd: HTTPServer | None = None
        self._patcher = None

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self._port}"

    def __enter__(self) -> "ServeurContexte":
        self._patcher = patch.object(module_serveur, "_get_storage", return_value=self._storage)
        self._patcher.start()
        self._httpd = HTTPServer(("localhost", self._port), module_serveur._Handler)
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{self.base_url}/", timeout=0.2)
                break
            except Exception:
                time.sleep(0.05)
        return self

    def __exit__(self, *_) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._patcher:
            self._patcher.stop()

    def get(self, chemin: str) -> tuple[int, str, bytes]:
        """Effectue un GET et retourne (code, content_type, body)."""
        req = urllib.request.Request(f"{self.base_url}{chemin}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def delete(self, chemin: str) -> tuple[int, bytes]:
        """Effectue un DELETE et retourne (code, body)."""
        req = urllib.request.Request(f"{self.base_url}{chemin}", method="DELETE")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
