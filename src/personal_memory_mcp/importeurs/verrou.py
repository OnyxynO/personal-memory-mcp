"""Verrou d'exclusion mutuelle inter-process pour sérialiser les imports.

Un import (reindex markdown-tree, import de conversations) est une opération
lourde : écritures SQLite + embeddings Ollama. Deux imports concurrents
provoquent contention SQLite (« database is locked »), pression RAM (un process
Python complet chacun) et, en cas de purge+ré-ingestion entrelacées, des
doublons de course. Ce verrou garantit **un seul import à la fois**.

Choix : acquisition **non bloquante** avec **refus immédiat** (et non mise en
file d'attente). Une file garderait chaque import surnuméraire vivant en
attente — exactement l'empilement RAM qu'on veut éviter ; le refus les fait
échouer tout de suite. Le verrou est **inter-process** (`flock`) car le risque
réel vient de plusieurs process `mmcp import`, pas d'un seul process.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - plateforme sans fcntl (Windows)
    fcntl = None  # type: ignore[assignment]


class ImportDejaEnCours(RuntimeError):
    """Levée quand un import est déjà en cours (verrou tenu par un autre process)."""


@contextmanager
def verrou_import(chemin_lock: Path) -> Iterator[None]:
    """Acquiert un verrou exclusif non bloquant le temps du bloc `with`.

    Args:
        chemin_lock: Fichier lock (son répertoire parent est créé au besoin).

    Yields:
        None — le contexte protégé s'exécute pendant que le verrou est tenu.

    Raises:
        ImportDejaEnCours: si un autre process tient déjà le verrou.
    """
    if fcntl is None:  # pragma: no cover - sans fcntl, best effort sans verrou
        yield
        return

    chemin_lock.parent.mkdir(parents=True, exist_ok=True)
    fichier = chemin_lock.open("w")
    try:
        try:
            fcntl.flock(fichier.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise ImportDejaEnCours(
                "Un import est déjà en cours. Attendez sa fin avant d'en lancer un autre."
            ) from e
        yield
    finally:
        # flock est libéré à la fermeture du descripteur.
        fichier.close()
