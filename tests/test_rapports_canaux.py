"""Rapports PDF depuis le chat (outils) et WhatsApp (documents en réponse)."""
from __future__ import annotations

import base64
import json

import pytest

from app.agent.tools import READONLY_TOOL_NAMES, confirmation_gate
from app.agent.tools import notify as notify_tools
from app.agent.tools.notify import envoyer_rapport
from app.agent.tools.tunicotex import TUNICOTEX_TOOLS, rapport_pdf
from app.api.routes import whatsapp as whatsapp_routes
from app.services import audit_service
from tests.test_outils_tunicotex import appeler

H = {"X-API-Key": "test-api-key"}


def test_rapport_pdf_artefact_sans_octets(divatex):
    txt, art = appeler(rapport_pdf, {"type": "R1"})
    assert art["kind"] == "document" and art["rapport"] == "R1"
    assert "base64" not in json.dumps(art) and "%PDF" not in json.dumps(art)
    assert art["nom_fichier"].startswith("r1-") and art["parametres"]["horizon_jours"] == 7
    assert "Données au 28/08/2026" in txt and "prêt" in txt


def test_rapport_pdf_web_autorise():
    assert "rapport_pdf" in READONLY_TOOL_NAMES
    assert "envoyer_rapport" not in READONLY_TOOL_NAMES
    assert rapport_pdf in TUNICOTEX_TOOLS


def test_rapport_pdf_resout_les_noms(divatex):
    _, art = appeler(rapport_pdf, {"type": "R1", "chaine": "CH1-L2", "date": "hier"})
    assert art["parametres"]["ligne_id"] == 10 and art["parametres"]["jour"] == "2026-08-27"
    _, art = appeler(rapport_pdf, {"type": "R5", "of": "90253"})
    assert art["kind"] == "document" and art["parametres"]["of_id"]


def test_rapport_pdf_of_ambigu_liste(divatex):
    txt, art = appeler(rapport_pdf, {"type": "R5", "of": "91294"})
    assert art["kind"] == "table" and len(art["lignes"]) == 2 and "Précisez" in txt


def test_rapport_pdf_erreurs(divatex):
    txt, art = appeler(rapport_pdf, {"type": "R9"})
    assert art is None and "R1" in txt
    txt, art = appeler(rapport_pdf, {"type": "R5"})
    assert art is None and "OF" in txt
    txt, art = appeler(rapport_pdf, {"type": "R1", "chaine": "zzz"})
    assert art is None and txt.startswith("Chaîne inconnue")
    txt, art = appeler(rapport_pdf, {"type": "R1", "date": "pas une date"})
    assert art is None


@pytest.fixture
def envois(monkeypatch):
    confirmation_gate._PROPOSITIONS.clear()
    monkeypatch.setattr(audit_service, "enregistrer_action", lambda **k: None)
    appels = []
    monkeypatch.setattr(
        notify_tools, "_envoyer", lambda *a, **k: appels.append((a, k)) or "Envoyé."
    )
    yield appels
    confirmation_gate._PROPOSITIONS.clear()


def _cfg(inv):
    return {"configurable": {"thread_id": "t-rap", "invocation_id": inv}}


def _appel(inv, confirmation):
    args = {
        "rapport": "R1", "canal": "whatsapp", "destinataire": "+216 12 345 678",
        "confirmation": confirmation,
    }
    msg = envoyer_rapport.invoke(
        {"type": "tool_call", "id": "t", "name": "envoyer_rapport", "args": args},
        config=_cfg(inv),
    )
    return msg.content, msg.artifact


def test_envoyer_rapport_exige_confirmation(divatex, envois):
    txt, art = _appel("inv-1", False)
    assert art["kind"] == "confirmation_attente" and "Confirmation requise" in txt
    # Auto-confirmation dans le même appel : toujours refusée.
    txt, art = _appel("inv-1", True)
    assert art["kind"] == "confirmation_attente" and envois == []
    # Nouvel appel opérateur : autorisé, PDF envoyé via _envoyer.
    txt, art = _appel("inv-2", True)
    assert art["kind"] == "action_executee" and len(envois) == 1
    (canal, dest, _sujet, _corps), kw = envois[0]
    assert canal == "whatsapp" and dest == "+21612345678"
    assert kw["document"][0].startswith("r1-") and kw["document"][1][:4] == b"%PDF"


def test_envoyer_rapport_entrees_invalides(divatex, envois):
    msg = envoyer_rapport.invoke(
        {"type": "tool_call", "id": "t", "name": "envoyer_rapport",
         "args": {"rapport": "R1", "canal": "sms", "destinataire": "x", "confirmation": True}},
        config=_cfg("i"),
    )
    assert "Canal invalide" in msg.content and envois == []
    msg = envoyer_rapport.invoke(
        {"type": "tool_call", "id": "t", "name": "envoyer_rapport",
         "args": {"rapport": "R5", "canal": "email", "destinataire": "a@b.tn",
                  "confirmation": False}},
        config=_cfg("i"),
    )
    assert msg.artifact is None and "OF" in msg.content


def test_whatsapp_renvoie_document(divatex, client, monkeypatch):
    artefact = {
        "kind": "document", "rapport": "R1", "titre": "Fiche de production journalière",
        "nom_fichier": "r1-x.pdf", "parametres": {"horizon_jours": 7},
    }

    async def faux_run(*a, **k):
        return "Voici la fiche.", [artefact]

    monkeypatch.setattr(whatsapp_routes, "run_agent_avec_artifacts", faux_run)
    monkeypatch.setattr(whatsapp_routes, "_resoudre_acces", lambda e164: (True, None))
    r = client.post(
        "/api/v1/whatsapp/inbound",
        json={"from_number": "+21612345678", "message": "bilan"}, headers=H,
    ).json()
    doc = r["reply_documents"][0]
    assert doc["filename"].startswith("r1-")
    assert base64.b64decode(doc["base64"])[:4] == b"%PDF"
    assert doc["caption"] == "Fiche de production journalière"


def test_rendre_documents_ignore_les_echecs(divatex):
    docs = whatsapp_routes._rendre_documents([
        {"kind": "document", "rapport": "R5", "titre": "t", "parametres": {}},  # OF manquant
        {"kind": "document", "rapport": "R1", "titre": "t", "parametres": {"jour": 5}},
        {"kind": "chart"},
    ])
    assert docs == []
