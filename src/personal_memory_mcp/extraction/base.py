from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Message:
    """Un message dans une conversation.

    Attributes:
        role: Rôle du message ("user" | "assistant").
        contenu: Texte du message.
        date: Timestamp ISO 8601, ou None si absent.
    """
    role: str
    contenu: str
    date: str | None


@dataclass
class Conversation:
    """Une conversation (session) à analyser.

    Attributes:
        source: Type de source ("claude-code" | "claude" | "chatgpt").
        source_detail: Chemin du fichier ou identifiant de session (pour la traçabilité).
        messages: Liste des messages dans l'ordre chronologique.
    """
    source: str
    source_detail: str
    messages: list[Message]


@dataclass
class FaitExtrait:
    """Un fait extrait d'une conversation par le LLM.

    Attributes:
        contenu: Texte du fait (~1 phrase, autonome).
        categorie: Catégorie du fait (stack | projet | preference | decision |
                  contrainte | contexte | autre).
        score_confiance: Confiance du LLM dans le fait [0.0, 1.0].
    """
    contenu: str
    categorie: str
    score_confiance: float


class ExtracteurBase(ABC):
    """Interface abstraite pour extraction de faits et embeddings.

    Deux implémentations possibles:
    - ExtracteurOllama: LLM local via Ollama (MVP actuel).
    - ExtracteurMem0: SDK mem0 (futurs).
    """

    @abstractmethod
    def extraire(self, conversation: Conversation) -> list[FaitExtrait]:
        """Analyse une conversation et en extrait les faits mémorisables.

        Args:
            conversation: Conversation à analyser (messages + contexte).

        Returns:
            Liste de FaitExtrait avec contenu, categorie, score_confiance.

        Raises:
            ValueError: Si le modèle d'extraction n'est pas disponible.
        """
        ...

    @abstractmethod
    def embeddings(self, textes: list[str]) -> list[list[float]]:
        """Calcule les embeddings en batch.

        Important: toujours calculer en batch plutôt que texte par texte
        pour un facteur de performance de ~5-10x.

        Args:
            textes: Liste de textes à encoder (min: 1).

        Returns:
            Liste de vecteurs d'embedding (mêmes ordre et longueur que textes).
            Chaque vecteur a 768 dimensions pour nomic-embed-text.

        Raises:
            ValueError: Si le modèle d'embeddings n'est pas disponible.
        """
        ...

    def version(self) -> str | None:
        """Version du moteur d'embedding sous-jacent, ou None si non applicable.

        Par défaut None — surchargé par les implémentations qui interrogent un
        service versionné (ex: Ollama via `/api/version`). Sert à détecter un
        changement de version susceptible d'avoir altéré les embeddings.
        """
        return None
