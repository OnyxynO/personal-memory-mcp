"""Tests de la réinjection de faits exportés (restore du snapshot portable).

L'extracteur est mocké (hérite de ExtracteurOllama, dont __init__ ne fait aucun
réseau) : aucun appel Ollama, mais on observe le *batching* réellement effectué.
"""

from pathlib import Path
from typing import Any

from personal_memory_mcp.extraction.ollama import ExtracteurOllama
from personal_memory_mcp.memory.service import MemoryService


class ExtracteurLots(ExtracteurOllama):
    """Faux extracteur : vecteurs déterministes, mémorise la taille des lots reçus."""

    def __init__(self) -> None:
        super().__init__()
        self.lots: list[int] = []

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        self.lots.append(len(textes))
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in textes]

    def version(self) -> str | None:
        return None


def _service(tmp_path: Path) -> tuple[MemoryService, ExtracteurLots]:
    svc = MemoryService(chemin_db=tmp_path / "memory.db")
    faux = ExtracteurLots()
    svc._extracteur = faux
    return svc, faux


def _fait(contenu: str, **extra: Any) -> dict[str, Any]:
    base = {
        "id": 1,
        "contenu": contenu,
        "categorie": "doc",
        "source": "workspace",
        "source_detail": None,
        "projet": None,
        "date_creation": "2026-08-06T10:00:00+00:00",
        "score_importance": 0.5,
    }
    base.update(extra)
    return base


def test_importer_faits_restaure_contenu_categorie_source_et_projet(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)

    res = svc.importer_faits([
        _fait("chunk curé", projet="sand", source_detail="projets/sand/CLAUDE.md#stack"),
    ])

    assert res["importes"] == 1
    [restaure] = svc._storage.exporter_faits()
    assert restaure["contenu"] == "chunk curé"
    assert restaure["projet"] == "sand"
    assert restaure["source"] == "workspace"
    assert restaure["source_detail"] == "projets/sand/CLAUDE.md#stack"
    assert restaure["categorie"] == "doc"


def test_importer_faits_preserve_la_date_de_creation_d_origine(tmp_path: Path) -> None:
    # `exporter_faits()` produit `date_creation`, mais avant ce correctif
    # `importer_faits()` ne la transmettait pas à `inserer_fait()` : tous les
    # faits restaurés portaient la date du restore, alors que l'info est dans
    # l'archive.
    svc, _ = _service(tmp_path)

    svc.importer_faits([_fait("chunk daté", **{"date_creation": "2025-01-15T08:30:00+00:00"})])

    [restaure] = svc._storage.exporter_faits()
    assert restaure["date_creation"] == "2025-01-15T08:30:00+00:00"


def test_importer_faits_sans_date_creation_retombe_sur_la_date_du_restore(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)

    svc.importer_faits([_fait("sans date", date_creation=None)])

    [restaure] = svc._storage.exporter_faits()
    assert restaure["date_creation"] is not None


def test_importer_faits_embarque_par_lots_de_32(tmp_path: Path) -> None:
    svc, faux = _service(tmp_path)

    svc.importer_faits([_fait(f"fait {i}") for i in range(70)])

    # 70 faits = 32 + 32 + 6, jamais 70 appels unitaires (principe #8)
    assert faux.lots == [32, 32, 6]


def test_importer_faits_ignore_les_entrees_sans_contenu(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)

    res = svc.importer_faits([_fait("valide"), _fait(""), {"categorie": "doc"}])

    assert res["importes"] == 1
    assert res["ignores"] == 2


def test_importer_faits_signale_la_progression(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)
    vus: list[tuple[int, int]] = []

    svc.importer_faits([_fait(f"fait {i}") for i in range(40)], callback=lambda n, t: vus.append((n, t)))

    assert vus == [(32, 40), (40, 40)]


def test_charger_faits_json_rejette_un_document_qui_n_est_pas_une_liste(tmp_path: Path) -> None:
    from personal_memory_mcp.cli.main import charger_faits_json

    chemin = tmp_path / "faits.json"
    chemin.write_text('{"contenu": "un objet, pas une liste"}', encoding="utf-8")

    try:
        charger_faits_json(chemin)
    except ValueError as e:
        assert "liste" in str(e)
    else:
        raise AssertionError("un document non-liste doit être rejeté")


def test_charger_faits_json_lit_une_liste_de_faits(tmp_path: Path) -> None:
    from personal_memory_mcp.cli.main import charger_faits_json

    chemin = tmp_path / "faits.json"
    chemin.write_text('[{"contenu": "un fait", "categorie": "doc"}]', encoding="utf-8")

    faits = charger_faits_json(chemin)
    assert [f["contenu"] for f in faits] == ["un fait"]


def test_charger_faits_json_rejette_un_element_qui_n_est_pas_un_objet(tmp_path: Path) -> None:
    from personal_memory_mcp.cli.main import charger_faits_json

    chemin = tmp_path / "faits.json"
    chemin.write_text('[1, "x", null]', encoding="utf-8")

    try:
        charger_faits_json(chemin)
    except ValueError as e:
        assert "liste" in str(e)
    else:
        raise AssertionError("une liste contenant un élément non-objet doit être rejetée")
