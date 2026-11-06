"""Crée dans fiches_pdf/ un PDF par paire de fiches profils, à imprimer recto verso puis à couper en deux.

Chaque feuille A4 paysage donne deux cartes A5 : les fiches côte à côte d'un côté, le logo YOLO Cuistot
derrière chacune de l'autre. Imprimer en recto verso, retournement sur le bord court.
Lancement depuis la racine du projet : python outils/generer_pdf_fiches.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app import donnees  # noqa: E402
from app.config import DOSSIER_LOGOS  # noqa: E402

SORTIE = RACINE / "fiches_pdf"
LOGO = DOSSIER_LOGOS / "Logo_yolo_cuistot_2.png"

DPI = 300
FEUILLE = (3508, 2480)         # A4 paysage (297 x 210 mm) à 300 dpi
CARTE = FEUILLE[0] // 2        # largeur d'une carte A5 (148,5 mm)
MARGE = 59                     # 5 mm autour de chaque fiche : la marge que laissent les imprimantes
LARGEUR_LOGO = 1181            # 100 mm


def _placer(feuille, image, position):
    """Centre l'image dans la moitié gauche (position 0) ou droite (position 1) de la feuille."""
    x = position * CARTE + (CARTE - image.width) // 2
    feuille.paste(image, (x, (FEUILLE[1] - image.height) // 2))


def charger_logo():
    logo = Image.open(LOGO).convert("RGB")
    # Le logo n'est pas centré dans son carré : on retire le blanc autour avant de le centrer.
    difference = ImageChops.difference(logo, Image.new("RGB", logo.size, "white")).convert("L")
    logo = logo.crop(difference.point(lambda v: 255 if v > 12 else 0).getbbox())
    return logo.resize((LARGEUR_LOGO, round(LARGEUR_LOGO * logo.height / logo.width)), Image.LANCZOS)


def charger_fiche(chemin):
    fiche = Image.open(chemin).convert("RGB")
    echelle = min((CARTE - 2 * MARGE) / fiche.width, (FEUILLE[1] - 2 * MARGE) / fiche.height)
    return fiche.resize((round(fiche.width * echelle), round(fiche.height * echelle)), Image.LANCZOS)


def _ligne_de_coupe(feuille):
    dessin = ImageDraw.Draw(feuille)
    for y in range(0, FEUILLE[1], 60):
        dessin.line([(CARTE, y), (CARTE, y + 30)], fill=(170, 170, 170), width=2)


def feuille(fiches, logo):
    """Les deux pages (recto, verso) d'une feuille portant une paire de fiches."""
    recto = Image.new("RGB", FEUILLE, "white")
    verso = Image.new("RGB", FEUILLE, "white")
    for position, fiche in enumerate(fiches):
        _placer(recto, fiche, position)
        _placer(verso, logo, 1 - position)  # une fois la feuille retournée, la gauche passe à droite
    _ligne_de_coupe(recto)
    return recto, verso


def _nom_fichier(images):
    """« 01_prof_de_sport » et « 02_cpe » donnent « 01-02_prof_de_sport_cpe.pdf »."""
    numeros, noms = zip(*(image.stem.split("_", 1) for image in images))
    return f"{'-'.join(numeros)}_{'_'.join(noms)}.pdf"


def main():
    SORTIE.mkdir(exist_ok=True)
    logo = charger_logo()
    profils = donnees.profils()
    for debut in range(0, len(profils), 2):
        paire = profils[debut:debut + 2]
        images = [RACINE / p["image"].lstrip("/") for p in paire]
        recto, verso = feuille([charger_fiche(image) for image in images], logo)
        chemin = SORTIE / _nom_fichier(images)
        recto.save(chemin, save_all=True, append_images=[verso], resolution=DPI, quality=96, subsampling=0,
                   title="Fiches " + " et ".join(p["nom"] for p in paire))
        print(f"{chemin.name} ({chemin.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
