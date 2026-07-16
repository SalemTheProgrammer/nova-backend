"""Rapport de production (fin d'équipe) généré depuis les données réelles."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DowntimeEvent
from app.services import cost_service, dashboard_service


def _fmt_duree(secondes: float) -> str:
    minutes = int(secondes // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def _cout_arrets_fenetre(db: Session, depuis: datetime, jusqua: datetime) -> float:
    """Coût total estimé (TND) des arrêts recoupant la fenêtre du rapport."""
    arrets = db.execute(
        select(DowntimeEvent).where(
            (DowntimeEvent.end_time.is_(None)) | (DowntimeEvent.end_time >= depuis),
            DowntimeEvent.start_time <= jusqua,
        )
    ).scalars().all()
    total = 0.0
    for arret in arrets:
        debut = max(arret.start_time, depuis)
        fin = min(arret.end_time or jusqua, jusqua)
        duree_min = max(0.0, (fin - debut).total_seconds() / 60)
        if duree_min <= 0:
            continue
        total += cost_service.estimer_cout_arret(arret.machine, duree_min).total_tnd
    return total


def generer_rapport(db: Session) -> tuple[str, dict]:
    """Rapport markdown + données structurées, sur la fenêtre standard (8 h)."""
    r = dashboard_service.construire_resume(db)
    maintenant = datetime.now()
    jusqua_utc = datetime.utcnow()
    cout_arrets = _cout_arrets_fenetre(
        db, jusqua_utc - dashboard_service.FENETRE_DEFAUT, jusqua_utc
    )

    taux_rebut = 0.0
    total = float(r.quantite_bonne + r.quantite_rejetee)
    if total > 0:
        taux_rebut = float(r.quantite_rejetee) / total

    lignes_md = [
        f"# Rapport de production — {maintenant.strftime('%d/%m/%Y %H:%M')}",
        "",
        "## Performance (AFNOR NF E60-182)",
        f"- **TRS global : {float(r.trs_global) * 100:.0f}%**"
        f" (Disponibilité {float(r.disponibilite) * 100:.0f}% ×"
        f" Performance {float(r.performance) * 100:.0f}% ×"
        f" Qualité {float(r.qualite) * 100:.0f}%)",
        "",
        "## Production",
        f"- Bonnes pièces : {r.quantite_bonne} | Rebuts : {r.quantite_rejetee}"
        f" (taux de rebut {taux_rebut * 100:.1f}%)",
        f"- Cadence actuelle : {r.cadence_actuelle_par_min} u/min",
        f"- Ordres de fabrication actifs : {r.ordres_actifs}"
        f" (cible cumulée {r.production_cible})",
        "",
        "## Fiabilité",
        f"- Machines en marche : {r.machines_en_marche}/{r.machines_total}",
        f"- Pannes/arrêts : {r.nb_pannes} | Temps d'arrêt cumulé : "
        + _fmt_duree(float(r.temps_arret_total_s)),
        f"- Coût estimé des arrêts : {cost_service.format_tnd(cout_arrets)} "
        "(production non réalisée + immobilisation machines)",
        f"- MTTR : {_fmt_duree(float(r.mttr_s))} | MTBF : {_fmt_duree(float(r.mtbf_s))}",
    ]

    if r.top_causes_arret:
        lignes_md += ["", "## Top causes d'arrêt"]
        for c in r.top_causes_arret:
            lignes_md.append(
                f"- {c.cause.replace('_', ' ').capitalize()} : {_fmt_duree(float(c.duree_s))}"
            )

    if r.alertes_actives:
        lignes_md += ["", "## Alertes non résolues"]
        for a in r.alertes_actives[:8]:
            lignes_md.append(f"- [{a.severity.value}] {a.message}")

    markdown = "\n".join(lignes_md)
    artifact = {
        "kind": "rapport",
        "genere_le": maintenant.isoformat(),
        "markdown": markdown,
        "trs_global": float(r.trs_global),
        "quantite_bonne": str(r.quantite_bonne),
        "quantite_rejetee": str(r.quantite_rejetee),
        "taux_rebut": round(taux_rebut, 4),
        "cout_arrets_tnd": round(cout_arrets, 0),
        "nb_pannes": r.nb_pannes,
        "mttr_s": str(r.mttr_s),
        "mtbf_s": str(r.mtbf_s),
    }
    return markdown, artifact
