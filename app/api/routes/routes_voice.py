"""Voix : transcription (STT) et synthèse (TTS) via l'API audio OpenAI.

L'opérateur parle à Nova (micro → /voice/transcribe → texte) et Nova répond à
voix haute (/voice/speak → mp3). Les appels OpenAI sont synchrones : ils sont
déportés dans un thread pour ne pas bloquer la boucle.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import require_api_key

router = APIRouter(prefix="/voice", tags=["voice"], dependencies=[Depends(require_api_key)])
logger = get_logger(__name__)

MAX_AUDIO_BYTES = 20 * 1024 * 1024


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class TranscriptionResponse(BaseModel):
    text: str


@lru_cache
def _client():
    from openai import OpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=settings.openai_api_key)


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(file: UploadFile = File(...)) -> TranscriptionResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Fichier audio vide.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Audio trop volumineux.")
    settings = get_settings()

    def _call() -> str:
        # Pas de `language` figé : détection automatique — l'opérateur peut
        # parler français ou anglais, Nova répond dans la même langue.
        result = _client().audio.transcriptions.create(
            model=settings.stt_model,
            file=(file.filename or "audio.webm", data),
        )
        return result.text

    try:
        text = await asyncio.to_thread(_call)
    except Exception as exc:  # noqa: BLE001
        logger.exception("voice_transcribe_failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Transcription échouée : {exc}")
    return TranscriptionResponse(text=text)


@router.post("/speak")
async def speak(payload: SpeakRequest) -> Response:
    settings = get_settings()

    def _call() -> bytes:
        response = _client().audio.speech.create(
            model=settings.tts_model,
            voice=settings.tts_voice,
            input=payload.text,
            response_format="mp3",
        )
        return response.content

    try:
        audio = await asyncio.to_thread(_call)
    except Exception as exc:  # noqa: BLE001
        logger.exception("voice_speak_failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Synthèse vocale échouée : {exc}")
    return Response(content=audio, media_type="audio/mpeg")
