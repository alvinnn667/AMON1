"""
test_qcm.py — Étape 4 : génère un QCM depuis les cours du pseudo "test" et l'affiche
dans le terminal (sans l'enregistrer en base).

Lancement :
    python test_qcm.py                      # extraits tirés au hasard
    python test_qcm.py "bande de Carson"    # centré sur une notion
"""

import sys
import time

import generation
import llm

notion = " ".join(sys.argv[1:])
print("Configuration :", llm.describe())
t0 = time.time()
try:
    questions = generation.generer_qcm("test", source=None, notion=notion, nb=3, enregistrer=False)
except (llm.LLMError, generation.GenerationError) as e:
    raise SystemExit(f"ERREUR : {e}")

print(f"{len(questions)} questions générées en {time.time() - t0:.1f} s\n")
for i, q in enumerate(questions, start=1):
    print(f"{i}. [{q['notion']}] {q['question']}")
    for j, c in enumerate(q["choix"]):
        print(f"   {'✔' if j == q['bonne_reponse'] else ' '} {'ABCD'[j]}) {c}")
    print(f"   Explication : {q['explication']}")
    print(f"   Source : {q['source']}, p. {q['page']}\n")
