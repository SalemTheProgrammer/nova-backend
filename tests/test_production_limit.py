"""Regression tests for OF planned-quantity enforcement."""
from __future__ import annotations

import unittest
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models  # noqa: F401 - register every table on Base.metadata
from app.db.base import Base
from app.models import (
    Article,
    LigneProduction,
    Machine,
    MachineEvent,
    Nomenclature,
    OrdreFabrication,
    QualityEvent,
)
from app.models.enums import (
    StatutMachine,
    StatutOF,
    TypeEvenementMachine,
    Unite,
)
from app.services import event_service


class ProductionLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        article = Article(code="TEST", designation="Test", unite=Unite.UN)
        ligne = LigneProduction(code="L-TEST", designation="Test line")
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
            nom="Test machine",
            ligne_production_id=ligne.id,
            ordre_fabrication_id=self.ordre.id,
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

    def test_last_tick_is_capped_and_completes_order(self) -> None:
        event = event_service.enregistrer_evenement(
            self.db,
            machine=self.machine,
            type_evenement=TypeEvenementMachine.GOOD_UNIT_PRODUCED,
            payload={"quantite": 5},
        )

        self.assertEqual(event.payload["quantite"], 1)
        self.assertEqual(event.ordre_fabrication_id, self.ordre.id)
        self.assertEqual(self.ordre.quantite_bonne, Decimal("50"))
        self.assertEqual(self.ordre.quantite_rejetee, Decimal("0"))
        self.assertEqual(self.ordre.statut, StatutOF.TERMINE)
        self.assertIsNotNone(self.ordre.date_fin_reelle)
        self.assertEqual(self.machine.quantite_produite, 50)
        self.assertEqual(self.machine.statut, StatutMachine.ARRET)
        self.assertIsNone(self.machine.ordre_fabrication_id)
        self.assertEqual(len(self.db.scalars(select(QualityEvent)).all()), 1)

    def test_full_legacy_order_accepts_no_more_units(self) -> None:
        self.ordre.quantite_bonne = Decimal("50")
        before_machine_total = self.machine.quantite_produite

        event = event_service.enregistrer_evenement(
            self.db,
            machine=self.machine,
            type_evenement=TypeEvenementMachine.GOOD_UNIT_PRODUCED,
            payload={"quantite": 3},
        )

        self.assertEqual(event.payload["quantite"], 0)
        self.assertEqual(self.ordre.quantite_bonne, Decimal("50"))
        self.assertEqual(self.machine.quantite_produite, before_machine_total)
        self.assertEqual(self.ordre.statut, StatutOF.TERMINE)
        self.assertEqual(self.machine.statut, StatutMachine.ARRET)
        self.assertIsNone(self.machine.ordre_fabrication_id)
        self.assertEqual(len(self.db.scalars(select(QualityEvent)).all()), 0)
        self.assertEqual(len(self.db.scalars(select(MachineEvent)).all()), 1)


if __name__ == "__main__":
    unittest.main()
