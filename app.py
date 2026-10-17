"""
app.py — Interface Streamlit du coach de révision.

Lancement : streamlit run app.py

Organisation :
  - Écran de connexion : compte Google (st.login) + information claire sur les données.
    Sans section [auth] dans les secrets : mode développeur avec un simple pseudo.
  - Navigation en haut (repliée en menu sur téléphone) : Cours, Questions, QCM,
    Fiches, Progrès, Avis, et Admin pour les e-mails listés dans ADMIN_EMAILS
    (ou avec le mot de passe ADMIN_PASSWORD en mode développeur).
  - Les cours sont rangés par module ; chaque page travaille sur le module choisi.
  - Thème clair / sombre / système : menu ⋮ > Settings (couleurs dans .streamlit/config.toml).
Uniquement des composants Streamlit natifs et des icônes Material : pas de CSS maison.
"""

import csv
import hmac
import html
import io
import os
import re
import time
from zoneinfo import ZoneInfo

import streamlit as st

import affichage
import assistant
import auth
import db
import generation
import ingest
import llm
import notif
import prompts
import storage
import web

st.set_page_config(page_title="Coach de révision", page_icon=":material/school:", layout="centered")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
EN_LIGNE = llm.LLM_MODE == "api"
TOUS_LES_MODULES = "Tous les modules"
NOUVEAU_MODULE = "Nouveau module…"
PARIS = ZoneInfo("Europe/Paris")  # les dates sont stockées en UTC, affichées à l'heure française

NIVEAUX = {  # code renvoyé par db.niveau() -> (libellé, couleur, icône)
    "maitrise": ("Maîtrisé", "green", ":material/check_circle:"),
    "fragile": ("Fragile", "orange", ":material/trending_flat:"),
    "a_retravailler": ("À retravailler", "red", ":material/priority_high:"),
    "non_teste": ("Pas encore répondu", "gray", ":material/hourglass_empty:"),
    "jamais": ("Jamais travaillée", "gray", ":material/radio_button_unchecked:"),
}
AVATARS = {"user": ":material/person:", "assistant": ":material/school:"}


# ---------------------------------------------------------------------------
# Petits utilitaires
# ---------------------------------------------------------------------------
CONNEXION_GOOGLE = auth.connexion_google_active()


def compte() -> dict | None:
    """Compte Google connecté ({"id", "email", "nom"}), ou None en mode développeur / déconnecté."""
    return auth.compte_connecte() if CONNEXION_GOOGLE else None


def identifiant() -> str:
    """Identifiant de l'utilisateur : son compte Google, ou son pseudo en mode développeur. "" si personne."""
    if CONNEXION_GOOGLE:
        c = compte()
        return c["id"] if c else ""
    return st.session_state.get("pseudo_valide", "")


def user() -> str:
    return ingest.user_key(identifiant())


def etat(nom: str) -> dict:
    """Dictionnaire de session propre à l'utilisateur courant (historique, quiz…)."""
    return st.session_state.setdefault(nom, {}).setdefault(identifiant(), {})


def toast_apres_rerun(message: str) -> None:
    """Un st.toast juste avant st.rerun() serait perdu : on le garde pour le prochain affichage."""
    st.session_state["_toast"] = message


def afficher_toast_en_attente() -> None:
    message = st.session_state.pop("_toast", None)
    if message:
        st.toast(message, icon=":material/check_circle:")


def sans_cours() -> bool:
    """Affiche un état vide explicite si aucun cours n'est importé."""
    if ingest.list_modules(identifiant()):
        return False
    st.info("Ajoute d'abord un cours en PDF dans la page **Cours**.", icon=":material/info:")
    st.page_link(PAGES["cours"], label="Aller à mes cours", icon=":material/folder_open:")
    return True


def selecteur_module(cle: str, tous: bool = False) -> str | None:
    """
    Choix du module de travail, partagé entre les pages (QCM, Fiches, Progrès…).
    Avec tous=True, propose aussi « Tous les modules » (renvoie alors None).
    """
    modules = sorted(ingest.list_modules(identifiant()))
    options = ([TOUS_LES_MODULES] if tous else []) + modules
    if st.session_state.get(f"memo_{cle}") == TOUS_LES_MODULES and tous:
        memo = TOUS_LES_MODULES
    else:
        memo = st.session_state.get("module_actif")
    index = options.index(memo) if memo in options else (1 if tous and len(options) > 1 else 0)
    choix = st.selectbox("Module", options, index=index, key=f"selecteur_{cle}")
    st.session_state[f"memo_{cle}"] = choix
    if choix != TOUS_LES_MODULES:
        st.session_state["module_actif"] = choix
    return None if choix == TOUS_LES_MODULES else choix


def texte_donnees() -> str:
    lignes = []
    if EN_LIGNE:
        lignes.append(
            ":material/cloud_upload: Tes questions et les extraits de tes cours sont envoyés à **Groq** (États-Unis) "
            "pour générer les réponses."
        )
    else:
        lignes.append(":material/computer: Mode local : les réponses sont générées sur cet ordinateur (Ollama).")
    if web.web_disponible():
        lignes.append(
            ":material/travel_explore: Quand l'assistant cherche sur le web, ta question reformulée est envoyée à "
            "**Tavily** (service de recherche web), et les résultats à l'IA pour rédiger la réponse. "
            f"Limite : {web.nb_recherches(web.limite_par_jour())} par jour et par personne, et "
            f"{web.nb_recherches(web.limite_globale_par_jour())} par jour pour toute l'app (seules leur date et "
            "leur heure sont notées pour les compter, et oubliées après 2 jours)."
        )
    lignes += [
        ":material/database: Tes cours et ta progression sont conservés dans la base de données de l'app "
        "(Supabase). Version de test : ils peuvent être réinitialisés si besoin.",
        ":material/block: N'importe **pas de document confidentiel**.",
    ]
    if CONNEXION_GOOGLE:
        lignes.append(
            ":material/badge: Ton compte Google sert seulement à t'identifier : l'app enregistre ton e-mail et "
            "ton nom, rien d'autre. Personne d'autre ne peut voir tes cours."
        )
    else:
        lignes.append(
            ":material/badge: Mode développeur : ton pseudo sépare tes cours de ceux des autres, "
            "mais ce n'est pas un mot de passe."
        )
    lignes.append(
        ":material/chat: Tes conversations du chat sont enregistrées pour que tu les retrouves "
        "(et que le coach comprenne tes questions de suivi). « Nouvelle conversation » les efface."
    )
    lignes.append(
        ":material/description: Tes fiches de révision sont enregistrées pour que tu les retrouves sans nouvel "
        "appel à l'IA ; tu peux les supprimer dans la page Fiches."
    )
    lignes.append(
        ":material/forum: Tes réponses aux QCM, tes signalements et tes avis sont enregistrés pour améliorer l'app"
        + (" et transmis à son créateur." if notif.actif() else ".")
    )
    return "\n\n".join(lignes)


# ---------------------------------------------------------------------------
# Connexion (Google, ou pseudo en mode développeur)
# ---------------------------------------------------------------------------
def page_connexion() -> None:
    st.title("Coach de révision")
    st.write(
        "Révise à partir de tes propres cours, rangés par module : pose des questions, entraîne-toi "
        "avec des QCM qui ciblent tes points faibles et suis ta progression. Chaque réponse indique "
        "le fichier et la page d'où elle vient."
    )
    with st.container(border=True):
        st.markdown("**Avant de commencer**")
        st.markdown(texte_donnees())
    compris = st.checkbox("J'ai lu comment mes données sont utilisées")

    if CONNEXION_GOOGLE:
        st.button(
            "Continuer avec Google", type="primary", icon=":material/login:", width="stretch",
            disabled=not compris, on_click=st.login,
        )
        st.caption("Coche la case pour activer la connexion. Ton mot de passe Google n'est jamais transmis à l'app.")
        return

    # Mode développeur : pas de section [auth] dans les secrets -> simple pseudo.
    st.caption(":material/code: Mode développeur : la connexion Google n'est pas configurée, on utilise un pseudo.")
    with st.form("connexion", border=False):
        saisie = st.text_input("Choisis un pseudo", placeholder="ex : alvin-m1", max_chars=30)
        if st.form_submit_button("Commencer", type="primary", icon=":material/arrow_forward:", width="stretch"):
            saisie = saisie.strip()
            if not re.fullmatch(r"[\w.-]{3,30}", saisie):
                st.error("Le pseudo doit faire 3 à 30 caractères : lettres, chiffres, point, tiret ou tiret bas.")
            elif not compris:
                st.error("Coche la case pour confirmer que tu as lu l'information sur tes données.")
            else:
                st.session_state["pseudo_valide"] = saisie
                st.rerun()


# ---------------------------------------------------------------------------
# Barre latérale
# ---------------------------------------------------------------------------
def formulaire_recuperation(c: dict) -> None:
    """Rattache à son compte les données d'un ancien pseudo (avant l'arrivée des comptes)."""
    with st.expander("Récupérer mes données d'avant", icon=":material/move_down:"):
        st.caption(
            "Tu utilisais l'app avec un pseudo ? Indique-le pour rattacher tes cours et ta progression "
            "à ton compte. Possible une seule fois par pseudo."
        )
        with st.form("recuperation", border=False, clear_on_submit=True):
            ancien = st.text_input("Ancien pseudo", max_chars=30)
            if st.form_submit_button("Récupérer", icon=":material/move_down:"):
                try:
                    bilan = db.recuperer_pseudo(c["id"], ancien)
                except db.RecuperationError as e:
                    st.error(str(e))
                    return
                libelles = {"passages": "passages de cours", "questions": "questions", "reponses": "réponses",
                            "notions": "notions", "feedback": "avis", "signalements": "signalements",
                            "messages_chat": "messages du chat", "fiches": "fiches"}
                detail = ", ".join(f"{n} {libelles.get(k, k)}" for k, n in bilan.items() if n)
                toast_apres_rerun(f"Données récupérées : {detail}.")
                st.rerun()


def barre_laterale() -> None:
    c = compte()
    with st.sidebar:
        if c:
            st.markdown(f":material/account_circle: **{c['nom']}**")
            st.caption(c["email"])
            st.button("Se déconnecter", icon=":material/logout:", type="tertiary", on_click=st.logout)
            formulaire_recuperation(c)
        elif identifiant():
            st.markdown(f":material/account_circle: Pseudo : **{identifiant()}**")
            if st.button("Changer de pseudo", icon=":material/logout:", type="tertiary"):
                st.session_state.pop("pseudo_valide", None)
                st.session_state.pop("module_actif", None)
                st.rerun()
        if identifiant():
            with st.expander("Tes données", icon=":material/shield:"):
                st.markdown(texte_donnees())
                st.caption(f"Moteur : {llm.describe()}")

        # Administration : par e-mail avec Google (ADMIN_EMAILS), par mot de passe en mode développeur.
        if CONNEXION_GOOGLE:
            st.session_state["admin"] = bool(c) and auth.est_admin(c["email"])
        elif ADMIN_PASSWORD:
            with st.expander("Administration", icon=":material/admin_panel_settings:"):
                mdp = st.text_input("Mot de passe", type="password", key="mdp_admin")
                if mdp:
                    st.session_state["admin"] = hmac.compare_digest(mdp, ADMIN_PASSWORD)
                    if st.session_state["admin"]:
                        st.caption(":material/check: Accès activé : page **Admin** dans la navigation.")
                    else:
                        st.error("Mot de passe incorrect.")


# ---------------------------------------------------------------------------
# Page « Cours » : import en deux temps, modules, carte des notions
# ---------------------------------------------------------------------------
@st.dialog("Supprimer ce fichier ?")
def dialog_supprimer(nom: str, module: str) -> None:
    st.write(
        f"**{nom}** sera retiré du module {module}, avec les fiches qui s'appuient sur ce fichier. "
        "Les questions de QCM déjà créées restent dans ta progression."
    )
    annuler, confirmer = st.columns(2)
    if annuler.button("Annuler", width="stretch"):
        st.rerun()
    if confirmer.button("Supprimer", type="primary", icon=":material/delete:", width="stretch"):
        ingest.delete_source(identifiant(), nom)
        nb_fiches = db.supprimer_fiches_fichier(user(), nom)
        if module not in ingest.list_modules(identifiant()):  # dernier fichier du module : on retire sa carte
            db.supprimer_carte(user(), module)
            nb_fiches += db.supprimer_fiches_module(user(), module)
        toast_apres_rerun(f"{nom} supprimé" + (f", ainsi que {nb_fiches} fiche(s)." if nb_fiches else "."))
        st.rerun()


@st.dialog("Renommer le module")
def dialog_renommer(module: str) -> None:
    st.caption("Si tu choisis le nom d'un module existant, les deux modules sont fusionnés.")
    nouveau = st.text_input("Nouveau nom", value=module, max_chars=40)
    if st.button("Renommer", type="primary", icon=":material/edit:", width="stretch"):
        try:
            final = ingest.renommer_module(identifiant(), module, nouveau)
        except ingest.IngestError as e:
            st.error(str(e))
            return
        db.renommer_module(user(), module, final)
        st.session_state["module_actif"] = final
        toast_apres_rerun(f"Module renommé en {final}.")
        st.rerun()


def analyser_fichiers(fichiers: list) -> None:
    """Étape 1 de l'import : lecture, embeddings et module suggéré par l'IA (rien n'est encore enregistré)."""
    attente = etat("import").setdefault("en_attente", [])
    modules = sorted(ingest.list_modules(identifiant()))
    erreurs = 0
    with st.status("Lecture et analyse des fichiers…", expanded=True) as statut:
        for f in fichiers:
            try:
                prep = ingest.preparer_pdf(f.name, f.getvalue())
                # Les modules proposés pour les fichiers précédents comptent comme existants :
                # plusieurs fichiers d'une même nouvelle matière sont ainsi regroupés.
                proposes = [a["suggestion"]["module"] for a in attente]
                suggestion = generation.suggerer_module(prep, sorted(set(modules + proposes)))
                attente.append({"prep": prep, "suggestion": suggestion})
                st.write(f":material/check: {f.name} : module suggéré « {suggestion['module']} »")
            except (ingest.IngestError, llm.LLMError) as e:
                erreurs += 1
                st.write(f":material/error: {f.name} : {e}")
        statut.update(
            label="Analyse terminée" if not erreurs else f"Analyse terminée, {erreurs} fichier(s) en erreur",
            state="complete" if not erreurs else "error",
            expanded=bool(erreurs),  # en cas d'erreur, le message reste visible
        )
    st.session_state["upload_n"] = st.session_state.get("upload_n", 0) + 1
    if attente and not erreurs:
        st.rerun()


def verifier_import(attente: list) -> None:
    """Étape 2 de l'import : l'étudiant valide ou corrige le module proposé pour chaque fichier."""
    st.subheader("Vérifie le rangement")
    st.caption("Un module est proposé pour chaque fichier d'après son contenu. Corrige si besoin, puis importe.")
    modules = sorted(ingest.list_modules(identifiant()))
    proposes = sorted({a["suggestion"]["module"] for a in attente} - set(modules))
    options = modules + proposes + [NOUVEAU_MODULE]

    choix = []
    for i, item in enumerate(attente):
        s, prep = item["suggestion"], item["prep"]
        with st.container(border=True):
            st.markdown(f":material/picture_as_pdf: **{prep['fichier']}**")
            etiquette = "Module existant" if s["module"] in modules else "Nouveau module"
            st.caption(f":material/lightbulb: {etiquette} proposé : **{s['module']}**. {s['raison']}")
            module = st.selectbox("Module", options, index=options.index(s["module"]), key=f"import_module_{i}")
            if module == NOUVEAU_MODULE:
                module = st.text_input("Nom du nouveau module", key=f"import_nouveau_{i}", max_chars=40)
            choix.append(module)

    importer, annuler = st.columns(2)
    if annuler.button("Annuler", width="stretch"):
        attente.clear()
        st.rerun()
    if importer.button("Importer", type="primary", icon=":material/upload:", width="stretch"):
        bilan, dernier_module = [], None
        with st.status("Import et création de la carte des notions…", expanded=True) as statut:
            for item, module in zip(attente, choix):
                nom = item["prep"]["fichier"]
                try:
                    stats = ingest.enregistrer_pdf(identifiant(), item["prep"], module)
                except ingest.IngestError as e:
                    bilan.append(f":material/error: {nom} : {e}")
                    continue
                dernier_module = stats["module"]
                # Réimport : les fiches faites sur l'ancienne version partent. Nouveau cours : les fiches
                # « pas dans le cours » du module aussi (la notion y est peut-être maintenant).
                nb_fiches = db.supprimer_fiches_fichier(user(), nom)
                nb_fiches += db.supprimer_fiches_module(user(), stats["module"], hors_cours_seulement=True)
                fiches_retirees = f" ; {nb_fiches} ancienne(s) fiche(s) retirée(s), à refaire" if nb_fiches else ""
                try:
                    nouvelles = generation.extraire_notions(identifiant(), stats["module"], nom)
                    bilan.append(f":material/check: {nom} rangé dans **{stats['module']}**, {nouvelles} notion(s) ajoutée(s) à la carte{fiches_retirees}")
                except (llm.LLMError, generation.GenerationError):
                    bilan.append(f":material/check: {nom} rangé dans **{stats['module']}** (carte des notions à relancer plus tard){fiches_retirees}")
                st.write(bilan[-1])
            statut.update(label="Import terminé", state="complete")
        attente.clear()
        st.session_state["bilan_import"] = bilan
        if dernier_module:
            st.session_state["module_actif"] = dernier_module
        st.rerun()


def page_cours() -> None:
    st.header("Mes cours")
    bilan = st.session_state.pop("bilan_import", None)
    if bilan:
        with st.container(border=True):
            st.markdown("**Import terminé**")
            st.markdown("\n\n".join(bilan))

    attente = etat("import").setdefault("en_attente", [])
    if attente:
        verifier_import(attente)
        return

    st.caption(
        f"PDF avec du texte sélectionnable (pas de scan), {ingest.MAX_FILE_MB} Mo et {ingest.MAX_PAGES} pages "
        f"au plus. Jusqu'à {ingest.MAX_FILES} fichiers répartis dans {ingest.MAX_MODULES} modules."
    )
    n = st.session_state.setdefault("upload_n", 0)  # changer la clé vide le sélecteur
    fichiers = st.file_uploader(
        "Ajouter des PDF", type=["pdf"], accept_multiple_files=True, key=f"upload_{n}", label_visibility="collapsed"
    )
    if fichiers and st.button(f"Analyser {len(fichiers)} fichier(s)", type="primary", icon=":material/search:"):
        analyser_fichiers(fichiers)

    modules = ingest.list_modules(identifiant())
    if not modules:
        st.info("Aucun cours pour l'instant. Ajoute tes premiers PDF ci-dessus : ils seront rangés par module.",
                icon=":material/info:")
        return

    st.subheader(f"Mes modules ({len(modules)})")
    for module in sorted(modules):
        fichiers_module = modules[module]
        carte = db.carte_notions(user(), module)
        with st.container(border=True):
            st.markdown(f"**{module}**")
            st.caption(
                f"{len(fichiers_module)} fichier(s), {sum(fichiers_module.values())} passages, "
                f"{len(carte)} notion(s) dans la carte"
            )
            with st.expander("Fichiers", icon=":material/folder:"):
                for nom, nb in sorted(fichiers_module.items()):
                    gauche, droite = st.columns([5, 2], vertical_alignment="center")
                    gauche.markdown(f":material/picture_as_pdf: {nom}")
                    gauche.caption(f"{nb} passages")
                    if droite.button("Supprimer", key=f"del_{nom}", icon=":material/delete:", type="tertiary"):
                        dialog_supprimer(nom, module)
            with st.expander(f"Carte des notions ({len(carte)})", icon=":material/hub:"):
                if carte:
                    for notion in carte:
                        st.markdown(f"**{notion['nom']}**" + (f" : {notion['resume']}" if notion["resume"] else ""))
                        st.caption(", ".join(notion["sources"]))
                else:
                    st.caption("Pas encore de carte pour ce module.")
                libelle = "Recréer la carte" if carte else "Créer la carte"
                if st.button(libelle, key=f"carte_{module}", icon=":material/refresh:", type="tertiary"):
                    with st.spinner("Lecture du plan de chaque fichier…"):
                        try:
                            total = generation.regenerer_carte(identifiant(), module)
                            toast_apres_rerun(f"Carte de {module} : {total} notion(s).")
                        except (llm.LLMError, generation.GenerationError) as e:
                            st.error(str(e), icon=":material/error:")
                            return
                    st.rerun()
            if st.button("Renommer le module", key=f"renommer_{module}", icon=":material/edit:", type="tertiary"):
                dialog_renommer(module)


# ---------------------------------------------------------------------------
# Page « Questions » (assistant : cours avec citations, web sourcé, ou discussion)
# ---------------------------------------------------------------------------
# Origine d'une réponse -> (libellé, couleur, icône). Les anciennes réponses n'ont pas de mode : « cours ».
ORIGINES = {
    "cours": ("D'après ton cours", "blue", ":material/menu_book:"),
    "hors_cours": ("Pas trouvé dans ton cours", "orange", ":material/search_off:"),
    "web": ("D'après le web", "violet", ":material/travel_explore:"),
    "hors_web": ("Rien de fiable trouvé sur le web", "orange", ":material/search_off:"),
    "discussion": ("Réponse générale, sans source", "gray", ":material/forum:"),
}


def origine(res: dict) -> str:
    mode = res.get("mode") or "cours"
    if mode == "cours" and res.get("hors_cours"):
        return "hors_cours"
    if mode == "web" and res.get("hors_web"):
        return "hors_web"
    return mode if mode in ORIGINES else "cours"


def texte_inerte(texte: str) -> str:
    """
    Texte écrit par l'IA (question reformulée, requête web) glissé dans une légende Markdown : entre
    accents graves, c'est du code, où Markdown ne crée jamais de lien ni d'image. Les accents graves
    et retours à la ligne du texte sont retirés pour qu'il ne puisse pas refermer ce code.
    """
    return "`" + " ".join(texte.replace("`", "'").split()) + "`"


def afficher_entete(res: dict) -> None:
    """Avant le texte de la réponse : d'où elle vient, et comment la question a été comprise."""
    libelle, couleur, icone = ORIGINES[origine(res)]
    st.badge(libelle, icon=icone, color=couleur)
    web_utilise = res.get("mode") == "web" and res.get("requete_web")
    if res.get("question_recherche") and res["question_recherche"] != (web_utilise or None):
        st.caption(f":material/manage_search: Compris comme : {texte_inerte(res['question_recherche'])}")
    if web_utilise:  # exactement ce qui a été envoyé à Tavily
        st.caption(f":material/travel_explore: Recherche web : {texte_inerte(res['requete_web'])}")
    if res.get("web_indisponible"):  # explique tout de suite pourquoi la réponse est « sans source »
        raison = res["web_indisponible"]
        st.caption(f":orange[:material/cloud_off:] Recherche web impossible : {raison[0].lower()}{raison[1:]}")


def badge_markdown(cle: str) -> str:
    """Étiquette d'origine écrite en Markdown, pour la glisser dans un texte (légende de l'accueil)."""
    libelle, couleur, icone = ORIGINES[cle]
    return f":{couleur}-badge[{icone} {libelle}]"


def afficher_reponse(res: dict) -> None:
    afficher_entete(res)
    if res["reponse"]:
        st.html(affichage.rendu_sur(res["reponse"]))  # jamais st.markdown : voir affichage.py
    afficher_sources(res)


EXTRAIT_WEB_AFFICHE = 280  # caractères : l'extrait complet est sur la page, via le lien


def afficher_sources(res: dict) -> None:
    if res.get("sans_citation"):
        if res.get("mode") == "web":
            st.warning("Cette réponse ne cite aucun résultat web : vérifie-la.", icon=":material/warning:")
        else:
            st.warning("Cette réponse ne cite aucun passage : vérifie-la dans ton cours.", icon=":material/warning:")
    if res.get("sources"):
        with st.expander(f"Sources du cours ({len(res['sources'])})", icon=":material/menu_book:"):
            for s in res["sources"]:
                module = f"{s['module']} : " if res.get("tous_modules") else ""
                st.markdown(f"**[{s['numero']}]** {module}{s['source']}, page {s['page']}")
                st.caption(texte_extrait(s["texte"]))  # une ligne, sans Markdown ni LaTeX involontaire
    if res.get("sources_web"):
        with st.expander(f"Sources web ({len(res['sources_web'])})", icon=":material/travel_explore:"):
            for s in res["sources_web"]:
                extrait = s["extrait"]
                if len(extrait) > EXTRAIT_WEB_AFFICHE:
                    extrait = extrait[:EXTRAIT_WEB_AFFICHE].rsplit(" ", 1)[0] + "…"
                # Titre et extrait viennent de la page web : échappés, en HTML construit ici. Le seul lien
                # est celui de la vraie source. Le site d'abord : c'est lui qui dit si la source est fiable.
                st.html(
                    f"<div class='reponse-ia'><p><strong>[W{s['numero']}]</strong> "
                    f"{affichage.lien_source(s['titre'], s['url'])}<br><small><strong>{html.escape(s['domaine'])}"
                    f"</strong> · {html.escape(' '.join(extrait.split()))}</small></p></div>"
                )


def demander_web(question: str, requete: str) -> None:
    """Rappel du bouton « Chercher sur le web » : la question sera traitée au prochain affichage."""
    st.session_state["question_web"] = {"id": identifiant(), "question": question, "requete": requete}


# L'assistant propose une recherche web (« chercher sur le web », « une recherche web »…), pas « webinaire ».
PROPOSE_WEB = re.compile(r"chercher sur (le )?web|recherches? (sur (le )?)?web\b", re.IGNORECASE)


def bouton_chercher_web(res: dict, question: str, cle: str) -> None:
    """Sous une réponse sans source (pas dans le cours, ou discussion) : relancer la question sur le web."""
    if not web.web_disponible() or res.get("web_indisponible"):
        return
    pas_dans_le_cours = origine(res) == "hors_cours"
    # En discussion, seulement si l'assistant propose lui-même de vérifier sur le web (fait précis).
    propose = res.get("mode") == "discussion" and PROPOSE_WEB.search(res["reponse"]) is not None
    if not (pas_dans_le_cours or propose):
        return
    limite = assistant.limite_web_atteinte(identifiant())
    if limite:  # personnelle ou de l'app : le message dit laquelle
        st.caption(f":material/block: {limite}")
        return
    restantes = assistant.recherches_web_restantes(identifiant())
    st.button(
        "Chercher sur le web",
        key=cle,
        icon=":material/travel_explore:",
        help=f"Il te reste {web.nb_recherches(restantes)} aujourd'hui.",
        type="secondary" if pas_dans_le_cours else "tertiary",
        on_click=demander_web,
        args=(question, res.get("question_recherche") or question),
    )


INTERVALLE_AFFICHAGE = 0.1  # secondes entre deux mises à jour de la réponse en cours d'écriture


def afficher_en_direct(flux) -> str:
    """
    La réponse au fil de l'eau (streaming) : on réaffiche tout le texte reçu, toujours par
    affichage.rendu_sur (jamais de lien ni d'image), au plus toutes les 0,1 s, puis une dernière fois.
    Une coupure du flux (LLMError) remonte à l'appelant.
    """
    zone, texte, dernier = st.empty(), "", 0.0
    for morceau in flux:
        texte += morceau
        if time.monotonic() - dernier >= INTERVALLE_AFFICHAGE:
            zone.html(affichage.rendu_sur(texte + " ▌"))  # curseur : la réponse continue
            dernier = time.monotonic()
    if texte:
        zone.html(affichage.rendu_sur(texte))
    return texte


def repondre_en_direct(question: str, module: str | None, historique: list[dict], cle_bouton: str,
                       requete_web_forcee: str | None = None) -> dict | None:
    """Affiche la réponse au fil de l'eau. Renvoie le résultat à enregistrer, ou None en cas d'erreur."""
    try:
        with st.spinner("Je réfléchis…"):  # reformulation, recherche dans les cours, choix du mode
            analyse = assistant.analyser(identifiant(), question, module, historique, requete_web_forcee)
        with st.spinner("Recherche sur le web…" if analyse["mode"] == "web" else "Je réfléchis…"):
            prep = assistant.preparer(analyse)
        entete = st.empty()
        with entete.container():
            afficher_entete(prep)
        texte = afficher_en_direct(llm.chat_stream(prep["messages"], temperature=prep["temperature"]))
        res = assistant.finaliser(texte, prep)
    except llm.LLMError as e:
        st.error(str(e), icon=":material/error:")  # rien n'est enregistré : on peut reposer la question
        return None
    if origine(res) != origine(prep):  # ex. « Je ne trouve pas… » : l'étiquette passe à « Pas trouvé dans ton cours »
        with entete.container():
            afficher_entete(res)
    res["tous_modules"] = module is None
    afficher_sources(res)
    bouton_chercher_web(res, question, cle_bouton)
    return res


def choisir_suggestion(cle: str) -> None:
    """Clic sur une suggestion de l'accueil : elle sera posée comme une question au prochain affichage."""
    st.session_state["question_suggeree"] = {"id": identifiant(), "question": st.session_state.get(cle)}
    st.session_state[cle] = None  # la pastille ne reste pas sélectionnée


def afficher_accueil(a_des_cours: bool, module: str | None, cle_module: str) -> None:
    """Conversation vide : ce que l'assistant sait faire, ses étiquettes de source et quelques idées de questions."""
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        if a_des_cours:
            portee = f"le module **{module}**" if module else "**tous tes modules**"
            st.markdown(f"Pose-moi une question sur {portee}, une question de culture générale, "
                        "ou demande-moi un conseil de méthode.")
        else:
            st.markdown("Pose-moi une question de culture générale, ou demande-moi un conseil de méthode.")
        # Légende : les mêmes étiquettes qu'en tête de chaque réponse.
        origines = (["cours"] if a_des_cours else []) + (["web"] if web.web_disponible() else []) + ["discussion"]
        st.markdown(":small[:gray[Chaque réponse dit d'où elle vient :]] " + " ".join(badge_markdown(o) for o in origines))
        idees = []
        if a_des_cours:
            noms = ([n["nom"] for n in db.carte_notions(user(), module)] if module else []) or db.notions(user(), module)
            idees = [f"Explique-moi : {nom}" for nom in noms[:2]]
        idees.append("Comment bien réviser ?")
        cle = f"suggestions_{cle_module}"
        st.pills("Idées de questions", idees, key=cle, on_change=choisir_suggestion, args=(cle,),
                 label_visibility="collapsed")


def page_questions() -> None:
    st.header("Questions")
    st.html(affichage.STYLE)  # tableaux, code et liens des réponses (voir affichage.py)
    a_des_cours = bool(ingest.list_modules(identifiant()))
    if a_des_cours:
        module = selecteur_module("questions", tous=True)
    else:
        module = None
        st.info("Tu n'as pas encore de cours. Ajoute un PDF pour des réponses tirées de ton cours, "
                "avec le fichier et la page.", icon=":material/info:")
        st.page_link(PAGES["cours"], label="Aller à mes cours", icon=":material/folder_open:")
    cle_module = module or ""  # "" = conversation sur tous les modules
    # La conversation est enregistrée : on la retrouve d'une visite et d'un appareil à l'autre.
    historique = db.messages_chat(user(), cle_module)

    # Lu avant d'afficher l'historique (la zone de saisie reste de toute façon en bas de page).
    # Placeholder court : il tient sur une ligne sur téléphone.
    question = st.chat_input("Pose ta question…")
    demande = st.session_state.pop("question_web", None)  # bouton « Chercher sur le web »
    suggestion = st.session_state.pop("question_suggeree", None)  # suggestion de l'accueil
    requete_web_forcee = None
    if not question and demande and demande["id"] == identifiant():
        question, requete_web_forcee = demande["question"], demande["requete"]
    elif not question and suggestion and suggestion["id"] == identifiant() and suggestion["question"]:
        question = suggestion["question"]

    if historique:
        if st.button("Nouvelle conversation", icon=":material/add_comment:", type="tertiary"):
            db.effacer_conversation(user(), cle_module)
            st.rerun()
    elif not question:
        afficher_accueil(a_des_cours, module, cle_module)

    for i, msg in enumerate(historique):
        with st.chat_message(msg["role"], avatar=AVATARS[msg["role"]]):
            if msg["role"] == "user":
                st.markdown(msg["contenu"])
            else:
                res = {"reponse": msg["contenu"], **msg["details"]}
                afficher_reponse(res)
                # Bouton web sous la dernière réponse seulement (pas si une nouvelle réponse arrive dessous).
                if i == len(historique) - 1 and not question:
                    # La question : le dernier message de l'étudiant avant cette réponse (après un clic sur
                    # « Chercher sur le web », deux réponses se suivent pour la même question).
                    precedente = next((m["contenu"] for m in reversed(historique[:i]) if m["role"] == "user"), None)
                    if precedente:
                        bouton_chercher_web(res, precedente, f"web_{cle_module}_{i}")

    if question:
        if not requete_web_forcee:  # « Chercher sur le web » : la question est déjà affichée juste au-dessus
            with st.chat_message("user", avatar=AVATARS["user"]):
                st.markdown(question)
        # Clé du bouton web : la même qu'au prochain affichage, où cette réponse sera la dernière
        # (index len(historique) si on n'ajoute que la réponse, len(historique) + 1 avec la question).
        index = len(historique) if requete_web_forcee else len(historique) + 1
        with st.chat_message("assistant", avatar=AVATARS["assistant"]):
            res = repondre_en_direct(question, module, historique, f"web_{cle_module}_{index}", requete_web_forcee)
        if res is None:
            return
        details = {k: v for k, v in res.items() if k != "reponse"}
        if requete_web_forcee:
            db.ajouter_reponse(user(), cle_module, res["reponse"], details)
        else:
            db.ajouter_echange(user(), cle_module, question, res["reponse"], details)


# ---------------------------------------------------------------------------
# Quiz (utilisé par les pages QCM et Progrès)
# ---------------------------------------------------------------------------
@st.dialog("Signaler cette question")
def dialog_signaler(q: dict) -> None:
    st.caption(q["question"])
    motif = st.radio("Quel est le problème ?", db.MOTIFS_SIGNALEMENT)
    commentaire = st.text_area("Précisions (facultatif)", max_chars=1000)
    if st.button("Envoyer le signalement", type="primary", icon=":material/send:", width="stretch"):
        db.signaler_question(user(), q["id"], motif, commentaire)
        notif.envoyer(notif.message_signalement(q, motif, commentaire, user()[5:13], llm.LLM_MODE))
        toast_apres_rerun("Merci, la question est signalée.")
        st.rerun()


def afficher_quiz(quiz: dict, prefixe: str) -> None:
    """`prefixe` évite les conflits de clés quand une question apparaît sur deux pages."""
    questions = db.get_questions(user(), quiz["ids"])
    if not questions:
        st.info("Aucune question à afficher.", icon=":material/info:")
        return
    demandees = quiz.get("demandees", len(questions))
    if len(questions) < demandees:
        if prefixe.startswith("rev"):
            st.caption(f":material/info: {len(questions)} question(s) à revoir dans ce module, sur {demandees} demandées.")
        else:
            st.warning(
                f"{len(questions)} questions sur {demandees} demandées : le cours ne contient pas assez de matière "
                "différente, ou la limite de l'API gratuite a été atteinte. Relance un QCM pour en avoir d'autres.",
                icon=":material/info:",
            )
    reponses = quiz["reponses"]  # {question_id: (choix, correct)}
    signalees = db.questions_signalees(user())
    bonnes = sum(1 for _, ok in reponses.values() if ok)
    st.progress(
        len(reponses) / len(questions),
        text=f"{len(reponses)} sur {len(questions)} répondues, {bonnes} bonne(s) réponse(s)",
    )

    for i, q in enumerate(questions, start=1):
        with st.container(border=True):
            st.caption(f":material/sell: {q['notion']}")
            st.markdown(f"**{i}. {q['question']}**")

            if q["id"] in reponses:
                choix, ok = reponses[q["id"]]
                for j, texte in enumerate(q["choix"]):
                    if j == q["bonne_reponse"]:
                        st.markdown(f":green[:material/check_circle:] **{texte}**")
                    elif j == choix:
                        st.markdown(f":red[:material/cancel:] {texte}")
                    else:
                        st.markdown(f":gray[:material/radio_button_unchecked:] {texte}")
                if ok:
                    st.success("Bonne réponse.", icon=":material/check:")
                else:
                    st.error("Raté : cette question te sera reposée.", icon=":material/replay:")
                st.markdown(q["explication"])
                with st.expander(f"{q['source']}, page {q['page']}", icon=":material/menu_book:"):
                    st.caption(q["extrait"])
            else:
                choix = st.radio(
                    "Ta réponse",
                    options=range(4),
                    format_func=lambda j, q=q: q["choix"][j],
                    index=None,
                    key=f"{prefixe}_radio_{q['id']}",
                    label_visibility="collapsed",
                )
                if st.button("Valider", key=f"{prefixe}_valider_{q['id']}", type="primary", disabled=choix is None):
                    reponses[q["id"]] = (choix, db.enregistrer_reponse(user(), q["id"], choix))
                    st.rerun()

            if q["id"] in signalees:
                st.caption(":material/flag: Signalée. Elle ne compte plus dans ta progression.")
            elif st.button("Signaler une erreur", key=f"{prefixe}_signal_{q['id']}", icon=":material/flag:", type="tertiary"):
                dialog_signaler(q)

    if len(reponses) == len(questions):
        st.success(f"Terminé : {bonnes} bonne(s) réponse(s) sur {len(questions)}.", icon=":material/flag_check:")


# ---------------------------------------------------------------------------
# Page « QCM » : automatique (adaptatif) ou personnalisé
# ---------------------------------------------------------------------------
def page_qcm() -> None:
    st.header("QCM")
    if sans_cours():
        return
    module = selecteur_module("qcm")
    quizz = etat("quiz")
    quiz = quizz.get(module)

    with st.expander("Nouveau QCM", expanded=not quiz, icon=":material/tune:"):
        mode = st.segmented_control("Mode", ["Automatique", "Personnalisé"], default="Automatique",
                                    required=True, key="mode_qcm")
        if mode == "Automatique":
            st.caption("Le QCM vise en priorité tes notions à retravailler, puis les fragiles et celles jamais travaillées.")
            nb = st.segmented_control("Nombre de questions", [5, 10, 15], default=5, required=True, key="nb_auto")
            lancer = st.button("Générer le QCM", type="primary", icon=":material/play_arrow:", width="stretch")
        else:
            with st.form("form_qcm", border=False):
                tous = "Tout le module"
                source = st.selectbox("Fichier", [tous] + sorted(ingest.list_sources(identifiant(), module)))
                notion = st.text_input("Notion à travailler (facultatif)", placeholder="ex : modulation FM")
                nb = st.segmented_control("Nombre de questions", [5, 10, 15], default=5, required=True)
                lancer = st.form_submit_button("Générer le QCM", type="primary", icon=":material/play_arrow:", width="stretch")

    if lancer:
        with st.spinner("Rédaction des questions à partir de ton cours…"):
            try:
                if mode == "Automatique":
                    ids, cibles = generation.generer_qcm_adaptatif(identifiant(), module, nb)
                else:
                    ids = generation.generer_qcm(identifiant(), None if source == tous else source, notion, nb, module=module)
                    cibles = []
                quizz[module] = quiz = {"ids": ids, "reponses": {}, "cibles": cibles, "demandees": nb}
            except (llm.LLMError, generation.GenerationError) as e:
                st.error(str(e), icon=":material/error:")

    if not quiz:
        st.caption("Le mode automatique choisit les notions pour toi. Le mode personnalisé te laisse choisir un fichier ou une notion.")
        return
    if quiz.get("cibles"):
        with st.container(border=True):
            st.markdown("**Ce QCM cible**")
            st.markdown("\n".join(f"- :material/my_location: **{c['notion']}** : {c['raison']}" for c in quiz["cibles"]))
    afficher_quiz(quiz, prefixe=f"qcm_{module}")


# ---------------------------------------------------------------------------
# Page « Fiches »
# ---------------------------------------------------------------------------
TOUT_LE_MODULE = "Tout le module"  # choix « tous les fichiers » ; enregistré comme source "" en base


def origine_fiche(source: str) -> str:
    return source or TOUT_LE_MODULE


@st.dialog("Supprimer cette fiche ?")
def dialog_supprimer_fiche(ligne: dict) -> None:
    st.write(
        f"La fiche **{ligne['notion']}** ({origine_fiche(ligne['source'])}) sera supprimée. "
        "Tu pourras la recréer, avec un nouvel appel à l'IA."
    )
    annuler, confirmer = st.columns(2)
    if annuler.button("Annuler", width="stretch"):
        st.rerun()
    if confirmer.button("Supprimer", type="primary", icon=":material/delete:", width="stretch"):
        db.supprimer_fiche(user(), ligne["id"])
        toast_apres_rerun(f"Fiche {ligne['notion']} supprimée.")
        st.rerun()


def rediger_fiche(module: str, notion: str, source: str) -> int | None:
    """Appel au LLM puis enregistrement (remplace la fiche existante). Renvoie l'id, ou None en cas d'erreur."""
    with st.spinner("Rédaction de la fiche à partir de ton cours…"):
        try:
            contenu = generation.generer_fiche(identifiant(), notion, source or None, module)
        except (llm.LLMError, generation.GenerationError) as e:
            st.error(str(e), icon=":material/error:")
            return None
    return db.enregistrer_fiche(user(), module, notion, source, contenu)


# Sections d'une fiche : (champ, titre, icône). L'icône aide à repérer chaque partie d'un coup d'œil ;
# les pièges sont en orange, comme un avertissement.
SECTIONS_FICHE = (
    ("points_cles", "Points clés", ":primary[:material/key:]"),
    ("formules", "Formules", ":primary[:material/function:]"),
    ("pieges", "Pièges classiques", ":orange[:material/warning:]"),
)


def texte_extrait(texte: str) -> str:
    """
    Extrait de PDF affiché tel quel : sur une seule ligne, pour que ses retours à la ligne et ses
    soulignements ne soient pas pris pour des titres ou des listes Markdown.
    """
    texte = " ".join(texte.split()).replace("$", r"\$")  # pas de formule LaTeX involontaire
    texte = re.sub(r"^([#>*+-])", r"\\\1", texte)        # titre, citation ou puce en début de ligne
    return re.sub(r"^(\d+)\.(?=\s)", r"\1\\.", texte)  # « 12. » (liste numérotée), mais pas « 1.5 V »


def afficher_fiche(ligne: dict, module: str) -> None:
    """Une fiche enregistrée : contenu sourcé, puis Télécharger / Régénérer / Supprimer."""
    fiche = ligne["contenu"]

    def refs(element: dict) -> str:
        # Renvois vers les sources en gris : visibles sans gêner la lecture.
        return " " + "".join(f":gray[\\[{n}\\]]" for n in element["extraits"])

    with st.container(border=True):
        # Titre = la notion demandée, le même libellé que dans la liste « Mes fiches ».
        st.subheader(ligne["notion"])
        st.caption(
            f":material/folder: {origine_fiche(ligne['source'])} · "
            f"mise à jour le {ligne['maj_le'].astimezone(PARIS):%d/%m/%Y}"  # stockée en UTC
        )
        if not fiche["dans_le_cours"]:
            st.info(prompts.PAS_DANS_LE_COURS, icon=":material/search_off:")
        else:
            if fiche["definition"]:
                st.markdown(":primary[:material/notes:] **Définition**")
                st.markdown(fiche["definition"]["texte"] + refs(fiche["definition"]))
            for champ, titre, icone in SECTIONS_FICHE:
                if fiche[champ]:
                    st.markdown(f"{icone} **{titre}**")
                    st.markdown("\n".join(f"- {e['texte']}{refs(e)}" for e in fiche[champ]))
            if fiche["retires"]:
                st.caption(f":material/filter_alt: {fiche['retires']} élément(s) retiré(s) faute de source dans ton cours.")
            # Même présentation que les sources de la page Questions.
            with st.expander(f"Sources ({len(fiche['extraits'])})", icon=":material/menu_book:"):
                for n, e in enumerate(fiche["extraits"], start=1):
                    st.markdown(f"**[{n}]** {e['source']}, page {e['page']}")
                    st.caption(texte_extrait(e["texte"]))

        # Actions sur une seule ligne, même sur téléphone (st.columns s'empilerait).
        # Télécharger est l'action courante ; Régénérer et Supprimer restent discrètes.
        with st.container(horizontal=True, vertical_alignment="center"):
            if fiche["dans_le_cours"]:
                # Nom du fichier = la notion de l'étudiant, accents gardés (« fiche_Détection_AM.md »).
                nom_fichier = re.sub(r"[^\w-]+", "_", ligne["notion"]).strip("_")
                st.download_button(
                    "Télécharger",
                    data=generation.fiche_markdown({**fiche, "notion": ligne["notion"]}),
                    file_name=f"fiche_{nom_fichier}.md",
                    mime="text/markdown",
                    icon=":material/download:",
                    help="Fichier texte (.md), sources comprises.",
                    key=f"dl_{ligne['id']}",
                )
            regenerer = st.button("Régénérer", key=f"regen_{ligne['id']}", icon=":material/refresh:", type="tertiary",
                                  help="Refait la fiche avec l'IA et remplace celle-ci.")
            if st.button("Supprimer", key=f"suppr_fiche_{ligne['id']}", icon=":material/delete:", type="tertiary"):
                dialog_supprimer_fiche(ligne)
        # Hors de la ligne de boutons : le message d'attente ou d'erreur prend toute la largeur.
        if regenerer and rediger_fiche(module, ligne["notion"], ligne["source"]) is not None:
            toast_apres_rerun(f"Fiche {ligne['notion']} régénérée.")
            st.rerun()


def page_fiches() -> None:
    st.header("Fiches")
    if sans_cours():
        return
    module = selecteur_module("fiches")
    # Clés propres à l'utilisateur : après « Changer de pseudo », rien ne suit l'ancien.
    cle_notion = f"fiche_notion_{user()}_{module}"
    cle_pastilles = f"fiche_pastilles_{user()}_{module}"
    cle_choix = f"fiche_choisie_{user()}_{module}"  # id de la fiche affichée dans « Mes fiches »

    mes_fiches = db.fiches(user(), module)

    # Création repliée dès qu'on a des fiches (comme « Nouveau QCM ») : sur téléphone, on arrive
    # directement sur ses fiches au lieu de devoir dépasser le formulaire. L'état de départ est
    # mémorisé : s'il changeait après la 1re fiche, le formulaire se refermerait au clic suivant.
    ouvert = st.session_state.setdefault(f"fiche_form_ouvert_{user()}_{module}", not mes_fiches)
    with st.expander("Nouvelle fiche", expanded=ouvert, icon=":material/add:"):
        noms = [n["nom"] for n in db.carte_notions(user(), module)] or db.notions(user(), module)
        if noms:
            def recopier_notion() -> None:
                st.session_state[cle_notion] = st.session_state.get(cle_pastilles) or ""

            st.pills("Notions du module", noms, key=cle_pastilles, on_change=recopier_notion)
        notion = st.text_input("Notion", key=cle_notion, placeholder="Choisis une notion ci-dessus ou écris-en une")
        choix_source = st.selectbox("Chercher dans", [TOUT_LE_MODULE] + sorted(ingest.list_sources(identifiant(), module)))
        source = "" if choix_source == TOUT_LE_MODULE else choix_source
        if st.button("Créer la fiche", type="primary", icon=":material/description:", width="stretch"):
            existante = db.trouver_fiche(user(), module, notion, source)
            if existante:  # déjà faite : on la montre sans rappeler l'IA
                st.session_state[cle_choix] = existante["id"]
                st.info(
                    f"Tu as déjà une fiche sur **{existante['notion']}** ({origine_fiche(source)}) : elle est "
                    "affichée ci-dessous, sans nouvel appel à l'IA. Clique **Régénérer** pour la refaire.",
                    icon=":material/bookmark:",
                )
            else:
                fiche_id = rediger_fiche(module, notion, source)
                if fiche_id is not None:
                    st.session_state[cle_choix] = fiche_id
                    mes_fiches = db.fiches(user(), module)  # la nouvelle fiche rejoint la liste

    st.subheader(f"Mes fiches ({len(mes_fiches)})")
    if not mes_fiches:
        st.caption(
            "Aucune fiche pour ce module. Crée-en une avec « Nouvelle fiche » : "
            "tu la retrouveras ici à chaque visite, sur tous tes appareils."
        )
        return
    par_id = {f["id"]: f for f in mes_fiches}
    if st.session_state.get(cle_choix) not in par_id:  # fiche supprimée entre-temps : on revient à la plus récente
        st.session_state.pop(cle_choix, None)
    if len(par_id) > 1:  # une seule fiche : pas besoin de liste de choix
        choisie = st.selectbox(
            "Fiche affichée",
            list(par_id),
            format_func=lambda i: f"{par_id[i]['notion']} · {origine_fiche(par_id[i]['source'])}",
            key=cle_choix,
            label_visibility="collapsed",
        )
    else:
        choisie = next(iter(par_id))
    afficher_fiche(par_id[choisie], module)


# ---------------------------------------------------------------------------
# Page « Progrès » (maîtrise par notion + révision des erreurs)
# ---------------------------------------------------------------------------
def page_progres() -> None:
    st.header("Progrès")
    if sans_cours():
        return
    module = selecteur_module("progres")
    lignes = db.maitrise_par_notion(user(), module)
    if not lignes:
        st.info("Réponds à quelques QCM de ce module : ta maîtrise s'affichera ici, notion par notion.",
                icon=":material/info:")
        st.page_link(PAGES["qcm"], label="Faire un QCM", icon=":material/quiz:")
        return

    total_rep = sum(l["reponses_recentes"] for l in lignes)
    total_ok = sum(round(l["taux"] * l["reponses_recentes"]) for l in lignes if l["taux"] is not None)
    travaillees = sum(1 for l in lignes if l["questions"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Notions travaillées", f"{travaillees} / {len(lignes)}", border=True)
    c2.metric("Réussite récente", f"{100 * total_ok / total_rep:.0f} %" if total_rep else "–", border=True)
    c3.metric("À consolider", sum(l["a_revoir"] for l in lignes), border=True,
              help="Questions pas encore réussies deux fois de suite (boîtes 1 et 2).")

    st.subheader("Par notion")
    st.caption(
        f"Calculé sur tes {db.FENETRE} dernières réponses par notion : maîtrisé à partir de "
        f"{db.SEUIL_VERT:.0%}, fragile à partir de {db.SEUIL_ORANGE:.0%}. Le QCM automatique "
        "vise d'abord les notions du haut de la liste."
    )
    for l in lignes:
        libelle, couleur, icone = NIVEAUX[l["niveau"]]
        with st.container(border=True):
            gauche, droite = st.columns([3, 2], vertical_alignment="center")
            gauche.markdown(f"**{l['notion']}**")
            with droite:
                st.badge(libelle, color=couleur, icon=icone)
            if l["taux"] is not None:
                st.progress(l["taux"], text=f"{l['taux']:.0%} de réussite sur {l['reponses_recentes']} réponse(s)")
            if l["questions"]:
                st.caption(f"{l['questions']} question(s), dont {l['a_revoir']} à consolider")
            else:
                st.caption("Repérée dans la carte des notions, pas encore de question.")

    st.subheader("Revoir mes erreurs")
    a_travailler = [l["notion"] for l in lignes if l["a_revoir"]]
    if not a_travailler:
        st.success("Rien à revoir dans ce module pour l'instant.", icon=":material/celebration:")
        return
    st.caption("Les questions ratées reviennent en premier, puis celles réussies une seule fois.")
    with st.form("form_revision"):
        toutes = "Toutes les notions"
        notion = st.selectbox("Notion", [toutes] + a_travailler)
        nb = st.segmented_control("Nombre de questions", [5, 10, 15], default=5, required=True)
        lancer = st.form_submit_button("Lancer la révision", type="primary", icon=":material/replay:", width="stretch")
    revisions = etat("revision")
    if lancer:
        ids = db.questions_a_revoir(user(), nb, None if notion == toutes else notion, module)
        revisions[module] = {"ids": ids, "reponses": {}, "demandees": nb}
    if revisions.get(module):
        afficher_quiz(revisions[module], prefixe=f"rev_{module}")


# ---------------------------------------------------------------------------
# Page « Avis »
# ---------------------------------------------------------------------------
def page_avis() -> None:
    st.header("Ton avis")
    st.caption(
        "Un bug, une idée, une remarque : tout est utile. Pour une question de QCM fausse, "
        "utilise plutôt le bouton « Signaler une erreur » sous la question."
    )
    n = st.session_state.setdefault("avis_n", 0)  # changer les clés vide le formulaire après envoi
    categorie = st.segmented_control(
        "Type", ["Bug", "Idée", "Avis général"], default="Avis général", required=True, key=f"avis_cat_{n}"
    )
    st.markdown("Ta note pour l'app")
    etoiles = st.feedback("stars", key=f"avis_note_{n}")
    message = st.text_area(
        "Ton message", max_chars=2000, key=f"avis_msg_{n}",
        placeholder="Pour un bug : ce que tu faisais, ce qui s'est passé, ce que tu attendais.",
    )
    if st.button("Envoyer", type="primary", icon=":material/send:"):
        if len(message.strip()) < 3:
            st.error("Écris au moins quelques mots avant d'envoyer.", icon=":material/edit:")
            return
        note = etoiles + 1 if etoiles is not None else None
        db.ajouter_feedback(user(), categorie, note, message, llm.LLM_MODE)
        notif.envoyer(notif.message_avis(categorie, note or 0, message, user()[5:13], llm.LLM_MODE))
        st.session_state["avis_n"] = n + 1
        toast_apres_rerun("Merci pour ton retour.")
        st.rerun()


# ---------------------------------------------------------------------------
# Page « Admin » (visible seulement après saisie de ADMIN_PASSWORD)
# ---------------------------------------------------------------------------
def en_csv(lignes: list[dict]) -> bytes:
    sortie = io.StringIO()
    ecrivain = csv.DictWriter(sortie, fieldnames=list(lignes[0].keys()), delimiter=";")
    ecrivain.writeheader()
    ecrivain.writerows(lignes)
    return sortie.getvalue().encode("utf-8-sig")  # utf-8-sig : accents corrects dans Excel


def page_admin() -> None:
    st.header("Retours des testeurs")
    stats = db.statistiques()
    c1, c2, c3 = st.columns(3)
    c1.metric("Testeurs actifs", stats["testeurs"], border=True,
              help=f"{db.nb_comptes()} compte(s) Google créé(s)." if CONNEXION_GOOGLE else None)
    c2.metric("Réponses QCM", stats["reponses"], border=True)
    c3.metric("Réussite moyenne", f"{stats['reussite']:.0f} %" if stats["reussite"] is not None else "–", border=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Questions créées", stats["questions"], border=True)
    c2.metric("Signalements", stats["signalements"], border=True)
    c3.metric("Avis", stats["feedback"], border=True)
    st.caption(
        ":material/database: Données conservées dans Supabase."
        + (" Chaque signalement et avis est aussi copié sur ton Telegram." if notif.actif() else "")
    )
    for titre, lignes, fichier in (
        ("Questions signalées", db.export_signalements(), "signalements.csv"),
        ("Avis et bugs", db.export_feedback(), "avis.csv"),
    ):
        st.subheader(f"{titre} ({len(lignes)})")
        if lignes:
            st.dataframe(lignes, hide_index=True, width="stretch")
            st.download_button(f"Télécharger {fichier}", data=en_csv(lignes), file_name=fichier,
                               mime="text/csv", icon=":material/download:", key=fichier)
        else:
            st.caption("Rien pour l'instant.")


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
PAGES = {
    "cours": st.Page(page_cours, title="Cours", icon=":material/folder_open:", url_path="cours", default=True),
    "questions": st.Page(page_questions, title="Questions", icon=":material/forum:", url_path="questions"),
    "qcm": st.Page(page_qcm, title="QCM", icon=":material/quiz:", url_path="qcm"),
    "fiches": st.Page(page_fiches, title="Fiches", icon=":material/description:", url_path="fiches"),
    "progres": st.Page(page_progres, title="Progrès", icon=":material/insights:", url_path="progres"),
    "avis": st.Page(page_avis, title="Avis", icon=":material/rate_review:", url_path="avis"),
}
PAGE_ADMIN = st.Page(page_admin, title="Admin", icon=":material/admin_panel_settings:", url_path="admin")
PAGE_CONNEXION = st.Page(page_connexion, title="Connexion", icon=":material/login:", url_path="connexion", default=True)

try:
    storage.verifier()  # base injoignable : message clair plutôt qu'une erreur au milieu d'une page
except storage.StorageError as e:
    st.error(str(e), icon=":material/cloud_off:")
    st.stop()

manquantes = auth.cles_auth_manquantes()
if manquantes:  # section [auth] présente mais incomplète : on le dit plutôt que de planter
    st.error(
        "Connexion Google mal configurée : dans les secrets, la section `[auth]` doit contenir "
        + ", ".join(f"`{cle}`" for cle in manquantes)
        + ". Vérifie aussi que `[auth]` est placée après toutes les autres lignes.",
        icon=":material/key_off:",
    )
    st.stop()

c = compte()
if c and st.session_state.get("compte_enregistre") != c["id"]:
    db.enregistrer_compte(c["id"], c["email"], c["nom"])  # une fois par session
    st.session_state["compte_enregistre"] = c["id"]

barre_laterale()
afficher_toast_en_attente()
admin = [PAGE_ADMIN] if st.session_state.get("admin") else []
if identifiant():
    navigation = st.navigation(list(PAGES.values()) + admin, position="top")
else:
    navigation = st.navigation([PAGE_CONNEXION] + admin, position="top" if admin else "hidden")
navigation.run()
