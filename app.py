"""
app.py — Interface Streamlit du coach de révision.

Lancement : streamlit run app.py

Organisation :
  - Écran de connexion : choix du pseudo + information claire sur les données.
  - Navigation en haut (repliée en menu sur téléphone) : Cours, Questions, QCM,
    Fiches, Progrès, Avis, et Admin si le mot de passe ADMIN_PASSWORD est saisi.
  - Les cours sont rangés par module ; chaque page travaille sur le module choisi.
  - Thème clair / sombre / système : menu ⋮ > Settings (couleurs dans .streamlit/config.toml).
Uniquement des composants Streamlit natifs et des icônes Material : pas de CSS maison.
"""

import csv
import hmac
import io
import os
import re

import streamlit as st

import db
import generation
import ingest
import llm
import notif
import prompts
import rag

st.set_page_config(page_title="Coach de révision", page_icon=":material/school:", layout="centered")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
EN_LIGNE = llm.LLM_MODE == "api"
TOUS_LES_MODULES = "Tous les modules"
NOUVEAU_MODULE = "Nouveau module…"

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
def pseudo() -> str:
    return st.session_state.get("pseudo_valide", "")


def user() -> str:
    return ingest.user_key(pseudo())


def etat(nom: str) -> dict:
    """Dictionnaire de session propre au pseudo courant (historique, quiz…)."""
    return st.session_state.setdefault(nom, {}).setdefault(pseudo(), {})


def toast_apres_rerun(message: str) -> None:
    """Un st.toast juste avant st.rerun() serait perdu : on le garde pour le prochain affichage."""
    st.session_state["_toast"] = message


def afficher_toast_en_attente() -> None:
    message = st.session_state.pop("_toast", None)
    if message:
        st.toast(message, icon=":material/check_circle:")


def sans_cours() -> bool:
    """Affiche un état vide explicite si aucun cours n'est importé."""
    if ingest.list_modules(pseudo()):
        return False
    st.info("Ajoute d'abord un cours en PDF dans la page **Cours**.", icon=":material/info:")
    st.page_link(PAGES["cours"], label="Aller à mes cours", icon=":material/folder_open:")
    return True


def selecteur_module(cle: str, tous: bool = False) -> str | None:
    """
    Choix du module de travail, partagé entre les pages (QCM, Fiches, Progrès…).
    Avec tous=True, propose aussi « Tous les modules » (renvoie alors None).
    """
    modules = sorted(ingest.list_modules(pseudo()))
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
        lignes += [
            ":material/cloud_upload: Les extraits de tes cours sont envoyés à **Groq** (États-Unis) pour générer les réponses.",
            ":material/history: Version de test : tes cours et tes scores peuvent être **effacés à tout moment**.",
            ":material/block: N'importe **pas de document confidentiel**.",
        ]
    else:
        lignes.append(":material/computer: Mode local : tes cours restent sur cet ordinateur.")
    lignes.append(
        ":material/badge: Ton pseudo sépare tes cours de ceux des autres, mais ce n'est pas un mot de passe : "
        "choisis-en un peu courant."
    )
    lignes.append(
        ":material/forum: Tes réponses aux QCM, tes signalements et tes avis sont enregistrés pour améliorer l'app"
        + (" et transmis à son créateur." if notif.actif() else ".")
    )
    return "\n\n".join(lignes)


# ---------------------------------------------------------------------------
# Connexion (choix du pseudo)
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
    with st.form("connexion", border=False):
        saisie = st.text_input("Choisis un pseudo", placeholder="ex : alvin-m1", max_chars=30)
        compris = st.checkbox("J'ai lu comment mes données sont utilisées")
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
def barre_laterale() -> None:
    with st.sidebar:
        if pseudo():
            st.markdown(f":material/account_circle: Connecté en tant que **{pseudo()}**")
            if st.button("Changer de pseudo", icon=":material/logout:", type="tertiary"):
                st.session_state.pop("pseudo_valide", None)
                st.session_state.pop("module_actif", None)
                st.rerun()
            with st.expander("Tes données", icon=":material/shield:"):
                st.markdown(texte_donnees())
                st.caption(f"Moteur : {llm.describe()}")
        if ADMIN_PASSWORD:
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
    st.write(f"**{nom}** sera retiré du module {module}. Les questions de QCM déjà créées restent dans ta progression.")
    annuler, confirmer = st.columns(2)
    if annuler.button("Annuler", width="stretch"):
        st.rerun()
    if confirmer.button("Supprimer", type="primary", icon=":material/delete:", width="stretch"):
        ingest.delete_source(pseudo(), nom)
        if module not in ingest.list_modules(pseudo()):  # dernier fichier du module : on retire sa carte
            db.supprimer_carte(user(), module)
        toast_apres_rerun(f"{nom} supprimé.")
        st.rerun()


@st.dialog("Renommer le module")
def dialog_renommer(module: str) -> None:
    st.caption("Si tu choisis le nom d'un module existant, les deux modules sont fusionnés.")
    nouveau = st.text_input("Nouveau nom", value=module, max_chars=40)
    if st.button("Renommer", type="primary", icon=":material/edit:", width="stretch"):
        try:
            final = ingest.renommer_module(pseudo(), module, nouveau)
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
    modules = sorted(ingest.list_modules(pseudo()))
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
        )
    st.session_state["upload_n"] = st.session_state.get("upload_n", 0) + 1
    if attente and not erreurs:
        st.rerun()


def verifier_import(attente: list) -> None:
    """Étape 2 de l'import : l'étudiant valide ou corrige le module proposé pour chaque fichier."""
    st.subheader("Vérifie le rangement")
    st.caption("Un module est proposé pour chaque fichier d'après son contenu. Corrige si besoin, puis importe.")
    modules = sorted(ingest.list_modules(pseudo()))
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
                    stats = ingest.enregistrer_pdf(pseudo(), item["prep"], module)
                except ingest.IngestError as e:
                    bilan.append(f":material/error: {nom} : {e}")
                    continue
                dernier_module = stats["module"]
                try:
                    nouvelles = generation.extraire_notions(pseudo(), stats["module"], nom)
                    bilan.append(f":material/check: {nom} rangé dans **{stats['module']}**, {nouvelles} notion(s) ajoutée(s) à la carte")
                except (llm.LLMError, generation.GenerationError):
                    bilan.append(f":material/check: {nom} rangé dans **{stats['module']}** (carte des notions à relancer plus tard)")
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

    modules = ingest.list_modules(pseudo())
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
                            total = generation.regenerer_carte(pseudo(), module)
                            toast_apres_rerun(f"Carte de {module} : {total} notion(s).")
                        except (llm.LLMError, generation.GenerationError) as e:
                            st.error(str(e), icon=":material/error:")
                            return
                    st.rerun()
            if st.button("Renommer le module", key=f"renommer_{module}", icon=":material/edit:", type="tertiary"):
                dialog_renommer(module)


# ---------------------------------------------------------------------------
# Page « Questions » (chat avec citations)
# ---------------------------------------------------------------------------
def afficher_reponse(res: dict) -> None:
    st.markdown(res["reponse"])
    if res.get("sans_citation"):
        st.warning("Cette réponse ne cite aucun passage : vérifie-la dans ton cours.", icon=":material/warning:")
    if res.get("sources"):
        with st.expander(f"Sources ({len(res['sources'])})", icon=":material/menu_book:"):
            for s in res["sources"]:
                module = f"{s['module']} : " if res.get("tous_modules") else ""
                st.markdown(f"**[{s['numero']}]** {module}{s['source']}, page {s['page']}")
                st.caption(s["texte"])


def page_questions() -> None:
    st.header("Questions")
    if sans_cours():
        return
    module = selecteur_module("questions", tous=True)
    historique = etat("historique").setdefault(module or TOUS_LES_MODULES, [])

    if historique:
        if st.button("Nouvelle conversation", icon=":material/add_comment:", type="tertiary"):
            historique.clear()
            st.rerun()
    elif module:
        st.caption(f"Pose une question sur le module {module}. Chaque réponse cite le fichier et la page utilisés.")
    else:
        st.caption("La recherche porte sur tous tes modules. Chaque source indique son module, son fichier et sa page.")

    for msg in historique:
        with st.chat_message(msg["role"], avatar=AVATARS[msg["role"]]):
            if msg["role"] == "user":
                st.markdown(msg["contenu"])
            else:
                afficher_reponse(msg["contenu"])

    question = st.chat_input("Ta question sur le cours")
    if question:
        historique.append({"role": "user", "contenu": question})
        with st.chat_message("user", avatar=AVATARS["user"]):
            st.markdown(question)
        with st.chat_message("assistant", avatar=AVATARS["assistant"]):
            with st.spinner("Recherche dans tes cours…"):
                try:
                    res = rag.repondre(pseudo(), question, module)
                    res["tous_modules"] = module is None
                except llm.LLMError as e:
                    res = {"reponse": f":material/error: {e}", "sources": []}
            afficher_reponse(res)
        historique.append({"role": "assistant", "contenu": res})


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
            nb = st.segmented_control("Nombre de questions", [3, 5, 8], default=5, required=True, key="nb_auto")
            lancer = st.button("Générer le QCM", type="primary", icon=":material/play_arrow:", width="stretch")
        else:
            with st.form("form_qcm", border=False):
                tous = "Tout le module"
                source = st.selectbox("Fichier", [tous] + sorted(ingest.list_sources(pseudo(), module)))
                notion = st.text_input("Notion à travailler (facultatif)", placeholder="ex : modulation FM")
                nb = st.segmented_control("Nombre de questions", [3, 5, 8], default=5, required=True)
                lancer = st.form_submit_button("Générer le QCM", type="primary", icon=":material/play_arrow:", width="stretch")

    if lancer:
        with st.spinner("Rédaction des questions à partir de ton cours…"):
            try:
                if mode == "Automatique":
                    ids, cibles = generation.generer_qcm_adaptatif(pseudo(), module, nb)
                else:
                    ids = generation.generer_qcm(pseudo(), None if source == tous else source, notion, nb, module=module)
                    cibles = []
                quizz[module] = quiz = {"ids": ids, "reponses": {}, "cibles": cibles}
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
def afficher_fiche(fiche: dict, cle: str) -> None:
    def refs(element: dict) -> str:
        return " " + "".join(f"[{n}]" for n in element["extraits"])

    with st.container(border=True):
        st.subheader(fiche["notion"])
        if not fiche["dans_le_cours"]:
            st.info(prompts.PAS_DANS_LE_COURS, icon=":material/search_off:")
            return
        if fiche["definition"]:
            st.markdown("**Définition**")
            st.markdown(fiche["definition"]["texte"] + refs(fiche["definition"]))
        for champ, titre in (("points_cles", "Points clés"), ("formules", "Formules"), ("pieges", "Pièges classiques")):
            if fiche[champ]:
                st.markdown(f"**{titre}**")
                st.markdown("\n".join(f"- {e['texte']}{refs(e)}" for e in fiche[champ]))
        if fiche["retires"]:
            st.caption(f":material/filter_alt: {fiche['retires']} élément(s) retiré(s) faute de source dans ton cours.")

        with st.expander("Sources", icon=":material/menu_book:"):
            for n, e in enumerate(fiche["extraits"], start=1):
                st.markdown(f"**[{n}]** {e['source']}, page {e['page']}")
                st.caption(e["texte"])
        st.download_button(
            "Télécharger la fiche",
            data=generation.fiche_markdown(fiche),
            file_name=f"fiche_{re.sub(r'[^A-Za-z0-9]+', '_', fiche['notion'])}.md",
            mime="text/markdown",
            icon=":material/download:",
            key=f"dl_{cle}",
        )


def page_fiches() -> None:
    st.header("Fiches")
    if sans_cours():
        return
    module = selecteur_module("fiches")
    cle_notion = f"fiche_notion_{module}"
    cle_pastilles = f"fiche_pastilles_{module}"

    noms = [n["nom"] for n in db.carte_notions(user(), module)] or db.notions(user(), module)
    if noms:
        def recopier_notion() -> None:
            st.session_state[cle_notion] = st.session_state.get(cle_pastilles) or ""

        st.pills("Notions du module", noms, key=cle_pastilles, on_change=recopier_notion)
    notion = st.text_input("Notion", key=cle_notion, placeholder="Choisis une notion ci-dessus ou écris-en une")
    tous = "Tout le module"
    source = st.selectbox("Chercher dans", [tous] + sorted(ingest.list_sources(pseudo(), module)))
    lancer = st.button("Créer la fiche", type="primary", icon=":material/description:", width="stretch")

    fiches = etat("fiches").setdefault(module, [])
    if lancer:
        with st.spinner("Rédaction de la fiche à partir de ton cours…"):
            try:
                fiches.insert(0, generation.generer_fiche(pseudo(), notion, None if source == tous else source, module))
            except (llm.LLMError, generation.GenerationError) as e:
                st.error(str(e), icon=":material/error:")
    for i, fiche in enumerate(fiches):
        afficher_fiche(fiche, f"{module}_{i}")


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
        revisions[module] = {"ids": ids, "reponses": {}}
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
    c1.metric("Testeurs", stats["testeurs"], border=True)
    c2.metric("Réponses QCM", stats["reponses"], border=True)
    c3.metric("Réussite moyenne", f"{stats['reussite']:.0f} %" if stats["reussite"] is not None else "–", border=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Questions créées", stats["questions"], border=True)
    c2.metric("Signalements", stats["signalements"], border=True)
    c3.metric("Avis", stats["feedback"], border=True)
    if notif.actif():
        st.success("Chaque signalement et chaque avis est aussi copié sur ton Telegram.", icon=":material/send:")
    else:
        st.warning(
            "Hébergement éphémère : ces données peuvent disparaître au redémarrage. Configure Telegram "
            "(TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID) ou télécharge régulièrement les CSV.",
            icon=":material/warning:",
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

barre_laterale()
afficher_toast_en_attente()
admin = [PAGE_ADMIN] if st.session_state.get("admin") else []
if pseudo():
    navigation = st.navigation(list(PAGES.values()) + admin, position="top")
else:
    navigation = st.navigation([PAGE_CONNEXION] + admin, position="top" if admin else "hidden")
navigation.run()
