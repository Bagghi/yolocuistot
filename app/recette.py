"""Génération de la recette : « avec ces aliments, quelle recette je peux faire ? »

La recette est inventée par un modèle de langage (Gemini en ligne, ou Ollama en local),
essayés dans l'ordre de FOURNISSEURS_RECETTE. Si aucun ne répond (pas d'internet, Ollama
éteint...), une recette de secours est construite sans IA à partir d'un petit livre de recettes.
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request

from .config import (FOURNISSEURS_RECETTE, GEMINI_CLE, GEMINI_DELAI, GEMINI_DELAI_TOTAL, GEMINI_MODELES,
                     OLLAMA_DELAI, OLLAMA_MODELE, OLLAMA_URL)

journal = logging.getLogger(__name__)

MAX_PLATS = 3
ORDRE_SERVICE = {"entree": 0, "plat": 1, "dessert": 2}
LIBELLES_SERVICE = {"entree": "Entrée", "plat": "Plat", "dessert": "Dessert"}

# Aliments qui vont plutôt dans un dessert ou un goûter.
CATEGORIES_SUCREES = {"fruit", "sucre", "fruit_coque"}
LAITIERS_SUCRES = {"yaourt", "lait", "chantilly"}
TUBERCULES = {"pomme_de_terre", "igname", "patate_douce", "madere", "fruit_a_pain", "banane_plantain"}


# --- Recette inventée par un modèle de langage ------------------------------

FORMAT_REPONSE = """Réponds uniquement avec un objet JSON de cette forme :
{
  "plats": [
    {
      "service": "Plat",
      "titre": "nom court et appétissant",
      "ingredients": ["quantité + ingrédient", "..."],
      "etapes": ["étape courte", "..."]
    }
  ]
}
"service" vaut "Entrée", "Plat" ou "Dessert"."""


def construire_prompt(elements, nom_profil):
    aliments = ", ".join(f"{a['nom']} ({q} × {a['unite_portion']})" for a, q in elements)
    return (
        "Tu es un chef cuisinier créatif qui cuisine pour une cantine scolaire en Guadeloupe.\n"
        f"Une équipe de collégiens a composé une assiette avec : {aliments}.\n"
        f"Le repas est destiné à : {nom_profil}.\n\n"
        "Invente une recette originale qui COMBINE ces aliments dans un même plat. Exemple : avec une banane "
        "et du chocolat, propose un gâteau banane-chocolat, pas « banane en dessert » et « chocolat en dessert ».\n"
        "Règles :\n"
        "- Utilise tous les aliments de l'assiette.\n"
        "- Tu peux ajouter d'autres ingrédients (farine, œufs, lait, épices, oignon, huile...).\n"
        "- Fais une seule recette si possible. Si l'assiette mélange du salé et du sucré qui ne vont pas ensemble, "
        "fais un plat et un dessert (2 recettes maximum).\n"
        "- Titre court et appétissant (5 mots maximum), comme sur une carte de restaurant.\n"
        "- 4 à 6 étapes courtes, réalisables à la maison par un collégien avec l'aide d'un adulte.\n"
        "- Écris en français, avec un ton simple adapté à des adolescents, en les tutoyant.\n\n"
        + FORMAT_REPONSE
    )


def _envoyer(url, corps, entetes, delai):
    requete = urllib.request.Request(url, data=json.dumps(corps).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **entetes})
    with urllib.request.urlopen(requete, timeout=delai) as reponse:
        return json.load(reponse)


def _demander_gemini(prompt):
    if not GEMINI_CLE:
        raise RuntimeError("pas de clé GEMINI_API_KEY")
    corps = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
    }
    fin = time.monotonic() + GEMINI_DELAI_TOTAL
    for modele in GEMINI_MODELES:
        restant = fin - time.monotonic()
        if restant < 3:
            break
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent"
        try:
            reponse = _envoyer(url, corps, {"x-goog-api-key": GEMINI_CLE}, min(GEMINI_DELAI, restant))
            return reponse["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as erreur:
            # 503 : surchargé, 429 : quota du modèle atteint, 404 : modèle retiré -> modèle suivant.
            if erreur.code not in (404, 429, 503):
                raise
            journal.warning("Gemini %s : erreur %s, essai du modèle suivant", modele, erreur.code)
    raise RuntimeError("aucun modèle Gemini disponible")


def _demander_ollama(prompt):
    corps = {"model": OLLAMA_MODELE, "prompt": prompt, "format": "json", "stream": False,
             "options": {"temperature": 0.9}}
    return _envoyer(f"{OLLAMA_URL}/api/generate", corps, {}, OLLAMA_DELAI)["response"]


FOURNISSEURS = {"gemini": _demander_gemini, "ollama": _demander_ollama}


def _lire_recette(texte):
    """Vérifie la réponse du modèle et la met au format de l'application."""
    donnees = json.loads(texte)
    plats = donnees["plats"] if isinstance(donnees, dict) and "plats" in donnees else [donnees]
    propres = []
    for p in plats[:MAX_PLATS]:
        # On retire la numérotation éventuelle (« 1. », « 2) ») : la page numérote déjà les étapes.
        liste = lambda cle: [re.sub(r"^\s*\d+\s*[.)-]\s*", "", str(x)).strip()
                             for x in (p.get(cle) or []) if str(x).strip()]
        if not p.get("titre") or not liste("etapes"):
            continue
        service = str(p.get("service", "Plat")).strip().capitalize()
        propres.append({
            "service": service if service in ("Entrée", "Plat", "Dessert") else "Plat",
            "titre": str(p["titre"]).strip(),
            "ingredients": liste("ingredients") + liste("en_plus"),
            "etapes": liste("etapes"),
        })
    if not propres:
        raise ValueError("réponse sans recette exploitable")
    return propres


# --- Petits outils de français ----------------------------------------------

def _de(nom):
    """« de riz », « d'igname », « de haricots » (h aspiré)."""
    if nom[0].lower() in "aeiouyéèêàâîïôûœ" or (nom.startswith("h") and not nom.startswith(("haricot", "houmous", "hot"))):
        return "d'" + nom
    return "de " + nom


# Pour l'article : on regarde le premier mot du nom (« patate douce » -> « patate »).
SINGULIERS_EN_S = {"ananas", "maïs", "houmous", "jus"}
MASCULINS_EN_E = {"beurre", "fromage", "concombre", "gingembre", "crabe", "vinaigre", "pamplemousse", "madère", "pancake"}
FEMININS_AUTRES = {"tortilla", "polenta", "pizza", "quesadilla", "salsa", "chantilly", "noix"}
H_ASPIRES = ("haricot", "houmous", "hot", "hamburger")


def _le(nom):
    """Nom précédé de son article : « le riz », « la tomate », « les lentilles », « l'igname »."""
    premier = nom.split()[0].split("-")[0]
    singulier_noix = premier == "noix" and " de " in nom and not nom.startswith("noix de Saint")
    if premier.endswith(("s", "x")) and premier not in SINGULIERS_EN_S and not singulier_noix:
        return "les " + nom
    voyelle = nom[0].lower() in "aeiouéèêàâîïôûœ" or (nom.startswith("h") and not nom.startswith(H_ASPIRES))
    if voyelle:
        return "l'" + nom
    feminin = (premier.endswith("e") and premier not in MASCULINS_EN_E) or premier in FEMININS_AUTRES
    return ("la " if feminin else "le ") + nom


def _les(noms):
    return _enumerer(_le(n) for n in noms)


def _enumerer(noms):
    noms = list(noms)
    if len(noms) <= 1:
        return "".join(noms)
    return ", ".join(noms[:-1]) + " et " + noms[-1]


def _majuscule(texte):
    return texte[:1].upper() + texte[1:]


def _quantite(aliment, quantite):
    if quantite == 1:
        return f"{aliment['nom']} : {aliment['unite_portion']}"
    return f"{aliment['nom']} : {quantite} portions ({aliment['unite_portion']} chacune)"


# --- Recette de secours, sans IA : livre de recettes ------------------------------------------------------
# "requis" : une liste de groupes ; il faut au moins un aliment de chaque groupe.
# Un groupe contient des classes d'aliments ou "@categorie" (tous les aliments de cette catégorie).
# Dans les étapes, {g0}, {g1}... sont remplacés par les aliments trouvés pour chaque groupe.

LIVRE = [
    {"titre": "Bananes rôties au chocolat", "service": "dessert",
     "requis": [["banane"], ["chocolat", "mousse_chocolat"]],
     "en_plus": ["1 noix de beurre", "1 pincée de cannelle"],
     "etapes": ["Préchauffer le four à 180 °C (avec un adulte).",
                "Ouvrir les bananes en deux dans la longueur, sans enlever la peau.",
                "Glisser des carrés de chocolat dans la fente avec une pincée de cannelle.",
                "Enfourner 10 minutes, jusqu'à ce que le chocolat soit fondu.",
                "Déguster à la cuillère, directement dans la peau !"]},
    {"titre": "Crêpes à la banane", "service": "dessert",
     "requis": [["crepe", "pancake"], ["banane"]],
     "en_plus": ["1 noix de beurre", "1 pincée de cannelle"],
     "etapes": ["Couper la banane en rondelles et la faire dorer 2 minutes à la poêle avec le beurre et la cannelle.",
                "Réchauffer {g0} dans la poêle.",
                "Garnir avec les rondelles de banane, puis plier et servir tiède."]},
    {"titre": "Smoothie banane-fraise", "service": "dessert",
     "requis": [["banane"], ["fraise"]],
     "en_plus": ["1 verre de lait", "quelques glaçons"],
     "etapes": ["Éplucher la banane et équeuter les fraises.",
                "Tout mettre dans un blender avec le lait et les glaçons.",
                "Mixer 1 minute et servir bien frais dans un grand verre."]},
    {"titre": "Blanc-manger coco", "service": "dessert",
     "requis": [["noix_coco"], ["lait", "yaourt"]],
     "en_plus": ["1 sachet de gélatine ou d'agar-agar", "1 gousse de vanille", "le zeste d'un citron vert"],
     "etapes": ["Râper la noix de coco.",
                "Faire chauffer {g1} avec la coco râpée, la vanille et le zeste de citron vert (avec un adulte).",
                "Ajouter la gélatine en remuant, puis verser dans des petits pots.",
                "Laisser prendre au moins 3 heures au réfrigérateur."]},
    {"titre": "Salade de fruits exotiques", "service": "dessert",
     "requis": [["ananas", "mangue", "papaye", "noix_coco", "banane", "orange", "mandarine", "kiwi", "pasteque", "melon"],
                ["ananas", "mangue", "papaye", "noix_coco", "pasteque", "melon", "kiwi"]],
     "en_plus": ["le jus d'un citron vert", "quelques feuilles de menthe"],
     "etapes": ["Éplucher et couper {g0} et {g1} en petits cubes.",
                "Mélanger dans un saladier avec le jus de citron vert et la menthe ciselée.",
                "Réserver 30 minutes au frais avant de servir."]},
    {"titre": "Fondue de fruits au chocolat", "service": "dessert",
     "requis": [["chocolat"], ["@fruit"]],
     "en_plus": ["3 cuillères à soupe de lait", "des pics en bois"],
     "etapes": ["Couper {g1} en morceaux et les piquer sur des pics en bois.",
                "Faire fondre le chocolat avec le lait au bain-marie (avec un adulte).",
                "Tremper les brochettes de fruits dans le chocolat fondu."]},
    {"titre": "Yaourt croquant aux fruits", "service": "dessert",
     "requis": [["yaourt"], ["@fruit"]],
     "en_plus": ["1 cuillère à café de miel", "quelques flocons d'avoine"],
     "etapes": ["Couper {g1} en petits morceaux.",
                "Dans un verre, alterner des couches de yaourt, de fruits et de flocons d'avoine.",
                "Terminer par un filet de miel."]},
    {"titre": "Riz et pois rouges créole", "service": "plat",
     "requis": [["riz"], ["haricots_rouges"]],
     "en_plus": ["1 oignon", "2 gousses d'ail", "1 branche de thym", "1 cive", "1 filet d'huile"],
     "etapes": ["Faire revenir l'oignon, l'ail et la cive émincés dans un filet d'huile.",
                "Ajouter les haricots rouges cuits et le thym, laisser mijoter 5 minutes.",
                "Ajouter le riz et deux fois son volume d'eau salée.",
                "Couvrir et cuire 15 minutes à feu doux, jusqu'à ce que l'eau soit absorbée."]},
    {"titre": "Colombo de poulet", "service": "plat",
     "requis": [["poulet"], ["pomme_de_terre", "christophine", "courgette", "aubergine", "igname", "carotte", "giraumon"]],
     "en_plus": ["2 cuillères à soupe de poudre à colombo", "1 oignon", "2 gousses d'ail", "le jus d'un citron vert", "1 filet d'huile"],
     "etapes": ["Couper le poulet en morceaux et le mariner 15 minutes avec le citron vert, l'ail et la poudre à colombo.",
                "Faire dorer le poulet avec l'oignon dans une cocotte avec un filet d'huile.",
                "Couper {g1} en morceaux, les ajouter dans la cocotte et couvrir d'eau.",
                "Laisser mijoter 30 minutes à feu doux (avec un adulte).",
                "Servir bien chaud."]},
    {"titre": "Blaff de poisson", "service": "plat",
     "requis": [["poisson"], ["citron_vert", "citron"]],
     "en_plus": ["1 oignon", "2 gousses d'ail", "1 bouquet de persil et de cive", "1 piment (facultatif)"],
     "etapes": ["Presser {g1} et faire mariner le poisson 30 minutes dans le jus avec l'ail écrasé.",
                "Porter à ébullition une casserole d'eau avec l'oignon, les herbes et le piment.",
                "Plonger le poisson et cuire 8 minutes à petits frémissements.",
                "Servir le poisson avec un peu de bouillon et un filet de citron."]},
    {"titre": "Court-bouillon de poisson", "service": "plat",
     "requis": [["poisson", "chatrou", "crevettes"], ["tomate"]],
     "en_plus": ["1 oignon", "2 gousses d'ail", "1 branche de thym", "le jus d'un citron vert", "1 filet d'huile"],
     "etapes": ["Faire mariner {g0} 15 minutes avec le citron vert et l'ail.",
                "Faire revenir l'oignon et les tomates coupées dans un filet d'huile.",
                "Ajouter un verre d'eau et le thym, puis {g0}.",
                "Laisser mijoter 10 minutes à feu doux."]},
    {"titre": "Fricassée de chatrou", "service": "plat",
     "requis": [["chatrou"]],
     "en_plus": ["1 oignon", "2 gousses d'ail", "1 branche de thym", "le jus d'un citron vert", "1 filet d'huile"],
     "etapes": ["Faire cuire le chatrou 30 minutes à l'eau bouillante (avec un adulte), puis le couper en morceaux.",
                "Faire revenir l'oignon et l'ail dans un filet d'huile.",
                "Ajouter le chatrou, le thym et un verre d'eau, laisser mijoter 15 minutes.",
                "Arroser de citron vert au moment de servir."]},
    {"titre": "Poisson grillé et bananes plantains", "service": "plat",
     "requis": [["poisson", "saumon"], ["banane_plantain"]],
     "en_plus": ["le jus d'un citron vert", "1 filet d'huile", "sel et poivre"],
     "etapes": ["Arroser {g0} de citron vert, saler et poivrer.",
                "Couper la banane plantain en rondelles et la faire dorer à la poêle dans un filet d'huile.",
                "Griller {g0} 4 minutes de chaque côté.",
                "Servir le poisson avec les bananes plantains bien dorées."]},
    {"titre": "Migan de fruit à pain", "service": "plat",
     "requis": [["fruit_a_pain"]],
     "en_plus": ["1 oignon", "1 cive", "1 branche de thym", "1 noix de beurre"],
     "etapes": ["Couper le fruit à pain en morceaux et le cuire 20 minutes à l'eau salée avec le thym.",
                "Faire revenir l'oignon et la cive dans le beurre.",
                "Écraser le fruit à pain à la fourchette avec un peu d'eau de cuisson.",
                "Mélanger avec l'oignon et servir chaud."]},
    {"titre": "Gratin de christophine", "service": "plat",
     "requis": [["christophine"], ["fromage", "creme", "beurre", "lait"]],
     "en_plus": ["1 gousse d'ail", "1 pincée de muscade", "un peu de chapelure"],
     "etapes": ["Préchauffer le four à 200 °C (avec un adulte).",
                "Cuire la christophine en dés 15 minutes à l'eau, puis l'écraser avec l'ail et la muscade.",
                "Mélanger avec {g1} et verser dans un plat à gratin.",
                "Parsemer de chapelure et enfourner 20 minutes, jusqu'à ce que le dessus soit doré."]},
    {"titre": "Velouté de giraumon", "service": "entree",
     "requis": [["giraumon"]],
     "en_plus": ["1 oignon", "1 pomme de terre", "1 cube de bouillon", "1 pincée de muscade"],
     "etapes": ["Éplucher et couper le giraumon et la pomme de terre en morceaux.",
                "Les cuire 20 minutes dans de l'eau avec l'oignon et le bouillon.",
                "Mixer jusqu'à obtenir une soupe bien lisse (avec un adulte).",
                "Ajouter une pincée de muscade et servir chaud."]},
    {"titre": "Pâtes à la bolognaise", "service": "plat",
     "requis": [["pates"], ["boeuf", "steak_hache", "boulettes"], ["tomate"]],
     "en_plus": ["1 oignon", "1 gousse d'ail", "1 pincée d'herbes de Provence", "1 filet d'huile"],
     "etapes": ["Faire revenir l'oignon et l'ail dans un filet d'huile, puis ajouter {g1} émietté.",
                "Ajouter les tomates coupées et les herbes, laisser mijoter 15 minutes.",
                "Pendant ce temps, cuire les pâtes dans de l'eau bouillante salée.",
                "Égoutter les pâtes et les servir nappées de sauce."]},
    {"titre": "Pâtes jambon-fromage", "service": "plat",
     "requis": [["pates"], ["jambon"], ["fromage"]],
     "en_plus": ["2 cuillères à soupe de crème", "poivre"],
     "etapes": ["Cuire les pâtes dans de l'eau bouillante salée, puis les égoutter.",
                "Couper le jambon en lanières et le fromage en dés.",
                "Mélanger les pâtes chaudes avec le jambon, le fromage et la crème. Poivrer et servir."]},
    {"titre": "Riz cantonais", "service": "plat",
     "requis": [["riz"], ["oeuf"], ["petits_pois", "jambon", "crevettes", "carotte"]],
     "en_plus": ["1 cuillère à soupe de sauce soja", "1 cive", "1 filet d'huile"],
     "etapes": ["Cuire le riz à l'avance et le laisser refroidir.",
                "Battre l'œuf et le cuire en omelette fine, puis le couper en lanières.",
                "Faire sauter le riz à la poêle avec {g2}, la cive et la sauce soja.",
                "Ajouter les lanières d'omelette et servir chaud."]},
    {"titre": "Curry de pois chiches", "service": "plat",
     "requis": [["pois_chiches"], ["tomate", "legumes_verts", "courgette", "aubergine", "poivron", "carotte"]],
     "en_plus": ["1 oignon", "1 cuillère à soupe de curry", "10 cl de lait de coco", "1 filet d'huile"],
     "etapes": ["Faire revenir l'oignon et le curry dans un filet d'huile.",
                "Couper {g1} en morceaux, les ajouter et cuire 5 minutes.",
                "Ajouter les pois chiches et le lait de coco, laisser mijoter 10 minutes.",
                "Servir bien chaud."]},
    {"titre": "Dal de lentilles", "service": "plat",
     "requis": [["lentilles"], ["riz", "pain", "pita", "tortilla"]],
     "en_plus": ["1 oignon", "1 cuillère à café de curry", "10 cl de lait de coco"],
     "etapes": ["Faire revenir l'oignon et le curry dans une casserole.",
                "Ajouter les lentilles, le lait de coco et un verre d'eau, laisser mijoter 20 minutes.",
                "Servir le dal avec {g1}."]},
    {"titre": "Omelette aux légumes", "service": "plat",
     "requis": [["oeuf", "omelette"], ["@legume"]],
     "en_plus": ["1 filet d'huile", "sel et poivre"],
     "etapes": ["Couper {g1} en petits morceaux et les faire revenir 5 minutes dans un filet d'huile.",
                "Battre les œufs avec le sel et le poivre.",
                "Verser les œufs sur les légumes et cuire à feu doux jusqu'à ce que l'omelette soit prise.",
                "Plier l'omelette en deux et servir."]},
    {"titre": "Guacamole maison", "service": "entree",
     "requis": [["avocat"], ["tomate", "oignon", "citron_vert", "citron", "piment", "cive"]],
     "en_plus": ["sel", "quelques chips de maïs ou du pain pour tremper"],
     "etapes": ["Écraser l'avocat à la fourchette.",
                "Hacher finement {g1}, puis mélanger avec l'avocat et une pincée de sel.",
                "Bien mélanger et servir aussitôt."]},
    {"titre": "Tartine avocat-œuf", "service": "plat",
     "requis": [["pain", "baguette", "toast", "pain_mais"], ["avocat"], ["oeuf"]],
     "en_plus": ["le jus d'un demi-citron", "sel et poivre"],
     "etapes": ["Faire griller {g0}.",
                "Écraser l'avocat avec le citron, le sel et le poivre, puis l'étaler sur le pain.",
                "Cuire l'œuf (dur ou au plat) et le poser sur la tartine."]},
    {"titre": "Burger maison", "service": "plat",
     "requis": [["petit_pain", "pain", "hamburger"], ["steak_hache", "boeuf", "steak", "poulet"],
                ["salade", "tomate", "fromage", "oignon", "cornichon"]],
     "en_plus": ["1 cuillère de ketchup ou de moutarde"],
     "etapes": ["Cuire {g1} à la poêle, 3 minutes de chaque côté.",
                "Couper le pain en deux et le faire toaster.",
                "Monter le burger : pain, sauce, viande, puis {g2}.",
                "Refermer et déguster aussitôt."]},
    {"titre": "Wraps au poulet", "service": "plat",
     "requis": [["tortilla", "pita"], ["poulet", "dinde"]],
     "en_plus": ["1 cuillère de yaourt ou de fromage frais", "1 pincée d'épices"],
     "etapes": ["Couper {g1} en lanières et le faire dorer à la poêle avec les épices.",
                "Réchauffer {g0} quelques secondes à la poêle.",
                "Garnir avec la viande et la sauce, puis rouler bien serré."]},
    {"titre": "Sandwich jambon-crudités", "service": "plat",
     "requis": [["baguette", "pain", "petit_pain"], ["jambon"]],
     "en_plus": ["1 noix de beurre"],
     "etapes": ["Ouvrir {g0} en deux et le tartiner de beurre.",
                "Garnir avec le jambon et les crudités de l'assiette.",
                "Refermer et couper en deux."]},
]


# --- Choix des recettes -----------------------------------------------------

def _correspond(groupe, aliment):
    return aliment["classe_yolo"] in groupe or f"@{aliment['categorie']}" in groupe


def _essayer(recette, restants):
    """Aliments de l'assiette utilisés par la recette, groupe par groupe (None si elle ne convient pas)."""
    pris, par_groupe = set(), []
    dernier = len(recette["requis"]) - 1
    for i, groupe in enumerate(recette["requis"]):
        trouves = [c for c, (a, _) in restants.items() if c not in pris and _correspond(groupe, a)]
        if not trouves:
            return None
        # Le dernier groupe (garniture) et les catégories entières prennent tous les aliments qui conviennent ;
        # les autres groupes n'en prennent qu'un (l'ingrédient principal).
        if i != dernier and not any(g.startswith("@") for g in groupe):
            trouves = trouves[:1]
        pris.update(trouves)
        par_groupe.append(trouves)
    return par_groupe


def _depuis_livre(recette, par_groupe, restants):
    noms = {f"g{i}": _les(restants[c][0]["nom"] for c in classes) for i, classes in enumerate(par_groupe)}
    classes = [c for groupe in par_groupe for c in groupe]
    return {
        "titre": recette["titre"],
        "service": recette["service"],
        "classes": classes,
        "en_plus": list(recette["en_plus"]),
        "etapes": [e.format(**noms) for e in recette["etapes"]],
    }


def _est_sucre(aliment):
    return aliment["categorie"] in CATEGORIES_SUCREES or aliment["classe_yolo"] in LAITIERS_SUCRES


def _plat_sale(elements):
    """Un plat qui cuisine ensemble tous les aliments salés restants."""
    par_cat = {}
    for a, _ in elements:
        par_cat.setdefault(a["categorie"], []).append(a)
    noms = lambda cat: [a["nom"] for a in par_cat.get(cat, [])]
    proteines, legumineuses, feculents = noms("proteine"), noms("legumineuse"), noms("feculent")
    legumes, plats, autres = noms("legume"), noms("plat"), noms("laitier") + noms("condiment")
    avec_legumes = (" aux légumes" if len(legumes) > 1 else f" et {legumes[0]}") if legumes else ""
    service, en_plus = "plat", ["1 filet d'huile", "sel et poivre"]
    geres = set()  # catégories cuisinées par l'assemblage : leur préparation de base n'est pas répétée

    if plats:
        titre = f"{_majuscule(plats[0])} maison"
        etapes = [f"Garnir avec {_les(legumes)} en fines tranches."] if legumes else []
        geres = {"legume"}
    elif legumineuses:
        titre = f"Curry {_de(legumineuses[0])}{avec_legumes}"
        en_plus += ["1 oignon", "1 cuillère à café de curry", "10 cl de lait de coco"]
        etapes = ["Faire revenir l'oignon et le curry dans un filet d'huile.",
                  f"Ajouter {_les(legumineuses + legumes + proteines)}, le lait de coco et un peu d'eau.",
                  "Laisser mijoter 15 minutes à feu doux."]
        geres = {"legumineuse", "legume"}
    elif proteines and legumes:
        titre = f"Sauté {_de(proteines[0])}{avec_legumes}"
        en_plus += ["1 gousse d'ail", "1 cuillère à soupe de sauce soja"]
        etapes = [f"Couper {_les(proteines)} en morceaux et les faire dorer à la poêle avec l'ail.",
                  f"Couper {_les(legumes)} en lamelles, les ajouter et faire sauter 5 minutes.",
                  "Ajouter la sauce soja et bien mélanger."]
        geres = {"proteine", "legume"}
    elif proteines and feculents:
        titre = f"Bol {_de(proteines[0])} et {feculents[0]}"
        en_plus += ["1 gousse d'ail", "1 cuillère à soupe de sauce soja", "1 cive"]
        etapes = [f"Couper {_les(proteines)} en morceaux et les faire dorer à la poêle avec l'ail et la sauce soja.",
                  f"Servir sur {_les(feculents)}, parsemé de cive émincée."]
        geres = {"proteine"}
    elif proteines:
        titre = f"Brochettes {_de(proteines[0])}" + (f" et {proteines[1]}" if len(proteines) > 1 else "")
        en_plus += ["le jus d'un citron vert", "1 pincée d'épices", "des pics en bois"]
        etapes = [f"Couper {_les(proteines)} en cubes et les faire mariner 15 minutes avec le citron et les épices.",
                  "Les piquer sur des pics en bois.",
                  "Les cuire à la poêle ou au four en les retournant, jusqu'à cuisson complète."]
        geres = {"proteine"}
    elif feculents and legumes:
        titre = f"Wok {_de(feculents[0])}{avec_legumes}"
        en_plus += ["1 gousse d'ail", "1 cuillère à soupe de sauce soja"]
        etapes = [f"Couper {_les(legumes)} en lamelles et les faire sauter 5 minutes au wok avec l'ail.",
                  f"Ajouter {_les(feculents)} et la sauce soja, puis mélanger 2 minutes."]
        geres = {"legume"}
    elif feculents:
        tubercule = next((a for a in par_cat["feculent"] if a["classe_yolo"] in TUBERCULES), None)
        if tubercule:
            titre = f"Purée {_de(tubercule['nom'])}"
            en_plus += ["1 noix de beurre", "1 pincée de muscade"]
            etapes = [f"Écraser {_les(feculents)} à la fourchette avec le beurre et la muscade."]
        else:
            titre = f"{_majuscule(feculents[0])} à l'ail et aux herbes"
            en_plus += ["1 gousse d'ail", "1 pincée d'herbes de Provence"]
            etapes = [f"Faire revenir l'ail et les herbes dans un filet d'huile, puis y mélanger {_les(feculents)}."]
    elif legumes:
        service = "entree"
        titre = f"Salade {_de(legumes[0])}" + (f" et {legumes[1]}" if len(legumes) == 2 else " composée" if len(legumes) > 2 else "")
        en_plus = ["1 cuillère à soupe d'huile", "1 cuillère à café de vinaigre", "1 pincée de sel"]
        etapes = ["Préparer une vinaigrette avec l'huile, le vinaigre et le sel.",
                  f"Mélanger {_les(legumes)} avec la vinaigrette au moment de servir."]
    else:
        titre = f"Assiette {_de(autres[0])}" if autres else "Assiette du chef"
        etapes = []
    if autres and (plats or legumineuses or proteines or feculents or legumes):
        etapes.append(f"Ajouter {_les(autres)} au moment de servir.")

    # Les préparations de base (cuire le riz...) d'abord, puis l'assemblage.
    preparations = [a["preparation"] for a, _ in elements
                    if a.get("preparation") and a["categorie"] not in geres]
    return {"titre": titre, "service": service, "en_plus": en_plus,
            "classes": [a["classe_yolo"] for a, _ in elements],
            "etapes": preparations + etapes + ["Dresser joliment et servir."]}


def _dessert(elements):
    """Un dessert avec tous les aliments sucrés restants."""
    fruits = [a["nom"] for a, _ in elements if a["categorie"] == "fruit"]
    sucres = [a["nom"] for a, _ in elements if a["categorie"] == "sucre"]
    laitiers = [a["nom"] for a, _ in elements if a["classe_yolo"] in LAITIERS_SUCRES]
    coques = [a["nom"] for a, _ in elements if a["categorie"] == "fruit_coque"]
    en_plus = []

    if sucres:
        titre = f"{_majuscule(sucres[0])} aux fruits" if fruits else f"{_majuscule(sucres[0])} maison"
        etapes = [f"Disposer {_les(sucres)} dans une assiette."]
        if fruits:
            etapes.append(f"Couper {_les(fruits)} en morceaux et les disposer par-dessus.")
        if laitiers:
            etapes.append(f"Servir avec {_les(laitiers)}.")
    elif len(fruits) >= 2:
        titre = "Salade de fruits"
        en_plus = ["le jus d'un citron", "quelques feuilles de menthe"]
        etapes = [f"Couper {_les(fruits)} en petits morceaux.",
                  "Mélanger avec le jus de citron et la menthe, puis réserver au frais."]
        if laitiers:
            etapes.append(f"Servir avec {_les(laitiers)}.")
    elif fruits and laitiers:
        titre = f"Smoothie {fruits[0]}"
        en_plus = ["quelques glaçons", "1 cuillère à café de miel"]
        etapes = [f"Mixer {_le(fruits[0])} avec {_les(laitiers)}, le miel et les glaçons.",
                  "Servir bien frais dans un grand verre."]
    elif fruits:
        titre = f"Brochettes {_de(fruits[0])} caramélisées"
        en_plus = ["1 cuillère à soupe de sucre roux", "des pics en bois"]
        etapes = [f"Couper {_le(fruits[0])} en gros morceaux et les piquer sur des pics.",
                  "Les saupoudrer de sucre roux et les faire dorer 3 minutes à la poêle."]
    else:
        titre = f"Coupe {_de((laitiers + coques)[0])}"
        etapes = [f"Servir {_les(laitiers)} dans une jolie coupe."] if laitiers else []
    if coques:
        etapes.append(f"Parsemer {_les(coques)} concassé(es) par-dessus.")
    return {"titre": titre, "service": "dessert", "en_plus": en_plus,
            "classes": [a["classe_yolo"] for a, _ in elements], "etapes": etapes}


def _accompagner(plat, elements):
    """Ajoute des aliments restants à un plat existant (« Servir avec... »)."""
    plat["classes"] += [a["classe_yolo"] for a, _ in elements]
    etape = f"Servir avec {_les(a['nom'] for a, _ in elements)}."
    if plat["etapes"] and plat["etapes"][-1].startswith(("Dresser", "Servir")):
        plat["etapes"].insert(-1, etape)
    else:
        plat["etapes"].append(etape)


def _recette_secours(ingredients, aliments_par_classe, profil):
    restants = {i["classe"]: (aliments_par_classe[i["classe"]], i["quantite"])
                for i in ingredients if i["classe"] in aliments_par_classe}
    tout = dict(restants)

    # 1. Recettes du livre : celle qui utilise le plus d'aliments d'abord.
    plats = []
    while restants and len(plats) < MAX_PLATS:
        meilleure = None
        for recette in LIVRE:
            par_groupe = _essayer(recette, restants)
            if par_groupe and (meilleure is None or sum(map(len, par_groupe)) > sum(map(len, meilleure[1]))):
                meilleure = (recette, par_groupe)
        if meilleure is None:
            break
        plat = _depuis_livre(*meilleure, restants)
        plats.append(plat)
        for c in plat["classes"]:
            restants.pop(c)

    # 2. Les aliments restants : un plat salé et/ou un dessert.
    sales = [e for e in restants.values() if not _est_sucre(e[0])]
    sucres = [e for e in restants.values() if _est_sucre(e[0])]
    for groupe, fabrique in ((sales, _plat_sale), (sucres, _dessert)):
        if not groupe:
            continue
        meme_service = [p for p in plats if p["service"] == ("dessert" if fabrique is _dessert else "plat")]
        if len(plats) >= MAX_PLATS or meme_service:
            # Pas de nouveau plat : ces aliments accompagnent un plat existant.
            _accompagner((meme_service or plats)[-1], groupe)
        else:
            plats.append(fabrique(groupe))

    plats.sort(key=lambda p: ORDRE_SERVICE[p["service"]])
    for p in plats:
        p["ingredients"] = [_quantite(*tout[c]) for c in p.pop("classes")] + p.pop("en_plus")
        p["service"] = LIBELLES_SERVICE[p["service"]]

    principal = next((p for p in plats if p["service"] == "Plat"), plats[0] if plats else None)
    return {
        "titre": principal["titre"] if principal else "Assiette du chef",
        "pour": profil["nom"],
        "plats": plats,
        "source": "secours",
    }


def generer_recette(ingredients, aliments_par_classe, profil):
    elements = [(aliments_par_classe[i["classe"]], i["quantite"])
                for i in ingredients if i["classe"] in aliments_par_classe]
    prompt = construire_prompt(elements, profil["nom"])
    for nom in FOURNISSEURS_RECETTE:
        try:
            plats = _lire_recette(FOURNISSEURS[nom](prompt))
        except Exception as erreur:  # pas d'internet, Ollama éteint, quota dépassé, réponse illisible...
            journal.warning("Recette : %s indisponible (%s)", nom, erreur)
            continue
        plats.sort(key=lambda p: ["Entrée", "Plat", "Dessert"].index(p["service"]))
        principal = next((p for p in plats if p["service"] == "Plat"), plats[0])
        return {"titre": principal["titre"], "pour": profil["nom"], "plats": plats, "source": nom}
    return _recette_secours(ingredients, aliments_par_classe, profil)
