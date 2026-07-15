"""Seed démo « riche » : beaucoup d'articles, deux nouvelles lignes contrastées
(stock quasi-illimité vs stock à sec) et des données dans chaque table pour
que le dashboard, le stock, les OF, les alertes et le superviseur autonome
aient tous du contenu à afficher.

Purement additif : ne touche à aucune ligne/article/machine/OF existant (le
seed d'origine `app.db.seed` et `scripts.reset_machines_demo` restent
intacts). Idempotent : si l'article IBU400 existe déjà, ne fait rien.

Run from the backend directory:
    python -m scripts.seed_demo_rich
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.db.session import SessionLocal, init_db
from app.services.numbering import generer_numero_lot_produit
from app.models import (
    Alert,
    AgentProposal,
    Article,
    DocumentRag,
    DowntimeEvent,
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
    QualityEvent,
)
from app.models.enums import (
    CauseArret,
    SeveriteAlerte,
    StatutLot,
    StatutMachine,
    StatutOF,
    StatutProposition,
    TypeArticle,
    TypeEvenementQualite,
    TypeMouvement,
    Unite,
)

FENETRE_S = 8 * 3600


def _quantites(*, cycle_s: float, trs: float, tq: float, do: float) -> tuple[int, int, float]:
    """Reconstruit (bonnes, rejets, duree_arret_s) pour atteindre le TRS visé
    (mêmes formules que trs_service._calculer, fenêtre 8h). Copié de
    scripts.reset_machines_demo pour ne pas coupler les deux scripts."""
    tp = trs / (tq * do)
    tr = float(FENETRE_S)
    tf = do * tr
    tn = tp * tf
    tu = tq * tn
    bonnes = round(tu / cycle_s)
    total = round(tn / cycle_s)
    rejets = max(0, total - bonnes)
    duree_arret = tr - tf
    return bonnes, rejets, duree_arret


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
            ),
            "IBU": MatierePremiere(
                code="MP-IBU", designation="Ibuprofène (principe actif)",
                unite=Unite.G, seuil_alerte=Decimal("50000"),
            ),
            "ASPIRINE": MatierePremiere(
                code="MP-ASPIRINE", designation="Acide acétylsalicylique (principe actif)",
                unite=Unite.G, seuil_alerte=Decimal("50000"),
            ),
            "AMOX": MatierePremiere(
                code="MP-AMOX", designation="Amoxicilline trihydratée (principe actif)",
                unite=Unite.G, seuil_alerte=Decimal("30000"),
            ),
            "LACTOSE": MatierePremiere(
                code="MP-LACTOSE", designation="Lactose monohydraté (excipient)",
                unite=Unite.G, seuil_alerte=Decimal("100000"),
            ),
            "CELLULOSE": MatierePremiere(
                code="MP-CELLULOSE", designation="Cellulose microcristalline (excipient)",
                unite=Unite.G, seuil_alerte=Decimal("80000"),
            ),
            "GELCAPS": MatierePremiere(
                code="MP-GELCAPS", designation="Capsules gélatine vides",
                unite=Unite.UN, seuil_alerte=Decimal("20000"),
            ),
            "SIROP-BASE": MatierePremiere(
                code="MP-SIROP-BASE", designation="Sirop de base sucré",
                unite=Unite.L, seuil_alerte=Decimal("200"),
            ),
            "AROME": MatierePremiere(
                code="MP-AROME-FRAISE", designation="Arôme fraise",
                unite=Unite.ML, seuil_alerte=Decimal("5000"),
            ),
            "FLACON": MatierePremiere(
                code="MP-FLACON100", designation="Flacon PET 100 ml",
                unite=Unite.UN, seuil_alerte=Decimal("5000"),
            ),
            "BLISTER-R": MatierePremiere(
                code="MP-BLISTER-R", designation="Blister PVC/Alu (plaquette)",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
            ),
            "ETUI-R": MatierePremiere(
                code="MP-ETUI-R", designation="Étui carton + notice",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
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
            ),
            "TALC": MatierePremiere(
                code="MP-TALC", designation="Talc pharmaceutique",
                unite=Unite.G, seuil_alerte=Decimal("15000"),
            ),
            "GELULE-VIDE": MatierePremiere(
                code="MP-GELULE-VIDE", designation="Gélules vides",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
            ),
            "SACHET": MatierePremiere(
                code="MP-SACHET-ALU", designation="Sachet aluminium thermosoudable",
                unite=Unite.UN, seuil_alerte=Decimal("10000"),
            ),
            "COLORANT": MatierePremiere(
                code="MP-COLORANT", designation="Colorant alimentaire",
                unite=Unite.ML, seuil_alerte=Decimal("2000"),
            ),
            "CREME-BASE": MatierePremiere(
                code="MP-CREME-BASE", designation="Base crème dermatologique",
                unite=Unite.KG, seuil_alerte=Decimal("50"),
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

        # ------------------------------------------------------------------
        # Machines — 2 sur la ligne riche (tournent bien), 2 sur la pauvre
        # (à l'arrêt / en panne, faute de matière).
        # ------------------------------------------------------------------
        machines_spec = [
            dict(code="M-07", nom="Comprimeuse rotative 2", ligne=ligne_riche, cycle=3.5,
                 statut=StatutMachine.MARCHE, trs=0.88, tq=0.97, do=1.0,
                 cause_arret=CauseArret.MICRO_ARRET),
            dict(code="M-08", nom="Remplisseuse sirop 1", ligne=ligne_riche, cycle=2.8,
                 statut=StatutMachine.MARCHE, trs=0.80, tq=0.95, do=0.97,
                 cause_arret=CauseArret.CHANGEMENT_SERIE),
            dict(code="M-09", nom="Doseuse poudre 1", ligne=ligne_pauvre, cycle=4.5,
                 statut=StatutMachine.ARRET, trs=0.15, tq=0.85, do=0.25,
                 cause_arret=CauseArret.ATTENTE_MATIERE),
            dict(code="M-10", nom="Conditionneuse sachets 1", ligne=ligne_pauvre, cycle=4.0,
                 statut=StatutMachine.PANNE, trs=0.05, tq=0.70, do=0.10,
                 cause_arret=CauseArret.ATTENTE_MATIERE),
        ]
        machines = {}
        for spec in machines_spec:
            bonnes, rejets, duree_arret = _quantites(
                cycle_s=spec["cycle"], trs=spec["trs"], tq=spec["tq"], do=spec["do"]
            )
            machine = Machine(
                code=spec["code"], nom=spec["nom"], ligne_production_id=spec["ligne"].id,
                statut=spec["statut"], temps_cycle_cible_s=Decimal(str(spec["cycle"])),
                dernier_evenement_at=maintenant,
            )
            db.add(machine)
            db.flush()
            machines[spec["code"]] = machine

            db.add(
                QualityEvent(
                    machine_id=machine.id, type=TypeEvenementQualite.BONNE,
                    quantite=bonnes, created_at=maintenant - timedelta(minutes=5),
                )
            )
            if rejets > 0:
                db.add(
                    QualityEvent(
                        machine_id=machine.id, type=TypeEvenementQualite.REBUT,
                        quantite=rejets, created_at=maintenant - timedelta(minutes=4),
                    )
                )
            if duree_arret > 1:
                actif = spec["statut"] in (StatutMachine.PANNE, StatutMachine.ARRET)
                if actif:
                    start = maintenant - timedelta(seconds=duree_arret)
                    end = None
                else:
                    end = maintenant - timedelta(minutes=10)
                    start = end - timedelta(seconds=duree_arret)
                db.add(
                    DowntimeEvent(
                        machine_id=machine.id, cause=spec["cause_arret"],
                        operator_comment=(
                            "Matière première indisponible — en attente de réapprovisionnement."
                            if spec["cause_arret"] == CauseArret.ATTENTE_MATIERE else None
                        ),
                        start_time=start, end_time=end,
                    )
                )
            print(f"{spec['code']} {spec['nom']}: bonnes={bonnes} rejets={rejets} arret_s={duree_arret:.0f}")

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
            ligne=ligne_riche, statut=StatutOF.EN_COURS, planifiee=Decimal("800"),
            bonne=Decimal("320"), rejetee=Decimal("10"),
            jours_debut=-1, jours_fin=1, debut_reel=maintenant - timedelta(hours=18),
        )
        ofs["MULTIVIT-GEL"] = _creer_of(
            numero="OF-2026-00105", article_code="MULTIVIT-GEL", nomenclatures=nomenclatures_riche,
            ligne=ligne_riche, statut=StatutOF.EN_COURS, planifiee=Decimal("1200"),
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
            ligne=ligne_pauvre, statut=StatutOF.EN_COURS, planifiee=Decimal("500"),
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

        # Les deux machines de la ligne pauvre bossent (en vain) sur l'OF bloqué.
        machines["M-09"].ordre_fabrication_id = ofs["CREME-DERM"].id
        machines["M-10"].ordre_fabrication_id = ofs["CREME-DERM"].id

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

        # ------------------------------------------------------------------
        # Maintenance
        # ------------------------------------------------------------------
        from app.models.maintenance_event import MaintenanceEvent
        from app.models.enums import TypeMaintenance

        db.add_all([
            MaintenanceEvent(
                machine_id=machines["M-07"].id, type=TypeMaintenance.PREVENTIVE,
                description="Graissage et contrôle des poinçons — maintenance planifiée.",
                start_time=maintenant - timedelta(days=20), end_time=maintenant - timedelta(days=20) + timedelta(hours=2),
                prochaine_maintenance=aujourdhui + timedelta(days=70),
            ),
            MaintenanceEvent(
                machine_id=machines["M-10"].id, type=TypeMaintenance.CORRECTIVE,
                description="Panne convoyeur — pièce détachée en commande.",
                start_time=maintenant - timedelta(hours=6), end_time=None,
                prochaine_maintenance=None,
            ),
        ])

        # ------------------------------------------------------------------
        # Alertes
        # ------------------------------------------------------------------
        db.add_all([
            Alert(
                machine_id=machines["M-09"].id, severity=SeveriteAlerte.CRITICAL,
                type="stock_epuise",
                message="Stock de MP-TALC (Talc pharmaceutique) épuisé — ligne LIGNE-COND-04 à l'arrêt.",
                created_at=maintenant - timedelta(hours=6), resolved=False,
            ),
            Alert(
                ordre_fabrication_id=ofs["CREME-DERM"].id, severity=SeveriteAlerte.CRITICAL,
                type="stock_epuise",
                message="Stock de MP-CREME-BASE épuisé — OF CREME-DERM bloqué.",
                created_at=maintenant - timedelta(hours=5), resolved=False,
            ),
            Alert(
                severity=SeveriteAlerte.WARNING, type="stock_bas",
                message="Stock de MP-VITC sous le seuil d'alerte (350 G restants sur 20000).",
                created_at=maintenant - timedelta(hours=4), resolved=False,
            ),
            Alert(
                severity=SeveriteAlerte.WARNING, type="stock_bas",
                message="Stock de MP-COLORANT sous le seuil d'alerte (15 ML restants sur 2000).",
                created_at=maintenant - timedelta(hours=3), resolved=False,
            ),
            Alert(
                machine_id=machines["M-10"].id, severity=SeveriteAlerte.CRITICAL,
                type="machine_panne",
                message="M-10 (Conditionneuse sachets 1) en panne — attente pièce détachée.",
                created_at=maintenant - timedelta(hours=6), resolved=False,
            ),
            Alert(
                machine_id=machines["M-08"].id, severity=SeveriteAlerte.WARNING,
                type="machine_arret",
                message="M-08 (Remplisseuse sirop 1) : micro-arrêt résolu automatiquement.",
                created_at=maintenant - timedelta(hours=2), resolved=True,
                resolved_at=maintenant - timedelta(hours=1, minutes=55),
            ),
            Alert(
                machine_id=machines["M-07"].id, severity=SeveriteAlerte.INFO,
                type="maintenance_terminee",
                message="Maintenance préventive terminée sur M-07 (Comprimeuse rotative 2).",
                created_at=maintenant - timedelta(days=20) + timedelta(hours=2), resolved=True,
                resolved_at=maintenant - timedelta(days=20) + timedelta(hours=2, minutes=5),
            ),
            Alert(
                machine_id=machines["M-09"].id, severity=SeveriteAlerte.WARNING,
                type="derive_qualite",
                message="M-09 (Doseuse poudre 1) : taux de rebut élevé avant l'arrêt (matière hors spec).",
                created_at=maintenant - timedelta(hours=7), resolved=False,
            ),
        ])

        # ------------------------------------------------------------------
        # Propositions du superviseur autonome — les 5 statuts, actions
        # cohérentes avec app.services.supervisor_service.
        # ------------------------------------------------------------------
        db.add_all([
            AgentProposal(
                cle_dedup=f"stock_bas:{mp_pauvre['TALC'].id}", type="stock_bas",
                severite=SeveriteAlerte.WARNING,
                titre=f"Stock bas : MP-TALC (0 G)",
                diagnostic=(
                    "Le stock de MP-TALC (Talc pharmaceutique) est descendu à 0 G, sous le "
                    "seuil d'alerte de 15000 G. Fournisseur connu : Pharma Excipients "
                    "International (commandes@pharmaexcip.com). Je recommande de créer "
                    "l'alerte de réapprovisionnement et de le prévenir."
                ),
                action_libelle="Créer l'alerte de réapprovisionnement et prévenir Pharma Excipients International",
                action={
                    "type": "alerte_reappro", "matiere_premiere_id": mp_pauvre["TALC"].id,
                    "fournisseur_nom": "Pharma Excipients International",
                    "fournisseur_contact": "commandes@pharmaexcip.com",
                },
                statut=StatutProposition.PROPOSEE,
                created_at=maintenant - timedelta(hours=6),
            ),
            AgentProposal(
                cle_dedup=f"stock_bas:{mp_pauvre['CREME-BASE'].id}", type="stock_bas",
                severite=SeveriteAlerte.WARNING,
                titre="Stock bas : MP-CREME-BASE (0 KG)",
                diagnostic=(
                    "Le stock de MP-CREME-BASE (base crème dermatologique) est descendu à "
                    "0 KG, sous le seuil d'alerte de 50 KG. Je recommande de créer l'alerte "
                    "de réapprovisionnement et de prévenir Pharma Excipients International."
                ),
                action_libelle="Créer l'alerte de réapprovisionnement et prévenir Pharma Excipients International",
                action={
                    "type": "alerte_reappro", "matiere_premiere_id": mp_pauvre["CREME-BASE"].id,
                    "fournisseur_nom": "Pharma Excipients International",
                    "fournisseur_contact": "commandes@pharmaexcip.com",
                },
                statut=StatutProposition.APPROUVEE,
                resultat="Fournisseur contacté par email — livraison annoncée sous 5 jours.",
                created_at=maintenant - timedelta(hours=5, minutes=30),
                decided_at=maintenant - timedelta(hours=5),
            ),
            AgentProposal(
                cle_dedup=f"maintenance_urgence:{machines['M-10'].id}", type="maintenance_urgence",
                severite=SeveriteAlerte.CRITICAL,
                titre="Panne bloquante sur M-10 (Conditionneuse sachets 1)",
                diagnostic=(
                    "M-10 est en panne depuis plusieurs heures, bloquant l'OF-2026-00112 "
                    "(CREME-DERM). Je recommande une maintenance d'urgence."
                ),
                action_libelle="Lancer une maintenance d'urgence sur M-10",
                action={"type": "maintenance_urgence", "machine_id": machines["M-10"].id},
                statut=StatutProposition.EXECUTEE,
                resultat="Maintenance d'urgence lancée, technicien sur site.",
                created_at=maintenant - timedelta(hours=6),
                decided_at=maintenant - timedelta(hours=5, minutes=45),
            ),
            AgentProposal(
                cle_dedup=f"maintenance_urgence:{machines['M-10'].id}:v2", type="maintenance_urgence",
                severite=SeveriteAlerte.CRITICAL,
                titre="Deuxième tentative — pièce détachée manquante sur M-10",
                diagnostic=(
                    "La maintenance d'urgence sur M-10 ne peut pas aboutir : la pièce "
                    "détachée nécessaire n'est pas en stock à l'atelier."
                ),
                action_libelle="Commander la pièce détachée en urgence",
                action={"type": "maintenance_urgence", "machine_id": machines["M-10"].id},
                statut=StatutProposition.ECHOUEE,
                resultat="Échec : pièce détachée indisponible en stock atelier.",
                created_at=maintenant - timedelta(hours=4),
                decided_at=maintenant - timedelta(hours=3, minutes=50),
            ),
            AgentProposal(
                cle_dedup=f"risque_panne:{machines['M-09'].id}:{aujourdhui:%Y%m%d}",
                type="maintenance_preventive", severite=SeveriteAlerte.WARNING,
                titre="Risque de panne élevé sur M-09 (Doseuse poudre 1)",
                diagnostic=(
                    "M-09 affiche un risque de panne élevé : plusieurs micro-arrêts sur 7 "
                    "jours, jamais maintenue. Je recommande de planifier une maintenance "
                    "préventive."
                ),
                action_libelle="Planifier une maintenance préventive sur M-09",
                action={"type": "maintenance_preventive", "machine_id": machines["M-09"].id},
                statut=StatutProposition.REJETEE,
                resultat="Reporté : priorité donnée au réapprovisionnement matière.",
                created_at=maintenant - timedelta(hours=7),
                decided_at=maintenant - timedelta(hours=6, minutes=40),
            ),
            AgentProposal(
                cle_dedup=f"stock_bas:{mp_pauvre['VITC'].id}", type="stock_bas",
                severite=SeveriteAlerte.WARNING,
                titre="Stock bas : MP-VITC (350 G)",
                diagnostic=(
                    "Le stock de MP-VITC (vitamine C) est descendu à 350 G, sous le seuil "
                    "d'alerte de 20000 G. Fournisseur connu : API Import Maghreb. Je "
                    "recommande de créer l'alerte de réapprovisionnement."
                ),
                action_libelle="Créer l'alerte de réapprovisionnement et prévenir API Import Maghreb",
                action={
                    "type": "alerte_reappro", "matiere_premiere_id": mp_pauvre["VITC"].id,
                    "fournisseur_nom": "API Import Maghreb",
                    "fournisseur_contact": "ventes@apimaghreb.com",
                },
                statut=StatutProposition.PROPOSEE,
                created_at=maintenant - timedelta(hours=4),
            ),
        ])

        # ------------------------------------------------------------------
        # Documents (métadonnées RAG — pas d'indexation Pinecone réelle ici)
        # ------------------------------------------------------------------
        db.add_all([
            DocumentRag(
                nom="BPF - Bonnes Pratiques de Fabrication (Guide UE)",
                fichier="bpf_guide_ue_2024.pdf", categorie="norme",
                nb_pages=180, nb_chunks=420, statut="INDEXEE", vector_ids=[],
            ),
            DocumentRag(
                nom="Procédure de nettoyage ligne comprimés",
                fichier="procedure_nettoyage_ligne_comprimes.pdf", categorie="procédure",
                nb_pages=12, nb_chunks=28, statut="INDEXEE", vector_ids=[],
            ),
            DocumentRag(
                nom="Manuel machine - Comprimeuse rotative",
                fichier="manuel_comprimeuse_rotative.pdf", categorie="manuel machine",
                nb_pages=64, nb_chunks=150, statut="INDEXEE", vector_ids=[],
            ),
            DocumentRag(
                nom="Fiche technique - Ibuprofène 400 mg",
                fichier="fiche_technique_ibuprofene_400.pdf", categorie="fiche technique",
                nb_pages=4, nb_chunks=9, statut="INDEXEE", vector_ids=[],
            ),
            DocumentRag(
                nom="Procédure de gestion des lots périmés",
                fichier="procedure_gestion_lots_perimes.pdf", categorie="procédure",
                nb_pages=8, nb_chunks=18, statut="INDEXEE", vector_ids=[],
            ),
            DocumentRag(
                nom="Plan HACCP - Ligne conditionnement",
                fichier="plan_haccp_conditionnement.pdf", categorie="norme",
                nb_pages=45, nb_chunks=95, statut="INDEXEE", vector_ids=[],
            ),
        ])

        db.commit()
        print(
            "Seed démo riche créé : 5 fournisseurs + 2 lignes (riche/pauvre) + "
            "18 matières premières + 29 lots + 14 articles/nomenclatures + "
            "4 machines + 14 OF + généalogie de conso + mouvements de stock + "
            "maintenance + 8 alertes + 6 propositions + 6 documents."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
