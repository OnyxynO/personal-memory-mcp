"""L'outil MCP `search` transmet bien le filtre `projet` au service."""

from pathlib import Path

import personal_memory_mcp.mcp.server as server_module
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


def test_mcp_search_filtre_par_projet(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    svc.add("piege eslint", categorie="doc", source="workspace", projet="sand", dedup=False)
    svc.add("piege eslint", categorie="doc", source="workspace", projet="vigie", dedup=False)

    ancien = server_module._service
    server_module._service = svc
    try:
        tous = server_module.search("piege eslint", top_k=10)
        scoped = server_module.search("piege eslint", top_k=10, projet="sand")
    finally:
        server_module._service = ancien

    assert len(tous) == 2
    assert len(scoped) == 1
