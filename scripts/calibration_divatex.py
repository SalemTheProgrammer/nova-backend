"""Calibration du rendement : affiche, par jour et par ligne de confection,
sortie, objectif, effectif, minutes produites, présence et rendement.

Usage : python -m scripts.calibration_divatex 2026-08-26 2026-08-27 2026-08-28
(DIVATEX_URL doit pointer vers la base DIVA, en lecture seule.)
"""
from __future__ import annotations

import sys
from datetime import date

from app.integrations.divatex.production import production_lignes


def _pct(valeur: float | None) -> str:
    return "-" if valeur is None else f"{valeur * 100:.1f}%"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not argv:
        print("Usage : python -m scripts.calibration_divatex AAAA-MM-JJ [AAAA-MM-JJ ...]")
        return 2
    for texte in argv:
        jour = date.fromisoformat(texte)
        print(f"== {jour.isoformat()} ==")
        print(
            f"{'ligne':<10}{'sortie':>8}{'objectif':>10}{'date obj.':>12}"
            f"{'effectif':>10}{'min prod.':>11}{'présence':>10}{'rendement':>11}"
        )
        for l in production_lignes(jour):
            if l.ligne.nature != "confection":
                continue
            objectif = "-" if l.objectif is None else str(l.objectif)
            date_obj = l.objectif_date.isoformat() if l.objectif_date else "-"
            presence = "estimée" if l.presence_estimee else "réelle"
            print(
                f"{l.ligne.code:<10}{l.sortie:>8}{objectif:>10}{date_obj:>12}"
                f"{l.effectif:>10}{l.minutes_produites:>11.0f}{presence:>10}"
                f"{_pct(l.rendement):>11}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
