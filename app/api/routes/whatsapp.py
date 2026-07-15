"""WhatsApp entrant : n'importe qui (autorisé) écrit au numéro WhatsApp de Nova
et dialogue directement avec l'agent — texte, message vocal ou photo.

Le service Baileys (dossier `whatsapp/`) écoute les messages reçus et les poste
ici ; la réponse de l'agent lui est renvoyée et il l'envoie à l'expéditeur.
Chaque numéro a son propre `thread_id` : la mémoire de conversation (et donc
les confirmations « oui » avant une action) fonctionne comme dans le chat web.

Cas particuliers gérés avant l'agent :
- « oui »/« non » en réponse à une proposition proactive du superviseur
  (voir `proactive_service`) → décision directe, sans passer par le LLM.
- Message vocal → transcription STT, et la réponse repart AUSSI en vocal (TTS).
- Photo → analyse par le modèle vision, injectée dans le message pour l'agent.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.agent.runner import run_agent_avec_artifacts
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.security import require_api_key
from app.services import chart_image_service, proactive_service
from app.services.notify_service import WHATSAPP_MAX_CHARS, normaliser_numero

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"], dependencies=[Depends(require_api_key)])
logger = get_logger(__name__)

MAX_MEDIA_BYTES = 20 * 1024 * 1024
TTS_MAX_CHARS = 600
FALLBACK_AUDIO = (
    "Je n'ai pas réussi à écouter ce message vocal — peux-tu me l'écrire en texte ?"
)
FALLBACK_IMAGE = "Je n'ai pas réussi à analyser cette photo — peux-tu la renvoyer ?"

VISION_PROMPT = (
    "Tu es l'inspecteur qualité d'une usine pharmaceutique (BPF/GMP). Décris ce "
    "que montre cette photo prise sur la ligne de production (produit, blisters, "
    "comprimés, équipement, étiquetage) et liste les défauts visibles : comprimé "
    "cassé/manquant/décoloré, blister mal scellé ou vide, corps étranger, "
    "étiquette illisible, encrassement machine… Si rien d'anormal, dis-le. "
    "3 phrases maximum, en français, factuel."
)


class WhatsAppInbound(BaseModel):
    from_number: str = Field(..., description="Numéro de l'expéditeur (E.164 ou 8 chiffres TN)")
    message: str | None = Field(default=None, max_length=8000)
    audio_base64: str | None = Field(default=None, description="Message vocal (ogg/opus) en base64")
    image_base64: str | None = Field(default=None, description="Photo (jpeg/png) en base64")
    image_mimetype: str | None = Field(default=None, max_length=100)
    push_name: str | None = Field(default=None, max_length=120)


class WhatsAppImage(BaseModel):
    """Image PNG à envoyer dans la conversation (graphique, jauge…)."""

    filename: str
    base64: str
    caption: str | None = None


class WhatsAppReply(BaseModel):
    reply: str
    # Rempli quand l'entrée était un vocal : la réponse repart aussi en note
    # vocale (opus/ogg, format natif WhatsApp).
    reply_audio_base64: str | None = None
    # Graphiques/jauges générés pendant ce tour, rendus en PNG côté backend.
    reply_images: list[WhatsAppImage] = []


def formater_pour_whatsapp(texte: str) -> str:
    """Markdown → mise en forme WhatsApp (*gras* à un astérisque, pas de titres)."""
    texte = re.sub(r"^#{1,6}\s*", "", texte, flags=re.MULTILINE)
    texte = re.sub(r"\*\*(.+?)\*\*", r"*\1*", texte, flags=re.DOTALL)
    texte = texte.replace("__", "").replace("`", "")
    texte = texte.strip()
    if len(texte) > WHATSAPP_MAX_CHARS:
        texte = texte[: WHATSAPP_MAX_CHARS - 1] + "…"
    return texte


def _numero_autorise(e164: str) -> bool:
    """Liste blanche `WHATSAPP_ALLOWED_NUMBERS` (CSV). Vide = tout le monde (démo)."""
    settings = get_settings()
    if not settings.whatsapp_allowed_numbers:
        return True
    for num in settings.whatsapp_allowed_numbers:
        try:
            if normaliser_numero(num) == e164:
                return True
        except AppError:
            continue
    return False


def _decoder_base64(donnees_b64: str, *, quoi: str) -> bytes:
    try:
        donnees = base64.b64decode(donnees_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{quoi} base64 invalide.") from exc
    if not donnees or len(donnees) > MAX_MEDIA_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{quoi} vide ou trop volumineux.")
    return donnees


def _client_openai():
    from openai import OpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=settings.openai_api_key)


def _transcrire_vocal(donnees: bytes) -> str:
    """Transcrit un vocal WhatsApp (ogg/opus) avec le même STT que le mode voix."""
    settings = get_settings()
    result = _client_openai().audio.transcriptions.create(
        model=settings.stt_model,
        file=("vocal.ogg", donnees),
    )
    return result.text


def _synthetiser_vocal(texte: str) -> bytes:
    """Synthétise la réponse en opus (le format des notes vocales WhatsApp)."""
    settings = get_settings()
    parle = texte.replace("*", "").replace("_", "")
    if len(parle) > TTS_MAX_CHARS:
        parle = parle[:TTS_MAX_CHARS] + "…"
    response = _client_openai().audio.speech.create(
        model=settings.tts_model,
        voice=settings.tts_voice,
        input=parle,
        response_format="opus",
    )
    return response.content


def _analyser_photo(donnees: bytes, mimetype: str) -> str:
    """Décrit une photo de la ligne avec le modèle vision (inspection qualité)."""
    settings = get_settings()
    b64 = base64.b64encode(donnees).decode("ascii")
    result = _client_openai().chat.completions.create(
        model=settings.vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mimetype};base64,{b64}"}},
                ],
            }
        ],
        max_tokens=300,
    )
    return (result.choices[0].message.content or "").strip()


@router.post("/inbound", response_model=WhatsAppReply)
async def whatsapp_inbound(payload: WhatsAppInbound) -> WhatsAppReply:
    settings = get_settings()
    if not settings.whatsapp_inbound_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "WhatsApp entrant désactivé.")

    try:
        e164 = normaliser_numero(payload.from_number)
    except AppError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from exc
    if not _numero_autorise(e164):
        logger.warning("whatsapp_inbound_refuse", numero=e164)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Numéro non autorisé.")

    message = (payload.message or "").strip()
    entree_vocale = bool(payload.audio_base64) and not message

    if entree_vocale:
        donnees = _decoder_base64(payload.audio_base64 or "", quoi="Audio")
        try:
            message = (await asyncio.to_thread(_transcrire_vocal, donnees)).strip()
        except Exception:  # noqa: BLE001
            logger.exception("whatsapp_vocal_transcription_failed", numero=e164)
            return WhatsAppReply(reply=FALLBACK_AUDIO)

    if not message and not payload.image_base64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message vide.")

    logger.info(
        "whatsapp_inbound",
        numero=e164,
        push_name=payload.push_name,
        vocal=entree_vocale,
        photo=bool(payload.image_base64),
    )

    # « oui »/« non » à une proposition proactive du superviseur : décision
    # directe (human-in-the-loop), sans passer par l'agent.
    if message and not payload.image_base64:
        decision = await asyncio.to_thread(
            proactive_service.traiter_reponse_operateur, e164, message
        )
        if decision is not None:
            return await _reponse(decision, entree_vocale, e164, [])

    # Photo : analyse vision injectée dans le message — l'agent commente,
    # rapproche des seuils qualité et peut proposer une action.
    if payload.image_base64:
        donnees = _decoder_base64(payload.image_base64, quoi="Image")
        mimetype = payload.image_mimetype or "image/jpeg"
        try:
            analyse = await asyncio.to_thread(_analyser_photo, donnees, mimetype)
        except Exception:  # noqa: BLE001
            logger.exception("whatsapp_photo_analyse_failed", numero=e164)
            return WhatsAppReply(reply=FALLBACK_IMAGE)
        message = (
            f"[Photo envoyée par l'opérateur — analyse visuelle automatique : {analyse}] "
            f"{message or 'Que penses-tu de cette photo ?'}"
        )

    # Préfixe d'identité : l'agent sait à qui il parle (« envoie-moi le bilan »
    # → envoyer_rapport vers ce numéro). Le prompt lui interdit de l'afficher.
    qui = f"{payload.push_name} {e164}" if payload.push_name else e164
    message = f"[WhatsApp — {qui}] {message}"
    # Un thread par numéro : mémoire multi-tours et confirmations comme sur le web.
    reponse, artifacts = await run_agent_avec_artifacts(
        message, thread_id=f"wa:{e164}", mode="whatsapp"
    )
    images = await asyncio.to_thread(_rendre_artifacts_en_images, artifacts, e164)
    return await _reponse(reponse, entree_vocale, e164, images)


def _rendre_artifacts_en_images(artifacts: list[dict], e164: str) -> list[WhatsAppImage]:
    """Graphiques et jauges du tour → PNG (matplotlib) prêts à partir en images."""
    images: list[WhatsAppImage] = []
    for i, artifact in enumerate(artifacts):
        kind = artifact.get("kind")
        try:
            if kind == "chart":
                png = chart_image_service.rendre_graphique_png(artifact)
                titre = artifact.get("title") or "graphique"
            elif kind == "gauge":
                png = chart_image_service.rendre_jauge_png(
                    artifact.get("title") or "Jauge",
                    float(artifact.get("valeur_pct") or 0.0),
                    artifact.get("objectif_pct"),
                    artifact.get("sous_titre"),
                )
                titre = artifact.get("title") or "jauge"
            else:
                continue
        except Exception:  # noqa: BLE001
            logger.exception("whatsapp_rendu_image_failed", numero=e164, kind=kind)
            continue
        nom = re.sub(r"[^a-z0-9]+", "-", titre.lower()).strip("-")[:60] or f"visuel-{i}"
        images.append(
            WhatsAppImage(
                filename=f"{nom}.png",
                base64=base64.b64encode(png).decode("ascii"),
                caption=titre,
            )
        )
    return images


async def _reponse(
    texte: str, entree_vocale: bool, e164: str, images: list[WhatsAppImage]
) -> WhatsAppReply:
    """Formate la réponse ; si l'entrée était un vocal, ajoute la version audio."""
    reply = formater_pour_whatsapp(texte)
    audio_b64: str | None = None
    if entree_vocale:
        try:
            audio = await asyncio.to_thread(_synthetiser_vocal, reply)
            audio_b64 = base64.b64encode(audio).decode("ascii")
        except Exception:  # noqa: BLE001
            logger.warning("whatsapp_tts_failed", numero=e164)
    return WhatsAppReply(reply=reply, reply_audio_base64=audio_b64, reply_images=images)
