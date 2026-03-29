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

- [ ] **Journalisation `OSError` dans `_lire_jsonl`** — actuellement silencieux, difficile à diagnostiquer en cas de problème de permissions sur un fichier JSONL
- [ ] **Tests d'intégration MCP** — tester le serveur end-to-end (appels MCP réels, pas seulement les unités)
- [ ] **Publication PyPI** — `personal-memory-mcp` est déjà configuré dans `pyproject.toml`

---

## Moyen terme — Hors MVP (par priorité)

### Phase 6 — Import ChatGPT
- Documenter le format d'export ZIP ChatGPT (structure réelle à vérifier)
- Implémenter `ImporteurChatGPT` + lecteur dédié
- Ajouter dans `import_conversations` : `source="chatgpt"`
- Critère de validation : faits extraits depuis un export réel

### Phase 7 — `mmcp export`
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
