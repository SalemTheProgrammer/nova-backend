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
    sortie_avec_objectif: int
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
        if valeur.startswith("0000"):  # date nulle MariaDB (pymysql la rend en None)
            return None
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
        if _en_date(r["Date"]) is None:  # objectif sans date : inexploitable
            continue
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
    """Sortie et objectif de la confection par jour : deux requêtes pour toute la période."""
    du = au - timedelta(days=jours - 1)
    confection = [
        l for l in lister_lignes()
        if l.nature == "confection"
        and (groupe_id is None or (l.groupe is not None and l.groupe.id == groupe_id))
    ]

    totaux: dict[tuple[date, int, int], int] = defaultdict(int)
    avec_lectures: set[tuple[date, int]] = set()  # (jour, ligne) ayant des sorties de phase
    for r in requete(
        "SELECT Date, IDChaineMontage, IdPhase, SUM(Quantite) AS pieces FROM lectures "
        "WHERE Date BETWEEN :du AND :au AND IdPhase > 0 "
        "GROUP BY Date, IDChaineMontage, IdPhase",
        du=du.isoformat(), au=au.isoformat(),
    ):
        jour_lu = _en_date(r["Date"])
        if jour_lu is None:
            continue
        totaux[(jour_lu, int(r["IDChaineMontage"]), int(r["IdPhase"]))] += int(r["pieces"] or 0)
        avec_lectures.add((jour_lu, int(r["IDChaineMontage"])))

    historique: dict[int, list[tuple[date, int, int | None]]] = defaultdict(list)
    for r in requete(
        "SELECT IDChaineMontage, Date, IDPhaseSortie, ObjectifSortie FROM objectifchaine "
        "WHERE Date <= :au ORDER BY Date, IDObjectifChaine",
        au=au.isoformat(),
    ):
        if _en_date(r["Date"]) is None:  # objectif sans date : inexploitable
            continue
        phase = int(r["IDPhaseSortie"]) if r["IDPhaseSortie"] else _PHASE_CONFECTION
        obj = int(r["ObjectifSortie"]) if r["ObjectifSortie"] is not None else None
        historique[int(r["IDChaineMontage"])].append((_en_date(r["Date"]), phase, obj))

    points = []
    for i in range(jours - 1, -1, -1):
        jour = au - timedelta(days=i)
        sortie = objectif = 0
        for ligne in confection:
            if (jour, ligne.id) not in avec_lectures:
                continue
            phase, obj = _phase_par_defaut(ligne), None
            for date_obj, ph, ob in historique.get(ligne.id, ()):
                if date_obj > jour:
                    break
                phase, obj = ph, ob
            if obj is None:  # sans objectif : exclue du ratio sortie / objectif
                continue
            sortie += totaux.get((jour, ligne.id, phase), 0)
            objectif += obj
        points.append(PointJour(jour=jour, sortie=sortie, objectif=objectif))
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
    a_lectures = any(l.a_des_lectures for l in confection)
    avec_taux = [l for l in confection if l.taux is not None]
    # le taux ne compare que les chaînes ayant un objectif (et des lectures)
    comparables = [l for l in confection if l.objectif is not None and l.a_des_lectures]
    sortie_avec_objectif = sum(l.sortie for l in comparables)
    objectif = sum(l.objectif for l in comparables)
    return SituationAtelier(
        jour=jour,
        sortie=sortie,
        sortie_avec_objectif=sortie_avec_objectif,
        objectif=objectif,
        taux=kpi.taux_atteinte(sortie_avec_objectif, objectif) if a_lectures else None,
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


_PERIODE_MAX_JOURS = 31
_TAILLE_LOT_OF = 100


def pieces_par_phase(
    du: date, au: date, *, of_id: int | None = None, client_id: int | None = None
) -> list[tuple[date, int, int]]:
    """Pièces par (jour, phase) sur la période, phases de production seulement.

    Filtre d'abord sur ``Date`` (ou ``IDOFabrication`` + ``Date`` pour un OF) ;
    avec ``client_id``, les OF du client sont résolus puis lus par lots.
    """
    if du > au:
        raise ValueError("Période invalide.")
    if (au - du).days + 1 > _PERIODE_MAX_JOURS:
        raise ValueError("Période limitée à 31 jours.")
    bornes = {"du": du.isoformat(), "au": au.isoformat()}
    selection = (
        "SELECT Date, IdPhase, SUM(Quantite) AS pieces FROM lectures "
    )
    groupe = " AND IdPhase > 0 GROUP BY Date, IdPhase"
    if of_id is not None:
        lots = [[of_id]]
    elif client_id is not None:
        ids = [
            int(r["IDOFabrication"])
            for r in requete(
                "SELECT IDOFabrication FROM ofabrication WHERE IDClient = :client",
                client=client_id,
            )
        ]
        lots = [ids[i:i + _TAILLE_LOT_OF] for i in range(0, len(ids), _TAILLE_LOT_OF)]
    else:
        lots = [None]
    cumul: dict[tuple[date, int], int] = defaultdict(int)
    for lot in lots:
        if lot is None:
            lignes = requete(
                selection + "WHERE Date BETWEEN :du AND :au" + groupe, **bornes
            )
        else:
            marques = ", ".join(f":o{i}" for i in range(len(lot)))
            params = {f"o{i}": v for i, v in enumerate(lot)}
            lignes = requete(
                selection
                + f"WHERE IDOFabrication IN ({marques}) AND Date BETWEEN :du AND :au"
                + groupe,
                **bornes, **params,
            )
        for r in lignes:
            cumul[(_en_date(r["Date"]), int(r["IdPhase"]))] += int(r["pieces"] or 0)
    return [(j, p, n) for (j, p), n in sorted(cumul.items())]
