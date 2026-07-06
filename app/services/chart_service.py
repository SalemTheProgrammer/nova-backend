"""Catalogue de données pour les graphiques dynamiques de l'agent.

L'agent choisit un dataset nommé (jamais de SQL libre) et le service renvoie une
spec de graphique générique que le frontend rend avec Recharts :

    {
      "kind": "chart",
      "chart_type": "line" | "bar" | "area" | "pie",
      "title": str,
      "unit": str | None,
      "series": [{"name": str, "data": [{"x": str, "y": float}]}],
    }

Chaque dataset définit son type de graphique par défaut ; l'agent peut le
surcharger (ex. « en camembert »).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DowntimeEvent,
    LigneProduction,
    LotMatierePremiere,
    Machine,
    MatierePremiere,
    QualityEvent,
)
from app.models.enums import StatutLot, TypeEvenementQualite
from app.services import trs_service

CHART_TYPES = ("line", "bar", "area", "pie")

# dataset -> (type par défaut, description pour le docstring de l'outil)
DATASETS: dict[str, tuple[str, str]] = {
    "trs_horaire": ("line", "TRS heure par heure d'une machine ou d'une ligne"),
    "production_horaire": ("area", "unités bonnes vs rebuts par heure"),
    "pareto_arrets": ("bar", "minutes d'arrêt cumulées par cause (Pareto)"),
    "rebuts_par_cause": ("pie", "répartition des rebuts par cause"),
    "trs_machines": ("bar", "TRS courant de chaque machine (comparaison)"),
    "stock_matieres": ("bar", "stock restant par matière première"),
}


def _machines_du_scope(db: Session, scope: str, id: int | None) -> list[Machine]:
    """Les machines concernées : une machine, celles d'une ligne, ou toute l'usine."""
    stmt = select(Machine).where(Machine.actif.is_(True))
    if scope == "machine" and id is not None:
        stmt = stmt.where(Machine.id == id)
    elif scope == "ligne" and id is not None:
        stmt = stmt.where(Machine.ligne_production_id == id)
    return list(db.execute(stmt.order_by(Machine.code)).scalars())


def _libelle_scope(db: Session, scope: str, id: int | None) -> str:
    if scope == "machine" and id is not None:
        m = db.get(Machine, id)
        return m.code if m else f"machine {id}"
    if scope == "ligne" and id is not None:
        l = db.get(LigneProduction, id)
        return l.designation if l else f"ligne {id}"
    return "usine"


def _heures(periode_heures: int) -> list[tuple[datetime, datetime, str]]:
    """Fenêtres horaires (début, fin, étiquette) couvrant les N dernières heures."""
    fin = datetime.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    fenetres = []
    for i in range(periode_heures, 0, -1):
        debut = fin - timedelta(hours=i)
        fenetres.append((debut, debut + timedelta(hours=1), f"{debut:%H}h"))
    return fenetres


def _dataset_trs_horaire(
    db: Session, scope: str, id: int | None, periode_heures: int
) -> tuple[str, list[dict]]:
    machines = _machines_du_scope(db, scope, id)
    points = []
    for debut, fin, label in _heures(periode_heures):
        resultats = [
            trs_service.calculer_trs_machine(db, m, depuis=debut, jusqua=fin)
            for m in machines
            if m.temps_cycle_cible_s
        ]
        trs = (
            float(sum(r.trs for r in resultats) / len(resultats)) * 100 if resultats else 0.0
        )
        points.append({"x": label, "y": round(trs, 1)})
    libelle = _libelle_scope(db, scope, id)
    return f"TRS horaire — {libelle}", [{"name": "TRS %", "data": points}]


def _dataset_production_horaire(
    db: Session, scope: str, id: int | None, periode_heures: int
) -> tuple[str, list[dict]]:
    machines = _machines_du_scope(db, scope, id)
    machine_ids = [m.id for m in machines]
    depuis = datetime.utcnow() - timedelta(hours=periode_heures)
    events = list(
        db.execute(
            select(QualityEvent).where(
                QualityEvent.machine_id.in_(machine_ids),
                QualityEvent.created_at >= depuis,
            )
        ).scalars()
    )
    bonnes: list[dict] = []
    rebuts: list[dict] = []
    for debut, fin, label in _heures(periode_heures):
        dans_fenetre = [e for e in events if debut <= e.created_at < fin]
        bonnes.append(
            {
                "x": label,
                "y": sum(
                    e.quantite for e in dans_fenetre if e.type == TypeEvenementQualite.BONNE
                ),
            }
        )
        rebuts.append(
            {
                "x": label,
                "y": sum(
                    e.quantite for e in dans_fenetre if e.type == TypeEvenementQualite.REBUT
                ),
            }
        )
    libelle = _libelle_scope(db, scope, id)
    return (
        f"Production horaire — {libelle}",
        [{"name": "Bonnes", "data": bonnes}, {"name": "Rebuts", "data": rebuts}],
    )


def _dataset_pareto_arrets(
    db: Session, scope: str, id: int | None, periode_heures: int
) -> tuple[str, list[dict]]:
    machines = _machines_du_scope(db, scope, id)
    machine_ids = [m.id for m in machines]
    jusqua = datetime.utcnow()
    depuis = jusqua - timedelta(hours=periode_heures)
    arrets = list(
        db.execute(
            select(DowntimeEvent).where(
                DowntimeEvent.machine_id.in_(machine_ids),
                DowntimeEvent.start_time < jusqua,
            )
        ).scalars()
    )
    minutes_par_cause: dict[str, float] = {}
    for a in arrets:
        debut = max(a.start_time, depuis)
        fin = min(a.end_time or jusqua, jusqua)
        if fin <= debut:
            continue
        cause = a.cause.value.replace("_", " ").capitalize()
        minutes_par_cause[cause] = (
            minutes_par_cause.get(cause, 0.0) + (fin - debut).total_seconds() / 60
        )
    points = [
        {"x": cause, "y": round(minutes, 1)}
        for cause, minutes in sorted(
            minutes_par_cause.items(), key=lambda kv: kv[1], reverse=True
        )
    ]
    libelle = _libelle_scope(db, scope, id)
    return f"Pareto des arrêts ({periode_heures} h) — {libelle}", [
        {"name": "Minutes d'arrêt", "data": points}
    ]


def _dataset_rebuts_par_cause(
    db: Session, scope: str, id: int | None, periode_heures: int
) -> tuple[str, list[dict]]:
    machines = _machines_du_scope(db, scope, id)
    machine_ids = [m.id for m in machines]
    depuis = datetime.utcnow() - timedelta(hours=periode_heures)
    events = list(
        db.execute(
            select(QualityEvent).where(
                QualityEvent.machine_id.in_(machine_ids),
                QualityEvent.type == TypeEvenementQualite.REBUT,
                QualityEvent.created_at >= depuis,
            )
        ).scalars()
    )
    par_cause: dict[str, int] = {}
    for e in events:
        cause = (e.cause.value if e.cause else "AUTRE").replace("_", " ").capitalize()
        par_cause[cause] = par_cause.get(cause, 0) + e.quantite
    points = [
        {"x": cause, "y": qte}
        for cause, qte in sorted(par_cause.items(), key=lambda kv: kv[1], reverse=True)
    ]
    libelle = _libelle_scope(db, scope, id)
    return f"Rebuts par cause ({periode_heures} h) — {libelle}", [
        {"name": "Rebuts", "data": points}
    ]


def _dataset_trs_machines(
    db: Session, scope: str, id: int | None, periode_heures: int
) -> tuple[str, list[dict]]:
    machines = _machines_du_scope(db, scope if scope == "ligne" else "usine", id)
    points = []
    for m in machines:
        if not m.temps_cycle_cible_s:
            continue
        r = trs_service.calculer_trs_machine(db, m)
        points.append({"x": m.code, "y": round(float(r.trs) * 100, 1)})
    libelle = _libelle_scope(db, scope, id)
    return f"TRS par machine — {libelle}", [{"name": "TRS %", "data": points}]


def _dataset_stock_matieres(
    db: Session, scope: str, id: int | None, periode_heures: int
) -> tuple[str, list[dict]]:
    matieres = list(db.execute(select(MatierePremiere)).scalars())
    lots = list(
        db.execute(
            select(LotMatierePremiere).where(
                LotMatierePremiere.statut == StatutLot.DISPONIBLE
            )
        ).scalars()
    )
    par_mp: dict[int, float] = {}
    for lot in lots:
        par_mp[lot.matiere_premiere_id] = par_mp.get(lot.matiere_premiere_id, 0.0) + float(
            lot.quantite_restante
        )
    points = [
        {"x": mp.code, "y": round(par_mp.get(mp.id, 0.0), 2)}
        for mp in sorted(matieres, key=lambda m: m.code)
    ]
    return "Stock restant par matière première", [{"name": "Stock", "data": points}]


_BUILDERS = {
    "trs_horaire": _dataset_trs_horaire,
    "production_horaire": _dataset_production_horaire,
    "pareto_arrets": _dataset_pareto_arrets,
    "rebuts_par_cause": _dataset_rebuts_par_cause,
    "trs_machines": _dataset_trs_machines,
    "stock_matieres": _dataset_stock_matieres,
}


def construire_graphique(
    db: Session,
    *,
    dataset: str,
    scope: str = "usine",
    id: int | None = None,
    periode_heures: int = 8,
    chart_type: str | None = None,
) -> dict:
    """Construit la spec de graphique pour un dataset du catalogue.

    Lève ValueError si le dataset est inconnu — l'outil transforme ça en message
    lisible pour l'agent.
    """
    if dataset not in _BUILDERS:
        raise ValueError(
            f"Dataset inconnu : {dataset!r}. Choix : {', '.join(sorted(_BUILDERS))}."
        )
    periode_heures = max(1, min(48, periode_heures))
    titre, series = _BUILDERS[dataset](db, scope, id, periode_heures)
    type_defaut = DATASETS[dataset][0]
    type_final = chart_type if chart_type in CHART_TYPES else type_defaut
    return {
        "kind": "chart",
        "chart_type": type_final,
        "title": titre,
        "unit": "%" if dataset in ("trs_horaire", "trs_machines") else None,
        "series": series,
    }
