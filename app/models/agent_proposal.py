"""AgentProposal : proposition d'action émise par le superviseur autonome Nova.

Le superviseur détecte un problème (arrêt bloquant, dérive qualité, stock bas),
construit une proposition avec diagnostic + action structurée, et attend la
décision de l'opérateur (human-in-the-loop). L'action n'est exécutée qu'après
approbation explicite — règle BPF du système.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import SeveriteAlerte, StatutProposition
from app.models.fabrication import OrdreFabrication
from app.models.machine import Machine


class AgentProposal(Base):
    __tablename__ = "agent_proposal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Clé de déduplication : un même problème ne génère qu'une proposition.
    cle_dedup: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    severite: Mapped[SeveriteAlerte] = mapped_column(SAEnum(SeveriteAlerte), nullable=False)
    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    # Diagnostic complet en français, affiché à l'opérateur.
    diagnostic: Mapped[str] = mapped_column(String(2000), nullable=False)
    # Libellé court de l'action proposée (bouton).
    action_libelle: Mapped[str] = mapped_column(String(255), nullable=False)
    # Action structurée exécutable : {"type": "...", ...params}.
    action: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    statut: Mapped[StatutProposition] = mapped_column(
        SAEnum(StatutProposition), default=StatutProposition.PROPOSEE, index=True, nullable=False
    )
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machine.id"), nullable=True)
    ordre_fabrication_id: Mapped[int | None] = mapped_column(
        ForeignKey("ordre_fabrication.id"), nullable=True
    )
    resultat: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Qui a décidé : "operateur" (bouton web, WhatsApp oui/non) ou "autopilote"
    # (mode assisté/autopilote — voir supervisor_service._appliquer_autonomie).
    decideur: Mapped[str] = mapped_column(String(20), default="operateur", nullable=False)
    # Horodatage de l'exécution automatique programmée (mode autopilote, risque
    # MOYEN uniquement) : la proposition reste PROPOSEE et décidable par
    # l'opérateur jusqu'à cet instant, où le superviseur l'exécute lui-même si
    # personne n'a tranché. None = pas de compte à rebours en cours.
    execution_auto_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    machine: Mapped[Machine | None] = relationship()
    ordre_fabrication: Mapped[OrdreFabrication | None] = relationship()
