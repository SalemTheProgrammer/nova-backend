"""Insights instantanés (règles métier, sans appel LLM) pour le panneau IA.

Ces phrases sont générées directement depuis les données réelles (TRS, arrêts, alertes,
OF bloqués) — utilisées comme premier niveau du panneau IA, toujours disponible même sans
`OPENAI_API_KEY`. Les questions libres passent par `/chat` (LangGraph), qui dispose des
mêmes données via `app/agent/tools/mes.py`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.temps import heure_usine
from app.models import AgentProposal, DowntimeEvent, Machine, OrdreFabrication
from app.models.enums import StatutMachine, StatutOF, StatutProposition
from app.services import trs_service


def generer_insights(db: Session) -> list[str]:
    insights: list[str] = []

    machines = list(db.execute(select(Machine).where(Machine.actif.is_(True))).scalars())

    arrets_actifs = list(
        db.execute(select(DowntimeEvent).where(DowntimeEvent.end_time.is_(None))).scalars()
    )
    if arrets_actifs:
        plus_long = min(arrets_actifs, key=lambda d: d.start_time)
        duree_min = int((datetime.utcnow() - plus_long.start_time).total_seconds() // 60)
        insights.append(
            f"Le TRS est impacté car {plus_long.machine.code} est arrêtée depuis "
            f"{duree_min} min ({plus_long.cause.value.replace('_', ' ').lower()})."
        )

    resultats = [
        (m, trs_service.calculer_trs_machine(db, m))
        for m in machines
        if m.temps_cycle_cible_s
    ]
    if resultats:
        pire_machine, pire_resultat = min(resultats, key=lambda mr: mr[1].trs)
        if pire_resultat.trs < 1:
            perte = pire_resultat.pertes.principale
            libelle = {
                "disponibilite": "la disponibilité",
                "performance": "la performance",
                "qualite": "la qualité",
            }[perte]
            insights.append(
                f"Sur {pire_machine.code}, la perte principale vient de {libelle} "
                f"(TRS actuel : {pire_resultat.trs * 100:.0f}%)."
            )

    ordres_bloques = list(
        db.execute(
            select(OrdreFabrication).where(OrdreFabrication.statut == StatutOF.EN_COURS)
        ).scalars()
    )
    for of in ordres_bloques:
        machine_bloquee = next(
            (m for m in machines if m.ordre_fabrication_id == of.id and m.statut in (
                StatutMachine.PANNE, StatutMachine.ARRET, StatutMachine.MAINTENANCE
            )),
            None,
        )
        if machine_bloquee is not None:
            insights.append(
                f"Priorité : résoudre l'arrêt actif sur {machine_bloquee.code}, "
                f"car il bloque l'ordre de production {of.numero}."
            )

    if not insights:
        if machines:
            insights.append("Aucune perte majeure détectée : la production tourne normalement.")
        else:
            insights.append("Aucune machine configurée pour le moment.")

    return insights


def generer_accueil(db: Session) -> list[str]:
    """Message d'accueil de Nova à l'ouverture du chat, en paragraphes : salutation,
    constats atelier (`generer_insights`), puis les décisions du superviseur en
    attente — présentées en cartes Oui/Non juste après dans le chat."""
    heure = heure_usine().hour
    salut = "Bonsoir" if heure >= 18 or heure < 5 else "Bonjour"
    messages = [f"{salut} ! Voici ce que je vois sur l'atelier en ce moment :", *generer_insights(db)]

    en_attente = db.scalar(
        select(func.count())
        .select_from(AgentProposal)
        .where(AgentProposal.statut == StatutProposition.PROPOSEE)
    ) or 0
    if en_attente == 1:
        messages.append("Une décision attend votre accord : je vous la présente juste en dessous.")
    elif en_attente > 1:
        messages.append(
            f"{en_attente} décisions attendent votre accord : je vous présente les plus "
            "récentes juste en dessous."
        )
    return messages
