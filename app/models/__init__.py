"""ORM models de Nova.

Importing this package registers every model on the shared Base metadata,
so `Base.metadata.create_all()` sees all tables.
"""
from __future__ import annotations

from app.models.audit import AuditLog
from app.models.code_verification import CodeVerification
from app.models.documents import DocumentRag
from app.models.envoi_planifie import EnvoiPlanifie
from app.models.utilisateur import Utilisateur

__all__ = [
    # authentification par numéro + périmètre d'outils
    "Utilisateur",
    "CodeVerification",
    # journal d'audit
    "AuditLog",
    # base documentaire (RAG)
    "DocumentRag",
    # envois sortants programmés
    "EnvoiPlanifie",
]
