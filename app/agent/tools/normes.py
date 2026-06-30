"""Sous-agent RAG sur les documents normatifs (BPF/GMP…), avec citations + page."""
from __future__ import annotations

from langchain_core.tools import tool

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.services.vector_store import search_normes

logger = get_logger(__name__)


@tool
def rechercher_normes(question: str) -> str:
    """Recherche dans les documents normatifs téléversés (normes BPF/GMP, procédures, qualité)
    et renvoie les passages les plus pertinents AVEC leur source et leur numéro de page.

    À utiliser dès que l'opérateur pose une question réglementaire ou qualité (« que dit la
    norme sur… », « procédure de… », « exigence BPF… »). Construis ensuite ta réponse à partir
    de ces passages et CITE systématiquement la source et la page (ex. « (Norme X, p. 12) »).
    """
    logger.info("tool_rechercher_normes", question=question)
    try:
        resultats = search_normes(question, top_k=5)
    except AppError as exc:
        return f"Erreur lors de la recherche dans les normes : {exc.message}"
    if not resultats:
        return (
            "Aucun passage pertinent trouvé dans les normes indexées. "
            "Indique-le honnêtement à l'opérateur."
        )
    blocs: list[str] = []
    for i, (doc, score) in enumerate(resultats, start=1):
        nom = doc.metadata.get("norme_nom", "—")
        page = doc.metadata.get("page", "?")
        source = doc.metadata.get("source", "—")
        blocs.append(
            f"[{i}] Source : {nom} ({source}), page {page} — pertinence {score:.2f}\n"
            f"Citation : « {doc.page_content.strip()} »"
        )
    entete = (
        "Passages trouvés dans les normes (utilise-les pour répondre et cite source + page) :"
    )
    return entete + "\n\n" + "\n\n".join(blocs)
