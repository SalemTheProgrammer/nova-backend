"""Sous-agent RAG sur la base documentaire (normes, procédures, manuels…), avec
citations + page."""
from __future__ import annotations

from langchain_core.tools import tool

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.services.citation_service import extraire_phrases_pertinentes
from app.services.vector_store import search_documents

logger = get_logger(__name__)


def _as_int(valeur: object) -> int | None:
    """Pinecone renvoie les nombres en float (ex. page=7.0) : normalise en int."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, (int, float)):
        return int(valeur)
    return None


@tool(response_format="content_and_artifact")
def rechercher_documents(question: str) -> tuple[str, dict | None]:
    """Recherche dans les documents téléversés (normes BPF/GMP, procédures qualité,
    manuels machines, fiches techniques…) et renvoie les passages les plus pertinents
    AVEC leur source et leur numéro de page.

    À utiliser dès que l'opérateur pose une question dont la réponse se trouve dans la
    documentation (« que dit la norme sur… », « procédure de… », « comment régler la
    machine… »). Construis ensuite ta réponse à partir de ces passages et CITE
    systématiquement la source et la page (ex. « (BPF Tunisie, p. 12) »).

    Les documents peuvent être en français OU en anglais : formule `question` avec les
    termes clés dans les deux langues quand un équivalent existe (ex. pour le TRS :
    « TRS OEE Overall Equipment Effectiveness définition »).
    """
    logger.info("tool_rechercher_documents", question=question)
    try:
        resultats = search_documents(question, top_k=5)
    except AppError as exc:
        return f"Erreur lors de la recherche documentaire : {exc.message}", None
    if not resultats:
        return (
            "Aucun passage pertinent trouvé dans la base documentaire. "
            "Indique-le honnêtement à l'opérateur."
        ), None
    # Phrases précises à surligner dans le viewer (pas le chunk entier).
    extraits_par_passage = extraire_phrases_pertinentes(
        question, [doc.page_content for doc, _ in resultats]
    )

    blocs: list[str] = []
    passages: list[dict] = []
    for i, (doc, score) in enumerate(resultats, start=1):
        nom = doc.metadata.get("document_nom", "—")
        page = _as_int(doc.metadata.get("page"))
        document_id = _as_int(doc.metadata.get("document_id"))
        source = doc.metadata.get("source", "—")
        blocs.append(
            f"[{i}] Source : {nom} ({source}), page {page if page is not None else '?'} — "
            f"pertinence {score:.2f}\n"
            f"Citation : « {doc.page_content.strip()} »"
        )
        passages.append(
            {
                "document_id": document_id,
                "document_nom": str(nom),
                "source": str(source),
                "page": page,
                "score": round(float(score), 3),
                "citation": doc.page_content.strip(),
                "extraits": extraits_par_passage[i - 1],
            }
        )
    entete = (
        "Passages trouvés dans la base documentaire "
        "(utilise-les pour répondre et cite source + page) :"
    )
    artifact = {"kind": "documents", "question": question, "passages": passages}
    return entete + "\n\n" + "\n\n".join(blocs), artifact
