#!/usr/bin/env bash
# deploy.sh — Met en ligne la dernière version du code sur le serveur.
#
# Sur le serveur, connecté en « deploy », dans le dossier du projet :
#     bash deploy.sh
#
# 1. récupère le code depuis GitHub
# 2. reconstruit l'image si besoin et relance les conteneurs (app + Caddy)
# 3. attend que l'app réponde et affiche l'état
set -euo pipefail
cd "$(dirname "$0")"

# --- Vérifications des secrets (présents sur le serveur seulement, jamais dans Git) ---
if [[ ! -f .env ]]; then
  echo "Fichier .env manquant : copie .env.example en .env et remplis-le (voir README)." >&2
  exit 1
fi
if ! grep -qE '^DOMAIN=.+' .env; then
  echo "Ajoute la ligne DOMAIN=ton-domaine.fr dans .env" >&2
  exit 1
fi
mkdir -p .streamlit
# Sans ce fichier, Docker créerait un dossier à sa place. Vide = pas de connexion Google (mode pseudo).
[[ -f .streamlit/secrets.toml ]] || touch .streamlit/secrets.toml
chmod 600 .env   # lu par Docker (root) uniquement
# Lu par l'app dans le conteneur, qui tourne avec un autre utilisateur : lisible, mais ton dossier
# personnel (/home/deploy, droits 750) reste fermé aux autres comptes du serveur.
chmod 644 .streamlit/secrets.toml

echo "==> Récupération du code"
git pull --ff-only

echo "==> Construction et redémarrage"
docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null   # supprime les anciennes images (place disque)

echo "==> Attente du démarrage de l'app"
for _ in $(seq 1 30); do
  etat=$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q app)" 2>/dev/null || echo "?")
  [[ "$etat" == "healthy" ]] && break
  sleep 3
done
docker compose ps
if [[ "$etat" != "healthy" ]]; then
  echo "L'app ne répond pas. Regarde les logs : docker compose logs --tail 50 app" >&2
  exit 1
fi
# La connexion Google est-elle bien configurée ? (sinon l'app reste en mode pseudo)
if docker compose exec -T app python -c "import streamlit as st, sys; sys.exit(0 if 'auth' in st.secrets else 1)" 2>/dev/null; then
  echo "Connexion Google : activée"
else
  echo "Connexion Google : désactivée (section [auth] absente ou illisible dans .streamlit/secrets.toml) -> mode pseudo"
fi
domaine=$(grep -E '^DOMAIN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
echo
echo "En ligne : https://$domaine"
