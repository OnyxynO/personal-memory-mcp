"""Importeur générique d'un arbre de fichiers Markdown.

Parcourt récursivement un répertoire, découpe chaque `.md` en sections (par
titre), et ingère chaque section comme un fait (`source="workspace"`) avec sa
provenance et son projet de rattachement. Contrairement aux importeurs de
conversations, il n'extrait PAS de faits via LLM : le contenu est déjà distillé,
on stocke le chunk tel quel (seul l'embedding est calculé, via `add`).

Générique : les spécificités d'un workspace (exclusions, base de dérivation du
projet, projet par défaut) sont des paramètres, pas des valeurs codées en dur.
"""

import re
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

from personal_memory_mcp.importeurs.base import ImporteurBase, ResultatImport
from personal_memory_mcp.memory.service import MemoryService

EXCLUSIONS_DEFAUT: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "dist",
        "build",
        ".superpowers",
        "__pycache__",
        # Environnements virtuels et dépendances Python : leur doc est tierce
        # et polluerait l'index (un reindex a déjà avalé 419 chunks d'un .venv).
        ".venv",
        "venv",
        "site-packages",
        ".tox",
        # Caches d'outillage : jamais du contenu utile.
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        # Secrets d'exploitation (tokens, credentials, coffres). Contrairement aux
        # entrées ci-dessus, l'enjeu n'est pas la pollution de l'index mais la
        # confidentialité : un reindex réel y avait pris 215 faits, dont des tokens
        # PyPI et un PAT GitHub, stockés en clair et vectorisés — donc remontables
        # par une simple recherche sémantique. Ces répertoires sont gitignorés
        # précisément pour cette raison ; l'indexation ne doit pas les rattraper.
        "infra",
        "secrets",
    }
)
MAX_CHARS_DEFAUT = 2000

_RE_TITRE = re.compile(r"^#{1,6}\s")


def _slug(titre: str) -> str:
    """Transforme un titre de section en ancre (slug) stable et minuscule.

    Args:
        titre: Texte du titre (sans les `#`).

    Returns:
        Slug ASCII minuscule (accents retirés, non-alphanumériques → tirets),
        ou chaîne vide si le titre est vide.
    """
    sans_accent = unicodedata.normalize("NFD", titre)
    sans_accent = "".join(c for c in sans_accent if unicodedata.category(c) != "Mn")
    slug = re.sub(r"[^a-z0-9]+", "-", sans_accent.lower()).strip("-")
    return slug


def _redecouper(texte: str, max_chars: int) -> list[str]:
    """Redécoupe un texte trop long en blocs <= max_chars.

    Découpe hiérarchique : à la ligne (ce qui préserve aussi bien les paragraphes
    séparés par `\\n\\n` que les items d'une liste à puces séparés par `\\n`), puis
    en tranches dures pour une ligne unique plus longue que `max_chars`. Les
    fragments atomiques ainsi obtenus sont regroupés glouton en blocs `<= max_chars`.

    Un découpage limité aux frontières de paragraphes (`\\n\\n`) laisserait passer
    une longue liste à puces (aucun `\\n\\n`) en un seul bloc surdimensionné, rejeté
    ensuite par le modèle d'embedding (HTTP 400).

    Args:
        texte: Texte de la section.
        max_chars: Taille maximale d'un bloc.

    Returns:
        Liste de blocs, chacun de longueur <= max_chars. Un seul élément si le texte
        tient déjà dans max_chars.
    """
    if len(texte) <= max_chars:
        return [texte]

    # Fragments atomiques garantis <= max_chars : par ligne, puis tranches dures.
    unites: list[str] = []
    for ligne in texte.split("\n"):
        if len(ligne) <= max_chars:
            unites.append(ligne)
        else:
            for i in range(0, len(ligne), max_chars):
                unites.append(ligne[i : i + max_chars])

    blocs: list[str] = []
    courant: list[str] = []
    taille = 0
    for unite in unites:
        ajout = len(unite) + 1  # séparateur '\n'
        if courant and taille + ajout > max_chars:
            blocs.append("\n".join(courant))
            courant, taille = [], 0
        courant.append(unite)
        taille += ajout
    if courant:
        blocs.append("\n".join(courant))
    return blocs


def decouper_en_sections(contenu: str, max_chars: int = MAX_CHARS_DEFAUT) -> list[tuple[str, str]]:
    """Découpe un document Markdown en sections (ancre, texte) par titre.

    Une section va d'un titre au titre suivant. Le texte inclut la ligne de titre.
    Le préambule avant le premier titre forme une section d'ancre vide. Une section
    plus longue que max_chars est redécoupée en sous-blocs (même ancre).

    Args:
        contenu: Contenu Markdown brut.
        max_chars: Taille maximale d'un chunk (défaut: 2000).

    Returns:
        Liste de tuples (ancre, texte). Vide si le document est vide.
    """
    sections: list[tuple[str, str]] = []
    titre_courant = ""
    buffer: list[str] = []

    def vider() -> None:
        texte = "\n".join(buffer).strip()
        if texte:
            for sous in _redecouper(texte, max_chars):
                sections.append((_slug(titre_courant), sous))

    for ligne in contenu.splitlines():
        if _RE_TITRE.match(ligne):
            vider()
            buffer = [ligne]
            titre_courant = ligne.lstrip("#").strip()
        else:
            buffer.append(ligne)
    vider()
    return sections


def deriver_projet(chemin_relatif: str, base: str, defaut: str | None) -> str | None:
    """Dérive le projet de rattachement depuis le chemin relatif (profondeur 1).

    Un fichier sous `<base>/<x>/…` est rattaché au projet `<x>`. Tout fichier hors
    de `<base>/` reçoit le projet par défaut.

    Args:
        chemin_relatif: Chemin POSIX relatif à la racine indexée (ex: "projets/sand/CLAUDE.md").
        base: Répertoire base des projets (ex: "projets"). Vide → tout au défaut.
        defaut: Projet attribué hors de la base (ou None).

    Returns:
        Nom du projet, ou `defaut`.
    """
    segments = chemin_relatif.split("/")
    if base and len(segments) >= 2 and segments[0] == base:
        return segments[1]
    return defaut


class ImporteurMarkdownTree(ImporteurBase):
    """Importe un arbre de fichiers Markdown dans la mémoire.

    Attributes:
        _service: Couche métier pour l'ingestion (add) et la purge.
        _exclusions: Noms de répertoires à ignorer pendant le parcours.
        _projet_base: Répertoire base pour la dérivation du projet.
        _projet_defaut: Projet attribué aux fichiers hors de la base.
        _max_chars: Taille maximale d'un chunk.
    """

    SOURCE = "workspace"

    def __init__(
        self,
        service: MemoryService,
        exclusions: frozenset[str] = EXCLUSIONS_DEFAUT,
        projet_base: str = "projets",
        projet_defaut: str | None = None,
        max_chars: int = MAX_CHARS_DEFAUT,
    ):
        """Initialise l'importeur.

        Args:
            service: MemoryService cible.
            exclusions: Répertoires à ignorer (défaut: .git, node_modules, …).
            projet_base: Base de dérivation du projet (défaut: "projets").
            projet_defaut: Projet des fichiers hors base (défaut: None).
            max_chars: Taille maximale d'un chunk (défaut: 2000).
        """
        self._service = service
        self._exclusions = exclusions
        self._projet_base = projet_base
        self._projet_defaut = projet_defaut
        self._max_chars = max_chars

    def _parcourir(self, racine: Path) -> list[Path]:
        """Liste les fichiers `.md` sous racine, en sautant les répertoires exclus.

        Args:
            racine: Répertoire racine à parcourir.

        Returns:
            Liste triée des chemins de fichiers `.md` retenus.
        """
        retenus: list[Path] = []
        for chemin in sorted(racine.rglob("*.md")):
            rel = chemin.relative_to(racine)
            if any(part in self._exclusions for part in rel.parts):
                continue
            retenus.append(chemin)
        return retenus

    def importer(
        self,
        chemin: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Importe l'arbre Markdown enraciné en `chemin`.

        Args:
            chemin: Racine à indexer (obligatoire pour cet importeur).
            on_progress: Callback optionnel de progression, appelé une fois par
                fichier avec `(fichiers_traités, total_fichiers)`. Permet à
                l'appelant (CLI) d'afficher une barre de progression.

        Returns:
            Dict de `ResultatImport.as_dict()` (ajoutes, dedupliques, duree, nb_erreurs).

        Raises:
            ValueError: Si `chemin` est None.
            FileNotFoundError: Si la racine n'existe pas.
        """
        if chemin is None:
            raise ValueError("ImporteurMarkdownTree requiert une racine explicite.")
        racine = Path(chemin)
        if not racine.is_dir():
            raise FileNotFoundError(f"Racine introuvable : {racine}")

        debut = time.monotonic()
        res = ResultatImport()
        fichiers = self._parcourir(racine)

        # Purge idempotente, scopée aux projets du périmètre : ré-indexer un
        # arbre remplace les projets qu'il couvre sans toucher aux autres déjà
        # indexés, et sans jamais empiler de doublons quel que soit le nombre de
        # relances. Si un fichier n'a pas de projet (projet_defaut absent), le
        # scope est indéterminé → repli sûr sur la purge totale de la source
        # (toujours idempotente, jamais une purge mal ciblée).
        projets_perimetre = {
            deriver_projet(f.relative_to(racine).as_posix(), self._projet_base, self._projet_defaut)
            for f in fichiers
        }
        if None in projets_perimetre or "" in projets_perimetre:
            self._service.purger_source(self.SOURCE)
        else:
            for projet_p in projets_perimetre:
                self._service.purger_source(self.SOURCE, projet=projet_p)

        total = len(fichiers)
        for i, fichier in enumerate(fichiers, start=1):
            rel = fichier.relative_to(racine).as_posix()
            projet = deriver_projet(rel, self._projet_base, self._projet_defaut)
            try:
                contenu = fichier.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                res.nb_erreurs += 1
                res.erreurs.append(f"{rel}: {e}")
            else:
                for ancre, texte in decouper_en_sections(contenu, self._max_chars):
                    detail = f"{rel}#{ancre}" if ancre else rel
                    try:
                        self._service.add(
                            texte,
                            categorie="doc",
                            source=self.SOURCE,
                            projet=projet,
                            source_detail=detail,
                            dedup=False,
                        )
                        res.ajoutes += 1
                    except Exception as e:  # noqa: BLE001 — un chunk KO ne doit pas tout arrêter
                        res.nb_erreurs += 1
                        res.erreurs.append(f"{detail}: {e}")
            if on_progress is not None:
                on_progress(i, total)

        res.duree = time.monotonic() - debut
        return res.as_dict()
