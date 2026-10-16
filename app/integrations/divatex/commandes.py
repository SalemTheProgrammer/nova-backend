"""OF à risque de livraison et consultation de commandes (lecture seule)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.integrations.divatex.connexion import date_reference, requete
from app.integrations.divatex.of import OFResume, charger_resumes, en_date
from app.integrations.divatex.referentiel import echapper_like

_FENETRE_RETARD_JOURS = 7
_HORIZON_PAR_DEFAUT_JOURS = 7


@dataclass(frozen=True)
class OFRisque:
    of: OFResume
    statut: Literal["en_retard", "a_risque"]
    jours: int  # jours de retard (positif) ou jours restants


@dataclass(frozen=True)
class CommandeDetail:
    id: int
    numero: str
    client: str | None
    date: date | None
    date_fin_visee: date | None
    quantite: int
    ofs: list[OFResume]
    a_risque: bool


def _evaluer(of: OFResume, ref: date, horizon_jours: int) -> OFRisque | None:
    d = of.date_export_prevue
    if not of.ouvert or d is None or of.avancement_final >= 1:
        return None
    if d < ref - timedelta(days=_FENETRE_RETARD_JOURS) or d > ref + timedelta(days=horizon_jours):
        return None
    if d < ref:
        return OFRisque(of, "en_retard", (ref - d).days)
    return OFRisque(of, "a_risque", (d - ref).days)


def of_a_risque(
    ref: date, horizon_jours: int = _HORIZON_PAR_DEFAUT_JOURS, client_id: int | None = None
) -> list[OFRisque]:
    conditions = "o.Etat = 1 AND o.DateExportPrevue BETWEEN :debut AND :fin"
    params: dict = {
        "debut": (ref - timedelta(days=_FENETRE_RETARD_JOURS)).isoformat(),
        "fin": (ref + timedelta(days=horizon_jours)).isoformat(),
    }
    if client_id is not None:
        conditions += " AND o.IDClient = :client"
        params["client"] = client_id
    candidats = charger_resumes(conditions, "o.DateExportPrevue, o.OFAbrication", **params)
    risques = [r for of in candidats if (r := _evaluer(of, ref, horizon_jours))]
    risques.sort(key=lambda r: (r.statut != "en_retard", r.of.date_export_prevue, r.of.numero))
    return risques


def consulter_commande(texte: str, limite: int = 20) -> list[CommandeDetail]:
    texte = (texte or "").strip()
    if not texte:
        return []
    commandes = requete(
        "SELECT cm.IDCommande, cm.Commande, cm.Date, cl.Client, cm.Qtte, cm.DateFinVise "
        "FROM commande cm LEFT JOIN client cl ON cl.IDClient = cm.IDClient "
        "WHERE cm.Commande LIKE :t ESCAPE '!' "
        "ORDER BY CASE WHEN cm.Commande = :exact THEN 0 ELSE 1 END, cm.Commande "
        "LIMIT :limite",
        t=echapper_like(texte) + "%", exact=texte, limite=limite,
    )
    if not commandes:
        return []
    ids = [int(c["IDCommande"]) for c in commandes]
    marques = ", ".join(f":c{i}" for i in range(len(ids)))
    params = {f"c{i}": cid for i, cid in enumerate(ids)}
    ofs = charger_resumes(f"o.IDCommande IN ({marques})", "o.OFAbrication", **params)
    commande_de = {
        int(r["IDOFabrication"]): int(r["IDCommande"])
        for r in requete(
            "SELECT IDOFabrication, IDCommande FROM ofabrication "
            f"WHERE IDCommande IN ({marques})",
            **params,
        )
    }
    ref = date_reference()
    resultat = []
    for c in commandes:
        cid = int(c["IDCommande"])
        ofs_cmd = [of for of in ofs if commande_de.get(of.id) == cid]
        resultat.append(
            CommandeDetail(
                id=cid, numero=c["Commande"] or "", client=c["Client"],
                date=en_date(c["Date"]), date_fin_visee=en_date(c["DateFinVise"]),
                quantite=int(c["Qtte"] or 0), ofs=ofs_cmd,
                a_risque=any(_evaluer(of, ref, _HORIZON_PAR_DEFAUT_JOURS) for of in ofs_cmd),
            )
        )
    return resultat
