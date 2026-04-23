"""Tests d'intégration MCP — list_facts avec pagination.

Deux niveaux :
- Tests sans API : appellent les fonctions MCP directement (pas de modèle requis).
- Tests avec haiku (ANTHROPIC_API_KEY requis) : extraction réelle via Claude haiku,
  puis vérification de la pagination end-to-end.

Lancer tous les tests :
    uv run pytest tests/test_integration_mcp.py -v

Lancer uniquement les tests sans API :
    uv run pytest tests/test_integration_mcp.py -v -m "not haiku"
"""

import json
import os
import random
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlite_vec

from personal_memory_mcp.extraction.base import (
    Conversation,
    ExtracteurBase,
    FaitExtrait,
)
from personal_memory_mcp.memory.deduplication import SEUIL_PAR_DEFAUT
from personal_memory_mcp.memory.service import MemoryService
from personal_memory_mcp.memory.storage import Storage, SCHEMA_SQL_BASE

DIM = 768


# ---------------------------------------------------------------------------
# Helpers partagés
# ---------------------------------------------------------------------------

def _creer_storage_memoire(dim: int = DIM) -> Storage:
    """Crée un Storage SQLite en mémoire sans toucher au système de fichiers."""
    with patch.object(Path, "mkdir"):
        storage = Storage.__new__(Storage)
    storage._chemin = Path(":memory:")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(SCHEMA_SQL_BASE)
    conn.executescript(f"CREATE VIRTUAL TABLE faits_vec USING vec0(embedding FLOAT[{dim}]);")
    conn.commit()
    storage._conn = conn
    storage._dim = dim
    return storage


def _vecteur_aleatoire(dim: int = DIM) -> list[float]:
    v = [random.gauss(0, 1) for _ in range(dim)]
    norme = sum(x * x for x in v) ** 0.5
    return [x / norme for x in v]


class _ExtracteurVecteursAleatoires(ExtracteurBase):
    """Extracteur factice : faits prédéfinis + embeddings aléatoires."""

    def __init__(self, faits: list[FaitExtrait]):
        self._faits = faits

    def extraire(self, conversation: Conversation) -> list[FaitExtrait]:  # noqa: ARG002
        return list(self._faits)

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        return [_vecteur_aleatoire() for _ in textes]


class _ServiceAvecExtracteur(MemoryService):
    """MemoryService avec storage mémoire et extracteur fourni."""

    def __init__(self, extracteur: ExtracteurBase):
        self._storage = _creer_storage_memoire()
        self._extracteur = extracteur
        self._seuil = SEUIL_PAR_DEFAUT


def _service_avec_faits(nb: int, categorie: str = "stack") -> MemoryService:
    """Crée un service avec nb faits insérés directement en base."""
    faits_predefinis = [
        FaitExtrait(contenu=f"Fait {i}", categorie=categorie, score_confiance=0.9)
        for i in range(nb)
    ]
    svc = _ServiceAvecExtracteur(_ExtracteurVecteursAleatoires(faits_predefinis))
    for i in range(nb):
        emb = _vecteur_aleatoire()
        svc._storage.inserer_fait(
            contenu=f"Fait de test numéro {i + 1}",
            categorie=categorie,
            source="integration-test",
            embedding=emb,
        )
    return svc


# ---------------------------------------------------------------------------
# Tests MCP directs (sans modèle)
# ---------------------------------------------------------------------------

class TestListFactsMcpSansModele:
    """Vérifie que list_facts MCP retourne bien un dict paginé."""

    def test_list_facts_retourne_dict_pagine(self):
        """list_facts() → dict avec clés faits, page, total_pages, total."""
        # On importe les fonctions MCP et on injecte un service de test
        import personal_memory_mcp.mcp.server as server_module

        svc = _service_avec_faits(12)
        ancien = server_module._service
        server_module._service = svc
        try:
            resultat = server_module.list_facts(page=1, taille_page=5)
        finally:
            server_module._service = ancien

        assert isinstance(resultat, dict), f"Attendu dict, reçu {type(resultat)}"
        assert "faits" in resultat
        assert "page" in resultat
        assert "total_pages" in resultat
        assert "total" in resultat
        assert resultat["total"] == 12
        assert resultat["total_pages"] == 3
        assert len(resultat["faits"]) == 5

    def test_list_facts_page_2(self):
        """Page 2 sur 3 → 5 faits différents de la page 1."""
        import personal_memory_mcp.mcp.server as server_module

        svc = _service_avec_faits(12)
        ancien = server_module._service
        server_module._service = svc
        try:
            p1 = server_module.list_facts(page=1, taille_page=5)
            p2 = server_module.list_facts(page=2, taille_page=5)
        finally:
            server_module._service = ancien

        ids_p1 = {f["id"] for f in p1["faits"]}
        ids_p2 = {f["id"] for f in p2["faits"]}
        assert ids_p1.isdisjoint(ids_p2), "Les pages partagent des faits"

    def test_list_facts_page_invalide(self):
        """page=0 → retourne {"erreur": ...}."""
        import personal_memory_mcp.mcp.server as server_module

        svc = _service_avec_faits(3)
        ancien = server_module._service
        server_module._service = svc
        try:
            resultat = server_module.list_facts(page=0, taille_page=10)
        finally:
            server_module._service = ancien

        assert "erreur" in resultat

    def test_list_facts_taille_page_invalide(self):
        """taille_page=0 → retourne {"erreur": ...}."""
        import personal_memory_mcp.mcp.server as server_module

        svc = _service_avec_faits(3)
        ancien = server_module._service
        server_module._service = svc
        try:
            resultat = server_module.list_facts(page=1, taille_page=0)
        finally:
            server_module._service = ancien

        assert "erreur" in resultat

    def test_list_facts_filtre_categorie(self):
        """Filtre categorie="projet" → total = faits projet seulement."""
        import personal_memory_mcp.mcp.server as server_module

        svc = _service_avec_faits(5, categorie="stack")
        for i in range(3):
            svc._storage.inserer_fait(
                contenu=f"Projet {i}",
                categorie="projet",
                source="integration-test",
                embedding=_vecteur_aleatoire(),
            )

        ancien = server_module._service
        server_module._service = svc
        try:
            resultat = server_module.list_facts(categorie="projet", page=1, taille_page=10)
        finally:
            server_module._service = ancien

        assert resultat["total"] == 3
        assert all(f["categorie"] == "projet" for f in resultat["faits"])


# ---------------------------------------------------------------------------
# Tests avec haiku (ANTHROPIC_API_KEY requis)
# ---------------------------------------------------------------------------

haiku_requis = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY non définie — tests haiku ignorés",
)


class ExtracteurHaiku(ExtracteurBase):
    """Extraction de faits via Claude haiku (API Anthropic réelle).

    Utilise des embeddings aléatoires (Claude ne fait pas d'embeddings).
    Réservé aux tests d'intégration qui vérifient l'extraction réelle.
    """

    MODELE = "claude-haiku-4-5-20251001"

    PROMPT = """Tu es un extracteur de mémoire personnelle.
Analyse cette conversation et extrais les faits mémorisables sur l'utilisateur.

Règles :
- Un fait = une phrase courte et autonome (~1 ligne)
- Uniquement ce qui sera utile dans de futures sessions
- Pas les faits éphémères (questions ponctuelles, bugs résolus, code ponctuel)
- Catégorie parmi : stack | projet | preference | decision | contrainte | contexte | autre

Retourne UNIQUEMENT un JSON valide, sans commentaire :
[{"contenu": "...", "categorie": "...", "score_confiance": 0.0}]

Conversation :
{texte}"""

    def __init__(self) -> None:
        import anthropic  # type: ignore[import-untyped]
        self._client = anthropic.Anthropic()

    def extraire(self, conversation: Conversation) -> list[FaitExtrait]:
        texte = "\n".join(
            f"{'Utilisateur' if m.role == 'user' else 'Assistant'}: {m.contenu[:500]}"
            for m in conversation.messages
        )
        reponse = self._client.messages.create(
            model=self.MODELE,
            max_tokens=512,
            messages=[{"role": "user", "content": self.PROMPT.format(texte=texte)}],
        )
        brut = reponse.content[0].text.strip()
        try:
            faits_json = json.loads(brut)
            return [
                FaitExtrait(
                    contenu=f["contenu"],
                    categorie=f.get("categorie", "autre"),
                    score_confiance=float(f.get("score_confiance", 0.8)),
                )
                for f in faits_json
                if isinstance(f, dict) and f.get("contenu")
            ]
        except (json.JSONDecodeError, KeyError):
            return []

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        # Claude ne fait pas d'embeddings — vecteurs aléatoires pour les tests
        return [_vecteur_aleatoire() for _ in textes]


@haiku_requis
class TestListFactsAvecHaiku:
    """Tests end-to-end : extraction haiku → stockage → pagination list_facts."""

    CONVERSATION_TEST = Conversation(
        source="test",
        source_detail="integration_haiku",
        messages=[
            __import__("personal_memory_mcp.extraction.base", fromlist=["Message"]).Message(
                role="user",
                contenu="Je travaille principalement avec Python et FastAPI. Mon projet actuel s'appelle DataMatch.",
                date=None,
            ),
            __import__("personal_memory_mcp.extraction.base", fromlist=["Message"]).Message(
                role="assistant",
                contenu="Je peux vous aider avec Python et FastAPI. DataMatch semble être un projet de data matching.",
                date=None,
            ),
        ],
    )

    def test_extraction_haiku_retourne_faits(self):
        """haiku extrait au moins 1 fait depuis la conversation de test."""
        extracteur = ExtracteurHaiku()
        faits = extracteur.extraire(self.CONVERSATION_TEST)

        assert len(faits) >= 1, f"haiku n'a extrait aucun fait : {faits}"
        assert all(hasattr(f, "contenu") and f.contenu for f in faits)

    def test_extraction_haiku_puis_list_pagine(self):
        """Extraction haiku → stockage → list_facts paginé fonctionne end-to-end."""
        import personal_memory_mcp.mcp.server as server_module

        extracteur = ExtracteurHaiku()
        svc = _ServiceAvecExtracteur(extracteur)

        # Extraire et stocker des faits via la conversation de test
        faits = extracteur.extraire(self.CONVERSATION_TEST)
        assert faits, "haiku n'a pas extrait de faits"

        for fait in faits:
            svc.add(fait.contenu, categorie=fait.categorie, source="haiku-test")

        # Tester list_facts via la fonction MCP
        ancien = server_module._service
        server_module._service = svc
        try:
            resultat = server_module.list_facts(page=1, taille_page=5)
        finally:
            server_module._service = ancien

        assert "faits" in resultat
        assert resultat["total"] >= 1
        assert len(resultat["faits"]) >= 1
