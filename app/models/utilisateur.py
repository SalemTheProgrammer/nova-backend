"""Utilisateur : un opérateur identifié par son numéro de téléphone.

Chaque numéro possède un jeu d'outils autorisés (`outils_autorises`, liste de
noms d'outils de l'agent). L'administrateur (`is_admin=True`) ignore cette liste
et dispose de tous les outils ainsi que de la page d'administration.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Utilisateur(Base, TimestampMixin):
    """Un utilisateur autorisé à se connecter (par numéro) et son périmètre d'outils."""

    __tablename__ = "utilisateurs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Numéro normalisé en E.164 (ex. +21600000000), unique.
    telephone: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    nom_complet: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Liste des noms d'outils autorisés. Ignorée pour l'admin (tous les outils).
    outils_autorises: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
