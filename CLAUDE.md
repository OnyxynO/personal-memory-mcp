# personal-memory — CLAUDE.md

@../GUIDELINES_PROJETS.md

## Contexte projet

Serveur MCP local qui extrait des faits mémorisables depuis les historiques
de conversations IA et les expose à tous les clients MCP compatibles.

- **CLI** : `mmcp`
- **Paquet PyPI** : `personal-memory-mcp`
- **Données** : `~/.personal-memory/`
- **Usage** : personnel, pas de multi-utilisateur

## Stack

- Python 3.13 + uv
- MCP SDK officiel Anthropic (`mcp`)
- sqlite-vec (stockage vectoriel)
- Ollama : `nomic-embed-text` (embeddings) + `qwen3:1.7b` (extraction faits)
- typer + rich (CLI)

## Specs

- `SPEC_FONCTIONNELLE.md` — comportements attendus, CLI, outils MCP, phases MVP
- `SPEC_TECHNIQUE.md` — architecture, modèle de données, formats d'import, interfaces

## Pièges connus (depuis expériences précédentes)

- sqlite-vec bindings : tester l'import sur macOS arm64 dès la phase 1
- INSERT OR IGNORE + lastInsertRowid : ne pas utiliser pour récupérer l'id existant
- Batching embeddings : toujours `input: [...]` en un seul appel Ollama
- qwen3 thinking tokens : filtrer les balises `<think>` avant de parser le JSON
- Export Claude : texte dans `content[].text`, pas dans `text` direct

## LSP

typescript-lsp non applicable. pyright-lsp ✅ actif globalement.

## Commandes courantes

```bash
uv run mmcp serve          # Lance le serveur MCP
uv run mmcp import claude-code
uv run pytest
```
