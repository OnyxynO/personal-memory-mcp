from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Message:
    role: str         # "user" | "assistant"
    contenu: str
    date: str | None  # ISO 8601


@dataclass
class Conversation:
    source: str        # "claude-code" | "claude" | "chatgpt"
    source_detail: str
    messages: list[Message]


@dataclass
class FaitExtrait:
    contenu: str
    categorie: str
    score_confiance: float


class ExtracteurBase(ABC):
    @abstractmethod
    def extraire(self, conversation: Conversation) -> list[FaitExtrait]: ...

    @abstractmethod
    def embeddings(self, textes: list[str]) -> list[list[float]]: ...
