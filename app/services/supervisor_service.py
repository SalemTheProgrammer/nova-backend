"""Superviseur autonome Nova : détection → diagnostic → proposition → exécution.

Boucle d'arrière-plan (voir `boucle_superviseur`) qui scanne l'atelier toutes les
quelques secondes. Quand un problème est détecté, une `AgentProposal` est créée
avec un diagnostic chiffré et une action structurée, puis poussée sur le
WebSocket. L'action n'est exécutée qu'après approbation de l'opérateur
(`executer_action`) — human-in-the-loop obligatoire, sauf délégation explicite
via le mode d'autonomie. Les actions machine passent par des commandes
Sparkplug confirmées par les automates (`machine_command_service`).

Règles de détection (déterministes, donc fiables en démo) :
  1. Arrêt machine > SEUIL avec OF actif  → basculer l'OF vers la meilleure ligne
     disponible, ou lancer une maintenance d'urgence si aucune alternative.
  2. Dérive qualité (taux de rebut élevé sur fenêtre courte) → mise en pause pour réglage.
  3. Stock MP sous le seuil d'alerte → alerte de réapprovisionnement (+ message
     fournisseur pré-rédigé, envoyé seulement si l'opérateur approuve).
  4. Risque de panne élevé (score `risk_service`) sur une machine qui tourne encore →
     maintenance préventive, avant que la panne ne survienne réellement.
  5. OF en cours dont la cadence actuelle ne tiendra pas la date de fin prévue →
     bascule vers la meilleure ligne alternative, ou alerte de retard sinon.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, FabricationError, NotFoundError
from app.core.logging import get_logger
from app.core.temps import date_usine
from app.db.session import session_scope
from app.models import (
    AgentProposal,
    Alert,
    DowntimeEvent,
    LotMatierePremiere,
    Machine,
    MatierePremiere,
    OrdreFabrication,
    QualityEvent,
)
from app.models.enums import (
    SeveriteAlerte,
    StatutMachine,
    StatutOF,
    StatutProposition,
    TypeEvenementQualite,
    TypeMaintenance,
)
from app.services import (
    broadcast_service,
    cost_service,
    line_scoring_service,
    machine_command_service,
    notify_service,
    production_control_service,
    risk_service,
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

# --------------------------------------------------------------------------- #
# Autonomie du superviseur : trois modes, deux niveaux de risque.
#
# - "manuel"     : comportement historique — toute proposition attend l'opérateur.
# - "assiste"    : les propositions à risque FAIBLE (aucun impact sur une
#   production en cours : un simple constat, ou une machine déjà libre)
#   s'exécutent seules, immédiatement.
# - "autopilote" : idem "assiste", PLUS les propositions à risque MOYEN (elles
#   déplacent un OF, arrêtent une machine en marche, ou contactent un tiers)
#   s'exécutent seules après `autopilote_delai_moyen_s` secondes — le temps
#   pour l'opérateur de les rejeter — sauf rejet explicite avant l'échéance.
#
# Le mode est mutable en RUNTIME (voir `definir_mode_autonomie`, exposé par
# POST /api/v1/agent/autonomie) : la démo doit pouvoir tourner le bouton sans
# redémarrer le backend.
# --------------------------------------------------------------------------- #

RISQUE_FAIBLE = "faible"
RISQUE_MOYEN = "moyen"
RISQUE_PAR_TYPE: dict[str, str] = {
    "alerte_retard": RISQUE_FAIBLE,  # crée juste une alerte, ne touche à rien
    "maintenance_preventive": RISQUE_FAIBLE,  # jamais sur une machine avec OF actif
    "stock_bas": RISQUE_MOYEN,  # peut contacter un fournisseur
    "derive_qualite": RISQUE_MOYEN,  # met en pause une machine en marche
    "basculer_of": RISQUE_MOYEN,  # déplace un OF, démarre une autre machine
    "maintenance_urgence": RISQUE_MOYEN,  # arrête une machine déjà bloquée
}

MODES_AUTONOMIE = ("manuel", "assiste", "autopilote")
# None = pas encore initialisé depuis la config (voir `mode_autonomie`).
_mode_autonomie: str | None = None


def mode_autonomie() -> str:
    """Mode d'autonomie courant. Initialisé depuis `Settings.autonomy_mode_defaut`
    au premier appel, puis mutable en mémoire process via `definir_mode_autonomie`."""
    global _mode_autonomie
    if _mode_autonomie is None:
        from app.core.config import get_settings

        _mode_autonomie = get_settings().autonomy_mode_defaut
    return _mode_autonomie


def definir_mode_autonomie(mode: str) -> str:
    global _mode_autonomie
    if mode not in MODES_AUTONOMIE:
        raise AppError(
            f"Mode d'autonomie invalide : {mode!r}. Choix : {', '.join(MODES_AUTONOMIE)}."
        )
    _mode_autonomie = mode
    logger.info("autonomie_mode_change", mode=mode)
    return _mode_autonomie


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
        "decideur": p.decideur,
        "execution_auto_at": p.execution_auto_at.isoformat() if p.execution_auto_at else None,
        "risque": RISQUE_PAR_TYPE.get(p.type, RISQUE_MOYEN),
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
    _appliquer_autonomie(db, proposition)
    return proposition


def _execution_immediate(proposition: AgentProposal) -> bool:
    """Risque FAIBLE en mode assisté/autopilote : exécutée dès sa création."""
    return (
        mode_autonomie() != "manuel"
        and proposition.statut == StatutProposition.PROPOSEE
        and RISQUE_PAR_TYPE.get(proposition.type, RISQUE_MOYEN) == RISQUE_FAIBLE
    )


def _appliquer_autonomie(db: Session, proposition: AgentProposal) -> None:
    """Programme le compte à rebours d'une proposition MOYEN en autopilote.

    Les propositions à risque FAIBLE ne sont PAS exécutées ici mais par
    `_tick_superviseur`, après le commit de l'analyse : une commande machine
    attend l'accusé de l'automate, et l'ingestion doit pouvoir écrire pendant
    ce temps (SQLite n'a qu'un écrivain).
    """
    if mode_autonomie() != "autopilote":
        return
    if RISQUE_PAR_TYPE.get(proposition.type, RISQUE_MOYEN) != RISQUE_MOYEN:
        return
    from app.core.config import get_settings

    delai = get_settings().autopilote_delai_moyen_s
    proposition.execution_auto_at = datetime.utcnow() + timedelta(seconds=delai)
    db.flush()
    broadcast_service.diffuser(
        {"type": "agent_proposal_update", "proposal": serialiser_proposition(proposition)}
    )


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

        # Chiffrage en dinars : ce que l'arrêt a déjà coûté et ce que chaque
        # minute supplémentaire coûte — c'est l'argument qui fait décider.
        cout = cost_service.estimer_cout_arret(machine, duree_s / 60, of=of)
        cout_txt = (
            f" Perte estimée depuis le début de l'arrêt : "
            f"{cost_service.format_tnd(cout.total_tnd)} "
            f"(≈ {cost_service.format_tnd(cout.par_minute_tnd)}/min supplémentaire)."
            if cout.total_tnd > 0
            else ""
        )

        alternative = line_scoring_service.meilleure_ligne_disponible(
            db, exclure_ligne_id=machine.ligne_production_id
        )
        if alternative is not None:
            diagnostic = (
                f"{machine.code} est arrêtée depuis {duree_txt} ({cause_txt}) et bloque "
                f"l'OF {of.numero} ({of.quantite_planifiee} {of.unite.value} de "
                f"{of.article.code}).{cout_txt} La ligne {alternative.code} est la "
                f"meilleure alternative : score {alternative.score * 100:.0f}/100 "
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
                f"l'OF {of.numero}.{cout_txt} Aucune ligne alternative n'a de machine "
                f"libre : je recommande une maintenance d'urgence pour remettre "
                f"{machine.code} en service au plus vite."
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

        # Fournisseur "connu" = celui du dernier lot reçu pour cette MP (pas de
        # relation directe MP → fournisseur, seulement au niveau du lot).
        dernier_lot = db.execute(
            select(LotMatierePremiere)
            .where(
                LotMatierePremiere.matiere_premiere_id == mp.id,
                LotMatierePremiere.fournisseur_id.is_not(None),
            )
            .order_by(LotMatierePremiere.date_creation.desc())
        ).scalars().first()
        fournisseur = dernier_lot.fournisseur if dernier_lot is not None else None

        action: dict = {"type": "alerte_reappro", "matiere_premiere_id": mp.id}
        action_libelle = "Créer l'alerte de réapprovisionnement"
        if fournisseur is not None and fournisseur.contact:
            message_fournisseur = (
                f"Bonjour {fournisseur.nom}, notre stock de {mp.designation} ({mp.code}) "
                f"est descendu à {dispo} {mp.unite.value}, sous notre seuil d'alerte. "
                "Pouvez-vous confirmer un réapprovisionnement dans les meilleurs délais ?"
            )
            diagnostic = (
                f"Le stock de {mp.code} ({mp.designation}) est descendu à {dispo} "
                f"{mp.unite.value}, sous le seuil d'alerte de {mp.seuil_alerte} "
                f"{mp.unite.value}. Je recommande de créer l'alerte de réapprovisionnement "
                f"et d'envoyer ce message à {fournisseur.nom} ({fournisseur.contact}), "
                f"seulement si vous approuvez : « {message_fournisseur} »"
            )
            action["fournisseur_nom"] = fournisseur.nom
            action["fournisseur_contact"] = fournisseur.contact
            action["message_fournisseur"] = message_fournisseur
            action_libelle += f" et prévenir {fournisseur.nom}"
        else:
            diagnostic = (
                f"Le stock de {mp.code} ({mp.designation}) est descendu à {dispo} "
                f"{mp.unite.value}, sous le seuil d'alerte de {mp.seuil_alerte} "
                f"{mp.unite.value}. Aucun fournisseur connu pour cette MP (pas de lot "
                "reçu avec fournisseur renseigné) : je recommande de créer l'alerte de "
                "réapprovisionnement."
            )

        nouvelles.append(
            _proposer(
                db,
                cle=cle,
                type_="stock_bas",
                severite=SeveriteAlerte.WARNING,
                titre=f"Stock bas : {mp.code} ({dispo} {mp.unite.value})",
                diagnostic=diagnostic,
                action_libelle=action_libelle,
                action=action,
            )
        )


def _regle_risque_panne_eleve(db: Session, nouvelles: list[AgentProposal]) -> None:
    risques = risk_service.analyser_risques(db)
    if not risques:
        return

    # Une machine déjà à l'arrêt est couverte par `_regle_arret_bloquant` (bascule
    # d'OF ou maintenance d'urgence) — pas besoin d'une proposition redondante ici.
    machines_en_arret = {
        d.machine_id
        for d in db.execute(select(DowntimeEvent).where(DowntimeEvent.end_time.is_(None))).scalars()
    }

    for r in risques:
        if r.niveau != "eleve":
            continue
        machine = db.get(Machine, r.machine_id)
        if machine is None or machine.statut == StatutMachine.MAINTENANCE:
            continue
        if machine.id in machines_en_arret:
            continue
        # Jamais sur une machine qui porte un OF : une maintenance préventive ne
        # doit pas interrompre une production en cours (c'est ce comportement qui
        # avait fait désactiver la règle). On ne propose que pour les machines
        # libres, où la maintenance est sans impact sur le plan.
        if machine.ordre_fabrication_id is not None:
            continue
        cle = f"risque_panne:{machine.id}:{datetime.utcnow():%Y%m%d}"
        if _deja_traitee(db, cle):
            continue

        maint_txt = (
            f"dernière maintenance il y a {r.jours_depuis_maintenance} j"
            if r.jours_depuis_maintenance is not None
            else "jamais maintenue"
        )
        diagnostic = (
            f"{machine.code} affiche un risque de panne élevé ({r.score * 100:.0f}/100) : "
            f"{r.nb_pannes_7j} panne(s) sur 7 jours, {maint_txt}. Je recommande de "
            "planifier une maintenance préventive avant qu'une panne réelle ne bloque "
            "un OF en cours."
        )
        nouvelles.append(
            _proposer(
                db,
                cle=cle,
                type_="maintenance_preventive",
                severite=SeveriteAlerte.WARNING,
                titre=f"Risque de panne élevé sur {machine.code} ({r.score * 100:.0f}/100)",
                diagnostic=diagnostic,
                action_libelle=f"Planifier une maintenance préventive sur {machine.code}",
                action={"type": "maintenance_preventive", "machine_id": machine.id},
                machine_id=machine.id,
            )
        )


def _regle_retard_of(db: Session, nouvelles: list[AgentProposal]) -> None:
    ofs = db.execute(
        select(OrdreFabrication).where(
            OrdreFabrication.statut == StatutOF.EN_COURS,
            OrdreFabrication.date_echeance.is_not(None),
        )
    ).scalars().all()

    for of in ofs:
        # Projection fiable seulement si une machine tourne dessus MAINTENANT
        # (même logique que `simuler_scenario_panne` : cycle actuel/cible).
        machine = db.execute(
            select(Machine).where(Machine.ordre_fabrication_id == of.id)
        ).scalars().first()
        if machine is None or machine.statut != StatutMachine.MARCHE:
            continue
        cycle = machine.temps_cycle_actuel_s or machine.temps_cycle_cible_s
        if not cycle or cycle <= 0:
            continue

        restant = max(
            0.0,
            float(of.quantite_planifiee) - float(of.quantite_bonne) - float(of.quantite_rejetee),
        )
        if restant <= 0:
            continue

        heures_restantes = restant * float(cycle) / 3600
        fin_estimee = datetime.utcnow() + timedelta(hours=heures_restantes)
        # Comparaison en date USINE (UTC+1) : l'échéance client est une date
        # locale, la projection UTC peut être un jour en retard autour de minuit.
        if date_usine(fin_estimee) <= of.date_echeance:
            continue  # au rythme actuel, l'échéance reste tenable

        cle = f"retard_of:{of.id}:{datetime.utcnow():%Y%m%d}"
        if _deja_traitee(db, cle):
            continue

        retard_j = (date_usine(fin_estimee) - of.date_echeance).days
        alternative = line_scoring_service.meilleure_ligne_disponible(
            db, exclure_ligne_id=machine.ligne_production_id
        )
        if alternative is not None:
            diagnostic = (
                f"Au rythme actuel, l'OF {of.numero} ({machine.code}) finirait le "
                f"{date_usine(fin_estimee):%Y-%m-%d}, soit {retard_j} j après l'échéance prévue "
                f"({of.date_echeance.isoformat()}). La ligne {alternative.code} est la "
                f"meilleure alternative : score {alternative.score * 100:.0f}/100 "
                f"({alternative.raison})."
            )
            nouvelles.append(
                _proposer(
                    db,
                    cle=cle,
                    type_="basculer_of",
                    severite=SeveriteAlerte.WARNING,
                    titre=f"OF {of.numero} en retard prévisionnel ({retard_j} j)",
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
                f"Au rythme actuel, l'OF {of.numero} ({machine.code}) finirait le "
                f"{date_usine(fin_estimee):%Y-%m-%d}, soit {retard_j} j après l'échéance prévue "
                f"({of.date_echeance.isoformat()}). Aucune ligne alternative n'a de "
                "machine libre : je recommande de signaler le retard dès maintenant."
            )
            nouvelles.append(
                _proposer(
                    db,
                    cle=cle,
                    type_="alerte_retard",
                    severite=SeveriteAlerte.WARNING,
                    titre=f"OF {of.numero} en retard prévisionnel ({retard_j} j)",
                    diagnostic=diagnostic,
                    action_libelle="Créer l'alerte de retard",
                    action={"type": "alerte_retard", "of_id": of.id},
                    machine_id=machine.id,
                    ordre_fabrication_id=of.id,
                )
            )


def analyser(db: Session) -> list[AgentProposal]:
    """Exécute toutes les règles et renvoie les nouvelles propositions créées."""
    nouvelles: list[AgentProposal] = []
    _regle_arret_bloquant(db, nouvelles)
    _regle_derive_qualite(db, nouvelles)
    _regle_stock_bas(db, nouvelles)
    # Maintenance prédictive : ne propose que pour les machines à risque élevé
    # SANS OF actif (voir le garde dans la règle) — une proposition ne peut donc
    # jamais bloquer une production en cours.
    _regle_risque_panne_eleve(db, nouvelles)
    _regle_retard_of(db, nouvelles)
    return nouvelles


# --------------------------------------------------------------------------- #
# Exécution des actions approuvées
# --------------------------------------------------------------------------- #


def executer_action(action: dict) -> str:
    """Exécute l'action structurée d'une proposition approuvée. Renvoie le résumé.

    Ouvre ses propres sessions : les actions machine attendent l'accusé des
    automates et ne doivent tenir aucune transaction d'écriture pendant ce temps.
    """
    type_ = action.get("type")

    if type_ == "basculer_of":
        bascule = production_control_service.basculer_of(action["of_id"], action["ligne_id"])
        source = f" ({bascule.machine_source} libérée)" if bascule.machine_source else ""
        return (
            f"OF {bascule.of_numero} basculé vers {bascule.ligne_code} : "
            f"machine {bascule.machine_cible} démarrée{source}."
        )

    if type_ == "maintenance_urgence":
        return machine_command_service.demarrer_maintenance(
            action["machine_id"],
            type_maintenance=TypeMaintenance.URGENCE.value,
            description="Maintenance d'urgence déclenchée par le superviseur Nova",
        )

    if type_ == "pause_reglage":
        resume = machine_command_service.mettre_en_pause(action["machine_id"])
        return f"{resume} Réglage qualité à effectuer avant la reprise."

    if type_ == "maintenance_preventive":
        return machine_command_service.demarrer_maintenance(
            action["machine_id"],
            type_maintenance=TypeMaintenance.PREVENTIVE.value,
            description="Maintenance préventive déclenchée par le superviseur Nova (risque de panne élevé)",
        )

    if type_ == "alerte_reappro":
        with session_scope() as db:
            mp = db.get(MatierePremiere, action["matiere_premiere_id"])
            if mp is None:
                raise AppError("Matière première introuvable.")
            dispo = manufacturing_svc.stock_disponible_mp(db, mp.id)
            db.add(
                Alert(
                    severity=SeveriteAlerte.WARNING,
                    type="REAPPROVISIONNEMENT",
                    message=(
                        f"Réapprovisionner {mp.code} ({mp.designation}) : stock {dispo} "
                        f"{mp.unite.value}, seuil {mp.seuil_alerte} {mp.unite.value}."
                    ),
                )
            )
        resultat = f"Alerte de réapprovisionnement créée pour {mp.code}."

        contact = action.get("fournisseur_contact")
        message = action.get("message_fournisseur")
        if contact and message:
            canal = "email" if notify_service.EMAIL_RE.fullmatch(contact.strip()) else "whatsapp"
            try:
                if canal == "email":
                    envoi = notify_service.envoyer_email(
                        contact, f"Réapprovisionnement {mp.code}", message
                    )
                else:
                    envoi = notify_service.envoyer_whatsapp(contact, message)
                resultat += f" {envoi}"
            except AppError as exc:
                resultat += f" Message fournisseur non envoyé : {exc.message}"
        return resultat

    if type_ == "alerte_retard":
        with session_scope() as db:
            of = db.get(OrdreFabrication, action["of_id"])
            if of is None:
                raise AppError("OF introuvable.")
            db.add(
                Alert(
                    severity=SeveriteAlerte.WARNING,
                    type="RETARD_OF",
                    message=(
                        f"OF {of.numero} en retard prévisionnel par rapport à l'échéance du "
                        f"{of.date_echeance.isoformat() if of.date_echeance else '—'}."
                    ),
                )
            )
            numero = of.numero
        return f"Alerte de retard créée pour l'OF {numero}."

    raise AppError(f"Action inconnue : {type_!r}")


def decider(
    db: Session,
    proposition_id: int,
    *,
    approuver: bool,
    canal: str = "web",
    identite: str | None = None,
    decideur: str = "operateur",
) -> AgentProposal:
    """Approuve (et exécute) ou rejette une proposition. Diffuse la mise à jour.

    `canal`/`identite` attribuent la décision dans le journal d'audit :
    "web"/console pour les cartes de l'interface, "whatsapp"/+216… pour une
    réponse « oui » depuis le téléphone (voir `proactive_service`).
    `decideur` distingue une décision humaine ("operateur", défaut) d'une
    exécution automatique du superviseur ("autopilote" — voir
    `_appliquer_autonomie` / `_executer_autopilote_echus`) : c'est ce que
    l'interface affiche ("🤖 Nova a agi seule").
    """
    proposition = db.get(AgentProposal, proposition_id)
    if proposition is None:
        raise NotFoundError("Proposition introuvable.")
    if proposition.statut != StatutProposition.PROPOSEE:
        raise FabricationError("Cette proposition a déjà été traitée.")

    proposition.decided_at = datetime.utcnow()
    proposition.decideur = decideur
    if not approuver:
        proposition.statut = StatutProposition.REJETEE
        proposition.resultat = (
            "Rejetée par l'opérateur." if decideur == "operateur" else "Rejetée automatiquement."
        )
    else:
        # Réservation atomique AVANT d'agir : une proposition approuvée depuis le
        # web et depuis WhatsApp au même instant n'est exécutée qu'une fois. Le
        # commit libère aussi la transaction pendant que les commandes machine
        # attendent l'accusé des automates.
        reservation = db.execute(
            update(AgentProposal)
            .where(
                AgentProposal.id == proposition.id,
                AgentProposal.statut == StatutProposition.PROPOSEE,
            )
            .values(statut=StatutProposition.APPROUVEE)
        )
        if reservation.rowcount != 1:
            db.rollback()
            raise FabricationError("Cette proposition a déjà été traitée.")
        db.commit()
        db.refresh(proposition)
        try:
            resume = executer_action(proposition.action or {})
        except AppError as exc:
            proposition.statut = StatutProposition.ECHOUEE
            proposition.resultat = f"Échec : {exc.message}"
        else:
            proposition.statut = StatutProposition.EXECUTEE
            proposition.resultat = resume
    db.commit()
    db.refresh(proposition)

    from app.services import audit_service

    audit_service.enregistrer_action(
        action=f"proposition_{'approuvee' if approuver else 'rejetee'}",
        arguments={"proposition_id": proposition.id, "type": proposition.type,
                   "action": proposition.action, "titre": proposition.titre},
        source="autopilote" if decideur == "autopilote" else "superviseur",
        canal=canal,
        identite=identite or ("console-web" if canal == "web" else None),
        resultat=proposition.resultat,
    )
    broadcast_service.diffuser(
        {"type": "agent_proposal_update", "proposal": serialiser_proposition(proposition)}
    )
    return proposition


# --------------------------------------------------------------------------- #
# Boucle d'arrière-plan
# --------------------------------------------------------------------------- #


def _decider_autopilote(proposition_id: int) -> bool:
    """Approbation automatique dans une session dédiée. True si décidée."""
    try:
        with session_scope() as db:
            decider(
                db, proposition_id, approuver=True, canal="systeme",
                identite="autopilote", decideur="autopilote",
            )
    except AppError:
        logger.exception("autopilote_execution_echouee", proposition_id=proposition_id)
        return False
    return True


def _executer_autopilote_echus() -> list[int]:
    """Exécute les propositions dont le compte à rebours autopilote (risque
    MOYEN) est écoulé et qui sont ENCORE en attente — l'opérateur n'a rejeté ni
    approuvé entre-temps, sinon leur statut ne serait plus PROPOSEE."""
    with session_scope() as db:
        echues = db.execute(
            select(AgentProposal.id).where(
                AgentProposal.statut == StatutProposition.PROPOSEE,
                AgentProposal.execution_auto_at.is_not(None),
                AgentProposal.execution_auto_at <= datetime.utcnow(),
            )
        ).scalars().all()
    return [pid for pid in echues if _decider_autopilote(pid)]


def _tick_superviseur() -> tuple[list[dict], list[dict]]:
    """Un passage de détection (exécuté dans un thread — sessions propres).

    1. Analyse et création des propositions, committée.
    2. Exécution immédiate des propositions à risque FAIBLE (modes assisté /
       autopilote), hors de la transaction d'analyse.
    3. Exécution des propositions MOYEN dont le compte à rebours autopilote
       vient de s'écouler (créées lors d'un tick précédent).

    Renvoie (nouvelles, échues), sérialisées dans leur état final.
    """
    with session_scope() as db:
        nouvelles = analyser(db)
        ids_nouvelles = [p.id for p in nouvelles]
        a_executer = [p.id for p in nouvelles if _execution_immediate(p)]
    for proposition_id in a_executer:
        _decider_autopilote(proposition_id)
    ids_echues = _executer_autopilote_echus()
    with session_scope() as db:
        return (
            [serialiser_proposition(db.get(AgentProposal, pid)) for pid in ids_nouvelles],
            [serialiser_proposition(db.get(AgentProposal, pid)) for pid in ids_echues],
        )


async def boucle_superviseur(intervalle_s: float = INTERVALLE_BOUCLE_S) -> None:
    """Boucle infinie : détection périodique + diffusion des nouvelles propositions."""
    logger.info("superviseur_demarre", intervalle_s=intervalle_s)
    from app.services.websocket_manager import manager

    from app.services import proactive_service

    while True:
        try:
            nouvelles, echues = await asyncio.to_thread(_tick_superviseur)
            for proposition in nouvelles:
                await manager.broadcast({"type": "agent_proposal", "proposal": proposition})
                if proposition["statut"] == "PROPOSEE":
                    # Nova proactive : la proposition part aussi sur WhatsApp
                    # (SUPERVISOR_NOTIFY_NUMBERS) — l'opérateur répond oui/non.
                    await asyncio.to_thread(proactive_service.notifier_proposition, proposition)
                else:
                    # Risque FAIBLE en mode assisté/autopilote : exécutée seule
                    # dès sa création — notification informative, pas de
                    # demande de décision.
                    await asyncio.to_thread(
                        proactive_service.notifier_execution_autonome, proposition
                    )
            for proposition in echues:
                # `decider` (appelé par `_executer_autopilote_echus`) a déjà
                # diffusé la mise à jour WebSocket : reste la notification
                # WhatsApp « Nova a agi seule ».
                await asyncio.to_thread(proactive_service.notifier_execution_autonome, proposition)
        except asyncio.CancelledError:
            logger.info("superviseur_arrete")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("superviseur_tick_failed")
        await asyncio.sleep(intervalle_s)
