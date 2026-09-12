"""Outils de test Sparkplug : base SQLite jetable, messages, faux automate."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import models  # noqa: F401 — enregistre toutes les tables
from app.db.base import Base
from app.models import Article, LigneProduction, Machine, Nomenclature, OrdreFabrication
from app.models.enums import StatutMachine, StatutOF, Unite
from app.protocols.sparkplug_b import codec
from app.protocols.sparkplug_b.codec import DataType, Metric, Payload
from app.protocols.sparkplug_b.contract import (
    BD_SEQ,
    CMD_ACTION,
    CMD_ID,
    CMD_LAST_ID,
    CMD_LAST_RESULT,
    METRIC_FAULT_CAUSE,
    METRIC_GOOD_COUNT,
    METRIC_REJECT_CAUSE,
    METRIC_REJECT_COUNT,
    METRIC_STATE,
    RESULT_ACCEPTED,
)
from app.protocols.sparkplug_b.topic import build_topic

GROUPE = "TestPlant"
EDGE = "LIGNE-T"
DEVICE = "M-T1"


@dataclass
class BaseTest:
    factory: object  # () -> context manager de Session
    machine_id: int
    of_id: int
    ligne_id: int


def creer_base(tmp_path: Path) -> BaseTest:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'nova_test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    fabrique = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @contextmanager
    def session() -> Iterator[Session]:
        db = fabrique()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    with session() as db:
        article = Article(code="ART-T", designation="Article test", unite=Unite.UN)
        ligne = LigneProduction(code=EDGE, designation="Ligne test")
        ligne.articles.append(article)
        db.add_all([article, ligne])
        db.flush()
        nomenclature = Nomenclature(article_id=article.id, version=1)
        db.add(nomenclature)
        db.flush()
        of = OrdreFabrication(
            numero="OF-T-0001",
            article_id=article.id,
            nomenclature_id=nomenclature.id,
            ligne_production_id=ligne.id,
            quantite_planifiee=Decimal("100"),
            unite=Unite.UN,
            statut=StatutOF.PLANIFIE,
        )
        machine = Machine(
            code=DEVICE,
            nom="Machine test",
            ligne_production_id=ligne.id,
            statut=StatutMachine.ARRET,
            temps_cycle_cible_s=Decimal("4"),
        )
        db.add_all([of, machine])
        db.flush()
        return BaseTest(factory=session, machine_id=machine.id, of_id=of.id, ligne_id=ligne.id)


def topic(message_type: str, device: str | None = DEVICE) -> str:
    return build_topic(GROUPE, message_type, EDGE, device if message_type.startswith("D") else None)


def trame(seq: int | None, *metrics: Metric) -> bytes:
    return codec.encode(Payload(metrics=list(metrics), seq=seq))


def s(nom: str, valeur: str) -> Metric:
    return Metric(nom, DataType.STRING, valeur)


def i(nom: str, valeur: int) -> Metric:
    return Metric(nom, DataType.INT64, valeur)


def naissance(ingestor, *, bd_seq: int = 0, etat: str = "ARRET", bonnes: int = 0, rejets: int = 0) -> int:
    """NBIRTH (seq 0) + DBIRTH (seq 1). Renvoie le dernier seq utilisé."""
    ingestor.handle(topic("NBIRTH"), trame(0, i(BD_SEQ, bd_seq)))
    ingestor.handle(
        topic("DBIRTH"),
        trame(
            1,
            s(METRIC_STATE, etat),
            s(METRIC_FAULT_CAUSE, ""),
            i(METRIC_GOOD_COUNT, bonnes),
            i(METRIC_REJECT_COUNT, rejets),
            s(METRIC_REJECT_CAUSE, "AUTRE"),
        ),
    )
    return 1


class FauxAutomate:
    """Faux edge node + broker pour tester la boucle commande → accusé → état.

    Chaque DCMD « publié » est traité dans un AUTRE thread (comme le vrai
    réseau), qui injecte dans l'ingestor un DDATA portant le nouvel état et
    l'accusé corrélé — exactement le chemin de production, sans broker.
    """

    ETATS = {
        "START": "MARCHE",
        "STOP": "ARRET",
        "PAUSE": "PAUSE",
        "RESET_FAULT": "MARCHE",
        "START_MAINTENANCE": "MAINTENANCE",
        "END_MAINTENANCE": "ARRET",
    }

    def __init__(self, ingestor, *, seq: int, reponse: str = RESULT_ACCEPTED, repondre: bool = True):
        self.ingestor = ingestor
        self.seq = seq
        self.reponse = reponse
        self.repondre = repondre
        self.connected = True
        self.commandes: list[dict] = []

    def publish_device_command(self, *, group_id, edge_node_id, device_id, metrics) -> None:
        valeurs = {m.name: m.value for m in metrics}
        self.commandes.append(valeurs)
        if self.repondre:
            threading.Thread(target=self._repondre, args=(device_id, valeurs), daemon=True).start()

    def _repondre(self, device_id: str, valeurs: dict) -> None:
        metrics = [s(CMD_LAST_ID, valeurs[CMD_ID]), s(CMD_LAST_RESULT, self.reponse)]
        if self.reponse == RESULT_ACCEPTED:
            metrics.insert(0, s(METRIC_STATE, self.ETATS[valeurs[CMD_ACTION]]))
        self.seq = (self.seq + 1) % 256
        self.ingestor.handle(topic("DDATA", device_id), trame(self.seq, *metrics))
