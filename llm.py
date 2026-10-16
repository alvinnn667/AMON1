"""
llm.py — Couche d'abstraction pour le LLM et les embeddings.

Tout le reste de l'application passe par ce fichier, sans savoir quel
fournisseur tourne derrière. Le mode est choisi par la variable d'environnement
LLM_MODE :

  - LLM_MODE=local : Ollama sur ta machine
        LLM        -> qwen2.5:7b      (modèle open source 7B, bon en français)
        Embeddings -> bge-m3          (multilingue, très bon en français)

  - LLM_MODE=api   : pour l'hébergement gratuit (CPU, peu de RAM)
        LLM        -> Groq (API compatible OpenAI), avec une liste de modèles
                      de secours si on atteint la limite gratuite d'un modèle
        Embeddings -> fastembed, calculés sur le serveur lui-même (CPU, ~220 Mo)

Astuce : Ollama et Groq parlent tous les deux le "format OpenAI", donc un seul
client (la librairie `openai`) suffit pour les deux modes.
"""

import json
import os
import re
from functools import lru_cache

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

# Charge le fichier .env s'il existe. Les variables déjà définies dans le
# terminal (ex : LLM_MODE=api python test_llm.py) restent prioritaires.
load_dotenv()


def _secrets_hebergeur_vers_env() -> None:
    """
    En ligne (Streamlit Community Cloud), les secrets sont saisis dans les réglages de l'app
    et lus par st.secrets. On les recopie dans les variables d'environnement pour que tout
    le code les lise de la même façon (os.getenv), en local comme en ligne.
    """
    try:
        import streamlit as st

        for cle, valeur in st.secrets.items():
            if isinstance(valeur, (str, int, float, bool)) and cle not in os.environ:
                os.environ[cle] = str(valeur)
    except Exception:
        pass  # pas de secrets.toml (cas normal en local) : on garde le .env


_secrets_hebergeur_vers_env()

# ---------------------------------------------------------------------------
# Configuration (tout est surchargeable par variable d'environnement)
# ---------------------------------------------------------------------------
LLM_MODE = os.getenv("LLM_MODE", "local").strip().lower()

# Mode local (Ollama)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")

# Mode api (Groq + fastembed)
GROQ_URL = "https://api.groq.com/openai/v1"
# Ordre de préférence : si le 1er modèle est limité (erreur 429), on passe au suivant.
# Chaque modèle a son propre quota gratuit chez Groq, ça multiplie la capacité.
GROQ_MODELS = [
    m.strip()
    for m in os.getenv(
        "GROQ_MODELS",
        "llama-3.3-70b-versatile,openai/gpt-oss-20b,llama-3.1-8b-instant",
    ).split(",")
    if m.strip()
]
FASTEMBED_MODEL = os.getenv(
    "FASTEMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

if LLM_MODE not in ("local", "api"):
    raise ValueError(f"LLM_MODE doit valoir 'local' ou 'api' (reçu : {LLM_MODE!r})")


class LLMError(RuntimeError):
    """Erreur lisible par un humain, affichable telle quelle dans l'interface."""


class JSONRefuseError(LLMError):
    """Aucun modèle n'a produit de JSON valide pour cette requête (on peut réessayer)."""


# ---------------------------------------------------------------------------
# Clients (créés une seule fois grâce au cache)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _client() -> OpenAI:
    if LLM_MODE == "local":
        # Ollama n'a pas besoin de clé, mais la librairie en exige une : on met n'importe quoi.
        # Timeout large : le 1er appel charge le modèle en mémoire (quelques secondes).
        return OpenAI(base_url=OLLAMA_URL, api_key="ollama", timeout=180, max_retries=0)

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise LLMError(
            "GROQ_API_KEY est vide. Mets ta clé dans le fichier .env (en local) "
            "ou dans les secrets de l'hébergeur (en ligne)."
        )
    # max_retries=0 : on gère nous-mêmes la bascule vers un autre modèle.
    return OpenAI(base_url=GROQ_URL, api_key=api_key, timeout=60, max_retries=0)


@lru_cache(maxsize=1)
def _fastembed_model():
    # Import ici pour ne pas charger fastembed (et sa RAM) en mode local.
    from fastembed import TextEmbedding

    # threads=1 : on reste sobre en CPU/RAM sur l'hébergement gratuit.
    return TextEmbedding(model_name=FASTEMBED_MODEL, threads=1)


# ---------------------------------------------------------------------------
# Génération de texte
# ---------------------------------------------------------------------------
def chat(messages: list[dict], temperature: float = 0.2, json_mode: bool = False) -> str:
    """
    Envoie une conversation au LLM et renvoie le texte de la réponse.

    messages : [{"role": "system"|"user"|"assistant", "content": "..."}]
    json_mode : force le modèle à répondre avec un objet JSON
                (le prompt doit contenir le mot "JSON", exigence de Groq).
    """
    kwargs = {"messages": messages, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    if LLM_MODE == "local":
        try:
            resp = _client().chat.completions.create(model=OLLAMA_MODEL, **kwargs)
        except APIConnectionError as e:
            raise LLMError(
                "Impossible de joindre Ollama. Vérifie que l'application Ollama est "
                "lancée (icône lama dans la barre des menus)."
            ) from e
        except APIStatusError as e:
            raise LLMError(
                f"Ollama a répondu une erreur ({e.status_code}). Le modèle "
                f"'{OLLAMA_MODEL}' est-il installé ? -> ollama pull {OLLAMA_MODEL}"
            ) from e
        return resp.choices[0].message.content or ""

    # Mode api : on essaie les modèles Groq dans l'ordre. Un modèle « en panne » pour cette
    # requête (quota, JSON refusé par Groq, erreur serveur) passe la main au suivant.
    quota_atteint = json_refuse = False
    last_error = None
    for model in GROQ_MODELS:
        extra = {}
        if model.startswith("openai/gpt-oss"):
            # Modèles qui « réfléchissent » avant de répondre : on limite cette réflexion,
            # sinon ils épuisent leur budget et renvoient parfois une réponse vide.
            extra["reasoning_effort"] = "low"
        try:
            resp = _client().chat.completions.create(model=model, **kwargs, **extra)
            contenu = resp.choices[0].message.content or ""
            if json_mode and not contenu.strip():
                json_refuse, last_error = True, None
                print(f"[llm] {model} : réponse vide, modèle suivant")
                continue
            return contenu
        except RateLimitError as e:
            quota_atteint, last_error = True, e  # quota atteint pour ce modèle -> on tente le suivant
            print(f"[llm] {model} : quota atteint, modèle suivant")
        except APIConnectionError as e:
            raise LLMError("Impossible de joindre l'API Groq (réseau).") from e
        except APIStatusError as e:
            last_error = e
            if e.status_code == 401:
                raise LLMError("Clé GROQ_API_KEY refusée (401). Vérifie-la dans les secrets.") from e
            if e.code == "json_validate_failed" or "json_validate_failed" in str(e):
                # Groq a refusé la sortie du modèle (JSON invalide ou vide) : on essaie le suivant.
                json_refuse = True
                print(f"[llm] {model} : JSON refusé par Groq, modèle suivant")
            elif e.status_code >= 500 or (e.status_code in (400, 404) and "model" in str(e).lower()):
                print(f"[llm] {model} : erreur {e.status_code}, modèle suivant")  # panne ou modèle retiré
            else:
                print(f"[llm] {model} : erreur {e.status_code} : {e.message}")
                raise LLMError(f"Le service d'IA a renvoyé une erreur ({e.status_code}). Réessaie dans un instant.") from e

    if json_refuse and not quota_atteint:
        raise JSONRefuseError("Les modèles n'ont pas produit de JSON valide.") from last_error
    raise LLMError(
        "Limite gratuite de l'API atteinte sur tous les modèles. "
        "Réessaie dans une minute."
    ) from last_error


def _extract_json(raw: str) -> str:
    """Nettoie une réponse : retire les ```json ... ``` et garde de la 1re { à la dernière }."""
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start : end + 1] if start != -1 and end > start else raw


def chat_json(
    messages: list[dict],
    schema: type[BaseModel],
    retries: int = 2,
    temperature: float = 0.2,
) -> BaseModel:
    """
    Demande une réponse JSON et la valide avec un modèle pydantic.
    Si le JSON est invalide (ou ne respecte pas le schéma), on renvoie l'erreur
    au LLM et on lui demande de corriger, jusqu'à `retries` fois.
    """
    conversation = list(messages)
    last_error = None
    for _ in range(retries + 1):
        try:
            raw = chat(conversation, temperature=temperature, json_mode=True)
        except JSONRefuseError as e:
            # Sortie refusée par l'API elle-même : on réessaie avec un rappel de consigne.
            last_error = e
            conversation = list(messages) + [
                {"role": "user", "content": "Réponds uniquement avec l'objet JSON demandé, complet et bien fermé."}
            ]
            continue
        try:
            return schema.model_validate_json(_extract_json(raw))
        except ValidationError as e:
            last_error = e
            # On montre au modèle sa réponse et ce qui ne va pas (3 erreurs max).
            erreurs = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:3]
            )
            conversation += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"Ta réponse n'est pas un JSON valide pour le format demandé ({erreurs}). "
                        "Renvoie UNIQUEMENT l'objet JSON corrigé, sans texte autour."
                    ),
                },
            ]
    raise LLMError(f"Le modèle n'a pas produit de JSON valide après {retries + 1} essais.") from last_error


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
def embed(texts: list[str]) -> list[list[float]]:
    """Transforme une liste de textes en vecteurs (un vecteur par texte)."""
    if not texts:
        return []

    if LLM_MODE == "local":
        vectors = []
        batch = 32  # par paquets pour ne pas envoyer une requête énorme
        for i in range(0, len(texts), batch):
            try:
                resp = _client().embeddings.create(model=OLLAMA_EMBED_MODEL, input=texts[i : i + batch])
            except APIConnectionError as e:
                raise LLMError("Impossible de joindre Ollama pour les embeddings.") from e
            except APIStatusError as e:
                raise LLMError(
                    f"Erreur Ollama ({e.status_code}). Modèle d'embeddings installé ? "
                    f"-> ollama pull {OLLAMA_EMBED_MODEL}"
                ) from e
            vectors.extend(d.embedding for d in resp.data)
        return vectors

    # fastembed renvoie des tableaux numpy : on les convertit en listes Python.
    return [v.tolist() for v in _fastembed_model().embed(texts, batch_size=16)]


def embedding_id() -> str:
    """
    Identifiant du modèle d'embeddings actif. Sert à séparer les bases ChromaDB :
    des vecteurs produits par deux modèles différents ne sont PAS comparables.
    """
    name = OLLAMA_EMBED_MODEL if LLM_MODE == "local" else FASTEMBED_MODEL
    return re.sub(r"[^a-zA-Z0-9]+", "-", f"{LLM_MODE}-{name}").strip("-").lower()


def describe() -> str:
    """Résumé lisible de la configuration (affiché dans l'app et dans les tests)."""
    if LLM_MODE == "local":
        return f"mode local — LLM Ollama '{OLLAMA_MODEL}', embeddings '{OLLAMA_EMBED_MODEL}'"
    return f"mode api — LLM Groq {GROQ_MODELS}, embeddings fastembed '{FASTEMBED_MODEL}'"


if __name__ == "__main__":
    print(describe())
    print(json.dumps({"embedding_id": embedding_id()}, ensure_ascii=False))
