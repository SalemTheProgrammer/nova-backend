"""KPI (NF E 60-182) : chaque règle de calcul, vérifiée indépendamment du reste.

Régressions constatées en prod avant correctif :
- une qualité (TQ) de 32,8 % sans rebut significatif, et DO × TP × TQ différent
  du TRS affiché : le TRS de l'usine était la MOYENNE des ratios des machines ;
- un prélèvement qualité (arrêt planifié) faisait baisser le TRS ;
- un MTBF gonflé : toutes les machines × la fenêtre, machines au repos comprises.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - enregistre toutes les tables sur Base.metadata
from app.db.base import Base
from app.models import Article, DowntimeEvent, LigneProduction, Machine, MachineEvent, QualityEvent
from app.models.enums import (
    CauseArret,
    StatutMachine,
    TypeEvenementMachine,
    TypeEvenementQualite,
    Unite,
)
from app.services import dashboard_service, trs_service

H = timedelta(hours=1)
FIN = datetime(2026, 9, 13, 16, 0)
DEBUT = FIN - 8 * H  # fenêtre de 8 h


def arret(cause: CauseArret, debut: datetime, fin: datetime | None):
    return SimpleNamespace(cause=cause, start_time=debut, end_time=fin)


def pieces(bonnes: int, rebuts: int = 0) -> list:
    return [
        SimpleNamespace(type=TypeEvenementQualite.BONNE, quantite=bonnes),
        SimpleNamespace(type=TypeEvenementQualite.REBUT, quantite=rebuts),
    ]


def calculer(downtimes: list, quality: list, cycle: str = "10") -> trs_service.TRSResult:
    return trs_service._calculer(
        depuis=DEBUT,
        jusqua=FIN,
        downtimes=downtimes,
        quality_events=quality,
        cycle_cible_s=Decimal(cycle),
        taux_charge=Decimal("1.0"),
        taux_engagement=Decimal("1.0"),
    )


class ModeleDeTempsTest(unittest.TestCase):
    def test_identite_trs_egal_tu_sur_tr(self) -> None:
        # 1 h de panne ; 2000 pièces dont 100 rebuts, cycle 10 s.
        r = calculer([arret(CauseArret.PANNE_MECANIQUE, DEBUT, DEBUT + H)], pieces(1900, 100))
        self.assertEqual(r.temps.tr, Decimal(8 * 3600))
        self.assertEqual(r.temps.tf, Decimal(7 * 3600))
        self.assertEqual(r.temps.tn, Decimal(2000 * 10))
        self.assertEqual(r.temps.tu, Decimal(1900 * 10))
        self.assertAlmostEqual(float(r.trs), float(r.temps.tu / r.temps.tr), places=9)
        self.assertAlmostEqual(float(r.trs), float(r.do * r.tp * r.tq), places=9)

    def test_arret_planifie_ne_penalise_pas_le_trs_mais_le_trg(self) -> None:
        sans = calculer([], pieces(2000))
        # 2 h de prélèvement qualité (planifié) et la même production.
        avec = calculer([arret(CauseArret.PRELEVEMENT_QUALITE, DEBUT, DEBUT + 2 * H)], pieces(2000))

        self.assertEqual(avec.temps.tr, Decimal(6 * 3600))  # retiré de TR
        self.assertEqual(avec.do, Decimal("1"))  # pas une perte de disponibilité
        self.assertGreater(avec.trs, sans.trs)  # même production sur moins de temps requis
        self.assertAlmostEqual(float(avec.trg), float(avec.temps.tu / avec.temps.to), places=9)
        self.assertLess(avec.trg, avec.trs)

    def test_micro_arret_est_une_perte_de_performance(self) -> None:
        r = calculer([arret(CauseArret.MICRO_ARRET, DEBUT, DEBUT + H)], pieces(2000))
        self.assertEqual(r.do, Decimal("1"))
        self.assertEqual(r.temps.tf, Decimal(8 * 3600))
        self.assertLess(r.tp, Decimal("1"))

    def test_arret_ouvert_borne_a_la_fenetre(self) -> None:
        # Ouvert depuis 20 h : il pèse 8 h dans la fenêtre, pas 20 et pas 0.
        r = calculer([arret(CauseArret.AUTRE, FIN - 20 * H, None)], pieces(0))
        self.assertEqual(r.temps.tf, Decimal("0"))
        self.assertEqual(r.do, Decimal("0"))


class AgregationUsineTest(unittest.TestCase):
    """Le TRS d'un ensemble de machines se calcule sur la SOMME de leurs temps."""

    def setUp(self) -> None:
        self._origine = trs_service.calculer_trs_machine

    def tearDown(self) -> None:
        trs_service.calculer_trs_machine = self._origine

    def _usine(self, resultats: dict[str, trs_service.TRSResult], machines: list):
        trs_service.calculer_trs_machine = lambda db, m, **_: resultats[m.code]
        return trs_service.calculer_trs_ligne(None, machines, depuis=DEBUT, jusqua=FIN)

    @staticmethod
    def _machine(code: str):
        ligne = SimpleNamespace(taux_engagement=Decimal("1.0"))
        return SimpleNamespace(
            code=code, temps_cycle_cible_s=Decimal("10"), statut=StatutMachine.ARRET,
            ordre_fabrication_id=None, ligne_production=ligne,
        )

    def test_une_machine_sans_production_ne_fausse_pas_la_qualite(self) -> None:
        productive = calculer([], pieces(2000))  # 0 rebut : TQ = 100 %
        au_repos = calculer([], pieces(0))  # rien produit : TQ = 0 par convention
        r = self._usine({"M-A": productive, "M-B": au_repos}, [self._machine("M-A"), self._machine("M-B")])

        self.assertEqual(r.tq, Decimal("1"))  # la moyenne des ratios donnait 50 %

    def test_do_tp_tq_redonnent_le_trs_de_l_usine(self) -> None:
        a = calculer([arret(CauseArret.PANNE_MECANIQUE, DEBUT, DEBUT + 3 * H)], pieces(1500, 300))
        b = calculer([arret(CauseArret.PANNE_ELECTRIQUE, DEBUT, DEBUT + H)], pieces(2500, 20))
        r = self._usine({"M-A": a, "M-B": b}, [self._machine("M-A"), self._machine("M-B")])

        self.assertAlmostEqual(float(r.trs), float(r.do * r.tp * r.tq), places=9)
        self.assertAlmostEqual(float(r.trs), float(r.temps.tu / r.temps.tr), places=9)


class FiabiliteTableauDeBordTest(unittest.TestCase):
    """MTBF = MTTF + MTTR ; un arrêt planifié n'est pas une défaillance."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        ligne = LigneProduction(code="L-TEST", designation="Ligne de test")
        self.db.add_all([ligne, Article(code="ART", designation="Article", unite=Unite.UN)])
        self.db.flush()
        self.machine = Machine(
            code="M-TEST", nom="Machine", ligne_production_id=ligne.id,
            statut=StatutMachine.MARCHE, temps_cycle_cible_s=Decimal("10"),
        )
        self.db.add(self.machine)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_mtbf_egal_mttf_plus_mttr_et_planifies_exclus(self) -> None:
        maintenant = datetime.utcnow()
        debut_poste = maintenant - 8 * H
        self.db.add_all(
            [
                MachineEvent(
                    machine_id=self.machine.id, type=TypeEvenementMachine.MACHINE_STARTED,
                    payload={}, created_at=debut_poste,
                ),
                # 2 pannes de 30 min et 1 h
                DowntimeEvent(machine_id=self.machine.id, cause=CauseArret.PANNE_MECANIQUE,
                              start_time=debut_poste + H, end_time=debut_poste + 1.5 * H),
                DowntimeEvent(machine_id=self.machine.id, cause=CauseArret.PANNE_ELECTRIQUE,
                              start_time=debut_poste + 3 * H, end_time=debut_poste + 4 * H),
                # 1 prélèvement planifié : PAS une défaillance
                DowntimeEvent(machine_id=self.machine.id, cause=CauseArret.PRELEVEMENT_QUALITE,
                              start_time=debut_poste + 5 * H, end_time=debut_poste + 6 * H),
                QualityEvent(machine_id=self.machine.id, type=TypeEvenementQualite.BONNE,
                             quantite=1000, created_at=debut_poste + 7 * H),
            ]
        )
        self.db.flush()

        r = dashboard_service.construire_resume(self.db)

        self.assertEqual(r.nb_pannes, 2)
        self.assertAlmostEqual(float(r.mttr_s), 45 * 60, delta=1)  # (30 + 60) / 2
        self.assertEqual(r.mtbf_s, r.mttf_s + r.mttr_s)
        # MTTF = TF / pannes, TF = 8 h − 1 h planifiée − 1 h 30 de pannes = 5 h 30
        self.assertAlmostEqual(float(r.mttf_s), 5.5 * 3600 / 2, delta=60)

    def test_pas_de_cible_inventee_sans_of(self) -> None:
        r = dashboard_service.construire_resume(self.db)
        self.assertEqual(r.production_cible, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
