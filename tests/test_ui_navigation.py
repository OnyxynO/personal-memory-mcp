"""Tests de navigation browser — personal_memory_mcp.ui.

Utilise Playwright (sync API) pour tester l'interface HTML/JS réelle.
Skippés automatiquement si playwright n'est pas installé.

Prérequis pour exécuter :
    uv add --dev playwright
    playwright install chromium
"""

import importlib.util

import pytest

from tests.conftest_ui import ServeurContexte, creer_storage_memoire, inserer_fait

# ---------------------------------------------------------------------------
# Détection playwright
# ---------------------------------------------------------------------------

PLAYWRIGHT_DISPO = importlib.util.find_spec("playwright") is not None

# ---------------------------------------------------------------------------
# Fixtures Playwright
# ---------------------------------------------------------------------------

if PLAYWRIGHT_DISPO:
    from playwright.sync_api import Browser, Page, sync_playwright  # type: ignore[import]

    @pytest.fixture(scope="module")
    def navigateur():
        """Navigateur Chromium partagé pour tous les tests du module."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()

    @pytest.fixture()
    def page_vide(navigateur: "Browser"):
        """Page Playwright fraîche pour chaque test."""
        page = navigateur.new_page()
        yield page
        page.close()


# ---------------------------------------------------------------------------
# Tests Playwright
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not PLAYWRIGHT_DISPO, reason="playwright non installé")
class TestNavigationUI:
    """Tests d'intégration browser via Playwright — vérifient le comportement JS réel."""

    def test_page_charge_titre(self, page_vide: "Page"):
        """La page doit se charger et afficher le titre 'Personal Memory'."""
        with ServeurContexte(creer_storage_memoire()) as ctx:
            page_vide.goto(ctx.base_url)
            page_vide.wait_for_selector("h1")
            titre = page_vide.text_content("h1")

        assert titre is not None
        assert "Personal Memory" in titre

    def test_faits_affiches_apres_chargement(self, page_vide: "Page"):
        """Les faits chargés depuis /api/faits doivent apparaître comme cartes .fait."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "Premier fait de test", "stack")
        inserer_fait(storage, "Deuxième fait de test", "projet")
        inserer_fait(storage, "Troisième fait de test", "meta")

        with ServeurContexte(storage) as ctx:
            page_vide.goto(ctx.base_url)
            page_vide.wait_for_selector(".fait")
            cartes = page_vide.query_selector_all(".fait")

        assert len(cartes) == 3

    def test_recherche_filtre_resultats(self, page_vide: "Page"):
        """La recherche texte doit filtrer les cartes affichées en temps réel."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "J'utilise Python 3.13 pour ce projet", "stack")
        inserer_fait(storage, "Le frontend est en HTML vanilla", "frontend")
        inserer_fait(storage, "Python est mon langage préféré", "preference")

        with ServeurContexte(storage) as ctx:
            page_vide.goto(ctx.base_url)
            page_vide.wait_for_selector(".fait")

            assert len(page_vide.query_selector_all(".fait")) == 3

            page_vide.fill("#search", "Python")
            # Attendre que le filtrage synchrone réduise le nombre de cartes
            page_vide.wait_for_function("() => document.querySelectorAll('.fait').length === 2")
            apres = page_vide.query_selector_all(".fait")

        assert len(apres) == 2

    def test_tri_change_ordre(self, page_vide: "Page"):
        """Changer le select de tri doit réorganiser les cartes affichées."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "Fait catégorie B", "beta", date="2026-01-10T00:00:00+00:00")
        inserer_fait(storage, "Fait catégorie A", "alpha", date="2026-01-20T00:00:00+00:00")

        with ServeurContexte(storage) as ctx:
            page_vide.goto(ctx.base_url)
            page_vide.wait_for_selector(".fait")

            # Tri par défaut : date_desc (le plus récent en premier = A)
            cartes = page_vide.query_selector_all(".fait-contenu")
            assert cartes[0].text_content() is not None and "A" in (cartes[0].text_content() or "")

            # Basculer vers date_asc
            page_vide.select_option("#tri", "date_asc")
            # Attendre l'inversion : B doit passer en premier
            page_vide.wait_for_function(
                "() => document.querySelectorAll('.fait-contenu')[0].textContent.includes('B')"
            )
            cartes_apres = page_vide.query_selector_all(".fait-contenu")

        assert "B" in (cartes_apres[0].text_content() or "")

    def test_badge_categorie_filtre(self, page_vide: "Page"):
        """Cliquer sur un badge catégorie doit ne montrer que les faits de cette catégorie."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "Fait stack 1", "stack")
        inserer_fait(storage, "Fait stack 2", "stack")
        inserer_fait(storage, "Fait projet 1", "projet")

        with ServeurContexte(storage) as ctx:
            page_vide.goto(ctx.base_url)
            page_vide.wait_for_selector(".fait")

            assert len(page_vide.query_selector_all(".fait")) == 3

            # Cliquer sur le badge "stack"
            badges = page_vide.query_selector_all(".badge")
            badge_stack = next(
                (b for b in badges if b.text_content() and "stack" in b.text_content()),
                None,
            )
            assert badge_stack is not None, "Badge 'stack' introuvable"
            badge_stack.click()

            # Attendre que le filtrage réduise à 2 cartes
            page_vide.wait_for_function("() => document.querySelectorAll('.fait').length === 2")
            cartes_filtrees = page_vide.query_selector_all(".fait")

        assert len(cartes_filtrees) == 2

    def test_suppression_retire_carte(self, page_vide: "Page"):
        """Supprimer un fait via le bouton ✕ doit retirer sa carte de la liste."""
        storage = creer_storage_memoire()
        inserer_fait(storage, "Fait à supprimer", "test")
        inserer_fait(storage, "Fait à conserver", "test")

        with ServeurContexte(storage) as ctx:
            page_vide.goto(ctx.base_url)
            page_vide.wait_for_selector(".fait")
            assert len(page_vide.query_selector_all(".fait")) == 2

            # Intercepter window.confirm pour valider automatiquement
            page_vide.evaluate("window.confirm = () => true")

            page_vide.click(".btn-del")
            # Attendre que la suppression (DELETE réseau + re-render) soit effective
            page_vide.wait_for_function(
                "() => document.querySelectorAll('.fait').length === 1",
                timeout=3000,
            )
            cartes_restantes = page_vide.query_selector_all(".fait")

        assert len(cartes_restantes) == 1
