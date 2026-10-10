"""
test_llm.py — Étape 1 : vérifie que le LLM et les embeddings répondent.

Lancement :
    python test_llm.py                 # utilise LLM_MODE du fichier .env
    LLM_MODE=api python test_llm.py    # force le mode api
    LLM_MODE=local python test_llm.py  # force le mode local
"""

import math
import time

from pydantic import BaseModel

import llm


def cosinus(a: list[float], b: list[float]) -> float:
    """Similarité cosinus : 1 = même direction (même sens), 0 = sans rapport."""
    produit = sum(x * y for x, y in zip(a, b))
    norme = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return produit / norme


class Ping(BaseModel):
    """Petit schéma pour tester la sortie JSON validée."""
    ok: bool
    definition: str


def main() -> None:
    print("Configuration :", llm.describe())
    print()

    # 1) Texte libre
    t0 = time.time()
    reponse = llm.chat([{"role": "user", "content": "Dis bonjour en une seule phrase courte, en français."}])
    print(f"[1/3] Chat OK ({time.time() - t0:.1f} s) -> {reponse.strip()}")

    # 2) JSON validé par pydantic
    t0 = time.time()
    ping = llm.chat_json(
        [
            {
                "role": "system",
                "content": 'Tu réponds uniquement en JSON avec ce format : {"ok": true, "definition": "..."}',
            },
            {"role": "user", "content": "Donne en une phrase la définition d'une porteuse en télécommunications."},
        ],
        schema=Ping,
    )
    print(f"[2/3] JSON OK ({time.time() - t0:.1f} s) -> ok={ping.ok}, definition={ping.definition!r}")

    # 3) Embeddings + similarité cosinus
    t0 = time.time()
    phrases = [
        "La modulation d'amplitude fait varier l'amplitude de la porteuse.",  # référence
        "En AM, c'est l'amplitude du signal haute fréquence qui change.",     # même idée, autres mots
        "La recette des crêpes demande de la farine, des œufs et du lait.",   # hors sujet
    ]
    vecteurs = llm.embed(phrases)
    print(f"[3/3] Embeddings OK ({time.time() - t0:.1f} s) -> {len(vecteurs)} vecteurs de dimension {len(vecteurs[0])}")
    print(f"      similarité(référence, même idée)  = {cosinus(vecteurs[0], vecteurs[1]):.3f}")
    print(f"      similarité(référence, hors sujet) = {cosinus(vecteurs[0], vecteurs[2]):.3f}")
    print()
    print("Tout fonctionne. La 1re similarité doit être nettement plus haute que la 2e.")


if __name__ == "__main__":
    try:
        main()
    except llm.LLMError as e:
        print(f"\nERREUR : {e}")
        raise SystemExit(1)
