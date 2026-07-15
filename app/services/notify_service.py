"""Envoi de messages sortants par Nova : e-mail (SMTP) et WhatsApp (Baileys).

Utilisé par les outils agent `envoyer_rapport` / `envoyer_message` : l'opérateur
demande « envoie le bilan à ce numéro / cet e-mail », l'agent confirme puis
appelle ce service. WhatsApp passe par le petit service Node local (dossier
`whatsapp/`, bibliothèque Baileys = WhatsApp Web) — un scan de QR code une
seule fois, aucun compte ni API payante. Chaque canal explique lui-même quoi
faire s'il n'est pas prêt — l'agent transmet le message tel quel à l'opérateur.
"""
from __future__ import annotations

import base64
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ConfigurationError
from app.core.logging import get_logger

logger = get_logger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Coupe les messages WhatsApp trop longs (lisibilité, pas une limite technique).
WHATSAPP_MAX_CHARS = 3500


class NotifyError(AppError):
    status_code = 502
    code = "notify_error"


def nettoyer_markdown(texte: str) -> str:
    """Markdown → texte brut lisible dans un e-mail simple ou WhatsApp."""
    texte = re.sub(r"^#{1,6}\s*", "", texte, flags=re.MULTILINE)  # titres
    texte = texte.replace("**", "").replace("__", "").replace("`", "")
    return texte.strip()


def normaliser_numero(numero: str) -> str:
    """Normalise un numéro en E.164. Confort démo : 8 chiffres = mobile tunisien
    (+216). Sinon le numéro doit inclure l'indicatif pays (+…)."""
    brut = re.sub(r"[\s().-]", "", numero)
    if brut.startswith("whatsapp:"):
        brut = brut[len("whatsapp:") :]
    if brut.startswith("00"):
        brut = "+" + brut[2:]
    if re.fullmatch(r"\d{8}", brut):
        brut = "+216" + brut
    if not re.fullmatch(r"\+\d{8,15}", brut):
        raise NotifyError(
            f"Numéro invalide : {numero!r}. Donnez un numéro avec l'indicatif pays "
            "(ex. +216 12 345 678)."
        )
    return brut


def envoyer_email(
    destinataire: str,
    sujet: str,
    corps: str,
    piece_jointe: tuple[str, bytes] | None = None,
) -> str:
    """Envoie un e-mail texte via SMTP (STARTTLS), avec pièce jointe optionnelle
    `(nom_fichier, contenu)`. Renvoie un résumé lisible."""
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        raise ConfigurationError(
            "L'envoi d'e-mails n'est pas configuré : renseignez SMTP_HOST, SMTP_USER "
            "et SMTP_PASSWORD dans le .env du backend."
        )
    if not EMAIL_RE.fullmatch(destinataire.strip()):
        raise NotifyError(f"Adresse e-mail invalide : {destinataire!r}.")
    destinataire = destinataire.strip()
    expediteur = settings.smtp_from or settings.smtp_user

    msg = MIMEMultipart()
    msg["From"] = expediteur
    msg["To"] = destinataire
    msg["Subject"] = sujet
    msg.attach(MIMEText(corps, "plain", "utf-8"))
    if piece_jointe is not None:
        nom_fichier, contenu = piece_jointe
        piece = MIMEApplication(contenu, _subtype="pdf")
        piece.add_header("Content-Disposition", "attachment", filename=nom_fichier)
        msg.attach(piece)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.sendmail(expediteur, [destinataire], msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception("notify_email_failed", destinataire=destinataire)
        raise NotifyError(f"Envoi e-mail échoué : {exc}") from exc
    logger.info("notify_email_sent", destinataire=destinataire, sujet=sujet)
    return f"E-mail envoyé à {destinataire} (objet : « {sujet} »)."


def envoyer_whatsapp(
    numero: str,
    corps: str,
    document: tuple[str, bytes] | None = None,
    image: bytes | None = None,
) -> str:
    """Envoie un message WhatsApp via le service Baileys local, avec document
    PDF optionnel `(nom_fichier, contenu)` ou image PNG (le message devient la
    légende). Renvoie un résumé lisible."""
    settings = get_settings()
    e164 = normaliser_numero(numero)
    if len(corps) > WHATSAPP_MAX_CHARS:
        corps = corps[: WHATSAPP_MAX_CHARS - 1] + "…"

    payload: dict = {"to": e164, "message": corps}
    if document is not None:
        nom_fichier, contenu = document
        payload["filename"] = nom_fichier
        payload["document_base64"] = base64.b64encode(contenu).decode("ascii")
    elif image is not None:
        payload["image_base64"] = base64.b64encode(image).decode("ascii")

    url = settings.whatsapp_service_url.rstrip("/") + "/send"
    try:
        reponse = httpx.post(url, json=payload, timeout=60)
    except httpx.HTTPError as exc:
        logger.warning("notify_whatsapp_service_unreachable", url=url, error=str(exc))
        raise ConfigurationError(
            "Le service WhatsApp n'est pas démarré : lancez `npm start` dans le "
            "dossier whatsapp/ du projet et scannez le QR code une fois."
        ) from exc
    if reponse.status_code == 409:
        raise NotifyError(
            "Session WhatsApp non appairée : scannez le QR code affiché dans le "
            "terminal du service WhatsApp (WhatsApp → Appareils connectés)."
        )
    if reponse.status_code >= 400:
        detail = ""
        try:
            detail = reponse.json().get("error", "")
        except Exception:  # noqa: BLE001
            detail = reponse.text[:200]
        logger.error("notify_whatsapp_rejected", numero=e164, detail=detail)
        raise NotifyError(f"Envoi WhatsApp refusé : {detail}")
    logger.info("notify_whatsapp_sent", numero=e164)
    return f"Message WhatsApp envoyé au {e164}."
