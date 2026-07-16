"""Journal d'audit des actions sur l'atelier (esprit BPF / Annexe 11).

Chaque action IRRÉVERSIBLE ou SORTANTE qui s'exécute réellement laisse une
trace attribuée : quel outil, quels arguments, depuis quel canal, décidée par
qui. Les entrées ne sont jamais modifiées ni supprimées par l'application —
c'est un journal en ajout seul.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    horodatage: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    # Ce qui a été exécuté.
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    arguments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # D'où vient la décision : "agent" (outil confirmé en conversation) ou
    # "superviseur" (proposition approuvée).
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    # Canal de la décision : "web", "whatsapp", "voix"…
    canal: Mapped[str] = mapped_column(String(30), nullable=False)
    # Identité de l'opérateur quand elle est connue (numéro WhatsApp E.164) ;
    # "console-web" sinon (pas encore de login sur l'interface web).
    identite: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Fil de conversation à l'origine de l'action (rejouable pour enquête).
    thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resultat: Mapped[str | None] = mapped_column(String(500), nullable=True)
