"""Remise à zéro de l'historique d'exécution de l'atelier (action d'administration).

Efface ce que les automates ont produit — événements machine, arrêts, qualité,
maintenance, alertes, propositions du superviseur — et remet les compteurs à
zéro, pour repartir d'un historique propre après une recette ou une démo.

Ce qui n'est JAMAIS effacé : le référentiel, le stock et la généalogie matière
des OF (`of_consommation_mp`). La consommation des lots a eu lieu à la création
de l'OF et doit rester tracée (exigence BPF de traçabilité).

Refusé tant qu'une machine produit : l'état réel des automates ferait
immédiatement diverger le MES remis à zéro. Après le reset, l'appelant demande
une re-naissance Sparkplug pour resynchroniser l'état des automates.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import FabricationError
from app.models import (
    AgentProposal,
    Alert,
    DowntimeEvent,
    Machine,
    MachineEvent,
    MaintenanceEvent,
    OrdreFabrication,
    QualityEvent,
)
from app.models.enums import StatutMachine, StatutOF


@dataclass(frozen=True)
class ResumeReset:
    evenements_machine: int
    arrets: int
    evenements_qualite: int
    maintenances: int
    alertes: int
    propositions: int
    machines: int
    ordres_reinitialises: int


def reinitialiser_historique(db: Session) -> ResumeReset:
    en_production = db.execute(
        select(Machine.code).where(
            Machine.statut.in_((StatutMachine.MARCHE, StatutMachine.PAUSE))
        )
    ).scalars().all()
    if en_production:
        raise FabricationError(
            "Arrêtez d'abord les machines en production : " + ", ".join(en_production) + "."
        )

    def _vider(modele: type) -> int:
        return db.execute(delete(modele)).rowcount or 0

    compteurs = {
        "evenements_machine": _vider(MachineEvent),
        "arrets": _vider(DowntimeEvent),
        "evenements_qualite": _vider(QualityEvent),
        "maintenances": _vider(MaintenanceEvent),
        "alertes": _vider(Alert),
        "propositions": _vider(AgentProposal),
    }

    machines = db.execute(select(Machine)).scalars().all()
    for machine in machines:
        # L'état réel sera republié par l'automate à sa re-naissance.
        machine.statut = StatutMachine.ARRET
        machine.ordre_fabrication_id = None
        machine.quantite_produite = 0
        machine.quantite_bonne = 0
        machine.quantite_rejetee = 0
        machine.temps_cycle_actuel_s = machine.temps_cycle_cible_s
        machine.dernier_evenement_at = None

    ordres = db.execute(
        select(OrdreFabrication).where(
            OrdreFabrication.statut.in_((StatutOF.EN_COURS, StatutOF.TERMINE))
            | (OrdreFabrication.quantite_bonne > 0)
            | (OrdreFabrication.quantite_rejetee > 0)
        )
    ).scalars().all()
    for of in ordres:
        if of.statut in (StatutOF.EN_COURS, StatutOF.TERMINE):
            of.statut = StatutOF.PLANIFIE
        of.quantite_bonne = 0
        of.quantite_rejetee = 0
        of.date_debut_reelle = None
        of.date_fin_reelle = None

    db.flush()
    return ResumeReset(**compteurs, machines=len(machines), ordres_reinitialises=len(ordres))
