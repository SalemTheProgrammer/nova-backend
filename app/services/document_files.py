"""Accès aux PDF sources de la base documentaire (envoi et consultation).

Le PDF téléversé est conservé sur disque ({documents_pdf_dir}/{id}.pdf, voir la
route d'upload). Ce module le retrouve par nom approximatif — « le manuel de la
blistereuse », « la norme BPF » — pour que l'agent puisse l'ENVOYER tel quel
(WhatsApp/e-mail), pas seulement le citer via le RAG.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models import DocumentRag


class DocumentFileError(AppError):
    status_code = 404
    code = "document_file_error"


def chemin_pdf(document_id: int) -> Path:
    """Chemin de stockage du PDF source d'un document (convention : {id}.pdf)."""
    return Path(get_settings().documents_pdf_dir) / f"{document_id}.pdf"


def _normaliser(texte: str) -> str:
    """Minuscules, sans accents ni ponctuation — pour comparer des noms tapés à la volée."""
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def trouver_document(db: Session, nom_ou_id: str) -> DocumentRag | None:
    """Retrouve un document par id, nom exact, sous-chaîne ou similarité.

    Tolère les à-peu-près de conversation (« manuel blistereuse » ↔
    « Manuel machine — Blistereuse B400 »). Renvoie None si rien d'assez proche.
    """
    brut = (nom_ou_id or "").strip()
    if not brut:
        return None
    if brut.isdigit():
        return db.get(DocumentRag, int(brut))

    documents = list(db.execute(select(DocumentRag)).scalars())
    if not documents:
        return None
    cible = _normaliser(brut)
    if not cible:
        return None

    # 1) Nom (ou fichier) qui contient — ou est contenu dans — la demande.
    for doc in documents:
        for champ in (doc.nom, doc.fichier):
            norme = _normaliser(champ)
            if norme and (cible in norme or norme in cible):
                return doc
    # 2) Tous les mots demandés présents dans le nom (ordre libre).
    mots = cible.split()
    for doc in documents:
        norme = _normaliser(f"{doc.nom} {doc.categorie or ''}")
        if mots and all(mot in norme for mot in mots):
            return doc
    # 3) Similarité globale, en dernier recours.
    scores = [
        (difflib.SequenceMatcher(None, cible, _normaliser(doc.nom)).ratio(), doc)
        for doc in documents
    ]
    score, doc = max(scores, key=lambda paire: paire[0])
    return doc if score >= 0.6 else None


def lire_pdf(document: DocumentRag) -> tuple[str, bytes]:
    """Renvoie (nom_fichier, contenu) du PDF source ; erreur claire s'il manque."""
    chemin = chemin_pdf(document.id)
    if not chemin.is_file():
        raise DocumentFileError(
            f"Le PDF source de « {document.nom} » n'est pas disponible (document "
            "indexé avant le stockage des fichiers). Retéléversez-le depuis la page "
            "Documents pour pouvoir l'envoyer."
        )
    nom_fichier = document.fichier if document.fichier.lower().endswith(".pdf") else f"{document.id}.pdf"
    return nom_fichier, chemin.read_bytes()
