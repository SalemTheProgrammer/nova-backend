from datetime import date

from sqlalchemy import create_engine

from app.agent.nodes.agent_node import bloc_temporel
from app.agent.prompts import SYSTEM_PROMPT, VOICE_PROMPT_ADDENDUM, WHATSAPP_PROMPT_ADDENDUM
from app.agent.tools.tunicotex import TUNICOTEX_TOOLS
from app.integrations.divatex.connexion import set_engine
from app.services.ai_agent_service import generer_accueil

INTERDITS = ("BPF", "TRS", "FEFO", "comprim", "Sparkplug", "MQTT", "machine")


def test_prompt_sans_vocabulaire_pharma():
    texte = (SYSTEM_PROMPT + WHATSAPP_PROMPT_ADDENDUM + VOICE_PROMPT_ADDENDUM).lower()
    assert not [m for m in INTERDITS if m.lower() in texte]


def test_prompt_cite_les_outils():
    assert all(t.name in SYSTEM_PROMPT for t in TUNICOTEX_TOOLS)


def test_prompt_regles_cles():
    assert "Tunicotex" in SYSTEM_PROMPT
    assert "Données au" in SYSTEM_PROMPT
    assert "(estimé)" in SYSTEM_PROMPT


def test_bloc_date_utilise_reference():
    bloc = bloc_temporel(date(2026, 8, 28))
    assert "28/08/2026" in bloc and "vendredi" in bloc
    assert "du 2026-08-31 au 2026-09-04" in bloc


def test_accueil(divatex):
    msgs = generer_accueil(None)
    assert 2 <= len(msgs) <= 3
    assert "28/08/2026" in msgs[0] and "Données au" in msgs[0]
    assert any("en retard de livraison (7 derniers jours)" in m for m in msgs)


def test_accueil_divatex_down():
    set_engine(create_engine("mysql+pymysql://x:y@127.0.0.1:1/n"))
    try:
        assert generer_accueil(None) == [
            "Bonjour, je suis Nova.",
            "Base DIVA injoignable pour le moment.",
        ]
    finally:
        set_engine(None)
