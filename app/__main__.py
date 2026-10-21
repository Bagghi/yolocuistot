"""Lancement : python -m app  puis ouvrir http://localhost:8000"""
import uvicorn

from .config import HOTE, PORT

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=HOTE, port=PORT)
