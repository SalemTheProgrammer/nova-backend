"""Rendement des opérateurs : classement et fiche individuelle (lecture seule)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from app.integrations.divatex import kpi
from app.integrations.divatex.connexion import requete
from app.integrations.divatex.referentiel import Operateur, lister_lignes, operateur

_PERIODE_MAX_JOURS = 31

# Un scan d'opération = IdPhase 0 ; la majoration est choisie en Python selon le réglage.
_SELECT_SCANS = (
    "SELECT IDEmploye, Date, IDChaineMontage, COUNT(*) AS nb_scans, "
    "SUM(Quantite) AS pieces, "
    "SUM(Temps * Quantite * COALESCE(NULLIF(Majoration, 0), 1)) AS avec_maj, "
    "SUM(Temps * Quantite) AS sans_maj FROM lectures "
)
_GROUP_SCANS = " GROUP BY IDEmploye, Date, IDChaineMontage"


@dataclass(frozen=True)
class RendementOperateur:
    operateur: Operateur
    ligne: str | None
    pieces_operations: int
    minutes_produites: float
    minutes_presence: float
    rendement: float | None
    presence_estimee: bool


@dataclass(frozen=True)
class FicheOperateur:
    operateur: Operateur
    du: date
    au: date
    par_jour: list[tuple[date, float | None]]
    operations: list[tuple[str, int]]
    total: RendementOperateur


def _verifier_periode(du: date, au: date) -> None:
    if du > au:
        raise ValueError("Période invalide.")
    if (au - du).days + 1 > _PERIODE_MAX_JOURS:
        raise ValueError("Période limitée à 31 jours.")


def _en_date(valeur) -> date:
    if isinstance(valeur, str):
        return date.fromisoformat(valeur[:10])
    if isinstance(valeur, datetime):
        return valeur.date()
    return valeur


def _presences(du: date, au: date) -> dict[tuple[int, date], float]:
    return {
        (int(r["IDEmploye"]), _en_date(r["Date"])): float(r["minutes"] or 0)
        for r in requete(
            "SELECT IDEmploye, Date, minutes FROM presence "
            "WHERE Date BETWEEN :du AND :au",
            du=du.isoformat(), au=au.isoformat(),
        )
    }


def _synthese(
    op: Operateur,
    scans: list[dict],
    presences: dict[tuple[int, date], float],
    codes_lignes: dict[int, str],
    natures: dict[int, str],
) -> tuple[RendementOperateur, dict[date, tuple[float, float]]]:
    """Agrège les scans d'un opérateur ; renvoie aussi (produites, présence) par jour.

    Rendement : seuls les scans des lignes de confection comptent (ruling R17) ; sur
    les autres lignes le temps d'opération est un temps machine. Les jours sans scan de
    confection figurent dans le détail avec (0, 0), donc sans rendement.
    """
    p = kpi.parametres_rendement()
    cle_minutes = "avec_maj" if p.appliquer_majoration else "sans_maj"
    par_jour: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    scans_par_ligne: dict[int, int] = defaultdict(int)
    pieces = 0
    for r in scans:
        jour = _en_date(r["Date"])
        cumul = par_jour[jour]
        if natures.get(int(r["IDChaineMontage"])) == "confection":
            cumul[0] += float(r[cle_minutes] or 0) * p.facteur_temps
            cumul[2] = 1.0
        pieces += int(r["pieces"] or 0)
        scans_par_ligne[int(r["IDChaineMontage"])] += int(r["nb_scans"] or 0)
    estimee = False
    for jour, cumul in par_jour.items():
        if not cumul[2]:
            continue
        if (op.id, jour) in presences:
            cumul[1] = presences[(op.id, jour)]
        else:
            cumul[1] = float(p.duree_poste_min)
            estimee = True
    ligne = None
    if scans_par_ligne:
        ligne_id = max(scans_par_ligne, key=lambda k: scans_par_ligne[k])
        ligne = codes_lignes.get(ligne_id)
    produites = sum(c[0] for c in par_jour.values())
    presence = sum(c[1] for c in par_jour.values())
    total = RendementOperateur(
        operateur=op, ligne=ligne, pieces_operations=pieces,
        minutes_produites=produites, minutes_presence=presence,
        rendement=kpi.rendement(produites, presence), presence_estimee=estimee,
    )
    return total, {j: (c[0], c[1]) for j, c in par_jour.items()}


def classement_operateurs(
    du: date, au: date, *, ligne_id: int | None = None, groupe_id: int | None = None
) -> list[RendementOperateur]:
    _verifier_periode(du, au)
    lignes = lister_lignes()
    codes = {l.id: l.code for l in lignes}
    natures = {l.id: l.nature for l in lignes}
    params: dict = {"du": du.isoformat(), "au": au.isoformat()}
    if ligne_id is not None:
        filtre = "WHERE IDChaineMontage = :ligne AND Date BETWEEN :du AND :au"
        params["ligne"] = ligne_id
    elif groupe_id is not None:
        ids = [l.id for l in lignes if l.groupe is not None and l.groupe.id == groupe_id]
        if not ids:
            return []
        noms = []
        for i, lid in enumerate(ids):
            params[f"l{i}"] = lid
            noms.append(f":l{i}")
        filtre = (
            f"WHERE IDChaineMontage IN ({', '.join(noms)}) "
            "AND Date BETWEEN :du AND :au"
        )
    else:
        filtre = "WHERE Date BETWEEN :du AND :au"
    scans = requete(_SELECT_SCANS + filtre + " AND IdPhase = 0" + _GROUP_SCANS, **params)

    par_employe: dict[int, list[dict]] = defaultdict(list)
    for r in scans:
        par_employe[int(r["IDEmploye"])].append(r)
    presences = _presences(du, au)
    resultat = []
    for emp_id, lignes_emp in par_employe.items():
        op = operateur(emp_id)
        if op is None:
            continue
        total, _ = _synthese(op, lignes_emp, presences, codes, natures)
        resultat.append(total)
    resultat.sort(
        key=lambda x: (x.rendement is None, -(x.rendement or 0.0), x.operateur.nom_complet)
    )
    return resultat


def fiche_operateur(operateur_id: int, du: date, au: date) -> FicheOperateur | None:
    _verifier_periode(du, au)
    op = operateur(operateur_id)
    if op is None:
        return None
    params = {"emp": operateur_id, "du": du.isoformat(), "au": au.isoformat()}
    scans = requete(
        _SELECT_SCANS
        + "WHERE IDEmploye = :emp AND Date BETWEEN :du AND :au AND IdPhase = 0"
        + _GROUP_SCANS,
        **params,
    )
    lignes = lister_lignes()
    codes = {l.id: l.code for l in lignes}
    natures = {l.id: l.nature for l in lignes}
    total, jours = _synthese(op, scans, _presences(du, au), codes, natures)
    par_jour = [(j, kpi.rendement(prod, pres)) for j, (prod, pres) in sorted(jours.items())]
    operations = [
        (r["Operation"] or "", int(r["pieces"] or 0))
        for r in requete(
            "SELECT o.Operation AS Operation, SUM(l.Quantite) AS pieces FROM lectures l "
            "JOIN operation o ON o.IDOperation = l.IDOperation "
            "WHERE l.IDEmploye = :emp AND l.Date BETWEEN :du AND :au AND l.IdPhase = 0 "
            "GROUP BY o.IDOperation, o.Operation ORDER BY pieces DESC, o.Operation",
            **params,
        )
    ]
    return FicheOperateur(
        operateur=op, du=du, au=au, par_jour=par_jour, operations=operations, total=total
    )
