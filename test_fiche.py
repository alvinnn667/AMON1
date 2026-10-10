"""
test_fiche.py — Étape 5 : génère une fiche de révision depuis les cours du pseudo "test"
et l'affiche dans le terminal.

Lancement :
    python test_fiche.py "modulation FM"
    python test_fiche.py "recette des crêpes"     # doit répondre que ce n'est pas dans le cours
"""

import sys
import time

import generation
import llm

notion = " ".join(sys.argv[1:]) or "modulation d'amplitude"
print("Configuration :", llm.describe())
t0 = time.time()
try:
    fiche = generation.generer_fiche("test", notion)
except (llm.LLMError, generation.GenerationError) as e:
    raise SystemExit(f"ERREUR : {e}")

print(f"Fiche générée en {time.time() - t0:.1f} s ({fiche['retires']} élément(s) retiré(s) faute de source)\n")
print(generation.fiche_markdown(fiche))
