// Page d'accueil : nom d'équipe, choix de la fiche tirée, tableau des scores.

let profils = [];
let scores = [];
let profilChoisi = null;

const champEquipe = document.getElementById("equipe");
const carrousels = [...document.querySelectorAll(".carrousel")];
const boutonGo = document.getElementById("bouton-go");
const volet = document.getElementById("volet");

function afficherProfils() {
  for (const carrousel of carrousels) {
    carrousel.querySelector(".carrousel-piste").innerHTML = profils
      .filter((p) => p.public === carrousel.dataset.public)
      .map((p) => carteProfil(p, { cliquable: true, selectionnee: p.id === profilChoisi }))
      .join("");
  }
}

function mettreAJourBouton() {
  boutonGo.disabled = !(champEquipe.value.trim() && profilChoisi);
}

function choisirProfil(carte) {
  if (!carte) return;
  profilChoisi = carte.dataset.id;
  // On change seulement les classes (sans redessiner) pour garder la position des carrousels.
  document.querySelectorAll(".carrousel .carte-profil").forEach((c) => {
    const choisie = c.dataset.id === profilChoisi;
    c.classList.toggle("selectionnee", choisie);
    c.setAttribute("aria-pressed", choisie);
  });
  mettreAJourBouton();
}

// --- Carrousels des fiches (élèves, professionnels) ------------------------------

function mettreAJourFleches() {
  for (const carrousel of carrousels) {
    const piste = carrousel.querySelector(".carrousel-piste");
    carrousel.querySelector(".fleche-precedente").disabled = piste.scrollLeft <= 2;
    carrousel.querySelector(".fleche-suivante").disabled = piste.scrollLeft + piste.clientWidth >= piste.scrollWidth - 2;
  }
}

for (const carrousel of carrousels) {
  const piste = carrousel.querySelector(".carrousel-piste");
  carrousel.querySelector(".fleche-precedente").addEventListener("click", () => piste.scrollBy({ left: -piste.clientWidth * 0.8, behavior: "smooth" }));
  carrousel.querySelector(".fleche-suivante").addEventListener("click", () => piste.scrollBy({ left: piste.clientWidth * 0.8, behavior: "smooth" }));
  piste.addEventListener("scroll", mettreAJourFleches, { passive: true });
  piste.addEventListener("click", (e) => choisirProfil(e.target.closest(".carte-profil")));
  piste.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      choisirProfil(e.target.closest(".carte-profil"));
    }
  });
}
window.addEventListener("resize", mettreAJourFleches);

champEquipe.addEventListener("input", mettreAJourBouton);

document.getElementById("formulaire").addEventListener("submit", (e) => {
  e.preventDefault();
  if (boutonGo.disabled) return;
  Partie.vider();
  Partie.ecrire({ equipe: champEquipe.value.trim(), profil_id: profilChoisi });
  location.href = "/detection";
});

// --- Tableau des scores ------------------------------------------------------

async function chargerScores() {
  try {
    scores = await api("/api/scores");
  } catch {
    return;
  }
  const corps = document.getElementById("corps-scores");
  document.getElementById("scores-vide").hidden = scores.length > 0;
  corps.innerHTML = scores.map((s, i) => {
    const medaille = ["🥇", "🥈", "🥉"][i] || i + 1;
    return `
      <tr tabindex="0" data-index="${i}">
        <td class="rang">${medaille}</td>
        <td><strong>${esc(s.equipe)}</strong></td>
        <td>${esc(s.profil_nom)}</td>
        <td>${esc(s.recette?.titre)}</td>
        <td class="nombre score-cellule">${nombre(s.score, 1)}${texteBonus(s) ? " ⭐" : ""}</td>
      </tr>`;
  }).join("");
}

function ouvrirVolet(score) {
  const profil = profils.find((p) => p.id === score.profil_id);
  document.getElementById("volet-corps").innerHTML = `
    <h2 id="volet-titre">${esc(score.equipe)}</h2>
    <p class="score-geant" style="font-size:3rem;text-align:left">${nombre(score.score, 1)} <small>points</small></p>
    ${texteBonus(score) ? `<p class="bandeau-bonus oui">⭐ Bonus ${texteBonus(score)} : l'assiette correspondait bien à la fiche !</p>` : ""}
    <h3>Fiche tirée</h3>
    ${profil ? carteProfil(profil) : `<p>${esc(score.profil_nom)}</p>`}
    <div class="panneau">${htmlRecette(score.recette)}</div>`;
  volet.hidden = false;
  volet.querySelector(".volet-fermer").focus();
}

function fermerVolet() {
  volet.hidden = true;
}

document.getElementById("corps-scores").addEventListener("click", (e) => {
  const ligne = e.target.closest("tr");
  if (ligne) ouvrirVolet(scores[ligne.dataset.index]);
});
document.getElementById("corps-scores").addEventListener("keydown", (e) => {
  const ligne = e.target.closest("tr");
  if (ligne && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault();
    ouvrirVolet(scores[ligne.dataset.index]);
  }
});
volet.addEventListener("click", (e) => { if (e.target.closest("[data-fermer]")) fermerVolet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") fermerVolet(); });

async function demarrer() {
  try {
    profils = await api("/api/profils");
  } catch (erreur) {
    carrousels[0].querySelector(".carrousel-piste").innerHTML = `<p class="erreur">Impossible de charger les fiches : ${esc(erreur.message)}</p>`;
    return;
  }
  afficherProfils();
  mettreAJourFleches();
  chargerScores();
  setInterval(chargerScores, 15000);
}

demarrer();
