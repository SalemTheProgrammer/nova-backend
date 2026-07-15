"""Remet le backlog en configuration de demo d'ordonnancement « 2 semaines ».

Trois effets, dans cet ordre :

1. Tout OF ouvert (BROUILLON, EN_COURS, PLANIFIE) repasse PLANIFIE : le moteur
   d'ordonnancement ne sequence que les PLANIFIE, on veut le voir deplacer TOUT
   le backlog. Les OF TERMINE / ANNULE ne sont pas touches (historique).
2. Chaque OF ouvert recoit une echeance client repartie sur les 14 prochains
   jours (jamais aujourd'hui : une echeance J+0 est deja quasi perdue et ecrase
   les autres signaux). La repartition est deterministe (ordre des ids) : deux
   executions donnent le meme resultat.
3. Les SORTIES de l'ordonnanceur (date_debut_prevue / date_fin_prevue) et les
   dates reelles sont effacees : la prochaine application d'une regle par Nova
   ecrit des creneaux visibles (flash ambre sur la page Ordres), au lieu de
   retomber sur d'anciennes dates.

Ajoute aussi un complement d'OF (marqueur `MARQUEUR`) pour etoffer le backlog :
echeances serrees et lointaines, articles repetes (SETUP / SETUP_EDD ont des
series a grouper), quantites contrastees (SPT / LPT / COVERT se departagent).
Idempotent sur ce point : les complements ne sont crees qu'une fois.

Run from the backend directory:
    python -m scripts.reset_backlog_planifie
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db.session import session_scope
from app.models import Article, LigneProduction, Machine, OrdreFabrication
from app.models.enums import StatutOF, Unite
from app.services.manufacturing import get_nomenclature_active
from app.services.numbering import generer_numero_lot_produit, generer_numero_of

MARQUEUR = "seed_backlog_2sem"

OUVERTS = (StatutOF.BROUILLON, StatutOF.EN_COURS, StatutOF.PLANIFIE)

# Jours d'echeance (depuis aujourd'hui) distribues aux OF existants, dans
# l'ordre des ids. Des doublons volontaires (2, 5, 9) : deux OF qui visent le
# meme jour se disputent la capacite, c'est la que les regles divergent.
JOURS_ECHEANCE = [1, 2, 2, 3, 4, 5, 5, 6, 7, 8, 9, 9, 10, 11, 12, 13, 14]

# Complement de backlog : (article, quantite, jours d'echeance, code ligne ou None).
SPECS: list[tuple[str, int, int, str | None]] = [
    ("PARA500", 1200, 2, "LIGNE-COMP-01"),
    ("IBU400", 300, 3, None),
    ("ASPIRINE500", 700, 5, "LIGNE-COMP-03"),
    ("PARA500", 250, 7, None),              # meme article que J+2 : serie a grouper
    ("MULTIVIT-GEL", 900, 8, None),
    ("PARA-SIROP", 450, 10, "LIGNE-COMP-03"),
    ("IBU400", 1100, 12, None),             # gros volume loin : LPT le remonte
    ("SACHET-EFFER", 150, 14, None),
]


def redater(db) -> int:
    """Repasse tous les OF ouverts en PLANIFIE, echeances sur 14 jours."""
    aujourdhui = date.today()
    ofs = (
        db.execute(
            select(OrdreFabrication)
            .where(OrdreFabrication.statut.in_(OUVERTS))
            .order_by(OrdreFabrication.id)
        )
        .scalars()
        .all()
    )
    for i, of in enumerate(ofs):
        jours = JOURS_ECHEANCE[i % len(JOURS_ECHEANCE)]
        of.statut = StatutOF.PLANIFIE
        of.date_echeance = aujourdhui + timedelta(days=jours)
        of.date_debut_prevue = None
        of.date_fin_prevue = None
        # Un OF PLANIFIE n'a pas encore demarre : les dates reelles heritees
        # d'un ancien statut EN_COURS n'ont plus de sens.
        of.date_debut_reelle = None
        of.date_fin_reelle = None
        print(f"  ~ {of.numero} -> PLANIFIE, echeance J+{jours} ({of.date_echeance})")

    # Les machines qui travaillaient sur un OF repasse PLANIFIE le lachent :
    # sinon la page Machines afficherait un OF « actif » que le planning
    # considere comme non demarre.
    ids = {of.id for of in ofs}
    machines = db.execute(
        select(Machine).where(Machine.ordre_fabrication_id.in_(ids))
    ).scalars().all()
    for m in machines:
        print(f"  ~ machine {m.code} liberee (travaillait sur OF #{m.ordre_fabrication_id})")
        m.ordre_fabrication_id = None
    return len(ofs)


def completer(db) -> int:
    """Ajoute les OF du complement, une seule fois (marqueur)."""
    deja = db.execute(
        select(OrdreFabrication).where(OrdreFabrication.cree_par == MARQUEUR)
    ).scalars().first()
    if deja is not None:
        print(f"  complement deja seme ({deja.numero}) : rien a ajouter.")
        return 0

    aujourdhui = date.today()
    articles = {a.code: a for a in db.execute(select(Article)).scalars()}
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
        echeance = aujourdhui + timedelta(days=jours)
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
                db, code_article=article.code, jour=echeance
            ),
            cree_par=MARQUEUR,
        )
        db.add(of)
        db.flush()  # numero suivant correct pour l'OF d'apres
        crees += 1
        print(f"  + {of.numero} {code_article} x{quantite} echeance=J+{jours} ligne={code_ligne or 'libre'}")
    return crees


def main() -> None:
    with session_scope() as db:
        nb = redater(db)
        crees = completer(db)
    print(f"{nb} OF repasses PLANIFIE et redates sur 14 jours, {crees} OF ajoutes.")


if __name__ == "__main__":
    main()
