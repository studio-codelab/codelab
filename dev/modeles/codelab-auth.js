/* codelab-auth -- se connecter avec CodeLab, sans connexions dans tous les sens.

   Depose par « codelab create », tenu a jour par « codelab update ». Aucune dependance : Node verifie Ed25519 de
   lui-meme (module crypto).

   LA CONNEXION SILENCIEUSE, dans cet ordre, a chaque requete :
     1. la personne a deja une session dans l'application : on continue ;
     2. sinon, elle est connectee a CodeLab (jeton signe X-CodeLab-Auth,
        verifie ici) : on retrouve ou on cree son compte local, et on ouvre
        la session de l'application -- sans formulaire ;
     3. sinon : le formulaire classique de l'application (connexion ou
        creation de compte). Rien ne change pour ces visiteurs.

   Exemple (Express + express-session) :

     const codelab = require("./codelab-auth");
     app.use(codelab.connexionSilencieuse(async (u) => {
       // u = { sub, nom, role, email, email_verified }
       // Retrouver par u.sub (colonne codelab_sub). Ne relier un compte
       // local existant par l'adresse QUE si u.email_verified est vrai.
       return idUtilisateurLocal;
     }));

     // Le bouton « Se connecter avec CodeLab » :
     app.get("/connexion-codelab", (req, res) => {
       codelab.reprendre(req.session);
       res.redirect(codelab.lienConnexion(req, "/"));
     });
     // Se deconnecter de l'application seulement :
     //   codelab.deconnecter(req.session)
     // ... et de CodeLab aussi :
     //   res.redirect(codelab.lienDeconnexion(req, "/"))

   TROIS REGLES, deja tenues par ce module :
     - ne jamais croire X-CodeLab-User seul : seul le jeton signe compte ;
     - le jeton ne vit que 60 secondes : c'est un laissez-passer, pas une
       session -- l'application ouvre la sienne ;
     - apres une deconnexion de l'application, ne pas reconnecter aussitot. */
"use strict";

const crypto = require("crypto");

const CLE_SESSION = "utilisateur";
const PAUSE = "codelab_pause";

function entete(req, nom) {
  const h = (req && req.headers) || {};
  const v = h[nom.toLowerCase()];
  return Array.isArray(v) ? v[0] || "" : v || "";
}

/* La personne connectee a CodeLab, ou null. Verifie la signature,
   l'emetteur, l'application visee et l'expiration. */
function utilisateur(req) {
  const jeton = entete(req, "X-CodeLab-Auth");
  const publique = process.env.CODELAB_SSO_PUBLIC_KEY || "";
  if (!jeton || !publique) return null;
  try {
    const [h, p, s] = jeton.split(".");
    if (!h || !p || !s) return null;
    const cle = crypto.createPublicKey({
      key: { kty: "OKP", crv: "Ed25519", x: publique }, format: "jwk" });
    if (!crypto.verify(null, Buffer.from(h + "." + p), cle, Buffer.from(s, "base64url"))) {
      return null;
    }
    const c = JSON.parse(Buffer.from(p, "base64url").toString("utf8"));
    if (c.iss !== (process.env.CODELAB_SSO_ISSUER || "codelab")) return null;
    if (c.aud !== process.env.CODELAB_SSO_AUDIENCE) return null;
    if (!(Number(c.exp) >= Math.floor(Date.now() / 1000))) return null;
    return { sub: c.sub, nom: c.preferred_username || c.sub, role: c.role || "",
             email: c.email || "", email_verified: c.email_verified === true };
  } catch (e) {
    return null;
  }
}

/* Middleware Express. trouverOuCreer(u) -> identifiant local (ou null pour
   refuser). Pose req.utilisateurCodeLab dans tous les cas, et ouvre
   req.session[cle] quand une session existe. */
function connexionSilencieuse(trouverOuCreer, cle = CLE_SESSION) {
  return async function (req, res, next) {
    try {
      req.utilisateurCodeLab = utilisateur(req);
      const s = req.session;
      if (s && s[cle] == null && !s[PAUSE] && req.utilisateurCodeLab && trouverOuCreer) {
        const id = await trouverOuCreer(req.utilisateurCodeLab);
        if (id != null) s[cle] = id;
      }
      next();
    } catch (e) {
      next(e);
    }
  };
}

/* Ferme la session de l'APPLICATION, sans reconnecter aussitot : CodeLab
   reste connecte, et l'etape 2 rouvrirait la session a la requete suivante. */
function deconnecter(session, cle = CLE_SESSION) {
  if (!session) return;
  delete session[cle];
  session[PAUSE] = true;
}

/* A appeler quand la personne clique « Se connecter avec CodeLab ». */
function reprendre(session) {
  if (session) delete session[PAUSE];
}

/* L'adresse du panneau, posee par le proxy de CodeLab (jamais par le
   navigateur : le proxy retire tout en-tete X-CodeLab-* venu du client). */
function panneau(req) {
  const p = entete(req, "X-CodeLab-Panneau");
  if (/^(https?:\/\/|\/)/.test(p)) return p.endsWith("/") ? p : p + "/";
  return "/";
}

function chemin(c) {
  return encodeURI(String(c || "/").replace(/^\/+/, "")).replace(/[?#]/g, encodeURIComponent);
}

/* Le lien « Se connecter avec CodeLab » : connexion, puis retour ici. */
function lienConnexion(req, c = "/") {
  const app = process.env.CODELAB_SSO_AUDIENCE || "";
  const suite = "/ouvrir.app/" + encodeURIComponent(app) + "/" + chemin(c);
  return panneau(req) + "login?suite=" + encodeURI(suite);
}

/* Se deconnecter de CodeLab aussi (confirmation demandee), puis retour ici. */
function lienDeconnexion(req, c = "/") {
  const app = process.env.CODELAB_SSO_AUDIENCE || "";
  return panneau(req) + "deconnexion.html?app=" + encodeURIComponent(app)
    + "&chemin=" + encodeURIComponent(chemin(c));
}

module.exports = { utilisateur, connexionSilencieuse, deconnecter, reprendre,
                   lienConnexion, lienDeconnexion, CLE_SESSION };
