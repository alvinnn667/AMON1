"""
db.py — Stockage SQLite : questions de QCM (rangées par module), réponses de l'étudiant,
boîtes de Leitner, carte des notions de chaque module, retours des testeurs.

Les utilisateurs sont identifiés par ingest.user_key(pseudo) (une empreinte du pseudo),
jamais par le pseudo en clair.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import ingest

DB_PATH = ingest.DATA_DIR / "coach.sqlite3"
NB_BOITES = 3  # Leitner simple : boîte 1 = à revoir souvent … boîte 3 = maîtrisée

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user          TEXT NOT NULL,
    notion        TEXT NOT NULL,
    question      TEXT NOT NULL,
    choix         TEXT NOT NULL,          -- liste JSON de 4 propositions
    bonne_reponse INTEGER NOT NULL,       -- indice 0 à 3
    explication   TEXT NOT NULL,
    source        TEXT NOT NULL,          -- nom du PDF
    page          INTEGER NOT NULL,
    extrait       TEXT NOT NULL,          -- passage du cours qui justifie la réponse
    boite         INTEGER NOT NULL DEFAULT 1,
    cree_le       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reponses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT NOT NULL,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    choix       INTEGER NOT NULL,
    correct     INTEGER NOT NULL,         -- 1 = bonne réponse, 0 = mauvaise
    repondu_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signalements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT NOT NULL,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    motif       TEXT NOT NULL,
    commentaire TEXT NOT NULL DEFAULT '',
    cree_le     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user      TEXT NOT NULL,
    categorie TEXT NOT NULL,
    note      INTEGER,                    -- 1 à 5, optionnelle
    message   TEXT NOT NULL,
    mode      TEXT NOT NULL,              -- local / api
    cree_le   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user    TEXT NOT NULL,
    module  TEXT NOT NULL,
    nom     TEXT NOT NULL,
    resume  TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',   -- liste JSON : ["TP4.pdf p. 2, 3", ...]
    cree_le TEXT NOT NULL,
    UNIQUE (user, module, nom)
);
CREATE INDEX IF NOT EXISTS idx_questions_user ON questions(user);
CREATE INDEX IF NOT EXISTS idx_reponses_user ON reponses(user);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # accès aux colonnes par leur nom
    conn.executescript(SCHEMA)      # crée les tables si besoin (rapide si elles existent)
    _migrer(conn)
    return conn


def _migrer(conn: sqlite3.Connection) -> None:
    """Ajoute la colonne module aux bases créées avant l'arrivée des modules."""
    colonnes = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    if "module" not in colonnes:
        conn.execute(
            f"ALTER TABLE questions ADD COLUMN module TEXT NOT NULL DEFAULT '{ingest.MODULE_PAR_DEFAUT}'"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_module ON questions(user, module)")
        conn.commit()


def _question_dict(row: sqlite3.Row) -> dict:
    q = dict(row)
    q["choix"] = json.loads(q["choix"])
    return q


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
def ajouter_questions(user: str, questions: list[dict]) -> list[int]:
    """Enregistre des questions générées (chacune avec son module) et renvoie leurs identifiants."""
    ids = []
    with closing(_connect()) as conn, conn:
        for q in questions:
            cur = conn.execute(
                """INSERT INTO questions
                   (user, module, notion, question, choix, bonne_reponse, explication, source, page, extrait, cree_le)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user, q["module"], q["notion"], q["question"], json.dumps(q["choix"], ensure_ascii=False),
                    q["bonne_reponse"], q["explication"], q["source"], q["page"], q["extrait"], _now(),
                ),
            )
            ids.append(cur.lastrowid)
    return ids


def get_questions(user: str, ids: list[int]) -> list[dict]:
    """Questions de cet utilisateur, dans l'ordre des ids demandés."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM questions WHERE user = ? AND id IN ({marks})", (user, *ids)
        ).fetchall()
    par_id = {r["id"]: _question_dict(r) for r in rows}
    return [par_id[i] for i in ids if i in par_id]


def notions(user: str, module: str | None = None) -> list[str]:
    """Noms de notions connus : carte des notions + notions déjà utilisées dans les QCM."""
    filtre, params = ("AND module = ?", [module]) if module else ("", [])
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"""SELECT nom FROM notions WHERE user = ? {filtre}
                UNION SELECT notion FROM questions WHERE user = ? {filtre}
                ORDER BY 1""",
            (user, *params, user, *params),
        ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Carte des notions de chaque module
# ---------------------------------------------------------------------------
def carte_notions(user: str, module: str) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT nom, resume, sources FROM notions WHERE user = ? AND module = ? ORDER BY id",
            (user, module),
        ).fetchall()
    return [{"nom": r["nom"], "resume": r["resume"], "sources": json.loads(r["sources"])} for r in rows]


def ajouter_notions(user: str, module: str, notions_extraites: list[dict]) -> int:
    """
    Ajoute des notions {"nom", "resume", "source"} à la carte du module.
    Une notion déjà présente (même nom, sans tenir compte de la casse) n'est pas dupliquée :
    on lui ajoute seulement la nouvelle source. Renvoie le nombre de notions nouvelles.
    """
    nouvelles = 0
    with closing(_connect()) as conn, conn:
        existantes = {
            r["nom"].lower(): r for r in conn.execute(
                "SELECT id, nom, sources FROM notions WHERE user = ? AND module = ?", (user, module)
            )
        }
        for n in notions_extraites:
            ancienne = existantes.get(n["nom"].lower())
            if ancienne:
                sources = json.loads(ancienne["sources"])
                if n["source"] not in sources:
                    conn.execute(
                        "UPDATE notions SET sources = ? WHERE id = ?",
                        (json.dumps(sources + [n["source"]], ensure_ascii=False), ancienne["id"]),
                    )
            else:
                conn.execute(
                    "INSERT INTO notions (user, module, nom, resume, sources, cree_le) VALUES (?, ?, ?, ?, ?, ?)",
                    (user, module, n["nom"], n["resume"], json.dumps([n["source"]], ensure_ascii=False), _now()),
                )
                nouvelles += 1
    return nouvelles


def renommer_module(user: str, ancien: str, nouveau: str) -> None:
    """Reporte le nouveau nom sur les questions et la carte des notions (fusion si besoin)."""
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE questions SET module = ? WHERE user = ? AND module = ?", (nouveau, user, ancien))
        # Les notions déjà présentes dans le module cible sont gardées, les autres sont déplacées.
        conn.execute("UPDATE OR IGNORE notions SET module = ? WHERE user = ? AND module = ?", (nouveau, user, ancien))
        conn.execute("DELETE FROM notions WHERE user = ? AND module = ?", (user, ancien))


def supprimer_carte(user: str, module: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM notions WHERE user = ? AND module = ?", (user, module))


# ---------------------------------------------------------------------------
# Réponses + boîtes de Leitner
# ---------------------------------------------------------------------------
def enregistrer_reponse(user: str, question_id: int, choix: int) -> bool:
    """
    Enregistre la réponse et met à jour la boîte de Leitner :
      - bonne réponse   -> la question monte d'une boîte (max NB_BOITES)
      - mauvaise réponse -> elle retourne en boîte 1 (à revoir en priorité)
    Renvoie True si la réponse est correcte.
    """
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT bonne_reponse, boite FROM questions WHERE id = ? AND user = ?", (question_id, user)
        ).fetchone()
        if row is None:
            raise ValueError("Question introuvable pour cet utilisateur.")
        correct = choix == row["bonne_reponse"]
        nouvelle_boite = min(row["boite"] + 1, NB_BOITES) if correct else 1
        conn.execute(
            "INSERT INTO reponses (user, question_id, choix, correct, repondu_le) VALUES (?, ?, ?, ?, ?)",
            (user, question_id, choix, int(correct), _now()),
        )
        conn.execute("UPDATE questions SET boite = ? WHERE id = ?", (nouvelle_boite, question_id))
    return correct


# ---------------------------------------------------------------------------
# Maîtrise par notion + répétition (étape 6)
# ---------------------------------------------------------------------------
FENETRE = 10      # on juge la maîtrise sur les 10 dernières réponses de chaque notion
SEUIL_VERT = 0.8  # >= 80 % de réussite : maîtrisé
SEUIL_ORANGE = 0.5  # 50 à 79 % : fragile ; < 50 % : à retravailler


def niveau(taux: float | None) -> str:
    """Code du niveau : l'interface décide de son affichage (libellé, couleur, icône)."""
    if taux is None:
        return "non_teste"
    if taux >= SEUIL_VERT:
        return "maitrise"
    if taux >= SEUIL_ORANGE:
        return "fragile"
    return "a_retravailler"


def maitrise_par_notion(user: str, module: str | None = None) -> list[dict]:
    """
    Une ligne par notion (d'un module ou de tous) : nombre de questions, de réponses, taux de
    réussite récent, questions à consolider (boîtes 1 et 2) et code de niveau (voir niveau()).
    Les notions de la carte jamais travaillées en QCM apparaissent avec le niveau "jamais".
    Trié du plus faible au plus fort, pour voir d'abord ce qu'il faut travailler.
    """
    filtre, params = ("AND module = ?", [module]) if module else ("", [])
    with closing(_connect()) as conn:
        questions = conn.execute(
            f"""SELECT notion, COUNT(*) AS nb, SUM(boite < ?) AS a_revoir
                FROM questions WHERE user = ? {filtre}
                  AND id NOT IN (SELECT question_id FROM signalements)
                GROUP BY notion""",
            (NB_BOITES, user, *params),
        ).fetchall()
        reponses = conn.execute(
            f"""SELECT q.notion, r.correct FROM reponses r JOIN questions q ON q.id = r.question_id
                WHERE r.user = ? {filtre.replace("module", "q.module")}
                  AND q.id NOT IN (SELECT question_id FROM signalements)
                ORDER BY r.repondu_le DESC, r.id DESC""",
            (user, *params),
        ).fetchall()
        carte = conn.execute(f"SELECT nom FROM notions WHERE user = ? {filtre}", (user, *params)).fetchall()

    recentes: dict[str, list[int]] = {}
    for r in reponses:  # du plus récent au plus ancien
        liste = recentes.setdefault(r["notion"], [])
        if len(liste) < FENETRE:
            liste.append(r["correct"])

    lignes = []
    for q in questions:
        rep = recentes.get(q["notion"], [])
        taux = sum(rep) / len(rep) if rep else None
        lignes.append(
            {
                "notion": q["notion"],
                "niveau": niveau(taux),
                "taux": taux,
                "reponses_recentes": len(rep),
                "questions": q["nb"],
                "a_revoir": q["a_revoir"],
            }
        )
    deja = {l["notion"].lower() for l in lignes}
    for c in carte:
        if c["nom"].lower() not in deja:
            deja.add(c["nom"].lower())
            lignes.append(
                {"notion": c["nom"], "niveau": "jamais", "taux": None, "reponses_recentes": 0, "questions": 0, "a_revoir": 0}
            )
    ordre = {"a_retravailler": 0, "fragile": 1, "non_teste": 2, "jamais": 3, "maitrise": 4}
    return sorted(lignes, key=lambda l: (ordre[l["niveau"]], l["taux"] if l["taux"] is not None else 0))


def questions_a_revoir(user: str, nb: int, notion: str | None = None, module: str | None = None) -> list[int]:
    """
    Boîtes de Leitner : on repose d'abord la boîte 1 (ratées), puis la boîte 2.
    Dans une même boîte : d'abord les questions déjà ratées, puis jamais répondues,
    et parmi elles la plus anciennement vue en premier. La boîte 3 (maîtrisée) est laissée de côté.
    """
    filtre, params = "", []
    if notion:
        filtre, params = filtre + " AND q.notion = ?", params + [notion]
    if module:
        filtre, params = filtre + " AND q.module = ?", params + [module]
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"""SELECT q.id, q.boite, COUNT(r.id) AS nb_rep, MAX(r.repondu_le) AS derniere
                FROM questions q LEFT JOIN reponses r ON r.question_id = q.id
                WHERE q.user = ? AND q.boite < ? {filtre}
                  AND q.id NOT IN (SELECT question_id FROM signalements)
                GROUP BY q.id
                ORDER BY q.boite ASC, (COUNT(r.id) = 0) ASC, derniere ASC
                LIMIT ?""",
            (user, NB_BOITES, *params, nb),
        ).fetchall()
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# Retours des testeurs (étape 7)
# ---------------------------------------------------------------------------
MOTIFS_SIGNALEMENT = [
    "La bonne réponse est fausse",
    "Question ambiguë ou mal formulée",
    "Pas dans le cours / inventée",
    "Source ou page incorrecte",
    "Autre",
]


def signaler_question(user: str, question_id: int, motif: str, commentaire: str = "") -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO signalements (user, question_id, motif, commentaire, cree_le) VALUES (?, ?, ?, ?, ?)",
            (user, question_id, motif, commentaire.strip()[:1000], _now()),
        )


def questions_signalees(user: str) -> set[int]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT DISTINCT question_id FROM signalements WHERE user = ?", (user,)).fetchall()
    return {r["question_id"] for r in rows}


def ajouter_feedback(user: str, categorie: str, note: int | None, message: str, mode: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO feedback (user, categorie, note, message, mode, cree_le) VALUES (?, ?, ?, ?, ?, ?)",
            (user, categorie, note, message.strip()[:2000], mode, _now()),
        )


def export_signalements() -> list[dict]:
    """Tous les signalements (tous testeurs), avec la question concernée. Pour l'admin."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT s.cree_le AS date, s.motif, s.commentaire, q.module, q.notion, q.question, q.choix,
                      q.bonne_reponse, q.explication, q.source, q.page, substr(s.user, 6, 8) AS testeur
               FROM signalements s JOIN questions q ON q.id = s.question_id
               ORDER BY s.cree_le DESC"""
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        choix = json.loads(d.pop("choix"))
        d["bonne_reponse"] = choix[d["bonne_reponse"]]
        d["autres_choix"] = " | ".join(c for c in choix if c != d["bonne_reponse"])
        out.append(d)
    return out


def export_feedback() -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT cree_le AS date, categorie, note, message, mode, substr(user, 6, 8) AS testeur
               FROM feedback ORDER BY cree_le DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def statistiques() -> dict:
    """Quelques chiffres globaux pour l'admin."""
    with closing(_connect()) as conn:
        un = lambda sql: conn.execute(sql).fetchone()[0]
        return {
            "testeurs": un("SELECT COUNT(DISTINCT user) FROM questions"),
            "questions": un("SELECT COUNT(*) FROM questions"),
            "reponses": un("SELECT COUNT(*) FROM reponses"),
            "reussite": un("SELECT ROUND(100.0 * AVG(correct)) FROM reponses"),
            "signalements": un("SELECT COUNT(*) FROM signalements"),
            "feedback": un("SELECT COUNT(*) FROM feedback"),
        }
