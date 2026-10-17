#!/usr/bin/env bash
# setup_serveur.sh — Préparation UNIQUE d'un serveur neuf (Ubuntu 24.04, Hetzner).
#
# À lancer une seule fois, connecté en root :
#     bash setup_serveur.sh
#
# Ce que fait le script :
#   1. Mises à jour du système + mises à jour de sécurité automatiques
#   2. Docker (dépôt officiel)
#   3. Utilisateur « deploy » (on n'utilise plus root ensuite), connexion par clé SSH uniquement
#   4. Pare-feu : seuls SSH (22), HTTP (80) et HTTPS (443) sont ouverts ; fail2ban contre les attaques SSH
#   5. 2 Go de swap (filet de sécurité si la RAM est pleine)
#   6. Clé SSH du serveur pour lire ton dépôt GitHub (« deploy key », lecture seule)
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Lance ce script en root : bash setup_serveur.sh" >&2
  exit 1
fi
# Sécurité : on va interdire les mots de passe SSH, donc il faut une clé déjà installée.
if [[ ! -s /root/.ssh/authorized_keys ]]; then
  echo "Aucune clé SSH dans /root/.ssh/authorized_keys : ajoute ta clé au serveur avant (sinon tu serais enfermé dehors)." >&2
  exit 1
fi

echo "==> 1/6 Mises à jour du système"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl git ufw fail2ban unattended-upgrades
timedatectl set-timezone Europe/Paris
# Mises à jour de sécurité installées automatiquement chaque jour
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "==> 2/6 Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> 3/6 Utilisateur deploy"
if ! id deploy >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" deploy
fi
usermod -aG sudo,docker deploy
# deploy n'a pas de mot de passe (connexion par clé uniquement) : sudo sans mot de passe.
echo "deploy ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-deploy
chmod 440 /etc/sudoers.d/90-deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -m 600 -o deploy -g deploy /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys

# SSH : clé obligatoire, root interdit
cat > /etc/ssh/sshd_config.d/99-coach.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sshd -t                      # vérifie la configuration avant de l'appliquer
systemctl restart ssh

echo "==> 4/6 Pare-feu et fail2ban"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable
systemctl enable --now fail2ban   # bannit les IP qui ratent leur connexion SSH en boucle

echo "==> 5/6 Swap de 2 Go"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

echo "==> 6/6 Clé de lecture GitHub pour deploy"
if [[ ! -f /home/deploy/.ssh/github ]]; then
  sudo -u deploy ssh-keygen -q -t ed25519 -N "" -C "serveur-coach" -f /home/deploy/.ssh/github
  cat > /home/deploy/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github
  IdentitiesOnly yes
EOF
  chown deploy:deploy /home/deploy/.ssh/config
  chmod 600 /home/deploy/.ssh/config
  ssh-keyscan -t ed25519 github.com 2>/dev/null >> /home/deploy/.ssh/known_hosts
  chown deploy:deploy /home/deploy/.ssh/known_hosts
fi

echo
echo "Serveur prêt."
echo "Désormais, connecte-toi avec :  ssh deploy@$(curl -4 -s https://ifconfig.me || hostname -I | cut -d' ' -f1)"
echo "(root ne peut plus se connecter en SSH)"
echo
echo "Ajoute cette clé dans GitHub > ton dépôt > Settings > Deploy keys > Add deploy key (sans cocher « write ») :"
echo
cat /home/deploy/.ssh/github.pub
