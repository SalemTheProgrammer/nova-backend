"""One-off additive migration for the MES/SCADA layer.

Adds new nullable/defaulted columns to existing tables (SQLite `ALTER TABLE ADD COLUMN`,
safe and non-destructive), then calls `init_db()` so `create_all` adds the brand-new
MES tables (machine, machine_event, downtime_event, quality_event, maintenance_event,
alert). Safe to run repeatedly: each ALTER is guarded by a column-existence check.

Run from the backend directory:
    python -m scripts.migrate_mes
"""
from __future__ import annotations

import sqlite3

from app.core.config import get_settings


def _existing_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _add_column_if_missing(cur: sqlite3.Cursor, table: str, column: str, ddl: str) -> None:
    if column not in _existing_columns(cur, table):
        print(f"  + {table}.{column}")
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    else:
        print(f"  = {table}.{column} (déjà présent)")


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        raise SystemExit("Cette migration est prévue pour SQLite uniquement.")
    db_path = settings.database_url.split("///")[-1]

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        print(f"Migration additive sur {db_path} :")

        _add_column_if_missing(cur, "article", "temps_cycle_cible_s", "temps_cycle_cible_s NUMERIC(10, 2)")
        _add_column_if_missing(
            cur, "ligne_production", "taux_charge", "taux_charge NUMERIC(5, 4) DEFAULT 1.0 NOT NULL"
        )
        _add_column_if_missing(
            cur,
            "ligne_production",
            "taux_engagement",
            "taux_engagement NUMERIC(5, 4) DEFAULT 1.0 NOT NULL",
        )
        _add_column_if_missing(
            cur, "ordre_fabrication", "date_debut_reelle", "date_debut_reelle DATETIME"
        )
        _add_column_if_missing(
            cur,
            "ordre_fabrication",
            "quantite_bonne",
            "quantite_bonne NUMERIC(18, 4) DEFAULT 0 NOT NULL",
        )
        _add_column_if_missing(
            cur,
            "ordre_fabrication",
            "quantite_rejetee",
            "quantite_rejetee NUMERIC(18, 4) DEFAULT 0 NOT NULL",
        )
        conn.commit()
    finally:
        conn.close()

    print("Colonnes migrées. Création des nouvelles tables MES (create_all)...")
    from app.db.session import init_db

    init_db()
    print("Migration terminée.")


if __name__ == "__main__":
    main()
