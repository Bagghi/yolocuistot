"""Lancement : python -m app  puis ouvrir http://localhost:8000"""
import logging

import psutil
import uvicorn

from .config import COEURS_CPU, HOTE, PORT

if __name__ == "__main__":
    # Messages de l'application (caméra, vitesse de détection...) affichés dans le terminal.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if COEURS_CPU:
        try:
            psutil.Process().cpu_affinity(COEURS_CPU)
        except (ValueError, psutil.Error) as erreur:  # cœurs absents sur cet ordinateur
            logging.warning("Cœurs %s non utilisables (%s) : Windows choisit.", COEURS_CPU, erreur)
    # access_log=False : pas de ligne par requête (la page détection interroge le serveur deux fois par seconde).
    uvicorn.run("app.main:app", host=HOTE, port=PORT, access_log=False)
