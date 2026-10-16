"""Outils de l'agent pour l'atelier Tunicotex (lecture seule sur la base DIVA).

Le dépôt est importé comme modules : plusieurs outils portent le même nom qu'une
fonction du dépôt (`situation_atelier`, `rechercher_of`, …).
"""
from __future__ import annotations

import functools
from datetime import date as Date

from langchain_core.tools import tool

from app.agent.tools.dates import resoudre_jour, resoudre_periode
from app.core.config import get_settings
from app.integrations.divatex import commandes as cmd
from app.integrations.divatex import connexion
from app.integrations.divatex import of as ofs
from app.integrations.divatex import operateurs as ops
from app.integrations.divatex import production as prod
from app.integrations.divatex import referentiel as ref

_MAX_CODES = 15
_NBSP = " "


# --- formatage -------------------------------------------------------------


def _jjmmaaaa(d: Date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _pct(valeur: float | None) -> str:
    return "—" if valeur is None else f"{round(valeur * 100)}{_NBSP}%"


def _rendement(valeur: float | None) -> str:
    if valeur is None:
        return "—"
    suffixe = "" if get_settings().divatex_calibre else " (estimé)"
    return _pct(valeur) + suffixe


def _fin(texte: str, jour: Date) -> str:
    return f"{texte}\n(Données au {_jjmmaaaa(jour)})"


def _chaine_inconnue(texte: str) -> str:
    codes = [l.code for l in ref.lister_lignes()][:_MAX_CODES]
    return f"Chaîne inconnue : {texte}. Chaînes valides : {', '.join(codes)}"


def _groupe_inconnu(texte: str, jour: Date) -> str:
    noms = ", ".join(g.nom for g in ref.lister_groupes()[:_MAX_CODES])
    return _fin(f"Groupe inconnu : {texte}. Groupes valides : {noms}", jour)


def _table(titre: str, jour: Date, colonnes: list[str], lignes: list[list], **extra) -> dict:
    return {
        "kind": "table", "titre": titre, "date_reference": _jjmmaaaa(jour),
        "colonnes": colonnes, "lignes": lignes, **extra,
    }


def _tuile(libelle: str, valeur, unite: str | None = None, etat: str = "neutre",
           note: str | None = None) -> dict:
    t: dict = {"libelle": libelle, "valeur": valeur, "etat": etat}
    if unite:
        t["unite"] = unite
    if note:
        t["note"] = note
    return t


def _hors_service(fonction):
    """Traduit l'indisponibilité de DIVA et les erreurs de saisie en texte d'outil."""

    @functools.wraps(fonction)
    def enveloppe(*args, **kwargs):
        try:
            return fonction(*args, **kwargs)
        except connexion.DivatexIndisponible:
            return "Base DIVA injoignable.", None
        except ValueError as exc:
            return str(exc), None

    return enveloppe


def _jour(texte: str | None) -> Date:
    return resoudre_jour(texte, connexion.date_reference())


# --- production ------------------------------------------------------------


@tool(response_format="content_and_artifact")
@_hors_service
def situation_atelier(date: str | None = None) -> tuple[str, dict | None]:
    """Situation globale de l'atelier de confection pour un jour : sortie, objectif,
    taux d'atteinte, rendement, chaînes sous/au-dessus de l'objectif, OF en retard.

    `date` : « hier », « 26/08 », « JJ/MM/AAAA » ou vide pour la date de référence.
    """
    jour = _jour(date)
    s = prod.situation_atelier(jour)
    if not s.a_des_lectures:
        return _fin(f"Aucune production enregistrée le {_jjmmaaaa(jour)}.", jour), None
    texte = (
        f"Atelier le {_jjmmaaaa(jour)} : {s.sortie} pièces sorties pour un objectif de "
        f"{s.objectif} ({_pct(s.taux)}). Rendement moyen : {_rendement(s.rendement_moyen)}. "
        f"Chaînes sous objectif : {', '.join(s.lignes_sous_objectif) or 'aucune'}. "
        f"Chaînes au-dessus : {', '.join(s.lignes_au_dessus) or 'aucune'}. "
        f"OF en retard : {s.nb_of_en_retard}."
    )
    etat_taux = "neutre" if s.taux is None else ("ok" if s.taux >= 1 else "alerte")
    artifact = {
        "kind": "kpi_tiles", "titre": "Situation de l'atelier",
        "date_reference": _jjmmaaaa(jour),
        "tuiles": [
            _tuile("Sortie", s.sortie, "pièces"),
            _tuile("Objectif", s.objectif, "pièces"),
            _tuile("Atteinte", _pct(s.taux), etat=etat_taux),
            _tuile("Rendement", _rendement(s.rendement_moyen)),
            _tuile("OF en retard", s.nb_of_en_retard,
                   etat="alerte" if s.nb_of_en_retard else "ok"),
        ],
    }
    return _fin(texte, jour), artifact


@tool(response_format="content_and_artifact")
@_hors_service
def production_chaines(
    date: str | None = None, groupe: str | None = None
) -> tuple[str, dict | None]:
    """Production de chaque chaîne (sortie et objectif) pour un jour, éventuellement
    limitée à un groupe de chaînes.

    `date` : « hier », « 26/08 », « JJ/MM/AAAA » ou vide pour la date de référence.
    """
    jour = _jour(date)
    groupe_id = None
    if groupe:
        g = ref.trouver_groupe(groupe)
        if g is None:
            return _groupe_inconnu(groupe, jour), None
        groupe_id = g.id
    lignes = [p for p in prod.production_lignes(jour, groupe_id) if p.a_des_lectures]
    if not lignes:
        return _fin(f"Aucune production enregistrée le {_jjmmaaaa(jour)}.", jour), None
    detail = "; ".join(
        f"{p.ligne.code} : {p.sortie}/{p.objectif if p.objectif is not None else '—'} "
        f"({_pct(p.taux)}), rendement {_rendement(p.rendement)}"
        for p in lignes
    )
    artifact = {
        "kind": "chart", "chart_type": "bar",
        "title": f"Production par chaîne — {_jjmmaaaa(jour)}", "unit": "pièces",
        "series": [
            {"name": "Sortie",
             "data": [{"x": p.ligne.code, "y": p.sortie} for p in lignes]},
            {"name": "Objectif",
             "data": [{"x": p.ligne.code, "y": p.objectif or 0} for p in lignes]},
        ],
    }
    return _fin(f"Production du {_jjmmaaaa(jour)} — {detail}.", jour), artifact


@tool(response_format="content_and_artifact")
@_hors_service
def detail_chaine(chaine: str, date: str | None = None) -> tuple[str, dict | None]:
    """Détail d'une chaîne pour un jour : sortie, objectif, effectif, rendement et
    courbe de production heure par heure. `chaine` est le code (ex. CH1-L2).

    `date` : « hier », « 26/08 », « JJ/MM/AAAA » ou vide pour la date de référence.
    """
    ligne = ref.trouver_ligne(chaine)
    if ligne is None:
        return _chaine_inconnue(chaine), None
    jour = _jour(date)
    p = next((x for x in prod.production_lignes(jour) if x.ligne.id == ligne.id), None)
    if p is None or not p.a_des_lectures:
        return _fin(f"Aucune production sur {ligne.code} le {_jjmmaaaa(jour)}.", jour), None
    courbe = prod.courbe_horaire(ligne.id, jour)
    texte = (
        f"Chaîne {ligne.code} le {_jjmmaaaa(jour)} : {p.sortie} pièces sorties, "
        f"objectif {p.objectif if p.objectif is not None else 'non défini'}"
        f"{' (périmé)' if p.objectif_perime else ''} ({_pct(p.taux)}), "
        f"effectif {p.effectif}, rendement {_rendement(p.rendement)}."
    )
    artifact = {
        "kind": "chart", "chart_type": "line",
        "title": f"{ligne.code} — pièces par heure, {_jjmmaaaa(jour)}", "unit": "pièces",
        "series": [{
            "name": "Pièces",
            "data": [{"x": f"{pt.heure:02d}h", "y": pt.pieces} for pt in courbe],
        }],
    }
    return _fin(texte, jour), artifact


# --- ordres de fabrication ---------------------------------------------------


def _statut_of(of: ofs.OFResume) -> str:
    if not of.ouvert:
        return "Clos"
    return "Terminé" if of.avancement_final >= 1 else "Ouvert"


def _table_ofs(titre: str, jour: Date, resultats: list[ofs.OFResume]) -> dict:
    return _table(
        titre, jour,
        ["OF", "Client", "Article", "Quantité", "Export prévu", "Avancement"],
        [[o.numero, o.client or "—", o.article or "—", o.quantite,
          _jjmmaaaa(o.date_export_prevue), _pct(o.avancement_final)] for o in resultats],
    )


@tool(response_format="content_and_artifact")
@_hors_service
def rechercher_of(texte: str) -> tuple[str, dict | None]:
    """Recherche des ordres de fabrication (OF) par numéro d'OF, commande client,
    PO ou code article."""
    jour = connexion.date_reference()
    resultats = ofs.rechercher_of(texte)
    if not resultats:
        return _fin(f"Aucun OF trouvé pour « {texte} ».", jour), None
    liste = "; ".join(
        f"{o.numero} ({o.client or '—'}, {_pct(o.avancement_final)})" for o in resultats
    )
    return (
        _fin(f"{len(resultats)} OF trouvé(s) pour « {texte} » : {liste}.", jour),
        _table_ofs(f"OF — {texte}", jour, resultats),
    )


@tool(response_format="content_and_artifact")
@_hors_service
def avancement_of(of: str) -> tuple[str, dict | None]:
    """Avancement d'un ordre de fabrication par phase et par taille. Si plusieurs OF
    correspondent au texte, renvoie la liste pour que l'opérateur précise."""
    jour = connexion.date_reference()
    resultats = ofs.rechercher_of(of)
    if not resultats:
        return _fin(f"Aucun OF trouvé pour « {of} ».", jour), None
    if len(resultats) > 1:
        liste = ", ".join(o.numero for o in resultats)
        return (
            _fin(f"Plusieurs OF correspondent à « {of} » : {liste}. Précisez lequel.", jour),
            _table_ofs(f"OF — {of}", jour, resultats),
        )
    av = ofs.avancement_of(resultats[0].id)
    if av is None:
        return _fin(f"Aucun OF trouvé pour « {of} ».", jour), None
    o = av.of
    phases_txt = "; ".join(f"{p.nom} : {p.quantite} ({_pct(p.pct)})" for p in av.phases)
    texte = (
        f"OF {o.numero} ({o.client or '—'}, {o.quantite} pièces, export prévu "
        f"{_jjmmaaaa(o.date_export_prevue)}). Avancement : {phases_txt or 'aucune lecture'}."
    )
    colonnes = ["Taille", *(p.nom for p in av.phases)]
    lignes = [
        [taille, *(qtes.get(p.phase_id, 0) for p in av.phases)] for taille, qtes in av.tailles
    ]
    artifact = {
        "kind": "phase_pipeline", "titre": f"Avancement de l'OF {o.numero}",
        "of": {
            "numero": o.numero, "client": o.client, "article": o.article,
            "quantite": o.quantite, "date_export_prevue": _jjmmaaaa(o.date_export_prevue),
            "statut": _statut_of(o),
        },
        "phases": [{"nom": p.nom, "quantite": p.quantite, "pct": p.pct} for p in av.phases],
        "tailles": {"colonnes": colonnes, "lignes": lignes},
        "date_reference": _jjmmaaaa(jour),
    }
    return _fin(texte, jour), artifact


# --- opérateurs --------------------------------------------------------------


@tool(response_format="content_and_artifact")
@_hors_service
def rendement_operateurs(
    chaine: str | None = None,
    groupe: str | None = None,
    periode_jours: int | None = 7,
    ordre: str = "meilleurs",
    limite: int = 5,
) -> tuple[str, dict | None]:
    """Classement des opérateurs par rendement sur les `periode_jours` derniers jours
    (31 maximum), éventuellement pour une chaîne ou un groupe. `ordre` : « meilleurs »
    ou « moins bons »."""
    jour = connexion.date_reference()
    ligne_id = groupe_id = None
    if chaine:
        ligne = ref.trouver_ligne(chaine)
        if ligne is None:
            return _chaine_inconnue(chaine), None
        ligne_id = ligne.id
    elif groupe:
        g = ref.trouver_groupe(groupe)
        if g is None:
            return _groupe_inconnu(groupe, jour), None
        groupe_id = g.id
    du, au = resoudre_periode(periode_jours, jour)
    classement = [
        r for r in ops.classement_operateurs(du, au, ligne_id=ligne_id, groupe_id=groupe_id)
        if r.rendement is not None
    ]
    if not classement:
        return _fin(
            f"Aucun rendement calculable du {_jjmmaaaa(du)} au {_jjmmaaaa(au)}.", jour
        ), None
    moins_bons = ordre.strip().lower().startswith("moins")
    if moins_bons:
        classement = sorted(classement, key=lambda r: r.rendement)
    classement = classement[: max(1, limite)]
    detail = "; ".join(
        f"{r.operateur.nom_complet} ({r.ligne or '—'}) : {_rendement(r.rendement)}"
        for r in classement
    )
    libelle = "Moins bons" if moins_bons else "Meilleurs"
    texte = f"{libelle} rendements du {_jjmmaaaa(du)} au {_jjmmaaaa(au)} — {detail}."
    chart = {
        "kind": "chart", "chart_type": "bar",
        "title": f"Rendement des opérateurs ({_jjmmaaaa(du)} – {_jjmmaaaa(au)})",
        "unit": "%",
        "series": [{"name": "Rendement", "data": [
            {"x": r.operateur.nom_complet, "y": round(r.rendement * 100)} for r in classement
        ]}],
    }
    table = _table(
        f"{libelle} rendements", jour,
        ["Opérateur", "Matricule", "Chaîne", "Pièces", "Rendement"],
        [[r.operateur.nom_complet, r.operateur.matricule, r.ligne or "—",
          r.pieces_operations, _rendement(r.rendement)] for r in classement],
        chart=chart,
    )
    return _fin(texte, jour), table


@tool(response_format="content_and_artifact")
@_hors_service
def fiche_operateur(operateur: str, periode_jours: int | None = 7) -> tuple[str, dict | None]:
    """Fiche de rendement d'un opérateur (nom, prénom ou matricule) sur les
    `periode_jours` derniers jours (31 maximum). Si plusieurs opérateurs portent ce
    nom, renvoie la liste pour que l'opérateur précise."""
    jour = connexion.date_reference()
    trouves = ref.rechercher_operateurs(operateur)
    if not trouves:
        return _fin(f"Aucun opérateur trouvé pour « {operateur} ».", jour), None
    if len(trouves) > 1:
        noms = ", ".join(f"{o.nom_complet} ({o.matricule})" for o in trouves)
        return (
            _fin(f"Plusieurs opérateurs correspondent à « {operateur} » : {noms}. "
                 "Précisez lequel (matricule).", jour),
            _table(f"Opérateurs — {operateur}", jour, ["Opérateur", "Matricule"],
                   [[o.nom_complet, o.matricule] for o in trouves]),
        )
    du, au = resoudre_periode(periode_jours, jour)
    fiche = ops.fiche_operateur(trouves[0].id, du, au)
    if fiche is None:
        return _fin(f"Aucun opérateur trouvé pour « {operateur} ».", jour), None
    t = fiche.total
    top = ", ".join(f"{nom} ({qte})" for nom, qte in fiche.operations[:3]) or "aucune"
    texte = (
        f"{fiche.operateur.nom_complet} ({fiche.operateur.matricule}) du {_jjmmaaaa(du)} "
        f"au {_jjmmaaaa(au)} : rendement {_rendement(t.rendement)}, "
        f"{t.pieces_operations} pièces, chaîne {t.ligne or '—'}. Opérations principales : {top}."
    )
    artifact = {
        "kind": "kpi_tiles", "titre": f"Fiche — {fiche.operateur.nom_complet}",
        "date_reference": _jjmmaaaa(jour),
        "tuiles": [
            _tuile("Rendement", _rendement(t.rendement)),
            _tuile("Pièces", t.pieces_operations, "pièces"),
            _tuile("Minutes produites", round(t.minutes_produites), "min"),
            _tuile("Jours travaillés", len(fiche.par_jour), "j"),
            _tuile("Chaîne", t.ligne or "—"),
        ],
    }
    return _fin(texte, jour), artifact


# --- commandes ---------------------------------------------------------------


@tool(response_format="content_and_artifact")
@_hors_service
def commandes_a_risque(horizon_jours: int | None = 7) -> tuple[str, dict | None]:
    """OF ouverts en retard de livraison ou dont l'export prévu tombe dans les
    `horizon_jours` prochains jours sans être terminés."""
    jour = connexion.date_reference()
    horizon = 7 if horizon_jours is None else max(0, horizon_jours)
    risques = cmd.of_a_risque(jour, horizon)
    if not risques:
        return _fin(f"Aucun OF à risque dans les {horizon} prochains jours.", jour), None
    retards = [r for r in risques if r.statut == "en_retard"]
    texte = (
        f"{len(risques)} OF à risque dont {len(retards)} en retard : "
        + "; ".join(
            f"{r.of.numero} ({r.of.client or '—'}) "
            + (f"en retard de {r.jours} j" if r.statut == "en_retard"
               else f"export dans {r.jours} j")
            for r in risques
        ) + "."
    )
    table = _table(
        "Commandes à risque", jour,
        ["OF", "Client", "Export prévu", "Avancement", "Situation"],
        [[r.of.numero, r.of.client or "—", _jjmmaaaa(r.of.date_export_prevue),
          _pct(r.of.avancement_final),
          f"En retard de {r.jours} j" if r.statut == "en_retard"
          else f"Dans {r.jours} j"] for r in risques],
    )
    return _fin(texte, jour), table


@tool(response_format="content_and_artifact")
@_hors_service
def consulter_commande(numero: str) -> tuple[str, dict | None]:
    """Consulte une commande client par son numéro : client, dates, quantité et OF liés."""
    jour = connexion.date_reference()
    commandes = cmd.consulter_commande(numero)
    if not commandes:
        return _fin(f"Aucune commande trouvée pour « {numero} ».", jour), None
    texte = " ".join(
        f"Commande {c.numero} ({c.client or '—'}) : {c.quantite} pièces, "
        f"fin visée {_jjmmaaaa(c.date_fin_visee)}, {len(c.ofs)} OF"
        f"{', à risque' if c.a_risque else ''}."
        for c in commandes
    )
    lignes = []
    for c in commandes:
        base = [c.numero, c.client or "—", _jjmmaaaa(c.date_fin_visee), c.quantite]
        if not c.ofs:
            lignes.append([*base, "—", "—"])
        for o in c.ofs:
            lignes.append([*base, o.numero, _pct(o.avancement_final)])
    table = _table(
        f"Commande {numero}", jour,
        ["Commande", "Client", "Fin visée", "Quantité", "OF", "Avancement OF"], lignes,
    )
    return _fin(texte, jour), table


TUNICOTEX_TOOLS = [
    situation_atelier,
    production_chaines,
    detail_chaine,
    rechercher_of,
    avancement_of,
    rendement_operateurs,
    fiche_operateur,
    commandes_a_risque,
    consulter_commande,
]
