"""
rag.py — Étape 3 : répondre à une question en s'appuyant sur le cours (RAG).

RAG = Retrieval-Augmented Generation :
  1. Retrieval  : on cherche les passages du cours les plus proches de la question.
  2. Generation : on donne ces passages au LLM, avec l'ordre de ne répondre qu'à partir d'eux.

Mémoire de conversation : un LLM n'a aucune mémoire entre deux appels. Pour qu'il comprenne
« et sa formule ? », on lui renvoie les derniers échanges à chaque question. Mais la recherche
vectorielle, elle, ne voit que la question : « et sa formule ? » ne ressemble à aucun passage.
On commence donc par réécrire la question pour qu'elle se comprenne seule (« Quelle est la
formule de la bande de Carson ? »), puis on cherche avec cette version.
"""

import re

import ingest
import llm
import prompts

NB_EXTRAITS = 5  # nombre de chunks donnés au LLM
MEMOIRE_ECHANGES = 3  # nombre de questions/réponses précédentes renvoyées au LLM
TEMPERATURE = 0.1  # peu de créativité : on veut de la fidélité au cours
TAILLE_MAX_MESSAGE = 700  # caractères gardés par message de l'historique (coût et limite de contexte)

_CITATION = re.compile(r"\s*\[\d+(?:\s*[,;]\s*\d+)*\]")


def citations_utilisees(reponse: str, nb_extraits: int) -> list[int]:
    """Trouve les numéros cités dans la réponse : [2], [1][3], [1, 3]… (sans doublon, dans l'ordre)."""
    numeros = []
    for groupe in re.findall(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]", reponse):
        for n in re.split(r"\s*[,;]\s*", groupe):
            n = int(n)
            if 1 <= n <= nb_extraits and n not in numeros:
                numeros.append(n)
    return numeros


def historique_recent(historique: list[dict] | None) -> list[dict]:
    """
    Les derniers échanges, allégés : les citations [n] des anciennes réponses sont retirées
    (elles renvoyaient à d'autres extraits que ceux de la question actuelle) et les messages
    longs sont coupés.
    """
    recents = [m for m in (historique or []) if m.get("role") in ("user", "assistant")][-2 * MEMOIRE_ECHANGES :]
    resultat = []
    for m in recents:
        texte = _CITATION.sub("", m["contenu"]).strip()
        if len(texte) > TAILLE_MAX_MESSAGE:
            texte = texte[:TAILLE_MAX_MESSAGE].rsplit(" ", 1)[0] + "…"
        resultat.append({"role": m["role"], "contenu": texte})
    return resultat


def reformuler(question: str, historique: list[dict]) -> str:
    """Réécrit une question de suivi pour qu'elle se comprenne seule. Sans historique : inchangée."""
    if not historique:
        return question
    try:
        reecrite = llm.chat(
            [
                {"role": "system", "content": prompts.SYSTEME_REFORMULATION},
                {"role": "user", "content": prompts.message_reformulation(historique, question)},
            ],
            temperature=0.0,
        )
    except llm.LLMError:
        return question  # la recherche marchera moins bien, mais on peut quand même répondre
    reecrite = reecrite.strip().splitlines()[0].strip(" «»\"'") if reecrite.strip() else ""
    # Garde-fou : une « reformulation » vide ou très longue est sûrement une réponse déguisée.
    if not reecrite or len(reecrite) > max(300, 4 * len(question)):
        return question
    return reecrite


def preparer(pseudo: str, question: str, module: str | None = None, historique: list[dict] | None = None) -> dict:
    """
    Tout ce qui précède l'écriture de la réponse : reformulation, recherche, messages pour le LLM.
    Renvoie {"messages", "extraits", "question_recherche"}, ou {"reponse_directe": dict} s'il n'y a
    rien à demander au LLM (aucun cours indexé).
    """
    echanges = historique_recent(historique)
    question_recherche = reformuler(question, echanges)
    extraits = ingest.search(pseudo, question_recherche, k=NB_EXTRAITS, module=module)
    if not extraits:
        return {
            "reponse_directe": {
                "reponse": "Tu n'as encore indexé aucun cours. Ajoute un PDF dans l'onglet « Mes cours ».",
                "sources": [],
                "hors_cours": True,
                "sans_citation": False,
            }
        }
    precise = question_recherche if question_recherche != question else None
    messages = [{"role": "system", "content": prompts.SYSTEME_CHAT}]
    messages += [{"role": m["role"], "content": m["contenu"]} for m in echanges]
    messages.append({"role": "user", "content": prompts.message_chat(question, extraits, precise)})
    return {"messages": messages, "extraits": extraits, "question_recherche": precise}


def finaliser(reponse: str, prep: dict) -> dict:
    """
    Une fois la réponse écrite : sources citées et alertes.
    Renvoie {"reponse", "sources", "hors_cours", "sans_citation", "question_recherche"}.
    Chaque extrait cité contient : numero, texte, source, page, module, score.
    """
    reponse = reponse.strip()
    extraits = prep["extraits"]
    hors_cours = prompts.PAS_DANS_LE_COURS.lower().rstrip(".") in reponse.lower()
    numeros = citations_utilisees(reponse, len(extraits))
    sources = [{"numero": n, **extraits[n - 1]} for n in numeros]
    return {
        "reponse": reponse,
        "sources": sources,
        "hors_cours": hors_cours,
        # Une réponse sans aucune citation est suspecte : le modèle a peut-être inventé.
        "sans_citation": not hors_cours and not sources,
        "question_recherche": prep["question_recherche"],
    }


def repondre(pseudo: str, question: str, module: str | None = None, historique: list[dict] | None = None) -> dict:
    """
    Réponse complète en une fois (scripts de test). L'interface, elle, affiche la réponse
    au fil de l'eau : preparer() -> llm.chat_stream() -> finaliser().
    module=None : recherche dans tous les modules. historique : messages précédents (mémoire).
    """
    prep = preparer(pseudo, question, module, historique)
    if "reponse_directe" in prep:
        return prep["reponse_directe"]
    reponse = llm.chat(prep["messages"], temperature=TEMPERATURE)
    return finaliser(reponse, prep)
