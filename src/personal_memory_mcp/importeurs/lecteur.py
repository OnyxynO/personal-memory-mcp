"""Lecteur de conversations — parsing pur, sans LLM ni embeddings.

Utilisé par l'outil MCP import_conversations pour retourner le texte brut
des conversations à l'IA cliente, qui décide elle-même quoi mémoriser.
"""

import io
import json
import zipfile
from pathlib import Path

CHEMIN_DEFAUT_CLAUDE_CODE = Path.home() / ".claude" / "projects"
MIN_MOTS = 10


# ---------------------------------------------------------------------------
# Claude Code (JSONL)
# ---------------------------------------------------------------------------

def _extraire_texte_message_jsonl(msg: dict) -> str:
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
    return contenu.get("text", "").strip()


def _lire_jsonl(chemin: Path) -> str | None:
    """Lit un fichier JSONL et retourne le texte formaté de la conversation."""
    lignes_conv = []
    try:
        with chemin.open("r", encoding="utf-8", errors="ignore") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    msg = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                role = msg.get("message", {}).get("role", "")
                if role not in ("user", "assistant"):
                    continue
                texte = _extraire_texte_message_jsonl(msg)
                if len(texte.split()) < MIN_MOTS:
                    continue
                label = "Utilisateur" if role == "user" else "Assistant"
                lignes_conv.append(f"{label}: {texte[:1000]}")
    except OSError:
        return None

    if not lignes_conv:
        return None
    return "\n".join(lignes_conv)


def lire_claude_code(chemin: str | None = None) -> list[dict]:
    """Retourne toutes les conversations Claude Code sous forme de liste.

    Chaque élément : {"source_detail": str, "texte": str}
    """
    racine = Path(chemin).expanduser() if chemin else CHEMIN_DEFAUT_CLAUDE_CODE
    if not racine.exists():
        return []

    conversations = []
    for fichier in sorted(racine.rglob("*.jsonl")):
        texte = _lire_jsonl(fichier)
        if texte:
            conversations.append({
                "source_detail": str(fichier),
                "texte": texte,
            })
    return conversations


# ---------------------------------------------------------------------------
# Claude ZIP
# ---------------------------------------------------------------------------

def _extraire_texte_message_claude(msg: dict) -> str:
    blocs = msg.get("content", [])
    if isinstance(blocs, list):
        parties = [
            b.get("text", "")
            for b in blocs
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parties if p).strip()
    return ""


def lire_claude_zip(chemin: str) -> list[dict]:
    """Retourne toutes les conversations depuis un export ZIP Claude.

    Chaque élément : {"source_detail": str, "texte": str}
    La mémoire synthétisée (memories.json) est retournée en premier,
    sous source_detail="memories.json", avec le markdown brut comme texte.
    """
    chemin_zip = Path(chemin).expanduser()
    if not chemin_zip.exists():
        return []

    conversations = []
    data = chemin_zip.read_bytes()

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        noms = zf.namelist()

        # 1. memories.json en premier — markdown brut, l'IA lit directement
        if "memories.json" in noms:
            try:
                memories_data = json.loads(zf.read("memories.json"))
                for entry in memories_data:
                    markdown = entry.get("conversations_memory", "").strip()
                    if markdown:
                        conversations.append({
                            "source_detail": "memories.json",
                            "texte": markdown,
                        })
            except Exception:
                pass

        # 2. conversations.json
        if "conversations.json" in noms:
            try:
                convs_data = json.loads(zf.read("conversations.json"))
                for conv in convs_data:
                    lignes_conv = []
                    for msg in conv.get("chat_messages", []):
                        role = msg.get("sender", "")
                        if role == "human":
                            label = "Utilisateur"
                        elif role == "assistant":
                            label = "Assistant"
                        else:
                            continue
                        texte = _extraire_texte_message_claude(msg)
                        if len(texte.split()) < MIN_MOTS:
                            continue
                        lignes_conv.append(f"{label}: {texte[:1000]}")
                    if lignes_conv:
                        conversations.append({
                            "source_detail": conv.get("uuid", ""),
                            "texte": "\n".join(lignes_conv),
                        })
            except Exception:
                pass

    return conversations


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def paginer(conversations: list[dict], page: int, taille_page: int) -> dict:
    """Découpe la liste en pages et retourne la page demandée."""
    total = len(conversations)
    taille_page = max(1, taille_page)
    total_pages = max(1, (total + taille_page - 1) // taille_page)
    page = max(1, min(page, total_pages))

    debut = (page - 1) * taille_page
    fin = debut + taille_page

    return {
        "conversations": conversations[debut:fin],
        "page": page,
        "total_pages": total_pages,
        "total_conversations": total,
    }
