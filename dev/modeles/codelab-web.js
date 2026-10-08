/* codelab-web -- pages d'erreur, journal des exceptions et en-tetes de securite.

   Depose par « codelab new ». Pour Express, sans autre dependance.

     const codelabWeb = require("./codelab-web");
     app.use(codelabWeb.entetesDeSecurite());   // facultatif : voir plus bas
     // ... les routes de l'application ...
     codelabWeb.pagesDErreur(app);               // EN DERNIER, apres les routes

   PAGES D'ERREUR. Une 404 ou une 500 s'affiche au style de CodeLab
   (/theme.css), sans pile d'appels : la trace part dans le journal de
   l'application -- celui que le panneau montre --, jamais dans la page, ou
   elle donnerait a n'importe quel visiteur les chemins et parfois les
   secrets du code. Un client qui demande du JSON recoit {"erreur": ...}.

   EN-TETES DE SECURITE. Le panneau n'impose aux applications que nosniff et
   frame-ancestors. Ce middleware est un CHOIX de l'application : une
   politique stricte par defaut (tout depuis l'application elle-meme), que
   l'on elargit en la passant :

     codelabWeb.entetesDeSecurite({ csp: codelabWeb.CSP_DEFAUT
                                    + "; img-src 'self' data: https://tuiles.exemple" })

   Un en-tete deja pose par une route est garde. Pas de frame-ancestors ici :
   le panneau pose le sien, qui autorise l'affichage dans le hub. */
"use strict";

/* script-src sans 'unsafe-inline' : c'est ce qui rend une injection de HTML
   inoffensive. Une page qui porte ses scripts en ligne doit les sortir dans
   un fichier, ou elargir cette regle -- en le sachant. */
const CSP_DEFAUT = "default-src 'self'; base-uri 'none'; object-src 'none'; "
  + "form-action 'self'; img-src 'self' data:; font-src 'self'; "
  + "style-src 'self' 'unsafe-inline'; script-src 'self'";

const ENTETES = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

const TITRES = { 400: "Requete invalide", 401: "Connexion requise", 403: "Acces refuse",
  404: "Page introuvable", 405: "Methode non permise", 413: "Envoi trop volumineux",
  429: "Trop de requetes", 500: "Erreur interne", 502: "Service indisponible",
  503: "Service indisponible" };

const MESSAGES = { 404: "Cette adresse ne correspond a aucune page.",
  500: "Une erreur s'est produite. Elle est notee dans le journal de "
     + "l'application ; reessaie dans un instant." };

function echapper(t) {
  return String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" })[c]);
}

/* Lien relatif vers la racine de l'application : le panneau la sert sous
   /<nom>/ et lui transmet le chemin SANS ce prefixe, donc un lien « / »
   menerait au hub. On remonte d'autant de dossiers que le chemin en compte. */
function versLAccueil(chemin) {
  const profondeur = Math.max(String(chemin || "/").split("/").length - 2, 0);
  return profondeur === 0 ? "./" : "../".repeat(profondeur);
}

function page(code, titre, message, chemin = "/") {
  titre = titre || TITRES[code] || "Erreur";
  message = message || MESSAGES[code] || "";
  return `<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${code} -- ${echapper(titre)}</title>
<link rel="stylesheet" href="/theme.css">
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;
       background:var(--bg,#fff);color:var(--txt,#111);
       font:var(--t-b,15px)/1.5 var(--sans,system-ui,sans-serif)}
  main{max-width:28rem;margin:16px;padding:24px;background:var(--surface,#fff);
       border:1px solid var(--line,#ddd);border-radius:var(--r,10px)}
  .code{font-variant-numeric:tabular-nums;color:var(--dim,#666);font-size:var(--t-s,13px)}
  h1{margin:.2em 0 .4em;font-size:var(--t-l,20px)}
  a{color:var(--accent,#0a7);font-weight:600}
</style></head>
<body><main>
  <div class="code">Erreur ${code}</div>
  <h1>${echapper(titre)}</h1>
  <p>${echapper(message)}</p>
  <p><a href="${versLAccueil(chemin)}">Retour a l'accueil</a></p>
</main></body></html>
`;
}

function repondre(req, res, code) {
  res.status(code);
  if (req.accepts(["html", "json"]) === "json") {
    return res.json({ erreur: TITRES[code] || "Erreur", code });
  }
  res.type("html").send(page(code, null, null, req.path));
}

/* A appeler APRES les routes : Express essaie les middlewares dans l'ordre. */
function pagesDErreur(app) {
  app.use((req, res) => repondre(req, res, 404));
  // Quatre arguments : c'est ce qui fait de cette fonction un gestionnaire
  // d'erreur pour Express. Ne pas en retirer un, meme inutilise.
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    const code = Number(err && (err.status || err.statusCode)) || 500;
    if (code >= 500) {
      // La trace complete, dans le journal ; rien de son contenu dans la page.
      console.error(`[codelab-web] ${req.method} ${req.path} -- exception non rattrapee\n`
        + ((err && err.stack) || err));
    }
    if (res.headersSent) return req.socket && req.socket.destroy();
    repondre(req, res, code >= 400 && code < 600 ? code : 500);
  });
  return app;
}

/* Middleware : en-tetes de securite, sans ecraser ceux qu'une route a deja
   choisis. { csp: null } : pas de Content-Security-Policy. */
function entetesDeSecurite(options = {}) {
  const csp = options.csp === undefined ? CSP_DEFAUT : options.csp;
  // Poses avant la route : elle peut ensuite les remplacer par setHeader.
  return function (req, res, next) {
    for (const [nom, valeur] of Object.entries(ENTETES)) {
      if (!res.getHeader(nom)) res.setHeader(nom, valeur);
    }
    if (csp && !res.getHeader("Content-Security-Policy")) {
      res.setHeader("Content-Security-Policy", csp);
    }
    next();
  };
}

module.exports = { pagesDErreur, entetesDeSecurite, page, CSP_DEFAUT, ENTETES };
