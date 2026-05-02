"""Couche SQLite + sqlite-vec : CRUD et recherche vectorielle."""

import sqlite3
import sqlite_vec
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL_BASE = """
CREATE TABLE IF NOT EXISTS faits (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    contenu                   TEXT NOT NULL,
    categorie                 TEXT NOT NULL,
    source                    TEXT NOT NULL,
    source_detail             TEXT,
    date_creation             TEXT NOT NULL,
    date_derniere_utilisation TEXT,
    actif                     INTEGER DEFAULT 1,
    score_importance          REAL DEFAULT 0.5
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

CREATE TABLE IF NOT EXISTS config (
    cle    TEXT PRIMARY KEY,
    valeur TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS faits_fts USING fts5(
    contenu,
    content='faits',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS faits_fts_ai AFTER INSERT ON faits BEGIN
    INSERT INTO faits_fts(rowid, contenu) VALUES (new.id, new.contenu);
END;

CREATE TRIGGER IF NOT EXISTS faits_fts_ad AFTER UPDATE OF actif ON faits WHEN new.actif = 0 BEGIN
    INSERT INTO faits_fts(faits_fts, rowid, contenu) VALUES('delete', old.id, old.contenu);
END;
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
        et initialise le schéma de base (faits, imports, config).

        La table vectorielle faits_vec est créée séparément via init_vecteurs()
        car sa dimension dépend du modèle d'embedding utilisé. Pour les bases
        existantes (avant cette version), la dimension 768 est auto-détectée
        et stockée en config pour garantir la rétrocompatibilité.

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
        self._conn.executescript(SCHEMA_SQL_BASE)
        self._conn.commit()
        self._appliquer_migrations()
        self._dim: int = self._lire_ou_detecter_dim()

    def _appliquer_migrations(self) -> None:
        """Applique les migrations de schéma pour les bases existantes.

        Appelé après executescript(SCHEMA_SQL_BASE). Ajoute les colonnes et tables
        manquantes sur les bases créées avant cette version sans recréer les tables.
        """
        # M1: colonne score_importance absente des anciennes bases
        colonnes = {r[1] for r in self._conn.execute("PRAGMA table_info(faits)").fetchall()}
        if "score_importance" not in colonnes:
            self._conn.execute("ALTER TABLE faits ADD COLUMN score_importance REAL DEFAULT 0.5")
            self._conn.commit()

        # M2: peupler l'index FTS5 via 'rebuild' si pas encore initialisé
        # (le manual INSERT ne construit pas l'index pour les content tables)
        fts_init = self._conn.execute(
            "SELECT valeur FROM config WHERE cle = 'fts5_initialise'"
        ).fetchone()
        if not fts_init:
            nb_faits = self._conn.execute("SELECT COUNT(*) FROM faits").fetchone()[0]
            if nb_faits > 0:
                self._conn.execute("INSERT INTO faits_fts(faits_fts) VALUES('rebuild')")
            self._conn.execute(
                "INSERT OR REPLACE INTO config (cle, valeur) VALUES ('fts5_initialise', '1')"
            )
            self._conn.commit()

    def _lire_ou_detecter_dim(self) -> int:
        """Lit la dimension depuis config, ou la détecte depuis faits_vec existante.

        Rétrocompatibilité : les bases créées avant cette version ont faits_vec
        en FLOAT[768] sans entrée config. On détecte ce cas et on stocke 768
        automatiquement pour éviter toute régression.

        Returns:
            Dimension des vecteurs (0 si nouvelle base non encore initialisée).
        """
        row = self._conn.execute(
            "SELECT valeur FROM config WHERE cle = 'dim_embeddings'"
        ).fetchone()
        if row:
            return int(row[0])
        # Base existante sans config (avant cette version) → faits_vec est en 768D
        faits_vec_existe = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='faits_vec'"
        ).fetchone()
        if faits_vec_existe:
            self._conn.execute(
                "INSERT INTO config (cle, valeur) VALUES ('dim_embeddings', '768')"
            )
            self._conn.commit()
            return 768
        return 0

    def init_vecteurs(self, dim: int) -> None:
        """Initialise la table vectorielle avec la dimension donnée.

        Appelé par MemoryService lors de la première opération d'embedding.
        Sans effet si la table existe déjà avec la même dimension.

        Args:
            dim: Nombre de dimensions du modèle d'embedding utilisé.

        Raises:
            ValueError: Si la base existe déjà avec une dimension différente.
                        Utiliser migrate_embeddings() pour changer de modèle.
        """
        if self._dim == dim:
            return
        if self._dim != 0:
            raise ValueError(
                f"Dimension incompatible : base initialisée en {self._dim}D, "
                f"modèle actuel produit {dim}D. "
                f"Utiliser 'mmcp migrate-embeddings' pour migrer."
            )
        self._dim = dim
        self._conn.execute(
            "INSERT INTO config (cle, valeur) VALUES ('dim_embeddings', ?)",
            (str(dim),),
        )
        self._conn.commit()
        self._conn.executescript(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS faits_vec USING vec0(embedding FLOAT[{dim}] distance_metric=cosine);"
        )
        self._conn.commit()

    def recreer_index_vecteurs(self, nouvelle_dim: int, modele: str | None = None) -> None:
        """Recrée faits_vec avec une nouvelle dimension (opération de migration).

        Supprime et recrée la table vectorielle. Les vecteurs existants sont
        perdus — la migration doit re-embedder tous les faits via
        mettre_a_jour_vecteur() après cet appel.

        Args:
            nouvelle_dim: Nouvelle dimension cible.
            modele: Nom du modèle d'embedding à stocker en config (optionnel).
        """
        self._conn.executescript("DROP TABLE IF EXISTS faits_vec;")
        self._conn.execute(
            "INSERT OR REPLACE INTO config (cle, valeur) VALUES ('dim_embeddings', ?)",
            (str(nouvelle_dim),),
        )
        if modele:
            self._conn.execute(
                "INSERT OR REPLACE INTO config (cle, valeur) VALUES ('modele_embeddings', ?)",
                (modele,),
            )
        self._conn.commit()
        self._conn.executescript(
            f"CREATE VIRTUAL TABLE faits_vec USING vec0(embedding FLOAT[{nouvelle_dim}] distance_metric=cosine);"
        )
        self._conn.commit()
        self._dim = nouvelle_dim

    def lire_config(self, cle: str) -> str | None:
        """Lit une valeur depuis la table config.

        Args:
            cle: Clé de configuration à lire.

        Returns:
            Valeur associée, ou None si la clé n'existe pas.
        """
        row = self._conn.execute(
            "SELECT valeur FROM config WHERE cle = ?", (cle,)
        ).fetchone()
        return row[0] if row else None

    def lister_tous_contenus(self) -> list[tuple[int, str]]:
        """Retourne (id, contenu) de tous les faits actifs, pour re-embedding.

        Returns:
            Liste de tuples (id, contenu) triés par id croissant.
        """
        rows = self._conn.execute(
            "SELECT id, contenu FROM faits WHERE actif = 1 ORDER BY id"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def mettre_a_jour_vecteur(self, id: int, embedding: list[float]) -> None:
        """Met à jour le vecteur d'un fait existant dans faits_vec.

        Supprime l'ancien vecteur et insère le nouveau. Utilisé lors de la
        migration pour re-embedder les faits avec un nouveau modèle.

        Args:
            id: Identifiant du fait (rowid dans faits_vec).
            embedding: Nouveau vecteur d'embedding.
        """
        import struct
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute("DELETE FROM faits_vec WHERE rowid = ?", (id,))
        self._conn.execute(
            "INSERT INTO faits_vec (rowid, embedding) VALUES (?, ?)",
            (id, blob),
        )
        self._conn.commit()

    def inserer_fait(
        self,
        contenu: str,
        categorie: str,
        source: str,
        embedding: list[float],
        source_detail: str | None = None,
        score_importance: float = 0.5,
    ) -> int:
        """Insère un nouveau fait avec son embedding.

        Insère dans `faits` et `faits_vec` en transaction atomique.
        L'embedding est encodé en blob float32 pour sqlite-vec.

        Args:
            contenu: Texte du fait (~1 phrase).
            categorie: Catégorie du fait (ex: "stack", "projet").
            source: Source du fait (ex: "claude-code", "manuel").
            embedding: Vecteur d'embedding.
            source_detail: Détail optionnel (chemin fichier, session_id, etc.).
            score_importance: Confiance du LLM dans le fait [0.0, 1.0] (défaut: 0.5).

        Returns:
            ID du fait inséré (rowid).

        Raises:
            sqlite3.Error: En cas d'erreur BDD.
        """
        curseur = self._conn.execute(
            """
            INSERT INTO faits (contenu, categorie, source, source_detail, date_creation, score_importance)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (contenu, categorie, source, source_detail, _maintenant(), score_importance),
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
                       f.score_importance, v.distance
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
                       f.score_importance, v.distance
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
                "score_importance": r["score_importance"],
            }
            for r in rows
        ]

    def rechercher_fts(
        self,
        query: str,
        top_k: int = 5,
        categorie: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recherche plein-texte BM25 via FTS5 (fallback ou complément vectoriel).

        Utilisé quand la recherche vectorielle retourne des scores trop faibles.
        BM25 retourne des valeurs négatives (plus négatif = meilleur match).
        Le score est normalisé à [0, 1] via 1/(1+|bm25|).

        Args:
            query: Texte de la requête (mots-clés, noms propres, identifiants).
            top_k: Nombre de résultats à retourner (défaut: 5).
            categorie: Filtre optionnel par catégorie.

        Returns:
            Liste de dicts avec clés: id, contenu, categorie, source, score, score_importance.
            Liste vide en cas d'erreur FTS5 (query malformée, etc.).
        """
        # Préfixe FTS5 ("mot"*) : couvre pluriels et variantes sans stemmer
        mots = [m.replace('"', '').rstrip('*') for m in query.split() if m.strip()]
        if not mots:
            return []
        requete_fts = " ".join(f'"{m}"*' for m in mots)

        try:
            if categorie:
                sql = """
                    SELECT f.id, f.contenu, f.categorie, f.source, f.date_creation,
                           f.score_importance, bm25(faits_fts) AS bm25_score
                    FROM faits_fts
                    JOIN faits f ON f.id = faits_fts.rowid
                    WHERE faits_fts MATCH ? AND f.actif = 1 AND f.categorie = ?
                    ORDER BY bm25_score
                    LIMIT ?
                """
                rows = self._conn.execute(sql, (requete_fts, categorie, top_k)).fetchall()
            else:
                sql = """
                    SELECT f.id, f.contenu, f.categorie, f.source, f.date_creation,
                           f.score_importance, bm25(faits_fts) AS bm25_score
                    FROM faits_fts
                    JOIN faits f ON f.id = faits_fts.rowid
                    WHERE faits_fts MATCH ? AND f.actif = 1
                    ORDER BY bm25_score
                    LIMIT ?
                """
                rows = self._conn.execute(sql, (requete_fts, top_k)).fetchall()
        except Exception:
            return []

        return [
            {
                "id": r["id"],
                "contenu": r["contenu"],
                "categorie": r["categorie"],
                "source": r["source"],
                "score": round(1.0 / (1.0 + abs(r["bm25_score"])), 4),
                "score_importance": r["score_importance"],
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

    def compter_faits(self, categorie: str | None = None) -> int:
        """Compte les faits actifs, optionnellement filtrés par catégorie.

        Args:
            categorie: Filtre optionnel (si None, compte tous les faits actifs).

        Returns:
            Nombre de faits actifs correspondant au filtre.
        """
        if categorie:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM faits WHERE actif = 1 AND categorie = ?", (categorie,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM faits WHERE actif = 1"
            ).fetchone()
        return row[0]

    def lister(
        self,
        categorie: str | None = None,
        limite: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Liste les faits triés par id descendant (plus récents d'abord).

        Args:
            categorie: Filtre optionnel par catégorie (si None, tous les faits).
            limite: Nombre maximal de faits à retourner (défaut: 20).
            offset: Nombre de faits à sauter avant de retourner (pour pagination).

        Returns:
            Liste de dicts avec clés: id, contenu, categorie, source, date_creation.
        """
        if categorie:
            sql = """
                SELECT id, contenu, categorie, source, source_detail, date_creation, score_importance
                FROM faits WHERE actif = 1 AND categorie = ?
                ORDER BY id DESC LIMIT ? OFFSET ?
            """
            rows = self._conn.execute(sql, (categorie, limite, offset)).fetchall()
        else:
            sql = """
                SELECT id, contenu, categorie, source, source_detail, date_creation, score_importance
                FROM faits WHERE actif = 1
                ORDER BY id DESC LIMIT ? OFFSET ?
            """
            rows = self._conn.execute(sql, (limite, offset)).fetchall()
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

    def sauvegarder(self, destination: Path) -> dict[str, Any]:
        """Sauvegarde la base via l'API SQLite backup (cohérent même si DB ouverte).

        Utilise sqlite3.Connection.backup() qui garantit une copie cohérente
        même en cas d'écritures concurrentes. Le répertoire de destination
        est créé si absent.

        Args:
            destination: Chemin du fichier de sauvegarde (.db).

        Returns:
            Dict avec clés: destination (str), faits (int), taille_mo (float).
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        conn_dest = sqlite3.connect(str(destination))
        self._conn.backup(conn_dest)
        conn_dest.close()
        stats = self.compter()
        taille = destination.stat().st_size / 1024 / 1024
        return {
            "destination": str(destination),
            "faits": stats["total"],
            "taille_mo": round(taille, 2),
        }

    @staticmethod
    def valider_backup(chemin: Path) -> dict[str, Any] | None:
        """Vérifie qu'un fichier est une sauvegarde personal-memory valide.

        Ouvre le fichier en lecture seule et vérifie la présence des tables
        attendues (faits, imports). Retourne None si le fichier est invalide
        ou corrompu.

        Args:
            chemin: Chemin vers le fichier de sauvegarde à valider.

        Returns:
            Dict avec clés: faits (int), taille_mo (float).
            None si le fichier n'est pas une sauvegarde valide.
        """
        try:
            conn = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not {"faits", "imports"}.issubset(tables):
                conn.close()
                return None
            total = conn.execute(
                "SELECT COUNT(*) FROM faits WHERE actif = 1"
            ).fetchone()[0]
            conn.close()
            taille = chemin.stat().st_size / 1024 / 1024
            return {"faits": total, "taille_mo": round(taille, 2)}
        except Exception:
            return None
