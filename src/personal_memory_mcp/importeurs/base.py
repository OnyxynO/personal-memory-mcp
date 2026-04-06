from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResultatImport:
    """Résultat d'un import de conversation(s).

    Attributes:
        ajoutes: Nombre de nouveaux faits insérés.
        dedupliques: Nombre de doublons détectés et ignorés.
        mis_a_jour: Nombre de faits existants modifiés (future feature).
        duree: Durée de l'import en secondes.
        nb_erreurs: Nombre d'erreurs rencontrées.
        erreurs: Liste des messages d'erreur (pour logs).
    """
    ajoutes: int = 0
    dedupliques: int = 0
    mis_a_jour: int = 0
    duree: float = 0.0
    nb_erreurs: int = 0
    erreurs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Convertit le résultat en dict sérialisable JSON (pour MCP outils).

        Exclut la liste complète des erreurs pour ne pas surcharger la réponse.

        Returns:
            Dict avec clés: ajoutes, dedupliques, mis_a_jour, duree, nb_erreurs.
        """
        return {
            "ajoutes": self.ajoutes,
            "dedupliques": self.dedupliques,
            "mis_a_jour": self.mis_a_jour,
            "duree": round(self.duree, 1),
            "nb_erreurs": self.nb_erreurs,
        }


class ImporteurBase(ABC):
    """Interface abstraite pour importer depuis différentes sources.

    Chaque implémentation (Claude Code JSONL, Claude ZIP, ChatGPT ZIP, etc.)
    doit parser les conversations depuis sa source respective et les traiter
    via la couche métier MemoryService.
    """

    @abstractmethod
    def importer(self, chemin: str | None = None) -> dict:
        """Importe les conversations depuis la source configurée.

        Args:
            chemin: Chemin personnalisé (ZIP pour Claude, dossier pour Claude Code).
                    Si None, utilise le chemin par défaut.

        Returns:
            Dict retourné par ResultatImport.as_dict() avec les stats d'import.

        Raises:
            FileNotFoundError: Si la source n'existe pas.
            ValueError: Si le format est invalide.
        """
        ...
