"""Outils agent pour le suivi MES/SCADA temps réel (machines, TRS, arrêts, alertes).

Même pattern que `manufacturing.py` : chaque outil ouvre sa propre session, ne modifie
rien (lecture seule), et renvoie un texte français lisible par l'opérateur.
"""
from __future__ import annotations

from langchain_core.tools import tool
from sqlalchemy import select

from app.db.session import session_scope
from app.models import Alert, DowntimeEvent, Machine, OrdreFabrication
from app.models.referentiel import LigneProduction
from app.services import cost_service, machine_read_service, trs_service

_LIBELLE_PERTE = {
    "disponibilite": "la disponibilité",
    "performance": "la performance",
    "qualite": "la qualité",
}


def _trouver_of(db, numero_ou_id: str) -> OrdreFabrication | None:
    of: OrdreFabrication | None = None
    if numero_ou_id.isdigit():
        of = db.get(OrdreFabrication, int(numero_ou_id))
    if of is None:
        of = db.execute(
            select(OrdreFabrication).where(OrdreFabrication.numero == numero_ou_id)
        ).scalars().first()
    return of


@tool(response_format="content_and_artifact")
def etat_machine(code_ou_id: str) -> tuple[str, dict | None]:
    """Donne l'état courant d'une machine (statut, OF actif, production, TRS/TQ/TP/DO).

    `code_ou_id` accepte le code (ex. 'M-01') ou l'id numérique.
    """
    with session_scope() as db:
        machine: Machine | None = None
        if code_ou_id.isdigit():
            machine = db.get(Machine, int(code_ou_id))
        if machine is None:
            machine = db.execute(
                select(Machine).where(Machine.code == code_ou_id)
            ).scalars().first()
        if machine is None:
            return f"Machine introuvable : {code_ou_id}", None

        lignes = [
            f"Machine {machine.code} ({machine.nom}) : statut {machine.statut.value}",
            f"OF actif : {machine.ordre_fabrication.numero if machine.ordre_fabrication else 'aucun'}",
            f"Production : {machine.quantite_produite} unités "
            f"({machine.quantite_bonne} bonnes, {machine.quantite_rejetee} rebuts)",
        ]
        artifact: dict = {
            "kind": "machine",
            "machine_id": machine.id,
            "code": machine.code,
            "nom": machine.nom,
            "statut": machine.statut.value,
            "of_actif": machine.ordre_fabrication.numero if machine.ordre_fabrication else None,
            "quantite_produite": machine.quantite_produite,
            "quantite_bonne": machine.quantite_bonne,
            "quantite_rejetee": machine.quantite_rejetee,
        }
        # Employé badgé au poste (Operator/Id + Operator/Name publiés par l'automate).
        operateur = machine_read_service.operateur_au_poste(db, machine.id)
        lignes.append(
            f"Opérateur au poste : {operateur.nom or operateur.matricule} (badge {operateur.matricule})"
            if operateur is not None
            else "Opérateur au poste : personne (aucun badge présenté, ou automate hors ligne)"
        )
        artifact["operateur"] = operateur.model_dump() if operateur is not None else None
        if machine.temps_cycle_cible_s:
            cadence_nominale = 3600 / float(machine.temps_cycle_cible_s)
            lignes.append(
                f"Cadence nominale : {cadence_nominale:.0f} u/h "
                f"(temps de cycle cible {float(machine.temps_cycle_cible_s):g} s) — "
                f"valeur théorique, indépendante de l'état courant de la machine"
            )
            artifact["cadence_nominale_u_h"] = round(cadence_nominale, 1)
            artifact["temps_cycle_cible_s"] = float(machine.temps_cycle_cible_s)
            r = trs_service.calculer_trs_machine(db, machine)
            lignes.append(
                f"TRS : {r.trs * 100:.0f}% (TQ {r.tq * 100:.0f}% / TP {r.tp * 100:.0f}% / "
                f"DO {r.do * 100:.0f}%) — perte principale : {_LIBELLE_PERTE[r.pertes.principale]}"
            )
            artifact.update(
                {
                    "trs": float(r.trs),
                    "tq": float(r.tq),
                    "tp": float(r.tp),
                    "do": float(r.do),
                    "perte_principale": r.pertes.principale,
                }
            )
        return "\n".join(lignes), artifact


@tool(response_format="content_and_artifact")
def etat_ligne(code_ou_id: str) -> tuple[str, dict | None]:
    """Donne l'état courant de TOUTES les machines d'une ligne de production
    (statut, OF actif, production par machine).

    `code_ou_id` accepte le code de la ligne (ex. 'LIGNE-COMP-03') ou son id
    numérique — PAS un code machine. À utiliser quand l'opérateur demande ce
    qui tourne / l'état sur une LIGNE (par opposition à `etat_machine`, qui
    ne connaît que les codes machine comme 'M-01').
    """
    with session_scope() as db:
        ligne: LigneProduction | None = None
        if code_ou_id.isdigit():
            ligne = db.get(LigneProduction, int(code_ou_id))
        if ligne is None:
            ligne = db.execute(
                select(LigneProduction).where(LigneProduction.code == code_ou_id)
            ).scalars().first()
        if ligne is None:
            return f"Ligne introuvable : {code_ou_id}", None

        machines = list(
            db.execute(
                select(Machine).where(Machine.ligne_production_id == ligne.id)
            ).scalars()
        )
        if not machines:
            return f"Aucune machine rattachée à la ligne {ligne.code}.", None

        lignes_txt = [f"Ligne {ligne.code} ({ligne.designation}) :"]
        machines_artifact = []
        cadences_nominales: list[float] = []
        for m in machines:
            of_actif = m.ordre_fabrication.numero if m.ordre_fabrication else None
            cadence_nominale = (
                3600 / float(m.temps_cycle_cible_s) if m.temps_cycle_cible_s else None
            )
            if cadence_nominale is not None:
                cadences_nominales.append(cadence_nominale)
            cadence_txt = (
                f" | cadence nominale {cadence_nominale:.0f} u/h"
                if cadence_nominale is not None
                else ""
            )
            lignes_txt.append(
                f"- {m.code} ({m.nom}) : statut {m.statut.value} | OF actif : "
                f"{of_actif or 'aucun'} | production {m.quantite_produite} unités "
                f"({m.quantite_bonne} bonnes, {m.quantite_rejetee} rebuts){cadence_txt}"
            )
            machines_artifact.append(
                {
                    "machine_id": m.id,
                    "code": m.code,
                    "nom": m.nom,
                    "statut": m.statut.value,
                    "of_actif": of_actif,
                    "quantite_produite": m.quantite_produite,
                    "quantite_bonne": m.quantite_bonne,
                    "quantite_rejetee": m.quantite_rejetee,
                    "cadence_nominale_u_h": (
                        round(cadence_nominale, 1) if cadence_nominale is not None else None
                    ),
                    "temps_cycle_cible_s": (
                        float(m.temps_cycle_cible_s) if m.temps_cycle_cible_s else None
                    ),
                }
            )

        # Ligne série (poste → poste) : la cadence nominale de la ligne est
        # celle du poste le plus lent (goulot), pas la somme des postes.
        cadence_ligne = min(cadences_nominales) if cadences_nominales else None
        if cadence_ligne is not None:
            lignes_txt.append(
                f"Cadence nominale de la ligne (goulot) : {cadence_ligne:.0f} u/h — "
                f"valeur théorique, indépendante de l'état courant des machines"
            )

        artifact = {
            "kind": "ligne",
            "ligne_id": ligne.id,
            "code": ligne.code,
            "designation": ligne.designation,
            "cadence_nominale_ligne_u_h": (
                round(cadence_ligne, 1) if cadence_ligne is not None else None
            ),
            "machines": machines_artifact,
        }
        return "\n".join(lignes_txt), artifact


@tool(response_format="content_and_artifact")
def resume_trs(scope: str, id: int | None = None, of_numero: str | None = None) -> tuple[str, dict | None]:
    """Résumé TRS/TRG/TRE pour une machine, une ligne ou un OF.

    `scope` doit valoir 'machine', 'ligne' ou 'of'. Pour 'machine'/'ligne', `id`
    est l'id numérique correspondant. Pour 'of', préférez `of_numero` (numéro
    lisible, ex. 'OF-2026-00039' — accepte aussi un id numérique en texte) ;
    `id` reste accepté en repli si vous connaissez déjà l'id numérique de l'OF.
    """
    with session_scope() as db:
        if scope == "machine":
            if id is None:
                return "scope 'machine' nécessite id.", None
            machine = db.get(Machine, id)
            if machine is None:
                return f"Machine introuvable (id={id}).", None
            r = trs_service.calculer_trs_machine(db, machine)
            libelle = machine.code
        elif scope == "ligne":
            if id is None:
                return "scope 'ligne' nécessite id.", None
            machines = list(
                db.execute(select(Machine).where(Machine.ligne_production_id == id)).scalars()
            )
            resultat = trs_service.calculer_trs_ligne(db, machines)
            if resultat is None:
                return f"Aucune machine avec un temps de cycle cible sur la ligne id={id}.", None
            r = resultat
            libelle = f"ligne id={id}"
        elif scope == "of":
            reference = of_numero or (str(id) if id is not None else None)
            if reference is None:
                return "scope 'of' nécessite of_numero (ex. 'OF-2026-00039').", None
            of = _trouver_of(db, reference)
            if of is None:
                return f"OF introuvable : {reference}.", None
            r = trs_service.calculer_trs_ordre(db, of)
            libelle = of.numero
        else:
            return "scope invalide : utilisez 'machine', 'ligne' ou 'of'.", None

        artifact = {
            "kind": "trs",
            "scope": scope,
            "libelle": libelle,
            "trs": float(r.trs),
            "trg": float(r.trg),
            "tre": float(r.tre),
            "tq": float(r.tq),
            "tp": float(r.tp),
            "do": float(r.do),
            "perte_principale": r.pertes.principale,
            "quantite_bonne": r.quantite_bonne,
            "quantite_rejetee": r.quantite_rejetee,
        }
        return (
            f"TRS de {libelle} : {r.trs * 100:.0f}% | TRG {r.trg * 100:.0f}% | TRE {r.tre * 100:.0f}%\n"
            f"  TQ (qualité) {r.tq * 100:.0f}% | TP (performance) {r.tp * 100:.0f}% | "
            f"DO (disponibilité) {r.do * 100:.0f}%\n"
            f"  Perte principale : {_LIBELLE_PERTE[r.pertes.principale]}\n"
            f"  Production : {r.quantite_bonne} bonnes, {r.quantite_rejetee} rebuts"
        ), artifact


@tool
def arrets_actifs() -> str:
    """Liste les arrêts machine actuellement en cours (non résolus), avec durée et cause."""
    from datetime import datetime

    with session_scope() as db:
        arrets = list(
            db.execute(select(DowntimeEvent).where(DowntimeEvent.end_time.is_(None))).scalars()
        )
        if not arrets:
            return "Aucun arrêt actif : toutes les machines tournent normalement."
        lignes = []
        for a in arrets:
            duree_min = int((datetime.utcnow() - a.start_time).total_seconds() // 60)
            lignes.append(
                f"- {a.machine.code} : arrêtée depuis {duree_min} min "
                f"({a.cause.value.replace('_', ' ').lower()})"
                + (f" — {a.operator_comment}" if a.operator_comment else "")
            )
        return "\n".join(lignes)


@tool
def alertes_actives() -> str:
    """Liste les alertes actives (non résolues) du système, triées par sévérité/date."""
    with session_scope() as db:
        alertes = list(
            db.execute(
                select(Alert).where(Alert.resolved.is_(False)).order_by(Alert.created_at.desc())
            ).scalars()
        )
        if not alertes:
            return "Aucune alerte active."
        return "\n".join(f"- [{a.severity.value}] {a.message}" for a in alertes)


@tool(response_format="content_and_artifact")
def calculer_cout_of(of_numero: str) -> tuple[str, dict | None]:
    """Chiffre le coût de production d'un OF (matières + immobilisation machine, en TND).

    `of_numero` : numéro d'OF (ex. 'OF-2026-00015', accepte aussi un id numérique).
    Décompose en coût matières (généalogie de consommation FEFO × prix unitaire
    des MP) et coût d'immobilisation machine (durée réelle × coût horaire), plus
    la perte valorisée des rebuts à part. Chiffre ce qui est chiffrable et
    signale ce qui ne l'est pas (prix MP ou valeur article manquants) — ne
    devine jamais un montant.
    """
    with session_scope() as db:
        of = _trouver_of(db, of_numero)
        if of is None:
            return f"OF introuvable : {of_numero}.", None
        c = cost_service.calculer_cout_of(db, of)
        return c.resume(), c.artifact()


MES_TOOLS = [etat_machine, etat_ligne, resume_trs, calculer_cout_of, arrets_actifs, alertes_actives]
