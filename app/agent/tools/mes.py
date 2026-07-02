"""Outils agent pour le suivi MES/SCADA temps réel (machines, TRS, arrêts, alertes).

Même pattern que `manufacturing.py` : chaque outil ouvre sa propre session, ne modifie
rien (lecture seule), et renvoie un texte français lisible par l'opérateur.
"""
from __future__ import annotations

from langchain_core.tools import tool
from sqlalchemy import select

from app.db.session import session_scope
from app.models import Alert, DowntimeEvent, Machine, OrdreFabrication
from app.services import trs_service

_LIBELLE_PERTE = {
    "disponibilite": "la disponibilité",
    "performance": "la performance",
    "qualite": "la qualité",
}


@tool
def etat_machine(code_ou_id: str) -> str:
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
            return f"Machine introuvable : {code_ou_id}"

        lignes = [
            f"Machine {machine.code} ({machine.nom}) : statut {machine.statut.value}",
            f"OF actif : {machine.ordre_fabrication.numero if machine.ordre_fabrication else 'aucun'}",
            f"Production : {machine.quantite_produite} unités "
            f"({machine.quantite_bonne} bonnes, {machine.quantite_rejetee} rebuts)",
        ]
        if machine.temps_cycle_cible_s:
            r = trs_service.calculer_trs_machine(db, machine)
            lignes.append(
                f"TRS : {r.trs * 100:.0f}% (TQ {r.tq * 100:.0f}% / TP {r.tp * 100:.0f}% / "
                f"DO {r.do * 100:.0f}%) — perte principale : {_LIBELLE_PERTE[r.pertes.principale]}"
            )
        return "\n".join(lignes)


@tool
def resume_trs(scope: str, id: int) -> str:
    """Résumé TRS/TRG/TRE pour une machine, une ligne ou un OF.

    `scope` doit valoir 'machine', 'ligne' ou 'of'. `id` est l'id correspondant.
    """
    with session_scope() as db:
        if scope == "machine":
            machine = db.get(Machine, id)
            if machine is None:
                return f"Machine introuvable (id={id})."
            r = trs_service.calculer_trs_machine(db, machine)
            libelle = machine.code
        elif scope == "ligne":
            machines = list(
                db.execute(select(Machine).where(Machine.ligne_production_id == id)).scalars()
            )
            resultat = trs_service.calculer_trs_ligne(db, machines)
            if resultat is None:
                return f"Aucune machine avec un temps de cycle cible sur la ligne id={id}."
            r = resultat
            libelle = f"ligne id={id}"
        elif scope == "of":
            of = db.get(OrdreFabrication, id)
            if of is None:
                return f"OF introuvable (id={id})."
            r = trs_service.calculer_trs_ordre(db, of)
            libelle = of.numero
        else:
            return "scope invalide : utilisez 'machine', 'ligne' ou 'of'."

        return (
            f"TRS de {libelle} : {r.trs * 100:.0f}% | TRG {r.trg * 100:.0f}% | TRE {r.tre * 100:.0f}%\n"
            f"  TQ (qualité) {r.tq * 100:.0f}% | TP (performance) {r.tp * 100:.0f}% | "
            f"DO (disponibilité) {r.do * 100:.0f}%\n"
            f"  Perte principale : {_LIBELLE_PERTE[r.pertes.principale]}\n"
            f"  Production : {r.quantite_bonne} bonnes, {r.quantite_rejetee} rebuts"
        )


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


MES_TOOLS = [etat_machine, resume_trs, arrets_actifs, alertes_actives]
