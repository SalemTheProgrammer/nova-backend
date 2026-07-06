"""Outils agent d'envoi sortant : le bilan ou un message, par e-mail ou WhatsApp.

« Envoie le bilan au +216 12 345 678 », « envoie le rapport à chef@usine.tn »,
« préviens le responsable qualité par WhatsApp que M-01 est en panne ».
Action SORTANTE (le contenu quitte le système) : confirmation obligatoire,
comme les commandes SCADA.
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from app.agent.tools.actions import _trouver_of
from app.core.exceptions import AppError
from app.db.session import session_scope
from app.services import notify_service, pdf_service

CONFIRMATION_REQUISE = (
    "Confirmation requise : rappelez le destinataire et le canal à l'opérateur et "
    "obtenez son accord explicite avant de rappeler cet outil avec confirmation=true."
)

CANAUX = ("email", "whatsapp")


def _envoyer(
    canal: str,
    destinataire: str,
    sujet: str,
    corps: str,
    document: tuple[str, bytes] | None = None,
) -> str:
    if canal == "email":
        return notify_service.envoyer_email(destinataire, sujet, corps, piece_jointe=document)
    return notify_service.envoyer_whatsapp(destinataire, corps, document=document)


@tool
def envoyer_rapport(
    canal: str,
    destinataire: str,
    confirmation: bool,
    of_numero: str | None = None,
) -> str:
    """Génère un bilan en PDF et l'ENVOIE par `canal` : "email" (adresse e-mail,
    PDF en pièce jointe) ou "whatsapp" (numéro de téléphone, ex. +216…, PDF en
    document). Deux bilans possibles :
    - sans `of_numero` : bilan de production de l'équipe (8 dernières heures) ;
    - avec `of_numero` (numéro ou id d'OF) : « Bilan Ordre de Fabrication » de
      cet OF — production, temps AFNOR, KPI (TRS/TRG/TRE), généalogie matière
      première, non-conformités, arrêts.

    À utiliser pour « envoie le bilan à… », « envoie le bilan de l'OF X au… ».
    ACTION SORTANTE : accord explicite de l'opérateur requis (récapitulez canal
    + destinataire) avant de rappeler avec confirmation=true.
    """
    if canal not in CANAUX:
        return f"Canal invalide : {canal!r}. Utilisez \"email\" ou \"whatsapp\"."
    if not confirmation:
        return CONFIRMATION_REQUISE
    horodatage = f"{datetime.now():%d/%m/%Y %H:%M}"
    with session_scope() as db:
        if of_numero:
            of = _trouver_of(db, of_numero)
            if of is None:
                return f"OF introuvable : {of_numero}"
            pdf = pdf_service.generer_bilan_of_pdf(db, of)
            nom_fichier = f"bilan-of-{of.numero.replace('/', '-')}.pdf"
            sujet = f"Bilan OF {of.numero} — {horodatage}"
            contenu = f"bilan de l'OF {of.numero}"
        else:
            pdf = pdf_service.generer_bilan_equipe_pdf(db)
            nom_fichier = "bilan-production.pdf"
            sujet = f"Bilan de production Nova — {horodatage}"
            contenu = "bilan de production de l'équipe"
    corps = f"{sujet}\nBilan en pièce jointe (PDF), généré par Nova."
    try:
        resultat = _envoyer(canal, destinataire, sujet, corps, document=(nom_fichier, pdf))
    except AppError as exc:
        return f"❌ {exc.message}"
    return f"✅ {resultat} Pièce jointe : {nom_fichier} ({contenu})."


@tool
def envoyer_message(canal: str, destinataire: str, contenu: str, confirmation: bool) -> str:
    """ENVOIE un message libre par `canal` : "email" (adresse e-mail) ou
    "whatsapp" (numéro de téléphone, ex. +216…). Rédige `contenu` toi-même à
    partir de la demande (ex. alerte panne, résumé d'un TRS, consigne d'équipe).

    À utiliser pour « envoie un message à… », « préviens X que… ».
    ACTION SORTANTE : accord explicite de l'opérateur requis (récapitulez canal,
    destinataire ET contenu) avant de rappeler avec confirmation=true.
    """
    if canal not in CANAUX:
        return f"Canal invalide : {canal!r}. Utilisez \"email\" ou \"whatsapp\"."
    if not contenu.strip():
        return "Le contenu du message est vide."
    if not confirmation:
        return CONFIRMATION_REQUISE
    sujet = f"Message de Nova — {datetime.now():%d/%m/%Y %H:%M}"
    try:
        resultat = _envoyer(canal, destinataire, sujet, contenu.strip())
    except AppError as exc:
        return f"❌ {exc.message}"
    return f"✅ {resultat}"


NOTIFY_TOOLS = [envoyer_rapport, envoyer_message]
