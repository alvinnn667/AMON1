"""
storage.py — Connexion à la base Postgres hébergée (Supabase) : pool de connexions + schéma.

Toutes les données de l'app vivent ici, de façon durable :
  - chunks       : passages des cours + leurs vecteurs (pgvector) pour la recherche
  - questions    : QCM générés, avec leur boîte de Leitner
  - reponses     : réponses des étudiants
  - notions      : carte des notions de chaque module
  - messages_chat : conversations du chat (une par module), pour la mémoire et l'historique
  - fiches       : fiches de révision enregistrées (une par notion et par fichier choisi)
  - signalements, feedback : retours des testeurs

Les tables sont dans un schéma privé « coach » : l'API REST publique de Supabase n'expose
que le schéma « public », donc ces données ne sont accessibles qu'avec DATABASE_URL.

Configuration : DATABASE_URL (fichier .env en local, secrets de l'hébergeur en ligne).
Sur Supabase, utilise la chaîne « Session pooler » (compatible IPv4, port 5432).
"""

import atexit
import os
from contextlib import contextmanager
from functools import lru_cache

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import llm  # noqa: F401  (charge le .env et les secrets de l'hébergeur avant de lire DATABASE_URL)

SCHEMA_APP = "coach"


class StorageError(RuntimeError):
    """Erreur lisible, affichable telle quelle dans l'interface."""


# Clé de comparaison d'un nom de notion : sans tenir compte de la casse ni des espaces en trop
# (« Modulation  FM » = « modulation fm »). Utilisée par la table fiches et par db.py.
CLE_NOTION_SQL = "btrim(lower(regexp_replace({}, '[[:space:]]+', ' ', 'g')))"


SCHEMA_SQL = f"""
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;  -- sans effet si pgvector est déjà installé
CREATE SCHEMA IF NOT EXISTS {SCHEMA_APP};
SET search_path TO {SCHEMA_APP}, extensions, public;

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.chunks (
    user_id      TEXT NOT NULL,
    embedding_id TEXT NOT NULL,           -- modèle d'embeddings : des vecteurs de modèles différents ne se comparent pas
    id           TEXT NOT NULL,           -- "fichier|p3|c0"
    module       TEXT NOT NULL,
    source       TEXT NOT NULL,           -- nom du PDF
    page         INTEGER NOT NULL,
    texte        TEXT NOT NULL,
    embedding    vector NOT NULL,         -- type pgvector, trouvé via le search_path
    PRIMARY KEY (user_id, embedding_id, id)
);
CREATE INDEX IF NOT EXISTS chunks_user_module ON {SCHEMA_APP}.chunks (user_id, embedding_id, module);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.questions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL,
    module        TEXT NOT NULL,
    notion        TEXT NOT NULL,
    question      TEXT NOT NULL,
    choix         JSONB NOT NULL,         -- liste de 4 propositions
    bonne_reponse INTEGER NOT NULL,       -- indice 0 à 3
    explication   TEXT NOT NULL,
    source        TEXT NOT NULL,
    page          INTEGER NOT NULL,
    extrait       TEXT NOT NULL,
    boite         INTEGER NOT NULL DEFAULT 1,
    cree_le       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS questions_user_module ON {SCHEMA_APP}.questions (user_id, module);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.reponses (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    question_id BIGINT NOT NULL REFERENCES {SCHEMA_APP}.questions(id) ON DELETE CASCADE,
    choix       INTEGER NOT NULL,
    correct     BOOLEAN NOT NULL,
    repondu_le  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reponses_user ON {SCHEMA_APP}.reponses (user_id);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.notions (
    id      BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    module  TEXT NOT NULL,
    nom     TEXT NOT NULL,
    resume  TEXT NOT NULL DEFAULT '',
    sources JSONB NOT NULL DEFAULT '[]',  -- ["TP4.pdf p. 2, 3", ...]
    cree_le TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, module, nom)
);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.signalements (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    question_id BIGINT NOT NULL REFERENCES {SCHEMA_APP}.questions(id) ON DELETE CASCADE,
    motif       TEXT NOT NULL,
    commentaire TEXT NOT NULL DEFAULT '',
    cree_le     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.comptes (
    id              TEXT PRIMARY KEY,     -- "acct-…" : empreinte de l'identité Google
    email           TEXT NOT NULL,
    nom             TEXT NOT NULL,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT now(),
    derniere_visite TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.pseudos_recuperes (
    pseudo_key TEXT PRIMARY KEY,          -- ancien identifiant "user-…" (empreinte du pseudo)
    compte_id  TEXT NOT NULL REFERENCES {SCHEMA_APP}.comptes(id) ON DELETE CASCADE,
    recupere_le TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.messages_chat (
    id        BIGSERIAL PRIMARY KEY,
    user_id   TEXT NOT NULL,
    module    TEXT NOT NULL,               -- '' = conversation sur tous les modules
    role      TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    contenu   TEXT NOT NULL,
    details   JSONB NOT NULL DEFAULT '{{}}'::jsonb,  -- sources citées, alertes…
    cree_le   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_chat_user_module ON {SCHEMA_APP}.messages_chat (user_id, module, id);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.fiches (
    id         BIGSERIAL PRIMARY KEY,
    user_id    TEXT NOT NULL,
    module     TEXT NOT NULL,
    notion     TEXT NOT NULL,              -- notion demandée par l'étudiant
    notion_cle TEXT GENERATED ALWAYS AS ({CLE_NOTION_SQL.format('notion')}) STORED,
    source     TEXT NOT NULL DEFAULT '',   -- fichier choisi, '' = tout le module
    contenu    JSONB NOT NULL,             -- la fiche telle que renvoyée par generation.generer_fiche
    cree_le    TIMESTAMPTZ NOT NULL DEFAULT now(),
    maj_le     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Une seule fiche par notion et par fichier choisi : régénérer remplace l'ancienne.
-- L'index sert aussi à lister les fiches d'un module.
CREATE UNIQUE INDEX IF NOT EXISTS fiches_unique ON {SCHEMA_APP}.fiches (user_id, module, notion_cle, source);

CREATE TABLE IF NOT EXISTS {SCHEMA_APP}.feedback (
    id        BIGSERIAL PRIMARY KEY,
    user_id   TEXT NOT NULL,
    categorie TEXT NOT NULL,
    note      INTEGER,
    message   TEXT NOT NULL,
    mode      TEXT NOT NULL,
    cree_le   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _configurer(conn) -> None:
    """Exécuté à chaque nouvelle connexion : on travaille dans le schéma de l'app."""
    conn.execute(f"SET search_path TO {SCHEMA_APP}, extensions, public")
    conn.commit()


@lru_cache(maxsize=1)
def _pool() -> ConnectionPool:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise StorageError(
            "DATABASE_URL est vide. Mets la chaîne de connexion Supabase dans le fichier .env "
            "(en local) ou dans les secrets de l'hébergeur (en ligne)."
        )
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=int(os.getenv("DB_POOL_MAX", "4")),  # le pooler gratuit de Supabase limite les connexions
        kwargs={"row_factory": dict_row, "prepare_threshold": None},  # None : compatible avec les poolers
        configure=_configurer,
        check=ConnectionPool.check_connection,  # vérifie qu'une connexion n'a pas été coupée avant de la prêter
        open=False,
        timeout=20,
    )
    try:
        pool.open(wait=True, timeout=20)
        with pool.connection() as conn:
            conn.execute(SCHEMA_SQL)  # crée ce qui manque, ne touche pas à l'existant
        atexit.register(pool.close)  # ferme proprement les connexions à la fin d'un script
    except Exception as e:
        pool.close()
        print(f"[storage] échec de connexion ou d'initialisation : {e!r}")  # détail visible dans les logs
        raise StorageError(
            "Impossible de se connecter à la base de données. Vérifie DATABASE_URL "
            "(chaîne « Session pooler » de Supabase) et que le projet Supabase n'est pas en pause."
        ) from e
    return pool


@contextmanager
def connexion():
    """
    Une connexion du pool, dans une transaction : validée (commit) si tout se passe bien,
    annulée (rollback) en cas d'erreur.
    """
    with _pool().connection() as conn:
        yield conn


def verifier() -> None:
    """Lève StorageError si la base est injoignable (appelé au démarrage de l'app)."""
    with connexion() as conn:
        conn.execute("SELECT 1")
