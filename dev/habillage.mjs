// CodeLab -- L'HABILLAGE DES VIDEOS DE PRESENTATION
// ---------------------------------------------------------------------------
// ROLE        Le « style pub » de « codelab video » : fond vivant, titre
//             anime, l'application dans une fenetre, sous-titres, carton de
//             fin et musique composee sur place. Il ne filme rien et ne monte
//             rien : fabriquer.mjs filme l'application, puis assemble avec
//             ffmpeg les pieces que ce module dessine.
// CHARGE PAR  fabriquer.mjs (meme dossier, /usr/local/share/codelab/video/).
// CONTRAINTES Rien ne sort du poste : ni police en ligne (Manrope est
//             copiee a cote, sinon la police du systeme), ni musique
//             telechargee, ni IA. Le conteneur n'a que 1,5 CPU et 2,5 Go :
//             seuls le titre et la fin (DUREE_INTRO + DUREE_FIN) sont
//             dessines image par image par Chromium ; la demonstration,
//             elle, est composee par ffmpeg sur des images fixes (fond qui
//             glisse, cadre, masque, sous-titres).
// STRUCTURE   mise en page (fenetre, fond qui glisse) ; la page d'habillage
//             (HTML, une fonction render(t) deterministe) ; les pieces fixes
//             (fond, cadre, masque, sous-titres) ; le titre et la fin,
//             filmes image par image ; la musique.
// CHOIX       La demonstration n'est pas redessinee image par image dans
//             Chromium (comme la pub de CodeLab) : mesure sur 4 coeurs,
//             ~4 images/s en 1080p, soit 8 min pour une minute de film --
//             plus du double sur les 1,5 CPU du poste. ffmpeg compose la
//             meme scene en temps reel. Le titre et la fin gardent le rendu
//             image par image : c'est la qu'est le mouvement.
//             La fin du titre et le debut de la fin dessinent la fenetre a
//             la place exacte ou ffmpeg la pose, avec la premiere et la
//             derniere image filmees : les raccords ne se voient pas.
// A AMELIORER Une couleur d'accent tiree de l'icone de l'application
//             (aujourd'hui : scenario.couleur, ou le turquoise de CodeLab).

import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";

export const LARGEUR = 1920, HAUTEUR = 1080, IPS = 30;
export const DUREE_INTRO = 5, DUREE_FIN = 5;
// Le fond est plus grand que l'image : il glisse doucement dessous.
const MARGE_X = 96, MARGE_Y = 54;
const FOND_L = LARGEUR + 2 * MARGE_X, FOND_H = HAUTEUR + 2 * MARGE_Y;
const BARRE = 40, RAYON = 18;

// ---------------------------------- mise en page ----------------------------------

// La place de l'application dans l'image : la plus grande qui tienne dans
// 1504 x 846 (16:9 au centre), sous une barre de fenetre.
export function fenetre(taille) {
  const l = taille?.largeur || 1600, h = taille?.hauteur || 900;
  const e = Math.min(1504 / l, 846 / h);
  const w = Math.round(l * e / 2) * 2, hc = Math.round(h * e / 2) * 2;
  const x = Math.round((LARGEUR - w) / 2), y = Math.round((HAUTEUR - hc - BARRE) / 2);
  return { x, y, w, h: hc, contenuY: y + BARRE };
}

// La position du fond a l'instant t de la demonstration. La meme formule
// en JS (titre, fin) et pour ffmpeg (demonstration) : t < 0 dans le titre,
// t > duree dans la fin, et le fond ne saute jamais.
export function posFond(t) {
  return { x: MARGE_X + 70 * Math.sin(t / 5), y: MARGE_Y + 34 * Math.sin(t / 7) };
}
export const POS_FOND_FFMPEG = `x='${MARGE_X}+70*sin(t/5)':y='${MARGE_Y}+34*sin(t/7)'`;

function echapper(t) {
  return String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
// Une donnee dans un <script> : « < » echappe, rien ne ferme la balise.
const enScript = v => JSON.stringify(v).replace(/</g, "\\u003c");
const enDataUrl = (fichier, type) => `data:${type};base64,${readFileSync(fichier).toString("base64")}`;

// ------------------------------- la page d'habillage -------------------------------
//
// Une seule page sert a tout : le fond, le cadre, le masque, les sous-titres
// (images fixes) et le titre et la fin (filmes). render(t) pose chaque
// element pour l'instant t, sans animation CSS : chaque image est
// reproductible, quelle que soit la vitesse de la capture.

function page(o) {
  const police = o.police
    ? `@font-face{font-family:Titre;src:url(${o.police}) format("woff2");font-weight:200 800}` : "";
  return `<!doctype html><html lang="fr"><meta charset="utf-8"><style>
${police}
:root{--accent:${o.accent};--fond:${o.fond}}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:${LARGEUR}px;height:${HAUTEUR}px;overflow:hidden;background:transparent;color:#eef3f8;
 font-family:Titre,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
.abs{position:absolute}
#fond{position:absolute;left:0;top:0;width:${FOND_L}px;height:${FOND_H}px;overflow:hidden;
 background:radial-gradient(1300px 900px at 50% 42%,color-mix(in srgb,var(--fond) 70%,#1a2a40) 0%,var(--fond) 72%)}
.tache{position:absolute;border-radius:50%;filter:blur(130px);opacity:.5}
#grille{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),
 linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:80px 80px;
 -webkit-mask-image:radial-gradient(ellipse at center,#000 15%,transparent 70%)}
#vignette{position:absolute;inset:0;background:radial-gradient(ellipse at center,transparent 50%,rgba(0,0,0,.6) 100%)}
#fen{position:absolute;left:${o.f.x}px;top:${o.f.y}px;width:${o.f.w}px;height:${o.f.h + BARRE}px;
 border-radius:${RAYON}px;background:#10151d;transform-origin:50% 50%;
 box-shadow:0 50px 110px -20px rgba(0,0,0,.75),0 0 0 1px rgba(255,255,255,.09),
  0 0 140px -30px color-mix(in srgb,var(--accent) 55%,transparent)}
#barre{height:${BARRE}px;display:flex;align-items:center;gap:8px;padding:0 16px;background:#161c26;
 border-radius:${RAYON}px ${RAYON}px 0 0;border-bottom:1px solid rgba(255,255,255,.06)}
#barre i{width:12px;height:12px;border-radius:50%;background:#ff5f57}
#barre i:nth-child(2){background:#febc2e}#barre i:nth-child(3){background:#28c840}
#barre span{margin:0 auto;transform:translateX(-30px);padding:5px 50px;border-radius:8px;background:#0d1117;
 color:#7d8899;font-size:14px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:60%}
#contenu{position:absolute;left:0;top:${BARRE}px;width:${o.f.w}px;height:${o.f.h}px;overflow:hidden;
 border-radius:0 0 ${RAYON}px ${RAYON}px;background:#fff}
#contenu img{width:100%;height:100%;display:block}
#masque{position:absolute;left:0;top:0;width:${o.f.w}px;height:${o.f.h}px;background:#fff;
 border-radius:0 0 ${RAYON}px ${RAYON}px}
.ligne{position:absolute;left:0;right:0;text-align:center}
.mot{display:inline-block;overflow:hidden;vertical-align:top;padding:0 .04em .3em;margin-bottom:-.18em}
.mot b{display:inline-block;font-weight:inherit}
.degrade{background:linear-gradient(100deg,color-mix(in srgb,var(--accent) 80%,#fff) 0%,#3b82f6 60%,#a78bfa 100%);
 -webkit-background-clip:text;background-clip:text;color:transparent}
#titre,#fin{font-weight:800;letter-spacing:-.045em;line-height:1.02}
#accroche,#texte{font-weight:500;color:#b7c3d2;letter-spacing:-.005em}
#signature{display:flex;align-items:center;justify-content:center;gap:12px;color:#8d9aac;font-size:22px;font-weight:600}
#signature svg{width:30px;height:30px}
#soustitre{position:absolute;left:0;right:0;bottom:40px;text-align:center}
#soustitre span{display:inline-block;max-width:1400px;padding:16px 30px;border-radius:18px;
 background:rgba(9,13,19,.84);border:1px solid rgba(255,255,255,.1);box-shadow:0 20px 50px -10px rgba(0,0,0,.6);
 font-size:34px;line-height:1.3;font-weight:600;color:#f3f7fb}
#noir{position:absolute;inset:0;background:#000;opacity:0}
</style><body>
<div id="fond">
 <div class="tache" style="width:900px;height:900px;left:-120px;top:-260px;background:var(--accent)"></div>
 <div class="tache" style="width:820px;height:820px;left:1260px;top:420px;background:#1d3f8f"></div>
 <div class="tache" style="width:620px;height:620px;left:760px;top:760px;background:color-mix(in srgb,var(--accent) 60%,#0b1a2e)"></div>
 <div id="grille"></div><div id="vignette"></div>
</div>
<div id="titre" class="ligne"></div>
<div id="accroche" class="ligne"></div>
<div id="fen"><div id="barre"><i></i><i></i><i></i><span>${echapper(o.adresse)}</span></div>
 <div id="contenu"><img id="image" alt=""></div></div>
<div id="fin" class="ligne"></div>
<div id="texte" class="ligne"></div>
<div id="signature" class="ligne"><svg viewBox="0 0 128 128" fill="none" stroke="var(--accent)" stroke-width="9"
 stroke-linecap="round" stroke-linejoin="round"><path d="M42 16H86"/><path d="M52 16V42L20 98Q13 112 30 112H98Q115 112 108 98L76 42V16"/>
 <path d="M28 80C44 68 60 88 76 78S98 72 104 80"/></svg>Fait avec CodeLab</div>
<div id="masque"></div>
<div id="soustitre"><span></span></div>
<div id="noir"></div>
<script>
"use strict";
const O = ${enScript({ titre: o.titre, accroche: o.accroche, fin: o.fin, texte: o.texte })};
const $ = id => document.getElementById(id);
const cl = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const pr = (t, a, b) => cl((t - a) / (b - a));
const eo = x => 1 - Math.pow(1 - x, 3);
const ex = x => x >= 1 ? 1 : 1 - Math.pow(2, -10 * x);
const eio = x => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const lerp = (a, b, x) => a + (b - a) * x;
function taille(texte, max, min, largeur) {
  return Math.round(cl(largeur / Math.max(1, texte.length * .56), min, max));
}
// Les mots d'un texte, chacun dans son masque ; le dernier mot du titre en degrade.
function mots(el, texte, degrade) {
  const liste = String(texte || "").split(/\\s+/).filter(Boolean);
  el.textContent = "";
  liste.forEach((m, i) => {
    const s = document.createElement("span"); s.className = "mot";
    const b = document.createElement("b"); b.textContent = m;
    if (degrade && (i === liste.length - 1)) b.className = "degrade";
    s.appendChild(b); el.appendChild(s); el.appendChild(document.createTextNode(" "));
  });
  return [...el.querySelectorAll("b")];
}
function reveler(ws, t, t0, pas, d) {
  ws.forEach((w, i) => {
    const x = ex(pr(t, t0 + i * pas, t0 + i * pas + d));
    w.style.transform = "translateY(" + (1 - x) * 110 + "%)";
    w.style.opacity = x;
    w.style.filter = x < .99 ? "blur(" + (1 - x) * 8 + "px)" : "none";
  });
}
function bloc(el, haut, px) { el.style.top = haut + "px"; el.style.fontSize = px + "px"; }
const tTitre = taille(O.titre, 150, 76, 1700), tFin = taille(O.fin, 150, 76, 1700);
const titre = mots($("titre"), O.titre, true), accroche = mots($("accroche"), O.accroche, false);
const fin = mots($("fin"), O.fin, true), texte = mots($("texte"), O.texte, false);
bloc($("titre"), 400 - tTitre / 2, tTitre); bloc($("accroche"), 430 + tTitre / 2, 40);
bloc($("fin"), 400 - tFin / 2, tFin); bloc($("texte"), 430 + tFin / 2, 40);
$("signature").style.top = "930px";
function montrer(ids) {
  for (const id of ["fond", "titre", "accroche", "fen", "fin", "texte", "signature", "masque", "soustitre", "noir"])
    $(id).style.display = ids.includes(id) ? "" : "none";
}
function fond(pos) { $("fond").style.transform = "translate(" + (-pos.x) + "px," + (-pos.y) + "px)"; }

// Le titre : le nom en cascade, l'accroche, puis la fenetre arrive et se
// pose exactement la ou la demonstration commence.
function intro(t, pos) {
  montrer(["fond", "titre", "accroche", "fen", "noir"]);
  fond(pos);
  reveler(titre, t, .3, .1, .75);
  reveler(accroche, t, 1.0, .05, .7);
  const part = eio(pr(t, 2.5, 3.3));
  for (const id of ["titre", "accroche"]) {
    $(id).style.transform = "translateY(" + (-part * 90) + "px)";
    $(id).style.opacity = 1 - part;
    $(id).style.filter = part > .01 ? "blur(" + part * 10 + "px)" : "none";
  }
  const v = ex(pr(t, 2.75, 4.5));
  $("fen").style.opacity = cl(pr(t, 2.75, 3.2));
  $("fen").style.transform = "perspective(2200px) translateY(" + (1 - v) * 420 + "px) rotateX(" +
    (1 - v) * 30 + "deg) scale(" + lerp(.84, 1, v) + ")";
  $("noir").style.opacity = 1 - pr(t, 0, .35);
}
// La fin : la fenetre s'eloigne, le nom revient, puis le noir.
function sortie(t, pos) {
  montrer(["fond", "fen", "fin", "texte", "signature", "noir"]);
  fond(pos);
  const v = eio(pr(t, 0, 1.1));
  $("fen").style.opacity = 1 - pr(t, .35, 1.05);
  $("fen").style.transform = "perspective(2200px) translateY(" + (-v * 120) + "px) rotateX(" + (-v * 22) +
    "deg) scale(" + lerp(1, .78, v) + ")";
  $("fen").style.filter = v > .01 ? "blur(" + v * 6 + "px)" : "none";
  reveler(fin, t, .8, .1, .75);
  reveler(texte, t, 1.45, .05, .7);
  $("signature").style.opacity = eo(pr(t, 2.0, 2.8)) * .9;
  $("noir").style.opacity = pr(t, ${DUREE_FIN} - .8, ${DUREE_FIN});
}
window.dessiner = (quoi, t, pos) => (quoi === "intro" ? intro : sortie)(t, pos);
window.montrer = montrer;
window.fond = fond;
</script></html>`;
}

// ---------------------------------- les pieces ----------------------------------

async function ouvrir(navigateur, o, viewport = { width: LARGEUR, height: HAUTEUR }) {
  const p = await navigateur.newPage({ viewport, deviceScaleFactor: 1 });
  await p.setContent(page(o), { waitUntil: "load" });
  await p.evaluate(() => document.fonts.ready);
  return p;
}

// Ce que toutes les pieces partagent. dossierOutils : la ou vit la police.
export function preparer({ scenario, adresse, dossierOutils }) {
  const f = fenetre(scenario.taille);
  const woff = `${dossierOutils}/manrope-latin.woff2`;
  const couleur = /^#[0-9a-f]{6}$/i.test(scenario.couleur || "") ? scenario.couleur : "#2ab5bf";
  const base = /^#[0-9a-f]{6}$/i.test(scenario.fond || "") ? scenario.fond : "#070b12";
  let chemin = "";
  try { const u = new URL(adresse); chemin = u.host.split(":")[0] === "codelab-app-manager" ? u.pathname : u.host + u.pathname; }
  catch { chemin = ""; }
  return {
    f, accent: couleur, fond: base, adresse: chemin || scenario.titre || "",
    police: existsSync(woff) ? enDataUrl(woff, "font/woff2") : "",
    titre: scenario.titre || "", accroche: scenario.accroche || "",
    fin: scenario.fin?.titre || scenario.titre || "", texte: scenario.fin?.texte || "",
  };
}

// Les images fixes de la demonstration : le fond (plus grand que l'image),
// le cadre (ombre, bord, barre ; transparent ailleurs) et le masque des
// coins arrondis du contenu.
export async function pieces(navigateur, o, dossier) {
  const fichiers = { fond: `${dossier}/fond.png`, cadre: `${dossier}/cadre.png`, masque: `${dossier}/masque.png` };
  let p = await ouvrir(navigateur, o, { width: FOND_L, height: FOND_H });
  await p.evaluate(([l, h]) => {
    for (const el of [document.documentElement, document.body]) { el.style.width = l + "px"; el.style.height = h + "px"; }
    montrer(["fond"]); fond({ x: 0, y: 0 });
  }, [FOND_L, FOND_H]);
  await p.screenshot({ path: fichiers.fond });
  await p.close();
  p = await ouvrir(navigateur, o);
  await p.evaluate(() => { montrer(["fen"]); document.getElementById("contenu").style.visibility = "hidden"; });
  await p.screenshot({ path: fichiers.cadre, omitBackground: true });
  await p.evaluate(() => { montrer(["masque"]); document.body.style.background = "#000"; });
  await p.screenshot({ path: fichiers.masque, clip: { x: 0, y: 0, width: o.f.w, height: o.f.h } });
  await p.close();
  return fichiers;
}

// Un sous-titre : la phrase de l'etape, en bas, sur fond transparent.
export async function sousTitres(navigateur, o, phrases, dossier) {
  const p = await ouvrir(navigateur, o);
  const sorties = [];
  for (const [i, phrase] of phrases.entries()) {
    if (!phrase) { sorties.push(null); continue; }
    await p.evaluate(txt => {
      montrer(["soustitre"]);
      document.querySelector("#soustitre span").textContent = txt;
    }, phrase);
    const png = `${dossier}/soustitre-${i}.png`;
    await p.screenshot({ path: png, omitBackground: true });
    sorties.push(png);
  }
  await p.close();
  return sorties;
}

// ------------------------------ le titre et la fin ------------------------------

// Filme la page image par image (render(t) puis capture), et encode a la
// volee : rien n'attend sur le disque.
async function filmer(p, quoi, image, debutFond, fichier) {
  await p.evaluate(src => { document.getElementById("image").src = src; }, enDataUrl(image, "image/png"));
  await p.waitForFunction(() => document.getElementById("image").complete);
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(IPS),
    "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
    "-pix_fmt", "yuv420p", "-threads", "2", fichier], { stdio: ["pipe", "ignore", "pipe"] });
  let erreur = "";
  ff.stderr.on("data", d => { erreur += d; });
  const fini = new Promise(r => ff.on("close", r));
  const duree = quoi === "intro" ? DUREE_INTRO : DUREE_FIN;
  for (let i = 0; i < Math.round(duree * IPS); i++) {
    const t = i / IPS;
    await p.evaluate(([q, t, pos]) => dessiner(q, t, pos), [quoi, t, posFond(debutFond + t)]);
    const jpg = await p.screenshot({ type: "jpeg", quality: 94 });
    if (!ff.stdin.write(jpg)) await new Promise(r => ff.stdin.once("drain", r));
  }
  ff.stdin.end();
  if (await fini !== 0) throw new Error(`ffmpeg (habillage ${quoi}) a echoue :\n${erreur.trim().slice(-800)}`);
}

// premiere / derniere : la premiere et la derniere image filmees, a la
// taille du contenu. dureeDemo : pour reprendre le fond la ou il en est.
export async function titreEtFin(navigateur, o, { premiere, derniere, dureeDemo, dossier }) {
  const p = await ouvrir(navigateur, o);
  const intro = `${dossier}/intro.mp4`, fin = `${dossier}/fin.mp4`;
  await filmer(p, "intro", premiere, -DUREE_INTRO, intro);
  await filmer(p, "fin", derniere, dureeDemo, fin);
  await p.close();
  return { intro, fin };
}

// ---------------------------------- la musique ----------------------------------
//
// Composee ici, a chaque video : aucun fichier a telecharger, aucun droit a
// verifier. 120 BPM, la mineur (la, fa, do, sol). Le titre monte vers un
// impact a DUREE_INTRO, la demonstration a son rythme (grosse caisse,
// charleston, basse, nappe, arpege), la fin retombe sur la nappe seule.
// Une synthese volontairement simple (oscillateurs, bruit filtre, une
// reverberation de Schroeder) : quelques secondes de calcul, pas de
// dependance.

function hasard(graine) {
  let a = graine >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
// Filtre biquadratique (formules RBJ), applique sur place.
function filtre(x, type, f, q = 0.707, sr = 44100) {
  const w = 2 * Math.PI * f / sr, c = Math.cos(w), al = Math.sin(w) / (2 * q);
  let b0, b1, b2;
  if (type === "bas") { b0 = (1 - c) / 2; b1 = 1 - c; b2 = b0; }
  else if (type === "haut") { b0 = (1 + c) / 2; b1 = -(1 + c); b2 = b0; }
  else { b0 = al; b1 = 0; b2 = -al; }
  const a0 = 1 + al, a1 = -2 * c, a2 = 1 - al;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const y = (b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
    x2 = x1; x1 = x[i]; y2 = y1; y1 = y; x[i] = y;
  }
  return x;
}
function reverb(x, sr) {
  const sortie = new Float64Array(x.length);
  for (const [ms, g] of [[29.7, .80], [37.1, .79], [41.1, .78], [43.7, .77]]) {
    const d = Math.round(ms * sr / 1000), tampon = new Float64Array(d);
    let k = 0, bas = 0;
    for (let i = 0; i < x.length; i++) {
      const y = tampon[k]; bas = bas * .3 + y * .7;
      tampon[k] = x[i] + bas * g; k = (k + 1) % d; sortie[i] += y / 4;
    }
  }
  for (const [ms, g] of [[5, .7], [1.7, .7]]) {
    const d = Math.round(ms * sr / 1000), tampon = new Float64Array(d);
    let k = 0;
    for (let i = 0; i < x.length; i++) {
      const b = tampon[k], y = -g * sortie[i] + b;
      tampon[k] = sortie[i] + g * y; k = (k + 1) % d; sortie[i] = y;
    }
  }
  return sortie;
}
const hz = n => 440 * Math.pow(2, (n - 69) / 12);

export function composer(fichier, total, { graine = 7, sr = 44100 } = {}) {
  const n = Math.ceil(total * sr), r = hasard(graine);
  const pistes = { kick: 0, caisse: 0, charley: 0, basse: 0, nappe: 0, arpege: 0, effets: 0 };
  for (const k in pistes) pistes[k] = new Float64Array(n);
  const ecrase = new Float64Array(n).fill(1);          // la nappe et la basse s'effacent sous la grosse caisse
  const poser = (piste, debut, f) => {
    const i0 = Math.round(debut * sr);
    for (let i = Math.max(0, i0); i < n; i++) { const v = f((i - i0) / sr); if (v === null) break; piste[i] += v; }
  };
  const ACCORDS = [[57, [57, 60, 64, 69]], [53, [53, 57, 60, 65]], [48, [55, 60, 64, 67]], [55, [55, 59, 62, 67]]];
  const accord = s => ACCORDS[Math.floor(Math.max(0, s) / 2) % 4];
  const debut = DUREE_INTRO, finDemo = total - DUREE_FIN;
  const scie = (f, t, ph) => 2 * ((f * t + ph) % 1) - 1;

  // la nappe : un accord par mesure, trois scies desaccordees
  for (let m = 0; m * 2 < total; m++) {
    const [, notes] = accord(m * 2), ph = notes.map(() => [r(), r(), r()]);
    poser(pistes.nappe, m * 2, t => {
      if (t > 2.4) return null;
      const e = Math.min(1, t / .4) * Math.min(1, (2.4 - t) / .5);
      let v = 0;
      notes.forEach((no, j) => { const f = hz(no); v += scie(f * .997, t, ph[j][0]) + scie(f, t, ph[j][1]) + scie(f * 1.003, t, ph[j][2]); });
      return v / 12 * e;
    });
  }
  filtre(pistes.nappe, "bas", 1500); filtre(pistes.nappe, "bas", 2200);
  // le rythme de la demonstration
  for (let b = debut; b < finDemo - .01; b += .5) {
    const k = Math.round((b - debut) / .5);
    poser(pistes.kick, b, t => t > .45 ? null
      : Math.sin(2 * Math.PI * (45 * t + 95 / 28 * (1 - Math.exp(-28 * t)))) * Math.exp(-7 * t));
    const i0 = Math.round(b * sr);
    for (let i = i0; i < Math.min(n, i0 + .3 * sr); i++) ecrase[i] = Math.min(ecrase[i], 1 - .7 * Math.exp(-(i - i0) / sr * 9));
    poser(pistes.charley, b + .25, t => t > .06 ? null : (r() * 2 - 1) * Math.exp(-70 * t) * .6);
    if (b - debut >= 4 && k % 4 === 2)
      poser(pistes.caisse, b, t => t > .3 ? null : ((r() * 2 - 1) * .8 + Math.sin(2 * Math.PI * 190 * t) * .5) * Math.exp(-16 * t));
    for (const d of [0, .25]) {
      const f = hz(accord(b + d)[0] - 24);
      poser(pistes.basse, b + d, t => t > .24 ? null
        : (Math.sin(2 * Math.PI * f * t) + .35 * Math.sin(4 * Math.PI * f * t)) * Math.min(1, t * 200) * Math.exp(-5 * t));
    }
  }
  if (finDemo - debut > 8) {
    for (let b = debut + 4; b < finDemo - .01; b += .125) {
      const [, notes] = accord(b), k = Math.round(b / .125) % 8;
      const f = hz(notes[[0, 1, 2, 3, 2, 1, 3, 2][k]] + 12), ph = r();
      poser(pistes.arpege, b, t => t > .2 ? null : (scie(f, t, ph) * .5 + Math.sin(2 * Math.PI * f * t)) * Math.exp(-18 * t) * (k % 2 ? .55 : .8));
    }
  }
  filtre(pistes.charley, "haut", 7500); filtre(pistes.caisse, "passe", 2500, .6); filtre(pistes.arpege, "bas", 3800);
  // la montee du titre, puis deux impacts : debut et fin de la demonstration
  poser(pistes.effets, .4, t => t > debut - .4 ? null : (r() * 2 - 1) * Math.pow(t / (debut - .4), 3) * .45);
  for (const [s, g] of [[debut, 1], [finDemo, .8]])
    poser(pistes.effets, s, t => t > 3 ? null
      : (Math.sin(2 * Math.PI * (38 * t + 60 / 8 * (1 - Math.exp(-8 * t)))) * Math.exp(-1.6 * t) + (r() * 2 - 1) * .25 * Math.exp(-2.4 * t)) * g);
  filtre(pistes.effets, "bas", 9000);
  // echo de l'arpege, puis reverberation de la nappe, de l'arpege et des effets
  const d = Math.round(.375 * sr);
  for (let i = n - 1; i >= d; i--) pistes.arpege[i] += pistes.arpege[i - d] * .38;
  const salle = new Float64Array(n);
  for (let i = 0; i < n; i++) salle[i] = pistes.nappe[i] * .5 + pistes.arpege[i] * .6 + pistes.effets[i] * .4 + pistes.caisse[i] * .4;
  const queue = reverb(salle, sr);
  const g = new Float64Array(2 * n);
  let max = 1e-9;
  for (let i = 0; i < n; i++) {
    const t = i / sr, e = ecrase[i];
    const fondu = Math.min(1, Math.max(0, (total - .2 - t) / 2)) * Math.min(1, t / .15);
    const m = pistes.kick[i] + pistes.caisse[i] * .5 + pistes.charley[i] * .3 + pistes.basse[i] * .5 * e
      + pistes.nappe[i] * .35 * (.5 + .5 * e) + pistes.arpege[i] * .18 + pistes.effets[i] * .7 + queue[i] * .3;
    const l = Math.tanh(m * 1.2) * fondu, s = queue[i] * .08 * fondu;
    g[2 * i] = l + s; g[2 * i + 1] = l - s;
    max = Math.max(max, Math.abs(g[2 * i]), Math.abs(g[2 * i + 1]));
  }
  // WAV 16 bits stereo, crete a -1 dB.
  const buf = Buffer.alloc(44 + 4 * n);
  buf.write("RIFF", 0); buf.writeUInt32LE(36 + 4 * n, 4); buf.write("WAVEfmt ", 8);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(2, 22); buf.writeUInt32LE(sr, 24);
  buf.writeUInt32LE(sr * 4, 28); buf.writeUInt16LE(4, 32); buf.writeUInt16LE(16, 34);
  buf.write("data", 36); buf.writeUInt32LE(4 * n, 40);
  const k = .89 / max;
  for (let i = 0; i < 2 * n; i++) buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, g[i] * k)) * 32767), 44 + 2 * i);
  writeFileSync(fichier, buf);
  return fichier;
}
