"""Tests de la couche stockage pour le scoping par source.

Purs SQLite + sqlite-vec (vecteurs factices), aucun appel réseau.

Motivation : un briefing/consommateur veut interroger uniquement le corpus
curé (source=workspace) sans se voir noyé par des faits d'étude de code
(source=chemin de fichier) ou de vieux imports de conversation.
"""

import math
from pathlib import Path

from personal_memory_mcp.memory.storage import Storage


def _storage(tmp_path: Path, dim: int = 4) -> Storage:
    s = Storage(tmp_path / "memory.db")
    s.init_vecteurs(dim)
    return s


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def test_recherche_filtre_par_source(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait("chunk curé", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("note d'étude de code", "doc", "Task.ts", [0.0, 1.0, 0.0, 0.0])

    tous = s.rechercher([1.0, 0.0, 0.0, 0.0], top_k=10)
    assert len(tous) == 2  # sans filtre : les deux sources

    scoped = s.rechercher([1.0, 0.0, 0.0, 0.0], top_k=10, source="workspace")
    assert [f["contenu"] for f in scoped] == ["chunk curé"]


def test_recherche_scopee_source_pas_evincee_par_voisins_hors_source(tmp_path: Path) -> None:
    # Même piège que le scoping projet : de nombreux voisins hors source, plus
    # proches de la requête, évinceraient le bon match de la source ciblée si le
    # KNN plafonnait avant le filtre. Le chemin scalaire doit ratisser large.
    s = _storage(tmp_path)
    q = [1.0, 0.0, 0.0, 0.0]
    s.inserer_fait("match curé", "doc", "workspace", _norm([0.85, 0.53, 0.0, 0.0]), projet="sand")
    for i in range(6):
        s.inserer_fait(f"bruit {i}", "doc", "Task.ts", _norm([1.0, 0.02 * (i + 1), 0.0, 0.0]))

    res = s.rechercher(q, top_k=3, source="workspace")
    assert res, "le match curé doit être trouvé malgré les voisins hors source"
    assert res[0]["contenu"] == "match curé"
    assert res[0]["score"] > 0.7


def test_recherche_fts_filtre_par_source(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait("piege eslint curé", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("piege eslint étude", "doc", "Task.ts", [0.0, 1.0, 0.0, 0.0])

    res = s.rechercher_fts("eslint", top_k=10, source="workspace")
    assert [f["contenu"] for f in res] == ["piege eslint curé"]


def test_source_et_projet_combinables(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    s.inserer_fait("sand curé", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    s.inserer_fait("vigie curé", "doc", "workspace", [0.0, 1.0, 0.0, 0.0], projet="vigie")
    s.inserer_fait("sand étude", "doc", "Task.ts", [1.0, 0.0, 0.0, 0.0], projet="sand")

    res = s.rechercher([1.0, 0.0, 0.0, 0.0], top_k=10, source="workspace", projet="sand")
    assert [f["contenu"] for f in res] == ["sand curé"]
