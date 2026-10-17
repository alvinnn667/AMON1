"""
web.py — Recherche web avec Tavily, pour les questions de faits que le cours ne couvre pas.

Tavily est un moteur de recherche pensé pour les LLM : au lieu d'une page de liens, il renvoie
directement pour chaque résultat un titre, une URL et un extrait du texte de la page. On donne ces
extraits au LLM, numérotés [W1], [W2]…, exactement comme les extraits du cours [1], [2]… : c'est
toujours du RAG, mais la « base de documents » est le web.

Configuration : TAVILY_API_KEY (fichier .env en local, secrets de l'hébergeur en ligne).
Sans clé, la recherche web est simplement désactivée (web_disponible() = False).
Le tier gratuit de Tavily donne 1 000 recherches par mois, partagées entre tous les testeurs :
d'où une limite personnelle par jour (WEB_RECHERCHES_PAR_JOUR, 10 par défaut) et une limite pour
toute l'app par jour (WEB_RECHERCHES_GLOBAL_PAR_JOUR, 30 par défaut).
"""

import os
import re
from urllib.parse import urlparse

import httpx  # installé avec openai : pas de dépendance en plus

TAVILY_URL = "https://api.tavily.com/search"  # modifiable par les scripts de test (faux serveur)
TIMEOUT = 15  # secondes
NB_RESULTATS = 5
TAILLE_MAX_EXTRAIT = 700  # caractères gardés par résultat (coût et limite de contexte du LLM)
TAILLE_MAX_REQUETE = 300  # caractères
TAILLE_MAX_TITRE = 200  # caractères
LIMITE_PAR_JOUR_DEFAUT = 10  # par utilisateur
LIMITE_GLOBALE_PAR_JOUR_DEFAUT = 30  # pour toute l'app : 30 x 31 jours < 1 000 recherches gratuites/mois

# Les variables sont lues au moment de l'appel, pas à l'import : llm.py (importé par l'app avant toute
# question) a alors déjà chargé le .env et recopié les secrets de l'hébergeur dans l'environnement.


class WebError(RuntimeError):
    """Erreur lisible par un humain, affichable telle quelle dans l'interface."""


def _cle() -> str:
    return os.getenv("TAVILY_API_KEY", "").strip()


def _entier_env(nom: str, defaut: int) -> int:
    try:
        return max(0, int(os.getenv(nom, defaut)))
    except ValueError:
        return defaut


def limite_par_jour() -> int:
    """Recherches web autorisées par utilisateur et par jour (WEB_RECHERCHES_PAR_JOUR, 10 par défaut)."""
    return _entier_env("WEB_RECHERCHES_PAR_JOUR", LIMITE_PAR_JOUR_DEFAUT)


def limite_globale_par_jour() -> int:
    """Recherches web par jour pour toute l'app (WEB_RECHERCHES_GLOBAL_PAR_JOUR, 30 par défaut)."""
    return _entier_env("WEB_RECHERCHES_GLOBAL_PAR_JOUR", LIMITE_GLOBALE_PAR_JOUR_DEFAUT)


def nb_recherches(n: int) -> str:
    """« 1 recherche web », « 20 recherches web » (accord pour les messages)."""
    return f"{n} recherche{'s' if n > 1 else ''} web"


def web_disponible() -> bool:
    """La recherche web est configurée (clé présente). Ne dit rien du quota restant."""
    return bool(_cle())


# URL de source acceptée : http(s), nom d'hôte simple (lettres, chiffres, points, tirets), port facultatif,
# puis un chemin sans espace ni antislash. Refuse « https://site-connu@evil.com » et
# « https://evil.com\@site-connu » : le navigateur et urlparse n'y verraient pas le même site.
_URL_SURE = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:[/?#][^\s\\<>\"`]*)?", re.IGNORECASE)


def url_sure(url: str) -> bool:
    return bool(_URL_SURE.fullmatch(url or ""))


def domaine(url: str) -> str:
    """« https://www.insee.fr/fr/statistiques » -> « insee.fr »."""
    # hostname, pas netloc : « https://fr.wikipedia.org@evil.com/ » doit afficher « evil.com »
    # (netloc contiendrait l'identifiant placé avant le @, qui imite un site de confiance).
    try:
        hote = urlparse(url).hostname or ""
    except ValueError:
        hote = ""
    return hote[4:] if hote.startswith("www.") else hote


# Message affiché selon le code d'erreur de Tavily (jamais la clé, ni la réponse brute).
_ERREURS = {
    401: "La clé TAVILY_API_KEY est refusée. Vérifie-la dans les secrets.",
    429: "Trop de recherches web en même temps. Réessaie dans une minute.",
    432: "Le quota de recherches web de l'app est épuisé pour ce mois.",
    433: "Le quota de recherches web de l'app est épuisé pour ce mois.",
    400: "La recherche web a été refusée (requête invalide).",
    422: "La recherche web a été refusée (requête invalide).",
}


def rechercher(requete: str) -> list[dict]:
    """
    Lance une recherche web (1 crédit Tavily) et renvoie les résultats, du plus pertinent au moins
    pertinent : [{"titre", "url", "domaine", "extrait", "score"}]. Lève WebError en cas de problème.
    """
    cle = _cle()
    if not cle:
        raise WebError("La recherche web n'est pas configurée (TAVILY_API_KEY absente).")
    if not cle.isascii():  # un en-tête HTTP n'accepte que l'ASCII (ex. guillemet typographique collé)
        raise WebError("La clé TAVILY_API_KEY contient un caractère invalide.")
    requete = " ".join(requete.split())[:TAILLE_MAX_REQUETE]
    if not requete:
        raise WebError("La recherche web a été refusée (requête vide).")
    corps = {
        "query": requete,
        "search_depth": "basic",  # 1 crédit (advanced en coûte 2)
        "max_results": NB_RESULTATS,
        "topic": "general",
        "include_answer": False,  # on veut des sources, pas une réponse toute faite
    }
    try:
        resp = httpx.post(
            TAVILY_URL, json=corps, headers={"Authorization": f"Bearer {cle}"}, timeout=TIMEOUT
        )
    except httpx.TimeoutException as e:
        raise WebError("La recherche web a mis trop de temps à répondre. Réessaie dans un instant.") from e
    except httpx.HTTPError as e:
        raise WebError("Impossible de joindre le service de recherche web (réseau).") from e

    if resp.status_code != 200:
        print(f"[web] Tavily a répondu {resp.status_code}")  # le code seulement : rien de la clé
        if resp.status_code >= 500:
            raise WebError("Le service de recherche web est en panne. Réessaie plus tard.")
        raise WebError(_ERREURS.get(resp.status_code, f"La recherche web a échoué (erreur {resp.status_code})."))

    try:
        bruts = resp.json().get("results") or []
    except (ValueError, AttributeError) as e:
        raise WebError("Le service de recherche web a renvoyé une réponse illisible.") from e
    if not isinstance(bruts, list):
        bruts = []  # format inattendu ("results": 5, true…) : comme si rien n'avait été trouvé

    resultats = []
    for r in bruts:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "")
        if not url_sure(url):
            continue  # on n'affichera que des liens web classiques et sans ambiguïté (voir url_sure)
        extrait = " ".join(str(r.get("content") or "").split())
        if len(extrait) > TAILLE_MAX_EXTRAIT:
            extrait = extrait[:TAILLE_MAX_EXTRAIT].rsplit(" ", 1)[0] + "…"
        resultats.append({
            "titre": " ".join(str(r.get("title") or domaine(url)).split())[:TAILLE_MAX_TITRE],
            "url": url,
            "domaine": domaine(url),
            "extrait": extrait,
            "score": round(r["score"], 3) if isinstance(r.get("score"), (int, float)) else 0.0,
        })
    return resultats
