"""
migrer_vers_supabase.py — Recopie les données de l'ancien stockage local (dossier data/ :
SQLite + ChromaDB) vers la base Postgres définie par DATABASE_URL.

À lancer une seule fois, sur ton Mac :
    uv pip install chromadb==1.5.9      # l'ancienne base vectorielle, seulement pour cette migration
    python migrer_vers_supabase.py

Le script refuse de tourner si la base contient déjà des questions (pour éviter les doublons).
Ajoute --force pour passer outre.
"""

import json
import sqlite3
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

import storage

DATA = Path("data")


def migrer_chunks(conn) -> int:
    """Copie chaque collection ChromaDB (une par utilisateur et par modèle d'embeddings)."""
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError:
        print("  chromadb n'est pas installé : passe `uv pip install chromadb==1.5.9`, puis relance.")
        sys.exit(1)

    total = 0
    for dossier in sorted((DATA / "chroma").glob("*")):
        if not dossier.is_dir():
            continue
        embedding_id = dossier.name  # ex : local-bge-m3, api-sentence-transformers-...
        client = chromadb.PersistentClient(path=str(dossier), settings=Settings(anonymized_telemetry=False))
        for col in client.list_collections():
            res = client.get_collection(col.name).get(include=["documents", "metadatas", "embeddings"])
            lignes = [
                (
                    col.name, embedding_id, id_, meta.get("module", "Général"), meta["source"], meta["page"], doc,
                    "[" + ",".join(repr(float(x)) for x in vec) + "]",
                )
                for id_, doc, meta, vec in zip(res["ids"], res["documents"], res["metadatas"], res["embeddings"])
            ]
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO chunks (user_id, embedding_id, id, module, source, page, texte, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector) ON CONFLICT DO NOTHING""",
                    lignes,
                )
            total += len(lignes)
            print(f"  {embedding_id} / {col.name} : {len(lignes)} passages")
    return total


def migrer_sqlite(conn) -> dict:
    """Copie questions, réponses, signalements, notions et avis. Les identifiants de questions changent."""
    chemin = DATA / "coach.sqlite3"
    if not chemin.exists():
        print("  pas de data/coach.sqlite3 : rien à copier côté progression.")
        return {}
    old = sqlite3.connect(chemin)
    old.row_factory = sqlite3.Row
    colonnes = {r["name"] for r in old.execute("PRAGMA table_info(questions)")}
    nouvel_id: dict[int, int] = {}  # ancien id de question -> nouvel id

    for q in old.execute("SELECT * FROM questions ORDER BY id"):
        row = conn.execute(
            """INSERT INTO questions (user_id, module, notion, question, choix, bonne_reponse, explication,
                                      source, page, extrait, boite, cree_le)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                q["user"], q["module"] if "module" in colonnes else "Général", q["notion"], q["question"],
                Jsonb(json.loads(q["choix"])), q["bonne_reponse"], q["explication"], q["source"], q["page"],
                q["extrait"], q["boite"], q["cree_le"],
            ),
        ).fetchone()
        nouvel_id[q["id"]] = row["id"]

    nb_reponses = 0
    for r in old.execute("SELECT * FROM reponses ORDER BY id"):
        if r["question_id"] in nouvel_id:
            conn.execute(
                "INSERT INTO reponses (user_id, question_id, choix, correct, repondu_le) VALUES (%s, %s, %s, %s, %s)",
                (r["user"], nouvel_id[r["question_id"]], r["choix"], bool(r["correct"]), r["repondu_le"]),
            )
            nb_reponses += 1

    tables = {t["name"] for t in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    nb_signal = nb_notions = nb_avis = 0
    if "signalements" in tables:
        for s in old.execute("SELECT * FROM signalements"):
            if s["question_id"] in nouvel_id:
                conn.execute(
                    """INSERT INTO signalements (user_id, question_id, motif, commentaire, cree_le)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (s["user"], nouvel_id[s["question_id"]], s["motif"], s["commentaire"], s["cree_le"]),
                )
                nb_signal += 1
    if "notions" in tables:
        for n in old.execute("SELECT * FROM notions"):
            conn.execute(
                """INSERT INTO notions (user_id, module, nom, resume, sources, cree_le)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (n["user"], n["module"], n["nom"], n["resume"], Jsonb(json.loads(n["sources"])), n["cree_le"]),
            )
            nb_notions += 1
    if "feedback" in tables:
        for f in old.execute("SELECT * FROM feedback"):
            conn.execute(
                "INSERT INTO feedback (user_id, categorie, note, message, mode, cree_le) VALUES (%s, %s, %s, %s, %s, %s)",
                (f["user"], f["categorie"], f["note"], f["message"], f["mode"], f["cree_le"]),
            )
            nb_avis += 1
    return {"questions": len(nouvel_id), "réponses": nb_reponses, "signalements": nb_signal,
            "notions": nb_notions, "avis": nb_avis}


def main() -> None:
    if not DATA.exists():
        raise SystemExit("Pas de dossier data/ ici : lance le script depuis le dossier du projet.")
    with storage.connexion() as conn:  # une seule transaction : tout est copié, ou rien
        deja = conn.execute("SELECT count(*) AS n FROM questions").fetchone()["n"]
        if deja and "--force" not in sys.argv:
            raise SystemExit(f"La base contient déjà {deja} questions. Relance avec --force si tu veux vraiment ajouter.")
        print("Passages de cours (ChromaDB) :")
        nb_chunks = migrer_chunks(conn)
        print("Progression (SQLite) :")
        bilan = migrer_sqlite(conn)
    print(f"\nTerminé : {nb_chunks} passages, " + ", ".join(f"{v} {k}" for k, v in bilan.items()) + ".")


if __name__ == "__main__":
    try:
        main()
    except storage.StorageError as e:
        raise SystemExit(f"ERREUR : {e}")
