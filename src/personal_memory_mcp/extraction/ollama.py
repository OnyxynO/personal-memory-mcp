"""Extracteur de faits et embeddings via Ollama."""

import json
import re
import httpx

from personal_memory_mcp.extraction.base import (
    Conversation,
    ExtracteurBase,
    FaitExtrait,
)

PROMPT_EXTRACTION = """Tu es un extracteur de mémoire personnelle.
Analyse cette conversation et extrais les faits mémorisables sur l'utilisateur.

Règles :
- Un fait = une phrase courte et autonome (~1 ligne)
- Uniquement ce qui sera utile dans de futures sessions
- Pas les faits éphémères (questions ponctuelles, bugs résolus, code ponctuel)
- Catégorie parmi : stack | projet | preference | decision | contrainte | contexte | autre

Retourne UNIQUEMENT un JSON valide, sans commentaire :
[{{"contenu": "...", "categorie": "...", "score_confiance": 0.0}}]

Conversation :
{texte}"""


def _filtrer_think(texte: str) -> str:
    """Supprime les blocs <think>...</think> générés par qwen3."""
    return re.sub(r"<think>.*?</think>", "", texte, flags=re.DOTALL).strip()


def _texte_conversation(conv: Conversation) -> str:
    lignes = []
    for msg in conv.messages:
        role = "Utilisateur" if msg.role == "user" else "Assistant"
        if msg.contenu.strip():
            lignes.append(f"{role}: {msg.contenu[:500]}")
    return "\n".join(lignes)


class ExtracteurOllama(ExtracteurBase):
    def __init__(
        self,
        url: str = "http://localhost:11434",
        modele_extraction: str = "qwen3:1.7b",
        modele_embeddings: str = "nomic-embed-text",
    ):
        self._url = url.rstrip("/")
        self._modele_extraction = modele_extraction
        self._modele_embeddings = modele_embeddings

    def extraire(self, conversation: Conversation) -> list[FaitExtrait]:
        texte = _texte_conversation(conversation)
        if not texte.strip():
            return []

        prompt = PROMPT_EXTRACTION.format(texte=texte)
        reponse = httpx.post(
            f"{self._url}/api/generate",
            json={
                "model": self._modele_extraction,
                "prompt": prompt,
                "stream": False,
                "think": False,
            },
            timeout=120.0,
        )
        reponse.raise_for_status()
        brut = reponse.json().get("response", "")
        nettoye = _filtrer_think(brut)

        try:
            donnees = json.loads(nettoye)
        except json.JSONDecodeError:
            # Tentative de récupération : extraire le JSON entre crochets
            match = re.search(r"\[.*\]", nettoye, re.DOTALL)
            if not match:
                return []
            try:
                donnees = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []

        faits = []
        for item in donnees:
            if not isinstance(item, dict):
                continue
            contenu = item.get("contenu", "").strip()
            categorie = item.get("categorie", "autre").strip()
            score = float(item.get("score_confiance", 0.5))
            if contenu:
                faits.append(FaitExtrait(contenu=contenu, categorie=categorie, score_confiance=score))
        return faits

    def embeddings(self, textes: list[str]) -> list[list[float]]:
        """Calcule les embeddings en un seul appel batch."""
        if not textes:
            return []
        reponse = httpx.post(
            f"{self._url}/api/embed",
            json={"model": self._modele_embeddings, "input": textes},
            timeout=60.0,
        )
        reponse.raise_for_status()
        donnees = reponse.json()
        embeddings = donnees.get("embeddings")
        if not embeddings:
            raise ValueError(
                f"Réponse Ollama inattendue pour les embeddings : {donnees!r}"
            )
        return embeddings

    def verifier_disponibilite(self) -> dict[str, bool]:
        """Vérifie que les modèles requis sont disponibles."""
        try:
            reponse = httpx.get(f"{self._url}/api/tags", timeout=5.0)
            reponse.raise_for_status()
            modeles = {m["name"] for m in reponse.json().get("models", [])}
            # Normaliser : "nomic-embed-text:latest" matche "nomic-embed-text"
            def disponible(nom: str) -> bool:
                return any(m.startswith(nom.split(":")[0]) for m in modeles)
            return {
                self._modele_embeddings: disponible(self._modele_embeddings),
                self._modele_extraction: disponible(self._modele_extraction),
            }
        except Exception:
            return {self._modele_embeddings: False, self._modele_extraction: False}
