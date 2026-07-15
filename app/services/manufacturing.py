"""Logique métier de fabrication : besoins MP, faisabilité (FEFO), création d'OF.

Toutes les fonctions reçoivent une `Session` et ne committent pas elles-mêmes :
l'appelant contrôle la transaction (sauf indication contraire).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, nullslast, select
from sqlalchemy.orm import Session

from app.core.exceptions import FabricationError, NotFoundError
from app.core.logging import get_logger
from app.models import (
    Article,
    LotMatierePremiere,
    MatierePremiere,
    MouvementStock,
    Nomenclature,
    NomenclatureLigne,
    OFConsommationMP,
    OrdreFabrication,
)
from app.models.enums import StatutLot, StatutOF, TypeMouvement
from app.services.numbering import generer_numero_lot_produit, generer_numero_of

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Résultats (DTO internes)
# --------------------------------------------------------------------------- #
@dataclass
class BesoinMP:
    matiere_premiere_id: int
    code: str
    designation: str
    unite: str
    quantite_requise: Decimal
    quantite_disponible: Decimal

    @property
    def manquant(self) -> Decimal:
        m = self.quantite_requise - self.quantite_disponible
        return m if m > 0 else Decimal("0")

    @property
    def suffisant(self) -> bool:
        return self.quantite_disponible >= self.quantite_requise


@dataclass
class Faisabilite:
    article_id: int
    quantite: Decimal
    besoins: list[BesoinMP] = field(default_factory=list)

    @property
    def faisable(self) -> bool:
        return all(b.suffisant for b in self.besoins) and bool(self.besoins)

    @property
    def manquants(self) -> list[BesoinMP]:
        return [b for b in self.besoins if not b.suffisant]


# --------------------------------------------------------------------------- #
# Lectures
# --------------------------------------------------------------------------- #
def get_article(db: Session, article_id: int) -> Article:
    article = db.get(Article, article_id)
    if article is None:
        raise NotFoundError(f"Article introuvable (id={article_id})")
    return article


def rechercher_articles(db: Session, query: str, *, limit: int = 10) -> list[Article]:
    """Recherche d'articles par code ou désignation (insensible à la casse)."""
    like = f"%{query.lower()}%"
    stmt = (
        select(Article)
        .where(
            Article.actif.is_(True),
            func.lower(Article.code).like(like) | func.lower(Article.designation).like(like),
        )
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def get_nomenclature_active(db: Session, article_id: int) -> Nomenclature:
    """Retourne la nomenclature active (version la plus élevée) de l'article."""
    stmt = (
        select(Nomenclature)
        .where(Nomenclature.article_id == article_id, Nomenclature.actif.is_(True))
        .order_by(Nomenclature.version.desc())
    )
    nom = db.execute(stmt).scalars().first()
    if nom is None:
        raise FabricationError(
            "Aucune nomenclature active pour cet article.",
            details={"article_id": article_id},
        )
    return nom


def stock_disponible_mp(db: Session, matiere_premiere_id: int) -> Decimal:
    """Somme des quantités restantes des lots DISPONIBLES d'une MP."""
    total = db.execute(
        select(func.coalesce(func.sum(LotMatierePremiere.quantite_restante), 0)).where(
            LotMatierePremiere.matiere_premiere_id == matiere_premiere_id,
            LotMatierePremiere.statut == StatutLot.DISPONIBLE,
        )
    ).scalar_one()
    return Decimal(str(total))


def get_lot(db: Session, lot_id: int) -> LotMatierePremiere:
    lot = db.get(LotMatierePremiere, lot_id)
    if lot is None:
        raise NotFoundError(f"Lot introuvable (id={lot_id})")
    return lot


def lister_lots(
    db: Session,
    *,
    matiere_premiere_id: int | None = None,
    statut: StatutLot | None = None,
) -> list[LotMatierePremiere]:
    """Liste des lots (tous MP confondus), triés FEFO puis par réception récente."""
    stmt = select(LotMatierePremiere)
    if matiere_premiere_id is not None:
        stmt = stmt.where(LotMatierePremiere.matiere_premiere_id == matiere_premiere_id)
    if statut is not None:
        stmt = stmt.where(LotMatierePremiere.statut == statut)
    stmt = stmt.order_by(
        nullslast(LotMatierePremiere.date_peremption.asc()),
        LotMatierePremiere.date_reception.desc(),
        LotMatierePremiere.id.desc(),
    )
    return list(db.execute(stmt).scalars())


def lister_mouvements(
    db: Session,
    *,
    matiere_premiere_id: int | None = None,
    lot_id: int | None = None,
    limit: int = 200,
) -> list[MouvementStock]:
    """Journal d'audit des mouvements de stock, du plus récent au plus ancien."""
    stmt = select(MouvementStock)
    if matiere_premiere_id is not None:
        stmt = stmt.where(MouvementStock.matiere_premiere_id == matiere_premiere_id)
    if lot_id is not None:
        stmt = stmt.where(MouvementStock.lot_matiere_premiere_id == lot_id)
    stmt = stmt.order_by(MouvementStock.date_mouvement.desc(), MouvementStock.id.desc()).limit(
        limit
    )
    return list(db.execute(stmt).scalars())


def ajuster_stock(
    db: Session,
    *,
    lot_id: int,
    quantite_restante: Decimal,
    commentaire: str | None = None,
) -> LotMatierePremiere:
    """Ajuste la quantité restante d'un lot (inventaire) et journalise l'écart.

    L'écart signé (nouvelle − ancienne) est enregistré comme un mouvement AJUSTEMENT.
    Le statut passe à ÉPUISÉ si la quantité tombe à 0, et redevient DISPONIBLE si on
    ré-approvisionne un lot épuisé.
    """
    nouvelle = Decimal(str(quantite_restante))
    if nouvelle < 0:
        raise FabricationError("La quantité restante ne peut pas être négative.")
    lot = get_lot(db, lot_id)

    ancienne = lot.quantite_restante
    ecart = nouvelle - ancienne
    lot.quantite_restante = nouvelle
    if nouvelle <= 0:
        lot.statut = StatutLot.EPUISE
    elif lot.statut == StatutLot.EPUISE:
        lot.statut = StatutLot.DISPONIBLE

    db.add(
        MouvementStock(
            type_mouvement=TypeMouvement.AJUSTEMENT,
            matiere_premiere_id=lot.matiere_premiere_id,
            lot_matiere_premiere_id=lot.id,
            quantite=ecart,
            reference_type="INVENTAIRE",
            reference_id=lot.id,
            commentaire=commentaire or f"Ajustement inventaire lot {lot.numero_lot}",
        )
    )
    db.flush()
    logger.info(
        "stock_ajuste",
        lot_id=lot.id,
        numero_lot=lot.numero_lot,
        ancienne=str(ancienne),
        nouvelle=str(nouvelle),
        ecart=str(ecart),
    )
    return lot


def supprimer_lot(db: Session, lot_id: int) -> None:
    """Supprime un lot et ses mouvements associés (réception/ajustement).

    Refuse la suppression si le lot a été consommé par un OF (traçabilité), afin de
    préserver la généalogie : bloquer/épuiser le lot est alors la bonne action.
    """
    lot = get_lot(db, lot_id)
    conso = db.execute(
        select(func.count())
        .select_from(OFConsommationMP)
        .where(OFConsommationMP.lot_matiere_premiere_id == lot_id)
    ).scalar_one()
    if conso:
        raise FabricationError(
            "Lot consommé par un OF — impossible à supprimer. Bloquez-le à la place.",
            details={"lot_id": lot_id, "consommations": conso},
        )
    for mvt in lister_mouvements(db, lot_id=lot_id, limit=10_000):
        db.delete(mvt)
    db.delete(lot)
    db.flush()


def lots_fefo(db: Session, matiere_premiere_id: int) -> list[LotMatierePremiere]:
    """Lots disponibles d'une MP triés FEFO (péremption la plus proche d'abord)."""
    stmt = (
        select(LotMatierePremiere)
        .where(
            LotMatierePremiere.matiere_premiere_id == matiere_premiere_id,
            LotMatierePremiere.statut == StatutLot.DISPONIBLE,
            LotMatierePremiere.quantite_restante > 0,
        )
        .order_by(
            nullslast(LotMatierePremiere.date_peremption.asc()),
            LotMatierePremiere.date_reception.asc(),
            LotMatierePremiere.id.asc(),
        )
    )
    return list(db.execute(stmt).scalars())


# --------------------------------------------------------------------------- #
# Calculs
# --------------------------------------------------------------------------- #
def calculer_besoins_mp(db: Session, article_id: int, quantite: Decimal) -> Faisabilite:
    """Calcule les besoins en MP = quantité × nomenclature, et le stock dispo."""
    quantite = Decimal(str(quantite))
    if quantite <= 0:
        raise FabricationError("La quantité à produire doit être positive.")

    get_article(db, article_id)
    nomenclature = get_nomenclature_active(db, article_id)

    lignes = db.execute(
        select(NomenclatureLigne).where(
            NomenclatureLigne.nomenclature_id == nomenclature.id
        )
    ).scalars()

    besoins: list[BesoinMP] = []
    for ligne in lignes:
        mp: MatierePremiere = ligne.matiere_premiere
        requis = (ligne.quantite_par_unite * quantite).quantize(Decimal("0.0001"))
        dispo = stock_disponible_mp(db, mp.id)
        besoins.append(
            BesoinMP(
                matiere_premiere_id=mp.id,
                code=mp.code,
                designation=mp.designation,
                unite=mp.unite.value,
                quantite_requise=requis,
                quantite_disponible=dispo,
            )
        )

    return Faisabilite(article_id=article_id, quantite=quantite, besoins=besoins)


def verifier_faisabilite(db: Session, article_id: int, quantite: Decimal) -> Faisabilite:
    """Alias explicite : besoins + verdict de faisabilité."""
    return calculer_besoins_mp(db, article_id, quantite)


# --------------------------------------------------------------------------- #
# Écritures (consommation + création OF)
# --------------------------------------------------------------------------- #
def _consommer_mp_fefo(
    db: Session,
    *,
    matiere_premiere_id: int,
    quantite: Decimal,
    of: OrdreFabrication,
) -> list[OFConsommationMP]:
    """Consomme `quantite` d'une MP en FEFO ; écrit lots, mouvements et généalogie.

    Lève FabricationError si le stock disponible est insuffisant.
    """
    restant = Decimal(str(quantite))
    consommations: list[OFConsommationMP] = []

    for lot in lots_fefo(db, matiere_premiere_id):
        if restant <= 0:
            break
        prise = min(lot.quantite_restante, restant)
        lot.quantite_restante = lot.quantite_restante - prise
        if lot.quantite_restante <= 0:
            lot.statut = StatutLot.EPUISE
        restant -= prise

        db.add(
            MouvementStock(
                type_mouvement=TypeMouvement.SORTIE,
                matiere_premiere_id=matiere_premiere_id,
                lot_matiere_premiere_id=lot.id,
                quantite=prise,
                reference_type="OF",
                reference_id=of.id,
                commentaire=f"Consommation OF {of.numero}",
            )
        )
        conso = OFConsommationMP(
            ordre_fabrication_id=of.id,
            lot_matiere_premiere_id=lot.id,
            matiere_premiere_id=matiere_premiere_id,
            quantite_consommee=prise,
        )
        db.add(conso)
        consommations.append(conso)

    if restant > 0:
        raise FabricationError(
            "Stock insuffisant pendant la consommation.",
            details={"matiere_premiere_id": matiere_premiere_id, "manquant": str(restant)},
        )
    return consommations


def creer_ordre_fabrication(
    db: Session,
    *,
    article_id: int,
    quantite: Decimal,
    date_echeance: date | None = None,
    ligne_production_id: int | None = None,
    cree_par: str | None = None,
) -> OrdreFabrication:
    """Crée un OF : vérifie la faisabilité, décrémente les MP (FEFO), trace la généalogie.

    Atomique : tout est écrit dans la session puis flush. L'appelant committe.
    """
    quantite = Decimal(str(quantite))
    faisabilite = verifier_faisabilite(db, article_id, quantite)
    if not faisabilite.faisable:
        manquants = {b.code: str(b.manquant) for b in faisabilite.manquants}
        raise FabricationError(
            "Fabrication impossible : matières premières insuffisantes.",
            details={"manquants": manquants},
        )

    article = get_article(db, article_id)
    nomenclature = get_nomenclature_active(db, article_id)

    of = OrdreFabrication(
        numero=generer_numero_of(db),
        article_id=article_id,
        nomenclature_id=nomenclature.id,
        quantite_planifiee=quantite,
        unite=article.unite,
        ligne_production_id=ligne_production_id,
        statut=StatutOF.PLANIFIE,
        # Le créneau (date_debut_prevue/date_fin_prevue) reste vide : il est écrit
        # par l'ordonnanceur, pas à la création.
        date_echeance=date_echeance,
        numero_lot_produit=generer_numero_lot_produit(db, code_article=article.code),
        cree_par=cree_par,
    )
    db.add(of)
    db.flush()  # obtenir of.id pour les mouvements/généalogie

    for besoin in faisabilite.besoins:
        _consommer_mp_fefo(
            db,
            matiere_premiere_id=besoin.matiere_premiere_id,
            quantite=besoin.quantite_requise,
            of=of,
        )

    db.flush()
    logger.info(
        "of_cree",
        numero=of.numero,
        article=article.code,
        quantite=str(quantite),
        lot_produit=of.numero_lot_produit,
    )
    return of


def receptionner_mp(
    db: Session,
    *,
    matiere_premiere_id: int,
    numero_lot: str,
    quantite: Decimal,
    date_reception: date | None = None,
    date_peremption: date | None = None,
    fournisseur_id: int | None = None,
) -> LotMatierePremiere:
    """Réceptionne un lot de MP (ENTREE) et journalise le mouvement."""
    quantite = Decimal(str(quantite))
    if quantite <= 0:
        raise FabricationError("La quantité réceptionnée doit être positive.")
    if db.get(MatierePremiere, matiere_premiere_id) is None:
        raise NotFoundError(f"Matière première introuvable (id={matiere_premiere_id})")

    lot = LotMatierePremiere(
        numero_lot=numero_lot,
        matiere_premiere_id=matiere_premiere_id,
        fournisseur_id=fournisseur_id,
        quantite_initiale=quantite,
        quantite_restante=quantite,
        date_reception=date_reception or date.today(),
        date_peremption=date_peremption,
        statut=StatutLot.DISPONIBLE,
    )
    db.add(lot)
    db.flush()
    db.add(
        MouvementStock(
            type_mouvement=TypeMouvement.ENTREE,
            matiere_premiere_id=matiere_premiere_id,
            lot_matiere_premiere_id=lot.id,
            quantite=quantite,
            reference_type="RECEPTION",
            reference_id=lot.id,
            commentaire=f"Réception lot {numero_lot}",
        )
    )
    return lot
