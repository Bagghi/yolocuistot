"""Lecture des fiches (aliments, profils, cahier des charges).

Les fichiers sont relus à chaque appel : on peut corriger une valeur dans data/
sans redémarrer le serveur (sauf la liste des aliments utilisée par YOLOE).
"""
import json

from .config import DOSSIER_DONNEES


def _lire(nom_fichier):
    with open(DOSSIER_DONNEES / nom_fichier, encoding="utf-8") as f:
        return json.load(f)


def aliments():
    return _lire("aliments.json")


def aliments_par_classe():
    return {a["classe_yolo"]: a for a in aliments()}


def profils():
    return _lire("profils.json")


def profil(profil_id):
    return next((p for p in profils() if p["id"] == profil_id), None)


def cahier_des_charges():
    return _lire("cahier_des_charges.json")
