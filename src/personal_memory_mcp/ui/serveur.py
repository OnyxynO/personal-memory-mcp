"""Serveur HTTP local pour l'interface web mmcp ui."""

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


def _get_storage():
    from personal_memory_mcp.memory.storage import Storage
    return Storage(Path.home() / ".personal-memory" / "memory.db")


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silencieux — override pour désactiver les logs HTTP

    def _json(self, code: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            body = _HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/faits":
            storage = _get_storage()
            stats = storage.compter()
            faits = storage.lister(limite=stats["total"] or 1000)
            self._json(200, {"faits": faits, "total": stats["total"], "par_categorie": stats["par_categorie"]})
            return

        self.send_error(404)

    def do_DELETE(self) -> None:
        if self.path.startswith("/api/faits/"):
            try:
                fait_id = int(self.path.split("/")[-1])
            except ValueError:
                self.send_error(400)
                return
            storage = _get_storage()
            ok = storage.supprimer(fait_id)
            self._json(200 if ok else 404, {"ok": ok})
            return
        self.send_error(404)


def lancer(port: int = 8766) -> None:
    """Lance le serveur HTTP et ouvre le navigateur."""
    url = f"http://localhost:{port}"
    serveur = HTTPServer(("localhost", port), _Handler)
    print(f"Interface web : {url}  (Ctrl+C pour quitter)")
    webbrowser.open(url)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
