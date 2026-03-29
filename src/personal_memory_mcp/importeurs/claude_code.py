"""ImporteurClaudeCode — parse les fichiers JSONL de ~/.claude/projects/."""

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from personal_memory_mcp.extraction.base import Conversation, Message
from personal_memory_mcp.importeurs.base import ImporteurBase, ResultatImport
from personal_memory_mcp.memory.deduplication import est_doublon

if TYPE_CHECKING:
    from personal_memory_mcp.memory.service import MemoryService

CHEMIN_DEFAUT = Path.home() / ".claude" / "projects"
MIN_MOTS = 10


def _extraire_texte_message(msg: dict) -> str:
    """Extrait le texte d'un message JSONL Claude Code."""
    # Format : message.content[].text (liste de blocs)
    contenu = msg.get("message", {})
    blocs = contenu.get("content", [])
    if isinstance(blocs, list):
        parties = [
            b.get("text", "")
            for b in blocs
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        texte = " ".join(p for p in parties if p).strip()
        if texte:
            return texte
    # Fallback : champ text direct
    return contenu.get("text", "").strip()


def _charger_jsonl(chemin: Path) -> list[dict]:
    messages = []
    with chemin.open("r", encoding="utf-8", errors="ignore") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                messages.append(json.loads(ligne))
            except json.JSONDecodeError:
                continue
    return messages


def _construire_conversation(chemin: Path) -> Conversation | None:
    messages_bruts = _charger_jsonl(chemin)
    messages = []
    for msg in messages_bruts:
        role = msg.get("message", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue
        texte = _extraire_texte_message(msg)
        mots = len(texte.split())
        if mots < MIN_MOTS:
            continue
        # Écarter les messages purement code (> 80% de lignes commençant par espace/tab)
        lignes = texte.splitlines()
        if len(lignes) > 5:
            lignes_code = sum(1 for l in lignes if l.startswith(("    ", "\t")))
            if lignes_code / len(lignes) > 0.8:
                continue
        messages.append(Message(
            role=role,
            contenu=texte[:2000],  # tronquer pour le prompt
            date=msg.get("timestamp"),
        ))
    if not messages:
        return None
    return Conversation(
        source="claude-code",
        source_detail=str(chemin),
        messages=messages,
    )


class ImporteurClaudeCode(ImporteurBase):
    def __init__(self, service: "MemoryService"):
        self._service = service

    def importer(self, chemin: str | None = None) -> dict:
        racine = Path(chemin) if chemin else CHEMIN_DEFAUT
        if not racine.exists():
            return {"erreur": f"Dossier introuvable : {racine}"}

        fichiers = sorted(racine.rglob("*.jsonl"))
        if not fichiers:
            return {"erreur": f"Aucun fichier .jsonl trouvé dans {racine}"}

        resultat = ResultatImport()
        debut = time.monotonic()
        extracteur = self._service._extracteur
        storage = self._service._storage

        for fichier in fichiers:
            try:
                conv = _construire_conversation(fichier)
                if conv is None:
                    continue

                faits = extracteur.extraire(conv)
                if not faits:
                    continue

                contenus = [f.contenu for f in faits]
                embeddings = extracteur.embeddings(contenus)

                for fait, embedding in zip(faits, embeddings):
                    if est_doublon(embedding, storage, self._service._seuil):
                        resultat.dedupliques += 1
                    else:
                        storage.inserer_fait(
                            contenu=fait.contenu,
                            categorie=fait.categorie,
                            source="claude-code",
                            embedding=embedding,
                            source_detail=str(fichier),
                        )
                        resultat.ajoutes += 1
            except Exception as e:
                resultat.nb_erreurs += 1
                resultat.erreurs.append(f"{fichier.name} : {e}")
                continue

        resultat.duree = time.monotonic() - debut
        storage.enregistrer_import(
            type="claude-code",
            chemin=str(racine),
            nb_ajoutes=resultat.ajoutes,
            nb_dedupliques=resultat.dedupliques,
            nb_mis_a_jour=resultat.mis_a_jour,
            duree=resultat.duree,
        )
        return {**resultat.as_dict(), "sessions": len(fichiers)}
