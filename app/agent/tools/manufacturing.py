"""Outils agent pour le workflow de fabrication (référentiel, stock, OF).

Chaque outil ouvre sa propre session et renvoie un texte lisible que le
superviseur peut relayer à l'opérateur. Les outils qui écrivent committent.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import select

from app.agent.tools import confirmation_gate
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import Article, LigneProduction, MatierePremiere, OrdreFabrication
from app.services import manufacturing as svc

logger = get_logger(__name__)


def _parse_date(valeur: str | None) -> date | None:
    if not valeur:
        return None
    try:
        return date.fromisoformat(valeur)
    except ValueError:
        raise AppError(f"Date invalide '{valeur}' (format attendu AAAA-MM-JJ).")


@tool
def lister_articles() -> str:
    """Liste les articles (produits finis) fabricables avec leur id et code.

    À utiliser pour montrer à l'opérateur les produits disponibles à la fabrication.
    """
    with session_scope() as db:
        articles = db.execute(select(Article).where(Article.actif.is_(True))).scalars().all()
        if not articles:
            return "Aucun article actif. Créez d'abord un article et sa nomenclature."
        return "\n".join(
            f"- id={a.id} | {a.code} | {a.designation} | unité {a.unite.value}" for a in articles
        )


@tool
def rechercher_article(query: str) -> str:
    """Recherche un article par code ou désignation (ex. 'paracétamol' ou 'PARA').

    Renvoie les correspondances avec leur id (nécessaire pour les autres outils).
    """
    with session_scope() as db:
        articles = svc.rechercher_articles(db, query)
        if not articles:
            return f"Aucun article ne correspond à '{query}'."
        return "\n".join(
            f"- id={a.id} | {a.code} | {a.designation} | unité {a.unite.value}" for a in articles
        )


@tool(response_format="content_and_artifact")
def verifier_disponibilite(article_id: int, quantite: float) -> tuple[str, dict | None]:
    """Calcule les besoins en matières premières pour produire `quantite` unités d'un article,
    compare au stock disponible et indique si la fabrication est possible.

    À appeler AVANT de créer un ordre de fabrication. Ne modifie rien.
    Renvoie, par MP : quantité requise, disponible et manquante, plus le verdict global.
    """
    try:
        qte = Decimal(str(quantite))
    except (InvalidOperation, ValueError):
        return f"Quantité invalide : {quantite!r}.", None
    with session_scope() as db:
        try:
            f = svc.verifier_faisabilite(db, article_id, qte)
        except AppError as exc:
            return f"Erreur : {exc.message}", None
        lignes = [
            f"  - {b.code} ({b.designation}) : requis {b.quantite_requise} {b.unite}, "
            f"dispo {b.quantite_disponible} {b.unite}"
            + ("" if b.suffisant else f"  ⚠ MANQUE {b.manquant} {b.unite}")
            for b in f.besoins
        ]
        entete = f"Besoins pour produire {qte} unité(s) de l'article id={article_id} :"
        verdict = (
            "✅ Fabrication POSSIBLE avec le stock actuel."
            if f.faisable
            else "❌ Fabrication IMPOSSIBLE : stock insuffisant (voir ⚠ ci-dessus)."
        )
        artifact = {
            "kind": "faisabilite",
            "article_id": article_id,
            "quantite": str(qte),
            "faisable": f.faisable,
            "besoins": [
                {
                    "code": b.code,
                    "designation": b.designation,
                    "unite": str(b.unite),
                    "requis": str(b.quantite_requise),
                    "disponible": str(b.quantite_disponible),
                    "manquant": str(b.manquant),
                    "suffisant": b.suffisant,
                }
                for b in f.besoins
            ],
        }
        return "\n".join([entete, *lignes, "", verdict]), artifact


@tool
def lister_lignes_production() -> str:
    """Liste les lignes de production actives (id, code, désignation) pour affecter un OF."""
    with session_scope() as db:
        lignes = (
            db.execute(select(LigneProduction).where(LigneProduction.actif.is_(True)))
            .scalars()
            .all()
        )
        if not lignes:
            return "Aucune ligne de production active."
        return "\n".join(f"- id={l.id} | {l.code} | {l.designation}" for l in lignes)


@tool
def etat_stock_matiere(query: str = "") -> str:
    """État du stock des matières premières (total disponible par MP).

    `query` optionnel filtre par code ou désignation. Ne modifie rien.
    """
    with session_scope() as db:
        stmt = select(MatierePremiere).where(MatierePremiere.actif.is_(True))
        mps = db.execute(stmt).scalars().all()
        if query:
            q = query.lower()
            mps = [m for m in mps if q in m.code.lower() or q in m.designation.lower()]
        if not mps:
            return "Aucune matière première trouvée."
        lignes = []
        for m in mps:
            dispo = svc.stock_disponible_mp(db, m.id)
            alerte = ""
            if m.seuil_alerte is not None and dispo < m.seuil_alerte:
                alerte = f"  ⚠ sous le seuil ({m.seuil_alerte})"
            lignes.append(f"- {m.code} | {m.designation} : {dispo} {m.unite.value}{alerte}")
        return "\n".join(lignes)


@tool(response_format="content_and_artifact")
def creer_ordre_fabrication(
    article_id: int,
    quantite: float,
    confirmation: bool,
    date_fin_prevue: str | None = None,
    ligne_production_id: int | None = None,
    *,
    config: RunnableConfig,
) -> tuple[str, dict | None]:
    """Crée un ordre de fabrication : décrémente les matières premières en FEFO,
    enregistre la généalogie du lot, et planifie l'OF.

    ATTENTION — action IRRÉVERSIBLE (le stock est consommé). N'appelez cet outil
    qu'après avoir : (1) vérifié la disponibilité, (2) présenté le résultat à
    l'opérateur, (3) obtenu sa confirmation explicite, (4) demandé la date de fin
    prévue (AAAA-MM-JJ) et éventuellement la ligne de production.

    `confirmation` DOIT valoir true uniquement si l'opérateur a confirmé explicitement.
    """
    libelle = f"créer l'OF de {quantite} unité(s) de l'article id={article_id}"
    if not confirmation_gate.evaluer(
        config,
        "creer_ordre_fabrication",
        {
            "article_id": article_id,
            "quantite": quantite,
            "date_fin_prevue": date_fin_prevue,
            "ligne_production_id": ligne_production_id,
        },
        confirmation,
    ):
        return (
            f"Confirmation requise : {libelle}. Présentez d'abord les besoins/disponibilités "
            "et demandez à l'opérateur de confirmer avant de rappeler cet outil "
            "(confirmation=true).",
            {"kind": "confirmation_attente", "libelle": libelle},
        )
    try:
        d_fin = _parse_date(date_fin_prevue)
    except AppError as exc:
        return f"Erreur : {exc.message}", None

    with session_scope() as db:
        try:
            of = svc.creer_ordre_fabrication(
                db,
                article_id=article_id,
                quantite=Decimal(str(quantite)),
                date_fin_prevue=d_fin,
                ligne_production_id=ligne_production_id,
                cree_par="agent",
            )
            db.flush()
            numero = of.numero
            lot = of.numero_lot_produit
            conso = [
                f"  - {c.matiere_premiere.code if c.matiere_premiere else c.matiere_premiere_id}"
                f" ← lot {c.lot.numero_lot} : {c.quantite_consommee}"
                for c in of.consommations
            ]
            artifact = {
                "kind": "of_cree",
                "of_id": of.id,
                "numero": numero,
                "lot_produit": lot,
                "statut": "PLANIFIE",
                "date_fin_prevue": date_fin_prevue,
                "ligne_production_id": ligne_production_id,
                "consommations": [
                    {
                        "code_mp": c.matiere_premiere.code
                        if c.matiere_premiere
                        else str(c.matiere_premiere_id),
                        "numero_lot": c.lot.numero_lot,
                        "quantite": str(c.quantite_consommee),
                    }
                    for c in of.consommations
                ],
            }
        except AppError as exc:
            detail = f" Détails : {exc.details}" if exc.details else ""
            return f"❌ Impossible de créer l'OF : {exc.message}{detail}", None

        lignes = [
            f"✅ Ordre de fabrication créé : {numero}",
            f"   Lot produit : {lot}",
            f"   Statut : PLANIFIÉ"
            + (f" | fin prévue {date_fin_prevue}" if date_fin_prevue else "")
            + (f" | ligne id={ligne_production_id}" if ligne_production_id else ""),
            "   Matières premières consommées (FEFO) :",
            *conso,
        ]
        return "\n".join(lignes), artifact


@tool
def consulter_ordre_fabrication(numero_ou_id: str) -> str:
    """Consulte un OF par numéro (ex. 'OF-2026-00001') ou par id, avec sa généalogie."""
    with session_scope() as db:
        of: OrdreFabrication | None = None
        if numero_ou_id.isdigit():
            of = db.get(OrdreFabrication, int(numero_ou_id))
        if of is None:
            of = db.execute(
                select(OrdreFabrication).where(OrdreFabrication.numero == numero_ou_id)
            ).scalars().first()
        if of is None:
            return f"OF introuvable : {numero_ou_id}"
        conso = [
            f"  - {c.matiere_premiere.code if c.matiere_premiere else c.matiere_premiere_id}"
            f" ← lot {c.lot.numero_lot} : {c.quantite_consommee}"
            for c in of.consommations
        ]
        return "\n".join(
            [
                f"OF {of.numero} | article {of.article.code} | {of.quantite_planifiee} {of.unite.value}",
                f"Statut : {of.statut.value} | lot produit : {of.numero_lot_produit}"
                f" | fin prévue : {of.date_fin_prevue}",
                "Généalogie (lots MP consommés) :",
                *(conso or ["  (aucune)"]),
            ]
        )


MANUFACTURING_TOOLS = [
    lister_articles,
    rechercher_article,
    verifier_disponibilite,
    lister_lignes_production,
    etat_stock_matiere,
    creer_ordre_fabrication,
    consulter_ordre_fabrication,
]
