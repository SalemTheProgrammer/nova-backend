"""API REST de l'atelier Tunicotex : tableaux de bord DIVA (lecture seule) et rapports PDF."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.access import require_category
from app.core.config import get_settings
from app.core.security import require_api_key
from app.integrations.divatex import commandes, connexion, of, operateurs, production
from app.integrations.divatex import referentiel as ref
from app.schemas.tunicotex import (
    AtelierSchema,
    AvancementOFSchema,
    ChaineDetailSchema,
    CommandeDetailSchema,
    EnvoiRapportReponse,
    EnvoiRapportRequete,
    FicheOperateurSchema,
    LigneReference,
    OFResumeSchema,
    OFRisqueSchema,
    ProductionLigneSchema,
    ReferenceSchema,
    RendementOperateurSchema,
)
from app.services import audit_service, notify_service, tunicotex_pdf

router = APIRouter(
    prefix="/tunicotex",
    tags=["tunicotex"],
    dependencies=[Depends(require_api_key), Depends(require_category("Atelier Tunicotex"))],
)

T = TypeVar("T")


def _jour(valeur: date | None) -> date:
    return valeur or connexion.date_reference()


def _periode(du: date | None, au: date | None) -> tuple[date, date]:
    fin = au or connexion.date_reference()
    return du or fin - timedelta(days=6), fin


def _valeurs_valides(appel: Callable[[], T]) -> T:
    """Les `ValueError` du dépôt ou des rapports deviennent des 400 lisibles."""
    try:
        return appel()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reference", response_model=ReferenceSchema)
def reference() -> ReferenceSchema:
    lignes = [
        LigneReference(
            id=ligne.id, code=ligne.code, nom=ligne.nom,
            groupe_id=ligne.groupe.id if ligne.groupe else None, nature=ligne.nature,
        )
        for ligne in ref.lister_lignes()
    ]
    return ReferenceSchema(
        date_reference=connexion.date_reference(),
        calibre=get_settings().divatex_calibre,
        groupes=ref.lister_groupes(),
        lignes=lignes,
        phases=ref.lister_phases(),
    )


@router.get("/atelier", response_model=AtelierSchema)
def atelier(jour: date | None = Query(None, alias="date")) -> AtelierSchema:
    j = _jour(jour)
    return AtelierSchema(
        situation=production.situation_atelier(j),
        lignes=production.production_lignes(j),
        tendance=production.tendance(j, 7),
    )


@router.get("/chaines", response_model=list[ProductionLigneSchema])
def chaines(jour: date | None = Query(None, alias="date"), groupe: int | None = None):
    return production.production_lignes(_jour(jour), groupe)


@router.get("/chaines/{ligne_id}", response_model=ChaineDetailSchema)
def chaine(ligne_id: int, jour: date | None = Query(None, alias="date")) -> ChaineDetailSchema:
    j = _jour(jour)
    prod = next((p for p in production.production_lignes(j) if p.ligne.id == ligne_id), None)
    if prod is None:
        raise HTTPException(status_code=404, detail="Chaîne introuvable.")
    return ChaineDetailSchema(
        production=prod,
        courbe=production.courbe_horaire(ligne_id, j),
        operateurs=operateurs.classement_operateurs(j, j, ligne_id=ligne_id),
        ofs=of.of_de_ligne(ligne_id, j),
    )


@router.get("/of", response_model=list[OFResumeSchema])
def rechercher_of(q: str = "", ouverts: bool = False, client: int | None = None):
    return of.rechercher_of(q, ouverts_seulement=ouverts, client_id=client)


@router.get("/of/{of_id}", response_model=AvancementOFSchema)
def avancement_of(of_id: int):
    avancement = of.avancement_of(of_id)
    if avancement is None:
        raise HTTPException(status_code=404, detail="OF introuvable.")
    return avancement


@router.get("/operateurs", response_model=list[RendementOperateurSchema])
def classement(
    du: date | None = None,
    au: date | None = None,
    chaine: int | None = None,
    groupe: int | None = None,
):
    debut, fin = _periode(du, au)
    return _valeurs_valides(
        lambda: operateurs.classement_operateurs(debut, fin, ligne_id=chaine, groupe_id=groupe)
    )


@router.get("/operateurs/{operateur_id}", response_model=FicheOperateurSchema)
def fiche_operateur(operateur_id: int, du: date | None = None, au: date | None = None):
    debut, fin = _periode(du, au)
    fiche = _valeurs_valides(lambda: operateurs.fiche_operateur(operateur_id, debut, fin))
    if fiche is None:
        raise HTTPException(status_code=404, detail="Opérateur introuvable.")
    return fiche


@router.get("/commandes", response_model=list[OFRisqueSchema])
def of_a_risque(horizon: int = Query(7, ge=1, le=365), client: int | None = None):
    return commandes.of_a_risque(connexion.date_reference(), horizon, client)


@router.get("/commandes/recherche", response_model=list[CommandeDetailSchema])
def recherche_commande(q: str):
    return commandes.consulter_commande(q)


def _type_rapport(type_: str) -> str:
    if type_ not in tunicotex_pdf.TITRES:
        raise HTTPException(status_code=404, detail="Rapport inconnu.")
    return type_


@router.get("/rapports/{type_}.pdf")
def rapport_pdf(
    type_: str,
    jour: date | None = None,
    du: date | None = None,
    au: date | None = None,
    groupe_id: int | None = None,
    ligne_id: int | None = None,
    of_id: int | None = None,
    client_id: int | None = None,
    horizon_jours: int = Query(7, ge=1, le=365),
) -> Response:
    params = tunicotex_pdf.ParametresRapport(
        jour=jour, du=du, au=au, groupe_id=groupe_id, ligne_id=ligne_id,
        of_id=of_id, client_id=client_id, horizon_jours=horizon_jours,
    )
    nom, pdf = _valeurs_valides(
        lambda: tunicotex_pdf.generer_rapport(_type_rapport(type_), params)
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


@router.post("/rapports/{type_}/envoyer", response_model=EnvoiRapportReponse)
def envoyer_rapport(type_: str, corps: EnvoiRapportRequete) -> EnvoiRapportReponse:
    type_ = _type_rapport(type_)

    def _generer() -> tuple[str, bytes]:
        params = tunicotex_pdf.ParametresRapport.from_dict(corps.parametres)
        return tunicotex_pdf.generer_rapport(type_, params)

    nom, pdf = _valeurs_valides(_generer)
    titre = tunicotex_pdf.TITRES[type_]
    texte = f"Veuillez trouver ci-joint : {titre} (Tunicotex)."
    if corps.canal == "whatsapp":
        numero = notify_service.normaliser_numero(corps.destinataire)
        notify_service.envoyer_whatsapp(numero, texte, document=(nom, pdf))
    else:
        notify_service.envoyer_email(
            corps.destinataire, f"{titre} — Tunicotex", texte, piece_jointe=(nom, pdf)
        )
    audit_service.enregistrer_action(
        action="envoyer_rapport_web",
        arguments={
            "type": type_,
            "canal": corps.canal,
            "destinataire": corps.destinataire,
            "parametres": corps.parametres,
        },
        source="web",
        thread_id=None,
    )
    return EnvoiRapportReponse(ok=True, message=f"Rapport envoyé à {corps.destinataire}.")
