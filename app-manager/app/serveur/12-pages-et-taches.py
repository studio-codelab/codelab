# CodeLab -- LE PANNEAU, PAGES ET TACHES
# ---------------------------------------------------------------------------
# ROLE        les pages du panneau, les videos de presentation, les taches et leur suivi, l'export d'un projet.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   pages, videos de presentation, taches, suivi cote utilisateur, export d'un projet.
#

# ------------------------------ pages --------------------------------

# La police du panneau, servie depuis l'image. 25 Ko, une seule fois, et le
# panneau ne demande rien a personne : un serveur auto-heberge qui irait
# chercher sa police chez Google ferait fuiter l'adresse IP de chaque visiteur
# vers un tiers, et s'afficherait mal des que la machine est hors ligne.
POLICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polices")


@flask_app.get("/polices/<nom>")
def police(nom):
    """Un fichier de police, et rien d'autre.

    La liste blanche est explicite : sans elle, ce chemin deviendrait une
    lecture de fichier arbitraire des qu'un nom contient "..". Werkzeug
    refuse deja les segments de ce genre, mais la garde ne doit pas dependre
    d'un detail du routeur.
    """
    if nom not in POLICES_SERVIES:
        return Response("Inconnu", status=404, mimetype="text/plain")
    return send_file(os.path.join(POLICES_DIR, nom), mimetype="font/woff2",
                     max_age=31536000)


@flask_app.get("/theme.css")
def theme_css():
    """Le theme, servi aux deux pages.

    Public sans session : la page de CONNEXION en a besoin, et il n'y a la
    que des couleurs. Le proteger n'aurait rien protege et aurait servi une
    page de connexion sans style.

    Werkzeug range les regles litterales avant les regles a variable : cette
    route gagne sur "/<n>", qui sert les applications hebergees. Et un nom
    d'application ne peut de toute facon pas contenir de point.

    Un cache court plutot qu'aucun : le fichier ne change qu'au deploiement
    d'une nouvelle image, mais une minute suffit a eviter de le redemander a
    chaque page sans qu'une mise a jour tarde a se voir.
    """
    return Response(THEME_CSS, mimetype="text/css",
                    headers={"Cache-Control": "public, max-age=60"})


@flask_app.get("/mode.js")
def mode_js():
    """Le mode -- site web ou application --, lu une fois pour les trois pages.

    Public sans session pour la meme raison que le theme : la page de
    connexion en a besoin, et il n'y a la qu'une detection d'affichage. Meme
    cache court.
    """
    return Response(MODE_JS, mimetype="text/javascript",
                    headers={"Cache-Control": "public, max-age=60"})


def _json_dans_un_script(valeur):
    """Une valeur en JSON, prete a entrer dans une balise script : « < »
    s'ecrit \\u003c (le meme caractere pour JavaScript), et rien ne peut
    donc y ecrire « </script> »."""
    return json.dumps(valeur).replace("<", "\\u003c")


@flask_app.get("/panneau.js")
@require_auth
def panneau_js():
    """Le script de la page du panneau (dashboard.html).

    A une session seulement : il ne porte aucun secret (chaque valeur propre
    a la session est dans la page, window.CODELAB_PAGE), mais il n'a rien a
    faire chez un visiteur, et la page qui le charge exige deja la session.
    CACHE : l'adresse porte l'empreinte du fichier (PANNEAU_JS_VERSION). A la
    bonne empreinte, le navigateur le garde un an sans redemander ; sinon
    (ancienne page, adresse tapee a la main), il le revalide a chaque fois.
    « private » : il passe par une session, aucun cache partage ne le garde.
    """
    garder = request.args.get("v") == PANNEAU_JS_VERSION
    entetes = {"Cache-Control": "private, max-age=31536000, immutable"
               if garder else "private, no-cache", "Vary": "Accept-Encoding"}
    # COMPRESSE, MEME AVEC ?v=. compresser() saute toute requete a
    # parametres, parade a BREACH (deviner un secret a la taille d'une
    # reponse compressee ou l'attaquant fait refleter son texte). Le
    # script ne porte AUCUN secret -- les valeurs de session sont dans la
    # page -- et n'echo rien de la requete : v est compare, jamais recopie.
    # Vecu (test de charge) : servi par ?v=, il partait donc en clair
    # (269 Ko au lieu de 87), et sans ?v= il etait recompresse a chaque
    # requete (12 ms).
    # DEPEND DE : la regle « aucune valeur propre a la session dans le
    # script » (AGENTS.md, panneau/*.js). Si elle tombait, cette
    # compression redeviendrait une prise pour BREACH.
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        entetes["Content-Encoding"] = "gzip"
        return Response(PANNEAU_JS_GZIP, mimetype="text/javascript", headers=entetes)
    return Response(PANNEAU_JS, mimetype="text/javascript", headers=entetes)


@flask_app.get("/login")
def login_page(invitation=False):
    # Une invitation s'ouvre meme avec une session en cours : c'est souvent
    # l'administrateur qui essaie le lien qu'il vient d'envoyer.
    if is_authed() and not invitation:
        # Deja connecte : « Se connecter avec CodeLab » depuis une
        # application ramene directement dedans, sans formulaire.
        return redirect(suite_locale(request.args.get("suite")))
    return Response(avec_style(LOGIN_PAGE), mimetype="text/html")


# ------------------------------ le nuancier ------------------------------
#
# Une couleur d'accent ne se pose pas telle quelle : il en faut une version
# claire et une sombre, chacune lisible sur son fond, avec sa teinte de survol,
# son aplat doux, son halo et la couleur du texte ecrit dessus. palette_accent
# les calcule depuis UNE teinte. Elle sert la couleur personnalisee
# (/couleur.css) ; les teintes Framboise, Ambre, Indigo et Olive de theme.css
# en sont sorties, recopiees une fois (voir regles_nuancier).
#
# Les seuils : 4,5:1 (texte blanc sur l'accent clair, et l'accent sur fond
# blanc) et 7:1 pour l'accent sombre sur le fond du theme sombre (#0D1015),
# ceux que tiennent deja Lagon et Ocean.
FOND_SOMBRE = "#0D1015"


def _rvb(hexa):
    h = hexa.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hexa(rvb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rvb)


def _luminance(hexa):
    canaux = []
    for c in _rvb(hexa):
        c /= 255
        canaux.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]


def contraste(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _clarte(hexa, delta):
    t, c, s = colorsys.rgb_to_hls(*(x / 255 for x in _rvb(hexa)))
    return _hexa(x * 255 for x in colorsys.hls_to_rgb(t, max(0.0, min(1.0, c + delta)), s))


def _melange(hexa, vers, part):
    a, b = _rvb(hexa), _rvb(vers)
    return _hexa(x + (y - x) * part for x, y in zip(a, b))


def _halo(hexa, opacite):
    r, v, b = _rvb(hexa)
    return f"rgba({r},{v},{b},{opacite})"


def palette_accent(hexa):
    """Les jetons d'accent d'une teinte, {"clair": {...}, "sombre": {...}}."""
    clair = hexa.upper()
    for _ in range(60):
        if contraste(clair, "#FFFFFF") >= 4.5:
            break
        clair = _clarte(clair, -0.02)
    sombre = hexa.upper()
    for _ in range(60):
        if contraste(sombre, FOND_SOMBRE) >= 7:
            break
        sombre = _clarte(sombre, 0.02)
    clair_survol = _clarte(clair, -0.08)
    t, _c, s = colorsys.rgb_to_hls(*(x / 255 for x in _rvb(sombre)))
    return {
        "clair": {"--accent": clair, "--accent-h": clair_survol,
                  "--accent-soft": _melange(clair, "#FFFFFF", 0.88),
                  "--accent-ring": _halo(clair, ".22"), "--sur-accent": "#FFFFFF",
                  "--bento-lien": _melange(clair, "#FFFFFF", 0.55)},
        "sombre": {"--accent": sombre, "--accent-h": _clarte(sombre, 0.08),
                   "--accent-soft": _halo(sombre, ".15"),
                   "--accent-ring": _halo(sombre, ".3"),
                   "--sur-accent": _hexa(x * 255 for x in colorsys.hls_to_rgb(t, 0.08, min(s, 0.8))),
                   "--bento-lien": clair_survol},
    }


def regles_nuancier(nom, hexa):
    """Les trois regles d'une teinte, comme celles de theme.css : clair,
    sombre du systeme, sombre choisi."""
    p = palette_accent(hexa)
    corps = {k: "{" + " ".join(f"{j}:{v};" for j, v in p[k].items()) + "}" for k in p}
    racine = f':root[data-variante="bento"][data-couleurs="{nom}"]'
    return (f"{racine}{corps['clair']}\n"
            f"@media (prefers-color-scheme: dark){{\n"
            f'  {racine}:not([data-theme="light"]):not([data-theme="dark"]){corps["sombre"]}\n'
            f"}}\n"
            f'{racine}[data-theme="dark"]{corps["sombre"]}\n')


# LA COULEUR PERSONNALISEE : six chiffres hexadecimaux, et rien d'autre.
# POURQUOI : la valeur vient du cookie du client et finit dans une adresse
#   (/couleur.css?c=...) et dans un attribut de <html>. L'expression la tient
#   a ce qu'une couleur peut etre ; tout le reste est ignore.
# DEPEND DE / PROTEGE AUSSI : reglages_du_hub (le cookie), avec_style
#   (l'attribut et le lien), couleur_css (la feuille).
COULEUR_PERSO = re.compile(r"[0-9a-fA-F]{6}")


@flask_app.get("/couleur.css")
def couleur_css():
    """La feuille de la couleur personnalisee. Sans session : elle ne dit rien
    de personne, et la page de connexion la charge aussi."""
    c = request.args.get("c", "")
    if not COULEUR_PERSO.fullmatch(c):
        return Response("/* couleur invalide */", 400, mimetype="text/css")
    r = Response(regles_nuancier("perso", "#" + c), mimetype="text/css")
    # La meme couleur rend toujours la meme feuille : elle se garde.
    r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return r


# LES REGLAGES DU HUB, poses en attribut sur <html> (data-tuiles,
# data-defilement...). La premiere valeur de chaque reglage est celle par
# defaut. Le fond et les surfaces sont ceux de data-variante="bento"
# (theme.css).
REGLAGES_HUB = {
    "tuiles": ("cartes", "icones"),
    "defilement": ("horizontal", "vertical"),
    "widgets": ("melanges", "haut"),
    # Le nuancier : la couleur d'accent du panneau (boutons, liens, onglet
    # actif), en clair comme en sombre. Jetons dans theme.css.
    # « perso » : la teinte choisie librement, portee par un jeton « x » suivi
    # de six chiffres hexadecimaux dans le meme cookie (voir couleur_perso).
    "couleurs": ("lagon", "ocean", "foret", "prune", "corail", "ardoise",
                 "framboise", "ambre", "indigo", "olive", "perso"),
}


def reglages_du_hub():
    """Les reglages du hub, lus dans le cookie codelab-hub.

    Le cookie liste des valeurs separees par des points
    (« icones.vertical.haut ») : chaque valeur n'appartient qu'a un
    reglage, l'ordre est libre. Rien d'inconnu n'est retenu -- le cookie
    vient du client, et seules les valeurs de REGLAGES_HUB arrivent dans
    la page.
    """
    reglages = {cle: valeurs[0] for cle, valeurs in REGLAGES_HUB.items()}
    brut = request.cookies.get("codelab-hub") or ""
    # Un jeton de plus que de reglages : la couleur personnalisee.
    for valeur in brut.split(".")[:len(REGLAGES_HUB) + 1]:
        for cle, valeurs in REGLAGES_HUB.items():
            if valeur in valeurs:
                reglages[cle] = valeur
    # « perso » sans couleur lisible : la teinte par defaut, plutot qu'un
    # accent qui ne serait defini nulle part.
    if reglages["couleurs"] == "perso" and not couleur_perso():
        reglages["couleurs"] = REGLAGES_HUB["couleurs"][0]
    return reglages


def couleur_perso():
    """La couleur personnalisee du cookie (six chiffres hexadecimaux), ou ""."""
    for valeur in (request.cookies.get("codelab-hub") or "").split(".")[:len(REGLAGES_HUB) + 1]:
        if valeur.startswith("x") and COULEUR_PERSO.fullmatch(valeur[1:]):
            return valeur[1:].upper()
    return ""


def avec_style(page):
    """Pose le style, les reglages du hub et les ecrans de demarrage sur la
    page servie.

    Pose cote serveur, l'attribut est la avant la premiere peinture : la
    page ne change pas de forme le temps que son script le lise.
    """
    reglages = reglages_du_hub()
    attributs = ' data-style="design" data-variante="bento"' + "".join(
        f' data-{cle}="{valeur}"' for cle, valeur in reglages.items())
    page = page.replace('<html lang="fr">', '<html lang="fr"' + attributs + '>', 1)
    # La couleur personnalisee : sa feuille, juste avant </head> -- donc apres
    # theme.css, dont elle remplace l'accent a specificite egale. Six chiffres
    # hexadecimaux verifies (COULEUR_PERSO) : rien d'autre n'entre ici.
    # L'attribut garde la couleur meme quand une autre teinte est choisie :
    # y revenir la retrouve.
    perso = couleur_perso()
    if perso:
        page = page.replace("<html lang=\"fr\"", f'<html lang="fr" data-couleur-perso="{perso}"', 1)
    if perso and reglages["couleurs"] == "perso":
        page = page.replace("</head>", '<link rel="stylesheet" id="couleur-perso" '
                            f'href="/couleur.css?c={perso}"></head>', 1)
    return page.replace("__ECRANS_DEMARRAGE__", BALISES_ECRAN_DE_DEMARRAGE, 1)


# LES PAGES DU PANNEAU ONT CHACUNE LEUR ADRESSE. Recharger les parametres
# laisse sur les parametres, un lien vers la fiche d'une application s'ouvre
# sur elle. C'est toujours la meme page (dashboard.html) : elle lit l'adresse
# et ouvre la bonne rubrique. Ces noms ne peuvent plus servir de chemin a une
# application (adresses_reservees).
PAGES_DU_PANNEAU = ("apercu", "applications", "utilisateurs", "messages", "parametres", "demandes")


def adresses_reservees():
    """Les premiers segments d'adresse qu'aucune application ne peut prendre.

    DERIVES DE LA TABLE DES ROUTES, pas recopies a la main : toute route du
    panneau -- /parametres, /invitation, /mot-de-passe, /qr, /polices,
    celles de demain -- est reservee des qu'elle existe. Une liste tenue a la
    main oubliait forcement la suivante, et l'application qui prenait ce nom
    devenait injoignable sur le port du panneau (la route du panneau passe
    avant le relais des applications).
    """
    noms = {"api", "static", "health", "login", "logout", *PAGES_DU_PANNEAU}
    for regle in flask_app.url_map.iter_rules():
        if regle.endpoint in ("proxy", "proxy_noslash", "static"):
            continue
        premier = regle.rule.lstrip("/").split("/", 1)[0]
        if premier and "<" not in premier:
            noms.add(premier.lower())
    return frozenset(noms)


def signaler_adresses_en_conflit():
    """Une application declaree AVANT qu'une route du panneau prenne son nom
    reste servie sur son origine (le port des applications), mais son lien
    sur le port du panneau ouvre la page du panneau. On le dit au demarrage."""
    for nom in sorted(set(load()) & adresses_reservees()):
        print(f"[codelab] l'application « {nom} » porte un nom reserve au panneau : "
              f"renomme-la (Applications > fiche > Renommer).", flush=True)


@flask_app.get("/apercu")
@flask_app.get("/applications")
@flask_app.get("/applications/<nom>")
@flask_app.get("/applications/<nom>/<onglet>")
@flask_app.get("/utilisateurs")
@flask_app.get("/parametres")
@flask_app.get("/parametres/<onglet>")
@require_auth
def page_du_panneau(**_):
    return index()


@flask_app.get("/messages")
@flask_app.get("/messages/<vue>")
@flask_app.get("/messages/<vue>/<cle>")
@require_auth
def page_messagerie(**_):
    """Les messages et la gestion de projet qui en sort, sur une page a eux.

    Pour l'administrateur. Un compte utilisateur a, a la meme adresse, SA
    rubrique « Messages » du panneau : ecrire a l'administrateur et suivre
    ce que chaque message est devenu (la page le route sur sa section).
    """
    if not est_admin():
        return index()
    page = MESSAGERIE_PAGE.replace("__JETON__", _json_dans_un_script(jeton_session()))
    return Response(avec_style(page), mimetype="text/html")


@flask_app.get("/")
@require_auth
def index():
    """Une seule application, un seul accueil.

    CodeLab est une page unique, servie a la meme adresse a tout le monde,
    et le hub (la liste des projets qu'on peut ouvrir) en est l'accueil,
    administrateur compris. Le role ne change pas de page : il ouvre en plus
    le menu lateral (vue d'ensemble, applications, journaux) et l'entree
    « Configuration » du menu du compte.

    Le role est injecte dans la page pour qu'elle sache quoi afficher --
    mais ce n'est qu'un confort d'affichage : chaque route d'administration
    verifie le role de son cote (require_admin), et une page bricolee ne
    donne donc aucun droit supplementaire.
    """
    # Les valeurs entrent dans un script en ligne (window.CODELAB_PAGE) :
    # _json_dans_un_script les ecrit en JSON ET y echappe « < », pour qu'aucune
    # ne puisse fermer la balise (un nom de compte, une racine, une adresse).
    page = (DASHBOARD_PAGE
            .replace("__JETON__", _json_dans_un_script(jeton_session()))
            .replace("__APPS_MODELE__", _json_dans_un_script(modele_adresse_application()))
            .replace("__ROOT__", _json_dans_un_script(ROOT))
            .replace("__ROLE__", _json_dans_un_script(role_courant() or ""))
            # Seul l'admin en a besoin : la page lui propose de regler l'envoi
            # tant qu'il manque, et grise la creation de comptes.
            .replace("__SMTP__", _json_dans_un_script(
                envoi_mail_configure() if role_courant() == "admin" else True))
            .replace("__UTILISATEUR__", _json_dans_un_script(utilisateur_courant()))
            # Le prenom et le nom choisis : le bonjour et l'en-tete les
            # disent des la premiere peinture.
            .replace("__PROFIL__", _json_dans_un_script(lire_profil()))
            .replace("__PANNEAU_VERSION__", PANNEAU_JS_VERSION))
    r = Response(avec_style(page), mimetype="text/html")
    # Le widget « A la une » du hub montre un APERCU de la derniere
    # application ouverte, dans un cadre : la politique du panneau, plus le
    # droit d'encadrer l'origine des applications -- et elle seule. Les
    # applications, elles, n'acceptent d'etre encadrees que par le panneau
    # (proteger_l_encadrement).
    r.headers["Content-Security-Policy"] = CSP_PANNEAU + "; frame-src " + cadres_permis()
    return r


# PARCOURIR LES APPLICATIONS SANS QUITTER LE PANNEAU INSTALLE.
#
# Le panneau ajoute a l'ecran d'accueil est une app dont la portee est SON
# origine. Les applications vivent sur une autre (le port des applications,
# voir separer_les_origines) : y naviguer, c'est sortir de l'app, et iOS
# l'affiche dans son navigateur integre -- « Done » en haut, fleches en bas.
# Aucun manifeste ne couvre deux origines.
#
# L'application est donc ENCADREE dans une page du panneau : la fenetre reste
# sur l'origine du panneau, donc dans l'app, et l'application garde la
# sienne -- l'iframe ne lui donne aucun acces au panneau, la separation tient.
# Le hub n'y passe qu'en mode app (display-mode: standalone) ; dans Safari,
# une tuile ouvre toujours l'application directement.
#
# Le point dans le chemin n'est pas decoratif : un nom d'application ne peut
# pas en contenir (valid_name), cette adresse ne peut donc jamais masquer une
# application.
@flask_app.get("/ouvrir.html")
@require_auth
def ouvrir_dans_le_panneau():
    nom = request.args.get("app", "")
    apps = load()
    a = apps.get(nom)
    if not a or not peut_voir(nom) or a.get("externe"):
        return Response(_page("Introuvable", "Aucune application \u00ab " + nom + " \u00bb."),
                        404, mimetype="text/html")
    cible = adresse_application(nom)
    titre = titre_de(nom, a)

    def esc(x):
        return (str(x).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    # Un seul passage : une valeur remplacee n'est jamais relue, donc un
    # titre qui contiendrait « __CIBLE__ » reste du texte. Chaque valeur est
    # echappee pour SON contexte (texte, URL, attribut, script) ; dans le
    # script, « < » s'ecrit \u003c pour qu'aucune valeur ne ferme la balise.
    valeurs = {"TITRE": esc(titre),
               "ICONE": urllib.parse.quote(nom, safe=""),
               "CIBLE": esc(cible),
               "ORIGINE": json.dumps(origine_de_l_application(nom)).replace("<", "\\u003c")}
    page = re.sub(r"__(TITRE|ICONE|CIBLE|ORIGINE)__", lambda m: valeurs[m.group(1)], OUVRIR_PAGE)
    r = Response(page, mimetype="text/html")
    # La politique du panneau, plus le droit d'encadrer l'origine des
    # applications -- et elle seule.
    r.headers["Content-Security-Policy"] = CSP_PANNEAU + "; frame-src " + cadres_permis()
    r.headers["Cache-Control"] = "no-store"
    return r


@flask_app.get("/espace")
@require_auth
def espace():
    """Ancienne adresse de l'espace utilisateur.

    Conservee parce qu'elle a pu etre mise en favori : le hub vit maintenant
    dans la page principale, en tant que mode.
    """
    return redirect("/")


# ------------------------ les videos de presentation ------------------------
#
# Une application peut porter sa video de presentation : video/presentation.mp4
# dans son dossier, et son image d'affiche video/affiche.jpg -- ce que
# produit « codelab video » dans le poste de developpement. Le panneau ne
# fabrique rien et n'appelle aucun modele : il montre un fichier s'il existe.
#
# Memes droits que l'application : qui ne peut pas l'ouvrir ne voit pas sa
# video. L'administrateur peut la retirer du hub sans supprimer le fichier
# (« presentation_masquee »), et choisir une AUTRE video du projet -- celle
# d'un rendu Remotion, d'un montage fait ailleurs (« presentation_fichier »,
# un chemin relatif au projet). L'affiche ne va qu'avec la video par defaut.
PRESENTATION = {"video": ("video/presentation.mp4", "video/mp4"),
                "affiche": ("video/affiche.jpg", "image/jpeg")}
TYPES_VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
# Ce qu'on ne fouille pas en cherchant les videos d'un projet : dependances,
# environnements, caches. Les dossiers caches non plus.
DOSSIERS_SANS_VIDEO = {"node_modules", "venv", "__pycache__", "vendor", "site-packages"}


def videos_du_projet(a, profondeur=6, limite=200):
    """Les videos du dossier du projet, en chemins relatifs tries.

    Sans suivre les liens symboliques : rien hors du projet n'est propose.
    """
    dossier = (a or {}).get("path") or ""
    if not dossier or not os.path.isdir(dossier):
        return []
    base = os.path.realpath(dossier)
    trouvees = []
    for racine, dossiers, fichiers in os.walk(base):
        niveau = os.path.relpath(racine, base).count(os.sep) + (racine != base)
        dossiers[:] = sorted(d for d in dossiers if not d.startswith(".")
                             and d not in DOSSIERS_SANS_VIDEO and niveau < profondeur)
        for f in sorted(fichiers):
            if os.path.splitext(f)[1].lower() in TYPES_VIDEO and not os.path.islink(os.path.join(racine, f)):
                trouvees.append(os.path.relpath(os.path.join(racine, f), base).replace(os.sep, "/"))
                if len(trouvees) >= limite:
                    return sorted(trouvees)
    return sorted(trouvees)


def fichier_presentation(a, quoi="video"):
    """Le chemin du fichier, s'il existe DANS le dossier du projet."""
    dossier = (a or {}).get("path") or ""
    if not dossier or quoi not in PRESENTATION:
        return None
    relatif = PRESENTATION[quoi][0]
    choisi = (a or {}).get("presentation_fichier")
    if choisi:
        if quoi == "affiche":
            return None
        relatif = choisi
    base = os.path.realpath(dossier)
    cible = os.path.realpath(os.path.join(base, relatif))
    # Un lien qui sortirait du projet ne mene a rien.
    if not cible.startswith(base + os.sep) or not os.path.isfile(cible):
        return None
    return cible


def presentation_de(a):
    """{maj, affiche} si la video existe, sinon None. maj sert a renouveler
    le cache du navigateur quand la video est regeneree."""
    video = fichier_presentation(a, "video")
    if not video:
        return None
    try:
        maj = int(os.path.getmtime(video))
    except OSError:
        return None
    return {"maj": maj, "affiche": bool(fichier_presentation(a, "affiche"))}


@flask_app.get("/api/presentations/<nom>/<quoi>")
@require_auth
def api_presentation(nom, quoi):
    """La video (ou l'affiche) d'une application, a qui peut l'ouvrir.

    conditional=True : le navigateur demande des morceaux (Range), et peut
    avancer dans la video sans tout telecharger.
    """
    a = load().get(nom)
    autorises = projets_autorises()
    if (quoi not in PRESENTATION or not a or not peut_voir(nom)
            or (autorises is not None and nom not in autorises)
            or (a.get("presentation_masquee") and not est_admin())):
        return jsonify({"error": "Présentation introuvable."}), 404
    chemin = fichier_presentation(a, quoi)
    if not chemin:
        return jsonify({"error": "Présentation introuvable."}), 404
    type_mime = (TYPES_VIDEO.get(os.path.splitext(chemin)[1].lower(), "video/mp4")
                 if quoi == "video" else PRESENTATION[quoi][1])
    reponse = send_file(chemin, mimetype=type_mime, conditional=True, max_age=0)
    reponse.headers["Cache-Control"] = "private, no-cache"
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    return reponse


@flask_app.put("/api/apps/<nom>/presentation")
@require_admin
def api_presentation_visibilite(nom):
    """Montrer ou retirer la video du hub ({visible}), sans toucher au
    fichier ; ou choisir la video a montrer ({fichier}, un chemin relatif
    parmi ceux de videos_du_projet ; vide : video/presentation.mp4)."""
    d = request.get_json(force=True, silent=True) or {}
    apps = load()
    if nom not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    if "fichier" in d:
        fichier = d.get("fichier") or ""
        if not isinstance(fichier, str) or (fichier and fichier not in videos_du_projet(apps[nom])):
            return jsonify({"error": "Cette vidéo n'est pas dans le dossier du projet."}), 400
        if fichier:
            apps[nom]["presentation_fichier"] = fichier
        else:
            apps[nom].pop("presentation_fichier", None)
    if "visible" in d:
        if d.get("visible"):
            apps[nom].pop("presentation_masquee", None)
        else:
            apps[nom]["presentation_masquee"] = True
    save(apps)
    return jsonify({"ok": True, "visible": not apps[nom].get("presentation_masquee"),
                    "fichier": apps[nom].get("presentation_fichier", "")})


@flask_app.get("/api/apps/<nom>/videos")
@require_admin
def api_videos_du_projet(nom):
    """Les videos qu'on peut choisir pour la presentation de l'application."""
    a = load().get(nom)
    if not a:
        return jsonify({"error": "Application inconnue."}), 404
    return jsonify({"fichiers": videos_du_projet(a), "choisi": a.get("presentation_fichier", ""),
                    "defaut": PRESENTATION["video"][0]})


@flask_app.get("/api/mes-apps")
@require_auth
def api_mes_apps():
    """Les projets ouvrables par la session, sans rien de plus.

    Ni chemin, ni commande, ni metriques : l'espace utilisateur n'a pas a
    connaitre l'organisation du serveur, et une reponse d'API est aussi
    publique que la page qui l'appelle.
    """
    autorises = projets_autorises()
    connues = lire_categories()
    # Ce que CE compte a range hors de son hub. La liste part avec la
    # reponse : la page des reglages doit pouvoir les rendre sans redemander,
    # et le hub doit savoir qu'il en cache -- une liste silencieusement plus
    # courte inquiete plus qu'elle ne simplifie.
    cachees = set(masquees_du_compte())
    liste, masquees = [], []
    toutes = load()
    versions = versions_des_apps(toutes)
    ouvertes = ouvertures_du_compte()
    admin = est_admin()
    for nom, a in sorted(toutes.items()):
        # Une application par defaut ne figure pas dans le hub d'un compte
        # utilisateur. Le proxy la lui refuse deja ; lui montrer une tuile qui
        # ne s'ouvre pas, ce serait promettre une porte fermee -- et il
        # chercherait ce qu'il a mal fait.
        if not peut_voir(nom):
            continue
        if autorises is not None and nom not in autorises:
            continue
        if nom in cachees:
            masquees.append({"name": nom, "titre": titre_de(nom, a),
                             "description": a.get("description") or ""})
            continue
        tuile = {
            "name": nom,
            "titre": titre_de(nom, a),
            "description": a.get("description") or "",
            "categorie": (CATEGORIE_CODELAB if est_app_par_defaut(nom)
                          else categorie_valide(a.get("categorie"), connues)),
            "visibility": visibilite(a),
            # Jamais ouverte par ce compte : pas d'indicateur. Sinon, une
            # version plus recente que celle vue a la derniere ouverture.
            "nouvelle_version": (nom in ouvertes
                                 and versions.get(nom, 0) > ouvertes[nom]["version"]),
            "ouverte_le": (ouvertes.get(nom) or {}).get("ts"),
            # La video de presentation, si le projet en a une et qu'elle
            # n'est pas retiree du hub.
            "presentation": (None if a.get("presentation_masquee") else presentation_de(a)),
        }
        # L'ETAT NE REGARDE QUE L'ADMINISTRATEUR. Un compte utilisateur ne
        # doit jamais savoir ce qui marche ou non : ni pastille, ni compteur,
        # ni aperçu qui trahirait une panne. La reponse ne porte donc pas
        # l'etat -- le cacher dans la page seulement le laisserait lisible
        # dans l'outil de developpement. Une application en panne, il la
        # decouvre en l'ouvrant, sur une page neutre (page_inaccessible).
        if admin:
            tuile["running"] = is_running(nom)
            tuile["listening"] = _listening.get(nom)
        liste.append(tuile)
    # Les categories accompagnent la liste : le hub les affiche dans l'ordre
    # voulu, sans avoir a deviner cet ordre a partir des projets.
    return jsonify({"apps": liste,
                    # A part, et pas melees aux applications : ce sont des
                    # services de la stack, que le panneau ne gere pas. La
                    # page les peint dans la meme grille, mais la liste des
                    # applications -- celle qu'on masque, celle qu'on cite
                    # dans un message -- reste ce qu'elle est.
                    "services": services_du_hub(),
                    # Les services RANGES partent a part des applications
                    # rangees : la page des reglages doit pouvoir les
                    # reproposer, et le choix « de quelle application
                    # veux-tu parler » ne doit toujours pas les voir.
                    "services_masques": services_masques(),
                    "masquees": masquees,
                    "categories": [CATEGORIE_CODELAB] + connues,
                    # Le widget Sauvegardes de l'administrateur : le resume,
                    # pas la liste (elle est dans Parametres > Securite).
                    "sauvegardes": resume_sauvegardes() if admin else None,
                    "utilisateur": utilisateur_courant(),
                    "role": role_courant()})


@flask_app.post("/api/messages")
@require_auth
def api_message():
    """Un mot laisse depuis le hub, range et envoye a l'administrateur.

    Trois sujets possibles, et pas un de plus : une application a laquelle on
    a acces, le hub lui-meme, ou l'idee d'une application qui manque. Un
    champ libre aurait demande a l'administrateur de deviner de quoi on
    parle -- ce qu'il ne peut pas faire depuis un mail de trois lignes.
    """
    d = request.get_json(force=True, silent=True) or {}
    cible = (d.get("cible") or "").strip()
    texte = (d.get("texte") or "").strip()
    nom_app = (d.get("app") or "").strip()
    qui = utilisateur_courant() or NOM_ADMIN

    if cible not in MESSAGE_CIBLES:
        return jsonify({"error": "Choisis de quoi tu veux parler."}), 400
    ids_pieces = d.get("pieces") or []
    if (not isinstance(ids_pieces, list) or len(ids_pieces) > PIECES_PAR_MESSAGE
            or not all(isinstance(x, str) for x in ids_pieces)):
        return jsonify({"error": f"{PIECES_PAR_MESSAGE} pièces jointes au plus."}), 400
    pieces = pieces_en_attente(qui, list(dict.fromkeys(ids_pieces)))
    if len(pieces) != len(set(ids_pieces)):
        return jsonify({"error": "Une pièce jointe a expiré : ajoute-la de nouveau."}), 400
    # Une maquette peut se passer de mots ; un message sans rien, non.
    if not texte and not pieces:
        return jsonify({"error": "Écris ton message."}), 400
    if len(texte) > MESSAGE_LONGUEUR_MAX:
        return jsonify({"error": "Message trop long : %d caractères pour %d au maximum."
                                 % (len(texte), MESSAGE_LONGUEUR_MAX)}), 400
    if cible == "application":
        # On ne parle que d'une application qu'on peut ouvrir : sinon ce
        # formulaire dirait l'existence de projets qu'on n'a pas le droit de
        # connaitre, exactement comme la liste des masquees.
        if nom_app not in load() or not peut_voir(nom_app):
            return jsonify({"error": "Application inconnue."}), 404
    else:
        nom_app = ""

    maintenant = time.time()
    if maintenant - _dernier_message.get(qui, 0) < MESSAGE_DELAI:
        return jsonify({"error": "Un message vient de partir. Laisse une minute "
                                 "avant le suivant."}), 429
    _dernier_message[qui] = maintenant

    message = {"id": secrets.token_hex(12), "ts": int(maintenant), "qui": qui,
               "cible": cible, "app": nom_app, "texte": texte, "lu": False,
               "statut": "en_attente", "archive": False}
    if pieces:
        message["pieces"] = pieces
    if not enregistrer_message(message):
        return jsonify({"error": "Message non enregistré : le disque n'a pas "
                                 "accepté l'écriture."}), 500

    # ENREGISTRE D'ABORD, ENVOYE ENSUITE. Un envoi qui echoue -- SMTP mal
    # configure, serveur injoignable -- ne doit pas faire perdre le message :
    # il est deja range, l'administrateur le verra dans le panneau.
    envoye, souci = False, ""
    cfg, ok = smtp_utilisable()
    destinataires = alertes_admin()
    if ok and destinataires:
        try:
            envoyer_mail(cfg, sujet_message(message), corps_message(message),
                         destinataires=destinataires)
            envoye = True
        except Exception as e:                                    # noqa: BLE001
            souci = f"{type(e).__name__}: {e}"
    extrait = texte.replace("\n", " ")[:140] or "Une pièce jointe."
    notifier_compte(NOM_ADMIN, f"Message de {qui}", extrait, url="/messages",
                    etiquette="message-" + message["id"])
    journaliser("message", qui=qui, app=nom_app or None,
                action=("envoye" if envoye else "enregistre sans mail"),
                ip=_adresse_client())
    return jsonify({"ok": True, "envoye": envoye, "erreur_mail": souci})


def sujet_message(message):
    ou = {"application": f"l'application « {message.get('app')} »",
          "hub": "le hub",
          "idee": "une idée d'application"}.get(message.get("cible"), "CodeLab")
    return f"[CodeLab] message de {message.get('qui')} sur {ou}"


def corps_message(message):
    quand = datetime.datetime.fromtimestamp(message.get("ts") or 0).strftime(
        "%Y-%m-%d %H:%M")
    sujet = {"application": f"Application : {message.get('app')}",
             "hub": "Sujet      : le hub",
             "idee": "Sujet      : une idee d'application"}.get(
                 message.get("cible"), "Sujet      : ?")
    pieces = message.get("pieces") or []
    jointes = ([f"Joint      : {', '.join(p['nom'] for p in pieces)} (a voir dans Messages)"]
               if pieces else [])
    return "\n".join([
        f"De         : {message.get('qui')}",
        sujet,
        f"Le         : {quand}",
        *jointes,
        "",
        (message.get("texte") or "").strip(),
        "",
        "-- CodeLab, message laisse depuis le hub",
    ])


@flask_app.post("/api/pieces")
@require_auth
def api_piece_deposer():
    """Depose une piece jointe : le corps EST le fichier, son nom dans X-Nom-Fichier.

    Rend son identifiant, que le message nommera a l'envoi.
    """
    donnees = request.get_data(cache=False)
    if not donnees:
        return jsonify({"error": "Fichier vide."}), 400
    if len(donnees) > PIECE_MAX:
        return _trop_gros(PIECE_MAX)
    type_, ext = type_de_piece(donnees)
    if not type_:
        return jsonify({"error": "Une image (PNG, JPEG, WebP, GIF) ou un PDF seulement."}), 415
    qui = utilisateur_courant() or NOM_ADMIN
    piece = {"id": secrets.token_hex(12), "nom": nom_de_piece(request.headers.get("X-Nom-Fichier"), ext),
             "type": type_, "taille": len(donnees)}
    with stockage.transaction(DB_FILE) as cx:
        _oublier_pieces_orphelines(cx)
        n = cx.execute("SELECT COUNT(*) FROM pieces WHERE message IS NULL AND qui = ?",
                       (qui,)).fetchone()[0]
        if n >= PIECES_EN_ATTENTE_MAX:
            return jsonify({"error": "Trop de pièces en attente : envoie ton message d'abord."}), 429
        cx.execute("INSERT INTO pieces (id, message, qui, nom, type, taille, ts, donnees) "
                   "VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
                   (piece["id"], qui, piece["nom"], type_, len(donnees), int(time.time()),
                    donnees))
    return jsonify(piece)


@flask_app.get("/api/pieces/<ident>")
@require_auth
def api_piece_lire(ident):
    """Une piece jointe : a l'administrateur, ou a qui l'a deposee."""
    lignes = stockage.lire(DB_FILE, "SELECT * FROM pieces WHERE id = ?", (ident,))
    if not lignes or (not est_admin() and lignes[0]["qui"] != utilisateur_courant()):
        return jsonify({"error": "Pièce introuvable."}), 404
    p = lignes[0]
    reponse = Response(bytes(p["donnees"]), mimetype=p["type"])
    reponse.headers["Content-Disposition"] = (
        "inline; filename*=UTF-8''" + urllib.parse.quote(p["nom"]))
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Cache-Control"] = "private, max-age=86400"
    if p["type"].startswith("image/"):
        # Une image n'execute rien ; on le dit quand meme au navigateur.
        reponse.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
    return reponse


@flask_app.get("/api/messages")
@require_admin
def api_messages_liste():
    """Les messages recus. Reserve a l'administrateur : ce sont des mots qui
    lui sont adresses, et ils portent le nom de qui les a ecrits.

    « apres » rend ce qui est arrive DEPUIS une date : c'est ce que demande
    la page toutes les vingt secondes pour annoncer un message nouveau. Sans
    lui, elle retelechargerait trois cents messages pour en decouvrir un.
    """
    try:
        apres = int(request.args.get("apres") or 0)
    except ValueError:
        apres = 0
    messages = lire_messages(limite=MESSAGES_AFFICHES)
    if apres:
        messages = [m for m in messages if (m.get("ts") or 0) > apres]
    # Les messages retenus d'abord : on les retient justement pour ne pas
    # avoir a les rechercher.
    messages.sort(key=lambda m: (not m.get("retenu"), -(m.get("ts") or 0)))
    return jsonify({"messages": messages,
                     "non_lus": sum(1 for m in messages if not m.get("lu")),
                     "boite": sum(1 for m in lire_messages(limite=MESSAGES_AFFICHES)
                                   if not m.get("archive"))})


@flask_app.post("/api/messages/lus")
@require_admin
def api_messages_lus():
    changes = marquer_messages_lus()
    return jsonify({"ok": True, "lus": changes})


@flask_app.put("/api/messages/<identifiant>")
@require_admin
def api_message_modifier(identifiant):
    """Change l'etat d'un message : retenu, statut, archive.

    Seules les cles PRESENTES changent. La route ne lisait que « retenu » :
    accepter, refuser ou archiver envoyaient leur etat, qui etait ignore --
    et l'epingle tombait au passage, faute de cle.
    """
    d = request.get_json(force=True, silent=True) or {}
    statut = d.get("statut")
    if statut is not None and statut not in STATUTS_MESSAGE:
        return jsonify({"error": "Statut inconnu."}), 400
    projet = _INCHANGE
    if "projet" in d:
        projet = d.get("projet") or None
        if projet and (projet == PROJET_GENERAL or not _projet_valide(projet)):
            return jsonify({"error": "Projet inconnu."}), 400
    if not modifier_message(identifiant,
                            retenu=bool(d["retenu"]) if "retenu" in d else None,
                            statut=statut,
                            archive=bool(d["archive"]) if "archive" in d else None,
                            projet=projet):
        return jsonify({"error": "Message introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.put("/api/messages/ordre")
@require_admin
def api_messages_ordre():
    """Ranger la boite a sa guise : {ordre: [ids]}, du haut vers le bas.

    Les messages nommes prennent ce rang ; un message qui arrive ensuite,
    sans rang, se place en tete jusqu'a ce qu'on le range.
    """
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > MESSAGES_MAX):
        return jsonify({"error": "Ordre invalide."}), 400
    if modifier_messages({mid: {"ordre": rang} for rang, mid in enumerate(ordre)}) != len(ordre):
        return jsonify({"error": "La liste nomme un message inconnu."}), 409
    return jsonify({"ok": True})


@flask_app.post("/api/messages/grouper")
@require_admin
def api_messages_grouper():
    """Regroupe des messages dans un projet : {ids, projet} ou {ids, nouveau_projet}.

    Des messages qui parlent de la meme chose forment un projet : on les y
    range ensemble, dans un projet qui existe ou qu'on cree a l'instant.
    """
    d = request.get_json(force=True, silent=True) or {}
    ids = d.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
        return jsonify({"error": "Choisis au moins un message."}), 400
    connus = {m.get("id") for m in lire_messages(limite=100000)}
    if not set(ids) <= connus:
        return jsonify({"error": "Message introuvable."}), 404
    nouveau = d.get("nouveau_projet")
    try:
        if isinstance(nouveau, dict):
            nom = _titre_tache(nouveau.get("nom"))[:PROJET_NOM_MAX]
            if not nom:
                return jsonify({"error": "Donne un nom au projet."}), 400
            projet = creer_projet(nom, _texte_long(nouveau.get("description"),
                                                  PROJET_DESCRIPTION_MAX), message=ids[0])
        else:
            projet = d.get("projet")
            if not projet or projet == PROJET_GENERAL or not _projet_valide(projet):
                return jsonify({"error": "Projet inconnu."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    modifier_messages({mid: {"projet": projet, "lu": True} for mid in ids})
    return jsonify({"ok": True, "projet": projet})


@flask_app.put("/api/messages/<identifiant>/lu")
@require_admin
def api_message_lu(identifiant):
    if not modifier_message(identifiant, lu=True):
        return jsonify({"error": "Message introuvable."}), 404
    _pg_deposer(("messages_lus",))
    return jsonify({"ok": True})


@flask_app.delete("/api/messages/<identifiant>")
@require_admin
def api_message_supprimer(identifiant):
    """Supprime un message, du fichier comme de la base."""
    if not modifier_message(identifiant, supprimer=True):
        return jsonify({"error": "Message introuvable."}), 404
    journaliser("message", qui=utilisateur_courant() or NOM_ADMIN,
                action="supprime", ip=_adresse_client())
    return jsonify({"ok": True})


# ------------------------------- les taches -------------------------------
#
# Un message accepte devient une tache, rangee dans un projet : une
# application declaree, ou « general » pour ce qui ne touche aucune d'elles.
# Une tache a des sous-taches qu'on coche une a une. Reserve a
# l'administrateur, comme les messages.
TACHE_TITRE_MAX = 200
SOUS_TACHES_MAX = 30
TACHES_MAX = 2000
PROJET_GENERAL = "general"
# L'application « Demandes » : un gestionnaire de taches. Une tache a un
# STATUT (sa colonne sur le tableau), une PRIORITE, et un ORDRE dans sa
# colonne, que le glisser-deposer fixe. « faite » reste tenue en accord avec
# le statut, pour les lectures anciennes.
STATUTS_TACHE = ("a_faire", "en_cours", "fait")
PRIORITES = (0, 1, 2, 3)              # urgente, haute, normale, basse
PRIORITE_NORMALE = 2
NOTES_MAX = 4000
# Un projet se CONSTRUIT puis se CONCRETISE : idee -> construction -> realise.
ETAPES_PROJET = ("idee", "construction", "realise")
PROJETS_MAX = 500
PROJET_NOM_MAX = 120
PROJET_DESCRIPTION_MAX = 4000
PREFIXE_PROJET = "p-"


def _titre_tache(brut):
    return " ".join(str(brut or "").split())[:TACHE_TITRE_MAX]


def _texte_long(brut, maximum):
    return str(brut or "").strip()[:maximum]


def _echeance_valide(brut):
    """Une date AAAA-MM-JJ, ou None. Rend False si elle est mal formee."""
    if brut in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(brut)).isoformat()
    except ValueError:
        return False


DUREE_MAX = 60                        # jours ouvres


def _duree_valide(brut):
    try:
        d = int(brut)
    except (TypeError, ValueError):
        return None
    return d if 1 <= d <= DUREE_MAX else None


def _priorite_valide(brut):
    try:
        p = int(brut)
    except (TypeError, ValueError):
        return None
    return p if p in PRIORITES else None


def _projet_valide(projet):
    """« general », une application declaree, ou un projet existant."""
    projet = str(projet or "").strip()
    if projet == PROJET_GENERAL:
        return projet
    if projet.startswith(PREFIXE_PROJET):
        try:
            existe = stockage.lire(DB_FILE, "SELECT 1 FROM projets WHERE id = ?", (projet,))
        except Exception:                                         # noqa: BLE001
            existe = []
        return projet if existe else None
    return projet if projet in load() else None


def lire_taches():
    """Les taches et leurs sous-taches, dans l'ordre du planning."""
    try:
        taches = stockage.lire(DB_FILE, "SELECT * FROM taches ORDER BY ordre, cree, rowid")
        sous = stockage.lire(DB_FILE, "SELECT * FROM sous_taches ORDER BY ordre, rowid")
    except Exception:                                             # noqa: BLE001
        return []
    par_tache = {}
    for st in sous:
        par_tache.setdefault(st["tache"], []).append(
            {"id": st["id"], "titre": st["titre"], "faite": bool(st["faite"])})
    return [{"id": t["id"], "projet": t["projet"], "titre": t["titre"],
             "message": t["message"], "cree": t["cree"], "faite": bool(t["faite"]),
             "statut": t["statut"], "priorite": t["priorite"], "echeance": t["echeance"],
             "notes": t["notes"], "ordre": t["ordre"], "duree": t["duree"],
             "fait_le": t["fait_le"],
             "sous_taches": par_tache.get(t["id"], [])} for t in taches]


def creer_tache(projet, titre, sous_taches=(), message=None, priorite=PRIORITE_NORMALE,
                statut="a_faire", echeance=None, notes="", duree=1, cx=None):
    """Cree la tache et ses sous-taches. Rend son id.

    En fin de planning : une tache neuve ne passe pas devant ce qu'on a
    deja ordonne a la main. Dans la transaction donnee, ou dans la sienne.
    """
    if cx is None:
        with stockage.transaction(DB_FILE) as cx:
            return creer_tache(projet, titre, sous_taches, message, priorite,
                               statut, echeance, notes, duree, cx=cx)
    ident = secrets.token_hex(8)
    nombre = cx.execute("SELECT COUNT(*) FROM taches").fetchone()[0]
    if nombre >= TACHES_MAX:
        raise ValueError("Trop de tâches : termine ou supprime les anciennes.")
    fin = cx.execute("SELECT COALESCE(MAX(ordre), -1) + 1 FROM taches").fetchone()[0]
    maintenant = int(time.time())
    cx.execute("INSERT INTO taches (id, projet, titre, message, cree, faite, ordre, statut,"
               " priorite, echeance, notes, duree, fait_le)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (ident, projet, titre, message, maintenant, 1 if statut == "fait" else 0,
                fin, statut, priorite, echeance, notes, duree,
                maintenant if statut == "fait" else None))
    for ordre, st in enumerate(sous_taches):
        cx.execute("INSERT INTO sous_taches (id, tache, titre, ordre) VALUES (?, ?, ?, ?)",
                   (secrets.token_hex(8), ident, st, ordre))
    return ident


def _sous_taches_propres(liste):
    propres = [_titre_tache(x) for x in (liste or []) if _titre_tache(x)]
    return propres[:SOUS_TACHES_MAX]


def lire_projets():
    try:
        lignes = stockage.lire(DB_FILE, "SELECT * FROM projets ORDER BY ordre, cree, rowid")
    except Exception:                                             # noqa: BLE001
        return []
    return [{"id": p["id"], "nom": p["nom"], "description": p["description"],
             "etape": p["etape"], "application": p["application"], "message": p["message"],
             "cree": p["cree"], "maj": p["maj"], "ordre": p["ordre"]} for p in lignes]


def creer_projet(nom, description="", etape="idee", message=None, taches=(), cx=None):
    """Cree un projet, et ses premieres taches (une par titre). Rend son id."""
    if cx is None:
        with stockage.transaction(DB_FILE) as cx:
            return creer_projet(nom, description, etape, message, taches, cx=cx)
    if cx.execute("SELECT COUNT(*) FROM projets").fetchone()[0] >= PROJETS_MAX:
        raise ValueError(f"{PROJETS_MAX} projets au plus : archive ou supprime les anciens.")
    ident = PREFIXE_PROJET + secrets.token_hex(6)
    maintenant = int(time.time())
    fin = cx.execute("SELECT COALESCE(MAX(ordre), -1) + 1 FROM projets").fetchone()[0]
    cx.execute("INSERT INTO projets (id, nom, description, etape, message, cree, maj, ordre)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (ident, nom, description, etape, message, maintenant, maintenant, fin))
    for titre in taches:
        creer_tache(ident, titre, message=message, cx=cx)
    return ident


@flask_app.post("/api/messages/<identifiant>/accepter")
@require_admin
def api_message_accepter(identifiant):
    """Accepte une demande, et en fait une tache, un projet, ou rien.

    - « nouveau_projet » {nom, description} : la demande devient un PROJET a
      construire ; les lignes de « taches » en sont les premieres taches ;
    - « titre » (+ projet, priorite, sous_taches) : elle devient une TACHE ;
    - rien de tout cela : elle est acceptee sans suite.
    """
    d = request.get_json(force=True, silent=True) or {}
    if not any(m.get("id") == identifiant for m in lire_messages(limite=100000)):
        return jsonify({"error": "Message introuvable."}), 404
    tache = projet = None
    nouveau = d.get("nouveau_projet")
    titre = _titre_tache(d.get("titre"))
    try:
        if isinstance(nouveau, dict):
            nom = _titre_tache(nouveau.get("nom"))[:PROJET_NOM_MAX]
            if not nom:
                return jsonify({"error": "Donne un nom au projet."}), 400
            projet = creer_projet(nom, _texte_long(nouveau.get("description"), PROJET_DESCRIPTION_MAX),
                                  message=identifiant,
                                  taches=_sous_taches_propres(d.get("taches")))
        elif titre:
            cible = _projet_valide(d.get("projet"))
            if not cible:
                return jsonify({"error": "Projet inconnu."}), 400
            priorite = _priorite_valide(d.get("priorite", PRIORITE_NORMALE))
            if priorite is None:
                return jsonify({"error": "Priorité inconnue."}), 400
            tache = creer_tache(cible, titre, _sous_taches_propres(d.get("sous_taches")),
                                message=identifiant, priorite=priorite)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    modifier_message(identifiant, statut="accepte", lu=True)
    return jsonify({"ok": True, "tache": tache, "projet": projet})


# ------------------------- le suivi, cote utilisateur -------------------------
#
# Qui a ecrit veut savoir ce que son message est devenu, sans demander. Le
# suivi se DEDUIT de ce que l'administrateur fait deja dans Messages : lire,
# mettre a l'etude, accepter, refuser, en faire des taches, les cocher. Rien
# a remplir de plus de son cote.
#
# Un compte ne voit que SES messages, et de leurs taches seulement le titre
# et si elle est faite : ni les notes, ni le reste du planning.


def suivi_message(m, taches, projets):
    """L'etape d'un message, et ce qui en est ne."""
    lies = [t for t in taches if t["message"] == m.get("id")]
    projet = None
    for p in projets:
        if p["message"] == m.get("id") or p["id"] == m.get("projet"):
            projet = p
            break
    if projet:
        deja = {t["id"] for t in lies}
        lies += [t for t in taches if t["projet"] == projet["id"] and t["id"] not in deja]
    faites = [t for t in lies if t["statut"] == "fait"]
    ouvertes = [t for t in lies if t["statut"] != "fait"]
    statut = m.get("statut") or "en_attente"
    if statut == "refuse":
        etape = "refuse"
    elif (lies and not ouvertes) or (projet and projet["etape"] == "realise"):
        etape = "fait"
    elif faites or any(t["statut"] == "en_cours" for t in lies) or (
            projet and projet["etape"] == "construction"):
        etape = "en_cours"
    elif lies or projet or statut == "accepte":
        etape = "planifie"
    elif statut == "a_reflechir":
        etape = "etude"
    elif m.get("lu"):
        etape = "lu"
    else:
        etape = "envoye"
    echeances = sorted(t["echeance"] for t in ouvertes if t["echeance"])
    fins = [t["fait_le"] for t in faites if t["fait_le"]]
    return {"etape": etape,
            "taches": [{"titre": t["titre"], "faite": t["statut"] == "fait"} for t in lies],
            "faites": len(faites), "total": len(lies),
            "prevu": echeances[-1] if echeances and etape != "fait" else None,
            "fait_le": max(fins) if fins and etape == "fait" else None,
            "projet": projet["nom"] if projet else None}


def mes_messages():
    qui = utilisateur_courant() or NOM_ADMIN
    taches, projets = lire_taches(), lire_projets()
    vus = lire_preference("suivi_vu", qui) or {}
    if not isinstance(vus, dict):
        vus = {}
    rendus = []
    for m in lire_messages(limite=MESSAGES_AFFICHES):
        if m.get("qui") != qui:
            continue
        suivi = suivi_message(m, taches, projets)
        # Nouveau : l'etape a change depuis la derniere visite. Un message
        # qu'on vient d'envoyer n'a rien de neuf a annoncer.
        vu = vus.get(m.get("id"), "envoye")
        rendus.append({"id": m.get("id"), "ts": m.get("ts"), "cible": m.get("cible"),
                       "app": m.get("app") or "", "texte": m.get("texte") or "",
                       "pieces": m.get("pieces") or [],
                       "suivi": suivi, "nouveau": suivi["etape"] != vu})
    return rendus


@flask_app.get("/api/mes-messages")
@require_auth
def api_mes_messages():
    """Les messages de ce compte, chacun avec son avancement."""
    rendus = mes_messages()
    return jsonify({"messages": rendus, "nouveaux": sum(1 for m in rendus if m["nouveau"])})


@flask_app.post("/api/mes-messages/vus")
@require_auth
def api_mes_messages_vus():
    """Retient l'etape vue de chaque message : les pastilles s'eteignent."""
    ecrire_preference("suivi_vu", {m["id"]: m["suivi"]["etape"] for m in mes_messages()})
    return jsonify({"ok": True})


@flask_app.get("/api/taches")
@require_admin
def api_taches():
    return jsonify({"taches": lire_taches()})


@flask_app.post("/api/taches")
@require_admin
def api_tache_creer():
    d = request.get_json(force=True, silent=True) or {}
    titre = _titre_tache(d.get("titre"))
    projet = _projet_valide(d.get("projet"))
    if not titre:
        return jsonify({"error": "Le titre est obligatoire."}), 400
    if not projet:
        return jsonify({"error": "Projet inconnu."}), 400
    priorite = _priorite_valide(d.get("priorite", PRIORITE_NORMALE))
    statut = d.get("statut") or "a_faire"
    echeance = _echeance_valide(d.get("echeance"))
    if priorite is None:
        return jsonify({"error": "Priorité inconnue."}), 400
    if statut not in STATUTS_TACHE:
        return jsonify({"error": "Statut inconnu."}), 400
    if echeance is False:
        return jsonify({"error": "Échéance invalide (AAAA-MM-JJ)."}), 400
    duree = _duree_valide(d.get("duree", 1))
    if duree is None:
        return jsonify({"error": f"Durée invalide : de 1 à {DUREE_MAX} jours."}), 400
    # Une tache peut naitre d'un message : elle le garde en reference.
    message = d.get("message") or None
    if message and not any(m.get("id") == message for m in lire_messages(limite=100000)):
        return jsonify({"error": "Message introuvable."}), 404
    try:
        ident = creer_tache(projet, titre, _sous_taches_propres(d.get("sous_taches")),
                            message=message, priorite=priorite, statut=statut,
                            echeance=echeance, notes=_texte_long(d.get("notes"), NOTES_MAX),
                            duree=duree)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": ident})


@flask_app.put("/api/taches/<ident>")
@require_admin
def api_tache_modifier(ident):
    """Seuls les champs PRESENTS changent. Changer de statut tient « faite »
    et « fait_le » en accord ; la tache garde sa place dans le planning."""
    d = request.get_json(force=True, silent=True) or {}
    champs, valeurs = [], []
    if "titre" in d:
        titre = _titre_tache(d.get("titre"))
        if not titre:
            return jsonify({"error": "Le titre est obligatoire."}), 400
        champs.append("titre = ?"); valeurs.append(titre)
    statut = None
    if "statut" in d:
        statut = d.get("statut")
        if statut not in STATUTS_TACHE:
            return jsonify({"error": "Statut inconnu."}), 400
    elif "faite" in d:
        statut = "fait" if d.get("faite") else "a_faire"
    if "projet" in d:
        projet = _projet_valide(d.get("projet"))
        if not projet:
            return jsonify({"error": "Projet inconnu."}), 400
        champs.append("projet = ?"); valeurs.append(projet)
    if "priorite" in d:
        priorite = _priorite_valide(d.get("priorite"))
        if priorite is None:
            return jsonify({"error": "Priorité inconnue."}), 400
        champs.append("priorite = ?"); valeurs.append(priorite)
    if "echeance" in d:
        echeance = _echeance_valide(d.get("echeance"))
        if echeance is False:
            return jsonify({"error": "Échéance invalide (AAAA-MM-JJ)."}), 400
        champs.append("echeance = ?"); valeurs.append(echeance)
    if "notes" in d:
        champs.append("notes = ?"); valeurs.append(_texte_long(d.get("notes"), NOTES_MAX))
    if "duree" in d:
        duree = _duree_valide(d.get("duree"))
        if duree is None:
            return jsonify({"error": f"Durée invalide : de 1 à {DUREE_MAX} jours."}), 400
        champs.append("duree = ?"); valeurs.append(duree)
    if not champs and statut is None:
        return jsonify({"error": "Rien à modifier."}), 400
    with stockage.transaction(DB_FILE) as cx:
        ligne = cx.execute("SELECT statut FROM taches WHERE id = ?", (ident,)).fetchone()
        if not ligne:
            return jsonify({"error": "Tâche introuvable."}), 404
        if statut is not None and statut != ligne["statut"]:
            # La tache garde sa place dans le planning ; cochee, elle date
            # sa fin -- la feuille de route l'y affiche.
            champs += ["statut = ?", "faite = ?", "fait_le = ?"]
            valeurs += [statut, 1 if statut == "fait" else 0,
                        int(time.time()) if statut == "fait" else None]
        if champs:
            cx.execute(f"UPDATE taches SET {', '.join(champs)} WHERE id = ?", (*valeurs, ident))
    return jsonify({"ok": True})


@flask_app.put("/api/taches/ordre")
@require_admin
def api_taches_ordre():
    """Une colonne du tableau, apres un glisser-deposer : {statut, ordre}.

    « ordre » nomme les taches de la colonne de haut en bas -- la plus haute
    est la plus prioritaire. Une tache venue d'une autre colonne y change de
    statut. Chaque id doit exister ; la liste ne peut pas en inventer.
    """
    d = request.get_json(force=True, silent=True) or {}
    statut, ordre = d.get("statut"), d.get("ordre")
    if statut not in STATUTS_TACHE:
        return jsonify({"error": "Statut inconnu."}), 400
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > TACHES_MAX):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        connues = {r[0] for r in cx.execute("SELECT id FROM taches")}
        if not set(ordre) <= connues:
            return jsonify({"error": "La liste nomme une tâche inconnue."}), 409
        for rang, tid in enumerate(ordre):
            cx.execute("UPDATE taches SET statut = ?, faite = ?, ordre = ?, "
                       "fait_le = CASE WHEN ? = 'fait' THEN COALESCE(fait_le, ?) ELSE NULL END "
                       "WHERE id = ?",
                       (statut, 1 if statut == "fait" else 0, rang, statut, int(time.time()), tid))
    return jsonify({"ok": True})


@flask_app.put("/api/taches/plan")
@require_admin
def api_taches_plan():
    """L'ORDRE DU PLANNING : {ordre: [ids]}, la premiere tache d'abord.

    C'est ainsi qu'on priorise -- par la place, pas par une etiquette
    d'urgence. Les taches sans date se suivent sur la feuille de route dans
    cet ordre. Le statut n'est pas touche. Chaque id doit exister.
    """
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > TACHES_MAX):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        connues = {r[0] for r in cx.execute("SELECT id FROM taches")}
        if not set(ordre) <= connues:
            return jsonify({"error": "La liste nomme une tâche inconnue."}), 409
        for rang, tid in enumerate(ordre):
            cx.execute("UPDATE taches SET ordre = ? WHERE id = ?", (rang, tid))
    return jsonify({"ok": True})


@flask_app.post("/api/taches/<ident>/scinder")
@require_admin
def api_tache_scinder(ident):
    """Scinde une tache : {titres: [...]}, deux au moins.

    La premiere ligne devient le titre de la tache ; les suivantes sont de
    nouvelles taches du meme projet, nees du meme message, placees juste
    apres elle dans le planning. La duree se partage entre elles.
    """
    titres = [_titre_tache(t) for t in ((request.get_json(force=True, silent=True) or {})
                                         .get("titres") or []) if _titre_tache(t)]
    if len(titres) < 2:
        return jsonify({"error": "Donne au moins deux titres pour scinder."}), 400
    if len(titres) > SOUS_TACHES_MAX:
        return jsonify({"error": f"{SOUS_TACHES_MAX} morceaux au plus."}), 400
    with stockage.transaction(DB_FILE) as cx:
        t = cx.execute("SELECT * FROM taches WHERE id = ?", (ident,)).fetchone()
        if not t:
            return jsonify({"error": "Tâche introuvable."}), 404
        if cx.execute("SELECT COUNT(*) FROM taches").fetchone()[0] + len(titres) - 1 > TACHES_MAX:
            return jsonify({"error": "Trop de tâches : termine ou supprime les anciennes."}), 400
        part = max(1, round((t["duree"] or 1) / len(titres)))
        cx.execute("UPDATE taches SET ordre = ordre + ? WHERE ordre > ?",
                   (len(titres) - 1, t["ordre"]))
        cx.execute("UPDATE taches SET titre = ?, duree = ? WHERE id = ?", (titres[0], part, ident))
        nouvelles = []
        for i, titre in enumerate(titres[1:], start=1):
            nid = secrets.token_hex(8)
            cx.execute("INSERT INTO taches (id, projet, titre, message, cree, faite, ordre, statut,"
                       " priorite, echeance, notes, duree) VALUES (?, ?, ?, ?, ?, 0, ?, 'a_faire',"
                       " ?, NULL, '', ?)",
                       (nid, t["projet"], titre, t["message"], int(time.time()),
                        t["ordre"] + i, t["priorite"], part))
            nouvelles.append(nid)
    return jsonify({"ok": True, "taches": [ident] + nouvelles})


@flask_app.delete("/api/taches/<ident>")
@require_admin
def api_tache_supprimer(ident):
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute("DELETE FROM taches WHERE id = ?", (ident,)).rowcount
    if not n:
        return jsonify({"error": "Tâche introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.get("/api/projets")
@require_admin
def api_projets():
    return jsonify({"projets": lire_projets()})


# ------------------------- exporter un projet -------------------------
#
# TOUT LE PROJET, D'UN COUP : sa description, ses taches dans l'ordre du
# planning, les messages qui l'ont fait naitre et leurs pieces jointes. Un
# fichier ZIP, que le systeme ouvre tout seul : un « projet.md » a lire (ou a
# donner tel quel a qui va le construire), un « projet.json » pour un
# programme, et les pieces dans « pieces/ ».
ETAPES_NOMS = {"idee": "Idée", "construction": "En construction", "realise": "Réalisé"}
STATUTS_NOMS = {"a_faire": "À faire", "en_cours": "En cours", "fait": "Fait"}
PRIORITES_NOMS = {0: "urgente", 1: "haute", 2: "normale", 3: "basse"}
SUJETS_NOMS = {"hub": "Améliorer le hub", "idee": "Nouveau projet", "application": "Application"}


def _slug(texte):
    t = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:60] or "projet"


def _cles_de_projets(taches, projets):
    """Toutes les cles qui portent du travail : projets, applications, general."""
    cles = [p["id"] for p in projets]
    for t in taches:
        if t["projet"] not in cles:
            cles.append(t["projet"])
    return cles


def _contenu_projet(cle, taches, projets, messages):
    projet = next((p for p in projets if p["id"] == cle), None)
    if projet:
        nom = projet["nom"]
    elif cle == PROJET_GENERAL:
        nom = "Général"
    else:
        nom = (load().get(cle) or {}).get("titre") or cle
    siennes = [t for t in taches if t["projet"] == cle]
    lies = {t["message"] for t in siennes if t["message"]}
    if projet and projet["message"]:
        lies.add(projet["message"])
    msgs = sorted((m for m in messages if m.get("projet") == cle or m.get("id") in lies),
                  key=lambda m: m.get("ts") or 0)
    return {"cle": cle, "nom": nom, "projet": projet, "taches": siennes, "messages": msgs}


def _date(ts):
    return datetime.datetime.fromtimestamp(ts or 0).strftime("%d/%m/%Y")


def _markdown_projet(c, chemins):
    p = c["projet"] or {}
    ouvertes = [t for t in c["taches"] if t["statut"] != "fait"]
    faites = [t for t in c["taches"] if t["statut"] == "fait"]
    lignes = [f"# {c['nom']}", ""]
    meta = []
    if p.get("etape"):
        meta.append("Étape : " + ETAPES_NOMS.get(p["etape"], p["etape"]))
    if p.get("application"):
        meta.append("Application : " + p["application"])
    meta.append(f"{len(faites)}/{len(c['taches'])} tâches faites")
    meta.append("Exporté le " + datetime.datetime.now().strftime("%d/%m/%Y à %H:%M"))
    lignes += [" · ".join(meta), ""]
    if p.get("description"):
        lignes += ["## Description", "", p["description"].strip(), ""]

    def tache(t):
        coche = "x" if t["statut"] == "fait" else " "
        details = [STATUTS_NOMS.get(t["statut"], t["statut"]),
                   "priorité " + PRIORITES_NOMS.get(t["priorite"], str(t["priorite"]))]
        if t["echeance"]:
            details.append("pour le " + t["echeance"])
        if t["duree"] and t["duree"] != 1:
            details.append(f"{t['duree']} jours")
        if t["fait_le"]:
            details.append("fait le " + _date(t["fait_le"]))
        out = [f"- [{coche}] **{t['titre']}** — {', '.join(details)}"]
        out += [f"  - [{'x' if st['faite'] else ' '}] {st['titre']}" for st in t["sous_taches"]]
        if t["notes"].strip():
            out += ["", *("  > " + l for l in t["notes"].strip().splitlines()), ""]
        return out

    if ouvertes:
        lignes += ["## Tâches, dans l'ordre du planning", ""]
        for t in ouvertes:
            lignes += tache(t)
        lignes.append("")
    if faites:
        lignes += ["## Tâches faites", ""]
        for t in faites:
            lignes += tache(t)
        lignes.append("")
    if c["messages"]:
        lignes += [f"## Messages ({len(c['messages'])})", ""]
        for m in c["messages"]:
            sujet = SUJETS_NOMS.get(m.get("cible"), "")
            if m.get("cible") == "application" and m.get("app"):
                sujet += " · " + m["app"]
            lignes += [f"### {m.get('qui') or '?'} — {_date(m.get('ts'))} — {sujet}", ""]
            if (m.get("texte") or "").strip():
                lignes += [m["texte"].strip(), ""]
            for piece in m.get("pieces") or []:
                chemin = chemins.get(piece["id"])
                if not chemin:
                    continue
                lien = urllib.parse.quote(chemin)
                lignes += [f"![{piece['nom']}]({lien})" if piece["type"].startswith("image/")
                           else f"[{piece['nom']}]({lien})", ""]
    return "\n".join(lignes).rstrip() + "\n"


def _ecrire_projet(z, dossier, c):
    """Ecrit un projet dans l'archive, sous « dossier » (vide : a la racine)."""
    chemins, pris = {}, set()
    for m in c["messages"]:
        for piece in m.get("pieces") or []:
            lignes = stockage.lire(DB_FILE, "SELECT donnees FROM pieces WHERE id = ?", (piece["id"],))
            if not lignes:
                continue
            base = datetime.datetime.fromtimestamp(m.get("ts") or 0).strftime("%Y-%m-%d") + "-" + piece["nom"]
            nom, k = base, 2
            while nom in pris:
                racine, _, ext = base.rpartition(".")
                nom, k = f"{racine}-{k}.{ext}", k + 1
            pris.add(nom)
            chemins[piece["id"]] = "pieces/" + nom
            z.writestr(dossier + "pieces/" + nom, bytes(lignes[0]["donnees"]))
    z.writestr(dossier + "projet.md", _markdown_projet(c, chemins))
    donnees = {"nom": c["nom"], "cle": c["cle"], "projet": c["projet"], "taches": c["taches"],
               "messages": [dict(m, pieces=[dict(p, fichier=chemins.get(p["id"]))
                                            for p in m.get("pieces") or []])
                            for m in c["messages"]]}
    z.writestr(dossier + "projet.json", json.dumps(donnees, ensure_ascii=False, indent=2))


def _envoyer_zip(tampon, nom):
    tampon.seek(0)
    return send_file(tampon, mimetype="application/zip", as_attachment=True, download_name=nom)


@flask_app.get("/api/projets/<cle>/export")
@require_admin
def api_projet_export(cle):
    taches, projets = lire_taches(), lire_projets()
    if cle not in _cles_de_projets(taches, projets) and cle != PROJET_GENERAL:
        return jsonify({"error": "Projet introuvable."}), 404
    c = _contenu_projet(cle, taches, projets, lire_messages(limite=100000))
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        _ecrire_projet(z, "", c)
    return _envoyer_zip(tampon, f"{_slug(c['nom'])}-{time.strftime('%Y-%m-%d')}.zip")


@flask_app.get("/api/projets-export")
@require_admin
def api_projets_export():
    """Tous les projets, un dossier chacun, et un sommaire."""
    taches, projets = lire_taches(), lire_projets()
    messages = lire_messages(limite=100000)
    tampon = io.BytesIO()
    sommaire = ["# Projets CodeLab", "",
                "Exporté le " + datetime.datetime.now().strftime("%d/%m/%Y à %H:%M"), ""]
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        pris = set()
        for cle in _cles_de_projets(taches, projets):
            c = _contenu_projet(cle, taches, projets, messages)
            dossier, k = _slug(c["nom"]), 2
            while dossier in pris:
                dossier, k = f"{_slug(c['nom'])}-{k}", k + 1
            pris.add(dossier)
            _ecrire_projet(z, dossier + "/", c)
            ouvertes = sum(1 for t in c["taches"] if t["statut"] != "fait")
            sommaire.append(f"- [{c['nom']}]({dossier}/projet.md) — {ouvertes} tâche"
                            f"{'s' if ouvertes > 1 else ''} ouverte{'s' if ouvertes > 1 else ''}, "
                            f"{len(c['messages'])} message{'s' if len(c['messages']) > 1 else ''}")
        z.writestr("LISEZMOI.md", "\n".join(sommaire) + "\n")
    return _envoyer_zip(tampon, f"projets-codelab-{time.strftime('%Y-%m-%d')}.zip")


@flask_app.post("/api/projets")
@require_admin
def api_projet_creer():
    d = request.get_json(force=True, silent=True) or {}
    nom = _titre_tache(d.get("nom"))[:PROJET_NOM_MAX]
    if not nom:
        return jsonify({"error": "Donne un nom au projet."}), 400
    etape = d.get("etape") or "idee"
    if etape not in ETAPES_PROJET:
        return jsonify({"error": "Étape inconnue."}), 400
    try:
        ident = creer_projet(nom, _texte_long(d.get("description"), PROJET_DESCRIPTION_MAX),
                             etape=etape, taches=_sous_taches_propres(d.get("taches")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": ident})


@flask_app.put("/api/projets/<ident>")
@require_admin
def api_projet_modifier(ident):
    """Nom, description, etape (idee, construction, realise) et l'application
    qui le concretise. Seuls les champs PRESENTS changent."""
    d = request.get_json(force=True, silent=True) or {}
    champs, valeurs = [], []
    if "nom" in d:
        nom = _titre_tache(d.get("nom"))[:PROJET_NOM_MAX]
        if not nom:
            return jsonify({"error": "Donne un nom au projet."}), 400
        champs.append("nom = ?"); valeurs.append(nom)
    if "description" in d:
        champs.append("description = ?")
        valeurs.append(_texte_long(d.get("description"), PROJET_DESCRIPTION_MAX))
    if "etape" in d:
        if d.get("etape") not in ETAPES_PROJET:
            return jsonify({"error": "Étape inconnue."}), 400
        champs.append("etape = ?"); valeurs.append(d.get("etape"))
    if "application" in d:
        nom_app = str(d.get("application") or "").strip()
        if nom_app and nom_app not in load():
            return jsonify({"error": "Application inconnue."}), 400
        champs.append("application = ?"); valeurs.append(nom_app or None)
    if not champs:
        return jsonify({"error": "Rien à modifier."}), 400
    champs.append("maj = ?"); valeurs.append(int(time.time()))
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute(f"UPDATE projets SET {', '.join(champs)} WHERE id = ?",
                       (*valeurs, ident)).rowcount
    if not n:
        return jsonify({"error": "Projet introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.put("/api/projets/ordre")
@require_admin
def api_projets_ordre():
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        connus = {r[0] for r in cx.execute("SELECT id FROM projets")}
        if set(ordre) != connus or len(ordre) != len(connus):
            return jsonify({"error": "La liste ne correspond pas aux projets."}), 409
        for rang, pid in enumerate(ordre):
            cx.execute("UPDATE projets SET ordre = ? WHERE id = ?", (rang, pid))
    return jsonify({"ok": True})


@flask_app.delete("/api/projets/<ident>")
@require_admin
def api_projet_supprimer(ident):
    """Supprime le projet. Ses taches ne disparaissent pas avec lui : elles
    passent dans « General », ou l'on decide de leur sort."""
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute("DELETE FROM projets WHERE id = ?", (ident,)).rowcount
        if n:
            cx.execute("UPDATE taches SET projet = ? WHERE projet = ?", (PROJET_GENERAL, ident))
    if n:
        # Ses messages redeviennent « a classer ».
        modifier_messages({m["id"]: {"projet": None} for m in lire_messages(limite=100000)
                           if m.get("projet") == ident})
    if not n:
        return jsonify({"error": "Projet introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.post("/api/taches/<ident>/sous-taches")
@require_admin
def api_sous_tache_creer(ident):
    titre = _titre_tache((request.get_json(force=True, silent=True) or {}).get("titre"))
    if not titre:
        return jsonify({"error": "Le titre est obligatoire."}), 400
    with stockage.transaction(DB_FILE) as cx:
        if not cx.execute("SELECT 1 FROM taches WHERE id = ?", (ident,)).fetchone():
            return jsonify({"error": "Tâche introuvable."}), 404
        nombre = cx.execute("SELECT COUNT(*) FROM sous_taches WHERE tache = ?",
                            (ident,)).fetchone()[0]
        if nombre >= SOUS_TACHES_MAX:
            return jsonify({"error": f"{SOUS_TACHES_MAX} sous-tâches au plus."}), 400
        sid = secrets.token_hex(8)
        cx.execute("INSERT INTO sous_taches (id, tache, titre, ordre) VALUES (?, ?, ?, ?)",
                   (sid, ident, titre, nombre))
    return jsonify({"ok": True, "id": sid})


@flask_app.put("/api/taches/<ident>/ordre")
@require_admin
def api_sous_taches_ordre(ident):
    """L'ordre des sous-taches, apres un glisser-deposer : la plus haute est
    la plus prioritaire. La liste doit nommer exactement les sous-taches de
    la tache, ni plus ni moins."""
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        if not cx.execute("SELECT 1 FROM taches WHERE id = ?", (ident,)).fetchone():
            return jsonify({"error": "Tâche introuvable."}), 404
        actuelles = {r[0] for r in cx.execute("SELECT id FROM sous_taches WHERE tache = ?",
                                              (ident,))}
        if set(ordre) != actuelles or len(ordre) != len(actuelles):
            return jsonify({"error": "La liste ne correspond pas aux sous-tâches."}), 409
        for rang, sid in enumerate(ordre):
            cx.execute("UPDATE sous_taches SET ordre = ? WHERE id = ?", (rang, sid))
    return jsonify({"ok": True})


@flask_app.put("/api/sous-taches/<sid>")
@require_admin
def api_sous_tache_modifier(sid):
    d = request.get_json(force=True, silent=True) or {}
    champs, valeurs = [], []
    if "titre" in d:
        titre = _titre_tache(d.get("titre"))
        if not titre:
            return jsonify({"error": "Le titre est obligatoire."}), 400
        champs.append("titre = ?"); valeurs.append(titre)
    if "faite" in d:
        champs.append("faite = ?"); valeurs.append(1 if d.get("faite") else 0)
    if not champs:
        return jsonify({"error": "Rien à modifier."}), 400
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute(f"UPDATE sous_taches SET {', '.join(champs)} WHERE id = ?",
                       (*valeurs, sid)).rowcount
    if not n:
        return jsonify({"error": "Sous-tâche introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.delete("/api/sous-taches/<sid>")
@require_admin
def api_sous_tache_supprimer(sid):
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute("DELETE FROM sous_taches WHERE id = ?", (sid,)).rowcount
    if not n:
        return jsonify({"error": "Sous-tâche introuvable."}), 404
    return jsonify({"ok": True})
