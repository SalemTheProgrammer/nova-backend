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

# Dernière proposition envoyée à chaque numéro (mémoire process : suffisant, les
# propositions elles-mêmes vivent en base et restent décidables depuis le web).
_derniere_proposition: dict[str, int] = {}

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
            _derniere_proposition[numero] = int(proposition["id"])
            logger.info("proactive_whatsapp_envoye", numero=numero, proposition_id=proposition["id"])


def traiter_reponse_operateur(e164: str, texte: str) -> str | None:
    """Route un « oui »/« non » WhatsApp vers la dernière proposition envoyée.

    Renvoie le texte de réponse à envoyer à l'opérateur, ou None si le message
    n'est pas une décision (ou plus rien à décider) — la conversation continue
    alors normalement avec l'agent.
    """
    mot = re.sub(r"[^\w'à-ÿ-]", "", texte.strip().lower())
    if mot in _OUI:
        approuver = True
    elif mot in _NON:
        approuver = False
    else:
        return None

    proposition_id = _derniere_proposition.get(e164)
    if proposition_id is None:
        return None

    from app.services import supervisor_service  # import tardif (cycle d'import)

    with session_scope() as db:
        existante = db.get(AgentProposal, proposition_id)
        if existante is None or existante.statut != StatutProposition.PROPOSEE:
            # Déjà décidée (depuis le web) ou disparue : le oui/non appartient
            # à la conversation normale avec l'agent.
            _derniere_proposition.pop(e164, None)
            return None
        proposition = supervisor_service.decider(db, proposition_id, approuver=approuver)
        statut = proposition.statut
        resultat = proposition.resultat or ""

    _derniere_proposition.pop(e164, None)
    if not approuver:
        return "OK, je n'exécute pas. La situation reste sous surveillance."
    if statut == StatutProposition.EXECUTEE:
        return f"✅ {resultat}"
    return f"⚠️ {resultat}"
