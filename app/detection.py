"""Lecture de la webcam et détection des aliments avec YOLOE, dans deux threads de fond.

Le thread caméra lit les images au rythme de la webcam, y dessine les derniers cadres
trouvés et garde la dernière image annotée (JPEG). Le thread YOLO travaille à son propre
rythme sur l'image la plus récente : la vidéo reste fluide même si YOLO est plus lent.
Le serveur web lit l'image et le comptage lissé des aliments quand le navigateur les demande.
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

from . import donnees, exemples_visuels
from .config import (CAMERA_INDEX, DELAI_ARRET_CAMERA, DEMI_PRECISION, DEVICE, DOSSIER_DONNEES, EXEMPLES_VISUELS,
                     LISSAGE_IMAGES, MODELE_YOLO, PROMPTS_TEXTE, RESOLUTION_CAMERA, SEUIL_CONFIANCE,
                     SEUIL_CONFIANCE_VISUEL, TAILLE_IMAGE_YOLO)

journal = logging.getLogger(__name__)

PRECISION = 16 if DEMI_PRECISION else None  # 16 bits sur la carte graphique, sinon 32 bits

# Couleurs foncées et saturées : bien visibles sur l'image, et le texte blanc des étiquettes reste lisible.
COULEURS = [
    (220, 20, 60), (0, 110, 210), (20, 140, 60), (140, 30, 180), (215, 90, 0),
    (0, 125, 125), (190, 20, 140), (30, 50, 160), (150, 75, 0), (0, 95, 55),
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
        self._etiquettes = {}
        self._noms = {}
        self._indices_visuels = set()     # indices des prompts visuels parmi les classes du modèle
        # Échange entre le thread caméra et le thread YOLO.
        self._image_brute = None          # dernière image de la caméra, à analyser
        self._nouvelle_image = threading.Event()
        self._detections = []             # derniers cadres trouvés, dessinés sur chaque image
        self._session = 0                 # change à chaque fermeture de caméra : les résultats en cours sont ignorés
        self._nb_yolo = 0                 # nombre de détections faites, pour mesurer la vitesse de YOLO
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
                encodage = self._encoder_prompts(modele, prompts)
                if EXEMPLES_VISUELS:
                    encodage = self._ajouter_prompts_visuels(modele, aliments, prompts, classes, encodage)
                modele.set_classes(prompts, encodage)
            else:
                classes = [modele.names[i] for i in sorted(modele.names)]
            # Première prédiction « à vide » : la carte graphique est prête quand la page s'ouvre.
            modele.predict(np.zeros((RESOLUTION_CAMERA[1], RESOLUTION_CAMERA[0], 3), np.uint8),
                           imgsz=TAILLE_IMAGE_YOLO, device=DEVICE, quantize=PRECISION, verbose=False)
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

    def _ajouter_prompts_visuels(self, modele, aliments, prompts, classes, encodage):
        """Ajoute aux prompts texte une empreinte visuelle par aliment photographié (voir exemples_visuels.py).

        prompts et classes sont complétés sur place ; renvoie l'encodage complété.
        """
        import torch
        try:
            visuels = exemples_visuels.empreintes(modele, {a["classe_yolo"] for a in aliments})
        except Exception:
            journal.exception("Prompts visuels non calculés : seuls les prompts texte sont utilisés")
            return encodage
        for classe, vecteur in visuels.items():
            self._indices_visuels.add(len(prompts))
            prompts.append(f"{classe} (photos)")
            classes.append(classe)
            encodage = torch.cat([encodage, vecteur.to(encodage.device, encodage.dtype)[None, None]], dim=1)
        if visuels:
            journal.info("Prompts visuels ajoutés : %s", ", ".join(visuels))
        return encodage

    def _page_ouverte(self):
        return time.monotonic() - self._derniere_activite < DELAI_ARRET_CAMERA

    def _boucle(self):
        self.etat = "chargement"
        self._publier_image(self._image_message("Chargement du modèle..."))
        self._tester_cameras()  # avant le modèle : la liste des caméras est disponible tout de suite
        modele, classes = self._charger_modele()
        if modele:
            threading.Thread(target=self._boucle_yolo, args=(modele, classes), name="yolo", daemon=True).start()
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
            self._publier_image(self._image_message("Démarrage de la caméra..."))
            camera = self._ouvrir_camera(index, forcer_format=index not in sans_image)
            if camera is None:
                self._attendre_autre_camera(index, f"Caméra n°{index} introuvable")
                continue

            self.resolution = (int(camera.get(cv2.CAP_PROP_FRAME_WIDTH)), int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            journal.info("Caméra %s ouverte en %sx%s", index, *self.resolution)
            self.etat = "détection" if modele else "caméra seule"
            self.message_camera = ""
            images_recues, echecs = 0, 0
            debut_mesure, images_mesure, yolo_mesure = time.monotonic(), 0, self._nb_yolo
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
                    with self._verrou:
                        self._image_brute = image
                    self._nouvelle_image.set()
                    self._publier_image(self._dessiner(image, self._detections))
                    # Vitesse réelle dans le journal toutes les 10 s, pour repérer un ordinateur bridé.
                    images_mesure += 1
                    duree = time.monotonic() - debut_mesure
                    if duree >= 10:
                        journal.info("Vidéo : %.1f images/s | YOLO : %.1f détections/s",
                                     images_mesure / duree, (self._nb_yolo - yolo_mesure) / duree)
                        debut_mesure, images_mesure, yolo_mesure = time.monotonic(), 0, self._nb_yolo
            finally:
                camera.release()
                journal.info("Caméra %s fermée", index)
                # Le groupe suivant repart d'un comptage vide.
                with self._verrou:
                    self._historique.clear()
                    self._comptes = {}
                    self._image_brute = None
                    self._detections = []
                    self._session += 1
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
        self._publier_image(self._image_message(texte))
        # On réessaie toutes les 2 s, ou tout de suite si une autre caméra est choisie.
        for _ in range(20):
            if not self._actif or self.camera_index != index or not self._page_ouverte():
                break
            time.sleep(0.1)

    def _boucle_yolo(self, modele, classes):
        """Détection en continu sur l'image la plus récente de la caméra.

        Les images arrivées pendant une détection sont sautées : YOLO n'accumule pas de retard.
        """
        while self._actif:
            if not self._nouvelle_image.wait(timeout=0.5):
                continue
            self._nouvelle_image.clear()
            with self._verrou:
                image, session = self._image_brute, self._session
            if image is None:
                continue
            try:
                detections = self._detecter(modele, classes, image)
            except Exception:
                journal.exception("Erreur pendant la détection")
                time.sleep(1)
                continue
            self._nb_yolo += 1
            with self._verrou:
                if session != self._session:  # caméra fermée ou changée pendant la détection
                    continue
                self._detections = detections
                self._ajouter_comptes(Counter(classe for classe, _, _ in detections))

    def _detecter(self, modele, classes, image):
        seuil_minimum = min(SEUIL_CONFIANCE, SEUIL_CONFIANCE_VISUEL) if self._indices_visuels else SEUIL_CONFIANCE
        resultat = modele.predict(image, imgsz=TAILLE_IMAGE_YOLO, conf=seuil_minimum,
                                  device=DEVICE, quantize=PRECISION, verbose=False)[0]
        detections = []
        for boite in resultat.boxes:
            indice = int(boite.cls)
            if indice >= len(classes):
                continue
            seuil = SEUIL_CONFIANCE_VISUEL if indice in self._indices_visuels else SEUIL_CONFIANCE
            if float(boite.conf) < seuil:
                continue
            x1, y1, x2, y2 = (int(v) for v in boite.xyxy[0].tolist())
            detections.append((classes[indice], float(boite.conf), (x1, y1, x2, y2)))
        return detections

    def _dessiner(self, image, detections):
        if not detections:
            return image
        image = image.copy()  # l'image brute est peut-être en cours d'analyse par YOLO
        hauteur_image, largeur_image = image.shape[:2]
        for classe, confiance, (x1, y1, x2, y2) in detections:
            couleur = COULEURS[hash(classe) % len(COULEURS)]
            cv2.rectangle(image, (x1, y1), (x2, y2), couleur[::-1], 4)
            etiquette = self._etiquette(f"{self._noms.get(classe, classe)} {confiance:.0%}", couleur)
            x1 = max(x1, 0)
            haut = max(y1 - etiquette.shape[0], 0)
            h = min(etiquette.shape[0], hauteur_image - haut)
            l = min(etiquette.shape[1], largeur_image - x1)
            if h > 0 and l > 0:
                image[haut:haut + h, x1:x1 + l] = etiquette[:h, :l]
        return image

    def _etiquette(self, texte, couleur):
        """Étiquette (fond coloré, texte blanc), gardée en mémoire.

        Dessinée avec Pillow pour afficher les accents (OpenCV ne les gère pas).
        """
        cle = (texte, couleur)
        if cle not in self._etiquettes:
            g, h, d, b = self._police.getbbox(texte)
            toile = Image.new("RGB", (d - g + 12, b - h + 10), couleur)
            ImageDraw.Draw(toile).text((6, 5 - h), texte, fill=(255, 255, 255), font=self._police)
            self._etiquettes[cle] = cv2.cvtColor(np.array(toile), cv2.COLOR_RGB2BGR)
        return self._etiquettes[cle]

    def _image_message(self, texte):
        largeur, hauteur = 960, 540
        toile = Image.new("RGB", (largeur, hauteur), (27, 54, 93))
        dessin = ImageDraw.Draw(toile)
        dessin.multiline_text((largeur / 2, hauteur / 2), texte, fill=(255, 255, 255),
                              font=self._police, anchor="mm", align="center")
        return cv2.cvtColor(np.array(toile), cv2.COLOR_RGB2BGR)

    def _publier_image(self, image):
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self._verrou:
                self._image_jpeg = jpeg.tobytes()

    def _ajouter_comptes(self, comptes):
        """Lissage : médiane sur les dernières détections, pour éviter qu'un aliment
        apparaisse et disparaisse à chaque image. À appeler avec le verrou pris."""
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
