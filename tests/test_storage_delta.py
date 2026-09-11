"""Tests de la couche stockage pour la réindexation delta (hash de contenu).

Purs SQLite + sqlite-vec (vecteurs factices), aucun appel réseau.

Motivation : `mmcp import markdown-tree` purgeait et ré-embeddait tout le
périmètre à chaque run (~30 min pour 6 fichiers modifiés). Le delta compare un
hash de contenu par chunk (`source_detail`) pour ne ré-embedder que ce qui a
changé. Cf. `_ideas/Atelier/2026-09-07-reindexation-delta-personal-memory-design.md`.
"""

from pathlib import Path

from personal_memory_mcp.memory.storage import Storage


def _storage(tmp_path: Path, dim: int = 4) -> Storage:
    s = Storage(tmp_path / "memory.db")
    s.init_vecteurs(dim)
    return s


def test_inserer_fait_stocke_le_hash(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    id_ = s.inserer_fait(
        "contenu", "doc", "workspace", [1.0, 0.0, 0.0, 0.0],
        source_detail="a.md#x", contenu_hash="abc123",
    )
    row = s._conn.execute("SELECT contenu_hash FROM faits WHERE id = ?", (id_,)).fetchone()
    assert row["contenu_hash"] == "abc123"


def test_lister_hashes_retourne_id_hash_projet_par_detail(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    id1 = s.inserer_fait(
        "c1", "doc", "workspace", [1.0, 0.0, 0.0, 0.0],
        source_detail="a.md#x", projet="sand", contenu_hash="h1",
    )
    # Une autre source ne doit pas apparaître dans le scope "workspace".
    s.inserer_fait("c2", "doc", "Task.ts", [0.0, 1.0, 0.0, 0.0], source_detail="a.md#x")
    # Un fait sans source_detail (ex: fait manuel) ne doit pas apparaître non plus.
    s.inserer_fait("c3", "doc", "workspace", [0.0, 0.0, 1.0, 0.0])

    hashes = s.lister_hashes("workspace")
    assert hashes == {"a.md#x": (id1, "h1", "sand")}


def test_lister_hashes_ignore_les_faits_inactifs(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    id_ = s.inserer_fait(
        "c1", "doc", "workspace", [1.0, 0.0, 0.0, 0.0],
        source_detail="a.md#x", contenu_hash="h1",
    )
    s.supprimer(id_)
    assert s.lister_hashes("workspace") == {}


def test_mettre_a_jour_contenu_change_texte_hash_et_vecteur(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    id_ = s.inserer_fait(
        "ancien texte", "doc", "workspace", [1.0, 0.0, 0.0, 0.0],
        source_detail="a.md#x", contenu_hash="h_ancien",
    )
    s.mettre_a_jour_contenu(id_, "nouveau texte", "h_nouveau", [0.0, 1.0, 0.0, 0.0])

    row = s._conn.execute(
        "SELECT contenu, contenu_hash FROM faits WHERE id = ?", (id_,)
    ).fetchone()
    assert row["contenu"] == "nouveau texte"
    assert row["contenu_hash"] == "h_nouveau"

    # Le vecteur a bien changé (recherche sur le nouveau vecteur le trouve).
    res = s.rechercher([0.0, 1.0, 0.0, 0.0], top_k=1)
    assert res[0]["contenu"] == "nouveau texte"


def test_mettre_a_jour_contenu_resynchronise_fts(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    id_ = s.inserer_fait(
        "mot ancien unique", "doc", "workspace", [1.0, 0.0, 0.0, 0.0],
        source_detail="a.md#x", contenu_hash="h1",
    )
    s.mettre_a_jour_contenu(id_, "mot nouveau distinct", "h2", [1.0, 0.0, 0.0, 0.0])

    assert s.rechercher_fts("ancien") == []
    res = s.rechercher_fts("nouveau")
    assert [f["contenu"] for f in res] == ["mot nouveau distinct"]


def test_supprimer_definitivement_retire_faits_vecteurs_et_fts(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    id1 = s.inserer_fait(
        "a supprimer", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], source_detail="a.md#x"
    )
    id2 = s.inserer_fait(
        "a garder", "doc", "workspace", [0.0, 1.0, 0.0, 0.0], source_detail="b.md#y"
    )

    nb = s.supprimer_definitivement([id1])

    assert nb == 1
    assert s._conn.execute("SELECT id FROM faits WHERE id = ?", (id1,)).fetchone() is None
    assert s.rechercher_fts("supprimer") == []
    assert s.compter()["total"] == 1
    restant = s.rechercher([0.0, 1.0, 0.0, 0.0], top_k=5)
    assert [f["id"] for f in restant] == [id2]


def test_supprimer_definitivement_liste_vide_ne_fait_rien(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    assert s.supprimer_definitivement([]) == 0


def test_migration_ajoute_contenu_hash_sur_base_existante(tmp_path: Path) -> None:
    # Une base créée avant cette feature n'a pas la colonne : la migration doit
    # l'ajouter sans planter, avec NULL pour les lignes déjà présentes.
    import sqlite3
    chemin = tmp_path / "vieille.db"
    conn = sqlite3.connect(str(chemin))
    conn.execute(
        """
        CREATE TABLE faits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contenu TEXT NOT NULL,
            categorie TEXT NOT NULL,
            source TEXT NOT NULL,
            source_detail TEXT,
            projet TEXT,
            date_creation TEXT NOT NULL,
            date_derniere_utilisation TEXT,
            actif INTEGER DEFAULT 1,
            score_importance REAL DEFAULT 0.5
        )
        """
    )
    conn.execute(
        "INSERT INTO faits (contenu, categorie, source, date_creation) "
        "VALUES ('vieux fait', 'doc', 'workspace', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    s = Storage(chemin)
    row = s._conn.execute("SELECT contenu_hash FROM faits").fetchone()
    assert row["contenu_hash"] is None
