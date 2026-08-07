"""Tests de la réinjection de faits exportés (restore du snapshot portable).

L'extracteur est mocké (hérite de ExtracteurOllama, dont __init__ ne fait aucun
réseau) : aucun appel Ollama, mais on observe le *batching* réellement effectué.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from personal_memory_mcp.cli.main import charger_faits_json
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


def test_importer_faits_restaure_la_date_de_derniere_utilisation(tmp_path: Path) -> None:
    # Sans ce champ, un fait jamais utilisé et un fait utilisé hier sont
    # indiscernables après restore : `mmcp clean` proposerait de purger tout
    # ce que le snapshot vient de restaurer.
    svc, _ = _service(tmp_path)

    svc.importer_faits([
        _fait("utilisé", date_derniere_utilisation="2026-07-01T12:00:00+00:00"),
        _fait("jamais utilisé"),
    ])

    dates = {f["contenu"]: f["date_derniere_utilisation"] for f in svc._storage.exporter_faits()}
    assert dates == {
        "utilisé": "2026-07-01T12:00:00+00:00",
        "jamais utilisé": None,
    }


def test_importer_faits_refuse_un_lot_d_embeddings_incomplet(tmp_path: Path) -> None:
    # Ollama peut renvoyer moins de vecteurs que de textes : sans garde, `zip`
    # tronque en silence et le compteur annonce quand même le lot entier.
    svc, faux = _service(tmp_path)
    faux.embeddings = lambda textes: [[1.0, 0.0, 0.0, 0.0]] * (len(textes) - 1)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="incomplète"):
        svc.importer_faits([_fait(f"fait {i}") for i in range(3)])


# --- Lecture du fichier de snapshot (enveloppe + validation) ---


def _ecrire(tmp_path: Path, contenu: str) -> Path:
    chemin = tmp_path / "faits.json"
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def test_charger_faits_json_rejette_un_document_ni_liste_ni_enveloppe(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, '"une chaîne"')

    with pytest.raises(ValueError, match="enveloppe"):
        charger_faits_json(chemin)


def test_charger_faits_json_lit_une_liste_nue_heritee(tmp_path: Path) -> None:
    # Rétrocompatibilité : les premiers exports étaient une liste sans enveloppe.
    chemin = _ecrire(tmp_path, '[{"contenu": "un fait", "categorie": "doc"}]')

    snapshot = charger_faits_json(chemin)
    assert [f["contenu"] for f in snapshot.faits] == ["un fait"]
    assert snapshot.version_format is None
    assert snapshot.modele_embeddings is None


def test_charger_faits_json_lit_l_enveloppe_et_ses_metadonnees(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, json.dumps({
        "version_format": 1,
        "modele_embeddings": "qwen3-embedding:0.6b",
        "dim_embeddings": 1024,
        "date_export": "2026-08-07T09:00:00+00:00",
        "faits": [{"contenu": "un fait", "categorie": "doc"}],
    }))

    snapshot = charger_faits_json(chemin)
    assert snapshot.version_format == 1
    assert snapshot.modele_embeddings == "qwen3-embedding:0.6b"
    assert snapshot.dim_embeddings == 1024
    assert snapshot.date_export == "2026-08-07T09:00:00+00:00"
    assert [f["contenu"] for f in snapshot.faits] == ["un fait"]


def test_charger_faits_json_rejette_une_version_de_format_inconnue(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, json.dumps({"version_format": 99, "faits": []}))

    with pytest.raises(ValueError, match="Version de format inconnue"):
        charger_faits_json(chemin)


def test_charger_faits_json_rejette_une_enveloppe_sans_liste_de_faits(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, json.dumps({"version_format": 1, "faits": {"a": 1}}))

    with pytest.raises(ValueError, match="'faits'"):
        charger_faits_json(chemin)


def test_charger_faits_json_rejette_un_element_qui_n_est_pas_un_objet(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, '[1, "x", null]')

    with pytest.raises(ValueError, match="élément #0"):
        charger_faits_json(chemin)


@pytest.mark.parametrize(
    ("fait_invalide", "motif"),
    [
        ({"categorie": "doc"}, r"'contenu'"),                        # contenu absent
        ({"contenu": "   "}, r"'contenu'"),                          # contenu vide
        ({"contenu": "ok", "categorie": 3}, r"'categorie'"),
        ({"contenu": "ok", "projet": {}}, r"'projet'"),              # dict → InterfaceError au bind
        ({"contenu": "ok", "source_detail": []}, r"'source_detail'"),
        ({"contenu": "ok", "score_importance": "haut"}, r"'score_importance'"),
        ({"contenu": "ok", "score_importance": 3.5}, r"\[0, 1\]"),
        ({"contenu": "ok", "date_creation": "hier"}, r"'date_creation'"),
        ({"contenu": "ok", "date_derniere_utilisation": 42}, r"'date_derniere_utilisation'"),
    ],
)
def test_charger_faits_json_valide_chaque_champ(tmp_path: Path, fait_invalide: dict, motif: str) -> None:
    # Un champ mal typé ne doit jamais atteindre SQLite : il planterait APRÈS
    # des insertions, sur une opération non transactionnelle entre les lots.
    chemin = _ecrire(tmp_path, json.dumps([{"contenu": "valide"}, fait_invalide]))

    with pytest.raises(ValueError, match=motif) as err:
        charger_faits_json(chemin)
    assert "élément #1" in str(err.value)


def test_charger_faits_json_accepte_un_fait_complet_bien_type(tmp_path: Path) -> None:
    chemin = _ecrire(tmp_path, json.dumps([_fait("complet", projet="sand", score_importance=1)]))

    snapshot = charger_faits_json(chemin)
    assert snapshot.faits[0]["projet"] == "sand"


def test_charger_faits_json_rejette_un_json_syntaxiquement_casse(tmp_path: Path) -> None:
    # json.JSONDecodeError hérite de ValueError : la CLI le rattrape déjà.
    chemin = _ecrire(tmp_path, "{pas du json")

    with pytest.raises(ValueError):
        charger_faits_json(chemin)
