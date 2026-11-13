"""Calcul du score de proximité entre l'assiette et le profil tiré.

1. On additionne les apports de chaque aliment (multipliés par sa quantité).
2. Écart total = somme sur les catégories de |objectif - total|, en grammes.
3. Score = (somme des objectifs - écart total) / somme des objectifs x 100 (minimum 0), plus le bonus.
   C'est un pourcentage de réussite (100 = assiette parfaite, 0 = assiette vide), comparable quelle que
   soit la fiche tirée. Sans le minimum, le score pourrait être négatif si une catégorie dépasse
   le double de son objectif.
4. Bonus de BONUS_BON_PROFIL points si le profil le plus proche de l'assiette (parmi tous les profils)
   est le profil tiré.
5. Malus : MALUS_ALLERGIE points si l'assiette contient un aliment auquel la fiche est allergique,
   MALUS_INTERDIT points si elle contient son aliment interdit (une fois chacun, quelle que soit la quantité).
   Les aliments concernés sont listés dans data/contraintes_profils.json. Le score final ne descend pas sous 0.
"""
from .config import BONUS_BON_PROFIL, CATEGORIES_SCORE, MALUS_ALLERGIE, MALUS_INTERDIT


def totaux_assiette(ingredients, aliments_par_classe):
    totaux = {c: 0.0 for c in CATEGORIES_SCORE}
    for ingredient in ingredients:
        aliment = aliments_par_classe.get(ingredient["classe"])
        if aliment is None:
            continue
        for c in CATEGORIES_SCORE:
            totaux[c] += ingredient["quantite"] * aliment["apports"].get(c, 0)
    return totaux


def ecarts(totaux, objectifs):
    """Écart en grammes de chaque catégorie, en valeur absolue (0 = objectif atteint)."""
    return {c: abs(objectifs.get(c, 0) - totaux[c]) for c in CATEGORIES_SCORE}


def erreur_globale(totaux, objectifs):
    """Écart total en grammes, toutes catégories confondues."""
    return sum(ecarts(totaux, objectifs).values())


def malus(ingredients, profil, aliments_par_classe, contraintes):
    """Allergie et interdit de la fiche trouvés dans l'assiette, avec les points retirés."""
    presents = [i["classe"] for i in ingredients if i["classe"] in aliments_par_classe]
    resultat = []
    for genre, points in (("allergie", MALUS_ALLERGIE), ("interdit", MALUS_INTERDIT)):
        texte = (profil.get(genre) or "").strip()
        if not texte:
            continue
        classes = contraintes.get(texte.lower())
        if classes is None:  # texte absent des correspondances : on cherche un aliment qui porte ce nom
            classes = [c for c, a in aliments_par_classe.items() if a["nom"].lower() == texte.lower()]
        trouves = [aliments_par_classe[c]["nom"] for c in presents if c in classes]
        if trouves:
            resultat.append({"type": genre, "texte": texte, "points": points, "aliments": trouves})
    return resultat


def calculer_score(ingredients, profil_tire, profils, aliments_par_classe, contraintes=None):
    totaux = totaux_assiette(ingredients, aliments_par_classe)
    ecarts_tire = ecarts(totaux, profil_tire["objectifs"])
    erreur = sum(ecarts_tire.values())
    somme_objectifs = sum(profil_tire["objectifs"].get(c, 0) for c in CATEGORIES_SCORE)
    score_base = max(0.0, (somme_objectifs - erreur) / somme_objectifs * 100) if somme_objectifs else 0.0

    # En cas d'égalité, le profil tiré l'emporte.
    plus_proche = min(
        profils,
        key=lambda p: (erreur_globale(totaux, p["objectifs"]), p["id"] != profil_tire["id"]),
    )
    bonus = BONUS_BON_PROFIL if plus_proche["id"] == profil_tire["id"] else 0
    penalites = malus(ingredients, profil_tire, aliments_par_classe, contraintes or {})
    points_malus = sum(m["points"] for m in penalites)

    return {
        "score": round(max(0.0, score_base + bonus - points_malus), 1),
        "score_base": round(score_base, 1),
        "somme_objectifs": round(somme_objectifs, 1),
        "bonus_points": bonus,
        "malus": penalites,
        "malus_points": points_malus,
        "ecart_total": round(erreur, 1),
        "profil_plus_proche": {"id": plus_proche["id"], "nom": plus_proche["nom"]},
        "detail": [
            {
                "categorie": c,
                "total": round(totaux[c], 1),
                "objectif": profil_tire["objectifs"].get(c, 0),
                "ecart": round(ecarts_tire[c], 1),
            }
            for c in CATEGORIES_SCORE
        ],
    }


def verifier_cahier_des_charges(ingredients, aliments_par_classe, cahier):
    """Bilan informatif (sans effet sur le score) : budget."""
    cout = 0.0
    for ingredient in ingredients:
        aliment = aliments_par_classe.get(ingredient["classe"])
        if aliment is None:
            continue
        cout += ingredient["quantite"] * aliment["prix"]
    budget = cahier["budget_par_eleve"]
    return {
        "cout": round(cout, 2),
        "budget": budget,
        "budget_respecte": cout <= budget + 1e-9,
    }
