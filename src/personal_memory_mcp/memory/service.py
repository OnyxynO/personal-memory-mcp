"""MemoryService — couche métier centrale."""

from pathlib import Path
from typing import Any

from personal_memory_mcp.extraction.ollama import ExtracteurOllama
from personal_memory_mcp.memory.deduplication import est_doublon, SEUIL_PAR_DEFAUT
from personal_memory_mcp.memory.storage import Storage


def _chemin_db_defaut() -> Path:
    return Path.home() / ".personal-memory" / "memory.db"


class MemoryService:
    def __init__(
        self,
        chemin_db: Path | None = None,
        ollama_url: str = "http://localhost:11434",
        modele_extraction: str = "qwen3:1.7b",
        modele_embeddings: str = "nomic-embed-text",
        seuil_deduplication: float = SEUIL_PAR_DEFAUT,
    ):
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
        [embedding] = self._extracteur.embeddings([query])
        return self._storage.rechercher(embedding, top_k=top_k, categorie=categorie)

    def add(
        self,
        contenu: str,
        categorie: str = "autre",
        source: str = "manuel",
    ) -> dict[str, Any]:
        [embedding] = self._extracteur.embeddings([contenu])
        if est_doublon(embedding, self._storage, self._seuil):
            # Trouver le fait existant le plus proche pour retourner son id
            voisins = self._storage.voisins_proches(embedding, top_k=1)
            id_existant = voisins[0][0] if voisins else -1
            faits = self._storage.lister(limite=1000)
            fait = next((f for f in faits if f["id"] == id_existant), None)
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
        return self._storage.lister(categorie=categorie, limite=limite)

    def delete(self, id: int) -> dict[str, Any]:
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
