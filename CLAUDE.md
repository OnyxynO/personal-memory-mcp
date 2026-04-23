# personal-memory — CLAUDE.md

@../../GUIDELINES_PROJETS.md

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

## Avertissements MCP — consommation de tokens

- **`list_facts` sans filtre = réponse volumineuse** : ~70 tokens/fait, soit ~12k tokens pour 176 faits. Éviter en session normale — préférer `search(query)` qui retourne seulement les faits pertinents.
- **`import_conversations` en Mode B** : chaque page de 5 conversations peut générer 2k-5k tokens de contexte. Préférer un **modèle peu coûteux** (haiku) pour les imports en boucle — la qualité d'extraction est suffisante et le coût est 10× inférieur à sonnet/opus.
- **Règle générale** : pour tout appel MCP en boucle (pagination), utiliser haiku. Réserver sonnet/opus pour les recherches ponctuelles ou les décisions complexes sur les faits.

## Pièges connus (depuis expériences précédentes)

- sqlite-vec bindings : tester l'import sur macOS arm64 dès la phase 1
- INSERT OR IGNORE + lastInsertRowid : ne pas utiliser pour récupérer l'id existant
- Batching embeddings : toujours `input: [...]` en un seul appel Ollama
- qwen3 thinking tokens : filtrer les balises `<think>` avant de parser le JSON
- Export Claude : texte dans `content[].text`, pas dans `text` direct
- `PROMPT_EXTRACTION` contient des accolades JSON — doubler `{{` / `}}` si `.format()` est utilisé, sinon `KeyError` systématique
- `ServiceMock` dans les tests doit hériter de `MemoryService` (pas juste duck-type) pour satisfaire Pyright sur les annotations `"MemoryService"` dans les importeurs
- **haiku enveloppe JSON dans backticks** : `json.loads()` échoue silencieusement — stripper avant parsing : `re.sub(r"^```[a-z]*\n?", "", brut).rstrip("`").strip()`. Même famille que le filtre `<think>` de qwen3.

## Tests

```bash
uv run pytest               # 52 tests, ~0.25s, sans Ollama ni réseau
uv run pytest -v            # avec détail par test
```

- `tests/test_deduplication.py` — logique vectorielle, sqlite-vec en mémoire
- `tests/test_extraction.py` — filtrage `<think>`, batch embeddings, httpx mocké
- `tests/test_importeurs.py` — ImporteurClaudeCode + ImporteurClaude, ExtracteurMock
- `tests/test_lecteur.py` — parsing pur JSONL et ZIP, pagination, filtrage

## État MVP (avril 2026)

- ✅ Phase 1 — Serveur MCP (search, add, list, delete, import_source)
- ✅ Phase 2 — Import Claude Code (JSONL)
- ✅ Phase 3 — mmcp setup (détection clients, merge non-destructif)
- ✅ Phase 4 — Import Claude ZIP (memories.json + conversations.json)
- ✅ Phase 5 — Outil `import_conversations` (lecteur.py, parsing pur, pagination)
- ✅ Phase 6 — Import ChatGPT ZIP (ImporteurChatGPT, source="chatgpt")
- ✅ `mmcp backup` / `mmcp restore` — sauvegarde/restauration DB SQLite
- ✅ `mmcp migrate-embeddings` — migration entre modèles + dimensions dynamiques
- ✅ Pagination `list_facts` — `page` + `taille_page`, dict `{faits, page, total_pages, total}`
- ✅ Suite de tests automatisés (54 tests : 52 sans réseau + 2 intégration haiku)

## LSP

typescript-lsp non applicable. pyright-lsp ✅ actif globalement.

## Règles pour Claude Code et agents IA

Basées sur les patterns de `badlogic/pi-mono` et `theodo-group/debug-that`, adaptées au contexte Python/MCP:

### Git
- **Jamais** `git add -A` ni `git add .` — toujours spécifier les fichiers: `git add src/ tests/ CLAUDE.md`
- **Jamais** `git reset --hard`, `git checkout .`, `git stash`
- **Jamais** `git commit --amend` après `git push` — créer un nouveau commit à la place
- Détail important: `git add -i` ne fonctionne pas (CLI agent non-interactif)

### Fichiers
- **Toujours** lire complètement un fichier avec Read avant de l'éditer (même si on ne modifie qu'une ligne)
- **Jamais** `sed`, `cat`, `echo`, `awk` pour modifier des fichiers — utiliser l'outil Edit
- **Jamais** `grep` ou `find` via bash — utiliser Grep ou Glob
- Si un fichier n'existe pas et que c'est nécessaire, créer explicitement avec Write (pas de création implicite)

### Code Python
- **Zéro `any` type** — utiliser les unions et literals explicites (`str | None`, `"option1" | "option2"`)
- **Zéro imports dynamiques** dans les chemins chauds (`await import(...)`) — charger les modules au démarrage ou via lazy loading explicit
- **Zéro imports inutiles** — éliminer un import pour simplifier, même s'il reste du code non utilisé
- **Docstrings obligatoires** sur toute classe publique et méthode MCP (style reStructuredText ou Google style)

### Opérations async
- Toujours vérifier que les appels réseau (httpx, etc.) sont dans des fonctions testables
- Mock les dépendances externes (Ollama, fichiers ZIP) dans les tests
- Fournir un fichier fixture pour tout test d'importation

### Tests
- Lancer `uv run pytest` après chaque changement — les 41 tests doivent passer
- Ajouter des tests si vous créez une nouvelle méthode publique
- Test coverage n'est pas un objectif rigide, mais chercher à couvrir les chemins critiques

### MCP
- Tous les outils doivent retourner un dictionnaire sérialisable en JSON
- Les erreurs doivent être claires: `{"erreur": "message"}`, pas une exception
- Documentations des outils doivent être dans les docstrings `@mcp.tool()`

## Configuration MCP (Claude Code)

Le serveur MCP est enregistré en **scope `user`** dans `~/.claude.json` — disponible dans tous les projets.

```json
// ~/.claude.json → mcpServers
"personal-memory": {
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--project", "/Users/seb/Documents/Claude projet/personal-memory", "mmcp", "serve"]
}
```

Pour modifier le scope : `claude mcp remove personal-memory` puis `claude mcp add -s user personal-memory -- uv run --project "..." mmcp serve`

> Note : `mmcp setup` écrit dans `~/.claude/mcp.json` (fichier qui n'est pas lu par Claude Code CLI). La vraie config Claude Code est dans `~/.claude.json`.

## Commandes courantes

```bash
uv run mmcp serve          # Lance le serveur MCP
uv run mmcp import claude-code
uv run mmcp import claude ~/Downloads/export.zip
uv run mmcp import chatgpt ~/Downloads/export.zip
uv run mmcp backup         # Sauvegarde vers ~/.personal-memory/backups/
uv run mmcp restore        # Restaure depuis une sauvegarde
uv run mmcp migrate-embeddings --modele qwen3-embedding:0.6b
uv run pytest
```
