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

Ce fichier fournit les briques du mode « cours » (mémoire, reformulation, citations) ;
assistant.py les assemble pour la page Questions et y ajoute la recherche web et la discussion.
"""

import re

import llm
import prompts

NB_EXTRAITS = 5  # nombre de chunks donnés au LLM
MEMOIRE_ECHANGES = 3  # nombre de questions/réponses précédentes renvoyées au LLM
TEMPERATURE = 0.1  # peu de créativité : on veut de la fidélité au cours
TAILLE_MAX_MESSAGE = 700  # caractères gardés par message de l'historique (coût et limite de contexte)

# Citations d'une réponse : [2], [1, 3] (cours) ou [W1], [W1, W2] (web).
_CITATION = re.compile(r"\s*\[W?\d+(?:\s*[,;]\s*W?\d+)*\]")


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
    messages = [m for m in (historique or []) if m.get("role") in ("user", "assistant")]
    # Deux réponses de suite (« Pas dans ton cours » puis la recherche web relancée par le bouton) :
    # seule la dernière compte, pour que l'historique alterne bien question / réponse.
    messages = [m for i, m in enumerate(messages)
                if not (m["role"] == "assistant" and i + 1 < len(messages) and messages[i + 1]["role"] == "assistant")]
    recents = messages[-2 * MEMOIRE_ECHANGES :]
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
            leger=True,  # petite tâche : le modèle léger suffit et préserve le quota du gros modèle
        )
    except llm.LLMError:
        return question  # la recherche marchera moins bien, mais on peut quand même répondre
    reecrite = reecrite.strip().splitlines()[0].strip(" «»\"'") if reecrite.strip() else ""
    # Garde-fou : une « reformulation » vide ou très longue est sûrement une réponse déguisée.
    if not reecrite or len(reecrite) > max(300, 4 * len(question)):
        return question
    return reecrite


def messages_avec_historique(systeme: str, echanges: list[dict], message: str) -> list[dict]:
    """Prompt système + échanges précédents (mémoire) + nouveau message, au format du LLM."""
    messages = [{"role": "system", "content": systeme}]
    messages += [{"role": m["role"], "content": m["contenu"]} for m in echanges]
    messages.append({"role": "user", "content": message})
    return messages


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

