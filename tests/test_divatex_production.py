"""Production des lignes vs objectif, tendance, courbe horaire, situation atelier."""
from __future__ import annotations

from datetime import date

import pytest

from app.integrations.divatex.production import (
    courbe_horaire,
    pieces_par_phase,
    production_lignes,
    situation_atelier,
    tendance,
)

JOUR = date(2026, 8, 28)


def _ligne(code, jour=JOUR):
    return next(p for p in production_lignes(jour) if p.ligne.code == code)


def test_production_ligne_objectif_ancien(divatex):
    l = _ligne("CH1-L2")
    assert (l.sortie, l.objectif, l.objectif_perime, l.effectif) == (30, 382, True, 3)
    assert l.presence_estimee and l.a_des_lectures
    assert l.objectif_date == date(2026, 3, 31)
    assert l.taux == pytest.approx(30 / 382)
    assert l.minutes_presence == 3 * 480
    assert l.minutes_produites == pytest.approx(600.0)
    assert l.rendement == pytest.approx(600 / 1440)


def test_ligne_sans_lecture_ce_jour_mais_objectif(divatex):
    l = _ligne("CH1-L1")
    assert (l.sortie, l.objectif, l.a_des_lectures, l.taux) == (0, 120, False, None)


def test_tricotage_sans_rendement(divatex):
    a3 = _ligne("A3")
    assert a3.rendement is None and a3.minutes_produites == 0 and a3.sortie == 20


def test_production_jour_sans_lecture(divatex):
    lignes = production_lignes(date(2026, 8, 30))
    assert lignes and all(not p.a_des_lectures and p.taux is None for p in lignes)


def test_filtre_groupe(divatex):
    assert {p.ligne.code for p in production_lignes(JOUR, groupe_id=7)} == {"CH1-L1", "CH1-L2"}


def test_courbe_horaire(divatex):
    courbe = courbe_horaire(10, JOUR)
    assert sum(p.pieces for p in courbe) == 30
    assert [p.heure for p in courbe] == list(range(7, 17))


def test_tendance(divatex):
    points = tendance(JOUR, jours=3)
    assert [p.jour for p in points] == [date(2026, 8, 26), date(2026, 8, 27), date(2026, 8, 28)]
    assert points[-1].sortie == 30
    assert points[1].sortie == 1
    assert points[-1].objectif == 120 + 382


def test_tendance_au_plus_trois_requetes(divatex, capture_sql):
    tendance(JOUR, jours=7)
    capture_sql.clear()
    tendance(JOUR, jours=7)
    assert len(capture_sql) <= 3


def test_situation_atelier(divatex):
    s = situation_atelier(JOUR)
    assert (s.sortie, s.objectif, s.a_des_lectures, s.nb_of_en_retard) == (30, 502, True, 2)
    assert s.taux == pytest.approx(30 / 502)
    assert s.lignes_sous_objectif == ["CH1-L2"]  # CH1-L1 sans lecture : ni sous ni au-dessus
    assert s.lignes_au_dessus == []
    assert s.rendement_moyen == pytest.approx(600 / 1440)


def test_situation_atelier_sans_lecture(divatex):
    s = situation_atelier(date(2026, 8, 30))
    assert not s.a_des_lectures and s.taux is None


def test_requetes_lectures_commencent_par_un_prefixe_indexe(capture_sql):
    production_lignes(JOUR)
    courbe_horaire(10, JOUR)
    for sql in capture_sql:
        if "FROM lectures" in sql:
            assert "Date = " in sql


def test_pieces_par_phase_atelier(divatex):
    r = pieces_par_phase(date(2026, 8, 27), JOUR)
    assert (date(2026, 8, 28), 5, 30) in r
    assert (date(2026, 8, 28), 1, 20) in r
    assert all(p > 0 for _, p, _ in r)


def test_pieces_par_phase_of_et_client(divatex):
    of = pieces_par_phase(JOUR, JOUR, of_id=1)
    assert (JOUR, 5, 15) in of and (JOUR, 12, 100) in of
    client = pieces_par_phase(JOUR, JOUR, client_id=1)
    assert (JOUR, 5, 30) in client and all(p != 1 for _, p, _ in client)
    assert pieces_par_phase(JOUR, JOUR, client_id=999) == []


def test_pieces_par_phase_periode_limitee(divatex):
    with pytest.raises(ValueError, match="31 jours"):
        pieces_par_phase(date(2026, 7, 1), date(2026, 8, 28))
