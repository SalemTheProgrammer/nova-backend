"""Production des lignes face à l'objectif, rendement, tendance et situation d'atelier."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core.config import get_settings
from app.integrations.divatex import commandes, kpi
from app.integrations.divatex.connexion import requete
from app.integrations.divatex.referentiel import Ligne, lister_lignes

_PHASE_TRICOTAGE = 1
_PHASE_CONFECTION = 5


@dataclass(frozen=True)
class ProductionLigne:
    ligne: Ligne
    jour: date
    sortie: int
    objectif: int | None
    objectif_date: date | None
    objectif_perime: bool
    taux: float | None
    effectif: int
    minutes_produites: float
    minutes_presence: float
    rendement: float | None
    presence_estimee: bool
    a_des_lectures: bool


@dataclass(frozen=True)
class PointJour:
    jour: date
    sortie: int
    objectif: int


@dataclass(frozen=True)
class PointHeure:
    heure: int
    pieces: int


@dataclass(frozen=True)
class SituationAtelier:
    jour: date
    sortie: int
    objectif: int
    taux: float | None
    rendement_moyen: float | None
    lignes_sous_objectif: list[str]
    lignes_au_dessus: list[str]
    nb_of_en_retard: int
    a_des_lectures: bool


def _en_date(valeur) -> date | None:
    if valeur is None:
        return None
    if isinstance(valeur, str):
        return date.fromisoformat(valeur[:10])
    if isinstance(valeur, datetime):
        return valeur.date()
    return valeur


def _en_datetime(valeur) -> datetime | None:
    if valeur is None:
        return None
    if isinstance(valeur, str):
        return datetime.fromisoformat(valeur.replace("T", " ")[:19])
    if isinstance(valeur, datetime):
        return valeur
    return None


def _objectifs(jour: date) -> dict[int, tuple[date, int, int | None]]:
    """Dernier objectif en vigueur par ligne : (date, phase de sortie, objectif)."""
    lignes = requete(
        "SELECT IDChaineMontage, Date, IDPhaseSortie, ObjectifSortie FROM objectifchaine "
        "WHERE Date <= :jour ORDER BY Date, IDObjectifChaine",
        jour=jour.isoformat(),
    )
    courant: dict[int, tuple[date, int, int | None]] = {}
    for r in lignes:
        phase = int(r["IDPhaseSortie"]) if r["IDPhaseSortie"] else _PHASE_CONFECTION
        obj = int(r["ObjectifSortie"]) if r["ObjectifSortie"] is not None else None
        courant[int(r["IDChaineMontage"])] = (_en_date(r["Date"]), phase, obj)
    return courant


def _phase_par_defaut(ligne: Ligne | None) -> int:
    if ligne is None:
        return _PHASE_CONFECTION
    if ligne.nature == "tricotage":
        return _PHASE_TRICOTAGE
    if ligne.nature == "finition":
        return get_settings().divatex_phase_finale
    return _PHASE_CONFECTION


def production_lignes(jour: date, groupe_id: int | None = None) -> list[ProductionLigne]:
    p = kpi.parametres_rendement()
    iso = jour.isoformat()
    lignes = [
        l for l in lister_lignes()
        if groupe_id is None or (l.groupe is not None and l.groupe.id == groupe_id)
    ]
    objectifs = _objectifs(jour)

    sorties: dict[tuple[int, int], int] = defaultdict(int)
    avec_lectures: set[int] = set()
    for r in requete(
        "SELECT IDChaineMontage, IdPhase, SUM(Quantite) AS pieces FROM lectures "
        "WHERE Date = :jour GROUP BY IDChaineMontage, IdPhase",
        jour=iso,
    ):
        ligne_id = int(r["IDChaineMontage"])
        avec_lectures.add(ligne_id)
        sorties[(ligne_id, int(r["IdPhase"]))] += int(r["pieces"] or 0)

    scans: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for r in requete(
        "SELECT IDChaineMontage, IDEmploye, SUM(Temps * Quantite * COALESCE(NULLIF(Majoration, 0), 1)) AS avec_maj, "
        "SUM(Temps * Quantite) AS sans_maj FROM lectures "
        "WHERE Date = :jour AND IdPhase = 0 GROUP BY IDChaineMontage, IDEmploye",
        jour=iso,
    ):
        scans[int(r["IDChaineMontage"])].append(
            (int(r["IDEmploye"]), float(r["avec_maj"] or 0), float(r["sans_maj"] or 0))
        )

    presence = {
        int(r["IDEmploye"]): float(r["minutes"] or 0)
        for r in requete(
            "SELECT IDEmploye, minutes FROM presence WHERE Date = :jour", jour=iso
        )
    }

    # la majoration est choisie ici : Σ temps×qté×maj ou Σ temps×qté (déjà appliquée)
    sans_majoration = kpi.ParametresRendement(p.facteur_temps, False, p.duree_poste_min)

    resultat = []
    for ligne in lignes:
        obj_date, phase, objectif = objectifs.get(
            ligne.id, (None, _phase_par_defaut(ligne), None)
        )
        sortie = sorties.get((ligne.id, phase), 0)
        a_lectures = ligne.id in avec_lectures
        taux = kpi.taux_atteinte(sortie, objectif) if a_lectures else None
        effectif = 0
        min_prod = min_pres = 0.0
        rend = None
        estimee = False
        if ligne.nature == "confection":
            employes = scans.get(ligne.id, [])
            effectif = len(employes)
            min_prod = kpi.minutes_produites(
                [((avec if p.appliquer_majoration else sans), 1, 1.0)
                 for _, avec, sans in employes],
                sans_majoration,
            )
            for emp_id, _, _ in employes:
                if emp_id in presence:
                    min_pres += presence[emp_id]
                else:
                    min_pres += p.duree_poste_min
                    estimee = True
            rend = kpi.rendement(min_prod, min_pres)
        resultat.append(
            ProductionLigne(
                ligne=ligne, jour=jour, sortie=sortie, objectif=objectif,
                objectif_date=obj_date,
                objectif_perime=bool(obj_date and kpi.objectif_perime(obj_date, jour)),
                taux=taux, effectif=effectif, minutes_produites=min_prod,
                minutes_presence=min_pres, rendement=rend,
                presence_estimee=estimee, a_des_lectures=a_lectures,
            )
        )
    return resultat


def tendance(au: date, jours: int = 7, groupe_id: int | None = None) -> list[PointJour]:
    points = []
    for i in range(jours - 1, -1, -1):
        jour = au - timedelta(days=i)
        confection = [
            l for l in production_lignes(jour, groupe_id) if l.ligne.nature == "confection"
        ]
        points.append(
            PointJour(
                jour=jour,
                sortie=sum(l.sortie for l in confection),
                objectif=sum(l.objectif or 0 for l in confection),
            )
        )
    return points


def courbe_horaire(ligne_id: int, jour: date) -> list[PointHeure]:
    ligne = next((l for l in lister_lignes() if l.id == ligne_id), None)
    phase = _phase_par_defaut(ligne)
    objectif = _objectifs(jour).get(ligne_id)
    if objectif:
        phase = objectif[1]
    pieces: dict[int, int] = defaultdict(int)
    for r in requete(
        "SELECT SaisiLe, Quantite FROM lectures "
        "WHERE Date = :jour AND IDChaineMontage = :ligne AND IdPhase = :phase",
        jour=jour.isoformat(), ligne=ligne_id, phase=phase,
    ):
        saisi = _en_datetime(r["SaisiLe"])
        if saisi is not None:
            pieces[saisi.hour] += int(r["Quantite"] or 0)
    return [PointHeure(heure=h, pieces=n) for h, n in sorted(pieces.items())]


def situation_atelier(jour: date) -> SituationAtelier:
    confection = [l for l in production_lignes(jour) if l.ligne.nature == "confection"]
    sortie = sum(l.sortie for l in confection)
    objectif = sum(l.objectif or 0 for l in confection)
    a_lectures = any(l.a_des_lectures for l in confection)
    avec_taux = [l for l in confection if l.taux is not None]
    return SituationAtelier(
        jour=jour,
        sortie=sortie,
        objectif=objectif,
        taux=kpi.taux_atteinte(sortie, objectif) if a_lectures else None,
        rendement_moyen=kpi.rendement(
            sum(l.minutes_produites for l in confection),
            sum(l.minutes_presence for l in confection),
        ),
        lignes_sous_objectif=[l.ligne.code for l in avec_taux if l.taux < 1],
        lignes_au_dessus=[l.ligne.code for l in avec_taux if l.taux >= 1],
        nb_of_en_retard=len([
            r for r in commandes.of_a_risque(jour) if r.statut == "en_retard"
        ]),
        a_des_lectures=a_lectures,
    )
