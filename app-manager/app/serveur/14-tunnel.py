# CodeLab -- LE PANNEAU, ACCES DEPUIS INTERNET PAR TAILSCALE FUNNEL
# ---------------------------------------------------------------------------
# ROLE        rendre le panneau et les applications joignables depuis
#             internet sans port ouvert sur la box ni domaine a payer :
#             tailscaled tourne dans le conteneur du panneau, et Funnel
#             publie https://<machine>.<reseau>.ts.net (le panneau, port 443)
#             et le meme nom en :8443 (les applications). Lance, surveille et
#             arrete par le panneau ; l'interrupteur et la cle sont dans
#             Parametres > Securite.
#             Ne fait PAS : le reglage du compte Tailscale (HTTPS et Funnel
#             s'y activent une fois, dans sa console), ni l'acces de
#             l'administrateur, qui reste au reseau local.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau. Ses fonctions sont
#             appelees par 02-sessions (origines), 05-comptes (cles
#             d'acces), 09-applications (reseau local, HTTPS), 13-hub-et-proxy
#             (le proxy) et 15-serveur-http (les ecouteurs).
# CONTRAINTES Une requete de Funnel se reconnait a son PORT d'arrivee
#             (TUNNEL_PORTS, sur 127.0.0.1), jamais a un en-tete : voir
#             par_le_tunnel. Une adresse n'entre dans une page, une CSP ou
#             une redirection qu'apres _HOTE_TUNNEL. La cle Tailscale ne
#             passe jamais en argument (voir « la cle »).
# STRUCTURE   Funnel ; l'API.
# CHOIX       - tailscaled dans le conteneur du panneau, et pas en service du
#               compose : un interrupteur suffit, sans commande ni profil
#               Compose (que CasaOS ne propose pas).
#             - Un seul nom, deux ports : deux origines (le port en fait
#               partie), comme en local avec 9001 et 9002 -- et le meme hote,
#               donc la session vaut des deux cotes sans rien transporter.
#             - En mode userspace (--tun=userspace-networking) : ni TUN ni
#               NET_ADMIN, le conteneur garde ses capacites reduites.
#             - ESSAYE ET ECARTE : Cloudflare. Les Quick Tunnels changent
#               d'adresse a chaque redemarrage (Face ID impossible), et une
#               adresse fixe exige un domaine paye ; deux sites distincts
#               obligeaient a transporter la session de l'un a l'autre.
# A AMELIORER - Un nom par application (Tailscale Services) quand Funnel le
#               permettra sans compte payant.
#

# ------------------------------ Funnel ------------------------------
#
# Vecu : « Y a t il un service gratuit all in one ? », puis « ajoute
# Tailscale Funnel plutot que cloudflare ». Un compte Tailscale gratuit, une
# cle d'authentification, et le panneau a une adresse FIXE en https, sans
# domaine a payer ni port a ouvrir.
#
# LE PORT D'ARRIVEE DIT QUE LA REQUETE VIENT D'INTERNET. Funnel ne parle
# qu'a deux ecouteurs a lui, sur 127.0.0.1 : TUNNEL_PORT_PANNEAU et
# TUNNEL_PORT_APPS. Ni publies par le compose, ni joignables depuis le
# reseau : seul un processus du conteneur les atteint.
# POURQUOI le port et pas les en-tetes : vue du panneau, la connexion de
#   tailscaled vient de 127.0.0.1, donc du « reseau local ». Si l'on se
#   fiait seulement a X-Forwarded-For pour reconnaitre l'intermediaire
#   (proxy_non_declare), il suffirait que cet en-tete manque un jour pour
#   ouvrir l'administration a tout internet.
# REGLE : 12 quater (l'administration reste au reseau local, Funnel compris).
# DEPEND DE / PROTEGE AUSSI : client_est_local et proxy_non_declare
#   (09-applications), _client_ip (02-sessions), les origines
#   (origine_applications, domaine_des_applications, lien_panneau), les cles
#   d'acces (_hote_et_schema, 05-comptes).
# ECARTE : reconnaitre Funnel a son nom d'hote (*.ts.net) ou a son en-tete
#   Tailscale-Funnel-Request : l'un et l'autre viennent du client, un
#   visiteur du reseau local les ecrit comme il veut.
TUNNEL_PORT_PANNEAU = 9011
TUNNEL_PORT_APPS = 9012
TUNNEL_PORTS = (TUNNEL_PORT_PANNEAU, TUNNEL_PORT_APPS)
# Les ports publics de Funnel : 443 pour le panneau (l'adresse nue), 8443
# pour les applications. Funnel n'en ouvre que quelques-uns (443, 8443,
# 10000).
FUNNEL_PORTS = {"panneau": (443, TUNNEL_PORT_PANNEAU), "apps": (8443, TUNNEL_PORT_APPS)}
TAILSCALE = os.environ.get("APP_MANAGER_TAILSCALE", "/usr/local/bin/tailscale")
TAILSCALED = os.environ.get("APP_MANAGER_TAILSCALED", "/usr/local/bin/tailscaled")
# L'etat de la machine Tailscale (son identite, donc son nom) : dans le
# volume d'etat, pour que l'adresse survive au redemarrage du conteneur.
TAILSCALE_DIR = os.path.join(STATE_DIR, "tailscale")
TUNNEL_VEILLE = 5            # secondes entre deux tours de la supervision
NOM_DE_MACHINE_DEFAUT = "codelab"

# LA CLE TAILSCALE (tskey-auth-...) est un secret : qui la tient ajoute une
# machine a ton reseau Tailscale. POURQUOI ces precautions :
#   - dans le coffre (ecrire_reglage, chiffre), jamais en clair sur le disque
#     ni dans une reponse d'API (« cle » : presente ou non) ;
#   - lue par « tailscale up » dans un fichier 0600 (--auth-key=file:...),
#     efface aussitot, et jamais en argument : la ligne de commande de tout
#     processus se lit dans /proc par n'importe quel utilisateur du conteneur,
#     donc par les applications ;
#   - tailscaled et la commande tailscale tournent sous un uid A LUI
#     (TUNNEL_UID), sans le groupe de /workspace : son etat (la cle de la
#     machine) est en 0700, et aucune application ni aucun build ne tourne
#     sous cet uid.
# REGLE : 16 (rien en clair sur le disque), 19 (rien de ce secret pour une
#   application), 12 quater.
# ECARTE : l'uid partage des applications (RUN_AS_UID) : un build sans
#   application tourne sous lui, et lirait l'etat de la machine.
TUNNEL_UID = int(os.environ.get("APP_MANAGER_TUNNEL_UID", "1002"))
_CLE_TAILSCALE = re.compile(r"^tskey-[A-Za-z0-9-]{10,200}$")
_NOM_DE_MACHINE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# Une adresse de Funnel, et rien d'autre : elle part dans des liens, une
# CSP, un en-tete Location et un mail. La reponse de tailscale est une
# donnee comme une autre (AGENTS.md 3.1).
_HOTE_TUNNEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.ts\.net$")

_tunnel = {"proc": None, "hote": "", "funnel": False, "erreur": "", "essai_cle": 0}
_tunnel_verrou = threading.Lock()


def socket_tailscale():
    """La socket de tailscaled, dans son dossier : calculee a l'appel, pour
    suivre TAILSCALE_DIR (les tests le detournent)."""
    return os.path.join(TAILSCALE_DIR, "tailscaled.sock")


def lire_tunnel():
    d = lire_parametre("tunnel", {})
    return d if isinstance(d, dict) else {}


def tunnel_voulu():
    return lire_tunnel().get("actif") is True


def nom_de_machine():
    nom = str(lire_tunnel().get("nom") or "")
    return nom if _NOM_DE_MACHINE.match(nom) else NOM_DE_MACHINE_DEFAUT


def cle_de_tailscale_valide(brut):
    """La cle telle que la console Tailscale la donne, ou "" sinon."""
    cle = str(brut or "").strip()
    return cle if _CLE_TAILSCALE.match(cle) else ""


def cle_de_tailscale():
    return str(reglage("tunnel_cle", "") or "")


def tunnel_disponible():
    return all(os.path.isfile(b) and os.access(b, os.X_OK) for b in (TAILSCALE, TAILSCALED))


def par_le_tunnel():
    """Cette requete est-elle arrivee par Funnel ?"""
    return (has_request_context()
            and str(request.environ.get("SERVER_PORT", "")) in {str(p) for p in TUNNEL_PORTS})


def sur_le_tunnel_des_apps():
    return (has_request_context()
            and str(request.environ.get("SERVER_PORT", "")) == str(TUNNEL_PORT_APPS))


def origine_du_tunnel(quoi):
    """https://<machine>.<reseau>.ts.net (le panneau) ou le meme nom en :8443
    (les applications), ou "" tant que l'adresse n'est pas connue."""
    hote = _tunnel["hote"]
    if not (hote and _HOTE_TUNNEL.match(hote)):
        return ""
    port = FUNNEL_PORTS[quoi][0]
    return "https://" + hote + ("" if port == 443 else f":{port}")


def _sans_privileges_tunnel():
    """preexec de tailscaled et de tailscale : leur propre uid et leur
    propre groupe, sans celui de /workspace (voir TUNNEL_UID). Sans effet
    hors root (tests)."""
    if os.geteuid() != 0:
        return
    os.setgroups([])
    os.setgid(TUNNEL_UID)
    os.setuid(TUNNEL_UID)
    os.umask(0o077)


def _env_tunnel():
    """Un environnement VIDE hors PATH et HOME : tailscale n'a besoin
    d'aucun secret du panneau (meme regle que les applications, section
    « secrets transmis aux applications »)."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": TAILSCALE_DIR}


def _preparer_le_dossier():
    os.makedirs(TAILSCALE_DIR, mode=0o700, exist_ok=True)
    try:
        os.chown(TAILSCALE_DIR, TUNNEL_UID, TUNNEL_UID)
        os.chmod(TAILSCALE_DIR, 0o700)
    except OSError:
        pass


def _tailscale(*args, timeout=20):
    """(code, sortie) d'une commande tailscale, sous TUNNEL_UID. Ne leve
    jamais : un echec est une sortie a montrer, pas une panne du panneau."""
    try:
        r = subprocess.run([TAILSCALE, "--socket=" + socket_tailscale(), *args],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=timeout, env=_env_tunnel(), cwd=TAILSCALE_DIR,
                           preexec_fn=_sans_privileges_tunnel)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                        # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def _sortie_lisible(texte):
    """Une sortie de tailscale pour la carte : une ligne, courte, sans
    caractere de controle (elle est affichee par textContent)."""
    texte = re.sub(r"[\x00-\x1f\x7f]+", " ", str(texte or "")).strip()
    return texte[:300]


def _pids_de_tailscaled():
    """Les tailscaled de CodeLab, reconnus a leur socket -- y compris celui
    d'un panneau precedent (les processus survivent a son redemarrage,
    comme les applications)."""
    marque = ("--socket=" + socket_tailscale()).encode()
    pids = []
    for entree in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not entree.isdigit():
            continue
        try:
            with open(f"/proc/{entree}/cmdline", "rb") as f:
                morceaux = f.read().split(b"\0")
        except OSError:
            continue
        if morceaux and morceaux[0].endswith(b"tailscaled") and marque in morceaux:
            pids.append(int(entree))
    return pids


def _commande_de_tailscaled():
    return [TAILSCALED, "--tun=userspace-networking", "--statedir=" + TAILSCALE_DIR,
            "--socket=" + socket_tailscale()]


def _lancer_tailscaled():
    _preparer_le_dossier()
    journal = open(os.path.join(LOG_DIR, "tailscale.log"), "wb")
    try:
        proc = subprocess.Popen(
            _commande_de_tailscaled(), stdin=subprocess.DEVNULL, stdout=journal,
            stderr=subprocess.STDOUT, env=_env_tunnel(), cwd=TAILSCALE_DIR,
            preexec_fn=_sans_privileges_tunnel, start_new_session=True)
    finally:
        journal.close()
    _tunnel.update(proc=proc, hote="", funnel=False)
    print(f"[app-manager] tailscale : tailscaled lance (pid {proc.pid}).", flush=True)


def _arreter_tailscaled():
    proc = _tunnel["proc"]
    pids = set(_pids_de_tailscaled())
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
    _tunnel.update(proc=None, hote="", funnel=False)


def _tailscaled_en_vie():
    proc = _tunnel["proc"]
    if proc is not None and proc.poll() is None:
        return True
    return bool(_pids_de_tailscaled())


def _etat_tailscale():
    """Le statut de la machine (tailscale status --json), ou {}."""
    code, sortie = _tailscale("status", "--json", "--peers=false", timeout=10)
    if code != 0 and not sortie.lstrip().startswith("{"):
        return {}
    try:
        d = json.loads(sortie[sortie.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _se_connecter(cle):
    """tailscale up, la cle lue dans un fichier 0600 efface aussitot."""
    chemin = os.path.join(TAILSCALE_DIR, "cle")
    try:
        fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(cle)
        try:
            os.chown(chemin, TUNNEL_UID, TUNNEL_UID)
        except OSError:
            pass
        return _tailscale("up", "--auth-key=file:" + chemin, "--hostname=" + nom_de_machine(),
                          "--accept-dns=false", "--timeout=30s", timeout=45)
    finally:
        try:
            os.unlink(chemin)
        except OSError:
            pass


def _ouvrir_funnel():
    """Les deux ports publics, chacun vers son ecouteur sur 127.0.0.1.
    Rend "" ou le message d'erreur de tailscale (HTTPS ou Funnel non
    actives dans la console : il dit lequel)."""
    for public, local in FUNNEL_PORTS.values():
        code, sortie = _tailscale("funnel", "--bg", f"--https={public}",
                                  f"http://127.0.0.1:{local}", timeout=30)
        if code != 0:
            return _sortie_lisible(sortie) or "Funnel n'a pas pu s'ouvrir."
    return ""


def _annoncer_l_adresse(adresse):
    """L'adresse du panneau est connue : l'administrateur la recoit par mail,
    une fois par adresse (« annoncee »). Elle ne change qu'avec le nom de la
    machine ; envoyee meme alertes eteintes : ce n'est pas une alerte, c'est
    la porte d'entree."""
    if lire_tunnel().get("annoncee") == adresse:
        return
    ecrire_parametre("tunnel", {**lire_tunnel(), "annoncee": adresse})
    cibles = alertes_admin()
    if not cibles:
        return
    corps = ("L'adresse de ton panneau CodeLab, depuis n'importe où :\n\n"
             f"{adresse}\n\n"
             "Paramètres > Sécurité > Accès depuis internet la montre aussi, avec un QR code.")
    try:
        envoyer_avec_repli("CodeLab : ton adresse depuis internet", corps, cibles)
    except Exception:                                             # noqa: BLE001
        pass


def _tour_connecte(etat):
    """La machine est connectee : son nom, son adresse, Funnel."""
    moi = etat.get("Self") if isinstance(etat.get("Self"), dict) else {}
    if str(moi.get("HostName") or "").lower() != nom_de_machine():
        code, sortie = _tailscale("set", "--hostname=" + nom_de_machine())
        if code != 0:
            _tunnel["erreur"] = _sortie_lisible(sortie)
        _tunnel["funnel"] = False
        return
    hote = str(moi.get("DNSName") or "").strip().rstrip(".").lower()
    if not _HOTE_TUNNEL.match(hote):
        return
    if hote != _tunnel["hote"]:
        _tunnel["hote"] = hote
        print(f"[app-manager] tailscale : https://{hote}", flush=True)
    if not _tunnel["funnel"]:
        _tunnel["erreur"] = _ouvrir_funnel()
        _tunnel["funnel"] = not _tunnel["erreur"]
    if _tunnel["funnel"]:
        _annoncer_l_adresse(origine_du_tunnel("panneau"))


def tour_du_tunnel():
    """Un tour de supervision : allume ce qui doit l'etre, eteint le reste,
    connecte la machine, ouvre Funnel. Ne leve jamais."""
    with _tunnel_verrou:
        try:
            if not (tunnel_voulu() and tunnel_disponible() and origines_separees()):
                if _tunnel["proc"] is not None or _pids_de_tailscaled():
                    _arreter_tailscaled()
                return
            if not _tailscaled_en_vie():
                _lancer_tailscaled()
                return
            etat = _etat_tailscale()
            if etat.get("BackendState") == "Running":
                _tour_connecte(etat)
                return
            _tunnel.update(hote="", funnel=False)
            if etat.get("BackendState") == "NeedsLogin":
                cle = cle_de_tailscale()
                # Une cle refusee n'est pas reessayee a chaque tour : une
                # fois par minute, ou des qu'on en enregistre une autre.
                if cle and time.time() - _tunnel["essai_cle"] > 60:
                    _tunnel["essai_cle"] = time.time()
                    code, sortie = _se_connecter(cle)
                    _tunnel["erreur"] = "" if code == 0 else (
                        _sortie_lisible(sortie) or "La clé a été refusée.")
        except Exception as e:                                    # noqa: BLE001
            print(f"[app-manager] tailscale : {type(e).__name__}: {e}", flush=True)


def demarrer_le_tunnel():
    def _boucle():
        while True:
            tour_du_tunnel()
            time.sleep(TUNNEL_VEILLE)
    threading.Thread(target=_boucle, daemon=True, name="tailscale").start()


def etat_du_tunnel():
    """Ce que montre la carte. La cle n'en sort jamais : seulement si elle
    est enregistree."""
    actif = tunnel_voulu()
    if not tunnel_disponible():
        etat = "indisponible"
    elif not origines_separees():
        etat = "sans-origines"
    elif not actif:
        etat = "arrete"
    elif _tunnel["funnel"] and _tunnel["hote"]:
        etat = "actif"
    elif _tunnel["erreur"]:
        etat = "erreur"
    elif not cle_de_tailscale() and not _tunnel["hote"]:
        etat = "sans-cle"
    else:
        etat = "demarrage"
    pret = etat == "actif"
    return {"actif": actif, "etat": etat, "nom": nom_de_machine(),
            "cle": bool(cle_de_tailscale()),
            "erreur": _tunnel["erreur"] if etat == "erreur" else "",
            "panneau": origine_du_tunnel("panneau") if pret else "",
            "apps": origine_du_tunnel("apps") if pret else ""}


# ------------------------------ l'API ------------------------------

@flask_app.get("/api/tunnel")
@require_admin
def api_tunnel_lire():
    return jsonify(etat_du_tunnel())


@flask_app.put("/api/tunnel")
@require_admin
def api_tunnel_ecrire():
    """{actif?, cle?, nom?, deconnecter?} : chaque champ absent garde sa
    valeur. Tout est verifie avant d'ecrire quoi que ce soit."""
    corps = request.get_json(silent=True) or {}
    nouveau = dict(lire_tunnel())
    if "actif" in corps:
        if not isinstance(corps["actif"], bool):
            return jsonify({"error": "Réglage invalide."}), 400
        nouveau["actif"] = corps["actif"]
    if "nom" in corps:
        nom = str(corps["nom"] or "").strip().lower()
        if not _NOM_DE_MACHINE.match(nom):
            return jsonify({"error": "Nom invalide : des lettres minuscules, des chiffres et des "
                                     "tirets (ex. codelab)."}), 400
        nouveau["nom"] = nom
    cle = None
    if corps.get("cle"):
        cle = cle_de_tailscale_valide(corps["cle"])
        if not cle:
            return jsonify({"error": "Ce n'est pas une clé Tailscale : elle commence par "
                                     "tskey-auth- (console Tailscale, Settings > Keys)."}), 400
    deconnecter = corps.get("deconnecter") is True
    if nouveau.get("actif") and not tunnel_disponible():
        return jsonify({"error": "Tailscale est absent de cette image : mets CodeLab à jour."}), 409
    try:
        if cle or deconnecter:
            ecrire_reglage("tunnel_cle", None if deconnecter else cle)
        if deconnecter:
            nouveau["actif"] = False
        ecrire_parametre("tunnel", nouveau)
    except Exception as e:                                        # noqa: BLE001
        return jsonify({"error": f"Réglage non enregistré : {e}"}), 500
    if cle:
        _tunnel.update(essai_cle=0, erreur="")
    if deconnecter:
        # La machine quitte le reseau Tailscale (son etat est oublie) :
        # rallumer demandera une cle.
        with _tunnel_verrou:
            if _tailscaled_en_vie():
                _tailscale("logout", timeout=20)
            _arreter_tailscaled()
            shutil.rmtree(TAILSCALE_DIR, ignore_errors=True)
            _tunnel.update(erreur="")
    journaliser("reglage", qui=utilisateur_courant() or NOM_ADMIN,
                action=("accès depuis internet (Tailscale Funnel) : "
                        + ("activé" if nouveau.get("actif") else "désactivé")
                        + (", clé changée" if cle else "")
                        + (", machine déconnectée" if deconnecter else "")),
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
