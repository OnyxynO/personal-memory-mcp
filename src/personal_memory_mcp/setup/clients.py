"""Détection et patch des configs MCP clients."""

import json
from dataclasses import dataclass
from pathlib import Path


ENTREE_MCP = {
    "command": "mmcp",
    "args": ["serve"],
}

CLIENTS = [
    {
        "nom": "Claude Code",
        "chemin": Path.home() / ".claude" / "mcp.json",
        "cle_serveurs": "mcpServers",
    },
    {
        "nom": "Claude Desktop",
        "chemin": Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "cle_serveurs": "mcpServers",
    },
    {
        "nom": "Cursor",
        "chemin": Path.home() / ".cursor" / "mcp.json",
        "cle_serveurs": "mcpServers",
    },
    {
        "nom": "Gemini Code Assist",
        "chemin": Path.home() / ".gemini" / "settings.json",
        "cle_serveurs": "mcpServers",
    },
]


@dataclass
class ResultatClient:
    nom: str
    detecte: bool
    action: str  # "mis à jour" | "déjà présent" | "non détecté" | "erreur"
    erreur: str = ""


def configurer_clients() -> list[ResultatClient]:
    resultats = []
    for client in CLIENTS:
        chemin = client["chemin"]
        cle = client["cle_serveurs"]

        if not chemin.exists():
            resultats.append(ResultatClient(nom=client["nom"], detecte=False, action="non détecté"))
            continue

        try:
            contenu = json.loads(chemin.read_text(encoding="utf-8")) if chemin.stat().st_size > 0 else {}
        except (json.JSONDecodeError, OSError) as e:
            resultats.append(ResultatClient(nom=client["nom"], detecte=True, action="erreur", erreur=str(e)))
            continue

        if cle not in contenu:
            contenu[cle] = {}

        if "personal-memory" in contenu[cle]:
            resultats.append(ResultatClient(nom=client["nom"], detecte=True, action="déjà présent"))
            continue

        contenu[cle]["personal-memory"] = ENTREE_MCP
        try:
            chemin.write_text(json.dumps(contenu, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            resultats.append(ResultatClient(nom=client["nom"], detecte=True, action="mis à jour"))
        except OSError as e:
            resultats.append(ResultatClient(nom=client["nom"], detecte=True, action="erreur", erreur=str(e)))

    return resultats
