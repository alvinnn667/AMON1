"""
db.py — Données de révision dans Postgres : questions de QCM (rangées par module), réponses,
boîtes de Leitner, carte des notions de chaque module, fiches enregistrées, retours des testeurs.

Les utilisateurs sont identifiés par ingest.user_key(pseudo) (une empreinte du pseudo),
jamais par le pseudo en clair. La connexion et le schéma sont gérés par storage.py.
"""

import re

from psycopg.types.json import Jsonb

import ingest
import storage

NB_BOITES = 3  # Leitner simple : boîte 1 = à revoir souvent … boîte 3 = maîtrisée


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
def ajouter_questions(user: str, questions: list[dict]) -> list[int]:
    """Enregistre des questions générées (chacune avec son module) et renvoie leurs identifiants."""
    ids = []
    with storage.connexion() as conn:
        for q in questions:
            row = conn.execute(
                """INSERT INTO questions
                   (user_id, module, notion, question, choix, bonne_reponse, explication, source, page, extrait)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    user, q["module"], q["notion"], q["question"], Jsonb(q["choix"]),
                    q["bonne_reponse"], q["explication"], q["source"], q["page"], q["extrait"],
                ),
            ).fetchone()
            ids.append(row["id"])
    return ids


def get_questions(user: str, ids: list[int]) -> list[dict]:
    """Questions de cet utilisateur, dans l'ordre des ids demandés."""
    if not ids:
        return []
    with storage.connexion() as conn:
        rows = conn.execute(
            "SELECT * FROM questions WHERE user_id = %s AND id = ANY(%s)", (user, list(ids))
        ).fetchall()
    par_id = {r["id"]: dict(r) for r in rows}
    return [par_id[i] for i in ids if i in par_id]


def notions(user: str, module: str | None = None) -> list[str]:
    """Noms de notions connus : carte des notions + notions déjà utilisées dans les QCM."""
    filtre, params = ("AND module = %s", [module]) if module else ("", [])
    with storage.connexion() as conn:
        rows = conn.execute(
            f"""SELECT nom FROM notions WHERE user_id = %s {filtre}
                UNION SELECT notion FROM questions WHERE user_id = %s {filtre}
                ORDER BY 1""",
            (user, *params, user, *params),
        ).fetchall()
    return [r["nom"] for r in rows]


# ---------------------------------------------------------------------------
# Carte des notions de chaque module
# ---------------------------------------------------------------------------
def carte_notions(user: str, module: str) -> list[dict]:
    with storage.connexion() as conn:
        rows = conn.execute(
            "SELECT nom, resume, sources FROM notions WHERE user_id = %s AND module = %s ORDER BY id",
            (user, module),
        ).fetchall()
    return [dict(r) for r in rows]


def ajouter_notions(user: str, module: str, notions_extraites: list[dict]) -> int:
    """
    Ajoute des notions {"nom", "resume", "source"} à la carte du module.
    Une notion déjà présente (même nom, sans tenir compte de la casse) n'est pas dupliquée :
    on lui ajoute seulement la nouvelle source. Renvoie le nombre de notions nouvelles.
    """
    nouvelles = 0
    with storage.connexion() as conn:
        existantes = {
            r["nom"].lower(): r
            for r in conn.execute(
                "SELECT id, nom, sources FROM notions WHERE user_id = %s AND module = %s", (user, module)
            ).fetchall()
        }
        for n in notions_extraites:
            ancienne = existantes.get(n["nom"].lower())
            if ancienne:
                if n["source"] not in ancienne["sources"]:
                    ancienne["sources"].append(n["source"])
                    conn.execute(
                        "UPDATE notions SET sources = %s WHERE id = %s", (Jsonb(ancienne["sources"]), ancienne["id"])
                    )
            else:
                row = conn.execute(
                    """INSERT INTO notions (user_id, module, nom, resume, sources) VALUES (%s, %s, %s, %s, %s)
                       RETURNING id, nom, sources""",
                    (user, module, n["nom"], n["resume"], Jsonb([n["source"]])),
                ).fetchone()
                existantes[n["nom"].lower()] = row
                nouvelles += 1
    return nouvelles


def renommer_module(user: str, ancien: str, nouveau: str) -> None:
    """Reporte le nouveau nom sur les questions, la conversation et la carte des notions (fusion si besoin)."""
    with storage.connexion() as conn:
        conn.execute("UPDATE questions SET module = %s WHERE user_id = %s AND module = %s", (nouveau, user, ancien))
        conn.execute("UPDATE messages_chat SET module = %s WHERE user_id = %s AND module = %s", (nouveau, user, ancien))
        # Les notions déjà présentes dans le module cible sont gardées, les autres sont déplacées.
        conn.execute(
            """UPDATE notions n SET module = %s
               WHERE n.user_id = %s AND n.module = %s
                 AND NOT EXISTS (SELECT 1 FROM notions c
                                 WHERE c.user_id = n.user_id AND c.module = %s AND c.nom = n.nom)""",
            (nouveau, user, ancien, nouveau),
        )
        conn.execute("DELETE FROM notions WHERE user_id = %s AND module = %s", (user, ancien))
        # Même logique pour les fiches : en cas de doublon (même notion, même fichier), on garde celle du module cible.
        conn.execute(
            """UPDATE fiches f SET module = %s
               WHERE f.user_id = %s AND f.module = %s
                 AND NOT EXISTS (SELECT 1 FROM fiches c
                                 WHERE c.user_id = f.user_id AND c.module = %s
                                   AND c.notion_cle = f.notion_cle AND c.source = f.source)""",
            (nouveau, user, ancien, nouveau),
        )
        conn.execute("DELETE FROM fiches WHERE user_id = %s AND module = %s", (user, ancien))


def supprimer_carte(user: str, module: str) -> None:
    with storage.connexion() as conn:
        conn.execute("DELETE FROM notions WHERE user_id = %s AND module = %s", (user, module))


# ---------------------------------------------------------------------------
# Fiches de révision enregistrées (une par module, notion et fichier choisi ; source "" = tout le module)
# ---------------------------------------------------------------------------
CLE_NOTION = storage.CLE_NOTION_SQL.format("%s")  # même normalisation que la colonne notion_cle


def _notion_propre(notion: str) -> str:
    return re.sub(r"\s+", " ", notion).strip()


def enregistrer_fiche(user: str, module: str, notion: str, source: str, contenu: dict) -> int:
    """Enregistre la fiche ; si elle existe déjà (même notion, même fichier), elle est remplacée. Renvoie son id."""
    with storage.connexion() as conn:
        row = conn.execute(
            """INSERT INTO fiches (user_id, module, notion, source, contenu) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (user_id, module, notion_cle, source)
               DO UPDATE SET notion = EXCLUDED.notion, contenu = EXCLUDED.contenu, maj_le = now()
               RETURNING id""",
            (user, module, _notion_propre(notion), source, Jsonb(contenu)),
        ).fetchone()
    return row["id"]


def fiches(user: str, module: str) -> list[dict]:
    """Les fiches du module, les plus récentes d'abord : [{"id", "notion", "source", "contenu", "cree_le", "maj_le"}]."""
    with storage.connexion() as conn:
        rows = conn.execute(
            """SELECT id, notion, source, contenu, cree_le, maj_le FROM fiches
               WHERE user_id = %s AND module = %s ORDER BY maj_le DESC, id DESC""",
            (user, module),
        ).fetchall()
    return [dict(r) for r in rows]


def trouver_fiche(user: str, module: str, notion: str, source: str) -> dict | None:
    """La fiche déjà enregistrée pour cette notion (casse et espaces ignorés) et ce fichier, ou None."""
    with storage.connexion() as conn:
        row = conn.execute(
            f"""SELECT id, notion, source, contenu, cree_le, maj_le FROM fiches
                WHERE user_id = %s AND module = %s AND notion_cle = {CLE_NOTION} AND source = %s""",
            (user, module, _notion_propre(notion), source),
        ).fetchone()
    return dict(row) if row else None


def supprimer_fiche(user: str, fiche_id: int) -> bool:
    """Supprime une fiche de cet utilisateur (jamais celle d'un autre). Renvoie True si elle existait."""
    with storage.connexion() as conn:
        return conn.execute("DELETE FROM fiches WHERE id = %s AND user_id = %s", (fiche_id, user)).rowcount > 0


def supprimer_fiches_fichier(user: str, fichier: str) -> int:
    """
    Fichier supprimé ou réimporté : retire les fiches qui s'appuient sur ses anciens passages,
    c'est-à-dire celles faites sur ce fichier, et celles faites sur tout le module dont un des
    extraits (contenu -> "extraits" -> [{"source": …}]) vient de ce fichier. Renvoie le nombre retiré.
    """
    with storage.connexion() as conn:
        return conn.execute(
            """DELETE FROM fiches WHERE user_id = %s
                 AND (source = %s OR contenu -> 'extraits' @> %s)""",
            (user, fichier, Jsonb([{"source": fichier}])),
        ).rowcount


def supprimer_fiches_module(user: str, module: str, hors_cours_seulement: bool = False) -> int:
    """
    Toutes les fiches du module (quand son dernier fichier est supprimé), ou seulement celles
    « pas dans le cours » (après l'import d'un fichier : la notion y est peut-être). Renvoie le nombre retiré.
    """
    filtre = " AND contenu ->> 'dans_le_cours' = 'false'" if hors_cours_seulement else ""
    with storage.connexion() as conn:
        return conn.execute(
            f"DELETE FROM fiches WHERE user_id = %s AND module = %s{filtre}", (user, module)
        ).rowcount


# ---------------------------------------------------------------------------
# Réponses + boîtes de Leitner
# ---------------------------------------------------------------------------
def enregistrer_reponse(user: str, question_id: int, choix: int) -> bool:
    """
    Enregistre la réponse et met à jour la boîte de Leitner :
      - bonne réponse    -> la question monte d'une boîte (max NB_BOITES)
      - mauvaise réponse -> elle retourne en boîte 1 (à revoir en priorité)
    Renvoie True si la réponse est correcte.
    """
    with storage.connexion() as conn:
        row = conn.execute(
            "SELECT bonne_reponse, boite FROM questions WHERE id = %s AND user_id = %s FOR UPDATE",
            (question_id, user),
        ).fetchone()
        if row is None:
            raise ValueError("Question introuvable pour cet utilisateur.")
        correct = choix == row["bonne_reponse"]
        nouvelle_boite = min(row["boite"] + 1, NB_BOITES) if correct else 1
        conn.execute(
            "INSERT INTO reponses (user_id, question_id, choix, correct) VALUES (%s, %s, %s, %s)",
            (user, question_id, choix, correct),
        )
        conn.execute("UPDATE questions SET boite = %s WHERE id = %s", (nouvelle_boite, question_id))
    return correct


# ---------------------------------------------------------------------------
# Maîtrise par notion + répétition
# ---------------------------------------------------------------------------
FENETRE = 10        # on juge la maîtrise sur les 10 dernières réponses de chaque notion
SEUIL_VERT = 0.8    # >= 80 % de réussite : maîtrisé
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
    filtre, params = ("AND module = %s", [module]) if module else ("", [])
    with storage.connexion() as conn:
        questions = conn.execute(
            f"""SELECT notion, count(*) AS nb, sum((boite < %s)::int) AS a_revoir
                FROM questions WHERE user_id = %s {filtre}
                  AND id NOT IN (SELECT question_id FROM signalements)
                GROUP BY notion""",
            (NB_BOITES, user, *params),
        ).fetchall()
        reponses = conn.execute(
            f"""SELECT q.notion, r.correct FROM reponses r JOIN questions q ON q.id = r.question_id
                WHERE r.user_id = %s {filtre.replace("module", "q.module")}
                  AND q.id NOT IN (SELECT question_id FROM signalements)
                ORDER BY r.repondu_le DESC, r.id DESC""",
            (user, *params),
        ).fetchall()
        carte = conn.execute(f"SELECT nom FROM notions WHERE user_id = %s {filtre}", (user, *params)).fetchall()

    recentes: dict[str, list[int]] = {}
    for r in reponses:  # du plus récent au plus ancien
        liste = recentes.setdefault(r["notion"], [])
        if len(liste) < FENETRE:
            liste.append(int(r["correct"]))

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
        filtre, params = filtre + " AND q.notion = %s", params + [notion]
    if module:
        filtre, params = filtre + " AND q.module = %s", params + [module]
    with storage.connexion() as conn:
        rows = conn.execute(
            f"""SELECT q.id
                FROM questions q LEFT JOIN reponses r ON r.question_id = q.id
                WHERE q.user_id = %s AND q.boite < %s {filtre}
                  AND q.id NOT IN (SELECT question_id FROM signalements)
                GROUP BY q.id
                ORDER BY q.boite ASC, (count(r.id) = 0) ASC, max(r.repondu_le) ASC NULLS LAST
                LIMIT %s""",
            (user, NB_BOITES, *params, nb),
        ).fetchall()
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# Conversations du chat (une par module ; module "" = tous les modules)
# ---------------------------------------------------------------------------
MAX_MESSAGES_AFFICHES = 60


def messages_chat(user: str, module: str, limite: int = MAX_MESSAGES_AFFICHES) -> list[dict]:
    """Les derniers messages de la conversation, du plus ancien au plus récent."""
    with storage.connexion() as conn:
        rows = conn.execute(
            """SELECT role, contenu, details FROM (
                   SELECT id, role, contenu, details FROM messages_chat
                   WHERE user_id = %s AND module = %s ORDER BY id DESC LIMIT %s
               ) derniers ORDER BY id""",
            (user, module, limite),
        ).fetchall()
    return [dict(r) for r in rows]


def ajouter_echange(user: str, module: str, question: str, reponse: str, details: dict) -> None:
    """Enregistre la question et la réponse ensemble (une seule transaction)."""
    with storage.connexion() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO messages_chat (user_id, module, role, contenu, details) VALUES (%s, %s, %s, %s, %s)",
                [(user, module, "user", question, Jsonb({})), (user, module, "assistant", reponse, Jsonb(details))],
            )


def effacer_conversation(user: str, module: str) -> None:
    with storage.connexion() as conn:
        conn.execute("DELETE FROM messages_chat WHERE user_id = %s AND module = %s", (user, module))


# ---------------------------------------------------------------------------
# Retours des testeurs
# ---------------------------------------------------------------------------
MOTIFS_SIGNALEMENT = [
    "La bonne réponse est fausse",
    "Question ambiguë ou mal formulée",
    "Pas dans le cours / inventée",
    "Source ou page incorrecte",
    "Autre",
]


def signaler_question(user: str, question_id: int, motif: str, commentaire: str = "") -> None:
    with storage.connexion() as conn:
        conn.execute(
            "INSERT INTO signalements (user_id, question_id, motif, commentaire) VALUES (%s, %s, %s, %s)",
            (user, question_id, motif, commentaire.strip()[:1000]),
        )


def questions_signalees(user: str) -> set[int]:
    with storage.connexion() as conn:
        rows = conn.execute("SELECT DISTINCT question_id FROM signalements WHERE user_id = %s", (user,)).fetchall()
    return {r["question_id"] for r in rows}


def ajouter_feedback(user: str, categorie: str, note: int | None, message: str, mode: str) -> None:
    with storage.connexion() as conn:
        conn.execute(
            "INSERT INTO feedback (user_id, categorie, note, message, mode) VALUES (%s, %s, %s, %s, %s)",
            (user, categorie, note, message.strip()[:2000], mode),
        )


def export_signalements() -> list[dict]:
    """Tous les signalements (tous testeurs), avec la question concernée. Pour l'admin."""
    with storage.connexion() as conn:
        rows = conn.execute(
            """SELECT to_char(s.cree_le, 'YYYY-MM-DD HH24:MI') AS date, s.motif, s.commentaire, q.module,
                      q.notion, q.question, q.choix, q.bonne_reponse, q.explication, q.source, q.page,
                      substr(s.user_id, 6, 8) AS testeur
               FROM signalements s JOIN questions q ON q.id = s.question_id
               ORDER BY s.cree_le DESC"""
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        choix = d.pop("choix")
        d["bonne_reponse"] = choix[d["bonne_reponse"]]
        d["autres_choix"] = " | ".join(c for c in choix if c != d["bonne_reponse"])
        out.append(d)
    return out


def export_feedback() -> list[dict]:
    with storage.connexion() as conn:
        rows = conn.execute(
            """SELECT to_char(cree_le, 'YYYY-MM-DD HH24:MI') AS date, categorie, note, message, mode,
                      substr(user_id, 6, 8) AS testeur
               FROM feedback ORDER BY cree_le DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def statistiques() -> dict:
    """Quelques chiffres globaux pour l'admin."""
    with storage.connexion() as conn:
        def un(sql: str):
            return next(iter(conn.execute(sql).fetchone().values()))

        return {
            "testeurs": un("SELECT count(DISTINCT user_id) FROM questions"),
            "questions": un("SELECT count(*) FROM questions"),
            "reponses": un("SELECT count(*) FROM reponses"),
            "reussite": un("SELECT round(100.0 * avg(correct::int)) FROM reponses"),
            "signalements": un("SELECT count(*) FROM signalements"),
            "feedback": un("SELECT count(*) FROM feedback"),
        }


# ---------------------------------------------------------------------------
# Comptes (connexion Google)
# ---------------------------------------------------------------------------
def enregistrer_compte(compte_id: str, email: str, nom: str) -> None:
    """Crée le compte à la première connexion, met à jour e-mail, nom et date de visite ensuite."""
    with storage.connexion() as conn:
        conn.execute(
            """INSERT INTO comptes (id, email, nom) VALUES (%s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, nom = EXCLUDED.nom, derniere_visite = now()""",
            (compte_id, email, nom),
        )


class RecuperationError(ValueError):
    """Message lisible : pseudo inconnu ou déjà récupéré."""


def recuperer_pseudo(compte_id: str, pseudo: str) -> dict:
    """
    Rattache à un compte les cours et la progression d'un ancien pseudo (avant les comptes).
    Une seule fois par pseudo : ensuite, ces données appartiennent au compte.
    Renvoie le nombre d'éléments déplacés par type.
    """
    ancien = ingest.user_key(pseudo)
    if not ancien.startswith("user-"):
        raise RecuperationError("Indique le pseudo que tu utilisais avant, pas un identifiant de compte.")
    with storage.connexion() as conn:  # une seule transaction : tout est déplacé, ou rien
        if conn.execute("SELECT 1 FROM pseudos_recuperes WHERE pseudo_key = %s", (ancien,)).fetchone():
            raise RecuperationError("Les données de ce pseudo ont déjà été récupérées par un compte.")

        bilan = {}
        # Passages de cours : si le compte a déjà importé le même fichier, on garde sa version.
        conn.execute(
            """DELETE FROM chunks o WHERE o.user_id = %s AND EXISTS (
                   SELECT 1 FROM chunks n WHERE n.user_id = %s AND n.embedding_id = o.embedding_id AND n.id = o.id)""",
            (ancien, compte_id),
        )
        # Ses fiches qui dépendent d'un fichier que le compte possède déjà partent aussi (même règle que
        # supprimer_fiches_fichier) : elles citeraient l'ancienne version. Non comptées, comme ces passages.
        conn.execute(
            """DELETE FROM fiches f WHERE f.user_id = %s AND EXISTS (
                   SELECT 1 FROM chunks c WHERE c.user_id = %s AND (f.source = c.source
                     OR f.contenu -> 'extraits' @> jsonb_build_array(jsonb_build_object('source', c.source))))""",
            (ancien, compte_id),
        )
        bilan["passages"] = conn.execute(
            "UPDATE chunks SET user_id = %s WHERE user_id = %s", (compte_id, ancien)
        ).rowcount
        for table in ("questions", "reponses", "signalements", "feedback", "messages_chat"):
            bilan[table] = conn.execute(
                f"UPDATE {table} SET user_id = %s WHERE user_id = %s", (compte_id, ancien)
            ).rowcount
        # Carte des notions : une notion déjà présente dans le compte n'est pas dupliquée.
        bilan["notions"] = conn.execute(
            """UPDATE notions o SET user_id = %s WHERE o.user_id = %s AND NOT EXISTS (
                   SELECT 1 FROM notions n WHERE n.user_id = %s AND n.module = o.module AND n.nom = o.nom)""",
            (compte_id, ancien, compte_id),
        ).rowcount
        conn.execute("DELETE FROM notions WHERE user_id = %s", (ancien,))
        # Fiches : si le compte a déjà une fiche sur la même notion et le même fichier, on garde la sienne.
        bilan["fiches"] = conn.execute(
            """UPDATE fiches o SET user_id = %s WHERE o.user_id = %s AND NOT EXISTS (
                   SELECT 1 FROM fiches n WHERE n.user_id = %s AND n.module = o.module
                     AND n.notion_cle = o.notion_cle AND n.source = o.source)""",
            (compte_id, ancien, compte_id),
        ).rowcount
        conn.execute("DELETE FROM fiches WHERE user_id = %s", (ancien,))

        if not any(bilan.values()):
            raise RecuperationError("Aucune donnée trouvée pour ce pseudo.")  # annule la transaction
        conn.execute("INSERT INTO pseudos_recuperes (pseudo_key, compte_id) VALUES (%s, %s)", (ancien, compte_id))
    return bilan


def nb_comptes() -> int:
    with storage.connexion() as conn:
        return conn.execute("SELECT count(*) AS n FROM comptes").fetchone()["n"]
