# personal-memory-mcp

[![PyPI](https://img.shields.io/pypi/v/personal-memory-mcp.svg)](https://pypi.org/project/personal-memory-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/personal-memory-mcp.svg)](https://pypi.org/project/personal-memory-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Serveur MCP local qui extrait des faits mémorisables depuis vos historiques de conversations IA et les expose à tous les clients MCP compatibles (Claude Code, Claude Desktop, Cursor…).

## Fonctionnement

```
Historiques IA → extraction LLM → SQLite + sqlite-vec → outils MCP
```

Les faits sont stockés localement dans `~/.personal-memory/memory.db`. Aucun cloud, aucune API externe — tout tourne sur votre machine via Ollama.

## Installation

```bash
pip install personal-memory-mcp
```

Prérequis : Python 3.13+, [Ollama](https://ollama.com) avec les modèles :

```bash
ollama pull qwen3:1.7b
ollama pull qwen3-embedding:0.6b
```

## Démarrage rapide

```bash
# Lancer le serveur MCP
mmcp serve

# Configurer automatiquement les clients MCP détectés
mmcp setup

# Importer vos sessions Claude Code
mmcp import claude-code

# Importer un export Claude (ZIP)
mmcp import claude ~/Downloads/export.zip

# Importer un export ChatGPT (ZIP)
mmcp import chatgpt ~/Downloads/export.zip

# Interface web locale
mmcp ui
```

## Outils MCP

| Outil | Description |
|---|---|
| `search(query)` | Recherche hybride (vectorielle + BM25) |
| `add(contenu, categorie)` | Ajoute un fait avec déduplication automatique |
| `list_facts(page, categorie)` | Liste paginée des faits |
| `delete(id)` | Supprime un fait |
| `import_source(type, chemin)` | Déclenche un import |
| `import_conversations(source, page)` | Expose les conversations brutes pour analyse par l'IA |

## Configuration Claude Code

```bash
claude mcp add -s user personal-memory -- mmcp serve
```

## Commandes CLI

```bash
mmcp serve                    # Lance le serveur MCP
mmcp ui                       # Interface web http://localhost:8766
mmcp import claude-code       # Import sessions Claude Code
mmcp import claude <zip>      # Import export Claude
mmcp import chatgpt <zip>     # Import export ChatGPT
mmcp export                   # Export JSON/CSV
mmcp backup                   # Sauvegarde SQLite
mmcp restore                  # Restauration
mmcp migrate-embeddings       # Migration de modèle d'embedding
mmcp status                   # État du serveur et de la base
```

## Stack technique

- Python 3.13 + [uv](https://github.com/astral-sh/uv)
- [MCP SDK Anthropic](https://github.com/anthropics/python-sdk)
- SQLite + [sqlite-vec](https://github.com/asg017/sqlite-vec) (stockage vectoriel + FTS5)
- Ollama : `qwen3-embedding:0.6b` (embeddings) + `qwen3:1.7b` (extraction)
- Recherche hybride : cosinus vectoriel + BM25 fallback

## Licence

MIT
