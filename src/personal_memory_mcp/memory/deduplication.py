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
    # faits_vec utilise distance_metric=cosine → distance = 1 - cosine_sim
    # donc cosine_sim = 1 - distance, le seuil s'applique directement
    distance_min = min(d for _, d in voisins)
    return (1 - distance_min) >= seuil
