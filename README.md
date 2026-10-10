# Coach de révision IA

Application web qui transforme tes cours PDF en outil de révision : questions-réponses
avec la source exacte, QCM qui ciblent tes points faibles, fiches par notion et suivi de
ta maîtrise. Les cours sont rangés par module (matière).

Principe : tout ce que l'IA affirme vient du cours fourni, avec le fichier et la page.
Si l'information n'y est pas, elle le dit.

## Fonctionnalités

- **Import intelligent** : l'IA propose le module de chaque PDF, puis extrait la carte
  des notions du cours à partir de son plan.
- **Questions** : réponses rédigées à partir des passages retrouvés (RAG), avec citations
  `[1]`, `[2]` vers le fichier et la page. Recherche dans un module ou dans tous.
- **QCM** : mode automatique (vise les notions à retravailler, fragiles ou jamais
  travaillées) ou personnalisé (fichier, notion). 4 choix, explication, source.
- **Fiches** : définition, points clés, formules, pièges classiques, chaque ligne sourcée.
- **Progrès** : maîtrise par notion (sur les 10 dernières réponses) et révision des erreurs
  avec des boîtes de Leitner.
- **Retours des testeurs** : signalement des questions fausses, avis, page Admin,
  copie sur Telegram.

## Architecture

| Fichier | Rôle |
|---|---|
| `app.py` | Interface Streamlit (pages, navigation, formulaires) |
| `llm.py` | Abstraction du LLM et des embeddings : mode `local` (Ollama) ou `api` (Groq + fastembed) |
| `ingest.py` | Lecture des PDF (PyMuPDF), nettoyage, découpage, ChromaDB (une collection par utilisateur) |
| `rag.py` | Réponses aux questions avec citations |
| `generation.py` | QCM, QCM adaptatif, fiches, rangement des PDF, carte des notions (JSON validé par pydantic) |
| `prompts.py` | Tous les prompts, en français |
| `db.py` | SQLite : questions, réponses, boîtes de Leitner, carte des notions, retours |
| `notif.py` | Copie des signalements et avis sur Telegram |
| `test_*.py` | Scripts de vérification étape par étape |

## Installation locale (macOS)

Prérequis : [uv](https://docs.astral.sh/uv/) et [Ollama](https://ollama.com/download).

```bash
cd coach-revision
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env          # puis remplis .env (voir ci-dessous)

ollama pull qwen2.5:7b        # LLM local
ollama pull bge-m3            # embeddings multilingues
```

Variables du fichier `.env` :

| Variable | Rôle |
|---|---|
| `LLM_MODE` | `local` (Ollama) ou `api` (Groq + fastembed) |
| `GROQ_API_KEY` | Clé Groq, nécessaire en mode `api` ([console.groq.com/keys](https://console.groq.com/keys)) |
| `ADMIN_PASSWORD` | Mot de passe de la page Admin (laisser vide pour la désactiver) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Facultatif : copie des retours sur Telegram |

Le fichier `.env` n'est jamais versionné (voir `.gitignore`).

## Lancer

```bash
streamlit run app.py                    # mode défini dans .env
LLM_MODE=api streamlit run app.py       # forcer le mode en ligne
```

Vérifications étape par étape :

```bash
python test_llm.py                      # connexion au LLM, JSON, embeddings
python test_ingest.py "ma question"     # indexation de cours_exemple/ et recherche
python test_qcm.py "une notion"         # génération de QCM
python test_fiche.py "une notion"       # génération de fiche
python test_telegram.py                 # notification Telegram
```

## Mise en ligne (Streamlit Community Cloud)

1. Pousser le dépôt sur GitHub (le `.gitignore` exclut `.env`, `data/` et `cours_exemple/`).
2. Sur [share.streamlit.io](https://share.streamlit.io) : **Create app**, choisir le dépôt,
   la branche `main` et le fichier `app.py`.
3. Dans **Advanced settings** : Python 3.12, puis coller les secrets :

```toml
LLM_MODE = "api"
GROQ_API_KEY = "ta_cle"
ADMIN_PASSWORD = "ton_mot_de_passe"
TELEGRAM_BOT_TOKEN = "ton_token"
TELEGRAM_CHAT_ID = "ton_chat_id"
```

Les secrets sont recopiés dans les variables d'environnement au démarrage (`llm.py`).
`pysqlite3-binary` remplace le SQLite trop ancien de l'hébergeur pour ChromaDB (`ingest.py`).

## Limites connues

- Hébergement gratuit et éphémère : les cours et scores des testeurs peuvent être effacés
  au redémarrage. Les signalements et avis sont copiés sur Telegram.
- Le pseudo sépare les espaces mais n'est pas un mot de passe.
- Quotas gratuits de Groq : en cas de dépassement, l'app bascule sur un autre modèle,
  puis demande de réessayer une minute plus tard.
- PDF scannés (images) non pris en charge : il faut du texte sélectionnable.
- Chaque question du chat est traitée seule (pas de mémoire de la conversation).

## Pistes

Analyse des annales, planning d'examen, algorithme FSRS, comptes utilisateurs,
stockage durable (base hébergée), OCR des PDF scannés.
