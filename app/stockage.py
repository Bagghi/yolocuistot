"""Enregistrement des scores dans data/scores.json.

Toute la lecture/écriture passe par ce module : pour passer à SQLite,
il suffira de réécrire ces deux fonctions.
"""
import json
import os
import threading
import time

from .config import FICHIER_SCORES

_verrou = threading.Lock()


def lire_scores():
    if not FICHIER_SCORES.exists():
        return []
    with open(FICHIER_SCORES, encoding="utf-8") as f:
        return json.load(f)


def ajouter_score(entree):
    with _verrou:
        scores = lire_scores()
        scores.append(entree)
        temporaire = FICHIER_SCORES.with_suffix(".tmp")
        with open(temporaire, "w", encoding="utf-8") as f:
            json.dump(scores, f, ensure_ascii=False, indent=2)
        # Écriture atomique : le fichier n'est jamais à moitié écrit.
        # Quelques essais, car OneDrive peut verrouiller brièvement le fichier.
        for essai in range(5):
            try:
                os.replace(temporaire, FICHIER_SCORES)
                break
            except PermissionError:
                if essai == 4:
                    raise
                time.sleep(0.2)
