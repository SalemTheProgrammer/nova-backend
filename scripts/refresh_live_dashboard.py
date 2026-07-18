"""Rafraîchit les données temps réel du tableau de bord pour qu'elles « vivent »
maintenant — les KPI (TRS, disponibilité, performance, qualité, cadence, série
de production, activité récente) sont TOUS recalculés à la lecture sur une
fenêtre glissante (5 min pour la cadence, 8 h pour le TRS). Dès que les
événements datent de plus de 8 h, tout retombe à zéro : c'est exactement l'état
dans lequel se trouvait la base (derniers événements il y a ~2 jours, toutes les
machines à l'arrêt sans OF monté).

Ce script remet la ligne en production « comme en vrai » :
  - monte un OF EN_COURS sur chaque machine productive et la passe en MARCHE ;
  - purge les vieux événements qualité/arrêt (hors fenêtre) devenus du bruit ;
  - régénère une fenêtre de 8 h d'événements qualité + arrêts qui SE TERMINE
    maintenant, minute par minute, pour chaque machine, avec un TRS cible
    crédible propre à chaque poste ;
  - laisse une machine en panne réaliste (downtime ouvert) pour que les arrêts
    non planifiés et le MTTR/MTBF ne soient pas nuls.

Après ce script, si AUTO_SIM_AUTOSTART=true (déjà le cas dans .env), la boucle
`auto_simulator` prend le relais et continue d'alimenter la fenêtre en direct :
les KPI restent vivants sans intervention.

Idempotent : relançable à volonté, il repart toujours d'une fenêtre propre.

Lancer depuis backend/ :
    python -m scripts.refresh_live_dashboard
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal, init_db
from app.models import (
    DowntimeEvent,
    Machine,
    MachineEvent,
    OrdreFabrication,
    QualityEvent,
)
from app.models.enums import (
    CauseArret,
    CauseRebut,
    StatutMachine,
    StatutOF,
    TypeEvenementMachine,
    TypeEvenementQualite,
)

# Graine fixe : mêmes chiffres « réalistes » à chaque exécution (reproductible).
random.seed(20260718)

FENETRE = timedelta(hours=8)
PAS = timedelta(minutes=1)

# Profil de production par machine (TRS visé plausible d'un atelier pharma).
# marche=True : la machine tourne maintenant (MARCHE + OF monté + cadence live).
# panne_ouverte : un arrêt non planifié TOUJOURS en cours (pour MTTR/MTBF/arrêts
# non planifiés non nuls et un statut PANNE cohérent sur le dashboard).
PROFILS = {
    "M-01": dict(marche=True, trs=0.86, tq=0.975, do=0.94, panne_ouverte=False),
    "M-02": dict(marche=True, trs=0.79, tq=0.955, do=0.92, panne_ouverte=False),
    "M-07": dict(marche=True, trs=0.88, tq=0.980, do=0.96, panne_ouverte=False),
    "M-08": dict(marche=True, trs=0.81, tq=0.960, do=0.93, panne_ouverte=False),
    "M-09": dict(marche=True, trs=0.72, tq=0.930, do=0.90, panne_ouverte=False),
    # M-10 : tournait bien puis est tombée en panne il y a 6 h. Elle a donc
    # produit (bonne qualité) sur les ~2 premières heures de la fenêtre, avant
    # l'arrêt encore ouvert — d'où une disponibilité faible mais une qualité
    # normale (au lieu d'une machine « fantôme » à 0 qui fausse la moyenne ligne).
    "M-10": dict(marche=False, trs=0.20, tq=0.965, do=0.25, panne_ouverte=True),
}

# Arrêts planifiés/non planifiés semés dans la fenêtre (déjà résolus) pour donner
# du corps au camembert des causes et au calcul de fiabilité.
ARRETS_RESOLUS = {
    "M-01": [(CauseArret.CHANGEMENT_SERIE, 14)],
    "M-02": [(CauseArret.MICRO_ARRET, 2), (CauseArret.REGLAGE_MACHINE, 9)],
    "M-07": [(CauseArret.NETTOYAGE, 11)],
    "M-08": [(CauseArret.MICRO_ARRET, 3), (CauseArret.MAINTENANCE_PLANIFIEE, 22)],
    "M-09": [(CauseArret.PANNE_ELECTRIQUE, 18), (CauseArret.MICRO_ARRET, 2)],
}


def _rythme_bonnes_par_min(*, cycle_s: float, trs: float, tq: float, do: float) -> float:
    """Débit de BONNES pièces/min pour atteindre le TRS visé sur la fenêtre.

    Mêmes relations que trs_service._calculer : TRS = TQ·TP·DO, TP = TN/TF,
    TN = (bonnes+rejets)·cycle, TU = bonnes·cycle, TF = DO·TR.
    En régime permanent, bonnes/min = 60·(TRS/(tq·do))·(tq)·do / cycle
                                     = 60·TRS / cycle.  (algébriquement exact)
    """
    if cycle_s <= 0:
        return 0.0
    return 60.0 * trs / cycle_s


def refresh() -> None:
    init_db()
    db = SessionLocal()
    try:
        # utcnow() : le reste du code (dashboard_service, trs_service) compare
        # les timestamps en naïf-UTC ; on reste cohérent avec eux ici.
        maintenant = datetime.utcnow()
        depuis = maintenant - FENETRE

        machines = {
            m.code: m
            for m in db.execute(select(Machine).where(Machine.actif.is_(True))).scalars()
        }
        if not machines:
            print("Aucune machine active — rien à faire.")
            return

        # ------------------------------------------------------------------
        # 1) Purge des événements hors fenêtre (bruit périmé) sur ces machines.
        # ------------------------------------------------------------------
        ids = [m.id for m in machines.values()]
        db.query(QualityEvent).filter(QualityEvent.machine_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(DowntimeEvent).filter(DowntimeEvent.machine_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(MachineEvent).filter(MachineEvent.machine_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.flush()

        # ------------------------------------------------------------------
        # 2) Monter un OF EN_COURS par ligne sur les machines qui tournent.
        #    On prend un OF EN_COURS de la ligne (il en existe déjà) sinon on
        #    promeut un OF PLANIFIE de la ligne. La machine référence l'OF pour
        #    que l'en-tête « OF actif » et l'auto_simulator fonctionnent.
        # ------------------------------------------------------------------
        of_par_ligne: dict[int, OrdreFabrication] = {}

        def _of_pour_ligne(ligne_id: int) -> OrdreFabrication | None:
            if ligne_id in of_par_ligne:
                return of_par_ligne[ligne_id]
            of = db.execute(
                select(OrdreFabrication)
                .where(
                    OrdreFabrication.ligne_production_id == ligne_id,
                    OrdreFabrication.statut == StatutOF.EN_COURS,
                )
                .order_by(OrdreFabrication.id)
            ).scalars().first()
            if of is None:
                of = db.execute(
                    select(OrdreFabrication)
                    .where(
                        OrdreFabrication.ligne_production_id == ligne_id,
                        OrdreFabrication.statut == StatutOF.PLANIFIE,
                    )
                    .order_by(OrdreFabrication.id)
                ).scalars().first()
                if of is not None:
                    of.statut = StatutOF.EN_COURS
            if of is None:
                # Dernier recours : rouvrir un OF TERMINE de la ligne (on le
                # relance en EN_COURS) pour qu'il y ait toujours un OF monté.
                of = db.execute(
                    select(OrdreFabrication)
                    .where(
                        OrdreFabrication.ligne_production_id == ligne_id,
                        OrdreFabrication.statut == StatutOF.TERMINE,
                    )
                    .order_by(OrdreFabrication.id.desc())
                ).scalars().first()
                if of is not None:
                    of.statut = StatutOF.EN_COURS
                    of.date_fin_reelle = None
                    of.date_debut_reelle = depuis
            if of is not None and of.date_debut_reelle is None:
                of.date_debut_reelle = depuis
            of_par_ligne[ligne_id] = of
            return of

        # ------------------------------------------------------------------
        # 3) Régénérer la fenêtre de 8 h par machine.
        # ------------------------------------------------------------------
        for code, machine in machines.items():
            profil = PROFILS.get(code, dict(marche=True, trs=0.80, tq=0.96, do=0.93, panne_ouverte=False))
            cycle = float(machine.temps_cycle_cible_s or 0) or 3.5

            of = _of_pour_ligne(machine.ligne_production_id) if profil["marche"] else None

            # Reset des compteurs cumulés machine (cohérents avec le nouveau run).
            machine.quantite_produite = 0
            machine.quantite_bonne = 0
            machine.quantite_rejetee = 0

            if profil["marche"] and of is not None:
                machine.statut = StatutMachine.MARCHE
                machine.ordre_fabrication_id = of.id
                machine.temps_cycle_actuel_s = machine.temps_cycle_cible_s
                db.add(
                    MachineEvent(
                        machine_id=machine.id,
                        ordre_fabrication_id=of.id,
                        type=TypeEvenementMachine.MACHINE_STARTED,
                        payload={"source": "refresh_live_dashboard"},
                        created_at=depuis,
                    )
                )
            elif profil["panne_ouverte"]:
                machine.statut = StatutMachine.PANNE
                machine.ordre_fabrication_id = None
            else:
                machine.statut = StatutMachine.ARRET
                machine.ordre_fabrication_id = None

            machine.dernier_evenement_at = maintenant

            # -- Arrêts déjà résolus semés dans la fenêtre (causes variées) --
            total_bonnes = 0
            total_rejets = 0
            for i, (cause, duree_min) in enumerate(ARRETS_RESOLUS.get(code, [])):
                # Réparti dans la fenêtre, terminé avant "maintenant".
                fin = maintenant - timedelta(minutes=45 * (i + 1))
                debut = fin - timedelta(minutes=duree_min)
                if debut < depuis:
                    debut = depuis
                db.add(
                    DowntimeEvent(
                        machine_id=machine.id,
                        ordre_fabrication_id=of.id if of else None,
                        cause=cause,
                        operator_comment=None,
                        start_time=debut,
                        end_time=fin,
                    )
                )

            # -- Panne toujours en cours (downtime ouvert) --
            # La production s'arrête à l'instant de la panne : au-delà, la
            # machine ne produit plus (fin_production borne la boucle ci-dessous).
            panne_debut = maintenant - timedelta(hours=6)
            fin_production = maintenant
            if profil["panne_ouverte"]:
                fin_production = panne_debut
                db.add(
                    DowntimeEvent(
                        machine_id=machine.id,
                        ordre_fabrication_id=None,
                        cause=CauseArret.PANNE_MECANIQUE,
                        operator_comment="Panne convoyeur — pièce détachée en commande.",
                        start_time=panne_debut,
                        end_time=None,
                    )
                )

            # -- Production minute par minute (BONNE + REBUT) sur la fenêtre --
            # Les machines en marche produisent jusqu'à maintenant ; la machine
            # en panne a produit normalement jusqu'à l'instant de sa panne.
            produit = profil["marche"] or profil["panne_ouverte"]
            if produit and profil["trs"] > 0:
                debit = _rythme_bonnes_par_min(
                    cycle_s=cycle, trs=profil["trs"], tq=profil["tq"], do=profil["do"]
                )
                # Crédit fractionnaire pour un débit régulier mais entier.
                credit = 0.0
                t = depuis
                while t < fin_production:
                    # Léger bruit ±12 % pour un rendu naturel, pas robotique.
                    credit += debit * random.uniform(0.88, 1.12)
                    n = int(credit)
                    credit -= n
                    if n > 0:
                        rejets = sum(1 for _ in range(n) if random.random() > profil["tq"])
                        bonnes = n - rejets
                        if bonnes > 0:
                            db.add(
                                QualityEvent(
                                    machine_id=machine.id,
                                    ordre_fabrication_id=of.id if of else None,
                                    type=TypeEvenementQualite.BONNE,
                                    quantite=bonnes,
                                    created_at=t,
                                )
                            )
                            total_bonnes += bonnes
                        if rejets > 0:
                            db.add(
                                QualityEvent(
                                    machine_id=machine.id,
                                    ordre_fabrication_id=of.id if of else None,
                                    type=TypeEvenementQualite.REBUT,
                                    quantite=rejets,
                                    cause=random.choice(
                                        [
                                            CauseRebut.DEFAUT_VISUEL,
                                            CauseRebut.DEFAUT_DIMENSIONNEL,
                                            CauseRebut.MAUVAIS_REGLAGE,
                                        ]
                                    ).value,
                                    created_at=t,
                                )
                            )
                            total_rejets += rejets
                    t += PAS

                # Compteurs machine cohérents avec les événements générés.
                machine.quantite_bonne = total_bonnes
                machine.quantite_rejetee = total_rejets
                machine.quantite_produite = total_bonnes + total_rejets

                # Journal d'activité récente : seulement pour les machines qui
                # tournent maintenant (le dashboard lit `activite_recente` depuis
                # MachineEvent). Une machine en panne n'émet pas de pièces là.
                if profil["marche"]:
                    for k in range(6):
                        ts = maintenant - timedelta(minutes=k * 2)
                        db.add(
                            MachineEvent(
                                machine_id=machine.id,
                                ordre_fabrication_id=of.id if of else None,
                                type=TypeEvenementMachine.GOOD_UNIT_PRODUCED,
                                payload={"quantite": max(1, int(debit))},
                                created_at=ts,
                            )
                        )
                    # Un tag capteur récent (température) pour la timeline.
                    db.add(
                        MachineEvent(
                            machine_id=machine.id,
                            ordre_fabrication_id=of.id if of else None,
                            type=TypeEvenementMachine.SENSOR_TAG_UPDATED,
                            payload={"tag": "temperature_C", "valeur": round(random.uniform(58, 74), 1)},
                            created_at=maintenant - timedelta(seconds=30),
                        )
                    )

            print(
                f"{code} {machine.nom}: statut={machine.statut.value} "
                f"OF={machine.ordre_fabrication_id} bonnes={total_bonnes} rejets={total_rejets}"
            )

        db.commit()
        print(
            "\nFenêtre temps réel régénérée (8 h se terminant maintenant). "
            "Les KPI du dashboard reflètent désormais une production vivante. "
            "AUTO_SIM_AUTOSTART=true prendra le relais pour la maintenir en direct."
        )
    finally:
        db.close()


if __name__ == "__main__":
    refresh()
