"""OF à risque (livraison) et consultation de commandes."""
from __future__ import annotations

from datetime import date

from app.integrations.divatex.commandes import consulter_commande, of_a_risque
from app.integrations.divatex.production import situation_atelier
from tests.divatex_fixtures import (
    NOM_OF_90253,
    NOM_OF_91294_1,
    REF_DATE,
    divatex,  # noqa: F401
)


def test_retard_et_risque(divatex):
    r = {x.of.numero: x.statut for x in of_a_risque(date(2026, 8, 28))}
    assert (
        r["91294_3879712_AF599-10_LCS4G-1"] == "en_retard"
        and r["90253_840534_METAVERSE-SET-G_HB001-1"] == "a_risque"
        and "88000_X_DONE" not in r
    )


def test_ordre_jours_et_filtre_client(divatex):
    res = of_a_risque(REF_DATE)
    assert [x.statut for x in res] == ["en_retard", "en_retard", "a_risque"]
    assert res[0].jours == 2 and res[2].jours == 5 and res[2].of.numero == NOM_OF_90253
    assert [x.of.numero for x in of_a_risque(REF_DATE, client_id=2)] == [NOM_OF_90253]


def test_horizon_exclut_les_exports_lointains(divatex):
    assert [x.statut for x in of_a_risque(REF_DATE, horizon_jours=3)] == ["en_retard"] * 2


def test_fenetre_retard_limitee_a_7_jours(divatex):
    assert of_a_risque(date(2026, 9, 10)) == []


def test_of_termine_en_phase_finale_non_a_risque(divatex):
    for x in of_a_risque(REF_DATE):
        assert x.of.avancement_final < 1


def test_situation_compte_retards(divatex):
    assert situation_atelier(date(2026, 8, 28)).nb_of_en_retard == 2


def test_consulter_commande(divatex):
    (c,) = consulter_commande("CMD-91294")
    assert c.client == "LC WAIKIKI" and c.quantite == 300 and c.a_risque
    assert c.date_fin_visee.isoformat() == "2026-08-26"
    assert [o.numero for o in c.ofs][0] == NOM_OF_91294_1 and len(c.ofs) == 2


def test_consulter_commande_prefixe_et_inconnue(divatex):
    assert len(consulter_commande("CMD-")) == 2
    assert consulter_commande("ZZZ") == []
