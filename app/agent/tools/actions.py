"""Outils agent d'ACTION sur l'atelier (commandes SCADA) + aide à la décision.

Même contrat que les autres modules d'outils : chaque outil ouvre sa session,
renvoie un texte français lisible et, quand c'est pertinent, un artifact
structuré pour l'interface. Les outils qui MODIFIENT l'atelier exigent
`confirmation=true` (jamais d'action sans accord explicite de l'opérateur —
c'est la règle BPF du système). Chaque action diffuse l'état machine sur le
WebSocket pour que le tableau de bord réagisse immédiatement.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import select

from app.agent.tools import confirmation_gate
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import Alert, Machine, OrdreFabrication
from app.models.enums import StatutMachine, TypeMaintenance
from app.services import (
    broadcast_service,
    line_scoring_service,
    report_service,
    risk_service,
    simulator_service,
)

logger = get_logger(__name__)

CONFIRMATION_REQUISE = (
    "Confirmation requise : décrivez l'action à l'opérateur et demandez son accord "
    "explicite avant de rappeler cet outil avec confirmation=true."
)


def _demande_confirmation(libelle: str) -> tuple[str, dict]:
    """Réponse + artifact quand le garde-fou refuse l'exécution (voir
    `confirmation_gate`) : le frontend affiche une carte d'attente distincte
    d'une simple étape de lecture, jamais une coche verte silencieuse."""
    return (
        f"Confirmation requise : {libelle}. " + CONFIRMATION_REQUISE,
        {"kind": "confirmation_attente", "libelle": libelle},
    )


def _action_executee(libelle: str) -> dict:
    return {"kind": "action_executee", "libelle": libelle}


def _trouver_machine(db, code_ou_id: str) -> Machine | None:
    machine: Machine | None = None
    if code_ou_id.isdigit():
        machine = db.get(Machine, int(code_ou_id))
    if machine is None:
        machine = db.execute(
            select(Machine).where(Machine.code == code_ou_id)
        ).scalars().first()
    return machine


def _trouver_of(db, numero_ou_id: str) -> OrdreFabrication | None:
    of: OrdreFabrication | None = None
    if numero_ou_id.isdigit():
        of = db.get(OrdreFabrication, int(numero_ou_id))
    if of is None:
        of = db.execute(
            select(OrdreFabrication).where(OrdreFabrication.numero == numero_ou_id)
        ).scalars().first()
    return of


# --------------------------------------------------------------------------- #
# Aide à la décision (lecture seule)
# --------------------------------------------------------------------------- #


@tool(response_format="content_and_artifact")
def choisir_meilleure_ligne(exclure_ligne_id: int | None = None) -> tuple[str, dict | None]:
    """Classe les lignes de production pour affecter un OF : score composite basé sur
    le TRS courant (45 %), les machines libres (35 %) et la charge (20 %).

    À utiliser quand l'opérateur demande « quelle ligne choisir ? » ou avant de créer
    un OF pour recommander la meilleure ligne, avec justification chiffrée.
    `exclure_ligne_id` permet d'écarter une ligne (ex. celle qui est en panne).
    """
    with session_scope() as db:
        scores = line_scoring_service.scorer_lignes(db, exclure_ligne_id=exclure_ligne_id)
        if not scores:
            return "Aucune ligne de production active avec des machines.", None
        lignes_txt = [
            f"{i}. {s.code} ({s.designation}) — score {s.score * 100:.0f}/100 : {s.raison}"
            for i, s in enumerate(scores, start=1)
        ]
        meilleure = scores[0]
        conclusion = (
            f"\nRecommandation : la ligne {meilleure.code} (id={meilleure.ligne_id}), "
            f"score {meilleure.score * 100:.0f}/100."
        )
        artifact = {
            "kind": "lignes_score",
            "lignes": [
                {
                    "ligne_id": s.ligne_id,
                    "code": s.code,
                    "designation": s.designation,
                    "score": float(s.score),
                    "trs": float(s.trs) if s.trs is not None else None,
                    "machines_total": s.machines_total,
                    "machines_libres": s.machines_libres,
                    "machines_en_panne": s.machines_en_panne,
                    "raison": s.raison,
                }
                for s in scores
            ],
        }
        return "Classement des lignes :\n" + "\n".join(lignes_txt) + conclusion, artifact


@tool(response_format="content_and_artifact")
def generer_rapport_production() -> tuple[str, dict | None]:
    """Génère le rapport de production de l'équipe (TRS, production, rebuts, pannes,
    MTTR/MTBF, top causes d'arrêt, alertes) à partir des données réelles des 8 dernières
    heures. À utiliser pour « fais-moi le rapport », « bilan de l'équipe », etc."""
    with session_scope() as db:
        markdown, artifact = report_service.generer_rapport(db)
        return markdown, artifact


@tool(response_format="content_and_artifact")
def risque_panne_machines() -> tuple[str, dict | None]:
    """Analyse prédictive simple : score de risque de panne (0-100) par machine, basé sur
    la fréquence des pannes sur 7 jours, le temps d'arrêt cumulé et l'ancienneté de la
    dernière maintenance. À utiliser pour « quelles machines risquent de tomber en panne ? »
    ou pour prioriser la maintenance préventive."""
    with session_scope() as db:
        risques = risk_service.analyser_risques(db)
        if not risques:
            return "Aucune machine active à analyser.", None
        lignes = [
            f"- {r.code} ({r.nom}) : risque {r.score * 100:.0f}/100 [{r.niveau}] — "
            f"{r.nb_pannes_7j} panne(s) sur 7 j, "
            + (
                f"dernière maintenance il y a {r.jours_depuis_maintenance} j. "
                if r.jours_depuis_maintenance is not None
                else "jamais maintenue. "
            )
            + r.recommandation
            for r in risques
        ]
        artifact = {
            "kind": "risque",
            "machines": [
                {
                    "machine_id": r.machine_id,
                    "code": r.code,
                    "nom": r.nom,
                    "score": r.score,
                    "niveau": r.niveau,
                    "nb_pannes_7j": r.nb_pannes_7j,
                    "jours_depuis_maintenance": r.jours_depuis_maintenance,
                    "recommandation": r.recommandation,
                }
                for r in risques
            ],
        }
        return "Risque de panne par machine :\n" + "\n".join(lignes), artifact


@tool(response_format="content_and_artifact")
def simuler_scenario_panne(
    machine_code_ou_id: str, duree_minutes: int
) -> tuple[str, dict | None]:
    """Analyse HYPOTHÉTIQUE (« et si ? ») : évalue l'impact d'une panne de
    `duree_minutes` minutes sur une machine, SANS RIEN MODIFIER dans l'atelier.

    Calcule : pièces non produites pendant l'arrêt, retard estimé sur l'OF en
    cours par rapport à sa date de fin prévue, et la meilleure ligne de repli
    si un re-routage devenait nécessaire. À utiliser pour « et si M-01 tombe en
    panne 2 heures ? », « quel serait l'impact d'un arrêt de 30 min ? », etc.
    Lecture seule : aucune confirmation nécessaire.
    """
    if duree_minutes <= 0:
        return "La durée simulée doit être positive (en minutes).", None
    with session_scope() as db:
        machine = _trouver_machine(db, machine_code_ou_id)
        if machine is None:
            return f"Machine introuvable : {machine_code_ou_id}", None

        cycle = machine.temps_cycle_actuel_s or machine.temps_cycle_cible_s
        lignes_txt: list[str] = [
            f"Scénario simulé : panne de {duree_minutes} min sur {machine.code} ({machine.nom}). "
            "Aucune modification réelle n'a été faite."
        ]

        pieces_perdues: int | None = None
        if cycle and cycle > 0:
            pieces_perdues = int(duree_minutes * 60 / float(cycle))
            lignes_txt.append(
                f"- Production perdue : ≈ {pieces_perdues} pièce(s) "
                f"(cycle {float(cycle):g} s/unité)."
            )
        else:
            lignes_txt.append("- Production perdue : inconnue (pas de temps de cycle).")

        of = machine.ordre_fabrication
        retard_txt: str | None = None
        of_info: dict | None = None
        if of is not None:
            restant = max(
                0.0, float(of.quantite_planifiee) - float(of.quantite_bonne)
            )
            of_info = {
                "numero": of.numero,
                "restant": restant,
                "date_fin_prevue": of.date_fin_prevue.isoformat() if of.date_fin_prevue else None,
            }
            if cycle and cycle > 0:
                heures_restantes = restant * float(cycle) / 3600
                nouvelle_fin = datetime.utcnow() + timedelta(
                    minutes=duree_minutes, hours=heures_restantes
                )
                lignes_txt.append(
                    f"- OF en cours {of.numero} : {restant:g} unité(s) restantes, "
                    f"fin estimée décalée au {nouvelle_fin:%Y-%m-%d %H:%M} UTC."
                )
                if of.date_fin_prevue is not None:
                    if nouvelle_fin.date() > of.date_fin_prevue:
                        retard_txt = (
                            f"⚠ La date de fin prévue ({of.date_fin_prevue.isoformat()}) "
                            "serait DÉPASSÉE."
                        )
                    else:
                        retard_txt = (
                            f"La date de fin prévue ({of.date_fin_prevue.isoformat()}) "
                            "resterait tenable."
                        )
                    lignes_txt.append(f"- {retard_txt}")
            else:
                lignes_txt.append(
                    f"- OF en cours {of.numero} : {restant:g} unité(s) restantes."
                )
        else:
            lignes_txt.append("- Aucun OF affecté à cette machine : impact limité à la capacité.")

        repli = line_scoring_service.meilleure_ligne_disponible(
            db, exclure_ligne_id=machine.ligne_production_id
        )
        if repli is not None:
            lignes_txt.append(
                f"- Repli possible : ligne {repli.code} ({repli.designation}), "
                f"score {repli.score * 100:.0f}/100, "
                f"{repli.machines_libres} machine(s) libre(s) — un re-routage absorberait l'arrêt."
            )
        else:
            lignes_txt.append(
                "- Aucun repli : aucune autre ligne n'a de machine libre actuellement."
            )

        artifact = {
            "kind": "whatif",
            "machine": machine.code,
            "duree_minutes": duree_minutes,
            "pieces_perdues": pieces_perdues,
            "of": of_info,
            "retard": retard_txt,
            "repli": (
                {
                    "ligne_id": repli.ligne_id,
                    "code": repli.code,
                    "designation": repli.designation,
                    "score": float(repli.score),
                    "machines_libres": repli.machines_libres,
                }
                if repli is not None
                else None
            ),
        }
        return "\n".join(lignes_txt), artifact


# --------------------------------------------------------------------------- #
# Commandes SCADA (écriture — confirmation obligatoire)
# --------------------------------------------------------------------------- #


@tool(response_format="content_and_artifact")
def demarrer_machine(
    code_ou_id: str,
    confirmation: bool,
    ordre_fabrication_id: int | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """Démarre une machine (commande SCADA), éventuellement en lui affectant un OF.

    ACTION SUR L'ATELIER : demandez toujours l'accord explicite de l'opérateur avant
    d'appeler avec confirmation=true.
    """
    libelle = f"démarrer la machine {code_ou_id}"
    if not confirmation_gate.evaluer(
        config, "demarrer_machine", {"code_ou_id": code_ou_id, "of": ordre_fabrication_id}, confirmation
    ):
        return _demande_confirmation(libelle)
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        try:
            simulator_service.demarrer(db, machine, ordre_fabrication_id=ordre_fabrication_id)
        except AppError as exc:
            return f"❌ {exc.message}", None
        db.flush()
        broadcast_service.diffuser_machine(db, machine)
        of_txt = f" avec l'OF id={ordre_fabrication_id}" if ordre_fabrication_id else ""
        return f"✅ Machine {machine.code} démarrée{of_txt}.", _action_executee(libelle)


@tool(response_format="content_and_artifact")
def arreter_machine(
    code_ou_id: str, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """Arrête une machine (commande SCADA). ACTION SUR L'ATELIER : accord explicite
    de l'opérateur requis avant confirmation=true."""
    libelle = f"arrêter la machine {code_ou_id}"
    if not confirmation_gate.evaluer(
        config, "arreter_machine", {"code_ou_id": code_ou_id}, confirmation
    ):
        return _demande_confirmation(libelle)
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        try:
            simulator_service.arreter(db, machine)
        except AppError as exc:
            return f"❌ {exc.message}", None
        db.flush()
        broadcast_service.diffuser_machine(db, machine)
        return f"✅ Machine {machine.code} arrêtée.", _action_executee(libelle)


@tool(response_format="content_and_artifact")
def resoudre_arret_machine(
    code_ou_id: str,
    confirmation: bool,
    commentaire: str | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """Clôture l'arrêt en cours d'une machine et la remet en service (commande SCADA).
    ACTION SUR L'ATELIER : accord explicite de l'opérateur requis avant confirmation=true."""
    libelle = f"résoudre l'arrêt de {code_ou_id}"
    if not confirmation_gate.evaluer(
        config,
        "resoudre_arret_machine",
        {"code_ou_id": code_ou_id, "commentaire": commentaire},
        confirmation,
    ):
        return _demande_confirmation(libelle)
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        try:
            simulator_service.resoudre_arret(db, machine, comment=commentaire)
        except AppError as exc:
            return f"❌ {exc.message}", None
        db.flush()
        broadcast_service.diffuser_machine(db, machine)
        return (
            f"✅ Arrêt résolu sur {machine.code} : la machine est de nouveau opérationnelle.",
            _action_executee(libelle),
        )


@tool(response_format="content_and_artifact")
def lancer_maintenance(
    code_ou_id: str,
    confirmation: bool,
    type_maintenance: str = "PREVENTIVE",
    description: str | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """Met une machine en maintenance (PREVENTIVE, CORRECTIVE ou URGENCE).
    ACTION SUR L'ATELIER : accord explicite de l'opérateur requis avant confirmation=true."""
    libelle = f"lancer une maintenance {type_maintenance} sur {code_ou_id}"
    if not confirmation_gate.evaluer(
        config,
        "lancer_maintenance",
        {"code_ou_id": code_ou_id, "type_maintenance": type_maintenance},
        confirmation,
    ):
        return _demande_confirmation(libelle)
    if type_maintenance not in TypeMaintenance.__members__:
        return "Type invalide : utilisez PREVENTIVE, CORRECTIVE ou URGENCE.", None
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        try:
            simulator_service.demarrer_maintenance(
                db, machine, type_maintenance=type_maintenance, description=description
            )
        except AppError as exc:
            return f"❌ {exc.message}", None
        db.flush()
        broadcast_service.diffuser_machine(db, machine)
        return (
            f"✅ Maintenance {type_maintenance} démarrée sur {machine.code}.",
            _action_executee(libelle),
        )


@tool(response_format="content_and_artifact")
def basculer_of_vers_ligne(
    of_numero_ou_id: str, ligne_id: int, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """Bascule un OF vers une autre ligne de production : libère la machine actuelle,
    réaffecte l'OF et démarre une machine libre de la ligne cible.

    À proposer quand la ligne actuelle est bloquée (panne, maintenance). Utilisez
    `choisir_meilleure_ligne` d'abord pour justifier la ligne cible.
    ACTION SUR L'ATELIER : accord explicite de l'opérateur requis avant confirmation=true.
    """
    libelle = f"basculer l'OF {of_numero_ou_id} vers la ligne id={ligne_id}"
    if not confirmation_gate.evaluer(
        config,
        "basculer_of_vers_ligne",
        {"of": of_numero_ou_id, "ligne_id": ligne_id},
        confirmation,
    ):
        return _demande_confirmation(libelle)
    with session_scope() as db:
        of = _trouver_of(db, of_numero_ou_id)
        if of is None:
            return f"OF introuvable : {of_numero_ou_id}", None

        cible = line_scoring_service.machine_libre_sur_ligne(db, ligne_id)
        if cible is None:
            return (
                f"❌ Aucune machine libre sur la ligne id={ligne_id} : "
                "impossible de basculer l'OF.",
                None,
            )

        # Libère la machine qui portait l'OF (si elle existe encore).
        source = db.execute(
            select(Machine).where(Machine.ordre_fabrication_id == of.id)
        ).scalars().first()
        source_txt = ""
        if source is not None:
            source.ordre_fabrication_id = None
            if source.statut == StatutMachine.MARCHE:
                source.statut = StatutMachine.ARRET
            source_txt = f" (machine {source.code} libérée)"

        of.ligne_production_id = ligne_id
        db.flush()

        try:
            simulator_service.demarrer(db, cible, ordre_fabrication_id=of.id)
        except AppError as exc:
            return f"❌ Bascule interrompue : {exc.message}", None
        db.flush()

        if source is not None:
            broadcast_service.diffuser_machine(db, source)
        broadcast_service.diffuser_machine(db, cible)
        return (
            f"✅ OF {of.numero} basculé vers la ligne id={ligne_id} : "
            f"machine {cible.code} démarrée{source_txt}. La production reprend.",
            _action_executee(libelle),
        )


@tool(response_format="content_and_artifact")
def acquitter_alerte(
    alerte_id: int, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """Acquitte (résout) une alerte du tableau de bord après traitement.
    ACTION : accord explicite de l'opérateur requis avant confirmation=true."""
    libelle = f"acquitter l'alerte id={alerte_id}"
    if not confirmation_gate.evaluer(
        config, "acquitter_alerte", {"alerte_id": alerte_id}, confirmation
    ):
        return _demande_confirmation(libelle)
    with session_scope() as db:
        alerte = db.get(Alert, alerte_id)
        if alerte is None:
            return f"Alerte introuvable (id={alerte_id}).", None
        if alerte.resolved:
            return f"L'alerte id={alerte_id} est déjà résolue.", None
        alerte.resolved = True
        alerte.resolved_at = datetime.utcnow()
        db.flush()
        broadcast_service.diffuser({"type": "alert_update", "alert_id": alerte_id})
        return (
            f"✅ Alerte id={alerte_id} acquittée : « {alerte.message} »",
            _action_executee(libelle),
        )


ACTION_TOOLS = [
    choisir_meilleure_ligne,
    generer_rapport_production,
    risque_panne_machines,
    simuler_scenario_panne,
    demarrer_machine,
    arreter_machine,
    resoudre_arret_machine,
    lancer_maintenance,
    basculer_of_vers_ligne,
    acquitter_alerte,
]
