"""Annotation des photos d'exemple pour les prompts visuels de YOLOE.

Organisation attendue (un dossier par aliment, nommé comme son "classe_yolo" dans data/aliments.json) :

    data/exemples_visuels/
        porc/          photo1.jpg, photo2.jpg, ...
        lentilles/     ...

Pour chaque photo pas encore annotée, une fenêtre s'ouvre avec un cadre proposé par YOLOE (en jaune) :
    Entrée / Espace / Échap   valider la photo avec les cadres affichés
    souris            dessiner un cadre (il remplace la proposition jaune, d'autres peuvent être ajoutés)
    Z / Retour arr.   effacer le dernier cadre
    I                 ignorer cette photo (elle ne sera plus proposée)
    Q                 quitter (tout ce qui est validé est déjà enregistré)
Les cadres sont enregistrés dans data/exemples_visuels/annotations.json, à la taille réelle des photos.

Photos prises avec la webcam du stand (conseillé : mêmes conditions que pendant l'atelier) :
    python outils/annoter_exemples.py --capture carotte [--camera 1]
    Espace : prendre une photo, Échap : terminer. L'application doit être arrêtée (elle occupe la caméra).
    Les photos vont dans data/exemples_visuels/carotte/, puis l'annotation démarre.

Lancement depuis la racine du projet :
    python outils/annoter_exemples.py            # photos pas encore annotées
    python outils/annoter_exemples.py --refaire  # toutes les photos
    python outils/annoter_exemples.py --sans-yolo  # sans proposition automatique de cadre
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app import donnees, exemples_visuels  # noqa: E402
from app.config import CAMERA_INDEX, MODELE_YOLO, RESOLUTION_CAMERA  # noqa: E402

TAILLE_FENETRE = (1400, 850)  # taille maximale d'affichage
VERT, JAUNE = (60, 200, 60), (0, 220, 255)
TOUCHES_VALIDER = {13, 10, 32, 27}  # Échap valide aussi (habitude de la première version de l'outil)
TOUCHES_EFFACER = {ord("z"), ord("Z"), 8}
TOUCHES_IGNORER = {ord("i"), ord("I")}
TOUCHES_QUITTER = {ord("q"), ord("Q")}


class Proposeur:
    """Propose le cadre le plus probable d'un objet, avec YOLOE (photos d'un seul objet sur fond uni)."""

    def __init__(self):
        from ultralytics import YOLOE
        print("Chargement de YOLOE pour proposer les cadres…")
        self.modele = YOLOE(MODELE_YOLO)
        self.modele.set_classes(["toy", "object", "figurine", "food"])

    def proposer(self, image):
        boites = self.modele.predict(image, imgsz=640, conf=0.03, verbose=False)[0].boxes
        if not len(boites):
            return None
        return [int(v) for v in boites.xyxy[int(boites.conf.argmax())].tolist()]


class Annotateur:
    def __init__(self, image, titre, proposition):
        self.titre = titre
        hauteur, largeur = image.shape[:2]
        self.echelle = min(1.0, TAILLE_FENETRE[0] / largeur, TAILLE_FENETRE[1] / hauteur)
        self.fond = cv2.resize(image, None, fx=self.echelle, fy=self.echelle) if self.echelle < 1 else image
        self.proposition = proposition
        self.cadres = []      # cadres dessinés, en coordonnées d'affichage
        self.depart = None
        self.courant = None

    def _souris(self, evenement, x, y, *_):
        if evenement == cv2.EVENT_LBUTTONDOWN:
            self.depart, self.courant = (x, y), (x, y)
        elif evenement == cv2.EVENT_MOUSEMOVE and self.depart:
            self.courant = (x, y)
        elif evenement == cv2.EVENT_LBUTTONUP and self.depart:
            (x1, y1), (x2, y2) = self.depart, (x, y)
            if abs(x2 - x1) > 5 and abs(y2 - y1) > 5:
                self.cadres.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
            self.depart = self.courant = None

    def _afficher(self):
        vue = self.fond.copy()
        if not self.cadres and self.proposition:
            x1, y1, x2, y2 = (round(v * self.echelle) for v in self.proposition)
            cv2.rectangle(vue, (x1, y1), (x2, y2), JAUNE, 2)
        for x1, y1, x2, y2 in self.cadres:
            cv2.rectangle(vue, (x1, y1), (x2, y2), VERT, 3)
        if self.depart:
            cv2.rectangle(vue, self.depart, self.courant, VERT, 1)
        aide = "Entree: valider   souris: dessiner   Z: effacer   I: ignorer   Q: quitter"
        cv2.putText(vue, aide, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
        cv2.putText(vue, aide, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.imshow(self.titre, vue)

    def executer(self):
        """Renvoie la liste des cadres (taille réelle), "ignorer" ou "quitter"."""
        cv2.namedWindow(self.titre, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(self.titre, self._souris)
        try:
            while True:
                self._afficher()
                touche = cv2.waitKey(20) & 0xFF
                if cv2.getWindowProperty(self.titre, cv2.WND_PROP_VISIBLE) < 1:
                    print("  Fenêtre fermée.")
                    return "quitter"
                if touche in TOUCHES_VALIDER:
                    if self.cadres:
                        return [[round(v / self.echelle) for v in c] for c in self.cadres]
                    if self.proposition:
                        return [self.proposition]
                    print("  Aucun cadre : dessinez-en un, ou I pour ignorer la photo.")
                elif touche in TOUCHES_EFFACER and self.cadres:
                    self.cadres.pop()
                elif touche in TOUCHES_IGNORER:
                    return "ignorer"
                elif touche in TOUCHES_QUITTER:
                    print("  Touche Q.")
                    return "quitter"
        finally:
            cv2.destroyWindow(self.titre)


def _option(nom):
    if nom in sys.argv and sys.argv.index(nom) + 1 < len(sys.argv):
        return sys.argv[sys.argv.index(nom) + 1]
    return None


def capturer(classe, index_camera):
    """Photos prises à la webcam, enregistrées dans le dossier de l'aliment."""
    dossier = exemples_visuels.DOSSIER_EXEMPLES / classe
    dossier.mkdir(parents=True, exist_ok=True)
    camera = cv2.VideoCapture(index_camera, cv2.CAP_DSHOW)
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, RESOLUTION_CAMERA[0])
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION_CAMERA[1])
    if not camera.isOpened() or not camera.read()[0]:
        print(f"Caméra {index_camera} indisponible (l'application est-elle arrêtée ? essayer --camera 0).")
        return
    titre = f"Capture {classe} - Espace: photo, Echap: terminer"
    nombre, flash = 0, 0
    try:
        while True:
            ok, image = camera.read()
            if not ok:
                continue
            vue = image.copy()
            if flash:
                cv2.rectangle(vue, (0, 0), (vue.shape[1] - 1, vue.shape[0] - 1), VERT, 12)
                flash -= 1
            cv2.putText(vue, f"{classe} : {nombre} photo(s)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5)
            cv2.putText(vue, f"{classe} : {nombre} photo(s)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.imshow(titre, vue)
            touche = cv2.waitKey(1) & 0xFF
            if touche == 32:
                nom = dossier / f"webcam_{time.strftime('%Y%m%d_%H%M%S')}_{nombre}.jpg"
                cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(str(nom))
                nombre, flash = nombre + 1, 4
                print(f"  {nom.name}")
            elif touche in TOUCHES_QUITTER | {27} or cv2.getWindowProperty(titre, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()
    print(f"{nombre} photo(s) de « {classe} » prises.")


def main():
    dossier_exemples = exemples_visuels.DOSSIER_EXEMPLES
    classe_capture = _option("--capture")
    if classe_capture:
        if classe_capture not in {a["classe_yolo"] for a in donnees.aliments()}:
            print(f"« {classe_capture} » n'est le classe_yolo d'aucun aliment de data/aliments.json.")
            return
        capturer(classe_capture, int(_option("--camera") or CAMERA_INDEX))
    if not dossier_exemples.is_dir():
        dossier_exemples.mkdir(parents=True)
        print(f"Dossier créé : {dossier_exemples}\nAjoutez-y un sous-dossier par aliment, puis relancez.")
        return
    refaire = "--refaire" in sys.argv
    proposeur = None
    if "--sans-yolo" not in sys.argv:
        try:
            proposeur = Proposeur()
        except Exception as erreur:
            print(f"Pas de proposition automatique ({erreur}) : cadres à dessiner à la main.")

    classes_connues = {a["classe_yolo"] for a in donnees.aliments()}
    annotations = exemples_visuels.lire_annotations()
    for dossier in sorted(p for p in dossier_exemples.iterdir() if p.is_dir()):
        if dossier.name not in classes_connues:
            print(f"⚠ « {dossier.name} » n'est le classe_yolo d'aucun aliment de data/aliments.json : dossier ignoré.")
            continue
        photos = exemples_visuels.photos(dossier)
        print(f"{dossier.name} : {len(photos)} photo(s)")
        for numero, photo in enumerate(photos, 1):
            cle = f"{dossier.name}/{photo.name}"
            deja = annotations.get(cle)
            if not refaire and (deja == "ignoree" or (isinstance(deja, list) and deja)):
                continue  # déjà annotée (une liste vide, ancien format, est reproposée)
            image = exemples_visuels.lire_image(photo)
            if image is None:
                print(f"  Illisible, ignorée : {photo.name}")
                continue
            proposition = proposeur.proposer(image) if proposeur else None
            titre = f"{dossier.name} - photo {numero}/{len(photos)}"
            resultat = Annotateur(image, titre, proposition).executer()
            if resultat == "quitter":
                print("Arrêt : les photos validées sont enregistrées (relancer sans --refaire pour continuer).")
                return
            annotations[cle] = "ignoree" if resultat == "ignorer" else resultat
            exemples_visuels.ecrire_annotations(annotations)
            print(f"  {photo.name} : {'ignorée' if resultat == 'ignorer' else f'{len(resultat)} cadre(s)'}")

    print(f"\nAnnotations enregistrées dans {exemples_visuels.FICHIER_ANNOTATIONS}")


if __name__ == "__main__":
    main()
