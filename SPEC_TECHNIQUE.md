# PersonalMemoryMCP — Spécifications techniques

> Version : 0.1 — mars 2026

---

## 1. Stack technique

| Composant | Technologie | Justification |
|---|---|---|
| Langage | Python 3.13 | Cohérence DataMatch, écosystème IA |
| Gestionnaire paquets | uv | Standard Python moderne |
| Serveur MCP | `mcp` SDK officiel Anthropic | Standard, maintenance assurée |
| Stockage vectoriel | `sqlite-vec` | Zéro service, stack validée DataMatch |
| Embeddings | Ollama `nomic-embed-text` | Local, configurable |
| Extraction de faits | Ollama `qwen3:1.7b` | Local, validé DataMatch |
| CLI | `typer` + `rich` | Autocomplétion, progress bars, tableaux |

**Interface d'extraction abstraite** (`ExtracteurBase` ABC) : mem0 ou Ollama direct
restent interchangeables — décision différée à l'implémentation.

---

## 2. Emplacement des données

```
~/.personal-memory/
├── memory.db          # SQLite principal (faits + sqlite-vec)
├── config.toml        # Configuration Ollama, seuils, chemins
└── logs/
    └── import.log     # Historique des imports
```

---

## 3. Modèle de données

### Table `faits`

```sql
CREATE TABLE faits (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    contenu                   TEXT NOT NULL,
    categorie                 TEXT NOT NULL,
    source                    TEXT NOT NULL,    -- "claude-code" | "claude" | "chatgpt" | "manuel"
    source_detail             TEXT,             -- chemin fichier ou session_id
    date_creation             TEXT NOT NULL,    -- ISO 8601
    date_derniere_utilisation TEXT,             -- NULL si jamais utilisé via MCP
    actif                     INTEGER DEFAULT 1 -- 0 = expiré (soft delete)
);
```

### Table `imports`

```sql
CREATE TABLE imports (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    type                 TEXT NOT NULL,
    chemin               TEXT,
    date_import          TEXT NOT NULL,    -- ISO 8601
    nb_faits_ajoutes     INTEGER DEFAULT 0,
    nb_faits_dedupliques INTEGER DEFAULT 0,
    nb_faits_mis_a_jour  INTEGER DEFAULT 0,
    duree_secondes       REAL
);
```

### Table virtuelle sqlite-vec

```sql
CREATE VIRTUAL TABLE faits_vec USING vec0(
    embedding FLOAT[768]    -- dimension nomic-embed-text
);
```

La jointure `faits.id = faits_vec.rowid` lie les deux tables.

---

## 4. Configuration `~/.personal-memory/config.toml`

```toml
[ollama]
url = "http://localhost:11434"
modele_extraction = "qwen3:1.7b"
modele_embeddings = "nomic-embed-text"

[memoire]
seuil_deduplication = 0.92
expiration_mois = 12
limite_list_defaut = 50

[import]
chemin_claude_code = "~/.claude/projects"    # auto-détecté si absent
```

---

## 5. Architecture — Structure de fichiers

```
personal-memory/
├── CLAUDE.md
├── SPEC_FONCTIONNELLE.md
├── SPEC_TECHNIQUE.md
├── pyproject.toml
├── README.md
│
└── src/
    └── personal_memory_mcp/
        ├── __init__.py
        ├── __main__.py                  # Point d'entrée : python -m personal_memory_mcp
        │
        ├── cli/
        │   ├── __init__.py
        │   └── main.py                  # Commandes typer
        │
        ├── mcp/
        │   ├── __init__.py
        │   └── server.py                # Serveur MCP + définition des 5 outils
        │
        ├── memory/
        │   ├── __init__.py
        │   ├── service.py               # MemoryService — couche métier centrale
        │   ├── storage.py               # SQLite + sqlite-vec (CRUD + search)
        │   └── deduplication.py         # Logique similarité cosinus
        │
        ├── extraction/
        │   ├── __init__.py
        │   ├── base.py                  # ExtracteurBase (ABC)
        │   └── ollama.py                # ExtracteurOllama — implémentation MVP
        │
        ├── importeurs/
        │   ├── __init__.py
        │   ├── base.py                  # ImporteurBase (ABC) + dataclasses
        │   ├── claude_code.py           # ImporteurClaudeCode — MVP phase 1
        │   └── claude.py                # ImporteurClaude (ZIP) — MVP phase 2
        │
        └── setup/
            ├── __init__.py
            └── clients.py               # Détection + patch configs MCP clients
```

**Tests :**
```
tests/
├── fixtures/
│   ├── claude_code_sample.jsonl
│   └── claude_memories_sample.json    # extrait anonymisé
├── test_importeurs.py
├── test_extraction.py
└── test_deduplication.py
```

---

## 6. Interfaces abstraites

### Parseur d'import

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Message:
    role: str           # "user" | "assistant"
    contenu: str
    date: str | None    # ISO 8601

@dataclass
class Conversation:
    source: str         # "claude-code" | "claude" | "chatgpt"
    source_detail: str  # chemin ou session_id (pour source_detail en BDD)
    messages: list[Message]

class ImporteurBase(ABC):
    @abstractmethod
    def charger(self, chemin: str) -> list[Conversation]: ...

    @abstractmethod
    def valider_format(self, chemin: str) -> bool: ...
```

### Extracteur de faits

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class FaitExtrait:
    contenu: str
    categorie: str       # valeur de l'enum catégories
    score_confiance: float  # 0.0 – 1.0

class ExtracteurBase(ABC):
    @abstractmethod
    def extraire(self, conversation: Conversation) -> list[FaitExtrait]: ...
```

---

## 7. Outils MCP — Signatures

```python
@tool
def search(
    query: str,
    top_k: int = 5,
    categorie: str | None = None
) -> list[dict]:
    # [{"id", "contenu", "categorie", "source", "score"}]

@tool
def add(
    contenu: str,
    categorie: str = "autre",
    source: str = "manuel"
) -> dict:
    # {"id", "contenu", "categorie", "nouveau": bool}

@tool
def list(
    categorie: str | None = None,
    limite: int = 50
) -> list[dict]:
    # [{"id", "contenu", "categorie", "source", "date_creation"}]

@tool
def import_source(
    type: str,              # "claude-code" | "claude" | "chatgpt"
    chemin: str | None = None
) -> dict:
    # {"ajoutes", "dedupliques", "mis_a_jour", "duree"}

@tool
def delete(id: int) -> dict:
    # {"succes": bool, "id": int}
```

---

## 8. Pipeline d'extraction de faits

```
Conversations
     │
1. Filtrage — écarter messages < 10 mots, messages code-only
     │
2. Chunking — regrouper par session (pas par message)
     │
3. Extraction — prompt qwen3:1.7b → JSON [{ contenu, categorie, score_confiance }]
     │
4. Filtrer les <think> tokens (qwen3 génère des balises de réflexion)
     │
5. Déduplication — similarité vectorielle vs existants
     │
6. Stockage sqlite + sqlite-vec
```

### Prompt d'extraction (ébauche)

```
Tu es un extracteur de mémoire personnelle.
Analyse cette conversation et extrais les faits mémorisables sur l'utilisateur.

Règles :
- Un fait = une phrase courte et autonome (~1 ligne)
- Uniquement ce qui sera utile dans de futures sessions
- Pas les faits éphémères (questions ponctuelles, bugs résolus, code ponctuel)
- Catégorie parmi : stack | projet | preference | decision | contrainte | contexte | autre

Retourne UNIQUEMENT un JSON valide, sans commentaire :
[{"contenu": "...", "categorie": "...", "score_confiance": 0.0}]

Conversation :
{texte}
```

---

## 9. Déduplication

Seuil cosinus : `>= 0.92` → doublon, ignorer.

```
Avant insertion d'un fait candidat :
  1. Calculer embedding via Ollama nomic-embed-text
  2. Chercher les 3 plus proches voisins dans faits_vec (sqlite-vec ANN)
  3. Si max(similarité) >= 0.92 → incrémenter dedupliques, ne pas insérer
  4. Si max(similarité) < 0.92 → insérer (faits + faits_vec)
```

**Important :** toujours passer les embeddings en batch à Ollama :
```python
# Correct — un seul appel HTTP
POST /api/embed { "model": "nomic-embed-text", "input": ["fait1", "fait2", ...] }

# Incorrect — N appels HTTP
for fait in faits: embed(fait)
```

---

## 10. Format des exports — Structure réelle vérifiée

### Export Claude (ZIP officiel)

```
data-YYYY-MM-DD-HH-MM-SS-batch-NNNN.zip
├── users.json          → [{ uuid, full_name, email_address }]
├── projects.json       → [{ uuid, name, description, prompt_template, created_at }]
├── memories.json       → [{ conversations_memory: "<markdown>", account_uuid }]
└── conversations.json  → liste de conversations
```

**`memories.json` — traitement spécial, sans extraction LLM**

Claude synthétise une mémoire structurée en sections markdown :
`**Work context**`, `**Personal context**`, `**Top of mind**`, `**Brief history**`...

Stratégie :
1. Découper par section (`**Titre**`)
2. Chaque paragraphe non vide → fait candidat direct
3. Catégorie déduite du titre de section :
   - Work context → `contexte`
   - Top of mind → `projet`
   - Personal context → `contexte`
   - Booklist → `preference`
   - Autres → `autre`

**`conversations.json` — structure d'un message**

```json
{
  "uuid": "...",
  "text": "",                   // souvent vide — NE PAS UTILISER
  "content": [
    {
      "type": "text",           // seul type observé
      "text": "contenu réel",
      "start_timestamp": "...",
      "citations": []
    }
  ],
  "sender": "human",            // ou "assistant"
  "created_at": "...",
  "attachments": [],
  "files": []
}
```

**Extraction du texte :**
```python
texte = " ".join(
    bloc["text"]
    for bloc in message["content"]
    if bloc.get("type") == "text" and bloc.get("text")
)
```

ZIP traité en mémoire (`zipfile.ZipFile(io.BytesIO(data))`) — rien écrit sur disque.

### Export Claude Code (JSONL)

Fichiers `~/.claude/projects/**/*.jsonl` — une entrée JSON par ligne.
Format connu, référence : sessions existantes dans le workspace.

### Export ChatGPT

Format à documenter quand export disponible.

---

## 11. Configuration MCP injectée par `mmcp setup`

Entrée ajoutée dans le bloc `mcpServers` de chaque client :

```json
"personal-memory": {
  "command": "mmcp",
  "args": ["serve"]
}
```

Merge non-destructif : lecture du JSON existant → ajout de la clé → réécriture.
Si `mcpServers` n'existe pas, le créer.

---

## 12. Points de vigilance

Issus des expériences DataMatch / BlueTang :

- **sqlite-vec bindings macOS arm64** : tester l'import au tout début de la phase 1
  (`import sqlite_vec` dans un script vide) avant de construire dessus
- **INSERT OR IGNORE + lastInsertRowid** : retourne le rowid du précédent insert
  si la ligne est ignorée — toujours faire un SELECT après pour récupérer l'id réel
- **Batching embeddings** : voir section 9 — un appel HTTP par fait = 5-10× plus lent
- **qwen3:1.7b thinking tokens** : le modèle génère des blocs `<think>...</think>`
  avant le JSON — les filtrer avec `re.sub(r'<think>.*?</think>', '', texte, flags=re.DOTALL)`
- **Export Claude `text` vide** : toujours lire via `content[].text`, jamais `text` direct
- **Nombre de conversations** : 290 conversations Claude = traitement potentiellement long
  (3-5 min) — progress bar obligatoire, ne pas bloquer sans feedback
