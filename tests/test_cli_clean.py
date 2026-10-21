"""Tests CLI de `mmcp clean` : quels faits sont proposés à l'expiration.

Constat du 2026-10-21 : `clean` traitait « jamais utilisé » comme « expiré »
quel que soit l'âge du fait, et proposait donc de désactiver presque toute la
base (10 152 chunks `workspace` sans date d'usage, créés la veille). Même patron
que `test_cli_import_facts.py` : `patch` sur `_service`, aucun réseau.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from personal_memory_mcp.cli.main import app
from personal_memory_mcp.memory.service import MemoryService

runner = CliRunner()


def _il_y_a(jours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()


def _service_vide(tmp_path: Path) -> MemoryService:
    svc = MemoryService(chemin_db=tmp_path / "memory.db")
    svc._assurer_vecteurs_init([0.0, 0.0, 0.0, 0.0])  # faits_vec n'existe qu'après le 1er embedding
    return svc


def _base(tmp_path: Path) -> MemoryService:
    """Une base avec un fait par cas, repérable par son contenu."""
    svc = _service_vide(tmp_path)
    cas = [
        # (contenu, source, créé il y a N jours, utilisé il y a N jours ou None)
        ("recent-jamais-utilise", "claude-code", 10, None),
        ("ancien-jamais-utilise", "claude-code", 400, None),
        ("ancien-utilise-recemment", "claude-code", 400, 5),
        ("ancien-utilise-il-y-a-longtemps", "claude-code", 500, 400),
        ("chunk-workspace-ancien", "workspace", 400, None),
    ]
    for i, (contenu, source, cree, utilise) in enumerate(cas):
        svc._storage.inserer_fait(
            contenu=contenu,
            categorie="stack",
            source=source,
            embedding=[float(i + 1), 1.0, 0.0, 0.0],
            date_creation=_il_y_a(cree),
            date_derniere_utilisation=_il_y_a(utilise) if utilise is not None else None,
        )
    return svc


def _proposes(sortie: str) -> set[str]:
    candidats = {
        "recent-jamais-utilise",
        "ancien-jamais-utilise",
        "ancien-utilise-recemment",
        "ancien-utilise-il-y-a-longtemps",
        "chunk-workspace-ancien",
    }
    return {c for c in candidats if c in sortie}


def test_clean_ne_propose_que_les_faits_reellement_expires(tmp_path: Path) -> None:
    svc = _base(tmp_path)
    with patch("personal_memory_mcp.cli.main._service", return_value=svc):
        res = runner.invoke(app, ["clean"], input="n\n")

    assert res.exit_code == 0, res.output
    assert _proposes(res.output) == {"ancien-jamais-utilise", "ancien-utilise-il-y-a-longtemps"}
    assert "[stack]" in res.output  # la catégorie n'est pas avalée comme balise rich


def test_clean_ignore_les_chunks_workspace(tmp_path: Path) -> None:
    # Les chunks `workspace` appartiennent à la réindexation (`import markdown-tree`),
    # pas au cycle d'expiration : les désactiver les rendrait invisibles sans que
    # le delta ne les recrée (leur hash est inchangé).
    svc = _service_vide(tmp_path)
    svc._storage.inserer_fait(
        contenu="chunk-workspace-ancien",
        categorie="doc",
        source="workspace",
        embedding=[1.0, 1.0, 0.0, 0.0],
        date_creation=_il_y_a(800),
    )
    with patch("personal_memory_mcp.cli.main._service", return_value=svc):
        res = runner.invoke(app, ["clean"], input="y\n")

    assert "Aucun fait expiré" in res.output
    assert svc._storage.compter()["total"] == 1


def test_clean_confirme_desactive_seulement_les_faits_proposes(tmp_path: Path) -> None:
    svc = _base(tmp_path)
    with patch("personal_memory_mcp.cli.main._service", return_value=svc):
        res = runner.invoke(app, ["clean"], input="y\n")

    assert res.exit_code == 0, res.output
    actifs = {
        r["contenu"]
        for r in svc._storage._conn.execute("SELECT contenu FROM faits WHERE actif = 1").fetchall()
    }
    assert actifs == {"recent-jamais-utilise", "ancien-utilise-recemment", "chunk-workspace-ancien"}
