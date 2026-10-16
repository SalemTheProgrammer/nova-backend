"""Rapport du matin automatique et envois planifiés de rapports PDF."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.agent.tools import confirmation_gate
from app.agent.tools.scheduling import planifier_envoi
from app.core.config import get_settings
from app.db.base import Base
from app.integrations.divatex import connexion
from app.models import EnvoiPlanifie
from app.models.envoi_planifie import (
    STATUT_ECHEC,
    STATUT_ENVOYE,
    TYPE_RAPPORT,
)
from app.services import audit_service, notify_service, scheduled_report_service, scheduler_service
from app.services import tunicotex_pdf


@pytest.fixture
def nova_db(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(scheduler_service, "session_scope", scope)
    monkeypatch.setattr("app.agent.tools.scheduling.session_scope", scope)
    yield scope
    engine.dispose()


@pytest.fixture
def whatsapp(monkeypatch):
    appels = []
    monkeypatch.setattr(
        notify_service,
        "envoyer_whatsapp",
        lambda numero, corps, document=None, image=None: appels.append((numero, corps, document))
        or "Envoyé.",
    )
    return appels


def test_bilan_auto_envoie_r1(divatex, whatsapp, monkeypatch):
    monkeypatch.setenv("AUTO_BILAN_CANAL", "whatsapp")
    monkeypatch.setenv("AUTO_BILAN_DESTINATAIRE", "+21612345678")
    get_settings.cache_clear()
    try:
        scheduled_report_service._envoyer_bilan_auto("07:30")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert len(whatsapp) == 1
    numero, legende, (nom, pdf) = whatsapp[0]
    assert numero == "+21612345678"
    assert nom.startswith("r1-") and pdf[:4] == b"%PDF"
    assert "28/08/2026" in legende and tunicotex_pdf.TITRES["R1"] in legende


def test_bilan_auto_sans_destinataire_ou_diva_en_panne(whatsapp, monkeypatch):
    monkeypatch.setenv("AUTO_BILAN_CANAL", "whatsapp")
    monkeypatch.setenv("AUTO_BILAN_DESTINATAIRE", "")
    get_settings.cache_clear()
    try:
        scheduled_report_service._envoyer_bilan_auto("07:30")  # ne plante pas
        monkeypatch.setenv("AUTO_BILAN_DESTINATAIRE", "+21612345678")
        get_settings.cache_clear()

        def panne(*a, **k):
            raise connexion.DivatexIndisponible()

        monkeypatch.setattr(tunicotex_pdf, "generer_rapport", panne)
        scheduled_report_service._envoyer_bilan_auto("07:30")  # ne plante pas
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert whatsapp == []


def test_envoi_planifie_rapport(divatex, nova_db, whatsapp):
    with nova_db() as db:
        db.add(
            EnvoiPlanifie(
                type_envoi=TYPE_RAPPORT, canal="whatsapp", destinataire="+21612345678",
                rapport="R4", parametres_json='{"horizon_jours": 7}',
                execute_at=datetime.now() - timedelta(seconds=5),
            )
        )
    scheduler_service._tick()
    assert len(whatsapp) == 1
    assert whatsapp[0][2][0].startswith("r4-")
    assert "28/08/2026" in whatsapp[0][1]
    with nova_db() as db:
        envoi = db.execute(select(EnvoiPlanifie)).scalar_one()
        assert envoi.statut == STATUT_ENVOYE
    scheduler_service._tick()
    assert len(whatsapp) == 1  # pas de second envoi


def test_type_inconnu_echec_sans_reessai(nova_db, whatsapp):
    with nova_db() as db:
        db.add(
            EnvoiPlanifie(
                type_envoi="bilan", canal="whatsapp", destinataire="+21612345678",
                execute_at=datetime.now() - timedelta(seconds=5),
            )
        )
    scheduler_service._tick()
    with nova_db() as db:
        envoi = db.execute(select(EnvoiPlanifie)).scalar_one()
        assert envoi.statut == STATUT_ECHEC and "inconnu" in envoi.resultat
        assert envoi.tentatives == 0
    assert whatsapp == []


def _planifier(inv, confirmation, **extra):
    args = {
        "type_envoi": "rapport", "canal": "whatsapp", "destinataire": "+216 12 345 678",
        "delai_minutes": 30, "confirmation": confirmation, "rapport": "R1", **extra,
    }
    msg = planifier_envoi.invoke(
        {"type": "tool_call", "id": "t", "name": "planifier_envoi", "args": args},
        config={"configurable": {"thread_id": "t-plan", "invocation_id": inv}},
    )
    return msg.content, msg.artifact


def test_planifier_rapport_confirmation_et_stockage(divatex, nova_db, monkeypatch):
    confirmation_gate._PROPOSITIONS.clear()
    monkeypatch.setattr(audit_service, "enregistrer_action", lambda **k: None)
    txt, art = _planifier("i1", False, chaine="CH1-L2", date="hier")
    assert art["kind"] == "confirmation_attente" and "Confirmation requise" in txt
    txt, art = _planifier("i2", True, chaine="CH1-L2", date="hier")
    assert art["kind"] == "action_executee"
    with nova_db() as db:
        envoi = db.execute(select(EnvoiPlanifie)).scalar_one()
    assert envoi.type_envoi == TYPE_RAPPORT and envoi.rapport == "R1"
    assert envoi.destinataire == "+21612345678"
    params = tunicotex_pdf.ParametresRapport.from_dict(__import__("json").loads(envoi.parametres_json))
    assert params.ligne_id == 10 and params.jour.isoformat() == "2026-08-27"
    confirmation_gate._PROPOSITIONS.clear()


def test_planifier_rapport_entrees_invalides(divatex, nova_db):
    txt, art = _planifier("i3", False, rapport="R9")
    assert art is None and "R1" in txt
    txt, art = _planifier("i4", False, rapport="R5")
    assert art is None and "OF" in txt
    txt, art = _planifier("i5", False, rapport=None)
    assert art is None and "rapport" in txt.lower()
