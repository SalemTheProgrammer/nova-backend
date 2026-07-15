"""EnvoiPlanifie : envoi sortant différé, programmé depuis une conversation.

« Dans 5 minutes, envoie le bilan au +216… » (WhatsApp ou chat web) : l'agent
confirme puis enregistre l'envoi ici ; la boucle `scheduler_service` l'exécute
à l'heure dite. Persisté en base : un redémarrage du backend ne perd pas les
envois programmés.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Statuts : EN_ATTENTE → ENVOYE | ECHEC | ANNULE | MANQUE (backend éteint trop
# longtemps : on n'envoie pas un bilan périmé sans prévenir).
STATUT_EN_ATTENTE = "EN_ATTENTE"
STATUT_ENVOYE = "ENVOYE"
STATUT_ECHEC = "ECHEC"
STATUT_ANNULE = "ANNULE"
STATUT_MANQUE = "MANQUE"

# Ce qui est envoyé : bilan (équipe ou OF si of_numero), document de la base
# documentaire (document_id), ou message texte libre (contenu).
TYPE_BILAN = "bilan"
TYPE_DOCUMENT = "document"
TYPE_MESSAGE = "message"


class EnvoiPlanifie(Base):
    __tablename__ = "envoi_planifie"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_envoi: Mapped[str] = mapped_column(String(20), nullable=False)
    canal: Mapped[str] = mapped_column(String(20), nullable=False)
    destinataire: Mapped[str] = mapped_column(String(255), nullable=False)
    # Bilan d'un OF précis (sinon bilan d'équipe) — résolu à l'envoi, données fraîches.
    of_numero: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Document de la base documentaire (PDF source envoyé tel quel).
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Message texte libre (type_envoi = "message").
    contenu: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    execute_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    statut: Mapped[str] = mapped_column(
        String(20), default=STATUT_EN_ATTENTE, index=True, nullable=False
    )
    # Qui a programmé l'envoi (ex. numéro WhatsApp de l'opérateur) — traçabilité.
    demande_par: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Essais d'envoi déjà effectués (erreurs transitoires retentées par la boucle).
    tentatives: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resultat: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def libelle(self) -> str:
        """Description lisible de l'envoi, pour les confirmations et les listes."""
        if self.type_envoi == TYPE_BILAN:
            quoi = f"le bilan de l'OF {self.of_numero}" if self.of_numero else "le bilan d'équipe"
        elif self.type_envoi == TYPE_DOCUMENT:
            quoi = f"le document n°{self.document_id}"
        else:
            apercu = (self.contenu or "").strip()[:60]
            quoi = f"le message « {apercu}… »" if len(self.contenu or "") > 60 else f"le message « {apercu} »"
        return f"{quoi} par {self.canal} à {self.destinataire}"
