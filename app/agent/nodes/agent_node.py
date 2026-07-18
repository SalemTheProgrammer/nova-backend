"""The reasoning node: calls the LLM with bound tools."""
from __future__ import annotations

from datetime import timedelta

from langchain_core.messages import SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig

from app.agent.prompts import SYSTEM_PROMPT, VOICE_PROMPT_ADDENDUM, WHATSAPP_PROMPT_ADDENDUM
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS, TOOL_CATALOG
from app.core.temps import heure_usine
from app.services.llm import get_chat_model

# Phrase de refus exacte quand l'opérateur demande une capacité hors de son
# périmètre (numéro sans l'outil autorisé). Reprise à l'identique par le
# garde-fou d'exécution (voir agent/graph.py) — garder les deux synchronisés.
REFUS_OUTIL = "Désolé, vous n'avez pas l'autorisation d'utiliser cet outil."

# Fenêtre d'historique envoyée au modèle (en NOMBRE de messages). Le
# checkpointer conserve tout l'historique (rejouable, auditable) mais un thread
# permanent — WhatsApp surtout : un thread par numéro, pour toujours — ne doit
# pas grossir indéfiniment l'entrée du modèle (latence, coût, débordement de
# contexte). `start_on="human"` garantit une fenêtre qui commence sur un message
# opérateur : jamais de réponse d'outil orpheline en tête (l'API refuse un
# tool-result sans son tool-call).
FENETRE_MESSAGES = 40

# Descriptions courtes par nom d'outil (pour formuler le périmètre bloqué).
_DESC_OUTIL = {o["name"]: o["description"] for o in TOOL_CATALOG}


def _outils_autorises(config: RunnableConfig | None) -> list[str] | None:
    """Liste des noms d'outils autorisés depuis la config d'exécution.

    `None` (admin ou appel interne) = tous les outils.
    """
    configurable = (config or {}).get("configurable") or {}
    return configurable.get("outils_autorises")


def _addendum_perimetre(bloques: list[str]) -> str:
    """Instruction ajoutée au prompt quand certains outils sont hors périmètre."""
    if not bloques:
        return ""
    lignes = "\n".join(f"- {nom} : {_DESC_OUTIL.get(nom, nom)}" for nom in bloques)
    return (
        "\n\nRESTRICTION D'ACCÈS (périmètre de cet utilisateur) :\n"
        "Tu n'as PAS accès aux capacités suivantes pour cet utilisateur :\n"
        f"{lignes}\n"
        "Si l'opérateur demande l'une de ces actions, n'essaie pas de la réaliser "
        "et n'invente pas de résultat — ne dis JAMAIS qu'une donnée est affichée, "
        "trouvée ou envoyée si tu n'as pas réellement appelé un outil qui l'a "
        "fait. Réponds poliment, en français, avec cette phrase exacte : "
        f"« {REFUS_OUTIL} »\n"
    )


def call_model(state: AgentState, config: RunnableConfig | None = None) -> dict:
    allowed = _outils_autorises(config)
    if allowed is None:
        outils = ALL_TOOLS
        bloques: list[str] = []
    else:
        autorises = set(allowed)
        outils = [t for t in ALL_TOOLS if t.name in autorises]
        bloques = [t.name for t in ALL_TOOLS if t.name not in autorises]

    model = get_chat_model().bind_tools(outils)
    now = heure_usine()
    today = now.date()
    next_monday = today + timedelta(days=(7 - today.weekday()))
    next_friday = next_monday + timedelta(days=4)
    prompt = SYSTEM_PROMPT + (
        "\n\nCONTEXTE TEMPOREL DYNAMIQUE (heure locale de l'usine, Africa/Tunis) :\n"
        f"- Aujourd'hui : {today.isoformat()} ({today.strftime('%A')}).\n"
        f"- Prochaine semaine ouvrée : du {next_monday.isoformat()} au {next_friday.isoformat()}.\n"
        "Utilise ces dates pour résoudre les expressions relatives de l'opérateur.\n"
    )
    prompt += _addendum_perimetre(bloques)
    if state.get("mode") == "voix":
        prompt += VOICE_PROMPT_ADDENDUM
    elif state.get("mode") == "whatsapp":
        prompt += WHATSAPP_PROMPT_ADDENDUM
    historique = trim_messages(
        state["messages"],
        max_tokens=FENETRE_MESSAGES,
        token_counter=len,  # compte des MESSAGES, pas des tokens
        strategy="last",
        start_on="human",
        include_system=False,  # le system est reconstruit à chaque tour ci-dessus
        allow_partial=False,
    )
    # Filet : si le trim renvoie vide (ex. tour courant plus long que la
    # fenêtre), on garde l'état intact plutôt que d'appeler le modèle sans rien.
    messages = [SystemMessage(content=prompt), *(historique or state["messages"])]
    response = model.invoke(messages)
    return {"messages": [response]}
