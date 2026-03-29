"""Déduplication vectorielle : seuil cosinus >= 0.92 → doublon."""

from personal_memory_mcp.memory.storage import Storage


SEUIL_PAR_DEFAUT = 0.92


def est_doublon(
    embedding: list[float],
    storage: Storage,
    seuil: float = SEUIL_PAR_DEFAUT,
) -> bool:
    """Retourne True si un fait similaire existe déjà."""
    voisins = storage.voisins_proches(embedding, top_k=3)
    if not voisins:
        return False
    # distance L2 sqlite-vec → similarité cosinus approximative : 1 - distance
    distance_min = min(d for _, d in voisins)
    return (1 - distance_min) >= seuil
