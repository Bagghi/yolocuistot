"""Réglages de l'application. Tout ce qu'on peut vouloir changer le jour J est ici."""
import os
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_DONNEES = RACINE / "data"
DOSSIER_STATIC = RACINE / "static"
DOSSIER_LOGOS = RACINE / "logos"
FICHIER_SCORES = DOSSIER_DONNEES / "scores.json"

# Serveur
HOTE = "127.0.0.1"
PORT = 8000

# Détection
CAMERA_INDEX = 1                  # 0 = webcam par défaut, 1 = webcam USB externe, etc.
MODELE_YOLO = "yoloe-26l-seg.pt"  # téléchargé automatiquement au premier lancement (internet requis une fois)
                                  # Tailles : n, s, m, l, x. Sur la RTX 4060 : l ≈ 47 img/s, x ≈ 32 img/s.
PROMPTS_TEXTE = True              # True : YOLOE pré-entraîné guidé par le champ "prompt_yolo" des fiches aliments.
                                  # False : modèle réentraîné dont les classes sont directement les "classe_yolo".
RESOLUTION_CAMERA = (1920, 1080)   # demandée à la caméra (sans réglage, OpenCV prend 640x480 et peut rogner l'image)
TAILLE_IMAGE_YOLO = 960          # côté le plus long de l'image vue par YOLO. 960 ou 1280 : petits objets mieux vus, mais plus lent
SEUIL_CONFIANCE = 0.25
DEVICE = None                     # None = automatique (carte graphique si disponible), "cpu" pour forcer le processeur
DELAI_ARRET_CAMERA = 5            # secondes après la fermeture de la page détection avant d'éteindre la caméra
LISSAGE_IMAGES = 10              # nombre d'images sur lesquelles on lisse le comptage en direct

# Score
CATEGORIES_SCORE = ["glucides", "fibres", "proteines", "legumineuses"]
BONUS_BON_PROFIL = 50             # points ajoutés si le profil le plus proche de l'assiette est le profil tiré

# Recette : inventée par un modèle de langage. Les fournisseurs sont essayés dans l'ordre ;
# si aucun ne répond, une recette de secours est construite sans IA (livre de recettes).
FOURNISSEURS_RECETTE = ["gemini", "ollama"]

# Gemini (en ligne, offre gratuite) : clé à créer sur https://aistudio.google.com/apikey,
# puis à mettre dans la variable d'environnement GEMINI_API_KEY (jamais dans ce fichier).
GEMINI_CLE = os.environ.get("GEMINI_API_KEY", "")
# Essayés dans l'ordre : si un modèle est surchargé (erreur 503) ou plus disponible, on passe au suivant.
GEMINI_MODELES = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash"]
GEMINI_DELAI = 25                 # secondes d'attente maximum par modèle
GEMINI_DELAI_TOTAL = 40           # secondes maximum pour Gemini, tous modèles confondus, avant de passer à Ollama

# Ollama (local, sans internet) : https://ollama.com, puis « ollama pull <modèle> ».
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODELE = "gemma3:4b"       # ~3 Go de mémoire graphique ; "qwen2.5:7b" (~5 Go) écrit un peu mieux
OLLAMA_DELAI = 90                 # le premier appel charge le modèle en mémoire : il peut être long
