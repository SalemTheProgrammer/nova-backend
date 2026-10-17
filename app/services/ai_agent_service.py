"""Message d'accueil de Nova (sans appel LLM), construit sur les données de l'atelier."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.integrations.divatex import connexion, production

_INJOIGNABLE = ["Bonjour, je suis Nova.", "Base DIVA injoignable pour le moment."]


def _pct(taux: float) -> str:
    return f"{round(taux * 100)}\u202f%"


def generer_accueil(db: Session | None) -> list[str]:
    """Phrases d'accueil : date des données, meilleure/pire chaîne, OF en retard."""
    try:
        jour = connexion.date_reference()
        lignes = production.production_lignes(jour)
        retards = production.situation_atelier(jour).nb_of_en_retard
    except connexion.DivatexIndisponible:
        return list(_INJOIGNABLE)

    messages = [f"Bonjour, je suis Nova. Données au {jour.strftime('%d/%m/%Y')}."]
    classees = sorted(
        (
            l for l in lignes
            if l.ligne.nature == "confection" and l.a_des_lectures and l.taux is not None
        ),
        key=lambda l: l.taux,
    )
    if len(classees) >= 2:
        pire, meilleure = classees[0], classees[-1]
        messages.append(
            f"Meilleure chaîne : {meilleure.ligne.code} ({_pct(meilleure.taux)} de l'objectif). "
            f"Plus en retrait : {pire.ligne.code} ({_pct(pire.taux)})."
        )
    elif classees:
        seule = classees[0]
        messages.append(f"Chaîne {seule.ligne.code} : {_pct(seule.taux)} de l'objectif.")
    messages.append(
        f"{retards} OF en retard de livraison (7 derniers jours)."
        if retards
        else "Aucun OF en retard de livraison (7 derniers jours)."
    )
    return messages
