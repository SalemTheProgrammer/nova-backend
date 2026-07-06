"""Sélection des phrases les plus pertinentes d'un chunk retrouvé par le RAG.

Un chunk fait ~1000 caractères : le surligner en entier dans le viewer PDF
colorie presque toute la page. Ce service découpe chaque chunk en phrases et
classe celles-ci par similarité (embeddings) avec la question de l'opérateur,
pour ne surligner que la ou les phrases qui répondent vraiment.
"""
from __future__ import annotations

import re

from app.core.logging import get_logger
from app.services.llm import get_embeddings

logger = get_logger(__name__)

# Une « phrase » plus courte que ça est un fragment (titre, numéro) : ignorée.
PHRASE_MIN = 30
# Au-delà de cette longueur, ce n'est pas une phrase mais un bloc de mise en page
# (liste à puces, diapositive sans ponctuation) : on le découpe en fenêtres de mots,
# sinon tout le bloc — donc presque toute la page — serait surligné.
PHRASE_MAX = 180
FENETRE_MOTS = 14
PAS_FENETRE = 7
# On garde la meilleure phrase, plus la 2e si elle est presque aussi pertinente.
ECART_SECONDE = 0.05
MAX_PHRASES_PAR_CITATION = 2


def _aplatir(texte: str) -> str:
    return re.sub(r"\s+", " ", texte).strip()


def _fenetres(texte: str) -> list[str]:
    """Fenêtres glissantes de mots pour les blocs sans ponctuation."""
    mots = texte.split()
    fenetres: list[str] = []
    for i in range(0, len(mots), PAS_FENETRE):
        fenetre = " ".join(mots[i : i + FENETRE_MOTS])
        if len(fenetre) >= PHRASE_MIN:
            fenetres.append(fenetre)
        if i + FENETRE_MOTS >= len(mots):
            break
    return fenetres


def _phrases(texte: str) -> list[str]:
    """Découpe un chunk en phrases (l'extraction PDF insère des sauts de ligne
    en pleine phrase : on aplatit d'abord). Les segments trop longs pour être de
    vraies phrases sont re-découpés en fenêtres de mots."""
    brutes = re.split(r"(?<=[.!?;:])\s+", _aplatir(texte))
    resultat: list[str] = []
    for p in brutes:
        p = p.strip()
        if len(p) < PHRASE_MIN:
            continue
        if len(p) <= PHRASE_MAX:
            resultat.append(p)
        else:
            resultat.extend(_fenetres(p))
    return resultat


def _dot(a: list[float], b: list[float]) -> float:
    # Les embeddings OpenAI sont normalisés : le produit scalaire = similarité cosinus.
    return sum(x * y for x, y in zip(a, b))


def extraire_phrases_pertinentes(question: str, citations: list[str]) -> list[list[str]]:
    """Pour chaque citation, renvoie les phrases qui répondent le mieux à la question.

    En cas d'échec (API embeddings indisponible…), retombe sur la citation
    entière : le surlignage reste fonctionnel, juste moins précis.
    """
    repli = [[_aplatir(c)] for c in citations]
    try:
        decoupes = [_phrases(c) for c in citations]
        toutes = [p for phrases in decoupes for p in phrases]
        if not toutes:
            return repli

        emb = get_embeddings()
        vecteur_question = emb.embed_query(question)
        vecteurs = emb.embed_documents(toutes)
        scores = [_dot(vecteur_question, v) for v in vecteurs]

        resultat: list[list[str]] = []
        offset = 0
        for i, phrases in enumerate(decoupes):
            if not phrases:
                resultat.append(repli[i])
                continue
            locaux = scores[offset : offset + len(phrases)]
            offset += len(phrases)
            classees = sorted(zip(phrases, locaux), key=lambda x: x[1], reverse=True)
            meilleur_score = classees[0][1]
            retenues = [
                phrase
                for phrase, score in classees[:MAX_PHRASES_PAR_CITATION]
                if score >= meilleur_score - ECART_SECONDE
            ]
            resultat.append(retenues)
        return resultat
    except Exception:  # noqa: BLE001
        logger.exception("extraits_pertinents_failed")
        return repli
