"""
auth.py — Qui est connecté ?

  - En production : connexion Google (OpenID Connect) gérée par Streamlit (st.login / st.user).
    L'app ne voit jamais le mot de passe : Google vérifie l'identité et renvoie un jeton signé
    contenant un identifiant stable (« sub »), l'e-mail et le nom.
  - En mode développeur (aucune section [auth] dans .streamlit/secrets.toml) : simple pseudo,
    comme avant, pratique pour tester en local.

Configuration de la connexion Google (secrets) :
    [auth]
    redirect_uri = "https://ton-domaine.fr/oauth2callback"
    cookie_secret = "une longue chaîne aléatoire"
    client_id = "....apps.googleusercontent.com"
    client_secret = "..."
    server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
Administrateurs : ADMIN_EMAILS = "toi@gmail.com, autre@gmail.com" (au niveau racine des secrets).
"""

import hashlib
import os

import streamlit as st


def connexion_google_active() -> bool:
    """Vrai si la section [auth] est configurée dans les secrets."""
    try:
        return "auth" in st.secrets
    except Exception:  # pas de fichier secrets.toml : mode développeur
        return False


def compte_connecte() -> dict | None:
    """
    {"id", "email", "nom"} de l'utilisateur connecté avec Google, ou None.
    L'identifiant est une empreinte de l'émetteur + du « sub » Google : stable même si
    l'utilisateur change de nom, et il ne révèle pas l'e-mail.
    """
    if not st.user.is_logged_in:
        return None
    infos = st.user.to_dict()
    sujet = infos.get("sub") or infos.get("email", "")
    emetteur = infos.get("iss", "google")
    empreinte = hashlib.sha256(f"{emetteur}|{sujet}".encode()).hexdigest()[:24]
    email = infos.get("email", "")
    return {"id": f"acct-{empreinte}", "email": email, "nom": infos.get("name") or email}


def est_admin(email: str) -> bool:
    """Les administrateurs sont listés par e-mail dans ADMIN_EMAILS (pas de mot de passe à retenir)."""
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(email) and email.lower() in admins
