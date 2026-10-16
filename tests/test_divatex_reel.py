"""Vérifications sur la base DIVA réelle (opt-in : ``-m divatex`` + ``DIVATEX_URL``)."""
from __future__ import annotations

import os
import time
from datetime import timedelta

import pytest

from app.core.config import get_settings
from app.integrations.divatex import commandes, connexion, of, operateurs, production, referentiel

pytestmark = pytest.mark.divatex

_URL_REELLE = os.environ.get("DIVATEX_URL")  # lue avant tout nettoyage
_LIMITE_S = 3.0


@pytest.fixture(autouse=True)
def _base_reelle(monkeypatch):
    if not _URL_REELLE:
        pytest.skip("DIVATEX_URL non défini")
    monkeypatch.setenv("DIVATEX_URL", _URL_REELLE)
    monkeypatch.setenv("DIVATEX_DATE_REFERENCE", "auto")
    get_settings.cache_clear()
    connexion.set_engine(None)
    connexion.vider_caches()
    yield
    connexion.set_engine(None)
    get_settings.cache_clear()


def _chronometrer(fonction, *args, **kwargs):
    debut = time.perf_counter()
    resultat = fonction(*args, **kwargs)
    duree = time.perf_counter() - debut
    print(f"\n[chrono] {fonction.__module__.split('.')[-1]}.{fonction.__name__}: {duree:.2f}s")
    assert duree < _LIMITE_S
    return resultat


def test_date_reference_auto():
    ref = _chronometrer(connexion.date_reference)
    assert ref.year >= 2026


def test_production_lignes():
    ref = connexion.date_reference()
    assert _chronometrer(production.production_lignes, ref)


def test_situation_atelier():
    ref = connexion.date_reference()
    situation = _chronometrer(production.situation_atelier, ref)
    assert situation.a_des_lectures


def test_tendance():
    ref = connexion.date_reference()
    assert _chronometrer(production.tendance, ref, 7)


def test_rechercher_of_et_avancement():
    trouves = _chronometrer(of.rechercher_of, "9")
    assert trouves
    ouverts = of.rechercher_of("", ouverts_seulement=True, limite=50)
    assert ouverts
    recent = max(ouverts, key=lambda o: o.id)
    avancement = _chronometrer(of.avancement_of, recent.id)
    assert avancement is not None and avancement.phases


def test_of_a_risque():
    ref = connexion.date_reference()
    assert _chronometrer(commandes.of_a_risque, ref) is not None


def test_classement_operateurs_chaine_01():
    ref = connexion.date_reference()
    groupe = referentiel.trouver_groupe("CHAINE 01")
    assert groupe is not None and groupe.id == 7
    classement = _chronometrer(
        operateurs.classement_operateurs, ref - timedelta(days=6), ref, groupe_id=groupe.id
    )
    assert classement


def test_rechercher_operateurs_like_mariadb():
    assert _chronometrer(referentiel.rechercher_operateurs, "ben")
