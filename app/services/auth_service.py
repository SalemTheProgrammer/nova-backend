"""Authentification par numéro de téléphone + code de vérification WhatsApp.

Flux : l'utilisateur saisit son numéro → `demander_code` génère un code à
6 chiffres, le stocke haché et l'envoie par WhatsApp (service Baileys) →
l'utilisateur saisit le code → `verifier_code` valide et renvoie l'utilisateur →
`creer_token` émet un jeton de session signé (HMAC).

Le périmètre d'outils de chaque numéro est porté par `Utilisateur.outils_autorises`
(l'admin ignore la liste : tous les outils). Les jetons sont signés avec
`settings.auth_secret` — aucune dépendance externe (stdlib uniquement).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.models.code_verification import CodeVerification
from app.models.utilisateur import Utilisateur
from app.services import notify_service

logger = get_logger(__name__)

_MAX_TENTATIVES = 5


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def _hash_code(telephone: str, code: str) -> str:
    """Hache le code lié au numéro (sha256) — jamais stocké en clair."""
    return hashlib.sha256(f"{telephone}:{code}".encode()).hexdigest()


def normaliser_numero(numero: str) -> str:
    """Normalise en E.164 (réutilise la logique de notify_service)."""
    return notify_service.normaliser_numero(numero)


def outils_pour(user: Utilisateur) -> list[str] | None:
    """Périmètre d'outils d'un utilisateur : `None` = tous les outils (admin)."""
    if user.is_admin:
        return None
    return list(user.outils_autorises or [])


def _get_utilisateur(db: Session, telephone: str) -> Utilisateur | None:
    return db.execute(
        select(Utilisateur).where(Utilisateur.telephone == telephone)
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Codes de vérification
# --------------------------------------------------------------------------- #
def demander_code(db: Session, numero: str) -> str | None:
    """Génère et envoie un code de vérification WhatsApp pour un numéro connu.

    Le numéro doit correspondre à un utilisateur existant et actif — sinon on
    refuse (pas d'auto-inscription : l'admin déclare les numéros).

    Renvoie le code EN CLAIR uniquement hors production (dépannage / démo) ; en
    production, renvoie `None` (le code ne transite que par WhatsApp).
    """
    telephone = normaliser_numero(numero)
    user = _get_utilisateur(db, telephone)
    if user is None or not user.actif:
        raise AuthenticationError(
            "Numéro non autorisé. Contactez l'administrateur pour être enregistré."
        )

    settings = get_settings()
    code = f"{secrets.randbelow(1_000_000):06d}"
    expire_le = datetime.now(timezone.utc) + timedelta(seconds=settings.verification_code_ttl_s)

    db.add(
        CodeVerification(
            telephone=telephone,
            code_hash=_hash_code(telephone, code),
            expire_le=expire_le,
        )
    )
    db.flush()

    # Hors production : le code est écrit dans la console du backend pour pouvoir
    # se connecter même si WhatsApp n'arrive pas (démo, deuxième compte WhatsApp
    # sur le même téléphone, adressage @lid…). JAMAIS en production.
    if not settings.is_production:
        logger.warning("auth_code_dev", numero=telephone, code=code)

    corps = (
        f"Nova — votre code de connexion : {code}\n"
        f"Valable {settings.verification_code_ttl_s // 60} minutes. "
        "Ne le communiquez à personne."
    )
    try:
        notify_service.envoyer_whatsapp(telephone, corps)
        logger.info("auth_code_envoye", numero=telephone)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth_envoi_code_whatsapp_failed", numero=telephone, error=str(exc))
        # En production, l'échec WhatsApp est bloquant (le code n'a pas d'autre
        # canal). Hors production, on laisse passer : le code est dans la console.
        if settings.is_production:
            raise AuthenticationError(
                "Impossible d'envoyer le code par WhatsApp : le service WhatsApp "
                "(Baileys) doit être démarré et appairé."
            )

    return code if not settings.is_production else None


def verifier_code(db: Session, numero: str, code: str) -> Utilisateur:
    """Valide un code et renvoie l'utilisateur. Lève AuthenticationError sinon."""
    telephone = normaliser_numero(numero)
    user = _get_utilisateur(db, telephone)
    if user is None or not user.actif:
        raise AuthenticationError("Numéro non autorisé.")

    now = datetime.now(timezone.utc)
    entree = db.execute(
        select(CodeVerification)
        .where(
            CodeVerification.telephone == telephone,
            CodeVerification.consomme.is_(False),
        )
        .order_by(CodeVerification.id.desc())
    ).scalars().first()

    if entree is None:
        raise AuthenticationError("Aucun code en attente. Redemandez un code.")

    # `expire_le` est stocké naïf (SQLite) : on compare en UTC naïf.
    expire = entree.expire_le
    if expire.tzinfo is not None:
        expire = expire.astimezone(timezone.utc).replace(tzinfo=None)
    if expire < now.replace(tzinfo=None):
        raise AuthenticationError("Code expiré. Redemandez un code.")

    if entree.tentatives >= _MAX_TENTATIVES:
        entree.consomme = True
        db.flush()
        raise AuthenticationError("Trop de tentatives. Redemandez un code.")

    if not hmac.compare_digest(entree.code_hash, _hash_code(telephone, code.strip())):
        entree.tentatives += 1
        db.flush()
        raise AuthenticationError("Code incorrect.")

    entree.consomme = True
    db.flush()
    logger.info("auth_code_valide", numero=telephone)
    return user


# --------------------------------------------------------------------------- #
# Jetons de session (HMAC, stdlib)
# --------------------------------------------------------------------------- #
def _b64url_encode(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).rstrip(b"=").decode("ascii")


def _b64url_decode(texte: str) -> bytes:
    padding = "=" * (-len(texte) % 4)
    return base64.urlsafe_b64decode(texte + padding)


def _signer(payload_b64: str) -> str:
    secret = get_settings().auth_secret.encode()
    signature = hmac.new(secret, payload_b64.encode(), hashlib.sha256).digest()
    return _b64url_encode(signature)


def creer_token(user: Utilisateur) -> str:
    """Émet un jeton de session signé `<payload>.<signature>`."""
    settings = get_settings()
    exp = int((datetime.now(timezone.utc) + timedelta(seconds=settings.auth_token_ttl_s)).timestamp())
    payload = {"sub": user.id, "tel": user.telephone, "adm": user.is_admin, "exp": exp}
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_signer(payload_b64)}"


def verifier_token(token: str) -> dict:
    """Vérifie la signature + l'expiration d'un jeton et renvoie son payload."""
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        raise AuthenticationError("Jeton invalide.")
    if not hmac.compare_digest(signature, _signer(payload_b64)):
        raise AuthenticationError("Jeton invalide.")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        raise AuthenticationError("Jeton invalide.")
    if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
        raise AuthenticationError("Session expirée. Reconnectez-vous.")
    return payload


# --------------------------------------------------------------------------- #
# Amorçage de l'administrateur
# --------------------------------------------------------------------------- #
def seed_admin() -> None:
    """Crée (ou promeut) l'utilisateur administrateur défini par `admin_phone`."""
    from app.db.session import session_scope

    settings = get_settings()
    try:
        telephone = normaliser_numero(settings.admin_phone)
    except Exception:  # noqa: BLE001
        logger.warning("auth_admin_phone_invalide", numero=settings.admin_phone)
        return

    with session_scope() as db:
        user = _get_utilisateur(db, telephone)
        if user is None:
            db.add(
                Utilisateur(
                    telephone=telephone,
                    nom_complet="Administrateur",
                    is_admin=True,
                    actif=True,
                    outils_autorises=[],
                )
            )
            logger.info("auth_admin_cree", numero=telephone)
        elif not user.is_admin or not user.actif:
            user.is_admin = True
            user.actif = True
            logger.info("auth_admin_promu", numero=telephone)


def connexion_demo(db: Session) -> Utilisateur:
    """Ouvre une session de DÉMONSTRATION, sans code de vérification.

    Pensé pour une présentation publique : le visiteur voit tout l'atelier et
    peut dialoguer avec Nova, mais son périmètre d'outils est recalculé à chaque
    connexion sur `READONLY_TOOL_NAMES` — aucune commande machine, aucun envoi
    WhatsApp/e-mail, aucune création d'OF, aucun accès administrateur. Le compte
    est créé au premier appel puis réutilisé.

    Refusé si `DEMO_LOGIN_ENABLED` n'est pas activé : hors présentation, la
    connexion reste celle par code WhatsApp.
    """
    from app.agent.tools import READONLY_TOOL_NAMES  # import tardif : cycle

    settings = get_settings()
    if not settings.demo_login_enabled:
        raise AuthenticationError("Le mode démonstration n'est pas activé.")

    telephone = normaliser_numero(settings.demo_phone)
    user = _get_utilisateur(db, telephone)
    if user is None:
        user = Utilisateur(
            telephone=telephone,
            nom_complet=settings.demo_nom,
            is_admin=False,
            actif=True,
            outils_autorises=list(READONLY_TOOL_NAMES),
        )
        db.add(user)
        logger.info("auth_demo_compte_cree", numero=telephone)
    else:
        # Jamais admin, toujours actif, et périmètre relu depuis le code : une
        # modification manuelle dans l'admin ne peut pas élargir la démo.
        user.is_admin = False
        user.actif = True
        user.outils_autorises = list(READONLY_TOOL_NAMES)
    db.commit()
    db.refresh(user)
    logger.info("auth_demo_session", outils=len(user.outils_autorises))
    return user
