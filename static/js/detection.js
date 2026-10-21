// Page de détection : flux vidéo et liste des aliments.
// Chaque aliment détecté par YOLO s'ajoute à la liste ; les élèves ajustent ensuite avec −, + et ✕.
// Un aliment retiré avec ✕ revient dans la liste si YOLO le détecte à nouveau.

const partie = Partie.lire();
if (!partie.equipe || !partie.profil_id) location.replace("/");

let aliments = {};            // classe_yolo -> fiche aliment
const assiette = {};          // classe_yolo -> quantité
const ajustes = new Set();    // aliments dont la quantité a été réglée à la main : YOLO n'y touche plus

const liste = document.getElementById("liste");
const boutonValider = document.getElementById("bouton-valider");

document.getElementById("nom-equipe").textContent = partie.equipe;

function afficherAssiette() {
  const classes = Object.keys(assiette).filter((c) => aliments[c]);
  if (!classes.length) {
    liste.innerHTML = '<li class="liste-vide">Posez vos aliments sous la caméra…</li>';
  } else {
    liste.innerHTML = classes.map((classe) => {
      const aliment = aliments[classe];
      const quantite = assiette[classe];
      const depasse = quantite > aliment.portion_max;
      return `
        <li data-classe="${esc(classe)}" class="${depasse ? "depasse" : ""}">
          <span class="nom">${esc(aliment.nom)}
            <small>${esc(aliment.unite_portion)}${depasse ? ` · portion max : ${aliment.portion_max}` : ""}</small>
          </span>
          <button type="button" class="bouton bouton-petit" data-action="moins" aria-label="Retirer un ${esc(aliment.nom)}">−</button>
          <span class="quantite">${quantite}</span>
          <button type="button" class="bouton bouton-petit" data-action="plus" aria-label="Ajouter un ${esc(aliment.nom)}">+</button>
          <button type="button" class="bouton bouton-petit bouton-danger" data-action="retirer" aria-label="Retirer ${esc(aliment.nom)} de la liste">✕</button>
        </li>`;
    }).join("");
  }
  boutonValider.disabled = classes.length === 0;
}

liste.addEventListener("click", (e) => {
  const bouton = e.target.closest("button[data-action]");
  if (!bouton) return;
  const classe = bouton.closest("li").dataset.classe;
  const action = bouton.dataset.action;
  ajustes.add(classe);
  if (action === "plus") assiette[classe] = Math.min(assiette[classe] + 1, 20);
  if (action === "moins") assiette[classe] -= 1;
  if (action === "retirer" || assiette[classe] <= 0) {
    delete assiette[classe];
    ajustes.delete(classe);
  }
  afficherAssiette();
});

document.getElementById("bouton-ajouter").addEventListener("click", () => {
  const classe = document.getElementById("choix-aliment").value;
  if (!classe) return;
  ajustes.add(classe);
  assiette[classe] = Math.min((assiette[classe] || 0) + 1, 20);
  afficherAssiette();
});

boutonValider.addEventListener("click", () => {
  const ingredients = Object.entries(assiette)
    .filter(([classe]) => aliments[classe])
    .map(([classe, quantite]) => ({ classe, quantite }));
  if (!ingredients.length) return;
  Partie.ecrire({ ingredients, resultat: null });
  location.href = "/resultat";
});

// Un aliment détecté est ajouté à la liste et y reste, même s'il sort du champ de la caméra.
// Tant que les élèves n'ont pas réglé sa quantité, elle suit le nombre maximum vu par YOLO.
function fusionnerDetections(comptes) {
  let change = false;
  for (const [classe, nombreVu] of Object.entries(comptes)) {
    if (!aliments[classe] || ajustes.has(classe)) continue;
    const quantite = Math.max(assiette[classe] || 0, nombreVu);
    if (assiette[classe] !== quantite) {
      assiette[classe] = quantite;
      change = true;
    }
  }
  return change;
}

async function rafraichirDetections() {
  try {
    const reponse = await api("/api/detections");
    document.getElementById("etat-camera").textContent = reponse.message || "";
    document.getElementById("resolution").textContent =
      reponse.resolution && reponse.etat !== "sans caméra" ? `${reponse.resolution[0]} × ${reponse.resolution[1]}` : "";
    if (fusionnerDetections(reponse.comptes)) afficherAssiette();
  } catch {
    document.getElementById("etat-camera").textContent = "Serveur injoignable…";
  }
}

// --- Choix de la caméra -------------------------------------------------------

const choixCamera = document.getElementById("choix-camera");

async function chargerCameras() {
  try {
    const { actuelle, disponibles } = await api("/api/cameras");
    const indices = disponibles.includes(actuelle) ? disponibles : [actuelle, ...disponibles];
    choixCamera.innerHTML = indices.map((i) =>
      `<option value="${i}" ${i === actuelle ? "selected" : ""}>Caméra ${i}${disponibles.includes(i) ? "" : " (introuvable)"}</option>`
    ).join("");
  } catch {
    choixCamera.innerHTML = "<option>Caméras indisponibles</option>";
  }
}

choixCamera.addEventListener("change", () => {
  api("/api/camera", { index: Number(choixCamera.value) }).catch(() => {});
});

async function demarrer() {
  const [listeProfils, listeAliments] = await Promise.all([api("/api/profils"), api("/api/aliments")]);
  aliments = Object.fromEntries(listeAliments.map((a) => [a.classe_yolo, a]));

  const profil = listeProfils.find((p) => p.id === partie.profil_id);
  document.getElementById("carte-mission").innerHTML = profil ? carteProfil(profil, { compacte: true }) : "";

  document.getElementById("choix-aliment").innerHTML =
    '<option value="">— Choisir un aliment —</option>' +
    [...listeAliments]
      .sort((a, b) => a.nom.localeCompare(b.nom, "fr"))
      .map((a) => `<option value="${esc(a.classe_yolo)}">${esc(a.nom)}</option>`)
      .join("");

  afficherAssiette();
  rafraichirDetections();
  setInterval(rafraichirDetections, 500);
  chargerCameras();
}

demarrer();
