"""Tests des fonctions pures de l'app-manager.

Portee volontairement etroite : ce qui se teste sans conteneur, sans Postgres
et sans reseau -- la detection de stack, le bornage des chemins, l'attribution
des ports, l'authentification. Le reste (cycle de vie des process, proxy)
demande une stack en marche et se verifie a la main.

Le module s'importe sans effet de bord : tout le demarrage vit derriere
if __name__ == "__main__".
"""
import importlib.util
import json
import os
import sys

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _charger_app():
    chemin = os.path.join(RACINE, "app-manager", "app.py")
    spec = importlib.util.spec_from_file_location("codelab_app_manager", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


app = _charger_app()


# ---------------------------- detection de stack ----------------------------

def _projet(tmp_path, fichiers):
    for nom, contenu in fichiers.items():
        cible = tmp_path / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu)
    return str(tmp_path)


def test_vite_est_construit_puis_servi_en_statique(tmp_path):
    """Le piege d'origine : un projet Vite n'a pas de script "start"."""
    chemin = _projet(tmp_path, {
        "package.json": json.dumps({"scripts": {"build": "vite build"},
                                    "devDependencies": {"vite": "^5"}}),
        "package-lock.json": "{}",
    })
    commande, build = app.detect_project(chemin)
    assert commande == "python3 -m http.server $PORT --directory dist"
    assert build == "npm ci && npm run build"
    assert "npm start" not in commande


def test_lockfile_absent_donne_npm_install(tmp_path):
    chemin = _projet(tmp_path, {
        "package.json": json.dumps({"scripts": {"build": "vite build"},
                                    "devDependencies": {"vite": "^5"}}),
    })
    _, build = app.detect_project(chemin)
    assert build == "npm install && npm run build"


def test_next_garde_son_propre_serveur(tmp_path):
    chemin = _projet(tmp_path, {
        "package.json": json.dumps({"scripts": {"build": "next build"},
                                    "dependencies": {"next": "14"}}),
    })
    commande, _ = app.detect_project(chemin)
    assert commande == "npx next start --port $PORT"


def test_flask_et_requirements(tmp_path):
    chemin = _projet(tmp_path, {"app.py": "from flask import Flask",
                                "requirements.txt": "flask\n"})
    assert app.detect_project(chemin) == ("python3 app.py",
                                          "pip install -r requirements.txt")


def test_site_statique(tmp_path):
    chemin = _projet(tmp_path, {"index.html": "<html>"})
    assert app.detect_project(chemin) == ("python3 -m http.server $PORT", "")


def test_dossier_vide_ne_propose_rien(tmp_path):
    assert app.detect_project(str(tmp_path)) == ("", "")


def test_toute_commande_de_lancement_utilise_la_variable_port(tmp_path):
    """Un port en dur se desynchronise du port attribue par l'app-manager."""
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


# ------------------------------ bornage des chemins ------------------------------

def test_under_root_accepte_la_racine_et_ses_enfants():
    assert app.under_root(app.ROOT)
    assert app.under_root(os.path.join(app.ROOT, "mon-projet"))


def test_under_root_refuse_l_exterieur_et_les_remontees():
    assert not app.under_root("/etc")
    assert not app.under_root(os.path.join(app.ROOT, "..", "etc"))
    # Un dossier voisin dont le nom commence comme la racine ("/workspace-bis")
    # ne doit pas passer pour un enfant de "/workspace".
    assert not app.under_root(app.ROOT + "-bis")


# --------------------------------- ports ---------------------------------

def test_next_port_prend_le_premier_libre():
    apps = {"a": {"port": app.PORT_MIN}, "b": {"port": app.PORT_MIN + 1}}
    assert app.next_port(apps) == app.PORT_MIN + 2


def test_next_port_rend_none_quand_la_plage_est_pleine():
    apps = {str(p): {"port": p} for p in range(app.PORT_MIN, app.PORT_MAX + 1)}
    assert app.next_port(apps) is None


# ------------------------------- noms -------------------------------

@pytest.mark.parametrize("saisie, attendu", [
    ("Mon Projet", "mon-projet"),
    ("  espaces  ", "espaces"),
    ("../../etc/passwd", "etc-passwd"),   # aucun separateur ne survit
    ("---", ""),
    ("", ""),
])
def test_valid_name(saisie, attendu):
    assert app.valid_name(saisie) == attendu


# ---------------------------- authentification ----------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()


def test_les_routes_api_refusent_sans_session(client):
    assert client.get("/api/apps").status_code == 401
    assert client.post("/api/toggle/quelconque").status_code == 401


def test_connexion_et_acces(client):
    assert client.post("/login", json={"password": "secret-de-test"}).status_code == 200
    assert client.get("/api/apps").status_code == 200


def test_mauvais_mot_de_passe(client):
    assert client.post("/login", json={"password": "faux"}).status_code == 401


def test_la_limite_de_tentatives_ne_se_contourne_pas_par_en_tete(client):
    """X-Forwarded-For est pose par le client : il ne doit pas remettre le
    compteur a zero quand le service est publie directement."""
    assert not app.TRUST_PROXY, "APP_MANAGER_TRUST_PROXY ne doit pas etre actif par defaut"
    for i in range(app.RATE_LIMIT_MAX):
        r = client.post("/login", json={"password": "faux"},
                        headers={"X-Forwarded-For": f"10.0.0.{i}"})
        assert r.status_code == 401
    r = client.post("/login", json={"password": "faux"},
                    headers={"X-Forwarded-For": "10.0.0.99"})
    assert r.status_code == 429


def test_le_cookie_de_session_est_samesite_lax(client):
    """Sans SameSite, un formulaire pose sur un autre site peut piloter la
    stack avec le cookie de l'utilisateur connecte."""
    assert app.flask_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.flask_app.config["SESSION_COOKIE_HTTPONLY"] is True
    r = client.post("/login", json={"password": "secret-de-test"})
    cookie = r.headers.get("Set-Cookie", "")
    assert "SameSite=Lax" in cookie and "HttpOnly" in cookie


def test_health_reste_public(client):
    assert client.get("/health").status_code == 200
