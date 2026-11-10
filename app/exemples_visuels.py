"""Prompts visuels de YOLOE : une « empreinte » par aliment, calculée à partir de photos d'exemple.

Le prompt texte décrit l'aliment par un mot (« carrot »). Le prompt visuel le décrit par des photos
de l'objet réel (la carotte en plastique de la dînette) où l'aliment est encadré. YOLOE transforme
chaque cadre en empreinte (512 nombres), dans le même espace que les empreintes texte. On fait la
moyenne des photos d'un même aliment, et l'empreinte obtenue est ajoutée aux prompts texte :
pendant la détection, une zone de l'image proche de cette empreinte est comptée pour l'aliment.
Le modèle n'est pas réentraîné : seules les empreintes cherchées changent.

Photos : data/exemples_visuels/<classe_yolo>/*.jpg ; cadres : annotations.json (outils/annoter_exemples.py).
Les empreintes sont gardées sur le disque et recalculées dès qu'une photo ou un cadre change.
"""
import hashlib
import json
import logging

import cv2
import numpy as np

from .config import DEVICE, DOSSIER_DONNEES, MODELE_YOLO

journal = logging.getLogger(__name__)

DOSSIER_EXEMPLES = DOSSIER_DONNEES / "exemples_visuels"
FICHIER_ANNOTATIONS = DOSSIER_EXEMPLES / "annotations.json"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
TAILLE_IMAGE = 640  # taille utilisée pour calculer les empreintes (celle de l'entraînement de YOLOE)


def lire_image(chemin):
    # imdecode plutôt qu'imread : imread échoue sous Windows sur les chemins avec accents.
    return cv2.imdecode(np.fromfile(str(chemin), np.uint8), cv2.IMREAD_COLOR)


def photos(dossier):
    return sorted(p for p in dossier.iterdir() if p.suffix.lower() in EXTENSIONS)


def lire_annotations():
    if FICHIER_ANNOTATIONS.exists():
        return json.loads(FICHIER_ANNOTATIONS.read_text(encoding="utf-8"))
    return {}


def ecrire_annotations(annotations):
    FICHIER_ANNOTATIONS.write_text(json.dumps(annotations, ensure_ascii=False, indent=1), encoding="utf-8")


def _exemples(classes_connues):
    """{classe_yolo: [(chemin de la photo, cadres), ...]} pour les photos annotées avec au moins un cadre."""
    exemples = {}
    for cle, cadres in sorted(lire_annotations().items()):
        classe = cle.split("/", 1)[0]
        chemin = DOSSIER_EXEMPLES / cle
        if classe in classes_connues and isinstance(cadres, list) and cadres and chemin.exists():
            exemples.setdefault(classe, []).append((chemin, cadres))
    return exemples


def empreintes(modele, classes_connues):
    """{classe_yolo: empreinte (tenseur de 512 nombres, sur le processeur)}, vide s'il n'y a pas d'exemple."""
    import torch
    exemples = _exemples(classes_connues)
    if not exemples:
        return {}
    signature = [MODELE_YOLO, TAILLE_IMAGE] + [
        (str(chemin.relative_to(DOSSIER_EXEMPLES)), chemin.stat().st_size, chemin.stat().st_mtime_ns, cadres)
        for liste in exemples.values() for chemin, cadres in liste]
    empreinte_fichier = hashlib.sha1(json.dumps(signature).encode()).hexdigest()[:12]
    fichier = DOSSIER_DONNEES / f"cache_visuels_{empreinte_fichier}.pt"
    if fichier.exists():
        return torch.load(fichier)

    from ultralytics.models.yolo.yoloe import YOLOEVPDetectPredictor, YOLOEVPSegPredictor
    classe_predicteur = YOLOEVPSegPredictor if modele.task == "segment" else YOLOEVPDetectPredictor
    predicteur = classe_predicteur(overrides=dict(task=modele.task, mode="predict", save=False, batch=1,
                                                  imgsz=TAILLE_IMAGE, verbose=False,
                                                  **({"device": DEVICE} if DEVICE else {})))
    predicteur.setup_model(model=modele.model, verbose=False)
    resultat = {}
    for classe, liste in exemples.items():
        vecteurs = []
        for chemin, cadres in liste:
            image = lire_image(chemin)
            if image is None:
                journal.warning("Photo d'exemple illisible : %s", chemin)
                continue
            # Tous les cadres d'une photo sont le même aliment (classe 0) : une seule empreinte par photo.
            predicteur.set_prompts({"bboxes": np.array(cadres, np.float32), "cls": np.zeros(len(cadres), int)})
            vecteurs.append(predicteur.get_vpe(image)[0, 0].float().cpu())
        if vecteurs:
            resultat[classe] = torch.nn.functional.normalize(torch.stack(vecteurs).mean(0), dim=-1)
            journal.info("Empreinte visuelle « %s » : %d photo(s)", classe, len(vecteurs))

    for ancien in DOSSIER_DONNEES.glob("cache_visuels_*.pt"):
        ancien.unlink()
    torch.save(resultat, fichier)
    return resultat
