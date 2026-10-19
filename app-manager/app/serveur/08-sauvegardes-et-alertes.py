# CodeLab -- LE PANNEAU, SAUVEGARDES ET ALERTES
# ---------------------------------------------------------------------------
# ROLE        les sauvegardes, la surveillance externe, la version, les alertes et l'envoi des mails.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   sauvegarde de l'etat, code de /workspace, surveillance externe, sauvegardes vues du panneau, version, alertes mail, configurations d'envoi.
#

# --------------------------- sauvegarde de l'etat ---------------------------
#
# Il n'y en avait aucune. Rien ne copiait utilisateurs.json, apps.json ni le
# journal des acces : un disque perdu, et il fallait tout redeclarer de
# memoire -- les comptes, les droits par projet, les applications, les
# categories, l'adresse publique. Postgres, lui, est sauvegarde par son
# propre conteneur (voir postgres/entrypoint.sh) : pg_dump refuse de
# sauvegarder un serveur plus recent que lui, et c'est le seul endroit ou il
# a la bonne version. Chacun sauvegarde ce qu'il est SEUL a pouvoir
# sauvegarder.
#
# Ce qui n'entre pas dans l'archive :
#
#   les journaux d'applications (logs/)  ils se regenerent, et ils pesent
#   credentials.env                      il n'est pas dans ce dossier, et
#                                        c'est voulu : l'archive se recopie,
#                                        se transporte, se depose ailleurs.
#                                        Y mettre le mot de passe du panneau
#                                        et la cle de session reviendrait a
#                                        les diffuser avec elle.
SAUVEGARDES_DIR = os.environ.get("APP_MANAGER_SAUVEGARDES", "/sauvegardes")
SAUVEGARDE_HEURES = int(os.environ.get("APP_MANAGER_SAUVEGARDE_HEURES", "24"))
SAUVEGARDES_GARDEES = int(os.environ.get("APP_MANAGER_SAUVEGARDES_GARDEES", "7"))
SAUVEGARDE_PREFIXE = "panneau-"


def sauvegardes_actives():
    """Le dossier n'existe que si le volume est monte.

    Sans lui, l'archive irait dans la couche du conteneur et disparaitrait au
    premier « docker compose down » -- une sauvegarde qui s'efface avec ce
    qu'elle sauvegarde est pire que rien, parce qu'on croit en avoir une.
    """
    return SAUVEGARDE_HEURES > 0 and os.path.isdir(SAUVEGARDES_DIR)


# Ce que l'archive laisse dehors, et pourquoi :
#
#   logs/   les journaux d'applications. Ils se regenerent, et ils sont de
#           loin ce qui pese le plus.
#   home/   le dossier personnel de chaque application. C'est un cache (npm,
#           pip), pas de l'etat -- et il est ECRIVABLE PAR LES APPLICATIONS.
#           L'y inclure laisserait n'importe laquelle d'entre elles decider
#           du poids de la sauvegarde, et y deposer ce qu'elle veut.
ARCHIVE_EXCLUS = ("logs", "home")


def _filtre_de_l_archive(info):
    """tarfile nomme les membres « ./quelque-chose » quand la racine est
    ajoutee sous le nom « . » : le prefixe se retire ici, sinon la
    comparaison porte sur le point et ne filtre rien."""
    chemin = info.name[2:] if info.name.startswith("./") else info.name
    if chemin.split("/")[0] in ARCHIVE_EXCLUS or chemin.endswith((".log", ".log.1")):
        return None
    # La base vivante ne se copie pas fichier par fichier : une ecriture en
    # cours, ou ce qui attend encore dans le -wal, donnerait une copie
    # incoherente. L'archive en recoit un instantane (voir plus bas).
    if chemin in ("codelab.db", "codelab.db-wal", "codelab.db-shm"):
        return None
    return info


def sauvegarder_l_etat():
    """Une archive du dossier d'etat, verifiee, puis la rotation.

    Ecrite a cote puis renommee : un « docker compose down » au milieu de
    l'ecriture laisserait sinon une archive tronquee portant l'heure la plus
    recente -- celle qu'on choisirait pour restaurer.

    VERIFIEE, comme les dumps Postgres : l'archive est relue entierement
    avant d'etre gardee. Une sauvegarde qu'on decouvre illisible le jour de
    la restauration ne vaut pas mieux que pas de sauvegarde.
    """
    horodatage = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    nom = f"{SAUVEGARDE_PREFIXE}{horodatage}.tar.gz"
    final = os.path.join(SAUVEGARDES_DIR, nom)
    provisoire = final + ".partielle"
    instantane = os.path.join(SAUVEGARDES_DIR, f".codelab-{horodatage}.db")
    try:
        # Un instantane coherent de la base, pris par SQLite lui-meme, meme
        # pendant des ecritures.
        stockage.sauvegarder_vers(DB_FILE, instantane)
        with tarfile.open(provisoire, "w:gz") as archive:
            archive.add(STATE_DIR, arcname=".", filter=_filtre_de_l_archive)
            archive.add(instantane, arcname="./codelab.db")
    finally:
        try:
            os.remove(instantane)
        except OSError:
            pass
    with tarfile.open(provisoire) as archive:
        if not archive.getmembers():
            raise OSError("archive vide")
    os.chmod(provisoire, 0o600)
    os.replace(provisoire, final)
    _rotation_sauvegardes()
    return final


def _rotation_sauvegardes():
    """Les N plus recentes, et rien d'autre.

    Le tri est sur le NOM : il porte la date en AAAAMMJJ-HHMMSS, donc
    l'ordre alphabetique est l'ordre chronologique -- y compris au passage
    d'une annee, la ou un tri sur la date de fichier suivrait une copie.
    """
    try:
        archives = sorted(n for n in os.listdir(SAUVEGARDES_DIR)
                          if n.startswith(SAUVEGARDE_PREFIXE) and n.endswith(".tar.gz"))
    except OSError:
        return
    for vieille in archives[:-SAUVEGARDES_GARDEES] if SAUVEGARDES_GARDEES > 0 else []:
        try:
            os.remove(os.path.join(SAUVEGARDES_DIR, vieille))
        except OSError:
            pass


# ------------------------- LE CODE DE /workspace -------------------------
#
# Les bases et l'etat du panneau etaient sauvegardes ; le CODE des projets ne
# l'etait pas. Tant qu'un projet est pousse sur Git, rien n'est perdu -- mais
# rien ne le verifie, et un projet commence un soir sans « git init » vit
# seulement sur ce disque.
#
# Une archive par semaine, quatre gardees. Sans ce qui se reconstruit
# (dependances, caches, builds) ni les fichiers de plus de 50 Mo (jeux de
# donnees, binaires) : l'archive doit rester petite pour qu'on la copie
# ailleurs sans y penser. Relue avant d'etre gardee, comme les autres.
WORKSPACE_PREFIXE = "workspace-"
WORKSPACE_JOURS = int(os.environ.get("APP_MANAGER_WORKSPACE_JOURS", "7"))
WORKSPACE_GARDEES = 4
WORKSPACE_FICHIER_MAX = 50 * 1024 * 1024
WORKSPACE_EXCLUS = {"node_modules", ".venv", "venv", "__pycache__", ".cache",
                    ".npm", "dist", "build", ".next", ".pytest_cache",
                    ".mypy_cache", ".vscode-server", ".codex", ".claude-code"}
# .codex et .claude-code portent les jetons de connexion des agents
# (dev/entrypoint.sh) : une archive copiee ailleurs ne les emporte pas, et
# une reconnexion suffit apres une restauration.


def _filtre_workspace(membre):
    parties = membre.name.split("/")
    if any(p in WORKSPACE_EXCLUS for p in parties):
        return None
    if membre.isfile() and membre.size > WORKSPACE_FICHIER_MAX:
        return None
    # Les secrets d'un projet (.env) restent dans l'archive : sans eux, le
    # projet restaure ne demarre pas. L'archive est donc en 0600, comme
    # celle de l'etat du panneau.
    return membre


def workspace_a_sauvegarder():
    """Vrai si la derniere archive a plus de WORKSPACE_JOURS jours."""
    if WORKSPACE_JOURS <= 0 or not os.path.isdir(ROOT):
        return False
    try:
        archives = sorted(n for n in os.listdir(SAUVEGARDES_DIR)
                          if n.startswith(WORKSPACE_PREFIXE) and n.endswith(".tar.gz"))
    except OSError:
        return False
    if not archives:
        return True
    age = time.time() - os.path.getmtime(os.path.join(SAUVEGARDES_DIR, archives[-1]))
    return age >= WORKSPACE_JOURS * 86400


def sauvegarder_le_workspace():
    horodatage = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    final = os.path.join(SAUVEGARDES_DIR, f"{WORKSPACE_PREFIXE}{horodatage}.tar.gz")
    provisoire = final + ".partielle"
    try:
        with tarfile.open(provisoire, "w:gz") as archive:
            archive.add(ROOT, arcname=".", filter=_filtre_workspace)
        with tarfile.open(provisoire) as archive:
            if not archive.getmembers():
                raise OSError("archive vide")
        os.chmod(provisoire, 0o600)
        os.replace(provisoire, final)
    except BaseException:
        try:
            os.remove(provisoire)
        except OSError:
            pass
        raise
    archives = sorted(n for n in os.listdir(SAUVEGARDES_DIR)
                      if n.startswith(WORKSPACE_PREFIXE) and n.endswith(".tar.gz"))
    for vieille in archives[:-WORKSPACE_GARDEES]:
        try:
            os.remove(os.path.join(SAUVEGARDES_DIR, vieille))
        except OSError:
            pass
    return final


# ------------------------ LA SURVEILLANCE EXTERNE ------------------------
#
# Toutes les alertes de la stack partent DE la machine : si elle s'eteint --
# coupure de courant, disque mort, box en panne --, personne ne previent
# personne. Seul un service EXTERIEUR peut constater un silence.
#
# Le principe est celui du « dead man's switch » : le panneau appelle une
# adresse toutes les cinq minutes, et le service exterieur (healthchecks.io,
# gratuit jusqu'a 20 surveillances, ou tout service compatible) envoie un
# mail quand les appels cessent. Aucun port a ouvrir : c'est la machine qui
# appelle.
SURVEILLANCE_INTERVALLE = 300
_surveillance_etat = {"quand": None, "ok": None, "detail": ""}


def lire_surveillance():
    d = reglage("surveillance", {}) or {}
    url = d.get("url") if isinstance(d, dict) else ""
    return url if isinstance(url, str) and url_surveillance_valide(url) else ""


def url_surveillance_valide(url):
    """https seulement, sans espace, d'une longueur raisonnable. C'est une
    adresse que le panneau appellera tout seul, a intervalles reguliers."""
    if not isinstance(url, str) or len(url) > 300 or any(c.isspace() for c in url):
        return False
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname)


def ecrire_surveillance(url):
    # Au coffre : l'adresse de ping suffit a faire croire le serveur vivant.
    ecrire_reglage("surveillance", {"url": url} if url else None)


def signaler_vie(url=None):
    """Un appel a l'adresse de surveillance. (ok, detail), jamais d'exception."""
    url = url if url is not None else lire_surveillance()
    if not url:
        return None, "aucune adresse"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CodeLab"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ok, detail = 200 <= r.status < 300, f"HTTP {r.status}"
    except Exception as e:                                        # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    _surveillance_etat.update(quand=time.time(), ok=ok, detail=detail)
    if not ok:
        print(f"[app-manager] surveillance externe injoignable : {detail}", flush=True)
    return ok, detail


def demarrer_la_surveillance_externe():
    def _boucle():
        while True:
            try:
                signaler_vie()
            except Exception as e:                                # noqa: BLE001
                print(f"[app-manager] surveillance externe : {e}", flush=True)
            time.sleep(SURVEILLANCE_INTERVALLE)
    threading.Thread(target=_boucle, daemon=True).start()


@flask_app.get("/api/surveillance-externe")
@require_admin
def api_surveillance_lire():
    etat = dict(_surveillance_etat)
    return jsonify({"url": lire_surveillance(), "intervalle": SURVEILLANCE_INTERVALLE,
                    "dernier_appel": etat["quand"], "ok": etat["ok"],
                    "detail": etat["detail"]})


@flask_app.post("/api/surveillance-externe")
@require_admin
def api_surveillance_ecrire():
    """Enregistre l'adresse et l'appelle AUSSITOT : une adresse mal copiee
    se voit ici, pas le jour ou la machine tombe sans que rien ne sonne."""
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    if url and not url_surveillance_valide(url):
        return jsonify({"error": "Adresse invalide : elle doit commencer par "
                                 "https:// (celle que donne healthchecks.io)."}), 400
    if url:
        ok, detail = signaler_vie(url)
        if not ok:
            return jsonify({"error": "Cette adresse n'a pas répondu, rien n'a "
                                     "été enregistré.", "detail": detail}), 400
    try:
        ecrire_surveillance(url)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Réglage non enregistré : {e}"}), 500
    if not url:
        _surveillance_etat.update(quand=None, ok=None, detail="")
    journaliser("reglage", qui=utilisateur_courant() or NOM_ADMIN,
                action="surveillance externe " + ("activee" if url else "retiree"),
                ip=_adresse_client())
    return jsonify({"ok": True, "url": url})


# ------------------- les sauvegardes, vues du panneau -------------------
#
# Elles ne sortaient jamais du serveur, et rien ne le disait : un disque qui
# lache emportait le panneau ET ses sauvegardes. Parametres > Securite les
# montre, permet de telecharger la derniere, et ALERTE tant qu'aucune copie
# n'est partie ailleurs depuis une semaine -- sauf si l'administrateur dit
# qu'il copie deja le dossier par ses propres moyens (NAS, rsync, restic).

SAUVEGARDES_SUIVI_FILE = os.path.join(STATE_DIR, "sauvegardes-suivi.json")
SAUVEGARDE_NOM = re.compile(r"^(panneau|workspace)-\d{8}-\d{6}\.tar\.gz$")
SAUVEGARDE_COPIE_JOURS = 7


def lire_suivi_sauvegardes():
    try:
        with open(SAUVEGARDES_SUIVI_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire_suivi_sauvegardes(d):
    tmp = SAUVEGARDES_SUIVI_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, SAUVEGARDES_SUIVI_FILE)


def archives_de_sauvegarde():
    """Les archives du dossier, la plus recente d'abord."""
    if not os.path.isdir(SAUVEGARDES_DIR):
        return []
    liste = []
    for nom in os.listdir(SAUVEGARDES_DIR):
        chemin = os.path.join(SAUVEGARDES_DIR, nom)
        if SAUVEGARDE_NOM.match(nom) and os.path.isfile(chemin):
            st = os.stat(chemin)
            liste.append({"nom": nom, "genre": nom.split("-", 1)[0],
                          "taille": st.st_size, "date": int(st.st_mtime)})
    return sorted(liste, key=lambda a: a["date"], reverse=True)


def etat_des_sauvegardes():
    archives = archives_de_sauvegarde()
    suivi = lire_suivi_sauvegardes()
    copie = suivi.get("derniere_copie")
    externe = bool(suivi.get("copie_externe"))
    maintenant = time.time()
    # L'alerte : des archives existent, et aucune n'est sortie du serveur
    # depuis une semaine (ou jamais), sans copie declaree ailleurs.
    alerte = bool(archives) and not externe and (
        not copie or maintenant - copie > SAUVEGARDE_COPIE_JOURS * 86400)
    return {"actives": sauvegardes_actives(), "toutes_les_heures": SAUVEGARDE_HEURES,
            "gardees": SAUVEGARDES_GARDEES, "archives": archives[:12],
            "derniere_copie": copie, "copie_externe": externe,
            "alerte_hors_serveur": alerte, "jours": SAUVEGARDE_COPIE_JOURS}


def resume_sauvegardes():
    """Ce que le widget du hub affiche : la derniere archive, et l'alerte
    tant qu'aucune copie n'est sortie du serveur."""
    try:
        etat = etat_des_sauvegardes()
    except OSError:
        return None
    derniere = etat["archives"][0] if etat["archives"] else None
    return {"actives": etat["actives"], "derniere": derniere,
            "alerte_hors_serveur": etat["alerte_hors_serveur"]}


@flask_app.get("/api/sauvegardes")
@require_admin
def api_sauvegardes():
    return jsonify(etat_des_sauvegardes())


@flask_app.get("/api/sauvegardes/<nom>")
@require_admin
def api_sauvegarde_telecharger(nom):
    """Une archive, en piece jointe. Seuls les noms produits par le panneau
    passent : ni chemin, ni autre fichier du dossier."""
    if not SAUVEGARDE_NOM.match(nom):
        return jsonify({"error": "Sauvegarde introuvable."}), 404
    chemin = os.path.join(SAUVEGARDES_DIR, nom)
    if not os.path.isfile(chemin):
        return jsonify({"error": "Sauvegarde introuvable."}), 404
    suivi = lire_suivi_sauvegardes()
    suivi["derniere_copie"] = int(time.time())
    try:
        ecrire_suivi_sauvegardes(suivi)
    except OSError:
        pass
    journaliser("sauvegarde", qui=utilisateur_courant() or NOM_ADMIN, action="telechargee",
                fichier=nom, ip=_adresse_client())
    return send_file(chemin, mimetype="application/gzip", as_attachment=True,
                     download_name=nom, max_age=0)


@flask_app.put("/api/sauvegardes/copie")
@require_admin
def api_sauvegardes_copie():
    """{externe: bool} -- l'administrateur copie deja le dossier ailleurs."""
    d = request.get_json(force=True, silent=True) or {}
    suivi = lire_suivi_sauvegardes()
    suivi["copie_externe"] = bool(d.get("externe"))
    ecrire_suivi_sauvegardes(suivi)
    return jsonify(etat_des_sauvegardes())


# ------------------------- la version de CodeLab -------------------------
#
# Rien ne disait quelle version tournait : « je ne vois pas les modifs »
# voulait souvent dire « l'image n'a pas ete retiree ». L'image porte son
# commit et sa date (CODELAB_VERSION, CODELAB_VERSION_DATE, poses par la CI
# au build) ; Parametres > Compte les affiche.
#
# AUCUN APPEL SORTANT : le panneau ne demande pas a GitHub s'il existe plus
# recent. C'est un choix -- un serveur qui contacte l'exterieur de lui-meme
# est un risque a prendre sans necessite. La page propose un lien vers les
# nouveautes ; c'est le navigateur de l'administrateur qui l'ouvre, s'il le
# veut.

CODELAB_VERSION = os.environ.get("CODELAB_VERSION", "").strip()
CODELAB_VERSION_DATE = os.environ.get("CODELAB_VERSION_DATE", "").strip()
DEPOT_CODELAB = "studio-codelab/codelab"
SHA_VALIDE = re.compile(r"^[0-9a-f]{40}$")


def etat_de_la_version():
    dev = not SHA_VALIDE.match(CODELAB_VERSION)
    return {"sha": "" if dev else CODELAB_VERSION, "court": "" if dev else CODELAB_VERSION[:7],
            "date": CODELAB_VERSION_DATE, "dev": dev, "depot": DEPOT_CODELAB}


@flask_app.get("/api/version")
@require_admin
def api_version():
    return jsonify(etat_de_la_version())


def demarrer_les_sauvegardes():
    if not sauvegardes_actives():
        if SAUVEGARDE_HEURES <= 0:
            print("[app-manager] sauvegardes desactivees "
                  "(APP_MANAGER_SAUVEGARDE_HEURES=0).", flush=True)
        else:
            print(f"[app-manager] pas de volume de sauvegarde monte sur "
                  f"{SAUVEGARDES_DIR} : l'etat du panneau ne sera pas "
                  f"sauvegarde.", flush=True)
        return

    def _boucle():
        while True:
            try:
                if workspace_a_sauvegarder():
                    chemin = sauvegarder_le_workspace()
                    journaliser("sauvegarde", fichier=os.path.basename(chemin),
                                octets=os.path.getsize(chemin))
            except Exception as e:
                print(f"[app-manager] archive du workspace impossible : {e}", flush=True)
            try:
                chemin = sauvegarder_l_etat()
                journaliser("sauvegarde", fichier=os.path.basename(chemin),
                            octets=os.path.getsize(chemin))
            except Exception as e:
                # Jamais d'exception qui remonte : le fil mourrait, et il n'y
                # aurait plus de sauvegarde du tout -- silencieusement.
                print(f"[app-manager] sauvegarde impossible : {e}", flush=True)
                journaliser("sauvegarde", erreur=str(e))
            time.sleep(SAUVEGARDE_HEURES * 3600)

    threading.Thread(target=_boucle, daemon=True).start()


# ------------------------------ alertes mail ------------------------------
#
# Le panneau surveille deja les applications et les redemarre quand elles
# tombent (voir monitor_tick). Mais il fallait avoir le panneau sous les yeux
# pour le savoir : une application qui s'arrete la nuit reste arretee jusqu'a
# ce qu'on pense a regarder. Un mail transforme cette surveillance passive en
# alerte.
#
# Deux endroits, pour une seule raison : le serveur d'envoi d'origine est
# partage avec Dagster, le reste est propre au panneau.
#
#   credentials.env, bloc "codelab-alertes" : le serveur SMTP et son mot de
#     passe. C'est le fichier en 0600, et c'est DEJA le bloc que lit le
#     capteur d'alerte de Dagster -- une seule configuration SMTP pour toute
#     la stack, pas deux a tenir a jour.
#   le coffre (reglages chiffres de la base) : l'interrupteur et les
#     destinataires. Une adresse n'est pas un secret, mais c'est une donnee
#     personnelle, et rien n'est en clair sur le disque (regle 16).
BLOC_ALERTES = "codelab-alertes"

# Nombre de lignes de journal jointes au mail. Assez pour reconnaitre une
# trace d'exception, pas assez pour rendre le mail illisible sur telephone.
ALERTE_LOG_LIGNES = 25


def lire_alertes():
    """L'interrupteur et l'ancienne liste globale, lus dans le coffre. Jamais
    d'exception : le moniteur doit tourner quoi qu'il arrive."""
    d = reglage("alertes", {}) or {}
    if not isinstance(d, dict):
        d = {}
    return {
        "actif": bool(d.get("actif")),
        "destinataires": [a for a in d.get("destinataires", []) if isinstance(a, str) and a.strip()],
    }


def ecrire_alertes(actif, destinataires):
    ecrire_reglage("alertes", {"actif": bool(actif),
                               "destinataires": list(destinataires)})


# --------------------- deux configurations d'envoi ---------------------
#
# POURQUOI DEUX. credentials.env porte la configuration d'ORIGINE : celle
# posee a l'installation, par le compose ou a la main. Elle a une qualite que
# rien d'autre n'a -- elle a toujours marche, et personne ne l'a touchee
# depuis une page web. C'est donc elle qu'on garde en repli.
#
# Une configuration saisie depuis le panneau vit a cote, dans smtp.json, et
# n'ECRASE JAMAIS la premiere. Sans cette separation, se tromper d'un
# caractere dans un nom de serveur depuis une page supprimait le seul moyen
# de prevenir qu'une application est tombee -- et l'on ne s'en apercevait
# qu'au premier incident, c'est-a-dire au pire moment.
#
# Ce qui en decoule :
#
#   a l'ENREGISTREMENT, une configuration personnalisee doit prouver qu'elle
#     fonctionne : on se connecte reellement au serveur, on chiffre, on
#     s'authentifie. Si cela echoue, rien n'est enregistre et le message du
#     serveur est remonte tel quel. C'est ce qui remplace l'ancien bouton
#     "envoyer un mail de test" -- un test qu'il fallait penser a lancer, et
#     dont l'oubli ne se voyait pas ;
#   a l'ENVOI, si la personnalisee echoue malgre tout (mot de passe revoque,
#     serveur eteint, quota), on repart aussitot sur celle d'origine. Une
#     alerte qui ne sort pas ajoute une panne a celle qu'elle signale.
# Vingt secondes : au-dela, ce n'est plus une lenteur mais une panne, et ni
# le moniteur ni une page ne doivent rester suspendus a un serveur muet.
SMTP_DELAI = 20

# Les champs d'une configuration d'envoi, et la variable de credentials.env
# qui porte chacun. Une seule liste : ajouter un champ ici le fait exister
# partout, plutot que dans trois fonctions a tenir d'accord.
CHAMPS_SMTP = {
    "host": "SMTP_HOST",
    "port": "SMTP_PORT",
    "tls": "SMTP_TLS",
    "user": "SMTP_USER",
    "password": "SMTP_PASSWORD",
    "expediteur": "ALERTE_FROM",
}

# Les cles que le panneau ecrit POUR LUI-MEME dans credentials.env. Elles y
# vivent parce que le capteur Dagster les lit dans ce fichier, pas parce
# qu'une application aurait a les connaitre.
#
# Elles sont donc retirees de secrets_partages() : une application est du
# code arbitraire tournant sous un autre uid, et lui remettre SMTP_PASSWORD
# lui donnerait de quoi expedier du courrier au nom de CodeLab -- une adresse
# de confiance, celle-la meme d'ou partent les alertes. ALERTE_ADMIN n'est
# pas un secret, mais c'est l'adresse de l'administrateur : elle n'a rien a
# faire dans l'environnement d'une application non plus.
#
# La regle existait deja pour le prefixe APP_MANAGER_ ; ce bloc lui avait
# echappe, faute de porter ce prefixe. Le renommer n'etait pas possible : le
# capteur Dagster et la documentation lisent ces noms-la.
#
# Une application recoit bien un SMTP_HOST et un SMTP_PORT, mais ce sont ceux
# de Mailpit (alias_du_relais_mail), jamais ceux-ci.
CLES_PANNEAU = set(CHAMPS_SMTP.values()) | {"ALERTE_ADMIN"}


def _port_smtp(brut, defaut=587):
    try:
        port = int(str(brut or "").strip())
    except (TypeError, ValueError):
        return defaut
    return port if 1 <= port <= 65535 else defaut


def _normalise_smtp(brut):
    """Met une configuration en forme, quelle que soit sa provenance."""
    brut = brut or {}
    tls = str(brut.get("tls") or "starttls").strip().lower()
    if tls not in ("starttls", "ssl", "none"):
        tls = "starttls"
    cfg = {
        "host": str(brut.get("host") or "").strip(),
        "port": _port_smtp(brut.get("port")),
        "tls": tls,
        "user": str(brut.get("user") or "").strip(),
        "password": str(brut.get("password") or ""),
        "expediteur": str(brut.get("expediteur") or "").strip(),
    }
    # Gmail et la plupart des fournisseurs refusent d'expedier au nom d'une
    # autre adresse que celle du compte : l'expediteur suit donc l'identifiant,
    # sauf mention explicite.
    if not cfg["expediteur"]:
        cfg["expediteur"] = cfg["user"]
    return cfg


def smtp_origine():
    """La configuration de credentials.env. Celle qui sert de repli."""
    return _normalise_smtp({
        cle: read_shared_value(env) for cle, env in CHAMPS_SMTP.items()
    })


def lire_smtp_personnalise():
    """La configuration saisie depuis le panneau, ou None.

    Jamais d'exception : un fichier illisible doit faire retomber sur la
    configuration d'origine, pas empecher le panneau de demarrer.
    """
    d = reglage("smtp")
    if not isinstance(d, dict) or not str(d.get("host") or "").strip():
        return None
    return _normalise_smtp(d)


def ecrire_smtp_personnalise(cfg):
    # Au coffre, chiffre : il porte le mot de passe du serveur d'envoi.
    ecrire_reglage("smtp", cfg)


def effacer_smtp_personnalise():
    """Revenir a la configuration d'origine. Toujours permis -- c'est la
    marche arriere, et elle ne doit dependre d'aucune condition."""
    try:
        ecrire_reglage("smtp", None)
    except (OSError, RuntimeError) as e:
        print(f"[app-manager] configuration d'envoi non effacee : {e}", flush=True)


BLOC_RELAIS_MAIL = "codelab-relais-mail"


def publier_relais_mail():
    """Publie pour codelab-mailpit le serveur d'envoi a relayer.

    Celui que le panneau utilise lui-meme : la configuration saisie dans
    Parametres > E-mail si elle existe, celle d'origine sinon. Mailpit ne
    peut pas lire smtp.json, qui vit dans le dossier d'etat du panneau ; il
    lit ce bloc, et se relance tout seul quand il change. Une seule
    configuration d'envoi pour toute la stack, donc.

    Cles prefixees CODELAB_INTERNE_ : jamais transmises aux applications.
    Elles envoient par Mailpit, sans jamais voir ce mot de passe.
    """
    cfg = lire_smtp_personnalise() or smtp_origine()
    valeurs = {}
    if cfg.get("host"):
        valeurs = {
            "CODELAB_INTERNE_RELAIS_HOST": cfg["host"],
            "CODELAB_INTERNE_RELAIS_PORT": str(cfg["port"]),
            "CODELAB_INTERNE_RELAIS_TLS": cfg["tls"],
            "CODELAB_INTERNE_RELAIS_USER": cfg["user"],
            "CODELAB_INTERNE_RELAIS_PASSWORD": cfg["password"],
            "CODELAB_INTERNE_RELAIS_FROM": cfg["expediteur"],
        }
    commentaires = [
        "# Le serveur d'envoi que codelab-mailpit relaie : celui de",
        "#   Parametres > E-mail, recopie ici par le panneau. Ne pas editer :",
        "#   le panneau reecrit ce bloc a chaque enregistrement.",
        "# Jamais transmis aux applications (prefixe CODELAB_INTERNE_).",
    ]
    return upsert_shared_block(BLOC_RELAIS_MAIL, commentaires,
                               {k: v for k, v in valeurs.items() if v})


def smtp_incomplet(cfg):
    """Ce qui manque a cette configuration pour pouvoir envoyer."""
    manques = []
    if not cfg.get("host"):
        manques.append("serveur d'envoi")
    if not cfg.get("expediteur"):
        manques.append("adresse d'expedition")
    return manques


def verifier_smtp(cfg):
    """Se connecte VRAIMENT au serveur, et renvoie (ok, message).

    Connexion, chiffrement, authentification -- tout sauf l'envoi. C'est
    volontaire : une verification qui expedie un message oblige a choisir un
    destinataire, donc a deranger quelqu'un a chaque enregistrement, et finit
    par etre contournee. Tout ce qui peut echouer a l'envoi echoue deja ici,
    a l'exception du refus d'un destinataire precis.
    """
    manques = smtp_incomplet(cfg)
    if manques:
        return False, "Configuration incomplete : " + ", ".join(manques) + "."
    try:
        if cfg["tls"] == "ssl":
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                                  context=ssl.create_default_context(),
                                  timeout=SMTP_DELAI) as s:
                if cfg["user"] and cfg["password"]:
                    s.login(cfg["user"], cfg["password"])
            return True, ""
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_DELAI) as s:
            s.ehlo()
            if cfg["tls"] != "none":
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            if cfg["user"] and cfg["password"]:
                s.login(cfg["user"], cfg["password"])
        return True, ""
    except Exception as e:
        # Le message du serveur est la seule chose qui aide vraiment ici
        # ("authentification refusee", "relais interdit", "nom inconnu") : on
        # le remonte tel quel plutot que de le resumer en "echec".
        return False, f"{type(e).__name__}: {e}"


def _adresses(brutes):
    """Nettoie une liste d'adresses saisies.

    Pas de validation stricte : un format valide n'est pas une adresse qui
    existe, et rien ici ne peut trancher cela. On retire les doublons et ce
    qui n'a manifestement pas la forme d'une adresse.
    """
    if isinstance(brutes, str):
        brutes = re.split(r"[,;\s]+", brutes)
    vues, propres = set(), []
    for a in brutes or []:
        a = (a or "").strip()
        if a and "@" in a and a not in vues:
            vues.add(a)
            propres.append(a)
    return propres


def alertes_admin():
    """L'adresse qui recoit TOUTES les alertes, quelle que soit l'application.

    Elle vit dans le bloc de credentials.env, a cote du serveur d'envoi : une
    installation peut donc la poser des le depart, et c'est le seul
    destinataire garanti le jour ou une application n'en declare aucun.

    Repli sur l'ancienne liste globale de alertes.json : avant les alertes
    par application, tous les destinataires etaient la. Sans ce repli, une
    installation existante aurait cesse d'etre prevenue a la mise a jour --
    silencieusement, et l'on ne s'en serait apercu qu'au premier incident.
    """
    # Celle reglee dans le panneau (au coffre), sinon celle posee a
    # l'installation dans credentials.env, sinon l'ancienne liste globale.
    return (_adresses(reglage("alertes_admin") or [])
            or _adresses(read_shared_value("ALERTE_ADMIN") or "")
            or _adresses(lire_alertes()["destinataires"]))


def config_smtp():
    """La configuration qui sera REELLEMENT utilisee, et ce qui lui manque.

    La personnalisee quand il y en a une, celle d'origine sinon. Renvoie
    (cfg, manquants) : cfg est utilisable si manquants est vide.
    """
    cfg = dict(lire_smtp_personnalise() or smtp_origine())
    cfg["destinataires"] = alertes_admin()
    manquants = smtp_incomplet(cfg)
    if not cfg["destinataires"]:
        manquants.append("adresse d'alerte de l'administrateur")
    return cfg, manquants


def smtp_utilisable():
    """(cfg, ok) pour un envoi a une adresse choisie.

    Distinct de config_smtp() : les alertes veulent en plus savoir a qui
    ecrire, alors qu'un code de verification part vers une adresse donnee.
    Sans cette distinction, un serveur d'envoi parfaitement configure
    passerait pour incomplet tant qu'aucune alerte n'est reglee.
    """
    cfg, _ = config_smtp()
    return cfg, not smtp_incomplet(cfg)


def envoi_mail_configure():
    """Un serveur d'envoi est-il regle ? Sans lui, aucun compte : un compte
    se connecte avec un code envoye par e-mail, et une invitation EST un
    e-mail. Creer un compte qui ne pourrait pas recevoir son code, ce
    serait le laisser entrer sans second facteur -- ou ne jamais entrer."""
    try:
        return smtp_utilisable()[1]
    except Exception:                                             # noqa: BLE001
        return False


SMTP_REQUIS = ("Règle d'abord un serveur d'envoi (Paramètres › E-mail) : sans lui, "
               "aucun compte ne peut recevoir son invitation ni ses codes de connexion.")


def _refus_sans_smtp():
    """409 si aucun serveur d'envoi n'est regle, None sinon."""
    if envoi_mail_configure():
        return None
    return jsonify({"error": SMTP_REQUIS, "smtp_requis": True}), 409


# LES MAILS DU PANNEAU PASSENT PAR MAILPIT, comme ceux des applications.
#
# Avant, le panneau parlait directement au serveur d'envoi : ses mails
# (alertes, codes, invitations) ne laissaient aucune trace, alors que ceux
# des applications se relisaient dans Mailpit. Desormais tout passe par le
# meme service : chaque mail se relit dans l'interface de Mailpit, le mode
# test vaut pour tout le monde, et le plafond d'envoi aussi. Mailpit relaie
# par le serveur de Parametres > E-mail, que le panneau lui publie deja
# (publier_relais_mail).
#
# Mailpit injoignable (service arrete, installation sans lui) : repli sur
# l'envoi direct, pour qu'une alerte ne tombe pas avec le service qui
# devait la porter.
#
# SAUF LES MAILS DE SECURITE. Un code de verification, de connexion ou de
# reinitialisation, un lien d'invitation, l'alerte d'une connexion
# administrateur inconnue : ce sont des secrets, ou des signaux qu'un
# intrus aurait interet a lire et a effacer. Garder leur copie dans Mailpit,
# que tout administrateur ouvre depuis le hub, ce serait laisser trainer la
# cle a cote de la porte. Ils partent en DIRECT par le serveur d'envoi, et
# ne se relisent nulle part dans CodeLab (sensible=True).
MAILPIT_SMTP_HOTE = os.environ.get("APP_MANAGER_MAILPIT_SMTP_HOTE") or MAILPIT_AMONT
MAILPIT_SMTP_PORT = int(os.environ.get("APP_MANAGER_MAILPIT_SMTP_PORT", "1025") or 1025)
MAIL_PAR_MAILPIT = (os.environ.get("APP_MANAGER_MAIL_PAR_MAILPIT", "1").strip().lower()
                    not in ("0", "non", "false", "no"))
# Mailpit remplace l'expediteur par celui du compte d'envoi ; il en faut
# quand meme un quand aucun serveur n'est regle (mail garde, pas relaye).
EXPEDITEUR_DEFAUT = "CodeLab <codelab@localhost>"


def _envoyer_par_mailpit(msg):
    """Remet le message a Mailpit, ou leve OSError / SMTPException."""
    with smtplib.SMTP(MAILPIT_SMTP_HOTE, MAILPIT_SMTP_PORT, timeout=10) as s:
        s.send_message(msg)


def envoyer_mail(cfg, sujet, corps, destinataires=None, sensible=False, html=None):
    """Envoie, ou leve. Par Mailpit d'abord, en direct sinon.

    sensible=True (mails de securite) : jamais par Mailpit, toujours en direct.
    html : une version mise en page, en plus du texte.
    """
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = cfg.get("expediteur") or EXPEDITEUR_DEFAUT
    msg["To"] = ", ".join(destinataires if destinataires is not None
                          else cfg["destinataires"])
    msg.set_content(corps)
    if html:
        msg.add_alternative(html, subtype="html")

    if sensible:
        if not cfg.get("host"):
            raise RuntimeError("Aucun serveur d'envoi configuré : un mail de sécurité "
                               "ne passe pas par Mailpit.")
    elif MAIL_PAR_MAILPIT:
        try:
            _envoyer_par_mailpit(msg)
            return
        except (OSError, smtplib.SMTPException) as e:
            if not cfg.get("host"):
                raise RuntimeError(f"Mailpit injoignable ({type(e).__name__}: {e}) "
                                   "et aucun serveur d'envoi configuré.") from e
            print(f"[app-manager] Mailpit injoignable ({type(e).__name__}: {e}) : "
                  "envoi direct.", flush=True)
    elif not cfg.get("host"):
        raise RuntimeError("Aucun serveur d'envoi configuré.")

    def _login(s):
        if cfg["user"] and cfg["password"]:
            s.login(cfg["user"], cfg["password"])

    if cfg["tls"] == "ssl":
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                              context=ssl.create_default_context(), timeout=20) as s:
            _login(s)
            s.send_message(msg)
    elif cfg["tls"] == "none":
        # Relais interne sans chiffrement : les identifiants passeraient en
        # clair, a ne faire que sur un reseau de confiance.
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            _login(s)
            s.send_message(msg)
    else:
        # STARTTLS : on ouvre en clair puis on chiffre AVANT de s'authentifier.
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            _login(s)
            s.send_message(msg)


def destinataires_alerte(name, apps=None):
    """A qui adresser l'alerte de cette application.

    Chaque application declare ses propres destinataires -- une application
    de facturation ne previent pas les memes personnes qu'un site vitrine,
    et une liste unique obligeait a prevenir tout le monde ou personne.
    L'adresse d'administration s'y ajoute TOUJOURS : c'est le filet, celui
    qui garantit qu'une application dont on a oublie de remplir la liste ne
    tombe pas en silence.
    """
    apps = apps if apps is not None else load()
    propres = _adresses((reglage("alertes_par_app", {}) or {}).get(name))
    vus, tout = set(), []
    for adresse in alertes_admin() + propres:
        if adresse not in vus:
            vus.add(adresse)
            tout.append(adresse)
    return tout


def envoyer_avec_repli(sujet, corps, destinataires, sensible=False):
    """Envoie, en repassant sur la configuration d'origine si besoin.

    La configuration personnalisee a ete verifiee le jour ou elle a ete
    enregistree, mais un mot de passe se revoque, un quota se remplit, un
    serveur s'eteint. Ce jour-la, celle de credentials.env -- qui n'a jamais
    bouge -- reprend le relais plutot que de laisser l'alerte a terre.

    Renvoie (envoye, detail).
    """
    personnalisee = lire_smtp_personnalise()
    tentatives = []
    if personnalisee:
        tentatives.append(("personnalisee", personnalisee))
    origine = smtp_origine()
    if not smtp_incomplet(origine) and origine != personnalisee:
        tentatives.append(("d'origine", origine))
    if not tentatives:
        return False, "aucune configuration d'envoi utilisable"

    echecs = []
    for nom, cfg in tentatives:
        if smtp_incomplet(cfg):
            continue
        try:
            envoyer_mail(cfg, sujet, corps, destinataires=destinataires, sensible=sensible)
            if echecs:
                print(f"[app-manager] envoi repli sur la configuration {nom} "
                      f"apres : {' ; '.join(echecs)}", flush=True)
            return True, nom
        except Exception as e:
            echecs.append(f"{nom} -> {type(e).__name__}: {e}")
    return False, " ; ".join(echecs)


def alerter(sujet, corps, name=None, apps=None):
    """Envoi best-effort depuis le moniteur.

    Ne leve jamais et ne bloque jamais le moniteur : une alerte qui ne part
    pas ne doit pas ajouter une panne a celle qu'elle signale. Renvoie True
    si le mail est parti.
    """
    if not lire_alertes()["actif"]:
        return False
    cibles = destinataires_alerte(name, apps) if name else alertes_admin()
    if not cibles:
        print("[app-manager] alerte non envoyee : aucun destinataire "
              "(ni adresse d'administration, ni adresse propre a "
              f"l'application {name or '?'})", flush=True)
        return False
    envoye, detail = envoyer_avec_repli(sujet, corps, cibles)
    if envoye:
        print(f"[app-manager] alerte envoyee a {len(cibles)} destinataire(s) "
              f"par la configuration {detail} : {sujet}", flush=True)
    else:
        print(f"[app-manager] alerte non envoyee ({detail})", flush=True)
    return envoye


def fin_du_journal(name, lignes=ALERTE_LOG_LIGNES):
    """Les dernieres lignes du journal d'une application.

    Lues depuis la fin : un journal de 2 Mo ne doit pas etre charge en
    memoire pour en extraire vingt lignes.
    """
    chemin = os.path.join(LOG_DIR, name + ".log")
    try:
        taille = os.path.getsize(chemin)
        with open(chemin, "rb") as f:
            f.seek(max(0, taille - 8192))
            texte = f.read().decode("utf-8", "replace")
    except OSError:
        return "(journal illisible)"
    fin = texte.splitlines()[-lignes:]
    return "\n".join(fin) or "(journal vide)"


def corps_alerte_chute(name, a):
    return "\n".join([
        f"L'application « {name} » ne répond plus.",
        "",
        f"Dossier   : {a.get('path', '?')}",
        f"Commande  : {a.get('command', '?')}",
        f"Port      : {a.get('port', '?')}",
        f"État      : arrêtée après {RESTART_MAX_ATTEMPTS} tentatives de "
        f"redémarrage en {RESTART_WINDOW // 60} minutes",
        "",
        f"Panneau   : {read_shared_value('APP_MANAGER_URL') or 'http://<IP-du-serveur>:9001'}/",
        "",
        f"Fin du journal ({ALERTE_LOG_LIGNES} dernières lignes)",
        "-" * 46,
        fin_du_journal(name),
        "",
        "-- CodeLab, panneau de gestion des applications",
    ])


def corps_alerte_retour(name):
    return "\n".join([
        f"L'application « {name} » répond de nouveau.",
        "",
        f"Panneau : {read_shared_value('APP_MANAGER_URL') or 'http://<IP-du-serveur>:9001'}/",
        "",
        "-- CodeLab, panneau de gestion des applications",
    ])


# Applications pour lesquelles une alerte de chute a deja ete envoyee. Sans
# cette memoire, le moniteur reexpedierait le meme mail toutes les dix
# secondes tant que l'application reste a terre -- une boite pleine, et une
# alerte qu'on finit par ignorer. Une entree disparait quand l'application
# repart (mail de retour) ou quand elle est arretee volontairement.
_alertes_en_cours = set()


def alerte_tick():
    """Compare l'etat des applications a celui du tour precedent, et envoie
    un mail sur les deux transitions qui comptent : tombee, puis revenue.

    Volontairement fonde sur is_crash_looping() et non sur "le process est
    mort" : une application qui plante et redemarre toute seule dans la
    seconde n'est pas un incident, c'est le filet de securite qui fonctionne.
    L'incident commence quand le panneau a epuise ses tentatives.
    """
    apps = load()
    for name in list(_alertes_en_cours):
        if name not in apps or not apps[name].get("enabled"):
            # Supprimee ou arretee a la main : l'incident est clos, sans mail
            # de retour -- personne n'a besoin d'etre prevenu d'une action
            # qu'il vient de faire lui-meme.
            _alertes_en_cours.discard(name)
        elif is_running(name) and not is_crash_looping(name):
            _alertes_en_cours.discard(name)
            alerter(f"[CodeLab] {name} est revenue", corps_alerte_retour(name),
                    name=name, apps=apps)
            notifier_compte(NOM_ADMIN, f"{titre_de(name, apps.get(name))} est revenue",
                            "L'application répond de nouveau.",
                            url="/applications/" + urllib.parse.quote(name), etiquette="app-" + name)

    for name, a in apps.items():
        if (a.get("enabled") and not is_running(name) and is_crash_looping(name)
                and name not in _alertes_en_cours):
            _alertes_en_cours.add(name)
            alerter(f"[CodeLab] {name} est tombee", corps_alerte_chute(name, a),
                    name=name, apps=apps)
            notifier_compte(NOM_ADMIN, f"{titre_de(name, a)} est tombée",
                            f"Arrêtée après {RESTART_MAX_ATTEMPTS} tentatives de redémarrage.",
                            url="/applications/" + urllib.parse.quote(name),
                            etiquette="app-" + name, urgence="high")
