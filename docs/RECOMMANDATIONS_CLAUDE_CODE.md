# Recommandations issues de l'analyse du source Claude Code

> Faits extraits du source TypeScript de Claude Code (v2.1.84) et croisés avec l'architecture
> actuelle de personal-memory-mcp. Classées par priorité d'implémentation.

---

## Priorité 1 — Impact fort, effort faible

### 1.1 Aligner la taxonomie des catégories sur Claude Code

Claude Code utilise une **taxonomie fermée de 4 types** validée en production :
`user` | `feedback` | `project` | `reference`

Avec des exclusions explicites documentées dans le code :
- ❌ Code patterns / architecture (dérivable du code source)
- ❌ Git history / qui a changé quoi (dérivable de `git log`)
- ❌ Détails de debug / recettes de fix (le fix est dans le code)
- ❌ État temporaire / contexte de la session courante

**Actuellement** : `stack | projet | preference | decision | contrainte | contexte | autre`
→ Catégories trop nombreuses, pas de frontières claires, `autre` capte tout.

**Recommandation** : migrer vers les 4 types Claude Code + ajouter les exclusions dans les
docstrings des outils MCP pour guider l'IA qui appelle `add()`.

```python
# Nouveau schéma de catégories
CATEGORIES = Literal["user", "feedback", "project", "reference"]

# Dans la docstring de add() :
# Ne PAS mémoriser : code patterns, architecture, git history,
# solutions de debug, état temporaire de la session.
```

---

### 1.2 Fraîcheur des faits — caveat automatique si >1 jour

Claude Code ajoute automatiquement un avertissement sur les faits anciens :
> *"claims about code may be outdated"* si age > 1 jour

Et formate l'âge de manière lisible : `today` / `yesterday` / `{n} days ago`

**Actuellement** : `date_derniere_utilisation` est stockée mais jamais exposée dans `search()`.

**Recommandation** : enrichir la réponse de `search()` avec `age_jours` et un champ
`avertissement` si > 7 jours.

```python
def search(...) -> list[dict]:
    résultats = self._storage.rechercher(...)
    for r in résultats:
        age = (datetime.now() - r["date_creation"]).days
        r["age"] = "aujourd'hui" if age == 0 else f"il y a {age}j"
        if age > 7:
            r["avertissement"] = "Ce fait peut être périmé"
    return résultats
```

---

### 1.3 MEMORY.md index — max 200 lignes

Claude Code génère un `MEMORY.md` qui est **toujours chargé en contexte** comme index.
Règles strictes : max 200 lignes / 25 KB, warning si dépassé.
Le fichier est un **index** (une ligne par mémoire), pas le contenu complet.

**Applicabilité** : implémenter une commande CLI `mmcp index` qui génère
`~/.personal-memory/MEMORY.md` à partir des faits les plus récents/importants.

```bash
mmcp index          # Génère ~/.personal-memory/MEMORY.md
mmcp index --watch  # Regénère après chaque import
```

Format de chaque ligne (pattern `formatMemoryManifest()` de Claude Code) :
```
- [user] préférences-dev (2026-04-01): expérience 10+ ans, PHP/Delphi → JS moderne
- [feedback] style-réponses (2026-03-28): réponses courtes et directes, pas de résumé final
```

---

## Priorité 2 — Impact moyen, effort moyen

### 2.1 Dependency injection dans MemoryService

Claude Code utilise une interface `QueryDeps` avec **4 dépendances injectables** :
```typescript
interface QueryDeps {
  callModel: CallModelFn
  microcompact: CompactFn
  autocompact: CompactFn
  uuid: () => string
}
```
→ Tests sans `spyOn`, `productionDeps()` retourne les vraies implémentations.

**Actuellement** : `MemoryService.__init__` instancie directement `Storage` et `ExtracteurOllama`.
→ Les tests doivent sous-classer `MemoryService` (contournement fragile).

**Recommandation** : extraire un `ServiceDeps` injectable :

```python
@dataclass
class ServiceDeps:
    storage: Storage
    extracteur: ExtracteurBase

    @classmethod
    def production(cls, chemin_db: Path, ollama_url: str, ...) -> "ServiceDeps":
        return cls(
            storage=Storage(chemin_db),
            extracteur=ExtracteurOllama(ollama_url, ...),
        )

class MemoryService:
    def __init__(self, deps: ServiceDeps):
        self._deps = deps
```

---

### 2.2 Fail open sur Ollama indisponible

Claude Code applique le **fail open** pour les policy limits : si le serveur est
injoignable, les limites ne s'appliquent pas (pas de blocage).

**Actuellement** : si Ollama est indisponible, `search()` et `add()` lèvent une exception
et bloquent complètement le serveur MCP.

**Recommandation** : fail open — retourner une réponse dégradée plutôt que planter.

```python
def search(self, query: str, ...) -> list[dict]:
    try:
        [embedding] = self._extracteur.embeddings([query])
        return self._storage.rechercher(embedding, ...)
    except OllamaIndisponible:
        # Fallback : recherche textuelle simple sans vecteurs
        return self._storage.rechercher_texte(query, top_k=top_k)
```

---

### 2.3 SessionMemory — seuils cumulatifs avant extraction

Claude Code ne déclenche l'extraction qu'après **3 seuils cumulatifs** :
1. `>10 000 tokens` au démarrage de la session
2. `>5 000 tokens` supplémentaires depuis la dernière extraction
3. `≥3 tool calls` depuis la dernière extraction

→ Évite les extractions trop fréquentes qui consomment des tokens inutilement.

**Applicabilité** : dans `import_conversations()`, ne pas extraire si la page contient
< N tokens de contenu utile. Ajouter un `min_tokens` par page.

```python
def import_conversations(..., min_tokens: int = 500) -> dict:
    """Ne traite les conversations que si elles dépassent min_tokens."""
```

---

### 2.4 Away Summary — résumé des nouveautés depuis dernière consultation

Claude Code génère un résumé après absence avec **haiku** sur une fenêtre de 30 messages.

**Applicabilité** : ajouter un outil MCP `whats_new(depuis: str)` qui retourne les faits
ajoutés depuis une date, formatés en résumé court.

```python
@mcp.tool()
def whats_new(depuis_jours: int = 7) -> dict:
    """Résume les faits ajoutés/modifiés depuis N jours."""
    faits = self._storage.faits_recents(depuis_jours)
    return {
        "nb_faits": len(faits),
        "résumé": f"{len(faits)} faits depuis {depuis_jours}j",
        "faits": faits[:10],  # Max 10 pour ne pas saturer le contexte
    }
```

---

## Priorité 3 — Nice to have

### 3.1 AutoDream — consolidation périodique des faits

Claude Code déclenche une consolidation nocturne avec :
- **Time-gate** : ≥ 24h depuis la dernière consolidation
- **Session-gate** : ≥ 5 sessions modifiées depuis `lastConsolidatedAt`
- **Lock exclusif** : une seule consolidation en cours

→ Distille les logs de sessions en `MEMORY.md` thématique.

**Applicabilité** : commande CLI `mmcp consolidate` qui :
1. Regroupe les faits par thème (clustering vectoriel sur les embeddings existants)
2. Fusionne les faits redondants que la déduplication cosinus n'a pas captés
3. Met à jour `MEMORY.md`

Déclencher automatiquement si >50 faits ajoutés depuis la dernière consolidation.

---

### 3.2 Post-add hook registry — validation et enrichissement

Claude Code expose un `registerPostSamplingHook()` avec graphe de dépendances
(`runBefore: [...]`). Erreurs catchées et loggées sans propagation.

**Applicabilité** : après chaque `add()`, permettre des validators optionnels :

```python
class MemoryService:
    _hooks: list[Callable] = []

    @classmethod
    def register_hook(cls, fn: Callable) -> None:
        cls._hooks.append(fn)

    def add(self, ...) -> dict:
        résultat = self._ajouter_interne(...)
        for hook in self._hooks:
            try:
                hook(résultat)
            except Exception as e:
                logger.warning(f"Hook {hook.__name__} failed: {e}")
        return résultat
```

Cas d'usage : enrichissement automatique de catégorie, normalisation du contenu,
notification après ajout.

---

### 3.3 Token budget warning sur list_facts

Claude Code track un **token budget** et détecte les diminishing returns
(δ < 500 tokens × 2 fois consécutives → stop).

**Actuellement** : `list_facts` sans filtre retourne ~70 tokens/fait.
Le warning est dans la docstring mais pas dans la réponse.

**Recommandation** : inclure une estimation tokens dans la réponse.

```python
def list(self, ...) -> list[dict]:
    faits = self._storage.lister(...)
    tokens_estimés = sum(len(f["contenu"].split()) * 1.3 for f in faits)
    if tokens_estimés > 3000:
        # Ajouter un champ d'avertissement dans le premier élément
        faits.insert(0, {
            "avertissement": f"⚠ ~{int(tokens_estimés)} tokens estimés. Préférer search()."
        })
    return faits
```

---

### 3.4 Versionning et migrations

Claude Code utilise un **migration runner** simple : chaque migration est une fonction
exportée avec un numéro de version, exécutée une fois au démarrage si la DB est en retard.

**Applicabilité** : au lieu de modifier le schéma SQLite à la main, implémenter
`migrations/001_add_type_column.py`, `migrations/002_rename_categories.py`, etc.

```python
MIGRATIONS = [
    ("001", migration_add_age_column),
    ("002", migration_rename_categories),
]

def run_migrations(db: Connection) -> None:
    current = db.execute("PRAGMA user_version").fetchone()[0]
    for idx, (name, fn) in enumerate(MIGRATIONS):
        if idx >= current:
            fn(db)
            db.execute(f"PRAGMA user_version = {idx + 1}")
```

---

## Résumé — ordre d'implémentation suggéré

| # | Recommandation | Effort | Impact |
|---|---|---|---|
| 1 | Aligner taxonomie (4 types + exclusions) | Faible | Fort |
| 2 | Fraîcheur des faits dans search() | Faible | Moyen |
| 3 | Commande mmcp index → MEMORY.md | Faible | Moyen |
| 4 | Fail open sur Ollama indisponible | Moyen | Fort |
| 5 | Dependency injection ServiceDeps | Moyen | Moyen |
| 6 | Outil whats_new() | Faible | Moyen |
| 7 | SessionMemory min_tokens | Faible | Faible |
| 8 | AutoDream / consolidation | Élevé | Moyen |
| 9 | Post-add hook registry | Moyen | Faible |
| 10 | Token budget warning list_facts | Faible | Faible |
| 11 | Migration runner | Moyen | Faible |

> Les items 1-3 et 6 sont des quick wins — peu de code, valeur immédiate.
> L'item 4 (fail open) est le plus important pour la robustesse en production.
