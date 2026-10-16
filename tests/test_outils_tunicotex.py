from datetime import date

import pytest

from sqlalchemy import create_engine

from app.agent.tools import TOOL_CATALOG
from app.agent.tools.dates import resoudre_jour, resoudre_periode
from app.agent.tools.navigation import PAGES, TOOL_PAGE_MAP
from app.agent.tools.tunicotex import (
    TUNICOTEX_TOOLS,
    avancement_of,
    commandes_a_risque,
    consulter_commande,
    detail_chaine,
    fiche_operateur,
    production_chaines,
    rechercher_of,
    rendement_operateurs,
    situation_atelier,
)
from app.integrations.divatex.connexion import set_engine



def appeler(outil, args):
    """Invoque l'outil comme le fait l'agent et renvoie (texte, artifact)."""
    msg = outil.invoke({"type": "tool_call", "id": "t1", "name": outil.name, "args": args})
    return msg.content, msg.artifact


def test_resoudre_jour():
    ref = date(2026, 8, 28)
    assert resoudre_jour("hier", ref) == date(2026, 8, 27)
    assert resoudre_jour("26/08", ref) == date(2026, 8, 26)
    assert resoudre_jour(None, ref) == ref
    assert resoudre_jour("avant-hier", ref) == date(2026, 8, 26)
    assert resoudre_jour("2026-08-01", ref) == date(2026, 8, 1)
    assert resoudre_jour("01/02/2025", ref) == date(2025, 2, 1)
    with pytest.raises(ValueError, match="Date non comprise"):
        resoudre_jour("demain", ref)


def test_resoudre_periode():
    assert resoudre_periode(None, date(2026, 8, 28)) == (date(2026, 8, 22), date(2026, 8, 28))
    assert resoudre_periode(1, date(2026, 8, 28)) == (date(2026, 8, 28), date(2026, 8, 28))


def test_situation_mentionne_date(divatex):
    txt, art = appeler(situation_atelier, {})
    assert "Données au 28/08/2026" in txt and art["kind"] == "kpi_tiles"
    assert art["date_reference"] == "28/08/2026" and art["tuiles"]


def test_production_chaines(divatex):
    txt, art = appeler(production_chaines, {})
    assert art["kind"] == "chart" and art["chart_type"] == "bar"
    assert [s["name"] for s in art["series"]] == ["Sortie", "Objectif"]
    assert "CH1-L2" in [p["x"] for p in art["series"][0]["data"]]
    assert txt.endswith("(Données au 28/08/2026)")


def test_rendement_estime(divatex):
    txt, art = appeler(detail_chaine, {"chaine": "CH1-L2"})
    assert "(estimé)" in txt and art["chart_type"] == "line"


def test_chaine_inconnue(divatex):
    txt, art = appeler(detail_chaine, {"chaine": "ZZ"})
    assert txt.startswith("Chaîne inconnue") and art is None


def test_rechercher_of(divatex):
    txt, art = appeler(rechercher_of, {"texte": "91294"})
    assert art["kind"] == "table" and len(art["lignes"]) == 2
    assert txt.endswith("(Données au 28/08/2026)")


def test_avancement_of_unique(divatex):
    _, art = appeler(avancement_of, {"of": "90253"})
    assert art["kind"] == "phase_pipeline" and art["of"]["quantite"] == 300


def test_avancement_of_ambigu_liste(divatex):
    _, art = appeler(avancement_of, {"of": "91294"})
    assert art["kind"] == "table" and len(art["lignes"]) == 2


def test_rendement_operateurs(divatex):
    txt, art = appeler(rendement_operateurs, {"chaine": "CH1-L2"})
    assert art["kind"] == "table" and art["chart"]["kind"] == "chart"
    assert len(art["lignes"]) == 3 and "(estimé)" in txt


def test_rendement_operateurs_periode_trop_longue(divatex):
    txt, art = appeler(rendement_operateurs, {"periode_jours": 60})
    assert "31 jours" in txt and art is None


def test_fiche_operateur_homonymes(divatex):
    _, art = appeler(fiche_operateur, {"operateur": "ben salah"})
    assert art["kind"] == "table"


def test_fiche_operateur_unique(divatex):
    txt, art = appeler(fiche_operateur, {"operateur": "ben salah mohamed"})
    assert art["kind"] == "kpi_tiles" and "Données au" in txt


def test_commandes(divatex):
    txt, art = appeler(commandes_a_risque, {})
    assert txt.endswith("(Données au 28/08/2026)")
    txt, art = appeler(consulter_commande, {"numero": "inexistante"})
    assert art is None and "Données au" in txt


def test_divatex_down(monkeypatch):
    set_engine(create_engine("mysql+pymysql://x:y@127.0.0.1:1/n"))
    assert appeler(situation_atelier, {})[0] == "Base DIVA injoignable."


def test_aucun_outil_a_confirmation():
    assert all("confirmation" not in t.args for t in TUNICOTEX_TOOLS)


def test_catalogue():
    assert {c["categorie"] for c in TOOL_CATALOG} == {
        "Documents", "Atelier Tunicotex", "Notifications", "Planification d'envois", "Navigation",
    }


def test_pages_et_carte_outils():
    assert set(PAGES) == {"atelier", "chaines", "of", "operateurs", "commandes",
                          "rapports", "documents"}
    assert {t.name for t in TUNICOTEX_TOOLS} == set(TOOL_PAGE_MAP)
    assert set(TOOL_PAGE_MAP.values()) <= set(PAGES)
