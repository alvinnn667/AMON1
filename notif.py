"""
notif.py — Copie des retours testeurs (signalements, avis) vers Telegram.

Pourquoi : en ligne, la base SQLite vit sur un disque éphémère et peut être effacée
au redémarrage. Telegram garde une copie durable et te prévient en direct.

Configuration (fichier .env en local, secrets de l'hébergeur en ligne) :
    TELEGRAM_BOT_TOKEN=...   (donné par @BotFather)
    TELEGRAM_CHAT_ID=...     (obtenu avec : python test_telegram.py --chat-id)
Si ces variables sont vides, rien n'est envoyé et l'app fonctionne normalement.
"""

import json
import os
import threading
import urllib.request

from dotenv import load_dotenv

load_dotenv()

LIMITE_TELEGRAM = 4000  # un message Telegram fait au maximum 4096 caractères


def _config() -> tuple[str, str]:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()


def actif() -> bool:
    token, chat_id = _config()
    return bool(token and chat_id)


def _envoyer_maintenant(texte: str) -> None:
    token, chat_id = _config()
    requete = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": texte[:LIMITE_TELEGRAM]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete, timeout=10) as reponse:
        if reponse.status != 200:
            raise RuntimeError(f"Telegram a répondu {reponse.status}")


def envoyer(texte: str, attendre: bool = False) -> None:
    """
    Envoie un message. Par défaut en arrière-plan (thread) : l'étudiant n'attend pas,
    et une panne de Telegram ne casse jamais l'app. attendre=True sert aux tests.
    """
    if not actif():
        return
    if attendre:
        _envoyer_maintenant(texte)
        return

    def tache() -> None:
        try:
            _envoyer_maintenant(texte)
        except Exception as e:  # on ne bloque jamais l'utilisateur pour une notification
            print(f"[notif] échec de l'envoi Telegram : {e}")

    threading.Thread(target=tache, daemon=True).start()


def message_signalement(q: dict, motif: str, commentaire: str, testeur: str, mode: str) -> str:
    choix = "\n".join(f"{'✅' if j == q['bonne_reponse'] else '▫️'} {c}" for j, c in enumerate(q["choix"]))
    return (
        f"🚩 Question signalée ({mode}) — testeur {testeur}\n"
        f"Motif : {motif}\n"
        f"Commentaire : {commentaire or '—'}\n\n"
        f"[{q['notion']}] {q['question']}\n{choix}\n\n"
        f"Explication : {q['explication']}\n"
        f"Source : {q['source']}, p. {q['page']}"
    )


def message_avis(categorie: str, note: int, message: str, testeur: str, mode: str) -> str:
    return f"📣 {categorie} — note {note}/5 ({mode}) — testeur {testeur}\n\n{message}"
