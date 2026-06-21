"""Tests de la détection d'incohérence des embeddings entre versions d'Ollama.

Couvre :
- ExtracteurOllama.version() (httpx mocké)
- _version_mineure() (logique pure)
- MemoryService.verifier_coherence_embeddings() (storage mémoire + faux extracteur)
- enregistrement de la version d'Ollama à la première vectorisation

Aucun appel réseau réel.
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import sqlite_vec

from personal_memory_mcp.extraction.ollama import ExtracteurOllama
from personal_memory_mcp.memory.deduplication import SEUIL_PAR_DEFAUT
from personal_memory_mcp.memory.service import MemoryService, _version_mineure
from personal_memory_mcp.memory.storage import Storage, SCHEMA_SQL_BASE

DIM = 768


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _creer_storage_memoire(dim: int = DIM) -> Storage:
    """Storage SQLite en mémoire, sans toucher au système de fichiers."""
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


class _FauxExtracteur(ExtracteurOllama):
    """Extracteur factice : version et modèle d'embedding contrôlés.

    Hérite d'ExtracteurOllama (dont le constructeur ne fait aucun appel réseau)
    pour satisfaire le typage de MemoryService._extracteur sans mock réseau.
    """

    def __init__(self, version: str | None, modele_embeddings: str = "nomic-embed-text"):
        super().__init__(modele_embeddings=modele_embeddings)
        self._version_factice = version

    def version(self) -> str | None:
        return self._version_factice


def _service(version_ollama: str | None, modele: str = "nomic-embed-text") -> MemoryService:
    """MemoryService avec storage mémoire et faux extracteur."""
    svc = MemoryService.__new__(MemoryService)
    svc._storage = _creer_storage_memoire()
    svc._extracteur = _FauxExtracteur(version_ollama, modele_embeddings=modele)
    svc._seuil = SEUIL_PAR_DEFAUT
    return svc


# ---------------------------------------------------------------------------
# _version_mineure
# ---------------------------------------------------------------------------

class TestVersionMineure:
    def test_extrait_majeur_mineur(self):
        assert _version_mineure("0.30.10") == "0.30"

    def test_deux_composants(self):
        assert _version_mineure("0.31") == "0.31"

    def test_un_seul_composant(self):
        assert _version_mineure("1") == "1"


# ---------------------------------------------------------------------------
# ExtracteurOllama.version()
# ---------------------------------------------------------------------------

class TestVersionOllama:
    def _extracteur(self) -> ExtracteurOllama:
        return ExtracteurOllama(url="http://localhost:11434")

    def test_version_retournee(self):
        """Réponse /api/version valide → version extraite."""
        reponse = MagicMock()
        reponse.raise_for_status = MagicMock()
        reponse.json.return_value = {"version": "0.30.10"}
        with patch("httpx.get", return_value=reponse):
            assert self._extracteur().version() == "0.30.10"

    def test_ollama_injoignable_retourne_none(self):
        """Erreur réseau → None (pas d'exception)."""
        with patch("httpx.get", side_effect=Exception("connexion refusée")):
            assert self._extracteur().version() is None

    def test_version_non_str_retourne_none(self):
        """Champ version absent ou non-str → None."""
        reponse = MagicMock()
        reponse.raise_for_status = MagicMock()
        reponse.json.return_value = {}
        with patch("httpx.get", return_value=reponse):
            assert self._extracteur().version() is None


# ---------------------------------------------------------------------------
# verifier_coherence_embeddings()
# ---------------------------------------------------------------------------

class TestCoherenceEmbeddings:
    def test_pas_de_version_stockee_retourne_none(self):
        """Base sans version enregistrée → aucun avertissement."""
        svc = _service(version_ollama="0.31.0")
        assert svc.verifier_coherence_embeddings() is None

    def test_meme_version_mineure_retourne_none(self):
        """0.30.10 stockée vs 0.30.12 courante → même mineure, pas d'avertissement."""
        svc = _service(version_ollama="0.30.12")
        svc._storage.ecrire_config("version_ollama", "0.30.10")
        assert svc.verifier_coherence_embeddings() is None

    def test_version_mineure_differente_avertit(self):
        """0.29.x stockée vs 0.30.x courante + modèle nomic → avertissement."""
        svc = _service(version_ollama="0.30.10")
        svc._storage.ecrire_config("version_ollama", "0.29.5")
        message = svc.verifier_coherence_embeddings()
        assert message is not None
        assert "migrate-embeddings" in message
        assert "0.29.5" in message and "0.30.10" in message

    def test_modele_non_instable_retourne_none(self):
        """Modèle hors liste instable (qwen3-embedding) → pas d'avertissement."""
        svc = _service(version_ollama="0.30.10", modele="qwen3-embedding:0.6b")
        svc._storage.ecrire_config("version_ollama", "0.29.5")
        assert svc.verifier_coherence_embeddings() is None

    def test_ollama_injoignable_retourne_none(self):
        """Version courante indisponible → pas d'avertissement (indéterminable)."""
        svc = _service(version_ollama=None)
        svc._storage.ecrire_config("version_ollama", "0.29.5")
        assert svc.verifier_coherence_embeddings() is None


# ---------------------------------------------------------------------------
# Enregistrement de la version à la première vectorisation
# ---------------------------------------------------------------------------

class TestEnregistrementVersion:
    def test_enregistre_si_absente(self):
        svc = _service(version_ollama="0.30.10")
        svc._enregistrer_version_ollama_si_absente()
        assert svc._storage.lire_config("version_ollama") == "0.30.10"

    def test_n_ecrase_pas_si_presente(self):
        svc = _service(version_ollama="0.31.0")
        svc._storage.ecrire_config("version_ollama", "0.29.5")
        svc._enregistrer_version_ollama_si_absente()
        # La valeur d'origine est préservée (pas d'écrasement)
        assert svc._storage.lire_config("version_ollama") == "0.29.5"

    def test_ollama_injoignable_n_enregistre_rien(self):
        svc = _service(version_ollama=None)
        svc._enregistrer_version_ollama_si_absente()
        assert svc._storage.lire_config("version_ollama") is None
