// fabriquer.mjs -- la video de presentation d'un projet, sans IA.
//
// Lance par « codelab video <projet> ». Lit video/scenario.mjs dans le
// projet, le joue dans un vrai navigateur (Chromium, par Playwright) en
// filmant l'ecran, synthetise la voix off en local (Piper, si elle est
// installee), puis monte le tout avec ffmpeg :
//
//   ecran titre  ->  la demonstration filmee  ->  ecran de fin
//
// Sorties, dans le dossier video/ du projet :
//   presentation.mp4   H.264 + AAC, 1920x1080, lisible partout (faststart)
//   affiche.jpg        une image de la demonstration, pour le lecteur
//
// Rien ne sort du serveur : ni le navigateur, ni la voix, ni le montage
// n'appellent un service en ligne. Le panneau montre ensuite le fichier
// dans le hub (bouton de lecture sur la tuile de l'application).

import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const LARGEUR = 1920, HAUTEUR = 1080, IPS = 30;
const DUREE_TITRE = 3.5, DUREE_FIN = 3, FONDU = 0.5;
const VOIX = process.env.CODELAB_VOIX || "/usr/local/share/codelab/voix/fr_FR-siwis-medium.onnx";

function dire(texte) { console.log(`[codelab video] ${texte}`); }
function echouer(texte) { console.error(`codelab video: ${texte}`); process.exit(1); }
function lancer(cmd, args, options = {}) {
  const r = spawnSync(cmd, args, { encoding: "utf-8", ...options });
  if (r.error) throw new Error(`${cmd} : ${r.error.message}`);
  if (r.status !== 0) throw new Error(`${cmd} a echoue :\n${(r.stderr || "").slice(-1500)}`);
  return r.stdout;
}
function duree(fichier) {
  return parseFloat(lancer("ffprobe", ["-v", "error", "-show_entries", "format=duration",
                                       "-of", "default=nw=1:nk=1", fichier]).trim()) || 0;
}
function echapper(t) {
  return String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
const attendre = s => new Promise(r => setTimeout(r, Math.max(0, s) * 1000));

// --------------------------------- le scenario ---------------------------------

const dossier = resolve(process.argv[2] || ".");
const fichierScenario = join(dossier, "video", "scenario.mjs");
if (!existsSync(fichierScenario)) echouer(`${fichierScenario} introuvable.`);
const scenario = (await import(pathToFileURL(fichierScenario).href + `?t=${Date.now()}`)).default;
if (!scenario || !Array.isArray(scenario.etapes) || !scenario.etapes.length)
  echouer("le scenario doit exporter par defaut { titre, adresse, etapes: [...] }.");
// L'ADRESSE, AVANT TOUT LE RESTE. Vecu : « apiRequestContext.post: connect
// ECONNREFUSED ::1:8000 ». Deux causes, chacune a dire clairement :
//  - « localhost » : Node le resout d'abord en IPv6 (::1), alors que les
//    applications ecoutent en IPv4 (127.0.0.1 ou 0.0.0.0, comme uvicorn par
//    defaut). On vise donc 127.0.0.1, ce que « localhost » voulait dire ;
//  - l'application arretee : on le verifie ici, en quelques secondes,
//    plutot qu'apres la voix off et au milieu de la premiere etape.
const HUB_INTERNE = process.env.CODELAB_VIDEO_HUB || "codelab-app-manager:9002";
async function repond(url) {
  try { await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(5000) }); return true; }
  catch { return false; }
}
// RIEN A L'ADRESSE LOCALE : LE HUB. Vecu : « Rien sur le port en question.
// je ne veux pas avoir a ouvrir le navigateur ». Une adresse locale muette
// passe par le hub (http://codelab-app-manager:9002/<projet>/) : le
// laissez-passer y demarre l'application et y connecte l'administrateur.
let parLeHub = false;
if (scenario.adresse) {
  const url = new URL(scenario.adresse);
  if (url.hostname === "localhost") url.hostname = "127.0.0.1";
  scenario.adresse = url.href;
  if (!(await repond(url))) {
    if (url.host === HUB_INTERNE)
      echouer(`le hub ne repond pas sur ${url.href} : codelab-app-manager tourne-t-il ?`);
    parLeHub = true;
    scenario.adresse = `http://${HUB_INTERNE}/${basename(dossier)}/`;
    dire(`rien sur ${url.href} : tournage par le hub, ${scenario.adresse}`);
  }
}
const taille = { width: scenario.taille?.largeur || 1600, height: scenario.taille?.hauteur || 900 };

// LE LAISSEZ-PASSER DE TOURNAGE. Vecu : « je ne veux pas avoir a me
// connecter ». Filmee par le hub (http://codelab-app-manager:9002/<app>/),
// une application privee reconnait l'administrateur sans formulaire : on
// presente au panneau la cle que le poste a recopiee (~/.codelab-tournage),
// il rend un ticket a usage unique pour CETTE application, que le
// navigateur echange contre un cookie de dix minutes avant de filmer. Voir
// « le laissez-passer de tournage » dans le proxy du panneau, et la regle
// 12 ter de SECURITE.md. Sans laissez-passer, le tournage continue : une
// application publique n'en a pas besoin.
const PANNEAU_INTERNE = process.env.CODELAB_PANNEAU_INTERNE || "http://codelab-app-manager:9001";
const FICHIER_CLE = process.env.CODELAB_TOURNAGE_FICHIER || join(homedir(), ".codelab-tournage");

// LE VRAI NOM. Vecu : « Prend le vrai nom de l'app avec les majuscules, pas
// le nom dans l'url ». Le modele de scenario met le nom du dossier
// (« wine-manager ») dans le titre, le carton de fin et la voix ; le panneau,
// lui, connait le nom affiche (« Wine Manager »). Seuls les noms techniques
// sont remplaces : un titre choisi a la main reste tel quel.
function prendreLeVraiNom(titre, techniques) {
  if (!titre) return;
  const noms = [...new Set(techniques.filter(n => n && n !== titre))];
  if (!noms.length) return;
  const motif = new RegExp(`(^|[^\\w-])(${noms.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\w-])`, "g");
  const remplacer = t => typeof t === "string" ? t.replace(motif, (_m, avant) => avant + titre) : t;
  const avant = scenario.titre;
  scenario.titre = noms.includes(scenario.titre) || !scenario.titre ? titre : remplacer(scenario.titre);
  if (scenario.fin) scenario.fin.titre = noms.includes(scenario.fin.titre) ? titre : remplacer(scenario.fin.titre);
  for (const e of scenario.etapes) e.voix = remplacer(e.voix);
  if (scenario.titre !== avant) dire(`titre : « ${scenario.titre} » (le nom de l'application au panneau).`);
}

async function demanderAuPanneau(chemin, corps) {
  const r = await fetch(`${PANNEAU_INTERNE}${chemin}`, {
    method: "POST",
    headers: { "X-CodeLab-Tournage": readFileSync(FICHIER_CLE, "utf-8").trim(),
               "Content-Type": "application/json" },
    body: JSON.stringify(corps || {}), signal: AbortSignal.timeout(60000),
  });
  const d = await r.json().catch(() => ({}));
  return r.ok && d.ticket ? d : { error: d.error || `HTTP ${r.status}` };
}

// Le ticket, d'abord par le DOSSIER du projet (vecu : « Application
// introuvable » -- l'application etait declaree sous un autre nom que son
// dossier), sinon par le nom ecrit dans l'adresse. L'adresse filmee devient
// celle que le panneau donne a l'application, le reste du chemin garde.
// 60 s d'attente : le panneau peut avoir a demarrer l'application.
async function demanderLaissezPasser(adresse) {
  if (!adresse) return null;
  const url = new URL(adresse);
  if (url.host !== HUB_INTERNE) {
    dire(`adresse locale (${url.host}) : pas de connexion CodeLab. Pour une application privee, ` +
         `filme-la par le hub (http://${HUB_INTERNE}/<app>/) : tu y seras connecte d'office.`);
    return null;
  }
  const [nom, ...reste] = url.pathname.split("/").filter(Boolean);
  if (!existsSync(FICHIER_CLE)) {
    dire(`pas de laissez-passer : ${FICHIER_CLE} absent (redemarre codelab-dev apres la mise a jour).`);
    return null;
  }
  try {
    let d = await demanderAuPanneau("/api/tournage", { dossier: dossier });
    if (d.error && nom) {
      const parNom = await demanderAuPanneau(`/api/tournage/${encodeURIComponent(nom)}`);
      d = parNom.error ? d : parNom;
    }
    if (d.error) { dire(`pas de laissez-passer : ${d.error}`); return null; }
    if (d.app !== nom) {
      url.pathname = `/${d.app}/` + (reste.length ? reste.join("/") + (url.pathname.endsWith("/") ? "/" : "") : "");
      scenario.adresse = url.href;
      dire(`application « ${d.app} » au panneau : tournage sur ${scenario.adresse}`);
    }
    if (d.demarree) dire("application demarree par le panneau pour le tournage.");
    prendreLeVraiNom(d.titre, [nom, d.app, basename(dossier)]);
    return d.ticket;
  } catch (e) {
    dire(`pas de laissez-passer : le panneau ne repond pas (${e.message}).`);
    return null;
  }
}
const ticket = await demanderLaissezPasser(scenario.adresse);
if (parLeHub && !ticket)
  echouer(`l'application ne repond pas, et le hub ne la filme pas sans laissez-passer : ` +
          "demarre-la (panneau, ou un autre terminal), puis relance « codelab video ».");
const travail = mkdtempSync(join(tmpdir(), "codelab-video-"));
const sortie = join(dossier, "video");
mkdirSync(sortie, { recursive: true });

try {
  // ------------------------------ 1. la voix off ------------------------------
  // Synthetisee AVANT de filmer : chaque etape dure au moins le temps de sa
  // phrase, et le film suit donc la voix, pas l'inverse.
  const avecVoix = scenario.voix !== false && existsSync(VOIX)
    && spawnSync("piper", ["--help"], { stdio: "ignore" }).status === 0;
  if (scenario.voix !== false && !avecVoix) dire("pas de voix off : Piper ou la voix francaise est absent.");
  const voix = [];
  for (const [i, etape] of scenario.etapes.entries()) {
    if (!avecVoix || !etape.voix) { voix.push(null); continue; }
    const texte = join(travail, `voix-${i}.txt`), wav = join(travail, `voix-${i}.wav`);
    writeFileSync(texte, etape.voix);
    lancer("piper", ["-m", VOIX, "-i", texte, "-f", wav, "--sentence-silence", "0.25"]);
    voix.push({ wav, duree: duree(wav) });
  }

  // --------------------- 2. les ecrans de titre et de fin ---------------------
  // Dessines par le navigateur (une page HTML, la police du systeme) plutot
  // qu'avec le texte de ffmpeg : accents, retours a la ligne et typographie
  // propres sans rien regler.
  const navigateur = await chromium.launch();
  const ecran = async (nom, titre, sous) => {
    const page = await navigateur.newPage({ viewport: { width: LARGEUR, height: HAUTEUR } });
    await page.setContent(`<!doctype html><meta charset="utf-8"><style>
      html,body{margin:0;height:100%}
      body{display:flex;flex-direction:column;justify-content:center;align-items:center;gap:26px;
        background:${scenario.fond || "#0f1720"};color:#fff;
        font-family:-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;text-align:center}
      h1{margin:0;font-size:118px;font-weight:800;letter-spacing:-.04em;max-width:1600px;line-height:1.02}
      p{margin:0;font-size:44px;opacity:.72;max-width:1400px;line-height:1.3}
      </style><h1>${echapper(titre)}</h1>${sous ? `<p>${echapper(sous)}</p>` : ""}`);
    const png = join(travail, `${nom}.png`);
    await page.screenshot({ path: png });
    await page.close();
    return png;
  };
  const titrePng = await ecran("titre", scenario.titre || "", scenario.accroche || "");
  const finPng = await ecran("fin", scenario.fin?.titre || scenario.titre || "", scenario.fin?.texte || "");

  // ------------------------------ 3. le tournage ------------------------------
  dire(`tournage de ${scenario.etapes.length} etape(s) sur ${scenario.adresse || "(adresse absente)"}...`);
  const contexte = await navigateur.newContext({
    viewport: taille, deviceScaleFactor: 1, locale: "fr-FR",
    baseURL: scenario.adresse, recordVideo: { dir: travail, size: taille },
  });
  // Le ticket s'echange HORS de toute page : rien n'apparait a l'image, et
  // le cookie rejoint ceux du contexte (contexte.request les partage).
  if (ticket) {
    const entree = new URL(scenario.adresse);
    entree.searchParams.set("codelab-tournage", ticket);
    const r = await contexte.request.get(entree.href, { maxRedirects: 0 });
    dire(r.status() === 302 ? "connecte par laissez-passer CodeLab (dix minutes)."
                            : `laissez-passer refuse (HTTP ${r.status()}).`);
  }
  // Un curseur VISIBLE : un navigateur sans ecran n'en dessine pas, et une
  // demonstration ou les clics viennent de nulle part se suit mal.
  await contexte.addInitScript(() => {
    addEventListener("DOMContentLoaded", () => {
      const c = document.createElement("div");
      c.style.cssText = "position:fixed;z-index:2147483647;left:0;top:0;width:22px;height:22px;" +
        "margin:-11px 0 0 -11px;border-radius:50%;background:rgba(20,20,20,.35);" +
        "border:2px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.35);pointer-events:none;" +
        "transition:transform .12s;transform:translate(-100px,-100px)";
      document.documentElement.appendChild(c);
      addEventListener("mousemove", e => { c.style.transform = `translate(${e.clientX}px,${e.clientY}px)`; }, true);
      addEventListener("mousedown", () => { c.style.background = "rgba(20,20,20,.6)"; }, true);
      addEventListener("mouseup", () => { c.style.background = "rgba(20,20,20,.35)"; }, true);
    });
  });
  const page = await contexte.newPage();
  const debutFilm = Date.now();
  const outils = {
    page,
    pause: attendre,
    // Le curseur glisse jusqu'a l'element, puis clique.
    cliquer: async (selecteur) => {
      const cible = page.locator(selecteur).first();
      await cible.scrollIntoViewIfNeeded();
      const b = await cible.boundingBox();
      if (b) await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 28 });
      await attendre(0.15);
      await cible.click();
    },
    // Frappe lettre a lettre, comme quelqu'un.
    taper: async (selecteur, texte) => {
      await outils.cliquer(selecteur);
      await page.keyboard.type(String(texte), { delay: 55 });
    },
    // Defilement doux, en pixels (negatif : vers le haut).
    defiler: async (pixels) => {
      const pas = Math.max(1, Math.round(Math.abs(pixels) / 40));
      for (let i = 0; i < pas; i++) { await page.mouse.wheel(0, Math.sign(pixels) * 40); await attendre(0.016); }
    },
    survoler: async (selecteur) => {
      const b = await page.locator(selecteur).first().boundingBox();
      if (b) await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 28 });
    },
  };
  const reperes = [];
  for (const [i, etape] of scenario.etapes.entries()) {
    const debut = (Date.now() - debutFilm) / 1000;
    reperes.push(debut);
    try {
      if (etape.geste) await etape.geste(outils);
    } catch (e) {
      throw new Error(`etape ${i + 1} : ${e.message.split("\n")[0]}`);
    }
    const voulu = Math.max(etape.duree || 0, voix[i] ? voix[i].duree + 0.5 : 0, 1.5);
    await attendre(voulu - ((Date.now() - debutFilm) / 1000 - debut));
  }
  await attendre(0.6);
  const filmBrut = await page.video().path();
  await contexte.close();
  await navigateur.close();

  // ------------------------------ 4. le montage ------------------------------
  dire("montage...");
  const echelle = `scale=${LARGEUR}:${HAUTEUR}:force_original_aspect_ratio=decrease,` +
    `pad=${LARGEUR}:${HAUTEUR}:(ow-iw)/2:(oh-ih)/2:color=0x0f1720,fps=${IPS},format=yuv420p,setsar=1`;
  const dureeDemo = duree(filmBrut);
  const filtres = [
    `[0:v]${echelle},fade=t=in:st=0:d=${FONDU},fade=t=out:st=${DUREE_TITRE - FONDU}:d=${FONDU}[t]`,
    `[1:v]${echelle},fade=t=in:st=0:d=${FONDU},fade=t=out:st=${Math.max(0, dureeDemo - FONDU)}:d=${FONDU}[d]`,
    `[2:v]${echelle},fade=t=in:st=0:d=${FONDU},fade=t=out:st=${DUREE_TITRE - FONDU}:d=${FONDU}[f]`,
    "[t][d][f]concat=n=3:v=1:a=0[v]",
  ];
  const entrees = ["-loop", "1", "-t", String(DUREE_TITRE), "-i", titrePng,
                   "-i", filmBrut,
                   "-loop", "1", "-t", String(DUREE_FIN), "-i", finPng];
  // Les voix, chacune a l'instant ou son etape commence (apres l'ecran titre).
  const pistes = [];
  voix.forEach((v, i) => {
    if (!v) return;
    entrees.push("-i", v.wav);
    const n = entrees.filter(x => x === "-i").length - 1;
    const ms = Math.round((DUREE_TITRE + reperes[i] + 0.2) * 1000);
    filtres.push(`[${n}:a]aresample=48000,adelay=${ms}|${ms},aformat=channel_layouts=stereo[a${i}]`);
    pistes.push(`[a${i}]`);
  });
  // Une musique, si le projet en fournit une (video/musique.mp3) : bas, sous
  // la voix, et en fondu a la fin.
  const total = DUREE_TITRE + dureeDemo + DUREE_FIN;
  const musique = join(sortie, "musique.mp3");
  if (existsSync(musique)) {
    entrees.push("-stream_loop", "-1", "-i", musique);
    const n = entrees.filter(x => x === "-i").length - 1;
    filtres.push(`[${n}:a]aresample=48000,volume=${pistes.length ? 0.12 : 0.5},atrim=0:${total},` +
                 `afade=t=out:st=${Math.max(0, total - 2)}:d=2,aformat=channel_layouts=stereo[m]`);
    pistes.push("[m]");
  }
  const sansSon = !pistes.length;
  if (!sansSon) filtres.push(`${pistes.join("")}amix=inputs=${pistes.length}:normalize=0,` +
                             `apad,atrim=0:${total}[a]`);
  const fichierMp4 = join(travail, "presentation.mp4");
  lancer("ffmpeg", ["-y", "-loglevel", "error", ...entrees,
    "-filter_complex", filtres.join(";"), "-map", "[v]", ...(sansSon ? [] : ["-map", "[a]"]),
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
    ...(sansSon ? [] : ["-c:a", "aac", "-b:a", "160k"]),
    "-movflags", "+faststart", "-t", String(total), fichierMp4]);
  // L'affiche : une image de la demonstration, pas l'ecran titre.
  const fichierJpg = join(travail, "affiche.jpg");
  lancer("ffmpeg", ["-y", "-loglevel", "error", "-ss", String(DUREE_TITRE + Math.min(2, dureeDemo / 2)),
                    "-i", fichierMp4, "-frames:v", "1", "-q:v", "3", fichierJpg]);
  // En dernier, et d'un coup : le hub ne voit jamais une video a moitie
  // ecrite. Copie a cote, puis renommage (le dossier temporaire peut etre
  // sur un autre disque que le projet).
  for (const [source, nom] of [[fichierJpg, "affiche.jpg"], [fichierMp4, "presentation.mp4"]]) {
    copyFileSync(source, join(sortie, `.${nom}.tmp`));
    renameSync(join(sortie, `.${nom}.tmp`), join(sortie, nom));
  }
  dire(`video/presentation.mp4 prete (${Math.round(total)} s${sansSon ? ", sans son" : ""}) :` +
       " elle apparait dans le hub, sur la tuile de l'application.");
} catch (e) {
  echouer(e.message);
} finally {
  rmSync(travail, { recursive: true, force: true });
}
