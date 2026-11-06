// Fonctions partagées par toutes les pages.

const CATEGORIES = [
  ["glucides", "Glucides"],
  ["fibres", "Fibres"],
  ["proteines", "Protéines"],
  ["lipides", "Lipides"],
];

// État de la partie en cours (équipe, fiche, assiette), conservé entre les pages.
const Partie = {
  lire() {
    try { return JSON.parse(sessionStorage.getItem("yolocuisto")) || {}; } catch { return {}; }
  },
  ecrire(modifs) {
    const partie = { ...Partie.lire(), ...modifs };
    try { sessionStorage.setItem("yolocuisto", JSON.stringify(partie)); } catch {}
    return partie;
  },
  vider() {
    try { sessionStorage.removeItem("yolocuisto"); } catch {}
  },
};

function esc(valeur) {
  return String(valeur ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function nombre(valeur, decimales = 0) {
  return Number(valeur).toLocaleString("fr-FR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimales,
  });
}

function euros(valeur) {
  return Number(valeur).toLocaleString("fr-FR", { style: "currency", currency: "EUR" });
}

// GET si pas de corps, POST JSON sinon.
async function api(url, corps) {
  const options = corps === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corps),
  };
  const reponse = await fetch(url, options);
  if (!reponse.ok) {
    let message = reponse.statusText;
    try { message = (await reponse.json()).detail || message; } catch {}
    throw new Error(typeof message === "string" ? message : "Requête refusée");
  }
  return reponse.json();
}

// Texte du bonus d'un résultat, ou "" s'il n'y en a pas.
// Les scores enregistrés avant le passage à « +50 points » avaient un bonus multiplicateur (×2).
function texteBonus(resultat) {
  if (resultat.bonus_points > 0) return `+${nombre(resultat.bonus_points)} points`;
  if (resultat.bonus > 1) return `×${resultat.bonus}`;
  return "";
}

// Allergie et interdit de la fiche, en toutes lettres.
function contraintesProfil(profil) {
  const allergie = profil.allergie ? `Allergie : ${profil.allergie}` : "Sans allergie";
  return `${allergie} · Interdit : ${profil.interdit}`;
}

// La carte complète est l'image de la fiche ; la version compacte (page caméra) reprend ses valeurs en texte.
function carteProfil(profil, { cliquable = false, selectionnee = false, compacte = false } = {}) {
  const classes = ["carte-profil", compacte ? "carte-compacte" : "carte-image", cliquable && "cliquable", selectionnee && "selectionnee"]
    .filter(Boolean).join(" ");
  const attributs = cliquable
    ? `tabindex="0" role="button" aria-pressed="${selectionnee}"`
    : "";
  const valeurs = CATEGORIES.map(([cle, libelle]) => [libelle, `${nombre(profil.objectifs[cle])} g`]);
  if (compacte) {
    return `
    <article class="${classes}" data-id="${esc(profil.id)}" ${attributs}>
      <h3>${esc(profil.nom)}</h3>
      <ul class="carte-objectifs">${valeurs.map(([l, v]) => `<li><span>${l}</span><strong>${v}</strong></li>`).join("")}</ul>
      <p class="carte-contraintes">${esc(contraintesProfil(profil))}</p>
    </article>`;
  }
  const description = `Fiche ${profil.nom} (${profil.personnage}). ${profil.contexte} Objectifs de la journée : `
    + `${valeurs.map(([l, v]) => `${l} ${v}`).join(", ")}. ${contraintesProfil(profil)}.`;
  return `
    <article class="${classes}" data-id="${esc(profil.id)}" ${attributs}>
      <img src="${esc(profil.image)}" alt="${esc(description)}">
    </article>`;
}

function htmlRecette(recette) {
  if (!recette) return "";
  // Anciennes recettes enregistrées : un seul plat, sans "plats".
  const plats = recette.plats || [{ titre: recette.titre, ingredients: recette.ingredients, etapes: recette.etapes }];
  const plusieurs = plats.length > 1;
  return `
    <div class="recette">
      <p class="pour">Pour : ${esc(recette.pour)}</p>
      ${plats.map((p) => `
        <div class="plat-recette">
          ${plusieurs && p.service ? `<p class="service">${esc(p.service)}</p>` : ""}
          <h3>${esc(p.titre)}</h3>
          <h4>Ingrédients</h4>
          <ul>
            ${p.ingredients.map((i) => `<li>${esc(i)}</li>`).join("")}
            ${(p.en_plus || []).map((i) => `<li>${esc(i)}</li>`).join("")}
          </ul>
          <h4>Étapes</h4>
          <ol>${p.etapes.map((e) => `<li>${esc(e)}</li>`).join("")}</ol>
        </div>`).join("")}
    </div>`;
}
