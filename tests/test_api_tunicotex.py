"""API REST /api/v1/tunicotex."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.integrations.divatex.connexion import set_engine
from app.services import audit_service, notify_service
from tests.divatex_fixtures import (
    ID_EMP_BEN_SALAH_AHMED,
    ID_LIGNE_CH1_L2,
    ID_OF_91294_1,
    divatex,  # noqa: F401
)

H = {"X-API-Key": "test-api-key"}
B = "/api/v1/tunicotex"


def test_reference(auth_client, divatex):
    r = auth_client.get(f"{B}/reference", headers=H).json()
    assert r["date_reference"] == "2026-08-28" and r["calibre"] is False
    assert {"id", "code", "nom", "groupe_id", "nature"} <= set(r["lignes"][0])
    assert {"id", "nom"} == set(r["groupes"][0]) and r["phases"]


def test_atelier(auth_client, divatex):
    r = auth_client.get(f"{B}/atelier", headers=H).json()
    assert r["situation"]["nb_of_en_retard"] == 2
    assert r["lignes"] and len(r["tendance"]) == 7


def test_chaines_filtre_groupe(auth_client, divatex):
    tout = auth_client.get(f"{B}/chaines", headers=H).json()
    un = auth_client.get(f"{B}/chaines?groupe=7&date=2026-08-28", headers=H).json()
    assert 0 < len(un) < len(tout)
    assert all(p["ligne"]["groupe"]["id"] == 7 for p in un)


def test_chaine_detail(auth_client, divatex):
    r = auth_client.get(f"{B}/chaines/{ID_LIGNE_CH1_L2}", headers=H).json()
    assert r["production"]["ligne"]["id"] == ID_LIGNE_CH1_L2
    assert r["courbe"] and r["operateurs"]
    assert {o["id"] for o in r["ofs"]} >= {ID_OF_91294_1}


def test_chaine_inconnue_404(auth_client, divatex):
    assert auth_client.get(f"{B}/chaines/999999", headers=H).status_code == 404


def test_of_recherche_et_detail(auth_client, divatex):
    assert len(auth_client.get(f"{B}/of?q=91294", headers=H).json()) == 2
    r = auth_client.get(f"{B}/of/{ID_OF_91294_1}", headers=H).json()
    assert r["of"]["id"] == ID_OF_91294_1 and r["phases"]


def test_of_404(auth_client, divatex):
    assert auth_client.get(f"{B}/of/999999", headers=H).status_code == 404


def test_operateurs_et_fiche(auth_client, divatex):
    r = auth_client.get(f"{B}/operateurs?du=2026-08-28&au=2026-08-28", headers=H)
    assert r.status_code == 200 and r.json()
    f = auth_client.get(f"{B}/operateurs/{ID_EMP_BEN_SALAH_AHMED}", headers=H)
    assert f.status_code == 200 and f.json()["operateur"]["id"] == ID_EMP_BEN_SALAH_AHMED
    assert auth_client.get(f"{B}/operateurs/999999", headers=H).status_code == 404


def test_operateurs_periode_400(auth_client, divatex):
    r = auth_client.get(f"{B}/operateurs?du=2026-06-01&au=2026-08-28", headers=H)
    assert r.status_code == 400


def test_date_invalide_422(auth_client, divatex):
    assert auth_client.get(f"{B}/atelier?date=pas-une-date", headers=H).status_code == 422


def test_commandes(auth_client, divatex):
    assert isinstance(auth_client.get(f"{B}/commandes?horizon=7", headers=H).json(), list)
    assert isinstance(auth_client.get(f"{B}/commandes/recherche?q=91294", headers=H).json(), list)


def test_pdf(auth_client, divatex):
    r = auth_client.get(f"{B}/rapports/R1.pdf", headers=H)
    assert r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    assert "attachment" in r.headers["content-disposition"]


def test_pdf_r5_sans_of_400(auth_client, divatex):
    assert auth_client.get(f"{B}/rapports/R5.pdf", headers=H).status_code == 400


def test_pdf_type_inconnu_404(auth_client, divatex):
    assert auth_client.get(f"{B}/rapports/R9.pdf", headers=H).status_code == 404


def test_envoyer_audite(auth_client, divatex, monkeypatch):
    envois, audits = [], []
    monkeypatch.setattr(
        notify_service, "envoyer_whatsapp", lambda *a, **k: envois.append((a, k)) or "ok"
    )
    monkeypatch.setattr(audit_service, "enregistrer_action", lambda **k: audits.append(k))
    r = auth_client.post(
        f"{B}/rapports/R1/envoyer", headers=H,
        json={"canal": "whatsapp", "destinataire": "+216 12 345 678", "parametres": {}},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "Rapport envoyé à +216 12 345 678."}
    assert len(envois) == 1 and envois[0][0][0] == "+21612345678"
    assert envois[0][1]["document"][1][:4] == b"%PDF"
    assert len(audits) == 1 and audits[0]["action"] == "envoyer_rapport_web"
    assert audits[0]["source"] == "web" and audits[0]["thread_id"] is None


def test_envoyer_email(auth_client, divatex, monkeypatch):
    envois = []
    monkeypatch.setattr(
        notify_service, "envoyer_email", lambda *a, **k: envois.append((a, k)) or "ok"
    )
    monkeypatch.setattr(audit_service, "enregistrer_action", lambda **k: None)
    r = auth_client.post(
        f"{B}/rapports/R1/envoyer", headers=H,
        json={"canal": "email", "destinataire": "a@b.tn", "parametres": {"jour": "2026-08-28"}},
    )
    assert r.status_code == 200
    assert envois[0][0][0] == "a@b.tn" and envois[0][1]["piece_jointe"][1][:4] == b"%PDF"


def test_envoyer_canal_invalide_422(auth_client, divatex):
    r = auth_client.post(
        f"{B}/rapports/R1/envoyer", headers=H,
        json={"canal": "sms", "destinataire": "x", "parametres": {}},
    )
    assert r.status_code == 422


def test_api_503_si_divatex_down(auth_client):
    set_engine(create_engine("mysql+pymysql://x:y@127.0.0.1:1/n"))
    try:
        r = auth_client.get(f"{B}/atelier", headers=H)
    finally:
        set_engine(None)
    assert r.status_code == 503 and r.json()["error"]["message"] == "Base DIVA injoignable."


def test_categorie_requise(client_sans_outils, divatex):
    assert client_sans_outils.get(f"{B}/atelier", headers=H).status_code == 403


def test_cle_api_requise(client, divatex):
    assert client.get(f"{B}/atelier").status_code in (401, 403)


@pytest.mark.parametrize(
    "parametres",
    [{"jour": 5}, {"jour": "pas-une-date"}, {"of_id": "x"}, {"of_id": 1.5}, {"horizon_jours": 0},
     {"groupe_id": True}],
)
def test_envoyer_parametres_mal_formes_400(auth_client, divatex, monkeypatch, parametres):
    envois = []
    monkeypatch.setattr(
        notify_service, "envoyer_whatsapp", lambda *a, **k: envois.append(a) or "ok"
    )
    r = auth_client.post(
        f"{B}/rapports/R1/envoyer", headers=H,
        json={"canal": "whatsapp", "destinataire": "+21612345678", "parametres": parametres},
    )
    assert r.status_code == 400 and envois == []
