"""Temps d'arrêt cumulé : il ne doit croître que pour une machine qui devait produire.

Régressions constatées en prod : une machine au repos (sans OF) cumulait du temps
d'arrêt indéfiniment, et le tableau de bord ignorait un arrêt ouvert dès qu'il
avait plus de 8 h d'ancienneté.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - enregistre toutes les tables sur Base.metadata
from app.db.base import Base
from app.models import (
    Article,
    DowntimeEvent,
    LigneProduction,
    Machine,
    Nomenclature,
    OrdreFabrication,
)
from app.models.enums import CauseArret, StatutMachine, StatutOF, TypeEvenementMachine, Unite
from app.services import dashboard_service, event_service


class ArretsCumulTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        article = Article(code="TEST", designation="Test", unite=Unite.UN)
        ligne = LigneProduction(code="L-TEST", designation="Ligne de test")
        self.db.add_all([article, ligne])
        self.db.flush()
        nomenclature = Nomenclature(article_id=article.id, version=1)
        self.db.add(nomenclature)
        self.db.flush()
        self.ordre = OrdreFabrication(
            numero="OF-TEST",
            article_id=article.id,
            nomenclature_id=nomenclature.id,
            ligne_production_id=ligne.id,
            quantite_planifiee=Decimal("50"),
            quantite_bonne=Decimal("49"),
            quantite_rejetee=Decimal("0"),
            unite=Unite.UN,
            statut=StatutOF.EN_COURS,
        )
        self.db.add(self.ordre)
        self.db.flush()
        self.machine = Machine(
            code="M-TEST",
            nom="Machine de test",
            ligne_production_id=ligne.id,
            statut=StatutMachine.MARCHE,
            quantite_produite=49,
            quantite_bonne=49,
            quantite_rejetee=0,
        )
        self.db.add(self.machine)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _arrets_ouverts(self) -> list[DowntimeEvent]:
        return list(
            self.db.scalars(select(DowntimeEvent).where(DowntimeEvent.end_time.is_(None)))
        )

    def _arreter(self) -> None:
        event_service.enregistrer_evenement(
            self.db, machine=self.machine, type_evenement=TypeEvenementMachine.MACHINE_STOPPED
        )

    def test_machine_sans_of_arretee_n_ouvre_pas_d_arret(self) -> None:
        self._arreter()

        self.assertEqual(self.machine.statut, StatutMachine.ARRET)
        self.assertEqual(self._arrets_ouverts(), [])

    def test_machine_avec_of_arretee_ouvre_un_arret(self) -> None:
        self.machine.ordre_fabrication_id = self.ordre.id
        self._arreter()

        (arret,) = self._arrets_ouverts()
        self.assertEqual(arret.cause, CauseArret.AUTRE)
        self.assertEqual(arret.ordre_fabrication_id, self.ordre.id)

    def test_of_solde_ferme_l_arret_encore_ouvert(self) -> None:
        self.machine.ordre_fabrication_id = self.ordre.id
        self._arreter()
        self.assertEqual(len(self._arrets_ouverts()), 1)

        # Dernière unité : l'OF atteint sa quantité planifiée et se détache.
        event_service.enregistrer_evenement(
            self.db,
            machine=self.machine,
            type_evenement=TypeEvenementMachine.GOOD_UNIT_PRODUCED,
            payload={"quantite": 1},
        )

        self.assertEqual(self.ordre.statut, StatutOF.TERMINE)
        self.assertIsNone(self.machine.ordre_fabrication_id)
        self.assertEqual(self._arrets_ouverts(), [])

    def test_resume_borne_chaque_arret_a_la_fenetre(self) -> None:
        maintenant = datetime.utcnow()
        self.db.add_all(
            [
                # Ouvert depuis 20 h : il chevauche la fenêtre de 8 h, il doit
                # compter (il était ignoré), mais pour 8 h au plus.
                DowntimeEvent(
                    machine_id=self.machine.id,
                    cause=CauseArret.AUTRE,
                    start_time=maintenant - timedelta(hours=20),
                ),
                # Terminé avant la fenêtre : hors calcul.
                DowntimeEvent(
                    machine_id=self.machine.id,
                    cause=CauseArret.PANNE_MECANIQUE,
                    start_time=maintenant - timedelta(hours=30),
                    end_time=maintenant - timedelta(hours=25),
                ),
            ]
        )
        self.db.flush()

        resume = dashboard_service.construire_resume(self.db)

        huit_heures = Decimal(8 * 3600)
        self.assertGreater(resume.temps_arret_total_s, huit_heures - 60)
        self.assertLessEqual(resume.temps_arret_total_s, huit_heures)
        self.assertEqual([c.cause for c in resume.top_causes_arret], ["AUTRE"])
        self.assertGreaterEqual(resume.mtbf_s, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
