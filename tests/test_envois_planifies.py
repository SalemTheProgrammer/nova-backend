"""Rapport du matin automatique et envois planifiés de rapports PDF."""
from __future__ import annotations

import json
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


@pytest.fixture
def reglages(monkeypatch):
    def appliquer(**valeurs):
        for cle, valeur in valeurs.items():
            monkeypatch.setenv(cle, valeur)
        get_settings.cache_clear()

    yield appliquer
    get_settings.cache_clear()  # monkeypatch restaure l'environnement après


def test_bilan_auto_envoie_r1(divatex, whatsapp, reglages):
    reglages(AUTO_BILAN_CANAL="whatsapp", AUTO_BILAN_DESTINATAIRE="+21612345678")
    scheduled_report_service._envoyer_bilan_auto("07:30")
    assert len(whatsapp) == 1
    numero, legende, (nom, pdf) = whatsapp[0]
    assert numero == "+21612345678"
    assert nom.startswith("r1-") and pdf[:4] == b"%PDF"
    assert "28/08/2026" in legende and tunicotex_pdf.TITRES["R1"] in legende


def test_bilan_auto_sans_destinataire_ou_diva_en_panne(whatsapp, reglages, monkeypatch):
    reglages(AUTO_BILAN_CANAL="whatsapp", AUTO_BILAN_DESTINATAIRE="")
    scheduled_report_service._envoyer_bilan_auto("07:30")  # ne plante pas
    reglages(AUTO_BILAN_DESTINATAIRE="+21612345678")

    def panne(*a, **k):
        raise connexion.DivatexIndisponible()

    monkeypatch.setattr(tunicotex_pdf, "generer_rapport", panne)
    scheduled_report_service._envoyer_bilan_auto("07:30")  # ne plante pas
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
    params = tunicotex_pdf.ParametresRapport.from_dict(json.loads(envoi.parametres_json))
    assert params.ligne_id == 10 and params.jour.isoformat() == "2026-08-27"
    confirmation_gate._PROPOSITIONS.clear()


def test_planifier_rapport_entrees_invalides(divatex, nova_db):
    txt, art = _planifier("i3", False, rapport="R9")
    assert art is None and "R1" in txt
    txt, art = _planifier("i4", False, rapport="R5")
    assert art is None and "OF" in txt
    txt, art = _planifier("i5", False, rapport=None)
    assert art is None and "rapport" in txt.lower()


def _ajouter(nova_db, **kw):
    base = dict(
        type_envoi=TYPE_RAPPORT, canal="whatsapp", destinataire="+21612345678",
        rapport="R1", parametres_json="{}",
        execute_at=datetime.now() - timedelta(seconds=5),
    )
    with nova_db() as db:
        envoi = EnvoiPlanifie(**{**base, **kw})
        db.add(envoi)
        db.flush()
        return envoi.id


def _rendre_du(nova_db):
    with nova_db() as db:
        for e in db.execute(select(EnvoiPlanifie)).scalars():
            if e.statut == "EN_ATTENTE":
                e.execute_at = datetime.now() - timedelta(seconds=1)


def _etat(nova_db, envoi_id):
    with nova_db() as db:
        return db.get(EnvoiPlanifie, envoi_id)


def test_exception_inattendue_isolee(divatex, nova_db, whatsapp, monkeypatch):
    mauvais = _ajouter(
        nova_db, rapport="R2", execute_at=datetime.now() - timedelta(minutes=2)
    )
    bon = _ajouter(nova_db)
    original = tunicotex_pdf.generer_rapport

    def generer(rapport, params):
        if rapport == "R2":
            raise KeyError("boom")  # hors AppError
        return original(rapport, params)

    monkeypatch.setattr(tunicotex_pdf, "generer_rapport", generer)
    scheduler_service._tick()
    _rendre_du(nova_db)
    scheduler_service._tick()
    assert len(whatsapp) == 1  # le bon : exactement une fois sur deux ticks
    assert _etat(nova_db, bon).statut == STATUT_ENVOYE
    mal = _etat(nova_db, mauvais)
    assert mal.statut == "EN_ATTENTE" and mal.tentatives == 2


def test_exception_inattendue_incremente_puis_echec(divatex, nova_db, whatsapp, monkeypatch):
    envoi_id = _ajouter(nova_db)

    def casse(*a, **k):
        raise KeyError("boom")

    monkeypatch.setattr(tunicotex_pdf, "generer_rapport", casse)
    for attendu in (1, 2, 3):
        scheduler_service._tick()
        assert _etat(nova_db, envoi_id).tentatives == attendu
        _rendre_du(nova_db)
    envoi = _etat(nova_db, envoi_id)
    assert envoi.statut == STATUT_ECHEC and "KeyError" in envoi.resultat
    assert whatsapp == []


def test_rapport_diva_en_panne_reessaye_puis_echec(nova_db, whatsapp, monkeypatch):
    envoi_id = _ajouter(nova_db)

    def panne(*a, **k):
        raise connexion.DivatexIndisponible()

    monkeypatch.setattr(tunicotex_pdf, "generer_rapport", panne)
    for attendu in (1, 2):
        scheduler_service._tick()
        envoi = _etat(nova_db, envoi_id)
        assert envoi.statut == "EN_ATTENTE" and envoi.tentatives == attendu
        _rendre_du(nova_db)
    scheduler_service._tick()
    envoi = _etat(nova_db, envoi_id)
    assert envoi.statut == STATUT_ECHEC and envoi.tentatives == 3
    assert "Base DIVA injoignable" in envoi.resultat and whatsapp == []


@pytest.mark.parametrize(
    "kw",
    [
        {"rapport": "R9"},
        {"rapport": None},
        {"rapport": "R5"},  # pas d'OF
        {"parametres_json": "{pas du json"},
        {"parametres_json": '{"jour": "xx"}'},
    ],
)
def test_entree_invalide_echec_sans_reessai(divatex, nova_db, whatsapp, kw):
    envoi_id = _ajouter(nova_db, **kw)
    scheduler_service._tick()
    envoi = _etat(nova_db, envoi_id)
    assert envoi.statut == STATUT_ECHEC and envoi.tentatives == 0
    assert envoi.resultat.startswith("Non envoyé") and whatsapp == []
