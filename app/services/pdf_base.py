"""Briques communes des documents PDF de Nova (charte, tables, mise en page).

Charte sobre (encre sombre, grille grise, en-têtes grisés) et pied de page
« Imprimé le … — Nova / Page n ». Les rapports métier assemblent leurs
éléments avec ces helpers puis appellent `document_pdf`.
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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

MARGE = 15 * mm
# Largeur utile du cadre avec les marges de `document_pdf` :
# A4 portrait 210 - 2×15 mm, A4 paysage 297 - 2×15 mm.
LARGEUR_UTILE = 180 * mm
LARGEUR_UTILE_PAYSAGE = 267 * mm

_STYLE_GRILLE = [
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
]


def _decor_page(sous_titre: str | None):
    """Pied de page « Imprimé le … — Nova / Page n » et, si fourni, une ligne
    d'en-tête `sous_titre` en haut de CHAQUE page."""

    def dessiner(canvas, doc) -> None:
        largeur_page, hauteur_page = doc.pagesize
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        if sous_titre:
            canvas.drawString(MARGE, hauteur_page - 9 * mm, sous_titre)
        canvas.drawString(MARGE, 10 * mm, f"Imprimé le {datetime.now():%d/%m/%Y %H:%M} — Nova")
        canvas.drawRightString(largeur_page - MARGE, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    return dessiner


def table_section(libelle: str, entetes: list[str], rangees: list[list[str]]) -> Table:
    """Table à libellé de section : 1re colonne = libellé fusionné sur toute la
    hauteur, puis une ligne d'en-têtes grisée et les valeurs.

    Réservée aux sections courtes : une cellule fusionnée ne se scinde pas
    d'une page à l'autre (voir `table_paginable` pour les longues listes).
    """
    if not rangees:
        rangees = [["---"] * len(entetes)]
    data = [
        [Paragraph(libelle, STYLE_SECTION)] + [Paragraph(e, STYLE_CELL_B) for e in entetes]
    ]
    for r in rangees:
        data.append([""] + [Paragraph(str(v), STYLE_CELL) for v in r])
    largeur_libelle = 28 * mm
    largeur_reste = (LARGEUR_UTILE - largeur_libelle) / len(entetes)
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
                *_STYLE_GRILLE,
            ]
        )
    )
    return table


def table_paginable(
    libelle: str,
    entetes: list[str],
    rangees: list[list[str]],
    largeur: float = LARGEUR_UTILE,
) -> list:
    """Section tabulaire qui peut s'étaler sur plusieurs pages.

    Le libellé est un titre au-dessus d'une grille classique : la grille se
    scinde proprement et réaffiche sa ligne d'en-têtes (`repeatRows=1`) sur
    chaque page. Passer `largeur=LARGEUR_UTILE_PAYSAGE` pour un document paysage.
    """
    if not rangees:
        rangees = [["---"] * len(entetes)]
    data = [[Paragraph(e, STYLE_CELL_B) for e in entetes]]
    for r in rangees:
        data.append([Paragraph(str(v), STYLE_CELL) for v in r])
    largeur_col = largeur / len(entetes)
    table = Table(data, colWidths=[largeur_col] * len(entetes), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, BORD),
                ("BACKGROUND", (0, 0), (-1, 0), FOND_ENTETE),
                *_STYLE_GRILLE,
            ]
        )
    )
    return [Paragraph(libelle, STYLE_SECTION), Spacer(0, 1.5 * mm), table]


def bandeau_identite(paires: list[tuple[str, str]]) -> Table:
    """Bandeau grisé d'une ligne : « Libellé : valeur » côte à côte."""
    cellules = [Paragraph(f"<b>{libelle} :</b> {valeur}", STYLE_CELL) for libelle, valeur in paires]
    table = Table([cellules], colWidths=[LARGEUR_UTILE / len(cellules)] * len(cellules))
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


def document_pdf(
    titre: str,
    elements: list,
    *,
    paysage: bool = False,
    sous_titre: str | None = None,
) -> bytes:
    """Assemble un PDF A4 (portrait, ou paysage si `paysage=True` — utiliser
    alors `LARGEUR_UTILE_PAYSAGE` pour `table_paginable`).

    `titre` est le titre du document (métadonnées) ; l'appelant pose son propre
    titre visible dans `elements` (ex. `Paragraph(..., STYLE_TITRE)`).
    `sous_titre`, s'il est fourni, est répété en en-tête de chaque page.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4) if paysage else A4,
        leftMargin=MARGE,
        rightMargin=MARGE,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=titre,
        author="Nova",
    )
    decor = _decor_page(sous_titre)
    doc.build(list(elements), onFirstPage=decor, onLaterPages=decor)
    return buffer.getvalue()
