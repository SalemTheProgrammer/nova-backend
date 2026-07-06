"""Superviseur autonome Nova : détection → diagnostic → proposition → exécution.

Boucle d'arrière-plan (voir `boucle_superviseur`) qui scanne l'atelier toutes les
quelques secondes. Quand un problème est détecté, une `AgentProposal` est créée
avec un diagnostic chiffré et une action structurée, puis poussée sur le
WebSocket. L'action n'est exécutée qu'après approbation de l'opérateur
(`executer_proposition`) — human-in-the-loop obligatoire.

Règles de détection (déterministes, donc fiables en démo) :
  1. Arrêt machine > SEUIL avec OF actif  → basculer l'OF vers la meilleure ligne
     disponible, ou lancer une maintenance d'urgence si aucune alternative.
  2. Dérive qualité (taux de rebut élevé sur fenêtre courte) → mise en pause pour réglage.
  3. Stock MP sous le seuil d'alerte → alerte de réapprovisionnement.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, FabricationError, NotFoundError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import (
    AgentProposal,
    Alert,
    DowntimeEvent,
    Machine,
    MatierePremiere,
    OrdreFabrication,
    QualityEvent,
)
from app.models.enums import (
    SeveriteAlerte,
    StatutMachine,
    StatutProposition,
    TypeEvenementQualite,
    TypeMaintenance,
)
from app.services import (
    broadcast_service,
    line_scoring_service,
    simulator_service,
)
from app.services import manufacturing as manufacturing_svc

logger = get_logger(__name__)

# Seuils de détection — courts exprès : en démo, le superviseur doit réagir vite.
SEUIL_ARRET_BLOQUANT_S = 45
SEUIL_TAUX_REBUT = 0.20
FENETRE_QUALITE = timedelta(minutes=15)
MIN_PIECES_QUALITE = 5
# Une proposition rejetée/exécutée ne se re-déclenche pas avant ce délai.
DELAI_REARMEMENT = timedelta(minutes=10)
INTERVALLE_BOUCLE_S = 5.0


def serialiser_proposition(p: AgentProposal) -> dict:
    return {
        "id": p.id,
        "type": p.type,
        "severite": p.severite.value,
        "titre": p.titre,
        "diagnostic": p.diagnostic,
        "action_libelle": p.action_libelle,
        "action": p.action,
        "statut": p.statut.value,
        "machine_id": p.machine_id,
        "ordre_fabrication_id": p.ordre_fabrication_id,
        "resultat": p.resultat,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
    }


def _deja_traitee(db: Session, cle: str) -> bool:
    """Une proposition avec cette clé est en attente, ou a été décidée récemment."""
    recente = datetime.utcnow() - DELAI_REARMEMENT
    existante = db.execute(
        select(AgentProposal)
        .where(AgentProposal.cle_dedup == cle)
        .order_by(AgentProposal.created_at.desc())
    ).scalars().first()
    if existante is None:
        return False
    if existante.statut == StatutProposition.PROPOSEE:
        return True
    return (existante.decided_at or existante.created_at) >= recente


def _proposer(
    db: Session,
    *,
    cle: str,
    type_: str,
    severite: SeveriteAlerte,
    titre: str,
    diagnostic: str,
    action_libelle: str,
    action: dict,
    machine_id: int | None = None,
    ordre_fabrication_id: int | None = None,
) -> AgentProposal:
    proposition = AgentProposal(
        cle_dedup=cle,
        type=type_,
        severite=severite,
        titre=titre,
        diagnostic=diagnostic,
        action_libelle=action_libelle,
        action=action,
        machine_id=machine_id,
        ordre_fabrication_id=ordre_fabrication_id,
    )
    db.add(proposition)
    db.flush()
    db.refresh(proposition)
    logger.info("proposition_creee", type=type_, cle=cle, id=proposition.id)
    return proposition


# --------------------------------------------------------------------------- #
# Règles de détection
# --------------------------------------------------------------------------- #


def _regle_arret_bloquant(db: Session, nouvelles: list[AgentProposal]) -> None:
    maintenant = datetime.utcnow()
    arrets = db.execute(
        select(DowntimeEvent).where(DowntimeEvent.end_time.is_(None))
    ).scalars().all()

    for arret in arrets:
        duree_s = (maintenant - arret.start_time).total_seconds()
        if duree_s < SEUIL_ARRET_BLOQUANT_S:
            continue
        machine = arret.machine
        of = (
            db.get(OrdreFabrication, machine.ordre_fabrication_id)
            if machine.ordre_fabrication_id
            else None
        )
        if of is None:
            continue  # pas d'OF bloqué → pas critique, l'alerte standard suffit
        cle = f"arret_bloquant:{arret.id}"
        if _deja_traitee(db, cle):
            continue

        duree_min = int(duree_s // 60)
        duree_txt = f"{duree_min} min" if duree_min >= 1 else f"{int(duree_s)} s"
        cause_txt = arret.cause.value.replace("_", " ").lower()

        alternative = line_scoring_service.meilleure_ligne_disponible(
            db, exclure_ligne_id=machine.ligne_production_id
        )
        if alternative is not None:
            diagnostic = (
                f"{machine.code} est arrêtée depuis {duree_txt} ({cause_txt}) et bloque "
                f"l'OF {of.numero} ({of.quantite_planifiee} {of.unite.value} de "
                f"{of.article.code}). La ligne {alternative.code} est la meilleure "
                f"alternative : score {alternative.score * 100:.0f}/100 "
                f"({alternative.raison})."
            )
            nouvelles.append(
                _proposer(
                    db,
                    cle=cle,
                    type_="basculer_of",
                    severite=SeveriteAlerte.CRITICAL,
                    titre=f"OF {of.numero} bloqué par l'arrêt de {machine.code}",
                    diagnostic=diagnostic,
                    action_libelle=f"Basculer l'OF vers la ligne {alternative.code}",
                    action={
                        "type": "basculer_of",
                        "of_id": of.id,
                        "ligne_id": alternative.ligne_id,
                        "machine_source_id": machine.id,
                    },
                    machine_id=machine.id,
                    ordre_fabrication_id=of.id,
                )
            )
        else:
            diagnostic = (
                f"{machine.code} est arrêtée depuis {duree_txt} ({cause_txt}) et bloque "
                f"l'OF {of.numero}. Aucune ligne alternative n'a de machine libre : "
                f"je recommande une maintenance d'urgence pour remettre {machine.code} "
                "en service au plus vite."
            )
            nouvelles.append(
                _proposer(
                    db,
                    cle=cle,
                    type_="maintenance_urgence",
                    severite=SeveriteAlerte.CRITICAL,
                    titre=f"OF {of.numero} bloqué — aucune ligne de repli",
                    diagnostic=diagnostic,
                    action_libelle=f"Lancer une maintenance d'urgence sur {machine.code}",
                    action={"type": "maintenance_urgence", "machine_id": machine.id},
                    machine_id=machine.id,
                    ordre_fabrication_id=of.id,
                )
            )


def _regle_derive_qualite(db: Session, nouvelles: list[AgentProposal]) -> None:
    depuis = datetime.utcnow() - FENETRE_QUALITE
    machines = db.execute(
        select(Machine).where(Machine.actif.is_(True), Machine.statut == StatutMachine.MARCHE)
    ).scalars().all()

    for machine in machines:
        events = db.execute(
            select(QualityEvent).where(
                QualityEvent.machine_id == machine.id, QualityEvent.created_at >= depuis
            )
        ).scalars().all()
        bonnes = sum(e.quantite for e in events if e.type == TypeEvenementQualite.BONNE)
        rebuts = sum(e.quantite for e in events if e.type == TypeEvenementQualite.REBUT)
        total = bonnes + rebuts
        if total < MIN_PIECES_QUALITE:
            continue
        taux = rebuts / total
        if taux < SEUIL_TAUX_REBUT:
            continue
        cle = f"derive_qualite:{machine.id}:{datetime.utcnow().strftime('%Y%m%d%H')}"
        if _deja_traitee(db, cle):
            continue

        causes = [e.cause.value for e in events if e.cause is not None]
        cause_principale = max(set(causes), key=causes.count) if causes else None
        cause_txt = (
            f" Cause dominante : {cause_principale.replace('_', ' ').lower()}."
            if cause_principale
            else ""
        )
        diagnostic = (
            f"Dérive qualité sur {machine.code} : {rebuts} rebut(s) sur {total} pièces "
            f"({taux * 100:.0f}% de rebut) sur les {int(FENETRE_QUALITE.total_seconds() // 60)} "
            f"dernières minutes, au-dessus du seuil de {SEUIL_TAUX_REBUT * 100:.0f}%."
            f"{cause_txt} Je recommande de mettre la machine en pause pour un réglage."
        )
        nouvelles.append(
            _proposer(
                db,
                cle=cle,
                type_="derive_qualite",
                severite=SeveriteAlerte.WARNING,
                titre=f"Dérive qualité sur {machine.code} ({taux * 100:.0f}% de rebut)",
                diagnostic=diagnostic,
                action_libelle=f"Mettre {machine.code} en pause pour réglage",
                action={"type": "pause_reglage", "machine_id": machine.id},
                machine_id=machine.id,
            )
        )


def _regle_stock_bas(db: Session, nouvelles: list[AgentProposal]) -> None:
    mps = db.execute(
        select(MatierePremiere).where(
            MatierePremiere.actif.is_(True), MatierePremiere.seuil_alerte.is_not(None)
        )
    ).scalars().all()

    for mp in mps:
        dispo = manufacturing_svc.stock_disponible_mp(db, mp.id)
        if mp.seuil_alerte is None or dispo >= mp.seuil_alerte:
            continue
        cle = f"stock_bas:{mp.id}"
        if _deja_traitee(db, cle):
            continue
        diagnostic = (
            f"Le stock de {mp.code} ({mp.designation}) est descendu à {dispo} "
            f"{mp.unite.value}, sous le seuil d'alerte de {mp.seuil_alerte} "
            f"{mp.unite.value}. Les prochains OF utilisant cette MP risquent d'être "
            "refusés. Je recommande de créer une alerte de réapprovisionnement."
        )
        nouvelles.append(
            _proposer(
                db,
                cle=cle,
                type_="stock_bas",
                severite=SeveriteAlerte.WARNING,
                titre=f"Stock bas : {mp.code} ({dispo} {mp.unite.value})",
                diagnostic=diagnostic,
                action_libelle="Créer l'alerte de réapprovisionnement",
                action={"type": "alerte_reappro", "matiere_premiere_id": mp.id},
            )
        )


def analyser(db: Session) -> list[AgentProposal]:
    """Exécute toutes les règles et renvoie les nouvelles propositions créées."""
    nouvelles: list[AgentProposal] = []
    _regle_arret_bloquant(db, nouvelles)
    _regle_derive_qualite(db, nouvelles)
    _regle_stock_bas(db, nouvelles)
    return nouvelles


# --------------------------------------------------------------------------- #
# Exécution des actions approuvées
# --------------------------------------------------------------------------- #


def executer_proposition(db: Session, proposition: AgentProposal) -> str:
    """Exécute l'action structurée d'une proposition approuvée. Renvoie le résumé."""
    action = proposition.action or {}
    type_ = action.get("type")

    if type_ == "basculer_of":
        of = db.get(OrdreFabrication, action["of_id"])
        if of is None:
            raise AppError("OF introuvable pour la bascule.")
        cible = line_scoring_service.machine_libre_sur_ligne(db, action["ligne_id"])
        if cible is None:
            raise AppError("Plus aucune machine libre sur la ligne cible.")
        source = db.get(Machine, action.get("machine_source_id"))
        if source is not None and source.ordre_fabrication_id == of.id:
            source.ordre_fabrication_id = None
        of.ligne_production_id = action["ligne_id"]
        db.flush()
        simulator_service.demarrer(db, cible, ordre_fabrication_id=of.id)
        db.flush()
        if source is not None:
            broadcast_service.diffuser_machine(db, source)
        broadcast_service.diffuser_machine(db, cible)
        return f"OF {of.numero} basculé : machine {cible.code} démarrée."

    if type_ == "maintenance_urgence":
        machine = db.get(Machine, action["machine_id"])
        if machine is None:
            raise AppError("Machine introuvable.")
        simulator_service.demarrer_maintenance(
            db,
            machine,
            type_maintenance=TypeMaintenance.URGENCE.value,
            description="Maintenance d'urgence déclenchée par le superviseur Nova",
        )
        db.flush()
        broadcast_service.diffuser_machine(db, machine)
        return f"Maintenance d'urgence lancée sur {machine.code}."

    if type_ == "pause_reglage":
        machine = db.get(Machine, action["machine_id"])
        if machine is None:
            raise AppError("Machine introuvable.")
        simulator_service.mettre_en_pause(db, machine)
        db.flush()
        broadcast_service.diffuser_machine(db, machine)
        return f"{machine.code} mise en pause pour réglage qualité."

    if type_ == "alerte_reappro":
        mp = db.get(MatierePremiere, action["matiere_premiere_id"])
        if mp is None:
            raise AppError("Matière première introuvable.")
        dispo = manufacturing_svc.stock_disponible_mp(db, mp.id)
        alerte = Alert(
            severity=SeveriteAlerte.WARNING,
            type="REAPPROVISIONNEMENT",
            message=(
                f"Réapprovisionner {mp.code} ({mp.designation}) : stock {dispo} "
                f"{mp.unite.value}, seuil {mp.seuil_alerte} {mp.unite.value}."
            ),
        )
        db.add(alerte)
        db.flush()
        return f"Alerte de réapprovisionnement créée pour {mp.code}."

    raise AppError(f"Action inconnue : {type_!r}")


def decider(db: Session, proposition_id: int, *, approuver: bool) -> AgentProposal:
    """Approuve (et exécute) ou rejette une proposition. Diffuse la mise à jour."""
    proposition = db.get(AgentProposal, proposition_id)
    if proposition is None:
        raise NotFoundError("Proposition introuvable.")
    if proposition.statut != StatutProposition.PROPOSEE:
        raise FabricationError("Cette proposition a déjà été traitée.")

    proposition.decided_at = datetime.utcnow()
    if not approuver:
        proposition.statut = StatutProposition.REJETEE
        proposition.resultat = "Rejetée par l'opérateur."
    else:
        proposition.statut = StatutProposition.APPROUVEE
        try:
            resume = executer_proposition(db, proposition)
        except AppError as exc:
            proposition.statut = StatutProposition.ECHOUEE
            proposition.resultat = f"Échec : {exc.message}"
        else:
            proposition.statut = StatutProposition.EXECUTEE
            proposition.resultat = resume
    db.flush()
    db.refresh(proposition)
    broadcast_service.diffuser(
        {"type": "agent_proposal_update", "proposal": serialiser_proposition(proposition)}
    )
    return proposition


# --------------------------------------------------------------------------- #
# Boucle d'arrière-plan
# --------------------------------------------------------------------------- #


def _tick_superviseur() -> list[dict]:
    """Un passage de détection (exécuté dans un thread — session propre)."""
    with session_scope() as db:
        nouvelles = analyser(db)
        return [serialiser_proposition(p) for p in nouvelles]


async def boucle_superviseur(intervalle_s: float = INTERVALLE_BOUCLE_S) -> None:
    """Boucle infinie : détection périodique + diffusion des nouvelles propositions."""
    logger.info("superviseur_demarre", intervalle_s=intervalle_s)
    from app.services.websocket_manager import manager

    while True:
        try:
            nouvelles = await asyncio.to_thread(_tick_superviseur)
            for proposition in nouvelles:
                await manager.broadcast({"type": "agent_proposal", "proposal": proposition})
        except asyncio.CancelledError:
            logger.info("superviseur_arrete")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("superviseur_tick_failed")
        await asyncio.sleep(intervalle_s)
