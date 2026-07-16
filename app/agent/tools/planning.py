"""Outils agent d'ordonnancement du backlog d'OF.

Répartition des rôles : le LLM mène le dialogue (quelle règle ? quelles
priorités ? pourquoi celle-là ?), `planning_service` calcule. Nova ne rédige
jamais une date elle-même — elle appelle l'outil et rapporte le résultat.

Les simulations d'affectation et d'ordonnancement sont en lecture seule. Les
deux outils d'application sont protégés par le `confirmation_gate` comme toute
action qui modifie l'atelier.
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.agent.tools import confirmation_gate
from app.agent.tools.notify import (
    CANAUX,
    _demande_confirmation,
    _destinataire_canonique,
    _envoyer,
)
from app.core.exceptions import AppError
from app.db.session import session_scope
from app.services import broadcast_service, pdf_service, planning_service

_REGLES = ", ".join(planning_service.ALGORITHMES)


def _prioritaires(of_prioritaires: list[str] | None) -> tuple[str, ...]:
    return tuple(o for o in (of_prioritaires or []) if o and o.strip())


@tool(response_format="content_and_artifact")
def simuler_affectation_lignes(
    strategie: str,
    reaffecter: bool,
) -> tuple[str, dict | None]:
    """Propose les lignes des OF PLANIFIE sans rien modifier.

    `strategie` doit être "ECT" pour équilibrer la charge et finir au plus tôt,
    ou "SETUP" pour regrouper les OF par article et limiter les réglages.
    `reaffecter` vaut true pour remettre en question les lignes déjà renseignées,
    false pour ne placer que les OF sans ligne ou actuellement incompatibles.

    Avant d'appeler cet outil, si l'opérateur n'a pas donné ces deux choix,
    demande-lui clairement son objectif et le périmètre. Ne choisis jamais à sa
    place. Après la simulation, présente les changements et demande confirmation
    avant d'utiliser `appliquer_affectation_lignes`.
    """
    with session_scope() as db:
        try:
            scenario = planning_service.proposer_affectation_lignes(
                db, strategie, reaffecter
            )
        except ValueError as exc:
            return f"❌ {exc}", None
        return (
            planning_service.resume_affectation_lignes(scenario),
            planning_service.artifact_affectation_lignes(scenario),
        )


@tool(response_format="content_and_artifact")
def appliquer_affectation_lignes(
    strategie: str,
    reaffecter: bool,
    confirmation: bool,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """Affecte les lignes proposées aux OF PLANIFIE.

    ACTION QUI ÉCRIT EN BASE : appelle d'abord `simuler_affectation_lignes` avec
    exactement la même stratégie et le même périmètre, présente chaque changement
    et obtiens un « oui » explicite. Une ligne modifiée invalide les anciennes
    dates prévues ; propose ensuite de comparer les 13 règles d'ordonnancement.
    """
    choix = (strategie or "").strip().upper()
    if choix not in planning_service.STRATEGIES_AFFECTATION:
        disponibles = ", ".join(planning_service.STRATEGIES_AFFECTATION)
        return (
            f"❌ Stratégie d'affectation inconnue : {strategie!r}. "
            f"Disponibles : {disponibles}.",
            None,
        )

    scope = (
        "tous les OF planifiés, y compris ceux déjà affectés"
        if reaffecter
        else "les OF sans ligne ou incompatibles uniquement"
    )
    libelle = (
        f"appliquer l'affectation {choix} aux {scope} et effacer les anciens "
        "créneaux des OF déplacés"
    )
    if not confirmation_gate.evaluer(
        config,
        "appliquer_affectation_lignes",
        {"strategie": choix, "reaffecter": reaffecter},
        confirmation,
    ):
        return (
            f"Confirmation requise : {libelle}. Présente d'abord la simulation "
            "et obtiens un « oui » explicite avant de rappeler cet outil "
            "(confirmation=true).",
            {"kind": "confirmation_attente", "libelle": libelle},
        )

    with session_scope() as db:
        scenario = planning_service.proposer_affectation_lignes(
            db, choix, reaffecter
        )
        modifies = planning_service.appliquer_affectation_lignes(db, scenario)
        art = planning_service.artifact_affectation_lignes(scenario)

    art["applique"] = True
    if modifies == 0:
        return (
            "Aucun changement de ligne à appliquer avec ces choix.\n\n"
            + planning_service.resume_affectation_lignes(scenario),
            art,
        )

    broadcast_service.diffuser(
        {
            "type": "ordres_update",
            "raison": "affectation_lignes",
            "strategie": choix,
            "nb_of": modifies,
        }
    )
    return (
        f"✅ Affectation {choix} appliquée : {modifies} OF déplacé(s). Les anciens "
        "créneaux de ces OF ont été effacés. Il faut maintenant recalculer "
        "l'ordonnancement.\n\n"
        + planning_service.resume_affectation_lignes(scenario),
        art,
    )


@tool(response_format="content_and_artifact")
def simuler_ordonnancement(
    algorithme: str = planning_service.ALGORITHME_DEFAUT,
    of_prioritaires: list[str] | None = None,
) -> tuple[str, dict | None]:
    """Ordonnance le backlog d'OF ouverts selon une règle de dispatching et
    renvoie le planning projeté (début/fin par OF, retards, changements de série).
    Ne modifie RIEN : c'est une simulation.

    `algorithme` — une règle parmi : FIFO (premier arrivé), LIFO (dernier arrivé),
    EDD (échéance la plus proche), SPT (production la plus courte), LPT (la plus
    longue), CR (ratio critique), SLACK (marge minimale), SETUP (regroupe par
    article pour limiter les changements de série), SETUP_EDD (regroupe par
    article, groupes classés par échéance la plus proche), MDD (échéance
    modifiée — ignore les délais déjà perdus), ATC (coût de retard apparent —
    urgence exponentielle pondérée par la durée), COVERT (coût de retard
    escompté — urgence linéaire par unité de temps de production), MOORE
    (Moore-Hodgson — minimise le NOMBRE d'OF en retard, quitte à en sacrifier un
    très en retard). Par défaut EDD.

    `of_prioritaires` — numéros d'OF à passer EN TÊTE de file avant d'appliquer la
    règle, dans l'ordre donné : pour « fais l'OF-2026-00007 en premier », passe
    ["OF-2026-00007"]. Les OF déjà EN_COURS ne sont jamais déplacés.

    À utiliser pour « dans quel ordre lancer les OF ? », « ordonnance en SPT »,
    « et si je passe l'OF-12 d'abord ? », « on tiendra les délais ? ». Pour savoir
    QUELLE règle choisir, utilise plutôt `comparer_algorithmes`. Pour ENREGISTRER
    les dates, utilise `appliquer_ordonnancement`.
    """
    with session_scope() as db:
        try:
            scenario = planning_service.ordonnancer(
                db, algorithme, _prioritaires(of_prioritaires)
            )
        except ValueError as exc:
            return f"❌ {exc}", None
        return planning_service.resume_texte(scenario), planning_service.artifact(scenario)


@tool(response_format="content_and_artifact")
def comparer_algorithmes(of_prioritaires: list[str] | None = None) -> tuple[str, dict | None]:
    """Joue les 13 règles de dispatching sur le MÊME backlog et les classe, de la
    meilleure à la pire. Ne modifie RIEN.

    Le classement privilégie le retard client (jours cumulés, puis nombre d'OF en
    retard), puis les changements de série, puis la durée totale du plan.

    `of_prioritaires` — numéros d'OF forcés en tête de file dans chaque scénario,
    si l'opérateur impose un passage prioritaire.

    À utiliser pour « quelle règle est la meilleure ? », « compare les
    algorithmes », « laquelle limite les retards ? », ou avant d'appliquer quand
    l'opérateur n'a pas nommé de règle.
    """
    with session_scope() as db:
        try:
            scenarios = planning_service.comparer(db, _prioritaires(of_prioritaires))
        except ValueError as exc:
            return f"❌ {exc}", None
        return (
            planning_service.resume_comparaison(scenarios),
            planning_service.artifact_comparaison(scenarios),
        )


@tool(response_format="content_and_artifact")
def appliquer_ordonnancement(
    algorithme: str,
    confirmation: bool,
    of_prioritaires: list[str] | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """ENREGISTRE le planning : écrit la date de début prévue, la date de fin
    prévue ET la ligne de chaque OF PLANIFIE, selon `algorithme` et
    `of_prioritaires`.

    La ligne écrite est celle sur laquelle le moteur pose l'OF : un OF qui avait
    déjà une ligne la conserve (aucun rééquilibrage) ; un OF sans ligne reçoit
    celle qui se libère le plus tôt. Ne touche ni au statut ni à l'échéance
    client, et laisse les OF EN_COURS intacts. Le plan est RECALCULÉ sur les
    données fraîches au moment de l'écriture : les dates enregistrées sont donc
    celles du plan annoncé, pas celles d'une simulation périmée.

    ACTION QUI ÉCRIT EN BASE : montre d'abord le plan (`simuler_ordonnancement`
    ou `comparer_algorithmes`), annonce combien d'OF seront datés, et n'appelle
    avec confirmation=true qu'après un « oui » explicite de l'opérateur.
    """
    algo = (algorithme or "").strip().upper()
    if algo not in planning_service.ALGORITHMES:
        return f"❌ Règle inconnue : {algorithme!r}. Disponibles : {_REGLES}.", None

    prioritaires = _prioritaires(of_prioritaires)
    libelle = f"appliquer l'ordonnancement {algo} (dates de début/fin des OF planifiés)"
    if prioritaires:
        libelle += f", en priorisant {', '.join(prioritaires)}"

    if not confirmation_gate.evaluer(
        config,
        "appliquer_ordonnancement",
        {"algorithme": algo, "of_prioritaires": list(prioritaires)},
        confirmation,
    ):
        return (
            f"Confirmation requise : {libelle}. Présente d'abord le plan à l'opérateur "
            "et obtiens un « oui » explicite avant de rappeler cet outil "
            "(confirmation=true).",
            {"kind": "confirmation_attente", "libelle": libelle},
        )

    with session_scope() as db:
        try:
            scenario = planning_service.ordonnancer(db, algo, prioritaires)
        except ValueError as exc:
            return f"❌ {exc}", None
        dates = planning_service.appliquer(db, scenario)
        art = planning_service.artifact(scenario)

    if dates == 0:
        return (
            "Aucun OF PLANIFIE à dater : le plan n'a rien changé en base.",
            None,
        )
    art["applique"] = True
    # APRÈS le commit du `session_scope` : la page Ordres se recharge en réaction
    # à ce message, elle doit lire les dates déjà écrites, pas la transaction en vol.
    broadcast_service.diffuser(
        {
            "type": "ordres_update",
            "raison": "ordonnancement",
            "algorithme": algo,
            "nb_of": dates,
        }
    )
    return (
        f"✅ Ordonnancement {algo} appliqué : {dates} OF planifié(s) datés "
        f"({scenario.nb_retards} retard(s) prévisionnel(s), "
        f"{scenario.retard_total_jours} j cumulés). Les dates sont visibles sur la "
        f"page Ordres de fabrication.\n\n" + planning_service.resume_texte(scenario),
        art,
    )


@tool(response_format="content_and_artifact")
def envoyer_ordonnancement(
    canal: str,
    destinataire: str,
    algorithme: str,
    confirmation: bool,
    of_prioritaires: list[str] | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """ENVOIE le plan d'ordonnancement en PDF, accompagné d'un message de
    synthèse, par `canal` : "whatsapp" (numéro, ex. +216…) ou "email".

    Le plan est recalculé sur les données fraîches avec `algorithme` (et
    `of_prioritaires` le cas échéant) : après un `appliquer_ordonnancement`,
    donne la MÊME règle pour que le PDF reflète ce qui a été enregistré.

    À utiliser pour « envoie le nouveau planning au +216… », « partage le plan
    EDD sur WhatsApp à l'équipe de nuit ». ACTION SORTANTE : récapitule règle +
    canal + destinataire et obtiens un « oui » explicite avant de rappeler avec
    confirmation=true.
    """
    if canal not in CANAUX:
        return f"Canal invalide : {canal!r}. Utilise \"email\" ou \"whatsapp\".", None
    algo = (algorithme or "").strip().upper()
    if algo not in planning_service.ALGORITHMES:
        return f"❌ Règle inconnue : {algorithme!r}. Disponibles : {_REGLES}.", None

    destinataire = _destinataire_canonique(canal, destinataire)
    prioritaires = _prioritaires(of_prioritaires)
    libelle = f"envoyer le plan d'ordonnancement {algo} (PDF + message) par {canal} à {destinataire}"
    if not confirmation_gate.evaluer(
        config,
        "envoyer_ordonnancement",
        {
            "canal": canal,
            "destinataire": destinataire,
            "algorithme": algo,
            "of_prioritaires": list(prioritaires),
        },
        confirmation,
    ):
        return _demande_confirmation(libelle)

    with session_scope() as db:
        try:
            scenario = planning_service.ordonnancer(db, algo, prioritaires)
        except ValueError as exc:
            return f"❌ {exc}", None
        if not scenario.creneaux:
            return "Aucun OF ouvert : il n'y a pas de plan à envoyer.", None
        pdf = pdf_service.generer_ordonnancement_pdf(scenario)

    horodatage = f"{datetime.now():%d/%m/%Y %H:%M}"
    sujet = f"Plan d'ordonnancement {algo} — {horodatage}"
    corps = (
        f"{sujet}\n"
        f"{len(scenario.planifies)} OF planifié(s), {scenario.nb_retards} retard(s) "
        f"prévisionnel(s) ({scenario.retard_total_jours} j cumulés), "
        f"{scenario.nb_changements_serie} changement(s) de série, plan sur "
        f"{scenario.makespan_h:.1f} h.\n"
        "Détail complet dans le PDF joint, généré par Nova."
    )
    nom_fichier = f"ordonnancement-{algo.lower()}-{datetime.now():%Y%m%d-%H%M}.pdf"
    try:
        resultat = _envoyer(canal, destinataire, sujet, corps, document=(nom_fichier, pdf))
    except AppError as exc:
        return f"❌ {exc.message}", None
    return (
        f"✅ {resultat} Pièce jointe : {nom_fichier} (plan {algo}, "
        f"{len(scenario.planifies)} OF).",
        {"kind": "action_executee", "libelle": libelle},
    )


PLANNING_TOOLS = [
    simuler_affectation_lignes,
    appliquer_affectation_lignes,
    simuler_ordonnancement,
    comparer_algorithmes,
    appliquer_ordonnancement,
    envoyer_ordonnancement,
]
