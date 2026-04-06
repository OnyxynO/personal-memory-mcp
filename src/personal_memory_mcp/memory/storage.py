"""Couche SQLite + sqlite-vec : CRUD et recherche vectorielle."""

import sqlite3
import sqlite_vec
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS faits (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    contenu                   TEXT NOT NULL,
    categorie                 TEXT NOT NULL,
    source                    TEXT NOT NULL,
    source_detail             TEXT,
    date_creation             TEXT NOT NULL,
    date_derniere_utilisation TEXT,
    actif                     INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS imports (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    type                 TEXT NOT NULL,
    chemin               TEXT,
    date_import          TEXT NOT NULL,
    nb_faits_ajoutes     INTEGER DEFAULT 0,
    nb_faits_dedupliques INTEGER DEFAULT 0,
    nb_faits_mis_a_jour  INTEGER DEFAULT 0,
    duree_secondes       REAL
);

CREATE VIRTUAL TABLE IF NOT EXISTS faits_vec USING vec0(
    embedding FLOAT[768]
);
"""


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    """Couche SQLite + sqlite-vec : CRUD et recherche vectorielle.

    Gère la persistance des faits avec leurs embeddings. Fournit l'interface
    pour insertion, recherche par similarité, suppression et statistiques.

    Attributes:
        _chemin: Chemin vers le fichier SQLite.
        _conn: Connexion SQLite thread-safe avec sqlite-vec chargé.
    """

    def __init__(self, chemin_db: Path):
        """Initialise la couche de stockage SQLite.

        Crée le répertoire parent si absent, charge l'extension sqlite-vec,
        et initialise le schéma (idempotent via CREATE TABLE IF NOT EXISTS).

        Args:
            chemin_db: Chemin vers memory.db (ex: ~/.personal-memory/memory.db).
        """
        self._chemin = chemin_db
        chemin_db.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(chemin_db), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def inserer_fait(
        self,
        contenu: str,
        categorie: str,
        source: str,
        embedding: list[float],
        source_detail: str | None = None,
    ) -> int:
        """Insère un nouveau fait avec son embedding.

        Insère dans `faits` et `faits_vec` en transaction atomique.
        L'embedding est encodé en blob float32[768] pour sqlite-vec.

        Args:
            contenu: Texte du fait (~1 phrase).
            categorie: Catégorie du fait (ex: "stack", "projet").
            source: Source du fait (ex: "claude-code", "manuel").
            embedding: Vecteur d'embedding (768 dimensions pour nomic-embed-text).
            source_detail: Détail optionnel (chemin fichier, session_id, etc.).

        Returns:
            ID du fait inséré (rowid).

        Raises:
            sqlite3.Error: En cas d'erreur BDD.
        """
        curseur = self._conn.execute(
            """
            INSERT INTO faits (contenu, categorie, source, source_detail, date_creation)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contenu, categorie, source, source_detail, _maintenant()),
        )
        rowid: int = curseur.lastrowid  # type: ignore[assignment]
        import struct
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute(
            "INSERT INTO faits_vec (rowid, embedding) VALUES (?, ?)",
            (rowid, blob),
        )
        self._conn.commit()
        return rowid

    def rechercher(
        self,
        embedding: list[float],
        top_k: int = 5,
        categorie: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recherche vectorielle (k-plus proches voisins) dans les faits.

        Utilise sqlite-vec pour recherche ANN rapide par similarité cosinus.
        Met à jour automatiquement `date_derniere_utilisation` des faits retournés
        (utilisé pour l'expiration des faits non utilisés > 12 mois).

        Args:
            embedding: Vecteur d'embedding de la requête (768 dimensions).
            top_k: Nombre de résultats à retourner (défaut: 5).
            categorie: Filtre optionnel par catégorie (si None, tous les faits).

        Returns:
            Liste de dicts avec clés: id, contenu, categorie, source, score.
            `score` est la similarité cosinus normalisée (1 - distance, range [0, 1]).
        """
        import struct
        blob = struct.pack(f"{len(embedding)}f", *embedding)

        if categorie:
            sql = """
                SELECT f.id, f.contenu, f.categorie, f.source, f.date_creation,
                       v.distance
                FROM faits_vec v
                JOIN faits f ON f.id = v.rowid
                WHERE f.actif = 1 AND f.categorie = ?
                  AND v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
            """
            rows = self._conn.execute(sql, (categorie, blob, top_k)).fetchall()
        else:
            sql = """
                SELECT f.id, f.contenu, f.categorie, f.source, f.date_creation,
                       v.distance
                FROM faits_vec v
                JOIN faits f ON f.id = v.rowid
                WHERE f.actif = 1
                  AND v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
            """
            rows = self._conn.execute(sql, (blob, top_k)).fetchall()

        # Mise à jour date_derniere_utilisation
        ids = [r["id"] for r in rows]
        if ids:
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE faits SET date_derniere_utilisation = ? WHERE id IN ({placeholders})",
                [_maintenant(), *ids],
            )
            self._conn.commit()

        return [
            {
                "id": r["id"],
                "contenu": r["contenu"],
                "categorie": r["categorie"],
                "source": r["source"],
                "score": round(1 - r["distance"], 4),
            }
            for r in rows
        ]

    def voisins_proches(
        self, embedding: list[float], top_k: int = 3
    ) -> list[tuple[int, float]]:
        """Retourne les k plus proches voisins (rowid, distance) pour déduplication.

        Utilisé par la couche métier pour vérifier si un fait est un doublon
        avant insertion. Distance en cosinus [0, 2], où 0 = identique.

        Args:
            embedding: Vecteur d'embedding du candidat (768 dimensions).
            top_k: Nombre de voisins à retourner (défaut: 3).

        Returns:
            Liste de tuples (id, distance) triés par distance croissante.
        """
        import struct
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        sql = """
            SELECT rowid, distance
            FROM faits_vec
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
        """
        rows = self._conn.execute(sql, (blob, top_k)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def lister(
        self,
        categorie: str | None = None,
        limite: int = 50,
    ) -> list[dict[str, Any]]:
        """Liste les faits triés par date de création descendante (plus récents d'abord).

        Attention: Sans filtre categorie, peut retourner ~70 tokens/fait.
        Pour les appels MCP répétés, préférer rechercher() qui retourne moins de résultats.

        Args:
            categorie: Filtre optionnel par catégorie (si None, tous les faits).
            limite: Nombre maximal de faits à retourner (défaut: 50).

        Returns:
            Liste de dicts avec clés: id, contenu, categorie, source, date_creation.
        """
        if categorie:
            sql = """
                SELECT id, contenu, categorie, source, date_creation
                FROM faits WHERE actif = 1 AND categorie = ?
                ORDER BY id DESC LIMIT ?
            """
            rows = self._conn.execute(sql, (categorie, limite)).fetchall()
        else:
            sql = """
                SELECT id, contenu, categorie, source, date_creation
                FROM faits WHERE actif = 1
                ORDER BY id DESC LIMIT ?
            """
            rows = self._conn.execute(sql, (limite,)).fetchall()
        return [dict(r) for r in rows]

    def obtenir_par_id(self, id: int) -> dict[str, Any] | None:
        """Retourne un fait actif par son identifiant.

        Args:
            id: Identifiant du fait.

        Returns:
            Dict avec clés: id, contenu, categorie, source, date_creation.
            None si le fait n'existe pas ou est marqué comme inactif (supprimé).
        """
        row = self._conn.execute(
            "SELECT id, contenu, categorie, source, date_creation FROM faits WHERE id = ? AND actif = 1",
            (id,),
        ).fetchone()
        return dict(row) if row else None

    def supprimer(self, id: int) -> bool:
        """Supprime un fait (soft delete: marque actif=0).

        Args:
            id: Identifiant du fait à supprimer.

        Returns:
            True si le fait existait et a été marqué comme inactif, False sinon.
        """
        self._conn.execute("UPDATE faits SET actif = 0 WHERE id = ?", (id,))
        self._conn.commit()
        return self._conn.execute(
            "SELECT changes()"
        ).fetchone()[0] > 0

    def compter(self) -> dict[str, Any]:
        """Retourne le compte de faits actifs par catégorie.

        Returns:
            Dict avec clés:
            - total: nombre total de faits actifs.
            - par_categorie: dict {categorie: count} trié par count descendant.
        """
        total = self._conn.execute(
            "SELECT COUNT(*) FROM faits WHERE actif = 1"
        ).fetchone()[0]
        par_categorie = self._conn.execute(
            "SELECT categorie, COUNT(*) FROM faits WHERE actif = 1 GROUP BY categorie ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {"total": total, "par_categorie": dict(par_categorie)}

    def enregistrer_import(
        self,
        type: str,
        chemin: str | None,
        nb_ajoutes: int,
        nb_dedupliques: int,
        nb_mis_a_jour: int,
        duree: float,
    ) -> None:
        """Enregistre les statistiques d'un import dans la table `imports`.

        Args:
            type: Type d'import ("claude-code", "claude", "chatgpt").
            chemin: Chemin du fichier importé (ZIP, répertoire, etc.) ou None.
            nb_ajoutes: Nombre de nouveaux faits ajoutés.
            nb_dedupliques: Nombre de faits détectés comme doublons.
            nb_mis_a_jour: Nombre de faits existants mis à jour.
            duree: Durée de l'import en secondes.
        """
        self._conn.execute(
            """
            INSERT INTO imports (type, chemin, date_import, nb_faits_ajoutes,
                                 nb_faits_dedupliques, nb_faits_mis_a_jour, duree_secondes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (type, chemin, _maintenant(), nb_ajoutes, nb_dedupliques, nb_mis_a_jour, duree),
        )
        self._conn.commit()

    def dernier_import(self) -> dict[str, Any] | None:
        """Retourne les stats du dernier import enregistré.

        Returns:
            Dict avec clés: type, date_import, nb_faits_ajoutes.
            None si aucun import enregistré.
        """
        row = self._conn.execute(
            "SELECT type, date_import, nb_faits_ajoutes FROM imports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
