"""Lecture de la webcam et détection des aliments avec YOLOE, dans un thread de fond.

Le thread garde en mémoire la dernière image annotée (JPEG) et le comptage lissé
des aliments ; le serveur web les lit quand le navigateur les demande.
Si le modèle ou la caméra ne sont pas disponibles, l'application reste utilisable :
les élèves peuvent ajouter les aliments à la main.
"""
import hashlib
import json
import logging
import threading
import time
from collections import Counter, deque

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import donnees
from .config import (CAMERA_INDEX, DELAI_ARRET_CAMERA, DEVICE, DOSSIER_DONNEES, LISSAGE_IMAGES, MODELE_YOLO,
                     PROMPTS_TEXTE, RESOLUTION_CAMERA, SEUIL_CONFIANCE,
                     TAILLE_IMAGE_YOLO)

journal = logging.getLogger(__name__)

COULEURS = [
    (230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60), (0, 128, 128),
]


def _police(taille):
    for nom in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nom, taille)
        except OSError:
            continue
    return ImageFont.load_default()


class Detecteur:
    def __init__(self):
        self._verrou = threading.Lock()
        self._image_jpeg = None
        self._comptes = {}
        self._historique = deque(maxlen=LISSAGE_IMAGES)
        self._actif = False
        self._thread = None
        self._police = _police(22)
        self._noms = {}
        self.camera_index = CAMERA_INDEX
        self.resolution = None
        self._derniere_activite = float("-inf")
        # Les caméras ne sont manipulées que par le thread de détection (deux ouvertures
        # simultanées d'une même caméra échouent sous Windows) : la liste est mise en cache.
        self._cameras = []
        self._liste_demandee = threading.Event()
        self._liste_prete = threading.Event()
        self.etat = "arrêté"
        self.message_modele = ""
        self.message_camera = ""

    # --- Interface utilisée par le serveur -------------------------------

    def demarrer(self):
        if self._thread and self._thread.is_alive():
            return
        self._actif = True
        self._thread = threading.Thread(target=self._boucle, name="detection", daemon=True)
        self._thread.start()

    def arreter(self):
        self._actif = False
        if self._thread:
            self._thread.join(timeout=3)

    def derniere_image(self):
        with self._verrou:
            return self._image_jpeg

    def comptes(self):
        with self._verrou:
            return dict(self._comptes)

    @property
    def message(self):
        return " ".join(m for m in (self.message_modele, self.message_camera) if m)

    def signaler_activite(self):
        """Appelé tant que la page détection est ouverte : la caméra reste allumée."""
        self._derniere_activite = time.monotonic()

    def changer_camera(self, index):
        """La boucle de détection rouvre la caméra demandée à la prochaine image."""
        self.camera_index = index

    def lister_cameras(self):
        """Indices des caméras qui répondent (OpenCV ne donne pas leur nom sous Windows).

        La liste est dressée par le thread de détection au démarrage ; si elle est vide,
        on lui demande de recommencer (ce qu'il fait dès que la caméra est fermée).
        """
        if not self._cameras:
            self._liste_prete.clear()
            self._liste_demandee.set()
            self._liste_prete.wait(timeout=15)
        return list(self._cameras)

    def _tester_cameras(self, maximum=5):
        disponibles = []
        for index in range(maximum):
            camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if camera.isOpened() and camera.read()[0]:
                disponibles.append(index)
            camera.release()
        self._cameras = disponibles
        self._liste_demandee.clear()
        self._liste_prete.set()
        journal.info("Caméras disponibles : %s", disponibles)

    def _ouvrir_camera(self, index, forcer_format):
        """Ouvre la caméra (3 essais : elle peut être encore occupée juste après une fermeture)."""
        for _ in range(3):
            camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if camera.isOpened():
                if forcer_format:
                    # MJPG : nécessaire pour les hautes résolutions à 30 images/s sur la plupart des webcams.
                    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    camera.set(cv2.CAP_PROP_FRAME_WIDTH, RESOLUTION_CAMERA[0])
                    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION_CAMERA[1])
                return camera
            camera.release()
            time.sleep(0.5)
        return None

    # --- Fonctionnement interne -------------------------------------------

    def _charger_modele(self):
        aliments = donnees.aliments()
        self._noms = {a["classe_yolo"]: a["nom"] for a in aliments}
        try:
            from ultralytics import YOLOE
        except ImportError:
            self.message_modele = "Ultralytics n'est pas installé : détection désactivée (pip install ultralytics)."
            journal.warning(self.message_modele)
            return None, None
        try:
            modele = YOLOE(MODELE_YOLO)
            if PROMPTS_TEXTE:
                # "prompt_yolo" peut être un texte ou une liste de textes (ex. ["chicken", "hen", "rooster"]) :
                # chaque prompt détecté est compté pour l'aliment correspondant.
                prompts, classes = [], []
                for a in aliments:
                    textes = a["prompt_yolo"] if isinstance(a["prompt_yolo"], list) else [a["prompt_yolo"]]
                    prompts += textes
                    classes += [a["classe_yolo"]] * len(textes)
                modele.set_classes(prompts, self._encoder_prompts(modele, prompts))
            else:
                classes = [modele.names[i] for i in sorted(modele.names)]
            # Première prédiction « à vide » : la carte graphique est prête quand la page s'ouvre.
            modele.predict(np.zeros((RESOLUTION_CAMERA[1], RESOLUTION_CAMERA[0], 3), np.uint8),
                           imgsz=TAILLE_IMAGE_YOLO, device=DEVICE, verbose=False)
            return modele, classes
        except Exception as erreur:  # modèle introuvable, téléchargement impossible, etc.
            self.message_modele = f"Modèle YOLOE indisponible : {erreur}"
            journal.exception("Chargement du modèle impossible")
            return None, None

    @staticmethod
    def _encoder_prompts(modele, prompts):
        """Encodage des prompts texte (une quinzaine de secondes), gardé sur le disque.

        Le fichier porte une empreinte du modèle et des prompts : il est recalculé
        automatiquement dès que la liste des aliments change.
        """
        import torch
        empreinte = hashlib.sha1(json.dumps([MODELE_YOLO, prompts]).encode()).hexdigest()[:12]
        fichier = DOSSIER_DONNEES / f"cache_prompts_{empreinte}.pt"
        if fichier.exists():
            return torch.load(fichier)
        encodage = modele.get_text_pe(prompts)
        for ancien in DOSSIER_DONNEES.glob("cache_prompts_*.pt"):
            ancien.unlink()
        torch.save(encodage.cpu(), fichier)
        return encodage

    def _page_ouverte(self):
        return time.monotonic() - self._derniere_activite < DELAI_ARRET_CAMERA

    def _boucle(self):
        self.etat = "chargement"
        self._publier(self._image_message("Chargement du modèle..."), Counter())
        self._tester_cameras()  # avant le modèle : la liste des caméras est disponible tout de suite
        modele, classes = self._charger_modele()
        sans_image = set()  # caméras qui n'ont rien envoyé avec le format forcé : on les ouvrira sans réglage

        while self._actif:
            if self._liste_demandee.is_set():
                self._tester_cameras()
            # Caméra éteinte et carte graphique au repos tant que la page détection n'est pas ouverte.
            if not self._page_ouverte():
                self.etat = "en veille"
                time.sleep(0.1)
                continue

            index = self.camera_index
            self._publier(self._image_message("Démarrage de la caméra..."), Counter())
            camera = self._ouvrir_camera(index, forcer_format=index not in sans_image)
            if camera is None:
                self._attendre_autre_camera(index, f"Caméra n°{index} introuvable")
                continue

            self.resolution = (int(camera.get(cv2.CAP_PROP_FRAME_WIDTH)), int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            journal.info("Caméra %s ouverte en %sx%s", index, *self.resolution)
            self.etat = "détection" if modele else "caméra seule"
            self.message_camera = ""
            images_recues, echecs = 0, 0
            try:
                while self._actif and self.camera_index == index and self._page_ouverte():
                    ok, image = camera.read()
                    if not ok:
                        echecs += 1
                        if echecs > 60:  # environ 3 secondes sans image
                            break
                        time.sleep(0.05)
                        continue
                    images_recues, echecs = images_recues + 1, 0
                    detections = self._detecter(modele, classes, image) if modele else []
                    comptes = Counter(classe for classe, _, _ in detections)
                    self._publier(self._dessiner(image, detections), comptes)
            finally:
                camera.release()
                journal.info("Caméra %s fermée", index)
                # Le groupe suivant repart d'un comptage vide.
                with self._verrou:
                    self._historique.clear()
                    self._comptes = {}
                time.sleep(0.3)  # laisse Windows libérer la caméra avant une éventuelle réouverture

            if echecs > 60:
                if images_recues == 0 and index not in sans_image:
                    sans_image.add(index)  # nouvel essai tout de suite, sans forcer MJPG ni la résolution
                    continue
                self._attendre_autre_camera(index, f"La caméra n°{index} n'envoie pas d'image")

    def _attendre_autre_camera(self, index, texte):
        self.etat = "sans caméra"
        self.message_camera = (f"{texte}. Choisissez-en une autre ou ajoutez les aliments à la main. "
                               "Si NVIDIA Broadcast est ouvert, il peut bloquer la webcam.")
        self._publier(self._image_message(texte), Counter())
        # On réessaie toutes les 2 s, ou tout de suite si une autre caméra est choisie.
        for _ in range(20):
            if not self._actif or self.camera_index != index or not self._page_ouverte():
                break
            time.sleep(0.1)

    def _detecter(self, modele, classes, image):
        resultat = modele.predict(image, imgsz=TAILLE_IMAGE_YOLO, conf=SEUIL_CONFIANCE,
                                  device=DEVICE, verbose=False)[0]
        detections = []
        for boite in resultat.boxes:
            indice = int(boite.cls)
            if indice >= len(classes):
                continue
            x1, y1, x2, y2 = (int(v) for v in boite.xyxy[0].tolist())
            detections.append((classes[indice], float(boite.conf), (x1, y1, x2, y2)))
        return detections

    def _dessiner(self, image, detections):
        if not detections:
            return image
        # Dessin avec Pillow pour afficher les accents (OpenCV ne les gère pas).
        toile = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        dessin = ImageDraw.Draw(toile)
        for classe, confiance, (x1, y1, x2, y2) in detections:
            couleur = COULEURS[hash(classe) % len(COULEURS)]
            dessin.rectangle((x1, y1, x2, y2), outline=couleur, width=4)
            texte = f"{self._noms.get(classe, classe)} {confiance:.0%}"
            g, h, d, b = dessin.textbbox((0, 0), texte, font=self._police)
            haut = max(y1 - (b - h) - 10, 0)
            dessin.rectangle((x1, haut, x1 + (d - g) + 12, haut + (b - h) + 10), fill=couleur)
            dessin.text((x1 + 6, haut + 5 - h), texte, fill=(255, 255, 255), font=self._police)
        return cv2.cvtColor(np.array(toile), cv2.COLOR_RGB2BGR)

    def _image_message(self, texte):
        largeur, hauteur = 960, 540
        toile = Image.new("RGB", (largeur, hauteur), (27, 54, 93))
        dessin = ImageDraw.Draw(toile)
        dessin.multiline_text((largeur / 2, hauteur / 2), texte, fill=(255, 255, 255),
                              font=self._police, anchor="mm", align="center")
        return cv2.cvtColor(np.array(toile), cv2.COLOR_RGB2BGR)

    def _publier(self, image, comptes):
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with self._verrou:
            if ok:
                self._image_jpeg = jpeg.tobytes()
            # Lissage : médiane sur les dernières images, pour éviter qu'un aliment
            # apparaisse et disparaisse à chaque image.
            self._historique.append(comptes)
            classes = set().union(*self._historique)
            lisses = {}
            for classe in classes:
                valeurs = sorted(h.get(classe, 0) for h in self._historique)
                mediane = valeurs[len(valeurs) // 2]
                if mediane > 0:
                    lisses[classe] = mediane
            self._comptes = lisses


detecteur = Detecteur()
