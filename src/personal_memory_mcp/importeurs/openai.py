"""ImporteurOpenAI — import depuis un export ZIP officiel ChatGPT."""

import io
import json
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from personal_memory_mcp.extraction.base import Conversation, Message
from personal_memory_mcp.importeurs.base import ImporteurBase, ResultatImport
from personal_memory_mcp.memory.deduplication import est_doublon

if TYPE_CHECKING:
    from personal_memory_mcp.memory.service import MemoryService


def _extraire_texte_message(msg: dict) -> str:
    """Extrait le texte d'un message depuis le format mapping ChatGPT."""
    content = msg.get("content", {})
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts", [])
    textes = [p for p in parts if isinstance(p, str) and p.strip()]
    return " ".join(textes).strip()


def _conversations_depuis_json(data: list[dict]) -> list[Conversation]:
    convs = []
    for conv in data:
        mapping = conv.get("mapping", {})
        if not mapping:
            continue

        # Reconstituer l'ordre des messages via le graphe parent/enfant
        # Partir du current_node et remonter vers la racine
        current_node_id = conv.get("current_node")
        if not current_node_id:
            continue

        chemin: list[str] = []
        noeud_id = current_node_id
        while noeud_id:
            chemin.append(noeud_id)
            noeud = mapping.get(noeud_id, {})
            noeud_id = noeud.get("parent")
        chemin.reverse()

        messages = []
        for noeud_id in chemin:
            noeud = mapping.get(noeud_id, {})
            msg = noeud.get("message")
            if not msg:
                continue
            role = msg.get("author", {}).get("role", "")
            if role not in ("user", "assistant"):
                continue
            # Ignorer les messages vides ou de poids nul (branches alternatives)
            if msg.get("weight", 1) == 0:
                continue
            texte = _extraire_texte_message(msg)
            if len(texte.split()) < 5:
                continue
            messages.append(Message(
                role=role,
                contenu=texte[:2000],
                date=str(msg.get("create_time") or ""),
            ))

        if messages:
            convs.append(Conversation(
                source="chatgpt",
                source_detail=conv.get("id", conv.get("conversation_id", "")),
                messages=messages,
            ))
    return convs


class ImporteurOpenAI(ImporteurBase):
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
            if "conversations.json" not in noms:
                return {"erreur": "conversations.json introuvable dans le ZIP"}

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
                            source="chatgpt",
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
            type="chatgpt",
            chemin=str(chemin_zip),
            nb_ajoutes=resultat.ajoutes,
            nb_dedupliques=resultat.dedupliques,
            nb_mis_a_jour=resultat.mis_a_jour,
            duree=resultat.duree,
        )
        return resultat.as_dict()
