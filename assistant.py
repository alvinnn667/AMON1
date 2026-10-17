"""
assistant.py — L'assistant de la page Questions : il répond d'après le cours, d'après le web,
ou discute simplement, et dit toujours d'où vient sa réponse.

Déroulé d'une question :
  1. Reformulation de la question de suivi (rag.reformuler) et recherche dans les cours
     (ingest.search, gratuite : calculée sur le serveur).
  2. Aiguillage : un petit appel au LLM (modèle léger d'abord) choisit le mode, au vu de la
     question et d'un aperçu des passages trouvés :
       - "cours"      : réponse à partir des extraits du cours, citations [1], [2]…
       - "web"        : question de fait hors du cours -> recherche Tavily, citations [W1], [W2]…
       - "discussion" : salutation, conseil, méthode… -> réponse directe, étiquetée « sans source ».
  3. Réponse écrite au fil de l'eau (llm.chat_stream), avec le prompt système du mode.

Ce que tu apprends ici : c'est un « routeur » (ou agent minimal). Plutôt que de tout envoyer au même
prompt, on demande d'abord au LLM de classer la demande, puis on choisit l'outil adapté (base de
cours, moteur de recherche, ou rien). Classer est une tâche facile : un petit modèle suffit.
"""

import re
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, field_validator

import db
import ingest
import llm
import prompts
import rag
import web

# Si l'aiguillage échoue (quota, JSON invalide) : on répond d'après le cours si le meilleur passage
# est assez proche de la question (similarité cosinus), sinon on discute.
SEUIL_REPLI_COURS = 0.5
TEMPERATURES = {"cours": rag.TEMPERATURE, "web": 0.1, "discussion": 0.5}
PARIS = ZoneInfo("Europe/Paris")


class Aiguillage(BaseModel):
    raison: str | None = ""
    mode: Literal["cours", "web", "discussion"]
    requete_web: str | None = ""

    @field_validator("mode", mode="before")
    @classmethod
    def _sans_majuscules(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("raison", "requete_web", mode="before")
    @classmethod
    def _vide_si_null(cls, v):
        return v or ""  # un petit modèle écrit parfois null au lieu de "" : pas une raison de tout refaire


def aiguiller(question: str, precise: str | None, echanges: list[dict], extraits: list[dict]) -> tuple[str, str]:
    """Choisit le mode ("cours", "web" ou "discussion") et la requête web éventuelle."""
    web_actif = web.web_disponible()
    meilleur = extraits[0]["score"] if extraits else 0.0
    try:
        choix = llm.chat_json(
            [
                {"role": "system", "content": prompts.SYSTEME_ROUTAGE if web_actif else prompts.SYSTEME_ROUTAGE_SANS_WEB},
                {"role": "user", "content": prompts.message_routage(echanges, question, precise, extraits)},
            ],
            Aiguillage,
            retries=1,  # petite tâche : on ne s'acharne pas, le repli est raisonnable
            temperature=0.0,
            leger=True,
        )
        mode, requete = choix.mode, " ".join(choix.requete_web.split())
    except llm.LLMError as e:
        print(f"[assistant] aiguillage impossible ({e}) : repli sur le score du meilleur passage")
        mode, requete = ("cours" if meilleur >= SEUIL_REPLI_COURS else "discussion"), ""
    if mode == "web" and not web_actif:  # le modèle a choisi le web malgré la consigne : même repli
        mode, requete = ("cours" if meilleur >= SEUIL_REPLI_COURS else "discussion"), ""
    if mode == "cours" and not extraits:
        mode = "discussion"  # aucun cours dans ce module : rien à citer
    return mode, requete


def recherches_web_restantes(pseudo: str) -> int:
    """Recherches web encore possibles aujourd'hui pour cet utilisateur (sa limite et celle de l'app)."""
    perso = web.limite_par_jour() - db.nb_recherches_web_du_jour(ingest.user_key(pseudo))
    app = web.limite_globale_par_jour() - db.nb_recherches_web_du_jour()
    return max(0, min(perso, app))


def limite_web_atteinte(pseudo: str) -> str | None:
    """Message expliquant quelle limite du jour est atteinte, ou None s'il reste des recherches."""
    if db.nb_recherches_web_du_jour(ingest.user_key(pseudo)) >= web.limite_par_jour():
        return (f"Tu as atteint ta limite personnelle de {web.nb_recherches(web.limite_par_jour())} par jour "
                "(elle protège le quota mensuel partagé par tous). Réessaie demain.")
    if db.nb_recherches_web_du_jour() >= web.limite_globale_par_jour():
        return "L'app a atteint sa limite de recherches web pour aujourd'hui (tous utilisateurs confondus). Réessaie demain."
    return None


def chercher_sur_le_web(pseudo: str, requete: str) -> list[dict]:
    """Recherche web dans les limites du jour. Lève web.WebError avec un message lisible sinon."""
    if not web.web_disponible():
        raise web.WebError("La recherche web n'est pas activée sur cette app.")
    limite = limite_web_atteinte(pseudo)
    if limite:
        raise web.WebError(limite)
    resultats = web.rechercher(requete)
    db.compter_recherche_web(ingest.user_key(pseudo))  # comptée seulement si Tavily a répondu
    return resultats


def analyser(
    pseudo: str,
    question: str,
    module: str | None = None,
    historique: list[dict] | None = None,
    requete_web_forcee: str | None = None,
) -> dict:
    """
    Étapes 1 et 2 : comprendre la question et choisir le mode.
    requete_web_forcee : bouton « Chercher sur le web » -> mode web direct, sans reformulation
    ni aiguillage (la question a déjà été comprise lors de la première réponse).
    """
    echanges = rag.historique_recent(historique)
    analyse = {"pseudo": pseudo, "question": question, "echanges": echanges, "extraits": []}
    if requete_web_forcee:
        precise = requete_web_forcee if requete_web_forcee != question else None
        return {**analyse, "precise": precise, "mode": "web", "requete_web": requete_web_forcee}

    question_recherche = rag.reformuler(question, echanges)
    precise = question_recherche if question_recherche != question else None
    extraits = ingest.search(pseudo, question_recherche, k=rag.NB_EXTRAITS, module=module)
    mode, requete = aiguiller(question, precise, echanges, extraits)
    return {**analyse, "precise": precise, "extraits": extraits, "mode": mode,
            "requete_web": requete or question_recherche}


def preparer(analyse: dict) -> dict:
    """
    Étape 3 (avant l'écriture) : recherche web si besoin, puis messages pour le LLM.
    Si le web est indisponible (pas de clé, limite du jour, erreur, aucun résultat), on bascule en
    discussion et on le dit : web_indisponible contient la raison.
    """
    mode, question, precise = analyse["mode"], analyse["question"], analyse["precise"]
    resultats, web_indisponible, requete_web = [], None, None
    if mode == "web":
        requete_web = analyse["requete_web"]
        try:
            resultats = chercher_sur_le_web(analyse["pseudo"], requete_web)
            if not resultats:
                web_indisponible = "La recherche web n'a rien trouvé."
        except web.WebError as e:
            web_indisponible = str(e)
        if web_indisponible:
            mode = "discussion"

    if mode == "cours":
        systeme, message = prompts.SYSTEME_CHAT, prompts.message_chat(question, analyse["extraits"], precise)
    elif mode == "web":
        date = datetime.now(PARIS).strftime("%d/%m/%Y")
        systeme, message = prompts.SYSTEME_WEB, prompts.message_web(question, resultats, precise, date)
    else:
        systeme, message = prompts.SYSTEME_DISCUSSION, prompts.message_discussion(
            question, precise, web_indisponible, web_actif=web.web_disponible()
        )

    return {
        "mode": mode,
        "messages": rag.messages_avec_historique(systeme, analyse["echanges"], message),
        "temperature": TEMPERATURES[mode],
        "extraits": analyse["extraits"] if mode == "cours" else [],
        "resultats_web": resultats,
        "question_recherche": precise,
        "requete_web": requete_web,  # ce qui a été envoyé (ou devait l'être) à Tavily
        "web_indisponible": web_indisponible,
    }


def citations_web(reponse: str, nb_resultats: int) -> list[int]:
    """Numéros des résultats web cités : [W2], [W1][W3], [W1, W3] (sans doublon, dans l'ordre)."""
    numeros = []
    for groupe in re.findall(r"\[(W\d+(?:\s*[,;]\s*W?\d+)*)\]", reponse, flags=re.IGNORECASE):
        for n in re.split(r"\s*[,;]\s*", groupe):
            n = int(n.lstrip("Ww"))
            if 1 <= n <= nb_resultats and n not in numeros:
                numeros.append(n)
    return numeros


def finaliser(reponse: str, prep: dict) -> dict:
    """
    Une fois la réponse écrite : sources citées et alertes, selon le mode.
    Renvoie {"reponse", "mode", "sources", "sources_web", "hors_cours", "hors_web", "sans_citation",
    "question_recherche", "requete_web", "web_indisponible"}.
    """
    mode = prep["mode"]
    reponse = reponse.strip()
    if mode == "cours":
        res = rag.finaliser(reponse, prep)
    else:
        res = {"reponse": reponse, "sources": [], "hors_cours": False, "sans_citation": False,
               "question_recherche": prep["question_recherche"]}
    res.update(mode=mode, sources_web=[], hors_web=False, requete_web=prep["requete_web"],
               web_indisponible=prep["web_indisponible"])
    if mode == "web":
        resultats = prep["resultats_web"]
        res["sources_web"] = [{"numero": n, **resultats[n - 1]} for n in citations_web(reponse, len(resultats))]
        res["hors_web"] = prompts.PAS_SUR_LE_WEB.lower().rstrip(".") in reponse.lower()
        res["sans_citation"] = not res["hors_web"] and not res["sources_web"]
    return res


def repondre(pseudo: str, question: str, module: str | None = None, historique: list[dict] | None = None) -> dict:
    """Réponse complète en une fois (scripts de test). L'interface, elle, affiche la réponse au fil de l'eau."""
    prep = preparer(analyser(pseudo, question, module, historique))
    reponse = llm.chat(prep["messages"], temperature=prep["temperature"])
    return finaliser(reponse, prep)
