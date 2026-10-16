"""Référentiel DIVA : chaînes, groupes, phases, opérateurs."""
from __future__ import annotations

import pytest

from app.integrations.divatex.referentiel import (
    lister_lignes,
    lister_phases,
    nom_phase,
    normaliser_code,
    operateur,
    rechercher_operateurs,
    trouver_groupe,
    trouver_ligne,
)
from tests.divatex_fixtures import (
    ID_EMP_BEN_SALAH_AHMED,
    ID_GROUPE_CHAINE01,
    ID_GROUPE_REPASSAGE,
    ID_LIGNE_CH1_L1,
    ID_LIGNE_CH1_L2,
)


def test_nature_par_groupe(divatex):
    assert {l.code: l.nature for l in lister_lignes()} == {
        "A3": "tricotage", "CH1-L1": "confection", "CH1-L2": "confection", "ST1": "finition",
    }


@pytest.mark.parametrize("t", ["CH1-L2", "ch1 l2", "ch1-l2", "chaîne 1 ligne 2", "Ch1L2"])
def test_trouver_ligne_variantes(divatex, t):
    assert trouver_ligne(t).id == ID_LIGNE_CH1_L2


def test_trouver_ligne_inconnue(divatex):
    assert trouver_ligne("CH9-L9") is None


def test_trouver_ligne_groupe_rattache(divatex):
    ligne = trouver_ligne("ch1-l1")
    assert ligne.id == ID_LIGNE_CH1_L1
    assert ligne.groupe.id == ID_GROUPE_CHAINE01


def test_trouver_groupe(divatex):
    assert trouver_groupe("chaîne 01").id == ID_GROUPE_CHAINE01
    assert trouver_groupe("repassage").id == ID_GROUPE_REPASSAGE
    assert trouver_groupe("inconnu") is None


def test_normaliser_code():
    assert {normaliser_code(t) for t in ("chaîne 1 ligne 2", "ch1 l2", "CH1-L2")} == {"CH1L2"}


def test_phases(divatex):
    assert nom_phase(5) == "SORTIE CONFECTION"
    assert nom_phase(999) == "Phase 999"
    assert {p.id for p in lister_phases()} >= {1, 5, 12}


def test_rechercher_operateurs_homonymes(divatex):
    assert {o.prenom for o in rechercher_operateurs("ben salah")} == {"Ahmed", "Mohamed"}


def test_rechercher_operateur_par_matricule_et_nom_complet(divatex):
    assert [o.id for o in rechercher_operateurs("M101")] == [ID_EMP_BEN_SALAH_AHMED]
    assert [o.id for o in rechercher_operateurs("ahmed ben salah")] == [ID_EMP_BEN_SALAH_AHMED]
    assert rechercher_operateurs("") == []
    assert len(rechercher_operateurs("ben", limite=1)) == 1


def test_operateur(divatex):
    op = operateur(ID_EMP_BEN_SALAH_AHMED)
    assert op.nom_complet == "Ahmed BEN SALAH"
    assert op.ligne_id == ID_LIGNE_CH1_L1
    assert operateur(99999) is None


def test_employe_sans_donnees_personnelles(divatex, capture_sql):
    rechercher_operateurs("ben salah")
    operateur(ID_EMP_BEN_SALAH_AHMED)
    assert capture_sql
    assert not any(c in sql for sql in capture_sql for c in ("NumCIN", "NumCNSS", "*"))


def test_rechercher_operateurs_jokers_echappes(divatex):
    assert rechercher_operateurs("%") == []
    assert rechercher_operateurs("_") == []
