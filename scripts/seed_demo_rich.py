"""Seed démo « riche » : beaucoup d'articles, deux lignes contrastées (stock
quasi illimité vs stock à sec), fournisseurs, lots, nomenclatures, OF et
généalogie matière — le référentiel et le stock dont le MES a besoin.

Aucune donnée d'exécution inventée : les machines sont créées à l'arrêt, sans
événement, arrêt, alerte ni proposition. Leur activité arrive par la télémétrie
de leurs automates (usine simulée `simulator/` ou équipements réels), comme en
production. Les OF déjà entamés sont repris en PLANIFIE avec leur reliquat.

Purement additif : ne touche à aucune ligne/article/machine/OF existant.
Idempotent : si l'article IBU400 existe déjà, ne fait rien.

Run from the backend directory:
    python -m scripts.seed_demo_rich
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.db.session import SessionLocal, init_db
from app.services.numbering import generer_numero_lot_produit
from app.models import (
    Article,
    Fournisseur,
    LigneProduction,
    LotMatierePremiere,
    Machine,
    MatierePremiere,
    MouvementStock,
    Nomenclature,
    NomenclatureLigne,
    OFConsommationMP,
    OrdreFabrication,
)
from app.models.enums import StatutLot, StatutOF, TypeArticle, TypeMouvement, Unite


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(Article).filter_by(code="IBU400").first():
            print("Seed démo riche déjà présent — rien à faire.")
            return

        maintenant = datetime.utcnow()
        aujourdhui = maintenant.date()

        # ------------------------------------------------------------------
        # Fournisseurs
        # ------------------------------------------------------------------
        fournisseurs = {
            "excip": Fournisseur(
                code="FRN-002", nom="Pharma Excipients International",
                contact="commandes@pharmaexcip.com",
            ),
            "pack": Fournisseur(
                code="FRN-003", nom="Packaging Plus Tunisie",
                contact="contact@packagingplus.tn",
            ),
            "api": Fournisseur(
                code="FRN-004", nom="API Import Maghreb",
                contact="ventes@apimaghreb.com",
            ),
            "verre": Fournisseur(
                code="FRN-005", nom="Verrerie Médicale Tunisie", contact=None,
            ),
            "carton": Fournisseur(
                code="FRN-006", nom="Cartonnage Industriel SA",
                contact="commercial@cartonnage-industriel.tn",
            ),
        }
        for f in fournisseurs.values():
            db.add(f)
        db.flush()

        # ------------------------------------------------------------------
        # Deux nouvelles lignes : une à stock quasi-illimité, une à sec.
        # ------------------------------------------------------------------
        ligne_riche = LigneProduction(
            code="LIGNE-COMP-03", designation="Ligne comprimés 3 (stock abondant)",
            actif=True,
        )
        ligne_pauvre = LigneProduction(
            code="LIGNE-COND-04", designation="Ligne conditionnement 4 (stock critique)",
            actif=True,
        )
        db.add_all([ligne_riche, ligne_pauvre])
        db.flush()

        # ------------------------------------------------------------------
        # Matières premières — lot riche (quantités énormes, tout DISPONIBLE)
        # ------------------------------------------------------------------
        mp_riche = {
            "PARA-API": MatierePremiere(
                code="MP-PARA-API", designation="Paracétamol (principe actif, lot 2)",
                unite=Unite.G, seuil_alerte=Decimal("50000"),
                prix_unitaire_tnd=Decimal("0.1500"),
            ),
            "IBU": MatierePremiere(
                code="MP-IBU", designation="Ibuprofène (principe actif)",
                unite=Unite.G, seuil_alerte=Decimal("50000"),
                prix_unitaire_tnd=Decimal("0.1200"),
            ),
            "ASPIRINE": MatierePremiere(
                code="MP-ASPIRINE", designation="Acide acétylsalicylique (principe actif)",
                unite=Unite.G, seuil_alerte=Decimal("50000"),
                prix_unitaire_tnd=Decimal("0.0400"),
            ),
            "AMOX": MatierePremiere(
                code="MP-AMOX", designation="Amoxicilline trihydratée (principe actif)",
                unite=Unite.G, seuil_alerte=Decimal("30000"),
                prix_unitaire_tnd=Decimal("0.6000"),
            ),
            "LACTOSE": MatierePremiere(
                code="MP-LACTOSE", designation="Lactose monohydraté (excipient)",
                unite=Unite.G, seuil_alerte=Decimal("100000"),
                prix_unitaire_tnd=Decimal("0.0040"),
            ),
            "CELLULOSE": MatierePremiere(
                code="MP-CELLULOSE", designation="Cellulose microcristalline (excipient)",
                unite=Unite.G, seuil_alerte=Decimal("80000"),
                prix_unitaire_tnd=Decimal("0.0060"),
            ),
            "GELCAPS": MatierePremiere(
                code="MP-GELCAPS", designation="Capsules gélatine vides",
                unite=Unite.UN, seuil_alerte=Decimal("20000"),
                prix_unitaire_tnd=Decimal("0.0700"),
            ),
            "SIROP-BASE": MatierePremiere(
                code="MP-SIROP-BASE", designation="Sirop de base sucré",
                unite=Unite.L, seuil_alerte=Decimal("200"),
                prix_unitaire_tnd=Decimal("2.5000"),
            ),
            "AROME": MatierePremiere(
                code="MP-AROME-FRAISE", designation="Arôme fraise",
                unite=Unite.ML, seuil_alerte=Decimal("5000"),
                prix_unitaire_tnd=Decimal("0.0500"),
            ),
            "FLACON": MatierePremiere(
                code="MP-FLACON100", designation="Flacon PET 100 ml",
                unite=Unite.UN, seuil_alerte=Decimal("5000"),
                prix_unitaire_tnd=Decimal("0.3000"),
            ),
            "BLISTER-R": MatierePremiere(
                code="MP-BLISTER-R", designation="Blister PVC/Alu (plaquette)",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
                prix_unitaire_tnd=Decimal("0.0900"),
            ),
            "ETUI-R": MatierePremiere(
                code="MP-ETUI-R", designation="Étui carton + notice",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
                prix_unitaire_tnd=Decimal("0.1400"),
            ),
        }
        for mp in mp_riche.values():
            db.add(mp)
        db.flush()

        # Deux gros lots par MP riche — stock volontairement énorme ("quasi
        # illimité") pour tester le comportement quand la matière n'est
        # jamais le facteur limitant.
        lots_riche_specs = [
            ("PARA-API", 800_000, 900),
            ("PARA-API", 600_000, 950),
            ("IBU", 800_000, 900),
            ("IBU", 1_200_000, 950),
            ("ASPIRINE", 700_000, 900),
            ("ASPIRINE", 500_000, 950),
            ("AMOX", 400_000, 700),
            ("AMOX", 300_000, 750),
            ("LACTOSE", 2_000_000, 1000),
            ("LACTOSE", 1_500_000, 1050),
            ("CELLULOSE", 1_000_000, 1000),
            ("CELLULOSE", 900_000, 1050),
            ("GELCAPS", 500_000, 800),
            ("GELCAPS", 300_000, 850),
            ("SIROP-BASE", 5_000, 600),
            ("SIROP-BASE", 3_000, 650),
            ("AROME", 100_000, 700),
            ("FLACON", 200_000, 800),
            ("FLACON", 150_000, 850),
            ("BLISTER-R", 300_000, 800),
            ("BLISTER-R", 250_000, 850),
            ("ETUI-R", 300_000, 800),
            ("ETUI-R", 250_000, 850),
        ]
        lots_riche: dict[str, list[LotMatierePremiere]] = {}
        fournisseur_cycle = [fournisseurs["api"], fournisseurs["excip"], fournisseurs["pack"],
                             fournisseurs["carton"], fournisseurs["verre"]]
        for i, (cle, qte, peremption_j) in enumerate(lots_riche_specs):
            frn = fournisseur_cycle[i % len(fournisseur_cycle)]
            lot = LotMatierePremiere(
                numero_lot=f"{mp_riche[cle].code}-2026-{i:02d}",
                matiere_premiere_id=mp_riche[cle].id,
                fournisseur_id=frn.id,
                quantite_initiale=Decimal(qte),
                quantite_restante=Decimal(qte),
                date_reception=aujourdhui - timedelta(days=30 + i),
                date_peremption=aujourdhui + timedelta(days=peremption_j),
                statut=StatutLot.DISPONIBLE,
            )
            db.add(lot)
            lots_riche.setdefault(cle, []).append(lot)
        db.flush()

        mouvements = []
        for cle_lots in lots_riche.values():
            for lot in cle_lots:
                mouvements.append(
                    MouvementStock(
                        type_mouvement=TypeMouvement.ENTREE,
                        matiere_premiere_id=lot.matiere_premiere_id,
                        lot_matiere_premiere_id=lot.id,
                        quantite=lot.quantite_initiale,
                        reference_type="reception",
                        commentaire=f"Réception lot {lot.numero_lot}",
                        date_mouvement=datetime.combine(lot.date_reception, datetime.min.time()),
                    )
                )

        # ------------------------------------------------------------------
        # Matières premières — lot pauvre (à sec : épuisé / bloqué / périmé)
        # ------------------------------------------------------------------
        mp_pauvre = {
            "VITC": MatierePremiere(
                code="MP-VITC", designation="Vitamine C (acide ascorbique)",
                unite=Unite.G, seuil_alerte=Decimal("20000"),
                prix_unitaire_tnd=Decimal("0.0800"),
            ),
            "TALC": MatierePremiere(
                code="MP-TALC", designation="Talc pharmaceutique",
                unite=Unite.G, seuil_alerte=Decimal("15000"),
                prix_unitaire_tnd=Decimal("0.0100"),
            ),
            "GELULE-VIDE": MatierePremiere(
                code="MP-GELULE-VIDE", designation="Gélules vides",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
                prix_unitaire_tnd=Decimal("0.0600"),
            ),
            "SACHET": MatierePremiere(
                code="MP-SACHET-ALU", designation="Sachet aluminium thermosoudable",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
                prix_unitaire_tnd=Decimal("0.0700"),
            ),
            "COLORANT": MatierePremiere(
                code="MP-COLORANT", designation="Colorant alimentaire",
                unite=Unite.ML, seuil_alerte=Decimal("2000"),
                prix_unitaire_tnd=Decimal("0.0300"),
            ),
            "CREME-BASE": MatierePremiere(
                code="MP-CREME-BASE", designation="Base crème dermatologique",
                unite=Unite.KG, seuil_alerte=Decimal("50"),
                prix_unitaire_tnd=Decimal("12.0000"),
            ),
        }
        for mp in mp_pauvre.values():
            db.add(mp)
        db.flush()

        lots_pauvre = {
            "VITC": LotMatierePremiere(
                numero_lot="MP-VITC-2026-01", matiere_premiere_id=mp_pauvre["VITC"].id,
                fournisseur_id=fournisseurs["api"].id,
                quantite_initiale=Decimal("20000"), quantite_restante=Decimal("350"),
                date_reception=aujourdhui - timedelta(days=90),
                date_peremption=aujourdhui + timedelta(days=400),
                statut=StatutLot.DISPONIBLE,
            ),
            "TALC": LotMatierePremiere(
                numero_lot="MP-TALC-2026-01", matiere_premiere_id=mp_pauvre["TALC"].id,
                fournisseur_id=fournisseurs["excip"].id,
                quantite_initiale=Decimal("15000"), quantite_restante=Decimal("0"),
                date_reception=aujourdhui - timedelta(days=120),
                date_peremption=aujourdhui + timedelta(days=300),
                statut=StatutLot.EPUISE,
            ),
            "GELULE-VIDE": LotMatierePremiere(
                numero_lot="MP-GELULE-VIDE-2026-01", matiere_premiere_id=mp_pauvre["GELULE-VIDE"].id,
                fournisseur_id=fournisseurs["excip"].id,
                quantite_initiale=Decimal("8000"), quantite_restante=Decimal("8000"),
                date_reception=aujourdhui - timedelta(days=10),
                date_peremption=aujourdhui + timedelta(days=500),
                statut=StatutLot.BLOQUE,
            ),
            "SACHET": LotMatierePremiere(
                numero_lot="MP-SACHET-2025-04", matiere_premiere_id=mp_pauvre["SACHET"].id,
                fournisseur_id=fournisseurs["pack"].id,
                quantite_initiale=Decimal("12000"), quantite_restante=Decimal("4200"),
                date_reception=aujourdhui - timedelta(days=400),
                date_peremption=aujourdhui - timedelta(days=15),
                statut=StatutLot.PERIME,
            ),
            "COLORANT": LotMatierePremiere(
                numero_lot="MP-COLORANT-2026-01", matiere_premiere_id=mp_pauvre["COLORANT"].id,
                fournisseur_id=fournisseurs["excip"].id,
                quantite_initiale=Decimal("2000"), quantite_restante=Decimal("15"),
                date_reception=aujourdhui - timedelta(days=60),
                date_peremption=aujourdhui + timedelta(days=200),
                statut=StatutLot.DISPONIBLE,
            ),
            "CREME-BASE": LotMatierePremiere(
                numero_lot="MP-CREME-BASE-2026-01", matiere_premiere_id=mp_pauvre["CREME-BASE"].id,
                fournisseur_id=fournisseurs["excip"].id,
                quantite_initiale=Decimal("50"), quantite_restante=Decimal("0"),
                date_reception=aujourdhui - timedelta(days=200),
                date_peremption=aujourdhui + timedelta(days=100),
                statut=StatutLot.EPUISE,
            ),
        }
        for lot in lots_pauvre.values():
            db.add(lot)
        db.flush()

        mouvements.append(
            MouvementStock(
                type_mouvement=TypeMouvement.ENTREE,
                matiere_premiere_id=lots_pauvre["VITC"].matiere_premiere_id,
                lot_matiere_premiere_id=lots_pauvre["VITC"].id,
                quantite=Decimal("20000"), reference_type="reception",
                commentaire="Réception lot MP-VITC-2026-01",
                date_mouvement=datetime.combine(lots_pauvre["VITC"].date_reception, datetime.min.time()),
            )
        )
        mouvements.append(
            MouvementStock(
                type_mouvement=TypeMouvement.SORTIE,
                matiere_premiere_id=lots_pauvre["VITC"].matiere_premiere_id,
                lot_matiere_premiere_id=lots_pauvre["VITC"].id,
                quantite=Decimal("19650"), reference_type="consommation",
                commentaire="Consommation cumulée sur plusieurs OF",
                date_mouvement=maintenant - timedelta(days=5),
            )
        )
        mouvements.append(
            MouvementStock(
                type_mouvement=TypeMouvement.AJUSTEMENT,
                matiere_premiere_id=lots_pauvre["TALC"].matiere_premiere_id,
                lot_matiere_premiere_id=lots_pauvre["TALC"].id,
                quantite=Decimal("-15000"), reference_type="rupture_stock",
                commentaire="Lot totalement consommé — réapprovisionnement en attente.",
                date_mouvement=maintenant - timedelta(days=3),
            )
        )
        mouvements.append(
            MouvementStock(
                type_mouvement=TypeMouvement.AJUSTEMENT,
                matiere_premiere_id=lots_pauvre["GELULE-VIDE"].matiere_premiere_id,
                lot_matiere_premiere_id=lots_pauvre["GELULE-VIDE"].id,
                quantite=Decimal("0"), reference_type="controle_qualite",
                commentaire="Lot bloqué en quarantaine — contrôle qualité en cours.",
                date_mouvement=maintenant - timedelta(days=8),
            )
        )
        mouvements.append(
            MouvementStock(
                type_mouvement=TypeMouvement.AJUSTEMENT,
                matiere_premiere_id=lots_pauvre["SACHET"].matiere_premiere_id,
                lot_matiere_premiere_id=lots_pauvre["SACHET"].id,
                quantite=Decimal("0"), reference_type="peremption",
                commentaire="Lot périmé, à détruire — bloqué pour la production.",
                date_mouvement=maintenant - timedelta(days=15),
            )
        )
        mouvements.append(
            MouvementStock(
                type_mouvement=TypeMouvement.AJUSTEMENT,
                matiere_premiere_id=lots_pauvre["CREME-BASE"].matiere_premiere_id,
                lot_matiere_premiere_id=lots_pauvre["CREME-BASE"].id,
                quantite=Decimal("-50"), reference_type="rupture_stock",
                commentaire="Lot totalement consommé — rupture fournisseur.",
                date_mouvement=maintenant - timedelta(days=2),
            )
        )
        db.add_all(mouvements)

        # ------------------------------------------------------------------
        # Articles + nomenclatures — ligne riche
        # ------------------------------------------------------------------
        def _creer_article_bom(*, code, designation, cycle, formule, ligne):
            article = Article(
                code=code, designation=designation, unite=Unite.UN, type=TypeArticle.PF,
                actif=True, temps_cycle_cible_s=Decimal(str(cycle)),
            )
            db.add(article)
            db.flush()
            nomenclature = Nomenclature(article_id=article.id, version=1, actif=True)
            db.add(nomenclature)
            db.flush()
            for mp_id, qte in formule:
                db.add(
                    NomenclatureLigne(
                        nomenclature_id=nomenclature.id, matiere_premiere_id=mp_id,
                        quantite_par_unite=Decimal(str(qte)),
                    )
                )
            ligne.articles.append(article)
            return article, nomenclature

        articles_riche = {}
        nomenclatures_riche = {}

        articles_riche["IBU400"], nomenclatures_riche["IBU400"] = _creer_article_bom(
            code="IBU400", designation="Ibuprofène 400 mg - Comprimés (boîte de 20)", cycle=3.5,
            formule=[
                (mp_riche["IBU"].id, "8"), (mp_riche["LACTOSE"].id, "3"),
                (mp_riche["BLISTER-R"].id, "1"), (mp_riche["ETUI-R"].id, "1"),
            ], ligne=ligne_riche,
        )
        articles_riche["PARA1000"], nomenclatures_riche["PARA1000"] = _creer_article_bom(
            code="PARA1000", designation="Paracétamol 1000 mg - Comprimés (boîte de 8)", cycle=3.5,
            formule=[
                (mp_riche["PARA-API"].id, "10"), (mp_riche["CELLULOSE"].id, "2"),
                (mp_riche["BLISTER-R"].id, "1"), (mp_riche["ETUI-R"].id, "1"),
            ], ligne=ligne_riche,
        )
        articles_riche["ASPIRINE500"], nomenclatures_riche["ASPIRINE500"] = _creer_article_bom(
            code="ASPIRINE500", designation="Aspirine 500 mg - Comprimés (boîte de 20)", cycle=3.5,
            formule=[
                (mp_riche["ASPIRINE"].id, "9"), (mp_riche["LACTOSE"].id, "2.5"),
                (mp_riche["BLISTER-R"].id, "1"), (mp_riche["ETUI-R"].id, "1"),
            ], ligne=ligne_riche,
        )
        articles_riche["AMOX500"], nomenclatures_riche["AMOX500"] = _creer_article_bom(
            code="AMOX500", designation="Amoxicilline 500 mg - Gélules (boîte de 12)", cycle=3.2,
            formule=[
                (mp_riche["AMOX"].id, "6"), (mp_riche["GELCAPS"].id, "12"),
                (mp_riche["ETUI-R"].id, "1"),
            ], ligne=ligne_riche,
        )
        articles_riche["MULTIVIT-GEL"], nomenclatures_riche["MULTIVIT-GEL"] = _creer_article_bom(
            code="MULTIVIT-GEL", designation="Multivitamines - Gélules (boîte de 30)", cycle=3.0,
            formule=[
                (mp_riche["GELCAPS"].id, "30"), (mp_riche["LACTOSE"].id, "5"),
                (mp_riche["ETUI-R"].id, "1"),
            ], ligne=ligne_riche,
        )
        articles_riche["VITC-SIROP"], nomenclatures_riche["VITC-SIROP"] = _creer_article_bom(
            code="VITC-SIROP", designation="Vitamine C - Sirop enfant (flacon 100 ml)", cycle=2.8,
            formule=[
                (mp_riche["SIROP-BASE"].id, "0.1"), (mp_riche["AROME"].id, "2"),
                (mp_riche["FLACON"].id, "1"),
            ], ligne=ligne_riche,
        )
        articles_riche["TOUX-SIROP"], nomenclatures_riche["TOUX-SIROP"] = _creer_article_bom(
            code="TOUX-SIROP", designation="Sirop antitussif (flacon 150 ml)", cycle=2.8,
            formule=[
                (mp_riche["SIROP-BASE"].id, "0.15"), (mp_riche["AROME"].id, "3"),
                (mp_riche["FLACON"].id, "1"),
            ], ligne=ligne_riche,
        )
        articles_riche["PARA-SIROP"], nomenclatures_riche["PARA-SIROP"] = _creer_article_bom(
            code="PARA-SIROP", designation="Paracétamol - Sirop enfant (flacon 100 ml)", cycle=2.8,
            formule=[
                (mp_riche["SIROP-BASE"].id, "0.1"), (mp_riche["PARA-API"].id, "2"),
                (mp_riche["FLACON"].id, "1"),
            ], ligne=ligne_riche,
        )

        # ------------------------------------------------------------------
        # Articles + nomenclatures — ligne pauvre (matière quasi absente)
        # ------------------------------------------------------------------
        articles_pauvre = {}
        nomenclatures_pauvre = {}

        articles_pauvre["VITC1000"], nomenclatures_pauvre["VITC1000"] = _creer_article_bom(
            code="VITC1000", designation="Vitamine C 1000 mg - Comprimés effervescents (tube de 10)",
            cycle=3.0,
            formule=[
                (mp_pauvre["VITC"].id, "1"), (mp_pauvre["SACHET"].id, "1"),
            ], ligne=ligne_pauvre,
        )
        articles_pauvre["TALC-POUDRE"], nomenclatures_pauvre["TALC-POUDRE"] = _creer_article_bom(
            code="TALC-POUDRE", designation="Talc pharmaceutique - Poudre (boîte 100 g)", cycle=4.0,
            formule=[
                (mp_pauvre["TALC"].id, "100"), (mp_pauvre["SACHET"].id, "1"),
            ], ligne=ligne_pauvre,
        )
        articles_pauvre["GELULE-VIDE-BTE"], nomenclatures_pauvre["GELULE-VIDE-BTE"] = _creer_article_bom(
            code="GELULE-VIDE-BTE", designation="Gélules vides - Boîte de 100", cycle=3.5,
            formule=[
                (mp_pauvre["GELULE-VIDE"].id, "100"),
            ], ligne=ligne_pauvre,
        )
        articles_pauvre["CREME-DERM"], nomenclatures_pauvre["CREME-DERM"] = _creer_article_bom(
            code="CREME-DERM", designation="Crème dermatologique (tube 30 g)", cycle=4.5,
            formule=[
                (mp_pauvre["CREME-BASE"].id, "0.03"), (mp_pauvre["COLORANT"].id, "1"),
            ], ligne=ligne_pauvre,
        )
        articles_pauvre["SACHET-EFFER"], nomenclatures_pauvre["SACHET-EFFER"] = _creer_article_bom(
            code="SACHET-EFFER", designation="Sachet effervescent multivitaminé", cycle=3.2,
            formule=[
                (mp_pauvre["VITC"].id, "0.5"), (mp_pauvre["SACHET"].id, "1"),
                (mp_pauvre["COLORANT"].id, "0.5"),
            ], ligne=ligne_pauvre,
        )
        articles_pauvre["POUDRE-BEBE"], nomenclatures_pauvre["POUDRE-BEBE"] = _creer_article_bom(
            code="POUDRE-BEBE", designation="Poudre pour bébé - Boîte 200 g", cycle=4.0,
            formule=[
                (mp_pauvre["TALC"].id, "200"), (mp_pauvre["SACHET"].id, "1"),
            ], ligne=ligne_pauvre,
        )
        db.flush()

        # Valeur commerciale unitaire (TND) — sert au chiffrage en dinars des
        # arrêts et scénarios what-if (voir services/cost_service.py).
        valeurs_unitaires = {
            "IBU400": "4.800", "PARA1000": "3.200", "ASPIRINE500": "4.100",
            "AMOX500": "7.500", "MULTIVIT-GEL": "9.800", "VITC-SIROP": "6.400",
            "TOUX-SIROP": "7.200", "PARA-SIROP": "5.900", "VITC1000": "5.500",
            "TALC-POUDRE": "3.000", "GELULE-VIDE-BTE": "2.500",
            "CREME-DERM": "8.500", "SACHET-EFFER": "1.900", "POUDRE-BEBE": "4.300",
        }
        for article in list(articles_riche.values()) + list(articles_pauvre.values()):
            valeur = valeurs_unitaires.get(article.code)
            article.valeur_unitaire = Decimal(valeur) if valeur else Decimal("4.000")
        db.flush()

        # ------------------------------------------------------------------
        # Machines — 2 sur la ligne riche, 2 sur la pauvre. Créées à l'arrêt et
        # sans historique : leur activité arrive par la télémétrie de leur
        # automate (device Sparkplug de même code), jamais inventée ici.
        # ------------------------------------------------------------------
        machines_spec = [
            ("M-07", "Comprimeuse rotative 2", ligne_riche, "3.5", "220"),
            ("M-08", "Remplisseuse sirop 1", ligne_riche, "2.8", "180"),
            ("M-09", "Doseuse poudre 1", ligne_pauvre, "4.5", "140"),
            ("M-10", "Conditionneuse sachets 1", ligne_pauvre, "4.0", "160"),
        ]
        for code, nom, ligne, cycle, cout_horaire in machines_spec:
            db.add(
                Machine(
                    code=code, nom=nom, ligne_production_id=ligne.id,
                    temps_cycle_cible_s=Decimal(cycle), cout_horaire=Decimal(cout_horaire),
                )
            )
        db.flush()

        # ------------------------------------------------------------------
        # Ordres de fabrication
        # ------------------------------------------------------------------
        def _creer_of(*, numero, article_code, nomenclatures, ligne, statut, planifiee,
                      bonne=Decimal("0"), rejetee=Decimal("0"), jours_debut=None, jours_fin=None,
                      debut_reel=None, fin_reelle=None):
            article = (articles_riche | articles_pauvre)[article_code]
            nomenclature = nomenclatures[article_code]
            jour_debut = aujourdhui + timedelta(days=jours_debut) if jours_debut is not None else None
            # `jours_fin` = l'échéance client attendue. Le créneau de production
            # (date_debut_prevue/date_fin_prevue) reste vide : il est produit par
            # l'ordonnanceur quand l'opérateur applique une règle via Nova.
            date_echeance = aujourdhui + timedelta(days=jours_fin) if jours_fin is not None else None
            jour_lot = jour_debut or date_echeance or aujourdhui
            of = OrdreFabrication(
                numero=numero, article_id=article.id, nomenclature_id=nomenclature.id,
                quantite_planifiee=planifiee, unite=Unite.UN,
                ligne_production_id=ligne.id, statut=statut,
                date_echeance=date_echeance,
                date_debut_reelle=debut_reel, date_fin_reelle=fin_reelle,
                numero_lot_produit=generer_numero_lot_produit(
                    db, code_article=article.code, jour=jour_lot
                ),
                cree_par="seed_demo_rich", quantite_bonne=bonne, quantite_rejetee=rejetee,
            )
            db.add(of)
            db.flush()
            return of

        ofs = {}
        # --- Ligne riche : production qui tourne bien ---
        ofs["IBU400"] = _creer_of(
            numero="OF-2026-00101", article_code="IBU400", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.TERMINE, planifiee=Decimal("2000"),
            bonne=Decimal("1950"), rejetee=Decimal("35"),
            jours_debut=-7, jours_fin=-6,
            debut_reel=maintenant - timedelta(days=7), fin_reelle=maintenant - timedelta(days=6, hours=18),
        )
        ofs["PARA1000"] = _creer_of(
            numero="OF-2026-00102", article_code="PARA1000", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.TERMINE, planifiee=Decimal("1500"),
            bonne=Decimal("1480"), rejetee=Decimal("12"),
            jours_debut=-5, jours_fin=-4,
            debut_reel=maintenant - timedelta(days=5), fin_reelle=maintenant - timedelta(days=4, hours=20),
        )
        ofs["ASPIRINE500"] = _creer_of(
            numero="OF-2026-00103", article_code="ASPIRINE500", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.TERMINE, planifiee=Decimal("1000"),
            bonne=Decimal("990"), rejetee=Decimal("8"),
            jours_debut=-3, jours_fin=-2,
            debut_reel=maintenant - timedelta(days=3), fin_reelle=maintenant - timedelta(days=2, hours=15),
        )
        ofs["AMOX500"] = _creer_of(
            numero="OF-2026-00104", article_code="AMOX500", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.PLANIFIE, planifiee=Decimal("800"),
            bonne=Decimal("320"), rejetee=Decimal("10"),
            jours_debut=-1, jours_fin=1, debut_reel=maintenant - timedelta(hours=18),
        )
        ofs["MULTIVIT-GEL"] = _creer_of(
            numero="OF-2026-00105", article_code="MULTIVIT-GEL", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.PLANIFIE, planifiee=Decimal("1200"),
            bonne=Decimal("540"), rejetee=Decimal("15"),
            jours_debut=-1, jours_fin=1, debut_reel=maintenant - timedelta(hours=10),
        )
        ofs["VITC-SIROP"] = _creer_of(
            numero="OF-2026-00106", article_code="VITC-SIROP", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.PLANIFIE, planifiee=Decimal("500"),
            jours_debut=2, jours_fin=3,
        )
        ofs["TOUX-SIROP"] = _creer_of(
            numero="OF-2026-00107", article_code="TOUX-SIROP", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.PLANIFIE, planifiee=Decimal("400"),
            jours_debut=3, jours_fin=4,
        )
        ofs["PARA-SIROP"] = _creer_of(
            numero="OF-2026-00108", article_code="PARA-SIROP", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.BROUILLON, planifiee=Decimal("600"),
        )

        # --- Ligne pauvre : production bloquée par la matière ---
        ofs["VITC1000"] = _creer_of(
            numero="OF-2026-00109", article_code="VITC1000", nomenclatures=nomenclatures_pauvre,
            ligne=ligne_pauvre, statut=StatutOF.PLANIFIE, planifiee=Decimal("2000"),
            jours_debut=1, jours_fin=2,
        )
        ofs["TALC-POUDRE"] = _creer_of(
            numero="OF-2026-00110", article_code="TALC-POUDRE", nomenclatures=nomenclatures_pauvre,
            ligne=ligne_pauvre, statut=StatutOF.PLANIFIE, planifiee=Decimal("1000"),
            jours_debut=1, jours_fin=2,
        )
        ofs["GELULE-VIDE-BTE"] = _creer_of(
            numero="OF-2026-00111", article_code="GELULE-VIDE-BTE", nomenclatures=nomenclatures_pauvre,
            ligne=ligne_pauvre, statut=StatutOF.BROUILLON, planifiee=Decimal("300"),
        )
        ofs["CREME-DERM"] = _creer_of(
            numero="OF-2026-00112", article_code="CREME-DERM", nomenclatures=nomenclatures_pauvre,
            ligne=ligne_pauvre, statut=StatutOF.PLANIFIE, planifiee=Decimal("500"),
            bonne=Decimal("40"), rejetee=Decimal("5"),
            jours_debut=-1, jours_fin=1, debut_reel=maintenant - timedelta(hours=14),
        )
        ofs["SACHET-EFFER"] = _creer_of(
            numero="OF-2026-00113", article_code="SACHET-EFFER", nomenclatures=nomenclatures_pauvre,
            ligne=ligne_pauvre, statut=StatutOF.ANNULE, planifiee=Decimal("1000"),
        )
        ofs["POUDRE-BEBE"] = _creer_of(
            numero="OF-2026-00114", article_code="POUDRE-BEBE", nomenclatures=nomenclatures_pauvre,
            ligne=ligne_pauvre, statut=StatutOF.BROUILLON, planifiee=Decimal("300"),
        )
        db.flush()

        # ------------------------------------------------------------------
        # Généalogie de consommation MP pour les OF terminés (ligne riche) :
        # prélève sur les lots, journalise, mais laisse un stock énorme.
        # ------------------------------------------------------------------
        def _consommer(of, formule_ids_qte, quantite_produite):
            for mp_id, qte_unite in formule_ids_qte:
                lot = next(
                    (l for lots in lots_riche.values() for l in lots if l.matiere_premiere_id == mp_id),
                    None,
                )
                if lot is None:
                    continue
                qte_consommee = Decimal(str(qte_unite)) * quantite_produite
                lot.quantite_restante -= qte_consommee
                db.add(
                    OFConsommationMP(
                        ordre_fabrication_id=of.id, lot_matiere_premiere_id=lot.id,
                        matiere_premiere_id=mp_id, quantite_consommee=qte_consommee,
                        date_consommation=of.date_fin_reelle or maintenant,
                    )
                )
                db.add(
                    MouvementStock(
                        type_mouvement=TypeMouvement.SORTIE, matiere_premiere_id=mp_id,
                        lot_matiere_premiere_id=lot.id, quantite=qte_consommee,
                        reference_type="ordre_fabrication", reference_id=of.id,
                        commentaire=f"Consommation {of.numero}",
                        date_mouvement=of.date_fin_reelle or maintenant,
                    )
                )

        _consommer(ofs["IBU400"], [
            (mp_riche["IBU"].id, "8"), (mp_riche["LACTOSE"].id, "3"),
            (mp_riche["BLISTER-R"].id, "1"), (mp_riche["ETUI-R"].id, "1"),
        ], Decimal("1985"))
        _consommer(ofs["PARA1000"], [
            (mp_riche["PARA-API"].id, "10"), (mp_riche["CELLULOSE"].id, "2"),
            (mp_riche["BLISTER-R"].id, "1"), (mp_riche["ETUI-R"].id, "1"),
        ], Decimal("1492"))
        _consommer(ofs["ASPIRINE500"], [
            (mp_riche["ASPIRINE"].id, "9"), (mp_riche["LACTOSE"].id, "2.5"),
            (mp_riche["BLISTER-R"].id, "1"), (mp_riche["ETUI-R"].id, "1"),
        ], Decimal("998"))

        db.commit()
        print(
            "Seed démo riche créé : 5 fournisseurs + 2 lignes (riche/pauvre) + "
            "18 matières premières + 29 lots + 14 articles/nomenclatures + "
            "4 machines (à l'arrêt, sans historique) + 14 OF + généalogie de "
            "consommation + mouvements de stock."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
