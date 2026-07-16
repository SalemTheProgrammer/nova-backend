"""One-off additive migration: colonnes ajoutees a `machine`, `agent_proposal`
et `article` depuis le dernier deploy.

Ajoute (SQLite `ALTER TABLE ADD COLUMN`, sur et non destructif) :
- `machine.cout_horaire` (NOT NULL, defaut 150.00 TND/heure)
- `agent_proposal.decideur` (NOT NULL, defaut 'operateur')
- `agent_proposal.execution_auto_at` (nullable)
- `article.valeur_unitaire` (nullable)

SQLite n'autorise `ADD COLUMN ... NOT NULL` que si une valeur par defaut est
fournie (sert au backfill des lignes existantes) ; les deux colonnes NOT NULL
recoivent donc un DEFAULT explicite dans le ALTER TABLE lui-meme.

Sur a relancer : chaque colonne n'est ajoutee qu'une fois (verifie via
PRAGMA table_info avant chaque ALTER).

Run from the backend directory:
    python -m scripts.migrate_decideur_couts
"""
from __future__ import annotations

import sqlite3

from app.core.config import get_settings


def _existing_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        raise SystemExit("Cette migration est prevue pour SQLite uniquement.")
    db_path = settings.database_url.split("///")[-1]

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        print(f"Migration additive sur {db_path} :")

        if "cout_horaire" not in _existing_columns(cur, "machine"):
            print("  + machine.cout_horaire")
            cur.execute(
                "ALTER TABLE machine ADD COLUMN cout_horaire NUMERIC(10, 2) "
                "NOT NULL DEFAULT 150.00"
            )
        else:
            print("  = machine.cout_horaire (deja present)")

        if "decideur" not in _existing_columns(cur, "agent_proposal"):
            print("  + agent_proposal.decideur")
            cur.execute(
                "ALTER TABLE agent_proposal ADD COLUMN decideur VARCHAR(20) "
                "NOT NULL DEFAULT 'operateur'"
            )
        else:
            print("  = agent_proposal.decideur (deja present)")

        if "execution_auto_at" not in _existing_columns(cur, "agent_proposal"):
            print("  + agent_proposal.execution_auto_at")
            cur.execute(
                "ALTER TABLE agent_proposal ADD COLUMN execution_auto_at DATETIME"
            )
        else:
            print("  = agent_proposal.execution_auto_at (deja present)")

        if "valeur_unitaire" not in _existing_columns(cur, "article"):
            print("  + article.valeur_unitaire")
            cur.execute(
                "ALTER TABLE article ADD COLUMN valeur_unitaire NUMERIC(10, 3)"
            )
        else:
            print("  = article.valeur_unitaire (deja present)")

        conn.commit()
    finally:
        conn.close()

    print("Migration terminee.")


if __name__ == "__main__":
    main()
