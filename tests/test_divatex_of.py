"""Recherche et avancement des OF."""
from __future__ import annotations

import pytest

from app.integrations.divatex.of import avancement_of, of_par_id, rechercher_of
from tests.divatex_fixtures import (
    ID_OF_90253,
    ID_OF_91294_1,
    ID_OF_91294_2,
    ID_OF_DONE,
    NOM_OF_91294_1,
    divatex,  # noqa: F401
)


def test_rechercher_of_prefixe_ambigu(divatex):
    assert len(rechercher_of("91294")) == 2


def test_rechercher_of_exact(divatex):
    assert [o.numero for o in rechercher_of(NOM_OF_91294_1)][0] == NOM_OF_91294_1


def test_rechercher_of_exact_en_premier_parmi_plusieurs(divatex):
    assert rechercher_of("91294_3879712_AF599-10_LCS4G-2")[0].id == ID_OF_91294_2


def test_rechercher_of_par_article_po_et_commande_client(divatex):
    assert {o.id for o in rechercher_of("AF599")} == {ID_OF_91294_1, ID_OF_91294_2}
    assert [o.id for o in rechercher_of("PO-90253")] == [ID_OF_90253]
    assert [o.id for o in rechercher_of("NC-88000")] == [ID_OF_DONE]


def test_rechercher_of_ouverts_seulement(divatex):
    ids = {o.id for o in rechercher_of("", ouverts_seulement=True)}
    assert ID_OF_DONE not in ids and len(ids) == 3


def test_rechercher_of_filtre_client_et_limite(divatex):
    assert {o.id for o in rechercher_of("", client_id=2)} == {ID_OF_90253, ID_OF_DONE}
    assert len(rechercher_of("", limite=1)) == 1


def test_rechercher_of_joker_echappe(divatex):
    assert rechercher_of("%") == []


def test_resume_of(divatex):
    o = of_par_id(ID_OF_91294_1)
    assert o.client == "LC WAIKIKI" and o.article == "AF599-10"
    assert o.quantite == 200 and o.ouvert and o.date_export_prevue.isoformat() == "2026-08-26"
    assert o.avancement_final == pytest.approx(0.5)
    assert not of_par_id(ID_OF_DONE).ouvert
    assert of_par_id(ID_OF_90253).avancement_final == 0.0
    assert of_par_id(999999) is None


def test_avancement_phase_finale(divatex):
    a = avancement_of(ID_OF_91294_1)
    assert next(p for p in a.phases if p.phase_id == 12).pct == pytest.approx(0.5)


def test_avancement_phases_ordonnees_et_vides_listees(divatex):
    a = avancement_of(ID_OF_91294_1)
    assert [p.phase_id for p in a.phases] == [1, 4, 5, 11, 12]
    assert next(p for p in a.phases if p.phase_id == 4).quantite == 0
    assert next(p for p in a.phases if p.phase_id == 5).nom == "SORTIE CONFECTION"
    assert next(p for p in a.phases if p.phase_id == 5).quantite == 16  # 15 le 28 + 1 la veille


def test_avancement_tailles_lignes_derniere_lecture(divatex):
    a = avancement_of(ID_OF_91294_1)
    assert a.tailles == [("M", {5: 16, 12: 100})]
    assert a.lignes == ["CH1-L2"]
    assert a.derniere_lecture.isoformat() == "2026-08-28"


def test_avancement_of_sans_lecture(divatex):
    a = avancement_of(ID_OF_DONE)
    assert a.derniere_lecture is None and a.tailles == [] and a.lignes == []
    assert all(p.quantite == 0 for p in a.phases)


def test_avancement_of_inconnu(divatex):
    assert avancement_of(999999) is None
