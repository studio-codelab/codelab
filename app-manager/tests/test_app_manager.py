"""Tests de l'app-manager -- uniquement ce qui protege une regression grave.

Chacun de ces tests correspond a un bug ou une faille reellement rencontres :
ils ne decrivent pas le comportement de l'application, ils empechent cinq
problemes precis de revenir sans qu'on s'en apercoive. Un test qui ne
repondrait pas a cette definition n'a pas sa place ici.

  1. la suggestion de commande, qui proposait "npm start" a un projet Vite ;
  2. le bornage des chemins a /workspace ;
  3. l'authentification du panneau et sa limite de tentatives ;
  4. l'isolation de ce que le panneau lance : privileges abandonnes, cookie
     de session non transmis ;
  5. l'inscription automatique du projet de diagnostic, qui ne doit jamais
     rejouer -- un projet supprime qui revient au redemarrage suivant.

Portee : ce qui se verifie sans conteneur, sans Postgres et sans reseau. Le
cycle de vie des process et le reverse proxy demandent une stack en marche et
se verifient a la main. Le module s'importe sans effet de bord, tout le
demarrage vivant derriere if __name__ == "__main__".

    python -m pytest app-manager/tests -q
"""
import base64
import importlib.util
import json
import os
import sys
import time

import pytest

# tests/ vit dans le service qu'il couvre : le module teste est le voisin
# d'a cote, app-manager/app/app.py.
SERVICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _charger_app():
    chemin = os.path.join(SERVICE, "app", "app.py")
    spec = importlib.util.spec_from_file_location("codelab_app_manager", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


app = _charger_app()


def _projet(tmp_path, fichiers):
    for nom, contenu in fichiers.items():
        cible = tmp_path / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu)
    return str(tmp_path)


# ------------------ 0. cache du registre d'applications ------------------
#
# load() est mis en cache pour eviter une lecture disque par requete
# proxifiee. Le risque du cache est de servir un registre perime : ces deux
# tests tiennent la seule propriete qui compte, "une modification est vue".

def test_le_registre_est_relu_quand_le_fichier_change(tmp_path, monkeypatch):
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None

    app.save({"un": {"port": 9101}})
    assert list(app.load()) == ["un"]

    # Ecriture exterieure, sans passer par save() : le panneau documente
    # d'editer apps.json a la main, et un job Dagster pourrait le faire.
    fichier.write_text(json.dumps({"deux": {"port": 9102}}))
    assert list(app.load()) == ["deux"]


def test_modifier_le_registre_recu_ne_corrompt_pas_le_cache(tmp_path, monkeypatch):
    """Les appelants modifient ce que load() renvoie avant de le repasser a
    save() : sans copie, ces modifications apparaitraient dans le cache avant
    l'ecriture -- et y resteraient meme si elle echouait."""
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None
    app.save({"un": {"port": 9101, "enabled": True}})

    registre = app.load()
    registre["un"]["enabled"] = False
    registre["intrus"] = {"port": 9999}

    relu = app.load()
    assert relu["un"]["enabled"] is True
    assert "intrus" not in relu


# ----------------------------- 1. detection -----------------------------

def test_un_projet_vite_est_construit_puis_servi_en_statique(tmp_path):
    """Le bug d'origine : un projet Vite n'a pas de script "start". La
    suggestion proposait quand meme "npm start", l'application etait declaree
    puis echouait au demarrage."""
    chemin = _projet(tmp_path, {
        "package.json": json.dumps({"scripts": {"build": "vite build"},
                                    "devDependencies": {"vite": "^5"}}),
        "package-lock.json": "{}",
    })
    commande, build = app.detect_project(chemin)
    assert commande == "python3 -m http.server $PORT --directory dist"
    assert build == "npm ci && npm run build"


def test_aucune_commande_ne_code_le_port_en_dur(tmp_path):
    """Le port est attribue par l'app-manager et injecte en $PORT. Un numero
    ecrit en dur se desynchronise des que le port change."""
    cas = [
        {"index.html": "<html>"},
        {"manage.py": ""},
        {"package.json": json.dumps({"scripts": {"build": "vite build"},
                                     "devDependencies": {"vite": "^5"}})},
        {"package.json": json.dumps({"scripts": {"build": "next build"},
                                     "dependencies": {"next": "14"}})},
    ]
    for i, fichiers in enumerate(cas):
        dossier = tmp_path / f"cas{i}"
        dossier.mkdir()
        commande, _ = app.detect_project(_projet(dossier, fichiers))
        assert "$PORT" in commande, commande


# --------------------------- 2. bornage des chemins ---------------------------

def test_les_chemins_restent_dans_le_workspace():
    assert app.under_root(os.path.join(app.ROOT, "mon-projet"))
    assert not app.under_root("/etc")
    assert not app.under_root(os.path.join(app.ROOT, "..", "etc"))
    # Un dossier voisin dont le nom commence comme la racine ne doit pas
    # passer pour un enfant : "/workspace-bis" n'est pas dans "/workspace".
    assert not app.under_root(app.ROOT + "-bis")


def test_un_nom_de_projet_ne_peut_pas_porter_de_separateur():
    """Le nom sert a construire des chemins de journaux et des routes."""
    assert app.valid_name("../../etc/passwd") == "etc-passwd"


# --------------------------- 3. authentification ---------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()


def test_les_routes_api_refusent_sans_session(client):
    assert client.get("/api/apps").status_code == 401
    assert client.post("/api/toggle/quelconque").status_code == 401
    assert client.post("/login", json={"password": "secret-de-test"}).status_code == 200
    assert client.get("/api/apps").status_code == 200


def test_la_limite_de_tentatives_ne_se_contourne_pas_par_en_tete(client):
    """X-Forwarded-For est pose par le client quand le service est publie
    directement : le faire varier donnait un compteur neuf a chaque essai, ce
    qui annulait la limite."""
    assert not app.TRUST_PROXY, "APP_MANAGER_TRUST_PROXY ne doit pas etre actif par defaut"
    for i in range(app.RATE_LIMIT_MAX):
        assert client.post("/login", json={"password": "faux"},
                           headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 401
    assert client.post("/login", json={"password": "faux"},
                       headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429


def test_le_point_d_appui_de_dagster_repond_par_un_code_sans_page(client):
    """nginx interroge cette route avant chaque requete vers Dagster : il lui
    faut un code, pas une redirection. Si elle se mettait a repondre 302
    comme les autres routes protegees, nginx la lirait comme un refus et
    Dagster deviendrait inaccessible meme connecte."""
    r = client.get("/api/auth-check")
    assert r.status_code == 401 and not r.data

    client.post("/login", json={"password": "secret-de-test"})
    r = client.get("/api/auth-check")
    assert r.status_code == 204 and not r.data


def test_le_cookie_de_session_est_samesite_lax(client):
    """Les actions du panneau sont des POST sans corps : sans SameSite, un
    formulaire pose sur un autre site peut les declencher avec le cookie de
    l'utilisateur connecte."""
    r = client.post("/login", json={"password": "secret-de-test"})
    cookie = r.headers.get("Set-Cookie", "")
    assert "SameSite=Lax" in cookie and "HttpOnly" in cookie


# ------------------- 3 bis. visibilite et second facteur -------------------
#
# Deux reglages destines a une exposition hors du reseau local. Ils sont
# inactifs par defaut : ces tests tiennent surtout le fait qu'ils ne changent
# rien tant qu'on ne les active pas, et qu'ils ferment bien une fois actifs.

def test_une_application_privee_exige_la_session(client, monkeypatch, tmp_path):
    """Le controle vit dans le proxy, pas dans l'interface : un lien qui
    circule ne doit pas suffire a ouvrir une application privee."""
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None
    app.save({"prive": {"path": str(tmp_path), "command": "x", "port": 9101,
                        "enabled": True, "visibility": "privee"}})
    monkeypatch.setattr(app, "is_running", lambda n: True)

    r = client.get("/prive/", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["Location"]

    client.post("/login", json={"password": "secret-de-test"})
    # Connecte, la requete traverse le controle et va jusqu'au proxy ; le
    # port 9101 n'ecoute pas ici, donc 502 -- mais plus de redirection.
    assert client.get("/prive/", follow_redirects=False).status_code != 302


def test_une_application_sans_champ_reste_publique(tmp_path):
    """Les applications declarees avant ce reglage ne doivent pas se fermer
    toutes seules a la mise a jour."""
    assert app.visibilite({"port": 9101}) == app.VISIBILITE_PUBLIQUE
    assert app.visibilite({"visibility": "n'importe quoi"}) == app.VISIBILITE_PUBLIQUE


def test_le_code_a_six_chiffres_suit_la_norme():
    """Vecteur de la RFC 6238 : le secret "12345678901234567890" en base32,
    a l'instant 59, donne 287082. S'il change, aucune application
    d'authentification du marche ne saura plus se synchroniser."""
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert app.totp_code(secret, 59 // app.TOTP_PAS) == "287082"


def test_le_code_tolere_un_intervalle_mais_pas_deux(monkeypatch):
    secret = app.totp_nouveau_secret()
    maintenant = int(time.time()) // app.TOTP_PAS
    assert app.totp_verifie(secret, app.totp_code(secret, maintenant))
    assert app.totp_verifie(secret, app.totp_code(secret, maintenant - 1))
    assert not app.totp_verifie(secret, app.totp_code(secret, maintenant - 4))
    assert not app.totp_verifie(secret, "000000")
    assert not app.totp_verifie(secret, "")
    assert not app.totp_verifie("", "123456")     # secret vide = desactive


def test_sans_secret_la_connexion_se_fait_au_seul_mot_de_passe(client):
    assert not app.totp_actif()
    assert client.post("/login", json={"password": "secret-de-test"}).status_code == 200


def test_avec_un_secret_le_mot_de_passe_seul_ne_suffit_plus(client, monkeypatch):
    monkeypatch.setattr(app, "_totp_secret", app.totp_nouveau_secret())
    assert client.post("/login", json={"password": "secret-de-test"}).status_code == 401
    bon = app.totp_code(app._totp_secret, int(time.time()) // app.TOTP_PAS)
    assert client.post("/login", json={"password": "secret-de-test", "code": bon}).status_code == 200


# --------------------- 4. isolation de ce qui est lance ---------------------

def test_le_cookie_du_panneau_ne_part_pas_dans_l_application():
    """Les applications sont servies sur la meme origine que le panneau : le
    navigateur leur envoie le cookie admin, et le proxy le relayait."""
    nom = app.flask_app.config.get("SESSION_COOKIE_NAME") or "session"
    reste = app.strip_session_cookie(f"theme=dark; {nom}=SECRET; {nom}_id=garde-moi")
    assert "SECRET" not in reste
    # Les cookies de l'application passent, y compris ceux au nom voisin.
    assert "theme=dark" in reste and f"{nom}_id=garde-moi" in reste


def test_les_privileges_sont_abandonnes_dans_le_bon_ordre(monkeypatch):
    """credentials.env est monte en 0600 root : un enfant lance en root le
    lisait. setgroups et setgid AVANT setuid -- apres, le processus ne peut
    plus changer ses groupes et garderait ceux de root."""
    ordre = []
    monkeypatch.setattr(app.os, "geteuid", lambda: 0)
    monkeypatch.setattr(app.os, "setgroups", lambda g: ordre.append(("setgroups", tuple(g))))
    monkeypatch.setattr(app.os, "setgid", lambda g: ordre.append(("setgid", g)))
    monkeypatch.setattr(app.os, "setuid", lambda u: ordre.append(("setuid", u)))
    monkeypatch.setattr(app.os, "umask", lambda m: ordre.append(("umask", m)))
    app.drop_privileges()
    assert ordre == [
        ("setgroups", (app.RUN_AS_GID,)),
        ("setgid", app.RUN_AS_GID),
        ("setuid", app.RUN_AS_UID),
        ("umask", 0o002),
    ]
    assert app.RUN_AS_UID != 0 and app.RUN_AS_GID != 0


def test_une_application_sans_limite_memoire_abandonne_quand_meme_ses_privileges(monkeypatch):
    """La limite memoire est optionnelle, l'abandon des privileges ne l'est
    pas : les deux passaient autrefois par le meme preexec_fn conditionnel."""
    ordre = []
    monkeypatch.setattr(app.resource, "setrlimit", lambda *a: ordre.append("rlimit"))
    monkeypatch.setattr(app, "drop_privileges", lambda: ordre.append("drop"))
    app.child_setup()()
    assert ordre == ["drop"]
    ordre.clear()
    app.child_setup(64)()
    assert ordre == ["rlimit", "drop"]   # la limite avant la bascule


# ------------------- 5. inscription du projet de diagnostic -------------------
#
# Le projet de diagnostic s'inscrit tout seul au premier demarrage. La
# propriete a tenir n'est pas "il s'inscrit" (visible du premier coup d'oeil)
# mais "il ne se reinscrit jamais" : un projet supprime qui revient au
# redemarrage suivant est exactement le defaut qui rend une installation
# penible, et il ne se voit qu'apres coup.

def _amorcage(tmp_path, monkeypatch, avec_projet=True):
    etat = tmp_path / "etat"
    etat.mkdir()
    racine = tmp_path / "workspace"
    racine.mkdir()
    if avec_projet:
        (racine / "diagnostic").mkdir()
        (racine / "diagnostic" / "app.py").write_text("")
    monkeypatch.setattr(app, "APPS_FILE", str(etat / "apps.json"))
    monkeypatch.setattr(app, "DIAGNOSTIC_MARQUEUR", str(etat / "diagnostic-inscrit"))
    monkeypatch.setattr(app, "ROOT", str(racine))
    app._apps_cache["signature"] = None
    return racine


def test_le_diagnostic_est_inscrit_au_premier_demarrage(tmp_path, monkeypatch):
    racine = _amorcage(tmp_path, monkeypatch)
    assert app.amorcer_diagnostic() == "diagnostic"
    inscrit = app.load()["diagnostic"]
    assert inscrit["path"] == str(racine / "diagnostic")
    assert inscrit["command"] == app.DIAGNOSTIC_COMMANDE
    assert inscrit["build_command"] == app.DIAGNOSTIC_BUILD
    # Pas demarree ici : c'est le thread d'amorcage qui la lance, apres le
    # build qui installe son pilote Postgres.
    assert inscrit["enabled"] is False
    assert inscrit["visibility"] == app.VISIBILITE_PRIVEE
    assert app.PORT_MIN <= inscrit["port"] <= app.PORT_MAX


def test_un_diagnostic_supprime_ne_revient_pas_au_redemarrage(tmp_path, monkeypatch):
    """Le defaut a empecher : supprimer le projet depuis le panneau, puis le
    retrouver au demarrage suivant."""
    _amorcage(tmp_path, monkeypatch)
    app.amorcer_diagnostic()
    app.save({})                       # suppression depuis le panneau
    assert app.amorcer_diagnostic() is None
    assert app.load() == {}


def test_un_panneau_deja_utilise_n_est_pas_touche(tmp_path, monkeypatch):
    """Mise a jour d'une installation existante : le registre a deja des
    applications, on n'y ajoute rien -- l'utilisateur a peut-etre inscrit ce
    projet lui-meme, ou l'a supprime volontairement."""
    _amorcage(tmp_path, monkeypatch)
    app.save({"mon-site": {"port": 9101}})
    assert app.amorcer_diagnostic() is None
    assert list(app.load()) == ["mon-site"]
    # Et la question est tranchee pour de bon, meme si le panneau se vide.
    app.save({})
    assert app.amorcer_diagnostic() is None


def test_un_projet_pas_encore_amorce_est_retente_au_demarrage_suivant(tmp_path, monkeypatch):
    """app-manager et dagster demarrent en parallele, et c'est dagster qui
    depose le projet dans /workspace : au premier demarrage le dossier peut
    ne pas encore exister. Renoncer definitivement ici priverait l'utilisateur
    du projet pour une simple question d'ordre de demarrage."""
    racine = _amorcage(tmp_path, monkeypatch, avec_projet=False)
    assert app.amorcer_diagnostic() is None
    assert not os.path.exists(app.DIAGNOSTIC_MARQUEUR)

    (racine / "diagnostic").mkdir()
    (racine / "diagnostic" / "app.py").write_text("")
    assert app.amorcer_diagnostic() == "diagnostic"


# ---------------- 6. secrets transmis aux applications ----------------
#
# credentials.env est en 0600 root ; les applications tournent sous l'uid
# 1001 et ne peuvent donc pas le lire, alors que c'est la que la
# documentation leur dit de prendre le mot de passe Postgres. Le panneau le
# lit pour elles et le transmet par l'environnement -- sans son propre bloc.

def _credentials(tmp_path, monkeypatch, contenu):
    fichier = tmp_path / "credentials.env"
    fichier.write_text(contenu)
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(fichier))
    return fichier


def test_le_mot_de_passe_du_panneau_n_est_pas_transmis_aux_applications(tmp_path, monkeypatch):
    """Le defaut a empecher : une application est du code arbitraire tournant
    sous un autre uid. Lui donner le mot de passe admin annulerait la
    separation pour lui offrir l'acces au panneau."""
    _credentials(tmp_path, monkeypatch, "\n".join([
        "# ===== codelab-postgres =====",
        "POSTGRES_PASSWORD=mdp-postgres",
        "# ===== /codelab-postgres =====",
        "APP_MANAGER_ADMIN_PASSWORD=mdp-panneau",
        "APP_MANAGER_SESSION_SECRET=cle-de-session",
        "APP_MANAGER_TOTP_SECRET=second-facteur",
    ]))
    partages = app.secrets_partages()
    assert partages["POSTGRES_PASSWORD"] == "mdp-postgres"
    assert not [c for c in partages if c.startswith("APP_MANAGER_")]


def test_une_cle_reservee_ne_peut_pas_casser_le_lancement(tmp_path, monkeypatch):
    """Une ligne PATH= ajoutee a la main casserait sinon toutes les
    applications d'un coup, sans rien pour l'expliquer."""
    _credentials(tmp_path, monkeypatch,
                 "PATH=/casse-tout\nHOME=/nulle-part\nPORT=1\nAPI_TOKEN=jeton\n")
    partages = app.secrets_partages()
    assert partages == {"API_TOKEN": "jeton"}


def test_la_derniere_occurrence_gagne_et_les_guillemets_sautent(tmp_path, monkeypatch):
    """Chaque service reecrit son bloc en fin de fichier : une valeur laissee
    plus haut est perimee. Et les guillemets, qu'on met par reflexe, donnent
    un mot de passe faux s'ils sont conserves."""
    _credentials(tmp_path, monkeypatch, "\n".join([
        "# commentaire",
        "",
        "ligne malformee sans egal",
        'POSTGRES_PASSWORD="perime"',
        "POSTGRES_PASSWORD='a-jour'",
    ]))
    assert app.secrets_partages() == {"POSTGRES_PASSWORD": "a-jour"}


def test_un_fichier_illisible_ne_empeche_pas_de_lancer(tmp_path, monkeypatch):
    """Volume config non monte : les applications se debrouillent avec leur
    propre .env, comme avant -- le panneau ne doit pas refuser de lancer."""
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "absent.env"))
    assert app.secrets_partages() == {}


# ------------------- 7. alertes par mail -------------------
#
# Une alerte qui part en boucle est pire que pas d'alerte : la boite se
# remplit, et on prend l'habitude de ne plus la lire. Ces tests tiennent la
# seule propriete qui compte vraiment -- un incident, un mail.

@pytest.fixture
def alertes(tmp_path, monkeypatch):
    """Un panneau avec une application declaree et les alertes actives."""
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "ALERTES_FILE", str(tmp_path / "alertes.json"))
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
                        lambda cfg, sujet, corps: envoyes.append(sujet))
    return envoyes


def _etat(monkeypatch, tourne, en_boucle):
    monkeypatch.setattr(app, "is_running", lambda n: tourne)
    monkeypatch.setattr(app, "is_crash_looping", lambda n: en_boucle)


def test_une_application_tombee_alerte_une_seule_fois(alertes, monkeypatch):
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    assert alertes == ["[CodeLab] site est tombee"]
    # Le moniteur repasse toutes les 10 secondes : sans memoire de l'incident,
    # c'est un mail toutes les 10 secondes tant que l'application est a terre.
    app.alerte_tick()
    app.alerte_tick()
    assert len(alertes) == 1


def test_le_retour_a_la_normale_est_signale_puis_oublie(alertes, monkeypatch):
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    _etat(monkeypatch, tourne=True, en_boucle=False)
    app.alerte_tick()
    assert alertes == ["[CodeLab] site est tombee", "[CodeLab] site est revenue"]
    app.alerte_tick()
    assert len(alertes) == 2   # l'incident est clos, plus rien a dire


def test_un_plantage_rattrape_par_un_redemarrage_n_alerte_pas(alertes, monkeypatch):
    """Le filet de securite qui fonctionne n'est pas un incident : une
    application relancee avec succes ne doit reveiller personne."""
    _etat(monkeypatch, tourne=False, en_boucle=False)
    app.alerte_tick()
    assert alertes == []


def test_un_arret_volontaire_n_alerte_pas(alertes, monkeypatch):
    """Personne n'a besoin d'un mail pour une action qu'il vient de faire."""
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    apps = app.load()
    apps["site"]["enabled"] = False       # arret depuis le panneau
    app.save(apps)
    app.alerte_tick()
    assert len(alertes) == 1              # ni deuxieme alerte, ni mail de retour
    assert app._alertes_en_cours == set()


def test_l_interrupteur_coupe_vraiment_les_alertes(alertes, monkeypatch):
    app.ecrire_alertes(False, ["moi@example.com"])
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    assert alertes == []


def test_la_configuration_incomplete_est_dite_champ_par_champ(tmp_path, monkeypatch):
    """"Ca ne marche pas" est inutilisable ; le nom de la cle manquante se
    corrige en dix secondes."""
    monkeypatch.setattr(app, "ALERTES_FILE", str(tmp_path / "alertes.json"))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    (tmp_path / "credentials.env").write_text("")
    app.ecrire_alertes(True, [])
    _, manquants = app.config_smtp()
    assert manquants == ["SMTP_HOST", "SMTP_USER (ou ALERTE_FROM)", "destinataires"]


def test_les_adresses_saisies_sont_nettoyees():
    """Saisie humaine : virgules, espaces, doublons, ligne vide."""
    assert app._adresses("moi@example.com, autre@example.com ,moi@example.com,") == [
        "moi@example.com", "autre@example.com"]
    assert app._adresses(["pas-une-adresse", " ok@example.com "]) == ["ok@example.com"]


# ------------------- 8. deux espaces : admin et utilisateur -------------------
#
# Le controle des droits est fait dans les routes, jamais dans l'interface :
# masquer un bouton ne protege rien, la route reste appelable a la main. Ces
# tests appellent donc les routes directement, comme le ferait quelqu'un qui
# a lu le code de la page.

@pytest.fixture
def deux_espaces(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
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


def _connecte(client, nom, mdp):
    """Connexion complete d'un compte utilisateur, second facteur compris.

    Ces comptes n'ouvrent jamais de session sur le seul mot de passe : au
    premier acces le serveur renvoie une cle a enregistrer, ensuite il exige
    un code. Les deux cas sont traites ici pour que les tests de droits
    parlent de droits et pas d'authentification.
    """
    r = client.post("/login", json={"nom": nom, "password": mdp})
    assert r.status_code in (200, 401), r.data
    d = r.get_json()

    if d.get("inscription"):
        secret = d["secret"]
        r = client.post("/login/second-facteur",
                        json={"code": app.totp_code(secret, int(time.time()) // app.TOTP_PAS)})
        assert r.status_code == 200, r.data
        return r.get_json()

    if d.get("totp"):
        secret = app.lire_utilisateurs()[nom]["totp"]
        r = client.post("/login", json={"nom": nom, "password": mdp,
                                        "code": app.totp_code(secret, int(time.time()) // app.TOTP_PAS)})
        assert r.status_code == 200, r.data
        return r.get_json()

    assert r.status_code == 200, r.data
    return d


def test_un_utilisateur_ne_peut_rien_administrer(deux_espaces):
    """Le coeur du sujet : un compte utilisateur ne deploie pas, ne configure
    pas, ne cree pas de compte -- meme en appelant les routes a la main."""
    c = deux_espaces
    assert _connecte(c, "marie", "mot-de-passe-long")["role"] == "utilisateur"
    for methode, route in [("get", "/api/apps"), ("post", "/api/add"),
                           ("post", "/api/toggle/public"), ("post", "/api/deploy/public"),
                           ("delete", "/api/app/public"), ("get", "/api/utilisateurs"),
                           ("post", "/api/utilisateurs"), ("get", "/api/alertes"),
                           ("post", "/api/alertes/test"), ("get", "/api/logs/public"),
                           ("get", "/api/browse")]:
        r = getattr(c, methode)(route, json={})
        assert r.status_code == 403, f"{methode.upper()} {route} a repondu {r.status_code}"


def test_un_utilisateur_ne_voit_que_ses_projets(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    noms = [a["name"] for a in c.get("/api/mes-apps").get_json()["apps"]]
    assert noms == ["prive-autorise"]

    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    noms = [a["name"] for a in c.get("/api/mes-apps").get_json()["apps"]]
    assert noms == ["prive-autorise", "prive-refuse", "public"]


def test_un_projet_prive_non_autorise_reste_ferme(deux_espaces, monkeypatch):
    """Connaitre l'adresse ne suffit pas : le refus est dans le proxy, pas
    dans la liste affichee."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/prive-refuse/").status_code == 403
    # Et l'application autorisee, elle, est bien servie : on s'arrete juste
    # avant la connexion reelle au port de l'application.
    monkeypatch.setattr(app, "is_running", lambda n: False)
    assert c.get("/prive-autorise/").status_code == 503   # "arretee", pas "refuse"


def test_un_projet_public_reste_ouvert_sans_compte(deux_espaces, monkeypatch):
    """La visibilite publique est ce qui permet de partager un lien : les
    comptes ne doivent pas l'avoir refermee au passage."""
    monkeypatch.setattr(app, "is_running", lambda n: False)
    assert deux_espaces.get("/public/").status_code == 503   # servie, mais arretee


def test_dagster_reste_reserve_a_l_administrateur(deux_espaces):
    """L'interface de Dagster lance des jobs, donc execute du code : y donner
    acces a un compte utilisateur serait lui donner l'administration."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/auth-check").status_code == 401
    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    assert c.get("/api/auth-check").status_code == 204


def test_une_session_ouverte_avant_les_comptes_reste_administratrice(deux_espaces):
    """Mise a jour d'une installation en service : les seules sessions qui
    existaient venaient du panneau d'administration. Les degrader
    deconnecterait l'administrateur de son propre panneau."""
    c = deux_espaces
    with c.session_transaction() as s:
        s["authed"] = True          # session d'avant, sans role enregistre
    assert c.get("/api/apps").status_code == 200


def test_le_nom_du_compte_d_administration_ne_peut_pas_etre_repris(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/utilisateurs", json={"nom": "admin", "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 400
    assert "administration" in r.get_json()["error"]


def test_les_mots_de_passe_sont_derives_et_sales(deux_espaces):
    """Deux comptes avec le meme mot de passe ne doivent pas donner la meme
    empreinte : sinon le fichier revele qui partage un mot de passe."""
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    for nom in ("paul", "jean"):
        assert c.post("/api/utilisateurs",
                      json={"nom": nom, "mot_de_passe": "le-meme-mot-de-passe"}).status_code == 200
    comptes = app.lire_utilisateurs()
    assert comptes["paul"]["hash"] != comptes["jean"]["hash"]
    assert "le-meme-mot-de-passe" not in json.dumps(comptes)
    assert app.verifie_mot_de_passe(comptes["paul"], "le-meme-mot-de-passe")
    assert not app.verifie_mot_de_passe(comptes["paul"], "presque-le-meme")


def test_un_droit_sur_un_projet_inexistant_n_est_pas_enregistre(deux_espaces):
    """Un projet supprime puis recree sous le meme nom rendrait sinon un
    droit qu'on croyait perdu."""
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.put("/api/utilisateurs/marie", json={"projets": ["public", "jamais-declare"]})
    assert r.status_code == 200
    assert app.lire_utilisateurs()["marie"]["projets"] == ["public"]


# ---------- 9. second facteur obligatoire pour les comptes utilisateurs ----------
#
# Ces comptes existent pour etre distribues : leur mot de passe circule par un
# canal qu'on ne maitrise pas, et sera reutilise ailleurs. Le point a tenir
# est qu'un mot de passe seul n'ouvre JAMAIS de session -- ni avant
# l'inscription du facteur, ni apres.

def _code_valide(secret):
    return app.totp_code(secret, int(time.time()) // app.TOTP_PAS)


def test_le_mot_de_passe_seul_n_ouvre_aucune_session(deux_espaces):
    """Le premier acces renvoie une cle a enregistrer, pas une session : entre
    les deux, le cookie ne vaut rien."""
    c = deux_espaces
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    assert r.status_code == 200 and r.get_json()["inscription"] is True

    # La session intermediaire n'ouvre rien du tout.
    assert c.get("/api/mes-apps").status_code == 401
    assert c.get("/prive-autorise/").status_code == 302   # renvoye vers /login
    assert c.get("/", follow_redirects=False).status_code == 302


def test_l_inscription_ouvre_la_session_et_persiste_la_cle(deux_espaces):
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    # Rien n'est enregistre tant que le code n'est pas confirme : une cle mal
    # recopiee ne doit pas enfermer dehors.
    assert not app.lire_utilisateurs()["marie"].get("totp")

    assert c.post("/login/second-facteur",
                  json={"code": _code_valide(secret)}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["totp"] == secret
    assert c.get("/api/mes-apps").status_code == 200


def test_un_code_faux_n_enregistre_rien(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    r = c.post("/login/second-facteur", json={"code": "000000"})
    assert r.status_code == 400
    assert not app.lire_utilisateurs()["marie"].get("totp")
    assert c.get("/api/mes-apps").status_code == 401


def test_une_fois_inscrit_le_code_est_exige_a_chaque_connexion(deux_espaces):
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    c.post("/logout")

    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    assert r.status_code == 401 and r.get_json()["totp"] is True
    assert c.get("/api/mes-apps").status_code == 401

    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                               "code": _code_valide(secret)})
    assert r.status_code == 200
    assert c.get("/api/mes-apps").status_code == 200


def test_la_reinitialisation_par_l_admin_refait_passer_par_l_inscription(deux_espaces):
    """Telephone perdu : l'administrateur remet l'etape a zero, sans jamais
    connaitre ni transmettre la cle de quelqu'un d'autre."""
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    c.post("/logout")

    c.post("/login", json={"password": "secret-de-test"})
    assert c.get("/api/utilisateurs").get_json()["utilisateurs"][0]["totp"] is True
    assert c.put("/api/utilisateurs/marie",
                 json={"reinitialiser_totp": True}).status_code == 200
    assert c.get("/api/utilisateurs").get_json()["utilisateurs"][0]["totp"] is False
    c.post("/logout")

    # Et l'ancienne cle ne vaut plus rien : c'est une NOUVELLE inscription.
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                               "code": _code_valide(secret)})
    assert r.get_json()["inscription"] is True
    assert r.get_json()["secret"] != secret


def test_l_inscription_ne_remplace_pas_un_facteur_deja_en_service(deux_espaces):
    """Deux sessions ouvertes en parallele : la seconde ne doit pas ecraser la
    cle que la premiere vient d'enregistrer, sinon le telephone deja
    configure cesse de fonctionner."""
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    comptes = app.lire_utilisateurs()          # une autre session a fini avant
    comptes["marie"]["totp"] = "AUTRECLEDEJAENREGISTREE"
    app.ecrire_utilisateurs(comptes)

    r = c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    assert r.status_code == 409
    assert app.lire_utilisateurs()["marie"]["totp"] == "AUTRECLEDEJAENREGISTREE"


def test_l_administrateur_garde_le_choix_de_son_second_facteur(deux_espaces):
    """Le rendre obligatoire pour lui aussi pourrait l'enfermer hors de son
    propre panneau : c'est un reglage, pas une regle."""
    c = deux_espaces
    assert not app.totp_actif()
    assert c.post("/login", json={"password": "secret-de-test"}).status_code == 200


# ------------------- 10. presentation des projets -------------------

def test_une_description_collee_depuis_un_readme_est_ramenee_a_une_ligne():
    """Retours a la ligne, espaces multiples, et plus long que ce que la carte
    peut afficher : la liste doit rester une liste."""
    propre = app.description_propre("  Premiere ligne\n\n  et   la suite  " + "z" * 200)
    assert propre.startswith("Premiere ligne et la suite z")
    assert "\n" not in propre and len(propre) == app.DESCRIPTION_MAX
    assert app.description_propre(None) == ""


def test_la_description_est_nettoyee_a_la_declaration(deux_espaces, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", str(tmp_path))
    dossier = tmp_path / "nouveau"
    dossier.mkdir()
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/add", json={"name": "nouveau", "path": str(dossier),
                                 "command": "python3 app.py",
                                 "description": "Deux\nlignes"})
    assert r.status_code == 200, r.data
    assert app.load()["nouveau"]["description"] == "Deux lignes"


def test_la_description_suit_le_projet_jusqu_a_l_espace_utilisateur(deux_espaces):
    apps = app.load()
    apps["prive-autorise"]["description"] = "Le tableau de bord des ventes"
    app.save(apps)

    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    app_vue = c.get("/api/mes-apps").get_json()["apps"][0]
    assert app_vue["description"] == "Le tableau de bord des ventes"
    # Et rien de plus : l'espace utilisateur n'a pas a connaitre le chemin ni
    # la commande de lancement.
    assert "path" not in app_vue and "command" not in app_vue


# ------------------- 11. plafond des flux de journal -------------------
#
# Un flux de journal occupe un thread du serveur tant qu'il est ouvert.
# Mesure faite sur une instance reelle : avec 16 threads, 20 flux simultanes
# rendaient le panneau entierement muet -- healthcheck compris, donc le
# conteneur passait "unhealthy". Le plafond transforme cette panne totale en
# un refus lisible sur le seul flux de trop.

def test_le_plafond_de_flux_protege_le_panneau(monkeypatch):
    monkeypatch.setattr(app, "SSE_MAX_FLUX", 2)
    monkeypatch.setattr(app, "_flux_ouverts", 0)
    assert app._prendre_place_flux() is True
    assert app._prendre_place_flux() is True
    assert app._prendre_place_flux() is False   # le flux de trop est refuse
    app._rendre_place_flux()
    assert app._prendre_place_flux() is True    # une place rendue est reutilisable


def test_une_place_rendue_deux_fois_n_en_cree_pas_une_troisieme(monkeypatch):
    """call_on_close et le generateur peuvent tous deux liberer : le compteur
    ne doit pas passer sous zero, sinon le plafond monterait a chaque
    deconnexion."""
    monkeypatch.setattr(app, "SSE_MAX_FLUX", 1)
    monkeypatch.setattr(app, "_flux_ouverts", 0)
    app._prendre_place_flux()
    app._rendre_place_flux()
    app._rendre_place_flux()
    assert app._prendre_place_flux() is True
    assert app._prendre_place_flux() is False


def test_le_flux_refuse_le_dit_avec_un_code_utilisable(deux_espaces, monkeypatch):
    """503 et un message qui nomme la cause : l'interface s'en sert pour
    expliquer, au lieu de rester sur « Connexion... »."""
    monkeypatch.setattr(app, "SSE_MAX_FLUX", 0)
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.get("/api/logs/public/stream")
    assert r.status_code == 503
    assert "journaux" in r.get_json()["error"]


# ---------- 12. une seule application, deux modes ----------
#
# Le hub et l'outil de developpement sont deux modes de la meme page. Ce qui
# doit rester vrai : la page est la meme pour tout le monde, mais elle sait
# qui la regarde, et surtout les ROUTES continuent de decider -- une page
# bricolee ne donne aucun droit.

def test_la_meme_page_est_servie_aux_deux_roles(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    page_utilisateur = c.get("/").get_data(as_text=True)
    assert '"utilisateur"' in page_utilisateur     # role injecte
    assert "sec-hub" in page_utilisateur

    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    page_admin = c.get("/").get_data(as_text=True)
    assert '"admin"' in page_admin
    # Meme page : ce sont les memes sections, c'est le mode qui change.
    assert "sec-hub" in page_admin and "mode-toggle" in page_admin
    assert "sec-settings" in page_admin


def test_l_ancienne_adresse_de_l_espace_ramene_a_la_page_unique(deux_espaces):
    """Elle a pu etre mise en favori : elle ne doit pas tomber en 404."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.get("/espace")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")


def test_le_mode_affiche_ne_donne_aucun_droit(deux_espaces):
    """Le coeur du sujet : la page connait le role pour savoir quoi afficher,
    mais un compte utilisateur qui appellerait les routes d'administration a
    la main -- ou qui modifierait la page -- reste refuse."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/").status_code == 200          # la page, oui
    assert c.get("/api/apps").status_code == 403  # les droits, non
    assert c.get("/api/utilisateurs").status_code == 403
    # Et le hub, lui, reste servi aux deux roles.
    assert c.get("/api/mes-apps").status_code == 200
