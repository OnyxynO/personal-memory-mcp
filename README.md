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

## Cohérence des embeddings et mises à jour d'Ollama

Les modèles d'embedding servis par Ollama ne produisent pas toujours les mêmes
vecteurs d'une version à l'autre. C'est documenté pour `nomic-embed-text` (le
modèle d'embedding par défaut) : les vecteurs varient entre versions mineures
d'Ollama (cf. [ollama/ollama#14449](https://github.com/ollama/ollama/issues/14449)).

**Symptôme** : après une mise à jour d'Ollama, les recherches deviennent moins
pertinentes (scores de similarité dégradés). En effet, votre base a été
vectorisée avec l'ancienne version, mais les nouvelles requêtes sont encodées
avec la nouvelle — les deux ne sont plus comparables.

**Détection automatique** : la version d'Ollama utilisée lors de la vectorisation
est mémorisée dans la base. Si elle change (au niveau `MAJEUR.MINEUR`),
`mmcp status` le signale (`coherence_embeddings`) et le serveur MCP émet un
avertissement au démarrage.

**Remède** : re-vectoriser toute la base avec la version courante d'Ollama
(une sauvegarde automatique est créée avant l'opération). Passez le modèle
d'embedding courant pour re-vectoriser à l'identique :

```bash
mmcp migrate-embeddings --modele nomic-embed-text   # re-vectorise à l'identique
mmcp status                                          # l'avertissement doit disparaître
```

> Sans l'option `--modele`, `mmcp migrate-embeddings` migre vers
> `qwen3-embedding:0.6b` (changement de modèle **et** de dimension). C'est aussi
> une façon valable de repartir sur une base cohérente si vous préférez ce modèle.

## Stack technique

- Python 3.14 (dev, compatible 3.13+) + [uv](https://github.com/astral-sh/uv)
- [MCP SDK Anthropic](https://github.com/anthropics/python-sdk)
- SQLite + [sqlite-vec](https://github.com/asg017/sqlite-vec) (stockage vectoriel + FTS5)
- Ollama : `qwen3-embedding:0.6b` (embeddings) + `qwen3:1.7b` (extraction)
- Recherche hybride : cosinus vectoriel + BM25 fallback

## Licence

MIT
