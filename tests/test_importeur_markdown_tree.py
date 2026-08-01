"""Tests de l'importeur générique d'arbre Markdown (fonctions pures + end-to-end)."""

from pathlib import Path

import pytest

from personal_memory_mcp.extraction.base import ExtracteurBase
from personal_memory_mcp.importeurs.markdown_tree import (
    ImporteurMarkdownTree,
    _slug,
    decouper_en_sections,
    deriver_projet,
)
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


# --- Fonctions pures ---

def test_slug_minuscule_sans_accent() -> None:
    assert _slug("Ma Section É") == "ma-section-e"
    assert _slug("Pièges connus !") == "pieges-connus"
    assert _slug("") == ""


def test_decouper_preambule_et_titres() -> None:
    doc = "avant.\n\n# Un\naaa\n\n## Deux\nbbb\n"
    sections = decouper_en_sections(doc)
    assert [ancre for ancre, _ in sections] == ["", "un", "deux"]
    assert sections[0][1].strip() == "avant."
    assert sections[1][1].startswith("# Un")  # le titre est inclus dans le texte


def test_decouper_redecoupe_les_sections_longues() -> None:
    corps = "\n\n".join("para " + "x" * 100 for _ in range(40))  # bien > 500 chars
    sections = decouper_en_sections(f"# T\n{corps}\n", max_chars=500)
    assert len(sections) > 1
    assert all(ancre == "t" for ancre, _ in sections)


def test_decouper_liste_a_puces_dense_respecte_max_chars() -> None:
    # Régression : une longue liste à puces a des items séparés par des '\n'
    # simples, aucun '\n\n'. Le redecoupage par paragraphes ne suffit pas ;
    # aucun chunk ne doit dépasser max_chars (sinon rejet 400 à l'embedding).
    corps = "\n".join(f"- item {i} " + "y" * 60 for i in range(40))  # > 500, zéro '\n\n'
    sections = decouper_en_sections(f"# T\n{corps}\n", max_chars=500)
    assert all(len(texte) <= 500 for _, texte in sections)


def test_decouper_ligne_unique_geante_respecte_max_chars() -> None:
    # Cas limite : une seule ligne sans aucun séparateur, plus longue que
    # max_chars. Doit être coupée en tranches dures <= max_chars.
    corps = "z" * 5000
    sections = decouper_en_sections(f"# T\n{corps}\n", max_chars=500)
    assert all(len(texte) <= 500 for _, texte in sections)


def test_deriver_projet_profondeur_1() -> None:
    assert deriver_projet("projets/sand/CLAUDE.md", "projets", "ouroboros") == "sand"
    assert deriver_projet("projets/projectmatch/datamatch/x.md", "projets", "ouroboros") == "projectmatch"
    assert deriver_projet("INDEX.md", "projets", "ouroboros") == "ouroboros"
    assert deriver_projet("_experiences/X.md", "projets", "ouroboros") == "ouroboros"


# --- Import end-to-end ---

def _arbre(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "projets" / "sand").mkdir(parents=True)
    (ws / "projets" / "sand" / "CLAUDE.md").write_text(
        "# Stack\nLaravel.\n\n## Pièges\nX.\n", encoding="utf-8"
    )
    (ws / "INDEX.md").write_text("# Index\nRacine.\n", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "junk.md").write_text("# Junk\nignore\n", encoding="utf-8")
    return ws


def test_importer_ingest_chunks_avec_projet_et_provenance(tmp_path: Path) -> None:
    ws = _arbre(tmp_path)
    svc = _ServiceTest(tmp_path / "m.db")
    res = ImporteurMarkdownTree(svc, projet_defaut="ouroboros").importer(str(ws))

    assert res["ajoutes"] == 3  # Stack + Pièges (sand) + Index (ouroboros) ; node_modules exclu
    lignes = svc._storage._conn.execute(
        "SELECT projet, source, source_detail FROM faits"
    ).fetchall()
    assert {l["projet"] for l in lignes} == {"sand", "ouroboros"}
    assert all(l["source"] == "workspace" for l in lignes)
    details = {l["source_detail"] for l in lignes}
    assert "projets/sand/CLAUDE.md#stack" in details
    assert "projets/sand/CLAUDE.md#pieges" in details
    assert "INDEX.md#index" in details


def test_reindexation_idempotente_grace_a_la_purge(tmp_path: Path) -> None:
    ws = _arbre(tmp_path)
    svc = _ServiceTest(tmp_path / "m.db")
    imp = ImporteurMarkdownTree(svc, projet_defaut="ouroboros")
    imp.importer(str(ws))
    imp.importer(str(ws))  # 2e run : la purge évite l'accumulation
    assert svc._storage.compter()["total"] == 3


def _arbre_projet(base: Path, projet: str, texte: str) -> Path:
    """Crée un arbre `<base>/projets/<projet>/C.md` et retourne la racine `<base>`."""
    d = base / "projets" / projet
    d.mkdir(parents=True)
    (d / "C.md").write_text(f"# {projet}\n{texte}\n", encoding="utf-8")
    return base


def _projets_en_base(svc: _ServiceTest) -> set[str]:
    return {
        r["projet"]
        for r in svc._storage._conn.execute(
            "SELECT projet FROM faits WHERE source = 'workspace'"
        ).fetchall()
    }


def test_purge_scopee_au_perimetre_preserve_les_autres_projets(tmp_path: Path) -> None:
    # La purge doit être scopée aux projets du périmètre indexé : indexer un
    # arbre (projet vigie) ne doit PAS effacer un arbre déjà indexé (projet
    # sand). Régression : l'ancienne purge globale de la source effaçait tout.
    svc = _ServiceTest(tmp_path / "m.db")
    imp = ImporteurMarkdownTree(svc, projet_defaut="ouroboros")
    imp.importer(str(_arbre_projet(tmp_path / "a", "sand", "deploiement")))
    imp.importer(str(_arbre_projet(tmp_path / "b", "vigie", "triggers")))
    assert _projets_en_base(svc) == {"sand", "vigie"}


def test_reindex_meme_perimetre_ne_cree_pas_de_doublon(tmp_path: Path) -> None:
    # Ré-indexer deux fois le même périmètre ne double jamais ses faits :
    # la purge scopée le vide avant chaque ré-ingestion (idempotence).
    svc = _ServiceTest(tmp_path / "m.db")
    imp = ImporteurMarkdownTree(svc, projet_defaut="ouroboros")
    racine = str(_arbre_projet(tmp_path / "a", "sand", "deploiement"))
    imp.importer(racine)
    total = svc._storage.compter()["total"]
    imp.importer(racine)
    assert svc._storage.compter()["total"] == total


def test_importer_rapporte_la_progression_par_fichier(tmp_path: Path) -> None:
    # L'importeur appelle on_progress(traités, total) une fois par fichier :
    # total constant, compteur croissant de 1 jusqu'à total (observabilité).
    ws = _arbre(tmp_path)  # 2 fichiers retenus (sand/CLAUDE.md + INDEX.md)
    svc = _ServiceTest(tmp_path / "m.db")
    appels: list[tuple[int, int]] = []
    ImporteurMarkdownTree(svc, projet_defaut="ouroboros").importer(
        str(ws), on_progress=lambda traites, total: appels.append((traites, total))
    )
    assert appels, "on_progress doit être appelé au moins une fois"
    total = appels[-1][1]
    assert [traites for traites, _ in appels] == list(range(1, total + 1))
    assert all(t == total for _, t in appels)


def test_importer_racine_absente_leve(tmp_path: Path) -> None:
    svc = _ServiceTest(tmp_path / "m.db")
    imp = ImporteurMarkdownTree(svc)
    with pytest.raises(FileNotFoundError):
        imp.importer(str(tmp_path / "introuvable"))
