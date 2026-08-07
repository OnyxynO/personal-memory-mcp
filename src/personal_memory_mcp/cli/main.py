"""CLI mmcp — Commandes typer avec rich."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

app = typer.Typer(
    name="mmcp",
    help="Personal Memory MCP — mémoire locale extraite depuis vos historiques IA.",
    no_args_is_help=True,
)
console = Console()


def _service():
    from personal_memory_mcp.memory.service import MemoryService
    return MemoryService()


# Version du format d'enveloppe du snapshot portable. Contrat inter-projets
# (consommé par `atelier`) : toute évolution incompatible incrémente ce numéro,
# et un fichier portant une version inconnue est refusé plutôt que mal relu.
VERSION_FORMAT_SNAPSHOT = 1

# Champs texte optionnels d'un fait : str ou absent/null, jamais un conteneur
# (un dict/list part en `sqlite3.InterfaceError` au bind, après insertions).
_CHAMPS_TEXTE_OPTIONNELS = ("categorie", "source", "source_detail", "projet")
# Champs horodatés : ISO 8601 parsable, sinon le tri chronologique et `mmcp clean`
# (qui tronquent la chaîne à 10 caractères) deviennent faux.
_CHAMPS_DATE = ("date_creation", "date_derniere_utilisation")


@dataclass(frozen=True)
class SnapshotFaits:
    """Contenu d'un export logique de faits, enveloppe comprise.

    Attributes:
        faits: Liste des faits, validée champ par champ.
        version_format: Version du format lue dans l'enveloppe, ou None pour un
            fichier hérité (liste JSON nue, sans enveloppe).
        modele_embeddings: Modèle d'embedding de la base source, ou None si
            l'archive ne le porte pas (format hérité).
        dim_embeddings: Dimension des vecteurs de la base source, ou None.
        date_export: Horodatage ISO 8601 de l'export, ou None.
    """

    faits: list[dict[str, Any]] = field(default_factory=list)
    version_format: int | None = None
    modele_embeddings: str | None = None
    dim_embeddings: int | None = None
    date_export: str | None = None


def _valider_faits(faits: list[Any]) -> None:
    """Valide le type de chaque champ de chaque fait, avant toute écriture.

    Une validation purement structurelle (« liste de dicts ») laisse passer des
    valeurs qui ne plantent qu'au moment du bind SQLite ou de la normalisation —
    donc *après* que des faits ont déjà été insérés, sur une opération qui n'est
    pas transactionnelle entre les lots. On rejette donc tout le fichier en amont.

    Args:
        faits: Liste candidate, telle que lue du JSON.

    Raises:
        ValueError: Au premier élément invalide, en citant son index et le champ.
    """
    for i, fait in enumerate(faits):
        if not isinstance(fait, dict):
            raise ValueError(
                f"élément #{i} : objet fait attendu, reçu {type(fait).__name__}. "
                "Le fichier doit contenir une liste JSON d'objets fait "
                "(comme produite par 'mmcp export --complet')."
            )

        contenu = fait.get("contenu")
        if not isinstance(contenu, str) or not contenu.strip():
            recu = type(contenu).__name__ if contenu is not None else "null"
            raise ValueError(f"élément #{i} : champ 'contenu' attendu str non vide, reçu {recu}")

        for cle in _CHAMPS_TEXTE_OPTIONNELS:
            valeur = fait.get(cle)
            if valeur is not None and not isinstance(valeur, str):
                raise ValueError(
                    f"élément #{i} : champ '{cle}' attendu str|null, "
                    f"reçu {type(valeur).__name__}"
                )

        score = fait.get("score_importance")
        if score is not None:
            # `bool` est un `int` en Python : l'exclure explicitement, sinon
            # `true` passerait pour un score valide de 1.0.
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError(
                    f"élément #{i} : champ 'score_importance' attendu nombre dans "
                    f"[0, 1] ou null, reçu {type(score).__name__}"
                )
            if not 0.0 <= float(score) <= 1.0:
                raise ValueError(
                    f"élément #{i} : champ 'score_importance' attendu dans [0, 1], reçu {score}"
                )

        for cle in _CHAMPS_DATE:
            valeur = fait.get(cle)
            if valeur is None:
                continue
            if not isinstance(valeur, str):
                raise ValueError(
                    f"élément #{i} : champ '{cle}' attendu date ISO 8601 ou null, "
                    f"reçu {type(valeur).__name__}"
                )
            try:
                datetime.fromisoformat(valeur)
            except ValueError:
                raise ValueError(
                    f"élément #{i} : champ '{cle}' attendu date ISO 8601, reçu '{valeur}'"
                ) from None


def charger_faits_json(chemin: Path) -> SnapshotFaits:
    """Lit un export logique de faits (`mmcp export --complet`).

    Accepte les deux formes :
    - l'enveloppe objet du contrat courant (`version_format`, `modele_embeddings`,
      `dim_embeddings`, `date_export`, `faits`) ;
    - la liste JSON nue héritée des premiers exports (sans métadonnées).

    Aucun fait n'est écrit tant que le fichier entier n'a pas été validé.

    Args:
        chemin: Fichier JSON à lire.

    Returns:
        Le snapshot : faits validés + métadonnées (None pour le format hérité).

    Raises:
        ValueError: Si le JSON est mal formé, si la structure n'est ni une
            enveloppe ni une liste, si `version_format` est inconnue, ou si un
            fait porte un champ mal typé.
    """
    donnees = json.loads(chemin.read_text(encoding="utf-8"))

    if isinstance(donnees, list):
        # Format hérité : liste nue, aucune métadonnée d'embedding.
        _valider_faits(donnees)
        return SnapshotFaits(faits=donnees)

    if not isinstance(donnees, dict):
        raise ValueError(
            "Format invalide : le fichier doit contenir une enveloppe JSON "
            "{'version_format': 1, …, 'faits': [...]} ou une liste JSON d'objets "
            "fait (comme produite par 'mmcp export --complet')."
        )

    version = donnees.get("version_format")
    if version != VERSION_FORMAT_SNAPSHOT:
        raise ValueError(
            f"Version de format inconnue : {version!r} (attendue : "
            f"{VERSION_FORMAT_SNAPSHOT}). Ce fichier a été produit par une autre "
            f"version de personal-memory — mettez à jour 'personal-memory-mcp' "
            f"(pip install -U personal-memory-mcp) ou ré-exportez le snapshot "
            f"avec la version installée."
        )

    faits = donnees.get("faits")
    if not isinstance(faits, list):
        raise ValueError(
            "Format invalide : l'enveloppe doit porter une clé 'faits' contenant "
            "une liste JSON d'objets fait."
        )
    _valider_faits(faits)

    dim = donnees.get("dim_embeddings")
    modele = donnees.get("modele_embeddings")
    return SnapshotFaits(
        faits=faits,
        version_format=version,
        modele_embeddings=modele if isinstance(modele, str) else None,
        dim_embeddings=dim if isinstance(dim, int) and not isinstance(dim, bool) else None,
        date_export=donnees.get("date_export") if isinstance(donnees.get("date_export"), str) else None,
    )


def _verifier_ollama_embeddings(svc: Any) -> None:
    """Refuse le restore si le modèle d'embedding n'est pas utilisable.

    Sans ce pré-check, le ré-embarquement casse en cours de route sur une erreur
    réseau opaque, après avoir déjà inséré une partie des faits (l'opération
    n'est pas transactionnelle entre les lots).

    `verifier_disponibilite` retourne False aussi bien quand le serveur ne
    répond pas que quand le modèle n'est pas téléchargé : on interroge d'abord
    `/api/version` pour distinguer les deux cas et donner la bonne commande.

    Args:
        svc: MemoryService dont l'extracteur porte le modèle effectif.

    Raises:
        typer.Exit: Code 1 si Ollama ou le modèle est indisponible.
    """
    modele = svc._extracteur._modele_embeddings
    if svc._extracteur.verifier_disponibilite().get(modele):
        return
    if svc._extracteur.version() is None:
        console.print("[red]Ollama est injoignable — le ré-embarquement des faits est impossible.[/red]")
        console.print("[yellow]Démarrer Ollama (`ollama serve`) puis relancer cette commande.[/yellow]")
    else:
        console.print(f"[red]Le modèle d'embedding '{modele}' est absent d'Ollama.[/red]")
        console.print(f"[yellow]Le télécharger (`ollama pull {modele}`) puis relancer cette commande.[/yellow]")
    raise typer.Exit(1)


def _reconcilier_modele_embeddings(svc: Any, snapshot: SnapshotFaits, base_vierge: bool) -> None:
    """Aligne le modèle d'embedding utilisé au restore sur celui de l'archive.

    C'est le cœur de la portabilité : le snapshot ne transporte pas les vecteurs
    mais le texte, donc restaurer avec un autre modèle que celui d'origine
    produit une base cohérente en interne mais différente de la source (autre
    dimension, autres scores) — pour un coût d'une réindexation complète.

    - Base vierge jamais configurée : on adopte le modèle de l'archive et on
      l'écrit en config AVANT la première insertion.
    - Divergence sur une base déjà configurée : avertissement rouge et
      confirmation explicite exigée.

    Args:
        svc: MemoryService à aligner (config DB + extracteur).
        snapshot: Snapshot lu, dont les métadonnées d'embedding.
        base_vierge: True si la base ne contient aucun fait.

    Raises:
        typer.Exit: Code 1 si l'utilisateur refuse de poursuivre malgré la
            divergence (ou en contexte non interactif).
    """
    modele_archive = snapshot.modele_embeddings
    modele_effectif = svc._extracteur._modele_embeddings

    if modele_archive is None:
        console.print(
            "[yellow]⚠ Archive au format hérité (sans métadonnées) : impossible de "
            f"vérifier le modèle d'embedding d'origine. Restauration avec "
            f"'{modele_effectif}'.[/yellow]"
        )
        return

    if modele_archive == modele_effectif:
        return

    if base_vierge and svc._storage.lire_config("modele_embeddings") is None:
        # Base neuve jamais vectorisée : le modèle de l'archive fait autorité.
        # Écrit en config *avant* toute insertion, sinon la base se figerait sur
        # la dimension du modèle par défaut du code.
        svc._storage.ecrire_config("modele_embeddings", modele_archive)
        svc._extracteur._modele_embeddings = modele_archive
        console.print(
            f"[green]Modèle d'embedding de l'archive adopté : '{modele_archive}'[/green] "
            f"[dim](défaut local : '{modele_effectif}')[/dim]"
        )
        return

    console.print(
        f"[red]⚠ Modèle d'embedding divergent : l'archive a été produite avec "
        f"'{modele_archive}', la base utilisera '{modele_effectif}'.[/red]"
    )
    console.print(
        "[yellow]Les faits seront ré-embarqués avec le modèle local : dimension et "
        "scores de similarité différeront de la base d'origine.[/yellow]"
    )
    if not typer.confirm("Poursuivre malgré tout ?", default=False):
        console.print("[dim]Annulé.[/dim]\n")
        raise typer.Exit(1)


def _importer_facts(svc: Any, chemin: str | None, force: bool) -> None:
    """Restaure un export logique de faits (`mmcp export --complet`).

    Args:
        svc: MemoryService cible.
        chemin: Chemin du fichier JSON (enveloppe ou liste héritée).
        force: Autorise l'import par-dessus une base déjà peuplée.

    Raises:
        typer.Exit: Code 1 sur fichier absent/invalide, base non vide sans
            `--force`, Ollama indisponible, ou échec en cours de restauration.
    """
    import time

    if not chemin:
        console.print("[red]Chemin du JSON requis : 'mmcp import facts <faits.json>'[/red]")
        raise typer.Exit(1)
    chemin_json = Path(chemin).expanduser()
    if not chemin_json.exists():
        console.print(f"[red]Fichier introuvable : {chemin_json}[/red]")
        raise typer.Exit(1)

    # Validation intégrale AVANT toute écriture : un champ mal typé découvert au
    # 40ᵉ lot laisserait une base à moitié restaurée.
    try:
        snapshot = charger_faits_json(chemin_json)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    total_existant = svc._storage.compter()["total"]
    if total_existant and not force:
        console.print(f"[red]La base contient déjà {total_existant} faits.[/red]")
        console.print("[yellow]Un restore vise une base neuve. Relancer avec --force pour ajouter par-dessus.[/yellow]")
        raise typer.Exit(1)

    _reconcilier_modele_embeddings(svc, snapshot, base_vierge=total_existant == 0)
    _verifier_ollama_embeddings(svc)

    faits = snapshot.faits
    console.print(f"\nRestauration de [bold]{len(faits)}[/bold] faits depuis {chemin_json.name}")
    if snapshot.date_export:
        console.print(f"[dim]Snapshot du {snapshot.date_export[:10]}[/dim]")
    console.print("[dim]Chaque fait est ré-embarqué localement — comptez plusieurs minutes.[/dim]")
    console.print(
        "[yellow]Opération longue et non réversible : lancez `mmcp backup` avant si la "
        "base contient déjà quelque chose.[/yellow]\n"
    )

    debut = time.time()
    try:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progression:
            tache = progression.add_task("Ré-embarquement", total=len(faits))
            res = svc.importer_faits(
                faits,
                callback=lambda n, t: progression.update(tache, completed=n, total=t),
            )
    except Exception as e:
        # Pas de transaction englobante entre les lots : ce qui est inséré reste.
        # On donne le compte réel et la procédure de reprise, plutôt qu'un traceback.
        nb_inseres = svc._storage.compter()["total"] - total_existant
        console.print(f"\n[red]Restauration interrompue : {e}[/red]")
        console.print(f"[yellow]{nb_inseres} faits ont déjà été insérés — la base est partielle.[/yellow]")
        console.print(
            "[yellow]Reprise : repartir d'une base neuve (ou `mmcp restore` d'une "
            "sauvegarde) puis relancer. Ne PAS relancer avec --force par-dessus le "
            "partiel : les faits déjà insérés seraient dupliqués.[/yellow]"
        )
        raise typer.Exit(1)
    duree = round(time.time() - debut, 1)

    svc._storage.enregistrer_import(
        type="facts",
        chemin=str(chemin_json),
        nb_ajoutes=res["importes"],
        nb_dedupliques=0,
        nb_mis_a_jour=0,
        duree=duree,
    )

    console.print(f"\n  [green]+ {res['importes']} faits restaurés[/green]")
    if res["ignores"]:
        console.print(f"  [yellow]! {res['ignores']} entrées ignorées (contenu vide)[/yellow]")
    console.print(f"  [bold]✓ Terminé en {duree}s[/bold]\n")


@app.command()
def serve():
    """Lance le serveur MCP en mode stdio (utilisé par les clients MCP)."""
    from personal_memory_mcp.mcp.server import lancer
    lancer()


@app.command()
def setup():
    """Détecte les clients MCP et met à jour leurs configurations."""
    from personal_memory_mcp.setup.clients import configurer_clients
    console.print("\n[bold]Clients MCP détectés :[/bold]")
    resultats = configurer_clients()

    for r in resultats:
        icone = "✓" if r.detecte else "✗"
        style = "green" if r.detecte else "dim"
        console.print(f"  [{style}]{icone} {r.nom}[/{style}]")

    console.print("\n[bold]Mise à jour des configs...[/bold]")
    for r in resultats:
        if not r.detecte:
            continue
        if r.action == "mis à jour":
            console.print(f"  [green]✓ {r.nom}[/green]   — mis à jour")
        elif r.action == "déjà présent":
            console.print(f"  [dim]~ {r.nom}[/dim]   — déjà présent")
        elif r.action == "erreur":
            console.print(f"  [red]✗ {r.nom}[/red]   — erreur : {r.erreur}")

    console.print("\n[dim]Redémarrer les clients pour activer personal-memory.[/dim]\n")


@app.command("import")
def import_cmd(
    source: str = typer.Argument(help="Source : claude-code | claude | chatgpt | markdown-tree | facts"),
    chemin: Annotated[Optional[str], typer.Argument(help="Chemin (fichier ZIP, ou racine pour markdown-tree)")] = None,
    inclure_refs: bool = typer.Option(False, "--inclure-refs", help="markdown-tree : indexer aussi _refs/ (repos tiers)"),
    projet_base: str = typer.Option("projets", "--projet-base", help="markdown-tree : base de dérivation du projet"),
    projet_defaut: Annotated[Optional[str], typer.Option("--projet-defaut", help="markdown-tree : projet des fichiers hors base")] = None,
    force: bool = typer.Option(False, "--force", "-f", help="facts : importer même si la base contient déjà des faits"),
):
    """Importe des faits depuis un historique IA ou un arbre Markdown."""
    svc = _service()

    if source == "claude-code":
        from personal_memory_mcp.importeurs.claude_code import ImporteurClaudeCode, CHEMIN_DEFAUT
        racine = Path(chemin) if chemin else CHEMIN_DEFAUT
        fichiers = sorted(racine.rglob("*.jsonl")) if racine.exists() else []
        console.print(f"\nScan {racine} ... [bold]{len(fichiers)} sessions[/bold] trouvées\n")

        importeur = ImporteurClaudeCode(svc)
        with console.status("Extraction des faits en cours..."):
            res = importeur.importer(chemin)

        if "erreur" in res:
            console.print(f"[red]Erreur : {res['erreur']}[/red]")
            raise typer.Exit(1)

        console.print(f"  [green]+ {res['ajoutes']} nouveaux faits[/green]")
        console.print(f"  [dim]= {res['dedupliques']} dédupliqués[/dim]")
        if res.get("nb_erreurs"):
            console.print(f"  [yellow]! {res['nb_erreurs']} erreurs[/yellow]")
        console.print(f"  [bold]✓ Terminé en {res['duree']}s[/bold]\n")

    elif source == "facts":
        _importer_facts(svc, chemin, force)

    elif source == "claude":
        if not chemin:
            console.print("[red]Chemin du ZIP requis pour 'mmcp import claude <chemin.zip>'[/red]")
            raise typer.Exit(1)
        chemin_zip = Path(chemin).expanduser()
        if not chemin_zip.exists():
            console.print(f"[red]Fichier introuvable : {chemin_zip}[/red]")
            raise typer.Exit(1)

        console.print(f"\nLecture de l'export Claude : [bold]{chemin_zip.name}[/bold]")
        from personal_memory_mcp.importeurs.claude import ImporteurClaude
        importeur = ImporteurClaude(svc)
        with console.status("Import en cours..."):
            res = importeur.importer(str(chemin_zip))

        if "erreur" in res:
            console.print(f"[red]Erreur : {res['erreur']}[/red]")
            raise typer.Exit(1)

        console.print(f"  [green]+ {res['ajoutes']} nouveaux faits[/green]")
        console.print(f"  [dim]= {res['dedupliques']} dédupliqués[/dim]")
        console.print(f"  [bold]✓ Terminé en {res['duree']}s[/bold]\n")

    elif source == "chatgpt":
        if not chemin:
            console.print("[red]Chemin du ZIP requis pour 'mmcp import chatgpt <chemin.zip>'[/red]")
            raise typer.Exit(1)
        chemin_zip = Path(chemin).expanduser()
        if not chemin_zip.exists():
            console.print(f"[red]Fichier introuvable : {chemin_zip}[/red]")
            raise typer.Exit(1)

        console.print(f"\nLecture de l'export ChatGPT : [bold]{chemin_zip.name}[/bold]")
        from personal_memory_mcp.importeurs.openai import ImporteurOpenAI
        importeur = ImporteurOpenAI(svc)
        with console.status("Import en cours (via Ollama qwen3)..."):
            res = importeur.importer(str(chemin_zip))

        if "erreur" in res:
            console.print(f"[red]Erreur : {res['erreur']}[/red]")
            raise typer.Exit(1)

        console.print(f"  [green]+ {res['ajoutes']} nouveaux faits[/green]")
        console.print(f"  [dim]= {res['dedupliques']} dédupliqués[/dim]")
        if res.get("nb_erreurs"):
            console.print(f"  [yellow]! {res['nb_erreurs']} erreurs[/yellow]")
        console.print(f"  [bold]✓ Terminé en {res['duree']}s[/bold]\n")

    elif source == "markdown-tree":
        if not chemin:
            console.print("[red]Racine requise : mmcp import markdown-tree <racine>[/red]")
            raise typer.Exit(1)
        from personal_memory_mcp.importeurs.markdown_tree import (
            EXCLUSIONS_DEFAUT,
            ImporteurMarkdownTree,
        )
        from personal_memory_mcp.importeurs.verrou import (
            ImportDejaEnCours,
            verrou_import,
        )
        exclusions = EXCLUSIONS_DEFAUT if inclure_refs else (EXCLUSIONS_DEFAUT | {"_refs"})
        importeur = ImporteurMarkdownTree(
            svc,
            exclusions=exclusions,
            projet_base=projet_base,
            projet_defaut=projet_defaut,
        )
        console.print(f"\nIndexation de [bold]{chemin}[/bold] (découpage + embedding)...")
        # Verrou inter-process : un seul import à la fois (anti-parallélisme —
        # contention SQLite, pression RAM, doublons de course).
        chemin_lock = Path.home() / ".personal-memory" / "import.lock"
        try:
            with verrou_import(chemin_lock), Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                tache = progress.add_task("Indexation", total=None)
                try:
                    res = importeur.importer(
                        chemin,
                        on_progress=lambda traites, total: progress.update(
                            tache, completed=traites, total=total
                        ),
                    )
                except (FileNotFoundError, ValueError) as e:
                    console.print(f"[red]Erreur : {e}[/red]")
                    raise typer.Exit(1)
        except ImportDejaEnCours as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        console.print(f"  [green]+ {res['ajoutes']} chunks indexés[/green]")
        if res.get("nb_erreurs"):
            console.print(f"  [yellow]! {res['nb_erreurs']} erreurs[/yellow]")
        console.print(f"  [bold]✓ Terminé en {res['duree']}s[/bold]\n")

    else:
        console.print(
            f"[red]Source inconnue : '{source}'. Valeurs valides : "
            f"claude-code, claude, chatgpt, markdown-tree, facts[/red]"
        )
        raise typer.Exit(1)


@app.command()
def search(
    query: str = typer.Argument(help="Requête de recherche"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Nombre de résultats"),
    seuil: float = typer.Option(0.20, "--seuil", "-s", help="Seuil de similarité minimum"),
    projet: Annotated[Optional[str], typer.Option("--projet", "-P", help="Filtrer par projet")] = None,
    source: Annotated[Optional[str], typer.Option("--source", help="Filtrer par source (ex: workspace pour le corpus curé)")] = None,
    json_: bool = typer.Option(False, "--json", help="Sortie JSON machine (sans rich)"),
):
    """Recherche sémantique dans la mémoire."""
    svc = _service()
    resultats = svc.search(query, top_k=top_k, projet=projet, source=source)
    filtres = [r for r in resultats if r["score"] >= seuil]

    if json_:
        champs = ("id", "contenu", "categorie", "source", "score")
        sortie = [{k: r[k] for k in champs if k in r} for r in filtres]
        typer.echo(json.dumps(sortie, ensure_ascii=False))
        return

    if not filtres:
        console.print("[dim]Aucun résultat au-dessus du seuil.[/dim]")
        return

    console.print(f"\n[bold]{len(filtres)} résultats[/bold] (similarité > {seuil}) :\n")
    for r in filtres:
        cat = f"[cyan]{r['categorie']:12}[/cyan]"
        score = f"[green]{r['score']:.2f}[/green]"
        console.print(f"  {cat} {r['contenu']:<60} {score}")
    console.print()


@app.command("list")
def list_cmd(
    categorie: Annotated[Optional[str], typer.Option("--categorie", "-c")] = None,
    page: int = typer.Option(1, "--page", "-p", help="Numéro de page (commence à 1)"),
    limite: int = typer.Option(50, "--limite", "-l", help="Faits par page"),
):
    """Liste les faits stockés (paginés)."""
    svc = _service()
    resultat = svc.list(categorie=categorie, page=page, taille_page=limite)
    faits = resultat["faits"]
    total = resultat["total"]
    total_pages = resultat["total_pages"]

    titre = f"[bold]{total} faits[/bold]"
    if categorie:
        titre += f" (catégorie : {categorie})"
    titre += f" — page {page}/{total_pages}"
    console.print(f"\n{titre}\n")

    if not faits:
        console.print("[dim]Aucun fait trouvé.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=5)
    table.add_column("Catégorie", width=12)
    table.add_column("Contenu")
    table.add_column("Source", width=12)
    table.add_column("Date", width=12)

    for f in faits:
        table.add_row(
            str(f["id"]),
            f["categorie"],
            f["contenu"],
            f["source"],
            f["date_creation"][:10],
        )
    console.print(table)
    if total_pages > 1:
        console.print(f"[dim]Page {page}/{total_pages} — utiliser --page N pour naviguer[/dim]")
    console.print()


@app.command()
def status():
    """Vue d'ensemble de l'état du système."""
    svc = _service()
    s = svc.status()

    taille = Path(svc._storage._chemin).stat().st_size if Path(svc._storage._chemin).exists() else 0
    taille_mb = taille / 1024 / 1024

    console.print(f"\n[bold]Base[/bold] : {s['chemin_db']}  ({taille_mb:.1f} MB)")
    console.print(f"[bold]Faits[/bold] : {s['faits']['total']} actifs\n")

    if s["faits"]["par_categorie"]:
        console.print("[bold]Par catégorie :[/bold]")
        for cat, n in s["faits"]["par_categorie"].items():
            console.print(f"  {cat:<14} {n}")

    console.print("\n[bold]Ollama :[/bold]")
    for modele, dispo in s["ollama"].items():
        icone = "✓" if dispo else "✗"
        style = "green" if dispo else "red"
        console.print(f"  [{style}]{icone} {modele}[/{style}]")

    if s["dernier_import"]:
        d = s["dernier_import"]
        console.print(f"\n[bold]Dernier import[/bold] : {d['type']} — {d['date_import'][:10]} (+{d['nb_faits_ajoutes']} faits)")

    if s["coherence_embeddings"]:
        console.print(f"\n[yellow]⚠ {s['coherence_embeddings']}[/yellow]")
    console.print()


@app.command()
def backup(
    destination: Annotated[Optional[str], typer.Argument(help="Fichier ou répertoire de destination")] = None,
):
    """Sauvegarde la base dans ~/.personal-memory/backups/ (ou chemin indiqué)."""
    from datetime import datetime
    svc = _service()
    storage = svc._storage

    if destination:
        dest = Path(destination).expanduser()
        if dest.is_dir() or str(destination).endswith("/"):
            horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = dest / f"memory_{horodatage}.db"
    else:
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = Path.home() / ".personal-memory" / "backups" / f"memory_{horodatage}.db"

    with console.status(f"Sauvegarde vers [bold]{dest}[/bold]..."):
        res = storage.sauvegarder(dest)

    console.print(f"\n[green]✓ Sauvegarde créée[/green]")
    console.print(f"  Fichier  : [bold]{res['destination']}[/bold]")
    console.print(f"  Faits    : {res['faits']} actifs")
    console.print(f"  Taille   : {res['taille_mo']} Mo\n")


@app.command()
def restore(
    fichier: Annotated[Optional[str], typer.Argument(help="Fichier de sauvegarde (.db)")] = None,
    liste: bool = typer.Option(False, "--list", "-l", help="Lister les sauvegardes disponibles"),
    force: bool = typer.Option(False, "--force", "-f", help="Ne pas demander confirmation"),
):
    """Restaure la base depuis une sauvegarde. Arrêter mmcp serve avant."""
    from personal_memory_mcp.memory.storage import Storage

    dossier_backups = Path.home() / ".personal-memory" / "backups"

    if liste or not fichier:
        if not dossier_backups.exists() or not list(dossier_backups.glob("*.db")):
            console.print("[dim]Aucune sauvegarde dans ~/.personal-memory/backups/[/dim]")
            if not fichier:
                raise typer.Exit(0)
        else:
            backups = sorted(dossier_backups.glob("*.db"), reverse=True)
            console.print(f"\n[bold]Sauvegardes disponibles[/bold] ({dossier_backups}) :\n")
            table = Table(show_header=True, header_style="bold")
            table.add_column("Fichier")
            table.add_column("Faits", justify="right")
            table.add_column("Taille", justify="right")
            table.add_column("Date", width=20)
            for b in backups:
                stats = Storage.valider_backup(b)
                if stats:
                    from datetime import datetime
                    mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    table.add_row(b.name, str(stats["faits"]), f"{stats['taille_mo']} Mo", mtime)
                else:
                    table.add_row(b.name, "[red]invalide[/red]", "-", "-")
            console.print(table)
            console.print()
            if not fichier:
                raise typer.Exit(0)

    chemin_backup = Path(fichier).expanduser()  # type: ignore[arg-type]
    if not chemin_backup.exists():
        console.print(f"[red]Fichier introuvable : {chemin_backup}[/red]")
        raise typer.Exit(1)

    stats = Storage.valider_backup(chemin_backup)
    if not stats:
        console.print(f"[red]Fichier invalide ou corrompu : {chemin_backup}[/red]")
        raise typer.Exit(1)

    console.print(f"\nSauvegarde : [bold]{chemin_backup.name}[/bold]")
    console.print(f"  Faits    : {stats['faits']} actifs")
    console.print(f"  Taille   : {stats['taille_mo']} Mo")

    chemin_db = Path.home() / ".personal-memory" / "memory.db"
    if chemin_db.exists():
        console.print(f"\n[yellow]⚠ Ceci remplacera la base actuelle :[/yellow] {chemin_db}")
        if not force:
            confirmer = typer.confirm("Continuer ?", default=False)
            if not confirmer:
                console.print("[dim]Annulé.[/dim]\n")
                raise typer.Exit(0)

    import shutil
    with console.status("Restauration en cours..."):
        shutil.copy2(chemin_backup, chemin_db)

    console.print(f"\n[green]✓ Base restaurée[/green] ({stats['faits']} faits)\n")


@app.command("migrate-embeddings")
def migrate_embeddings(
    modele: str = typer.Option(
        "qwen3-embedding:0.6b",
        "--modele", "-m",
        help="Modèle d'embedding cible (doit être disponible dans Ollama)",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Ne pas demander confirmation"),
):
    """Migre tous les faits vers un nouveau modèle d'embedding.

    Une sauvegarde automatique est créée avant la migration.
    Le serveur MCP (mmcp serve) doit être arrêté avant de lancer cette commande.
    """
    svc = _service()

    stats = svc._storage.compter()
    nb_faits = stats["total"]
    dim_actuelle = svc._storage._dim

    console.print(f"\n[bold]Migration d'embedding[/bold]")
    console.print(f"  Modèle cible  : [cyan]{modele}[/cyan]")
    console.print(f"  Faits à migrer: {nb_faits}")
    console.print(f"  Dimension actuelle : {dim_actuelle}D")
    console.print()

    if not force:
        console.print("[yellow]Une sauvegarde automatique sera créée avant la migration.[/yellow]")
        confirmer = typer.confirm(f"Migrer les {nb_faits} faits vers '{modele}' ?", default=False)
        if not confirmer:
            console.print("[dim]Annulé.[/dim]\n")
            raise typer.Exit(0)

    console.print()
    with console.status("Migration en cours...") as statut:
        try:
            res = svc.migrer_embeddings(modele, callback=lambda n, t: statut.update(f"Migration : {n}/{t} faits..."))
        except Exception as e:
            console.print(f"[red]Erreur durant la migration : {e}[/red]")
            console.print("[yellow]La base originale a été sauvegardée avant la migration.[/yellow]")
            raise typer.Exit(1)

    console.print(f"[green]✓ Migration terminée[/green]")
    console.print(f"  Faits migrés  : {res['faits_migres']}")
    console.print(f"  Ancien modèle : {res['ancien_modele']}")
    console.print(f"  Nouveau modèle: {res['nouveau_modele']}")
    console.print(f"  Sauvegarde    : {res['sauvegarde']}\n")


@app.command()
def export(
    format: str = typer.Option("json", "--format", "-f", help="Format de sortie : json ou csv"),
    categorie: Annotated[Optional[str], typer.Option("--categorie", "-c")] = None,
    sortie: Annotated[Optional[str], typer.Option("--sortie", "-o", help="Fichier de destination (stdout si absent)")] = None,
    complet: bool = typer.Option(False, "--complet", help="Export fidèle (tous champs dont projet, sans plafond) — pour un snapshot portable"),
):
    """Exporte les faits en JSON ou CSV (stdout ou fichier)."""
    import csv
    import io
    import json as json_mod
    from datetime import timezone

    svc = _service()
    enveloppe: dict[str, Any] | None = None
    if complet:
        if format != "json":
            console.print("[red]--complet exige --format json (le round-trip du snapshot est JSON).[/red]")
            raise typer.Exit(1)
        if categorie:
            console.print("[red]--complet exporte toute la mémoire : --categorie n'est pas applicable.[/red]")
            raise typer.Exit(1)
        faits = svc._storage.exporter_faits()
        # Enveloppe du snapshot portable : le fichier doit porter le modèle
        # d'embedding de la base source, sinon un restore sur base neuve
        # retomberait silencieusement sur le modèle par défaut du code (et sa
        # dimension), figeant la base cible sur un modèle qui n'est pas le bon.
        dim = svc._storage.lire_config("dim_embeddings")
        enveloppe = {
            "version_format": VERSION_FORMAT_SNAPSHOT,
            "modele_embeddings": svc._extracteur._modele_embeddings,
            "dim_embeddings": int(dim) if dim else None,
            "date_export": datetime.now(timezone.utc).isoformat(),
            "faits": faits,
        }
    else:
        stats = svc._storage.compter()
        total = stats["total"]
        faits = svc._storage.lister(categorie=categorie, limite=total or 1000)

    if format == "csv":
        buffer = io.StringIO()
        champs = ["id", "contenu", "categorie", "source", "source_detail", "date_creation"]
        writer = csv.DictWriter(buffer, fieldnames=champs, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(faits)
        contenu = buffer.getvalue()
    elif format == "json":
        contenu = json_mod.dumps(enveloppe if enveloppe is not None else faits, ensure_ascii=False, indent=2)
    else:
        console.print(f"[red]Format inconnu : '{format}'. Valeurs valides : json, csv[/red]")
        raise typer.Exit(1)

    if sortie:
        chemin = Path(sortie).expanduser()
        chemin.write_text(contenu, encoding="utf-8")
        filtre = f" (catégorie : {categorie})" if categorie else ""
        console.print(f"\n[green]✓ {len(faits)} faits exportés[/green]{filtre} → [bold]{chemin}[/bold]\n")
    else:
        print(contenu)


@app.command()
def ui(
    port: int = typer.Option(8766, "--port", "-p", help="Port HTTP local"),
):
    """Lance l'interface web locale pour visualiser et gérer les faits."""
    from personal_memory_mcp.ui.serveur import lancer
    lancer(port=port)


@app.command()
def clean():
    """Supprime les faits expirés (jamais utilisés ou > 12 mois sans utilisation)."""
    from datetime import datetime, timezone, timedelta
    svc = _service()
    storage = svc._storage
    seuil_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

    conn = storage._conn
    rows = conn.execute(
        """
        SELECT id, contenu, categorie, date_creation, date_derniere_utilisation
        FROM faits
        WHERE actif = 1
          AND (date_derniere_utilisation IS NULL OR date_derniere_utilisation < ?)
        ORDER BY id
        """,
        (seuil_date,),
    ).fetchall()

    if not rows:
        console.print("[green]Aucun fait expiré.[/green]")
        return

    console.print(f"\n[bold]Faits expirés[/bold] (dernière utilisation > 12 mois) :\n")
    for r in rows:
        utilisation = r[4][:10] if r[4] else "jamais utilisé"
        console.print(f"  #{r[0]}  [{r[2]}]  {r[1][:60]}   ({utilisation}, créé {r[3][:10]})")

    console.print()
    confirmer = typer.confirm(f"Supprimer ces {len(rows)} faits ?", default=False)
    if confirmer:
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"UPDATE faits SET actif = 0 WHERE id IN ({placeholders})", ids)
        conn.commit()
        console.print(f"  [green]✓ {len(ids)} faits supprimés[/green]\n")
    else:
        console.print("[dim]Annulé.[/dim]\n")
