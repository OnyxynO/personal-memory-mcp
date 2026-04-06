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
    """Supprime les blocs <think>...</think> générés par qwen3.

    qwen3 produit des balises de réflexion avant la réponse JSON. Elles doivent
    être supprimées pour parser correctement le JSON.

    Args:
        texte: Réponse brute d'Ollama.

    Returns:
        Texte nettoyé sans blocs de réflexion.
    """
    return re.sub(r"<think>.*?</think>", "", texte, flags=re.DOTALL).strip()


def _texte_conversation(conv: Conversation) -> str:
    """Formate une conversation en texte lisible pour le LLM.

    Messages tronqués à 500 chars chacun pour rester dans les limites du contexte.
    Vide messages au format "Utilisateur: ... \n Assistant: ..." alterne.

    Args:
        conv: Conversation à formater.

    Returns:
        Texte formaté, prêt à être inséré dans le prompt.
    """
    lignes = []
    for msg in conv.messages:
        role = "Utilisateur" if msg.role == "user" else "Assistant"
        if msg.contenu.strip():
            lignes.append(f"{role}: {msg.contenu[:500]}")
    return "\n".join(lignes)


class ExtracteurOllama(ExtracteurBase):
    """Implémentation MVP : extraction de faits et embeddings via Ollama local.

    Utilise deux modèles:
    - qwen3:1.7b pour extraction de faits (LLM).
    - nomic-embed-text pour embeddings (768 dimensions).

    Attributes:
        _url: URL du serveur Ollama (ex: http://localhost:11434).
        _modele_extraction: Modèle pour extraction de faits.
        _modele_embeddings: Modèle pour embeddings.
    """

    def __init__(
        self,
        url: str = "http://localhost:11434",
        modele_extraction: str = "qwen3:1.7b",
        modele_embeddings: str = "nomic-embed-text",
    ):
        """Initialise l'extracteur Ollama.

        Les modèles ne sont pas vérifiés à la construction — la vérification
        se fait à la première utilisation. Cela permet d'instancier le service
        même si Ollama n'est pas disponible (utile pour tests).

        Args:
            url: URL du serveur Ollama.
            modele_extraction: Modèle pour extraire les faits.
            modele_embeddings: Modèle pour calculer les embeddings.
        """
        self._url = url.rstrip("/")
        self._modele_extraction = modele_extraction
        self._modele_embeddings = modele_embeddings

    def extraire(self, conversation: Conversation) -> list[FaitExtrait]:
        """Analyse une conversation et en extrait les faits.

        1. Formate la conversation en prompt.
        2. Appelle le LLM (qwen3:1.7b) via `/api/generate`.
        3. Filtre les blocs <think>.
        4. Parse le JSON retourné.
        5. Retourne les FaitExtrait validés.

        La réponse LLM est attendue en JSON:
        ```json
        [{"contenu": "...", "categorie": "...", "score_confiance": 0.5}]
        ```

        En cas d'erreur de parsing, tente une récupération en cherchant du JSON
        entre crochets. Si ça échoue aussi, retourne une liste vide.

        Args:
            conversation: Conversation à analyser.

        Returns:
            Liste de FaitExtrait (contenu, categorie, score_confiance).
            Peut être vide si la conversation est vide ou si le parsing échoue.

        Raises:
            httpx.HTTPError: Si la requête Ollama échoue.
        """
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
        """Calcule les embeddings en un seul appel batch Ollama.

        **Critique:** Toujours calculer en batch (une requête HTTP) plutôt que
        texte par texte. Un seul appel batch = 5-10x plus rapide.

        Args:
            textes: Liste de textes à encoder (min: 1, max: limité par Ollama).

        Returns:
            Liste de vecteurs d'embedding (même longueur que textes).
            Chaque vecteur a 768 dimensions pour nomic-embed-text.

        Raises:
            ValueError: Si la réponse Ollama ne contient pas de clé "embeddings".
            httpx.HTTPError: Si la requête échoue.
        """
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
        """Vérifie que les modèles requis sont disponibles sur Ollama.

        Appelle `/api/tags` pour lister les modèles présents. En cas d'erreur
        réseau ou de timeout, retourne False pour tous les modèles.

        Returns:
            Dict {nom_modele: disponible} pour les deux modèles de ce service.
            Exemple: {"nomic-embed-text": True, "qwen3:1.7b": False}

        Note:
            La correspondance est faite sur le prefix du nom (ex: "nomic-embed-text"
            correspond à "nomic-embed-text:latest").
        """
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
