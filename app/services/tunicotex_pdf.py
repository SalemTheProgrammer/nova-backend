"""Les cinq rapports PDF de l'atelier Tunicotex (R1 à R5).

Les chiffres viennent du dépôt `app.integrations.divatex` (lecture seule) ;
la mise en page repose sur `pdf_base`. Chaque page porte l'en-tête
« Nova Opsis — Tunicotex · Données au JJ/MM/AAAA ». R2 et R3 sont en paysage
(tables à `LARGEUR_UTILE_PAYSAGE`, sans `table_section` ni `bandeau_identite`).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import date, timedelta
from typing import Literal
from xml.sax.saxutils import escape

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer

from app.core.config import get_settings
from app.integrations.divatex import commandes, connexion, kpi, of, operateurs, production
from app.integrations.divatex import referentiel as ref
from app.services.pdf_base import (
    LARGEUR_UTILE_PAYSAGE,
    STYLE_CELL,
    STYLE_TITRE,
    bandeau_identite,
    document_pdf,
    table_paginable,
)

TypeRapport = Literal["R1", "R2", "R3", "R4", "R5"]

TITRES: dict[str, str] = {
    "R1": "Fiche de production journalière",
    "R2": "Suivi des phases par jour",
    "R3": "Rendement opérateurs",
    "R4": "Commandes à risque (OTD)",
    "R5": "Fiche OF",
}

_NBSP = " "
_TOP = 5


@dataclass(frozen=True)
class ParametresRapport:
    jour: date | None = None
    du: date | None = None
    au: date | None = None
    groupe_id: int | None = None
    ligne_id: int | None = None
    of_id: int | None = None
    client_id: int | None = None
    horizon_jours: int = 7

    def to_dict(self) -> dict:
        return {
            f.name: (v.isoformat() if isinstance(v := getattr(self, f.name), date) else v)
            for f in fields(self)
        }

    @classmethod
    def from_dict(cls, d: dict) -> ParametresRapport:
        valeurs = {}
        for f in fields(cls):
            v = d.get(f.name)
            if v is None:
                continue
            valeurs[f.name] = date.fromisoformat(v) if f.name in ("jour", "du", "au") else v
        return cls(**valeurs)


# --- formatage -------------------------------------------------------------


def _jj(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _pct(valeur: float | None) -> str:
    return "—" if valeur is None else f"{round(valeur * 100)}{_NBSP}%"


def _rendement(valeur: float | None) -> str:
    if valeur is None:
        return "—"
    return _pct(valeur) + ("" if get_settings().divatex_calibre else " (estimé)")


def _nombre(n: float) -> str:
    return f"{round(n):,}".replace(",", _NBSP)


def _esc(texte: str | None) -> str:
    return escape(texte) if texte else "—"


def _slug(texte: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", texte).strip("-") or "x"


def _titre(texte: str) -> list:
    return [Paragraph(escape(texte), STYLE_TITRE), Spacer(0, 4 * mm)]


def _note(texte: str) -> list:
    return [Paragraph(escape(texte), STYLE_CELL), Spacer(0, 3 * mm)]


def _espace() -> Spacer:
    return Spacer(0, 5 * mm)


def _document(type_: str, elements: list, *, paysage: bool = False) -> bytes:
    sous_titre = f"Nova Opsis — Tunicotex · Données au {_jj(connexion.date_reference())}"
    return document_pdf(TITRES[type_], elements, paysage=paysage, sous_titre=sous_titre)


def _groupe(groupe_id: int | None) -> ref.Groupe | None:
    return next((g for g in ref.lister_groupes() if g.id == groupe_id), None)


def _ligne(ligne_id: int | None) -> ref.Ligne | None:
    return next((l for l in ref.lister_lignes() if l.id == ligne_id), None)


def _periode(p: ParametresRapport) -> tuple[date, date]:
    au = p.au or connexion.date_reference()
    du = p.du or (au - timedelta(days=6))
    return du, au


# --- R1 : fiche de production journalière ----------------------------------


def _objectif_texte(p: production.ProductionLigne) -> str:
    if p.objectif is None:
        return "—"
    texte = _nombre(p.objectif)
    if p.objectif_perime and p.objectif_date:
        texte += f" (du {_jj(p.objectif_date)})"
    return texte


def _r1(p: ParametresRapport) -> tuple[str, bytes]:
    jour = p.jour or connexion.date_reference()
    groupe = _groupe(p.groupe_id)
    ligne = _ligne(p.ligne_id)
    lignes = [
        l for l in production.production_lignes(jour, p.groupe_id)
        if p.ligne_id is None or l.ligne.id == p.ligne_id
    ]
    portee = ligne.code if ligne else (groupe.nom if groupe else "Atelier")
    elements = _titre(f"{TITRES['R1']} — {portee} — {_jj(jour)}")
    if not any(l.a_des_lectures for l in lignes):
        elements += _note("Aucune lecture ce jour-là.")
    else:
        rangees = [
            [
                _esc(l.ligne.code), _objectif_texte(l),
                _nombre(l.sortie) if l.a_des_lectures else "—",
                _pct(l.taux), str(l.effectif) if l.effectif else "—",
                _rendement(l.rendement),
            ]
            for l in lignes
        ]
        confection = [l for l in lignes if l.ligne.nature == "confection"]
        # même périmètre que le taux : chaînes avec objectif et lectures
        comparables = [l for l in confection if l.objectif is not None and l.a_des_lectures]
        sortie = sum(l.sortie for l in comparables)
        objectif = sum(l.objectif for l in comparables)
        taux_total = kpi.taux_atteinte(sortie, objectif)
        total_rendement = kpi.rendement(
            sum(l.minutes_produites for l in confection),
            sum(l.minutes_presence for l in confection),
        )
        # Le taux ne porte que sur les chaînes avec objectif ; la sortie, l'effectif
        # et le rendement de toute la confection ont leur propre ligne.
        rangees.append([
            "<b>Total (chaînes avec objectif)</b>", f"<b>{_nombre(objectif)}</b>",
            f"<b>{_nombre(sortie)}</b>", f"<b>{_pct(taux_total)}</b>", "", "",
        ])
        rangees.append([
            "<b>Sortie confection totale</b>", "",
            f"<b>{_nombre(sum(l.sortie for l in confection))}</b>", "",
            f"<b>{sum(l.effectif for l in confection)}</b>",
            f"<b>{_rendement(total_rendement)}</b>",
        ])
        elements += table_paginable(
            "Production par chaîne",
            ["Chaîne", "Objectif", "Sortie", "Taux", "Effectif", "Rendement"],
            rangees,
        )
        classement = [
            r for r in operateurs.classement_operateurs(
                jour, jour, ligne_id=p.ligne_id, groupe_id=p.groupe_id
            )
            if r.rendement is not None
        ]
        n = len(classement)
        meilleurs = [(i + 1, r) for i, r in enumerate(classement[:_TOP])]
        derniers = [(n - i, r) for i, r in enumerate(reversed(classement[_TOP:][-_TOP:]))]
        for libelle, liste in (("5 meilleurs opérateurs", meilleurs),
                               ("5 opérateurs les moins performants", derniers)):
            if not liste:
                continue
            elements.append(_espace())
            elements += table_paginable(
                libelle, ["Rang", "Opérateur", "Chaîne", "Rendement"],
                [
                    [str(rang), _esc(r.operateur.nom_complet), _esc(r.ligne),
                     _rendement(r.rendement)]
                    for rang, r in liste
                ],
            )
    nom = f"r1-production-{_slug(groupe.nom) if groupe else 'atelier'}-{jour.isoformat()}.pdf"
    return nom, _document("R1", elements)


# --- R2 : suivi des phases par jour ----------------------------------------


def _r2(p: ParametresRapport) -> tuple[str, bytes]:
    du, au = _periode(p)
    donnees = production.pieces_par_phase(du, au, of_id=p.of_id, client_id=p.client_id)
    elements = _titre(f"{TITRES['R2']} — du {_jj(du)} au {_jj(au)}")
    pieces: dict[tuple[date, int], int] = {(j, ph): n for j, ph, n in donnees}
    phases = sorted({ph for _, ph in pieces})
    if not phases:
        elements += _note("Aucune lecture sur la période.")
    else:
        jours = [du + timedelta(days=i) for i in range((au - du).days + 1)]
        rangees = [
            [_jj(j)]
            + [_nombre(pieces[(j, ph)]) if (j, ph) in pieces else "—" for ph in phases]
            + [_nombre(sum(pieces.get((j, ph), 0) for ph in phases))]
            for j in jours
        ]
        rangees.append(
            ["<b>Total</b>"]
            + [f"<b>{_nombre(sum(n for (_, q), n in pieces.items() if q == ph))}</b>"
               for ph in phases]
            + [f"<b>{_nombre(sum(pieces.values()))}</b>"]
        )
        elements += table_paginable(
            "Pièces par phase et par jour",
            ["Jour"] + [_esc(ref.nom_phase(ph)) for ph in phases] + ["Total"],
            rangees, largeur=LARGEUR_UTILE_PAYSAGE,
        )
    nom = f"r2-phases-{du.isoformat()}-{au.isoformat()}.pdf"
    return nom, _document("R2", elements, paysage=True)


# --- R3 : rendement opérateurs ---------------------------------------------


def _r3(p: ParametresRapport) -> tuple[str, bytes]:
    du, au = _periode(p)
    classement = operateurs.classement_operateurs(
        du, au, ligne_id=p.ligne_id, groupe_id=p.groupe_id
    )
    ligne, groupe = _ligne(p.ligne_id), _groupe(p.groupe_id)
    portee = ligne.code if ligne else (groupe.nom if groupe else "atelier")
    elements = _titre(f"{TITRES['R3']} — {portee} — du {_jj(du)} au {_jj(au)}")
    if not classement:
        elements += _note("Aucune lecture sur la période.")
    else:
        elements += table_paginable(
            "Classement",
            ["Rang", "Matricule", "Opérateur", "Chaîne", "Pièces (opérations)",
             "Minutes produites", "Minutes de présence", "Rendement"],
            [
                [
                    str(i), _esc(r.operateur.matricule), _esc(r.operateur.nom_complet),
                    _esc(r.ligne), _nombre(r.pieces_operations), _nombre(r.minutes_produites),
                    _nombre(r.minutes_presence) + (" (estimé)" if r.presence_estimee else ""),
                    _rendement(r.rendement),
                ]
                for i, r in enumerate(classement, 1)
            ],
            largeur=LARGEUR_UTILE_PAYSAGE,
        )
    nom = f"r3-rendement-{_slug(portee)}-{du.isoformat()}-{au.isoformat()}.pdf"
    return nom, _document("R3", elements, paysage=True)


# --- R4 : commandes à risque (OTD) -----------------------------------------


def _statut(r: commandes.OFRisque) -> str:
    if r.statut == "en_retard":
        return f"En retard de {r.jours} j"
    return "À risque : échéance aujourd'hui" if r.jours == 0 else f"À risque : dans {r.jours} j"


def _r4(p: ParametresRapport) -> tuple[str, bytes]:
    jour = p.jour or connexion.date_reference()
    risques = commandes.of_a_risque(jour, p.horizon_jours, p.client_id)
    elements = _titre(f"{TITRES['R4']} — {_jj(jour)}")
    elements += _note(f"Horizon : {p.horizon_jours} jours ; retards des 7 derniers jours inclus.")
    if not risques:
        elements += _note("Aucun OF à risque.")
    par_client: dict[str, list[commandes.OFRisque]] = defaultdict(list)
    for r in risques:
        par_client[r.of.client or "Sans client"].append(r)
    for client in sorted(par_client):
        elements += table_paginable(
            escape(client),
            ["OF", "Article", "Quantité", "Export prévu", "Avancement", "Statut"],
            [
                [
                    _esc(r.of.numero), _esc(r.of.article), _nombre(r.of.quantite),
                    _jj(r.of.date_export_prevue), _pct(r.of.avancement_final), _statut(r),
                ]
                for r in par_client[client]
            ],
            poids=[3.4, 1.3, 1, 1.3, 1, 2],
        )
        elements.append(_espace())
    return f"r4-otd-{jour.isoformat()}.pdf", _document("R4", elements)


# --- R5 : fiche OF ----------------------------------------------------------


def _r5(p: ParametresRapport) -> tuple[str, bytes]:
    if p.of_id is None:
        raise ValueError("Fiche OF : préciser l'OF.")
    av = of.avancement_of(p.of_id)
    if av is None:
        raise ValueError("OF introuvable.")
    o = av.of
    elements = _titre(f"{TITRES['R5']} — {o.numero}")
    elements.append(bandeau_identite([
        ("OF", _esc(o.numero)), ("Client", _esc(o.client)),
        ("Article", _esc(o.article)), ("Quantité", _nombre(o.quantite)),
    ]))
    elements.append(Spacer(0, 2 * mm))
    elements.append(bandeau_identite([
        ("Export prévu", _jj(o.date_export_prevue)),
        ("Avancement final", _pct(o.avancement_final)),
        ("Dernière lecture", _jj(av.derniere_lecture)),
        ("Chaînes", _esc(", ".join(av.lignes))),
    ]))
    elements.append(_espace())
    elements += table_paginable(
        "Avancement par phase", ["Phase", "Quantité", "Avancement"],
        [[_esc(ph.nom), _nombre(ph.quantite), _pct(ph.pct)] for ph in av.phases],
    )
    elements.append(_espace())
    phases = sorted({ph for _, par in av.tailles for ph in par})
    elements += table_paginable(
        "Taille × phase",
        ["Taille"] + [_esc(ref.nom_phase(ph)) for ph in phases],
        [
            [_esc(taille)] + [_nombre(par[ph]) if ph in par else "—" for ph in phases]
            for taille, par in av.tailles
        ],
    )
    return f"r5-of-{_slug(o.numero)}.pdf", _document("R5", elements)


_GENERATEURS = {"R1": _r1, "R2": _r2, "R3": _r3, "R4": _r4, "R5": _r5}


def generer_rapport(type: TypeRapport, p: ParametresRapport) -> tuple[str, bytes]:  # noqa: A002
    """Génère un rapport : renvoie `(nom_fichier, pdf)`."""
    generateur = _GENERATEURS.get(type)
    if generateur is None:
        raise ValueError(f"Rapport inconnu : {type}.")
    return generateur(p)
