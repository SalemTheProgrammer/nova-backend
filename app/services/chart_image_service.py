"""Rendu image (PNG) des graphiques et jauges pour les canaux sans écran riche.

Le frontend web rend les specs `{kind: "chart"}` en Recharts interactif ; sur
WhatsApp il n'y a pas de moteur de rendu — ce service transforme la même spec
en PNG avec matplotlib (backend "Agg", sans interface graphique). Les jauges
sont dessinées en vectoriel (arcs matplotlib, même rendu qu'un SVG) puis
rasterisées, car WhatsApp n'affiche pas les fichiers SVG en aperçu.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # rendu hors écran (thread-safe, pas de fenêtre)

import matplotlib.pyplot as plt
from matplotlib.patches import Wedge

# Palette proche du dashboard Nova (bleu primaire, vert bonnes, rouge rebuts…).
COULEURS = ["#1049b8", "#10b981", "#ef4444", "#f59e0b", "#8b5cf6", "#06b6d4", "#64748b"]
COULEUR_FOND = "#ffffff"
COULEUR_TEXTE = "#0f172a"
COULEUR_GRILLE = "#e2e8f0"

DPI = 150


def _figure(largeur: float = 8.0, hauteur: float = 4.5):
    fig, ax = plt.subplots(figsize=(largeur, hauteur), dpi=DPI)
    fig.patch.set_facecolor(COULEUR_FOND)
    ax.set_facecolor(COULEUR_FOND)
    return fig, ax


def _exporter(fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", facecolor=COULEUR_FOND)
    plt.close(fig)
    return buffer.getvalue()


def _style_axes(ax, titre: str, unit: str | None) -> None:
    ax.set_title(titre, color=COULEUR_TEXTE, fontsize=13, fontweight="bold", pad=12)
    ax.tick_params(colors=COULEUR_TEXTE, labelsize=9)
    ax.grid(True, axis="y", color=COULEUR_GRILLE, linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COULEUR_GRILLE)
    if unit:
        ax.set_ylabel(unit, color=COULEUR_TEXTE, fontsize=10)


def rendre_graphique_png(spec: dict) -> bytes:
    """Rend une spec `{kind:"chart", chart_type, title, unit, series}` en PNG."""
    chart_type = spec.get("chart_type", "line")
    titre = spec.get("title", "Graphique")
    unit = spec.get("unit")
    series = spec.get("series", [])

    if chart_type == "pie":
        # Camembert : une seule série attendue.
        data = series[0]["data"] if series else []
        labels = [str(p["x"]) for p in data]
        valeurs = [max(0.0, float(p["y"])) for p in data]
        fig, ax = _figure(6.5, 5.0)
        if sum(valeurs) > 0:
            ax.pie(
                valeurs,
                labels=labels,
                colors=COULEURS[: len(valeurs)] or COULEURS,
                autopct="%1.0f%%",
                textprops={"color": COULEUR_TEXTE, "fontsize": 9},
                startangle=90,
                counterclock=False,
            )
        ax.set_title(titre, color=COULEUR_TEXTE, fontsize=13, fontweight="bold", pad=12)
        return _exporter(fig)

    fig, ax = _figure()
    nb_series = max(1, len(series))
    for i, serie in enumerate(series):
        data = serie["data"]
        xs = [str(p["x"]) for p in data]
        ys = [float(p["y"]) for p in data]
        couleur = COULEURS[i % len(COULEURS)]
        if chart_type == "bar":
            # Barres groupées si plusieurs séries.
            largeur = 0.8 / nb_series
            positions = [j + i * largeur - 0.4 + largeur / 2 for j in range(len(xs))]
            ax.bar(positions, ys, width=largeur, color=couleur, label=serie["name"])
            ax.set_xticks(range(len(xs)))
            ax.set_xticklabels(xs, rotation=30, ha="right")
        elif chart_type == "area":
            ax.fill_between(range(len(xs)), ys, color=couleur, alpha=0.35)
            ax.plot(range(len(xs)), ys, color=couleur, linewidth=2, label=serie["name"])
            ax.set_xticks(range(len(xs)))
            ax.set_xticklabels(xs, rotation=0 if len(xs) <= 12 else 30)
        else:  # line
            ax.plot(
                range(len(xs)), ys, color=couleur, linewidth=2, marker="o",
                markersize=4, label=serie["name"],
            )
            ax.set_xticks(range(len(xs)))
            ax.set_xticklabels(xs, rotation=0 if len(xs) <= 12 else 30)

    _style_axes(ax, titre, unit)
    if len(series) > 1:
        ax.legend(loc="upper left", fontsize=9, frameon=False, labelcolor=COULEUR_TEXTE)
    return _exporter(fig)


def _couleur_jauge(pct: float) -> str:
    """Rouge < 50 %, orange < 74 %, vert au-delà (repères TRS usuels)."""
    if pct < 50:
        return "#ef4444"
    if pct < 74:
        return "#f59e0b"
    return "#10b981"


def rendre_jauge_png(
    titre: str,
    valeur_pct: float,
    objectif_pct: float | None = None,
    sous_titre: str | None = None,
) -> bytes:
    """Jauge semi-circulaire (comme les vignettes machines du dashboard) en PNG.

    Dessinée en vectoriel (arcs) puis rasterisée — WhatsApp ne prévisualise pas
    le SVG, le PNG reste net à ce DPI.
    """
    valeur_pct = max(0.0, min(100.0, float(valeur_pct)))
    fig, ax = plt.subplots(figsize=(5.0, 3.4), dpi=DPI)
    fig.patch.set_facecolor(COULEUR_FOND)
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-0.35, 1.25)
    ax.axis("off")

    # Fond de jauge (arc gris) puis arc de valeur.
    ax.add_patch(Wedge((0, 0), 1.0, 0, 180, width=0.28, facecolor="#e2e8f0"))
    angle_valeur = 180 - (valeur_pct / 100.0) * 180
    ax.add_patch(
        Wedge((0, 0), 1.0, angle_valeur, 180, width=0.28, facecolor=_couleur_jauge(valeur_pct))
    )

    # Trait repère de l'objectif.
    if objectif_pct is not None:
        import math

        angle_obj = math.radians(180 - (max(0.0, min(100.0, objectif_pct)) / 100.0) * 180)
        x0, y0 = 0.68 * math.cos(angle_obj), 0.68 * math.sin(angle_obj)
        x1, y1 = 1.04 * math.cos(angle_obj), 1.04 * math.sin(angle_obj)
        ax.plot([x0, x1], [y0, y1], color=COULEUR_TEXTE, linewidth=1.6, linestyle="--")

    ax.text(
        0, 0.28, f"{valeur_pct:.0f}%", ha="center", va="center",
        fontsize=30, fontweight="bold", color=_couleur_jauge(valeur_pct),
    )
    ax.text(0, 1.16, titre, ha="center", va="center", fontsize=12, fontweight="bold", color=COULEUR_TEXTE)
    bas = sous_titre or (f"Objectif {objectif_pct:.0f}%" if objectif_pct is not None else "")
    if bas:
        ax.text(0, -0.22, bas, ha="center", va="center", fontsize=9, color="#64748b")
    return _exporter(fig)
