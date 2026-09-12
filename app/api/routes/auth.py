"""Authentification par numéro de téléphone (code de vérification WhatsApp)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
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


@router.get("/me", response_model=UtilisateurRead)
def me(user: Utilisateur = Depends(get_current_user)) -> UtilisateurRead:
    """Profil de l'utilisateur connecté (réhydrate la session au chargement)."""
    return UtilisateurRead.model_validate(user)
