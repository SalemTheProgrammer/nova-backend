"""Remplace le parc machines démo (M-01/02/03) par 6 machines avec un historique
d'événements récent (8 h) donnant des TRS variés et crédibles pour le tableau
de bord — exécution unique, pas idempotente.

Run from the backend directory:
    python -m scripts.reset_machines_demo
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.db.session import SessionLocal
from app.models import Alert, AgentProposal, DowntimeEvent, Machine, QualityEvent
from app.models.enums import CauseArret, StatutMachine, TypeEvenementQualite

FENETRE_S = 8 * 3600


def _quantites(*, cycle_s: float, trs: float, tq: float, do: float) -> tuple[int, int, float]:
    """Reconstruit (bonnes, rejets, duree_arret_s) pour atteindre le TRS visé,
    avec les mêmes formules que trs_service._calculer (TR = fenêtre 8h)."""
    tp = trs / (tq * do)
    tr = float(FENETRE_S)
    tf = do * tr
    tn = tp * tf
    tu = tq * tn
    bonnes = round(tu / cycle_s)
    total = round(tn / cycle_s)
    rejets = max(0, total - bonnes)
    duree_arret = tr - tf
    return bonnes, rejets, duree_arret


MACHINES = [
    dict(
        code="M-01", nom="Comprimeuse rotative 1", ligne_id=1, cycle=4.0,
        statut=StatutMachine.MARCHE, trs=0.90, tq=0.97, do=1.0,
    ),
    dict(
        code="M-02", nom="Blistrière 1", ligne_id=1, cycle=4.0,
        statut=StatutMachine.MARCHE, trs=0.82, tq=0.95, do=1.0,
    ),
    dict(
        code="M-03", nom="Encartonneuse 1", ligne_id=1, cycle=4.0,
        statut=StatutMachine.ARRET, trs=0.55, tq=0.90, do=0.85,
    ),
    dict(
        code="M-04", nom="Compteuse-remplisseuse 1", ligne_id=2, cycle=3.0,
        statut=StatutMachine.MARCHE, trs=0.68, tq=0.93, do=0.92,
    ),
    dict(
        code="M-05", nom="Étiqueteuse 1", ligne_id=2, cycle=3.0,
        statut=StatutMachine.PANNE, trs=0.30, tq=0.80, do=0.625,
    ),
    dict(
        code="M-06", nom="Peseuse de contrôle 1", ligne_id=2, cycle=3.0,
        statut=StatutMachine.MARCHE, trs=0.95, tq=0.98, do=1.0,
    ),
]


def run() -> None:
    db = SessionLocal()
    try:
        anciens = db.query(Machine).filter(Machine.code.in_(["M-01", "M-02", "M-03"])).all()
        anciens_ids = [m.id for m in anciens]
        if anciens_ids:
            db.query(AgentProposal).filter(AgentProposal.machine_id.in_(anciens_ids)).delete(
                synchronize_session=False
            )
            db.query(Alert).filter(Alert.machine_id.in_(anciens_ids)).delete(
                synchronize_session=False
            )
            for m in anciens:
                db.delete(m)
            db.commit()
            print(f"Anciennes machines supprimées : {[m.code for m in anciens]}")

        jusqua = datetime.utcnow()
        for spec in MACHINES:
            bonnes, rejets, duree_arret = _quantites(
                cycle_s=spec["cycle"], trs=spec["trs"], tq=spec["tq"], do=spec["do"]
            )
            machine = Machine(
                code=spec["code"],
                nom=spec["nom"],
                ligne_production_id=spec["ligne_id"],
                statut=spec["statut"],
                temps_cycle_cible_s=Decimal(str(spec["cycle"])),
                dernier_evenement_at=jusqua,
            )
            db.add(machine)
            db.flush()

            db.add(
                QualityEvent(
                    machine_id=machine.id,
                    type=TypeEvenementQualite.BONNE,
                    quantite=bonnes,
                    created_at=jusqua - timedelta(minutes=5),
                )
            )
            if rejets > 0:
                db.add(
                    QualityEvent(
                        machine_id=machine.id,
                        type=TypeEvenementQualite.REBUT,
                        quantite=rejets,
                        created_at=jusqua - timedelta(minutes=4),
                    )
                )
            if duree_arret > 1:
                actif = spec["statut"] == StatutMachine.PANNE
                if actif:
                    # Panne toujours en cours : durée = maintenant - début.
                    start = jusqua - timedelta(seconds=duree_arret)
                    end = None
                else:
                    # Arrêt déjà résolu, terminé il y a 10 min — la durée exacte
                    # tient entre start et end, peu importe leur position dans
                    # la fenêtre de 8h tant que les deux y restent.
                    end = jusqua - timedelta(minutes=10)
                    start = end - timedelta(seconds=duree_arret)
                db.add(
                    DowntimeEvent(
                        machine_id=machine.id,
                        cause=CauseArret.PANNE_MECANIQUE if actif else CauseArret.MICRO_ARRET,
                        start_time=start,
                        end_time=end,
                    )
                )
            print(f"{spec['code']} {spec['nom']}: bonnes={bonnes} rejets={rejets} arret_s={duree_arret:.0f}")

        db.commit()
        print("6 nouvelles machines créées.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
