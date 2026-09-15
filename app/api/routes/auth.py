"""Authentification par numéro de téléphone (code de vérification WhatsApp)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.utilisateur import Utilisateur
from app.schemas.auth_schema import (
    DemandeCodeRequest,
    DemandeCodeResponse,
    UtilisateurRead,
    VerifierCodeRequest,
    VerifierCodeResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/request-code", response_model=DemandeCodeResponse)
def request_code(payload: DemandeCodeRequest, db: Session = Depends(get_db)) -> DemandeCodeResponse:
    """Envoie un code de vérification par WhatsApp au numéro (s'il est enregistré).

    Hors production, le code est aussi renvoyé (`dev_code`) et écrit dans la
    console du backend pour permettre la connexion sans dépendre de WhatsApp.
    """
    dev_code = auth_service.demander_code(db, payload.telephone)
    return DemandeCodeResponse(sent=True, dev_code=dev_code)


@router.post("/verify-code", response_model=VerifierCodeResponse)
def verify_code(payload: VerifierCodeRequest, db: Session = Depends(get_db)) -> VerifierCodeResponse:
    """Valide le code et renvoie un jeton de session + le profil utilisateur."""
    user = auth_service.verifier_code(db, payload.telephone, payload.code)
    token = auth_service.creer_token(user)
    return VerifierCodeResponse(token=token, user=UtilisateurRead.model_validate(user))


@router.get("/demo")
def demo_disponible() -> dict:
    """Le mode démonstration est-il ouvert ? Sert à n'afficher le bouton
    « Entrer en mode démonstration » que s'il fonctionnera vraiment."""
    return {"enabled": get_settings().demo_login_enabled}


@router.post("/demo", response_model=VerifierCodeResponse)
def demo_login(db: Session = Depends(get_db)) -> VerifierCodeResponse:
    """Session de démonstration, sans code WhatsApp (si `DEMO_LOGIN_ENABLED`).

    Le compte démo est en lecture seule : il voit tout l'atelier et dialogue
    avec Nova, mais ne peut ni piloter une machine, ni envoyer un message, ni
    créer un OF (voir `auth_service.connexion_demo`).
    """
    user = auth_service.connexion_demo(db)
    token = auth_service.creer_token(user)
    return VerifierCodeResponse(token=token, user=UtilisateurRead.model_validate(user))


@router.post("/demo/reset-usine")
def demo_reset_usine(db: Session = Depends(get_db)) -> dict:
    """Prépare un état de démonstration crédible en un clic (bouton de la page de
    connexion) : historique à zéro, coûts réalistes, échéances tenables, 3 lignes
    démarrées et arrêts passés pour un Pareto parlant. Réservé au mode démo."""
    if not get_settings().demo_login_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Le mode démonstration n'est pas activé.")
    from app.api.routes.routes_kpi import clear_kpi_caches
    from app.services import broadcast_service, demo_service

    resume = demo_service.preparer(db)
    clear_kpi_caches()
    broadcast_service.diffuser({"type": "reset"})
    return {
        "machines_demarrees": resume.machines_demarrees,
        "arrets_injectes": resume.arrets_injectes,
        "articles_chiffres": resume.articles_chiffres,
        "of_a_l_heure": resume.of_a_l_heure,
        "evenements_qualite": resume.evenements_qualite,
        "maintenances": resume.maintenances,
        "of_termines": resume.of_termines,
    }


@router.get("/me", response_model=UtilisateurRead)
def me(user: Utilisateur = Depends(get_current_user)) -> UtilisateurRead:
    """Profil de l'utilisateur connecté (réhydrate la session au chargement)."""
    return UtilisateurRead.model_validate(user)
