# personal-memory — CLAUDE.md

@../../PRINCIPES.md

## Contexte projet

Serveur MCP local qui extrait des faits mémorisables depuis les historiques
de conversations IA et les expose à tous les clients MCP compatibles.

- **CLI** : `mmcp`
- **Paquet PyPI** : `personal-memory-mcp` — **publié v0.1.3 le 2026-06-21** (https://pypi.org/project/personal-memory-mcp/0.1.3/)
- **Install end-user** : `pip install personal-memory-mcp`
- **Données** : `~/.personal-memory/`
- **Usage** : personnel, pas de multi-utilisateur

## Publication PyPI

Workflow de release (validé v0.1.1 → v0.1.3) :
- Bump `version` dans `pyproject.toml`, puis `uv lock` (met à jour la version du paquet dans `uv.lock`)
- Build natif : `rm -rf dist && uv build` (build backend `uv_build`)
- `uv tool run twine check dist/*`
- **Tester `pip install` du wheel dans un venv vierge AVANT upload** (`python3.13 -m venv /tmp/test && .../pip install dist/*.whl && .../mmcp status`). Le venv dev a déjà tout → une mauvaise déclaration de deps passe inaperçue.
- Upload : `TWINE_USERNAME=__token__ TWINE_PASSWORD=$(grep '^PYPI_TOKEN=' infra/.env | cut -d= -f2-) uv tool run twine upload dist/*`
- Tag `git tag -a vX.Y.Z` + `gh release create vX.Y.Z`
- **Token PyPI** : scopé projet dans `infra/.env` local (`PYPI_TOKEN`), gitignored. Le token global est dans `infra/pypi-tokens.md` (workspace racine).

Packaging (depuis v0.1.1) : LICENSE MIT à la racine + `license = "MIT"` + `license-files = ["LICENSE"]` (PEP 621) ; `[project.urls]` Homepage/Repository/Issues/Changelog ; `anthropic` **uniquement en dev-dep** (`[dependency-groups] dev`, tests d'intégration haiku) — pas dans le paquet publié.

### v0.1.3 (2026-06-21) — cohérence embeddings entre versions d'Ollama
- `nomic-embed-text` (modèle d'embedding **par défaut du code**) produit des vecteurs différents entre versions mineures d'Ollama (issue ollama/ollama#14449) → scores de similarité dégradés après upgrade
- Détection : clé config DB `version_ollama` écrite à la 1ʳᵉ vectorisation + à chaque `migrate-embeddings` ; `MemoryService.verifier_coherence_embeddings()` compare MAJEUR.MINEUR, n'avertit que pour `MODELES_EMBEDDING_INSTABLES` ; exposé via `mmcp status`, log au démarrage MCP, et le message donne `mmcp migrate-embeddings --modele <m>`
- `ExtracteurOllama.version()` (GET `/api/version`) + `version()` défaut `None` sur `ExtracteurBase`

## Stack

- Python 3.14 (dev) + uv — `requires-python >=3.13` conservé pour ne pas exclure les installs PyPI en 3.13
- MCP SDK officiel Anthropic (`mcp`)
- sqlite-vec (stockage vectoriel + FTS5)
- Ollama : `qwen3-embedding:0.6b` (embeddings, 1024 dims) + `qwen3:1.7b` (extraction faits)
- typer + rich (CLI)

## Specs

- `SPEC_FONCTIONNELLE.md` — comportements attendus, CLI, outils MCP, phases MVP
- `SPEC_TECHNIQUE.md` — architecture, modèle de données, formats d'import, interfaces

## Avertissements MCP — consommation de tokens

- **`list_facts` sans filtre = réponse volumineuse** : ~70 tokens/fait, soit ~12k tokens pour 176 faits. Éviter en session normale — préférer `search(query)` qui retourne seulement les faits pertinents.
- **`import_conversations` en Mode B** : chaque page de 5 conversations peut générer 2k-5k tokens de contexte. Préférer un **modèle peu coûteux** (haiku) pour les imports en boucle — la qualité d'extraction est suffisante et le coût est 10× inférieur à sonnet/opus.
- **Règle générale** : pour tout appel MCP en boucle (pagination), utiliser haiku. Réserver sonnet/opus pour les recherches ponctuelles ou les décisions complexes sur les faits.
- **Dépréciation MCP WebSocket (SDK `mcp` 1.28.0) — non concerné** : la v1.28.0 déprécie le transport WebSocket (suppression en v2). Vérifié le 2026-06-26 : ce projet utilise `mcp.run()` en **stdio**, aucun usage WebSocket → aucune migration requise. (Ne pas refaire la vérif.)
- **Piste — `headroom` (MCP, à évaluer)** : serveur MCP drop-in qui compresse les outputs d'outils/RAG de 60-95 % avant qu'ils atteignent le LLM (topic `claude-code`). Pertinent pour réduire le coût des contextes longs ici. À tester quand le volume de requêtes Anthropic devient non trivial. (Veille GitHub trending 2026-06-22)

## Pièges connus (depuis expériences précédentes)

- sqlite-vec bindings : tester l'import sur macOS arm64 dès la phase 1
- INSERT OR IGNORE + lastInsertRowid : ne pas utiliser pour récupérer l'id existant
- Batching embeddings : toujours `input: [...]` en un seul appel Ollama
- qwen3 thinking tokens : filtrer les balises `<think>` avant de parser le JSON
- Export Claude : texte dans `content[].text`, pas dans `text` direct
- `PROMPT_EXTRACTION` contient des accolades JSON — doubler `{{` / `}}` si `.format()` est utilisé, sinon `KeyError` systématique
- `ServiceMock` dans les tests doit hériter de `MemoryService` (pas juste duck-type) pour satisfaire Pyright sur les annotations `"MemoryService"` dans les importeurs
- **haiku enveloppe JSON dans backticks** : `json.loads()` échoue silencieusement — stripper avant parsing : `re.sub(r"^```[a-z]*\n?", "", brut).rstrip("`").strip()`. Même famille que le filtre `<think>` de qwen3.
- **sqlite-vec distance L2 vs cosinus** : par défaut `vec0` utilise la distance L2. Pour des vecteurs normalisés, `1 - L2_distance` ≠ cosine_sim (score erroné ~0.03 au lieu de ~0.53). Toujours créer la table avec `distance_metric=cosine` : `CREATE VIRTUAL TABLE faits_vec USING vec0(embedding FLOAT[dim] distance_metric=cosine)`. Avec ce mode, `distance = 1 - cosine_sim` donc `score = 1 - distance` est correct.
- **sqlite-vec : KNN `MATCH`/`k` filtre APRÈS le KNN et plafonne `k` à 4096** : une requête `WHERE embedding MATCH ? AND k = ? AND projet = ?` fait le KNN sur **toute** la base (k voisins globaux) *puis* applique le filtre `projet`. Les bons voisins d'un projet **minoritaire** sont évincés par des voisins globaux hors filtre → la recherche scopée retombe sur le fallback FTS (scores ~0.05) alors que le vrai cosinus est ~0.6. Gonfler `k` ne marche pas (plafond 4096). Remède : quand un filtre est actif, calculer la distance **scalaire** `vec_distance_cosine(embedding, ?)` sur le sous-ensemble filtré (`ORDER BY distance LIMIT top_k`) — exact, sans limite de k. Le KNN `MATCH` reste utilisé sans filtre. Cf. `Storage.rechercher`.
- **embeddings instables entre versions d'Ollama** : `nomic-embed-text` produit des vecteurs différents d'une version mineure d'Ollama à l'autre (issue ollama/ollama#14449). Une base vectorisée avec une version puis interrogée après upgrade renvoie des scores dégradés. Détecté depuis v0.1.3 (cf. `verifier_coherence_embeddings`). Remède : re-vectoriser avec `mmcp migrate-embeddings --modele <m>`.
- **défaut code ≠ défaut doc pour l'embedding** : `MemoryService`/`ExtracteurOllama` ont pour défaut `nomic-embed-text` (768d), alors que le CLAUDE.md/CLI `migrate-embeddings` poussent `qwen3-embedding:0.6b` (1024d). Volontaire — le modèle effectif est lu depuis la config DB (`modele_embeddings`), le défaut ne s'applique qu'aux nouvelles bases jamais configurées. Ne pas « corriger » le défaut sans script de migration : casserait les bases nomic existantes (dimension différente).
- **mock extracteur dans les tests** : faire hériter le faux extracteur de `ExtracteurOllama` (dont le `__init__` ne fait aucun réseau), pas un duck-type, sinon Pyright rejette l'affectation à `MemoryService._extracteur`. Même famille que le piège `ServiceMock` ci-dessus.
- **`markdown_tree._redecouper` — redécouper au-delà des paragraphes** : le redécoupage des sections longues doit descendre sous la frontière `\n\n`. Une section sans double saut de ligne (grande liste à puces, tableau : items séparés par des `\n` simples) restait en un seul bloc surdimensionné, envoyé entier au modèle d'embedding → **HTTP 400** sur `/api/embed` (rejet silencieux d'un chunk, découvert au 1ᵉʳ reindex réel du workspace, 2026-08-01). Remède : découpage hiérarchique ligne → tranches dures pour une ligne unique géante, chaque fragment garanti `<= max_chars`. Tests de régression : `test_decouper_liste_a_puces_dense_respecte_max_chars` + `test_decouper_ligne_unique_geante_respecte_max_chars`. Toujours **asserter `len(chunk) <= max_chars`**, pas seulement « ça découpe en plusieurs ».
- **`importer_faits` n'est pas transactionnel entre lots, et la reprise après échec partiel est piégeuse.** L'insertion se fait lot par lot (32 faits, principe #8) sans transaction englobante : si Ollama meurt à la 20ᵉ minute d'un restore de snapshot (des milliers de faits, ~30 min de ré-embarquement), la base garde un import **partiel** — ni vide, ni complète. Le pré-check Ollama (avant de lancer l'import) ne couvre que l'instant initial, pas toute la durée. Piège de reprise : `mmcp import facts` **refuse** une base non vide (garde-fou volontaire, cf. `import.ts` côté atelier) ; relancer avec `--force` réinjecte l'intégralité du fichier `facts.json` **sans déduplication** (le restore cible une base neuve par design, cf. docstring `importer_faits`) → doublons pour tout ce qui avait déjà été inséré avant l'échec. Procédure de reprise recommandée : `mmcp backup` **avant** toute restauration (rend l'échec partiel réversible) ; en cas d'échec en cours de route, purger la base restaurée (repartir d'une base neuve, ou `mmcp restore` vers l'état pré-restauration) puis relancer l'import complet plutôt que de forcer par-dessus le partiel. Documentation seulement ici — pas de refonte transactionnelle tentée (portée hors du correctif qui a introduit cette note). La CLI matérialise désormais cette note : avertissement « lancez `mmcp backup` » avant de démarrer, et en cas d'échec en cours de route un message qui donne le nombre de faits déjà insérés + la procédure de reprise (jamais `--force` par-dessus le partiel), au lieu d'un traceback.
- **Le snapshot portable transporte son modèle d'embedding — ne jamais revenir à une liste nue.** `mmcp export --complet` produit une **enveloppe** `{version_format, modele_embeddings, dim_embeddings, date_export, faits[]}` (contrat inter-projets, lu par `atelier`). Raison : le snapshot ne transporte pas les vecteurs mais le texte ; sans l'en-tête, un restore sur base neuve instancie `MemoryService()` sans argument, la table `config` est vide → repli sur le **défaut du code** `nomic-embed-text` (768d) au lieu du `qwen3-embedding:0.6b` (1024d) de la base source, et `_assurer_vecteurs_init` **fige** la base cible sur 768d sans broncher. Découvert au bout de ~30 min de ré-embarquement, avec `migrate-embeddings` (second cycle complet) comme seule sortie. Au restore : `charger_faits_json` accepte l'enveloppe **et** la liste nue héritée (rétrocompat), rejette une `version_format` inconnue, et `_reconcilier_modele_embeddings` écrit la config `modele_embeddings` **avant la première insertion** sur base vierge (sinon avertissement rouge + confirmation).
- **Valider un fichier de faits champ par champ, jamais seulement « liste de dicts ».** Les plantages de typage surviennent *après* des insertions (opération non transactionnelle) : `categorie` non-`str` → `TypeError` dans `unicodedata.normalize` ; `projet`/`source_detail` conteneur → `sqlite3.InterfaceError` au bind ; `score_importance` textuel → stocké tel quel dans une colonne `REAL` (typage dynamique SQLite, corruption silencieuse) ; `date_creation` arbitraire → tri chronologique et `mmcp clean` faussés (`r[3][:10]`). `_valider_faits` refuse tout le fichier en amont en citant l'index de l'élément et le champ fautif.
- **`dim_embeddings` doit être confronté à la dimension réellement produite, pas seulement transporté.** Le nom du modèle ne suffit pas : un modèle *portant le même nom* peut changer de dimension d'une version d'Ollama à l'autre (#14449) — c'est précisément le scénario que le snapshot est censé rattraper. `_verifier_dimension_embeddings` calcule un embedding de **sonde** (un texte court) juste après le pré-check Ollama et **avant** la boucle d'insertion, et exige une confirmation en cas de divergence. Faire la sonde après la première insertion ne servirait à rien : `_assurer_vecteurs_init` a déjà figé la table sur la mauvaise dimension.
- **ISO 8601 : `datetime.fromisoformat` accepte la forme *basique* — la refuser explicitement.** `"20260807"` passe le parsing mais casse tout le reste : `mmcp clean` tronque à 10 caractères (`r[3][:10]`) et compare `date_derniere_utilisation < seuil` **en tant que chaînes**, or `'-'` (0x2D) < `'0'` (0x30) — à année égale, basique et étendu ne s'ordonnent pas pareil. `_valider_date_iso` exige la forme étendue (longueur ≥ 10, `-` en positions 4 et 7), seule produite par `datetime.isoformat()`.
- **Chemin d'échec = zéro mutation d'état.** Au restore, le modèle de l'archive est adopté **en mémoire seulement** (`_extracteur._modele_embeddings`) — c'est lui que le pré-check Ollama et le contrôle de dimension éprouvent —, et la config DB `modele_embeddings` n'est écrite qu'une fois toutes les vérifications passées, juste avant la boucle d'insertion. Écrire la config avant laisserait une base marquée d'un modèle jamais utilisé sur une sortie en exit 1.
- **Le snapshot ne conserve que les champs du contrat — le dire, et le signaler.** `importer_faits` ne relit que les clés listées en §10 de `SPEC_TECHNIQUE.md` : toute autre clé disparaît au ré-export. Sur un contrat inter-projets (`atelier`), un producteur plus récent verrait ses données s'évaporer en silence. `charger_faits_json` recense les clés hors contrat (`SnapshotFaits.cles_inattendues`) et la CLI avertit en jaune, sans bloquer — un champ en plus n'est pas une corruption. Un vrai ajout de champ = incrément de `version_format`.
- **`zip(lot, vecteurs)` sur une réponse d'embedding : toujours vérifier les longueurs.** Si Ollama renvoie moins de vecteurs que de textes, `zip` tronque en silence pendant que le compteur additionne `len(lot)` → des faits disparaissent et le rapport ment. `importer_faits` et `migrer_embeddings` lèvent maintenant une `ValueError` explicite + `strict=True`.

## Tests

```bash
uv run pytest               # 98 tests (96 sans réseau + 2 haiku skippés), ~11s
uv run pytest -v            # avec détail par test
```

- `tests/test_deduplication.py` — logique vectorielle, sqlite-vec en mémoire
- `tests/test_extraction.py` — filtrage `<think>`, batch embeddings, httpx mocké
- `tests/test_importeurs.py` — ImporteurClaudeCode + ImporteurClaude, ExtracteurMock
- `tests/test_lecteur.py` — parsing pur JSONL et ZIP, pagination, filtrage
- `tests/test_ui_serveur.py` — 15 tests HTTP serveur UI (GET, DELETE, routes)
- `tests/test_ui_navigation.py` — 6 tests Playwright browser (skippés sans playwright)
- `tests/test_integration_mcp.py` — 15 tests MCP (add, search, delete, list_facts) + 2 haiku (skippés sans `ANTHROPIC_API_KEY`)
- `tests/test_coherence_embeddings.py` — 14 tests : `version()`, `_version_mineure`, détection d'incohérence Ollama, enregistrement de version (httpx mocké)
- `tests/test_storage_export.py` — `exporter_faits()` : fidélité des champs, pas de plafond, faits supprimés exclus
- `tests/test_import_facts.py` — `importer_faits()` (batching, dates, lot incomplet) + lecture/validation du fichier de snapshot
- `tests/test_cli_import_facts.py` — garde-fous CLI du snapshot (base non vide, Ollama, modèle divergent, `--complet` + csv/catégorie) et round-trip export → import

## État (juin 2026) — v0.1.3 publiée

**Projet livré.** Dernière version PyPI **v0.1.3 (2026-06-21)** ; publication initiale v0.1.0 le 2026-05-16. `pip install personal-memory-mcp`
- GitHub : https://github.com/OnyxynO/personal-memory-mcp/releases/tag/v0.1.0
- PyPI : https://pypi.org/project/personal-memory-mcp/
- Token PyPI : `infra/.env` (ignoré git)

- ✅ Phase 1 — Serveur MCP (search, add, list, delete, import_source)
- ✅ Phase 2 — Import Claude Code (JSONL)
- ✅ Phase 3 — mmcp setup (détection clients, merge non-destructif)
- ✅ Phase 4 — Import Claude ZIP (memories.json + conversations.json)
- ✅ Phase 5 — Outil `import_conversations` (lecteur.py, parsing pur, pagination)
- ✅ Phase 6 — Import ChatGPT ZIP (ImporteurChatGPT, source="chatgpt")
- ✅ Phase 7 — `mmcp ui` (interface web locale, HTML/JS vanilla, zéro dépendance)
- ✅ Phase 8 — `mmcp export` (JSON/CSV, filtre catégorie, sortie fichier)
- ✅ Phase 9 — `score_importance` + FTS5 hybride (recherche vectorielle + BM25 fallback)
- ✅ `mmcp backup` / `mmcp restore` — sauvegarde/restauration DB SQLite
- ✅ `mmcp migrate-embeddings` — migration entre modèles + dimensions dynamiques
- ✅ Modèle embedding : `qwen3-embedding:0.6b` (1024 dims) + `distance_metric=cosine`
- ✅ Suite de tests : 98 tests (96 sans réseau + 2 intégration haiku)

### Évolutions post-v0.1.0
- ✅ v0.1.1 (2026-06-12) — audit packaging (LICENSE, urls, `anthropic` en dev-dep)
- ✅ v0.1.2 (2026-06-12) — hotfix sécurité MCTS (`delete(id, confirm_id)`, `_valider_chemin_local`)
- ✅ v0.1.3 (2026-06-21) — détection d'incohérence des embeddings entre versions d'Ollama (#14449)
- ✅ Audit MCTS acté (2026-06-21) : findings critiques = faux positifs structurels déjà mitigés, baseline `mcts_baseline.json` commité
- ✅ Publication PyPI v0.1.0 + GitHub Release + tag git
- ✅ Indexation « arbre Markdown » + scoping par projet (2026-07-31, Phase 1 de l'Atelier ouroboros, non publié) :
  - colonne `projet` sur `faits` (migration idempotente) + filtre `projet` sur `search` (CLI/MCP)
  - `add(projet, source_detail, dedup)` ; `MemoryService.purger_source` + `Storage.purger_source` (hard delete + rebuild FTS)
  - `ImporteurMarkdownTree` (`importeurs/markdown_tree.py`) : **chunk-and-embed** générique (découpe par section, exclusions paramétrables), **sans extraction LLM** — pour du contenu déjà distillé
  - **Purge idempotente scopée au périmètre** : avant ingestion, l'importeur purge la source `workspace` **pour chaque projet qu'il va (ré)écrire** — réindexer un arbre remplace les projets qu'il couvre sans toucher aux autres, et sans jamais empiler de doublons quel que soit le nombre de relances (le mode `--sans-purge` qui permettait l'accumulation a été retiré). Repli sûr : un fichier sans projet (aucun `--projet-defaut`) → purge totale de la source.
  - **Verrou anti-parallélisme** (`importeurs/verrou.py`) : un seul `import markdown-tree` à la fois (verrou `flock` sur `~/.personal-memory/import.lock`). Un 2ᵉ import concurrent est **refusé immédiatement** (`ImportDejaEnCours`), pas mis en file. Protège des deux protections complémentaires à l'idempotence : contention SQLite (« database is locked »), doublons de course (purge+insert entrelacés), empilement RAM (N process Python). Refus plutôt qu'attente = pas d'empilement. Inter-process car le risque vient de plusieurs `mmcp import`, pas d'un singleton mémoire.
  - CLI `mmcp import markdown-tree <racine>` (options `--inclure-refs`, `--projet-base`, `--projet-defaut`)
  - **Dérivation projet en profondeur 1** sous une base (`projets/<x>` → `<x>` ; familles au niveau famille) — la granularité sous-projet (aligner sur le registry) reste un raffinement à venir
- ✅ Filtre `--source` sur `search` (2026-08-02, dette §10 de l'Atelier) : `search(..., source=None)` traverse CLI/MCP → `Storage.rechercher`/`rechercher_fts` (même patron que `--projet`, inclus dans le chemin scalaire `vec_distance_cosine`). Permet de scoper une recherche au **corpus curé** (`--source workspace`) sans se faire noyer par les facts d'étude de code ou d'import de conversation qui partagent la DB. Consommé par `atelier role` (briefing scopé). Tests : `tests/test_storage_source.py`.

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
- Lancer `uv run pytest` après chaque changement — les 98 tests doivent passer
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
  "args": ["run", "--project", "/Users/seb/Documents/Claude projet/projets/personal-memory", "mmcp", "serve"]
}
```

Pour modifier le scope : `claude mcp remove personal-memory` puis `claude mcp add -s user personal-memory -- uv run --project "..." mmcp serve`

> Note : `mmcp setup` écrit dans `~/.claude/mcp.json` (fichier qui n'est pas lu par Claude Code CLI). La vraie config Claude Code est dans `~/.claude.json`.

## Commandes courantes

```bash
uv run mmcp serve          # Lance le serveur MCP
uv run mmcp ui             # Interface web locale http://localhost:8766
uv run mmcp import claude-code
uv run mmcp import claude ~/Downloads/export.zip
uv run mmcp import chatgpt ~/Downloads/export.zip
uv run mmcp import markdown-tree "/chemin/workspace"  # indexe un arbre .md (chunk-and-embed, sans LLM)
uv run mmcp search "requête" --projet sand       # recherche scopée par projet
uv run mmcp search "requête" --source workspace  # recherche scopée au corpus curé (arbre markdown)
uv run mmcp export                               # JSON vers stdout
uv run mmcp export --format csv --sortie faits.csv
uv run mmcp export --format json --categorie stack
uv run mmcp export --complet --format json --sortie faits.json  # snapshot portable : enveloppe + tous champs (dont projet)
uv run mmcp import facts faits.json                             # restore : ré-embarque tous les faits (Ollama requis)
uv run mmcp backup         # Sauvegarde vers ~/.personal-memory/backups/
uv run mmcp restore        # Restaure depuis une sauvegarde
uv run mmcp migrate-embeddings --modele qwen3-embedding:0.6b
uv run pytest
```
