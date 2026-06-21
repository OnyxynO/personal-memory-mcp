"""Serveur MCP personal-memory — 6 outils."""

from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from personal_memory_mcp.memory.service import MemoryService

mcp = FastMCP("personal-memory")
_service: MemoryService | None = None


def _valider_chemin_local(chemin: str) -> str | None:
    """Retourne un message d'erreur si le chemin n'est pas un fichier local sûr, sinon None.

    Refuse les URLs (http, https, file, ftp, etc.) et exige un chemin absolu existant.
    Protection contre l'exfiltration via chaîne d'outils (MCTS-T-1005).
    """
    parsed = urlparse(chemin)
    if parsed.scheme and parsed.scheme not in ("", "file"):
        return f"chemin refusé : schéma '{parsed.scheme}' non autorisé, seuls les chemins locaux sont acceptés"
    p = Path(chemin).expanduser()
    if not p.is_absolute():
        return f"chemin refusé : chemin relatif non autorisé, fournir un chemin absolu : {chemin}"
    if not p.exists():
        return f"chemin introuvable : {p}"
    return None


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
def list_facts(categorie: str | None = None, page: int = 1, taille_page: int = 20) -> dict:
    """Liste les faits stockés avec pagination.

    Préférer search() pour les recherches ponctuelles — list_facts() est conçu pour
    l'exploration exhaustive de la base. Sans filtre, chaque fait pèse ~70 tokens.

    Args:
        categorie: Filtre optionnel (stack, projet, preference, decision,
                   contrainte, contexte, autre). Si absent, tous les faits.
        page:       Numéro de page, commence à 1 (défaut: 1).
        taille_page: Faits par page (défaut: 20, max conseillé: 50).

    Returns:
        {
          "faits": [{"id": int, "contenu": str, "categorie": str, ...}, ...],
          "page": int,
          "total_pages": int,
          "total": int
        }
    """
    if page < 1:
        return {"erreur": f"page doit être >= 1, reçu : {page}"}
    if not (1 <= taille_page <= 100):
        return {"erreur": f"taille_page doit être entre 1 et 100, reçu : {taille_page}"}
    return _get_service().list(categorie=categorie, page=page, taille_page=taille_page)


@mcp.tool()
def import_source(type: str, chemin: str | None = None) -> dict:
    """Déclenche un import depuis le client MCP (claude-code ou claude)."""
    svc = _get_service()

    if chemin is not None:
        erreur = _valider_chemin_local(chemin)
        if erreur:
            return {"erreur": erreur}

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
    elif type == "chatgpt":
        if not chemin:
            return {"erreur": "chemin requis pour l'import chatgpt"}
        from personal_memory_mcp.importeurs.openai import ImporteurOpenAI
        importeur = ImporteurOpenAI(svc)
        return importeur.importer(chemin)
    else:
        return {"erreur": f"type inconnu : {type}. Valeurs valides : claude-code, claude, chatgpt"}


@mcp.tool()
def delete(id: int, confirm_id: int) -> dict:
    """Supprime un fait par son identifiant.

    Opération destructive irréversible. Pour confirmer, `confirm_id` doit être
    égal à `id` — l'agent doit donc explicitement saisir l'identifiant deux fois,
    ce qui prévient les suppressions accidentelles dans une chaîne d'outils.

    Args:
        id: Identifiant du fait à supprimer.
        confirm_id: Doit être égal à `id` pour confirmer la suppression.

    Returns:
        {"succes": bool, "id": int} ou {"erreur": str} si la confirmation échoue.
    """
    if confirm_id != id:
        return {
            "erreur": (
                f"confirmation requise : confirm_id ({confirm_id}) doit être égal à id ({id})"
            )
        }
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
        source:      "claude-code" (sessions ~/.claude), "claude" ou "chatgpt" (ZIP export)
        chemin:      Chemin vers le fichier ZIP (requis si source="claude" ou "chatgpt")
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
        lire_openai_zip,
        paginer,
    )

    # Validation des paramètres de pagination
    if page < 1:
        return {"erreur": f"page doit être >= 1, reçu : {page}"}
    if not (1 <= taille_page <= 50):
        return {"erreur": f"taille_page doit être entre 1 et 50, reçu : {taille_page}"}

    # Validation du chemin (refuse URLs et chemins relatifs — MCTS-T-1005)
    if chemin is not None:
        erreur = _valider_chemin_local(chemin)
        if erreur:
            return {"erreur": erreur}

    if source == "claude-code":
        conversations = lire_claude_code(chemin)
    elif source == "claude":
        if not chemin:
            return {"erreur": "chemin requis pour source='claude'"}
        conversations = lire_claude_zip(chemin)
    elif source == "chatgpt":
        if not chemin:
            return {"erreur": "chemin requis pour source='chatgpt'"}
        conversations = lire_openai_zip(chemin)
    else:
        return {"erreur": f"source inconnue : '{source}'. Valeurs : claude-code, claude, chatgpt"}

    if not conversations:
        return {"erreur": f"Aucune conversation trouvée (source={source}, chemin={chemin})"}

    return paginer(conversations, page=page, taille_page=taille_page)


def lancer():
    import logging

    # Avertir (sur stderr, via logging) si la version d'Ollama a changé depuis
    # la vectorisation — les embeddings peuvent être devenus incohérents.
    avertissement = _get_service().verifier_coherence_embeddings()
    if avertissement:
        logging.getLogger(__name__).warning(avertissement)
    mcp.run()
