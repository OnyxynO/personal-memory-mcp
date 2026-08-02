"""La commande `mmcp search --json` émet un JSON machine parsable (pas de rich)."""
import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from personal_memory_mcp.cli.main import app

runner = CliRunner()


def _faux_service(resultats):
    svc = MagicMock()
    svc.search.return_value = resultats
    return svc


def test_search_json_emet_un_tableau_json_filtre_par_seuil():
    resultats = [
        {"id": 1, "contenu": "Pièges TDD", "categorie": "doc", "source": "workspace",
         "score": 0.76, "score_importance": 0.5},
        {"id": 2, "contenu": "bruit sous le seuil", "categorie": "autre", "source": "workspace",
         "score": 0.10, "score_importance": 0.1},
    ]
    with patch("personal_memory_mcp.cli.main._service", return_value=_faux_service(resultats)):
        res = runner.invoke(app, ["search", "tdd", "-k", "5", "-s", "0.2", "--json"])

    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)          # doit parser sans erreur
    assert [r["id"] for r in data] == [1]  # le second est sous le seuil 0.2
    assert data[0]["contenu"] == "Pièges TDD"
    assert set(data[0]) >= {"id", "contenu", "categorie", "source", "score"}
