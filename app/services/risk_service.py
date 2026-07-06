"""Analyse de risque de panne par machine (heuristique maintenance prédictive v1).

Score 0..1 par machine à partir de l'historique récent :
    50 % fréquence des pannes sur la fenêtre (nb d'arrêts non planifiés)
    30 % gravité (part du temps passé en arrêt)
    20 % ancienneté de la dernière maintenance

Volontairement déterministe (pas de ML) : rapide, explicable au jury, et le même
calcul sert au superviseur pour proposer une maintenance préventive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DowntimeEvent, Machine, MaintenanceEvent
from app.models.enums import CauseArret

FENETRE = timedelta(days=7)
# Au-delà de ce nombre de pannes sur la fenêtre, la composante fréquence sature à 1.
PANNES_SATURATION = 5
# Au-delà de ce délai sans maintenance, la composante maintenance sature à 1.
JOURS_MAINTENANCE_SATURATION = 30

CAUSES_PANNE = {
    CauseArret.PANNE_MECANIQUE,
    CauseArret.PANNE_ELECTRIQUE,
    CauseArret.MICRO_ARRET,
    CauseArret.AUTRE,
}


@dataclass
class RisqueMachine:
    machine_id: int
    code: str
    nom: str
    score: float
    nb_pannes_7j: int
    duree_arret_7j_s: float
    jours_depuis_maintenance: int | None
    niveau: str  # "faible" | "modere" | "eleve"
    recommandation: str


def _niveau(score: float) -> str:
    if score >= 0.6:
        return "eleve"
    if score >= 0.3:
        return "modere"
    return "faible"


def analyser_risques(db: Session) -> list[RisqueMachine]:
    maintenant = datetime.utcnow()
    depuis = maintenant - FENETRE
    fenetre_s = FENETRE.total_seconds()

    machines = db.execute(select(Machine).where(Machine.actif.is_(True))).scalars().all()
    resultats: list[RisqueMachine] = []

    for m in machines:
        downtimes = (
            db.execute(
                select(DowntimeEvent).where(
                    DowntimeEvent.machine_id == m.id, DowntimeEvent.start_time >= depuis
                )
            )
            .scalars()
            .all()
        )
        pannes = [d for d in downtimes if d.cause in CAUSES_PANNE]
        duree_arret = sum(
            ((d.end_time or maintenant) - d.start_time).total_seconds() for d in downtimes
        )

        derniere_maint = (
            db.execute(
                select(MaintenanceEvent)
                .where(MaintenanceEvent.machine_id == m.id)
                .order_by(MaintenanceEvent.start_time.desc())
            )
            .scalars()
            .first()
        )
        jours_maint: int | None = None
        if derniere_maint is not None:
            jours_maint = max(0, (maintenant - derniere_maint.start_time).days)

        freq = min(1.0, len(pannes) / PANNES_SATURATION)
        gravite = min(1.0, duree_arret / fenetre_s * 10)  # 10 % d'arrêt sur 7 j → 1.0
        anciennete = (
            min(1.0, jours_maint / JOURS_MAINTENANCE_SATURATION)
            if jours_maint is not None
            else 0.7  # jamais maintenue → risque notable par défaut
        )
        score = round(0.5 * freq + 0.3 * gravite + 0.2 * anciennete, 3)

        if score >= 0.6:
            reco = "Planifier une maintenance préventive dès que possible."
        elif score >= 0.3:
            reco = "Surveiller de près ; prévoir une maintenance à la prochaine fenêtre."
        else:
            reco = "Aucune action requise."

        resultats.append(
            RisqueMachine(
                machine_id=m.id,
                code=m.code,
                nom=m.nom,
                score=score,
                nb_pannes_7j=len(pannes),
                duree_arret_7j_s=duree_arret,
                jours_depuis_maintenance=jours_maint,
                niveau=_niveau(score),
                recommandation=reco,
            )
        )

    resultats.sort(key=lambda r: r.score, reverse=True)
    return resultats
