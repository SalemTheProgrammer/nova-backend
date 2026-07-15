"""Backlog supplementaire pour la demo d'ordonnancement : 8 OF PLANIFIE varies.

Le jeu de base laisse ~5 OF planifiables : trop peu pour que les 13 regles de
dispatching se departagent visiblement. Ce script ajoute un mix concu pour ca :
echeances serrees (retard garanti), lointaines (jusqu'a J+14), absentes ;
petites et grosses quantites ; OF avec et sans ligne imposee ; articles repetes
pour que SETUP / SETUP_EDD aient des changements de serie a economiser.

Insertion directe (comme seed_demo_rich) : PAS de consommation de stock MP —
ces OF servent au sequencement, pas a la genealogie matiere.

Idempotent : ne fait rien si des OF marques `cree_par = MARQUEUR` existent deja.

Run from the backend directory:
    python -m scripts.seed_ordonnancement_extra
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db.session import session_scope
from app.models import Article, LigneProduction, OrdreFabrication
from app.models.enums import StatutOF, Unite
from app.services.manufacturing import get_nomenclature_active
from app.services.numbering import generer_numero_lot_produit, generer_numero_of

MARQUEUR = "seed_ordonnancement_extra"

# (article, quantite, jours d'echeance depuis aujourd'hui (None = sans), code ligne (None = au choix))
SPECS: list[tuple[str, int, int | None, str | None]] = [
    ("PARA500", 1500, 1, "LIGNE-COMP-01"),   # gros volume, echeance demain : tension
    ("PARA500", 200, 6, "LIGNE-COMP-01"),    # meme article : SETUP peut grouper
    ("IBU400", 800, 3, "LIGNE-COMP-03"),
    ("ASPIRINE500", 400, 2, None),           # echeance serree : retard probable
    ("MULTIVIT-GEL", 600, 9, None),          # large : les regles peuvent la retarder
    ("PARA-SIROP", 350, 4, "LIGNE-COMP-03"),
    ("CREME-DERM", 900, None, "LIGNE-COND-04"),  # sans echeance : fin de file attendue
    ("SACHET-EFFER", 250, 14, None),         # bout de l'horizon 2 semaines
]


def main() -> None:
    aujourdhui = date.today()
    with session_scope() as db:
        deja = db.execute(
            select(OrdreFabrication).where(OrdreFabrication.cree_par == MARQUEUR)
        ).scalars().first()
        if deja is not None:
            print(f"Deja seme ({deja.numero} porte le marqueur {MARQUEUR!r}) : rien a faire.")
            return

        articles = {
            a.code: a for a in db.execute(select(Article)).scalars()
        }
        lignes = {
            l.code: l
            for l in db.execute(
                select(LigneProduction).where(LigneProduction.actif.is_(True))
            ).scalars()
        }

        crees = 0
        for code_article, quantite, jours, code_ligne in SPECS:
            article = articles.get(code_article)
            if article is None:
                print(f"  ! article inconnu {code_article} — ignore")
                continue
            try:
                nomenclature = get_nomenclature_active(db, article.id)
            except Exception:  # noqa: BLE001 — pas de nomenclature : OF impossible
                print(f"  ! pas de nomenclature active pour {code_article} — ignore")
                continue
            ligne = lignes.get(code_ligne) if code_ligne else None
            echeance = aujourdhui + timedelta(days=jours) if jours is not None else None
            of = OrdreFabrication(
                numero=generer_numero_of(db),
                article_id=article.id,
                nomenclature_id=nomenclature.id,
                quantite_planifiee=Decimal(quantite),
                unite=Unite.UN,
                ligne_production_id=ligne.id if ligne else None,
                statut=StatutOF.PLANIFIE,
                date_echeance=echeance,
                numero_lot_produit=generer_numero_lot_produit(
                    db, code_article=article.code, jour=echeance or aujourdhui
                ),
                cree_par=MARQUEUR,
            )
            db.add(of)
            db.flush()  # numero suivant correct pour l'OF d'apres
            crees += 1
            print(
                f"  + {of.numero} {code_article} x{quantite} "
                f"echeance={echeance or 'aucune'} ligne={code_ligne or 'libre'}"
            )
        print(f"{crees} OF ajoutes au backlog.")


if __name__ == "__main__":
    main()
