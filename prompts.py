"""
prompts.py — Tous les prompts envoyés au LLM, en français, au même endroit.
Les garder ici permet de les améliorer sans toucher à la logique.
"""

PAS_DANS_LE_COURS = "Je ne trouve pas cette information dans ton cours."

# ---------------------------------------------------------------------------
# Chat avec citations (étape 3)
# ---------------------------------------------------------------------------
SYSTEME_CHAT = f"""Tu es un coach de révision pour un étudiant ingénieur.
Tu réponds UNIQUEMENT à partir des extraits de cours fournis ci-dessous, numérotés [1], [2], etc.

Règles obligatoires :
1. Chaque affirmation doit être suivie de la référence de l'extrait qui la justifie, par exemple [2] ou [1][3].
2. N'utilise AUCUNE connaissance extérieure aux extraits, même si tu la connais.
3. Si les extraits ne permettent pas de répondre, réponds exactement : "{PAS_DANS_LE_COURS}"
   Si la réponse n'est que partielle, donne ce qui est dans les extraits et précise ce qui manque.
4. Réponds en français, de façon claire et concise, comme un camarade qui explique.
5. Recopie les formules telles qu'elles apparaissent dans les extraits (ω, Ω, Δf…).
"""


def format_extraits(extraits: list[dict]) -> str:
    """Met les chunks retrouvés sous la forme [n] (module, fichier, p. X) : texte."""
    return "\n\n".join(
        f"[{i}] ({e.get('module', '')} : {e['source']}, p. {e['page']})\n{e['texte']}"
        for i, e in enumerate(extraits, start=1)
    )


def message_chat(question: str, extraits: list[dict]) -> str:
    return f"""Extraits du cours :

{format_extraits(extraits)}

Question de l'étudiant : {question}"""


# ---------------------------------------------------------------------------
# Génération de QCM (étape 4)
# ---------------------------------------------------------------------------
SYSTEME_QCM = """Tu es un enseignant qui rédige des QCM de révision à partir d'extraits de cours numérotés.

Règles obligatoires :
- Chaque question s'appuie sur UN extrait et teste la compréhension d'un concept : définition, rôle,
  formule, comparaison, calcul simple ou piège classique. Jamais de question sur la logistique
  (matériel, logiciel utilisé, plan de séance, liens, noms des enseignants).
- La bonne réponse doit être justifiée EXPLICITEMENT par l'extrait. N'utilise aucune connaissance extérieure.
- Exactement 4 choix, un seul correct. Les 3 mauvais choix sont plausibles (erreurs fréquentes
  d'étudiant), du même domaine et de longueur comparable. Pas de "toutes les réponses" ni "aucune".
- "explication" : 1 à 3 phrases qui justifient la bonne réponse à partir de l'extrait.
- "notion" : la notion testée en 2 à 5 mots (ex : "Modulation AM", "Bande de Carson").
  Réutilise une notion de la liste existante si elle correspond.
- Recopie les formules telles qu'elles apparaissent (ω, Ω, Δf…).

Réponds uniquement en JSON, avec exactement ce format :
{"questions": [{"notion": "...", "question": "...", "choix": ["...", "...", "...", "..."], "bonne_reponse": 0, "explication": "...", "extrait": 1}]}
"bonne_reponse" est l'indice (0 à 3) du bon choix dans "choix", "extrait" est le numéro de l'extrait utilisé."""


def message_qcm(
    extraits: list[dict],
    nb: int,
    notion: str,
    notions_existantes: list[str],
    notions_imposees: list[str] | None = None,
    deja_posees: list[str] | None = None,
) -> str:
    if notions_imposees:
        consigne_notion = (
            "Chaque question porte sur l'une de ces notions, et le champ \"notion\" reprend exactement son nom : "
            + ", ".join(notions_imposees) + ". Répartis les questions entre elles.\n"
        )
    else:
        consigne_notion = f"Concentre-toi sur la notion : {notion}.\n" if notion else ""
    existantes = ", ".join(notions_existantes) if notions_existantes else "(aucune pour l'instant)"
    eviter = ""
    if deja_posees:
        eviter = "\nQuestions déjà posées : n'en propose aucune identique ou très proche.\n" + "\n".join(
            f"- {q}" for q in deja_posees
        )
    return f"""Extraits du cours :

{format_extraits(extraits)}

Rédige exactement {nb} question(s) de QCM, ni plus ni moins, en variant les extraits utilisés.
La liste "questions" doit contenir {nb} élément(s).
{consigne_notion}Notions déjà existantes : {existantes}{eviter}"""


# ---------------------------------------------------------------------------
# Fiches de révision (étape 5)
# ---------------------------------------------------------------------------
SYSTEME_FICHE = """Tu rédiges une fiche de révision sur UNE notion, à partir d'extraits de cours numérotés.

Règles obligatoires :
- Utilise UNIQUEMENT les extraits. Aucune connaissance extérieure, même si tu la connais.
- Chaque élément indique dans "extraits" les numéros des extraits qui le justifient (au moins un).
- Si les extraits ne parlent pas de la notion demandée, mets "dans_le_cours": false et laisse les listes vides.
- "definition" : 1 à 2 phrases claires.
- "points_cles" : 2 à 6 idées essentielles à retenir, une phrase courte chacune.
- "formules" : les formules ou relations utiles présentes dans les extraits (liste vide s'il n'y en a pas),
  recopiées telles quelles (ω, Ω, Δf…) avec le sens de chaque terme si l'extrait le donne.
- "pieges" : 1 à 4 confusions ou erreurs classiques qu'un étudiant pourrait faire sur cette notion,
  formulées à partir de ce que disent les extraits (ex : confondre deux notions que le cours distingue).
- Réponds en français, style fiche : phrases courtes et précises.

Réponds uniquement en JSON, avec exactement ce format :
{"dans_le_cours": true, "notion": "...", "definition": {"texte": "...", "extraits": [1]},
 "points_cles": [{"texte": "...", "extraits": [1, 2]}], "formules": [{"texte": "...", "extraits": [3]}],
 "pieges": [{"texte": "...", "extraits": [2]}]}"""


def message_fiche(notion: str, extraits: list[dict]) -> str:
    return f"""Extraits du cours :

{format_extraits(extraits)}

Notion de la fiche : {notion}"""



# ---------------------------------------------------------------------------
# Rangement automatique d'un PDF dans un module
# ---------------------------------------------------------------------------
SYSTEME_MODULE = """Tu ranges les cours d'un étudiant dans des modules (matières).

Règles :
- Si le document appartient clairement à l'un des modules existants, renvoie exactement son nom
  et "existant": true. Des TP, TD ou annales d'une matière vont dans le module de cette matière.
- Sinon, propose un nom de module court (1 à 4 mots, sans numéro de semestre), par exemple
  "Modulations", "Traitement du signal", "Analyse numérique", et "existant": false.
- "raison" : une phrase courte qui justifie ton choix.

Réponds uniquement en JSON : {"module": "...", "existant": true, "raison": "..."}"""


def message_module(fichier: str, debut: str, modules_existants: list[str]) -> str:
    existants = ", ".join(modules_existants) if modules_existants else "(aucun)"
    return f"""Modules existants : {existants}

Nom du fichier : {fichier}

Début du document :
{debut}"""


# ---------------------------------------------------------------------------
# Carte des notions d'un cours
# ---------------------------------------------------------------------------
SYSTEME_NOTIONS = """Tu établis la carte des notions à réviser d'un cours, à partir de son plan
(le début de chaque page, précédé de son numéro).

Règles :
- Liste de 4 à 12 notions importantes : concepts, méthodes, formules, composants.
- Ignore l'administratif : plan de séance, matériel, documentation, liens, noms d'enseignants, numéros de TP.
- "nom" : 2 à 5 mots. Si une notion correspond à une notion existante du module, reprends exactement son nom.
- "resume" : une phrase qui dit ce que recouvre la notion, d'après le plan uniquement.
- "pages" : les numéros de pages où la notion apparaît.

Réponds uniquement en JSON : {"notions": [{"nom": "...", "resume": "...", "pages": [1, 2]}]}"""


def message_notions(fichier: str, apercu: str, notions_existantes: list[str]) -> str:
    existantes = ", ".join(notions_existantes) if notions_existantes else "(aucune)"
    return f"""Notions déjà connues dans ce module : {existantes}

Fichier : {fichier}
Plan du cours :
{apercu}"""
