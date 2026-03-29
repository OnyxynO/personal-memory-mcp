from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResultatImport:
    ajoutes: int = 0
    dedupliques: int = 0
    mis_a_jour: int = 0
    duree: float = 0.0
    nb_erreurs: int = 0
    erreurs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ajoutes": self.ajoutes,
            "dedupliques": self.dedupliques,
            "mis_a_jour": self.mis_a_jour,
            "duree": round(self.duree, 1),
            "nb_erreurs": self.nb_erreurs,
        }


class ImporteurBase(ABC):
    @abstractmethod
    def importer(self, chemin: str | None = None) -> dict: ...
