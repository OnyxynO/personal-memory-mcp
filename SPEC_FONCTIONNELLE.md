# PersonalMemoryMCP — Spécifications fonctionnelles

> Version : 0.2 — mars 2026

---

## 1. Problème résolu

Chaque session IA repart de zéro. Les faits partagés avec Claude hier,
Cursor ne les connaît pas aujourd'hui. PersonalMemoryMCP centralise une
mémoire personnelle locale depuis les historiques existants et l'injecte
automatiquement dans tous les clients MCP compatibles.

**Contraintes non négociables :**
- 100% local — zéro cloud, zéro service externe sauf Ollama
- Usage personnel — un seul utilisateur, zéro gestion d'équipe
- Import manuel uniquement — aucun cron, aucun hook automatique

---

## 2. CLI `mmcp` — Comportement attendu

### `mmcp setup`

Détecte les clients MCP installés sur la machine et met à jour leurs
fichiers de configuration pour enregistrer le serveur `personal-memory`.

```
$ mmcp setup

Clients MCP détectés :
  ✓ Claude Code       ~/.claude/mcp.json
  ✓ Claude Desktop    ~/Library/Application Support/Claude/claude_desktop_config.json
  ✗ Cursor            (non détecté)

Mise à jour des configs...
  ✓ Claude Code       — mis à jour
  ✓ Claude Desktop    — mis à jour

Redémarrer les clients pour activer personal-memory.
```

Comportement :
- Merge non-destructif : ne supprime rien d'existant dans les configs
- Si le serveur est déjà configuré dans un client : afficher "déjà présent", ne rien modifier
- Ne patch que les clients détectés (fichier de config existant)

### `mmcp serve`

Lance le serveur MCP en mode stdio. Utilisé par les clients, pas lancé
manuellement en temps normal.

```
$ mmcp serve
# (rien affiché — stdin/stdout réservés au protocole MCP)
```

### `mmcp import claude-code`

Scanne `~/.claude/projects/**/*.jsonl`, extrait les faits via Ollama,
stocke dans `~/.personal-memory/memory.db`.

```
$ mmcp import claude-code

Scan ~/.claude/projects/ ... 34 sessions trouvées

Extraction des faits [=============>    ] 28/34 sessions
  + 47 nouveaux faits
  = 12 dédupliqués
  ~ 3 mis à jour
  ✓ Terminé en 42s
```

Comportement :
- Re-scan complet à chaque appel (pas d'historique des fichiers déjà traités)
- La déduplication vectorielle évite les doublons entre imports successifs

### `mmcp import claude <chemin.zip>`

Importe depuis un export officiel Claude (fichier ZIP).

```
$ mmcp import claude ~/Downloads/data-2026-03-29-14-16-24-batch-0000.zip

Lecture de l'export Claude...
  memories.json    → 1 mémoire synthétisée trouvée
  conversations.json → 290 conversations

Import mémoire synthétisée...
  + 31 faits extraits (sans LLM)

Extraction depuis conversations [=====>    ] 87/290
  + 23 nouveaux faits
  = 45 dédupliqués
  ✓ Terminé en 3m12s
```

Comportement :
- ZIP dézippé en mémoire (rien écrit sur disque)
- `memories.json` traité en premier (extraction directe, pas de LLM)
- `conversations.json` traité ensuite (extraction LLM par conversation)

### `mmcp search <query>`

Recherche dans la mémoire — outil de test CLI.

```
$ mmcp search "préférences python"

3 résultats (similarité > 0.70) :

  [stack]       Préfère Python pour les scripts de traitement de données     0.94
  [stack]       Utilise sentence-transformers plutôt que OpenAI embeddings   0.87
  [projet]      Travaille sur DataMatch (Python + FastAPI + PyWebView)        0.81
```

### `mmcp list`

Liste les faits stockés.

```
$ mmcp list
$ mmcp list --categorie stack
$ mmcp list --categorie projet --limite 20

142 faits (actifs)

  #  Catégorie    Contenu                                                    Source         Date
  1  contexte     Développeur Python avec 10+ ans d'expérience               claude         2026-03-29
  2  stack        Préfère uv comme gestionnaire de paquets Python             claude-code    2026-03-28
  ...
```

### `mmcp status`

Vue d'ensemble de l'état du système.

```
$ mmcp status

Base : ~/.personal-memory/memory.db  (2.3 MB)
Faits : 142 actifs, 0 expirés

Par catégorie :
  contexte      34
  stack         28
  projet        25
  preference    21
  decision      18
  contrainte    10
  autre          6

Ollama :
  ✓ nomic-embed-text   disponible
  ✓ qwen3:1.7b         disponible

Dernier import : claude-code — 2026-03-29 à 14h32 (+12 faits)
```

### `mmcp clean`

Supprime les faits expirés (> 12 mois sans utilisation).

```
$ mmcp clean

Faits expirés (dernière utilisation > 12 mois) :
  #12  [stack]    Utilisait Webpack avant de passer à Vite   (jamais utilisé, créé 2025-01-12)
  #34  [projet]   Projet X archivé                           (jamais utilisé, créé 2025-02-03)

Supprimer ces 2 faits ? [o/N] o
  ✓ 2 faits supprimés
```

---

## 3. Outils MCP — Comportement attendu

Les clients MCP (Claude Code, Claude Desktop, Cursor...) accèdent aux
outils suivants via le protocole standard.

### `search` — Recherche sémantique

Le client formule une question en langage naturel. Le serveur retourne
les faits les plus proches par similarité vectorielle.

Chaque appel met à jour `date_derniere_utilisation` des faits retournés
(utilisé pour l'expiration).

### `add` — Ajout manuel

Permet à l'assistant d'ajouter un fait en cours de session :
_"Tu préfères utiliser uv plutôt que pip — je le mémorise."_

La catégorie est suggérée par le modèle appelant ou déduite automatiquement.

### `list` — Parcourir la mémoire

Utile pour que l'assistant sache ce qu'il connaît déjà avant de poser
des questions redondantes.

### `import_source` — Déclencher un import depuis le client

Permet à l'assistant de lancer un import sans passer par le CLI.

### `delete` — Supprimer un fait

Permet à l'assistant de retirer un fait obsolète signalé par l'utilisateur.

### `import_conversations` — Récupérer les conversations brutes (6ème outil)

Retourne les conversations brutes sous forme paginée, **sans extraction LLM côté serveur**.
L'IA cliente reçoit le texte et décide elle-même quels faits mémoriser via `add`.

**Arguments :**
- `source` : `"claude-code"` | `"claude"` — source à lire
- `chemin` *(optionnel)* : chemin personnalisé (ZIP pour Claude, répertoire pour Claude Code)
- `page` *(défaut : 1)* : numéro de page à récupérer
- `taille_page` *(défaut : 5)* : nombre de conversations par page

**Retour :**
```json
{
  "conversations": [
    { "source_detail": "~/.claude/projects/.../session.jsonl", "texte": "..." },
    ...
  ],
  "page": 1,
  "total_pages": 12,
  "total_conversations": 58
}
```

**Workflow attendu :**

```
1. L'IA cliente appelle import_conversations(source, page=1)
2. Elle analyse les conversations reçues
3. Elle appelle add() pour chaque fait qu'elle juge mémorisable
4. Elle boucle : import_conversations(source, page=2), import_conversations(source, page=3)...
5. Elle s'arrête quand page > total_pages
```

**Différence avec `import_source` :**

| | `import_source` | `import_conversations` |
|---|---|---|
| Extraction de faits | Côté serveur (qwen3 via Ollama) | Côté client (l'IA appelante) |
| Contrôle de l'IA | Aucun | Total — elle choisit ce qu'elle mémorise |
| Usage typique | CLI / import en masse | Session interactive, analyse fine |
| Dépendance Ollama | Requise | Non requise pour le parsing |

---

## 4. Catégories de faits

| Catégorie | Ce qu'elle contient |
|---|---|
| `stack` | Langages, frameworks, outils préférés, versions utilisées |
| `projet` | Projets actifs, leur état, leur stack, leur objectif |
| `preference` | Style de code, conventions, façon de travailler |
| `decision` | Décisions techniques prises et leur rationale |
| `contrainte` | Contraintes connues (environnement, règles, limites) |
| `contexte` | Domaine d'expertise, parcours, niveau sur une techno |
| `autre` | Tout ce qui ne rentre pas dans les catégories ci-dessus |

Un fait = une phrase courte (~1 ligne), autonome et compréhensible hors contexte.

---

## 5. Durée de vie des faits

- Un fait est **actif** par défaut
- Il devient **expiré** (soft delete) si `date_derniere_utilisation` est absente
  ou ancienne de plus de 12 mois
- Suppression physique uniquement via `mmcp clean` (confirmation requise)
- Aucune expiration automatique en arrière-plan

---

## 6. Clients MCP supportés

| Client | Détection | Fichier config |
|---|---|---|
| Claude Code | `~/.claude/mcp.json` existe | `~/.claude/mcp.json` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` existe | idem |
| Cursor | `~/.cursor/mcp.json` existe | `~/.cursor/mcp.json` |
| VS Code | `.vscode/mcp.json` dans le dossier courant | `.vscode/mcp.json` |
| Gemini Code Assist | `~/.gemini/settings.json` existe | `~/.gemini/settings.json` |

---

## 7. Sources d'import

| Source | Commande | Format | Priorité d'implémentation |
|---|---|---|---|
| Claude Code | `mmcp import claude-code` | JSONL auto-détecté | 1 — MVP |
| Claude | `mmcp import claude <zip>` | ZIP officiel | 2 — MVP |
| ChatGPT | `mmcp import chatgpt <zip>` | ZIP officiel (format à documenter) | 3 — post-MVP |

---

## 8. Plan de développement MVP

### Phase 1 — Serveur MCP minimal
Outils `search` et `add` fonctionnels. sqlite-vec opérationnel. Embeddings Ollama.

**Critère de validation :** Claude Code voit le serveur dans `/mcp` et retourne
des résultats à `search`.

### Phase 2 — Import Claude Code
Parseur JSONL, pipeline extraction qwen3, déduplication.
`mmcp import claude-code` avec progress bar.

**Critère de validation :** faits pertinents extraits depuis les sessions
existantes, re-import sans doublons.

### Phase 3 — `mmcp setup`
Détection clients, merge non-destructif des configs.

**Critère de validation :** serveur actif dans Claude Code + Claude Desktop
après `mmcp setup` + redémarrage.

### Phase 4 — Import Claude
Parseur ZIP en mémoire.
- `memories.json` → faits directs (sans LLM)
- `conversations.json` → extraction LLM

**Critère de validation :** faits cohérents extraits depuis le ZIP d'export.

### Phase 5 — Outil `import_conversations`
Module `lecteur.py` de parsing pur (sans LLM ni embeddings).
Exposition via outil MCP `import_conversations` avec pagination.

**Critère de validation :** une IA cliente peut parcourir toutes les pages de conversations et mémoriser des faits via `add()` sans passer par `import_source`.

### Hors MVP
- Import ChatGPT (format à documenter)
- UI web FastAPI légère
- `mmcp export` (backup)
- Support Obsidian / markdown

---

## 9. Critères de validation MVP complets

- [ ] `mmcp serve` → Claude Code liste les 6 outils MCP disponibles
- [ ] `mmcp import claude-code` → faits extraits, catégorisés, stockés
- [ ] Re-import immédiat → compteur dédupliqués = total, zéro doublon
- [ ] `search` depuis Claude Code → résultats pertinents en < 500ms
- [ ] `mmcp setup` → config Claude Code mise à jour sans casser l'existant
- [ ] `mmcp status` → confirme Ollama disponible + nb de faits correct
- [ ] `mmcp import claude <zip>` → faits depuis memories.json + conversations
- [ ] `import_conversations` → retourne conversations paginées sans extraction LLM ; une IA cliente peut boucler sur toutes les pages et appeler `add()` pour mémoriser
