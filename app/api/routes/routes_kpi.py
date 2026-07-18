"""TRS/TRG/TRE (AFNOR), résumé tableau de bord, et insights IA instantanés."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Machine, OrdreFabrication
from app.schemas.ai_schema import InsightsRead
from app.schemas.kpi_schema import (
    ActiviteRead,
    ArretCategorieRead,
    CauseArretResumeRead,
    DashboardResumeRead,
    MatiereConsommeeRead,
    OFActifRead,
    PertesRead,
    PointOEERead,
    PointSerieRead,
    TempsModelRead,
    TRSRead,
)
from app.schemas.kpi_schema import AlertRead
from app.services import ai_agent_service, dashboard_service, trs_service

router = APIRouter(
    tags=["kpi"],
    dependencies=[Depends(require_api_key), Depends(require_category("Supervision / MES"))],
)


def _trs_read(scope: str, scope_id: int | None, resultat: trs_service.TRSResult) -> TRSRead:
    return TRSRead(
        scope=scope,
        scope_id=scope_id,
        temps=TempsModelRead(**resultat.temps.__dict__),
        tq=resultat.tq,
        tp=resultat.tp,
        do=resultat.do,
        trs=resultat.trs,
        trg=resultat.trg,
        tre=resultat.tre,
        pertes=PertesRead(
            disponibilite_s=resultat.pertes.disponibilite_s,
            performance_s=resultat.pertes.performance_s,
            qualite_s=resultat.pertes.qualite_s,
            principale=resultat.pertes.principale,
        ),
        quantite_bonne=resultat.quantite_bonne,
        quantite_rejetee=resultat.quantite_rejetee,
    )


@router.get("/kpi/trs", response_model=TRSRead)
def trs(
    scope: Literal["machine", "ligne", "of"] = Query(...),
    id: int = Query(...),
    debut: datetime | None = Query(default=None),
    fin: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
) -> TRSRead:
    if scope == "machine":
        machine = db.get(Machine, id)
        if machine is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine introuvable")
        return _trs_read(
            scope, id, trs_service.calculer_trs_machine(db, machine, depuis=debut, jusqua=fin)
        )

    if scope == "ligne":
        machines = list(
            db.execute(select(Machine).where(Machine.ligne_production_id == id)).scalars()
        )
        resultat = trs_service.calculer_trs_ligne(db, machines, depuis=debut, jusqua=fin)
        if resultat is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Aucune machine avec un temps de cycle cible sur cette ligne"
            )
        return _trs_read(scope, id, resultat)

    of = db.get(OrdreFabrication, id)
    if of is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ordre de fabrication introuvable")
    return _trs_read(scope, id, trs_service.calculer_trs_ordre(db, of))


# Le résumé recompute le TRS de la fenêtre 8 h depuis les événements bruts à
# chaque appel (~0,5-3 s) et chaque client ouvert le poll à ~1/s : un cache
# court mutualise le calcul entre les clients sans staleness perceptible.
_RESUME_CACHE_TTL_S = 2.0
_resume_cache: dict[int | None, tuple[float, DashboardResumeRead]] = {}
# L'historique OEE recalcule le TRS de chaque bucket (7 jours / 30 jours) : le
# plus lourd de tous les endpoints, pour une courbe qui ne bouge qu'à l'heure.
_HISTORY_CACHE_TTL_S = 60.0
_history_cache: dict[tuple[int | None, str], tuple[float, list[PointOEERead]]] = {}


@router.get("/dashboard/resume", response_model=DashboardResumeRead)
def dashboard_resume(
    ligne_id: int | None = Query(default=None), db: Session = Depends(get_db)
) -> DashboardResumeRead:
    now = time.monotonic()
    cached = _resume_cache.get(ligne_id)
    if cached is not None and now - cached[0] < _RESUME_CACHE_TTL_S:
        return cached[1]
    r = dashboard_service.construire_resume(db, ligne_id=ligne_id)
    lecture = DashboardResumeRead(
        trs_global=r.trs_global,
        disponibilite=r.disponibilite,
        performance=r.performance,
        qualite=r.qualite,
        trs_detail=_trs_read("global", None, r.trs_detail) if r.trs_detail else None,
        machines_en_marche=r.machines_en_marche,
        machines_arretees=r.machines_arretees,
        machines_total=r.machines_total,
        ordres_actifs=r.ordres_actifs,
        production_reelle=r.production_reelle,
        production_cible=r.production_cible,
        quantite_bonne=r.quantite_bonne,
        quantite_rejetee=r.quantite_rejetee,
        temps_arret_total_s=r.temps_arret_total_s,
        mttr_s=r.mttr_s,
        mtbf_s=r.mtbf_s,
        mttf_s=r.mttf_s,
        nb_pannes=r.nb_pannes,
        top_causes_arret=[
            CauseArretResumeRead(cause=c.cause, duree_s=c.duree_s) for c in r.top_causes_arret
        ],
        alertes_actives=[
            AlertRead(
                id=a.id,
                machine_id=a.machine_id,
                ordre_fabrication_id=a.ordre_fabrication_id,
                severity=a.severity.value,
                type=a.type,
                message=a.message,
                created_at=a.created_at,
                resolved=a.resolved,
            )
            for a in r.alertes_actives
        ],
        serie_production=[
            PointSerieRead(horodatage=p.horodatage, quantite_bonne_cumulee=p.quantite_bonne_cumulee)
            for p in r.serie_production
        ],
        cadence_actuelle_par_min=r.cadence_actuelle_par_min,
        activite_recente=[
            ActiviteRead(
                id=a.id,
                machine_id=a.machine_id,
                code_machine=a.code_machine,
                type=a.type,
                payload=a.payload,
                created_at=a.created_at,
            )
            for a in r.activite_recente
        ],
        of_actif=(
            OFActifRead(
                id=r.of_actif.id,
                numero=r.of_actif.numero,
                article_code=r.of_actif.article_code,
                article_designation=r.of_actif.article_designation,
                lot_produit=r.of_actif.lot_produit,
                quantite_planifiee=r.of_actif.quantite_planifiee,
                quantite_bonne=r.of_actif.quantite_bonne,
                quantite_rejetee=r.of_actif.quantite_rejetee,
                statut=r.of_actif.statut,
                ligne_production_id=r.of_actif.ligne_production_id,
            )
            if r.of_actif
            else None
        ),
        taux_charge=r.taux_charge,
        taux_engagement=r.taux_engagement,
        cadence_nominale_par_min=r.cadence_nominale_par_min,
        production_theorique=r.production_theorique,
        reste_a_produire=r.reste_a_produire,
        arrets_planifies=ArretCategorieRead(
            nb_actifs=r.arrets_planifies.nb_actifs, duree_totale_s=r.arrets_planifies.duree_totale_s
        ),
        arrets_non_planifies=ArretCategorieRead(
            nb_actifs=r.arrets_non_planifies.nb_actifs,
            duree_totale_s=r.arrets_non_planifies.duree_totale_s,
        ),
        micro_arrets_nombre=r.micro_arrets_nombre,
        matieres_consommees=[
            MatiereConsommeeRead(
                code_mp=m.code_mp,
                designation_mp=m.designation_mp,
                numero_lot=m.numero_lot,
                quantite=m.quantite,
            )
            for m in r.matieres_consommees
        ],
    )
    _resume_cache[ligne_id] = (now, lecture)
    return lecture


@router.get("/kpi/oee-history", response_model=list[PointOEERead])
def oee_history(
    ligne_id: int | None = Query(default=None),
    periode: Literal["day", "week", "month"] = Query(default="week"),
    db: Session = Depends(get_db),
) -> list[PointOEERead]:
    now = time.monotonic()
    cached = _history_cache.get((ligne_id, periode))
    if cached is not None and now - cached[0] < _HISTORY_CACHE_TTL_S:
        return cached[1]
    points = dashboard_service.construire_historique_oee(db, ligne_id=ligne_id, periode=periode)
    lecture = [
        PointOEERead(
            label=p.label,
            horodatage=p.horodatage,
            disponibilite=p.disponibilite,
            performance=p.performance,
            qualite=p.qualite,
            trs=p.trs,
        )
        for p in points
    ]
    _history_cache[(ligne_id, periode)] = (now, lecture)
    return lecture


# Les insights recalculent le TRS de chaque machine à chaque appel : coûteux,
# et tous les panneaux IA ouverts posent la même question. Un cache de 10 s
# rend la réponse instantanée pour les suiveurs sans staleness perceptible
# (le texte est consultatif, pas un compteur temps réel).
_INSIGHTS_CACHE_TTL_S = 10.0
_insights_cache: tuple[float, list[str]] | None = None


@router.get("/ai/insights", response_model=InsightsRead)
def ai_insights(db: Session = Depends(get_db)) -> InsightsRead:
    global _insights_cache
    now = time.monotonic()
    if _insights_cache is not None and now - _insights_cache[0] < _INSIGHTS_CACHE_TTL_S:
        return InsightsRead(insights=_insights_cache[1])
    insights = ai_agent_service.generer_insights(db)
    _insights_cache = (now, insights)
    return InsightsRead(insights=insights)
