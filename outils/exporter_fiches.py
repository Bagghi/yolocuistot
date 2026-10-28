"""Exporte la page des fiches en un seul fichier HTML autonome, à ouvrir sans le serveur.

Styles, logos et données (profils, aliments) sont intégrés dans la page.
Lancement depuis la racine du projet : python outils/exporter_fiches.py
"""
import base64
import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app import donnees  # noqa: E402
from app.config import DOSSIER_LOGOS, DOSSIER_STATIC  # noqa: E402

SORTIE = RACINE / "fiches_yolocuistot.html"


def _logo(nom):
    contenu = base64.b64encode((DOSSIER_LOGOS / nom).read_bytes()).decode()
    return f"data:image/png;base64,{contenu}"


def _json(valeur):
    # « </ » échappé : le JSON ne doit pas fermer la balise <script> qui le contient.
    return json.dumps(valeur, ensure_ascii=False).replace("</", "<\\/")


def main():
    page = (DOSSIER_STATIC / "profils.html").read_text(encoding="utf-8")
    css = (DOSSIER_STATIC / "css" / "style.css").read_text(encoding="utf-8")
    commun = (DOSSIER_STATIC / "js" / "commun.js").read_text(encoding="utf-8")

    remplacements = [
        ('<link rel="stylesheet" href="/static/css/style.css">', f"<style>\n{css}\n</style>"),
        ('<script src="/static/js/commun.js"></script>', f"<script>\n{commun}\n</script>"),
        ('const [profils, aliments] = await Promise.all([api("/api/profils"), api("/api/aliments")]);',
         f"const profils = {_json(donnees.profils())};\n      const aliments = {_json(donnees.aliments())};"),
    ]
    for ancien, nouveau in remplacements:
        if ancien not in page:
            sys.exit(f"Élément introuvable dans profils.html : {ancien}")
        page = page.replace(ancien, nouveau)

    # Le lien vers l'accueil n'a pas de sens hors du serveur.
    page = re.sub(r'\s*<nav class="liens">.*?</nav>', "", page, flags=re.S)
    page = re.sub(r'src="/logos/([^"]+)"', lambda m: f'src="{_logo(m.group(1))}"', page)

    SORTIE.write_text(page, encoding="utf-8")
    print(f"Page exportée : {SORTIE} ({SORTIE.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
