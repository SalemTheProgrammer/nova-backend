"""Conversations des utilisateurs avec Nova (web et WhatsApp) : administrateur
uniquement, en LECTURE SEULE — aucune route ici ne modifie ou n'efface un thread.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.runner import get_thread_history
from app.core.exceptions import NotFoundError
from app.core.security import require_admin
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.utilisateur import Utilisateur
from app.schemas.conversation import ConversationDetail, ConversationResume
from app.services import conversation_service

router = APIRouter(
    prefix="/admin/conversations", tags=["admin"], dependencies=[Depends(require_admin)]
)


def _resume(conv: Conversation, compte: Utilisateur | None) -> ConversationResume:
    return ConversationResume(
        thread_id=conv.thread_id,
        canal=conv.canal,
        utilisateur_id=conv.utilisateur_id,
        utilisateur_nom=(compte.nom_complet if compte else "") or "",
        telephone=(compte.telephone if compte else conv.numero) or "",
        apercu=conv.apercu,
        date_creation=conv.date_creation,
        date_maj=conv.date_maj,
    )


@router.get("", response_model=list[ConversationResume])
async def lister(
    utilisateur_id: int | None = Query(None),
    db: Session = Depends(get_db),
) -> list[ConversationResume]:
    """Conversations, de la plus récente à la plus ancienne (filtrables par compte)."""
    await asyncio.to_thread(conversation_service.synchroniser_whatsapp)
    requete = select(Conversation, Utilisateur).outerjoin(
        Utilisateur, Conversation.utilisateur_id == Utilisateur.id
    )
    if utilisateur_id is not None:
        requete = requete.where(Conversation.utilisateur_id == utilisateur_id)
    lignes = db.execute(requete.order_by(Conversation.date_maj.desc())).all()
    return [_resume(conv, compte) for conv, compte in lignes]


@router.get("/{thread_id}", response_model=ConversationDetail)
async def detail(thread_id: str, db: Session = Depends(get_db)) -> ConversationDetail:
    """Messages d'une conversation (questions, réponses et outils utilisés)."""
    conv = db.get(Conversation, thread_id)
    if conv is None:
        raise NotFoundError("Conversation introuvable.")
    compte = db.get(Utilisateur, conv.utilisateur_id) if conv.utilisateur_id else None
    turns = await get_thread_history(thread_id)
    for turn in turns:
        if turn["role"] == "user":
            for segment in turn["segments"]:
                if segment.get("type") == "text":
                    segment["content"] = conversation_service.sans_prefixe_identite(
                        segment.get("content") or ""
                    )
    return ConversationDetail(conversation=_resume(conv, compte), turns=turns)
