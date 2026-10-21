"""Serveur web : pages, API et flux vidéo."""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import donnees, recette, scoring, stockage
from .config import DOSSIER_LOGOS, DOSSIER_STATIC
from .detection import detecteur


@asynccontextmanager
async def cycle_de_vie(app):
    # Charge YOLOE tout de suite ; la caméra ne s'allume que sur la page détection.
    detecteur.demarrer()
    yield
    detecteur.arreter()


app = FastAPI(title="YOLO cuisto", lifespan=cycle_de_vie)
app.mount("/static", StaticFiles(directory=DOSSIER_STATIC), name="static")
app.mount("/logos", StaticFiles(directory=DOSSIER_LOGOS), name="logos")


# --- Pages ------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def page_accueil():
    return FileResponse(DOSSIER_STATIC / "index.html")


@app.get("/detection", include_in_schema=False)
def page_detection():
    return FileResponse(DOSSIER_STATIC / "detection.html")


@app.get("/resultat", include_in_schema=False)
def page_resultat():
    return FileResponse(DOSSIER_STATIC / "resultat.html")


@app.get("/profils", include_in_schema=False)
def page_profils():
    return FileResponse(DOSSIER_STATIC / "profils.html")


# --- Fiches -----------------------------------------------------------------

@app.get("/api/profils")
def lire_profils():
    return donnees.profils()


@app.get("/api/aliments")
def lire_aliments():
    return donnees.aliments()


@app.get("/api/cahier")
def lire_cahier():
    return donnees.cahier_des_charges()


# --- Détection --------------------------------------------------------------

@app.get("/video_feed", include_in_schema=False)
async def flux_video(request: Request):
    async def images():
        derniere = None
        while not await request.is_disconnected():
            detecteur.signaler_activite()
            image = detecteur.derniere_image()
            if image is not None and image is not derniere:
                derniere = image
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + image + b"\r\n"
            await asyncio.sleep(1 / 30)

    return StreamingResponse(images(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/detections")
def lire_detections():
    detecteur.signaler_activite()
    return {
        "etat": detecteur.etat,
        "message": detecteur.message,
        "resolution": detecteur.resolution,
        "comptes": detecteur.comptes(),
    }


class DemandeCamera(BaseModel):
    index: int = Field(ge=0, le=9)


@app.get("/api/cameras")
def lire_cameras():
    return {"actuelle": detecteur.camera_index, "disponibles": detecteur.lister_cameras()}


@app.post("/api/camera")
def choisir_camera(demande: DemandeCamera):
    detecteur.changer_camera(demande.index)
    return {"actuelle": demande.index}


# --- Résultat et scores -----------------------------------------------------

class Ingredient(BaseModel):
    classe: str
    quantite: int = Field(ge=1, le=20)


class DemandeResultat(BaseModel):
    equipe: str = Field(min_length=1, max_length=40)
    profil_id: str
    ingredients: list[Ingredient] = Field(min_length=1)


class DemandeIdentifiant(BaseModel):
    id: str


# Résultats calculés mais pas encore validés par « Terminer ».
_resultats_en_attente: dict[str, dict] = {}


@app.post("/api/resultat")
def creer_resultat(demande: DemandeResultat):
    profil = donnees.profil(demande.profil_id)
    if profil is None:
        raise HTTPException(404, "Fiche profil inconnue")
    aliments = donnees.aliments_par_classe()
    ingredients = [i.model_dump() for i in demande.ingredients if i.classe in aliments]
    if not ingredients:
        raise HTTPException(400, "L'assiette est vide")

    resultat = {
        "id": uuid.uuid4().hex,
        "date": datetime.now().isoformat(timespec="seconds"),
        "equipe": demande.equipe.strip(),
        "profil_id": profil["id"],
        "profil_nom": profil["nom"],
        "ingredients": [
            {"classe": i["classe"], "nom": aliments[i["classe"]]["nom"], "quantite": i["quantite"]}
            for i in ingredients
        ],
        **scoring.calculer_score(ingredients, profil, donnees.profils(), aliments),
        "cahier_des_charges": scoring.verifier_cahier_des_charges(
            ingredients, aliments, donnees.cahier_des_charges()),
        "recette": None,  # générée ensuite par /api/recette : le score s'affiche sans attendre l'IA
    }
    _resultats_en_attente[resultat["id"]] = resultat
    return resultat


def _resultat_en_attente(identifiant):
    resultat = _resultats_en_attente.get(identifiant)
    if resultat is None:
        raise HTTPException(404, "Résultat introuvable (le serveur a peut-être redémarré)")
    return resultat


@app.post("/api/recette")
def creer_recette(demande: DemandeIdentifiant):
    resultat = _resultat_en_attente(demande.id)
    if resultat["recette"] is None:
        resultat["recette"] = recette.generer_recette(
            resultat["ingredients"], donnees.aliments_par_classe(), donnees.profil(resultat["profil_id"]))
    return resultat["recette"]


@app.post("/api/terminer")
def terminer(demande: DemandeIdentifiant):
    resultat = _resultat_en_attente(demande.id)
    if resultat["recette"] is None:
        raise HTTPException(409, "La recette n'est pas encore prête")
    stockage.ajouter_score(_resultats_en_attente.pop(demande.id))
    return {"ok": True}


@app.get("/api/scores")
def lire_scores():
    return sorted(stockage.lire_scores(), key=lambda s: s["score"], reverse=True)
