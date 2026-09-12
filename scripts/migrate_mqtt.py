"""One-off migration vers l'ingestion Sparkplug B réelle (MQTT).

1. Ajoute `sparkplug_device.last_values` et `sparkplug_tag_mapping.description`
   (ALTER TABLE ADD COLUMN, non destructif).
2. Supprime les devices créés par l'ancien bus Sparkplug en mémoire
   (`broker_url = 'internal'`) et leurs règles : ils ne correspondent à aucun
   automate réel. Les vrais automates se ré-enregistrent à leur naissance.
3. Nettoie les valeurs d'énumération retirées : transformation `DELTA_RATE`
   (jamais implémentée) → `DIRECT` ; règles `ARRET_DETECTE` (jamais alimentées)
   supprimées.
4. Homologation article ↔ ligne : lancer un OF exige désormais que l'article
   soit homologué sur la ligne. Chaque article est rattaché aux lignes où il a
   déjà un OF, pour ne pas bloquer l'existant.

Sûr à relancer. Run from the backend directory:
    python -m scripts.migrate_mqtt
"""
from __future__ import annotations

import sqlite3

from app.core.config import get_settings
from app.db.session import init_db


def _colonnes(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite:///"):
        raise SystemExit("Migration écrite pour SQLite (DATABASE_URL=sqlite:///...).")
    chemin = settings.database_url.split("sqlite:///", 1)[1]

    con = sqlite3.connect(chemin)
    try:
        cur = con.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rapport: list[str] = []

        if "sparkplug_device" in tables and "last_values" not in _colonnes(cur, "sparkplug_device"):
            cur.execute("ALTER TABLE sparkplug_device ADD COLUMN last_values JSON")
            rapport.append("colonne sparkplug_device.last_values ajoutée")
        if "sparkplug_tag_mapping" in tables and "description" not in _colonnes(
            cur, "sparkplug_tag_mapping"
        ):
            cur.execute("ALTER TABLE sparkplug_tag_mapping ADD COLUMN description VARCHAR(255)")
            rapport.append("colonne sparkplug_tag_mapping.description ajoutée")

        if "sparkplug_device" in tables:
            ids = [r[0] for r in cur.execute(
                "SELECT id FROM sparkplug_device WHERE broker_url = 'internal'"
            )]
            cur.executemany("DELETE FROM sparkplug_tag_mapping WHERE device_id = ?", [(i,) for i in ids])
            cur.executemany("DELETE FROM sparkplug_device WHERE id = ?", [(i,) for i in ids])
            if ids:
                rapport.append(f"{len(ids)} device(s) de l'ancien bus en mémoire supprimé(s)")

        if "sparkplug_tag_mapping" in tables:
            cur.execute(
                "UPDATE sparkplug_tag_mapping SET transformation = 'DIRECT' "
                "WHERE transformation = 'DELTA_RATE'"
            )
            if cur.rowcount:
                rapport.append(f"{cur.rowcount} règle(s) DELTA_RATE → DIRECT")
            cur.execute("DELETE FROM sparkplug_tag_mapping WHERE target_kpi = 'ARRET_DETECTE'")
            if cur.rowcount:
                rapport.append(f"{cur.rowcount} règle(s) ARRET_DETECTE supprimée(s)")

        if {"ligne_article", "ordre_fabrication"} <= tables:
            cur.execute(
                "INSERT OR IGNORE INTO ligne_article (ligne_production_id, article_id) "
                "SELECT DISTINCT ligne_production_id, article_id FROM ordre_fabrication "
                "WHERE ligne_production_id IS NOT NULL"
            )
            if cur.rowcount:
                rapport.append(f"{cur.rowcount} homologation(s) article/ligne déduite(s) des OF")

        con.commit()
    finally:
        con.close()

    init_db()  # tables manquantes (base neuve ou partielle)
    print("Migration MQTT :", "; ".join(rapport) if rapport else "rien à faire.")


if __name__ == "__main__":
    main()
