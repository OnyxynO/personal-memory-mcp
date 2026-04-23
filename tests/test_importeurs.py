"""Tests des importeurs Claude Code et Claude.

L'extracteur Ollama est remplacé par un ExtracteurMock — aucun appel réseau réel.
Le storage utilise sqlite3 + sqlite_vec en mémoire (:memory:).
"""

import io
import json
import random
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import patch

import sqlite_vec

from personal_memory_mcp.extraction.base import (
    Conversation,
    ExtracteurBase,
    FaitExtrait,
)
from personal_memory_mcp.importeurs.claude_code import ImporteurClaudeCode
from personal_memory_mcp.importeurs.claude import ImporteurClaude
from personal_memory_mcp.memory.deduplication import SEUIL_PAR_DEFAUT
from personal_memory_mcp.memory.service import MemoryService
from personal_memory_mcp.memory.storage import Storage, SCHEMA_SQL_BASE


# Chemin vers les fixtures
FIXTURES = Path(__file__).parent / "fixtures"
DIM = 768


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _creer_storage_memoire() -> Storage:
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
    conn.executescript(f"CREATE VIRTUAL TABLE faits_vec USING vec0(embedding FLOAT[{DIM}]);")
    conn.commit()
    storage._conn = conn
    storage._dim = DIM
    return storage


def _vecteur_aleatoire(dim: int = DIM) -> list[float]:
    """Génère un vecteur normalisé aléatoire."""
    v = [random.gauss(0, 1) for _ in range(dim)]
    norme = sum(x * x for x in v) ** 0.5
    return [x / norme for x in v]


class ExtracteurMock(ExtracteurBase):
    """Extracteur factice — retourne des faits prédéfinis et des embeddings aléatoires."""

    def __init__(self, faits: list[FaitExtrait] | None = None):
        # Faits retournés par défaut si non précisés
        self._faits = faits or [
            FaitExtrait(
                contenu="Développe principalement avec Python et FastAPI",
                categorie="stack",
                score_confiance=0.9,
            ),
            FaitExtrait(
                contenu="Utilise SQLite pour les projets personnels locaux",
                categorie="stack",
                score_confiance=0.85,
            ),
        ]

    def extraire(self, conversation: Conversation) -> list[FaitExtrait]:  # noqa: ARG002
        return list(self._faits)

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        # Un vecteur aléatoire unique par texte
        return [_vecteur_aleatoire() for _ in textes]


class ServiceMock(MemoryService):
    """Simule MemoryService avec un storage en mémoire et un extracteur mock.

    N'appelle pas super().__init__() pour éviter de créer des fichiers ou de
    contacter Ollama. Les attributs requis sont initialisés directement.
    """

    def __init__(self, extracteur: ExtracteurBase | None = None):
        # Bypass intentionnel du __init__ parent
        self._storage = _creer_storage_memoire()
        self._extracteur = extracteur or ExtracteurMock()
        self._seuil = SEUIL_PAR_DEFAUT


# ---------------------------------------------------------------------------
# Tests ImporteurClaudeCode
# ---------------------------------------------------------------------------

class TestImporteurClaudeCode:
    """Tests de l'importeur de sessions Claude Code (fichiers JSONL)."""

    def test_claude_code_import_basique(self, tmp_path):
        """Importer le fichier fixture JSONL → au moins 1 fait ajouté, 0 erreur."""
        # Copier la fixture dans un dossier temporaire
        fixture = FIXTURES / "claude_code_sample.jsonl"
        dest = tmp_path / "session.jsonl"
        dest.write_bytes(fixture.read_bytes())

        service = ServiceMock()
        importeur = ImporteurClaudeCode(service)
        resultat = importeur.importer(str(tmp_path))

        assert "erreur" not in resultat, f"Import inattendu en erreur : {resultat}"
        assert resultat["ajoutes"] >= 1, f"Aucun fait ajouté : {resultat}"
        assert resultat["nb_erreurs"] == 0, f"Des erreurs sont survenues : {resultat}"

    def test_claude_code_reponse_sans_doublon(self, tmp_path):
        """Deux imports successifs du même fichier → second import : ajoutes=0, dedupliques > 0."""
        fixture = FIXTURES / "claude_code_sample.jsonl"
        dest = tmp_path / "session.jsonl"
        dest.write_bytes(fixture.read_bytes())

        # L'extracteur mock retourne TOUJOURS les mêmes faits
        # Pour simuler la déduplication, on utilise des embeddings identiques
        faits_fixes = [
            FaitExtrait(
                contenu="Fait identique pour test de déduplication",
                categorie="stack",
                score_confiance=0.9,
            )
        ]
        # Vecteur fixe pour que la déduplication fonctionne
        vecteur_fixe = _vecteur_aleatoire()

        class ExtracteurEmbeddingFixe(ExtracteurBase):
            def extraire(self, conversation):
                return list(faits_fixes)
            def embeddings(self, textes):
                return [list(vecteur_fixe) for _ in textes]

        service = ServiceMock(extracteur=ExtracteurEmbeddingFixe())
        importeur = ImporteurClaudeCode(service)

        # Premier import
        resultat1 = importeur.importer(str(tmp_path))
        assert resultat1["ajoutes"] >= 1

        # Second import du même contenu avec mêmes embeddings
        resultat2 = importeur.importer(str(tmp_path))
        assert resultat2["ajoutes"] == 0, f"Des faits ont été ajoutés en double : {resultat2}"
        assert resultat2["dedupliques"] > 0, f"Aucun doublon détecté : {resultat2}"

    def test_claude_code_fichier_inexistant(self):
        """Chemin inexistant → résultat contient la clé 'erreur'."""
        service = ServiceMock()
        importeur = ImporteurClaudeCode(service)
        resultat = importeur.importer("/chemin/qui/nexiste/pas")

        assert "erreur" in resultat, f"Devrait contenir 'erreur' : {resultat}"

    def test_claude_code_dossier_vide(self, tmp_path):
        """Dossier sans .jsonl → résultat contient la clé 'erreur'."""
        service = ServiceMock()
        importeur = ImporteurClaudeCode(service)
        resultat = importeur.importer(str(tmp_path))

        assert "erreur" in resultat


# ---------------------------------------------------------------------------
# Tests ImporteurClaude (export ZIP officiel)
# ---------------------------------------------------------------------------

def _creer_zip_memories(memories_json: str) -> bytes:
    """Crée un ZIP en mémoire contenant uniquement memories.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr("memories.json", memories_json)
    return buffer.getvalue()


class TestImporteurClaude:
    """Tests de l'importeur d'export ZIP officiel Claude."""

    def test_claude_import_memories_json(self, tmp_path):
        """Importer un ZIP en mémoire depuis la fixture memories_sample.json → faits extraits > 0."""
        fixture = FIXTURES / "claude_memories_sample.json"
        memories_json = fixture.read_text(encoding="utf-8")

        # Créer le ZIP temporaire
        zip_path = tmp_path / "export_claude.zip"
        zip_path.write_bytes(_creer_zip_memories(memories_json))

        service = ServiceMock()
        importeur = ImporteurClaude(service)
        resultat = importeur.importer(str(zip_path))

        assert "erreur" not in resultat, f"Import inattendu en erreur : {resultat}"
        assert resultat["ajoutes"] > 0, f"Aucun fait ajouté depuis memories.json : {resultat}"

    def test_claude_import_memories_erreur_reseau(self, tmp_path):
        """Mock extracteur.embeddings() lève Exception → nb_erreurs > 0, import ne crash pas."""
        memories_json = json.dumps([
            {
                "conversations_memory": (
                    "**Work context**\n"
                    "- Développeur Python avec expérience en data engineering et APIs REST\n"
                    "- Utilise principalement FastAPI et SQLite pour les projets locaux\n"
                )
            }
        ])

        zip_path = tmp_path / "export_erreur.zip"
        zip_path.write_bytes(_creer_zip_memories(memories_json))

        class ExtracteurErreurReseau(ExtracteurBase):
            def extraire(self, conversation):
                return []
            def embeddings(self, textes):
                raise Exception("Ollama indisponible : connexion refusée")

        service = ServiceMock(extracteur=ExtracteurErreurReseau())
        importeur = ImporteurClaude(service)
        resultat = importeur.importer(str(zip_path))

        # L'import ne doit pas lever d'exception et doit comptabiliser l'erreur
        assert resultat["nb_erreurs"] > 0, f"Aucune erreur comptabilisée : {resultat}"

    def test_claude_import_sans_chemin(self):
        """Appel sans chemin → résultat contient 'erreur'."""
        service = ServiceMock()
        importeur = ImporteurClaude(service)
        resultat = importeur.importer(None)

        assert "erreur" in resultat

    def test_claude_import_zip_inexistant(self):
        """ZIP inexistant → résultat contient 'erreur'."""
        service = ServiceMock()
        importeur = ImporteurClaude(service)
        resultat = importeur.importer("/chemin/inexistant/export.zip")

        assert "erreur" in resultat
