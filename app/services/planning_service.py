"""Ordonnancement du backlog d'OF : quelle séquence maximise le respect des délais.

Heuristique déterministe (fiable en démo, sans dépendance solveur) :
  1. Les OF EN_COURS avec une machine qui tourne restent où ils sont — on ne
     projette que leur fin estimée (reste à produire × temps de cycle).
  2. Les autres OF ouverts (PLANIFIE, ou EN_COURS sans machine) sont triés par
     échéance la plus proche (EDD — Earliest Due Date) puis affectés en glouton
     à la machine qui se libère le plus tôt (temps de cycle cible connu).
  3. Chaque affectation projette une fin estimée, comparée à l'échéance : le
     plan signale les retards prévisionnels et là où ils se concentrent.

Utilisé par l'outil agent `optimiser_planning`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Machine, OrdreFabrication
from app.models.enums import StatutMachine, StatutOF

# Les machines en panne/maintenance ne reçoivent pas de nouvel OF.
_STATUTS_INDISPONIBLES = (StatutMachine.PANNE, StatutMachine.MAINTENANCE)


@dataclass
class AffectationPlan:
    of_numero: str
    article: str
    restant: float
    machine_code: str
    ligne: str
    debut_estime: datetime
    fin_estimee: datetime
    echeance: date | None
    retard_jours: int  # 0 = dans les temps
    en_cours: bool  # déjà sur cette machine (non déplaçable)


def _restant(of: OrdreFabrication) -> float:
    return max(
        0.0,
        float(of.quantite_planifiee) - float(of.quantite_bonne) - float(of.quantite_rejetee),
    )


def _retard(fin: datetime, echeance: date | None) -> int:
    if echeance is None:
        return 0
    return max(0, (fin.date() - echeance).days)


def optimiser_backlog(db: Session) -> tuple[str, dict | None]:
    """Construit le plan et le rend en texte opérateur + artifact structuré."""
    maintenant = datetime.utcnow()

    ofs = db.execute(
        select(OrdreFabrication).where(
            OrdreFabrication.statut.in_([StatutOF.PLANIFIE, StatutOF.EN_COURS])
        )
    ).scalars().all()
    ofs = [of for of in ofs if _restant(of) > 0]
    if not ofs:
        return "Aucun OF ouvert à ordonnancer : le backlog est vide.", None

    machines = db.execute(select(Machine).where(Machine.actif.is_(True))).scalars().all()
    machines_cadencees = [m for m in machines if m.temps_cycle_cible_s]

    # 1. OF déjà en production : figés sur leur machine, on projette leur fin.
    plan: list[AffectationPlan] = []
    of_traites: set[int] = set()
    # Quand chaque machine redevient libre (elle enchaîne après son OF courant).
    liberation: dict[int, datetime] = {}
    for m in machines_cadencees:
        if m.statut in _STATUTS_INDISPONIBLES:
            continue
        liberation[m.id] = maintenant
        if m.ordre_fabrication_id is None:
            continue
        of = next((o for o in ofs if o.id == m.ordre_fabrication_id), None)
        if of is None:
            continue
        cycle = float(m.temps_cycle_actuel_s or m.temps_cycle_cible_s)
        restant = _restant(of)
        fin = maintenant + timedelta(seconds=restant * cycle)
        liberation[m.id] = fin
        plan.append(
            AffectationPlan(
                of_numero=of.numero,
                article=of.article.code,
                restant=restant,
                machine_code=m.code,
                ligne=m.ligne_production.code if m.ligne_production else "?",
                debut_estime=maintenant,
                fin_estimee=fin,
                echeance=of.date_fin_prevue,
                retard_jours=_retard(fin, of.date_fin_prevue),
                en_cours=True,
            )
        )
        of_traites.add(of.id)

    # 2. Backlog restant : EDD, puis machine qui se libère le plus tôt.
    backlog = sorted(
        (of for of in ofs if of.id not in of_traites),
        key=lambda o: (o.date_fin_prevue is None, o.date_fin_prevue or date.max),
    )
    non_affectables: list[OrdreFabrication] = []
    for of in backlog:
        candidates = [m for m in machines_cadencees if m.id in liberation]
        if not candidates:
            non_affectables.append(of)
            continue
        machine = min(candidates, key=lambda m: (liberation[m.id], float(m.temps_cycle_cible_s)))
        cycle = float(machine.temps_cycle_cible_s)
        restant = _restant(of)
        debut = liberation[machine.id]
        fin = debut + timedelta(seconds=restant * cycle)
        liberation[machine.id] = fin
        plan.append(
            AffectationPlan(
                of_numero=of.numero,
                article=of.article.code,
                restant=restant,
                machine_code=machine.code,
                ligne=machine.ligne_production.code if machine.ligne_production else "?",
                debut_estime=debut,
                fin_estimee=fin,
                echeance=of.date_fin_prevue,
                retard_jours=_retard(fin, of.date_fin_prevue),
                en_cours=False,
            )
        )

    plan.sort(key=lambda a: a.fin_estimee)
    en_retard = [a for a in plan if a.retard_jours > 0]

    lignes = [f"Plan pour {len(plan)} OF ouvert(s) ({len(en_retard)} retard(s) prévisionnel(s)) :"]
    for a in plan:
        etat = "en cours" if a.en_cours else f"à lancer vers {a.debut_estime:%d/%m %H:%M} UTC"
        verdict = (
            f"⚠ retard estimé {a.retard_jours} j (échéance {a.echeance:%d/%m})"
            if a.retard_jours > 0
            else ("dans les temps" if a.echeance else "sans échéance")
        )
        lignes.append(
            f"- {a.of_numero} ({a.article}, reste {a.restant:.0f}) → {a.machine_code} "
            f"[{a.ligne}], {etat}, fin estimée {a.fin_estimee:%d/%m %H:%M} — {verdict}"
        )
    for of in non_affectables:
        lignes.append(
            f"- {of.numero} ({of.article.code}) : AUCUNE machine cadencée disponible — non planifiable."
        )
    if en_retard:
        pire = max(en_retard, key=lambda a: a.retard_jours)
        lignes.append(
            f"Priorité : {pire.of_numero} concentre le plus gros retard prévisionnel "
            f"({pire.retard_jours} j) — avancer son lancement ou libérer une machine plus rapide."
        )

    artifact = {
        "kind": "planning",
        "genere_le": maintenant.isoformat(),
        "nb_of": len(plan),
        "nb_retards": len(en_retard),
        "plan": [
            {
                "of": a.of_numero,
                "article": a.article,
                "restant": a.restant,
                "machine": a.machine_code,
                "ligne": a.ligne,
                "debut_estime": a.debut_estime.isoformat(),
                "fin_estimee": a.fin_estimee.isoformat(),
                "echeance": a.echeance.isoformat() if a.echeance else None,
                "retard_jours": a.retard_jours,
                "en_cours": a.en_cours,
            }
            for a in plan
        ],
        "non_affectables": [of.numero for of in non_affectables],
    }
    return "\n".join(lignes), artifact
