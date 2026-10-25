# CodeLab -- LE PANNEAU, SESSIONS
# ---------------------------------------------------------------------------
# ROLE        les assertions SSO, le second facteur, la session, l'origine des applications.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   SSO des applications, double authentification, auth, origine des applications.
#

# --------------------------- SSO des applications ---------------------------
#
# Une application ne reçoit jamais le cookie Flask ni APP_MANAGER_SESSION_SECRET.
# Le proxy lui transmet une assertion courte, signee avec une cle Ed25519 privee
# qui reste dans le control-plane. La cle publique est injectee dans le
# processus : l'application peut donc verifier elle-meme l'identite sans
# recevoir un secret qui permettrait de forger une session CodeLab.
SSO_ISSUER = "codelab"
SSO_TOKEN_TTL = 60
_sso_private_key = None
_sso_public_key = ""


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64json(data):
    return _b64url(json.dumps(data, separators=(",", ":"), sort_keys=True).encode())


def _charger_cle_sso(raw):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (ImportError, ValueError, TypeError):
        return None


def _generer_cle_sso():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


def _public_sso_b64(cle):
    from cryptography.hazmat.primitives import serialization
    return _b64url(cle.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw))


def bootstrap_sso():
    """Charge ou cree la cle de signature SSO, sans jamais l'exposer aux apps."""
    global _sso_private_key, _sso_public_key
    from cryptography.hazmat.primitives import serialization

    raw = None
    encoded = read_shared_value("CODELAB_SSO_PRIVATE_KEY")
    if encoded:
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, TypeError):
            raw = None

    if raw:
        _sso_private_key = _charger_cle_sso(raw)

    if _sso_private_key is None:
        try:
            with open(SSO_PRIVATE_FILE, "rb") as f:
                raw = f.read()
            _sso_private_key = _charger_cle_sso(raw)
        except (OSError, ValueError, TypeError):
            _sso_private_key = None

    if _sso_private_key is None:
        try:
            _sso_private_key = _generer_cle_sso()
        except ImportError:
            print("[app-manager] cryptography absente : SSO des applications indisponible.",
                  flush=True)
            return False

    raw = _sso_private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    private_b64 = _b64url(raw)
    _sso_public_key = _public_sso_b64(_sso_private_key)

    written = upsert_shared_block(
        "codelab-sso",
        [
            "# Identite signee transmise aux applications par le reverse proxy.",
            "# CODELAB_SSO_PRIVATE_KEY : secret du control-plane, jamais transmis aux apps.",
            "# CODELAB_SSO_PUBLIC_KEY : cle publique permettant aux apps de verifier les assertions.",
        ],
        {
            "CODELAB_SSO_PRIVATE_KEY": private_b64,
            "CODELAB_SSO_PUBLIC_KEY": _sso_public_key,
        })
    if not written:
        try:
            with open(SSO_PRIVATE_FILE, "wb") as f:
                f.write(raw)
            os.chmod(SSO_PRIVATE_FILE, 0o600)
        except OSError as e:
            print(f"[app-manager] cle SSO non persistante ({e}).", flush=True)
    return True


def jeton_signe(nom_app, sujet, role, en_plus=None):
    """Une assertion JWT-like EdDSA valable 60 secondes, pour l'audience
    « nom_app » : le seul moyen qu'a une application de savoir qui parle."""
    if _sso_private_key is None:
        return ""
    maintenant = int(time.time())
    payload = {
        "iss": SSO_ISSUER,
        "sub": sujet,
        "preferred_username": sujet,
        "role": role,
        "aud": nom_app,
        "iat": maintenant,
        "exp": maintenant + SSO_TOKEN_TTL,
        **(en_plus or {}),
    }
    message = _b64json({"alg": "EdDSA", "typ": "JWT"}) + "." + _b64json(payload)
    signature = _b64url(_sso_private_key.sign(message.encode("ascii")))
    return message + "." + signature


def jeton_sso(nom_app, tournage=False):
    """L'identite de la session, pour une application.

    tournage : l'identite de l'administrateur, pour un laissez-passer de
    « codelab video » deja verifie par le proxy (voir « le laissez-passer de
    tournage »), avec « tournage »: true pour que l'application le sache.
    """
    if not (is_authed() or tournage):
        return ""
    if tournage:
        return jeton_signe(nom_app, NOM_ADMIN, "admin", {"tournage": True})
    utilisateur = utilisateur_courant()
    compte = None if est_admin() else lire_utilisateurs().get(utilisateur)
    en_plus = {}
    email = (compte or {}).get("email", "")
    if email:
        en_plus["email"] = email
        # Confirmee ou non : une application ne relie un compte local a
        # celui-ci par l'adresse QUE si elle est confirmee -- sinon, qui
        # saisit l'adresse d'un autre dans CodeLab prendrait son compte.
        en_plus["email_verified"] = bool((compte or {}).get("email_verifie"))
    return jeton_signe(nom_app, utilisateur, role_courant() or "", en_plus)


# LE PANNEAU LUI-MEME, quand il parle a une application par defaut (Messages)
# hors de toute session : la synchronisation, l'oubli d'un compte, la reprise
# des donnees. POURQUOI un role a part : l'application ouvre a « panneau »
# des routes qu'aucun compte n'atteint (/interne/...), et aucune session ne
# peut porter ce role -- role_courant() ne rend que « admin » ou
# « utilisateur ». DEPEND DE : verifier_l_identite (workspace/messages/app.py).
ROLE_PANNEAU = "panneau"


def jeton_du_panneau(nom_app):
    return jeton_signe(nom_app, "codelab", ROLE_PANNEAU)


def admin_password():
    return _admin_password


# ------------------------- double authentification -------------------------
#
# TOTP (RFC 6238) : le code a six chiffres d'une application d'authentification.
# Ecrit ici plutot qu'importe : l'algorithme tient en vingt lignes avec la
# bibliotheque standard, et ce service n'a que trois dependances -- en ajouter
# une pour cela serait disproportionne.
#
# A quoi cela sert : le mot de passe du panneau est un secret unique. S'il
# fuit -- capture sur un reseau, note quelque part, reutilise -- il donne
# l'execution de commandes sur la machine. Le second facteur exige en plus un
# appareil physique, et une fuite du seul mot de passe ne suffit plus.
#
# Desactive par defaut : sans secret enregistre, la connexion se fait comme
# avant. C'est un reglage a activer depuis le panneau le jour ou il est
# expose au-dela du reseau local.
TOTP_PAS = 30          # secondes par code, valeur universelle
TOTP_CHIFFRES = 6
TOTP_TOLERANCE = 1     # +/- un intervalle, pour une horloge legerement decalee


def totp_nouveau_secret():
    """20 octets aleatoires en base32, le format que lisent les applications."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret, compteur):
    # Le "=" de remplissage est retire du secret affiche (les applications ne
    # l'aiment pas) : il faut donc le remettre avant de decoder.
    cle = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    empreinte = hmac.new(cle, struct.pack(">Q", compteur), hashlib.sha1).digest()
    debut = empreinte[-1] & 0x0F
    tronque = struct.unpack(">I", empreinte[debut:debut + 4])[0] & 0x7FFFFFFF
    return str(tronque % (10 ** TOTP_CHIFFRES)).zfill(TOTP_CHIFFRES)


def totp_verifie(secret, code):
    code = (code or "").strip().replace(" ", "")
    if not secret or not code.isdigit() or len(code) != TOTP_CHIFFRES:
        return False
    compteur = int(time.time()) // TOTP_PAS
    for ecart in range(-TOTP_TOLERANCE, TOTP_TOLERANCE + 1):
        # compare_digest plutot que "==" : meme raison que pour le mot de passe.
        if secrets.compare_digest(code, totp_code(secret, compteur + ecart)):
            return True
    return False


def totp_actif():
    return bool(_totp_secret)


def totp_uri(secret, compte=None):
    """L'adresse otpauth:// que lisent les applications d'authentification.

    Le nom du compte apparait dans l'application du telephone : avec plusieurs
    comptes CodeLab sur le meme appareil, "CodeLab:admin" et "CodeLab:marie"
    se distinguent, la ou deux entrees "CodeLab" seraient indiscernables.
    """
    compte = compte or TOTP_COMPTE
    return (f"otpauth://totp/CodeLab:{compte}?secret={secret}"
            f"&issuer=CodeLab&algorithm=SHA1&digits={TOTP_CHIFFRES}&period={TOTP_PAS}")


def qr_svg(donnee):
    """L'adresse otpauth en QR code, en SVG. "" si la bibliotheque manque.

    Recopier une cle de 32 caracteres a la main sur un telephone est le
    moment ou l'inscription echoue : une lettre pour une autre, et le code
    genere ne tombera jamais juste. Le QR code supprime cette etape.

    Facultatif volontairement : le panneau doit rester lancable depuis un
    depot fraichement clone avec Flask pour seule dependance. Sans la
    bibliotheque, la page retombe sur la cle a saisir -- ce qui marchait
    hier marche encore.

    Le SVG est peint en noir sur blanc, quel que soit le theme : un lecteur
    de QR code a besoin de ce contraste, et un code clair sur fond sombre
    n'est pas lu par tous les telephones.
    """
    try:
        import qrcode
    except ImportError:
        return ""
    code = qrcode.QRCode(border=2)
    code.add_data(donnee)
    code.make(fit=True)
    grille = code.get_matrix()
    cote = len(grille)

    # Un rectangle par suite horizontale de modules noirs, pas un par module :
    # le SVG passe de plusieurs milliers de balises a quelques centaines.
    rects = []
    for y, ligne in enumerate(grille):
        x = 0
        while x < cote:
            if not ligne[x]:
                x += 1
                continue
            debut = x
            while x < cote and ligne[x]:
                x += 1
            rects.append(f'<rect x="{debut}" y="{y}" width="{x - debut}" height="1"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {cote} {cote}" shape-rendering="crispEdges">'
            f'<rect width="{cote}" height="{cote}" fill="#ffffff"/>'
            f'<g fill="#000000">{"".join(rects)}</g></svg>')


# --------------------------- auth ---------------------------

RATE_LIMIT_WINDOW = 300  # 5 min
RATE_LIMIT_MAX = 5


def _client_ip():
    """L'adresse du client : celle de la connexion, jamais X-Forwarded-For.

    POURQUOI : n'importe quel client pose l'en-tete qu'il veut. S'y fier
      laissait se dire « 192.168.1.10 » depuis Internet -- et passer la
      restriction de l'administration au reseau local -- ou changer
      d'adresse a chaque essai, ce qui annulait la limite de tentatives.
      Derriere un proxy, tous les visiteurs partagent donc son adresse :
      plus strict (la limite les compte ensemble), jamais moins.
    REGLE : 6 (limitation des essais) et la restriction de l'administration.
    ECARTE : un reglage « proxy de confiance » (Exposition,
      APP_MANAGER_TRUST_PROXY), retire : la securite la plus stricte n'est
      pas une option.
    """
    return request.remote_addr or "unknown"


def rate_limited():
    ip = _client_ip()
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= RATE_LIMIT_MAX


def register_failed_attempt():
    ip = _client_ip()
    _login_attempts.setdefault(ip, []).append(time.time())


def detail_pour_admin(message, e):
    """Le message d'une erreur technique, selon qui le lit.

    L'administrateur recoit le detail (le type et le texte de l'exception) :
    c'est lui qui repare. Un compte utilisateur, ou une page de connexion,
    ne recoit que la phrase simple -- les erreurs techniques ne remontent
    qu'a l'administrateur. Le detail part toujours dans le journal du
    conteneur, pour qu'il ne soit pas perdu.
    """
    detail = f"{type(e).__name__}: {e}"
    print(f"[app-manager] {message} ({detail})", flush=True)
    if is_authed() and est_admin():
        return f"{message} ({detail})"
    return message


def avec_essais_restants(message):
    """Le refus dit combien d'essais il reste avant la pause.

    « Identifiants incorrects. » seul laissait taper au hasard jusqu'au
    blocage, puis decouvrir une attente de cinq minutes sans l'avoir vue
    venir. A appeler APRES register_failed_attempt() : le compte inclut
    l'essai qu'on vient de rater. Rien n'est dit tant qu'il en reste plus de
    trois -- un mot de passe mal tape une fois n'appelle pas d'avertissement.
    """
    ip = _client_ip()
    now = time.time()
    faits = len([t for t in _login_attempts.get(ip, []) if now - t < RATE_LIMIT_WINDOW])
    restants = RATE_LIMIT_MAX - faits
    pause = f"{RATE_LIMIT_WINDOW // 60} minutes"
    if restants <= 0:
        return f"{message} Trop d'essais : patiente {pause} avant de réessayer."
    if restants <= 3:
        return (f"{message} Encore {restants} essai{'s' if restants > 1 else ''} "
                f"avant une pause de {pause}.")
    return message


def is_authed():
    return session.get("authed") is True


def _refus(message, code):
    """Refus coherent entre l'API et les pages : un appel fetch() veut un
    code et un message, un clic dans le navigateur veut la page de
    connexion."""
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), code
    # Une page du panneau demandee sans session : on y revient apres la
    # connexion (login.html ne suit qu'un chemin local, voir SUITE).
    if request.method == "GET" and request.path != "/":
        return redirect("/login?suite=" + urllib.parse.quote(request.path, safe="/"))
    return redirect("/login")


def require_auth(view):
    """Une session, quel que soit son role. Pour ce qu'un utilisateur voit."""
    def wrapped(*a, **kw):
        if not is_authed():
            return _refus("Non authentifie.", 401)
        return view(*a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


# --------------------- une origine a part pour les applications ---------------
#
# Le probleme, tant qu'il n'etait pas resolu : les applications etaient servies
# sous :9001/<nom>/, c'est-a-dire dans l'ORIGINE du panneau. Une faille XSS
# dans une application quelconque -- du code que tu ecris vite, pas du code
# durci -- donnait acces au panneau : lire son DOM, lire le jeton CSRF dans sa
# page, piloter la stack. Aucun jeton ne protege de cela, puisque le script
# hostile est dans la meme origine que ce qu'il attaque.
#
# Les applications ont donc leur propre port, donc leur propre ORIGINE :
# une origine, c'est un schema, un hote ET un port. Ce que cela change :
#
#   - un script d'une application ne lit plus le DOM du panneau ni sa page,
#     donc plus le jeton ;
#   - il peut encore ENVOYER une requete au panneau avec le cookie -- le
#     cookie est porte par l'hote, pas par le port, et SameSite raisonne en
#     "site", ou le port ne compte pas non plus. Mais sans le jeton, cette
#     requete est refusee. C'est ici que le jeton CSRF prend tout son sens :
#     seul, il ne servait a rien contre une application ; avec la separation
#     des origines, il devient ce qui la rend efficace.
#
# Le cookie de session continue par ailleurs d'etre retire avant d'atteindre
# l'application elle-meme (voir strip_session_cookie).
#
# 0 desactive la separation : les applications repassent sous le port du
# panneau, comme avant. C'est aussi ce qui se produit tout seul si le second
# port ne peut pas s'ouvrir -- mieux vaut des applications joignables et un
# avertissement qu'une stack a moitie morte apres une mise a jour.
APPS_PORT = int(os.environ.get("APP_MANAGER_APPS_PORT", "9002") or 0)

# Le port du panneau. Sert a une seule chose, mais elle compte : ecrire, DANS
# une page servie sur le port des applications, un lien qui ramene au hub.
PANEL_PORT = int(os.environ.get("MANAGER_PORT", "9001") or 9001)

# Pose par servir() une fois le second ecouteur reellement ouvert. Tant qu'il
# est faux, rien ne change : ni redirection, ni restriction.
_origines_separees = {"actif": False}


def origines_separees():
    return _origines_separees["actif"]


def requete_d_application():
    """Cette requete est-elle pour une application (le proxy, ou un
    sous-domaine) plutot que pour le panneau ?"""
    return (request.endpoint in ("proxy", "proxy_noslash")
            or bool(getattr(g, "sous_domaine", "")))


# Un nom d'hote ordinaire, et rien d'autre. Ce qui suit n'est pas de la
# prudence gratuite : request.host vient de l'en-tete Host, donc du CLIENT.
# Nous le recopions dans un attribut href (le ruban de retour, les pages
# d'erreur du proxy) et dans un en-tete Location. Un guillemet bien place
# dans un Host sortirait de l'attribut et deviendrait du script dans la page
# d'une application. Un nom d'hote n'a aucune raison de contenir autre chose
# que des lettres, des chiffres, un point ou un tiret : on refuse tout le
# reste au lieu de l'echapper -- il n'existe aucun cas legitime a rattraper.
_HOTE_ORDINAIRE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


def hote_propre(brut):
    """Le nom d'hote d'un en-tete Host, sans son port. Vide s'il est douteux.

    Werkzeug ecarte deja les Host malformes avant nous -- request.host rend
    alors une chaine vide. Ce filtre-ci ne fait donc pas double emploi par
    hasard : il tient tout seul, sans dependre de la version d'une
    bibliotheque ni de la porte d'entree utilisee, pour une regle dont le
    cout est une expression reguliere.
    """
    hote = (brut or "").split(":")[0]
    return hote if _HOTE_ORDINAIRE.match(hote) else ""


def hote_demande():
    """Le nom d'hote de la requete, s'il est propre. Sinon la chaine vide."""
    if not has_request_context():
        return ""
    return hote_propre(request.host)


def origine_applications():
    """Le port des applications, vu depuis le navigateur (en local).

    Chaine vide quand le nom d'hote n'est pas exploitable : l'appelant
    renonce alors a separer plutot que d'ecrire une adresse douteuse.
    """
    hote = hote_demande()
    if not hote:
        return ""
    return f"{request.scheme}://{hote}:{APPS_PORT}"


# UNE APPLICATION, UN SOUS-DOMAINE. Ouvert par un nom de domaine depuis
# internet (relaye par le VPS), le panneau sert chaque application sous son
# propre nom : « blog » sur https://blog.toi.duckdns.org quand le panneau est
# a https://toi.duckdns.org. Rien a declarer : le nom de l'application EST
# son adresse. Chaque sous-domaine est une origine a lui, la separation tient
# comme avec le port. En local -- une adresse IP, un nom sans point ou du
# reseau domestique --, rien ne change : le port des applications, /<nom>/.
_SUFFIXES_LOCAUX = (".local", ".lan", ".home", ".internal", ".localhost", ".arpa")


def sur_le_port_des_apps():
    return (origines_separees() and has_request_context()
            and str(request.environ.get("SERVER_PORT", "")) == str(APPS_PORT))


def schema_vu():
    """http ou https, tel que le navigateur le voit (derriere le VPS, nginx
    l'annonce dans X-Forwarded-Proto)."""
    annonce = (request.headers.get("X-Forwarded-Proto") or "").lower()
    return "https" if annonce == "https" or request.scheme == "https" else "http"


def _nom_de_domaine(hote):
    if "." not in hote or hote.endswith(_SUFFIXES_LOCAUX):
        return False
    try:
        ipaddress.ip_address(hote)
        return False
    except ValueError:
        return True


def domaine_des_applications():
    """Le domaine sous lequel chaque application prend son sous-domaine, ou "".

    Seulement pour une requete RELAYEE (le VPS pose X-Forwarded-*) qui arrive
    par un nom de domaine. Sur le port du panneau, c'est son nom d'hote ; sur
    celui des applications, ce nom d'hote moins l'application.
    """
    if not origines_separees() or not has_request_context():
        return ""
    if not (request.headers.get("X-Forwarded-Proto") or request.headers.get("X-Forwarded-For")):
        return ""
    hote = hote_demande().lower()
    if not _nom_de_domaine(hote):
        return ""
    if sur_le_port_des_apps():
        nom, _, reste = hote.partition(".")
        return reste if nom in load() and _nom_de_domaine(reste) else ""
    return hote


def application_du_sous_domaine():
    """Sur le port des applications : l'application que nomme le sous-domaine."""
    if not sur_le_port_des_apps() or not domaine_des_applications():
        return ""
    return hote_demande().lower().partition(".")[0]


def modele_adresse_application():
    """L'adresse d'une application, « {nom} » a remplacer : la page en fait
    les liens de toutes ses tuiles sans redemander au serveur."""
    domaine = domaine_des_applications()
    if domaine:
        return f"{schema_vu()}://{{nom}}.{domaine}/"
    base = origine_applications() if origines_separees() else ""
    return base + "/{nom}/"


def origine_de_l_application(nom):
    """L'origine (schema, hote, port) d'une application, ou "" si c'est
    celle du panneau."""
    adresse = modele_adresse_application().replace("{nom}", nom)
    m = re.match(r"^[a-z]+://[^/]+", adresse)
    return m.group(0) if m else ""


def cadres_permis():
    """La source frame-src des pages du panneau qui encadrent une application."""
    domaine = domaine_des_applications()
    if domaine:
        return f"{schema_vu()}://*.{domaine}"
    return (origine_applications() if origines_separees() else "") or "'self'"
