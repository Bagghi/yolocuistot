// Page de résultat : score, détail par catégorie, cahier des charges et recette.

const partie = Partie.lire();
if (!partie.equipe || !partie.profil_id || !partie.ingredients) location.replace("/");

document.getElementById("nom-equipe").textContent = partie.equipe;

// Couleur de la jauge selon la part de l'objectif manquée ou dépassée (affichage seulement).
function couleurEcart(ecart, objectif) {
  const part = objectif > 0 ? ecart / objectif : 1;
  if (part <= 0.15) return "var(--vert)";
  if (part <= 0.4) return "var(--ambre)";
  return "var(--rouge)";
}

function animerScore(cible) {
  const element = document.getElementById("score");
  const debut = performance.now();
  const duree = 1200;
  function etape(maintenant) {
    const t = Math.min((maintenant - debut) / duree, 1);
    const adouci = 1 - Math.pow(1 - t, 3);
    element.textContent = nombre(cible * adouci, 1);
    if (t < 1) requestAnimationFrame(etape);
  }
  requestAnimationFrame(etape);
}

function afficher(resultat, profil) {
  animerScore(resultat.score);
  document.getElementById("score-base").textContent =
    `Écart total : ${nombre(resultat.ecart_total, 1)} g sur ${nombre(resultat.somme_objectifs, 1)} g d'objectifs, ` +
    `soit ${nombre(resultat.score_base, 1)} % de réussite.`;
  document.getElementById("bonus").innerHTML = texteBonus(resultat)
    ? `<p class="bandeau-bonus oui">⭐ Bonus ${texteBonus(resultat)} : de toutes les fiches, c'est bien la vôtre dont votre assiette est la plus proche !</p>`
    : `<p class="bandeau-bonus non">Votre assiette se rapproche plutôt de la fiche « ${esc(resultat.profil_plus_proche.nom)} ». Pas de bonus cette fois !</p>`;

  // Jauges : l'objectif est placé aux deux tiers de la piste (150 % = piste pleine).
  const libelles = Object.fromEntries(CATEGORIES);
  document.getElementById("jauges").innerHTML = resultat.detail.map((d) => {
    const ratio = d.objectif > 0 ? d.total / d.objectif : 0;
    const largeur = Math.min(ratio / 1.5, 1) * 100;
    return `
      <div>
        <div class="jauge-entete">
          <span>${libelles[d.categorie] || esc(d.categorie)}</span>
          <span>${nombre(d.total, 1)} g / ${nombre(d.objectif)} g <small class="ecart">(écart ${nombre(d.ecart, 1)} g)</small></span>
        </div>
        <div class="jauge-piste" role="img" aria-label="${libelles[d.categorie]} : ${nombre(d.total, 1)} grammes sur ${nombre(d.objectif)}">
          <div class="jauge-barre" style="width:${largeur}%;background:${couleurEcart(d.ecart, d.objectif)}"></div>
          <div class="jauge-objectif"></div>
        </div>
      </div>`;
  }).join("");

  const cdc = resultat.cahier_des_charges;
  document.getElementById("cahier").innerHTML = cdc.depassements.length
    ? cdc.depassements.map((d) =>
        `<p class="cdc-ligne">⚠️ ${esc(d.nom)} : ${d.quantite} portions (maximum ${d.portion_max})</p>`).join("")
    : '<p class="cdc-ligne">✅ Portions maximales respectées</p>';

  document.getElementById("carte-profil").innerHTML = profil ? carteProfil(profil) : "";

  document.getElementById("chargement").hidden = true;
  document.getElementById("contenu").hidden = false;
}

// --- Recette : générée par l'IA pendant que les élèves regardent leur score ---

function messagesAttente(resultat) {
  const noms = resultat.ingredients.map((i) => i.nom);
  const auHasard = () => noms[Math.floor(Math.random() * noms.length)];
  return [
    "Le chef regarde votre assiette…",
    `Il réfléchit à une idée avec vos ${noms.length} aliment${noms.length > 1 ? "s" : ""}…`,
    `Que faire avec ${auHasard()} ? Il a une idée !`,
    "Il sort les casseroles…",
    `Il découpe ${auHasard()}…`,
    "Il goûte la sauce… encore un peu de sel !",
    "Il invente un nom pour votre recette…",
    "Il dresse l'assiette avec soin…",
    "Encore quelques secondes, ça sent bon…",
  ];
}

function lancerAttente(resultat) {
  const messages = messagesAttente(resultat);
  const zone = document.getElementById("message-attente");
  const barre = document.getElementById("barre-progression");
  const debut = performance.now();
  let indice = 0;
  zone.textContent = messages[0];
  const minuteur = setInterval(() => {
    indice = Math.min(indice + 1, messages.length - 1);
    zone.classList.remove("apparition");
    void zone.offsetWidth;  // relance l'animation d'apparition
    zone.textContent = messages[indice];
    zone.classList.add("apparition");
  }, 3500);
  // La barre avance vite au début puis ralentit, sans jamais atteindre 100 % avant la fin.
  let anime = true;
  (function avancer() {
    if (!anime) return;
    const secondes = (performance.now() - debut) / 1000;
    barre.style.width = `${95 * (1 - Math.exp(-secondes / 15))}%`;
    requestAnimationFrame(avancer);
  })();
  return () => {
    anime = false;
    clearInterval(minuteur);
    barre.style.width = "100%";
  };
}

function afficherRecette(recette) {
  document.getElementById("attente-recette").hidden = true;
  document.getElementById("recette").innerHTML = htmlRecette(recette);
  const sources = {
    gemini: "Recette inventée par une IA (Gemini) à partir de votre assiette.",
    ollama: "Recette inventée par une IA qui tourne sur cet ordinateur (Ollama) à partir de votre assiette.",
  };
  document.getElementById("source-recette").textContent =
    sources[recette.source] || "Recette proposée à partir de votre assiette.";
  document.getElementById("bouton-terminer").disabled = false;
}

async function chargerRecette(resultat) {
  if (resultat.recette) {
    afficherRecette(resultat.recette);
    return;
  }
  const arreterAttente = lancerAttente(resultat);
  try {
    const recette = await api("/api/recette", { id: resultat.id });
    arreterAttente();
    Partie.ecrire({ resultat: { ...resultat, recette } });
    setTimeout(() => afficherRecette(recette), 300);
  } catch (erreur) {
    arreterAttente();
    document.getElementById("message-attente").textContent = `Recette impossible : ${erreur.message}`;
  }
}

document.getElementById("bouton-terminer").addEventListener("click", async (e) => {
  const bouton = e.currentTarget;
  bouton.disabled = true;
  try {
    await api("/api/terminer", { id: Partie.lire().resultat.id });
    Partie.vider();
    location.href = "/";
  } catch (erreur) {
    bouton.disabled = false;
    const zone = document.getElementById("erreur");
    zone.textContent = `Enregistrement impossible : ${erreur.message}`;
    zone.hidden = false;
  }
});

async function demarrer() {
  try {
    const profils = await api("/api/profils");
    let resultat = partie.resultat;
    if (!resultat) {
      resultat = await api("/api/resultat", {
        equipe: partie.equipe,
        profil_id: partie.profil_id,
        ingredients: partie.ingredients,
      });
      Partie.ecrire({ resultat });
    }
    afficher(resultat, profils.find((p) => p.id === partie.profil_id));
    chargerRecette(resultat);
  } catch (erreur) {
    document.getElementById("chargement").hidden = true;
    const zone = document.getElementById("erreur");
    zone.innerHTML = `Oups : ${esc(erreur.message)}. <a class="lien" href="/detection">Revenir à la détection</a>`;
    zone.hidden = false;
  }
}

demarrer();
