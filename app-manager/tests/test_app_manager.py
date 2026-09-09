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
