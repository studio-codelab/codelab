# CodeLab -- LE PANNEAU, ACCES PAR LE TUNNEL CLOUDFLARE
# ---------------------------------------------------------------------------
# ROLE        rendre le panneau et les applications joignables depuis
#             internet sans port ouvert sur la box : deux « Quick Tunnels »
#             Cloudflare (adresses *.trycloudflare.com, sans compte), ou un
#             tunnel nomme sur un domaine a soi ; lances, surveilles et
#             arretes par le panneau ; le passage de la connexion de
#             l'adresse du panneau a celle des applications.
#             Ne fait PAS : la configuration cote Cloudflare (le tunnel nomme
#             et ses adresses se creent dans son tableau de bord), Cloudflare
#             Access, ni l'acces de l'administrateur, qui reste au reseau
#             local.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau. Ses fonctions sont
#             appelees par 02-sessions (origines), 09-applications (reseau
#             local, HTTPS), 13-hub-et-proxy (le proxy) et 15-serveur-http
#             (les ecouteurs).
# CONTRAINTES Une requete du tunnel se reconnait a son PORT d'arrivee
#             (TUNNEL_PORTS, sur 127.0.0.1), jamais a un en-tete : voir
#             par_le_tunnel. Une adresse de tunnel n'entre dans une page,
#             une CSP ou une redirection qu'apres _HOTE_TUNNEL.
# STRUCTURE   le tunnel Cloudflare ; le passage de la connexion ; l'API.
# CHOIX       - cloudflared dans le conteneur du panneau, et pas en service
#               du compose : un interrupteur dans les Parametres suffit a
#               l'allumer, sans commande ni profil Compose (que CasaOS ne
#               propose pas).
#             - Deux tunnels, deux adresses : le panneau et les applications
#               gardent deux origines distinctes (voir « une origine a part
#               pour les applications », 02-sessions).
#             - Le tunnel nomme se configure chez Cloudflare (tableau de bord)
#               et se lance avec son seul jeton : le panneau n'a besoin
#               d'aucune cle d'API Cloudflare, qui ouvrirait tout le compte.
# A AMELIORER - Un sous-domaine par application en mode domaine (adresse
#               generique *.domaine chez Cloudflare), comme derriere le VPS.
#

# ------------------------------ le tunnel Cloudflare ------------------------------
#
# Vecu : « Integre Cloudflare a CodeLab. Cree une configuration que je peux
# activer/desactiver facilement [...] je ne veux pas payer pour un nom de
# domaine, et peu importe l'url generee ». Un Quick Tunnel ne demande ni
# compte ni domaine : cloudflared ouvre une connexion SORTANTE vers
# Cloudflare, qui donne une adresse https://<mots-au-hasard>.trycloudflare.com.
# Rien a ouvrir sur la box, rien a installer sur les appareils.
#
# Ce que cela coute, dit dans les Parametres et dans FONCTIONNALITES.md :
#   - l'adresse CHANGE a chaque redemarrage du tunnel (du conteneur). Le
#     panneau la montre, avec un QR code, et l'envoie par mail a
#     l'administrateur des qu'elle change ;
#   - Cloudflare la reserve aux essais (pas de garantie, un plafond de
#     requetes simultanees) : pour un usage personnel, cela suffit.
#
# LE PORT D'ARRIVEE DIT QUE LA REQUETE VIENT DU TUNNEL. cloudflared ne parle
# qu'a deux ecouteurs a lui, sur 127.0.0.1 : TUNNEL_PORT_PANNEAU et
# TUNNEL_PORT_APPS. Ni publies par le compose, ni joignables depuis le
# reseau : seul un processus du conteneur les atteint.
# POURQUOI le port et pas les en-tetes : vue du panneau, la connexion de
#   cloudflared vient de 127.0.0.1, donc du « reseau local ». Si l'on se
#   fiait seulement a X-Forwarded-For pour reconnaitre l'intermediaire
#   (proxy_non_declare), il suffirait que cet en-tete manque un jour pour
#   ouvrir l'administration a tout internet.
# REGLE : 12 quater (l'administration reste au reseau local, tunnel compris).
# DEPEND DE / PROTEGE AUSSI : client_est_local et proxy_non_declare
#   (09-applications), _client_ip (02-sessions), les origines
#   (origine_applications, domaine_des_applications, lien_panneau).
# ECARTE : reconnaitre le tunnel a son nom d'hote (*.trycloudflare.com) :
#   l'en-tete Host vient du client, un visiteur du reseau local l'ecrit
#   comme il veut.
TUNNEL_PORT_PANNEAU = 9011
TUNNEL_PORT_APPS = 9012
TUNNEL_PORTS = (TUNNEL_PORT_PANNEAU, TUNNEL_PORT_APPS)
TUNNEL_CIBLES = {"panneau": TUNNEL_PORT_PANNEAU, "apps": TUNNEL_PORT_APPS}
CLOUDFLARED = os.environ.get("APP_MANAGER_CLOUDFLARED", "/usr/local/bin/cloudflared")
TUNNEL_VEILLE = 5            # secondes entre deux tours de la supervision

# DEUX FACONS D'AVOIR UNE ADRESSE.
#   « aleatoire » : deux Quick Tunnels, sans compte, une adresse
#     *.trycloudflare.com chacun, tiree au hasard a chaque lancement.
#   « domaine » : un tunnel NOMME, cree dans le compte Cloudflare de
#     l'administrateur, sur un domaine a lui. Un seul cloudflared, lance avec
#     le jeton du tunnel ; les deux adresses (panneau, applications) sont
#     declarees chez Cloudflare (« Public hostnames ») vers
#     http://localhost:9011 et http://localhost:9012, et recopiees ici.
#     Elles ne changent plus.
# Vecu : « Je dois pouvoir personnaliser le nom de domaine facilement depuis
# l'interface ». Un Quick Tunnel ne se nomme pas : seul un tunnel nomme le
# permet, et il exige un domaine dans un compte Cloudflare.
TUNNEL_MODES = ("aleatoire", "domaine")
# Un cloudflared par entree, chacun avec son serveur de metriques sur
# 127.0.0.1 : /quicktunnel y rend l'adresse d'un Quick Tunnel, /ready dit si
# le tunnel nomme est connecte. Lus la plutot que dans le journal, dont le
# format n'est pas un contrat.
TUNNEL_METRIQUES = {"panneau": 20251, "apps": 20252, "domaine": 20253}

# LE JETON DU TUNNEL NOMME est un secret : qui le tient fait passer le
# tunnel par sa machine. POURQUOI ces precautions :
#   - dans le coffre (ecrire_reglage, chiffre), jamais en clair sur le disque
#     ni dans une reponse d'API (« jeton » : present ou non) ;
#   - transmis par TUNNEL_TOKEN et jamais par --token : la ligne de commande
#     de tout processus se lit dans /proc par n'importe quel utilisateur du
#     conteneur, donc par les applications ;
#   - cloudflared tourne sous un uid A LUI (TUNNEL_UID) : /proc/<pid>/environ
#     ne se lit que sous le meme uid, et aucune application ni aucun build ne
#     tourne sous celui-ci.
# REGLE : 16 (rien en clair sur le disque), 19 (rien de ce secret pour une
#   application), 12 quater.
# ECARTE : l'uid partage des applications (RUN_AS_UID) : un build sans
#   application tourne sous lui, et lirait l'environnement de cloudflared.
TUNNEL_UID = int(os.environ.get("APP_MANAGER_TUNNEL_UID", "1002"))
_JETON_TUNNEL = re.compile(r"^[A-Za-z0-9+/=_-]{40,4096}$")

# Une adresse de tunnel, et rien d'autre : elle part dans des liens, une CSP,
# un en-tete Location et un mail. La reponse de cloudflared comme la saisie
# de l'administrateur sont des donnees (AGENTS.md 3.1).
_HOTE_TUNNEL = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com$")
_HOTE_DOMAINE = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

_tunnel = {quoi: {"proc": None, "hote": "", "depuis": 0} for quoi in TUNNEL_METRIQUES}
_tunnel_verrou = threading.Lock()
_tunnel_signature = {"valeur": None}


def lire_tunnel():
    d = lire_parametre("tunnel", {})
    return d if isinstance(d, dict) else {}


def tunnel_voulu():
    return lire_tunnel().get("actif") is True


def mode_du_tunnel():
    mode = lire_tunnel().get("mode")
    return mode if mode in TUNNEL_MODES else "aleatoire"


def hote_de_domaine_valide(brut):
    """Une adresse saisie pour le tunnel nomme, en minuscules, ou ""."""
    hote = str(brut or "").strip().lower().rstrip(".")
    if not _HOTE_DOMAINE.match(hote) or hote.endswith(".trycloudflare.com"):
        return ""
    return hote


def jeton_de_tunnel_valide(brut):
    """Le jeton tel que Cloudflare le donne (« cloudflared service install
    <jeton> » se colle aussi), ou "" s'il n'en a pas la forme : du base64
    d'un JSON qui porte le compte (a), le tunnel (t) et le secret (s)."""
    jeton = str(brut or "").strip().split()[-1] if str(brut or "").strip() else ""
    if not _JETON_TUNNEL.match(jeton):
        return ""
    try:
        contenu = json.loads(base64.b64decode(jeton + "=" * (-len(jeton) % 4)))
    except Exception:                                             # noqa: BLE001
        return ""
    if not (isinstance(contenu, dict) and all(contenu.get(k) for k in ("a", "t", "s"))):
        return ""
    return jeton


def jeton_du_tunnel():
    return str(reglage("tunnel_jeton", "") or "")


def domaine_du_tunnel():
    """Les deux adresses declarees pour le tunnel nomme : {panneau, apps}."""
    d = lire_tunnel()
    return {"panneau": hote_de_domaine_valide(d.get("panneau")),
            "apps": hote_de_domaine_valide(d.get("apps"))}


def domaine_complet():
    d = domaine_du_tunnel()
    return bool(jeton_du_tunnel() and d["panneau"] and d["apps"] and d["panneau"] != d["apps"])


def tunnel_disponible():
    return os.path.isfile(CLOUDFLARED) and os.access(CLOUDFLARED, os.X_OK)


def par_le_tunnel():
    """Cette requete est-elle arrivee par l'un des deux tunnels ?"""
    return (has_request_context()
            and str(request.environ.get("SERVER_PORT", "")) in {str(p) for p in TUNNEL_PORTS})


def sur_le_tunnel_des_apps():
    return (has_request_context()
            and str(request.environ.get("SERVER_PORT", "")) == str(TUNNEL_PORT_APPS))


def origine_du_tunnel(quoi):
    """https://<adresse> du tunnel « panneau » ou « apps », ou "" s'il n'est
    pas (encore) pret."""
    if mode_du_tunnel() == "domaine":
        hote = domaine_du_tunnel()[quoi] if domaine_complet() else ""
        return "https://" + hote if hote else ""
    hote = _tunnel[quoi]["hote"]
    return "https://" + hote if hote and _HOTE_TUNNEL.match(hote) else ""


def _metriques(quoi, chemin):
    """(statut, corps) du serveur de metriques de ce cloudflared, ou (0, b"")."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{TUNNEL_METRIQUES[quoi]}{chemin}", timeout=2) as r:
            return r.status, r.read(4096)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:                                             # noqa: BLE001
        return 0, b""


def _adresse_attribuee(quoi):
    """L'adresse d'un Quick Tunnel, selon son serveur de metriques, ou ""."""
    statut, corps = _metriques(quoi, "/quicktunnel")
    if statut != 200:
        return ""
    try:
        hote = str(json.loads(corps.decode("utf-8", "replace")).get("hostname") or "")
    except Exception:                                             # noqa: BLE001
        return ""
    hote = hote.strip().lower()
    return hote if _HOTE_TUNNEL.match(hote) else ""


def _tunnel_nomme_connecte():
    """/ready : 200 quand le tunnel nomme a au moins une connexion a
    Cloudflare. Un cloudflared qui ne l'expose pas (404) compte comme
    connecte : il tourne, et c'est tout ce qu'on peut en dire."""
    return _metriques("domaine", "/ready")[0] in (200, 404)


def _pids_du_tunnel(quoi):
    """Les cloudflared de cette entree, reconnus a leur adresse de metriques
    -- y compris ceux d'un panneau precedent (les processus survivent a son
    redemarrage, comme les applications)."""
    marque = f"127.0.0.1:{TUNNEL_METRIQUES[quoi]}".encode()
    pids = []
    for entree in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not entree.isdigit():
            continue
        try:
            with open(f"/proc/{entree}/cmdline", "rb") as f:
                ligne = f.read()
        except OSError:
            continue
        morceaux = ligne.split(b"\0")
        if morceaux and morceaux[0].endswith(b"cloudflared") and marque in morceaux:
            pids.append(int(entree))
    return pids


def _sans_privileges_tunnel():
    """preexec de cloudflared : son propre uid et son propre groupe, sans
    celui de /workspace (voir TUNNEL_UID). Sans effet hors root (tests)."""
    if os.geteuid() != 0:
        return
    os.setgroups([])
    os.setgid(TUNNEL_UID)
    os.setuid(TUNNEL_UID)
    os.umask(0o077)


def _commande_du_tunnel(quoi):
    """(commande, environnement) de cette entree. Environnement VIDE hors
    PATH, HOME et, pour le tunnel nomme, son jeton : cloudflared n'a besoin
    d'aucun secret du panneau (meme regle que les applications, section
    « secrets transmis aux applications »)."""
    commande = [CLOUDFLARED, "tunnel", "--no-autoupdate",
                "--metrics", f"127.0.0.1:{TUNNEL_METRIQUES[quoi]}"]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    if quoi == "domaine":
        return commande + ["run"], {**env, "TUNNEL_TOKEN": jeton_du_tunnel()}
    return commande + ["--url", f"http://127.0.0.1:{TUNNEL_CIBLES[quoi]}"], env


def _lancer_le_tunnel(quoi):
    maison = os.path.join(tempfile.gettempdir(), "codelab-tunnel")
    os.makedirs(maison, mode=0o700, exist_ok=True)
    try:
        os.chown(maison, TUNNEL_UID, TUNNEL_UID)
    except OSError:
        pass
    commande, env = _commande_du_tunnel(quoi)
    journal = open(os.path.join(LOG_DIR, f"tunnel-{quoi}.log"), "wb")
    try:
        proc = subprocess.Popen(
            commande, stdin=subprocess.DEVNULL, stdout=journal, stderr=subprocess.STDOUT,
            env={**env, "HOME": maison}, cwd=maison,
            preexec_fn=_sans_privileges_tunnel, start_new_session=True)
    finally:
        journal.close()
    _tunnel[quoi].update(proc=proc, hote="", depuis=int(time.time()))
    print(f"[app-manager] tunnel {quoi} : cloudflared lance (pid {proc.pid}).", flush=True)


def _arreter_le_tunnel(quoi):
    proc = _tunnel[quoi]["proc"]
    pids = set(_pids_du_tunnel(quoi))
    if proc is not None and proc.poll() is None:
        pids.add(proc.pid)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if proc is not None:
        try:
            proc.wait(timeout=5)
        except Exception:                                         # noqa: BLE001
            pass
    _tunnel[quoi].update(proc=None, hote="", depuis=0)


def _tunnel_en_vie(quoi):
    proc = _tunnel[quoi]["proc"]
    if proc is not None and proc.poll() is None:
        return True
    return bool(_pids_du_tunnel(quoi))


def _annoncer_l_adresse(adresse):
    """L'adresse du panneau a change : l'administrateur la recoit par mail.

    POURQUOI : loin de chez soi, l'adresse d'un Quick Tunnel est la seule
    porte, et elle change a chaque redemarrage. Sans ce mail, une coupure de
    courant enferme dehors. Envoye meme alertes eteintes : ce n'est pas une
    alerte, c'est l'adresse. Une fois par adresse (« annoncee ») : un domaine
    a soi ne s'annonce donc qu'a sa mise en service.
    """
    if lire_tunnel().get("annoncee") == adresse:
        return
    ecrire_parametre("tunnel", {**lire_tunnel(), "annoncee": adresse})
    cibles = alertes_admin()
    if not cibles:
        return
    change = ("Elle change à chaque redémarrage du tunnel (ou du serveur). "
              if mode_du_tunnel() == "aleatoire" else "")
    corps = ("La nouvelle adresse de ton panneau CodeLab, depuis n'importe où :\n\n"
             f"{adresse}\n\n" + change
             + "Paramètres > Sécurité > Accès depuis internet la montre aussi, avec un QR code.")
    try:
        envoyer_avec_repli("CodeLab : nouvelle adresse depuis internet", corps, cibles)
    except Exception:                                             # noqa: BLE001
        pass


def entrees_voulues():
    """Les cloudflared qui doivent tourner, selon le reglage."""
    if not (tunnel_voulu() and tunnel_disponible() and origines_separees()):
        return ()
    if mode_du_tunnel() == "domaine":
        return ("domaine",) if domaine_complet() else ()
    return ("panneau", "apps")


def _signature_voulue():
    """Ce qui, en changeant, oblige a relancer : le mode et le jeton (une
    empreinte, pas le jeton). Les adresses du domaine, elles, se reglent chez
    Cloudflare : les changer ici ne relance rien."""
    return (mode_du_tunnel(), hashlib.sha256(jeton_du_tunnel().encode()).hexdigest())


def tour_du_tunnel():
    """Un tour de supervision : allume ce qui doit l'etre, eteint le reste,
    relit les adresses. Ne leve jamais."""
    with _tunnel_verrou:
        try:
            voulues = entrees_voulues()
            signature = _signature_voulue()
            relancer = (_tunnel_signature["valeur"] is not None
                        and _tunnel_signature["valeur"] != signature)
            _tunnel_signature["valeur"] = signature
            for quoi in TUNNEL_METRIQUES:
                if quoi not in voulues or relancer:
                    if _tunnel[quoi]["proc"] is not None or _pids_du_tunnel(quoi):
                        _arreter_le_tunnel(quoi)
                    if quoi not in voulues:
                        continue
                if not _tunnel_en_vie(quoi):
                    _lancer_le_tunnel(quoi)
                    continue
                if quoi == "domaine":
                    _tunnel[quoi]["hote"] = "connecte" if _tunnel_nomme_connecte() else ""
                    continue
                hote = _adresse_attribuee(quoi)
                if hote and hote != _tunnel[quoi]["hote"]:
                    _tunnel[quoi]["hote"] = hote
                    print(f"[app-manager] tunnel {quoi} : https://{hote}", flush=True)
            if voulues and tunnel_pret():
                _annoncer_l_adresse(origine_du_tunnel("panneau"))
        except Exception as e:                                    # noqa: BLE001
            print(f"[app-manager] tunnel : {type(e).__name__}: {e}", flush=True)


def tunnel_pret():
    if mode_du_tunnel() == "domaine":
        return domaine_complet() and bool(_tunnel["domaine"]["hote"])
    return bool(origine_du_tunnel("panneau") and origine_du_tunnel("apps"))


def demarrer_le_tunnel():
    def _boucle():
        while True:
            tour_du_tunnel()
            time.sleep(TUNNEL_VEILLE)
    threading.Thread(target=_boucle, daemon=True, name="tunnel").start()


def etat_du_tunnel():
    """Ce que montre la carte. Le jeton n'en sort jamais : seulement s'il
    est enregistre."""
    actif = tunnel_voulu()
    mode = mode_du_tunnel()
    if not tunnel_disponible():
        etat = "indisponible"
    elif not origines_separees():
        etat = "sans-origines"
    elif not actif:
        etat = "arrete"
    elif mode == "domaine" and not domaine_complet():
        etat = "incomplet"
    elif tunnel_pret():
        etat = "actif"
    else:
        etat = "demarrage"
    pret = etat == "actif"
    return {"actif": actif, "mode": mode, "etat": etat,
            "panneau": origine_du_tunnel("panneau") if pret else "",
            "apps": origine_du_tunnel("apps") if pret else "",
            "domaine": {**domaine_du_tunnel(), "jeton": bool(jeton_du_tunnel())},
            "cibles": {quoi: f"http://localhost:{port}" for quoi, port in TUNNEL_CIBLES.items()}}


# ------------------------------ le passage de la connexion ------------------------------
#
# Deux adresses : en *.trycloudflare.com, un suffixe public, chacune est un
# SITE a elle ; sur un domaine a soi, le cookie de session reste lie a son
# hote exact. Dans les deux cas, un cookie pose par l'une ne vaut jamais
# pour l'autre. La session ouverte sur
# l'adresse du panneau n'arrive donc pas a celle des applications, et une
# application privee y renverrait sans fin a la connexion.
#
# Le passage : depuis l'adresse du panneau, /passage/<app>/ tire un ticket
# (usage unique, 60 s, lie a l'application) et renvoie vers
# <adresse des applications>/<app>/?codelab-passage=<ticket>. Le proxy
# l'echange contre une session sur cette adresse : la MEME (meme sid, meme
# compte, meme heure d'ouverture), donc soumise aux memes gardes a chaque
# requete -- compte suspendu, mot de passe change, appareil deconnecte,
# deconnexion (revoquer_appareils) la ferment des deux cotes.
# POURQUOI cette forme : c'est exactement la situation du port des
# applications en local, ou le navigateur envoie deja le cookie de session
# (un cookie ignore le port). Le proxy le retire avant l'application
# (strip_session_cookie, regle 19), les API du panneau ne repondent pas sur
# cette origine (separer_les_origines) : rien de plus n'est ouvert.
# REGLE : 12 quater ; 19 (le cookie ne part jamais a une application).
# DEPEND DE : _proxy (13-hub-et-proxy), qui echange le ticket et renvoie ici
#   une application privee sans session ; lienOuverture (panneau/00-socle.js),
#   qui passe par ici quand CODELAB_PAGE.passage est vrai.
# ECARTE : le cookie en SameSite=None pour l'encadrer dans /ouvrir.html --
#   une session envoyee depuis n'importe quel site ; en mode application,
#   le hub ouvre donc l'application sans cadre par le tunnel. Un jeton dans
#   chaque adresse d'application : il finirait dans l'historique et les
#   liens partages.
PASSAGE_PARAMETRE = "codelab-passage"
PASSAGE_TTL = 60
_passages = {}
_passages_verrou = threading.Lock()


def _oublier_les_passages_finis():
    maintenant = time.time()
    for ticket in [t for t, v in _passages.items() if v["fin"] <= maintenant]:
        _passages.pop(ticket, None)


@flask_app.get("/passage/<n>/")
@flask_app.get("/passage/<n>/<path:chemin>")
@require_auth
def passage_vers_application(n, chemin=""):
    """Ouvre une application sur l'adresse des applications du tunnel, avec
    la session de l'adresse du panneau. Hors tunnel : l'adresse habituelle."""
    if n not in load():
        return Response(_page("Introuvable", "Aucune application « " + n + " »."),
                        404, mimetype="text/html")
    apps = origine_du_tunnel("apps")
    if not par_le_tunnel() or not apps:
        return redirect(adresse_application(n, chemin), 302)
    if est_admin():
        return Response(_page("Accès refusé", refus_admin_hors_reseau()
                              or "L'administration ne passe pas par le tunnel."),
                        403, mimetype="text/html")
    with _passages_verrou:
        _oublier_les_passages_finis()
        ticket = secrets.token_urlsafe(32)
        _passages[ticket] = {
            "app": n, "fin": time.time() + PASSAGE_TTL,
            "session": {"sid": session.get("sid"), "utilisateur": utilisateur_courant(),
                        "role": session.get("role"), "ouverte": session.get("ouverte")},
        }
    cible = (apps + "/" + urllib.parse.quote(n, safe="") + "/"
             + urllib.parse.quote(chemin_dans_app(chemin), safe="/%~")
             + "?" + urllib.parse.urlencode({PASSAGE_PARAMETRE: ticket}))
    return redirect(cible, 302)


def echanger_le_passage(name):
    """/<app>/?codelab-passage=<ticket>, sur l'adresse des applications du
    tunnel : le ticket, une seule fois, contre la session ; puis la meme
    page, sans le ticket dans l'adresse."""
    with _passages_verrou:
        entree = _passages.pop(request.args.get(PASSAGE_PARAMETRE, ""), None)
    if (not entree or entree["app"] != name or entree["fin"] <= time.time()
            or not sur_le_tunnel_des_apps() or not entree["session"].get("sid")
            or entree["session"].get("role") == ROLE_ADMIN):
        return Response(_page("Passage refusé",
                              "Ce lien d'ouverture est expiré ou déjà utilisé.",
                              "Rouvre l'application depuis le hub."), 403, mimetype="text/html")
    session.clear()
    session.update(entree["session"])
    session["authed"] = True
    session.permanent = True
    jeton_session()
    reste = [(k, v) for k, v in request.args.items(multi=True) if k != PASSAGE_PARAMETRE]
    return redirect(request.path + ("?" + urllib.parse.urlencode(reste) if reste else ""), 302)


# ------------------------------ l'API ------------------------------

@flask_app.get("/api/tunnel")
@require_admin
def api_tunnel_lire():
    return jsonify(etat_du_tunnel())


@flask_app.put("/api/tunnel")
@require_admin
def api_tunnel_ecrire():
    """{actif?, mode?, jeton?, oublier_jeton?, panneau?, apps?} : chaque
    champ absent garde sa valeur. Tout est verifie avant d'ecrire quoi que ce
    soit : un reglage a moitie enregistre laisserait un tunnel incoherent."""
    corps = request.get_json(silent=True) or {}
    actuel = lire_tunnel()
    nouveau = dict(actuel)
    if "actif" in corps:
        if not isinstance(corps["actif"], bool):
            return jsonify({"error": "Réglage invalide."}), 400
        nouveau["actif"] = corps["actif"]
    if "mode" in corps:
        if corps["mode"] not in TUNNEL_MODES:
            return jsonify({"error": "Type d'adresse inconnu."}), 400
        nouveau["mode"] = corps["mode"]
    for quoi in ("panneau", "apps"):
        if quoi in corps:
            hote = hote_de_domaine_valide(corps[quoi])
            if not hote:
                return jsonify({"error": "Adresse invalide : un nom de domaine, sans https:// "
                                         "ni chemin (ex. codelab.mondomaine.fr)."}), 400
            nouveau[quoi] = hote
    if nouveau.get("panneau") and nouveau.get("panneau") == nouveau.get("apps"):
        return jsonify({"error": "Le panneau et les applications ont besoin de deux adresses "
                                 "différentes : c'est ce qui les sépare."}), 400
    jeton = None
    if corps.get("jeton"):
        jeton = jeton_de_tunnel_valide(corps["jeton"])
        if not jeton:
            return jsonify({"error": "Ce n'est pas un jeton de tunnel Cloudflare : copie celui "
                                     "de la page du tunnel (Install connector)."}), 400
    oublier = corps.get("oublier_jeton") is True
    if nouveau.get("actif") and not tunnel_disponible():
        return jsonify({"error": "cloudflared est absent de cette image : mets CodeLab à jour."}), 409
    try:
        if jeton or oublier:
            ecrire_reglage("tunnel_jeton", None if oublier else jeton)
        ecrire_parametre("tunnel", nouveau)
    except Exception as e:                                        # noqa: BLE001
        return jsonify({"error": f"Réglage non enregistré : {e}"}), 500
    mode = nouveau.get("mode") or "aleatoire"
    journaliser("reglage", qui=utilisateur_courant() or NOM_ADMIN,
                action=("accès par le tunnel Cloudflare : "
                        + ("activé" if nouveau.get("actif") else "désactivé")
                        + (" (mon domaine)" if mode == "domaine" else " (adresse aléatoire)")
                        + (", jeton changé" if jeton else "") + (", jeton retiré" if oublier else "")),
                ip=_adresse_client())
    threading.Thread(target=tour_du_tunnel, daemon=True).start()
    return jsonify(etat_du_tunnel())


@flask_app.get("/api/tunnel/qr")
@require_admin
def api_tunnel_qr():
    adresse = origine_du_tunnel("panneau")
    svg = qr_svg(adresse + "/") if adresse else ""
    if not svg:
        return Response("", status=404)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})
