"""
CodeLab -- TESTS : LES FIXTURES
---------------------------------------------------------------------------
ROLE        les fixtures de la suite, dont le filet (_bac_a_sable, autouse)
            qui detourne tous les chemins d'etat avant chaque test.
CHARGE PAR  pytest : python3 -m pytest workspace/diagnostic/tests -q
            (depuis la racine du depot), la CI, et la page /tests
            du diagnostic.
CONTRAINTES Les aides, constantes et modules viennent de commun.py ; les
            fixtures, et le filet qui detourne TOUS les chemins d'etat
            avant chaque test, de conftest.py. Une aide nouvelle va dans
            commun.py, une fixture dans conftest.py, un test dans le
            fichier de son sujet. Aucun test ne lit /var/lib/codelab ni
            /workspace reels.
            pytest charge ce fichier de lui-meme : ses fixtures valent pour
            tous les fichiers de tests/.
"""

from commun import *  # noqa: F401,F403 -- aides, constantes, modules


# ==========================================================================
#                        TESTS DU PANNEAU (app-manager)
# ==========================================================================
#
# La suite de regressions du panneau vit ici, avec l'etat des lieux, pour une
# raison simple : c'est le seul endroit ou elle est lancable des DEUX cotes
# -- par la CI avant qu'une image ne parte, et depuis l'installation qui
# tourne, par la page /tests du diagnostic.
#
#     python -m pytest workspace/diagnostic/tests -q
#
# CE QUI REND CELA SANS DANGER, ET QU'IL FAUT LIRE AVANT DE TOUCHER A LA
# SUITE. Ces tests ecrivent : ils enregistrent des applications, creent des
# comptes, posent des cles d'acces. Tant qu'ils vivaient dans un dossier de
# tests, on pouvait admettre que leurs fixtures ne redirigent pas TOUS les
# chemins -- le fixture "client", par exemple, ne redirigeait pas APPS_FILE.
# Ici, ils peuvent tourner dans une installation reelle, ou ces chemins
# existent : sans precaution, le premier lancement remplacerait tes
# applications et tes comptes par des donnees de test.
#
# D'ou le filet ci-dessous : AVANT chaque test, sans exception et sans que
# personne ait a y penser, tous les chemins d'etat du panneau sont detournes
# vers un dossier jetable, et l'on verifie qu'aucun ne pointe encore vers le
# vrai. Un test qui oublierait de rediriger quelque chose ne peut plus faire
# de degat -- et si le filet lui-meme se cassait, les tests s'arretent au lieu
# de s'executer.


@pytest.fixture(autouse=True)
def _bac_a_sable(tmp_path, monkeypatch):
    """Le filet. autouse : il s'applique a TOUS les tests, y compris ceux
    ecrits demain par quelqu'un qui n'aura pas lu ce commentaire."""
    # Noms prefixes : quatre tests creent deja leur propre tmp_path/"etat",
    # et se heurtaient a celui du filet ("File exists"). Le bac du filet ne
    # doit marcher sur les pieds de personne.
    etat = tmp_path / "_filet-etat"
    partage = tmp_path / "_filet-config"
    travail = tmp_path / "_filet-workspace"
    etat.mkdir()
    partage.mkdir()
    travail.mkdir()
    remplacements = {
        "STATE_DIR": str(etat),
        "LOG_DIR": str(etat / "logs"),
        "CHILD_HOME": str(etat / "home"),
        "SHARED_CONFIG_DIR": str(partage),
        "SHARED_ENV_FILE": str(partage / "credentials.env"),
        "REPLI_ADMIN_PASSWORD_FILE": str(etat / "admin_password"),
        "REPLI_SECRET_KEY_FILE": str(etat / "flask_secret_key"),
        "SSO_PRIVATE_FILE": str(etat / "codelab_sso_private.key"),
        "DB_FILE": str(etat / "codelab.db"),
        "SAUVEGARDES_SUIVI_FILE": str(etat / "sauvegardes-suivi.json"),
    }
    for nom in CHEMINS_ETAT:
        assert hasattr(app, nom), (
            f"{nom} a disparu du panneau : le filet ne couvre plus tout, "
            f"mets CHEMINS_ETAT a jour avant de continuer")
        monkeypatch.setattr(app, nom, remplacements[nom])
    # Ceinture : aucun chemin ne doit plus designer une installation reelle.
    for nom in CHEMINS_ETAT:
        valeur = getattr(app, nom)
        assert str(tmp_path) in valeur, f"{nom} pointe encore vers {valeur}"
    # Le cache du registre garde les valeurs de l'ancien chemin.
    app._apps_cache["signature"] = None
    # isolement_disponible() garde son verdict, et il LANCE UN PROCESS
    # pour le calculer. Deux raisons de le fixer ici : un test qui
    # simule un noyau qui refuse ne doit pas contaminer les suivants,
    # et aucun test ne doit lancer unshare sans le vouloir -- ceux qui
    # remplacent Popen s'y casseraient. Repli par defaut, donc : la
    # valeur sure. Les trois tests qui portent sur la detection
    # elle-meme remettent None, ceux qui veulent la branche isolee
    # posent True.
    app._isolement.update(verdict=False, raison="fixe par le filet")
    # Le second facteur obligatoire de l'administrateur est eteint par
    # defaut ICI SEULEMENT : des centaines de tests se connectent en
    # administrateur avec le mot de passe, et parlent d'autre chose. Les
    # tests de ce facteur le rallument (voir « admin_2fa »).
    monkeypatch.setenv("APP_MANAGER_ADMIN_2FA", "0")
    # Sans serveur d'envoi, aucun compte ne se cree. Des dizaines de tests
    # creent des comptes et parlent d'autre chose : le filet fait comme si
    # l'envoi etait regle. Les tests de cette regle remettent le vrai.
    monkeypatch.setattr(app, "envoi_mail_configure", lambda: True)
    # Le coffre : une cle neuve par test, et rien en memoire d'un test a
    # l'autre -- sinon un reglage ecrit par l'un se lirait dans le suivant.
    from cryptography.fernet import Fernet
    monkeypatch.setattr(app, "_cle_chiffrement", Fernet.generate_key().decode())
    app._coffre["donnees"] = None
    yield
    app._coffre["donnees"] = None





@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    # Un test qui active le second facteur ecrit dans credentials.env et pose
    # un secret global : sans ces deux lignes il ecrirait le VRAI fichier de
    # la machine, et laisserait la 2FA active pour les tests suivants.
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(app, "_totp_secret", "")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()





@pytest.fixture
def alertes(tmp_path, monkeypatch):
    """Un panneau avec une application declaree et les alertes actives."""
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    app._apps_cache["signature"] = None
    app._alertes_en_cours.clear()
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n")
    app.ecrire_alertes(True, ["moi@example.com"])
    app.save({"site": {"path": "/workspace/site", "command": "python3 app.py",
                       "port": 9101, "enabled": True}})
    envoyes = []
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None, **_:
                        envoyes.append(sujet))
    return envoyes





@pytest.fixture
def deux_espaces(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)   # 200 000 par test, c'est long
    monkeypatch.setattr(app, "is_running", lambda n: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({
        "prive-autorise": {"path": "/w/a", "command": "x", "port": 9101,
                           "enabled": True, "visibility": "privee"},
        "prive-refuse": {"path": "/w/b", "command": "x", "port": 9102,
                         "enabled": True, "visibility": "privee"},
        "public": {"path": "/w/c", "command": "x", "port": 9103,
                   "enabled": True, "visibility": "publique"},
    })
    sel = "aa" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["prive-autorise"], "cree": 0}})
    return app.flask_app.test_client()





@pytest.fixture
def compte_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "ancien-mot-de-passe")
    monkeypatch.setattr(app, "_totp_secret", "")
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app.ecrire_bloc_panneau("ancien-mot-de-passe", "cle-de-session-existante", "")
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "ancien-mot-de-passe"})
    return c





@pytest.fixture
def acces(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"facturation": {"path": "/w/f", "command": "x", "port": 9101,
                              "enabled": True, "visibility": "privee"},
              "vitrine": {"path": "/w/v", "command": "x", "port": 9102,
                          "enabled": True, "visibility": "publique"}})
    sel = "cc" * 16
    app.ecrire_utilisateurs({
        "marie": {"sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
                  "projets": ["vitrine"], "cree": 0, "email": "marie@example.com"},
        "paul": {"sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
                 "projets": [], "cree": 0},
    })
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c





@pytest.fixture
def messagerie(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app._dernier_message.clear()
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n"
        "ALERTE_ADMIN=chef@example.com\n")
    app.save({"compta": {"path": "/w/a", "command": "x", "port": 9101,
                         "enabled": True, "visibility": "privee"},
              "secret": {"path": "/w/c", "command": "x", "port": 9103,
                         "enabled": True, "visibility": "privee"}})
    sel = "ff" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["compta"], "cree": 0}})
    partis = []
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None, **_:
                        partis.append((destinataires, sujet, corps)))
    c = app.flask_app.test_client()
    _connecte(c, "marie", "mot-de-passe-long")
    return c, partis





@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"compta": {"path": "/w/a", "command": "x", "port": 9101,
                         "enabled": True, "visibility": "privee"},
              "vitrine": {"path": "/w/b", "command": "x", "port": 9102,
                          "enabled": True, "visibility": "publique"},
              "secret": {"path": "/w/c", "command": "x", "port": 9103,
                         "enabled": True, "visibility": "privee"}})
    sel = "dd" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["compta"], "cree": 0}})
    return app.flask_app.test_client()




@pytest.fixture
def logo(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    projet = tmp_path / "site"
    projet.mkdir()
    app.save({"site": {"path": str(projet), "command": "x", "port": 9101,
                       "enabled": False}})
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c, projet





@pytest.fixture
def en_marche(tmp_path, monkeypatch):
    """Une application qui tourne, et dont on peut editer la configuration."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"site": {"path": "/w/a", "command": "python3 app.py", "port": 9101,
                       "enabled": True, "description": "avant",
                       "max_memory_mb": 256}})
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c





@pytest.fixture
def categorise(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    # Arretee : ces tests-la ne parlent pas du redemarrage, et une
    # application a l'arret repond sans bandeau.
    monkeypatch.setattr(app, "is_running", lambda n: False)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"site": {"path": "/w/a", "command": "x", "port": 9101,
                       "enabled": True, "categorie": "Outils"}})
    app.ecrire_categories(["Outils", "Donnees"])
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c





@pytest.fixture
def comptes_mail(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({})
    app.ecrire_utilisateurs({})
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n")
    partis = []
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None, **_:
                        partis.append((destinataires, corps)))
    return app.flask_app.test_client(), partis





@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app._dernier_acces.clear()
    app.save({"prive": {"path": "/w/a", "command": "x", "port": 9101,
                        "enabled": True, "visibility": "privee"},
              "public": {"path": "/w/b", "command": "x", "port": 9102,
                         "enabled": True, "visibility": "publique"}})
    sel = "cc" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": [], "cree": 0, "totp": app.totp_nouveau_secret()}})
    return app.flask_app.test_client()





@pytest.fixture
def survivants(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app, "ISOLER_APPS", False)
    app._apps_cache["signature"] = None
    projet = tmp_path / "projet"
    projet.mkdir()
    app.save({"site": {"path": str(projet), "command": "sleep 120",
                       "port": 9399, "enabled": False}})
    app.procs.clear()
    yield tmp_path
    for nom in list(app.procs):
        try:
            app.stop(nom)
        except Exception:                                         # noqa: BLE001
            pass
    app.procs.clear()





@pytest.fixture
def exposition(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.delenv("APP_MANAGER_PUBLIC_URL", raising=False)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"deja-public": {"path": "/w/a", "command": "x", "port": 9101,
                              "enabled": True, "visibility": "publique"},
              "prive": {"path": "/w/b", "command": "x", "port": 9102,
                        "enabled": True, "visibility": "privee"}})
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c





@pytest.fixture
def cles(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "trust_proxy", lambda: False)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()





@pytest.fixture
def envoi(tmp_path, monkeypatch):
    """Un panneau connecte, avec une configuration d'envoi D'ORIGINE posee
    dans credentials.env -- celle qui doit survivre a tout."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app._alertes_en_cours.clear()
    app.ecrire_bloc_alertes({"SMTP_HOST": "origine.example.com",
                             "SMTP_USER": "origine@example.com",
                             "SMTP_PASSWORD": "mot-de-passe-origine"})
    app.ecrire_alertes(False, [])
    app.save({"site": {"path": "/workspace/site", "command": "python3 app.py",
                       "port": 9101, "enabled": True}})
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c





@pytest.fixture
def sauvegardes(tmp_path, monkeypatch):
    """Un dossier d'etat a sauvegarder, et un dossier ou ecrire."""
    etat = tmp_path / "etat"
    (etat / "logs").mkdir(parents=True)
    (etat / "utilisateurs.json").write_text('{"marie": {}}')
    (etat / "apps.json").write_text('{"demo": {}}')
    (etat / "acces.jsonl").write_text('{"genre": "connexion"}\n')
    (etat / "logs" / "demo.log").write_text("x" * 5000)
    (etat / "home" / "12302").mkdir(parents=True)
    (etat / "home" / "12302" / "cache").write_text("x" * 5000)
    cible = tmp_path / "sauvegardes"
    cible.mkdir()
    monkeypatch.setattr(app, "STATE_DIR", str(etat))
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(cible))
    return etat, cible





@pytest.fixture
def deux_origines(deux_espaces, monkeypatch):
    monkeypatch.setattr(app, "APPS_PORT", 9302)
    monkeypatch.setitem(app._origines_separees, "actif", True)
    return deux_espaces





@pytest.fixture
def config_isolee(tmp_path, monkeypatch):
    """Un credentials.env a nous, et aucune variable heritee de la machine."""
    fichier = tmp_path / "credentials.env"
    fichier.write_text("POSTGRES_USER=codelab\n")
    monkeypatch.setattr(checks, "ENV_FILE", str(fichier))
    monkeypatch.setattr(checks, "PROJET_ENV", str(tmp_path / ".env"))
    for cle in CLES_MOT_DE_PASSE_PG:
        monkeypatch.delenv(cle, raising=False)
    return fichier




@pytest.fixture
def faux_panneau(monkeypatch):
    """Un panneau minuscule, sur un vrai port : la sonde fait de vraies
    requetes, c'est tout son interet."""
    import http.server
    import threading

    reponses = {"/health": 200}

    class _Poignee(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                          # noqa: N802
            code = reponses.get(self.path, 401)
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    serveur = http.server.HTTPServer(("127.0.0.1", 0), _Poignee)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    monkeypatch.setenv("CODELAB_PANNEAU_HOTE", "127.0.0.1")
    monkeypatch.setenv("MANAGER_PORT", str(serveur.server_address[1]))
    monkeypatch.setenv("APP_MANAGER_APPS_PORT", str(serveur.server_address[1]))
    try:
        yield reponses
    finally:
        serveur.shutdown()
        serveur.server_close()




@pytest.fixture
def asset_diagnostic(monkeypatch):
    """definitions.py charge avec un faux dagster, et sa base remplacee."""
    import types
    faux = types.ModuleType("dagster")

    def _decorateur(*_args, **_kwargs):
        return lambda fonction: fonction

    class _Statut:
        RUNNING = "RUNNING"

    class _Metadonnee:
        @staticmethod
        def md(texte):
            return texte

    faux.AssetExecutionContext = object
    faux.RunFailureSensorContext = object
    faux.Definitions = lambda **kw: kw
    faux.ScheduleDefinition = lambda **kw: kw
    faux.define_asset_job = lambda **kw: kw
    faux.DefaultScheduleStatus = _Statut
    faux.DefaultSensorStatus = _Statut
    faux.MetadataValue = _Metadonnee
    faux.Failure = type("Failure", (Exception,), {})
    faux.asset = _decorateur
    faux.run_failure_sensor = _decorateur
    monkeypatch.setitem(sys.modules, "dagster", faux)

    chemin = os.path.join(DOSSIER_DIAGNOSTIC, "definitions.py")
    spec = importlib.util.spec_from_file_location("codelab_definitions_test", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # La base : ecrite pour de bon nulle part, mais la chaine complete est
    # jouee -- connexion, ecriture, fermeture.
    ecrit = {}

    class _Connexion:
        def close(self):
            ecrit["fermee"] = True

    monkeypatch.setattr(checks, "connect_pg", lambda *a, **k: _Connexion())
    monkeypatch.setattr(checks, "write_heartbeat",
                        lambda _conn, source, detail: ecrit.setdefault("detail", detail) and 0 or 7)
    module.ecrit = ecrit
    return module





@pytest.fixture
def a_renommer(tmp_path, monkeypatch):
    """Un panneau avec une application, un compte qui y a droit, un journal."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    os.makedirs(str(tmp_path / "logs"), exist_ok=True)
    projet = tmp_path / "projet"
    projet.mkdir()
    app._apps_cache["signature"] = None
    app.save({
        "site": {"path": str(projet), "command": "python3 app.py", "port": 9401,
                 "enabled": False, "description": "avant", "alertes": ["moi@example.com"]},
        "autre": {"path": str(projet), "command": "python3 b.py", "port": 9402,
                  "enabled": False},
    })
    app.ecrire_utilisateurs({"marie": {"sel": "x", "hash": "y", "projets": ["site", "autre"]}})
    app.ecrire_masquees({"marie": ["site"]})
    (tmp_path / "logs" / "site.log").write_text("une ligne de journal\n")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c, tmp_path





@pytest.fixture
def deux_comptes(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    app._apps_cache["signature"] = None
    app.save({"prive": {"path": "/w/p", "command": "x", "port": 9501,
                        "visibility": "privee"}})
    sel = "abcdef"
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("un-mot-de-passe", sel),
        "projets": ["prive"], "totp": "JBSWY3DPEHPK3PXP"}})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()





@pytest.fixture
def hub_dagster(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    # Le port de Dagster est sonde pour de vrai : on repond a sa place.
    monkeypatch.setattr(app, "dagster_repond", lambda: True)
    app._apps_cache["signature"] = None
    app.save({"site": {"path": "/w/s", "command": "x", "port": 9701,
                       "visibility": "privee"}})
    sel = "a1b2c3d4"      # le sel se lit en hexadecimal, pas en toutes lettres
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("un-mot-de-passe", sel),
        "projets": ["site"], "totp": "JBSWY3DPEHPK3PXP"}})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()





@pytest.fixture
def boite(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    app._apps_cache["signature"] = None
    app.save({})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c, tmp_path




@pytest.fixture
def pg_reel(tmp_path):
    """Un vrai PostgreSQL, ephemere, monte comme celui de CodeLab : un
    super-utilisateur « codelab », les bases dagster et codelab. Ignore si la
    machine n'a pas de serveur Postgres (les runners GitHub en ont un)."""
    import subprocess, socket
    binaires = _initdb()
    if not binaires or not shutil.which("psql"):
        pytest.skip("serveur PostgreSQL absent")
    dossier = tmp_path / "pg"
    dossier.mkdir()
    en_root = os.geteuid() == 0
    prefixe = ["runuser", "-u", "postgres", "--"] if en_root else []
    if en_root:
        shutil.chown(dossier, "postgres")
        os.chmod(tmp_path, 0o755)
        for parent in tmp_path.parents:
            try:
                os.chmod(parent, os.stat(parent).st_mode | 0o001)
            except OSError:
                pass
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    r = subprocess.run(prefixe + [f"{binaires}/initdb", "-D", str(dossier / "data"), "-U", "codelab",
                                  "--auth-local=trust", "--auth-host=scram-sha-256"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip(f"initdb impossible : {r.stderr[:200]}")
    subprocess.run(prefixe + [f"{binaires}/pg_ctl", "-D", str(dossier / "data"), "-w", "-l",
                              str(dossier / "log"), "-o",
                              f"-p {port} -k {dossier} -c listen_addresses=127.0.0.1", "start"],
                   check=True, capture_output=True, timeout=120)
    env = dict(os.environ, PGHOST="127.0.0.1", PGPORT=str(port), PGUSER="codelab")
    env.pop("PGPASSWORD", None)

    def psql(*args, utilisateur="codelab", mdp=None, base="dagster", entree=None, extra=None):
        e = dict(env, PGHOST=str(dossier) if mdp is None and utilisateur == "codelab" else "127.0.0.1",
                 PGUSER=utilisateur, **(extra or {}))
        if mdp is not None:
            e["PGPASSWORD"] = mdp
        return subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-Atq", "-d", base, *args],
                              env=e, capture_output=True, text=True, timeout=60, input=entree)
    psql("-c", "ALTER ROLE codelab PASSWORD 'cle-admin'", base="postgres")
    for b in ("dagster", "codelab"):
        psql("-c", f"CREATE DATABASE {b}", base="postgres")
    yield {"port": port, "socket": str(dossier), "psql": psql, "cle": "cle-admin"}
    subprocess.run(prefixe + [f"{binaires}/pg_ctl", "-D", str(dossier / "data"), "-m", "fast", "stop"],
                   capture_output=True, timeout=120)




@pytest.fixture
def invitations(comptes_mail):
    c, partis = comptes_mail
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_ADMIN, utilisateur=app.NOM_ADMIN,
                 ouverte=int(time.time()), jeton="j")
    return c, partis, {app.JETON_ENTETE: "j"}





@pytest.fixture
def admin_2fa(journal, monkeypatch):
    monkeypatch.setenv("APP_MANAGER_ADMIN_2FA", "1")
    monkeypatch.setattr(app, "_totp_secret", "")
    monkeypatch.setattr(app, "ecrire_bloc_panneau", lambda pw, key, totp: True)
    return journal





@pytest.fixture
def appli_locale():
    import threading
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    vu = {"connexions": set(), "requetes": []}

    class Gestion(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _repondre(self):
            n = int(self.headers.get("Content-Length") or 0)
            corps = self.rfile.read(n) if n else b""
            vu["connexions"].add(self.client_address)
            vu["requetes"].append((self.command, self.path, corps))
            cible = {"/interne": "/arrivee", "/dehors": "https://accounts.example/o",
                     "/absolue": "http://127.0.0.1:%d/arrivee" % self.server.server_port,
                     "/temporaire": "/arrivee"}.get(self.path.split("?")[0])
            if cible:
                self.send_response(307 if self.path == "/temporaire" else 302)
                self.send_header("Location", cible)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            reponse = (self.command + " " + self.path).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(reponse)))
            self.end_headers()
            self.wfile.write(reponse)
            if self.path == "/ferme":
                # Annonce une connexion gardee, puis la coupe : ce que fait
                # une application qui redemarre entre deux requetes.
                self.close_connection = True

        do_GET = do_POST = do_HEAD = _repondre

        def log_message(self, *a):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Gestion)
    serveur.daemon_threads = True
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    vu["port"] = serveur.server_port
    yield vu
    serveur.shutdown()
    serveur.server_close()
    app._connexions_libres.pop(vu["port"], None)





@pytest.fixture
def presentation(hub, tmp_path):
    dossiers = {}
    apps = app.load()
    for nom in ("compta", "secret"):
        d = tmp_path / nom
        (d / "video").mkdir(parents=True)
        (d / "video" / "presentation.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 4000)
        apps[nom]["path"] = str(d)
        dossiers[nom] = d
    (dossiers["compta"] / "video" / "affiche.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    app.save(apps)
    return hub, dossiers




@pytest.fixture
def archives_sauvegarde(hub, tmp_path, monkeypatch):
    dossier = tmp_path / "sauvegardes"
    dossier.mkdir()
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(dossier))
    monkeypatch.setattr(app, "SAUVEGARDES_SUIVI_FILE", str(tmp_path / "suivi.json"))
    (dossier / "panneau-20261010-030000.tar.gz").write_bytes(b"ancienne")
    os.utime(dossier / "panneau-20261010-030000.tar.gz", (1, 1))
    (dossier / "panneau-20261011-030000.tar.gz").write_bytes(b"x" * 2048)
    (dossier / "workspace-20261011-031000.tar.gz").write_bytes(b"w" * 4096)
    (dossier / "notes.txt").write_text("pas une sauvegarde")
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return admin, dossier





@pytest.fixture
def appareils(hub, monkeypatch):
    app._sid_revoques.clear()
    app._sid_controles.clear()
    yield hub
    app._sid_revoques.clear()
    app._sid_controles.clear()




@pytest.fixture
def envois_push(monkeypatch):
    envois = _Envois()
    envois.code = 201

    class _Immediat:
        def __init__(self, target=None, daemon=None, args=(), **k):
            self.f, self.a = target, args

        def start(self):
            self.f(*self.a)

    class _Reponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def urlopen(req, timeout=None):
        envois.append(req)
        if envois.code >= 400:
            raise urllib.error.HTTPError(req.full_url, envois.code, "refus", {}, None)
        return _Reponse()

    monkeypatch.setattr(app.threading, "Thread", _Immediat)
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    return envois
