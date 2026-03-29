"""ImporteurClaude — import depuis un export ZIP officiel Claude."""

import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from personal_memory_mcp.extraction.base import Conversation, Message
from personal_memory_mcp.importeurs.base import ImporteurBase, ResultatImport
from personal_memory_mcp.memory.deduplication import est_doublon

if TYPE_CHECKING:
    from personal_memory_mcp.memory.service import MemoryService

# Correspondance section memories.json → catégorie
SECTION_CATEGORIES = {
    "work context": "contexte",
    "personal context": "contexte",
    "top of mind": "projet",
    "booklist": "preference",
    "brief history": "contexte",
}


def _categorie_section(titre: str) -> str:
    titre_bas = titre.lower().strip()
    for cle, cat in SECTION_CATEGORIES.items():
        if cle in titre_bas:
            return cat
    return "autre"


def _extraire_faits_memories(contenu_markdown: str) -> list[tuple[str, str]]:
    """Extrait les faits directement depuis memories.json (sans LLM)."""
    faits = []
    section_courante = "autre"

    for ligne in contenu_markdown.splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        # Titre de section : **Titre**
        match_titre = re.match(r"^\*\*(.+?)\*\*$", ligne)
        if match_titre:
            section_courante = _categorie_section(match_titre.group(1))
            continue
        # Ligne de contenu non vide → fait candidat
        texte = re.sub(r"^\s*[-*]\s*", "", ligne).strip()
        if len(texte.split()) >= 5:
            faits.append((texte, section_courante))
    return faits


def _extraire_texte_message_claude(msg: dict) -> str:
    """Extrait le texte d'un message export Claude officiel."""
    blocs = msg.get("content", [])
    if isinstance(blocs, list):
        parties = [
            b.get("text", "")
            for b in blocs
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parties if p).strip()
    return ""


def _conversations_depuis_json(data: list[dict]) -> list[Conversation]:
    convs = []
    for conv in data:
        messages_bruts = conv.get("chat_messages", [])
        messages = []
        for msg in messages_bruts:
            role = msg.get("sender", "")
            if role == "human":
                role = "user"
            elif role == "assistant":
                role = "assistant"
            else:
                continue
            texte = _extraire_texte_message_claude(msg)
            if len(texte.split()) < 10:
                continue
            messages.append(Message(
                role=role,
                contenu=texte[:2000],
                date=msg.get("created_at"),
            ))
        if messages:
            convs.append(Conversation(
                source="claude",
                source_detail=conv.get("uuid", ""),
                messages=messages,
            ))
    return convs


class ImporteurClaude(ImporteurBase):
    def __init__(self, service: "MemoryService"):
        self._service = service

    def importer(self, chemin: str | None = None) -> dict:
        if not chemin:
            return {"erreur": "Chemin du ZIP requis"}
        chemin_zip = Path(chemin).expanduser()
        if not chemin_zip.exists():
            return {"erreur": f"Fichier introuvable : {chemin_zip}"}

        debut = time.monotonic()
        resultat = ResultatImport()
        extracteur = self._service._extracteur
        storage = self._service._storage

        data = chemin_zip.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            noms = zf.namelist()

            # 1. memories.json — sans LLM
            if "memories.json" in noms:
                try:
                    memories_data = json.loads(zf.read("memories.json"))
                    for entry in memories_data:
                        markdown = entry.get("conversations_memory", "")
                        if not markdown:
                            continue
                        faits_bruts = _extraire_faits_memories(markdown)
                        if not faits_bruts:
                            continue
                        contenus = [f for f, _ in faits_bruts]
                        embeddings = extracteur.embeddings(contenus)
                        for (contenu, categorie), embedding in zip(faits_bruts, embeddings):
                            if est_doublon(embedding, storage, self._service._seuil):
                                resultat.dedupliques += 1
                            else:
                                storage.inserer_fait(
                                    contenu=contenu,
                                    categorie=categorie,
                                    source="claude",
                                    embedding=embedding,
                                    source_detail="memories.json",
                                )
                                resultat.ajoutes += 1
                except Exception as e:
                    resultat.nb_erreurs += 1
                    resultat.erreurs.append(f"memories.json : {e}")

            # 2. conversations.json — avec LLM
            if "conversations.json" in noms:
                convs_data = json.loads(zf.read("conversations.json"))
                convs = _conversations_depuis_json(convs_data)
                for conv in convs:
                    try:
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
                                    source="claude",
                                    embedding=embedding,
                                    source_detail=conv.source_detail,
                                )
                                resultat.ajoutes += 1
                    except Exception as e:
                        resultat.nb_erreurs += 1
                        resultat.erreurs.append(f"conv {conv.source_detail} : {e}")
                        continue

        resultat.duree = time.monotonic() - debut
        storage.enregistrer_import(
            type="claude",
            chemin=str(chemin_zip),
            nb_ajoutes=resultat.ajoutes,
            nb_dedupliques=resultat.dedupliques,
            nb_mis_a_jour=resultat.mis_a_jour,
            duree=resultat.duree,
        )
        return resultat.as_dict()
