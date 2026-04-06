"""MemoryService — couche métier centrale."""

from pathlib import Path
from typing import Any

from personal_memory_mcp.extraction.ollama import ExtracteurOllama
from personal_memory_mcp.memory.deduplication import est_doublon, SEUIL_PAR_DEFAUT
from personal_memory_mcp.memory.storage import Storage


def _chemin_db_defaut() -> Path:
    return Path.home() / ".personal-memory" / "memory.db"


class MemoryService:
    """Couche métier centrale pour la mémoire personnelle.

    Orchestre l'interaction entre le stockage (SQLite + sqlite-vec) et l'extraction
    (embeddings via Ollama). Fournit les outils MCP (search, add, list, delete) et
    les services CLI (import, status, clean).

    Attributes:
        _storage: Couche SQLite + sqlite-vec pour stockage et recherche vectorielle.
        _extracteur: ExtracteurOllama pour embeddings et extraction de faits (lazy).
        _seuil: Seuil cosinus pour déduplication (défaut 0.92).
    """

    def __init__(
        self,
        chemin_db: Path | None = None,
        ollama_url: str = "http://localhost:11434",
        modele_extraction: str = "qwen3:1.7b",
        modele_embeddings: str = "nomic-embed-text",
        seuil_deduplication: float = SEUIL_PAR_DEFAUT,
    ):
        """Initialise le service mémoire.

        Args:
            chemin_db: Chemin vers memory.db (défaut: ~/.personal-memory/memory.db).
            ollama_url: URL du serveur Ollama (défaut: http://localhost:11434).
            modele_extraction: Modèle pour extraction de faits (défaut: qwen3:1.7b).
            modele_embeddings: Modèle pour embeddings (défaut: nomic-embed-text).
            seuil_deduplication: Seuil cosinus (défaut: 0.92, range [0, 1]).
        """
        self._storage = Storage(chemin_db or _chemin_db_defaut())
        self._extracteur = ExtracteurOllama(
            url=ollama_url,
            modele_extraction=modele_extraction,
            modele_embeddings=modele_embeddings,
        )
        self._seuil = seuil_deduplication

    # --- Outils MCP ---

    def search(
        self,
        query: str,
        top_k: int = 5,
        categorie: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recherche sémantique dans la mémoire.

        Calcule l'embedding de la requête et retourne les faits les plus proches
        par similarité cosinus. Met à jour date_derniere_utilisation des faits
        retournés (utilisé pour l'expiration).

        Args:
            query: Texte de la requête en langage naturel.
            top_k: Nombre de résultats à retourner (défaut: 5).
            categorie: Filtre optionnel par catégorie (si None, tous les faits).

        Returns:
            Liste de dicts avec clés: id, contenu, categorie, source, score.
        """
        [embedding] = self._extracteur.embeddings([query])
        return self._storage.rechercher(embedding, top_k=top_k, categorie=categorie)

    def add(
        self,
        contenu: str,
        categorie: str = "autre",
        source: str = "manuel",
    ) -> dict[str, Any]:
        """Ajoute un fait en mémoire (avec déduplication automatique).

        Calcule l'embedding du contenu, vérifie la déduplication vectorielle,
        et insère si nouveau. En cas de doublon, retourne le fait existant
        le plus proche.

        Args:
            contenu: Texte du fait à mémoriser (~1 phrase).
            categorie: Catégorie du fait (défaut: "autre"). Valeurs: stack, projet,
                      preference, decision, contrainte, contexte, autre.
            source: Source du fait (défaut: "manuel"). Valeurs: manuel, claude-code,
                   claude, chatgpt.

        Returns:
            Dict avec clés: id, contenu, categorie, nouveau (bool).

        Raises:
            ValueError: Si l'embedding ne peut pas être calculé (Ollama indisponible).
        """
        [embedding] = self._extracteur.embeddings([contenu])
        if est_doublon(embedding, self._storage, self._seuil):
            # Trouver le fait existant le plus proche pour retourner son id
            voisins = self._storage.voisins_proches(embedding, top_k=1)
            id_existant = voisins[0][0] if voisins else -1
            fait = self._storage.obtenir_par_id(id_existant) if id_existant != -1 else None
            return {
                "id": id_existant,
                "contenu": fait["contenu"] if fait else contenu,
                "categorie": fait["categorie"] if fait else categorie,
                "nouveau": False,
            }
        id_nouveau = self._storage.inserer_fait(
            contenu=contenu,
            categorie=categorie,
            source=source,
            embedding=embedding,
        )
        return {"id": id_nouveau, "contenu": contenu, "categorie": categorie, "nouveau": True}

    def list(
        self,
        categorie: str | None = None,
        limite: int = 50,
    ) -> list[dict[str, Any]]:
        """Liste les faits stockés.

        Avertissement: Sans filtre, retourne ~70 tokens/fait (ex: 176 faits = ~12k tokens).
        Préférer search() pour les appels MCP répétés.

        Args:
            categorie: Filtre optionnel par catégorie (si None, tous les faits).
            limite: Nombre maximal de faits à retourner (défaut: 50).

        Returns:
            Liste de dicts avec clés: id, contenu, categorie, source, date_creation.
        """
        return self._storage.lister(categorie=categorie, limite=limite)

    def delete(self, id: int) -> dict[str, Any]:
        """Supprime un fait par son identifiant.

        Args:
            id: Identifiant du fait à supprimer (soft delete: actif=0).

        Returns:
            Dict avec clés: succes (bool), id.
        """
        succes = self._storage.supprimer(id)
        return {"succes": succes, "id": id}

    def status(self) -> dict[str, Any]:
        stats = self._storage.compter()
        disponibilite = self._extracteur.verifier_disponibilite()
        dernier = self._storage.dernier_import()
        return {
            "chemin_db": str(self._storage._chemin),
            "faits": stats,
            "ollama": disponibilite,
            "dernier_import": dernier,
        }
