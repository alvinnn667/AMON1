"""
ingest.py — PDF -> texte par page -> chunks -> embeddings -> ChromaDB.

Chaque utilisateur (pseudo) a SA collection ChromaDB : personne ne voit les cours
d'un autre. Chaque chunk garde ses métadonnées (module, fichier, page) pour pouvoir
filtrer par module et citer la source exacte.

L'import se fait en deux temps :
  1. preparer_pdf()   : lecture, découpage et embeddings (rien n'est encore enregistré) ;
  2. enregistrer_pdf(): rangement dans le module choisi (éventuellement suggéré par l'IA).
"""

import hashlib
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

# Streamlit Community Cloud fournit un SQLite trop ancien pour ChromaDB (3.35 minimum).
# On le remplace par pysqlite3, installé seulement sous Linux (voir requirements.txt).
# Ce remplacement doit avoir lieu AVANT l'import de chromadb.
try:
    import pysqlite3  # noqa: F401
    import sys

    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass  # sur ton Mac, le SQLite fourni est assez récent

import chromadb
import pymupdf
from chromadb.config import Settings

import llm

# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
MAX_FILE_MB = 10      # taille max d'un PDF
MAX_FILES = 30        # nombre max de PDF par utilisateur (tous modules confondus)
MAX_MODULES = 8       # nombre max de modules par utilisateur
MODULE_PAR_DEFAUT = "Général"  # module des cours importés avant l'arrivée des modules
MAX_PAGES = 200       # pages max par PDF
CHUNK_SIZE = 900      # taille visée d'un chunk, en caractères
CHUNK_OVERLAP = 150   # chevauchement entre deux chunks voisins
MIN_CHUNK_CHARS = 40  # en dessous, c'est du bruit (titre de slide seul, numéro…)


class IngestError(ValueError):
    """Erreur lisible, affichable telle quelle dans l'interface."""


# ---------------------------------------------------------------------------
# Nettoyage du texte
# ---------------------------------------------------------------------------
# Les formules de tes cours utilisent la police "Symbol" : PyMuPDF renvoie alors des
# caractères privés (ex :  au lieu de ω). On les retraduit en vrais caractères.
_SYMBOL = {
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε", 0x46: "Φ", 0x47: "Γ",
    0x48: "Η", 0x49: "Ι", 0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο",
    0x50: "Π", 0x51: "Θ", 0x52: "Ρ", 0x53: "Σ", 0x54: "Τ", 0x55: "Υ", 0x57: "Ω",
    0x58: "Ξ", 0x59: "Ψ", 0x5A: "Ζ",
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ", 0x67: "γ",
    0x68: "η", 0x69: "ι", 0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν",
    0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x74: "τ", 0x75: "υ",
    0x77: "ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ",
    0x2D: "−", 0x40: "≅", 0xA2: "′", 0xA3: "≤", 0xA5: "∞", 0xAE: "→", 0xB0: "°",
    0xB1: "±", 0xB3: "≥", 0xB4: "×", 0xB6: "∂", 0xB8: "÷", 0xB9: "≠", 0xBB: "≈",
    0xC5: "⊕", 0xC6: "⊗", 0xD6: "√", 0xD7: "·", 0xE5: "∑", 0xF2: "∫",
}


def _fix_symbol_char(match: re.Match) -> str:
    code = ord(match.group()) - 0xF000
    if code in _SYMBOL:
        return _SYMBOL[code]
    if 0x20 <= code <= 0x3F or code in (0x5B, 0x5D):  # chiffres, ponctuation, [ ]
        return chr(code)
    return ""  # morceaux de grandes parenthèses, puces décoratives… : on jette


def clean_text(text: str) -> str:
    text = re.sub(r"[-]", _fix_symbol_char, text)
    text = re.sub(r"[ \t ]+", " ", text)          # espaces multiples -> un seul
    text = re.sub(r" ?\n[ \n]*", "\n", text)           # lignes vides en série -> une
    return text.strip()


def _remove_repeated_lines(pages: list[str]) -> list[str]:
    """
    Supprime les en-têtes/pieds de page : une ligne présente sur plus de la moitié
    des pages (ex : "UE3 LES MODULATIONS TP 1 – PAGE 3") est du bruit, pas du contenu.
    """
    if len(pages) < 4:
        return pages
    norm = lambda line: re.sub(r"\d+", "#", line.strip().lower())
    counts = Counter(n for p in pages for n in {norm(l) for l in p.splitlines() if l.strip()})
    repeated = {line for line, c in counts.items() if c > len(pages) / 2}
    return ["\n".join(l for l in p.splitlines() if norm(l) not in repeated) for p in pages]


# ---------------------------------------------------------------------------
# Lecture PDF et découpage
# ---------------------------------------------------------------------------
def extract_pages(pdf_bytes: bytes) -> list[str]:
    """Renvoie le texte nettoyé de chaque page (index 0 = page 1)."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise IngestError("Ce fichier n'est pas un PDF lisible.") from e
    if doc.page_count > MAX_PAGES:
        raise IngestError(f"PDF trop long ({doc.page_count} pages, max {MAX_PAGES}).")
    pages = [clean_text(page.get_text()) for page in doc]
    return _remove_repeated_lines(pages)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Découpe un texte en morceaux d'environ `size` caractères. On coupe de préférence
    à une fin de phrase ou de ligne, et deux chunks voisins se chevauchent de `overlap`
    caractères pour ne pas couper une idée en deux.
    """
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        # Le chunk suivant repart un peu en arrière, au début d'un mot.
        space = text.find(" ", end - overlap, end)
        start = space + 1 if space != -1 else end - overlap
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# ChromaDB : une collection par utilisateur
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _chroma() -> chromadb.ClientAPI:
    # Un dossier par modèle d'embeddings : des vecteurs de modèles différents
    # ne sont pas comparables entre eux.
    path = DATA_DIR / "chroma" / llm.embedding_id()
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def user_key(pseudo: str) -> str:
    """Identifiant technique dérivé du pseudo (insensible à la casse et aux espaces)."""
    pseudo = pseudo.strip().lower()
    if not pseudo:
        raise IngestError("Pseudo vide.")
    return "user-" + hashlib.sha256(pseudo.encode()).hexdigest()[:20]


def _collection(pseudo: str):
    return _chroma().get_or_create_collection(
        name=user_key(pseudo),
        embedding_function=None,                 # on fournit nous-mêmes les vecteurs
        metadata={"hnsw:space": "cosine"},       # distance cosinus pour la recherche
    )


def nom_module(nom: str) -> str:
    """Nettoie un nom de module saisi ou proposé : espaces, longueur, majuscule initiale."""
    nom = re.sub(r"\s+", " ", nom or "").strip()[:40]
    if len(nom) < 2:
        raise IngestError("Le nom du module doit faire au moins 2 caractères.")
    return nom[:1].upper() + nom[1:]


def _filtre(source: str | None = None, module: str | None = None) -> dict:
    """Construit le filtre ChromaDB (where) pour un fichier et/ou un module."""
    conditions = [{"source": source}] if source else []
    conditions += [{"module": module}] if module else []
    if not conditions:
        return {}
    return {"where": conditions[0] if len(conditions) == 1 else {"$and": conditions}}


def _metadonnees(pseudo: str) -> list[dict]:
    """
    Métadonnées de tous les chunks de l'utilisateur. Au passage, range dans le module
    par défaut les cours importés avant l'arrivée des modules (migration automatique).
    """
    col = _collection(pseudo)
    res = col.get(include=["metadatas"])
    anciens = [(i, m) for i, m in zip(res["ids"], res["metadatas"]) if "module" not in m]
    if anciens:
        col.update(
            ids=[i for i, _ in anciens],
            metadatas=[{**m, "module": MODULE_PAR_DEFAUT} for _, m in anciens],
        )
        for _, m in anciens:
            m["module"] = MODULE_PAR_DEFAUT
    return res["metadatas"]


def list_modules(pseudo: str) -> dict[str, dict[str, int]]:
    """{module: {fichier: nombre_de_chunks}} pour cet utilisateur."""
    modules: dict[str, Counter] = {}
    for m in _metadonnees(pseudo):
        modules.setdefault(m["module"], Counter())[m["source"]] += 1
    return {mod: dict(fichiers) for mod, fichiers in modules.items()}


def list_sources(pseudo: str, module: str | None = None) -> dict[str, int]:
    """{fichier: nombre_de_chunks}, pour un module ou pour tous."""
    sources: dict[str, int] = {}
    for mod, fichiers in list_modules(pseudo).items():
        if module is None or mod == module:
            sources.update(fichiers)
    return sources


def delete_source(pseudo: str, filename: str) -> None:
    _collection(pseudo).delete(where={"source": filename})


def renommer_module(pseudo: str, ancien: str, nouveau: str) -> str:
    """Renomme un module (ou le fusionne avec un module existant du même nom). Renvoie le nom final."""
    nouveau = nom_module(nouveau)
    col = _collection(pseudo)
    res = col.get(where={"module": ancien}, include=["metadatas"])
    if res["ids"]:
        col.update(ids=res["ids"], metadatas=[{**m, "module": nouveau} for m in res["metadatas"]])
    return nouveau


# ---------------------------------------------------------------------------
# Import en deux temps : préparer (lecture + embeddings), puis enregistrer dans un module
# ---------------------------------------------------------------------------
def preparer_pdf(filename: str, pdf_bytes: bytes) -> dict:
    """
    Lit, découpe et vectorise un PDF sans rien enregistrer.
    Renvoie {"fichier", "pages", "ids", "docs", "metas", "vecteurs", "debut"}.
    """
    if len(pdf_bytes) > MAX_FILE_MB * 1024 * 1024:
        raise IngestError(f"{filename} dépasse {MAX_FILE_MB} Mo.")
    pages = extract_pages(pdf_bytes)
    ids, docs, metas = [], [], []
    for page_num, page_text in enumerate(pages, start=1):
        for i, chunk in enumerate(chunk_text(page_text)):
            if len(chunk) < MIN_CHUNK_CHARS:
                continue
            ids.append(f"{filename}|p{page_num}|c{i}")
            docs.append(chunk)
            metas.append({"source": filename, "page": page_num})
    if not docs:
        raise IngestError(f"Aucun texte exploitable dans {filename}. Est-ce un PDF scanné (image) ?")
    return {
        "fichier": filename,
        "pages": len(pages),
        "ids": ids,
        "docs": docs,
        "metas": metas,
        "vecteurs": llm.embed(docs),
        "debut": "\n".join(docs)[:2500],  # sert à deviner le module
    }


def enregistrer_pdf(pseudo: str, prep: dict, module: str) -> dict:
    """Range un PDF préparé dans un module. Ré-importer le même fichier remplace l'ancien."""
    module = nom_module(module)
    modules = list_modules(pseudo)
    tous_fichiers = {f for fichiers in modules.values() for f in fichiers}
    if prep["fichier"] not in tous_fichiers and len(tous_fichiers) >= MAX_FILES:
        raise IngestError(f"Maximum {MAX_FILES} fichiers. Supprimes-en un d'abord.")
    if module not in modules and len(modules) >= MAX_MODULES:
        raise IngestError(f"Maximum {MAX_MODULES} modules. Range ce fichier dans un module existant.")

    delete_source(pseudo, prep["fichier"])
    _collection(pseudo).add(
        ids=prep["ids"],
        documents=prep["docs"],
        metadatas=[{**m, "module": module} for m in prep["metas"]],
        embeddings=prep["vecteurs"],
    )
    return {
        "fichier": prep["fichier"],
        "module": module,
        "pages": prep["pages"],
        "pages_avec_texte": len({m["page"] for m in prep["metas"]}),
        "chunks": len(prep["docs"]),
    }


def ingest_pdf(pseudo: str, filename: str, pdf_bytes: bytes, module: str = MODULE_PAR_DEFAUT) -> dict:
    """Raccourci : préparer puis enregistrer directement (utilisé par les scripts de test)."""
    return enregistrer_pdf(pseudo, preparer_pdf(filename, pdf_bytes), module)


def apercu_fichier(pseudo: str, filename: str, par_page: int = 160, maximum: int = 6000) -> str:
    """
    Plan condensé d'un fichier : le début de chaque page (souvent son titre).
    Sert à extraire la carte des notions sans envoyer tout le cours au LLM.
    """
    res = _collection(pseudo).get(where={"source": filename}, include=["documents", "metadatas"])
    premiers: dict[int, tuple[int, str]] = {}  # page -> (numéro du chunk, texte)
    for id_, doc, meta in zip(res["ids"], res["documents"], res["metadatas"]):
        numero = int(id_.rsplit("|c", 1)[-1])
        if meta["page"] not in premiers or numero < premiers[meta["page"]][0]:
            premiers[meta["page"]] = (numero, doc)
    lignes = [f"p. {page} : {texte[:par_page].replace(chr(10), ' ')}" for page, (_, texte) in sorted(premiers.items())]
    return "\n".join(lignes)[:maximum]


# ---------------------------------------------------------------------------
# Lecture et recherche
# ---------------------------------------------------------------------------
def _extrait(doc: str, meta: dict, score: float | None) -> dict:
    return {
        "texte": doc,
        "source": meta["source"],
        "page": meta["page"],
        "module": meta.get("module", MODULE_PAR_DEFAUT),
        "score": score,
    }


def get_chunks(pseudo: str, source: str | None = None, module: str | None = None) -> list[dict]:
    """Tous les chunks de l'utilisateur, d'un module ou d'un fichier, sans recherche."""
    res = _collection(pseudo).get(include=["documents", "metadatas"], **_filtre(source, module))
    return [_extrait(doc, meta, None) for doc, meta in zip(res["documents"], res["metadatas"])]


def search(
    pseudo: str, question: str, k: int = 5, source: str | None = None, module: str | None = None
) -> list[dict]:
    """
    Les k chunks les plus proches de la question, dans un fichier, un module ou partout :
    [{"texte", "source", "page", "module", "score"}], score = similarité cosinus (1 = identique).
    """
    col = _collection(pseudo)
    if col.count() == 0:
        return []
    qvec = llm.embed([question])[0]
    res = col.query(
        query_embeddings=[qvec],
        n_results=min(k, col.count()),
        include=["documents", "metadatas", "distances"],
        **_filtre(source, module),
    )
    return [
        _extrait(doc, meta, round(1 - dist, 3))
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]
