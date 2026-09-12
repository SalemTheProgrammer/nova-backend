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
from app.core.temps import date_usine, en_heure_usine
from app.db.session import session_scope
from app.models import Alert, LigneProduction, Machine, OrdreFabrication
from app.models.enums import StatutMachine, TypeMaintenance
from app.services import (
    broadcast_service,
    cost_service,
    line_queue_service,
    line_scoring_service,
    machine_command_service,
    production_control_service,
    report_service,
    risk_service,
)
from app.services.line_queue_service import DispositionPreemption

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

    Calcule : pièces non produites pendant l'arrêt, PERTE FINANCIÈRE estimée en
    dinars (production non réalisée + immobilisation machine), retard estimé sur
    l'OF en cours par rapport à sa date de fin prévue, et la meilleure ligne de
    repli si un re-routage devenait nécessaire. À utiliser pour « et si M-01
    tombe en panne 2 heures ? », « quel serait l'impact d'un arrêt de 30 min ? »,
    « combien coûterait une panne d'une heure ? ». Lecture seule : aucune
    confirmation nécessaire.
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

        cout = cost_service.estimer_cout_arret(
            machine, float(duree_minutes), pieces_perdues=pieces_perdues
        )
        lignes_txt.append(f"- Perte financière estimée : {cout.resume()}.")

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
                "date_echeance": of.date_echeance.isoformat() if of.date_echeance else None,
            }
            if cycle and cycle > 0:
                heures_restantes = restant * float(cycle) / 3600
                nouvelle_fin = datetime.utcnow() + timedelta(
                    minutes=duree_minutes, hours=heures_restantes
                )
                lignes_txt.append(
                    f"- OF en cours {of.numero} : {restant:g} unité(s) restantes, "
                    f"fin estimée décalée au {en_heure_usine(nouvelle_fin):%Y-%m-%d %H:%M}."
                )
                if of.date_echeance is not None:
                    if date_usine(nouvelle_fin) > of.date_echeance:
                        retard_txt = (
                            f"⚠ L'échéance client ({of.date_echeance.isoformat()}) "
                            "serait DÉPASSÉE."
                        )
                    else:
                        retard_txt = (
                            f"L'échéance client ({of.date_echeance.isoformat()}) "
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
            "cout": cout.artifact(),
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


@tool(response_format="content_and_artifact")
def analyser_bascule_of(
    of_numero_ou_id: str, ligne_id: int
) -> tuple[str, dict | None]:
    """Analyse l'impact d'une bascule d'OF vers une ligne cible, SANS MODIFIER
    l'atelier. Vérifie la compatibilité article/ligne et la disponibilité, puis
    estime le temps restant avec 30 minutes de changement de format.

    À appeler avant toute proposition de `basculer_of_vers_ligne`. Lecture seule :
    aucune confirmation nécessaire.
    """
    with session_scope() as db:
        of = _trouver_of(db, of_numero_ou_id)
        if of is None:
            return f"OF introuvable : {of_numero_ou_id}", None
        ligne = db.get(LigneProduction, ligne_id)
        if ligne is None or not ligne.actif:
            return f"Ligne cible introuvable ou inactive : id={ligne_id}", None

        source = db.execute(
            select(Machine).where(Machine.ordre_fabrication_id == of.id)
        ).scalars().first()
        compatible = any(article.id == of.article_id for article in ligne.articles)
        cible = line_scoring_service.machine_libre_sur_ligne(db, ligne_id)
        restant = max(0.0, float(of.quantite_planifiee) - float(of.quantite_bonne))
        source_cycle = (
            float(source.temps_cycle_actuel_s or source.temps_cycle_cible_s)
            if source and (source.temps_cycle_actuel_s or source.temps_cycle_cible_s)
            else None
        )
        cible_cycle = (
            float(cible.temps_cycle_actuel_s or cible.temps_cycle_cible_s)
            if cible and (cible.temps_cycle_actuel_s or cible.temps_cycle_cible_s)
            else None
        )
        setup_minutes = 30
        source_minutes = restant * source_cycle / 60 if source_cycle else None
        cible_minutes = setup_minutes + restant * cible_cycle / 60 if cible_cycle else None
        delta_minutes = (
            cible_minutes - source_minutes
            if source_minutes is not None and cible_minutes is not None
            else None
        )

        blockers: list[str] = []
        if restant <= 0:
            blockers.append("l'OF ne comporte plus de quantité à produire")
        if of.ligne_production_id == ligne_id:
            blockers.append("l'OF est déjà affecté à cette ligne")
        if not compatible:
            blockers.append(f"l'article {of.article.code} n'est pas homologué sur {ligne.code}")
        if cible is None:
            blockers.append("aucune machine cible n'est libre")

        feasibility = "FAISABLE" if not blockers else "NON RECOMMANDÉE"
        lines = [
            f"Analyse lecture seule : bascule de l'OF {of.numero} vers {ligne.code} — {feasibility}.",
            f"- Article : {of.article.code} ({of.article.designation}) — "
            + ("compatible." if compatible else "non compatible avec la ligne."),
            f"- Quantité restante : {restant:g} unité(s).",
            f"- Réglage/changement de format estimé : {setup_minutes} min.",
        ]
        if cible is not None:
            lines.append(
                f"- Machine cible disponible : {cible.code} ({cible.nom})"
                + (f", cycle {cible_cycle:g} s/unité." if cible_cycle else ", cycle inconnu.")
            )
        if cible_minutes is not None:
            lines.append(f"- Durée estimée sur la cible, réglage inclus : {cible_minutes:.0f} min.")
        if delta_minutes is not None:
            direction = "retard" if delta_minutes > 0 else "gain"
            lines.append(f"- Impact par rapport à la machine actuelle : {abs(delta_minutes):.0f} min de {direction} estimé.")
        if blockers:
            lines.append("- Points bloquants : " + "; ".join(blockers) + ".")
        lines.append("Aucune affectation ni commande machine n'a été modifiée.")

        artifact = {
            "kind": "switch_impact",
            "of": of.numero,
            "article": {"id": of.article_id, "code": of.article.code},
            "source": {
                "ligne_id": of.ligne_production_id,
                "machine": source.code if source else None,
                "cycle_s": source_cycle,
            },
            "cible": {
                "ligne_id": ligne.id,
                "code": ligne.code,
                "machine": cible.code if cible else None,
                "cycle_s": cible_cycle,
            },
            "compatible": compatible,
            "faisable": not blockers,
            "restant": restant,
            "setup_minutes": setup_minutes,
            "duree_cible_minutes": cible_minutes,
            "delta_minutes": delta_minutes,
            "blocages": blockers,
        }
        return "\n".join(lines), artifact


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
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        # L'OF est résolu AVANT la confirmation, pour que l'opérateur valide
        # exactement ce qui sera lancé : l'OF demandé, sinon celui que la
        # machine porte déjà, sinon la tête de file (EDD) de sa ligne.
        of: OrdreFabrication | None
        if ordre_fabrication_id is not None:
            of = db.get(OrdreFabrication, ordre_fabrication_id)
            if of is None:
                return f"OF introuvable (id={ordre_fabrication_id}).", None
        elif machine.ordre_fabrication_id is not None:
            of = db.get(OrdreFabrication, machine.ordre_fabrication_id)
        else:
            of = line_queue_service.prochain_of(db, machine.ligne_production_id)
        if of is None:
            return (
                f"Aucun OF en attente sur la ligne de {machine.code} : précisez l'OF à lancer.",
                None,
            )
        machine_id, of_id = machine.id, of.id
        libelle = f"démarrer la machine {machine.code} sur l'OF {of.numero}"
    # Signature du garde-fou sur les ID CANONIQUES, pas sur la chaîne fournie :
    # « M-01 » à la proposition puis « 1 » à la confirmation doivent matcher.
    if not confirmation_gate.evaluer(
        config, "demarrer_machine", {"machine_id": machine_id, "of_id": of_id}, confirmation
    ):
        return _demande_confirmation(libelle)
    try:
        message = machine_command_service.demarrer(machine_id, ordre_fabrication_id=of_id)
    except AppError as exc:
        return f"❌ {exc.message}", None
    return f"✅ {message}", _action_executee(libelle)


@tool(response_format="content_and_artifact")
def lancer_of_maintenant(
    of_numero_ou_id: str,
    confirmation: bool,
    preempt_disposition: str | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """Lance immédiatement un OF sur une machine libre de sa ligne affectée.

    Cette commande d'exécution SCADA ne nécessite PAS d'ordonnancement ni de
    créneau prévisionnel : la date de début réelle est enregistrée au démarrage.
    Elle résout automatiquement l'OF, sa ligne et une machine disponible.

    Si la ligne est PLEINE (aucune machine libre), l'outil renvoie qui occupe la
    ligne et n'agit pas : proposez alors à l'opérateur SOIT de préempter un OF en
    cours en rappelant avec `preempt_disposition` = "requeue" (l'OF interrompu
    reprendra son reliquat plus tard), "pause" (remis en attente hors ligne) ou
    "cancel" (annulé) ; SOIT de mettre l'OF en file via `mettre_of_en_file`.

    ACTION SUR L'ATELIER : accord explicite obligatoire avant `confirmation=true`.
    """
    with session_scope() as db:
        of = _trouver_of(db, of_numero_ou_id)
        if of is None:
            return f"OF introuvable : {of_numero_ou_id}", None
        if of.statut.value == "EN_COURS":
            machine = db.execute(
                select(Machine).where(Machine.ordre_fabrication_id == of.id)
            ).scalars().first()
            suffixe = f" sur {machine.code}" if machine else ""
            return f"L'OF {of.numero} est déjà en cours{suffixe}.", None
        if of.statut.value not in {"BROUILLON", "PLANIFIE"}:
            return f"Impossible de lancer {of.numero} : statut {of.statut.value}.", None
        if of.ligne_production_id is None or of.ligne_production is None:
            return f"Impossible de lancer {of.numero} : aucune ligne ne lui est affectée.", None

        disposition: DispositionPreemption | None = None
        if preempt_disposition is not None:
            try:
                disposition = DispositionPreemption(preempt_disposition)
            except ValueError:
                return (
                    f"Disposition inconnue : {preempt_disposition}. "
                    "Choix : requeue, pause, cancel.",
                    None,
                )

        machine_libre = line_scoring_service.machine_libre_sur_ligne(db, of.ligne_production_id)
        if machine_libre is None and disposition is None:
            # Ligne pleine et aucune consigne de préemption : on informe sans agir.
            occupations = line_queue_service.occupations_ligne(db, of.ligne_production_id)
            details = "; ".join(
                f"{occ.machine.code} → OF {occ.ordre.numero} ({occ.ordre.article.code}, "
                f"reste {line_queue_service.reste_a_produire(occ.ordre)})"
                for occ in occupations
            ) or "aucun OF identifié"
            return (
                f"La ligne {of.ligne_production.code} est occupée : {details}. "
                f"Pour lancer {of.numero} maintenant, préemptez un OF en cours "
                "(rappelez avec preempt_disposition=requeue|pause|cancel) ou "
                "mettez-le en file (mettre_of_en_file).",
                {
                    "kind": "ligne_occupee",
                    "of": of.numero,
                    "ligne": of.ligne_production.code,
                    "occupations": [
                        {
                            "machine": occ.machine.code,
                            "of": occ.ordre.numero,
                            "article": occ.ordre.article.code if occ.ordre.article else None,
                            "reste": line_queue_service.reste_a_produire(occ.ordre),
                        }
                        for occ in occupations
                    ],
                },
            )

        quantite = format(of.quantite_planifiee, "f").rstrip("0").rstrip(".")
        cible_txt = (
            machine_libre.code
            if machine_libre is not None
            else f"une machine préemptée ({disposition.value})"
        )
        libelle = (
            f"lancer maintenant l'OF {of.numero} ({of.article.code}, "
            f"quantité {quantite}) sur {cible_txt} / {of.ligne_production.code}"
        )
        if not confirmation_gate.evaluer(
            config,
            "lancer_of_maintenant",
            {"of_id": of.id, "disposition": preempt_disposition},
            confirmation,
        ):
            return _demande_confirmation(libelle)

        of_id = of.id
    try:
        lancement = production_control_service.lancer_of(of_id, preempt_disposition=disposition)
    except AppError as exc:
        return f"❌ {exc.message}", None
    broadcast_service.diffuser(
        {"type": "ordres_update", "raison": "demarrage", "numero": lancement.of_numero}
    )
    resume = (
        f"✅ OF {lancement.of_numero} lancé sur {lancement.machine_code} "
        f"({lancement.ligne_code}), démarrage confirmé par l'automate. Statut : EN_COURS."
    )
    if lancement.of_preempte is not None and lancement.disposition is not None:
        resume += f" OF {lancement.of_preempte} préempté (sort : {lancement.disposition.value})."
    return resume, _action_executee(libelle)


@tool(response_format="content_and_artifact")
def mettre_of_en_file(
    of_numero_ou_id: str,
    ligne_code_ou_id: str,
    confirmation: bool,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """Met un OF en file d'attente sur une ligne, sans le démarrer.

    L'OF est rattaché à la ligne et passé en PLANIFIE ; il prend sa place dans la
    file (triée par échéance) et sera proposé au lancement quand la ligne se
    libère. ACTION SUR L'ATELIER : accord explicite avant `confirmation=true`.
    """
    with session_scope() as db:
        of = _trouver_of(db, of_numero_ou_id)
        if of is None:
            return f"OF introuvable : {of_numero_ou_id}", None
        ligne: LigneProduction | None = None
        if ligne_code_ou_id.isdigit():
            ligne = db.get(LigneProduction, int(ligne_code_ou_id))
        if ligne is None:
            ligne = db.execute(
                select(LigneProduction).where(LigneProduction.code == ligne_code_ou_id)
            ).scalars().first()
        if ligne is None:
            return f"Ligne introuvable : {ligne_code_ou_id}", None

        libelle = f"mettre l'OF {of.numero} en file sur la ligne {ligne.code}"
        if not confirmation_gate.evaluer(
            config, "mettre_of_en_file", {"of_id": of.id, "ligne_id": ligne.id}, confirmation
        ):
            return _demande_confirmation(libelle)

        try:
            line_queue_service.mettre_en_file(db, of, ligne.id)
        except AppError as exc:
            return f"❌ {exc.message}", None
        db.commit()
        broadcast_service.diffuser(
            {"type": "ordres_update", "raison": "mise_en_file", "numero": of.numero}
        )
        position = next(
            (
                i
                for i, q in enumerate(line_queue_service.file_attente(db, ligne.id), start=1)
                if q.id == of.id
            ),
            None,
        )
        pos_txt = f" (position {position} dans la file)" if position else ""
        return (
            f"✅ OF {of.numero} mis en file sur {ligne.code}{pos_txt}. Statut : PLANIFIE.",
            _action_executee(libelle),
        )


@tool(response_format="content_and_artifact")
def arreter_machine(
    code_ou_id: str, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """Arrête une machine (commande SCADA). ACTION SUR L'ATELIER : accord explicite
    de l'opérateur requis avant confirmation=true."""
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        libelle = f"arrêter la machine {machine.code}"
        if not confirmation_gate.evaluer(
            config, "arreter_machine", {"machine_id": machine.id}, confirmation
        ):
            return _demande_confirmation(libelle)
        machine_id = machine.id
    try:
        message = machine_command_service.arreter(machine_id)
    except AppError as exc:
        return f"❌ {exc.message}", None
    return f"✅ {message}", _action_executee(libelle)


@tool(response_format="content_and_artifact")
def arreter_ligne(
    ligne_code_ou_id: str, confirmation: bool, *, config: RunnableConfig
) -> tuple[str, dict | None]:
    """Arrête TOUTES les machines actives d'une ligne de production (commande SCADA).

    À utiliser pour « arrête la ligne X » / « stoppe toute la ligne », par
    opposition à `arreter_machine` qui ne touche qu'une seule machine.
    ACTION SUR L'ATELIER : accord explicite de l'opérateur requis avant
    confirmation=true.
    """
    with session_scope() as db:
        ligne: LigneProduction | None = None
        if ligne_code_ou_id.isdigit():
            ligne = db.get(LigneProduction, int(ligne_code_ou_id))
        if ligne is None:
            ligne = db.execute(
                select(LigneProduction).where(LigneProduction.code == ligne_code_ou_id)
            ).scalars().first()
        if ligne is None:
            return f"Ligne introuvable : {ligne_code_ou_id}", None

        libelle = f"arrêter toute la ligne {ligne.code}"
        if not confirmation_gate.evaluer(
            config, "arreter_ligne", {"ligne_id": ligne.id}, confirmation
        ):
            return _demande_confirmation(libelle)

        ligne_id = ligne.id
    try:
        arret = production_control_service.arreter_ligne(ligne_id)
    except AppError as exc:
        return f"❌ {exc.message}", None
    if not arret.arretees and not arret.echecs:
        return f"Aucune machine en production sur {arret.ligne_code} : rien à arrêter.", None
    morceaux: list[str] = []
    if arret.arretees:
        morceaux.append(f"✅ Ligne {arret.ligne_code} : {', '.join(arret.arretees)} arrêtée(s).")
    if arret.deja_arretees:
        morceaux.append(f"Déjà à l'arrêt : {', '.join(arret.deja_arretees)}.")
    if arret.indisponibles:
        morceaux.append(f"En panne ou maintenance (non concernées) : {', '.join(arret.indisponibles)}.")
    if arret.echecs:
        morceaux.append(
            "❌ Non arrêtée(s) : " + "; ".join(f"{code} ({raison})" for code, raison in arret.echecs)
        )
    return " ".join(morceaux), _action_executee(libelle) if arret.arretees else None


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
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        libelle = f"résoudre l'arrêt de {machine.code}"
        # Le commentaire ne fait pas partie de la signature : le modèle peut le
        # reformuler entre proposition et confirmation sans invalider l'accord.
        if not confirmation_gate.evaluer(
            config, "resoudre_arret_machine", {"machine_id": machine.id}, confirmation
        ):
            return _demande_confirmation(libelle)
        machine_id = machine.id
    try:
        message = machine_command_service.resoudre_arret(machine_id, commentaire=commentaire)
    except AppError as exc:
        return f"❌ {exc.message}", None
    return f"✅ {message}", _action_executee(libelle)


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
    if type_maintenance not in TypeMaintenance.__members__:
        return "Type invalide : utilisez PREVENTIVE, CORRECTIVE ou URGENCE.", None
    with session_scope() as db:
        machine = _trouver_machine(db, code_ou_id)
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None
        libelle = f"lancer une maintenance {type_maintenance} sur {machine.code}"
        if not confirmation_gate.evaluer(
            config,
            "lancer_maintenance",
            {"machine_id": machine.id, "type_maintenance": type_maintenance},
            confirmation,
        ):
            return _demande_confirmation(libelle)
        machine_id = machine.id
    try:
        message = machine_command_service.demarrer_maintenance(
            machine_id, type_maintenance=type_maintenance, description=description
        )
    except AppError as exc:
        return f"❌ {exc.message}", None
    return f"✅ {message}", _action_executee(libelle)


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
    with session_scope() as db:
        of = _trouver_of(db, of_numero_ou_id)
        if of is None:
            return f"OF introuvable : {of_numero_ou_id}", None

        # Préconditions vérifiées AVANT de demander l'accord : on ne fait jamais
        # confirmer une bascule qui serait refusée ensuite.
        try:
            of, ligne, cible = production_control_service.verifier_bascule(db, of.id, ligne_id)
        except AppError as exc:
            return f"❌ Bascule refusée : {exc.message}", None
        of_id = of.id
        libelle = (
            f"basculer l'OF {of.numero} vers la ligne {ligne.code} (démarrage sur {cible.code})"
        )
    if not confirmation_gate.evaluer(
        config, "basculer_of_vers_ligne", {"of_id": of_id, "ligne_id": ligne_id}, confirmation
    ):
        return _demande_confirmation(libelle)
    try:
        bascule = production_control_service.basculer_of(of_id, ligne_id)
    except AppError as exc:
        return f"❌ Bascule interrompue : {exc.message}", None
    source = f" (machine {bascule.machine_source} libérée)" if bascule.machine_source else ""
    return (
        f"✅ OF {bascule.of_numero} basculé vers {bascule.ligne_code} : machine "
        f"{bascule.machine_cible} démarrée{source}. La production reprend.",
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
        db.commit()
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
    analyser_bascule_of,
    demarrer_machine,
    lancer_of_maintenant,
    mettre_of_en_file,
    arreter_machine,
    arreter_ligne,
    resoudre_arret_machine,
    lancer_maintenance,
    basculer_of_vers_ligne,
    acquitter_alerte,
]
