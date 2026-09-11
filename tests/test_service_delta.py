"""Tests service : réindexation delta (hash de contenu, mise à jour, suppression ciblée).

Extracteur factice à embedding constant, aucun appel réseau. Complète
`test_service_projet.py` avec les trois primitives consommées par
`ImporteurMarkdownTree` en mode delta : `add` doit stocker un hash, et le
service doit exposer `chunks_existants` / `mettre_a_jour_chunk` /
`supprimer_definitivement`.
"""

from pathlib import Path

from personal_memory_mcp.extraction.base import ExtracteurBase
from personal_memory_mcp.memory.service import MemoryService
from personal_memory_mcp.memory.storage import Storage


class _ExtracteurConstant(ExtracteurBase):
    def extraire(self, conversation: object) -> list[object]:  # noqa: ARG002
        return []

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in textes]


class _ServiceTest(MemoryService):
    def __init__(self, chemin: Path):
        self._storage = Storage(chemin)
        self._extracteur = _ExtracteurConstant()
        self._seuil = 0.92


def test_add_calcule_et_stocke_le_hash_du_contenu(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    res = svc.add(
        "un chunk de doc", source="workspace", source_detail="a.md#x", dedup=False
    )
    ligne = svc._storage._conn.execute(
        "SELECT contenu_hash FROM faits WHERE id = ?", (res["id"],)
    ).fetchone()
    assert ligne["contenu_hash"]  # non vide

    # Même contenu -> même hash (déterministe, indépendant de l'id).
    res2 = svc.add(
        "un chunk de doc", source="workspace", source_detail="b.md#y", dedup=False
    )
    ligne2 = svc._storage._conn.execute(
        "SELECT contenu_hash FROM faits WHERE id = ?", (res2["id"],)
    ).fetchone()
    assert ligne["contenu_hash"] == ligne2["contenu_hash"]


def test_chunks_existants_expose_les_hash_par_detail(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    res = svc.add(
        "contenu", source="workspace", projet="sand", source_detail="a.md#x", dedup=False
    )
    existants = svc.chunks_existants("workspace")
    id_, hash_, projet = existants["a.md#x"]
    assert id_ == res["id"]
    assert projet == "sand"
    assert hash_


def test_mettre_a_jour_chunk_change_le_contenu_et_le_hash(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    res = svc.add("ancien", source="workspace", source_detail="a.md#x", dedup=False)
    hash_avant = svc.chunks_existants("workspace")["a.md#x"][1]

    svc.mettre_a_jour_chunk(res["id"], "nouveau")

    ligne = svc._storage._conn.execute(
        "SELECT contenu, contenu_hash FROM faits WHERE id = ?", (res["id"],)
    ).fetchone()
    assert ligne["contenu"] == "nouveau"
    assert ligne["contenu_hash"] != hash_avant


def test_supprimer_definitivement_retire_le_fait(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    res = svc.add("a virer", source="workspace", source_detail="a.md#x", dedup=False)
    nb = svc.supprimer_definitivement([res["id"]])
    assert nb == 1
    assert svc._storage.compter()["total"] == 0
