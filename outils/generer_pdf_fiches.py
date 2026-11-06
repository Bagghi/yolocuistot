"""Crée un PDF A4 recto verso par fiche profil, dans le dossier fiches_pdf/.

Page 1 (recto) : le logo YOLO Cuistot. Page 2 (verso) : l'image de la fiche.
Lancement depuis la racine du projet : python outils/generer_pdf_fiches.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app import donnees  # noqa: E402
from app.config import DOSSIER_LOGOS  # noqa: E402

SORTIE = RACINE / "fiches_pdf"
LOGO = DOSSIER_LOGOS / "Logo_yolo_cuistot_2.png"

DPI = 300
A4 = (2480, 3508)              # 210 x 297 mm à 300 dpi
MARGE = 94                     # 8 mm : les imprimantes n'impriment pas jusqu'au bord
LARGEUR_LOGO = 1772            # 150 mm


def _page(image, largeur, hauteur, nettete):
    """Centre l'image, redimensionnée à (largeur, hauteur), sur une page A4 blanche."""
    page = Image.new("RGB", A4, "white")
    # Un agrandissement rend les contours flous : on redonne de la netteté au texte.
    image = image.resize((largeur, hauteur), Image.LANCZOS).filter(nettete)
    page.paste(image, ((A4[0] - largeur) // 2, (A4[1] - hauteur) // 2))
    return page


def page_logo():
    logo = Image.open(LOGO).convert("RGB")
    # Le logo n'est pas centré dans son carré : on retire le blanc autour avant de le centrer sur la page.
    difference = ImageChops.difference(logo, Image.new("RGB", logo.size, "white")).convert("L")
    logo = logo.crop(difference.point(lambda v: 255 if v > 12 else 0).getbbox())
    return _page(logo, LARGEUR_LOGO, round(LARGEUR_LOGO * logo.height / logo.width),
                 ImageFilter.UnsharpMask(radius=3, percent=90, threshold=2))


def page_fiche(chemin):
    fiche = Image.open(chemin).convert("RGB")
    echelle = min((A4[0] - 2 * MARGE) / fiche.width, (A4[1] - 2 * MARGE) / fiche.height)
    # Images en haute définition, à peine agrandies : une netteté légère suffit.
    return _page(fiche, round(fiche.width * echelle), round(fiche.height * echelle),
                 ImageFilter.UnsharpMask(radius=1, percent=60, threshold=2))


def main():
    SORTIE.mkdir(exist_ok=True)
    recto = page_logo()
    for profil in donnees.profils():
        image = RACINE / profil["image"].lstrip("/")
        chemin = SORTIE / f"{image.stem}.pdf"
        recto.save(chemin, save_all=True, append_images=[page_fiche(image)],
                   resolution=DPI, quality=96, subsampling=0, title=f"Fiche {profil['nom']}")
        print(f"{chemin.name} ({chemin.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
