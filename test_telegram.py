"""
test_telegram.py — Configure et teste les notifications Telegram.

    python test_telegram.py --chat-id   # affiche ton chat_id (après avoir écrit au bot)
    python test_telegram.py             # envoie un message de test
"""

import json
import sys
import urllib.request

import notif

token, chat_id = notif._config()
if not token:
    raise SystemExit("TELEGRAM_BOT_TOKEN est vide dans .env (voir @BotFather sur Telegram).")

if "--chat-id" in sys.argv:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10) as r:
        updates = json.load(r).get("result", [])
    chats = {
        u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
        for u in updates
        if "message" in u
    }
    if not chats:
        raise SystemExit("Aucun message reçu. Ouvre ton bot dans Telegram, appuie sur « Démarrer » "
                         "ou écris-lui « salut », puis relance cette commande.")
    for cid, nom in chats.items():
        print(f"chat_id trouvé : {cid}  ({nom})  -> mets TELEGRAM_CHAT_ID={cid} dans .env")
    raise SystemExit(0)

if not chat_id:
    raise SystemExit("TELEGRAM_CHAT_ID est vide : lance d'abord  python test_telegram.py --chat-id")

notif.envoyer("✅ Test du coach de révision : les notifications Telegram fonctionnent !", attendre=True)
print("Message envoyé : regarde Telegram.")
