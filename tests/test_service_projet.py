"""Tests service : threading de `projet` / `source_detail` / `dedup` dans add + search.

Extracteur factice à embedding constant → le KNN renvoie tous les faits et c'est
le filtre `projet` qui décide, de façon déterministe (aucun appel réseau).
"""

from pathlib import Path

from personal_memory_mcp.extraction.base import ExtracteurBase
from personal_memory_mcp.memory.service import MemoryService
from personal_memory_mcp.memory.storage import Storage


class _ExtracteurConstant(ExtracteurBase):
    """Renvoie toujours le même vecteur : distances égales, ordre neutre."""

    def extraire(self, conversation: object) -> list[object]:  # noqa: ARG002
        return []

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in textes]


class _ServiceTest(MemoryService):
    def __init__(self, chemin: Path):
        self._storage = Storage(chemin)
        self._extracteur = _ExtracteurConstant()
        self._seuil = 0.92


def test_add_stocke_projet_et_source_detail(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    res = svc.add(
        "un chunk de doc",
        categorie="doc",
        source="workspace",
        projet="sand",
        source_detail="projets/sand/CLAUDE.md#stack",
        dedup=False,
    )
    assert res["nouveau"] is True
    ligne = svc._storage._conn.execute(
        "SELECT projet, source, source_detail FROM faits WHERE id = ?", (res["id"],)
    ).fetchone()
    assert (ligne["projet"], ligne["source"], ligne["source_detail"]) == (
        "sand",
        "workspace",
        "projets/sand/CLAUDE.md#stack",
    )


def test_search_scope_par_projet(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    # Deux faits identiques (même embedding) dans deux projets → dedup désactivée
    svc.add("piege eslint", categorie="doc", source="workspace", projet="sand", dedup=False)
    svc.add("piege eslint", categorie="doc", source="workspace", projet="vigie", dedup=False)

    assert len(svc.search("piege eslint", top_k=10)) == 2  # sans filtre
    scoped = svc.search("piege eslint", top_k=10, projet="sand")
    assert len(scoped) == 1


def test_dedup_desactivee_permet_les_quasi_doublons(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    a = svc.add("contenu identique", source="workspace", projet="sand", dedup=False)
    b = svc.add("contenu identique", source="workspace", projet="sand", dedup=False)
    assert a["nouveau"] is True and b["nouveau"] is True  # les deux insérés
    assert svc._storage.compter()["total"] == 2
