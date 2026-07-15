"""Bilans PDF de Nova, inspirés du document MES « Bilan Ordre de Fabrication ».

Deux documents, même charte (tables à libellé de section, modèle de temps
AFNOR, pied de page « Imprimé le … ») :
- `generer_bilan_of_pdf` : bilan d'un OF — identité (article, lot, quantité),
  production, temps emboîtés (TO→TU), KPI (DO/TP/TQ/TRS/TRG/TRE), généalogie
  matière première (FEFO), non-conformité (rebuts par cause), arrêts.
- `generer_bilan_equipe_pdf` : bilan de production d'équipe (fenêtre 8 h).

Utilisés par l'outil agent `envoyer_rapport` : le bilan part en pièce jointe
PDF (e-mail) ou en document (WhatsApp), plus jamais en texte brut.
"""
from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DowntimeEvent, OrdreFabrication, QualityEvent
from app.models.enums import TypeEvenementQualite
from app.services import dashboard_service, trs_service

# ------------------------------- Charte ------------------------------- #

ENCRE = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
BORD = colors.HexColor("#9ca3af")
FOND_TITRE = colors.HexColor("#e5e7eb")
FOND_ENTETE = colors.HexColor("#f3f4f6")

STYLE_TITRE = ParagraphStyle(
    "titre", fontName="Helvetica-Bold", fontSize=15, textColor=ENCRE, alignment=1
)
STYLE_SECTION = ParagraphStyle(
    "section", fontName="Helvetica-Bold", fontSize=8.5, textColor=ENCRE
)
STYLE_CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, textColor=ENCRE)
STYLE_CELL_B = ParagraphStyle(
    "cellb", fontName="Helvetica-Bold", fontSize=8, textColor=ENCRE
)


def _hms(secondes: float | Decimal) -> str:
    s = max(0, int(float(secondes)))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _pct(valeur: float | Decimal) -> str:
    return f"{float(valeur) * 100:.2f}%".replace(".", ",")


def _pied_de_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        15 * mm, 10 * mm, f"Imprimé le {datetime.now():%d/%m/%Y %H:%M} — Nova"
    )
    canvas.drawRightString(195 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _table_section(libelle: str, entetes: list[str], rangees: list[list[str]]) -> Table:
    """Table façon bilan MES : 1re colonne = libellé de section fusionné,
    puis une ligne d'en-têtes grisée et les valeurs."""
    if not rangees:
        rangees = [["---"] * len(entetes)]
    data = [
        [Paragraph(libelle, STYLE_SECTION)] + [Paragraph(e, STYLE_CELL_B) for e in entetes]
    ]
    for r in rangees:
        data.append([""] + [Paragraph(str(v), STYLE_CELL) for v in r])
    largeur_libelle = 28 * mm
    largeur_reste = (180 * mm - largeur_libelle) / len(entetes)
    table = Table(
        data,
        colWidths=[largeur_libelle] + [largeur_reste] * len(entetes),
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, BORD),
                ("SPAN", (0, 0), (0, -1)),
                ("BACKGROUND", (0, 0), (0, -1), FOND_TITRE),
                ("BACKGROUND", (1, 0), (-1, 0), FOND_ENTETE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


# Largeur utile du cadre A4 avec les marges de `_document` (210 - 2×15 mm).
LARGEUR_UTILE = 180 * mm


def _table_paginable(
    libelle: str, entetes: list[str], rangees: list[list[str]]
) -> list:
    """Section tabulaire qui peut s'étaler sur plusieurs pages.

    `_table_section` fusionne le libellé sur TOUTE la hauteur (SPAN vertical) :
    pratique pour une poignée de lignes, mais reportlab ne sait pas scinder une
    cellule fusionnée, donc une longue table (ex. 26 OF) lève `LayoutError`. Ici
    le libellé est un titre au-dessus d'une grille classique — la grille se scinde
    proprement et réaffiche sa ligne d'en-têtes (`repeatRows=1`) sur chaque page.
    """
    if not rangees:
        rangees = [["---"] * len(entetes)]
    data = [[Paragraph(e, STYLE_CELL_B) for e in entetes]]
    for r in rangees:
        data.append([Paragraph(str(v), STYLE_CELL) for v in r])
    largeur_col = LARGEUR_UTILE / len(entetes)
    table = Table(data, colWidths=[largeur_col] * len(entetes), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, BORD),
                ("BACKGROUND", (0, 0), (-1, 0), FOND_ENTETE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [Paragraph(libelle, STYLE_SECTION), Spacer(0, 1.5 * mm), table]


def _bandeau_identite(paires: list[tuple[str, str]]) -> Table:
    cellules = [
        Paragraph(
            f"<b>{libelle} :</b> {valeur}", STYLE_CELL
        )
        for libelle, valeur in paires
    ]
    table = Table([cellules], colWidths=[180 * mm / len(cellules)] * len(cellules))
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, BORD),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORD),
                ("BACKGROUND", (0, 0), (-1, -1), FOND_ENTETE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _document(titre: str, elements: list) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=titre,
        author="Nova",
    )
    doc.build(elements, onFirstPage=_pied_de_page, onLaterPages=_pied_de_page)
    return buffer.getvalue()


# --------------------------- Bilan d'un OF --------------------------- #


def generer_bilan_of_pdf(db: Session, of: OrdreFabrication) -> bytes:
    trs = trs_service.calculer_trs_ordre(db, of)
    t = trs.temps
    ligne = of.ligne_production

    debut = of.date_debut_reelle
    fin = of.date_fin_reelle
    duree_s = (
        ((fin or datetime.utcnow()) - debut).total_seconds() if debut else 0.0
    )
    cycle = float(of.article.temps_cycle_cible_s or 0)
    cadence_theorique = f"{3600 / cycle:.0f} u/h" if cycle > 0 else "—"
    total_produit = float(of.quantite_bonne + of.quantite_rejetee)
    cadence_reelle = (
        f"{total_produit / (duree_s / 3600):.2f} u/h".replace(".", ",")
        if duree_s > 0
        else "—"
    )

    elements: list = [
        Paragraph("Bilan Ordre de Fabrication (OF)", STYLE_TITRE),
        Spacer(0, 6 * mm),
        _bandeau_identite(
            [
                ("OF", of.numero),
                ("Article", f"{of.article.code} {of.article.designation}"),
                ("Lot", of.numero_lot_produit or "—"),
                ("Quantité OF", f"{float(of.quantite_planifiee):g}"),
            ]
        ),
        Spacer(0, 3 * mm),
        _bandeau_identite(
            [
                ("Ligne", ligne.designation if ligne else "—"),
                ("Du", f"{debut:%d/%m/%Y %H:%M}" if debut else "—"),
                ("Au", f"{fin:%d/%m/%Y %H:%M}" if fin else "en cours"),
                ("Durée", _hms(duree_s)),
                ("Cad. Théorique", cadence_theorique),
                ("Cad. Réelle", cadence_reelle),
            ]
        ),
        Spacer(0, 5 * mm),
        _table_section(
            "Production (Unité)",
            ["Qte. Prod. Brut", "Qte. Rejet", "Qte. Prod. Net", "Statut"],
            [
                [
                    f"{total_produit:g}",
                    f"{float(of.quantite_rejetee):g}",
                    f"{float(of.quantite_bonne):g}",
                    of.statut.value.replace("_", " ").capitalize(),
                ]
            ],
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "Temps (hh:mm:ss)",
            [
                "Tps.Ouverture",
                "Arrêts Planif.",
                "Tps.Requis",
                "Arrêts N.Planif.",
                "Tps.Fonct.",
                "Perte Cad.",
                "Tps.Net",
                "Non.Qualité",
                "Tps.Utile",
            ],
            [
                [
                    _hms(t.to),
                    _hms(t.to - t.tr),
                    _hms(t.tr),
                    _hms(trs.pertes.disponibilite_s),
                    _hms(t.tf),
                    _hms(trs.pertes.performance_s),
                    _hms(t.tn),
                    _hms(trs.pertes.qualite_s),
                    _hms(t.tu),
                ]
            ],
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "KPI",
            [
                "Taux Disponibilité",
                "Taux Performan.",
                "Taux Qualité",
                "TRS",
                "TRG",
                "TRE",
            ],
            [
                [
                    _pct(trs.do),
                    _pct(trs.tp),
                    _pct(trs.tq),
                    _pct(trs.trs),
                    _pct(trs.trg),
                    _pct(trs.tre),
                ]
            ],
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "Matière Première",
            ["Code", "Désignation", "Lot", "Quantité"],
            [
                [
                    c.matiere_premiere.code,
                    c.matiere_premiere.designation,
                    c.lot.numero_lot,
                    f"{float(c.quantite_consommee):g}",
                ]
                for c in of.consommations
            ],
        ),
        Spacer(0, 3 * mm),
    ]

    # Non-conformité : rebuts de l'OF groupés par cause.
    rebuts = list(
        db.execute(
            select(QualityEvent).where(
                QualityEvent.ordre_fabrication_id == of.id,
                QualityEvent.type == TypeEvenementQualite.REBUT,
            )
        ).scalars()
    )
    par_cause: dict[str, int] = {}
    for e in rebuts:
        cause = (e.cause.value if e.cause else "AUTRE").replace("_", " ").capitalize()
        par_cause[cause] = par_cause.get(cause, 0) + e.quantite
    elements.append(
        _table_section(
            "Gestion Non Conformité",
            ["Type Rejet", "Qte. Rejet"],
            [[cause, str(qte)] for cause, qte in sorted(par_cause.items(), key=lambda kv: -kv[1])],
        )
    )
    elements.append(Spacer(0, 3 * mm))

    # Arrêts liés à l'OF.
    arrets = list(
        db.execute(
            select(DowntimeEvent)
            .where(DowntimeEvent.ordre_fabrication_id == of.id)
            .order_by(DowntimeEvent.start_time)
        ).scalars()
    )
    elements.append(
        _table_section(
            "Arrêts",
            ["Cause", "Début", "Fin", "Durée"],
            [
                [
                    a.cause.value.replace("_", " ").capitalize(),
                    f"{a.start_time:%d/%m %H:%M}",
                    f"{a.end_time:%d/%m %H:%M}" if a.end_time else "en cours",
                    _hms(((a.end_time or datetime.utcnow()) - a.start_time).total_seconds()),
                ]
                for a in arrets
            ],
        )
    )

    return _document(f"Bilan OF {of.numero}", elements)


# ------------------------- Bilan d'équipe (8 h) ------------------------- #


def generer_bilan_equipe_pdf(db: Session) -> bytes:
    r = dashboard_service.construire_resume(db)
    total = float(r.quantite_bonne + r.quantite_rejetee)
    taux_rebut = float(r.quantite_rejetee) / total if total > 0 else 0.0

    elements: list = [
        Paragraph("Bilan de Production — Équipe", STYLE_TITRE),
        Spacer(0, 6 * mm),
        _bandeau_identite(
            [
                ("Généré le", f"{datetime.now():%d/%m/%Y %H:%M}"),
                ("Fenêtre", "8 dernières heures"),
                ("Machines en marche", f"{r.machines_en_marche}/{r.machines_total}"),
                ("OF actifs", str(r.ordres_actifs)),
            ]
        ),
        Spacer(0, 5 * mm),
        _table_section(
            "KPI",
            ["Taux Disponibilité", "Taux Performan.", "Taux Qualité", "TRS"],
            [[_pct(r.disponibilite), _pct(r.performance), _pct(r.qualite), _pct(r.trs_global)]],
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "Production (Unité)",
            ["Qte. Prod. Brut", "Qte. Rejet", "Qte. Prod. Net", "Taux Rebut", "Cadence"],
            [
                [
                    f"{total:g}",
                    str(r.quantite_rejetee),
                    str(r.quantite_bonne),
                    _pct(taux_rebut),
                    f"{r.cadence_actuelle_par_min} u/min",
                ]
            ],
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "Fiabilité",
            ["Pannes / arrêts", "Temps d'arrêt", "MTTR", "MTBF"],
            [
                [
                    str(r.nb_pannes),
                    _hms(float(r.temps_arret_total_s)),
                    _hms(float(r.mttr_s)),
                    _hms(float(r.mtbf_s)),
                ]
            ],
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "Top causes d'arrêt",
            ["Cause", "Durée"],
            [
                [c.cause.replace("_", " ").capitalize(), _hms(float(c.duree_s))]
                for c in r.top_causes_arret
            ],
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "Alertes non résolues",
            ["Sévérité", "Message"],
            [[a.severity.value, a.message] for a in r.alertes_actives[:10]],
        ),
    ]
    return _document("Bilan de production Nova", elements)


# --------------------------- Plan d'ordonnancement --------------------------- #


def generer_ordonnancement_pdf(scenario) -> bytes:
    """PDF du plan d'ordonnancement (un `planning_service.Scenario`), même charte
    que les bilans : bandeau d'identité, KPI, créneaux par OF, non-planifiables.

    Utilisé par l'outil agent `envoyer_ordonnancement` : le plan part en pièce
    jointe (e-mail) ou en document (WhatsApp) pour l'équipe suivante.
    """
    creneaux = sorted(scenario.creneaux, key=lambda c: (c.debut, c.ligne_code))
    elements: list = [
        Paragraph("Plan d'ordonnancement", STYLE_TITRE),
        Spacer(0, 5 * mm),
        _bandeau_identite(
            [
                ("Règle", f"{scenario.algorithme} — {scenario.libelle}"),
                ("Calculé le", f"{scenario.calcule_le:%d/%m/%Y %H:%M}"),
                ("OF planifiés", str(len(scenario.planifies))),
            ]
        ),
        Spacer(0, 3 * mm),
        _table_section(
            "Indicateurs",
            ["OF en retard", "Retard cumulé", "Changements de série", "Durée du plan"],
            [
                [
                    str(scenario.nb_retards),
                    f"{scenario.retard_total_jours} j ({scenario.retard_total_h:.1f} h)",
                    str(scenario.nb_changements_serie),
                    f"{scenario.makespan_h:.1f} h",
                ]
            ],
        ),
        Spacer(0, 3 * mm),
        *_table_paginable(
            "Séquence",
            ["#", "OF", "Article", "Qté", "Ligne", "Début", "Fin", "Échéance", "Retard"],
            [
                [
                    "en cours" if c.en_cours else str(c.position),
                    c.numero + (" ★" if c.prioritaire else ""),
                    c.article,
                    f"{c.quantite:.0f}",
                    c.ligne_code,
                    f"{c.debut:%d/%m %H:%M}",
                    f"{c.fin:%d/%m %H:%M}",
                    f"{c.echeance:%d/%m}" if c.echeance else "---",
                    f"{c.retard_jours} j" if c.retard_jours > 0 else "---",
                ]
                for c in creneaux
            ],
        ),
    ]
    if scenario.prioritaires:
        elements += [
            Spacer(0, 3 * mm),
            _table_section(
                "Priorités opérateur",
                ["OF passés en tête de file"],
                [[", ".join(scenario.prioritaires)]],
            ),
        ]
    if scenario.non_planifiables:
        elements += [
            Spacer(0, 3 * mm),
            _table_section(
                "Non planifiables",
                ["OF", "Raison"],
                [[num, raison] for num, raison in scenario.non_planifiables],
            ),
        ]
    return _document("Plan d'ordonnancement Nova", elements)
