from datetime import date

import pytest
from sqlalchemy import text

from app.integrations.divatex.operateurs import (
    classement_operateurs,
    fiche_operateur,
)
from tests.divatex_fixtures import (
    ID_EMP_BEN_SALAH_MOHAMED,
    ID_EMPS_CH1_L2,
    ID_GROUPE_CHAINE01,
    ID_GROUPE_TRICOTAGE,
    ID_LIGNE_CH1_L2,
    REF_DATE,
    divatex,  # noqa: F401
)


def jours_travailles(engine, emp_id: int, du: date, au: date) -> int:
    with engine.connect() as c:
        return c.execute(
            text(
                "SELECT COUNT(DISTINCT Date) FROM lectures WHERE IDEmploye = :e "
                "AND IdPhase = 0 AND Date BETWEEN :du AND :au"
            ),
            {"e": emp_id, "du": du.isoformat(), "au": au.isoformat()},
        ).scalar()


def test_classement_trie(divatex):
    r = classement_operateurs(REF_DATE, REF_DATE, ligne_id=ID_LIGNE_CH1_L2)
    assert len(r) == 3
    valeurs = [x.rendement for x in r]
    assert valeurs == sorted(valeurs, reverse=True)
    # 10 scans x 20 min (facteur 1) = 200 min produites pour 480 min estimées
    assert r[0].minutes_produites == pytest.approx(10 * 20)
    assert r[0].pieces_operations == 10
    assert r[0].ligne == "CH1-L2"


def test_presence_estimee_par_jour_travaille(divatex):
    r = classement_operateurs(date(2026, 8, 27), REF_DATE, ligne_id=ID_LIGNE_CH1_L2)
    assert r
    for x in r:
        assert x.presence_estimee
        assert x.minutes_presence == 480 * jours_travailles(
            divatex, x.operateur.id, date(2026, 8, 27), REF_DATE
        )


def test_presence_reelle_prioritaire(divatex):
    with divatex.begin() as c:
        c.execute(
            text("INSERT INTO presence (Date, IDEmploye, minutes) VALUES (:d, :e, 400)"),
            {"d": REF_DATE.isoformat(), "e": ID_EMPS_CH1_L2[0]},
        )
    r = {
        x.operateur.id: x
        for x in classement_operateurs(REF_DATE, REF_DATE, ligne_id=ID_LIGNE_CH1_L2)
    }
    assert r[ID_EMPS_CH1_L2[0]].minutes_presence == 400
    assert not r[ID_EMPS_CH1_L2[0]].presence_estimee
    assert r[ID_EMPS_CH1_L2[1]].presence_estimee


def test_classement_groupe_et_sans_filtre(divatex):
    assert len(classement_operateurs(REF_DATE, REF_DATE, groupe_id=ID_GROUPE_CHAINE01)) == 3
    assert classement_operateurs(REF_DATE, REF_DATE, groupe_id=ID_GROUPE_TRICOTAGE) == []
    assert len(classement_operateurs(REF_DATE, REF_DATE)) == 3


def test_periode_trop_longue(divatex):
    with pytest.raises(ValueError, match="31 jours"):
        classement_operateurs(date(2026, 6, 1), date(2026, 8, 28))
    with pytest.raises(ValueError, match="31 jours"):
        fiche_operateur(ID_EMPS_CH1_L2[0], date(2026, 6, 1), date(2026, 8, 28))
    classement_operateurs(date(2026, 7, 29), REF_DATE)  # 31 jours exactement


def test_periode_invalide(divatex):
    with pytest.raises(ValueError, match="invalide"):
        classement_operateurs(REF_DATE, date(2026, 8, 1))


def test_fiche_operateur(divatex):
    f = fiche_operateur(ID_EMPS_CH1_L2[0], REF_DATE, REF_DATE)
    assert f.operations == [("ASSEMBLAGE COL", 10)]
    assert f.total.pieces_operations > 0
    assert f.par_jour == [(REF_DATE, pytest.approx(10 * 20 / 480))]


def test_fiche_inconnue_ou_sans_scan(divatex):
    assert fiche_operateur(99999, REF_DATE, REF_DATE) is None
    f = fiche_operateur(ID_EMP_BEN_SALAH_MOHAMED, REF_DATE, REF_DATE)
    assert f.operations == [] and f.par_jour == []
    assert f.total.rendement is None and f.total.ligne is None
