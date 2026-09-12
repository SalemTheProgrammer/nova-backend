"""Ingestion Sparkplug B : registre, compteurs, transitions, accusés, séquences."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import DowntimeEvent, Machine, OrdreFabrication, QualityEvent
from app.models.enums import CauseArret, StatutMachine, StatutOF
from app.models.sparkplug import SparkplugDevice
from app.protocols.sparkplug_b.contract import (
    BD_SEQ,
    CMD_LAST_ID,
    CMD_LAST_RESULT,
    METRIC_FAULT_CAUSE,
    METRIC_GOOD_COUNT,
    METRIC_REJECT_CAUSE,
    METRIC_REJECT_COUNT,
    METRIC_STATE,
    CommandAction,
    format_rejet,
)
from app.protocols.sparkplug_b.ingestion import SparkplugIngestor
from app.services.machine_command_service import CommandRegistry
from tests.sparkplug_fixtures import GROUPE, EDGE, creer_base, i, naissance, s, topic, trame


@pytest.fixture
def env(tmp_path):
    base = creer_base(tmp_path)
    rebirths: list[tuple[str, str]] = []
    diffusees: list[int] = []
    registre = CommandRegistry()
    ingestor = SparkplugIngestor(
        broker_url="mqtt://test:1883",
        session_factory=base.factory,
        commandes=registre,
        rebirth_requester=lambda g, e: rebirths.append((g, e)),
        on_machines_modifiees=diffusees.extend,
        on_evenement=lambda _msg: None,
        intervalle_rebirth_s=0,
    )
    return base, ingestor, registre, rebirths, diffusees


def _machine(base) -> Machine:
    with base.factory() as db:
        return db.get(Machine, base.machine_id)


def test_naissance_enregistre_rattache_et_sert_de_reference(env) -> None:
    base, ingestor, _, _, _ = env
    naissance(ingestor, bonnes=500, rejets=7)

    with base.factory() as db:
        device = db.execute(select(SparkplugDevice)).scalar_one()
        assert device.machine_id == base.machine_id  # device_id == code machine
        assert device.online and device.broker_url == "mqtt://test:1883"
        assert {m.tag_name for m in device.mappings} >= {METRIC_STATE, METRIC_GOOD_COUNT}
        # Les totaux de naissance sont une référence : rien n'est compté.
        assert db.execute(select(QualityEvent)).first() is None
    assert _machine(base).quantite_produite == 0


def test_commande_acceptee_applique_le_contexte_et_libere_l_appelant(env) -> None:
    base, ingestor, registre, _, diffusees = env
    seq = naissance(ingestor)
    commande = registre.enregistrer(
        base.machine_id, CommandAction.START, {"ordre_fabrication_id": base.of_id}
    )

    ingestor.handle(
        topic("DDATA"),
        trame(seq + 1, s(METRIC_STATE, "MARCHE"), s(CMD_LAST_ID, commande.id), s(CMD_LAST_RESULT, "ACCEPTED")),
    )

    assert commande.terminee.is_set() and commande.acceptee is True
    machine = _machine(base)
    assert machine.statut == StatutMachine.MARCHE
    assert machine.ordre_fabrication_id == base.of_id
    with base.factory() as db:
        assert db.get(OrdreFabrication, base.of_id).statut == StatutOF.EN_COURS
    assert base.machine_id in diffusees


def test_compteurs_en_delta_et_regression(env) -> None:
    base, ingestor, registre, _, _ = env
    seq = naissance(ingestor, bonnes=100, rejets=0)
    commande = registre.enregistrer(
        base.machine_id, CommandAction.START, {"ordre_fabrication_id": base.of_id}
    )
    ingestor.handle(
        topic("DDATA"),
        trame(seq + 1, s(METRIC_STATE, "MARCHE"), s(CMD_LAST_ID, commande.id), s(CMD_LAST_RESULT, "ACCEPTED")),
    )
    ingestor.handle(
        topic("DDATA"),
        trame(seq + 2, i(METRIC_GOOD_COUNT, 110), i(METRIC_REJECT_COUNT, 2), s(METRIC_REJECT_CAUSE, "DEFAUT_VISUEL")),
    )
    # Régression (remise à zéro automate) : nouvelle référence, rien de compté.
    ingestor.handle(topic("DDATA"), trame(seq + 3, i(METRIC_GOOD_COUNT, 3)))
    ingestor.handle(topic("DDATA"), trame(seq + 4, i(METRIC_GOOD_COUNT, 5)))

    machine = _machine(base)
    assert machine.quantite_bonne == 12  # 10 + 2
    assert machine.quantite_rejetee == 2
    with base.factory() as db:
        of = db.get(OrdreFabrication, base.of_id)
        assert of.quantite_bonne == Decimal("12") and of.quantite_rejetee == Decimal("2")
        causes = {e.cause for e in db.execute(select(QualityEvent)).scalars() if e.cause}
        assert causes == {"DEFAUT_VISUEL"} or {c.value for c in causes} == {"DEFAUT_VISUEL"}


def test_panne_signalee_ouvre_un_arret_avec_sa_cause(env) -> None:
    base, ingestor, _, _, _ = env
    seq = naissance(ingestor, etat="MARCHE")
    ingestor.handle(
        topic("DDATA"),
        trame(seq + 1, s(METRIC_STATE, "PANNE"), s(METRIC_FAULT_CAUSE, "PANNE_ELECTRIQUE")),
    )
    assert _machine(base).statut == StatutMachine.PANNE
    with base.factory() as db:
        arret = db.execute(select(DowntimeEvent).where(DowntimeEvent.end_time.is_(None))).scalar_one()
        assert arret.cause == CauseArret.PANNE_ELECTRIQUE


def test_commande_refusee_ne_change_rien(env) -> None:
    base, ingestor, registre, _, _ = env
    seq = naissance(ingestor)
    commande = registre.enregistrer(base.machine_id, CommandAction.START, {})
    ingestor.handle(
        topic("DDATA"),
        trame(seq + 1, s(CMD_LAST_ID, commande.id), s(CMD_LAST_RESULT, format_rejet("aucun OF chargé"))),
    )
    assert commande.terminee.is_set()
    assert commande.acceptee is False and commande.raison == "aucun OF chargé"
    assert _machine(base).statut == StatutMachine.ARRET


def test_rupture_de_sequence_et_donnee_sans_naissance_demandent_une_renaissance(env) -> None:
    _, ingestor, _, rebirths, _ = env
    ingestor.handle(topic("DDATA"), trame(5, i(METRIC_GOOD_COUNT, 1)))  # aucun NBIRTH vu
    assert rebirths == [(GROUPE, EDGE)]

    seq = naissance(ingestor)
    ingestor.handle(topic("DDATA"), trame(seq + 3, i(METRIC_GOOD_COUNT, 1)))  # trou
    assert rebirths == [(GROUPE, EDGE), (GROUPE, EDGE)]


def test_ndeath_obsolete_ignoree_puis_ndeath_valide(env) -> None:
    base, ingestor, _, _, _ = env
    naissance(ingestor, bd_seq=4)
    ingestor.handle(topic("NDEATH"), trame(None, i(BD_SEQ, 3)))  # session précédente
    with base.factory() as db:
        assert db.execute(select(SparkplugDevice)).scalar_one().online is True

    ingestor.handle(topic("NDEATH"), trame(None, i(BD_SEQ, 4)))
    with base.factory() as db:
        assert db.execute(select(SparkplugDevice)).scalar_one().online is False
