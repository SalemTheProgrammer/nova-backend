"""Référentiel DIVA : chaînes, groupes, phases, opérateurs (lecture seule)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.integrations.divatex.connexion import cache, requete

Nature = Literal["tricotage", "confection", "finition"]

_TTL_REFERENTIEL_S = 600


@dataclass(frozen=True)
class Groupe:
    id: int
    nom: str


@dataclass(frozen=True)
class Ligne:
    id: int
    code: str
    nom: str
    groupe: Groupe | None
    nature: Nature


@dataclass(frozen=True)
class Phase:
    id: int
    nom: str


@dataclass(frozen=True)
class Operateur:
    id: int
    matricule: str
    nom: str
    prenom: str
    ligne_id: int | None

    @property
    def nom_complet(self) -> str:
        return f"{self.prenom} {self.nom}"


def _sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def _sans_zeros_initiaux(texte: str) -> str:
    return re.sub(r"(?<!\d)0+(?=\d)", "", texte)


def normaliser_code(texte: str) -> str:
    """``"chaîne 1 ligne 2"``, ``"ch1 l2"`` et ``"CH1-L2"`` donnent ``"CH1L2"``."""
    t = _sans_accents(texte).upper()
    t = t.replace("CHAINE", "CH").replace("LIGNE", "L")
    t = re.sub(r"[\s\-_]+", "", t)
    return _sans_zeros_initiaux(t)


def _cle_groupe(texte: str) -> str:
    t = _sans_accents(texte).upper()
    t = re.sub(r"[\s\-_]+", " ", t).strip()
    return _sans_zeros_initiaux(t)


def _nature(nom_groupe: str | None) -> Nature:
    nom = (nom_groupe or "").upper()
    if "TRICOT" in nom:
        return "tricotage"
    if "CHAINE" in _sans_accents(nom):
        return "confection"
    return "finition"


@cache(ttl_s=_TTL_REFERENTIEL_S)
def lister_groupes() -> list[Groupe]:
    lignes = requete("SELECT IDGroupe, Groupe FROM groupechaine ORDER BY IDGroupe")
    return [Groupe(id=int(r["IDGroupe"]), nom=r["Groupe"] or "") for r in lignes]


@cache(ttl_s=_TTL_REFERENTIEL_S)
def lister_lignes() -> list[Ligne]:
    groupes = {g.id: g for g in lister_groupes()}
    lignes = requete(
        "SELECT IDChaineMontage, Code, ChaineMontage, IDGroupe FROM chainemontage "
        "WHERE Etat = 1 ORDER BY IDChaineMontage"
    )
    resultat = []
    for r in lignes:
        groupe = groupes.get(r["IDGroupe"])
        resultat.append(
            Ligne(
                id=int(r["IDChaineMontage"]),
                code=r["Code"] or "",
                nom=r["ChaineMontage"] or "",
                groupe=groupe,
                nature=_nature(groupe.nom if groupe else None),
            )
        )
    return resultat


def trouver_ligne(texte: str) -> Ligne | None:
    cible = normaliser_code(texte)
    if not cible:
        return None
    for ligne in lister_lignes():
        if normaliser_code(ligne.code) == cible:
            return ligne
    return None


def trouver_groupe(texte: str) -> Groupe | None:
    cible = _cle_groupe(texte)
    if not cible:
        return None
    groupes = lister_groupes()
    for g in groupes:
        if _cle_groupe(g.nom) == cible:
            return g
    approchants = [g for g in groupes if cible in _cle_groupe(g.nom)]
    return approchants[0] if len(approchants) == 1 else None


@cache(ttl_s=_TTL_REFERENTIEL_S)
def lister_phases() -> list[Phase]:
    lignes = requete("SELECT IDPhase, Phase FROM phase ORDER BY IDPhase")
    return [Phase(id=int(r["IDPhase"]), nom=r["Phase"] or "") for r in lignes]


def nom_phase(phase_id: int) -> str:
    for phase in lister_phases():
        if phase.id == phase_id:
            return phase.nom
    return f"Phase {phase_id}"


# Jamais de SELECT * ni de colonnes personnelles : seulement ces six champs.
_COLONNES_EMPLOYE = "IDEmploye, Matricule, Nom, Prenom, IDChaineMontage, IDPosteTravail"


def _operateur(r: dict) -> Operateur:
    return Operateur(
        id=int(r["IDEmploye"]),
        matricule=str(r["Matricule"]) if r["Matricule"] is not None else "",
        nom=r["Nom"] or "",
        prenom=r["Prenom"] or "",
        ligne_id=int(r["IDChaineMontage"]) if r["IDChaineMontage"] else None,
    )


def echapper_like(mot: str) -> str:
    """Échappe ``!``, ``%`` et ``_`` pour un ``LIKE ... ESCAPE '!'``."""
    return mot.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _motif_like(mot: str) -> str:
    return f"%{echapper_like(mot)}%"


def rechercher_operateurs(texte: str, limite: int = 10) -> list[Operateur]:
    texte = (texte or "").strip()
    if not texte:
        return []
    # DIVA stocke le matricule en entier : la recherche exacte ne se fait que sur
    # un texte numérique (MariaDB convertirait « Ben » en 0 et le comparerait).
    if texte.isascii() and texte.isdigit() and len(texte) <= 10:
        exact = requete(
            f"SELECT {_COLONNES_EMPLOYE} FROM employe WHERE Matricule = :m "
            "ORDER BY Nom, Prenom LIMIT :limite",
            m=int(texte), limite=limite,
        )
        if exact:
            return [_operateur(r) for r in exact]
    mots = texte.split()
    params: dict = {"limite": limite}
    conditions = []
    for i, mot in enumerate(mots):
        params[f"t{i}"] = _motif_like(mot)
        conditions.append(
            f"(Nom LIKE :t{i} ESCAPE '!' OR Prenom LIKE :t{i} ESCAPE '!')"
        )
    lignes = requete(
        f"SELECT {_COLONNES_EMPLOYE} FROM employe WHERE {' AND '.join(conditions)} "
        "ORDER BY Nom, Prenom LIMIT :limite",
        **params,
    )
    return [_operateur(r) for r in lignes]


def operateur(operateur_id: int) -> Operateur | None:
    lignes = requete(
        f"SELECT {_COLONNES_EMPLOYE} FROM employe WHERE IDEmploye = :i", i=operateur_id
    )
    return _operateur(lignes[0]) if lignes else None


_TAILLE_LOT_IDS = 100


def operateurs_par_ids(ids: list[int]) -> dict[int, Operateur]:
    """Opérateurs par id, en lots d'au plus 100 ids liés (une requête par lot)."""
    uniques = sorted({int(i) for i in ids})
    resultat: dict[int, Operateur] = {}
    for debut in range(0, len(uniques), _TAILLE_LOT_IDS):
        lot = uniques[debut : debut + _TAILLE_LOT_IDS]
        params = {f"i{k}": v for k, v in enumerate(lot)}
        marqueurs = ", ".join(f":i{k}" for k in range(len(lot)))
        for r in requete(
            f"SELECT {_COLONNES_EMPLOYE} FROM employe WHERE IDEmploye IN ({marqueurs})",
            **params,
        ):
            op = _operateur(r)
            resultat[op.id] = op
    return resultat
