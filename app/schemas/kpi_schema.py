"""Pydantic schemas for TRS/TRG/TRE and the dashboard summary."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class TempsModelRead(BaseModel):
    tt: Decimal
    to: Decimal
    tr: Decimal
    tf: Decimal
    tn: Decimal
    tu: Decimal


class PertesRead(BaseModel):
    disponibilite_s: Decimal
    performance_s: Decimal
    qualite_s: Decimal
    principale: str


class TRSRead(BaseModel):
    scope: str
    scope_id: int | None
    temps: TempsModelRead
    tq: Decimal
    tp: Decimal
    do: Decimal
    trs: Decimal
    trg: Decimal
    tre: Decimal
    pertes: PertesRead
    quantite_bonne: int
    quantite_rejetee: int


class CauseArretResumeRead(BaseModel):
    cause: str
    duree_s: Decimal


class PointSerieRead(BaseModel):
    horodatage: datetime
    quantite_bonne_cumulee: int


class AlertRead(BaseModel):
    id: int
    machine_id: int | None
    ordre_fabrication_id: int | None
    severity: str
    type: str
    message: str
    created_at: datetime
    resolved: bool


class ActiviteRead(BaseModel):
    id: int
    machine_id: int
    code_machine: str
    type: str
    payload: dict
    created_at: datetime


class OFActifRead(BaseModel):
    id: int
    numero: str
    article_code: str
    article_designation: str
    lot_produit: str | None
    quantite_planifiee: Decimal
    quantite_bonne: Decimal
    quantite_rejetee: Decimal
    statut: str
    ligne_production_id: int | None


class ArretCategorieRead(BaseModel):
    nb_actifs: int
    duree_totale_s: Decimal


class MatiereConsommeeRead(BaseModel):
    code_mp: str
    designation_mp: str
    numero_lot: str
    quantite: Decimal


class PointOEERead(BaseModel):
    label: str
    horodatage: datetime
    disponibilite: Decimal
    performance: Decimal
    qualite: Decimal
    trs: Decimal | None


class DashboardResumeRead(BaseModel):
    trs_global: Decimal
    disponibilite: Decimal
    performance: Decimal
    qualite: Decimal
    trs_detail: TRSRead | None
    machines_en_marche: int
    machines_arretees: int
    machines_total: int
    ordres_actifs: int
    production_reelle: Decimal
    production_cible: Decimal
    quantite_bonne: Decimal
    quantite_rejetee: Decimal
    temps_arret_total_s: Decimal
    mttr_s: Decimal
    mtbf_s: Decimal
    mttf_s: Decimal
    nb_pannes: int
    top_causes_arret: list[CauseArretResumeRead]
    alertes_actives: list[AlertRead]
    serie_production: list[PointSerieRead]
    cadence_actuelle_par_min: Decimal
    activite_recente: list[ActiviteRead]
    of_actif: OFActifRead | None
    taux_charge: Decimal
    taux_engagement: Decimal
    cadence_nominale_par_min: Decimal
    production_theorique: Decimal
    reste_a_produire: Decimal
    arrets_planifies: ArretCategorieRead
    arrets_non_planifies: ArretCategorieRead
    micro_arrets_nombre: int
    matieres_consommees: list[MatiereConsommeeRead]
