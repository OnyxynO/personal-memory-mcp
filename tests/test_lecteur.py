"""Tests du lecteur de conversations — parsing pur, sans LLM ni réseau.

Couvre :
- paginer() : pagination, cas limites (page hors-limites, taille nulle, liste vide)
- lire_claude_code() : JSONL valides, filtrés, troncature, dossier absent/vide
- lire_claude_zip() : ZIP valide (memories.json, conversations.json), fichier absent,
  fichier corrompu, ordre de sortie, ZIP sans fichiers reconnus
"""

import io
import json
import zipfile

from personal_memory_mcp.importeurs.lecteur import lire_claude_code, lire_claude_zip, paginer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _creer_zip(contenu: dict[str, str]) -> bytes:
    """Crée un ZIP en mémoire contenant les fichiers fournis dans le dict {nom: contenu_str}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        for nom, texte in contenu.items():
            zf.writestr(nom, texte)
    return buffer.getvalue()


def _msg_jsonl(role: str, texte: str) -> str:
    """Construit une ligne JSONL au format Claude Code."""
    return json.dumps({
        "message": {
            "role": role,
            "content": [{"type": "text", "text": texte}],
        }
    })


# ---------------------------------------------------------------------------
# Tests paginer()
# ---------------------------------------------------------------------------

class TestPaginer:
    """Tests de la fonction paginer — logique pure, sans I/O."""

    def test_page_1_sur_liste_de_10(self):
        """Page 1, taille 5 sur 10 conversations → 2 pages, 5 éléments retournés."""
        convs = [{"id": i} for i in range(10)]
        resultat = paginer(convs, page=1, taille_page=5)

        assert resultat["total_pages"] == 2
        assert resultat["total_conversations"] == 10
        assert len(resultat["conversations"]) == 5
        assert resultat["page"] == 1

    def test_derniere_page_incomplete(self):
        """7 conversations, taille 5 → page 2 retourne 2 éléments."""
        convs = [{"id": i} for i in range(7)]
        resultat = paginer(convs, page=2, taille_page=5)

        assert resultat["total_pages"] == 2
        assert len(resultat["conversations"]) == 2
        assert resultat["page"] == 2

    def test_page_hors_limites_clampee(self):
        """Page 99 sur une liste qui n'a que 2 pages → clampée à la page 2."""
        convs = [{"id": i} for i in range(10)]
        resultat = paginer(convs, page=99, taille_page=5)

        assert resultat["page"] == 2
        assert len(resultat["conversations"]) == 5

    def test_taille_page_zero_clampee_a_1(self):
        """taille_page=0 → clampée à 1 (max(1, taille_page)), chaque page = 1 élément."""
        convs = [{"id": i} for i in range(3)]
        resultat = paginer(convs, page=1, taille_page=0)

        assert resultat["total_pages"] == 3
        assert len(resultat["conversations"]) == 1

    def test_taille_page_negative_clampee_a_1(self):
        """taille_page=-5 → clampée à 1, comme taille_page=0."""
        convs = [{"id": i} for i in range(3)]
        resultat = paginer(convs, page=1, taille_page=-5)

        assert resultat["total_pages"] == 3
        assert len(resultat["conversations"]) == 1

    def test_liste_vide(self):
        """Liste vide → total_pages=1, total_conversations=0, liste conversations vide."""
        resultat = paginer([], page=1, taille_page=5)

        assert resultat["total_pages"] == 1
        assert resultat["total_conversations"] == 0
        assert resultat["conversations"] == []


# ---------------------------------------------------------------------------
# Tests lire_claude_code()
# ---------------------------------------------------------------------------

class TestLireClaudeCode:
    """Tests du lecteur de sessions Claude Code (fichiers JSONL)."""

    def test_repertoire_inexistant(self):
        """Chemin inexistant → retourne liste vide, sans exception."""
        resultat = lire_claude_code("/chemin/qui/nexiste/absolument/pas")
        assert resultat == []

    def test_repertoire_vide(self, tmp_path):
        """Dossier sans aucun .jsonl → retourne liste vide."""
        # tmp_path est vide par construction
        resultat = lire_claude_code(str(tmp_path))
        assert resultat == []

    def test_messages_trop_courts_filtres(self, tmp_path):
        """Messages de moins de MIN_MOTS (10) mots → conversation absente du résultat."""
        fichier = tmp_path / "session.jsonl"
        # Moins de 10 mots pour chaque message
        fichier.write_text(
            _msg_jsonl("user", "Bonjour") + "\n" +
            _msg_jsonl("assistant", "Bonsoir") + "\n",
            encoding="utf-8",
        )

        resultat = lire_claude_code(str(tmp_path))
        assert resultat == []

    def test_messages_valides_retournes(self, tmp_path):
        """Messages valides (≥ 10 mots) → entrée avec source_detail et texte formaté."""
        fichier = tmp_path / "session.jsonl"
        texte_user = "Peux-tu m'expliquer comment fonctionne la pagination en Python avec des listes longues ?"
        texte_assistant = "La pagination consiste à découper une grande liste en sous-listes de taille fixe appelées pages."
        fichier.write_text(
            _msg_jsonl("user", texte_user) + "\n" +
            _msg_jsonl("assistant", texte_assistant) + "\n",
            encoding="utf-8",
        )

        resultat = lire_claude_code(str(tmp_path))

        assert len(resultat) == 1
        assert "source_detail" in resultat[0]
        assert "texte" in resultat[0]
        assert "Utilisateur:" in resultat[0]["texte"]
        assert "Assistant:" in resultat[0]["texte"]

    def test_troncature_a_1000_chars(self, tmp_path):
        """Message de 2000 caractères → tronqué à 1000 dans le texte retourné."""
        fichier = tmp_path / "session.jsonl"
        # 2000 caractères : assez long pour déclencher la troncature
        texte_long = "mot " * 500   # 2000 chars environ
        fichier.write_text(
            _msg_jsonl("user", texte_long) + "\n",
            encoding="utf-8",
        )

        resultat = lire_claude_code(str(tmp_path))

        assert len(resultat) == 1
        # "Utilisateur: " + 1000 chars max
        texte_retourne = resultat[0]["texte"]
        # Le texte après le préfixe "Utilisateur: " doit faire au plus 1000 chars
        prefixe = "Utilisateur: "
        assert texte_retourne.startswith(prefixe)
        contenu = texte_retourne[len(prefixe):]
        assert len(contenu) == 1000

    def test_source_detail_contient_chemin_fichier(self, tmp_path):
        """source_detail doit contenir le chemin absolu du fichier JSONL."""
        fichier = tmp_path / "ma_session.jsonl"
        texte_valide = "Peux-tu m'expliquer comment fonctionne la pagination en Python avec des listes longues ?"
        fichier.write_text(
            _msg_jsonl("user", texte_valide) + "\n",
            encoding="utf-8",
        )

        resultat = lire_claude_code(str(tmp_path))

        assert len(resultat) == 1
        assert "ma_session.jsonl" in resultat[0]["source_detail"]


# ---------------------------------------------------------------------------
# Tests lire_claude_zip()
# ---------------------------------------------------------------------------

class TestLireClaudeZip:
    """Tests du lecteur d'export ZIP officiel Claude."""

    def test_fichier_inexistant(self):
        """Chemin vers un ZIP inexistant → retourne liste vide."""
        resultat = lire_claude_zip("/chemin/inexistant/export.zip")
        assert resultat == []

    def test_fichier_non_zip(self, tmp_path):
        """Fichier avec contenu invalide (non-ZIP) → liste avec une entrée d'erreur, pas d'exception."""
        faux_zip = tmp_path / "corrompu.zip"
        faux_zip.write_bytes(b"ceci n'est pas un zip valide du tout")

        resultat = lire_claude_zip(str(faux_zip))

        # Doit retourner une entrée d'erreur, pas lever d'exception
        assert isinstance(resultat, list)
        assert len(resultat) == 1
        assert "Erreur" in resultat[0]["texte"]

    def test_zip_avec_memories_json(self, tmp_path):
        """ZIP valide contenant memories.json → entrée avec source_detail='memories.json'."""
        memories_data = json.dumps([
            {"conversations_memory": "Développeur Python utilisant FastAPI et SQLite pour les projets locaux."}
        ])
        zip_path = tmp_path / "export.zip"
        zip_path.write_bytes(_creer_zip({"memories.json": memories_data}))

        resultat = lire_claude_zip(str(zip_path))

        assert len(resultat) >= 1
        sources = [e["source_detail"] for e in resultat]
        assert "memories.json" in sources

    def test_zip_avec_conversations_json(self, tmp_path):
        """ZIP valide avec conversations.json → entrées avec UUID comme source_detail."""
        uuid_test = "abc123-test-uuid"
        convs_data = json.dumps([
            {
                "uuid": uuid_test,
                "chat_messages": [
                    {
                        "sender": "human",
                        "content": [{"type": "text", "text": "Peux-tu m'expliquer la programmation fonctionnelle en Python avec des exemples concrets ?"}],
                    },
                    {
                        "sender": "assistant",
                        "content": [{"type": "text", "text": "La programmation fonctionnelle en Python s'appuie sur des fonctions pures, map, filter et reduce."}],
                    },
                ],
            }
        ])
        zip_path = tmp_path / "export.zip"
        zip_path.write_bytes(_creer_zip({"conversations.json": convs_data}))

        resultat = lire_claude_zip(str(zip_path))

        assert len(resultat) >= 1
        sources = [e["source_detail"] for e in resultat]
        assert uuid_test in sources

    def test_memories_json_en_premier(self, tmp_path):
        """ZIP avec memories.json ET conversations.json → memories.json en premier dans la liste."""
        uuid_test = "uuid-ordre-test"
        memories_data = json.dumps([
            {"conversations_memory": "Développeur Python senior avec expérience en data engineering."}
        ])
        convs_data = json.dumps([
            {
                "uuid": uuid_test,
                "chat_messages": [
                    {
                        "sender": "human",
                        "content": [{"type": "text", "text": "Comment configurer un environnement virtuel Python avec uv pour un nouveau projet ?"}],
                    },
                ],
            }
        ])
        zip_path = tmp_path / "export_complet.zip"
        # Intentionnellement, conversations.json est écrit en premier dans le ZIP
        zip_path.write_bytes(_creer_zip({
            "conversations.json": convs_data,
            "memories.json": memories_data,
        }))

        resultat = lire_claude_zip(str(zip_path))

        assert len(resultat) >= 1
        assert resultat[0]["source_detail"] == "memories.json"

    def test_zip_sans_fichiers_reconnus(self, tmp_path):
        """ZIP ne contenant ni memories.json ni conversations.json → retourne liste vide."""
        zip_path = tmp_path / "export_inconnu.zip"
        zip_path.write_bytes(_creer_zip({"autre_fichier.txt": "données non reconnues"}))

        resultat = lire_claude_zip(str(zip_path))

        assert resultat == []

    def test_memories_json_malformes(self, tmp_path):
        """ZIP avec memories.json contenant du JSON invalide → entrée d'erreur avec source_detail='memories.json'."""
        zip_path = tmp_path / "export_malformed.zip"
        zip_path.write_bytes(_creer_zip({"memories.json": "pas du json"}))

        resultat = lire_claude_zip(str(zip_path))

        assert isinstance(resultat, list)
        assert len(resultat) >= 1
        entree_erreur = next((e for e in resultat if e.get("source_detail") == "memories.json"), None)
        assert entree_erreur is not None
        assert "Erreur" in entree_erreur["texte"]

    def test_uuid_manquant_remplace_par_inconnu(self, tmp_path):
        """Conversation sans champ 'uuid' dans conversations.json → source_detail vaut 'uuid-inconnu'."""
        convs_data = json.dumps([
            {
                "chat_messages": [
                    {
                        "sender": "human",
                        "content": [{"type": "text", "text": "Peux-tu m'expliquer comment fonctionne la pagination en Python avec des listes longues ?"}],
                    },
                    {
                        "sender": "assistant",
                        "content": [{"type": "text", "text": "La pagination consiste à découper une grande liste en sous-listes de taille fixe appelées pages."}],
                    },
                ],
            }
        ])
        zip_path = tmp_path / "export_sans_uuid.zip"
        zip_path.write_bytes(_creer_zip({"conversations.json": convs_data}))

        resultat = lire_claude_zip(str(zip_path))

        sources = [e["source_detail"] for e in resultat]
        assert "uuid-inconnu" in sources

    def test_zip_conversations_messages_trop_courts_filtres(self, tmp_path):
        """Messages de moins de 10 mots dans conversations.json → conversation absente."""
        uuid_test = "uuid-filtre"
        convs_data = json.dumps([
            {
                "uuid": uuid_test,
                "chat_messages": [
                    {
                        "sender": "human",
                        "content": [{"type": "text", "text": "Bonjour"}],
                    },
                    {
                        "sender": "assistant",
                        "content": [{"type": "text", "text": "Salut"}],
                    },
                ],
            }
        ])
        zip_path = tmp_path / "export_court.zip"
        zip_path.write_bytes(_creer_zip({"conversations.json": convs_data}))

        resultat = lire_claude_zip(str(zip_path))

        # La conversation ne contient que des messages trop courts → non incluse
        sources = [e["source_detail"] for e in resultat]
        assert uuid_test not in sources
