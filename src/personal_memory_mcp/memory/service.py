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
        # Le modèle d'embedding stocké en config prime sur la valeur par défaut.
        # Cela permet de retrouver automatiquement le bon modèle après une migration.
        modele_embeddings_effectif = (
            self._storage.lire_config("modele_embeddings") or modele_embeddings
        )
        self._extracteur = ExtracteurOllama(
            url=ollama_url,
            modele_extraction=modele_extraction,
            modele_embeddings=modele_embeddings_effectif,
        )
        self._seuil = seuil_deduplication

    # --- Initialisation lazy des vecteurs ---

    def _assurer_vecteurs_init(self, embedding: list[float]) -> None:
        """Initialise faits_vec si nécessaire, détecte la dimension depuis l'embedding.

        Appelé avant toute opération vectorielle (search, add). Sans effet si
        faits_vec est déjà initialisée avec la bonne dimension.

        Raises:
            ValueError: Si la dimension détectée diffère de celle de la base existante.
        """
        self._storage.init_vecteurs(len(embedding))

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
        self._assurer_vecteurs_init(embedding)
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
        self._assurer_vecteurs_init(embedding)
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
        page: int = 1,
        taille_page: int = 20,
    ) -> dict[str, Any]:
        """Liste les faits stockés avec pagination.

        Avertissement: Sans filtre et avec une grande taille_page, la réponse peut
        saturer le contexte MCP (~70 tokens/fait). Préférer search() pour les
        appels ponctuels, et list() avec pagination pour l'exploration.

        Args:
            categorie: Filtre optionnel par catégorie (si None, tous les faits).
            page: Numéro de page, commence à 1 (défaut: 1).
            taille_page: Nombre de faits par page (défaut: 20, max conseillé: 50).

        Returns:
            Dict avec clés:
            - faits: liste de dicts (id, contenu, categorie, source, date_creation)
            - page: numéro de page courant
            - total_pages: nombre total de pages
            - total: nombre total de faits actifs (filtré si categorie précisée)
        """
        import math
        total = self._storage.compter_faits(categorie)
        offset = (page - 1) * taille_page
        faits = self._storage.lister(categorie=categorie, limite=taille_page, offset=offset)
        total_pages = math.ceil(total / taille_page) if total > 0 else 1
        return {
            "faits": faits,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        }

    def delete(self, id: int) -> dict[str, Any]:
        """Supprime un fait par son identifiant.

        Args:
            id: Identifiant du fait à supprimer (soft delete: actif=0).

        Returns:
            Dict avec clés: succes (bool), id.
        """
        succes = self._storage.supprimer(id)
        return {"succes": succes, "id": id}

    def migrer_embeddings(
        self,
        nouveau_modele: str,
        callback: Any | None = None,
    ) -> dict[str, Any]:
        """Re-embed tous les faits actifs avec un nouveau modèle d'embedding.

        Sauvegarde automatiquement la base avant de commencer. Recrée faits_vec
        avec la nouvelle dimension, puis re-calcule les embeddings de tous les
        faits en batch (par tranches de 32 pour limiter la consommation mémoire).

        Args:
            nouveau_modele: Nom du modèle Ollama cible (ex: "qwen3-embedding:0.6b").
            callback: Fonction optionnelle appelée après chaque batch avec
                      (nb_traites, nb_total). Utile pour afficher une progression.

        Returns:
            Dict avec clés: faits_migres (int), ancien_modele (str),
            nouveau_modele (str), sauvegarde (str).
        """
        from datetime import datetime
        from pathlib import Path

        # 1. Sauvegarde automatique avant migration
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_backup = Path.home() / ".personal-memory" / "backups" / f"pre_migration_{horodatage}.db"
        self._storage.sauvegarder(dest_backup)

        # 2. Changer le modèle d'embedding sur l'extracteur
        ancien_modele = self._extracteur._modele_embeddings
        self._extracteur._modele_embeddings = nouveau_modele

        # 3. Détecter la nouvelle dimension via un embedding test
        [vecteur_test] = self._extracteur.embeddings(["test"])
        nouvelle_dim = len(vecteur_test)

        # 4. Recréer faits_vec avec la nouvelle dimension (+ stocker le modèle en config)
        self._storage.recreer_index_vecteurs(nouvelle_dim, modele=nouveau_modele)

        # 5. Re-embedder tous les faits en batch
        faits = self._storage.lister_tous_contenus()
        nb_total = len(faits)
        taille_batch = 32
        nb_traites = 0

        for i in range(0, nb_total, taille_batch):
            batch = faits[i : i + taille_batch]
            ids = [f[0] for f in batch]
            contenus = [f[1] for f in batch]
            embeddings = self._extracteur.embeddings(contenus)
            for id_, embedding in zip(ids, embeddings):
                self._storage.mettre_a_jour_vecteur(id_, embedding)
            nb_traites += len(batch)
            if callback:
                callback(nb_traites, nb_total)

        return {
            "faits_migres": nb_traites,
            "ancien_modele": ancien_modele,
            "nouveau_modele": nouveau_modele,
            "sauvegarde": str(dest_backup),
        }

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
