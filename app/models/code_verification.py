"""CodeVerification : code à usage unique envoyé par WhatsApp pour la connexion.

Le code n'est jamais stocké en clair (sha256). Un code expire après quelques
minutes (voir `verification_code_ttl_s`), est marqué `consomme` après une
vérification réussie, et le nombre de tentatives est plafonné.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CodeVerification(Base):
    """Un code de vérification à usage unique lié à un numéro de téléphone."""

    __tablename__ = "codes_verification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telephone: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expire_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    tentatives: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consomme: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    date_creation: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
