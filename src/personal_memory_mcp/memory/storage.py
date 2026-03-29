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
    def __init__(self, chemin_db: Path):
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
        """Retourne les (rowid, distance) pour la déduplication."""
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
        """Retourne un fait par son id, ou None s'il n'existe pas."""
        row = self._conn.execute(
            "SELECT id, contenu, categorie, source, date_creation FROM faits WHERE id = ? AND actif = 1",
            (id,),
        ).fetchone()
        return dict(row) if row else None

    def supprimer(self, id: int) -> bool:
        self._conn.execute("UPDATE faits SET actif = 0 WHERE id = ?", (id,))
        self._conn.commit()
        return self._conn.execute(
            "SELECT changes()"
        ).fetchone()[0] > 0

    def compter(self) -> dict[str, Any]:
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
        row = self._conn.execute(
            "SELECT type, date_import, nb_faits_ajoutes FROM imports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
