"""One-off additive migration: prix d'achat unitaire des matières premières.

Ajoute `matiere_premiere.prix_unitaire_tnd` (SQLite `ALTER TABLE ADD COLUMN`,
sûr et non destructif) puis backfille les lignes existantes avec les mêmes
valeurs que `db/seed.py` et `scripts/seed_demo_rich.py`, par code MP. Sert au
chiffrage du coût matières d'un OF (`app/services/cost_service.calculer_cout_of`).
Sans backfill, les MP déjà en base auraient un prix NULL (coût non chiffrable).

Sûr à relancer : la colonne n'est ajoutée qu'une fois, et le backfill ne touche
que les lignes dont le prix est encore NULL.

Run from the backend directory:
    python -m scripts.migrate_cout_mp
"""
from __future__ import annotations

import sqlite3

from app.core.config import get_settings

# Mêmes valeurs que db/seed.py et scripts/seed_demo_rich.py (TND / unité de `unite`).
PRIX_PAR_CODE: dict[str, float] = {
    "MP-API": 0.1500,
    "MP-AMIDON": 0.0060,
    "MP-STEAR": 0.0250,
    "MP-BLISTER": 0.0900,
    "MP-ETUI": 0.1400,
    "MP-PARA-API": 0.1500,
    "MP-IBU": 0.1200,
    "MP-ASPIRINE": 0.0400,
    "MP-AMOX": 0.6000,
    "MP-LACTOSE": 0.0040,
    "MP-CELLULOSE": 0.0060,
    "MP-GELCAPS": 0.0700,
    "MP-SIROP-BASE": 2.5000,
    "MP-AROME-FRAISE": 0.0500,
    "MP-FLACON100": 0.3000,
    "MP-BLISTER-R": 0.0900,
    "MP-ETUI-R": 0.1400,
    "MP-VITC": 0.0800,
    "MP-TALC": 0.0100,
    "MP-GELULE-VIDE": 0.0600,
    "MP-SACHET-ALU": 0.0700,
    "MP-COLORANT": 0.0300,
    "MP-CREME-BASE": 12.0000,
}


def _existing_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        raise SystemExit("Cette migration est prévue pour SQLite uniquement.")
    db_path = settings.database_url.split("///")[-1]

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        print(f"Migration additive sur {db_path} :")

        if "prix_unitaire_tnd" not in _existing_columns(cur, "matiere_premiere"):
            print("  + matiere_premiere.prix_unitaire_tnd")
            cur.execute("ALTER TABLE matiere_premiere ADD COLUMN prix_unitaire_tnd NUMERIC(12, 4)")
        else:
            print("  = matiere_premiere.prix_unitaire_tnd (déjà présent)")

        for code, prix in PRIX_PAR_CODE.items():
            cur.execute(
                "UPDATE matiere_premiere SET prix_unitaire_tnd = ? "
                "WHERE code = ? AND prix_unitaire_tnd IS NULL",
                (prix, code),
            )
            if cur.rowcount:
                print(f"  ~ {code} -> {prix} TND")

        cur.execute("SELECT code FROM matiere_premiere WHERE prix_unitaire_tnd IS NULL")
        restants = [r[0] for r in cur.fetchall()]
        if restants:
            print(f"  ! MP sans prix connu (coût matières non chiffrable) : {', '.join(restants)}")

        conn.commit()
    finally:
        conn.close()

    print("Migration terminée.")


if __name__ == "__main__":
    main()
