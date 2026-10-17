# CodeLab -- LE PANNEAU, REQUETES
# ---------------------------------------------------------------------------
# ROLE        le retour apres connexion, les appareils, en-tetes et CSP, reference d'une panne, taille d'une requete, jeton CSRF.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   connexion puis retour, appareils connectes, droits du navigateur, reference d'une panne, taille d'une requete, jeton CSRF.
#

# ----------------- se connecter avec CodeLab, puis revenir ----------------
#
# Une application (privee, ou publique avec un bouton « Se connecter avec
# CodeLab ») envoie le visiteur a la connexion du panneau. Il doit revenir LA
# OU IL ETAIT, pas sur le hub. Mais la page de connexion ne suit qu'un chemin
# LOCAL (voir SUITE dans login.html) : une adresse quelconque en ferait un
# tremplin vers n'importe quel site. Le retour passe donc par une adresse du
# panneau, /ouvrir.app/<application>/<chemin>, qui ne mene QU'A une
# application declaree, sur l'origine des applications de ce serveur.
#
# Le point dans « ouvrir.app » n'est pas decoratif : un nom d'application ne
# peut pas en contenir (valid_name), cette adresse ne masque donc jamais une
# application.
_CHEMIN_APP = re.compile(r"^[A-Za-z0-9_\-./%~]*$")
# Le meme filtre que SUITE dans login.html, cote serveur.
_SUITE_LOCALE = re.compile(r"^/(?![/\\])[A-Za-z0-9_\-/%.]*$")


def suite_locale(brute):
    """Un chemin du panneau ou revenir apres la connexion, ou "/"."""
    s = str(brute or "")
    return s if _SUITE_LOCALE.match(s) and ".." not in s else "/"


def chemin_dans_app(brut):
    """Un chemin a l'interieur d'une application : jamais de « .. », jamais
    de schema ni d'hote -- au pire, la racine de l'application."""
    c = str(brut or "").lstrip("/")
    if not _CHEMIN_APP.match(c) or ".." in c.split("/") or "\\" in c:
        return ""
    return c


def adresse_application(nom, chemin=""):
    """L'adresse d'une page d'application, vue depuis le navigateur."""
    return (modele_adresse_application().replace("{nom}", urllib.parse.quote(nom, safe=""))
            + urllib.parse.quote(chemin_dans_app(chemin), safe="/%~"))


def suite_vers_application(nom, chemin=""):
    """Le « suite » de la connexion qui ramene dans une application."""
    return ("/ouvrir.app/" + urllib.parse.quote(nom, safe="") + "/"
            + urllib.parse.quote(chemin_dans_app(chemin), safe="/%"))


@flask_app.get("/ouvrir.app/<n>/")
@flask_app.get("/ouvrir.app/<n>/<path:chemin>")
@require_auth
def ouvrir_application_apres_connexion(n, chemin=""):
    if n not in load():
        return Response(_page("Introuvable", "Aucune application \u00ab " + n + " \u00bb."),
                        404, mimetype="text/html")
    # Pas de controle de droits ICI : le proxy les fait a l'arrivee, et dit
    # « Acces refuse » avec le nom de l'application -- une redirection n'a
    # rien a cacher de plus.
    return redirect(adresse_application(n, chemin), 302)


# SE DECONNECTER DE CODELAB DEPUIS UNE APPLICATION, puis y revenir.
#
# Une page qui DEMANDE avant de deconnecter : une deconnexion par simple
# lien serait declenchable par n'importe quel site (une image suffit). Le
# bouton envoie POST /logout avec le jeton de la session, comme le panneau.
@flask_app.get("/deconnexion.html")
def page_deconnexion():
    nom = request.args.get("app", "")
    cible = (adresse_application(nom, request.args.get("chemin", ""))
             if nom in load() else "/")

    def esc(x):
        return (str(x).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    if is_authed():
        titre = "Se d\u00e9connecter de CodeLab ?"
        texte = ("Tu seras d\u00e9connect\u00e9 du panneau et de toutes les "
                 "applications o\u00f9 CodeLab te reconna\u00eet.")
        boutons = ('<a class="b" href="' + esc(cible) + '">Annuler</a>'
                   '<button class="b p" id="ok" type="button">Se d\u00e9connecter</button>')
        script = ('<script>document.getElementById("ok").onclick=function(){'
                  'this.disabled=true;fetch("/logout",{method:"POST",credentials:"same-origin",'
                  'headers:{"X-CodeLab-Jeton":' + json.dumps(jeton_session()) + '}})'
                  '.finally(function(){location.href=' + json.dumps(cible) + ';});};</script>')
    else:
        titre = "Tu n\u2019es pas connect\u00e9 \u00e0 CodeLab"
        texte = "Il n\u2019y a rien \u00e0 fermer."
        boutons = '<a class="b p" href="' + esc(cible) + '">Revenir</a>'
        script = ""
    page = ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>D\u00e9connexion \u00b7 CodeLab</title>'
            '<link rel="icon" type="image/png" href="/codelab-onglet.png">'
            '<style>body{margin:0;min-height:100vh;display:grid;place-items:center;'
            'background:#f1f2f7;color:#15172a;font:15px/1.5 -apple-system,BlinkMacSystemFont,'
            '"Segoe UI",Roboto,sans-serif}.c{background:#fff;border-radius:22px;padding:28px 30px;'
            'max-width:400px;margin:16px;box-shadow:0 10px 40px rgba(21,23,42,.1)}'
            'h1{font-size:19px;margin:0 0 8px}p{color:#5e6280;margin:0 0 22px}'
            '.r{display:flex;gap:10px;justify-content:flex-end}.b{font:inherit;font-weight:700;'
            'padding:9px 18px;border-radius:11px;border:1px solid #e3e5ee;background:#f5f6fa;'
            'color:#15172a;text-decoration:none;cursor:pointer}.p{background:#3b3fd8;'
            'border-color:#3b3fd8;color:#fff}@media(prefers-color-scheme:dark){body{background:#0b0d18;'
            'color:#eceefb}.c{background:#121527}p{color:#9599b8}.b{background:#181c32;'
            'border-color:#232843;color:#eceefb}.p{background:#6366f1;border-color:#6366f1}}</style>'
            '</head><body><main class="c"><h1>' + titre + '</h1><p>' + texte + '</p>'
            '<div class="r">' + boutons + '</div></main>' + script + '</body></html>')
    r = Response(page, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r


@flask_app.before_request
def fermer_les_sessions_suspendues():
    """Suspendre doit valoir TOUT DE SUITE, pas a la prochaine connexion.

    Sans cette garde, un compte suspendu gardait sa session ouverte : celui
    dont on vient de fermer l'acces continuait d'ouvrir ses projets tant
    qu'il ne se deconnectait pas -- c'est-a-dire, en pratique, indefiniment.
    Le registre est relu a chaque requete (il l'est deja pour les projets
    autorises), et une session dont le compte a disparu ou a ete suspendu
    est effacee sur place.

    On efface et on laisse passer : la route dira elle-meme ce qu'elle exige,
    et rediriger ici renverrait la page de connexion vers elle-meme.
    """
    if session.get("authed") is not True or est_admin():
        return None
    nom = utilisateur_courant()
    compte = lire_utilisateurs().get(nom) if nom else None
    if compte is None or compte_suspendu(compte):
        session.clear()
        return None
    # Mot de passe change ou reinitialise, cles Face ID retirees : les
    # sessions ouvertes AVANT tombent. Une session est un cookie signe, que
    # le serveur ne peut pas rappeler -- sans cette date, un cookie vole
    # survivait au changement de mot de passe fait justement pour s'en
    # debarrasser.
    depuis = compte.get("sessions_depuis") or 0
    if (session.get("ouverte") or 0) < depuis:
        session.clear()
    return None


# AUTHENTIFICATION RECENTE. Ajouter une cle Face ID ou changer l'adresse qui
# recoit les codes, c'est installer un acces DURABLE au compte. Un cookie de
# session vole ne doit pas y suffire : ces deux gestes exigent une connexion
# de moins d'un quart d'heure. Le panneau propose Face ID juste apres la
# connexion, donc dans cette fenetre.
AUTHENTIFICATION_RECENTE = 900


def authentification_recente():
    return int(time.time()) - int(session.get("ouverte") or 0) <= AUTHENTIFICATION_RECENTE


def refus_authentification_ancienne():
    if authentification_recente():
        return None
    return jsonify({"error": "Par sécurité, reconnecte-toi avant cette opération "
                             "(connexion de moins de 15 minutes requise).",
                    "reconnexion": True}), 403


def fermer_les_autres_sessions(compte, garder_celle_ci=False, nom=None):
    """Invalide les sessions ouvertes de ce compte (a ecrire ensuite)."""
    maintenant = int(time.time())
    compte["sessions_depuis"] = maintenant
    if garder_celle_ci:
        # Celle qui vient de prouver le mot de passe reste ouverte.
        session["ouverte"] = maintenant
    if nom:
        # Et la liste des appareils le reflete aussitot.
        try:
            garder = session.get("sid") if garder_celle_ci else None
            revoquer_appareils([l["sid"] for l in appareils_du_compte(nom) if l["sid"] != garder])
        except Exception:                                         # noqa: BLE001
            pass


@flask_app.before_request
def expirer_les_sessions_trop_vieilles():
    """La limite absolue : trente jours depuis l'ouverture, point.

    L'inactivite est tenue par le cookie lui-meme (PERMANENT_SESSION_LIFETIME,
    repoussee a chaque requete). Elle ne dit rien d'une session ENTRETENUE :
    un onglet laisse ouvert sur une page qui interroge l'API la maintiendrait
    valable pour toujours. Cette garde-ci coupe au bout de trente jours,
    quoi qu'il arrive -- et c'est le SERVEUR qui compte, dans un cookie signe
    que le navigateur ne peut pas retoucher.

    On efface et on laisse passer, sans rediriger : la requete continue en
    visiteur anonyme, et c'est la route qui dira ce qu'elle exige. Rediriger
    ici renverrait aussi la page de connexion vers elle-meme.
    """
    if session.get("authed") is not True:
        return None
    ouverte = session.get("ouverte")
    if not isinstance(ouverte, (int, float)):
        # Session ouverte avant ce mecanisme : on la date maintenant plutot
        # que de deconnecter tout le monde a la mise a jour.
        session["ouverte"] = int(time.time())
        return None
    limite = (SESSION_ADMIN_ABSOLUE if session.get("role", ROLE_ADMIN) == ROLE_ADMIN
              else SESSION_ABSOLUE)
    if time.time() - ouverte > limite.total_seconds():
        qui = session.get("utilisateur") or ""
        session.clear()
        journaliser("session", qui=qui, action="expiree (" + (
                        "12 heures" if limite == SESSION_ADMIN_ABSOLUE else "30 jours") + ")",
                    ip=_adresse_client())
    return None


# ------------------------- les appareils connectes -------------------------
#
# Une session est un cookie signe : le serveur ne pouvait ni dire ou l'on
# etait connecte, ni couper un appareil perdu sans changer le mot de passe.
# Chaque session recoit desormais un identifiant (session["sid"]) et une
# ligne dans la table appareils ; la retirer deconnecte l'appareil a sa
# requete suivante.
#
# Le registre n'est pas relu a chaque requete -- le proxy en sert des
# dizaines par page. Une deconnexion faite ici est connue tout de suite
# (_sid_revoques) ; la base est relue au plus toutes les minutes par
# session, ce qui suffit apres un redemarrage du panneau.

APPAREIL_CONTROLE = 60
_sid_controles = {}
_sid_revoques = set()
_appareils_verrou = threading.Lock()


def id_public_appareil(sid):
    """Ce que la page voit : jamais le sid lui-meme."""
    return hashlib.sha256(("appareil:" + sid).encode()).hexdigest()[:16]


def _agent_court():
    return (request.headers.get("User-Agent") or "")[:300]


def revoquer_appareils(sids):
    sids = [x for x in sids if x]
    if not sids:
        return 0
    with _appareils_verrou:
        _sid_revoques.update(sids)
        for x in sids:
            _sid_controles.pop(x, None)
    with stockage.transaction(DB_FILE) as cx:
        n = 0
        for x in sids:
            n += cx.execute("DELETE FROM appareils WHERE sid = ?", (x,)).rowcount
    return n


def appareils_du_compte(compte):
    """Les sessions ouvertes de ce compte, la plus recente d'abord. Les
    lignes plus vieilles que la duree maximale d'une session sont oubliees."""
    limite = int(time.time() - SESSION_ABSOLUE.total_seconds())
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM appareils WHERE vue < ?", (limite,))
    return stockage.lire(DB_FILE, "SELECT * FROM appareils WHERE compte = ? ORDER BY vue DESC",
                         (compte,))


@flask_app.before_request
def suivre_l_appareil():
    if session.get("authed") is not True:
        return None
    qui = utilisateur_courant() or NOM_ADMIN
    sid = session.get("sid")
    maintenant = int(time.time())
    if not sid:
        # Premiere requete d'une session (ou session ouverte avant le
        # registre) : elle s'inscrit.
        sid = secrets.token_hex(16)
        session["sid"] = sid
        try:
            with stockage.transaction(DB_FILE) as cx:
                cx.execute("INSERT OR REPLACE INTO appareils (sid, compte, ouverte, vue, ip, agent) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (sid, qui, int(session.get("ouverte") or maintenant), maintenant,
                            _adresse_client() or "", _agent_court()))
        except Exception:                                         # noqa: BLE001
            return None
        _sid_controles[sid] = maintenant
        return None
    if sid in _sid_revoques:
        session.clear()
        return None
    if maintenant - _sid_controles.get(sid, 0) < APPAREIL_CONTROLE:
        return None
    _sid_controles[sid] = maintenant
    try:
        with stockage.transaction(DB_FILE) as cx:
            vu = cx.execute("UPDATE appareils SET vue = ?, ip = ?, agent = ? WHERE sid = ? AND compte = ?",
                            (maintenant, _adresse_client() or "", _agent_court(), sid, qui)).rowcount
    except Exception:                                             # noqa: BLE001
        # Base momentanement illisible : on ne deconnecte personne pour ca.
        return None
    if not vu:
        # La ligne a ete retiree (ou appartient a un autre compte) : cet
        # appareil a ete deconnecte.
        _sid_revoques.add(sid)
        session.clear()
    return None


# Ce que le port des applications accepte de servir. Le proxy et la sonde de
# sante, evidemment -- mais aussi le theme et les polices : une application
# hebergee (le Diagnostic, par exemple) charge "/theme.css" pour heriter de
# l'apparence choisie par l'utilisateur dans le hub. Sans ces deux entrees,
# la feuille repondait 404 sur ce port et la page s'affichait sans un seul
# jeton de couleur. Les deux routes sont deja publiques sur le panneau et ne
# revelent rien : des couleurs et un fichier de police.
ROUTES_APPLICATIONS = {"proxy", "proxy_noslash", "health", "theme_css", "police",
                       "favicon_panneau"}


@flask_app.before_request
def separer_les_origines():
    """Chaque port ne sert que ce qui lui appartient.

    Sans cette garde, ouvrir le second port ne separerait rien : le panneau
    repondrait sur les deux, et les deux origines se vaudraient.
    """
    if not origines_separees():
        return None
    if sur_le_port_des_apps():
        # blog.toi.duckdns.org : tout le chemin appartient a l'application,
        # « / » et « /theme.css » compris.
        nom = application_du_sous_domaine()
        if nom:
            g.sous_domaine = nom
            return _proxy(nom, request.path.lstrip("/"))
        if request.endpoint in ROUTES_APPLICATIONS or request.endpoint is None:
            return None
        # Le panneau ne s'affiche pas ici, et ses API n'y repondent pas : ce
        # port n'est pas de confiance, c'est tout l'interet.
        return Response(_page("Ce n'est pas le panneau",
                              "Cette adresse ne sert que les applications."),
                        404, mimetype="text/html")
    # Les routes /api/* appartiennent au panneau. Elles doivent rester sur
    # son origine, meme si Flask les ferait tomber sur le catch-all des
    # applications (endpoint "proxy"). Sans cette exception, une faute de
    # route ou un appel API depuis le panneau est transforme en redirection
    # vers 9002, ou "api" est ensuite interprete comme un nom d'application.
    if request.path == "/api" or request.path.startswith("/api/"):
        return None

    # Sur le port du panneau : une application demandee ici est renvoyee chez
    # elle. Les favoris et les liens deja partages continuent de marcher.
    if request.endpoint in ("proxy", "proxy_noslash"):
        # Sans adresse utilisable, on ne redirige pas : une cible relative
        # renverrait vers la page elle-meme, et le navigateur tournerait en
        # rond. Le panneau sert alors l'application, comme avant la
        # separation -- degrade, mais joignable.
        if not (domaine_des_applications() or origine_applications()):
            return None
        nom = request.view_args.get("n", "") if request.view_args else ""
        sous = request.view_args.get("sub", "") if request.view_args else ""
        cible = (modele_adresse_application().replace("{nom}", urllib.parse.quote(nom))
                 + sous)
        if request.query_string:
            cible += "?" + request.query_string.decode("latin-1")
        return redirect(cible, 302)
    return None


# --------------------- ce que le navigateur a le droit de faire ---------------
#
# Le panneau n'a jamais pose un seul en-tete de securite sur ses pages. Rien
# n'y obligeait tant qu'il n'y avait pas de faille -- mais c'est precisement
# la qu'ils servent : ils ne reparent rien, ils reduisent ce qu'une faille
# permet. La regle « une donnee ne devient jamais du programme » est la
# premiere barriere ; celle-ci est la seconde, pour le jour ou la premiere
# cede quand meme.
#
# Ce que chacun ferme :
#
#   frame-ancestors / X-Frame-Options   le panneau ne s'encadre plus. Sans
#       eux, un site pouvait le poser dans une iframe invisible sous un
#       bouton anodin et faire cliquer « Supprimer » a l'utilisateur. Le
#       jeton CSRF n'y peut rien : c'est un VRAI clic, dans une VRAIE
#       session.
#   default-src / script-src            un script injecte ne peut plus
#       charger de code depuis ailleurs, ni renvoyer ce qu'il a lu vers un
#       serveur tiers. 'unsafe-inline' reste necessaire : les deux pages
#       portent leur script et leur style en ligne, et les retirer est un
#       autre chantier. Le reste de la regle garde sa valeur malgre lui.
#   base-uri / form-action              une balise <base> injectee
#       detournerait toutes les adresses relatives de la page ; un formulaire
#       reecrit posterait le mot de passe ailleurs.
#   Referrer-Policy                     l'adresse d'une page du panneau ne
#       part plus chez un tiers.
#   nosniff                             un fichier servi comme du texte ne
#       peut plus etre relu comme du script.
#
# LES APPLICATIONS HEBERGEES SONT EXCLUES, sauf nosniff. Leur imposer la
# politique du panneau casserait la premiere qui charge une police, une carte
# ou un script depuis ailleurs -- et ce serait decider a leur place. Le
# panneau protege le panneau.
# Une seule regle leur est imposee, frame-ancestors, qui ne dit rien de ce
# qu'elles chargent : seulement qui peut les encadrer. Voir
# proteger_l_encadrement.
CSP_PANNEAU = ("default-src 'self'; base-uri 'none'; object-src 'none'; "
               "frame-ancestors 'none'; form-action 'self'; "
               "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
               "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")

# Six mois, et sans includeSubDomains. HSTS est un ENGAGEMENT : une fois
# l'en-tete recu, le navigateur refuse le http pendant toute cette duree, et
# rien ne peut le lui faire oublier plus tot. Un an sur une machine
# auto-hebergee ou l'on essaie des choses, c'est long ; les sous-domaines,
# eux, ne nous appartiennent pas forcement. Pose seulement quand le panneau
# SAIT qu'il est servi en TLS -- sinon il se rendrait lui-meme injoignable.
HSTS = "max-age=15552000"


# LA COMPRESSION. La page du panneau pese 300 Ko et repartait telle quelle a
# chaque visite : sans importance sur le reseau local, sensible depuis un
# telephone a l'exterieur. En gzip, elle en fait environ 60.
#
# Ce qui n'est PAS compresse, et pourquoi :
#   - le proxy des applications : leurs reponses sont les leurs, et elles
#     compressent deja si elles le veulent ;
#   - les flux (journaux en direct) : un flux compresse arrive par paquets ;
#   - tout ce qui n'est pas un GET sans parametres. C'est la parade a BREACH,
#     l'attaque qui devine un secret (ici le jeton de la page) a la taille
#     d'une reponse compressee -- elle exige que l'attaquant fasse refleter
#     un texte a lui dans la MEME reponse, donc une requete qu'il compose.
COMPRESSIBLES = {"text/html", "application/json", "text/css",
                 "application/javascript", "text/javascript", "image/svg+xml",
                 "text/plain"}


@flask_app.after_request
def compresser(reponse):
    if (request.method != "GET" or request.query_string
            or requete_d_application()
            or "gzip" not in request.headers.get("Accept-Encoding", "")
            or reponse.status_code != 200 or reponse.direct_passthrough
            or reponse.is_streamed or "Content-Encoding" in reponse.headers
            or reponse.mimetype not in COMPRESSIBLES):
        return reponse
    donnees = reponse.get_data()
    if len(donnees) < 1024:
        return reponse
    reponse.set_data(gzip.compress(donnees, compresslevel=6))
    reponse.headers["Content-Encoding"] = "gzip"
    reponse.headers["Content-Length"] = str(len(reponse.get_data()))
    reponse.vary.add("Accept-Encoding")
    return reponse


# ------------------- une reference pour chaque panne -------------------
#
# « (reponse 500) » ne disait ni quoi faire ni ou chercher. Chaque reponse
# 5xx du panneau porte desormais une REFERENCE courte (X-CodeLab-Ref), que
# la page affiche et que le journal du conteneur ecrit sur la meme ligne
# que la route : « docker logs codelab-app-manager | grep 7F3A2C » retrouve
# la panne. Rien de sensible dans la reference : six caracteres au hasard.

def nouvelle_reference():
    return secrets.token_hex(3).upper()


@flask_app.errorhandler(500)
def erreur_inattendue(e):
    """Une exception non prevue : sa pile part au journal, avec la
    reference ; la page recoit une phrase et la reference, jamais la pile."""
    ref = nouvelle_reference()
    origine = getattr(e, "original_exception", None) or e
    print(f"[codelab] erreur {ref} : {request.method} {request.path}\n"
          + "".join(traceback.format_exception(type(origine), origine, origine.__traceback__)),
          flush=True)
    reponse = jsonify({"error": "Erreur inattendue du panneau.", "ref": ref})
    reponse.status_code = 500
    reponse.headers["X-CodeLab-Ref"] = ref
    return reponse


@flask_app.after_request
def referencer_les_pannes(reponse):
    if (reponse.status_code >= 500 and not requete_d_application()
            and "X-CodeLab-Ref" not in reponse.headers):
        ref = nouvelle_reference()
        reponse.headers["X-CodeLab-Ref"] = ref
        print(f"[codelab] erreur {ref} : {request.method} {request.path} -> "
              f"{reponse.status_code}", flush=True)
    return reponse


@flask_app.after_request
def poser_les_entetes(reponse):
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    if requete_d_application():
        # Toute reponse d'application, page d'erreur et redirection
        # comprises : seul le panneau peut l'encadrer.
        entetes = proteger_l_encadrement(list(reponse.headers.items()))
        reponse.headers.clear()
        for k, v in entetes:
            reponse.headers.add(k, v)
        return reponse
    reponse.headers.setdefault("Content-Security-Policy", CSP_PANNEAU)
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers.setdefault("Referrer-Policy", "no-referrer")
    reponse.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if https_actif():
        reponse.headers.setdefault("Strict-Transport-Security", HSTS)
    return reponse


# ------------------------- la taille d'une requete --------------------------
#
# Il n'y en avait aucune. Un seul POST pouvait donc faire lire au serveur
# autant d'octets qu'il en envoyait, et waitress tient seize fils : seize
# corps sans fin, et le service tombe sans qu'aucun mot de passe ait ete
# tente. Ce n'est pas une faille, c'est une porte laissee ouverte.
#
# DEUX plafonds, parce qu'il y a deux usages qui n'ont rien a voir :
#
#   le panneau       ses propres API. Le plus gros envoi legitime est un logo
#                    de 512 Ko, qui voyage en base64 dans du JSON. 2 Mo
#                    laissent large.
#   les applications ce que le proxy transmet a une application hebergee. On
#                    ne sait pas ce qu'elle recoit -- un fichier deposé, une
#                    archive. Le plafond est donc genereux, et reglable : ce
#                    qu'il borne, c'est la memoire du panneau, qui met le
#                    corps entier en memoire avant de le transmettre.
CORPS_MAX_PANNEAU = 2 * 1024 * 1024
# Une piece jointe (capture, maquette), deposee seule : 10 Mo.
PIECE_MAX = 10 * 1024 * 1024
CORPS_MAX_APPLICATION = int(os.environ.get("APP_MANAGER_CORPS_MAX_MO", "32")) * 1024 * 1024

# Le filet de Flask, pour un corps annonce sans taille (Transfer-Encoding:
# chunked) : la lecture s'arrete d'elle-meme au plafond.
flask_app.config["MAX_CONTENT_LENGTH"] = CORPS_MAX_APPLICATION


@flask_app.before_request
def borner_le_corps():
    """Refuse AVANT de lire : le Content-Length suffit a decider."""
    if requete_d_application():
        return None
    taille = request.content_length
    limite = PIECE_MAX if request.endpoint == "api_piece_deposer" else CORPS_MAX_PANNEAU
    if taille is not None and taille > limite:
        return _trop_gros(limite)
    return None


def _trop_gros(limite):
    message = ("Envoi trop volumineux : %d Mo au maximum."
               % (limite // (1024 * 1024)))
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), 413
    return Response(_page("Envoi trop volumineux", message), 413,
                    mimetype="text/html")


@flask_app.errorhandler(413)
def trop_gros(e):
    """Le meme refus quand c'est Flask qui coupe, et non la garde ci-dessus.

    Sans lui, un depot trop gros rendait la page d'erreur par defaut : du
    HTML la ou la page attend du JSON, donc un message illisible."""
    limite = (CORPS_MAX_APPLICATION if requete_d_application()
              else PIECE_MAX if request.endpoint == "api_piece_deposer"
              else CORPS_MAX_PANNEAU)
    return _trop_gros(limite)


# ------------------------------- jeton CSRF --------------------------------
#
# SameSite=Lax bloque deja l'essentiel : un autre SITE ne peut plus faire
# poster le navigateur vers /api/toggle/<app> avec le cookie de session.
# Ce jeton couvre ce que SameSite ne couvre pas.
#
# Ce qu'il apporte VRAIMENT, et ce qu'il n'apporte pas -- parce que la
# nuance decide de la suite :
#
#   Il protege d'une AUTRE ORIGINE. Un script servi ailleurs (un autre port
#   de cette machine, par exemple) peut declencher une requete vers le
#   panneau avec le cookie, mais la politique d'origine l'empeche de LIRE la
#   reponse d'un GET -- donc d'apprendre le jeton. Sans le jeton, sa requete
#   est refusee.
#
#   Il ne protege PAS d'un script servi sous la MEME origine. Les
#   applications sont servies sous :9001/<nom>/, donc un script hostile qui
#   y tourne lit le jeton comme le panneau le lit. La reponse a ce
#   probleme-la n'est pas un jeton, c'est une origine separee -- un port ou
#   un nom d'hote distinct pour les applications. Le jeton est ce qui rendra
#   cette separation efficace le jour ou elle sera faite : sans lui, changer
#   d'origine n'empecherait pas la requete, seulement sa lecture.
#
# Verifie ici, dans un before_request, et pas route par route : une route
# d'ecriture ajoutee demain est protegee sans que personne y pense. C'est
# l'inverse d'un decorateur qu'on oublie.
JETON_ENTETE = "X-CodeLab-Jeton"

# Les seules routes d'ecriture atteignables SANS session : on ne peut pas
# exiger d'un visiteur un jeton qui vit dans une session qu'il n'a pas
# encore. Elles ont leur propre garde -- mot de passe, code a six chiffres,
# limite de tentatives.
JETON_EXEMPTS = {
    "login_submit", "login_second_facteur",
    "login_passkey_options", "login_passkey",
    "login_inscription_passkey_options", "login_inscription_passkey",
    "login_facteur_passkey_options", "login_facteur_passkey",
    "invitation_accepter",
    # Le proxy transporte les requetes des applications hebergees : leurs
    # formulaires ne connaissent pas le jeton du panneau, et n'ont aucune
    # raison de le connaitre.
    "proxy", "proxy_noslash",
}

JETON_METHODES = {"POST", "PUT", "PATCH", "DELETE"}


def jeton_session():
    """Le jeton de la session courante, cree a la demande.

    Vit dans le cookie de session, donc signe : un client ne peut pas s'en
    fabriquer un, et il disparait avec la session.
    """
    j = session.get("jeton")
    if not j:
        j = secrets.token_urlsafe(32)
        session["jeton"] = j
    return j


@flask_app.before_request
def verifier_jeton():
    if request.method not in JETON_METHODES:
        return None
    if request.endpoint in JETON_EXEMPTS:
        return None
    # Pas de session ouverte : rien a proteger ici, et la route dira
    # elle-meme qu'il faut s'authentifier -- repondre 403 masquerait le vrai
    # motif. Une session ouverte, elle, porte TOUJOURS un jeton : il est pose
    # au moment ou elle s'ouvre (voir ouvrir_session), jamais plus tard. Sans
    # cette garantie, "pas de jeton donc on laisse passer" serait un
    # contournement au lieu d'une exemption.
    if not is_authed():
        return None
    # session.get et pas session["jeton"] : l'invariant "une session ouverte
    # porte un jeton" est vrai, mais s'il cassait un jour, une KeyError
    # rendrait un 500 la ou un 403 est la bonne reponse -- et un 500 sur une
    # ecriture se lit comme une panne du panneau, pas comme un refus.
    attendu = session.get("jeton") or ""
    fourni = request.headers.get(JETON_ENTETE, "")
    if not attendu or not secrets.compare_digest(fourni, attendu):
        return jsonify({"error": "Jeton de sécurité absent ou invalide. "
                                 "Recharge la page."}), 403
    return None


def require_admin(view):
    """Reserve a l'administrateur : declarer, deployer, configurer, gerer les
    comptes. Tout ce qui n'est pas "ouvrir un projet autorise" passe par ici.

    Le controle est fait ICI et non dans l'interface : masquer un bouton ne
    protege rien, la route reste appelable a la main.
    """
    def wrapped(*a, **kw):
        if not is_authed():
            return _refus("Non authentifie.", 401)
        if not est_admin():
            return _refus("Reserve a l'administrateur.", 403)
        # Apres le controle de role, et non avant : le message ne doit rien
        # apprendre a qui n'est deja administrateur. Verifie a CHAQUE
        # requete, pas seulement a la connexion -- une session ouverte dans
        # le salon puis reprise depuis l'exterieur (portable qui se deplace,
        # cookie vole) doit cesser d'administrer en sortant.
        hors = refus_admin_hors_reseau()
        if hors:
            return _refus(hors, 403)
        return view(*a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped
