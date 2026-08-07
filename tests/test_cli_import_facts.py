"""Tests CLI du snapshot portable : `mmcp export --complet` et `mmcp import facts`.

Ce sont les garde-fous de l'intégrité au restore (base non vide, Ollama
indisponible, modèle d'embedding divergent, format rejeté). Ils sont testés au
niveau CliRunner parce qu'ils vivent dans la CLI, pas dans le service : sans ces
tests, les supprimer ne casse rien.

Même patron que `test_cli_search_json.py` : `patch` sur `_service`, aucun réseau.
"""

import json
from pathlib import Path
from typing import Any

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


def _service(tmp_path: Path, nom: str = "memory.db", **kwargs: Any) -> MemoryService:
    svc = MemoryService(chemin_db=tmp_path / nom)
    svc._extracteur = ExtracteurFactice(**kwargs)
    return svc


def _snapshot(chemin: Path, faits: list[dict], modele: str = "qwen3-embedding:0.6b") -> Path:
    chemin.write_text(
        json.dumps({
            "version_format": 1,
            "modele_embeddings": modele,
            "dim_embeddings": 4,
            "date_export": "2026-08-07T09:00:00+00:00",
            "faits": faits,
        }),
        encoding="utf-8",
    )
    return chemin


def _fait(contenu: str, **extra: Any) -> dict[str, Any]:
    base = {
        "contenu": contenu,
        "categorie": "doc",
        "source": "workspace",
        "source_detail": None,
        "projet": None,
        "date_creation": "2026-08-06T10:00:00+00:00",
        "date_derniere_utilisation": None,
        "score_importance": 0.5,
    }
    base.update(extra)
    return base


def _invoquer(svc: MemoryService, args: list[str], **kwargs: Any):
    from unittest.mock import patch

    with patch("personal_memory_mcp.cli.main._service", return_value=svc):
        return runner.invoke(app, args, **kwargs)


# --- Garde-fous de `mmcp import facts` ---


def test_import_facts_refuse_une_base_deja_peuplee(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc._storage.init_vecteurs(4)
    svc._storage.inserer_fait("déjà là", "doc", "manuel", [1.0, 0.0, 0.0, 0.0])
    fichier = _snapshot(tmp_path / "snap.json", [_fait("nouveau")])

    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 1, res.output
    assert "contient déjà" in res.output
    # Rien n'a été inséré : la base reste à son seul fait d'origine.
    assert svc._storage.compter()["total"] == 1


def test_import_facts_accepte_une_base_peuplee_avec_force(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc._storage.init_vecteurs(4)
    svc._storage.inserer_fait("déjà là", "doc", "manuel", [1.0, 0.0, 0.0, 0.0])
    svc._storage.ecrire_config("modele_embeddings", "qwen3-embedding:0.6b")
    fichier = _snapshot(tmp_path / "snap.json", [_fait("nouveau")])

    res = _invoquer(svc, ["import", "facts", str(fichier), "--force"])

    assert res.exit_code == 0, res.output
    assert svc._storage.compter()["total"] == 2


def test_import_facts_refuse_si_ollama_est_injoignable(tmp_path: Path) -> None:
    svc = _service(tmp_path, disponible=False, serveur_joignable=False)
    fichier = _snapshot(tmp_path / "snap.json", [_fait("nouveau")])

    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 1, res.output
    assert "injoignable" in res.output
    assert "ollama serve" in res.output
    assert svc._storage.compter()["total"] == 0


def test_import_facts_distingue_le_modele_absent_du_serveur_eteint(tmp_path: Path) -> None:
    # Ollama répond mais le modèle n'est pas pullé : « injoignable » serait faux
    # et enverrait l'utilisateur redémarrer un service qui tourne déjà.
    svc = _service(tmp_path, disponible=False, serveur_joignable=True)
    fichier = _snapshot(tmp_path / "snap.json", [_fait("nouveau")])

    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 1, res.output
    assert "ollama pull" in res.output
    assert "injoignable" not in res.output


def test_import_facts_refuse_un_fichier_invalide_sans_rien_ecrire(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    fichier = tmp_path / "snap.json"
    fichier.write_text(json.dumps([_fait("valide"), _fait("cassé", projet={})]), encoding="utf-8")

    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 1, res.output
    assert "élément #1" in res.output
    assert svc._storage.compter()["total"] == 0


def test_import_facts_refuse_un_fichier_absent(tmp_path: Path) -> None:
    svc = _service(tmp_path)

    res = _invoquer(svc, ["import", "facts", str(tmp_path / "inexistant.json")])

    assert res.exit_code == 1, res.output
    assert "introuvable" in res.output


# --- Modèle d'embedding transporté par l'archive ---


def test_import_facts_adopte_le_modele_de_l_archive_sur_base_neuve(tmp_path: Path) -> None:
    # Cœur de la promesse « snapshot portable » : sans cela, une base neuve
    # retombe sur le modèle par défaut du code et se fige sur sa dimension.
    svc = _service(tmp_path)
    svc._extracteur._modele_embeddings = "nomic-embed-text"  # défaut local
    fichier = _snapshot(tmp_path / "snap.json", [_fait("un fait")], modele="qwen3-embedding:0.6b")

    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 0, res.output
    assert svc._storage.lire_config("modele_embeddings") == "qwen3-embedding:0.6b"
    assert svc._extracteur._modele_embeddings == "qwen3-embedding:0.6b"


def test_import_facts_exige_une_confirmation_si_le_modele_diverge(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc._storage.init_vecteurs(4)
    svc._storage.ecrire_config("modele_embeddings", "nomic-embed-text")
    svc._extracteur._modele_embeddings = "nomic-embed-text"
    svc._storage.inserer_fait("déjà là", "doc", "manuel", [1.0, 0.0, 0.0, 0.0])
    fichier = _snapshot(tmp_path / "snap.json", [_fait("un fait")], modele="qwen3-embedding:0.6b")

    refus = _invoquer(svc, ["import", "facts", str(fichier), "--force"], input="n\n")
    assert refus.exit_code == 1, refus.output
    assert "divergent" in refus.output
    assert svc._storage.compter()["total"] == 1

    accord = _invoquer(svc, ["import", "facts", str(fichier), "--force"], input="y\n")
    assert accord.exit_code == 0, accord.output
    assert svc._storage.compter()["total"] == 2


def test_import_facts_avertit_sur_une_archive_heritee_sans_metadonnees(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    fichier = tmp_path / "snap.json"
    fichier.write_text(json.dumps([_fait("un fait")]), encoding="utf-8")

    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 0, res.output
    assert "hérité" in res.output


def test_import_facts_recommande_une_sauvegarde_avant_de_lancer(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    fichier = _snapshot(tmp_path / "snap.json", [_fait("un fait")])

    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 0, res.output
    assert "mmcp backup" in res.output


def test_import_facts_signale_l_echec_en_cours_de_route_sans_traceback(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    fichier = _snapshot(tmp_path / "snap.json", [_fait(f"fait {i}") for i in range(40)])

    appels = {"n": 0}

    def embeddings_capricieux(textes: list[str]) -> list[list[float]]:
        appels["n"] += 1
        if appels["n"] > 1:  # casse après le premier lot, base déjà partielle
            raise RuntimeError("connexion perdue")
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in textes]

    svc._extracteur.embeddings = embeddings_capricieux  # type: ignore[method-assign]
    res = _invoquer(svc, ["import", "facts", str(fichier)])

    assert res.exit_code == 1, res.output
    assert "connexion perdue" in res.output
    assert "32 faits ont déjà été insérés" in res.output
    assert "--force" in res.output  # procédure de reprise explicite


# --- Garde-fous de `mmcp export --complet` ---


def test_export_complet_refuse_le_format_csv(tmp_path: Path) -> None:
    svc = _service(tmp_path)

    res = _invoquer(svc, ["export", "--complet", "--format", "csv"])

    assert res.exit_code == 1, res.output
    assert "--complet exige --format json" in res.output


def test_export_complet_refuse_un_filtre_de_categorie(tmp_path: Path) -> None:
    svc = _service(tmp_path)

    res = _invoquer(svc, ["export", "--complet", "--categorie", "stack"])

    assert res.exit_code == 1, res.output
    assert "--categorie" in res.output


def test_export_complet_emet_l_enveloppe_avec_le_modele_et_la_dimension(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc._storage.init_vecteurs(4)
    svc._storage.inserer_fait("un fait", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    sortie = tmp_path / "snap.json"

    res = _invoquer(svc, ["export", "--complet", "--sortie", str(sortie)])

    assert res.exit_code == 0, res.output
    enveloppe = json.loads(sortie.read_text(encoding="utf-8"))
    assert enveloppe["version_format"] == 1
    assert enveloppe["modele_embeddings"] == "qwen3-embedding:0.6b"
    assert enveloppe["dim_embeddings"] == 4
    assert enveloppe["date_export"]
    assert [f["contenu"] for f in enveloppe["faits"]] == ["un fait"]


def test_export_complet_n_est_pas_plafonne_ni_ampute_du_projet(tmp_path: Path) -> None:
    # Garde-fou contre un repli sur `lister()` : plafonné (limite) et sans `projet`.
    svc = _service(tmp_path)
    svc._storage.init_vecteurs(4)
    for i in range(60):
        svc._storage.inserer_fait(f"fait {i}", "doc", "workspace", [1.0, 0.0, 0.0, 0.0], projet="sand")
    sortie = tmp_path / "snap.json"

    res = _invoquer(svc, ["export", "--complet", "--sortie", str(sortie)])

    assert res.exit_code == 0, res.output
    faits = json.loads(sortie.read_text(encoding="utf-8"))["faits"]
    assert len(faits) == 60
    assert all(f["projet"] == "sand" for f in faits)


def test_export_sans_complet_reste_une_liste_nue(tmp_path: Path) -> None:
    # L'export « courant » (lecture humaine) ne change pas de forme.
    svc = _service(tmp_path)
    svc._storage.init_vecteurs(4)
    svc._storage.inserer_fait("un fait", "doc", "workspace", [1.0, 0.0, 0.0, 0.0])
    sortie = tmp_path / "export.json"

    res = _invoquer(svc, ["export", "--sortie", str(sortie)])

    assert res.exit_code == 0, res.output
    assert isinstance(json.loads(sortie.read_text(encoding="utf-8")), list)


# --- Round-trip bout en bout ---


def test_round_trip_export_complet_puis_import_facts(tmp_path: Path) -> None:
    source = _service(tmp_path, nom="source.db")
    source._storage.init_vecteurs(4)
    source._storage.inserer_fait(
        "chunk curé",
        "doc",
        "workspace",
        [1.0, 0.0, 0.0, 0.0],
        source_detail="projets/sand/CLAUDE.md#stack",
        projet="sand",
        score_importance=0.8,
        date_creation="2025-01-15T08:30:00+00:00",
        date_derniere_utilisation="2026-07-01T12:00:00+00:00",
    )
    source._storage.inserer_fait("note manuelle", "preference", "manuel", [0.0, 1.0, 0.0, 0.0])
    snapshot = tmp_path / "snap.json"

    assert _invoquer(source, ["export", "--complet", "--sortie", str(snapshot)]).exit_code == 0

    cible = _service(tmp_path, nom="cible.db")
    cible._extracteur._modele_embeddings = "nomic-embed-text"
    assert _invoquer(cible, ["import", "facts", str(snapshot)]).exit_code == 0

    # Comparaison champ à champ des deux exports logiques (hors `id`, réattribué).
    def sans_id(faits: list[dict]) -> list[dict]:
        return [{k: v for k, v in f.items() if k != "id"} for f in faits]

    assert sans_id(cible._storage.exporter_faits()) == sans_id(source._storage.exporter_faits())
    assert cible._storage.lire_config("modele_embeddings") == "qwen3-embedding:0.6b"
