"""Schémas Pydantic pour l'authentification et l'administration des utilisateurs."""
from __future__ import annotations

from pydantic import BaseModel, Field


class DemandeCodeRequest(BaseModel):
    telephone: str = Field(..., min_length=6, max_length=25)


class DemandeCodeResponse(BaseModel):
    sent: bool = True
    # Rempli uniquement hors production (dépannage / démo) : le code en clair.
    dev_code: str | None = None


class VerifierCodeRequest(BaseModel):
    telephone: str = Field(..., min_length=6, max_length=25)
    code: str = Field(..., min_length=4, max_length=10)


class UtilisateurRead(BaseModel):
    id: int
    telephone: str
    nom_complet: str
    is_admin: bool
    actif: bool
    outils_autorises: list[str]

    model_config = {"from_attributes": True}


class VerifierCodeResponse(BaseModel):
    token: str
    user: UtilisateurRead


class UtilisateurCreate(BaseModel):
    telephone: str = Field(..., min_length=6, max_length=25)
    nom_complet: str = Field(default="", max_length=120)
    outils_autorises: list[str] = Field(default_factory=list)


class UtilisateurUpdate(BaseModel):
    nom_complet: str | None = Field(default=None, max_length=120)
    actif: bool | None = None
    outils_autorises: list[str] | None = None


class OutilCatalogue(BaseModel):
    name: str
    categorie: str
    description: str
