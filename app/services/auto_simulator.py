"""Simulation autonome de l'atelier : la « vraie vie » sans clics.

Quand le mode auto est actif, une boucle produit pour chaque machine EN MARCHE :
  - des bonnes pièces au rythme du temps de cycle (crédit fractionnaire cumulé) ;
  - des rebuts occasionnels (taux ~4 %) ;
  - des micro-arrêts aléatoires auto-résolus (15-40 s) ;
  - une dérive lente de température (tag capteur), pour nourrir la timeline.

Tous les événements passent par `event_service.enregistrer_evenement` : ce sont de
vrais événements MES (TRS, arrêts, qualité) — le superviseur autonome y réagit
exactement comme il le ferait sur une usine réelle.

Le module expose aussi les 3 scénarios de démo (panne critique, dérive qualité,
rupture de stock) : un clic = un incident réaliste = Nova réagit en direct.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.exceptions import FabricationError, NotFoundError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import (
    DowntimeEvent,
    LotMatierePremiere,
    Machine,
    MaintenanceEvent,
    MatierePremiere,
)
from app.models.enums import CauseArret, CauseRebut, StatutLot, StatutMachine, TypeEvenementMachine
from app.services import broadcast_service, event_service
from app.services import manufacturing as manufacturing_svc

logger = get_logger(__name__)

TICK_S = 2.0
TAUX_REBUT = 0.04
# Plus de micro-arrêt aléatoire : une machine en marche ne tombe plus en panne
# toute seule. Les pannes ne surviennent que sur action délibérée (bouton
# « Provoquer une panne » / alarme manuelle).
PROBA_MICRO_ARRET = 0.0
DUREE_MICRO_ARRET_S = (15, 40)
PERIODE_TAG_TICKS = 8

CAUSES_REBUT_ALEATOIRES = [
    CauseRebut.DEFAUT_VISUEL,
    CauseRebut.DEFAUT_DIMENSIONNEL,
    CauseRebut.MAUVAIS_REGLAGE,
]


class AutoSimulator:
    """État global du mode auto (process unique — suffisant pour le MVP)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._credit: dict[int, float] = {}  # machine_id -> secondes de production cumulées
        self._micro_fin: dict[int, datetime] = {}  # machine_id -> fin du micro-arrêt
        self._temperature: dict[int, float] = {}
        self._tick_compteur = 0

    @property
    def actif(self) -> bool:
        return self._task is not None and not self._task.done()

    def demarrer(self) -> None:
        if self.actif:
            return
        # Amorçage : on remet toute la ligne en marche avant de lancer la boucle,
        # pour un plateau vivant et stable (plus aucune machine bloquée à l'arrêt,
        # en panne ou en maintenance résiduelle).
        try:
            self._amorcer()
        except Exception:  # noqa: BLE001
            logger.exception("auto_simulator_amorcage_failed")
        self._task = asyncio.create_task(self._boucle())
        logger.info("auto_simulator_demarre")

    def _amorcer(self) -> None:
        """Redémarre toute machine active qui n'est pas déjà en marche.

        Ferme les arrêts et maintenances encore ouverts puis repasse la machine en
        MARCHE — la ligne repart proprement et rien ne reste figé d'un run précédent.
        """
        with session_scope() as db:
            machines = db.execute(
                select(Machine).where(Machine.actif.is_(True))
            ).scalars().all()
            for machine in machines:
                if machine.statut == StatutMachine.MARCHE:
                    continue
                for dt in db.execute(
                    select(DowntimeEvent).where(
                        DowntimeEvent.machine_id == machine.id,
                        DowntimeEvent.end_time.is_(None),
                    )
                ).scalars().all():
                    dt.end_time = datetime.utcnow()
                for me in db.execute(
                    select(MaintenanceEvent).where(
                        MaintenanceEvent.machine_id == machine.id,
                        MaintenanceEvent.end_time.is_(None),
                    )
                ).scalars().all():
                    me.end_time = datetime.utcnow()
                self._micro_fin.pop(machine.id, None)
                event_service.enregistrer_evenement(
                    db,
                    machine=machine,
                    type_evenement=TypeEvenementMachine.MACHINE_STARTED,
                )
                broadcast_service.diffuser_machine(db, machine)

    def arreter(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        logger.info("auto_simulator_arrete")

    async def _boucle(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("auto_simulator_tick_failed")
            await asyncio.sleep(TICK_S)

    def _tick(self) -> None:
        self._tick_compteur += 1
        with session_scope() as db:
            machines = db.execute(
                select(Machine).where(Machine.actif.is_(True))
            ).scalars().all()

            for machine in machines:
                # Résolution automatique des micro-arrêts arrivés à échéance.
                fin = self._micro_fin.get(machine.id)
                if fin is not None and datetime.utcnow() >= fin:
                    del self._micro_fin[machine.id]
                    if machine.statut == StatutMachine.PANNE:
                        event_service.enregistrer_evenement(
                            db,
                            machine=machine,
                            type_evenement=TypeEvenementMachine.DOWNTIME_RESOLVED,
                            payload={"comment": "Micro-arrêt auto-résolu (simulation)"},
                        )
                        broadcast_service.diffuser_machine(db, machine)
                    continue

                # Production is valid only while a machine is assigned to an OF.
                if machine.statut != StatutMachine.MARCHE or machine.ordre_fabrication_id is None:
                    continue

                cycle = float(machine.temps_cycle_actuel_s or machine.temps_cycle_cible_s or 0)
                if cycle <= 0:
                    continue

                # Production au rythme du temps de cycle.
                credit = self._credit.get(machine.id, 0.0) + TICK_S
                n_pieces = int(credit // cycle)
                self._credit[machine.id] = credit - n_pieces * cycle
                if n_pieces > 0:
                    n_rebuts = sum(1 for _ in range(n_pieces) if random.random() < TAUX_REBUT)
                    n_bonnes = n_pieces - n_rebuts
                    if n_bonnes > 0:
                        event_service.enregistrer_evenement(
                            db,
                            machine=machine,
                            type_evenement=TypeEvenementMachine.GOOD_UNIT_PRODUCED,
                            payload={"quantite": n_bonnes},
                        )
                    if n_rebuts > 0 and machine.statut == StatutMachine.MARCHE:
                        event_service.enregistrer_evenement(
                            db,
                            machine=machine,
                            type_evenement=TypeEvenementMachine.SCRAP_UNIT_PRODUCED,
                            payload={
                                "quantite": n_rebuts,
                                "cause": random.choice(CAUSES_REBUT_ALEATOIRES).value,
                            },
                        )
                    broadcast_service.diffuser_machine(db, machine)

                # Dérive lente de température (tag capteur) toutes les N ticks.
                if self._tick_compteur % PERIODE_TAG_TICKS == 0:
                    temp = self._temperature.get(machine.id, 62.0)
                    temp = round(
                        min(95.0, max(45.0, temp + random.uniform(-1.5, 1.8))), 1
                    )
                    self._temperature[machine.id] = temp
                    event_service.enregistrer_evenement(
                        db,
                        machine=machine,
                        type_evenement=TypeEvenementMachine.SENSOR_TAG_UPDATED,
                        payload={"tag": "temperature_C", "valeur": temp},
                    )


auto_simulator = AutoSimulator()


# --------------------------------------------------------------------------- #
# Scénarios de démonstration
# --------------------------------------------------------------------------- #


def _machine_en_marche(db, *, avec_of: bool = False) -> Machine | None:
    machines = db.execute(
        select(Machine).where(Machine.actif.is_(True), Machine.statut == StatutMachine.MARCHE)
    ).scalars().all()
    if avec_of:
        prioritaires = [m for m in machines if m.ordre_fabrication_id is not None]
        if prioritaires:
            return prioritaires[0]
    return machines[0] if machines else None


def scenario_panne_critique(db) -> str:
    """Panne mécanique sur une machine en production (avec OF de préférence)."""
    machine = _machine_en_marche(db, avec_of=True)
    if machine is None:
        raise FabricationError(
            "Aucune machine en marche : démarrez une machine (ou le mode auto) d'abord."
        )
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.SENSOR_TAG_UPDATED,
        payload={"tag": "temperature_C", "valeur": 97.4},
    )
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.DOWNTIME_STARTED,
        payload={
            "cause": CauseArret.PANNE_MECANIQUE.value,
            "comment": "Surchauffe broche détectée (97 °C) — arrêt de sécurité",
        },
    )
    broadcast_service.diffuser_machine(db, machine)
    return (
        f"💥 Panne critique déclenchée sur {machine.code} (surchauffe broche). "
        "Le superviseur Nova va détecter le blocage et proposer une action."
    )


def scenario_derive_qualite(db) -> str:
    """Rafale de rebuts sur une machine en marche → la règle qualité se déclenche.

    Le nombre de rebuts injectés est calculé pour dépasser franchement le seuil
    du superviseur (20 %) quel que soit le volume déjà produit sur la fenêtre.
    """
    from math import ceil

    from app.models import QualityEvent
    from app.models.enums import TypeEvenementQualite

    machine = _machine_en_marche(db)
    if machine is None:
        raise FabricationError(
            "Aucune machine en marche : démarrez une machine (ou le mode auto) d'abord."
        )

    depuis = datetime.utcnow() - timedelta(minutes=15)
    events = db.execute(
        select(QualityEvent).where(
            QualityEvent.machine_id == machine.id, QualityEvent.created_at >= depuis
        )
    ).scalars().all()
    total = sum(e.quantite for e in events)
    rebuts = sum(e.quantite for e in events if e.type == TypeEvenementQualite.REBUT)
    # n tel que (rebuts + n) / (total + n) >= 25 % (marge au-dessus du seuil de 20 %).
    n = max(6, ceil((0.25 * total - rebuts) / 0.75) + 1)

    restant = n
    while restant > 0:
        lot = min(3, restant)
        event_service.enregistrer_evenement(
            db,
            machine=machine,
            type_evenement=TypeEvenementMachine.SCRAP_UNIT_PRODUCED,
            payload={"quantite": lot, "cause": CauseRebut.MAUVAIS_REGLAGE.value},
        )
        restant -= lot
    broadcast_service.diffuser_machine(db, machine)
    return (
        f"⚠ Dérive qualité injectée sur {machine.code} ({n} rebuts, mauvais réglage). "
        "Nova va analyser le taux de rebut et proposer un réglage."
    )


def scenario_rupture_stock(db) -> str:
    """Vide (presque) le stock d'une MP à seuil d'alerte → règle stock bas."""
    mps = db.execute(
        select(MatierePremiere).where(
            MatierePremiere.actif.is_(True), MatierePremiere.seuil_alerte.is_not(None)
        )
    ).scalars().all()
    cible: MatierePremiere | None = None
    for mp in mps:
        if manufacturing_svc.stock_disponible_mp(db, mp.id) > (mp.seuil_alerte or 0):
            cible = mp
            break
    if cible is None:
        raise NotFoundError(
            "Aucune MP avec un seuil d'alerte et du stock au-dessus du seuil."
        )

    reste_cible = (cible.seuil_alerte or Decimal("0")) * Decimal("0.3")
    lots = db.execute(
        select(LotMatierePremiere).where(
            LotMatierePremiere.matiere_premiere_id == cible.id,
            LotMatierePremiere.statut == StatutLot.DISPONIBLE,
            LotMatierePremiere.quantite_restante > 0,
        )
    ).scalars().all()
    total = sum((lot.quantite_restante for lot in lots), Decimal("0"))
    a_retirer = total - reste_cible
    for lot in lots:
        if a_retirer <= 0:
            break
        retrait = min(lot.quantite_restante, a_retirer)
        lot.quantite_restante = lot.quantite_restante - retrait
        if lot.quantite_restante == 0:
            lot.statut = StatutLot.EPUISE
        a_retirer -= retrait
    db.flush()
    return (
        f"📦 Rupture de stock simulée sur {cible.code} ({cible.designation}) : "
        f"stock ramené à ~{reste_cible} {cible.unite.value}, sous le seuil de "
        f"{cible.seuil_alerte} {cible.unite.value}. Nova va proposer un réapprovisionnement."
    )
