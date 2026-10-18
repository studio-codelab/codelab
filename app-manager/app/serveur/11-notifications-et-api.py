# CodeLab -- LE PANNEAU, NOTIFICATIONS ET API
# ---------------------------------------------------------------------------
# ROLE        les notifications Web Push, l'API des applications, le renommage.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   notifications Web Push, API, renommer une application.
#

# ------------------------ les notifications (Web Push) ------------------------
#
# Une notification sur le telephone, la tablette ou l'ordinateur, meme
# CodeLab ferme : un nouveau message (administrateur), une application qui
# tombe et qui revient (administrateur), l'avancement d'un message qu'on a
# envoye (compte utilisateur). Le mail continue de partir comme avant : la
# notification s'y ajoute, elle ne le remplace pas.
#
# LE STANDARD, ET RIEN D'AUTRE. Web Push (RFC 8030), chiffre de bout en bout
# (RFC 8291) et signe par le serveur (VAPID, RFC 8292) : le service d'Apple,
# de Google ou de Mozilla transporte un message qu'il ne peut pas lire.
# Aucun compte chez eux, aucune bibliotheque de plus : cryptography, deja la
# pour le coffre, suffit.
#
# Ou ca marche : Chrome, Edge et Firefox (ordinateur et Android), Safari sur
# Mac, et sur iPhone et iPad le panneau INSTALLE sur l'ecran d'accueil
# (iOS 16.4 et plus). Toujours en HTTPS : le navigateur refuse en clair.
#
# QUI RECOIT QUOI. Un abonnement appartient a un compte ET a une session
# (l'appareil connecte) : deconnecter l'appareil, depuis Securite, coupe ses
# notifications, et un compte ne recoit jamais celles d'un autre.
#
# PAS D'ADRESSE ARBITRAIRE. Le navigateur fournit l'adresse ou envoyer
# (endpoint) ; le serveur la contacte. N'importe quelle URL ferait du
# panneau un relais vers le reseau local (SSRF) : seuls les services de
# notification connus, en HTTPS, sont acceptes.
SERVICES_PUSH = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com",
                 "notify.windows.com", "updates.push.services.mozilla.com")
PUSH_ABONNEMENTS_MAX = 20          # par compte : au-dela, le plus ancien s'en va
PUSH_SUJET_VAPID = "https://github.com/studio-codelab/codelab"


def _de_b64url(texte):
    texte = str(texte or "")
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


def service_push_permis(endpoint):
    """Une adresse d'envoi d'un service de notification connu, en HTTPS."""
    try:
        u = urllib.parse.urlsplit(str(endpoint or ""))
    except ValueError:
        return False
    hote = (u.hostname or "").lower()
    return (u.scheme == "https" and len(str(endpoint)) <= 1024 and u.port in (None, 443)
            and any(hote == s or hote.endswith("." + s) for s in SERVICES_PUSH))


def cle_vapid():
    """La cle de signature du serveur, creee au premier besoin, gardee au coffre.

    None quand le coffre n'a pas de cle de chiffrement : sans elle, la cle
    privee ne pourrait pas etre rangee ailleurs qu'en clair, et elle ne l'est
    jamais.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = reglage("vapid_prive")
    if not pem:
        if not _cle_chiffrement:
            return None
        cle = ec.generate_private_key(ec.SECP256R1())
        pem = cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        try:
            ecrire_reglage("vapid_prive", pem)
        except OSError as e:
            print(f"[app-manager] cle de notification non enregistree : {e}", flush=True)
            return None
    return serialization.load_pem_private_key(pem.encode(), password=None)


def cle_publique_vapid():
    from cryptography.hazmat.primitives import serialization
    cle = cle_vapid()
    if cle is None:
        return ""
    return _b64url(cle.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint))


def _jeton_vapid(endpoint, cle):
    """Le JWT ES256 qui prouve au service que l'envoi vient de ce serveur."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    u = urllib.parse.urlsplit(endpoint)
    tete = _b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    corps = _b64url(json.dumps({"aud": f"{u.scheme}://{u.netloc}",
                                "exp": int(time.time()) + 12 * 3600,
                                "sub": PUSH_SUJET_VAPID}).encode())
    signe = (tete + "." + corps).encode()
    r, s = utils.decode_dss_signature(cle.sign(signe, ec.ECDSA(hashes.SHA256())))
    return tete + "." + corps + "." + _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def chiffrer_push(contenu, p256dh, auth, _ephemere=None, _sel=None):
    """Le corps chiffre d'une notification (RFC 8291, « aes128gcm »).

    _ephemere et _sel ne servent qu'aux tests (le vecteur de la RFC)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    cle_client = _de_b64url(p256dh)
    secret_auth = _de_b64url(auth)
    public_client = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), cle_client)
    ephemere = _ephemere or ec.generate_private_key(ec.SECP256R1())
    public_serveur = ephemere.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    partage = ephemere.exchange(ec.ECDH(), public_client)
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=secret_auth,
               info=b"WebPush: info\x00" + cle_client + public_serveur).derive(partage)
    sel = _sel or secrets.token_bytes(16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=sel,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=sel,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)
    chiffre = AESGCM(cek).encrypt(nonce, contenu + b"\x02", None)
    return sel + (4096).to_bytes(4, "big") + bytes([len(public_serveur)]) + public_serveur + chiffre


def abonnements_push(compte):
    return stockage.lire(DB_FILE, "SELECT * FROM abonnements_push WHERE compte = ? "
                                  "ORDER BY cree DESC", (compte,))


def retirer_abonnement_push(ident):
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM abonnements_push WHERE id = ?", (ident,))


def _envoyer_un_push(ab, contenu, cle, urgence):
    """Un envoi. Rend False quand l'abonnement est mort (a retirer)."""
    try:
        corps = chiffrer_push(contenu, ab["p256dh"], ab["auth"])
    except Exception:                                             # noqa: BLE001
        return False                 # cles de l'abonnement illisibles : on l'oublie
    req = urllib.request.Request(ab["endpoint"], data=corps, method="POST", headers={
        "TTL": "86400", "Urgency": urgence, "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": "vapid t=" + _jeton_vapid(ab["endpoint"], cle)
                         + ", k=" + cle_publique_vapid()})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True
    except urllib.error.HTTPError as e:
        # 404 et 410 : l'abonnement n'existe plus (notifications coupees,
        # application desinstallee). Le reste est passager.
        if e.code in (404, 410):
            return False
        print(f"[app-manager] notification refusee ({e.code}) par "
              f"{urllib.parse.urlsplit(ab['endpoint']).hostname}", flush=True)
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"[app-manager] notification non envoyee : {e}", flush=True)
        return True


def notifier_compte(compte, titre, texte, url="/", etiquette=None, urgence="normal"):
    """Une notification sur chaque appareil abonne du compte. Ne leve jamais.

    Envoyee dans un thread : le service de notification peut mettre des
    secondes a repondre, et ni une requete ni le moniteur ne l'attendent.
    """
    try:
        abonnes = abonnements_push(compte)
        if not abonnes:
            return 0
        cle = cle_vapid()
        if cle is None:
            return 0
        sessions = {l["sid"] for l in appareils_du_compte(compte)}
    except Exception as e:                                        # noqa: BLE001
        print(f"[app-manager] notifications indisponibles : {e}", flush=True)
        return 0
    contenu = json.dumps({"titre": titre[:120], "texte": texte[:240], "url": url,
                          "etiquette": etiquette or ""}, ensure_ascii=False).encode()

    def _envoyer():
        for ab in abonnes:
            # Appareil deconnecte depuis : son abonnement part avec lui.
            if ab["sid"] not in sessions or not _envoyer_un_push(ab, contenu, cle, urgence):
                try:
                    retirer_abonnement_push(ab["id"])
                except Exception:                                 # noqa: BLE001
                    pass
    threading.Thread(target=_envoyer, daemon=True).start()
    return len(abonnes)


@flask_app.get("/api/notifications")
@require_auth
def api_notifications():
    """La cle publique du serveur, et si CET appareil est deja abonne."""
    qui = utilisateur_courant() or NOM_ADMIN
    sid = session.get("sid")
    try:
        cle = cle_publique_vapid()
    except Exception:                                             # noqa: BLE001
        cle = ""
    abonne = any(a["sid"] == sid for a in abonnements_push(qui))
    return jsonify({"disponible": bool(cle), "cle": cle, "abonne": abonne})


@flask_app.post("/api/notifications/abonnement")
@require_auth
def api_notifications_abonner():
    """{endpoint, keys: {p256dh, auth}}, tel que le rend pushManager.subscribe()."""
    qui = utilisateur_courant() or NOM_ADMIN
    d = request.get_json(force=True, silent=True) or {}
    endpoint = d.get("endpoint")
    cles = d.get("keys") if isinstance(d.get("keys"), dict) else {}
    p256dh, auth = str(cles.get("p256dh") or ""), str(cles.get("auth") or "")
    if not service_push_permis(endpoint):
        return jsonify({"error": "Ce service de notification n'est pas reconnu."}), 400
    try:
        if len(_de_b64url(p256dh)) != 65 or len(_de_b64url(auth)) != 16:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Abonnement illisible."}), 400
    if cle_vapid() is None:
        return jsonify({"error": "Notifications indisponibles sur ce serveur."}), 503
    ident = hashlib.sha256(endpoint.encode()).hexdigest()
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM abonnements_push WHERE id = ?", (ident,))
        cx.execute("INSERT INTO abonnements_push (id, compte, sid, endpoint, p256dh, auth, cree, agent) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (ident, qui, session.get("sid") or "", endpoint, p256dh, auth, int(time.time()),
                    (request.headers.get("User-Agent") or "")[:300]))
        anciens = [l["id"] for l in cx.execute(
            "SELECT id FROM abonnements_push WHERE compte = ? ORDER BY cree DESC LIMIT -1 OFFSET ?",
            (qui, PUSH_ABONNEMENTS_MAX)).fetchall()]
        for x in anciens:
            cx.execute("DELETE FROM abonnements_push WHERE id = ?", (x,))
    return jsonify({"ok": True})


@flask_app.delete("/api/notifications/abonnement")
@require_auth
def api_notifications_desabonner():
    """Coupe les notifications de CET appareil (session courante)."""
    qui = utilisateur_courant() or NOM_ADMIN
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM abonnements_push WHERE compte = ? AND sid = ?",
                   (qui, session.get("sid") or ""))
    return jsonify({"ok": True})


@flask_app.post("/api/notifications/essai")
@require_auth
def api_notifications_essai():
    qui = utilisateur_courant() or NOM_ADMIN
    if not notifier_compte(qui, "CodeLab", "Les notifications arrivent bien sur cet appareil.",
                           url="/parametres", etiquette="essai"):
        return jsonify({"error": "Aucun appareil abonné pour ce compte."}), 404
    return jsonify({"ok": True})


# L'avancement d'un message, pousse a son auteur : la meme etape que la
# pastille du hub (suivi_message), annoncee quand elle change. Le moniteur
# compare chaque minute, et seulement pour les comptes abonnes. Ce qui a
# deja ete annonce est retenu (preference « suivi_notifie ») : un
# redemarrage du panneau ne rejoue rien.
ETAPES_ANNONCEES = {"etude": "est à l'étude", "planifie": "est planifié",
                    "en_cours": "est en cours", "fait": "est fait",
                    "refuse": "n'est pas retenu"}
_suivi_push = {"dernier": 0}


def suivi_push_tick(maintenant=None):
    maintenant = time.time() if maintenant is None else maintenant
    if maintenant - _suivi_push["dernier"] < 60:
        return
    _suivi_push["dernier"] = maintenant
    comptes = {l["compte"] for l in stockage.lire(DB_FILE, "SELECT DISTINCT compte FROM abonnements_push")}
    comptes.discard(NOM_ADMIN)
    if not comptes:
        return
    taches, projets = lire_taches(), lire_projets()
    par_compte = {}
    for m in lire_messages(limite=MESSAGES_AFFICHES):
        if m.get("qui") in comptes:
            par_compte.setdefault(m["qui"], []).append(m)
    for qui in comptes:
        connues = lire_preference("suivi_notifie", qui)
        premiere = not isinstance(connues, dict)
        connues = connues if isinstance(connues, dict) else {}
        nouvelles = dict(connues)
        for m in par_compte.get(qui, []):
            etape = suivi_message(m, taches, projets)["etape"]
            avant = connues.get(m["id"])
            nouvelles[m["id"]] = etape
            if premiere or avant == etape or etape not in ETAPES_ANNONCEES:
                continue
            extrait = (m.get("texte") or "").strip().replace("\n", " ")
            notifier_compte(qui, "Ton message " + ETAPES_ANNONCEES[etape],
                            extrait[:120] or "Ouvre CodeLab pour voir son avancement.",
                            url="/messages", etiquette="suivi-" + m["id"])
        if nouvelles != connues or premiere:
            ecrire_preference("suivi_notifie", nouvelles, qui)


@flask_app.get("/api/mon-compte/appareils")
@require_auth
def api_mes_appareils():
    qui = utilisateur_courant() or NOM_ADMIN
    return jsonify({"appareils": liste_des_appareils(qui, session.get("sid"))})


@flask_app.delete("/api/mon-compte/appareils/<ident>")
@require_auth
def api_deconnecter_un_appareil(ident):
    qui = utilisateur_courant() or NOM_ADMIN
    cible = [l["sid"] for l in appareils_du_compte(qui) if id_public_appareil(l["sid"]) == ident]
    if not cible:
        return jsonify({"error": "Cet appareil n'est plus connecté."}), 404
    revoquer_appareils(cible)
    journaliser("session", qui=qui, action="appareil déconnecté", ip=_adresse_client())
    if cible[0] == session.get("sid"):
        session.clear()
    return jsonify({"ok": True})


@flask_app.post("/api/mon-compte/appareils/deconnecter-autres")
@require_auth
def api_deconnecter_les_autres():
    qui = utilisateur_courant() or NOM_ADMIN
    courant = session.get("sid")
    n = revoquer_appareils([l["sid"] for l in appareils_du_compte(qui) if l["sid"] != courant])
    journaliser("session", qui=qui, action=f"autres appareils déconnectés ({n})",
                ip=_adresse_client())
    return jsonify({"ok": True, "deconnectes": n})


@flask_app.get("/api/utilisateurs/<nom>/appareils")
@require_admin
def api_appareils_d_un_compte(nom):
    if nom not in lire_utilisateurs():
        return jsonify({"error": "Compte inconnu."}), 404
    return jsonify({"appareils": liste_des_appareils(nom)})


@flask_app.delete("/api/utilisateurs/<nom>/appareils")
@require_admin
def api_deconnecter_un_compte(nom):
    """L'administrateur deconnecte tous les appareils d'un compte, sans
    toucher a son mot de passe ni a ses cles."""
    if nom not in lire_utilisateurs():
        return jsonify({"error": "Compte inconnu."}), 404
    n = revoquer_appareils([l["sid"] for l in appareils_du_compte(nom)])
    journaliser("session", qui=utilisateur_courant() or NOM_ADMIN, cible=nom,
                action=f"appareils déconnectés par l'administrateur ({n})", ip=_adresse_client())
    return jsonify({"ok": True, "deconnectes": n})


@flask_app.post("/logout")
def logout():
    try:
        revoquer_appareils([session.get("sid")])
    except Exception:                                             # noqa: BLE001
        pass
    session.clear()
    return jsonify({"ok": True})


@flask_app.get("/api/auth-check")
def api_auth_check():
    """Repond 200 si la session est valide, 401 sinon. Rien d'autre.

    C'est le point d'appui du proxy de Dagster : nginx interroge cette route
    avant chaque requete (directive auth_request) et laisse passer ou renvoie
    vers la page de connexion du panneau. Dagster herite ainsi de la session
    du panneau -- meme mot de passe, meme second facteur, meme deconnexion --
    au lieu d'avoir sa propre authentification HTTP Basic, qui n'a ni session,
    ni expiration, ni deconnexion possible.
    """
    # Sans redirection, contrairement a require_auth : nginx a besoin d'un
    # code, pas d'une page. C'est lui qui decide ou envoyer le visiteur.
    #
    # Reserve a l'administrateur : l'interface de Dagster permet de lancer des
    # jobs, donc d'executer du code sur cette machine. Y donner acces a un
    # compte utilisateur reviendrait a lui donner l'administration par la
    # bande, quels que soient les projets qu'on lui a autorises.
    if not est_admin():
        return Response("", 401)
    return Response("", 204)


_erreurs_navigateur = {}


@flask_app.post("/api/erreur-navigateur")
@require_auth
def api_erreur_navigateur():
    """Une erreur JavaScript de la page du panneau, ecrite dans ses journaux.

    Tout compte connecte peut en envoyer -- c'est sa page qui plante --, mais
    trente par cinq minutes et par adresse au plus, et chaque champ tronque :
    cette route ecrit dans un journal, elle ne doit pas servir a le remplir.
    """
    ip = _client_ip()
    maintenant = time.time()
    recentes = [x for x in _erreurs_navigateur.get(ip, []) if maintenant - x < 300]
    if len(recentes) >= 30:
        _erreurs_navigateur[ip] = recentes
        return jsonify({"ok": False}), 429
    recentes.append(maintenant)
    _erreurs_navigateur[ip] = recentes
    d = request.get_json(silent=True) or {}

    def champ(nom, taille):
        # Une seule ligne par champ : un saut de ligne glisse dans le message
        # fabriquerait de fausses lignes dans le journal.
        return " ".join(str(d.get(nom) or "").split())[:taille]

    print(f"[navigateur] {utilisateur_courant() or NOM_ADMIN} {champ('page', 100)} : "
          f"{champ('message', 500)} ({champ('source', 200)}:{champ('ligne', 10)}:"
          f"{champ('colonne', 10)}) {champ('pile', 1500)}", flush=True)
    return jsonify({"ok": True})


@flask_app.get("/health")
def health():
    return Response("ok\n", mimetype="text/plain")


# ------------------------------ API --------------------------------

@flask_app.get("/api/apps")
@require_admin
def api_apps():
    # Les archivees ont leur propre liste (/api/apps/archivees).
    apps = apps_actives()
    out = []
    releve = _ports_ecoutes()           # un seul pour toute la liste
    par_app = reglage("alertes_par_app", {}) or {}
    for name in sorted(apps):
        a = apps[name]
        run = is_running(name)
        stats = proc_stats(procs[name].pid) if run and name in procs else {"cpu_percent": 0.0, "memory_mb": 0.0}
        if run:
            record_metrics(name, stats["cpu_percent"], stats["memory_mb"])
        out.append({
            "name": name, "titre": titre_de(name, a),
            "path": a["path"], "command": a["command"],
            "port": a["port"], "port_listeners": _ecouteurs_port(a["port"], releve),
            "running": run,
            "failed": bool(a.get("enabled")) and not run,
            # Plantee au lancement (voir start) : la fiche en montre la cause.
            "dernier_echec": None if run else a.get("dernier_echec"),
            "crash_looping": is_crash_looping(name),
            # None tant que la sonde n'est pas passee (app tout juste
            # demarree) : l'interface ne signale "ne repond pas" que sur un
            # False franc, jamais sur une absence de mesure.
            "listening": _listening.get(name) if run else None,
            # Ecoute sur 0.0.0.0 : joignable sans le panneau (voir
            # ports_ouverts_au_reseau). Reserve a l'administrateur, comme
            # toute cette route.
            "ecoute_large": bool(_ecoute_large.get(name)) if run else False,
            "visibility": visibilite(a),
            "has_build": bool((a.get("build_command") or "").strip()),
            "build_command": a.get("build_command") or "",
            "max_memory_mb": a.get("max_memory_mb"),
            "description": a.get("description") or "",
            "categorie": categorie_de(name, a),
            # Les destinataires d'alerte se reglent dans la fiche de
            # l'application : la liste doit donc arriver avec elle. Aucun
            # secret ici -- une adresse de destination n'en est pas un, et
            # cette route est deja reservee a l'administrateur.
            "alertes": _adresses(par_app.get(name)),
            # Ce qui n'est pas configurable, la page doit le savoir : c'est
            # elle qui retire les onglets et les boutons correspondants.
            "par_defaut": est_app_par_defaut(name),
            "presentation": presentation_de(a),
            "presentation_masquee": bool(a.get("presentation_masquee")),
            "presentation_fichier": a.get("presentation_fichier", ""),
            **stats,
        })
    # Dagster figure ici comme les autres. Il n'est pas declare dans
    # apps.json -- le panneau ne le lance ni ne l'arrete -- mais c'est une
    # application du point de vue de qui s'en sert : elle doit se trouver la
    # ou on cherche les applications, et pas seulement sur le hub.
    # Messages n'y figure pas : ce n'est pas un service qui tourne ou tombe,
    # c'est une page du panneau, sans journal ni metriques a surveiller.
    out.extend(services_du_hub(avec_messagerie=False))
    out.sort(key=lambda a: a["name"])
    return jsonify({"apps": out})


@flask_app.get("/api/browse")
@require_admin
def api_browse():
    path = os.path.abspath(request.args.get("path", ROOT))
    if not under_root(path):
        path = ROOT
    try:
        entries = os.listdir(path)
    except (PermissionError, FileNotFoundError):
        entries = []
    return jsonify({
        "path": path,
        "label": path.replace(ROOT, "") or "/",
        "parent": os.path.dirname(path) if path != ROOT else None,
        "dirs": sorted(d for d in entries
                       if os.path.isdir(os.path.join(path, d)) and not d.startswith(".")),
        "hasIndex": os.path.exists(os.path.join(path, "index.html")),
    })


@flask_app.get("/api/detect")
@require_admin
def api_detect():
    path = os.path.abspath(request.args.get("path", ""))
    if not under_root(path) or not os.path.isdir(path):
        return jsonify({"command": "", "build_command": ""})
    command, build_command = detect_project(path)
    return jsonify({"command": command, "build_command": build_command})


@flask_app.post("/api/add")
@require_admin
def api_add():
    d = request.get_json(force=True)
    titre = titre_propre(d.get("name"))
    name = valid_name(d.get("name"))
    path = (d.get("path") or "").strip()
    command = (d.get("command") or "").strip()
    build_command = (d.get("build_command") or "").strip()
    max_memory_mb = d.get("max_memory_mb") or None
    vis = d.get("visibility") if d.get("visibility") in VISIBILITES else VISIBILITE_PUBLIQUE
    # Sans adresse publique declaree, une application neuve nait privee --
    # y compris si le formulaire demande autre chose. C'est le defaut sur
    # lequel on ne peut pas se tromper : elle s'ouvre en une bascule, alors
    # qu'une application ouverte par megarde ne se referme qu'apres coup.
    if not adresse_publique():
        vis = VISIBILITE_PRIVEE
    if est_categorie_codelab(d.get("categorie")):
        return jsonify({"error": f"« {CATEGORIE_CODELAB} » est réservée aux applications de CodeLab."}), 400
    apps = load()

    if not titre:
        return jsonify({"error": "Le nom est obligatoire."}), 400
    # Le refus porte sur le CHEMIN, et il le dit : un nom refuse sans qu'on
    # montre ce qu'il donnait dans l'adresse ne s'apprend pas.
    if not name:
        return jsonify({"error": f"« {titre} » ne donne aucune adresse utilisable. "
                                 f"Il faut au moins une lettre sans accent "
                                 f"ou un chiffre."}), 400
    if name in adresses_reservees():
        return jsonify({"error": f"L'adresse « /{name}/ » est réservée."}), 400
    if name in apps:
        if est_archivee(apps[name]):
            return jsonify({"error": f"L'adresse « /{name}/ » est gardée par une application "
                                     f"aux archives : désarchive-la, ou supprime-la."}), 400
        return jsonify({"error": f"L'adresse « /{name}/ » est déjà prise par "
                                 f"une autre application."}), 400
    if not os.path.isdir(path):
        return jsonify({"error": "Dossier introuvable : " + path}), 400
    if not under_root(path):
        return jsonify({"error": "Le dossier doit se trouver dans " + ROOT + "."}), 400
    if not command:
        return jsonify({"error": "La commande de lancement est obligatoire."}), 400
    port = next_port(apps)
    if not port:
        return jsonify({"error": "Plus de port interne disponible."}), 400

    apps[name] = {
        "titre": titre,
        "path": path, "command": command, "port": port, "enabled": False,
        "build_command": build_command, "max_memory_mb": max_memory_mb,
        "visibility": vis,
        "description": description_propre(d.get("description")),
        "categorie": categorie_valide(d.get("categorie")),
    }
    save(apps)
    return jsonify({"ok": True, "name": name, "titre": titre, "port": port})


# CE QUI NE SE VOIT QU'AU PROCHAIN DEMARRAGE.
#
# Ces trois-la sont lus au lancement du processus : le dossier de travail, la
# ligne de commande, la limite de memoire posee dans le preexec_fn. Les
# changer pendant qu'une application tourne ne touche pas le processus en
# vie -- il faudra le relancer.
#
# Tout le reste s'applique sur-le-champ, parce que le panneau le relit a
# chaque requete : la description et la categorie (affichees dans le hub), la
# visibilite (le proxy la consulte pour chaque visiteur), la commande de
# build (executee a la demande, jamais au lancement).
#
# La liste vit ICI plutot que dans la page : c'est le serveur qui sait ce
# qu'il relit et quand. Une copie dans le navigateur aurait diverge a la
# premiere evolution.
CHAMPS_AU_DEMARRAGE = ("path", "command", "max_memory_mb")


@flask_app.put("/api/app/<n>")
@require_admin
def api_edit(n):
    """Enregistre la configuration, application en marche ou non.

    CE QUI A CHANGE, ET POURQUOI. Cette route refusait tout net pendant
    qu'une application tournait : « Arrete l'application avant de la
    modifier. » Corriger une faute dans une description demandait donc de
    couper le service. Le refus protegeait d'une illusion reelle -- croire
    qu'une commande modifiee s'appliquait au processus deja lance -- mais il
    la traitait en interdisant tout, alors qu'il suffit de DIRE lequel des
    champs attend un redemarrage.
    """
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True)
    if est_categorie_codelab(d.get("categorie")):
        return jsonify({"error": f"« {CATEGORIE_CODELAB} » est réservée aux applications de CodeLab."}), 400
    path = (d.get("path") or "").strip()
    command = (d.get("command") or "").strip()
    if not os.path.isdir(path):
        return jsonify({"error": "Dossier introuvable : " + path}), 400
    if not under_root(path):
        return jsonify({"error": "Le dossier doit se trouver dans " + ROOT + "."}), 400
    if not command:
        return jsonify({"error": "La commande de lancement est obligatoire."}), 400
    avant = {c: apps[n].get(c) for c in CHAMPS_AU_DEMARRAGE}
    apps[n]["path"] = path
    apps[n]["command"] = command
    apps[n]["build_command"] = (d.get("build_command") or "").strip()
    apps[n]["max_memory_mb"] = d.get("max_memory_mb") or None
    apps[n]["description"] = description_propre(d.get("description"))
    apps[n]["categorie"] = categorie_valide(d.get("categorie"))
    if d.get("visibility") in VISIBILITES:
        apps[n]["visibility"] = d["visibility"]
    save(apps)

    # Ce qui a reellement change parmi les champs lus au demarrage. On
    # compare APRES nettoyage (chemin normalise, memoire en entier ou None) :
    # comparer les valeurs brutes du formulaire annoncerait un redemarrage
    # necessaire pour un espace en fin de ligne.
    attendent = [c for c in CHAMPS_AU_DEMARRAGE if avant.get(c) != apps[n].get(c)]
    en_marche = is_running(n)
    return jsonify({
        "ok": True,
        "running": en_marche,
        # Un redemarrage n'est "requis" que si quelque chose tourne : sur une
        # application arretee, le prochain demarrage prendra la nouvelle
        # configuration tout seul.
        "redemarrage_requis": bool(en_marche and attendent),
        "champs_en_attente": attendent,
    })


# UNE ACTION A LA FOIS PAR APPLICATION.
#
# Un double clic sur « Demarrer » envoyait deux requetes : la premiere
# demarrait, la seconde -- arrivee quand l'application tournait deja --
# l'arretait. Et deux deploiements lances ensemble construisaient dans le
# meme dossier en meme temps. Le bouton desactive dans la page ne suffit
# pas : deux onglets, ou deux administrateurs, ou un appel a la main,
# passent a cote. Le serveur tient donc la regle : pendant qu'une action
# tourne sur une application, les autres sont refusees en 409, avec un
# message qui dit pourquoi.
_actions_en_cours = {}
_actions_verrou = threading.Lock()


def une_action_a_la_fois(route):
    @functools.wraps(route)
    def enveloppe(n, *args, **kwargs):
        with _actions_verrou:
            verrou = _actions_en_cours.setdefault(n, threading.Lock())
        if not verrou.acquire(blocking=False):
            return jsonify({"error": f"Une action est déjà en cours sur « {n} ». "
                                     f"Attends qu'elle se termine, puis réessaie.",
                            "en_cours": True}), 409
        try:
            return route(n, *args, **kwargs)
        finally:
            verrou.release()
    return enveloppe


@flask_app.post("/api/toggle/<n>")
@require_admin
@une_action_a_la_fois
def api_toggle(n):
    refus = refus_action_protegee(n, "arrêt/redémarrage")
    if refus:
        return jsonify({"error": refus}), 403
    """Demarrer / arreter, et DIRE CE QU'ON A ESSAYE DE FAIRE.

    Signale par Lucas : « je ne peux pas arreter les applications », avec le
    message « L'application n'a pas demarre. (reponse 500) ». Deux defauts se
    superposaient, et le second cachait le premier :

      - l'action tentee n'etait renvoyee nulle part. La page ne pouvait donc
        que deviner, et son message de repli est ecrit pour le demarrage :
        tout arret rate s'annoncait « n'a pas demarre », ce qui envoie
        chercher exactement a l'oppose de la panne ;
      - une exception dans stop() ou start() sortait en page d'erreur HTML,
        sans JSON. Le motif reel restait dans les journaux du panneau, que
        l'interface ne montre pas.

    L'action se decide donc AVANT d'agir, elle accompagne toutes les
    reponses, et plus aucune exception ne sort d'ici sans explication.
    """
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    action = "arret" if is_running(n) else "demarrage"
    # L'action VOULUE, quand la page la donne. Un second clic parti avant
    # que la page ait vu le premier aboutir demande encore « demarrage » ;
    # l'application tourne deja, il n'y a donc rien a faire -- surtout pas
    # l'arreter.
    voulue = (request.get_json(silent=True) or {}).get("action")
    if voulue in ("arret", "demarrage") and voulue != action:
        return jsonify({"ok": True, "action": voulue, "deja": True,
                        "running": voulue == "demarrage"})
    try:
        erreur = stop(n) if action == "arret" else start(n)
        if erreur:
            # 409 et non 500 : le panneau a fait son travail, c'est
            # l'application qui refuse de s'arreter ou de demarrer. La
            # nuance compte pour qui lit les journaux du panneau.
            return jsonify({"error": erreur, "action": action}), 409
        return jsonify({"ok": True, "action": action,
                        "running": action == "demarrage"})
    except Exception as e:                                        # noqa: BLE001
        traceback.print_exc()
        verbe = "arrêter" if action == "arret" else "démarrer"
        return jsonify({
            "action": action,
            "error": f"Impossible de {verbe} l'application "
                     f"({type(e).__name__}: {e}). Le détail complet est dans "
                     f"les journaux du panneau : docker logs --tail 50 "
                     f"codelab-app-manager",
        }), 500


def restart_app(n):
    """Rend None si l'application est repartie, sinon POURQUOI.

    Elle se taisait : l'erreur de start() partait a la poubelle, la route
    repondait « ok », et le bouton « Redemarrer » laissait une application
    arretee sans un mot. C'est le meme defaut que start() avait avant d'etre
    corrige -- il restait ici, une ligne plus loin.
    """
    stop(n)
    for _ in range(30):
        if not is_running(n):
            break
        time.sleep(0.1)
    return start(n)


@flask_app.post("/api/visibility/<n>")
@require_admin
def api_visibility(n):
    # Une application par defaut ne se rend pas publique : le diagnostic
    # offrirait a un visiteur anonyme l'etat des lieux complet de
    # l'installation -- les conteneurs, les comptes sans second facteur, ce
    # qui est expose.
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True, silent=True) or {}
    vis = d.get("visibility")
    if vis not in VISIBILITES:
        # Sans valeur explicite, on bascule d'un etat a l'autre.
        vis = (VISIBILITE_PRIVEE if visibilite(apps[n]) == VISIBILITE_PUBLIQUE
               else VISIBILITE_PUBLIQUE)
    # Rendre publique une application sur un serveur que personne ne peut
    # joindre ne partage rien : cela retire seulement l'authentification.
    # Une application DEJA publique reste modifiable dans l'autre sens --
    # on ne bloque jamais le chemin qui referme.
    if vis == VISIBILITE_PUBLIQUE and not adresse_publique():
        return jsonify({"error": "Ce serveur n'a encore jamais été ouvert depuis "
                                 "internet : rendre une application publique ne "
                                 "ferait que retirer l'authentification. Ouvre le "
                                 "panneau une fois par ton nom de domaine."}), 400
    apps[n]["visibility"] = vis
    save(apps)
    return jsonify({"ok": True, "visibility": vis})


@flask_app.post("/api/restart/<n>")
@require_admin
@une_action_a_la_fois
def api_restart(n):
    refus = refus_action_protegee(n, "redémarrage")
    if refus:
        return jsonify({"error": refus}), 403
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    erreur = restart_app(n)
    if erreur:
        return jsonify({"error": erreur}), 409
    return jsonify({"ok": True})


@flask_app.post("/api/deploy/<n>")
@require_admin
@une_action_a_la_fois
def api_deploy(n):
    refus = refus_action_protegee(n, "déploiement")
    if refus:
        return jsonify({"error": refus}), 403
    """Build puis mise en ligne, en une action.

    L'enchainement etait a faire a la main en deux entrees de menu, et rien
    ne rappelait la seconde : on lancait le build, la page ne changeait pas,
    et on cherchait pourquoi -- le process servait toujours l'ancien dist/.

    L'ordre compte. Le build tourne pendant que l'ancienne version est encore
    servie, et un build en echec n'arrete rien du tout : on ne remplace une
    version qui marche que par une version qui compile.
    """
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    ok, msg = run_build(n)
    if not ok:
        return jsonify({"error": msg}), 400
    # "Deployer" sur une application arretee la met en ligne : c'est le sens
    # attendu du mot, et l'alternative (builder puis ne rien faire) est
    # exactement le geste oublie que cette route supprime.
    marquer_nouvelle_version(n)
    erreur = restart_app(n) if is_running(n) else start(n)
    if erreur:
        # Le build a reussi, la mise en ligne non : la nuance compte, et elle
        # se perdait entierement -- la route repondait « ok ».
        return jsonify({"error": "Le build a reussi, mais la mise en ligne a "
                                 "echoue : " + erreur}), 409
    return jsonify({"ok": True})


@flask_app.post("/api/build/<n>")
@require_admin
@une_action_a_la_fois
def api_build(n):
    refus = refus_action_protegee(n, "build")
    if refus:
        return jsonify({"error": refus}), 403
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    ok, msg = run_build(n)
    if not ok:
        return jsonify({"error": msg}), 400
    marquer_nouvelle_version(n)
    return jsonify({"ok": True})


@flask_app.get("/api/metrics/<n>")
@require_admin
def api_metrics(n):
    if n not in load() and not est_app_par_defaut(n):
        return jsonify({"error": "Application inconnue."}), 404

    # La fiche peut etre ouverte avant le premier rafraichissement de
    # /api/apps. Dans ce cas l'historique etait vide et les deux cartes
    # affichaient « Aucune mesure » jusqu'au prochain polling. Une ouverture
    # de l'onglet met maintenant au moins une mesure reelle a disposition.
    if n in procs and is_running(n):
        stats = proc_stats(procs[n].pid)
        record_metrics(n, stats["cpu_percent"], stats["memory_mb"])

    hist = get_metrics_history(n)
    return jsonify({
        "points": [{"t": t, "cpu": cpu, "mem": mem} for t, cpu, mem in hist],
    })


@flask_app.delete("/api/app/<n>")
@require_admin
def api_delete(n):
    # Le projet de diagnostic ne se supprime pas.
    #
    # C'est l'etat des lieux de l'installation : il dit si les services se
    # parlent, si la base repond, si le panneau est correctement expose. Une
    # stack sans lui n'a plus aucun moyen de se controler elle-meme -- et
    # comme l'inscription n'a lieu qu'UNE fois (marqueur), le supprimer le
    # ferait disparaitre pour de bon, pas jusqu'au prochain redemarrage.
    #
    # Refuse ici et pas seulement dans la page : masquer un bouton ne protege
    # rien, la route reste appelable a la main.
    if est_app_par_defaut(n):
        return jsonify({"error": f"« {n} » est une application par défaut de "
                                 f"CodeLab : elle est gérée par CodeLab et ne "
                                 f"se modifie ni ne se supprime à la main."}), 403
    supprimer_application(n)
    return jsonify({"ok": True})


def supprimer_application(n):
    """Arrete l'application et efface sa declaration : la fiche, et les
    destinataires de ses alertes. Le code, dans /workspace, n'est jamais
    touche (le panneau n'ecrit pas dans /workspace)."""
    stop(n)
    apps = load()
    apps.pop(n, None)
    save(apps)
    par_app = reglage("alertes_par_app", {}) or {}
    if par_app.pop(n, None) is not None:
        try:
            ecrire_reglage("alertes_par_app", par_app or None)
        except (OSError, RuntimeError):
            pass


# ------------------------ l'archivage d'une application ------------------------
#
# Vecu : « donner la possibilite d'archiver une app. La configuration
# disparait au bout de 30 jours si elle n'est pas desarchivee. Un espace
# dedie dans les configurations avec toutes les app archivees. »
#
# Archivee, une application est arretee et sort de partout -- liste des
# applications, hub, proxy (elle repond comme une adresse inconnue) -- mais
# sa fiche reste entiere : commandes, port, visibilite, droits des comptes,
# alertes. Desarchivee, elle revient telle quelle, arretee. Trente jours
# plus tard, purger_les_apps_archivees l'efface comme une suppression.
#
# Le nom et le port restent reserves tant qu'elle est archivee : elle est
# toujours dans load(), seulement marquee (archivee_le, dans les options de
# la fiche : aucune migration).
DELAI_ARCHIVE = 30 * 86400


def est_archivee(a):
    """La fiche (dict) d'une application archivee ? Un booleen ne compte
    pas : seule une date d'archivage en est une."""
    quand = (a or {}).get("archivee_le")
    return isinstance(quand, int) and not isinstance(quand, bool)


def apps_actives(apps=None):
    """Les applications declarees, sans les archivees."""
    apps = load() if apps is None else apps
    return {n: a for n, a in apps.items() if not est_archivee(a)}


def suppression_de_l_archive(a):
    return int(a["archivee_le"]) + DELAI_ARCHIVE if est_archivee(a) else None


@flask_app.post("/api/app/<n>/archiver")
@require_admin
def api_archiver(n):
    """Arrete l'application et la range aux archives."""
    if est_app_par_defaut(n):
        return jsonify({"error": refus_configuration(n)}), 403
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    if est_archivee(apps[n]):
        return jsonify({"ok": True})
    raison = stop(n)
    if raison:
        return jsonify({"error": f"« {n} » ne s'arrête pas : {raison}"}), 409
    apps = load()
    apps[n]["enabled"] = False
    apps[n]["archivee_le"] = int(time.time())
    save(apps)
    journaliser("application", qui=utilisateur_courant(), app=n, action="archivée",
                ip=_adresse_client())
    return jsonify({"ok": True, "suppression_le": suppression_de_l_archive(apps[n])})


@flask_app.post("/api/app/<n>/desarchiver")
@require_admin
def api_desarchiver(n):
    """La ressort des archives, arretee : la redemarrer reste un geste."""
    apps = load()
    if n not in apps or not est_archivee(apps[n]):
        return jsonify({"error": "Cette application n'est pas aux archives."}), 404
    apps[n].pop("archivee_le", None)
    save(apps)
    journaliser("application", qui=utilisateur_courant(), app=n, action="désarchivée",
                ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.get("/api/apps/archivees")
@require_admin
def api_apps_archivees():
    """Les applications aux archives, la plus recente d'abord, avec la date
    a laquelle leur fiche sera effacee."""
    liste = [{"name": n, "titre": titre_de(n, a), "path": a.get("path") or "",
              "archivee_le": a["archivee_le"], "suppression_le": suppression_de_l_archive(a)}
             for n, a in load().items() if est_archivee(a)]
    liste.sort(key=lambda x: -x["archivee_le"])
    return jsonify({"apps": liste, "delai_jours": DELAI_ARCHIVE // 86400})


def purger_les_apps_archivees(maintenant=None):
    """Efface les applications archivees depuis DELAI_ARCHIVE ; rend leurs
    noms. Ne leve jamais : la purge quotidienne ne doit pas tomber."""
    maintenant = time.time() if maintenant is None else maintenant
    effacees = []
    for n, a in list(load().items()):
        fin = suppression_de_l_archive(a)
        if fin is None or maintenant < fin:
            continue
        try:
            supprimer_application(n)
            journaliser("application", app=n, action="effacée après trente jours aux archives")
            effacees.append(n)
        except Exception as e:                                    # noqa: BLE001
            print(f"[app-manager] archive de {n} non effacee : {e}", flush=True)
    return effacees


# ---------------------- renommer une application ----------------------
#
# LE NOM N'EST PAS UNE ETIQUETTE, C'EST LA CLE. Il designe l'application dans
# apps.json, dans l'adresse qui l'ouvre, dans le journal qu'elle ecrit, dans
# la liste des projets autorises de chaque compte, dans les applications que
# chacun a masquees de son hub -- et il decide meme de l'uid sous lequel elle
# tourne. Changer la cle sans changer ce qui la designe, c'est perdre
# l'application de vue tout en la laissant tourner.
#
# D'ou une seule fonction qui fait le tour, plutot qu'un renommage dans la
# page et des oublis qui se decouvrent trois semaines plus tard.

def _renommer_references(ancien, nouveau):
    """Tout ce qui designe une application par son nom, hors apps.json."""
    # Les projets autorises de chaque compte.
    comptes = lire_utilisateurs()
    touche = False
    for compte in comptes.values():
        projets = compte.get("projets")
        if isinstance(projets, list) and ancien in projets:
            compte["projets"] = [nouveau if p == ancien else p for p in projets]
            touche = True
    if touche:
        ecrire_utilisateurs(comptes)

    # Les destinataires d'alerte de l'application, au coffre.
    par_app = reglage("alertes_par_app", {}) or {}
    if ancien in par_app:
        par_app[nouveau] = par_app.pop(ancien)
        ecrire_reglage("alertes_par_app", par_app)

    # Les applications que chacun a retirees de son hub.
    masquees = lire_masquees()
    touche = False
    for qui, liste in masquees.items():
        if isinstance(liste, list) and ancien in liste:
            masquees[qui] = [nouveau if v == ancien else v for v in liste]
            touche = True
    if touche:
        ecrire_masquees(masquees)

    # Le journal suit l'application : le perdre a chaque renommage effacerait
    # justement ce qu'on relit pour comprendre ce qui vient de se passer.
    for suffixe in (".log", ".log.1"):
        avant = os.path.join(LOG_DIR, ancien + suffixe)
        if os.path.exists(avant):
            try:
                os.replace(avant, os.path.join(LOG_DIR, nouveau + suffixe))
            except OSError as e:
                print(f"[app-manager] journal de {ancien} non renomme ({e}).", flush=True)


def _reprendre_les_fichiers(dossier, ancien_uid, nouvel_uid):
    """Redonne au nouvel uid les fichiers qui appartenaient a l'ancien.

    L'uid d'une application est DERIVE de son nom (voir uid_application) :
    la renommer lui en donne un autre, et tout ce qu'elle avait ecrit
    devient illisible pour elle. Un projet qui ne peut plus relire sa propre
    base SQLite apres un simple changement de nom serait un piege.

    On ne touche QUE ce qui appartenait a l'ancien uid -- le code source
    appartient a l'utilisateur SSH, et il doit le rester -- et jamais a
    travers un lien symbolique : ce code tourne en root.
    """
    if os.geteuid() != 0 or ancien_uid == nouvel_uid or not os.path.isdir(dossier):
        return 0
    repris = 0
    for racine, dossiers, fichiers in os.walk(dossier, followlinks=False):
        for nom in dossiers + fichiers:
            cible = os.path.join(racine, nom)
            try:
                st = os.lstat(cible)
                if st.st_uid == ancien_uid:
                    os.lchown(cible, nouvel_uid, st.st_gid)
                    repris += 1
            except OSError:
                pass
    return repris


@flask_app.post("/api/app/<n>/renommer")
@require_admin
def api_renommer(n):
    """Change le nom d'une application, et tout ce qui la designe par ce nom."""
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    # Meme raison que pour la suppression : l'etat des lieux de
    # l'installation est inscrit sous ce nom-la, et le marqueur qui evite une
    # seconde inscription porte dessus.
    if n == DIAGNOSTIC_NOM:
        return jsonify({"error": "Le projet de diagnostic ne se renomme pas : "
                                 "c'est lui qui dit si cette installation va bien, "
                                 "et le panneau le retrouve par son nom."}), 403
    brut = (request.get_json(force=True, silent=True) or {}).get("nom")
    titre = titre_propre(brut)
    nouveau = valid_name(brut)
    if not titre:
        return jsonify({"error": "Le nouveau nom est obligatoire."}), 400
    if not nouveau:
        return jsonify({"error": f"« {titre} » ne donne aucune adresse utilisable. "
                                 f"Il faut au moins une lettre sans accent "
                                 f"ou un chiffre."}), 400
    # LE NOM SEUL A PU CHANGER. « site » renomme en « Site » garde le chemin
    # /site/ : rien a deplacer, rien a arreter -- on enregistre le titre et
    # on s'arrete la. Sans ce cas, le renommage echouait sur « une adresse
    # deja prise » : la sienne.
    if nouveau == n:
        if titre != (apps[n].get("titre") or n):
            apps[n]["titre"] = titre
            save(apps)
        return jsonify({"ok": True, "nom": n, "titre": titre, "redemarree": False})
    if nouveau in adresses_reservees():
        return jsonify({"error": f"L'adresse « /{nouveau}/ » est réservée."}), 400
    if nouveau in apps:
        if est_archivee(apps[nouveau]):
            return jsonify({"error": f"L'adresse « /{nouveau}/ » est gardée par une application "
                                     f"aux archives : désarchive-la, ou supprime-la."}), 400
        return jsonify({"error": f"L'adresse « /{nouveau}/ » est déjà prise par "
                                 f"une autre application."}), 400

    # L'application doit s'arreter le temps du renommage : son processus
    # porte l'ancien nom dans son environnement (CODELAB_APP), c'est ce qui
    # permet au panneau de le reconnaitre apres un redemarrage. Le laisser
    # tourner reviendrait a le perdre.
    tournait = is_running(n)
    if tournait:
        souci = stop(n)
        if souci:
            return jsonify({"error": "Arrêt impossible avant le renommage : " + souci}), 409

    ancien_uid, nouvel_uid = uid_application(n), uid_application(nouveau)
    dossier = apps[n].get("path") or ""

    # La cle change de nom SANS changer de place : l'ordre d'apps.json est
    # celui des listes de la page, et une application qui saute en fin de
    # liste parce qu'on l'a renommee se cherche.
    apps = {(nouveau if cle == n else cle): valeur for cle, valeur in apps.items()}
    apps[nouveau]["titre"] = titre
    save(apps)
    _renommer_references(n, nouveau)
    repris = _reprendre_les_fichiers(dossier, ancien_uid, nouvel_uid)

    journaliser("renommage", qui=utilisateur_courant() or NOM_ADMIN, app=nouveau,
                action=f"{n} -> {nouveau}", ip=_adresse_client())

    erreur = None
    if tournait:
        erreur = start(nouveau)
    return jsonify({"ok": True, "nom": nouveau, "titre": titre,
                    "redemarree": tournait and not erreur,
                    "fichiers_repris": repris,
                    "erreur_demarrage": erreur or ""})


@flask_app.get("/api/logs/<n>")
@require_admin
def api_logs(n):
    f = os.path.join(LOG_DIR, n + ".log")
    if not os.path.exists(f):
        return jsonify({"lines": ["Aucun journal pour le moment."]})
    with open(f, errors="replace") as fh:
        lines = [l.rstrip() for l in fh.readlines()[-120:]]
    return jsonify({"lines": lines or ["Journal vide."]})


@flask_app.get("/api/logs/<n>/stream")
@require_admin
def api_logs_stream(n):
    f = os.path.join(LOG_DIR, n + ".log")

    if not _prendre_place_flux():
        return jsonify({"error": f"Trop de journaux suivis en même temps "
                                 f"({SSE_MAX_FLUX} au maximum). Ferme une "
                                 f"fenêtre de journal et réessaie."}), 503

    def gen():
        pos = max(0, os.path.getsize(f) - 4000) if os.path.exists(f) else 0
        debut = derniere_emission = time.time()
        yield "retry: 2000\n\n"
        while True:
            envoye = False
            if os.path.exists(f):
                with open(f, errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                for line in chunk.splitlines():
                    yield f"data: {line}\n\n"
                    envoye = True
            maintenant = time.time()
            if envoye:
                derniere_emission = maintenant
            elif maintenant - derniere_emission > SSE_BATTEMENT:
                # Commentaire SSE : ignore par le navigateur, mais il traverse
                # la connexion. Sans lui, un journal silencieux fait passer le
                # flux pour mort aux yeux du serveur (et de tout proxy pose
                # devant), qui finit par le fermer.
                yield ": battement\n\n"
                derniere_emission = maintenant
            if maintenant - debut > SSE_DUREE_MAX:
                # Un flux ouvert occupe un thread du serveur pour toujours :
                # quelques onglets oublies suffiraient a saturer le panneau.
                # On rend la main, et EventSource se reconnecte tout seul
                # (c'est a quoi sert le "retry" envoye en tete).
                return
            time.sleep(0.5)

    reponse = Response(stream_with_context(gen()), mimetype="text/event-stream")
    # call_on_close plutot qu'un "finally" dans le generateur : celui-ci ne
    # s'execute que si le generateur a demarre. Un client qui se deconnecte
    # avant laisserait sinon une place reservee pour toujours, et le plafond
    # se refermerait tout seul sur le panneau.
    reponse.call_on_close(_rendre_place_flux)
    return reponse


@flask_app.put("/api/app/<n>/logo")
@require_admin
def api_logo(n):
    """Depose le logo d'une application, envoye depuis sa page de reglages.

    Le corps est l'image elle-meme, pas un formulaire : il n'y a qu'un seul
    fichier, et un multipart n'apporterait qu'un analyseur de plus a nourrir.
    """
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    a = apps.get(n)
    if not a:
        return jsonify({"error": "Application inconnue."}), 404
    donnees = request.get_data(cache=False)
    if not donnees:
        return jsonify({"error": "Aucune image reçue."}), 400
    if len(donnees) > LOGO_MAX_OCTETS:
        return jsonify({"error": "Image trop lourde : %d Ko pour %d Ko au maximum."
                                 % (len(donnees) // 1024, LOGO_MAX_OCTETS // 1024)}), 400
    format_ = _format_du_logo(donnees)
    if not format_:
        # Le SVG tombe ici, et c'est voulu : servi dans l'origine du panneau,
        # un SVG peut y executer du script.
        return jsonify({"error": "Format non accepté. PNG, JPEG, GIF ou WebP — "
                                 "le SVG est refusé : il peut porter du script."}), 400
    ext, _mime = format_
    dossier = a["path"]
    if not os.path.isdir(dossier) or not under_root(dossier):
        return jsonify({"error": "Dossier du projet introuvable."}), 400

    # Un seul logo a la fois : les autres noms que NOUS ecrivons partent, sinon
    # icon.png survivrait a l'envoi d'un icon.jpg et continuerait de s'afficher
    # (find_icon prend le premier de la liste).
    for nom in LOGO_NOMS_ECRITS:
        chemin = os.path.join(dossier, nom)
        if os.path.isfile(chemin) and not os.path.islink(chemin):
            try:
                os.remove(chemin)
            except OSError:
                pass
    cible = os.path.join(dossier, "icon." + ext)
    tmp = cible + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(donnees)
        os.replace(tmp, cible)
        # Le fichier appartient au projet : il doit rester modifiable depuis
        # une session SSH comme le reste de son dossier.
        try:
            os.chmod(cible, 0o664)
        except OSError:
            pass
    except OSError as e:
        return jsonify({"error": f"Écriture impossible : {e}"}), 500
    journaliser("logo", qui=utilisateur_courant() or NOM_ADMIN, app=n,
                action="depose", ip=_adresse_client())
    return jsonify({"ok": True, "fichier": os.path.basename(cible)})


@flask_app.delete("/api/app/<n>/logo")
@require_admin
def api_logo_retirer(n):
    """Retire le logo depose. Ne touche qu'aux noms que le panneau ecrit :
    un favicon.ico pose a la main dans le projet lui appartient."""
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    a = load().get(n)
    if not a:
        return jsonify({"error": "Application inconnue."}), 404
    retires = []
    for nom in LOGO_NOMS_ECRITS:
        chemin = os.path.join(a["path"], nom)
        if os.path.isfile(chemin) and not os.path.islink(chemin):
            try:
                os.remove(chemin)
                retires.append(nom)
            except OSError as e:
                return jsonify({"error": f"Suppression impossible : {e}"}), 500
    if retires:
        journaliser("logo", qui=utilisateur_courant() or NOM_ADMIN, app=n,
                    action="retire", ip=_adresse_client())
    return jsonify({"ok": True, "retires": retires})


# LES ICONES DES APPLICATIONS PAR DEFAUT.
#
# Sans elles, ces deux-la tombaient sur l'initiale coloree servie a toute
# application sans logo : un « D » pour dagster, un « D » pour diagnostic, et
# un troisieme « D » pour demo. Trois carrees identiques dans la grille du
# hub, a distinguer en lisant le nom dessous -- c'est-a-dire en cessant de
# les reconnaitre d'un coup d'oeil.
#
# Dessinees ici plutot que posees en fichier : ces deux applications
# appartiennent a CodeLab, leur icone doit arriver avec l'image et ne rien
# demander a personne. Le format est le meme que celui des logos deposes, un
# SVG carre de 64, pour que la grille ne fasse aucune difference entre elles
# et les autres.

# Dagster : le graphe oriente acyclique, ce que l'outil manipule. Dessin
# original, dans le violet de la marque -- ce n'est pas le logo officiel, et
# il ne pretend pas l'etre.
ICONE_DAGSTER = (
    # Le logo OFFICIEL, tel que dagster-webserver le sert lui-meme
    # (webapp/build/favicon.svg) : memes traces, memes couleurs -- l'indigo
    # #4F43DD, son ombre #352D8E, l'oeil blanc. Recopie, pas redessine.
    #
    # Une seule retouche : la variante « ecran sombre » du fichier d'origine
    # (@media prefers-color-scheme) est retiree. Elle eclaircit le logo en
    # lavande, pensee pour un fond sombre ; or ici le logo est toujours pose
    # sur la plaque blanche (--plaque-logo), ou ce lavande disparaitrait.
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 67 67" role="img" aria-'
    'label="Dagster" fill="none"><style> .daggy-body { fill: #4F43DD; } .daggy-sh'
    'adow { fill: #352D8E; }  </style><path class="daggy-body" d="M22.0142 63.398'
    '9C22.0153 63.7681 22.0925 64.1331 22.2409 64.4711C22.3894 64.8091 22.606 65.'
    '1129 22.8771 65.3635C23.1482 65.6141 23.4681 65.806 23.8167 65.9275C24.1654 '
    '66.0489 24.5353 66.0971 24.9034 66.0692C38.8959 65.0678 52.1113 55.1693 56.3'
    '304 39.7088C56.5521 38.8196 57.2196 38.3737 58.1088 38.3737C58.5646 38.392 5'
    '8.9949 38.5892 59.3063 38.9226C59.6177 39.256 59.7852 39.6987 59.7725 40.154'
    '7C59.7725 43.6019 55.3395 52.6113 49.0004 57.8395C48.6773 58.1113 48.4197 58'
    '.4524 48.2468 58.8375C48.0739 59.2226 47.9901 59.6418 48.0017 60.0638C48.008'
    '1 60.4069 48.0822 60.7454 48.2198 61.0599C48.3573 61.3743 48.5555 61.6585 48'
    '.8031 61.8961C49.0507 62.1338 49.3428 62.3202 49.6626 62.4448C49.9823 62.569'
    '3 50.3236 62.6295 50.6667 62.6219C51.2221 62.6219 52.1113 62.2881 53.2221 61'
    '.2868C57.6551 57.2815 66.1038 46.0479 66.1038 33.7008C66.1038 15.5988 52.103'
    '5 0 32.5646 0C15.1301 0 0.0267944 14.2376 0.0267944 30.6995C0.0267944 41.599'
    '3 8.68929 49.8289 20.1263 49.8289C28.9009 49.8289 37.008 43.6019 39.2297 35.'
    '0359C39.4513 34.1467 40.1163 33.7008 41.0055 33.7008C41.4615 33.7192 41.892 '
    '33.9163 42.2038 34.2496C42.5157 34.5829 42.6838 35.0256 42.6717 35.4818C42.6'
    '717 39.375 35.3417 52.9528 20.4601 52.9528C16.9059 52.9528 12.4651 51.9515 9'
    '.35423 50.1705C8.93778 49.9687 8.48415 49.8551 8.02174 49.8368C7.66768 49.82'
    '32 7.31463 49.8829 6.9848 50.0124C6.65497 50.1418 6.35549 50.3381 6.10519 50'
    '.5889C5.85489 50.8397 5.65919 51.1396 5.53038 51.4697C5.40158 51.7997 5.3424'
    '7 52.1529 5.35676 52.5069C5.37113 52.9605 5.50107 53.4028 5.73426 53.7921C5.'
    '96744 54.1813 6.29615 54.5046 6.68925 54.7312C10.7989 57.0781 15.6855 58.290'
    '6 20.6817 58.2906C33.12 58.2906 44.4475 49.8367 47.7801 37.3802C48.0017 36.4'
    '91 48.6693 36.0451 49.5558 36.0451C50.0119 36.0634 50.4424 36.2606 50.7542 3'
    '6.5939C51.066 36.9272 51.2341 37.3698 51.2221 37.8261C51.2221 42.9422 42.116'
    '3 59.4041 24.5697 60.7392C23.8908 60.7896 23.2544 61.089 22.7828 61.5799C22.'
    '3111 62.0708 22.0374 62.7186 22.0142 63.3989Z" fill="#DEDEFC"/><path d="M39.'
    '4016 20.8013C41.7966 20.7834 44.1476 21.4453 46.1814 22.71C46.3869 21.5851 4'
    '6.5003 20.4453 46.5204 19.3019C46.5204 14.0215 42.4943 9.29126 37.592 9.2912'
    '6C33.7796 9.29126 31.3963 12.4491 31.3963 16.3501C31.3781 18.4574 32.1185 20'
    '.501 33.4824 22.1077C35.3301 21.2267 37.3548 20.7798 39.4016 20.8013Z" fill='
    '"white"/><path class="daggy-shadow" d="M50.8881 39.9307C51.5687 37.6047 51.8'
    '685 35.9906 51.8685 34.9423C51.8478 34.4896 51.6537 34.0622 51.3266 33.7487C'
    '50.9994 33.4351 50.5642 33.2593 50.111 33.2578C49.6888 33.2657 49.2814 33.41'
    '44 48.9535 33.6804C48.6256 33.9464 48.396 34.3144 48.3013 34.7259C48.1162 35'
    '.4899 47.7198 37.5604 47.3287 38.8746C47.4903 38.3826 47.6381 37.8828 47.772'
    ' 37.3752C47.9936 36.4834 48.6612 36.0401 49.5477 36.0401C50.0036 36.0578 50.'
    '4341 36.2544 50.746 36.5873C51.0579 36.9201 51.226 37.3625 51.214 37.8185C51'
    '.1931 38.5354 51.0783 39.2463 50.8724 39.9333L50.8881 39.9307Z" fill="#C9C6F'
    'A"/><path class="daggy-shadow" d="M60.3271 37.5004C60.3058 37.0484 60.1119 3'
    '6.6218 59.7854 36.3084C59.459 35.995 59.0248 35.8188 58.5722 35.8159C58.1505'
    ' 35.8243 57.7437 35.9734 57.4163 36.2393C57.0889 36.5053 56.8598 36.873 56.7'
    '652 37.284C56.5774 38.0663 56.1732 40.168 55.7769 41.4797H55.7951C55.9881 40'
    '.8955 56.168 40.3036 56.3166 39.7013C56.5383 38.8095 57.2058 38.3662 58.095 '
    '38.3662C58.5506 38.3838 58.9809 38.5805 59.2923 38.9135C59.6038 39.2464 59.7'
    '714 39.6888 59.7587 40.1446C59.746 40.6982 59.6698 41.2485 59.5318 41.7848C6'
    '0.0846 39.8447 60.3271 38.4418 60.3271 37.5004Z" fill="#C9C6FA"/><path d="M3'
    '9.4009 20.8014C40.5911 20.799 41.7761 20.9577 42.9237 21.2734C43.5263 20.451'
    '7 43.8273 19.4476 43.7763 18.43C43.7253 17.4124 43.3253 16.4434 42.6437 15.6'
    '862C41.962 14.9289 41.0403 14.4296 40.0336 14.2722C39.027 14.1148 37.9968 14'
    '.309 37.1166 14.8221L38.9419 17.8261L35.5233 16.3476C35.0603 17.0948 34.8281'
    ' 17.962 34.8559 18.8406C34.8838 19.7192 35.1704 20.57 35.6798 21.2864C36.893'
    ' 20.9594 38.1444 20.7963 39.4009 20.8014Z" fill="#163B36"/><path class="dagg'
    'y-shadow" d="M13.0639 40.1334C14.5041 40.1334 15.6715 38.966 15.6715 37.5258'
    'C15.6715 36.0857 14.5041 34.9182 13.0639 34.9182C11.6238 34.9182 10.4563 36.'
    '0857 10.4563 37.5258C10.4563 38.966 11.6238 40.1334 13.0639 40.1334Z" fill="'
    '#C9C6FA"/><path class="daggy-shadow" d="M13.0639 29.7035C14.5041 29.7035 15.'
    '6715 28.536 15.6715 27.0959C15.6715 25.6558 14.5041 24.4883 13.0639 24.4883C'
    '11.6238 24.4883 10.4563 25.6558 10.4563 27.0959C10.4563 28.536 11.6238 29.70'
    '35 13.0639 29.7035Z" fill="#C9C6FA"/><path class="daggy-shadow" d="M7.84895 '
    '34.9191C9.28909 34.9191 10.4566 33.7516 10.4566 32.3115C10.4566 30.8713 9.28'
    '909 29.7039 7.84895 29.7039C6.4088 29.7039 5.24133 30.8713 5.24133 32.3115C5'
    '.24133 33.7516 6.4088 34.9191 7.84895 34.9191Z" fill="#C9C6FA"/><path class='
    '"daggy-shadow" d="M19.5832 49.8241H20.1047C28.8793 49.8241 36.9864 43.5971 3'
    '9.2081 35.0311C39.4298 34.1419 40.0947 33.696 40.9839 33.696C41.4399 33.7143'
    ' 41.8704 33.9115 42.1823 34.2447C42.4941 34.578 42.6622 35.0207 42.6502 35.4'
    '77C42.6104 36.3872 42.4045 37.2824 42.0426 38.1185C42.1055 38.0214 42.1604 3'
    '7.9193 42.2069 37.8134C42.8667 36.3053 43.2076 34.6771 43.2082 33.031C43.208'
    '2 30.473 41.4298 28.8041 38.8769 28.8041C34.4257 28.8041 33.5339 32.6894 31.'
    '6564 36.8434C29.7086 41.1564 26.1309 45.8214 17.1086 45.8214C7.73419 45.8214'
    ' -1.89834 39.258 0.45112 25.9122C0.479804 25.7453 0.50327 25.5967 0.521523 2'
    '5.4585C0.177264 27.1827 0.00259008 28.9364 0 30.6946C0.0260762 41.438 8.4199'
    '9 49.5737 19.5832 49.8241Z" fill="#C9C6FA"/></svg>'
)

# Diagnostic : le trace d'un moniteur cardiaque, et un point qui bat. C'est
# ce que la page montre -- l'etat de sante de l'installation -- et c'est deja
# la marque que porte sa propre barre du haut : les deux se repondent.
ICONE_DIAGNOSTIC = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="16" fill="#0e7c86"/>'
    '<path d="M12 33h9l5-14 9 28 5-14h12" fill="none" stroke="#fff" '
    'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
    '</svg>')

# Mailpit : une enveloppe blanche au trait sur un degrade violet -- l'icone
# des maquettes de son interface. LE MEME dessin que celui de l'interface
# Mailpit de CodeLab (dagster/proxy/mailpit-ui/logo.svg) : la tuile et
# l'application se reconnaissent. Dessin original, pas le logo de Mailpit.
# Jamais dans un onglet : l'onglet porte toujours le logo CodeLab.
ICONE_MAILPIT = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-'
    'label="Mailpit"><defs><linearGradient id="cl-mp" x1="0" y1="0" x2="1" y2="1"'
    '><stop offset="0" stop-color="#7c6cf6"/><stop offset="1" stop-color="#4f46e5'
    '"/></linearGradient></defs><rect width="64" height="64" rx="18" fill="url(#c'
    'l-mp)"/><rect x="14" y="19" width="36" height="26" rx="5" fill="none" stroke'
    '="#fff" stroke-width="4" stroke-linejoin="round"/><path d="M16 22 32 34 48 2'
    '2" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-'
    'linejoin="round"/></svg>'
)

# Messages : deux bulles de conversation, la premiere qui ecrit (trois
# points), sur un degrade sarcelle -- l'accent de CodeLab. On y lit d'abord
# « messages », ce que l'application recoit, avant le travail qu'on en tire.
ICONE_MESSAGERIE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs>'
    '<linearGradient id="cl-ms" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#2fc7b8"/><stop offset="1" stop-color="#0b6c74"/>'
    '</linearGradient></defs>'
    '<rect width="64" height="64" rx="18" fill="url(#cl-ms)"/>'
    '<path d="M27 13h20a8 8 0 0 1 8 8v10a8 8 0 0 1-8 8h-1v6.5L39.5 39H27a8 8 0 0 1-8-8V21'
    'a8 8 0 0 1 8-8z" fill="#fff" opacity=".42"/>'
    '<path d="M17 22h21a8 8 0 0 1 8 8v11a8 8 0 0 1-8 8H26.5L18 56.5V49h-1a8 8 0 0 1-8-8V30'
    'a8 8 0 0 1 8-8z" fill="#fff"/>'
    '<circle cx="19.5" cy="35.5" r="2.7" fill="#0b6c74"/>'
    '<circle cx="27.5" cy="35.5" r="2.7" fill="#0b6c74"/>'
    '<circle cx="35.5" cy="35.5" r="2.7" fill="#0b6c74"/></svg>')

ICONES_PAR_DEFAUT = {
    MESSAGERIE_NOM: ICONE_MESSAGERIE,
    DAGSTER_NOM: ICONE_DAGSTER,
    DIAGNOSTIC_NOM: ICONE_DIAGNOSTIC,
    MAILPIT_NOM: ICONE_MAILPIT,
}


# OUVRIR DAGSTER PASSE PAR ICI, et pas directement par le port 3000.
#
# Trois choses en une seule redirection :
#
#   1. l'ouverture est NOTEE. C'est ce qui donne a Dagster l'onglet
#      « Activite » des autres applications -- qui l'a ouverte, quand,
#      combien de fois. Sans ce passage, le panneau ne voyait rien : le lien
#      partait droit sur un autre port.
#   2. elle s'ouvre A LA PLACE de la page courante, comme une application.
#      L'ancien lien s'ouvrait dans un nouvel onglet, parce qu'il n'y avait
#      alors aucun moyen de revenir ; le ruban de retour pose par le proxy
#      de Dagster a supprime cette raison.
#   3. l'adresse est construite par le SERVEUR, a partir de l'hote demande :
#      la page n'a plus a deviner sur quel nom de machine on est arrive.
#
# Une redirection, et non un proxy : l'interface de Dagster suit ses runs par
# websocket, et le proxy du panneau ne sait pas relayer une connexion
# montante. Le proxy de Dagster, lui, est fait pour cela.
def ouvrir_dagster():
    hors = refus_admin_hors_reseau()
    if hors:
        journaliser("refus", app=DAGSTER_NOM, motif="hors reseau local",
                    ip=_adresse_client())
        return Response(_page("Accès refusé", hors), 403, mimetype="text/html")
    adresse = origine_dagster()
    if not adresse:
        return Response(_page("Adresse introuvable",
                              "Le panneau n'a pas su construire l'adresse de "
                              "Dagster depuis cette requête."),
                        503, mimetype="text/html")
    journaliser_ouverture(DAGSTER_NOM)
    return redirect(adresse, 302)


flask_app.add_url_rule("/" + DAGSTER_NOM + "/", "ouvrir_dagster",
                       require_admin(ouvrir_dagster))
flask_app.add_url_rule("/" + DAGSTER_NOM, "ouvrir_dagster_sans_barre",
                       require_admin(ouvrir_dagster))


# OUVRIR MAILPIT : la meme porte que Dagster, pour les memes raisons --
# l'ouverture est notee, l'adresse construite par le serveur, et le reseau
# local verifie si l'administration y est limitee.
def ouvrir_mailpit():
    hors = refus_admin_hors_reseau()
    if hors:
        journaliser("refus", app=MAILPIT_NOM, motif="hors reseau local",
                    ip=_adresse_client())
        return Response(_page("Accès refusé", hors), 403, mimetype="text/html")
    adresse = origine_mailpit()
    if not adresse:
        return Response(_page("Adresse introuvable",
                              "Le panneau n'a pas su construire l'adresse de "
                              "Mailpit depuis cette requête."),
                        503, mimetype="text/html")
    journaliser_ouverture(MAILPIT_NOM)
    return redirect(adresse, 302)


flask_app.add_url_rule("/" + MAILPIT_NOM + "/", "ouvrir_mailpit",
                       require_admin(ouvrir_mailpit))
flask_app.add_url_rule("/" + MAILPIT_NOM, "ouvrir_mailpit_sans_barre",
                       require_admin(ouvrir_mailpit))


# L'ICONE DE DAGSTER : CELLE QUE DAGSTER SERT LUI-MEME.
#
# Un dessin fait a la main ne sera jamais le logo de Dagster -- au mieux une
# ressemblance, au pire une marque deformee. Or l'application porte le sien,
# a la racine de son serveur, et le panneau sait lui parler. Il le lui
# demande donc, et le garde : c'est LE logo, exactement celui qu'on voit dans
# l'onglet quand on ouvre Dagster, et il suivra ses evolutions sans que
# personne n'ait a redessiner quoi que ce soit.
#
# Le dessin d'origine reste, en repli : tant que Dagster n'a pas repondu une
# premiere fois -- stack qui demarre, conteneur arrete -- la tuile doit
# afficher quelque chose, et surtout pas la lettre « D » qu'elle partagerait
# avec « demo » et « diagnostic ».
# LE LOGO DE DAGSTER NE SE TELECHARGE PLUS, ET C'EST UNE CORRECTION.
#
# Le panneau allait chercher /favicon.ico sur l'instance Dagster et servait
# ce qu'il trouvait, en priorite sur le SVG ci-dessus. L'intention etait
# bonne -- porter le vrai logo plutot qu'une reproduction. Le resultat ne
# l'etait pas : un favicon fait 32 px de cote, la tuile du hub en fait 40 a
# 48. Le navigateur agrandissait donc une image matricielle, et le logo
# paraissait flou. Signale tel quel : « choisis un logo plus net ».
#
# Un test affirmait depuis longtemps que ce logo « est vectoriel et net ».
# Il lisait la constante, pas la reponse servie : la garantie existait sur le
# papier et etait defaite a l'execution par la branche qui est retiree ici.
# Le test de la reponse, lui, est ajoute avec ce changement.
#
# Ce qui disparait avec la branche : quatre chemins candidats, un cache sur
# disque et son fichier de type, une lecture des octets pour verifier que
# Dagster ne renvoie pas sa page d'accueil en guise d'image, et un appel
# reseau toutes les 24 h. Le SVG livre avec l'image ne demande rien a
# personne, reste net a toutes les tailles, et s'affiche meme quand Dagster
# est eteint -- ce qui est precisement le moment ou l'on regarde sa tuile.


# L'ONGLET DU HUB PORTE LE LOGO CODELAB, ET RIEN D'AUTRE.
#
# L'icone etait ecrite DANS la page (<link rel="icon" href="data:...">).
# Plusieurs navigateurs n'en tiennent pas compte pour l'onglet -- Safari en
# tete -- et retombent sur leur cache d'icones ou sur /favicon.ico, qui
# repondait 404 : l'onglet du hub montrait alors une icone vue ailleurs.
# Les deux pages pointent desormais vers /favicon.png?v=... : une vraie
# adresse, que tous suivent, et versionnee pour qu'aucun cache ne serve un
# ancien dessin. /favicon.ico repond la meme chose, pour ceux qui la
# demandent d'eux-memes.
#
# Cela n'a pas suffi : Safari range les icones par NOM D'HOTE, sans le port.
# Mailpit (8025) servait son logo sous /favicon.ico, et l'onglet du panneau
# le reprenait des qu'on revenait d'une application. D'ou :
#   - /icone-codelab.png, une adresse qu'aucun autre service ne sert ;
#   - /favicon.ico = le logo CodeLab sur TOUS les ports du nom d'hote
#     (le panneau, l'origine des applications, Mailpit) : quoi que Safari
#     retienne pour l'hote, c'est le notre.
# Une application garde son icone : celle qu'elle declare, ou celle que le
# proxy lui injecte, passe toujours avant /favicon.ico.
@flask_app.get("/favicon.ico")
@flask_app.get("/favicon.png")
@flask_app.get("/icone-codelab.png")
@flask_app.get("/codelab-onglet.png")
def favicon_panneau():
    r = Response(ICONE_CODELAB_PNG, mimetype="image/png")
    r.headers["Cache-Control"] = "no-cache"
    return r


# LE PANNEAU S'AJOUTE A L'ECRAN D'ACCUEIL D'UN TELEPHONE.
#
# Safari > Partager > « Sur l'ecran d'accueil » : sans ce qui suit, iOS posait
# une capture de la page en guise d'icone, et l'ouvrait dans un onglet Safari
# ordinaire, barre d'adresse comprise. Le manifeste et les balises
# apple-mobile-web-app-* des deux pages en font une application a part :
# plein ecran, son propre nom sous l'icone, son propre logo.
#
# Publics, comme le favicon : iOS les demande au moment d'ajouter l'icone,
# souvent depuis la page de connexion, et ils ne revelent qu'un nom et un
# logo. /apple-touch-icon.png est aussi l'adresse qu'iOS essaie de lui-meme
# quand une page n'en declare aucune.
#
# A savoir : sur iOS, l'application ajoutee a SES PROPRES cookies, separes de
# ceux de Safari. Il faut donc s'y connecter une fois, la session ouverte
# dans Safari ne la suit pas.
@flask_app.get("/apple-touch-icon.png")
@flask_app.get("/apple-touch-icon-precomposed.png")
def ecran_accueil_icone():
    r = Response(ICONE_ECRAN_ACCUEIL_PNG, mimetype="image/png")
    r.headers["Cache-Control"] = "no-cache"
    return r


# L'ECRAN DE DEMARRAGE. Ouverte depuis l'icone, une application web iOS
# affichait un ecran BLANC le temps de charger : une vraie app montre son fond
# et son logo. iOS exige une image par taille d'ecran (apple-touch-startup-
# image, choisie par media query) : le panneau les fabrique a la demande, au
# pixel pres, en Python pur -- fond de la couleur du bandeau, logo au centre
# -- et les garde en memoire. Clair et sombre.
ECRANS_DEMARRAGE = {
    # (largeur, hauteur) en points, portrait, densite. Les iPhone depuis le
    # SE...
    (320, 568, 2), (375, 667, 2), (414, 896, 2), (375, 812, 3), (414, 896, 3),
    (390, 844, 3), (428, 926, 3), (393, 852, 3), (430, 932, 3), (402, 874, 3),
    (440, 956, 3), (360, 780, 3),
    # ... et les iPad, du mini au Pro 13 pouces.
    (744, 1133, 2), (768, 1024, 2), (810, 1080, 2), (820, 1180, 2),
    (834, 1112, 2), (834, 1194, 2), (834, 1210, 2), (1024, 1366, 2),
    (1032, 1376, 2),
}
# Une tablette s'ouvre aussi bien couchee : les iPad ont aussi leur image en
# paysage. Un iPhone, lui, demarre toujours en portrait.
def _orientations(largeur):
    return ("portrait", "landscape") if largeur >= 744 else ("portrait",)


def _images_de_demarrage():
    """{(largeur, hauteur) en pixels: (densite, orientation, media)}."""
    images = {}
    for w, h, d in ECRANS_DEMARRAGE:
        for orientation in _orientations(w):
            taille = (w * d, h * d) if orientation == "portrait" else (h * d, w * d)
            media = (f"screen and (device-width: {w}px) and (device-height: {h}px) and "
                     f"(-webkit-device-pixel-ratio: {d}) and (orientation: {orientation})")
            images[taille] = (d, media)
    return images


IMAGES_DEMARRAGE = _images_de_demarrage()
FONDS_DEMARRAGE = {"clair": (0xE9, 0xEB, 0xEE), "sombre": (0x0D, 0x10, 0x15)}
_ecrans_faits = {}


def _png_lire_rgba(donnees):
    """Decode un PNG 8 bits RGBA non entrelace (le logo du panneau)."""
    largeur, hauteur, prof, couleur = struct.unpack(">IIBB", donnees[16:26])
    if prof != 8 or couleur != 6:
        raise ValueError("PNG RGBA 8 bits attendu")
    idat, i = b"", 8
    while i < len(donnees):
        n = struct.unpack(">I", donnees[i:i + 4])[0]
        if donnees[i + 4:i + 8] == b"IDAT":
            idat += donnees[i + 8:i + 8 + n]
        i += 12 + n
    brut, ligne, pas = zlib.decompress(idat), largeur * 4, 4
    pixels, prec, j = [], bytearray(ligne), 0
    for _ in range(hauteur):
        filtre, cur = brut[j], bytearray(brut[j + 1:j + 1 + ligne])
        j += 1 + ligne
        for x in range(ligne):
            a = cur[x - pas] if x >= pas else 0
            b = prec[x]
            c = prec[x - pas] if x >= pas else 0
            if filtre == 1:
                cur[x] = (cur[x] + a) & 255
            elif filtre == 2:
                cur[x] = (cur[x] + b) & 255
            elif filtre == 3:
                cur[x] = (cur[x] + ((a + b) >> 1)) & 255
            elif filtre == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[x] = (cur[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        pixels.append(bytes(cur))
        prec = cur
    return largeur, hauteur, pixels


def _agrandir(largeur, hauteur, pixels, facteur):
    """Agrandissement bilineaire, pour un logo net sur un ecran Retina."""
    if facteur == 1:
        return largeur, hauteur, pixels
    nl, nh = int(largeur * facteur), int(hauteur * facteur)
    sortie = []
    for y in range(nh):
        fy = min(max((y + .5) / facteur - .5, 0), hauteur - 1)
        y0 = int(fy); y1 = min(y0 + 1, hauteur - 1); dy = fy - y0
        r0, r1, ligne = pixels[y0], pixels[y1], bytearray(nl * 4)
        for x in range(nl):
            fx = min(max((x + .5) / facteur - .5, 0), largeur - 1)
            x0 = int(fx); x1 = min(x0 + 1, largeur - 1); dx = fx - x0
            for k in range(4):
                v = (r0[x0 * 4 + k] * (1 - dx) + r0[x1 * 4 + k] * dx) * (1 - dy) \
                    + (r1[x0 * 4 + k] * (1 - dx) + r1[x1 * 4 + k] * dx) * dy
                ligne[x * 4 + k] = int(v + .5)
        sortie.append(bytes(ligne))
    return nl, nh, sortie


def _png_ecrire_rgb(largeur, hauteur, lignes):
    def bloc(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    brut = b"".join(b"\x00" + l for l in lignes)
    return (b"\x89PNG\r\n\x1a\n" + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut, 6)) + bloc(b"IEND", b""))


def ecran_de_demarrage(largeur, hauteur, theme):
    cle = (largeur, hauteur, theme)
    if cle not in _ecrans_faits:
        fond = FONDS_DEMARRAGE[theme]
        lw, lh, logo = _png_lire_rgba(ICONE_CODELAB_PNG)
        # Le logo fait environ 90 points de large, quelle que soit la densite.
        densite = IMAGES_DEMARRAGE[(largeur, hauteur)][0]
        facteur = max(1, round(densite * 90 / lw * 10) / 10)
        lw, lh, logo = _agrandir(lw, lh, logo, facteur)
        ligne_fond = bytes(fond) * largeur
        x0, y0 = (largeur - lw) // 2, (hauteur - lh) // 2
        lignes = []
        for y in range(hauteur):
            if y0 <= y < y0 + lh:
                ligne = bytearray(ligne_fond)
                src = logo[y - y0]
                for x in range(lw):
                    a = src[x * 4 + 3]
                    if a:
                        o = (x0 + x) * 3
                        for k in range(3):
                            ligne[o + k] = (src[x * 4 + k] * a + fond[k] * (255 - a)) // 255
                lignes.append(bytes(ligne))
            else:
                lignes.append(ligne_fond)
        _ecrans_faits[cle] = _png_ecrire_rgb(largeur, hauteur, lignes)
    return _ecrans_faits[cle]


# LES ICONES DU MANIFESTE. Android et Chrome demandent du 192 et du 512 px ;
# l'icone d'ecran d'accueil d'iOS (180 px) etait etiree jusque-la, floue.
# « maskable » : Android decoupe l'icone en cercle ou en goutte, le logo se
# tient donc dans la zone sure (les 80 % du centre). Meme fabrication que
# l'ecran de demarrage, fond blanc comme l'icone d'iOS, gardee en memoire.
ICONES_MANIFESTE = {"192": (192, .62), "512": (512, .62), "512-maskable": (512, .46)}
_icones_faites = {}


def icone_manifeste(nom):
    if nom not in _icones_faites:
        cote, part = ICONES_MANIFESTE[nom]
        fond = (255, 255, 255)
        lw, lh, logo = _png_lire_rgba(ICONE_CODELAB_PNG)
        lw, lh, logo = _agrandir(lw, lh, logo, cote * part / lw)
        x0, y0 = (cote - lw) // 2, (cote - lh) // 2
        ligne_fond, lignes = bytes(fond) * cote, []
        for y in range(cote):
            if not y0 <= y < y0 + lh:
                lignes.append(ligne_fond)
                continue
            ligne, src = bytearray(ligne_fond), logo[y - y0]
            for x in range(lw):
                a = src[x * 4 + 3]
                if a:
                    o = (x0 + x) * 3
                    for k in range(3):
                        ligne[o + k] = (src[x * 4 + k] * a + fond[k] * (255 - a)) // 255
            lignes.append(bytes(ligne))
        _icones_faites[nom] = _png_ecrire_rgb(cote, cote, lignes)
    return _icones_faites[nom]


@flask_app.get("/icone-<nom>.png")
def icone_du_manifeste(nom):
    if nom not in ICONES_MANIFESTE:
        return Response("Inconnu", status=404, mimetype="text/plain")
    r = Response(icone_manifeste(nom), mimetype="image/png")
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


@flask_app.get("/ecran-demarrage/<int:largeur>x<int:hauteur>-<theme>.png")
def ecran_demarrage(largeur, hauteur, theme):
    if (largeur, hauteur) not in IMAGES_DEMARRAGE or theme not in FONDS_DEMARRAGE:
        return Response("Inconnu", status=404, mimetype="text/plain")
    r = Response(ecran_de_demarrage(largeur, hauteur, theme), mimetype="image/png")
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


def balises_ecran_de_demarrage():
    """Les <link rel="apple-touch-startup-image"> des pages du panneau."""
    return "\n".join(
        f'<link rel="apple-touch-startup-image" media="{media} and '
        f'(prefers-color-scheme: {schema})" href="/ecran-demarrage/{w}x{h}-{theme}.png">'
        for (w, h), (_, media) in sorted(IMAGES_DEMARRAGE.items())
        for theme, schema in (("clair", "light"), ("sombre", "dark")))


BALISES_ECRAN_DE_DEMARRAGE = balises_ecran_de_demarrage()


# HORS LIGNE, L'APPLICATION S'OUVRE QUAND MEME. Sans serveur (ou sans
# reseau), une application web installee affichait la page d'erreur de
# Safari : plus rien d'une app. Un service worker -- enregistre seulement en
# mode application, pas dans le navigateur, ou le panneau reste un site --
# garde UNE page en reserve, /hors-ligne.html, et la montre quand une page du
# panneau ne repond pas. Il ne met en cache ni les pages du panneau (elles
# portent la session) ni aucune donnee, et laisse passer tout le reste.
PAGE_HORS_LIGNE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#E9EBEE" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0D1015" media="(prefers-color-scheme: dark)">
<title>CodeLab</title>
<style>
:root{--bg:#E9EBEE;--txt:#14181d;--dim:#5b6470;--carte:#fff;--ligne:#dadee3}
@media (prefers-color-scheme: dark){:root{--bg:#0D1015;--txt:#e8ebef;--dim:#98a2ae;--carte:#161B22;--ligne:#262E38}}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
 font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 -webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;overscroll-behavior:none}
main{min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;
 padding:24px;box-sizing:border-box;text-align:center}
img{width:72px;height:72px}
h1{font-size:20px;margin:6px 0 0}
p{margin:0;color:var(--dim);max-width:320px}
button{margin-top:8px;font:inherit;font-weight:650;padding:10px 20px;border-radius:999px;border:1px solid var(--ligne);
 background:var(--carte);color:var(--txt)}
</style></head><body><main>
<img src="data:image/png;base64,__LOGO__" alt="">
<h1>Serveur injoignable</h1>
<p>CodeLab ne répond pas : le serveur est arrêté, ou ton téléphone n'a pas de réseau.</p>
<p id="etat">Nouvel essai dans 5 s.</p>
<button type="button" onclick="essayer()">Réessayer</button>
</main><script>
var n=5;
function essayer(){
  document.getElementById('etat').textContent='Connexion…';
  fetch('/health',{cache:'no-store'}).then(function(r){ if(r.ok) location.reload(); else attendre(); }, attendre);
}
function attendre(){ n=5; tic(); }
function tic(){
  if(n<=0){ essayer(); return; }
  document.getElementById('etat').textContent='Nouvel essai dans '+n+' s.';
  n--; setTimeout(tic,1000);
}
tic();
window.addEventListener('online', essayer);
</script></body></html>"""

SERVICE_WORKER = """// CodeLab, mode application : une page de reserve quand le serveur se tait.
const CACHE = 'codelab-hors-ligne-v2';
const RESERVE = ['/hors-ligne.html'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(RESERVE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(cles => Promise.all(cles.filter(c => c !== CACHE).map(c => caches.delete(c))))
    .then(() => self.clients.claim()));
});
// Seules les PAGES passent par ici, et seulement en cas d'echec : le reste
// (API, images, applications) va au reseau sans detour.
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => caches.match('/hors-ligne.html')));
});
// Les notifications : le panneau pousse {titre, texte, url, etiquette},
// chiffre de bout en bout. Une meme etiquette remplace la precedente (une
// application tombee puis revenue ne laisse qu'une notification).
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) {}
  e.waitUntil(self.registration.showNotification(d.titre || 'CodeLab', {
    body: d.texte || '', icon: '/apple-touch-icon.png?v=4',
    tag: d.etiquette || undefined, renotify: !!d.etiquette,
    data: { url: d.url || '/' }
  }));
});
// Toucher la notification ouvre CodeLab a la bonne page : la fenetre deja
// ouverte si elle existe, sinon une nouvelle. Jamais une adresse d'ailleurs.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  let cible = new URL('/', self.location.origin);
  try {
    const u = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin);
    if (u.origin === self.location.origin) cible = u;
  } catch (err) {}
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(fenetres => {
    for (const f of fenetres) {
      if (new URL(f.url).origin === self.location.origin && 'focus' in f) {
        return f.focus().then(g => (g || f).navigate ? (g || f).navigate(cible.href) : null);
      }
    }
    return self.clients.openWindow(cible.href);
  }));
});
"""


@flask_app.get("/hors-ligne.html")
def page_hors_ligne():
    # Le logo est DANS la page : hors ligne, rien d'autre ne se charge.
    r = Response(PAGE_HORS_LIGNE.replace("__LOGO__", base64.b64encode(ICONE_CODELAB_PNG).decode()),
                 mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@flask_app.get("/sw.js")
def service_worker():
    r = Response(SERVICE_WORKER, mimetype="text/javascript")
    # Toujours relu : une nouvelle version du panneau remplace l'ancienne.
    r.headers["Cache-Control"] = "no-cache"
    return r


def est_un_telephone(agent):
    """Le manifeste est-il demande par un telephone ? Lu dans l'en-tete
    User-Agent : un iPhone, ou un Android qui se dit « Mobile » (une
    tablette Android ne le dit pas, un iPad se presente comme un Mac).

    Ce n'est pas un controle : une erreur de lecture ne donne que
    l'orientation d'un autre appareil, rien de plus."""
    agent = agent or ""
    return "iPhone" in agent or "iPod" in agent or ("Android" in agent and "Mobile" in agent)


@flask_app.get("/manifest.webmanifest")
def ecran_accueil_manifeste():
    manifeste = {
        "name": "CodeLab",
        "short_name": "CodeLab",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        # La couleur du fond et du bandeau (style par defaut, clair) : c'est
        # celle de l'ecran de lancement et de la barre d'etat d'Android. Une
        # barre blanche au-dessus d'un bandeau gris cassait l'effet
        # d'application ; la page l'ajuste ensuite au theme choisi.
        "background_color": "#E9EBEE",
        "theme_color": "#E9EBEE",
        # Un identifiant stable : l'application installee reste la meme si
        # start_url change un jour.
        "id": "/",
        "lang": "fr",
        "description": "Ton atelier de développement : ouvrir, mettre en ligne et partager tes applications.",
        # TELEPHONE : TOUJOURS DROIT. Vecu : « je ne veux pas que la rotation
        # soit autorisee tout court ». Le manifeste demande le portrait au
        # telephone, l'orientation libre aux tablettes et aux ordinateurs
        # (une tablette se tient dans les deux sens). Android le respecte ;
        # iOS ne verrouille pas une page web -- il peut lire ce champ, sans
        # garantie --, et mode.js redresse alors la page (« PAS DE
        # ROTATION »). Le manifeste est lu a l'installation : il faut
        # rajouter l'icone a l'ecran d'accueil pour qu'il compte.
        "orientation": ("portrait" if est_un_telephone(request.headers.get("User-Agent"))
                        else "any"),
        "categories": ["developer", "productivity"],
        "icons": [{"src": "/apple-touch-icon.png", "sizes": "180x180",
                   "type": "image/png", "purpose": "any"},
                  {"src": "/icone-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": "/icone-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": "/icone-512-maskable.png", "sizes": "512x512", "type": "image/png",
                   "purpose": "maskable"}],
        # L'appui long sur l'icone (Android, Windows, Dock du Mac) : aller
        # droit ou l'on va le plus souvent.
        "shortcuts": [
            {"name": "Hub", "url": "/", "icons": [{"src": "/icone-192.png", "sizes": "192x192"}]},
            {"name": "Messages", "url": "/messages",
             "icons": [{"src": "/icone-192.png", "sizes": "192x192"}]},
            {"name": "Paramètres", "url": "/parametres",
             "icons": [{"src": "/icone-192.png", "sizes": "192x192"}]},
        ],
    }
    r = Response(json.dumps(manifeste), mimetype="application/manifest+json")
    r.headers["Cache-Control"] = "no-cache"
    # La reponse depend de l'appareil : un cache intermediaire ne doit pas
    # servir au telephone le manifeste de la tablette.
    r.headers["Vary"] = "User-Agent"
    return r


@flask_app.get("/api/icon/<n>")
@require_auth
def api_icon(n):
    reponse = _api_icon(n)
    # Gardee cinq minutes par le navigateur : le hub se redessine toutes les
    # dix secondes, et une icone redemandee a chaque fois faisait clignoter
    # toutes les tuiles. « private » : la reponse depend de la session.
    reponse.headers["Cache-Control"] = "private, max-age=300"
    return reponse


def _api_icon(n):
    """L'icone d'une application. Trois sources, dans cet ordre.

      1. le logo DEPOSE dans le projet -- il gagne toujours, c'est un choix
         explicite de celui qui l'a pose ;
      2. l'icone livree avec CodeLab, pour les applications par defaut. C'est
         un DEFAUT, pas une reservation : elle ne recouvre pas un logo pose ;
      3. l'initiale coloree, pour tout le reste.

    Sans le 2, « dagster », « diagnostic » et « demo » tombaient sur le meme
    « D » colore : trois carres a distinguer en lisant le nom dessous,
    c'est-a-dire en cessant de les reconnaitre d'un coup d'oeil.
    """
    # Accessible a un compte utilisateur, pour que son espace affiche les
    # icones -- mais seulement des projets qu'il peut ouvrir : la liste des
    # icones est une liste des projets existants.
    if not peut_voir(n):
        return Response(default_icon_svg(n), mimetype="image/svg+xml")
    apps = load()
    a = apps.get(n)
    icon_path = find_icon(a["path"]) if a else None
    if not icon_path and n in ICONES_PAR_DEFAUT:
        return Response(ICONES_PAR_DEFAUT[n], mimetype="image/svg+xml",
                        headers={"X-Content-Type-Options": "nosniff"})
    if icon_path:
        # UNE IMAGE, ET RIEN QUE CA. Ce chemin sert un fichier pris dans le
        # dossier d'un projet, dans l'ORIGINE DU PANNEAU. Un SVG depose a la
        # main y executerait son script : la politique ci-dessous le rend
        # inerte, et nosniff empeche le navigateur de deviner un autre type
        # que celui annonce.
        reponse = send_file(icon_path)
        reponse.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        reponse.headers["X-Content-Type-Options"] = "nosniff"
        return reponse
    return Response(default_icon_svg(n), mimetype="image/svg+xml",
                    headers={"X-Content-Type-Options": "nosniff"})
