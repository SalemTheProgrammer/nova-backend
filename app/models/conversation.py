"""Conversation : à qui appartient un thread de l'agent (web ou WhatsApp).

Le checkpointer LangGraph stocke les messages par `thread_id` sans savoir qui
les a écrits. Ce registre fait le lien thread → utilisateur pour la page
d'administration « Conversations » (lecture seule) et pour réserver l'historique
d'un thread web à son auteur.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

CANAL_WEB = "web"
CANAL_WHATSAPP = "whatsapp"


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    # Même identifiant que le checkpointer (UUID côté web, « wa:+216… » côté WhatsApp).
    thread_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    # NULL : numéro WhatsApp autorisé mais non enregistré, ou compte supprimé.
    utilisateur_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    canal: Mapped[str] = mapped_column(String(20), nullable=False)
    # Numéro E.164 de l'interlocuteur WhatsApp (vide côté web).
    numero: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # Début du premier message, pour la liste des conversations.
    apercu: Mapped[str] = mapped_column(String(200), nullable=False, default="")
