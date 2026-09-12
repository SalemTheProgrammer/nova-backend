"""Boucle complète commande machine → DCMD → accusé DDATA → état MES."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import FabricationError
from app.models import Machine, OrdreFabrication, ligne_article
from app.models.enums import StatutMachine, StatutOF
from app.models.sparkplug import SparkplugDevice
from app.protocols.sparkplug_b.contract import CMD_ORDER_NUMBER, CMD_TARGET_QUANTITY, format_rejet
from app.protocols.sparkplug_b.ingestion import SparkplugIngestor
from app.services import machine_command_service as commandes
from tests.sparkplug_fixtures import FauxAutomate, creer_base, naissance


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = creer_base(tmp_path)
    ingestor = SparkplugIngestor(
        broker_url="mqtt://test:1883",
        session_factory=base.factory,
        on_machines_modifiees=lambda _ids: None,
        on_evenement=lambda _msg: None,
        intervalle_rebirth_s=0,
    )
    seq = naissance(ingestor)
    monkeypatch.setattr(
        commandes, "get_settings", lambda: SimpleNamespace(machine_command_timeout_s=2.0)
    )
    yield base, ingestor, seq
    commandes.configurer()  # rétablit l'hôte réel et session_scope


def test_demarrer_attend_la_confirmation_de_l_automate(env) -> None:
    base, ingestor, seq = env
    automate = FauxAutomate(ingestor, seq=seq)
    commandes.configurer(publisher=automate, session_factory=base.factory)

    message = commandes.demarrer(base.machine_id, ordre_fabrication_id=base.of_id)

    assert "OF-T-0001" in message
    assert automate.commandes[0][CMD_ORDER_NUMBER] == "OF-T-0001"
    assert automate.commandes[0][CMD_TARGET_QUANTITY] == 100
    with base.factory() as db:
        assert db.get(Machine, base.machine_id).statut == StatutMachine.MARCHE
        assert db.get(OrdreFabrication, base.of_id).statut == StatutOF.EN_COURS

    commandes.arreter(base.machine_id)
    with base.factory() as db:
        assert db.get(Machine, base.machine_id).statut == StatutMachine.ARRET


def test_refus_de_l_automate(env) -> None:
    base, ingestor, seq = env
    automate = FauxAutomate(ingestor, seq=seq, reponse=format_rejet("porte de sécurité ouverte"))
    commandes.configurer(publisher=automate, session_factory=base.factory)

    with pytest.raises(commandes.MachineCommandError, match="porte de sécurité ouverte"):
        commandes.demarrer(base.machine_id, ordre_fabrication_id=base.of_id)
    with base.factory() as db:
        assert db.get(Machine, base.machine_id).statut == StatutMachine.ARRET


def test_absence_d_accuse(env, monkeypatch) -> None:
    base, ingestor, seq = env
    monkeypatch.setattr(
        commandes, "get_settings", lambda: SimpleNamespace(machine_command_timeout_s=0.2)
    )
    commandes.configurer(
        publisher=FauxAutomate(ingestor, seq=seq, repondre=False), session_factory=base.factory
    )
    with pytest.raises(commandes.MachineCommandTimeout):
        commandes.demarrer(base.machine_id, ordre_fabrication_id=base.of_id)
    assert len(commandes.registry) == 0  # commande expirée retirée du registre


def test_automate_hors_ligne_rien_n_est_publie(env) -> None:
    base, ingestor, seq = env
    automate = FauxAutomate(ingestor, seq=seq)
    commandes.configurer(publisher=automate, session_factory=base.factory)
    with base.factory() as db:
        db.query(SparkplugDevice).update({SparkplugDevice.online: False})

    with pytest.raises(commandes.MachineUnreachableError):
        commandes.demarrer(base.machine_id, ordre_fabrication_id=base.of_id)
    assert automate.commandes == []


def test_article_non_homologue_refuse_avant_envoi(env) -> None:
    base, ingestor, seq = env
    automate = FauxAutomate(ingestor, seq=seq)
    commandes.configurer(publisher=automate, session_factory=base.factory)
    with base.factory() as db:
        db.execute(ligne_article.delete())  # plus aucune homologation

    with pytest.raises(FabricationError, match="homologué"):
        commandes.demarrer(base.machine_id, ordre_fabrication_id=base.of_id)
    assert automate.commandes == []
