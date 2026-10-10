"""
test_ingest.py — Étape 2 : indexe les PDF de cours_exemple/ puis affiche les
3 chunks les plus proches d'une question.

Lancement :
    python test_ingest.py                                  # question par défaut
    python test_ingest.py "Qu'est-ce que la bande de Carson ?"
    python test_ingest.py --reindex "ma question"          # force la ré-indexation
"""

import sys
import time
from pathlib import Path

import ingest
import llm

PSEUDO = "test"
DOSSIER = Path("cours_exemple")
QUESTION_PAR_DEFAUT = "À quoi sert un détecteur d'enveloppe ?"


def main() -> None:
    args = sys.argv[1:]
    reindex = "--reindex" in args
    args = [a for a in args if a != "--reindex"]
    question = " ".join(args) or QUESTION_PAR_DEFAUT

    print("Configuration :", llm.describe())

    # 1) Indexation (seulement si nécessaire, c'est le plus long)
    deja = ingest.list_sources(PSEUDO)
    pdfs = sorted(DOSSIER.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"Aucun PDF dans {DOSSIER}/")
    for pdf in pdfs:
        if pdf.name in deja and not reindex:
            print(f"  déjà indexé : {pdf.name} ({deja[pdf.name]} chunks)")
            continue
        t0 = time.time()
        stats = ingest.ingest_pdf(PSEUDO, pdf.name, pdf.read_bytes())
        print(
            f"  indexé : {stats['fichier']} — {stats['pages']} pages "
            f"({stats['pages_avec_texte']} avec du texte), {stats['chunks']} chunks, "
            f"{time.time() - t0:.1f} s"
        )

    # 2) Recherche par similarité
    print(f"\nQuestion : {question}\n")
    for rang, r in enumerate(ingest.search(PSEUDO, question, k=3), start=1):
        extrait = r["texte"].replace("\n", " ")[:350]
        print(f"#{rang}  score={r['score']:.3f}  [{r['source']}, p. {r['page']}]")
        print(f"    {extrait}…\n")


if __name__ == "__main__":
    try:
        main()
    except (llm.LLMError, ingest.IngestError) as e:
        print(f"\nERREUR : {e}")
        raise SystemExit(1)
