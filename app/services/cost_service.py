"""Chiffrage en dinars (TND) des arrêts et pertes de production.

Deux composantes, volontairement simples et explicables en démo :
- production non réalisée = pièces perdues × `Article.valeur_unitaire` ;
- immobilisation machine  = durée × `Machine.cout_horaire` (main-d'œuvre,
  énergie, amortissement).

Toutes les fonctions tolèrent les données manquantes (pas de valeur unitaire,
pas de cycle) : elles chiffrent ce qui est chiffrable et signalent le reste.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Machine, OrdreFabrication
from app.models.machine_event import MachineEvent


def format_tnd(montant: float) -> str:
    """1234.5 -> '1 235 TND' (arrondi au dinar, espace fine pour les milliers)."""
    return f"{montant:,.0f}".replace(",", " ") + " TND"


@dataclass(frozen=True)
class CoutArret:
    """Décomposition du coût d'un arrêt machine."""

    duree_minutes: float
    perte_production_tnd: float | None  # None si non chiffrable (voir raison)
    immobilisation_tnd: float
    pieces_perdues: int | None
    # Pourquoi la production perdue n'a pas pu être valorisée (None si chiffrée).
    raison_non_chiffree: str | None = None

    @property
    def total_tnd(self) -> float:
        return (self.perte_production_tnd or 0.0) + self.immobilisation_tnd

    @property
    def par_minute_tnd(self) -> float:
        if self.duree_minutes <= 0:
            return 0.0
        return self.total_tnd / self.duree_minutes

    def resume(self) -> str:
        """Phrase prête à afficher à l'opérateur."""
        if self.perte_production_tnd is not None:
            return (
                f"≈ {format_tnd(self.total_tnd)} "
                f"({format_tnd(self.perte_production_tnd)} de production non réalisée "
                f"+ {format_tnd(self.immobilisation_tnd)} d'immobilisation machine)"
            )
        raison = self.raison_non_chiffree or "production perdue non chiffrée"
        return f"≈ {format_tnd(self.immobilisation_tnd)} d'immobilisation machine ({raison})"

    def artifact(self) -> dict:
        return {
            "duree_minutes": self.duree_minutes,
            "perte_production_tnd": self.perte_production_tnd,
            "immobilisation_tnd": self.immobilisation_tnd,
            "total_tnd": self.total_tnd,
            "par_minute_tnd": round(self.par_minute_tnd, 2),
            "pieces_perdues": self.pieces_perdues,
        }


def estimer_cout_arret(
    machine: Machine,
    duree_minutes: float,
    *,
    of: OrdreFabrication | None = None,
    pieces_perdues: int | None = None,
) -> CoutArret:
    """Chiffre un arrêt de `duree_minutes` sur `machine`.

    `pieces_perdues` peut être fourni (déjà calculé par l'appelant) ; sinon il
    est déduit du temps de cycle courant. La valeur unitaire vient de l'article
    de l'OF (`of` explicite, ou l'OF actif de la machine).
    """
    ordre = of if of is not None else machine.ordre_fabrication
    article = ordre.article if ordre is not None else None

    if pieces_perdues is None:
        cycle = machine.temps_cycle_actuel_s or machine.temps_cycle_cible_s
        if cycle and float(cycle) > 0:
            pieces_perdues = int(duree_minutes * 60 / float(cycle))

    perte_production: float | None = None
    raison: str | None = None
    if pieces_perdues is None:
        raison = "temps de cycle inconnu : production perdue non chiffrable"
    elif ordre is None:
        raison = "aucun OF actif sur la machine : seule l'immobilisation est chiffrée"
    elif article is None or article.valeur_unitaire is None:
        raison = "valeur unitaire de l'article inconnue : production perdue non chiffrée"
    else:
        perte_production = pieces_perdues * float(article.valeur_unitaire)

    immobilisation = duree_minutes / 60 * float(machine.cout_horaire or 0)
    return CoutArret(
        duree_minutes=duree_minutes,
        perte_production_tnd=perte_production,
        immobilisation_tnd=immobilisation,
        pieces_perdues=pieces_perdues,
        raison_non_chiffree=raison,
    )


@dataclass(frozen=True)
class CoutOF:
    """Coût de production d'un OF : matières + immobilisation machine.

    Les rebuts ne sont PAS ajoutés au coût matières (la consommation MP couvre
    déjà tout ce qui a été produit, bon ou rejeté) : ils sont valorisés à part,
    à la valeur commerciale de l'article, comme une perte distincte du coût de
    revient — pour ne jamais les compter deux fois.
    """

    of_numero: str
    cout_matieres_tnd: float | None  # None si aucune consommation chiffrable
    cout_machine_tnd: float
    duree_heures: float
    valeur_rebuts_tnd: float | None
    quantite_bonne: int
    quantite_rejetee: int
    raisons_non_chiffrees: list[str] = field(default_factory=list)

    @property
    def cout_production_tnd(self) -> float:
        return (self.cout_matieres_tnd or 0.0) + self.cout_machine_tnd

    @property
    def cout_unitaire_tnd(self) -> float | None:
        total_produit = self.quantite_bonne + self.quantite_rejetee
        if total_produit <= 0:
            return None
        return self.cout_production_tnd / total_produit

    def resume(self) -> str:
        """Phrase prête à afficher à l'opérateur."""
        detail = []
        if self.cout_matieres_tnd is not None:
            detail.append(f"{format_tnd(self.cout_matieres_tnd)} matières")
        detail.append(
            f"{format_tnd(self.cout_machine_tnd)} immobilisation machine "
            f"({self.duree_heures:.1f} h)"
        )
        texte = f"≈ {format_tnd(self.cout_production_tnd)} de coût de production (" + " + ".join(detail) + ")"
        if self.valeur_rebuts_tnd:
            texte += (
                f" ; perte rebuts ≈ {format_tnd(self.valeur_rebuts_tnd)} "
                f"({self.quantite_rejetee} u.)"
            )
        return texte

    def artifact(self) -> dict:
        return {
            "of_numero": self.of_numero,
            "cout_matieres_tnd": self.cout_matieres_tnd,
            "cout_machine_tnd": round(self.cout_machine_tnd, 2),
            "duree_heures": round(self.duree_heures, 2),
            "cout_production_tnd": round(self.cout_production_tnd, 2),
            "cout_unitaire_tnd": (
                round(self.cout_unitaire_tnd, 3) if self.cout_unitaire_tnd is not None else None
            ),
            "valeur_rebuts_tnd": self.valeur_rebuts_tnd,
            "quantite_bonne": self.quantite_bonne,
            "quantite_rejetee": self.quantite_rejetee,
            "raisons_non_chiffrees": self.raisons_non_chiffrees,
        }


def calculer_cout_of(db: Session, of: OrdreFabrication) -> CoutOF:
    """Chiffre le coût de production d'un OF.

    Matières : somme des consommations FEFO de l'OF (`OFConsommationMP`) ×
    `MatierePremiere.prix_unitaire_tnd` de chaque MP (non chiffrée si un prix
    manque). Machine : durée réelle de l'OF (début → fin réelle, ou maintenant
    s'il tourne encore) × coût horaire moyen des machines l'ayant exécuté,
    retrouvées via l'historique `MachineEvent` (fiable même après la fin de
    l'OF, contrairement à `Machine.ordre_fabrication_id` qui ne pointe que sur
    l'affectation courante).
    """
    raisons: list[str] = []

    cout_matieres: float | None = 0.0
    if not of.consommations:
        cout_matieres = None
        raisons.append("aucune consommation de matière enregistrée pour cet OF")
    else:
        prix_manquant = False
        for c in of.consommations:
            prix = c.matiere_premiere.prix_unitaire_tnd
            if prix is None:
                prix_manquant = True
                continue
            cout_matieres += float(c.quantite_consommee) * float(prix)
        if prix_manquant:
            raisons.append(
                "prix unitaire manquant pour au moins une matière première : "
                "coût matières sous-estimé"
            )

    jusqua = of.date_fin_reelle or datetime.utcnow()
    depuis = of.date_debut_reelle
    duree_heures = 0.0
    if depuis is not None:
        duree_heures = max(0.0, (jusqua - depuis).total_seconds() / 3600)

    machine_ids = list(
        db.execute(
            select(MachineEvent.machine_id)
            .where(MachineEvent.ordre_fabrication_id == of.id)
            .distinct()
        ).scalars()
    )
    if not machine_ids:
        machine_ids = list(
            db.execute(
                select(Machine.id).where(Machine.ordre_fabrication_id == of.id)
            ).scalars()
        )

    cout_horaire_moyen = 0.0
    if machine_ids:
        couts_horaires = list(
            db.execute(
                select(Machine.cout_horaire).where(Machine.id.in_(machine_ids))
            ).scalars()
        )
        if couts_horaires:
            cout_horaire_moyen = float(sum(couts_horaires)) / len(couts_horaires)
    elif depuis is not None:
        raisons.append("aucune machine identifiée pour cet OF : immobilisation non chiffrée")

    cout_machine = duree_heures * cout_horaire_moyen

    valeur_rebuts: float | None = None
    if of.quantite_rejetee:
        if of.article.valeur_unitaire is not None:
            valeur_rebuts = float(of.quantite_rejetee) * float(of.article.valeur_unitaire)
        else:
            raisons.append("valeur unitaire de l'article inconnue : perte des rebuts non chiffrée")

    return CoutOF(
        of_numero=of.numero,
        cout_matieres_tnd=cout_matieres,
        cout_machine_tnd=cout_machine,
        duree_heures=duree_heures,
        valeur_rebuts_tnd=valeur_rebuts,
        quantite_bonne=int(of.quantite_bonne),
        quantite_rejetee=int(of.quantite_rejetee),
        raisons_non_chiffrees=raisons,
    )
