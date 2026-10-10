"""
generation.py — Sorties structurées du LLM : QCM (personnalisés ou adaptatifs), fiches de
révision, rangement automatique des PDF dans un module et carte des notions.

Principe : le LLM répond en JSON, pydantic vérifie la forme (4 choix, indice valide…),
llm.chat_json réessaie si le JSON est invalide, puis on vérifie le fond nous-mêmes
(la question cite-t-elle un extrait qui existe ?).
"""

import random
import re

from pydantic import BaseModel, Field, field_validator

import db
import ingest
import llm
import prompts

NB_EXTRAITS_QCM = 6
LONGUEUR_MIN_EXTRAIT = 250  # on évite les slides quasi vides pour générer des questions


class GenerationError(RuntimeError):
    """Erreur lisible, affichable dans l'interface."""


# ---------------------------------------------------------------------------
# Schémas pydantic : la forme exacte attendue du JSON
# ---------------------------------------------------------------------------
class QuestionQCM(BaseModel):
    notion: str = Field(min_length=2, max_length=60)
    question: str = Field(min_length=10)
    choix: list[str] = Field(min_length=4, max_length=4)
    bonne_reponse: int = Field(ge=0, le=3)
    explication: str = Field(min_length=10)
    extrait: int = Field(ge=1)

    @field_validator("choix")
    @classmethod
    def choix_propres(cls, choix: list[str]) -> list[str]:
        # Retire les préfixes "A) ", "b. ", "3 - " que les modèles ajoutent souvent.
        nettoyes = [re.sub(r"^\s*([A-Da-d1-4])\s*[).:-]\s+", "", c).strip() for c in choix]
        if any(not c for c in nettoyes):
            raise ValueError("un choix est vide")
        if len({c.lower() for c in nettoyes}) < 4:
            raise ValueError("les 4 choix doivent être différents")
        return nettoyes

    @field_validator("notion")
    @classmethod
    def notion_propre(cls, notion: str) -> str:
        notion = notion.strip().rstrip(".")
        return notion[:1].upper() + notion[1:]


class LotQCM(BaseModel):
    questions: list[QuestionQCM] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Choix des extraits
# ---------------------------------------------------------------------------
def choisir_extraits(pseudo: str, source: str | None, notion: str, k: int, module: str | None = None) -> list[dict]:
    """
    Avec une notion : les passages les plus proches de cette notion (recherche vectorielle).
    Sans notion : des passages tirés au hasard, pour couvrir tout le cours au fil des QCM.
    """
    if notion:
        extraits = ingest.search(pseudo, notion, k=k, source=source, module=module)
    else:
        tous = ingest.get_chunks(pseudo, source, module)
        riches = [c for c in tous if len(c["texte"]) >= LONGUEUR_MIN_EXTRAIT] or tous
        extraits = random.sample(riches, min(k, len(riches)))
    if not extraits:
        raise GenerationError("Aucun passage de cours disponible. Importe d'abord un PDF.")
    return extraits


# ---------------------------------------------------------------------------
# QCM adaptatif : quelles notions viser en priorité ?
# ---------------------------------------------------------------------------
PRIORITE = {"a_retravailler": 0, "fragile": 1, "jamais": 2, "non_teste": 3, "maitrise": 4}


def _raison(ligne: dict) -> str:
    if ligne["niveau"] == "a_retravailler":
        return f"à retravailler ({ligne['taux']:.0%} de réussite)"
    if ligne["niveau"] == "fragile":
        return f"fragile ({ligne['taux']:.0%} de réussite)"
    if ligne["niveau"] == "jamais":
        return "jamais travaillée"
    if ligne["niveau"] == "non_teste":
        return "questions pas encore répondues"
    return "maîtrisée, pour l'entretenir"


def cibles_adaptatives(pseudo: str, module: str, nb_cibles: int = 3) -> list[dict]:
    """
    Choisit les notions à travailler : d'abord celles à retravailler, puis les fragiles,
    puis celles jamais travaillées (tirées au hasard pour varier), puis le reste.
    Renvoie [{"notion", "raison"}].
    """
    lignes = db.maitrise_par_notion(ingest.user_key(pseudo), module)
    random.shuffle(lignes)  # le tri est stable : le hasard ne départage que les ex æquo
    lignes.sort(key=lambda l: (PRIORITE[l["niveau"]], l["taux"] if l["taux"] is not None else 0))
    return [{"notion": l["notion"], "raison": _raison(l)} for l in lignes[:nb_cibles]]


def _extraits_pour(pseudo: str, module: str, notions: list[str], par_notion: int = 2) -> list[dict]:
    """Les passages les plus proches de chaque notion ciblée, sans doublon."""
    vus, extraits = set(), []
    for notion in notions:
        for e in ingest.search(pseudo, notion, k=par_notion + 1, module=module):
            cle = (e["source"], e["page"], e["texte"][:60])
            if cle not in vus and sum(1 for x in extraits if x.get("_pour") == notion) < par_notion:
                vus.add(cle)
                extraits.append({**e, "_pour": notion})
    return extraits


# ---------------------------------------------------------------------------
# QCM
# ---------------------------------------------------------------------------
def _rediger_qcm(
    pseudo: str, extraits: list[dict], nb: int, notion: str, module: str | None, notions_imposees: list[str] | None = None
) -> list[dict]:
    """Demande le QCM au LLM, vérifie chaque question et la rattache à son extrait source."""
    user = ingest.user_key(pseudo)
    lot = llm.chat_json(
        [
            {"role": "system", "content": prompts.SYSTEME_QCM},
            {
                "role": "user",
                "content": prompts.message_qcm(extraits, nb, notion, db.notions(user, module), notions_imposees),
            },
        ],
        schema=LotQCM,
        temperature=0.5,  # un peu de variété dans les questions
    )
    # Pour ranger les questions sous les noms exacts des notions ciblées (casse comprise).
    noms_imposes = {n.lower(): n for n in (notions_imposees or [])}

    questions = []
    for q in lot.questions[:nb]:
        if q.extrait > len(extraits):
            continue  # cite un extrait qui n'existe pas : on ne peut pas sourcer, on jette
        src = extraits[q.extrait - 1]

        # On mélange les choix : les LLM mettent trop souvent la bonne réponse en premier.
        ordre = random.sample(range(4), 4)
        questions.append(
            {
                "module": src["module"],
                "notion": noms_imposes.get(q.notion.lower(), q.notion),
                "question": q.question,
                "choix": [q.choix[i] for i in ordre],
                "bonne_reponse": ordre.index(q.bonne_reponse),
                "explication": q.explication,
                "source": src["source"],
                "page": src["page"],
                "extrait": src["texte"],
            }
        )
    if not questions:
        raise GenerationError("Le modèle n'a produit aucune question exploitable. Réessaie.")
    return questions


def generer_qcm(
    pseudo: str, source: str | None, notion: str, nb: int, enregistrer: bool = True, module: str | None = None
) -> list:
    """
    QCM « personnalisé » : sur un fichier et/ou une notion choisis par l'étudiant.
    Si enregistrer=True, sauve les questions et renvoie leurs ids ; sinon renvoie les questions.
    """
    notion = notion.strip()
    extraits = choisir_extraits(pseudo, source, notion, NB_EXTRAITS_QCM, module)
    questions = _rediger_qcm(pseudo, extraits, nb, notion, module)
    return db.ajouter_questions(ingest.user_key(pseudo), questions) if enregistrer else questions


def generer_qcm_adaptatif(pseudo: str, module: str, nb: int, enregistrer: bool = True) -> tuple[list, list[dict]]:
    """
    QCM « automatique » : vise les notions faibles ou jamais travaillées du module.
    Renvoie (ids ou questions, cibles) où cibles = [{"notion", "raison"}] pour l'expliquer à l'étudiant.
    """
    cibles = cibles_adaptatives(pseudo, module)
    extraits = _extraits_pour(pseudo, module, [c["notion"] for c in cibles]) if cibles else []
    if len(extraits) < 2:  # pas encore de carte des notions ni d'historique : on couvre le module au hasard
        cibles = []
        extraits = choisir_extraits(pseudo, None, "", NB_EXTRAITS_QCM, module)
    questions = _rediger_qcm(pseudo, extraits, nb, "", module, [c["notion"] for c in cibles] or None)
    resultat = db.ajouter_questions(ingest.user_key(pseudo), questions) if enregistrer else questions
    return resultat, cibles


# ---------------------------------------------------------------------------
# Fiches de révision
# ---------------------------------------------------------------------------
NB_EXTRAITS_FICHE = 6


class ElementFiche(BaseModel):
    texte: str = Field(min_length=3)
    extraits: list[int] = Field(min_length=1)


class Fiche(BaseModel):
    dans_le_cours: bool
    notion: str = ""
    definition: ElementFiche | None = None
    points_cles: list[ElementFiche] = []
    formules: list[ElementFiche] = []
    pieges: list[ElementFiche] = []


def _ancrer(elements: list[ElementFiche], nb_extraits: int) -> list[dict]:
    """
    Garde seulement les éléments qui citent au moins un extrait existant.
    Un élément sans source valide est retiré : c'est notre garde-fou contre les hallucinations.
    """
    gardes = []
    for e in elements:
        refs = sorted({n for n in e.extraits if 1 <= n <= nb_extraits})
        if refs:
            gardes.append({"texte": e.texte.strip(), "extraits": refs})
    return gardes


def generer_fiche(pseudo: str, notion: str, source: str | None = None, module: str | None = None) -> dict:
    """
    Renvoie {"notion", "dans_le_cours", "definition", "points_cles", "formules", "pieges",
    "extraits", "retires"} où chaque élément est {"texte", "extraits": [numéros]}.
    """
    notion = notion.strip()
    if len(notion) < 2:
        raise GenerationError("Indique la notion à résumer.")
    extraits = ingest.search(pseudo, notion, k=NB_EXTRAITS_FICHE, source=source, module=module)
    if not extraits:
        raise GenerationError("Aucun passage de cours disponible. Importe d'abord un PDF.")

    fiche = llm.chat_json(
        [
            {"role": "system", "content": prompts.SYSTEME_FICHE},
            {"role": "user", "content": prompts.message_fiche(notion, extraits)},
        ],
        schema=Fiche,
        temperature=0.2,
    )

    n = len(extraits)
    definition = _ancrer([fiche.definition], n) if fiche.definition else []
    sections = {
        "points_cles": _ancrer(fiche.points_cles, n),
        "formules": _ancrer(fiche.formules, n),
        "pieges": _ancrer(fiche.pieges, n),
    }
    total_avant = (1 if fiche.definition else 0) + len(fiche.points_cles) + len(fiche.formules) + len(fiche.pieges)
    total_apres = len(definition) + sum(len(v) for v in sections.values())
    dans_le_cours = fiche.dans_le_cours and total_apres > 0

    return {
        "notion": (fiche.notion or notion).strip(),
        "dans_le_cours": dans_le_cours,
        "definition": definition[0] if definition else None,
        **sections,
        "extraits": extraits,
        "retires": total_avant - total_apres,  # éléments supprimés faute de source valide
    }


def fiche_markdown(fiche: dict) -> str:
    """Met la fiche en forme (affichage dans l'app et téléchargement .md)."""
    refs = lambda e: " " + "".join(f"[{n}]" for n in e["extraits"])
    lignes = [f"# {fiche['notion']}", ""]
    if not fiche["dans_le_cours"]:
        lignes.append(f"_{prompts.PAS_DANS_LE_COURS}_")
        return "\n".join(lignes)

    if fiche["definition"]:
        lignes += ["## Définition", fiche["definition"]["texte"] + refs(fiche["definition"]), ""]
    for cle, titre in (("points_cles", "Points clés"), ("formules", "Formules"), ("pieges", "Pièges classiques")):
        if fiche[cle]:
            lignes += [f"## {titre}"] + [f"- {e['texte']}{refs(e)}" for e in fiche[cle]] + [""]

    cites = sorted({n for cle in ("points_cles", "formules", "pieges") for e in fiche[cle] for n in e["extraits"]}
                   | set(fiche["definition"]["extraits"] if fiche["definition"] else []))
    lignes.append("## Sources")
    for n in cites:
        e = fiche["extraits"][n - 1]
        lignes.append(f"- [{n}] {e['source']}, page {e['page']}")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Rangement automatique d'un PDF dans un module
# ---------------------------------------------------------------------------
class SuggestionModule(BaseModel):
    module: str = Field(min_length=2, max_length=60)
    existant: bool
    raison: str = ""


def suggerer_module(prep: dict, modules_existants: list[str]) -> dict:
    """
    Classification « zéro exemple » : le LLM lit le début du PDF et choisit un module
    existant ou en propose un nouveau. Renvoie {"module", "existant", "raison"}.
    """
    try:
        s = llm.chat_json(
            [
                {"role": "system", "content": prompts.SYSTEME_MODULE},
                {"role": "user", "content": prompts.message_module(prep["fichier"], prep["debut"], modules_existants)},
            ],
            schema=SuggestionModule,
            temperature=0.0,
        )
    except llm.LLMError:
        defaut = modules_existants[0] if modules_existants else ingest.MODULE_PAR_DEFAUT
        return {"module": defaut, "existant": defaut in modules_existants, "raison": "Suggestion indisponible."}

    # Le modèle peut renvoyer un module existant avec une casse différente : on le retrouve.
    existants = {m.lower(): m for m in modules_existants}
    nom = existants.get(s.module.strip().lower())
    if nom:
        return {"module": nom, "existant": True, "raison": s.raison}
    return {"module": ingest.nom_module(s.module), "existant": False, "raison": s.raison}


# ---------------------------------------------------------------------------
# Carte des notions
# ---------------------------------------------------------------------------
class NotionCarte(BaseModel):
    nom: str = Field(min_length=2, max_length=60)
    resume: str = ""
    pages: list[int] = []


class CarteNotions(BaseModel):
    notions: list[NotionCarte] = Field(min_length=1, max_length=15)


def extraire_notions(pseudo: str, module: str, fichier: str) -> int:
    """
    Lit le plan condensé d'un fichier (début de chaque page) et ajoute ses notions à la
    carte du module. Renvoie le nombre de notions nouvelles.
    """
    user = ingest.user_key(pseudo)
    apercu = ingest.apercu_fichier(pseudo, fichier)
    if not apercu:
        return 0
    carte = llm.chat_json(
        [
            {"role": "system", "content": prompts.SYSTEME_NOTIONS},
            {"role": "user", "content": prompts.message_notions(fichier, apercu, db.notions(user, module))},
        ],
        schema=CarteNotions,
        temperature=0.2,
    )
    existantes = {n.lower(): n for n in db.notions(user, module)}
    a_ajouter = []
    for n in carte.notions:
        nom = n.nom.strip().rstrip(".")
        nom = existantes.get(nom.lower(), nom[:1].upper() + nom[1:])
        pages = sorted({p for p in n.pages if p > 0})
        source = f"{fichier} p. {', '.join(map(str, pages))}" if pages else fichier
        a_ajouter.append({"nom": nom, "resume": n.resume.strip(), "source": source})
    return db.ajouter_notions(user, module, a_ajouter)


def regenerer_carte(pseudo: str, module: str) -> int:
    """Refait la carte d'un module à partir de tous ses fichiers (ex : cours importés avant cette fonction)."""
    db.supprimer_carte(ingest.user_key(pseudo), module)
    return sum(extraire_notions(pseudo, module, f) for f in ingest.list_sources(pseudo, module))
