"""Les cinq rapports PDF Tunicotex (R1 à R5)."""
from __future__ import annotations

import io
from datetime import date

import pytest
from pypdf import PdfReader

from app.services.tunicotex_pdf import TITRES, ParametresRapport, generer_rapport
from tests.divatex_fixtures import (
    ID_GROUPE_CHAINE01,
    ID_LIGNE_CH1_L2,
    ID_OF_91294_1,
    JOUR_SANS_LECTURE,
    NOM_OF_91294_1,
)


def texte_pdf(pdf: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def _pages(pdf: bytes) -> list:
    return list(PdfReader(io.BytesIO(pdf)).pages)


@pytest.mark.parametrize("t", ["R1", "R2", "R3", "R4"])
def test_rapport_genere(divatex, t):
    nom, pdf = generer_rapport(t, ParametresRapport())
    assert pdf[:4] == b"%PDF" and nom.startswith(t.lower()) and nom.endswith(".pdf")


def test_r5_exige_of(divatex):
    with pytest.raises(ValueError, match="préciser l'OF"):
        generer_rapport("R5", ParametresRapport())


def test_r5(divatex):
    nom, pdf = generer_rapport("R5", ParametresRapport(of_id=ID_OF_91294_1))
    assert pdf[:4] == b"%PDF" and nom == f"r5-of-{NOM_OF_91294_1}.pdf"
    texte = texte_pdf(pdf)
    assert NOM_OF_91294_1 in texte and "Fiche OF" in texte


def test_date_reference_dans_le_texte(divatex):
    texte = texte_pdf(generer_rapport("R1", ParametresRapport())[1])
    assert "28/08/2026" in texte and "Nova Opsis — Tunicotex" in texte
    assert "(estimé)" in texte


def test_rapport_jour_vide(divatex):
    texte = texte_pdf(generer_rapport("R1", ParametresRapport(jour=JOUR_SANS_LECTURE))[1])
    assert "Aucune lecture" in texte


def test_noms_de_fichier(divatex):
    assert generer_rapport("R1", ParametresRapport())[0] == "r1-production-atelier-2026-08-28.pdf"
    p = ParametresRapport(groupe_id=ID_GROUPE_CHAINE01)
    assert generer_rapport("R1", p)[0].startswith("r1-production-")
    assert generer_rapport("R2", ParametresRapport())[0] == "r2-phases-2026-08-22-2026-08-28.pdf"
    assert generer_rapport("R3", ParametresRapport(ligne_id=ID_LIGNE_CH1_L2))[0] == (
        "r3-rendement-CH1-L2-2026-08-22-2026-08-28.pdf"
    )
    assert generer_rapport("R4", ParametresRapport())[0] == "r4-otd-2026-08-28.pdf"


def test_r2_r3_paysage_et_entete_sur_chaque_page(divatex):
    for t in ("R2", "R3"):
        pdf = generer_rapport(t, ParametresRapport())[1]
        for page in _pages(pdf):
            assert page.mediabox.width > page.mediabox.height
        assert "Données au 28/08/2026" in texte_pdf(pdf)


def test_r2_matrice_jours_phases(divatex):
    texte = texte_pdf(generer_rapport("R2", ParametresRapport())[1])
    assert "28/08/2026" in texte and "27/08/2026" in texte


def test_r2_periode_limitee(divatex):
    with pytest.raises(ValueError, match="31 jours"):
        generer_rapport("R2", ParametresRapport(du=date(2026, 7, 1), au=date(2026, 8, 28)))


def test_r1_contient_lignes_et_total(divatex):
    texte = texte_pdf(generer_rapport("R1", ParametresRapport())[1])
    assert "CH1-L2" in texte
    plat = " ".join(texte.split())
    assert "Total confection" not in plat
    assert "Total (chaînes avec objectif) 382 30 8 %" in plat
    assert "Sortie confection totale 30 3 42 % (estimé)" in plat


def test_r1_sortie_totale_hors_objectif(divatex):
    """Sans objectif, le total « avec objectif » est vide mais la sortie totale reste."""
    from sqlalchemy import text

    from app.integrations.divatex import connexion

    with connexion.get_engine().begin() as conn:
        conn.execute(text("DELETE FROM objectifchaine"))
    connexion.vider_caches()
    plat = " ".join(texte_pdf(generer_rapport("R1", ParametresRapport())[1]).split())
    assert "Total (chaînes avec objectif) 0 0" in plat
    assert "Sortie confection totale 30" in plat


def test_r4_groupe_par_client(divatex):
    texte = texte_pdf(generer_rapport("R4", ParametresRapport())[1])
    assert "LC WAIKIKI" in texte and NOM_OF_91294_1 in texte


def test_jamais_de_mots_interdits(divatex):
    for t, p in (("R1", ParametresRapport()), ("R5", ParametresRapport(of_id=ID_OF_91294_1))):
        texte = texte_pdf(generer_rapport(t, p)[1])
        for mot in ("BPF", "TRS", "FEFO", "MQTT"):
            assert mot not in texte


def test_parametres_aller_retour():
    p = ParametresRapport(du=date(2026, 8, 1), ligne_id=10)
    assert ParametresRapport.from_dict(p.to_dict()) == p
    assert p.to_dict()["du"] == "2026-08-01"


def test_titres():
    assert set(TITRES) == {"R1", "R2", "R3", "R4", "R5"}
