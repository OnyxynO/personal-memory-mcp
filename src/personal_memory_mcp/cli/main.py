"""CLI mmcp — Commandes typer avec rich."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
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
    source: str = typer.Argument(help="Source : 'claude-code' ou 'claude'"),
    chemin: Annotated[Optional[str], typer.Argument(help="Chemin vers le fichier (requis pour 'claude')")] = None,
):
    """Importe des faits depuis un historique IA."""
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

    else:
        console.print(f"[red]Source inconnue : '{source}'. Valeurs valides : claude-code, claude, chatgpt[/red]")
        raise typer.Exit(1)


@app.command()
def search(
    query: str = typer.Argument(help="Requête de recherche"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Nombre de résultats"),
    seuil: float = typer.Option(0.70, "--seuil", "-s", help="Seuil de similarité minimum"),
):
    """Recherche sémantique dans la mémoire."""
    svc = _service()
    resultats = svc.search(query, top_k=top_k)
    filtres = [r for r in resultats if r["score"] >= seuil]

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
    limite: int = typer.Option(50, "--limite", "-l"),
):
    """Liste les faits stockés."""
    svc = _service()
    faits = svc.list(categorie=categorie, limite=limite)
    stats = svc._storage.compter()

    console.print(f"\n[bold]{stats['total']} faits[/bold] (actifs)\n")
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
    console.print()


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
