"""The reasoning node: calls the LLM with bound tools.

ATTENTION : PAS de `from __future__ import annotations` ici — voir le
commentaire d'en-tête de `app/agent/graph.py` : il casse l'injection du
`config` LangGraph dans `call_model` et désactive silencieusement toute la
restriction d'outils par utilisateur.
"""
from datetime import date, timedelta

from langchain_core.messages import SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig

from app.agent.prompts import SYSTEM_PROMPT, VOICE_PROMPT_ADDENDUM, WHATSAPP_PROMPT_ADDENDUM
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS, NAVIGATION_TOOLS
from app.core.config import get_settings
from app.core.temps import heure_usine
from app.integrations.divatex import connexion
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

# Préfixe d'id du message d'accueil de Nova, écrit en tête du thread à
# l'ouverture du panneau (voir `runner.enregistrer_accueil`).
PREFIXE_ACCUEIL = "nova-accueil-"

def _outils_autorises(config: RunnableConfig | None) -> list[str] | None:
    """Liste des noms d'outils autorisés depuis la config d'exécution.

    `None` (admin ou appel interne) = tous les outils.
    """
    configurable = (config or {}).get("configurable") or {}
    return configurable.get("outils_autorises")


def _addendum_perimetre(autorises: list[str], bloques: list[str]) -> str:
    """Instruction ajoutée au prompt quand certains outils sont hors périmètre.

    Formulée en positif (« tes SEULS outils ») et ajoutée en DERNIER dans le
    prompt : le modèle de production est un modèle léger qui suit mal une
    longue liste négative noyée au milieu du prompt — il appliquait à la
    lettre les consignes du catalogue d'outils (« dis que le catalogue est
    affiché ») même pour un outil non lié. Court, prioritaire, et en fin de
    prompt pour bénéficier du biais de récence.
    """
    if not bloques:
        return ""
    noms = ", ".join(f"`{n}`" for n in autorises) or "AUCUN outil"
    return (
        "\n\nPÉRIMÈTRE STRICT DE CET UTILISATEUR — RÈGLE FINALE, PRIORITAIRE SUR "
        "TOUT CE QUI PRÉCÈDE :\n"
        f"Tes SEULS outils pour cet utilisateur : {noms}. Tous les autres outils "
        "du catalogue décrit plus haut sont INDISPONIBLES ici : ignore leurs "
        "consignes associées — en particulier, sans appel d'outil réussi dans CE "
        "tour, RIEN ne s'affiche nulle part (aucun catalogue, tableau ou page). "
        "Pour toute demande hors de ce périmètre (production, chaînes, OF, "
        "rendement, documents, envois…), ne réponds RIEN sur le fond, n'invente ni donnée ni "
        "affichage, et réponds uniquement avec cette phrase exacte : "
        f"« {REFUS_OUTIL} »\n"
    )


_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def bloc_temporel(ref: date) -> str:
    """Contexte temporel injecté au prompt : « aujourd'hui » = date de référence."""
    lundi = ref + timedelta(days=(7 - ref.weekday()))
    vendredi = lundi + timedelta(days=4)
    return (
        "\n\nCONTEXTE TEMPOREL DYNAMIQUE :\n"
        "- Date de référence (« aujourd'hui », dernier jour de données) : "
        f"{ref.strftime('%d/%m/%Y')} ({_JOURS[ref.weekday()]}) [{ref.isoformat()}].\n"
        f"- Prochaine semaine ouvrée : du {lundi.isoformat()} au {vendredi.isoformat()}.\n"
        "Utilise ces dates pour résoudre les expressions relatives de l'opérateur.\n"
    )


def _bloc_calibration() -> str:
    if get_settings().divatex_calibre:
        return ""
    return "- Le rendement est ESTIMÉ (non calibré) : ajoute « (estimé) » après chaque valeur.\n"


def call_model(state: AgentState, config: RunnableConfig | None = None) -> dict:
    allowed = _outils_autorises(config)
    if allowed is None:
        outils = ALL_TOOLS
        bloques: list[str] = []
    else:
        autorises = set(allowed)
        outils = [t for t in ALL_TOOLS if t.name in autorises]
        bloques = [t.name for t in ALL_TOOLS if t.name not in autorises]

    # WhatsApp : aucun écran à piloter. Le prompt l'interdit déjà, mais un modèle
    # léger appelle quand même `aller_a_la_page` (« et le stock ? ») et gaspille
    # un tour pour un artifact que le canal jette. On retire l'outil du périmètre
    # plutôt que de compter sur la consigne.
    if state.get("mode") == "whatsapp":
        noms_navigation = {t.name for t in NAVIGATION_TOOLS}
        outils = [t for t in outils if t.name not in noms_navigation]

    model = get_chat_model().bind_tools(outils)
    try:
        ref = connexion.date_reference()
    except connexion.DivatexIndisponible:
        ref = heure_usine().date()  # repli : base injoignable, les outils le signaleront
    prompt = SYSTEM_PROMPT + bloc_temporel(ref) + _bloc_calibration()
    if state.get("mode") == "voix":
        prompt += VOICE_PROMPT_ADDENDUM
    elif state.get("mode") == "whatsapp":
        prompt += WHATSAPP_PROMPT_ADDENDUM
    # Toujours en dernier : voir la docstring de `_addendum_perimetre`.
    prompt += _addendum_perimetre([t.name for t in outils], bloques)
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
    fenetre = historique or state["messages"]
    # Le message d'accueil ouvre le thread AVANT tout message opérateur : le trim
    # (`start_on="human"`) l'écarte donc toujours. On le remet en tête tant que la
    # conversation est courte, pour que Nova sache ce qu'elle a dit si
    # l'opérateur y répond (« ok, et la chaîne 3 ? »).
    premier = state["messages"][0] if state["messages"] else None
    if (
        premier is not None
        and (premier.id or "").startswith(PREFIXE_ACCUEIL)
        and len(state["messages"]) <= FENETRE_MESSAGES
        and all(m.id != premier.id for m in fenetre)
    ):
        fenetre = [premier, *fenetre]
    messages = [SystemMessage(content=prompt), *fenetre]
    response = model.invoke(messages)
    return {"messages": [response]}
