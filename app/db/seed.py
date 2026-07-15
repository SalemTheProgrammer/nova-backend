"""Seed the database with a Saiph-style pharma manufacturing example.

Run from the backend directory:
    python -m app.db.seed
Idempotent: skips seeding if the example article already exists.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.db.session import SessionLocal, init_db
from app.models import (
    Article,
    Fournisseur,
    LigneProduction,
    LotMatierePremiere,
    Machine,
    MatierePremiere,
    Nomenclature,
    NomenclatureLigne,
)
from app.models.enums import StatutLot, StatutMachine, TypeArticle, Unite


def _seed_machines_si_absentes(db) -> None:
    """Ajoute les machines démo même si le reste du seed a déjà tourné (idempotent)."""
    existing = db.query(Article).filter_by(code="PARA500").first()
    if existing is None:
        return
    if existing.temps_cycle_cible_s is None:
        existing.temps_cycle_cible_s = Decimal("4.0")
    ligne = db.query(LigneProduction).filter_by(code="LIGNE-COMP-01").first()
    if ligne is None or db.query(Machine).filter_by(code="M-01").first():
        return
    db.add_all(
        [
            Machine(
                code="M-01", nom="Comprimeuse rotative 1",
                ligne_production_id=ligne.id, statut=StatutMachine.ARRET,
                temps_cycle_cible_s=existing.temps_cycle_cible_s,
            ),
            Machine(
                code="M-02", nom="Blistrière 1",
                ligne_production_id=ligne.id, statut=StatutMachine.ARRET,
                temps_cycle_cible_s=existing.temps_cycle_cible_s,
            ),
        ]
    )
    db.commit()
    print("Machines démo ajoutées : M-01, M-02.")


def _seed_compatibilite_ligne(db) -> None:
    """Répare les anciennes bases où PARA500 n'était relié à aucune ligne."""
    article = db.query(Article).filter_by(code="PARA500").first()
    ligne = db.query(LigneProduction).filter_by(code="LIGNE-COMP-01").first()
    if article is None or ligne is None or article in ligne.articles:
        return
    ligne.articles.append(article)
    db.commit()
    print("Compatibilité ajoutée : PARA500 -> LIGNE-COMP-01.")


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(Article).filter_by(code="PARA500").first():
            print("Seed déjà présent — rien à faire.")
            _seed_machines_si_absentes(db)
            _seed_compatibilite_ligne(db)
            return

        fournisseur = Fournisseur(
            code="FRN-001", nom="Chimie Tunisie SA", contact="contact@chimie.tn"
        )
        db.add(fournisseur)
        db.flush()

        ligne = LigneProduction(
            code="LIGNE-COMP-01", designation="Ligne comprimés 1", actif=True
        )
        db.add(ligne)

        # --- Matières premières ---
        mps = {
            "API": MatierePremiere(
                code="MP-API", designation="Paracétamol (principe actif)",
                unite=Unite.G, seuil_alerte=Decimal("500"),
            ),
            "AMIDON": MatierePremiere(
                code="MP-AMIDON", designation="Amidon de maïs",
                unite=Unite.G, seuil_alerte=Decimal("300"),
            ),
            "STEAR": MatierePremiere(
                code="MP-STEAR", designation="Stéarate de magnésium",
                unite=Unite.G, seuil_alerte=Decimal("100"),
            ),
            "BLISTER": MatierePremiere(
                code="MP-BLISTER", designation="Blister PVC/Alu (plaquette x16)",
                unite=Unite.UN, seuil_alerte=Decimal("1000"),
            ),
            "ETUI": MatierePremiere(
                code="MP-ETUI", designation="Étui carton + notice",
                unite=Unite.UN, seuil_alerte=Decimal("1000"),
            ),
        }
        for mp in mps.values():
            db.add(mp)
        db.flush()

        # --- Lots de MP (avec péremptions pour démontrer le FEFO) ---
        lots = [
            # Deux lots d'API: le plus ancien expire en premier (FEFO le consomme d'abord)
            LotMatierePremiere(
                numero_lot="API-2024-01", matiere_premiere_id=mps["API"].id,
                fournisseur_id=fournisseur.id, quantite_initiale=Decimal("1000"),
                quantite_restante=Decimal("1000"), date_reception=date(2024, 1, 15),
                date_peremption=date(2026, 12, 31), statut=StatutLot.DISPONIBLE,
            ),
            LotMatierePremiere(
                numero_lot="API-2024-02", matiere_premiere_id=mps["API"].id,
                fournisseur_id=fournisseur.id, quantite_initiale=Decimal("5000"),
                quantite_restante=Decimal("5000"), date_reception=date(2024, 6, 10),
                date_peremption=date(2027, 6, 30), statut=StatutLot.DISPONIBLE,
            ),
            LotMatierePremiere(
                numero_lot="AMIDON-2024-01", matiere_premiere_id=mps["AMIDON"].id,
                fournisseur_id=fournisseur.id, quantite_initiale=Decimal("2000"),
                quantite_restante=Decimal("2000"), date_reception=date(2024, 3, 1),
                date_peremption=date(2027, 1, 31), statut=StatutLot.DISPONIBLE,
            ),
            LotMatierePremiere(
                numero_lot="STEAR-2024-01", matiere_premiere_id=mps["STEAR"].id,
                fournisseur_id=fournisseur.id, quantite_initiale=Decimal("500"),
                quantite_restante=Decimal("500"), date_reception=date(2024, 3, 1),
                date_peremption=date(2027, 3, 31), statut=StatutLot.DISPONIBLE,
            ),
            LotMatierePremiere(
                numero_lot="BLISTER-2024-01", matiere_premiere_id=mps["BLISTER"].id,
                fournisseur_id=fournisseur.id, quantite_initiale=Decimal("10000"),
                quantite_restante=Decimal("10000"), date_reception=date(2024, 2, 1),
                date_peremption=None, statut=StatutLot.DISPONIBLE,
            ),
            LotMatierePremiere(
                numero_lot="ETUI-2024-01", matiere_premiere_id=mps["ETUI"].id,
                fournisseur_id=fournisseur.id, quantite_initiale=Decimal("10000"),
                quantite_restante=Decimal("10000"), date_reception=date(2024, 2, 1),
                date_peremption=None, statut=StatutLot.DISPONIBLE,
            ),
        ]
        db.add_all(lots)

        # --- Article + nomenclature (formule par boîte produite) ---
        article = Article(
            code="PARA500",
            designation="Paracétamol 500 mg - Comprimés (boîte de 16)",
            unite=Unite.UN, type=TypeArticle.PF, actif=True,
            temps_cycle_cible_s=Decimal("4.0"),
        )
        db.add(article)
        db.flush()
        ligne.articles.append(article)

        nomenclature = Nomenclature(article_id=article.id, version=1, actif=True)
        db.add(nomenclature)
        db.flush()

        # Quantités par boîte produite
        formule = [
            (mps["API"].id, Decimal("8")),       # 8 g d'API par boîte
            (mps["AMIDON"].id, Decimal("2")),    # 2 g d'amidon
            (mps["STEAR"].id, Decimal("0.2")),   # 0,2 g de stéarate
            (mps["BLISTER"].id, Decimal("1")),   # 1 blister
            (mps["ETUI"].id, Decimal("1")),      # 1 étui
        ]
        for mp_id, qte in formule:
            db.add(
                NomenclatureLigne(
                    nomenclature_id=nomenclature.id,
                    matiere_premiere_id=mp_id,
                    quantite_par_unite=qte,
                )
            )

        # --- Machines (simulateur SCADA) ---
        machines = [
            Machine(
                code="M-01", nom="Comprimeuse rotative 1",
                ligne_production_id=ligne.id, statut=StatutMachine.ARRET,
                temps_cycle_cible_s=article.temps_cycle_cible_s,
            ),
            Machine(
                code="M-02", nom="Blistrière 1",
                ligne_production_id=ligne.id, statut=StatutMachine.ARRET,
                temps_cycle_cible_s=article.temps_cycle_cible_s,
            ),
        ]
        db.add_all(machines)

        db.commit()
        print(
            "Seed créé : article PARA500 + 5 MP (6 lots) + 1 ligne + formule + 2 machines "
            "(M-01, M-02)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
