"""Tests HTTP du serveur UI — personal_memory_mcp.ui.serveur.

Aucune dépendance externe : sqlite3 en mémoire, urllib pour les requêtes HTTP,
serveur démarré dans un thread de fond sur un port libre choisi dynamiquement.
"""

import json
from http.server import HTTPServer
from unittest.mock import patch

from personal_memory_mcp.ui import serveur as module_serveur
from tests.conftest_ui import ServeurContexte, creer_storage_memoire, inserer_fait, port_libre


class TestServeurUI:
    """Tests HTTP du serveur local — aucune dépendance externe."""

    # ------------------------------------------------------------------
    # GET /
    # ------------------------------------------------------------------

    def test_get_racine_statut_200(self):
        """GET / doit retourner 200."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            code, _, _ = ctx.get("/")
        assert code == 200

    def test_get_racine_content_type_html(self):
        """GET / doit retourner Content-Type text/html."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            _, content_type, _ = ctx.get("/")
        assert "text/html" in content_type

    def test_get_racine_body_contient_titre(self):
        """GET / doit retourner la page HTML avec le titre 'Personal Memory'."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            _, _, body = ctx.get("/")
        assert b"Personal Memory" in body

    def test_get_index_html_alias(self):
        """GET /index.html doit retourner la même page que GET /."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            code, content_type, body = ctx.get("/index.html")
        assert code == 200
        assert "text/html" in content_type
        assert b"Personal Memory" in body

    # ------------------------------------------------------------------
    # GET /api/faits
    # ------------------------------------------------------------------

    def test_api_faits_db_vide(self):
        """GET /api/faits avec DB vide → {faits: [], total: 0, par_categorie: {}}."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            code, content_type, body = ctx.get("/api/faits")

        assert code == 200
        assert "application/json" in content_type
        donnees = json.loads(body)
        assert donnees["faits"] == []
        assert donnees["total"] == 0
        assert donnees["par_categorie"] == {}

    def test_api_faits_avec_trois_faits(self):
        """GET /api/faits avec 3 faits → total=3, par_categorie correct."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "J'utilise Python 3.13", "stack")
        inserer_fait(storage, "Projet personal-memory actif", "projet")
        inserer_fait(storage, "J'utilise uv comme gestionnaire", "stack")

        with ServeurContexte(storage) as ctx:
            code, _, body = ctx.get("/api/faits")

        assert code == 200
        donnees = json.loads(body)
        assert donnees["total"] == 3
        assert len(donnees["faits"]) == 3
        assert donnees["par_categorie"].get("stack") == 2
        assert donnees["par_categorie"].get("projet") == 1

    def test_api_faits_champs_presents(self):
        """Chaque fait retourné doit avoir les champs id, contenu, categorie, source, date_creation."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "Un fait avec tous ses champs", "meta")

        with ServeurContexte(storage) as ctx:
            _, _, body = ctx.get("/api/faits")

        fait = json.loads(body)["faits"][0]
        for champ in ("id", "contenu", "categorie", "source", "date_creation"):
            assert champ in fait, f"Champ manquant : {champ}"

    def test_api_faits_encodage_utf8(self):
        """Les faits avec accents et caractères spéciaux doivent être correctement encodés."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "Été 2026 : préférence pour l'écriture française €", "preference")

        with ServeurContexte(storage) as ctx:
            _, content_type, body = ctx.get("/api/faits")

        assert "utf-8" in content_type.lower()
        donnees = json.loads(body.decode("utf-8"))
        assert "Été" in donnees["faits"][0]["contenu"]
        assert "€" in donnees["faits"][0]["contenu"]

    # ------------------------------------------------------------------
    # DELETE /api/faits/{id}
    # ------------------------------------------------------------------

    def test_delete_fait_existant(self):
        """DELETE /api/faits/{id} existant → 200 avec {"ok": true}."""
        storage = creer_storage_memoire()
        fait_id = inserer_fait(storage, "Fait à supprimer", "test")

        with ServeurContexte(storage) as ctx:
            code, body = ctx.delete(f"/api/faits/{fait_id}")

        assert code == 200
        assert json.loads(body) == {"ok": True}

    def test_delete_fait_existant_absent_ensuite(self):
        """Après DELETE réussi, le fait ne doit plus apparaître dans /api/faits."""
        storage = creer_storage_memoire()
        fait_id = inserer_fait(storage, "Fait supprimé", "test")

        with ServeurContexte(storage) as ctx:
            ctx.delete(f"/api/faits/{fait_id}")
            _, _, body = ctx.get("/api/faits")

        ids_restants = [f["id"] for f in json.loads(body)["faits"]]
        assert fait_id not in ids_restants

    def test_delete_fait_inexistant(self):
        """DELETE /api/faits/{id} inexistant → 404 avec {"ok": false}."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            code, body = ctx.delete("/api/faits/99999")

        assert code == 404
        assert json.loads(body) == {"ok": False}

    def test_delete_id_non_numerique(self):
        """DELETE /api/faits/abc (id non numérique) → 400."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            code, _ = ctx.delete("/api/faits/abc")

        assert code == 400

    # ------------------------------------------------------------------
    # Routes inconnues
    # ------------------------------------------------------------------

    def test_route_inconnue_404(self):
        """GET sur une route inconnue doit retourner 404."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            code, _, _ = ctx.get("/nope")
        assert code == 404

    def test_route_api_inconnue_404(self):
        """GET /api/inconnu → 404."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            code, _, _ = ctx.get("/api/inconnu")
        assert code == 404


class TestLancerFonction:
    """Tests de la fonction lancer() — vérifie que webbrowser.open est appelé."""

    def test_lancer_ouvre_navigateur(self):
        """lancer() doit appeler webbrowser.open avec l'URL correcte."""
        port = port_libre()
        appels_open: list[str] = []

        def _fake_open(url: str) -> None:
            appels_open.append(url)

        class _HTTPServerCapture(HTTPServer):
            def serve_forever(self) -> None:  # type: ignore[override]
                raise KeyboardInterrupt

        with (
            patch("webbrowser.open", side_effect=_fake_open),
            patch.object(module_serveur, "_get_storage", return_value=creer_storage_memoire()),
            patch("personal_memory_mcp.ui.serveur.HTTPServer", _HTTPServerCapture),
        ):
            module_serveur.lancer(port)

        assert any(str(port) in url for url in appels_open), (
            f"webbrowser.open non appelé avec le port {port}. Appels : {appels_open}"
        )
