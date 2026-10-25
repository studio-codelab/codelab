# CodeLab -- LE PANNEAU, PAGES ET TACHES
# ---------------------------------------------------------------------------
# ROLE        les pages du panneau, les videos de presentation, le relais du hub vers l'application Messages et le suivi des messages.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   pages, videos de presentation, le hub et l'application Messages, suivi cote utilisateur.
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
    # Les applications par defaut sont inscrites par le panneau lui-meme, sous
    # un nom qu'il connait : « messages » est aussi l'adresse de la rubrique
    # des comptes, et c'est voulu (page_messagerie).
    for nom in sorted((set(load()) & adresses_reservees()) - set(APPS_PAR_DEFAUT)):
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
@require_auth
def page_messagerie():
    """L'adresse que donnent les notifications et les liens « Messages ».

    Un compte utilisateur y trouve SA rubrique du panneau : ecrire a
    l'administrateur et suivre ce que chaque message est devenu.
    L'administrateur, lui, part dans l'application Messages, sur le port des
    applications, comme vers toute application.
    """
    if not est_admin():
        return index()
    return redirect(adresse_application(MESSAGERIE_NOM), 302)


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

    esc = _echapper
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
    toutes = apps_actives()
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
            # Jamais ouverte depuis le hub par ce compte : une application
            # qu'il ne connait pas encore (badge « Nouveau », widget
            # Nouveautes). Pas pour l'administrateur : il les a declarees.
            "nouvelle": not admin and nom not in ouvertes,
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
        # La tuile de Messages dit ce qui attend, comme le compteur d'une
        # boite de reception.
        if nom == MESSAGERIE_NOM:
            nouvelles = non_lus_de_la_messagerie()
            tuile["non_lues"] = nouvelles
            if nouvelles:
                tuile["description"] = (f"{nouvelles} nouveau{'x' if nouvelles > 1 else ''} "
                                        f"message{'s' if nouvelles > 1 else ''} à lire.")
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


# ------------------------- le hub et l'application Messages -------------------------
#
# Le hub ecrit a l'administrateur et suit ses messages ; il ne les garde pas.
# Chaque route ci-dessous verifie ce que le panneau seul sait (le compte, ses
# droits, le delai), puis relaie a l'application Messages au nom du compte
# connecte (voir « la messagerie, une application »).

@flask_app.post("/api/messages")
@require_auth
def api_message():
    """Un mot laisse depuis le hub, range dans Messages et envoye a
    l'administrateur.

    Trois sujets possibles, et pas un de plus : une application a laquelle on
    a acces, le hub lui-meme, ou l'idee d'une application qui manque. Un
    champ libre aurait demande a l'administrateur de deviner de quoi on
    parle -- ce qu'il ne peut pas faire depuis un mail de trois lignes.
    """
    d = request.get_json(force=True, silent=True) or {}
    cible = (d.get("cible") or "").strip()
    nom_app = (d.get("app") or "").strip()
    qui = utilisateur_courant() or NOM_ADMIN
    if cible not in MESSAGE_CIBLES:
        return jsonify({"error": "Choisis de quoi tu veux parler."}), 400
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
    try:
        statut, r = appeler_messagerie_json(
            "POST", "/compte/messages",
            {"cible": cible, "app": nom_app, "texte": d.get("texte") or "",
             "pieces": d.get("pieces") or []}, au_nom_du_compte=True)
    except MessagerieIndisponible:
        return messagerie_injoignable()
    if statut != 200:
        return jsonify({"error": r.get("error") or "Message non enregistré."}), statut
    _dernier_message[qui] = maintenant
    message = r.get("message") or {}

    # ENREGISTRE D'ABORD, ENVOYE ENSUITE. Un envoi qui echoue -- SMTP mal
    # configure, serveur injoignable -- ne doit pas faire perdre le message :
    # il est deja range, l'administrateur le verra dans Messages.
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
    extrait = (message.get("texte") or "").replace("\n", " ")[:140] or "Une pièce jointe."
    notifier_compte(NOM_ADMIN, f"Message de {qui}", extrait, url="/messages",
                    etiquette="message-" + str(message.get("id") or ""))
    journaliser("message", qui=qui, app=nom_app or None,
                action=("envoye" if envoye else "enregistre sans mail"),
                ip=_adresse_client())
    _messagerie["ts"] = 0       # la tuile du hub recompte
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
    """Depose une piece jointe : le corps EST le fichier, son nom dans
    X-Nom-Fichier. Rend son identifiant, que le message nommera a l'envoi.
    La taille est verifiee avant (PIECE_MAX), la nature par l'application."""
    donnees = request.get_data(cache=False)
    if not donnees:
        return jsonify({"error": "Fichier vide."}), 400
    nom = str(request.headers.get("X-Nom-Fichier") or "")[:400]
    try:
        statut, r = appeler_messagerie_json(
            "POST", "/compte/pieces", donnees, au_nom_du_compte=True,
            entetes={"X-Nom-Fichier": nom, "Content-Type": "application/octet-stream"})
    except MessagerieIndisponible:
        return messagerie_injoignable()
    return jsonify(r), statut


# Ce que l'application dit d'une piece, et que le navigateur doit recevoir
# tel quel : le type reconnu aux premiers octets, et ce qui la rend inerte.
ENTETES_D_UNE_PIECE = ("content-type", "content-disposition", "x-content-type-options",
                       "cache-control", "content-security-policy")


@flask_app.get("/api/pieces/<ident>")
@require_auth
def api_piece_lire(ident):
    """Une piece jointe : a qui l'a deposee (ou a l'administrateur), dans
    « Mes messages ». L'application decide, d'apres le jeton du compte."""
    try:
        statut, entetes, data = appeler_messagerie(
            "GET", "/compte/pieces/" + urllib.parse.quote(ident, safe=""), au_nom_du_compte=True)
    except MessagerieIndisponible:
        return messagerie_injoignable()
    if statut != 200:
        return jsonify({"error": "Pièce introuvable."}), 404
    reponse = Response(data, 200)
    for cle in ENTETES_D_UNE_PIECE:
        if cle in entetes:
            reponse.headers[cle] = entetes[cle]
    return reponse


@flask_app.get("/api/messages/nouveaux")
@require_admin
def api_messages_nouveaux():
    """Ce qui est arrive DEPUIS une date : le panneau l'annonce dans son coin,
    ou que l'on soit. Les messages se lisent, eux, dans l'application."""
    try:
        apres = int(request.args.get("apres") or 0)
    except ValueError:
        apres = 0
    try:
        statut, r = appeler_messagerie_json("GET", f"/interne/nouveaux?apres={apres}")
    except MessagerieIndisponible:
        return messagerie_injoignable()
    return jsonify({"messages": r.get("messages") or []}), statut


# ------------------------- le suivi, cote utilisateur -------------------------
#
# Qui a ecrit veut savoir ce que son message est devenu, sans demander.
# L'application Messages deduit l'etape (suivi_message) de ce que
# l'administrateur y fait ; le panneau retient ce que CE compte a deja vu.

def mes_messages():
    """Les messages du compte, chacun avec son avancement. Leve
    MessagerieIndisponible si l'application ne repond pas."""
    qui = utilisateur_courant() or NOM_ADMIN
    statut, r = appeler_messagerie_json("GET", "/compte/messages", au_nom_du_compte=True)
    if statut != 200:
        raise MessagerieIndisponible(f"reponse {statut}")
    vus = lire_preference("suivi_vu", qui) or {}
    if not isinstance(vus, dict):
        vus = {}
    rendus = []
    for m in r.get("messages") or []:
        # Nouveau : l'etape a change depuis la derniere visite. Un message
        # qu'on vient d'envoyer n'a rien de neuf a annoncer.
        etape = (m.get("suivi") or {}).get("etape")
        rendus.append(dict(m, nouveau=etape != vus.get(m.get("id"), "envoye")))
    return rendus


@flask_app.get("/api/mes-messages")
@require_auth
def api_mes_messages():
    try:
        rendus = mes_messages()
    except MessagerieIndisponible:
        return messagerie_injoignable()
    return jsonify({"messages": rendus, "nouveaux": sum(1 for m in rendus if m["nouveau"])})


@flask_app.post("/api/mes-messages/vus")
@require_auth
def api_mes_messages_vus():
    """Retient l'etape vue de chaque message : les pastilles s'eteignent."""
    try:
        rendus = mes_messages()
    except MessagerieIndisponible:
        return messagerie_injoignable()
    ecrire_preference("suivi_vu", {m["id"]: m["suivi"]["etape"] for m in rendus})
    return jsonify({"ok": True})
