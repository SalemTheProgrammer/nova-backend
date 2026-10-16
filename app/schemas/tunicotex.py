"""Schémas de l'API Tunicotex : miroirs des dataclasses du dépôt DIVA (dates ISO)."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt

Nature = Literal["tricotage", "confection", "finition"]


class _Modele(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GroupeSchema(_Modele):
    id: int
    nom: str


class LigneSchema(_Modele):
    id: int
    code: str
    nom: str
    groupe: GroupeSchema | None
    nature: Nature


class LigneReference(BaseModel):
    id: int
    code: str
    nom: str
    groupe_id: int | None
    nature: Nature


class PhaseSchema(_Modele):
    id: int
    nom: str


class ReferenceSchema(BaseModel):
    date_reference: date
    calibre: bool
    groupes: list[GroupeSchema]
    lignes: list[LigneReference]
    phases: list[PhaseSchema]


class ProductionLigneSchema(_Modele):
    ligne: LigneSchema
    jour: date
    sortie: int
    objectif: int | None
    objectif_date: date | None
    objectif_perime: bool
    taux: float | None
    effectif: int
    minutes_produites: float
    minutes_presence: float
    rendement: float | None
    presence_estimee: bool
    a_des_lectures: bool


class PointJourSchema(_Modele):
    jour: date
    sortie: int
    objectif: int


class PointHeureSchema(_Modele):
    heure: int
    pieces: int


class SituationAtelierSchema(_Modele):
    jour: date
    sortie: int
    objectif: int
    taux: float | None
    rendement_moyen: float | None
    lignes_sous_objectif: list[str]
    lignes_au_dessus: list[str]
    nb_of_en_retard: int
    a_des_lectures: bool


class AtelierSchema(BaseModel):
    situation: SituationAtelierSchema
    lignes: list[ProductionLigneSchema]
    tendance: list[PointJourSchema]


class OperateurSchema(_Modele):
    id: int
    matricule: str
    nom: str
    prenom: str
    ligne_id: int | None


class RendementOperateurSchema(_Modele):
    operateur: OperateurSchema
    ligne: str | None
    pieces_operations: int
    minutes_produites: float
    minutes_presence: float
    rendement: float | None
    presence_estimee: bool


class FicheOperateurSchema(_Modele):
    operateur: OperateurSchema
    du: date
    au: date
    par_jour: list[tuple[date, float | None]]
    operations: list[tuple[str, int]]
    total: RendementOperateurSchema


class OFResumeSchema(_Modele):
    id: int
    numero: str
    client: str | None
    article: str | None
    quantite: int
    date_export_prevue: date | None
    ouvert: bool
    avancement_final: float


class AvancementPhaseSchema(_Modele):
    phase_id: int
    nom: str
    quantite: int
    pct: float


class AvancementOFSchema(_Modele):
    of: OFResumeSchema
    phases: list[AvancementPhaseSchema]
    tailles: list[tuple[str, dict[int, int]]]
    lignes: list[str]
    derniere_lecture: date | None


class ChaineDetailSchema(BaseModel):
    production: ProductionLigneSchema
    courbe: list[PointHeureSchema]
    operateurs: list[RendementOperateurSchema]
    ofs: list[OFResumeSchema]


class OFRisqueSchema(_Modele):
    of: OFResumeSchema
    statut: Literal["en_retard", "a_risque"]
    jours: int


class CommandeDetailSchema(_Modele):
    id: int
    numero: str
    client: str | None
    date: date | None
    date_fin_visee: date | None
    quantite: int
    ofs: list[OFResumeSchema]
    a_risque: bool


def _date_iso_texte(valeur: Any) -> Any:
    """Une date de rapport est un texte ISO : refuse nombres et booléens."""
    if valeur is not None and not isinstance(valeur, str):
        raise ValueError("date ISO (AAAA-MM-JJ) attendue")
    return valeur


DateIso = Annotated[date | None, BeforeValidator(_date_iso_texte)]


class ParametresRapportSchema(BaseModel):
    """Paramètres d'un rapport : miroir typé de `tunicotex_pdf.ParametresRapport`."""

    jour: DateIso = None
    du: DateIso = None
    au: DateIso = None
    groupe_id: StrictInt | None = None
    ligne_id: StrictInt | None = None
    of_id: StrictInt | None = None
    client_id: StrictInt | None = None
    horizon_jours: StrictInt | None = Field(default=None, ge=1, le=365)


class EnvoiRapportRequete(BaseModel):
    canal: Literal["whatsapp", "email"]
    destinataire: str = Field(min_length=1)
    parametres: dict = Field(default_factory=dict)


class EnvoiRapportReponse(BaseModel):
    ok: bool
    message: str
