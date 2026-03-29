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


def lancer():
    mcp.run()
