"""Tests de la couche stockage pour le scoping par projet + la purge de source.

Purs SQLite + sqlite-vec (vecteurs factices), aucun appel réseau.
"""

import sqlite3
from pathlib import Path

from personal_memory_mcp.memory.storage import Storage


def _storage(tmp_path: Path, dim: int = 4) -> Storage:
    s = Storage(tmp_path / "memory.db")
    s.init_vecteurs(dim)
    return s


def test_projet_persiste_et_filtre_la_recherche_vectorielle(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait("fait sand", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("fait vigie", "doc", "workspace", [0.0, 1.0, 0.0, 0.0], projet="vigie")

    tous = s.rechercher([1.0, 0.0, 0.0, 0.0], top_k=10)
    assert len(tous) == 2  # sans filtre : les deux projets

    scoped = s.rechercher([1.0, 0.0, 0.0, 0.0], top_k=10, projet="sand")
    assert [f["contenu"] for f in scoped] == ["fait sand"]


def test_recherche_fts_filtre_par_projet(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait("piege eslint sand", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("piege eslint vigie", "doc", "workspace", [0.0, 1.0, 0.0, 0.0], projet="vigie")

    res = s.rechercher_fts("eslint", top_k=10, projet="sand")
    assert [f["contenu"] for f in res] == ["piege eslint sand"]


def test_purger_source_supprime_et_permet_reingestion_sans_doublon(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait("w1", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("w2", "doc", "workspace", [0.0, 1.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("note manuelle", "note", "manuel", [0.0, 0.0, 1.0, 0.0])

    assert s.purger_source("workspace") == 2
    assert s.compter()["total"] == 1  # seul le fait manuel subsiste

    # ré-ingestion : la purge évite l'accumulation
    s.inserer_fait("w1", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    assert s.compter()["total"] == 2


def test_purger_source_scope_par_projet(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait("a", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("b", "doc", "workspace", [0.0, 1.0, 0.0, 0.0], projet="vigie")
    assert s.purger_source("workspace", projet="sand") == 1
    assert s.compter()["total"] == 1


def test_migration_ajoute_colonne_et_index_projet_sur_base_ancienne(tmp_path: Path) -> None:
    # Simule une base d'avant la colonne projet.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE faits (id INTEGER PRIMARY KEY AUTOINCREMENT, contenu TEXT NOT NULL, "
        "categorie TEXT NOT NULL, source TEXT NOT NULL, source_detail TEXT, "
        "date_creation TEXT NOT NULL, date_derniere_utilisation TEXT, actif INTEGER DEFAULT 1)"
    )
    conn.commit()
    conn.close()

    s = Storage(db)  # applique les migrations au démarrage
    colonnes = {r[1] for r in s._conn.execute("PRAGMA table_info(faits)").fetchall()}
    assert "projet" in colonnes
    index = {r[1] for r in s._conn.execute("PRAGMA index_list(faits)").fetchall()}
    assert "idx_faits_projet" in index
