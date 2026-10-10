"""
rag.py — Étape 3 : répondre à une question en s'appuyant sur le cours (RAG).

RAG = Retrieval-Augmented Generation :
  1. Retrieval  : on cherche les passages du cours les plus proches de la question.
  2. Generation : on donne ces passages au LLM, avec l'ordre de ne répondre qu'à partir d'eux.
"""

import re

import ingest
import llm
import prompts

NB_EXTRAITS = 5  # nombre de chunks donnés au LLM


def citations_utilisees(reponse: str, nb_extraits: int) -> list[int]:
    """Trouve les numéros cités dans la réponse : [2], [1][3], [1, 3]… (sans doublon, dans l'ordre)."""
    numeros = []
    for groupe in re.findall(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]", reponse):
        for n in re.split(r"\s*[,;]\s*", groupe):
            n = int(n)
            if 1 <= n <= nb_extraits and n not in numeros:
                numeros.append(n)
    return numeros


def repondre(pseudo: str, question: str, module: str | None = None) -> dict:
    """
    Renvoie {"reponse": str, "sources": [extraits cités], "hors_cours": bool, "sans_citation": bool}.
    Chaque extrait cité contient : numero, texte, source, page, module, score.
    module=None : recherche dans tous les modules de l'utilisateur.
    """
    extraits = ingest.search(pseudo, question, k=NB_EXTRAITS, module=module)
    if not extraits:
        return {
            "reponse": "Tu n'as encore indexé aucun cours. Ajoute un PDF dans l'onglet « Mes cours ».",
            "sources": [],
            "hors_cours": True,
            "sans_citation": False,
        }

    reponse = llm.chat(
        [
            {"role": "system", "content": prompts.SYSTEME_CHAT},
            {"role": "user", "content": prompts.message_chat(question, extraits)},
        ],
        temperature=0.1,  # peu de créativité : on veut de la fidélité au cours
    ).strip()

    hors_cours = prompts.PAS_DANS_LE_COURS.lower().rstrip(".") in reponse.lower()
    numeros = citations_utilisees(reponse, len(extraits))
    sources = [{"numero": n, **extraits[n - 1]} for n in numeros]
    return {
        "reponse": reponse,
        "sources": sources,
        "hors_cours": hors_cours,
        # Une réponse sans aucune citation est suspecte : le modèle a peut-être inventé.
        "sans_citation": not hors_cours and not sources,
    }
