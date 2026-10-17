# Coach de révision IA

Application web qui transforme tes cours PDF en outil de révision : questions-réponses
avec la source exacte, QCM qui ciblent tes points faibles, fiches par notion et suivi de
ta maîtrise. Les cours sont rangés par module (matière).

Principe : l'étudiant sait toujours d'où vient une réponse. Sur le cours, tout est cité avec le
fichier et la page ; si l'information n'y est pas, l'IA le dit. Dans la page Questions, l'assistant
peut aussi chercher sur le web (réponse citée avec les liens) ou simplement discuter (réponse
étiquetée « sans source »).

## Fonctionnalités

- **Import intelligent** : l'IA propose le module de chaque PDF, puis extrait la carte
  des notions du cours à partir de son plan.
- **Questions** : réponses rédigées à partir des passages retrouvés (RAG), avec citations
  `[1]`, `[2]` vers le fichier et la page. Recherche dans un module ou dans tous.
  Le chat a de la mémoire : on peut enchaîner (« et sa formule ? ») et la conversation est
  conservée d'une visite à l'autre. La réponse s'affiche au fur et à mesure (streaming).
  C'est un vrai assistant : un petit appel au LLM (modèle léger) choisit d'abord comment répondre :
  - **cours** : d'après les passages du cours, citations `[1]`, `[2]` ;
  - **web** : question de fait que le cours ne couvre pas (définition précise, chiffre, date,
    actualité) -> recherche [Tavily](https://tavily.com), réponse citée `[W1]`, `[W2]` avec les liens ;
  - **discussion** : salutation, conseil, méthode de travail, reformulation -> réponse directe,
    étiquetée « Réponse générale, sans source ».
  Sous une réponse « pas dans ton cours », un bouton **Chercher sur le web** relance la question
  sur le web. Recherches web limitées par jour : 10 par personne et 30 pour toute l'app
  (le quota gratuit de Tavily est partagé).
- **QCM** : mode automatique (vise les notions à retravailler, fragiles ou jamais
  travaillées) ou personnalisé (fichier, notion). 4 choix, explication, source.
- **Fiches** : définition, points clés, formules, pièges classiques, chaque ligne sourcée.
  Les fiches sont enregistrées (« Mes fiches ») : on les retrouve sur tous ses appareils sans
  nouvel appel à l'IA, on peut les régénérer ou les supprimer. Supprimer ou réimporter un
  fichier retire les fiches qui s'appuient sur lui.
- **Progrès** : maîtrise par notion (sur les 10 dernières réponses) et révision des erreurs
  avec des boîtes de Leitner.
- **Comptes** : connexion avec Google ; chacun retrouve ses cours sur tous ses appareils.
  Les données d'un ancien pseudo peuvent être rattachées au compte.
- **Retours des testeurs** : signalement des questions fausses, avis, page Admin,
  copie sur Telegram.

## Architecture

| Fichier | Rôle |
|---|---|
| `app.py` | Interface Streamlit (pages, navigation, formulaires) |
| `auth.py` | Connexion Google (`st.login`) et administrateurs par e-mail |
| `llm.py` | Abstraction du LLM et des embeddings : mode `local` (Ollama) ou `api` (Groq + fastembed) |
| `ingest.py` | Lecture des PDF (PyMuPDF), nettoyage, découpage, stockage et recherche vectorielle (pgvector) |
| `rag.py` | Réponses aux questions avec citations, mémoire de conversation (reformulation des questions de suivi) |
| `assistant.py` | Page Questions : choix du mode (cours, web, discussion), recherche web dans les limites du jour, réponse web sans lien ni image |
| `web.py` | Client de recherche web Tavily (`httpx`), erreurs lisibles |
| `affichage.py` | Affichage sûr des réponses de l'assistant : Markdown -> HTML sans lien ni image (`markdown-it-py`) |
| `generation.py` | QCM, QCM adaptatif, fiches, rangement des PDF, carte des notions (JSON validé par pydantic) |
| `prompts.py` | Tous les prompts, en français |
| `db.py` | Questions, réponses, boîtes de Leitner, carte des notions, fiches enregistrées, retours (Postgres) |
| `storage.py` | Connexion à la base Supabase (pool) et création du schéma `coach` |
| `migrer_vers_supabase.py` | Migration unique de l'ancien stockage local (SQLite + ChromaDB) |
| `notif.py` | Copie des signalements et avis sur Telegram |
| `test_*.py` | Scripts de vérification étape par étape |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `*.sh` | Mise en ligne sur un serveur (voir plus bas) |

## Installation locale (macOS)

Prérequis : [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com/download) et un projet
[Supabase](https://supabase.com) gratuit (base Postgres avec pgvector).

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
| `TAVILY_API_KEY` | Facultatif : clé Tavily pour la recherche web ([app.tavily.com](https://app.tavily.com), 1 000 recherches/mois gratuites). Sans clé, pas de recherche web |
| `WEB_RECHERCHES_PAR_JOUR` | Facultatif : recherches web par personne et par jour (défaut `10`) |
| `WEB_RECHERCHES_GLOBAL_PAR_JOUR` | Facultatif : recherches web par jour pour toute l'app, tous utilisateurs confondus (défaut `30`) |
| `GROQ_MODELE_LEGER` | Facultatif : modèle Groq essayé en premier pour le choix du mode (défaut `llama-3.1-8b-instant`) |
| `DATABASE_URL` | Chaîne de connexion Supabase « Session pooler » (obligatoire) |
| `ADMIN_EMAILS` | E-mails des administrateurs (connexion Google), séparés par des virgules |
| `ADMIN_PASSWORD` | Mode développeur seulement : mot de passe de la page Admin |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Facultatif : copie des retours sur Telegram |

Le fichier `.env` n'est jamais versionné (voir `.gitignore`).

**Connexion Google en local (facultatif).** Sans configuration, l'app démarre en *mode
développeur* : un simple pseudo, pratique pour tester. Pour tester la vraie connexion,
crée `.streamlit/secrets.toml` (ignoré par Git) avec la section `[auth]` décrite plus bas,
et `redirect_uri = "http://localhost:8501/oauth2callback"`.

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

## Mise en ligne (serveur Hetzner + nom de domaine)

L'app tourne dans Docker sur un petit serveur (VPS Hetzner CX23 : 2 vCPU, 4 Go de RAM).
[Caddy](https://caddyserver.com) est placé devant : il obtient et renouvelle tout seul le
certificat HTTPS pour le domaine, puis relaie les requêtes (et les websockets de Streamlit)
vers l'app. Les données restent dans Supabase : le serveur ne stocke rien d'important.

| Fichier | Rôle |
|---|---|
| `Dockerfile` | Image de l'app (dépendances + modèle d'embeddings préchargé, utilisateur sans droits) |
| `docker-compose.yml` | Les deux conteneurs : `app` (privé) et `caddy` (ports 80 et 443) |
| `Caddyfile` | HTTPS automatique, redirection `www`, en-têtes de sécurité |
| `setup_serveur.sh` | Préparation unique du serveur : Docker, utilisateur `deploy`, pare-feu, fail2ban, swap |
| `deploy.sh` | Mise à jour : `git pull`, reconstruction, redémarrage, vérification |

**Première installation**

1. DNS du domaine : un enregistrement `A` pour `@` et un pour `www` vers l'IPv4 du serveur
   (et `AAAA` vers l'IPv6).
2. En root sur le serveur neuf (Ubuntu 24.04) : `bash setup_serveur.sh`, puis ajouter la clé
   affichée dans **GitHub > Settings > Deploy keys** du dépôt.
3. En `deploy` : `git clone git@github.com:<toi>/<depot>.git coach && cd coach`
4. Créer les secrets, qui ne quittent jamais le serveur :
   - `.env` : `DOMAIN`, `GROQ_API_KEY`, `TAVILY_API_KEY`, `DATABASE_URL`, `ADMIN_EMAILS`, `TELEGRAM_*`
     (`LLM_MODE=api` est imposé par `docker-compose.yml`) ;
   - `.streamlit/secrets.toml` : la section `[auth]` de la connexion Google (ci-dessous).
5. `bash deploy.sh`

**Mises à jour** : `git push` depuis ton ordinateur, puis sur le serveur `bash deploy.sh`.

**Commandes utiles sur le serveur**

```bash
docker compose ps                      # état des conteneurs
docker compose logs -f --tail 50 app   # logs de l'app en direct
docker compose restart app             # redémarrer l'app (après un changement de secrets)
docker stats --no-stream               # RAM et CPU utilisés
```

**Connexion Google** (`.streamlit/secrets.toml` sur le serveur) :

```toml
[auth]
redirect_uri = "https://ton-domaine.fr/oauth2callback"
cookie_secret = "longue_chaine_aleatoire"
client_id = "xxxx.apps.googleusercontent.com"
client_secret = "xxxx"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Le `client_id` et le `client_secret` viennent de Google Cloud Console (**Google Auth Platform >
Clients**, type *Web application*), avec comme URI de redirection autorisées
`https://ton-domaine.fr/oauth2callback` et `http://localhost:8501/oauth2callback`.
Générer `cookie_secret` avec `python -c "import secrets; print(secrets.token_hex(32))"`.
Fichier vide ou absent : l'app reste en mode développeur (pseudo).

Les tables sont créées automatiquement au premier démarrage, dans un schéma privé `coach`
que l'API publique de Supabase n'expose pas.

*Alternative gratuite* : Streamlit Community Cloud (dépôt GitHub, secrets collés dans
**Settings > Secrets** de l'app). Pas de domaine personnalisé. Les secrets de premier niveau sont
recopiés dans les variables d'environnement au démarrage (`llm.py`) ; la section `[auth]` vient
en dernier, sinon les lignes suivantes lui appartiendraient :

```toml
LLM_MODE = "api"
GROQ_API_KEY = "gsk_…"
TAVILY_API_KEY = "tvly-…"          # facultatif : recherche web
WEB_RECHERCHES_PAR_JOUR = "10"     # facultatif : par personne
WEB_RECHERCHES_GLOBAL_PAR_JOUR = "30"  # facultatif : pour toute l'app
DATABASE_URL = "postgresql://…"
ADMIN_EMAILS = "toi@gmail.com"

[auth]
redirect_uri = "https://ton-app.streamlit.app/oauth2callback"
# … (voir ci-dessus)
```

## Limites connues

- Projet Supabase gratuit : mis en pause après 7 jours sans activité (à réactiver depuis
  le tableau de bord, les données sont conservées), 500 Mo de base.
- Quotas gratuits de Groq : en cas de dépassement, l'app bascule sur un autre modèle,
  puis demande de réessayer une minute plus tard.
- PDF scannés (images) non pris en charge : il faut du texte sélectionnable.
- Mémoire du chat limitée aux 3 derniers échanges (au-delà, le coach ne s'en souvient plus).
- Recherche web : 1 000 recherches par mois sur l'offre gratuite de Tavily, partagées entre tous
  les utilisateurs ; au-delà de la limite du jour, l'assistant répond sans web et le dit. Le choix
  du mode (cours, web, discussion) peut se tromper : le bouton « Chercher sur le web » rattrape
  les questions de fait classées à tort.
  Les réponses de l'assistant sont affichées par un analyseur Markdown configuré sans liens ni
  images (`affichage.py`) : une page web piégée ne peut pas faire écrire au LLM une image qui
  enverrait la conversation ailleurs. Les seuls liens cliquables sont ceux de la liste « Sources web ».

## Pistes

Analyse des annales, planning d'examen, algorithme FSRS, OCR des PDF scannés.
