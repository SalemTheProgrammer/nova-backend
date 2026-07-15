"""One-off additive migration séparant l'échéance client du créneau ordonnancé.

Avant : `date_fin_prevue` (DATE) portait l'ÉCHÉANCE client — c'est elle que les
règles EDD / ratio critique / marge trient et contre laquelle le retard se
mesure. L'ordonnanceur n'avait donc aucun champ où écrire son résultat.

Après :
  - `date_echeance`    (DATE)     : l'échéance client, ENTRÉE de l'ordonnancement ;
  - `date_debut_prevue` (DATETIME) : début projeté, SORTIE de l'ordonnanceur ;
  - `date_fin_prevue`   (DATETIME) : fin projetée, SORTIE de l'ordonnanceur.

La migration déplace donc l'ancienne valeur de `date_fin_prevue` vers
`date_echeance`, puis vide les deux champs de créneau : tant que Nova n'a pas
ordonnancé, « pas encore planifié » est la vérité.

SQLite est typé dynamiquement : la déclaration DATE/DATETIME n'est qu'une
affinité, seul le FORMAT stocké compte. Une valeur « 2026-07-14 » relue par le
type DateTime de SQLAlchemy lèverait une erreur de parsing — on purge donc les
valeurs jour-seul (longueur 10) résiduelles.

Idempotente : chaque étape est bornée par une condition qui devient fausse une
fois appliquée. Une valeur de créneau écrite par l'ordonnanceur (longueur 19+)
n'est jamais touchée, même si le script est relancé.

Run from the backend directory:
    python -m scripts.migrate_ordonnancement
"""
from __future__ import annotations

import sqlite3

from app.core.config import get_settings

TABLE = "ordre_fabrication"


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
        print(f"Migration ordonnancement sur {db_path} :")

        _add_column_if_missing(cur, TABLE, "date_echeance", "date_echeance DATE")

        # L'ancienne date_fin_prevue EST l'échéance : on la déplace, et on libère
        # le créneau dans le même UPDATE — la ligne migrée sort du WHERE ensuite.
        cur.execute(
            f"""
            UPDATE {TABLE}
               SET date_echeance     = date(date_fin_prevue),
                   date_fin_prevue   = NULL,
                   date_debut_prevue = NULL
             WHERE date_echeance IS NULL
               AND date_fin_prevue IS NOT NULL
            """
        )
        print(f"  -> {cur.rowcount} echeance(s) deplacee(s) vers date_echeance")

        # Reliquats jour-seul (ex. OF semé avec un début mais sans fin) : illisibles
        # par le type DateTime, et de toute façon non produits par une règle.
        for colonne in ("date_debut_prevue", "date_fin_prevue"):
            cur.execute(
                f"UPDATE {TABLE} SET {colonne} = NULL WHERE length({colonne}) = 10"
            )
            if cur.rowcount:
                print(f"  -> {cur.rowcount} valeur(s) jour-seul purgee(s) dans {colonne}")

        conn.commit()
    finally:
        conn.close()

    print("Colonnes migrées. Synchronisation du schéma (create_all)...")
    from app.db.session import init_db

    init_db()
    print("Migration terminée.")


if __name__ == "__main__":
    main()
