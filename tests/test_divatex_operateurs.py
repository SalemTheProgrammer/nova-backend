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


ID_EMP_TRICOT = 301
ID_EMP_MIXTE = 302


def _scans_tricot(engine, emp_id: int, jour: str, nb: int, temps: float = 100) -> None:
    from tests.divatex_fixtures import ID_LIGNE_A3, ID_OF_90253

    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO employe (IDEmploye, Matricule, Nom, Prenom, IDChaineMontage, "
                "IDPosteTravail, NumCIN, NumCNSS) VALUES (:e, :m, 'TRICOT', 'Test', :l, 1, "
                "'0', '0') ON CONFLICT(IDEmploye) DO NOTHING"
            ),
            {"e": emp_id, "m": f"M{emp_id}", "l": ID_LIGNE_A3},
        )
        for k in range(nb):
            c.execute(
                text(
                    "INSERT INTO lectures (Date, SaisiLe, IDChaineMontage, IDEmploye, "
                    "IDOFabrication, IDOperation, IdPhase, Quantite, Temps, Majoration, "
                    "LibTaille) VALUES (:d, :s, :l, :e, :of, 1, 0, 1, :t, 1, 'M')"
                ),
                {"d": jour, "s": f"{jour} 1{k % 10}:00:00", "l": ID_LIGNE_A3, "e": emp_id,
                 "of": ID_OF_90253, "t": temps},
            )


def test_tricotage_seul_rendement_none_et_dernier(divatex):
    _scans_tricot(divatex, ID_EMP_TRICOT, REF_DATE.isoformat(), 5)
    r = classement_operateurs(REF_DATE, REF_DATE)
    tricot = next(x for x in r if x.operateur.id == ID_EMP_TRICOT)
    assert tricot.rendement is None
    assert tricot.minutes_produites == 0 and tricot.minutes_presence == 0
    assert tricot.presence_estimee is False
    assert tricot.pieces_operations == 5
    assert tricot.ligne == "A3"
    assert r[-1].operateur.id == ID_EMP_TRICOT
    assert all(x.rendement is not None for x in r[:-1])


def test_mixte_seules_les_minutes_confection_comptent(divatex):
    from tests.divatex_fixtures import ID_OF_91294_1

    # jour 27 : scans tricotage ; jour 28 : 10 scans confection x 20 min + tricotage
    _scans_tricot(divatex, ID_EMP_MIXTE, "2026-08-27", 3)
    _scans_tricot(divatex, ID_EMP_MIXTE, REF_DATE.isoformat(), 2)
    with divatex.begin() as c:
        for k in range(10):
            c.execute(
                text(
                    "INSERT INTO lectures (Date, SaisiLe, IDChaineMontage, IDEmploye, "
                    "IDOFabrication, IDOperation, IdPhase, Quantite, Temps, Majoration, "
                    "LibTaille) VALUES (:d, :s, :l, :e, :of, 1, 0, 1, 20, 1, 'M')"
                ),
                {"d": REF_DATE.isoformat(), "s": f"{REF_DATE.isoformat()} 08:{k:02d}:00",
                 "l": ID_LIGNE_CH1_L2, "e": ID_EMP_MIXTE, "of": ID_OF_91294_1},
            )
    du = date(2026, 8, 27)
    r = {x.operateur.id: x for x in classement_operateurs(du, REF_DATE)}[ID_EMP_MIXTE]
    assert r.minutes_produites == pytest.approx(200)
    assert r.minutes_presence == 480  # un seul jour avec scan confection
    assert r.rendement == pytest.approx(200 / 480)
    assert r.pieces_operations == 15
    assert r.ligne == "CH1-L2"  # ligne principale = plus de scans, inchangée

    f = fiche_operateur(ID_EMP_MIXTE, du, REF_DATE)
    assert f.par_jour == [(du, None), (REF_DATE, pytest.approx(200 / 480))]
    assert f.total.rendement == pytest.approx(200 / 480)
    assert sum(q for _, q in f.operations) == 15


def test_fiche_tricotage_seul(divatex):
    _scans_tricot(divatex, ID_EMP_TRICOT, REF_DATE.isoformat(), 4)
    f = fiche_operateur(ID_EMP_TRICOT, REF_DATE, REF_DATE)
    assert f.par_jour == [(REF_DATE, None)]
    assert f.total.rendement is None and f.total.minutes_produites == 0
    assert f.total.pieces_operations == 4
