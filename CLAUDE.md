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
- `PROMPT_EXTRACTION` contient des accolades JSON — doubler `{{` / `}}` si `.format()` est utilisé, sinon `KeyError` systématique
- `ServiceMock` dans les tests doit hériter de `MemoryService` (pas juste duck-type) pour satisfaire Pyright sur les annotations `"MemoryService"` dans les importeurs

## Tests

```bash
uv run pytest               # 20 tests, ~0.09s, sans Ollama ni réseau
uv run pytest -v            # avec détail par test
```

- `tests/test_deduplication.py` — logique vectorielle, sqlite-vec en mémoire
- `tests/test_extraction.py` — filtrage `<think>`, batch embeddings, httpx mocké
- `tests/test_importeurs.py` — ImporteurClaudeCode + ImporteurClaude, ExtracteurMock

## État MVP (mars 2026)

- ✅ Phase 1 — Serveur MCP (search, add, list, delete, import_source)
- ✅ Phase 2 — Import Claude Code (JSONL)
- ✅ Phase 3 — mmcp setup (détection clients, merge non-destructif)
- ✅ Phase 4 — Import Claude ZIP (memories.json + conversations.json)
- ✅ Suite de tests automatisés (20 tests)

## LSP

typescript-lsp non applicable. pyright-lsp ✅ actif globalement.

## Commandes courantes

```bash
uv run mmcp serve          # Lance le serveur MCP
uv run mmcp import claude-code
uv run mmcp import claude ~/Downloads/export.zip
uv run pytest
```
