"""Nova proactive : le superviseur parle en premier, sur WhatsApp.

Quand la boucle du superviseur autonome crée une proposition (machine en panne
qui bloque un OF, dérive qualité…), elle est envoyée par WhatsApp aux numéros
configurés (`SUPERVISOR_NOTIFY_NUMBERS`). L'opérateur répond « oui » ou « non »
dans la conversation : la réponse est routée ici AVANT l'agent conversationnel
et déclenche `supervisor_service.decider` — même human-in-the-loop que les
cartes de proposition de l'interface web, mais depuis le téléphone.
"""
from __future__ import annotations

import re

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import AgentProposal
from app.models.enums import StatutProposition
from app.services import notify_service

logger = get_logger(__name__)

# File FIFO des propositions envoyées à chaque numéro et pas encore décidées
# (mémoire process : suffisant, les propositions elles-mêmes vivent en base et
# restent décidables depuis le web). Un « oui »/« non » décide la PLUS ANCIENNE
# encore en attente ; s'il en reste, la suivante est aussitôt rappelée à
# l'opérateur — aucune proposition ne devient injoignable parce qu'une plus
# récente l'a écrasée.
_propositions_en_attente: dict[str, list[int]] = {}

_OUI = {"oui", "yes", "ok", "d'accord", "daccord", "go", "vas-y", "vasy", "confirme", "valide"}
_NON = {"non", "no", "annule", "refuse", "ignore", "stop"}


def numeros_configures() -> list[str]:
    """Numéros de notification proactive, normalisés en E.164."""
    settings = get_settings()
    numeros: list[str] = []
    for brut in settings.supervisor_notify_numbers:
        try:
            numeros.append(notify_service.normaliser_numero(brut))
        except AppError:
            logger.warning("proactive_numero_invalide", numero=brut)
    return numeros


def notifier_proposition(proposition: dict) -> None:
    """Envoie une proposition du superviseur par WhatsApp (appel synchrone).

    `proposition` est le dict sérialisé par `serialiser_proposition`. Silencieux
    si aucun numéro n'est configuré ou si la sévérité est filtrée.
    """
    settings = get_settings()
    numeros = numeros_configures()
    if not numeros:
        return
    if settings.supervisor_notify_critical_only and proposition.get("severite") != "CRITICAL":
        return

    icone = "🚨" if proposition.get("severite") == "CRITICAL" else "⚠️"
    message = (
        f"{icone} *{proposition['titre']}*\n\n"
        f"{proposition['diagnostic']}\n\n"
        f"👉 Réponds *oui* pour : {proposition['action_libelle']}\n"
        f"Réponds *non* pour ignorer."
    )
    for numero in numeros:
        try:
            notify_service.envoyer_whatsapp(numero, message)
        except AppError as exc:
            logger.warning("proactive_whatsapp_echec", numero=numero, error=exc.message)
        else:
            file = _propositions_en_attente.setdefault(numero, [])
            proposition_id = int(proposition["id"])
            if proposition_id not in file:
                file.append(proposition_id)
            logger.info("proactive_whatsapp_envoye", numero=numero, proposition_id=proposition["id"])


def notifier_execution_autonome(proposition: dict) -> None:
    """Nova a agi SEULE (mode assisté/autopilote) : notification informative,
    rien à décider — contrairement à `notifier_proposition`, aucune file
    d'attente « oui/non » n'est ouverte pour ce message.
    """
    numeros = numeros_configures()
    if not numeros:
        return
    resultat = proposition.get("resultat") or proposition.get("action_libelle") or ""
    message = f"🤖 *Nova a agi automatiquement*\n\n{proposition['titre']}\n{resultat}"
    for numero in numeros:
        try:
            notify_service.envoyer_whatsapp(numero, message)
        except AppError as exc:
            logger.warning("proactive_whatsapp_echec", numero=numero, error=exc.message)


def _prochaine_en_attente(db, e164: str) -> AgentProposal | None:
    """Défile jusqu'à la plus ancienne proposition ENCORE décidable de la file
    (celles déjà décidées depuis le web sont retirées au passage)."""
    file = _propositions_en_attente.get(e164) or []
    while file:
        existante = db.get(AgentProposal, file[0])
        if existante is not None and existante.statut == StatutProposition.PROPOSEE:
            return existante
        file.pop(0)
    _propositions_en_attente.pop(e164, None)
    return None


def traiter_reponse_operateur(e164: str, texte: str) -> str | None:
    """Route un « oui »/« non » WhatsApp vers la plus ancienne proposition en attente.

    Renvoie le texte de réponse à envoyer à l'opérateur, ou None si le message
    n'est pas une décision (ou plus rien à décider) — la conversation continue
    alors normalement avec l'agent. S'il reste d'autres propositions en file
    après la décision, la suivante est rappelée dans la même réponse.
    """
    mot = re.sub(r"[^\w'à-ÿ-]", "", texte.strip().lower())
    if mot in _OUI:
        approuver = True
    elif mot in _NON:
        approuver = False
    else:
        return None

    from app.services import supervisor_service  # import tardif (cycle d'import)

    with session_scope() as db:
        en_attente = _prochaine_en_attente(db, e164)
        if en_attente is None:
            # Rien à décider : le oui/non appartient à la conversation normale.
            return None
        proposition = supervisor_service.decider(
            db, en_attente.id, approuver=approuver, canal="whatsapp", identite=e164
        )
        statut = proposition.statut
        resultat = proposition.resultat or ""

        file = _propositions_en_attente.get(e164)
        if file and file[0] == proposition.id:
            file.pop(0)
        suivante = _prochaine_en_attente(db, e164)
        rappel = (
            f"\n\n📋 Autre situation en attente : *{suivante.titre}*\n"
            f"👉 Réponds *oui* pour : {suivante.action_libelle} — *non* pour ignorer."
            if suivante is not None
            else ""
        )

    if not approuver:
        return "OK, je n'exécute pas. La situation reste sous surveillance." + rappel
    if statut == StatutProposition.EXECUTEE:
        return f"✅ {resultat}" + rappel
    return f"⚠️ {resultat}" + rappel
