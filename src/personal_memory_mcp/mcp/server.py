"""Serveur MCP personal-memory — 5 outils."""

from mcp.server.fastmcp import FastMCP
from personal_memory_mcp.memory.service import MemoryService

mcp = FastMCP("personal-memory")
_service: MemoryService | None = None


def _get_service() -> MemoryService:
    global _service
    if _service is None:
        _service = MemoryService()
    return _service


@mcp.tool()
def search(query: str, top_k: int = 5, categorie: str | None = None) -> list[dict]:
    """Recherche sémantique dans la mémoire personnelle."""
    return _get_service().search(query, top_k=top_k, categorie=categorie)


@mcp.tool()
def add(contenu: str, categorie: str = "autre", source: str = "manuel") -> dict:
    """Ajoute un fait en mémoire (avec déduplication automatique)."""
    return _get_service().add(contenu, categorie=categorie, source=source)


@mcp.tool()
def list_facts(categorie: str | None = None, limite: int = 50) -> list[dict]:
    """Liste les faits stockés, optionnellement filtrés par catégorie."""
    return _get_service().list(categorie=categorie, limite=limite)


@mcp.tool()
def import_source(type: str, chemin: str | None = None) -> dict:
    """Déclenche un import depuis le client MCP (claude-code ou claude)."""
    svc = _get_service()

    if type == "claude-code":
        from personal_memory_mcp.importeurs.claude_code import ImporteurClaudeCode
        importeur = ImporteurClaudeCode(svc)
        return importeur.importer(chemin)
    elif type == "claude":
        if not chemin:
            return {"erreur": "chemin requis pour l'import claude"}
        from personal_memory_mcp.importeurs.claude import ImporteurClaude
        importeur = ImporteurClaude(svc)
        return importeur.importer(chemin)
    else:
        return {"erreur": f"type inconnu : {type}. Valeurs valides : claude-code, claude"}


@mcp.tool()
def delete(id: int) -> dict:
    """Supprime un fait par son identifiant."""
    return _get_service().delete(id)


@mcp.tool()
def import_conversations(
    source: str,
    chemin: str | None = None,
    page: int = 1,
    taille_page: int = 5,
) -> dict:
    """Retourne des conversations brutes paginées pour que l'IA les analyse.

    L'IA doit appeler cet outil en boucle (page=1, 2, 3...) jusqu'à épuisement,
    et pour chaque conversation décider quels faits mémoriser via add().

    Workflow attendu :
      1. Appeler import_conversations(source, page=1) → lire les conversations
      2. Pour chaque fait durable détecté → appeler add(contenu, categorie)
      3. Appeler import_conversations(source, page=2) → continuer
      4. Répéter jusqu'à ce que page > total_pages

    Catégories disponibles pour add() :
      stack | projet | preference | decision | contrainte | contexte | autre

    Args:
        source:      "claude-code" (sessions ~/.claude) ou "claude" (ZIP export)
        chemin:      Chemin vers le fichier ZIP (requis si source="claude")
        page:        Numéro de page, commence à 1
        taille_page: Nombre de conversations par page (défaut 5, max conseillé 10)

    Returns:
        {
          "conversations": [{"source_detail": str, "texte": str}, ...],
          "page": int,
          "total_pages": int,
          "total_conversations": int
        }
    """
    from personal_memory_mcp.importeurs.lecteur import (
        lire_claude_code,
        lire_claude_zip,
        paginer,
    )

    if source == "claude-code":
        conversations = lire_claude_code(chemin)
    elif source == "claude":
        if not chemin:
            return {"erreur": "chemin requis pour source='claude'"}
        conversations = lire_claude_zip(chemin)
    else:
        return {"erreur": f"source inconnue : '{source}'. Valeurs : claude-code, claude"}

    if not conversations:
        return {"erreur": f"Aucune conversation trouvée (source={source}, chemin={chemin})"}

    return paginer(conversations, page=page, taille_page=taille_page)


def lancer():
    mcp.run()
