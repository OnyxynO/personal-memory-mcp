"""Tests CLI du garde-fou Ollama sur `mmcp import markdown-tree`.

Motivation (post-mortem 2026-09-07, clôture kiosque) : sans pré-check, un
Ollama éteint faisait échouer silencieusement chaque chunk (catché par
`except Exception` dans l'importeur) et la commande sortait en code 0 avec
« 577/577 · +0 chunks indexés · ! 9462 erreurs ». Le garde-fou doit refuser
avant toute purge/insertion, comme `mmcp import facts` (même patron, cf.
`test_cli_import_facts.py`).
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from personal_memory_mcp.cli.main import app
from personal_memory_mcp.extraction.ollama import ExtracteurOllama
from personal_memory_mcp.memory.service import MemoryService

runner = CliRunner()


class ExtracteurFactice(ExtracteurOllama):
    """Extracteur sans réseau : vecteurs déterministes, disponibilité pilotable."""

    def __init__(self, disponible: bool = True, serveur_joignable: bool = True) -> None:
        super().__init__(modele_embeddings="qwen3-embedding:0.6b")
        self._disponible = disponible
        self._serveur_joignable = serveur_joignable

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in textes]

    def verifier_disponibilite(self) -> dict[str, bool]:
        return {self._modele_embeddings: self._disponible, self._modele_extraction: self._disponible}

    def version(self) -> str | None:
        return "0.30.1" if self._serveur_joignable else None


def _service(tmp_path: Path, **kwargs: Any) -> MemoryService:
    svc = MemoryService(chemin_db=tmp_path / "memory.db")
    svc._extracteur = ExtracteurFactice(**kwargs)
    return svc


def _invoquer(svc: MemoryService, args: list[str]):
    with patch("personal_memory_mcp.cli.main._service", return_value=svc):
        return runner.invoke(app, args)


def test_import_markdown_tree_refuse_si_ollama_est_injoignable(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.md").write_text("# T\ncontenu\n", encoding="utf-8")
    svc = _service(tmp_path, disponible=False, serveur_joignable=False)

    res = _invoquer(svc, ["import", "markdown-tree", str(ws)])

    assert res.exit_code == 1, res.output
    assert "injoignable" in res.output
    assert svc._storage.compter()["total"] == 0


def test_import_markdown_tree_delta_par_defaut_ne_reindexe_pas_l_inchange(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.md").write_text("# T\ncontenu\n", encoding="utf-8")
    svc = _service(tmp_path)

    _invoquer(svc, ["import", "markdown-tree", str(ws)])
    res = _invoquer(svc, ["import", "markdown-tree", str(ws)])  # rien n'a changé

    assert res.exit_code == 0, res.output
    assert "+ 0 chunks indexés" in res.output


def test_import_markdown_tree_full_force_la_reindexation_totale(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.md").write_text("# T\ncontenu\n", encoding="utf-8")
    svc = _service(tmp_path)

    _invoquer(svc, ["import", "markdown-tree", str(ws)])
    res = _invoquer(svc, ["import", "markdown-tree", str(ws), "--full"])  # forcé malgré l'absence de changement

    assert res.exit_code == 0, res.output
    assert "+ 1 chunks indexés" in res.output
    assert svc._storage.compter()["total"] == 1  # toujours idempotent
