"""Administration de l'atelier : remise à zéro de l'historique d'exécution."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.routes_kpi import clear_kpi_caches
from app.core.security import require_admin, require_api_key
from app.db.session import get_db
from app.models import Machine
from app.models.utilisateur import Utilisateur
from app.protocols.sparkplug_b import runtime
from app.services import atelier_reset_service, audit_service, broadcast_service

router = APIRouter(
    prefix="/admin/atelier", tags=["admin"], dependencies=[Depends(require_api_key)]
)


class ResetRead(BaseModel):
    evenements_machine: int
    arrets: int
    evenements_qualite: int
    maintenances: int
    alertes: int
    propositions: int
    machines: int
    ordres_reinitialises: int
    rebirths_demandes: int


@router.post("/reset", response_model=ResetRead)
def reinitialiser_historique(
    user: Utilisateur = Depends(require_admin), db: Session = Depends(get_db)
) -> ResetRead:
    """Efface l'historique d'exécution (événements, arrêts, qualité, alertes…)
    et remet les compteurs à zéro. Référentiel, stock et généalogie conservés."""
    resume = atelier_reset_service.reinitialiser_historique(db)
    db.commit()
    clear_kpi_caches()
    audit_service.enregistrer_action(
        action="reset_historique_atelier",
        arguments=asdict(resume),
        source="admin",
        canal="web",
        identite=user.telephone,
        resultat="ok",
    )

    # Les automates republient leur état réel : le MES se resynchronise.
    host = runtime.get_host()
    rebirths = host.request_rebirth_all() if host is not None and host.connected else 0

    broadcast_service.diffuser({"type": "reset"})
    broadcast_service.diffuser_machines_par_id(list(db.execute(select(Machine.id)).scalars()))
    return ResetRead(**asdict(resume), rebirths_demandes=rebirths)
