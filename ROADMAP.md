# PersonalMemoryMCP — Roadmap

> Dernière mise à jour : mars 2026

---

## État actuel — MVP complet (phases 1-5)

- ✅ Serveur MCP : 6 outils (search, add, list_facts, delete, import_source, import_conversations)
- ✅ CLI `mmcp` : serve, setup, import, search, list, status, clean
- ✅ Import Claude Code (JSONL) via LLM + via lecture brute paginée
- ✅ Import Claude ZIP (memories.json + conversations.json)
- ✅ 41 tests automatisés (~0.09s, sans Ollama ni réseau)
- ✅ Docs : ARCHITECTURE.html, specs v0.2, pyrightconfig.json

---

## Prochaine étape immédiate — Validation en conditions réelles

Avant d'ajouter des features, utiliser ce qui existe sur de vraies données :

- [ ] Lancer `mmcp serve` et appeler `import_conversations` depuis Claude Code
- [ ] Parcourir les pages de sessions réelles, appeler `add()` pour les faits retenus
- [ ] Vérifier `mmcp list` et `mmcp search` sur la base résultante
- [ ] Identifier les frictions UX (messages trop longs ? pagination trop petite ? catégories manquantes ?)

---

## Court terme — Améliorations techniques

- [ ] **Pagination `list_facts`** — sans filtre, retourne tous les faits en une réponse (~12k tokens pour 176 faits), ce qui sature le contexte MCP rapidement. Ajouter `page` + `taille_page` comme dans `import_conversations`.
- [ ] **Journalisation `OSError` dans `_lire_jsonl`** — actuellement silencieux, difficile à diagnostiquer en cas de problème de permissions sur un fichier JSONL
- [ ] **Tests d'intégration MCP** — tester le serveur end-to-end (appels MCP réels, pas seulement les unités)
- [ ] **Publication PyPI** — `personal-memory-mcp` est déjà configuré dans `pyproject.toml`

---

## Moyen terme — Hors MVP (par priorité)

### ✅ Phase 6 — Import ChatGPT (mars 2026)
- Format ZIP OpenAI documenté et supporté (`conversations.json`)
- `ImporteurChatGPT` implémenté + `source="chatgpt"` dans `import_conversations`
- Validé en conditions réelles : 36 conversations → 39 faits extraits via Mode B (agent haiku)

### Phase 7 — `mmcp ui` (interface web locale)

Commande CLI qui lance un mini-serveur HTTP indépendant du serveur MCP :

```bash
mmcp ui            # ouvre http://localhost:8766 automatiquement
mmcp ui --port 9000
```

**Architecture :**
- Lit directement `~/.personal-memory/memory.db` via SQLite (pas besoin que `mmcp serve` tourne)
- Serveur HTTP Python pur (`http.server` ou `uvicorn` minimal) — pas de FastAPI
- Frontend : HTML + JS vanilla (zéro npm, zéro build, zéro dépendance externe)
- Module séparé : `src/personal_memory_mcp/ui/` — indépendant du serveur MCP

**Fonctionnalités MVP :**
- Liste paginée des faits (25/page)
- Filtre par catégorie (badges cliquables)
- Recherche texte (filtre côté client sur les faits chargés)
- Compteur par catégorie dans l'en-tête
- Bouton supprimer un fait (avec confirmation)

**Hors scope :**
- Édition de faits (lecture seule dans un premier temps)
- Authentification (local uniquement)
- Ajout manuel de faits depuis l'UI

**Critère de validation :** `mmcp ui` ouvre le navigateur, affiche les 176 faits, filtre et suppression fonctionnels.

### Phase 8 — `mmcp export`
- Backup des faits en JSON ou CSV
- Usage : migration, sauvegarde avant `mmcp clean`, partage entre machines
- Commande : `mmcp export [--format json|csv] [--categorie X]`

### Phase 8 — Support Obsidian / Markdown
- Import de notes Markdown structurées comme source de faits
- Format : fichiers `.md` avec frontmatter YAML ou sections `##`
- Usage : notes personnelles, journal de bord, wiki local

---

## Long terme — Idées non planifiées

- UI web FastAPI légère (visualisation + édition de la base)
- Synchronisation entre machines (export/import manuel ou via fichier partagé)
- Import Cursor / Windsurf (si formats accessibles)
- Catégories personnalisables (au-delà des 7 prédéfinies)

---

## Ce qui ne sera PAS fait

- Multi-utilisateur (usage personnel uniquement)
- Cloud / service distant (contrainte non négociable)
- Cron / hooks automatiques (import manuel uniquement)
- Import via abonnements conversationnels (Claude Pro, ChatGPT Plus) — pas d'accès programmatique officiel stable (cf. expériences Clem/UnifiedMemory)
