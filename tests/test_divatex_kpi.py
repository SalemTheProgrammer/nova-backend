"""Formules d'indicateurs de production."""
from __future__ import annotations

from datetime import date

import pytest

from app.integrations.divatex.kpi import (
    ParametresRendement,
    minutes_produites,
    objectif_perime,
    rendement,
    taux_atteinte,
)

P = ParametresRendement(1 / 60, True, 480)


def test_minutes_produites_secondes():
    assert minutes_produites([(20, 1, 1.0)] * 3, P) == pytest.approx(1.0)


def test_majoration_appliquee():
    assert minutes_produites([(60, 1, 1.5)], P) == pytest.approx(1.5)


def test_majoration_desactivee():
    assert minutes_produites([(60, 1, 1.5)], ParametresRendement(1 / 60, False, 480)) == pytest.approx(1.0)


def test_rendement_sans_presence():
    assert rendement(10, 0) is None


def test_rendement_calcule():
    assert rendement(5, 10) == pytest.approx(0.5)


def test_taux_atteinte():
    assert taux_atteinte(30, 60) == pytest.approx(0.5)
    assert taux_atteinte(30, None) is None
    assert taux_atteinte(30, 0) is None


def test_objectif_perime():
    assert objectif_perime(date(2026, 3, 31), date(2026, 8, 28))
    assert not objectif_perime(date(2026, 8, 25), date(2026, 8, 28))
    assert not objectif_perime(date(2026, 8, 21), date(2026, 8, 28))


def test_majoration_absente_comptee_1():
    assert minutes_produites([(60, 1, None)], P) == pytest.approx(1.0)
    assert minutes_produites([(60, 1, 0)], P) == pytest.approx(1.0)
