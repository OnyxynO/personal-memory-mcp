"""Tests du verrou d'exclusion mutuelle des imports (anti-parallélisme)."""

from pathlib import Path

import pytest

from personal_memory_mcp.importeurs.verrou import ImportDejaEnCours, verrou_import


def test_verrou_refuse_un_second_import_concurrent(tmp_path: Path) -> None:
    # Deux acquisitions simultanées du même verrou : la seconde est refusée
    # immédiatement (pas de mise en attente qui empilerait les process).
    lock = tmp_path / "import.lock"
    with verrou_import(lock):
        with pytest.raises(ImportDejaEnCours):
            with verrou_import(lock):
                pass


def test_verrou_reutilisable_apres_liberation(tmp_path: Path) -> None:
    # Après la fin d'un import, le verrou est libéré et ré-acquérable.
    lock = tmp_path / "import.lock"
    with verrou_import(lock):
        pass
    with verrou_import(lock):  # ne doit pas lever
        pass


def test_verrou_cree_le_repertoire_parent(tmp_path: Path) -> None:
    # Le fichier lock peut être dans un répertoire pas encore créé.
    lock = tmp_path / "sous" / "dossier" / "import.lock"
    with verrou_import(lock):
        pass
    assert lock.parent.is_dir()
