"""Ordres de fabrication : recherche et avancement par phase (lecture seule)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from app.core.config import get_settings
from app.integrations.divatex.connexion import requete
from app.integrations.divatex.referentiel import (
    echapper_like,
    lister_lignes,
    lister_phases,
    nom_phase,
)


@dataclass(frozen=True)
class OFResume:
    id: int
    numero: str
    client: str | None
    article: str | None
    quantite: int
    date_export_prevue: date | None
    ouvert: bool
    avancement_final: float


@dataclass(frozen=True)
class AvancementPhase:
    phase_id: int
    nom: str
    quantite: int
    pct: float


@dataclass(frozen=True)
class AvancementOF:
    of: OFResume
    phases: list[AvancementPhase]
    tailles: list[tuple[str, dict[int, int]]]
    lignes: list[str]
    derniere_lecture: date | None


def en_date(valeur) -> date | None:
    if valeur is None:
        return None
    if isinstance(valeur, str):
        return date.fromisoformat(valeur[:10])
    if isinstance(valeur, datetime):
        return valeur.date()
    return valeur


_SELECT_OF = (
    "SELECT o.IDOFabrication, o.OFAbrication, c.Client, a.Code AS CodeArticle, "
    "o.Quantite, o.Etat, o.DateExportPrevue FROM ofabrication o "
    "LEFT JOIN client c ON c.IDClient = o.IDClient "
    "LEFT JOIN article a ON a.IDArticle = o.IDArticle"
)


_TAILLE_LOT_OF = 100


def _totaux_phase_finale(of_ids: list[int]) -> dict[int, int]:
    """Quantité passée en phase finale par OF ; filtre d'abord sur IDOFabrication.

    Les identifiants sont traités par lots (une grosse liste IN ralentit fortement
    la base à froid) ; les totaux sont fusionnés ici.
    """
    totaux: dict[int, int] = {}
    phase = get_settings().divatex_phase_finale
    for debut in range(0, len(of_ids), _TAILLE_LOT_OF):
        lot = of_ids[debut:debut + _TAILLE_LOT_OF]
        marques = ", ".join(f":o{i}" for i in range(len(lot)))
        params = {f"o{i}": of_id for i, of_id in enumerate(lot)}
        lignes = requete(
            f"SELECT IDOFabrication, SUM(Quantite) AS total FROM lectures "
            f"WHERE IDOFabrication IN ({marques}) AND IdPhase = :phase_finale "
            "GROUP BY IDOFabrication",
            phase_finale=phase, **params,
        )
        for r in lignes:
            of_id = int(r["IDOFabrication"])
            totaux[of_id] = totaux.get(of_id, 0) + int(r["total"] or 0)
    return totaux


def charger_resumes(
    conditions: str = "", ordre: str = "o.OFAbrication", limite: int | None = None,
    **params,
) -> list[OFResume]:
    """OF correspondant à ``conditions`` (fragment SQL statique, valeurs liées)."""
    sql = _SELECT_OF + (f" WHERE {conditions}" if conditions else "") + f" ORDER BY {ordre}"
    if limite is not None:
        sql += " LIMIT :limite"
        params["limite"] = limite
    lignes = requete(sql, **params)
    totaux = _totaux_phase_finale([int(r["IDOFabrication"]) for r in lignes])
    resultat = []
    for r in lignes:
        of_id = int(r["IDOFabrication"])
        quantite = int(r["Quantite"] or 0)
        avancement = min(totaux.get(of_id, 0) / quantite, 1.0) if quantite > 0 else 0.0
        resultat.append(
            OFResume(
                id=of_id, numero=r["OFAbrication"] or "", client=r["Client"],
                article=r["CodeArticle"], quantite=quantite,
                date_export_prevue=en_date(r["DateExportPrevue"]),
                ouvert=r["Etat"] == 1, avancement_final=avancement,
            )
        )
    return resultat


def rechercher_of(
    texte: str = "", *, ouverts_seulement: bool = False,
    client_id: int | None = None, limite: int = 20,
) -> list[OFResume]:
    texte = (texte or "").strip()
    conditions = []
    params: dict = {}
    ordre = "o.DateExportPrevue, o.OFAbrication"
    if texte:
        params["t"] = echapper_like(texte) + "%"
        params["exact"] = texte
        conditions.append(
            "(o.OFAbrication LIKE :t ESCAPE '!' OR o.NumCmdClient LIKE :t ESCAPE '!' "
            "OR o.PO LIKE :t ESCAPE '!' OR a.Code LIKE :t ESCAPE '!')"
        )
        ordre = (
            "CASE WHEN o.OFAbrication = :exact THEN 0 "
            "WHEN o.OFAbrication LIKE :t ESCAPE '!' THEN 1 ELSE 2 END, o.OFAbrication"
        )
    if ouverts_seulement:
        conditions.append("o.Etat = 1")
    if client_id is not None:
        conditions.append("o.IDClient = :client")
        params["client"] = client_id
    return charger_resumes(" AND ".join(conditions), ordre, limite, **params)


def of_par_id(of_id: int) -> OFResume | None:
    trouves = charger_resumes("o.IDOFabrication = :id", id=of_id)
    return trouves[0] if trouves else None


def avancement_of(of_id: int) -> AvancementOF | None:
    of = of_par_id(of_id)
    if of is None:
        return None
    lectures = requete(
        "SELECT IdPhase, LibTaille, SUM(Quantite) AS total, MAX(Date) AS derniere "
        "FROM lectures WHERE IDOFabrication = :of AND IdPhase > 0 "
        "GROUP BY IdPhase, LibTaille",
        of=of_id,
    )
    par_phase: dict[int, int] = defaultdict(int)
    par_taille: dict[str, dict[int, int]] = defaultdict(dict)
    derniere: date | None = None
    for r in lectures:
        phase, total = int(r["IdPhase"]), int(r["total"] or 0)
        par_phase[phase] += total
        taille = r["LibTaille"] or ""
        par_taille[taille][phase] = par_taille[taille].get(phase, 0) + total
        jour = en_date(r["derniere"])
        if jour and (derniere is None or jour > derniere):
            derniere = jour

    ids_phases = sorted({p.id for p in lister_phases()} | set(par_phase))
    phases = [
        AvancementPhase(
            phase_id=pid, nom=nom_phase(pid), quantite=par_phase.get(pid, 0),
            pct=par_phase.get(pid, 0) / of.quantite if of.quantite > 0 else 0.0,
        )
        for pid in ids_phases
    ]

    codes = {l.id: l.code for l in lister_lignes()}
    ids_lignes = sorted(
        int(r["IDChaineMontage"])
        for r in requete(
            "SELECT DISTINCT IDChaineMontage FROM lectures "
            "WHERE IDOFabrication = :of AND IDChaineMontage > 0",
            of=of_id,
        )
    )
    return AvancementOF(
        of=of, phases=phases, tailles=sorted(par_taille.items()),
        lignes=[codes[i] for i in ids_lignes if i in codes], derniere_lecture=derniere,
    )


def of_de_ligne(ligne_id: int, jour: date) -> list[OFResume]:
    """OF ayant au moins une lecture sur cette ligne ce jour-là."""
    return charger_resumes(
        "o.IDOFabrication IN (SELECT DISTINCT IDOFabrication FROM lectures "
        "WHERE IDChaineMontage = :ligne AND Date = :jour)",
        ligne=ligne_id, jour=jour.isoformat(),
    )
