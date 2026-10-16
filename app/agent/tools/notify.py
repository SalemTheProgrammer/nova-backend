"""Outils agent d'envoi sortant : un message ou un document, par e-mail ou WhatsApp.

« Envoie la procédure de coupe à chef@usine.tn », « préviens le chef de
chaîne par WhatsApp que la chaîne 3 est arrêtée ».
Action SORTANTE (le contenu quitte le système) : confirmation obligatoire.
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.agent.tools import confirmation_gate
from app.core.exceptions import AppError
from app.db.session import session_scope
from app.services import document_files, notify_service

CONFIRMATION_REQUISE = (
    "Confirmation requise : rappelez le destinataire et le canal à l'opérateur et "
    "obtenez son accord explicite avant de rappeler cet outil avec confirmation=true."
)

CANAUX = ("email", "whatsapp")


def _demande_confirmation(libelle: str) -> tuple[str, dict]:
    return (
        f"Confirmation requise : {libelle}. " + CONFIRMATION_REQUISE,
        {"kind": "confirmation_attente", "libelle": libelle},
    )


def _destinataire_canonique(canal: str, destinataire: str) -> str:
    """Forme canonique du destinataire pour la signature du garde-fou :
    « +216 12 345 678 » à la proposition et « +21612345678 » à la confirmation
    doivent matcher. Un numéro invalide est renvoyé tel quel — l'envoi lèvera
    l'erreur explicite au moment voulu."""
    d = destinataire.strip()
    if canal == "whatsapp":
        try:
            return notify_service.normaliser_numero(d)
        except AppError:
            return d
    return d.lower()


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


@tool(response_format="content_and_artifact")
def envoyer_message(
    canal: str, destinataire: str, contenu: str, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """ENVOIE un message libre par `canal` : "email" (adresse e-mail) ou
    "whatsapp" (numéro de téléphone, ex. +216…). Rédige `contenu` toi-même à
    partir de la demande (ex. alerte d'arrêt, point de production, consigne d'équipe).

    À utiliser pour « envoie un message à… », « préviens X que… ».
    ACTION SORTANTE : accord explicite de l'opérateur requis (récapitulez canal,
    destinataire ET contenu) avant de rappeler avec confirmation=true.
    """
    if canal not in CANAUX:
        return f"Canal invalide : {canal!r}. Utilisez \"email\" ou \"whatsapp\".", None
    if not contenu.strip():
        return "Le contenu du message est vide.", None
    destinataire = _destinataire_canonique(canal, destinataire)
    libelle = f"envoyer « {contenu.strip()[:80]} » par {canal} à {destinataire}"
    if not confirmation_gate.evaluer(
        config,
        "envoyer_message",
        {"canal": canal, "destinataire": destinataire, "contenu": contenu.strip()},
        confirmation,
    ):
        return _demande_confirmation(libelle)
    sujet = f"Message de Nova — {datetime.now():%d/%m/%Y %H:%M}"
    try:
        resultat = _envoyer(canal, destinataire, sujet, contenu.strip())
    except AppError as exc:
        return f"❌ {exc.message}", None
    return f"✅ {resultat}", _action_executee(libelle)


@tool(response_format="content_and_artifact")
def envoyer_document(
    nom_document: str, canal: str, destinataire: str, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """ENVOIE un document de la base documentaire (norme, procédure, manuel
    machine, fiche technique…) — le PDF source tel quel — par `canal` : "email"
    ou "whatsapp" (numéro, ex. +216…). `nom_document` : nom approximatif accepté
    (« manuel machine à coudre », « procédure de coupe »).

    À utiliser pour « envoie-moi le manuel… », « transmets la procédure X à… ».
    Ne sert PAS à répondre à une question sur le contenu (→ `rechercher_documents`)
    En cas de doute sur le document visé,
    `lister_documents_disponibles` donne la liste. ACTION SORTANTE : accord
    explicite de l'opérateur requis (récapitulez document + canal + destinataire)
    avant de rappeler avec confirmation=true.
    """
    if canal not in CANAUX:
        return f"Canal invalide : {canal!r}. Utilisez \"email\" ou \"whatsapp\".", None
    destinataire = _destinataire_canonique(canal, destinataire)
    with session_scope() as db:
        document = document_files.trouver_document(db, nom_document)
        if document is None:
            return (
                f"Document introuvable : « {nom_document} ». Appelle "
                "`lister_documents_disponibles` et propose à l'opérateur le bon nom.",
                None,
            )
        nom = document.nom
        libelle = f"envoyer le document « {nom} » par {canal} à {destinataire}"
        if not confirmation_gate.evaluer(
            config,
            "envoyer_document",
            {"canal": canal, "destinataire": destinataire, "document_id": document.id},
            confirmation,
        ):
            return _demande_confirmation(libelle)
        try:
            nom_fichier, pdf = document_files.lire_pdf(document)
        except AppError as exc:
            return f"❌ {exc.message}", None
    sujet = f"Document « {nom} » — {datetime.now():%d/%m/%Y %H:%M}"
    corps = f"{sujet}\nTransmis par Nova."
    try:
        resultat = _envoyer(canal, destinataire, sujet, corps, document=(nom_fichier, pdf))
    except AppError as exc:
        return f"❌ {exc.message}", None
    return (
        f"✅ {resultat} Pièce jointe : {nom_fichier} (« {nom} »).",
        _action_executee(libelle),
    )


def _action_executee(libelle: str) -> dict:
    return {"kind": "action_executee", "libelle": libelle}


NOTIFY_TOOLS = [envoyer_message, envoyer_document]
