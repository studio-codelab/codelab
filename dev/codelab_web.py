"""codelab_web -- pages d'erreur, journal des exceptions et en-tetes de securite.

Depose par « codelab create », tenu a jour par « codelab update ». Pour Flask, sans autre dependance.

    from flask import Flask
    import codelab_web

    app = Flask(__name__)
    codelab_web.pages_d_erreur(app)          # 404/500 propres, exceptions journalisees
    codelab_web.entetes_de_securite(app)     # facultatif : voir plus bas

PAGES D'ERREUR. Une 404 ou une 500 s'affiche au style de CodeLab (/theme.css),
sans pile d'appels : la trace part dans le journal de l'application -- celui
que le panneau montre --, jamais dans la page, ou elle donnerait a n'importe
quel visiteur les chemins, les requetes et parfois les secrets du code. Un
client qui demande du JSON (une API, un fetch) recoit {"erreur": ...} au lieu
d'une page HTML.

EN-TETES DE SECURITE. Le panneau n'impose aux applications que nosniff et
frame-ancestors : leur imposer davantage casserait la premiere qui charge
une carte ou un script d'ailleurs, et ce serait decider a leur place. Cette
fonction est donc un CHOIX de l'application, pas une obligation. Elle pose
une politique stricte par defaut (tout depuis l'application elle-meme), que
l'on elargit en la passant :

    codelab_web.entetes_de_securite(app, csp=codelab_web.CSP_DEFAUT
                                    + "; img-src 'self' data: https://tuiles.exemple")

Une reponse qui pose deja l'un de ces en-tetes le garde : une route peut
toujours decider pour elle-meme. Pas de frame-ancestors ici : le panneau
pose le sien, qui autorise l'affichage dans le hub.
"""

import html
import logging

# script-src sans 'unsafe-inline' : c'est ce qui rend une injection de HTML
# inoffensive. Une page qui porte ses scripts en ligne doit les sortir dans
# un fichier, ou elargir cette regle -- en le sachant.
CSP_DEFAUT = ("default-src 'self'; base-uri 'none'; object-src 'none'; "
              "form-action 'self'; img-src 'self' data:; font-src 'self'; "
              "style-src 'self' 'unsafe-inline'; script-src 'self'")

ENTETES = {
    "X-Content-Type-Options": "nosniff",
    # L'adresse complete d'une page (avec ses identifiants dans l'URL) ne
    # part pas chez un site tiers ; seule l'origine le fait.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

journal = logging.getLogger("codelab_web")

TITRES = {400: "Requete invalide", 401: "Connexion requise", 403: "Acces refuse",
          404: "Page introuvable", 405: "Methode non permise",
          413: "Envoi trop volumineux", 429: "Trop de requetes",
          500: "Erreur interne", 502: "Service indisponible",
          503: "Service indisponible"}

MESSAGES = {404: "Cette adresse ne correspond a aucune page.",
            500: "Une erreur s'est produite. Elle est notee dans le journal "
                 "de l'application ; reessaie dans un instant."}


def _vers_l_accueil(chemin):
    """Lien relatif vers la racine de l'application.

    Le panneau sert l'application sous /<nom>/ et lui transmet le chemin SANS
    ce prefixe : un lien absolu « / » menerait au hub, pas ici. On remonte
    donc d'autant de dossiers que le chemin en compte.
    """
    profondeur = max(str(chemin or "/").count("/") - 1, 0)
    return "./" if profondeur == 0 else "../" * profondeur


def page(code, titre=None, message=None, chemin="/"):
    """La page HTML d'une erreur, au style de CodeLab."""
    titre = titre or TITRES.get(code, "Erreur")
    message = message or MESSAGES.get(code, "")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{code} -- {html.escape(titre)}</title>
<link rel="stylesheet" href="/theme.css">
<style>
  body{{margin:0;min-height:100vh;display:grid;place-items:center;
       background:var(--bg,#fff);color:var(--txt,#111);
       font:var(--t-b,15px)/1.5 var(--sans,system-ui,sans-serif)}}
  main{{max-width:28rem;margin:16px;padding:24px;background:var(--surface,#fff);
       border:1px solid var(--line,#ddd);border-radius:var(--r,10px)}}
  .code{{font-variant-numeric:tabular-nums;color:var(--dim,#666);font-size:var(--t-s,13px)}}
  h1{{margin:.2em 0 .4em;font-size:var(--t-l,20px)}}
  a{{color:var(--accent,#0a7);font-weight:600}}
</style></head>
<body><main>
  <div class="code">Erreur {code}</div>
  <h1>{html.escape(titre)}</h1>
  <p>{html.escape(message)}</p>
  <p><a href="{_vers_l_accueil(chemin)}">Retour a l'accueil</a></p>
</main></body></html>
"""


def _veut_du_json(requete):
    meilleur = requete.accept_mimetypes.best_match(["text/html", "application/json"])
    return meilleur == "application/json" or requete.is_json


def pages_d_erreur(app):
    """Remplace les pages d'erreur de Flask et journalise les exceptions."""
    from flask import request, jsonify
    from werkzeug.exceptions import HTTPException

    def repondre(code, titre=None, message=None):
        if _veut_du_json(request):
            return jsonify({"erreur": titre or TITRES.get(code, "Erreur"),
                            "code": code}), code
        return page(code, titre, message, request.path), code, {
            "Content-Type": "text/html; charset=utf-8"}

    @app.errorhandler(HTTPException)
    def erreur_http(e):
        # Une redirection levee comme exception (RequestRedirect) n'est pas
        # une erreur : on la rend telle quelle.
        if e.code is None or e.code < 400:
            return e
        return repondre(e.code)

    @app.errorhandler(Exception)
    def erreur_interne(e):
        # La trace complete, dans le journal ; rien de son contenu dans la page.
        journal.exception("%s %s -- exception non rattrapee", request.method, request.path)
        return repondre(500)

    if not journal.handlers and not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return app


def entetes_de_securite(app, csp=CSP_DEFAUT):
    """Pose les en-tetes de securite sur chaque reponse (sans ecraser ceux
    qu'une route a deja choisis). csp=None : pas de Content-Security-Policy."""
    @app.after_request
    def poser(reponse):
        for nom, valeur in ENTETES.items():
            reponse.headers.setdefault(nom, valeur)
        if csp:
            reponse.headers.setdefault("Content-Security-Policy", csp)
        return reponse
    return app
