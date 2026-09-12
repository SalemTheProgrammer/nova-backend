"""Orchestrations de production en plusieurs commandes machine.

Lancer un OF sur une ligne pleine, basculer un OF vers une autre ligne, arrêter
toute une ligne : chaque orchestration enchaîne des commandes machine
(`machine_command_service`) et des écritures MES.

Règle : chaque écriture MES est committée AVANT la commande machine suivante —
l'ingestion Sparkplug doit pouvoir écrire l'état confirmé par l'automate
pendant qu'on attend son accusé. Si une étape échoue, l'exception décrit
l'étape atteinte ; la base reste cohérente avec ce que les automates ont
réellement confirmé (rien n'est « supposé » démarré ou arrêté).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, FabricationError, NotFoundError
from app.db.session import session_scope
from app.models import LigneProduction, Machine, OrdreFabrication
from app.models.enums import StatutMachine, StatutOF
from app.services import broadcast_service, line_queue_service, line_scoring_service
from app.services import machine_command_service
from app.services.line_queue_service import DispositionPreemption

_PRODUCTIVES = (StatutMachine.MARCHE, StatutMachine.PAUSE)
_INDISPONIBLES = (StatutMachine.PANNE, StatutMachine.MAINTENANCE)


# --------------------------------------------------------------------------- #
# Lancement immédiat (avec préemption éventuelle)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Lancement:
    of_numero: str
    machine_code: str
    ligne_code: str
    of_preempte: str | None = None
    disposition: DispositionPreemption | None = None


@dataclass(frozen=True)
class _Preemption:
    machine_id: int
    machine_statut: StatutMachine
    of_id: int
    of_numero: str


def lancer_of(
    of_id: int, *, preempt_disposition: DispositionPreemption | None = None
) -> Lancement:
    """Lance l'OF sur une machine libre de sa ligne ; si la ligne est pleine et
    qu'une disposition est fournie, préempte l'OF le moins urgent (EDD)."""
    with session_scope() as db:
        of = db.get(OrdreFabrication, of_id)
        if of is None:
            raise NotFoundError(f"OF introuvable (id={of_id}).")
        if of.statut in (StatutOF.TERMINE, StatutOF.ANNULE):
            raise FabricationError(f"Impossible de lancer {of.numero} : statut {of.statut.value}.")
        porteuse = db.execute(
            select(Machine).where(Machine.ordre_fabrication_id == of.id)
        ).scalar_one_or_none()
        preemption: _Preemption | None = None
        if porteuse is not None:
            if porteuse.statut == StatutMachine.MARCHE:
                raise FabricationError(
                    f"L'OF {of.numero} est déjà en cours de production sur la machine {porteuse.code}."
                )
            machine_id, machine_code = porteuse.id, porteuse.code
            numero = of.numero
            ligne_code = of.ligne_production.code if of.ligne_production else porteuse.ligne_production.code
        else:
            if of.ligne_production_id is None or of.ligne_production is None:
                raise FabricationError(f"Aucune ligne n'est affectée à l'OF {of.numero}.")

            numero, ligne_id, ligne_code = of.numero, of.ligne_production_id, of.ligne_production.code
            libre = line_scoring_service.machine_libre_sur_ligne(db, ligne_id)
            if libre is not None:
                machine_id, machine_code = libre.id, libre.code
            else:
                if preempt_disposition is None:
                    raise FabricationError(
                        f"Aucune machine libre sur {ligne_code} : préemptez un OF en cours "
                        f"ou mettez {numero} en file."
                    )
                candidates = [
                    occ
                    for occ in line_queue_service.occupations_ligne(db, ligne_id)
                    if occ.machine.statut not in _INDISPONIBLES
                ]
                if not candidates:
                    raise FabricationError(
                        f"Aucune machine préemptable sur {ligne_code} (toutes en panne ou en maintenance)."
                    )
                # On préempte l'OF dont l'échéance est la plus lointaine (le moins urgent).
                cible = max(candidates, key=lambda occ: line_queue_service.cle_edd(occ.ordre))
                machine_id, machine_code = cible.machine.id, cible.machine.code
                preemption = _Preemption(
                    machine_id=cible.machine.id,
                    machine_statut=cible.machine.statut,
                    of_id=cible.ordre.id,
                    of_numero=cible.ordre.numero,
                )

    if preemption is not None and preempt_disposition is not None:
        if preemption.machine_statut in _PRODUCTIVES:
            machine_command_service.arreter(
                preemption.machine_id, commentaire=f"Préemption par l'OF {numero}"
            )
        with session_scope() as db:
            line_queue_service.appliquer_disposition(
                db,
                machine_id=preemption.machine_id,
                of_id=preemption.of_id,
                disposition=preempt_disposition,
            )

    machine_command_service.demarrer(machine_id, ordre_fabrication_id=of_id)
    return Lancement(
        of_numero=numero,
        machine_code=machine_code,
        ligne_code=ligne_code,
        of_preempte=preemption.of_numero if preemption else None,
        disposition=preempt_disposition if preemption else None,
    )


# --------------------------------------------------------------------------- #
# Bascule d'un OF vers une autre ligne
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Bascule:
    of_numero: str
    ligne_code: str
    machine_cible: str
    machine_source: str | None


def verifier_bascule(
    db: Session, of_id: int, ligne_id: int
) -> tuple[OrdreFabrication, LigneProduction, Machine]:
    """Préconditions d'une bascule (lecture seule) : OF actif, ligne active,
    article homologué, machine libre. Renvoie (of, ligne, machine cible)."""
    of = db.get(OrdreFabrication, of_id)
    if of is None:
        raise NotFoundError(f"OF introuvable (id={of_id}).")
    if of.statut in (StatutOF.TERMINE, StatutOF.ANNULE):
        raise FabricationError(f"L'OF {of.numero} est {of.statut.value} : bascule impossible.")
    ligne = db.get(LigneProduction, ligne_id)
    if ligne is None or not ligne.actif:
        raise FabricationError(f"Ligne cible introuvable ou inactive (id={ligne_id}).")
    if all(article.id != of.article_id for article in ligne.articles):
        raise FabricationError(
            f"L'article {of.article.code} n'est pas homologué sur la ligne {ligne.code}."
        )
    cible = line_scoring_service.machine_libre_sur_ligne(db, ligne_id)
    if cible is None:
        raise FabricationError(f"Aucune machine libre sur la ligne {ligne.code}.")
    return of, ligne, cible


def basculer_of(of_id: int, ligne_id: int) -> Bascule:
    with session_scope() as db:
        of, ligne, cible = verifier_bascule(db, of_id, ligne_id)
        source = db.execute(
            select(Machine).where(Machine.ordre_fabrication_id == of.id)
        ).scalars().first()
        numero, ligne_code, cible_id, cible_code = of.numero, ligne.code, cible.id, cible.code
        source_id = source.id if source else None
        source_code = source.code if source else None
        source_statut = source.statut if source else None

    # 1. La machine source ne produit plus cet OF (une machine en panne reste en panne).
    if source_id is not None and source_statut in _PRODUCTIVES:
        machine_command_service.arreter(
            source_id, commentaire=f"Bascule de l'OF {numero} vers {ligne_code}"
        )

    # 2. MES : l'OF quitte la source et change de ligne (créneau prévu invalidé).
    with session_scope() as db:
        of = db.get(OrdreFabrication, of_id)
        if source_id is not None:
            source = db.get(Machine, source_id)
            if source is not None and source.ordre_fabrication_id == of_id:
                source.ordre_fabrication_id = None
        of.ligne_production_id = ligne_id
        of.date_debut_prevue = None
        of.date_fin_prevue = None
    if source_id is not None:
        broadcast_service.diffuser_machines_par_id([source_id])

    # 3. Démarrage sur la ligne cible (confirmé par l'automate).
    machine_command_service.demarrer(cible_id, ordre_fabrication_id=of_id)
    return Bascule(
        of_numero=numero, ligne_code=ligne_code, machine_cible=cible_code, machine_source=source_code
    )


# --------------------------------------------------------------------------- #
# Arrêt d'une ligne complète
# --------------------------------------------------------------------------- #
@dataclass
class ArretLigne:
    ligne_code: str
    arretees: list[str] = field(default_factory=list)
    deja_arretees: list[str] = field(default_factory=list)
    indisponibles: list[str] = field(default_factory=list)
    echecs: list[tuple[str, str]] = field(default_factory=list)


def arreter_ligne(ligne_id: int) -> ArretLigne:
    with session_scope() as db:
        ligne = db.get(LigneProduction, ligne_id)
        if ligne is None:
            raise NotFoundError(f"Ligne introuvable (id={ligne_id}).")
        machines = [
            (m.id, m.code, m.statut)
            for m in db.execute(
                select(Machine)
                .where(Machine.ligne_production_id == ligne_id, Machine.actif.is_(True))
                .order_by(Machine.code)
            ).scalars()
        ]
        resultat = ArretLigne(ligne_code=ligne.code)

    for machine_id, code, statut in machines:
        if statut == StatutMachine.ARRET:
            resultat.deja_arretees.append(code)
        elif statut in _INDISPONIBLES:
            resultat.indisponibles.append(code)
        else:
            try:
                machine_command_service.arreter(machine_id, commentaire=f"Arrêt de la ligne {resultat.ligne_code}")
            except AppError as exc:
                resultat.echecs.append((code, exc.message))
            else:
                resultat.arretees.append(code)
    return resultat
