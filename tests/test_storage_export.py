"""Tests de l'export logique complet des faits (round-trip du snapshot portable).

Purs SQLite + sqlite-vec (vecteurs factices), aucun appel réseau.

Motivation : `Storage.lister()` omet la colonne `projet` — un export bâti dessus
perdrait le scoping par projet au restore. `exporter_faits()` est le chemin fidèle.
"""

from pathlib import Path

from personal_memory_mcp.memory.storage import Storage


def _storage(tmp_path: Path, dim: int = 4) -> Storage:
    s = Storage(tmp_path / "memory.db")
    s.init_vecteurs(dim)
    return s


def test_exporter_faits_conserve_le_projet_et_la_source_detail(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait(
        "chunk curé",
        "doc",
        "workspace",
        [1.0, 0.0, 0.0, 0.0],
        source_detail="projets/sand/CLAUDE.md#stack",
        projet="sand",
    )

    [fait] = s.exporter_faits()
    assert fait["contenu"] == "chunk curé"
    assert fait["projet"] == "sand"
    assert fait["source"] == "workspace"
    assert fait["source_detail"] == "projets/sand/CLAUDE.md#stack"
    assert fait["categorie"] == "doc"
    assert fait["score_importance"] == 0.5
    assert fait["date_creation"]
    assert "embedding" not in fait  # le vecteur n'est jamais exporté


def test_exporter_faits_ignore_les_faits_supprimes(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    garde = s.inserer_fait("gardé", "doc", "workspace", [1.0, 0.0, 0.0, 0.0])
    jete = s.inserer_fait("jeté", "doc", "workspace", [0.0, 1.0, 0.0, 0.0])
    s.supprimer(jete)

    ids = [f["id"] for f in s.exporter_faits()]
    assert ids == [garde]


def test_exporter_faits_n_est_pas_plafonne_par_une_limite(tmp_path: Path) -> None:
    # `lister()` plafonne à `limite` (20 par défaut) : le chemin d'export ne doit
    # dépendre d'aucun plafond, sinon un snapshot tronque silencieusement la mémoire.
    s = _storage(tmp_path)
    for i in range(75):
        s.inserer_fait(f"fait {i}", "doc", "workspace", [1.0, 0.0, 0.0, 0.0])

    assert len(s.exporter_faits()) == 75


def test_exporter_faits_trie_par_id_croissant(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    for i in range(3):
        s.inserer_fait(f"fait {i}", "doc", "workspace", [1.0, 0.0, 0.0, 0.0])

    ids = [f["id"] for f in s.exporter_faits()]
    assert ids == sorted(ids)
