"""
CodeLab -- TESTS : LE PANNEAU
---------------------------------------------------------------------------
ROLE        les tests du panneau (app-manager) : socle de la suite, affichage, applications, comptes, securite, proxy, acces par Tailscale Funnel, journal, sauvegardes, messages et notifications.
CHARGE PAR  pytest : python3 -m pytest workspace/diagnostic/tests -q
            (depuis la racine du depot), la CI, et la page /tests
            du diagnostic.
CONTRAINTES Les aides, constantes, modules et fixtures viennent de
            conftest.py (tests des autres services : test_services.py). Un test nouveau va dans la partie de
            son sujet. Aucun test ne lit /var/lib/codelab ni /workspace
            reels.
STRUCTURE   le socle de la suite, l'affichage, les applications, les
            comptes, la securite, le proxy, l'acces depuis internet par
            tailscale funnel, le journal, sauvegardes et alertes, les
            messages
"""

from conftest import *  # noqa: F401,F403 -- aides, constantes, modules



# ===========================================================================
# LE SOCLE DE LA SUITE
# le filet qui detourne les chemins d'etat, le client de test, la detection de
# l'isolement, l'importabilite des sondes sans pytest, le cache du registre.
# ===========================================================================

def test_le_filet_detourne_bien_tous_les_chemins():
    """Le filet se teste lui-meme : si un chemin d'etat lui echappait, ce
    test le dirait avant qu'une ecriture n'atteigne une vraie installation."""
    import inspect
    source = _source_du_panneau()
    # Tout ce qui ressemble a un chemin construit sous STATE_DIR,
    # SHARED_CONFIG_DIR ou ROOT doit figurer dans CHEMINS_ETAT.
    #
    # ROOT a ete ajoute avec la page des cles : le panneau y ecrit desormais
    # /workspace/.codex et /workspace/.openclaw, et ces deux-la echappaient
    # au motif -- un test qui cree une cle serait alle ecrire dans le vrai
    # /workspace de la machine qui lance la suite.
    trouves = set(re.findall(
        r"^([A-Z][A-Z0-9_]*) = os\.path\.join\((?:STATE_DIR|SHARED_CONFIG_DIR|ROOT)",
        source, re.M))
    oublies = trouves - set(CHEMINS_ETAT)
    assert not oublies, (
        f"chemins d'etat non couverts par le filet : {sorted(oublies)}")


# ------------------------- le client de test et le jeton -------------------
#
# Le panneau exige un jeton CSRF sur toute ecriture d'une session ouverte.
# Dans un navigateur, c'est l'enveloppe posee autour de fetch qui l'ajoute --
# une fois, pour tous les appels. Ici, c'est ce client : sans lui, chacun des
# ~120 appels d'ecriture des tests devrait poser l'en-tete a la main, et le
# jour ou l'un serait oublie on croirait a une regression du panneau.
#
# Il lit le jeton dans la session, exactement comme la page le lit dans le
# HTML servi. Ce qui n'est PAS teste par ce client -- l'absence de jeton, un
# jeton faux -- l'est explicitement, plus bas, section 22.
# ---------- l'isolement se detecte en l'essayant, pas en le cherchant -----
#
# Vecu sur une vraie machine : "unshare -Ur true" repondait "Operation not
# permitted", et le panneau lancait quand meme les applications derriere
# unshare -- donc aucune ne demarrait. La detection cherchait le BINAIRE.
# unshare vient de util-linux : il est toujours present. Ce qui manquait,
# c'etait l'autorisation du noyau, et elle ne se lit pas dans un PATH.
#
# Les deux tests posent un faux unshare en tete de PATH : l'un refuse comme
# le noyau refusait, l'autre accepte. La detection doit les distinguer.




def test_un_noyau_qui_refuse_fait_tomber_l_isolement(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    app._isolement.update(verdict=None, raison="")

    assert app.isolement_disponible() is False
    assert "Operation not permitted" in app._isolement["raison"]

    # Et surtout : la commande construite doit etre la commande ORDINAIRE.
    # C'est ce qui fait la difference entre une application qui demarre sans
    # isolement et une application qui ne demarre pas du tout.
    argv, env = app.commande_isolee("mon-projet", "/workspace/mon-projet",
                                    "python app.py")
    assert argv[0] != "unshare", argv
    assert argv == ["bash", "-lc", "python app.py"]
    assert env == {}




def test_un_noyau_qui_accepte_garde_l_isolement(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _faux_unshare(tmp_path, 0)
                       + os.pathsep + os.environ["PATH"])
    app._isolement.update(verdict=None, raison="")

    assert app.isolement_disponible() is True

    argv, env = app.commande_isolee("mon-projet", "/workspace/mon-projet",
                                    "python app.py")
    assert argv[0] == "unshare", argv
    assert env["CODELAB_COMMANDE"] == "python app.py"




def test_la_sonde_du_diagnostic_voit_le_refus_du_noyau(tmp_path, monkeypatch):
    """La sonde se trompait de la meme facon : elle affichait vert sur une
    machine ou aucune application ne pouvait demarrer."""
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("APP_MANAGER_ISOLER", raising=False)

    ok, _nom, detail = check_isolation()
    # ALERTE et non ECHEC : l'uid par application tient toujours, et ce refus
    # vient de l'hote -- aucune commande du conteneur ne le leve. Faire
    # tomber le run de diagnostic la-dessus quatre fois par heure, c'etait
    # apprendre a ne plus lire ses alertes.
    assert ok is Etat.ALERTE
    assert "namespace utilisateur" in detail
    # Ce qui reste vrai doit etre dit aussi : l'uid par application tient
    # toujours. Annoncer "aucune isolation" ferait chercher une panne la ou
    # il n'y en a pas.
    assert "propre uid" in detail




def test_le_refus_du_noyau_nomme_le_verrou_qui_bloque(tmp_path, monkeypatch):
    """Conseiller deux sysctl sans regarder s'ils sont en cause envoyait
    taper des commandes sans effet -- sur une machine ou c'est AppArmor qui
    refuse, elles ne changent rien et l'on recommence indefiniment."""
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("APP_MANAGER_ISOLER", raising=False)

    ouvert = tmp_path / "max_user_namespaces"
    ouvert.write_text("15000\n")
    ferme = tmp_path / "apparmor_restrict_unprivileged_userns"
    ferme.write_text("1\n")
    monkeypatch.setattr(checks, "VERROUS_USERNS", [
        (str(tmp_path / "absent"), "0", "sysctl -w kernel.unprivileged_userns_clone=1"),
        (str(ouvert), "0", "sysctl -w user.max_user_namespaces=15000"),
        (str(ferme), "1", "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0"),
    ])

    _ok, _nom, detail = check_isolation()
    # Le verrou ferme est nomme, avec sa commande.
    assert "apparmor_restrict_unprivileged_userns=1" in detail
    assert "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" in detail
    # Les deux autres ne sont pas en cause : les citer serait envoyer taper
    # des commandes qui ne changent rien.
    assert "max_user_namespaces=15000" not in detail
    assert "unprivileged_userns_clone" not in detail




def test_sans_verrou_ferme_la_sonde_ne_conseille_pas_de_sysctl(tmp_path, monkeypatch):
    """Quand aucun sysctl n'interdit rien, c'est le bac a sable du conteneur
    qui refuse -- et aucun sysctl n'y changera quoi que ce soit."""
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("APP_MANAGER_ISOLER", raising=False)
    ouvert = tmp_path / "max_user_namespaces"
    ouvert.write_text("15000\n")
    monkeypatch.setattr(checks, "VERROUS_USERNS",
                        [(str(ouvert), "0", "sysctl -w user.max_user_namespaces=15000")])

    _ok, _nom, detail = check_isolation()
    assert "seccomp" in detail and "AppArmor" in detail
    assert "sysctl -w" not in detail




# ------------- ce fichier doit rester importable sans pytest --------------
#
# Regression vecue : "import pytest" en tete de fichier, puis une classe
# definie a partir du module du panneau, ont fait disparaitre le projet
# diagnostic de Dagster. L'image Dagster n'a ni pytest ni flask ni le
# panneau ; definitions.py ignore silencieusement un projet qui ne se charge
# pas, donc l'asset n'etait plus la et rien ne criait.
#
# Le test rejoue exactement cette situation dans un interpreteur neuf : les
# paquets absents de l'image Dagster y sont rendus introuvables, et le
# panneau aussi. Ce qui doit survivre, ce sont les SONDES -- run_all() --,
# c'est-a-dire tout ce que Dagster vient chercher ici.

def test_checks_reste_importable_sans_pytest_ni_panneau():
    import subprocess

    script = """
import importlib.util, os, sys

ABSENTS = {"pytest", "flask", "psutil", "waitress", "webauthn", "qrcode"}


class Bloqueur:
    def find_spec(self, nom, chemin=None, cible=None):
        if nom.split(".")[0] in ABSENTS:
            raise ModuleNotFoundError("No module named " + repr(nom), name=nom)
        return None


sys.meta_path.insert(0, Bloqueur())

# Le panneau n'est pas dans l'image Dagster : aucun des chemins cherches par
# _charger_panneau() ne doit repondre.
_existe = os.path.exists
os.path.exists = lambda c: (
    False if str(c).endswith(os.path.join("app-manager", "app", "app.py"))
    else _existe(c))

spec = importlib.util.spec_from_file_location("checks_sans_pytest", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

os.path.exists = _existe

assert module.app is None, "le panneau aurait du rester introuvable"
assert callable(module.run_all), "les sondes doivent survivre a l'absence de pytest"
print("IMPORT OK")
"""

    # checks.__file__ et non __file__ : c'est l'importabilite des SONDES
    # qu'on verifie, pas celle de cette suite -- qui, elle, a le droit
    # d'exiger pytest.
    r = subprocess.run(
        [sys.executable, "-c", script, os.path.abspath(checks.__file__)],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (
        "checks.py ne s'importe plus sans pytest ni panneau -- c'est la "
        "situation de l'image Dagster, ou le projet diagnostic disparaitrait "
        "sans bruit :\n" + r.stdout + r.stderr)
    assert "IMPORT OK" in r.stdout




# ------------------ 0. cache du registre d'applications ------------------
#
# load() est mis en cache pour eviter une lecture disque par requete
# proxifiee. Le risque du cache est de servir un registre perime : ces deux
# tests tiennent la seule propriete qui compte, "une modification est vue".

def test_le_registre_est_relu_quand_la_base_change(tmp_path, monkeypatch):
    import sqlite3
    app._apps_cache["signature"] = None
    app.save({"un": {"port": 9101}})
    assert list(app.load()) == ["un"]

    # Ecriture exterieure, sans passer par save() : avec sqlite3 ou DB
    # Browser, par exemple. Les declencheurs de la table la signalent.
    cx = sqlite3.connect(app.DB_FILE)
    cx.execute("UPDATE applications SET nom = 'deux', port = 9102")
    cx.commit()
    cx.close()
    assert list(app.load()) == ["deux"]




def test_modifier_le_registre_recu_ne_corrompt_pas_le_cache(tmp_path, monkeypatch):
    """Les appelants modifient ce que load() renvoie avant de le repasser a
    save() : sans copie, ces modifications apparaitraient dans le cache avant
    l'ecriture -- et y resteraient meme si elle echouait."""
    fichier = tmp_path / "apps.json"
    app._apps_cache["signature"] = None
    app.save({"un": {"port": 9101, "enabled": True}})

    registre = app.load()
    registre["un"]["enabled"] = False
    registre["intrus"] = {"port": 9999}

    relu = app.load()
    assert relu["un"]["enabled"] is True
    assert "intrus" not in relu


# ===========================================================================
# L'AFFICHAGE
# theme, script des pages, accents, parametres, hub et tuiles, categories,
# mode application, defilement, telephone, video.
# ===========================================================================

# ---------- 12. une seule application, un seul hub ----------
#
# CodeLab est une seule page. Le hub est l'accueil de tout le monde ; ce que
# le menu propose en plus depend du role. Ce qui doit rester vrai : la page
# est la meme pour tout le monde, mais elle sait qui la regarde, et surtout
# les ROUTES continuent de decider -- une page bricolee ne donne aucun droit.

def test_la_meme_page_est_servie_aux_deux_roles(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    page_utilisateur = c.get("/").get_data(as_text=True)
    assert '"utilisateur"' in page_utilisateur     # role injecte
    assert "sec-hub" in page_utilisateur

    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    page_admin = c.get("/").get_data(as_text=True)
    assert '"admin"' in page_admin
    # Les memes sections sont servies aux deux roles : seul le role injecte
    # change. C'est lui, et les routes, qui decident de ce qui est utilisable.
    for section in ("sec-hub", "sec-overview", "sec-apps", "sec-parametres", "sec-users"):
        assert f'id="{section}"' in page_admin
        assert f'id="{section}"' in page_utilisateur




def test_le_hub_est_l_accueil_des_deux_roles(deux_espaces):
    """L'administrateur atterrit sur le hub, comme tout le monde.

    Il n'y a plus de bascule « Hub / Developpeur » : la page s'ouvre sur le
    lanceur, et la console d'administration s'atteint par le menu.
    """
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    page = _page_servie(c)
    # En debut de ligne : l'appel du demarrage, pas un onclick de menu. Il
    # lit l'adresse, et « / » est le hub.
    assert "\nrouterDepuisAdresse();\n" in page
    assert "hub:'/'" in page
    assert "mode-toggle" not in page and "setMode" not in page
    # Le pied du menu lateral annoncait une evidence : il n'est plus la.
    assert "Serveur en service" not in page




def test_les_comptes_ont_leur_propre_entree_de_menu(deux_espaces):
    """Gerer qui entre n'est pas un reglage du serveur.

    Les comptes vivaient dans Configuration, entre les categories et les
    alertes. Ils ont leur section, et le menu lateral y mene.
    """
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    page = c.get("/").get_data(as_text=True)
    assert 'id="sec-users"' in page
    assert 'data-sec="users"' in page
    # La liste des comptes se dessine dans la section Utilisateurs, et nulle
    # part ailleurs : la dedoubler ferait diverger deux formulaires qui
    # ecrivent le meme fichier.
    assert page.count('id="us-liste"') == 1
    avant, apres = page.split('id="sec-users"', 1)
    assert 'id="us-liste"' in apres and 'id="us-liste"' not in avant




def test_les_parametres_tiennent_en_un_seul_endroit(deux_espaces):
    """Une seule entree de menu, et des onglets derriere.

    « Parametres » et « Configuration » etaient deux portes pour la meme
    piece : il fallait se demander dans laquelle chercher. Les reglages du
    serveur sont maintenant des onglets, montres au seul administrateur.
    """
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    page = _page_servie(c)
    assert 'id="sec-parametres"' in page
    assert "acct-configuration" not in page   # l'entree en double a disparu
    # Les onglets d'administration sont dans la page, mais masques par
    # defaut (CSS) et n'apparaissent que si le role injecte est admin.
    assert '.fiche-onglets button[data-admin]{display:none}' in page
    assert "if(estAdmin) document.querySelectorAll('#param-onglets button[data-admin]')" in page
    for onglet in ("param-compte", "param-securite", "param-email"):
        assert f'id="{onglet}"' in page
    # Les alertes vivent avec le serveur d'envoi, dans un seul onglet.
    assert 'id="param-alertes"' not in page




def test_aucun_onglet_de_parametres_ne_reste_vide():
    """Un onglet sans rien a regler pour ce compte ne s'affiche pas."""
    page = _page_panneau("dashboard.html")
    assert "function majOngletsParametres()" in page
    assert page.count("majOngletsParametres();") >= 2




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


# ---------- 12 quater. masquer une application de SON hub ----------
#
# Ce n'est ni un droit retire, ni une application arretee : le projet
# continue de tourner, les autres comptes le voient, et son adresse reste
# ouverte a qui la connait. C'est du RANGEMENT -- et l'interface doit le dire,
# pour que personne ne croie avoir ferme quelque chose.




def test_une_application_masquee_quitte_le_hub_de_ce_compte_seulement(hub):
    c = _connexion_marie(hub)
    assert c.post("/api/mes-apps/compta/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert "compta" not in [a["name"] for a in d["apps"]]
    assert [a["name"] for a in d["masquees"]] == ["compta"]

    # L'administrateur, lui, la voit toujours : masquer n'est pas supprimer.
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert "compta" in [a["name"] for a in admin.get("/api/mes-apps").get_json()["apps"]]




def test_masquer_ne_ferme_aucun_acces(hub):
    """Le point a ne pas se tromper : l'application reste ouverte a qui
    connait son adresse. Croire le contraire ferait prendre un rangement
    pour une protection."""
    c = _connexion_marie(hub)
    c.post("/api/mes-apps/compta/masquer")
    # Le proxy ne regarde pas la liste des masquees : il regarde les droits.
    # 502 ici veut dire qu'il a cherche a joindre l'application (elle
    # n'ecoute pas dans un test) -- donc qu'il a laisse passer.
    r = c.get("/compta/", follow_redirects=False)
    assert r.status_code != 403, "masquer ne doit pas fermer l'acces"
    assert r.status_code != 404, "l'application doit rester joignable"




def test_on_ne_masque_que_ce_qu_on_peut_deja_voir(hub):
    """Sinon la liste des masquees revelerait l'existence de projets qu'on
    n'a pas le droit de connaitre."""
    c = _connexion_marie(hub)
    assert c.post("/api/mes-apps/secret/masquer").status_code == 404
    assert c.post("/api/mes-apps/fantome/masquer").status_code == 404
    assert app.lire_masquees() == {}




def test_le_masquage_est_journalise_pour_l_administrateur(hub):
    """Une application qui disparait d'un hub sans que personne n'ait touche
    aux droits doit rester explicable."""
    c = _connexion_marie(hub)
    c.post("/api/mes-apps/compta/masquer")
    c.delete("/api/mes-apps/compta/masquer")
    actions = [(e.get("qui"), e.get("app"), e.get("action"))
               for e in app.lire_acces() if e.get("genre") == "masquage"]
    assert ("marie", "compta", "masque") in actions
    assert ("marie", "compta", "affiche") in actions




def test_reafficher_rend_l_application_au_hub(hub):
    c = _connexion_marie(hub)
    c.post("/api/mes-apps/compta/masquer")
    assert c.delete("/api/mes-apps/compta/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert "compta" in [a["name"] for a in d["apps"]]
    assert d["masquees"] == []
    # Aucune entree vide ne traine derriere : le fichier reste lisible.
    assert app.lire_masquees() == {}




def test_l_administrateur_range_son_hub_comme_les_autres(hub):
    """Il ne figure pas dans le registre des comptes -- d'ou un fichier a
    part -- mais il a les memes yeux et le meme ecran que les autres."""
    c = hub
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.post("/api/mes-apps/vitrine/masquer").status_code == 200
    assert app.masquees_du_compte(app.NOM_ADMIN) == ["vitrine"]
    assert "vitrine" not in [a["name"] for a in c.get("/api/mes-apps").get_json()["apps"]]




def test_masquer_demande_une_session(hub):
    anonyme = app.flask_app.test_client()
    assert anonyme.post("/api/mes-apps/vitrine/masquer").status_code in (401, 403)


# ---------- 13. categories du hub ----------
#
# Une categorie ne donne aucun droit : c'est du rangement. Ce qui doit rester
# vrai, c'est qu'un projet ne porte jamais une categorie qui n'existe pas --
# sinon supprimer une categorie le rendrait invisible dans le hub, range dans
# un tiroir que plus rien n'affiche.




def test_une_categorie_supprimee_ne_reste_pas_collee_a_un_projet(categorise):
    """Le cas qui rendrait un projet invisible dans le hub.

    Le hub n'affiche que les groupes qu'il connait : un projet qui garderait
    « Outils » apres la disparition d'« Outils » ne serait dans aucun groupe.
    """
    r = categorise.put("/api/categories", json={"categories": ["Donnees"]})
    assert r.status_code == 200, r.data
    assert r.get_json()["declasses"] == 1
    assert app.load()["site"]["categorie"] == ""
    hub = categorise.get("/api/mes-apps").get_json()
    assert hub["apps"][0]["categorie"] == ""
    assert hub["categories"] == ["CodeLab", "Donnees"]   # CodeLab, pose d'office




def test_un_projet_ne_prend_pas_une_categorie_inventee(categorise):
    """Une categorie arrive par le reseau : elle se verifie comme le reste."""
    r = categorise.post("/api/add", json={
        "name": "neuf", "path": "/w/neuf", "command": "python3 app.py",
        "categorie": "Inventee"})
    assert r.status_code == 200, r.data
    assert app.load()["neuf"]["categorie"] == ""

    # A l'edition aussi : une categorie connue passe, une inconnue est videe.
    assert categorise.put("/api/app/site", json={
        "path": "/w/a", "command": "x", "categorie": "Donnees"}).status_code == 200
    assert app.load()["site"]["categorie"] == "Donnees"
    assert categorise.put("/api/app/site", json={
        "path": "/w/a", "command": "x", "categorie": "Fantome"}).status_code == 200
    assert app.load()["site"]["categorie"] == ""




def test_deux_tiroirs_pour_la_meme_chose_sont_refuses(categorise):
    """« Outils » et « outils » rangeraient les projets a deux endroits."""
    r = categorise.put("/api/categories", json={"categories": ["Outils", "outils", " Outils "]})
    assert r.status_code == 200, r.data
    assert r.get_json()["categories"] == ["CodeLab", "Outils"]




def test_un_utilisateur_lit_les_categories_mais_n_en_cree_pas(categorise):
    """Le hub d'un compte utilisateur en a besoin pour se ranger ; le
    rangement lui-meme reste une decision d'administration."""
    sel = "bb" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["site"], "cree": 0}})
    categorise.post("/logout")
    _connecte(categorise, "marie", "mot-de-passe-long")
    assert categorise.get("/api/categories").status_code == 200
    assert categorise.put("/api/categories", json={"categories": ["A moi"]}).status_code == 403
    assert app.lire_categories() == ["Outils", "Donnees"]


# ---------- 23 bis. le theme n'existe qu'a un seul endroit ----------
#
# La demande etait "que le style soit facilement modifiable". Ce qui l'en
# empechait n'etait pas le CSS mais la RECOPIE : les memes variables vivaient
# six fois -- trois blocs de theme (clair, sombre automatique, sombre choisi)
# dans chacune des deux pages. Changer un gris demandait six retouches
# identiques, et en oublier une laissait le theme sombre de travers sans que
# rien ne le signale.
#
# Ces deux tests tiennent la propriete, pas la mise en forme : il y a un
# fichier de theme, et les pages n'en redefinissent aucun.




def test_le_panneau_tient_en_un_fichier_range_en_parties():
    """Vecu : « un fichier par type si possible ». Les quinze morceaux de
    serveur/ sont revenus dans app.py, chacun sous sa banniere, dans leur
    ordre ; il n'y a plus de dossier a charger. Les fonctions vivent dans
    app : un remplacement par monkeypatch.setattr(app, ...) atteint leurs
    appels."""
    dossier = os.path.join(DOSSIER_PANNEAU, "app")
    assert not os.path.exists(os.path.join(dossier, "serveur"))
    source = _source_du_panneau()
    parties = re.findall(r"^# (\d\d)-[a-z-]+ : [A-Z]", source, re.M)
    assert parties == [f"{i:02d}" for i in range(1, 16)], parties
    assert inspect.getsourcefile(app._proxy).endswith(os.path.join("app", "app.py"))
    assert app._proxy.__globals__ is vars(app)


# ---------------- le script des pages doit s'ANALYSER ----------------
#
# Toute l'interface du panneau tient dans une balise <script> de plusieurs
# milliers de lignes, servie telle quelle. Une parenthese en trop et la page
# entiere ne fait plus rien : pas de liste d'applications, pas de bouton, pas
# la moindre erreur visible ailleurs que dans la console du navigateur. Aucun
# test ne le voyait -- ils lisent le fichier comme du TEXTE, et un texte est
# toujours valide.
#
# node est present dans l'image app-manager (il construit les applications) et
# sur les machines de CI. La ou il manque, le test le dit et s'arrete : mieux
# vaut une ligne ignoree, visible, qu'une couverture qu'on croit avoir.




@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node absent de cette image (present dans app-manager et en CI)")
def test_le_script_des_pages_du_panneau_est_analysable():
    import subprocess
    import tempfile
    for nom in ("dashboard.html", "login.html", PAGE_MESSAGES):
        scripts = _scripts_de_la_page(nom)
        assert scripts, f"{nom} n'a plus de script : la page ne ferait plus rien"
        for i, script in enumerate(scripts):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as f:
                f.write(script)
                chemin = f.name
            try:
                r = subprocess.run(["node", "--check", chemin],
                                   capture_output=True, text=True, timeout=60)
            finally:
                os.unlink(chemin)
            assert r.returncode == 0, (
                f"{nom}, script #{i} : la page ne s'executerait pas du tout\n"
                + (r.stderr or "")[:800])




# ---------------- les parametres : on replie, on ne deroule plus ----------
#
# Vecu : « Reduis toutes les zones des parametres, je dois pouvoir developper
# pour parametrer, et reduire. » Un onglet deroulait jusqu'a six cartes
# ouvertes -- pour changer une ligne, il fallait traverser tout le reste.

def test_chaque_carte_de_reglages_se_replie():
    """La transformation est faite une fois pour toutes les cartes, et non
    ecrite a la main dans chacune : une carte ajoutee plus tard doit se
    replier sans que personne n'ait a y penser."""
    page = _page_panneau("dashboard.html")
    assert "function plierLesCartes()" in page
    assert ".onglet-p .settings-card:not(.pliable)" in page, (
        "le pliage doit viser toutes les cartes de parametres")
    # Repliee par defaut : c'est tout l'objet de la demande.
    assert ".settings-card.pliable>.card-corps{display:none" in page
    assert ".settings-card.pliable.ouverte>.card-corps{display:block}" in page
    # La pastille d'etat reste lisible carte fermee : c'est ce qu'on vient
    # verifier avant de decider d'ouvrir.
    assert ".settings-card.pliable>.card-top>.pill" in page
    # Et la poignee repond au clavier, pas seulement au pointeur.
    assert "tete.addEventListener('keydown'" in page
    assert "aria-expanded" in page




def test_l_etat_d_une_carte_est_retenu_par_son_titre():
    """Une cle fondee sur la position rouvrirait des cartes fermees des
    qu'on en reordonne une."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function pliCle(")[1].split("}")[0]
    assert "card-name b" in bloc and "textContent" in bloc
    assert "indexOf" not in bloc and "index" not in bloc




def test_la_deconnexion_est_rouge_aux_deux_endroits():
    """Deux boutons pour la meme action : ils doivent se ressembler. Un
    « Deconnexion » gris dans les parametres et rouge dans le menu se lit
    comme deux choses differentes."""
    page = _page_panneau("dashboard.html")
    declencheurs = page.split('data-agir="doLogout"')[:-1]
    assert len(declencheurs) == 2, "il y a deux boutons de deconnexion"
    for avant in declencheurs:
        balise = avant[avant.rindex("<"):]
        assert "acct-sortir" in balise or "btn-danger-quiet" in balise, (
            "un declencheur de deconnexion sans marque rouge : " + balise)
    assert ".acct-item.acct-sortir{color:var(--err)}" in page




def test_l_apparence_se_choisit_dans_une_liste_a_icones():
    """Trois mots colles dans un interrupteur segmente se lisent comme un
    reglage binaire mal compte. Une ligne par choix, avec son icone."""
    page = _page_panneau("dashboard.html")
    bloc = page.split('class="choix-liste" id="theme-toggle"')[1].split("</div>")[0]
    for choix in ("auto", "light", "dark"):
        assert f'data-t="{choix}"' in bloc
    # Une icone et une coche par ligne : trois choix, six svg.
    assert bloc.count("<svg") == 6, bloc.count("<svg")
    # applyTheme continue de piloter la liste : sans cela le choix actif ne
    # se verrait nulle part.
    assert "#theme-toggle button" in page




def test_une_case_a_cocher_n_est_pas_un_champ_de_saisie():
    """La regle input{width:100%} s'appliquait aussi aux cases : elles
    s'etiraient sur toute la largeur et leur intitule tombait a la ligne en
    dessous, sans lien visible entre les deux."""
    page = _page_panneau("dashboard.html")
    assert "input[type=checkbox],input[type=radio]{width:auto" in page
    assert "label:has(> input[type=checkbox])" in page




def test_les_preferences_d_affichage_ne_sont_plus_une_carte():
    """Le reglage 25/50 vit maintenant au-dessus du tableau qu'il concerne.
    La carte qui restait dans le compte ne parlait plus que d'elle-meme."""
    page = _page_panneau("dashboard.html")
    for trace in ("pref-taille", "prefTaille", "pref-msg", "Préférences d'affichage"):
        assert trace not in page, trace




def test_les_deux_pages_lisent_le_meme_theme():
    """Un seul fichier de variables, lie par les deux pages."""
    theme = _page_panneau("theme.css")
    # Les jetons structurants y sont, et dans les trois etats de theme.
    for cle in ("--accent:", "--bg:", "--ok:", "--err:", "--warn:", "--mono:", "--r:"):
        assert cle in theme, f"{cle} manque au theme"
    # Pour chacun des styles : celui des applications (:root nu), puis celui
    # du panneau (data-variante="bento"), qui tiennent chacun leurs trois
    # etats. Le style « Design », que le panneau redefinissait en entier et
    # que les applications ne portent pas, est parti.
    classique, variantes = theme.split("LE STYLE DU PANNEAU.", 1)
    variantes, nuancier = variantes.split("le nuancier ---", 1)
    for partie, attendus in ((classique, 3), (variantes, 3)):
        assert partie.count("--accent:") == attendus, (
            "les trois etats de theme doivent etre tenus : clair, sombre du "
            "systeme, sombre choisi explicitement")
    for bloc in (':root[data-variante="bento"]{',
                 ':root[data-variante="bento"]:not([data-theme="light"]):not([data-theme="dark"]){',
                 ':root[data-variante="bento"][data-theme="dark"]{'):
        assert bloc in variantes, bloc
    assert ':root[data-style="design"]' not in theme and "Jakarta" not in theme
    # Plus de style « lanceur » : il est devenu un jeu de reglages du hub.
    assert 'data-variante="lanceur"' not in theme
    assert '[data-theme="dark"]' in theme and "prefers-color-scheme" in theme

    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        assert '<link rel="stylesheet" href="/theme.css">' in page, (
            f"{nom} ne lit pas le theme partage")




def test_aucune_page_ne_redefinit_un_jeton_du_theme():
    """La recopie ne doit pas pouvoir revenir en silence.

    C'est ce test qui donne son sens au precedent : sans lui, on peut lier
    theme.css ET reposer un bloc :root dans la page, qui gagnerait par
    l'ordre de cascade. Le fichier partage serait alors mort sans que
    personne le remarque.
    """
    for nom in ("dashboard.html", "login.html"):
        css = _page_panneau(nom).split("</style>")[0]
        for cle in ("--accent:", "--bg:", "--surface:", "--txt:", "--ok:", "--err:"):
            assert cle not in css, (
                f"{nom} redefinit {cle} : le theme partage ne sert plus a rien, "
                "et les deux pages vont diverger")




# ---------- 23 ter. la police ne vient de nulle part ailleurs ----------
#
# Un panneau auto-heberge qui irait chercher sa police chez Google ferait
# fuiter l'adresse IP de chaque visiteur vers un tiers, et s'afficherait mal
# des que la machine est hors ligne -- c'est-a-dire exactement quand on a
# besoin de lui. La regle etait deja ecrite dans le depot ; elle n'etait
# tenue par rien.

def test_aucune_page_ne_va_chercher_une_police_ailleurs():
    """Aucun appel a un hebergeur de polices, dans aucun fichier servi."""
    for nom in ("theme.css", "dashboard.html", "login.html"):
        texte = _page_panneau(nom)
        for hote in ("fonts.googleapis.com", "fonts.gstatic.com", "use.typekit",
                     "fonts.bunny.net", "cdn.jsdelivr.net"):
            assert hote not in texte, (
                f"{nom} va chercher une police sur {hote} : le panneau ne doit "
                "dependre d'aucun tiers pour s'afficher")




def test_la_police_est_livree_avec_l_image():
    """Elle est declaree, elle est presente, et sa licence l'accompagne."""
    theme = _page_panneau("theme.css")
    assert "@font-face" in theme, "aucune police declaree"
    # On lit le BLOC, pas le fichier : un commentaire qui parle de swap
    # satisfaisait la verification alors que la declaration avait disparu.
    # Trouve en mutant le fichier -- la mutation survivait.
    debut = theme.rindex("@font-face", 0, theme.index('font-family:"Manrope"'))
    bloc = theme[debut:theme.index("}", debut)]
    assert 'src:url("/polices/manrope-latin.woff2")' in bloc, (
        "la police n'est pas servie par le panneau lui-meme")
    assert "font-display:swap" in bloc, (
        "sans swap, le texte reste invisible tant que la police n'est pas la")

    polices = os.path.join(DOSSIER_PANNEAU, "app")
    fichier = os.path.join(polices, "manrope-latin.woff2")
    assert os.path.isfile(fichier), "le fichier de police manque a l'image"
    with open(fichier, "rb") as f:
        assert f.read(4) == b"wOF2", "ce n'est pas un woff2"
    assert os.path.isfile(os.path.join(polices, "LICENCE-manrope.txt")), (
        "une police redistribuee sans sa licence, c'est une licence violee")


# ---------- 23 quater. les accents ne debordent pas sur les identifiants ----------
#
# Signale par Lucas, et la cause est une campagne precedente : en accentuant
# les textes visibles de l'interface, trois IDENTIFIANTS ont ete accentues au
# passage. Un texte accentue se lit mieux ; un identifiant accentue ne
# correspond plus a rien, et se tait.
#
#   data-activité="..."  dans le balisage, contre [data-activite] dans le
#                        selecteur : le noeud n'etait jamais trouve ;
#   c.dernière           contre le champ derniere renvoye par /api/activite :
#                        undefined, donc ilYA() repondait "jamais".
#
# Resultat : la ligne "derniere connexion" de chaque compte etait morte
# depuis. Aucune erreur, aucune trace -- juste une information absente, ce
# qui est exactement ce qu'aucune relecture ne remarque.




def test_aucun_attribut_data_n_est_accentue():
    """Un nom d'attribut accentue ne correspond a aucun selecteur."""
    page = _page_panneau("dashboard.html")
    fautifs = re.findall(r'data-([A-Za-z0-9_-]*[^\x00-\x7F][A-Za-z0-9_-]*)\s*=', page)
    assert not fautifs, f"attributs data- accentues : {sorted(set(fautifs))}"




def test_chaque_selecteur_data_trouve_son_attribut():
    """Le lien selecteur <-> balisage, tenu dans les deux sens.

    C'est ce test qui aurait attrape le defaut : les deux formes existaient,
    chacune correcte de son cote, mais elles ne se rencontraient jamais.
    """
    page = _page_panneau("dashboard.html")
    cherches = set(re.findall(r'\[data-([A-Za-z0-9_-]+)\]', page))
    poses = set(re.findall(r'data-([A-Za-z0-9_-]+)\s*=', page))
    # dataset.machin pose exactement data-machin : le script y accede par la
    # propriete, et ne pas le compter ferait echouer ce test sur un attribut
    # qui existe bel et bien dans le document.
    for nom in re.findall(r'\.dataset\.([A-Za-z0-9_]+)\s*=', page):
        poses.add(re.sub(r'([A-Z])', lambda m: '-' + m.group(1).lower(), nom))
    orphelins = cherches - poses
    assert not orphelins, (
        f"selecteurs sans attribut correspondant : {sorted(orphelins)} -- "
        "le noeud ne sera jamais trouve")




def test_la_derniere_connexion_lit_le_champ_de_l_api():
    """Le champ s'appelle derniere, sans accent, et c'est /api/activite qui
    le nomme. Le lire accentue donne undefined, et undefined affiche
    "jamais" -- un compte qui vient de se connecter s'annonce alors comme
    ne s'etant jamais connecte."""
    script = _script_panneau()
    assert "c.dernière" not in script, (
        "le champ de l'API s'ecrit derniere, sans accent")
    assert "ilYA(c.derniere)" in script




# ---------- 23 quinquies. le reglage 25/50 est sur le journal ----------
#
# Signale par Lucas : il etait pose sur la liste des COMPTES, qui tient a
# l'ecran, alors que le journal des connexions grandit a chaque visite et
# etait coupe net a 40 lignes, sans que rien ne le dise.

def test_le_journal_des_connexions_se_pagine():
    page = _page_panneau("dashboard.html")
    for cle in ('id="ac-taille"', 'id="ac-pagination"', 'id="ac-prec"',
                'id="ac-suiv"', 'id="ac-compte"'):
        assert cle in page, f"{cle} manque : le journal ne se pagine pas"
    script = _script_panneau()
    assert "function acParPage()" in script and "function acAllerA(" in script
    assert "slice(0,40)" not in script, (
        "la coupe seche a 40 lignes est revenue : ni reglable, ni annoncee")




def test_la_liste_des_comptes_ne_se_pagine_plus():
    """Deux paginations sur la meme page, dont une inutile, se confondent."""
    page = _page_panneau("dashboard.html")
    for cle in ('id="us-taille"', 'id="us-prec"', 'id="us-suiv"'):
        assert cle not in page, f"{cle} est revenu sur la liste des comptes"


# ---------- 23 septies. l'accent ne deborde sur AUCUN identifiant ----------
#
# La verification precedente ne couvrait que les attributs data-. Six autres
# identifiants accentues dormaient dans la page, tous nes de la meme campagne
# d'accentuation des textes visibles :
#
#   la classe qui grise une application arretee, accentuee dans le gabarit
#     et sans accent dans le CSS : l'application n'etait plus grisee dans le
#     hub, et restait CLIQUABLE -- le lien ne menant qu'a une page d'erreur ;
#   les deux classes de couleur du bouton demarrer / arreter, accentuees de
#     meme : le bouton de la fiche perdait sa couleur ;
#   une variable declaree sans accent et relue avec, DEUX fois :
#     ReferenceError a l'ouverture d'une fiche, le rendu s'arretait la ;
#   deux champs d'objet accentues contre les noms renvoyes par l'API : les
#     cases d'autorisation ne se cochaient plus, et chaque cle d'acces
#     s'affichait "Ajoutee jamais".
#
# Un texte accentue se lit mieux. Un identifiant accentue ne correspond plus
# a rien -- et selon l'endroit, il se tait ou il leve.




def test_aucune_classe_css_n_est_accentuee():
    """Une classe accentuee ne correspond a aucune regle : le style saute."""
    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        classes = set()
        for m in re.finditer(r'class="([^"]*)"', page):
            classes.update(m.group(1).split())
        fautives = sorted(c for c in classes
                          if re.search(r"[^\x00-\x7F]", c))
        assert not fautives, f"{nom} : classes accentuees {fautives}"




def test_aucune_balise_html_n_est_accentuee():
    """Un nom de BALISE accentue donne un element inconnu, silencieusement.

    Trouve dans l'assistant VPS : <detabils> ecrit avec un accent n'etait
    plus un <details>. Les quatre fichiers de configuration s'affichaient
    donc deroules d'un coup, au lieu d'etre replies -- la page etait noyee,
    et rien n'indiquait pourquoi.
    """
    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        fautives = sorted(set(
            m.group(1) for m in re.finditer(
                r"</?([A-Za-z0-9]*[^\x00-\x7F\s/>][A-Za-z0-9]*)[\s/>]", page)))
        assert not fautives, f"{nom} : balises accentuees {fautives}"




def test_aucun_identifiant_javascript_n_est_accentue():
    """Ni une variable, ni un champ d'objet.

    Une variable accentuee lue sans etre declaree LEVE (ReferenceError) et
    arrete le rendu en cours ; un champ accentue vaut undefined et se tait.
    Les deux viennent de la meme erreur, et aucun des deux ne doit passer.
    """
    _, script = _script_panneau_html()
    lus = set()
    # Le premier caractere peut lui-meme porter l'accent (etape) : le motif
    # ne doit donc PAS exiger un caractere ASCII devant. C'est ce que la
    # premiere version supposait, et "etape" lui a echappe.
    for m in re.finditer(r"\$\{\s*([\w$\u00C0-\u024F]*[^\x00-\x7F][\w$\u00C0-\u024F]*)",
                         script):
        lus.add(m.group(1))
    for m in re.finditer(r"\.([\w$\u00C0-\u024F]*[^\x00-\x7F][\w$\u00C0-\u024F]*)\b",
                         script):
        lus.add(m.group(1))
    assert not lus, f"identifiants JavaScript accentues : {sorted(lus)}"




# ---------- 23 nonies. revenir au hub, et voir plus loin ----------

def test_le_ruban_de_retour_refuse_les_cas_risques():
    """Injecter dans la page d'une application ne se fait pas a l'aveugle.

    Quatre refus, et chacun evite de casser quelque chose : un code autre que
    200, autre chose que du HTML, un corps COMPRESSE (les octets ne
    contiennent alors pas "</body>"), et l'absence de </body>.
    """
    src = _page_panneau("app.py")
    bloc = src[src.index("def injecter_ruban("):src.index("def _proxy(")]
    for garde in ("status != 200", "text/html", "content-encoding", "rfind"):
        assert garde in bloc, f"le refus sur {garde} a disparu"
    # Content-Length doit suivre, sinon le navigateur tronque la page juste
    # avant le ruban.
    assert "Content-Length" in bloc

    # Et il ne se pose que pour quelqu'un de connecte : une application
    # publique vue par un visiteur anonyme n'a pas a lui annoncer qu'un
    # panneau existe derriere.
    pose = src[src.index("sortants = [(k, v) for k, v in headers if"):]
    pose = pose[:pose.index("return Response")]
    assert "if is_authed():" in pose




def test_le_ruban_ramene_au_hub_et_pas_a_la_racine_des_applications(monkeypatch):
    """Le lien du ruban etait « / », et « / » a change de sens.

    Depuis que les applications ont leur propre port, la racine de CE port
    n'est pas le hub : la garde des origines y repond « Ce n'est pas le
    panneau ». Cliquer sur « CodeLab » depuis une application tombait donc
    sur une page d'erreur. Le lien doit sortir de l'origine des applications.
    """
    monkeypatch.setitem(app._origines_separees, "actif", True)
    monkeypatch.setattr(app, "APPS_PORT", 9002)
    monkeypatch.setattr(app, "PANEL_PORT", 9001)

    with app.flask_app.test_request_context(
            "/appli/", base_url="http://machine:9002"):
        assert app.lien_panneau() == "http://machine:9001/"
        assert b'href="http://machine:9001/"' in app.ruban_retour()
        # La page d'erreur du proxy (application arretee, acces refuse) sort
        # du meme port et souffrait du meme lien mort.
        assert 'href="http://machine:9001/"' in app._page("T", "M")

    # Sur le port du panneau, le lien relatif reste le bon : il suit le nom
    # d'hote par lequel on est arrive, quel qu'il soit.
    with app.flask_app.test_request_context(
            "/", base_url="http://machine:9001"):
        assert app.lien_panneau() == "/"




def test_un_nom_d_hote_hostile_ne_se_recopie_pas_dans_la_page():
    """request.host vient de l'en-tete Host, donc du client. Il finit dans un
    attribut href : un guillemet bien place en sortirait, et deviendrait du
    script dans la page d'une application."""
    for hostile in ('machine" onmouseover="alert(1)', "ma chine", "<script>",
                    "machine/../x", "machine\r\nX: 1", ""):
        assert app.hote_propre(hostile) == "", f"{hostile!r} est passe"
    # Un nom d'hote ordinaire passe, lui : une garde qui refuse tout ne
    # laisserait jamais le lien sortir de l'origine des applications.
    assert app.hote_propre("codelab.local:9002") == "codelab.local"
    assert app.hote_propre("192.168.1.42") == "192.168.1.42"

    # Bout en bout : le lien retombe sur le relatif plutot que d'ecrire une
    # adresse douteuse. (Werkzeug ecarte deja ces Host de son cote -- la
    # garde ci-dessus ne depend pas de lui pour autant.)
    ancien = app._origines_separees["actif"]
    ancien_apps = app.APPS_PORT
    app._origines_separees["actif"] = True
    app.APPS_PORT = 9002
    try:
        with app.flask_app.test_request_context(
                "/appli/", environ_overrides={"HTTP_HOST": 'x" onmouseover="1',
                                              "SERVER_PORT": "9002"}):
            assert app.lien_panneau() == "/"
            assert b'href="/"' in app.ruban_retour()
        with app.flask_app.test_request_context(
                "/appli/", environ_overrides={"HTTP_HOST": "codelab.local:9002",
                                              "SERVER_PORT": "9002"}):
            assert app.lien_panneau() == f"http://codelab.local:{app.PANEL_PORT}/"
    finally:
        app._origines_separees["actif"] = ancien
        app.APPS_PORT = ancien_apps




def test_sans_adresse_utilisable_on_ne_redirige_pas_en_rond():
    """Une cible relative renverrait la requete vers elle-meme, et le
    navigateur tournerait en rond jusqu'a abandonner."""
    src = _page_panneau("app.py")
    bloc = src.split("def separer_les_origines():")[1].split("\n\n\n")[0]
    assert "if not origine_applications():" in bloc
    assert "return None" in bloc




def test_le_ruban_est_construit_a_chaque_page():
    """Une constante d'octets ne peut pas porter un lien qui depend de
    l'origine : le ruban doit etre fabrique au moment de l'injection."""
    src = _page_panneau("app.py")
    assert "RUBAN_RETOUR" not in src, (
        "le ruban est redevenu une constante : son lien ne peut plus suivre "
        "l'origine qui sert la page")
    bloc = src[src.index("def injecter_ruban("):src.index("def _proxy(")]
    assert "ruban_retour()" in bloc




def test_le_mot_a_l_administrateur_a_sa_propre_rubrique():
    """Il vivait au bas du hub, replie sous les applications : la ou tout le
    monde passe, mais aussi la ou personne ne vient pour ecrire. Les
    reglages sont l'endroit ou l'on va pour AGIR sur son compte, et un onglet
    se trouve par son nom -- ce qu'une carte au bas d'une liste de tuiles ne
    permet pas."""
    page = _page_panneau("dashboard.html")
    # Vecu ensuite : « sur telephone, la partie messages ne doit pas etre
    # dans les parametres mais doit etre une partie a part ». Une rubrique
    # du panneau, plus un onglet des parametres.
    assert '<button data-p="message"' not in page and 'id="param-message"' not in page
    assert '<div id="sec-message" class="section"' in page
    entree = page.split('data-sec="message"')[1].split(">")[0]
    assert 'data-agir="allerA" data-args=\'["message"]\'' in entree
    assert "message:'/messages'" in page

    # Le formulaire est DANS cette rubrique, et plus dans le hub.
    hub = page.split('<div id="sec-hub"')[1].split('<!-- ==================== VUE')[0]
    assert "msg-carte" not in hub, "le formulaire traine encore dans le hub"
    message = page.split('<div id="sec-message" class="section"')[1] \
                  .split('<!-- ==================== ')[0]
    for champ in ('id="msg-cible"', 'id="msg-app"', 'id="msg-texte"', 'id="msg-envoyer"'):
        assert champ in message, champ




def test_une_carte_seule_dans_son_onglet_ne_se_replie_pas():
    """Le pli sert a passer devant ce qu'on ne vient pas regler. Quand il n'y
    a rien d'autre dans l'onglet, il ne fait que cacher la seule chose de la
    page derriere un clic -- vu sur la rubrique Message, ou le formulaire
    disparaissait entierement."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function plierLesCartes(){")[1].split("\n}")[0]
    assert "closest('.onglet-p')" in bloc
    assert "querySelectorAll('.settings-card').length < 2" in bloc




def test_le_hub_ne_garde_que_sa_grille():
    """UNE SEULE VUE. Le hub offrait deux tailles de tuile, retenues par
    navigateur : une decision de plus a prendre pour une page ou l'on ne
    vient que pour ouvrir une application. L'etat d'une application se lit
    dans « Applications » ; ici, la grille d'icones dit « c'est la », et
    c'est tout ce qu'on lui demande."""
    page = _page_panneau("dashboard.html")
    assert "hubTaille(" not in page, "la bascule de taille est revenue"
    assert "codelab.hub.taille" not in page, (
        "le reglage de taille est revenu dans le stockage du navigateur")
    assert "id=\"hub-taille\"" not in page, "le selecteur de taille est revenu"
    # La forme des tuiles ne se regle plus qu'avec les autres reglages du hub
    # (Parametres > Compte > Apparence), garde cote serveur dans un cookie.
    assert "reglageHub('tuiles')==='icones'" in page
    # Ce qu'on accepte de perdre en icone : la description et l'etat sous le
    # nom. C'est le prix pour voir trente projets d'un coup. (Les tuiles
    # sont rendues par la fonction « carte » du hub : hub-icone en icones,
    # bento-carte en cartes.)
    icone = page.split("? (a, e) => { const [c]=etatApp(a);")[1].split(": (a, e) =>")[0]
    assert 'class="hub-icone-nom"' in icone
    assert "bento-carte-desc" not in icone and "badgeEtat(" not in icone
    # Une application arretee reste grisee. Elle reste cliquable : son lien
    # mene a une page qui dit qu'elle est fermee (page_application_fermee),
    # plus a une erreur.
    assert ".hub-icone.off img{opacity:.55" in page




def test_la_tuile_en_mode_icone_n_a_ni_fond_ni_cadre():
    """En mode icone, la carte devenait un carre gris autour d'une petite
    image : trente carres gris alignes font une grille de cases, pas un
    bureau. Ce qu'on vient voir, c'est l'icone."""
    page = _page_panneau("dashboard.html")
    regle = page.split(".hub-icone{")[1].split("}")[0]
    assert "background" not in regle, "la tuile en icone porte un fond"
    assert "border:" not in regle and "box-shadow" not in regle, "la tuile en icone porte un cadre"
    # Le survol aussi : aucune regle ne lui rend un fond des qu'on approche
    # la souris, et c'est justement la qu'on le verrait.
    assert ".hub-icone:hover{" not in page




def test_les_trois_cartes_de_la_vue_d_ensemble_font_la_meme_hauteur():
    """« Applications » etait un cadre transparent contenant trois tuiles
    cadrees : elles commencaient plus bas et finissaient plus haut que
    « Sante » et « Ressources », et les trois colonnes ne s'alignaient sur
    rien."""
    page = _page_panneau("dashboard.html")
    assert ".card:has(> .ov-nums){background:transparent" not in page, (
        "la carte des chiffres redevient un cadre transparent, et ses tuiles "
        "ne s'alignent plus sur les deux voisines")
    # La grille etire deja les colonnes ; ce qui manquait, c'est que le
    # contenu remplisse la carte.
    assert ".card{display:flex;flex-direction:column}" in page
    assert ".card>.ov-nums{flex:1" in page
    # Et que le contenu des deux autres se pose au milieu de ce qui reste.
    assert ".card>.healthbar{margin-top:auto}" in page
    assert ".card>.health-legend{margin-bottom:auto}" in page
    assert ".card>.res-row:first-of-type{margin-top:auto}" in page




def test_la_carte_de_chaleur_ne_defile_pas():
    """Une carte de chaleur se lit d'un coup d'oeil -- c'est sa seule raison
    d'etre. La moitie cachee derriere un glissement horizontal ne se lit
    jamais."""
    page = _page_panneau("dashboard.html")
    zone = page.split(".carte-zone{")[1].split("}")[0]
    assert "overflow" not in zone, "la barre de defilement est revenue"
    # La taille d'une case se CALCULE depuis la place disponible : ni 11 px
    # en dur (qui debordent d'une carte etroite), ni « 1fr » (dont la hauteur
    # dependrait de la largeur, qui dependrait de la hauteur -- le navigateur
    # tranchait ce cercle par des cases de 54 px).
    bloc = page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    assert "$('carte-zone').clientWidth" in bloc, (
        "la carte doit mesurer la place dont elle dispose")
    assert "grid-template-rows:repeat(7,${cote}px)" in bloc, (
        "les lignes suivent la meme taille, sinon les cases ne sont pas carrees")
    # Le cote et l'ecart se calculent dans carteTaille, depuis la largeur mesuree.
    assert "carteTaille(large, colonnes)" in bloc
    bloc = page.split("function carteTaille(large, colonnes){")[1].split("\nfunction ")[0]
    # L'ecart entre les cases se resserre avant que la case ne rapetisse :
    # 52 ecarts de 3 px font 156 px, qui debordent a eux seuls d'une carte
    # etroite.
    assert "for(const e of [3,2,1,0])" in bloc, (
        "sans resserrer l'ecart, la grille deborde meme avec des cases minuscules")
    assert "gap:${ecart}px" in page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    # Et elle se repeint quand la fenetre change : une carte calculee pour
    # 1400 px deborde a 900.
    assert "addEventListener('resize'" in page and "peindreCarte(derniereCarte)" in page




def test_les_tuiles_du_hub_sont_deux_fois_plus_grandes():
    """A 44 px, on ne distinguait plus une application d'une autre. Une
    grille d'icones sert justement a reconnaitre d'un coup d'oeil."""
    page = _page_panneau("dashboard.html")
    icone = page.split(".hub-icone img{")[1].split("}")[0]
    assert "width:88px" in icone and "height:88px" in icone
    # Au telephone, 62 px : quatre par ligne, sans que les noms se touchent.
    assert ':root[data-tuiles="icones"] .hub-icone img{width:62px;height:62px' in page




def test_les_controles_du_navigateur_suivent_le_theme():
    """Une case cochee en bleu Windows et une fleche grise systeme suffisent
    a faire passer la page pour un formulaire pose sur un theme soigne."""
    page = _page_panneau("dashboard.html")
    assert "accent-color:var(--accent)" in page, (
        "sans accent-color, les cases et les boutons radio gardent le bleu du "
        "systeme")
    assert "caret-color:var(--accent)" in page
    bloc = page.split("select{appearance:none")[1].split("}")[0]
    assert "background-image:url(\"data:image/svg+xml" in bloc, (
        "la fleche du navigateur doit etre remplacee par la notre")
    # Et en sombre aussi : une fleche claire sur fond sombre se voit.
    assert ':root[data-theme="dark"] select{background-image' in page
    assert "select option{background:var(--surface)" in page




def test_les_zones_des_parametres_ne_sont_plus_des_boites():
    """Six boites blanches empilees dans un onglet donnaient six rectangles
    qui se disputaient l'attention -- alors qu'aucun n'est un objet."""
    page = _page_panneau("dashboard.html")
    bloc = page.split(".settings-card{")[1].split("}")[0]
    assert "background:transparent" in bloc and "border:0" in bloc
    assert "box-shadow:none" in bloc
    # Il reste de quoi separer deux reglages : sans filet, ils se collent.
    assert "border-bottom:1px solid var(--line)" in bloc




def test_le_reglage_d_affichage_se_deroule_quand_il_y_a_trop_d_applications():
    """Quinze applications faisaient de ce reglage la moitie de la page, pour
    une liste qu'on ouvre trois fois par an."""
    page = _page_panneau("dashboard.html")
    assert "MASQUAGE_SEUIL_DEROULANT" in page
    bloc = page.split("function peindreMasquees(){")[1].split("\n}")[0]
    assert "<details" in bloc, "la liste longue doit se replier"
    assert "if(toutes.length <= MASQUAGE_SEUIL_DEROULANT)" in bloc, (
        "une liste courte ne gagne rien a etre repliee")
    # Redessinee a chaque case cochee, elle se refermerait sous le doigt.
    assert "ouverte" in bloc and "details" in bloc




def test_les_destinataires_d_alerte_vivent_dans_la_fiche_de_l_application():
    """Ils etaient dans les parametres globaux, en une liste de champs ou il
    fallait retrouver la bonne ligne -- et rien, depuis la fiche d'une
    application, ne disait qui serait prevenu si elle tombait."""
    page = _page_panneau("dashboard.html")
    assert 'id="al-par-app"' not in page, (
        "la liste globale des destinataires est revenue")
    assert "peindreAlertesParApp" not in page
    config = page.split('<div class="onglet" id="onglet-config">')[1]
    config = config.split('id="sec-parametres"')[0]
    assert 'id="e-alertes"' in config, (
        "le champ doit etre dans l'onglet Configuration de l'application")
    # Enregistre par le meme bouton que le reste de la configuration.
    envoi = page.split("async function submitEdit(){")[1].split("\n}")[0]
    assert "/api/alertes/application/" in envoi
    # Et relu a l'ouverture de la fiche, sinon le champ parait toujours vide.
    lecture = page.split("function chargerConfigFiche(){")[1].split("\n}")[0]
    assert "$('e-alertes').value" in lecture
    src = _page_panneau("app.py")
    bloc = src.split("def api_apps():")[1].split("\n\n\n")[0]
    assert '"alertes": _adresses(par_app.get(name))' in bloc, (
        "la liste doit arriver avec l'application")




def test_le_masquage_ne_se_regle_que_dans_les_parametres():
    """La croix posee sur chaque tuile transformait le hub en liste a trier,
    et se cliquait par accident. Le reglage vit la ou vivent les reglages."""
    page = _page_panneau("dashboard.html")
    assert "hub-masquer" not in page, (
        "le bouton de masquage est revenu sur la tuile du hub")
    # La tuile n'est plus qu'un lien (et ses trois points de menu).
    tuile = page.split("function peindreBento(")[1].split("const tuile = e =>")[0]
    assert "<button" not in tuile, "la tuile du hub porte de nouveau un bouton"
    # Et le reglage est bien une case a cocher, dans les deux sens.
    assert "function basculerMasquage(" in page
    bascule = page.split("async function basculerMasquage(nom, masquer){")[1]
    bascule = bascule.split("\n}")[0]
    assert "masquer ? 'POST' : 'DELETE'" in bascule, (
        "cocher doit masquer, decocher doit reafficher")




def test_les_parametres_listent_toutes_les_applications_a_cocher():
    """N'afficher que les masquees obligeait a en retirer une depuis le hub
    avant de pouvoir la retrouver ici : la liste doit etre complete."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function peindreMasquees(){")[1].split("\n}")[0]
    assert "(hubApps||[]).concat(hubMasquees||[])" in bloc, (
        "la liste des reglages doit reunir les affichees ET les masquees")
    assert 'type="checkbox"' in bloc
    # La case ne porte plus son appel en attribut : elle nomme son action, et
    # l'ecouteur unique la route. Les deux moities sont verifiees, sinon le
    # test passerait sur une case qui ne fait plus rien.
    assert 'data-changer="app-masquer"' in bloc, (
        "la case ne declare plus son action")
    ecoute = page.split("document.addEventListener('change'")[1].split("\n}")[0]
    assert "'app-masquer'" in ecoute and "basculerMasquage(" in ecoute, (
        "personne n'ecoute la case : la cocher ne masquerait plus rien")
    # Une case cochee doit se lire sans lire l'etiquette : la ligne se retire.
    assert "cachee" in bloc




def test_les_projets_autorises_forment_une_liste_lisible():
    """Une rangee de cases nues ne disait ni de quelle application il
    s'agissait, ni lesquelles etaient publiques -- donc lesquelles cocher ne
    changeait rien."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("$('us-f-projets').innerHTML =")[1].split("</label>`;")[0]
    assert "proj-ligne" in bloc
    assert "icone(a.name)" in bloc, "une ligne sans icone ne se reconnait pas"
    assert "badgeVis(a)" in bloc, "le badge public/prive doit rester visible"
    assert 'type="checkbox"' in bloc
    # Le style existe vraiment : une classe sans regle ne dessine rien.
    assert ".proj-ligne img{" in page
    # Et c'est la LIGNE qu'on choisit, pas la case : elle porte la classe
    # commune a toutes les listes a choix du panneau.
    assert "choix-ligne" in bloc
    assert ".choix-ligne:has(>input:checked)" in page, (
        "rien ne distingue plus une ligne retenue d'une ligne ordinaire")




def test_la_fiche_d_un_compte_attend_la_liste_des_applications():
    """Ouverte avant que /api/apps ne reponde, la rubrique « Projets
    autorises » restait vide sans rien dire."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("async function usOuvrir(nom){")[1].split("\n}")[0]
    assert "if(!apps.length)" in bloc and "await refresh()" in bloc
    assert "usPeindreFiche()" in bloc.split("await refresh()")[1], (
        "il faut repeindre une fois la liste arrivee")




def test_la_rubrique_message_ferme_la_marche():
    """On ne vient pas dans les parametres pour ecrire, on y vient pour
    regler : le mot a l'administrateur a quitte les onglets."""
    page = _page_panneau("dashboard.html")
    onglets = page.split('<div class="fiche-onglets" id="param-onglets">')[1]
    onglets = onglets.split("</div>")[0]
    noms = re.findall(r'data-p="([a-z]+)"', onglets)
    assert "message" not in noms, f"ordre des onglets : {noms}"
    assert noms[0] == "compte", "le compte reste la premiere rubrique"




def test_le_bandeau_n_a_plus_de_recherche():
    """Le menu et la marque a gauche, le compte a droite : la barre de
    recherche de l'entete est retiree, et rien ne filtre plus en silence le
    hub ou la liste des applications."""
    page = _page_panneau("dashboard.html")
    barre = page.split('<div class="topbar">')[1].split('<div class="acct-wrap">')[0]
    groupe = barre.split('<div class="topbar-gauche">')[1]
    assert "side-toggle-btn" in groupe[:400] and "topbar-brand" in groupe[:2000]
    assert barre.count('class="spacer"') == 1
    for trace in ('class="rechercher"', 'id="q"', "onRecherche", "correspond(", ".rechercher{"):
        assert trace not in page, trace




def test_aucun_texte_du_hub_n_a_perdu_ses_accents():
    """Les textes AFFICHES portent leurs accents -- le code qui les entoure,
    lui, n'en porte aucun. C'est la regle de ce depot, et elle se relachait a
    chaque ajout : « Sante », « Visibilite », « Presentation », « Repeter »,
    « Identite » sont tous arrives comme cela.

    Le HTML ET les phrases du script : la moitie de l'interface est ecrite
    par la page elle-meme, et un test qui l'ignore laisse passer « limitee a
    ce que tu leur autorisés » -- vu sur la page d'accueil.
    """
    page = _page_panneau("dashboard.html")
    fautes = []
    for texte in _textes_visibles(page) + _phrases_du_script(page):
        for mot in MOTIF_SANS_ACCENT.findall(texte):
            fautes.append(f"{mot} — dans « {texte[:70]} »")
    assert not fautes, "textes visibles sans accents :\n" + "\n".join(fautes[:12])




def test_aucun_imperatif_n_est_ecrit_au_participe_passe():
    """« Déclaré-la », « Réglé-le », « modifié-le là-bas » : un participe
    passe la ou le francais demande un imperatif. La faute vient d'une passe
    d'accentuation trop zelee -- on accentue « declare » sans regarder si la
    phrase donne un ordre.

    La regle mecanique qui les attrape toutes : un verbe en -é suivi d'un
    trait d'union et d'un pronom est TOUJOURS faux. « Déclare-la »,
    « règle-le », « modifie-le » : l'imperatif ne prend pas d'accent final.
    """
    motif = re.compile(r"[A-Za-zÀ-ÿ]+é-(?:le|la|les|y|en|moi|toi)\b")
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "app.py"),
                   os.path.join(DOSSIER_DIAGNOSTIC, "app.py")):
        src = _lire_source(chemin)
        for faute in motif.findall(src):
            fautes.append(f"{os.path.basename(chemin)} : {faute}")
    assert not fautes, "imperatifs au participe passe :\n" + "\n".join(fautes)




def test_les_phrases_qui_donnent_un_ordre_le_donnent_en_francais():
    """Les memes fautes sans trait d'union, que la regle mecanique
    ci-dessus ne peut pas attraper : « Déclaré un dossier », « Créé un
    compte », « réglé les destinataires ». Une liste, donc -- courte, et
    nourrie de ce qui a ete trouve en place."""
    fautives = ["Déclaré un", "Déclaré l'", "Déclaré d'abord", "Créé un",
                "créé un compte", "réglé les destinataires", "Réglé-le",
                "Activé-la", "Ajouté un", "Supprimé le"]
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "app.py")):
        src = _lire_source(chemin)
        for faute in fautives:
            if faute in src:
                fautes.append(f"{os.path.basename(chemin)} : « {faute} »")
    assert not fautes, "participe passe au lieu d'un imperatif :\n" + "\n".join(fautes)




def test_les_etiquettes_d_un_seul_mot_portent_leurs_accents():
    """« Desactivee », « Actives » : les pastilles d'etat font UN mot, et le
    controle des phrases ne les voyait pas -- il ne regarde que ce qui
    compte trois mots ou plus. Ce sont pourtant les textes les plus lus de
    la page : on les relit a chaque coup d'oeil.

    On ne cherche que dans ce qui est manifestement une etiquette : entre
    guillemets ou entre balises. Sans cela, totpDesactiver() -- un nom de
    fonction, qui ne s'affiche nulle part -- serait signale.
    """
    motif = re.compile(r"""(?<=['">])(Desactivee?s?|Desactiver|Activee|Activees|"""
                       r"""Arretee|Arretees|Demarree|Reussi|Echoue)(?=['"<])""")
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html")):
        page = _lire_source(chemin)
        for faute in motif.findall(page):
            fautes.append(f"{os.path.basename(chemin)} : « {faute} »")
    assert not fautes, "etiquettes sans accents :\n" + "\n".join(fautes)




def test_la_preposition_a_garde_son_accent_devant_un_infinitif():
    """« rien a taper », « A activer ci-dessous », « A poser une fois le TLS
    en place » : c'est la preposition, pas le verbe avoir -- qui ne se suit
    d'ailleurs jamais directement d'un infinitif."""
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html")):
        page = _lire_source(chemin)
        for texte in _textes_visibles(page) + _phrases_du_script(page):
            texte = " ".join(texte.split())
            for faute in MOTIF_A_INFINITIF.findall(texte):
                fautes.append(f"{os.path.basename(chemin)} : « {faute} » "
                              f"dans « {texte[:70]} »")
    assert not fautes, "« a » au lieu de « à » :\n" + "\n".join(fautes[:12])




def test_aucun_participe_passe_ne_tient_lieu_de_verbe_conjugue():
    """« la limite de tentatives ne protégé plus rien » : l'accent de trop,
    pose par une relecture qui accentue sans lire la phrase."""
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html")):
        page = _lire_source(chemin)
        for texte in _textes_visibles(page) + _phrases_du_script(page):
            texte = " ".join(texte.split())
            for faute in MOTIF_PARTICIPE_SANS_AUXILIAIRE.findall(texte):
                fautes.append(f"{os.path.basename(chemin)} : « {faute} »")
    assert not fautes, "participe passe sans auxiliaire :\n" + "\n".join(fautes)




def test_aucun_texte_de_la_page_de_connexion_n_a_perdu_ses_accents():
    """La page de connexion echappait au controle, et cela se voyait :
    « Applications centralisees », « Heberge chez toi », « Accede a tes
    applications », « Connexion chiffree cote serveur ». C'est pourtant la
    PREMIERE page que voit quelqu'un a qui l'on partage une application."""
    page = _page_panneau("login.html")
    fautes = []
    for texte in _textes_visibles(page) + _phrases_du_script(page):
        for mot in MOTIF_SANS_ACCENT.findall(texte):
            fautes.append(f"{mot} — dans « {texte[:70]} »")
    assert not fautes, "textes visibles sans accents :\n" + "\n".join(fautes[:12])




def test_aucun_texte_du_diagnostic_n_a_perdu_ses_accents():
    """Meme regle pour l'application diagnostic : c'est une page de CodeLab,
    pas une application invitee."""
    fautes = []
    for texte in _textes_du_diagnostic() + _messages_des_sondes():
        if texte.startswith("{") or "checks." in texte:
            continue                          # une expression Python, pas un texte
        for mot in MOTIF_SANS_ACCENT.findall(texte):
            fautes.append(f"{mot} — dans « {texte[:70]} »")
    assert not fautes, "textes visibles sans accents :\n" + "\n".join(fautes[:12])


# ---------- l'accent ne doit pas deborder sur un NOM DE VARIABLE ----------
#
# La relecture des messages a accentue quatre identifiants au passage :
# len(clés), {détail_hotes}, {numéro} et {après - avant}. Chacun leve
# NameError a l'execution, et aucun ne se voit a la lecture -- dans une
# f-string, « clés » a exactement l'air d'un mot de la phrase.
#
# Deux d'entre eux vivaient sur le chemin NORMAL de check_cles_ssh : sur une
# installation ou authorized_keys est lisible, la page entiere du diagnostic
# tombait. Les deux autres faisaient echouer un test approfondi avec un
# message qui parlait d'autre chose.
#
# Ce controle lit l'arbre syntaxique et cherche les noms CHARGES qui ne sont
# definis nulle part. C'est exactement ce que fait un interpreteur au moment
# d'evaluer la ligne -- sauf qu'ici on le sait avant de livrer.




def test_aucun_accent_n_a_deborde_sur_un_nom_de_variable():
    """Le test qui aurait vu passer len(clés) et {numéro}."""
    fichiers = [os.path.join(DOSSIER_DIAGNOSTIC, nom)
                for nom in ("checks.py", "app.py")]
    if DOSSIER_PANNEAU:
        fichiers.append(os.path.join(DOSSIER_PANNEAU, "app", "app.py"))
    for chemin in fichiers:
        if not os.path.exists(chemin):
            continue
        inconnus = _noms_jamais_definis(chemin)
        assert not inconnus, (
            f"{os.path.basename(chemin)} charge des noms qui n'existent pas : "
            f"{inconnus} -- un accent a deborde sur une variable, et cela leve "
            f"NameError a l'execution")




# ---------- 30 ter. la tuile d'un service ressemble aux autres ----------

def test_aucune_tuile_ne_porte_de_cadre_a_part():
    """Demande, en deux temps.

    D'abord : « L'app Dagster ne doit pas avoir ce contour blanc. Seul le
    logo doit avoir un fond comme les autres applications. » Dagster portait
    un fond ET une bordure au niveau de la TUILE, pour se detacher du hub --
    une vignette cerclee de blanc au milieu de projets qui n'en ont pas.

    Puis, capture a l'appui : « Retire le fond derriere dagster. Je veux
    juste un contour autour du logo qui fait la meme taille que l'icone de
    diagnostic. » Le fond plein pose sous l'image refaisait la meme vignette
    en plus petit. Ce qu'il fallait n'etait pas une plaque mais un CONTOUR.

    Puis : « reduis la taille du logo pour que le rendu soit plus
    esthetique. » Le logo touchait son cadre.

    Enfin : « ajoute un fond derriere dagster quand meme. »

    Le fond revient donc, et ce n'est pas un aller-retour pour rien : ce qui
    le rendait laid les deux premieres fois, ce n'etait pas le fond, c'etait
    son ABSENCE DE MARGE. Sur la tuile, la plaque debordait l'icone et le nom
    tombait en dehors ; sous l'image sans marge, le logo touchait les bords.
    Avec la marge, les trois ensemble donnent une plaque de la taille exacte
    des autres icones et un logo centre -- la forme d'une icone
    d'application.

    La regle vise l'ICONE et pas la tuile : ce qui distingue Dagster n'est pas
    d'etre un service, c'est que son logo est TRANSPARENT la ou les projets
    deposent une image pleine. Habiller la tuile traitait la mauvaise
    difference -- c'etait la vignette de depart.
    """
    page = _page_panneau("dashboard.html")

    # LA TUILE elle-meme n'a rien de particulier : c'est la vignette cerclee
    # de blanc qu'on a retiree, et c'est elle qui ne doit pas revenir.
    # (Les tuiles en icone sont rendues par .hub-icone.)
    tuile = page.split(".hub-icone{")[1].split("}")[0]
    assert "background" not in tuile and "border:" not in tuile, tuile
    assert ".hub-icone.service-stack{" not in page, "la regle habille la tuile, pas l'icone"

    # L'ICONE d'un service, elle, a sa plaque. LA MARGE fait la difference :
    # un fond sans marge donne un carre gris derriere un dessin qui touche
    # les bords ; avec la marge, c'est une plaque d'icone et un logo centre.
    # Un jeton A PART, et pas --surface : en theme sombre celui-ci vaut
    # #141b23 sur un fond #0d1218, soit 1,08:1 -- la plaque y etait
    # invisible. Voir --plaque-logo dans theme.css.
    icone = page.split(".hub-icone.service-stack img{")[1].split("}")[0]
    assert "background:var(--plaque-logo)" in icone, icone
    assert "padding:" in icone, icone
    # box-sizing:border-box fait tenir la marge DEDANS : sans lui, l'icone du
    # service serait plus grande que celle du diagnostic.
    assert "box-sizing:border-box" in icone, icone

    # Les 88 px sont la mesure commune. "border:" et non "border" :
    # border-radius est legitime ici, c'est le coin arrondi commun.
    commune = page.split(".hub-icone img{")[1].split("}")[0]
    assert "width:88px;height:88px" in commune, commune
    assert "border:" not in commune, commune


# ------------------------- la liste des reglages -----------------------




def test_une_ligne_de_rangement_ne_porte_que_le_nom_et_la_case():
    """« Rends la liste plus legere. »

    Chaque ligne portait l'icone, le nom, la description complete et le mot
    « Affichée » ou « Masquée » : quatre informations pour un reglage qui en
    a une. La description est deja sur la tuile du hub, et l'etat est dans la
    case -- cochee veut dire affichee, c'est tout le propos.
    """
    bloc = _bloc_liste_rangement()
    assert "a.description" not in bloc, (
        "la description est revenue dans la ligne : deux hauteurs de ligne "
        "differentes selon que l'application en a une")
    assert "masquee-etat" not in bloc, (
        "le libelle « Affichée / Masquée » est revenu : il repete la case")
    assert "nomAffiche(a)" in bloc and "app-masquer" in bloc, (
        "une ligne doit toujours porter le nom et la case")




def test_la_liste_du_rangement_est_batie_sur_les_quatre_sources():
    """Sans les services, Dagster se masque par l'API et ne se remet plus.

    La verification porte sur L'EXPRESSION qui construit la liste, et pas sur
    la presence des noms dans la fonction : un mutant qui remplacait
    « .concat(hubServices||[]) » par « .concat([]) » laissait les deux noms
    ailleurs -- dans le commentaire au-dessus et dans le calcul des cochees --
    et passait au travers. C'est ce mutant qui a ecrit ce test-ci.
    """
    page = _page_panneau("dashboard.html")
    debut = page.index("function peindreMasquees(){")
    fin = page.index("}", page.index("const lignes", debut))
    corps = page[debut:fin]
    depart = corps.index("const toutes =")
    expression = corps[depart:corps.index(";", depart)]
    for source in ("hubApps", "hubMasquees", "hubServices", "hubServicesMasques"):
        assert source in expression, (
            f"la liste du rangement ne part pas de {source} : "
            f"« {expression.strip()} »")




def test_la_carte_du_rangement_ne_dit_plus_l_inverse_de_ce_qu_elle_fait():
    """Elle disait « Coche une application pour la retirer de ton hub ».

    Le code fait l'inverse depuis longtemps -- cochee veut dire AFFICHEE --
    et la phrase etait restee. Sur un reglage dont tout l'interet est le sens
    de la case, c'est la seule phrase qu'il fallait avoir juste.
    """
    page = _page_panneau("dashboard.html")
    debut = page.index('<b>Applications du hub</b>')
    carte = page[debut:debut + 900]
    assert "Cochée, elle est affichée" in carte
    assert "Coche une application pour la retirer" not in carte
    # Le rangement n'est pas une fermeture : la carte doit continuer de le dire.
    assert "rangement" in carte




# ---------- adresses propres ----------

@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node absent de cette image (present dans app-manager et en CI)")
def test_le_script_du_panneau_tient_en_un_fichier_range_en_parties():
    """Vecu : « un fichier par type si possible ». Les neuf modules de
    panneau/ sont revenus dans panneau.js, chacun sous sa banniere et dans
    son ordre ; le fichier s'analyse, et c'est lui que sert /panneau.js."""
    import subprocess
    chemin = os.path.join(DOSSIER_PANNEAU, "app", "panneau.js")
    assert not os.path.exists(os.path.join(DOSSIER_PANNEAU, "app", "panneau"))
    r = subprocess.run(["node", "--check", chemin], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    source = open(chemin, encoding="utf-8").read()
    assert re.findall(r"^   (0\d)-[a-z]+ : ", source, re.M) == [f"0{i}" for i in range(9)]
    assert app.PANNEAU_JS == source


def test_aucune_fonction_du_panneau_n_est_declaree_deux_fois():
    """Vecu : dateCourte existait dans 02-hub.js ET dans 08-reglages.js. Les
    parties partagent une portee : la seconde declaration remplacait la
    premiere sans bruit, et le suivi d'une demande affichait « Realise le »
    une date de janvier 1970 (des secondes lues comme des millisecondes)."""
    source = _lire_source("app-manager/app/panneau.js")
    vues = set()
    for fonction in re.findall(r"^(?:async\s+)?function\s+(\w+)\s*\(", source, re.M):
        assert fonction not in vues, f"{fonction} declaree deux fois"
        vues.add(fonction)




def test_la_feuille_de_style_du_panneau_est_equilibree():
    """Vecu, pendant la fusion du CSS : la fin d'un commentaire est partie
    avec une regle vide ; le « /* » reste ouvert a englouti la regle suivante
    (le compteur du menu a perdu ses coins arrondis), et aucun autre test ne
    l'a vu. Un commentaire ou une accolade desequilibres font disparaitre des
    regles EN SILENCE : le navigateur ne signale rien."""
    for nom in ("dashboard.html", "login.html", PAGE_MESSAGES):
        page = _page_panneau(nom)
        for css in re.findall(r"<style[^>]*>(.*?)</style>", page, re.S):
            assert css.count("/*") == css.count("*/"), f"{nom} : commentaire non ferme"
            sans = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            assert "/*" not in sans and "*/" not in sans, f"{nom} : commentaire imbrique ou orphelin"
            assert sans.count("{") == sans.count("}"), f"{nom} : accolades desequilibrees"




def test_le_script_du_panneau_se_met_en_cache_et_reste_derriere_la_session(deux_espaces):
    """Le script du panneau (panneau.js) est sorti de la page pour se mettre
    en cache. Il ne se sert qu'a une session, garde un an a la bonne
    empreinte, se revalide sinon ; et les valeurs de la session restent dans
    la page, ecrites sans « < » brut."""
    c = deux_espaces
    adresse = f"/panneau.js?v={app.PANNEAU_JS_VERSION}"
    assert c.get(adresse).status_code in (302, 401)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.get(adresse)
    assert r.status_code == 200 and r.mimetype == "text/javascript"
    assert r.headers["Cache-Control"] == "private, max-age=31536000, immutable"
    assert c.get("/panneau.js?v=ancienne").headers["Cache-Control"] == "private, no-cache"
    # Le meme fichier pour tous : aucune valeur de session dedans.
    assert "__JETON__" not in r.get_data(as_text=True) and "CODELAB_PAGE.jeton" in r.get_data(as_text=True)
    assert app._json_dans_un_script("</script>") == '"\\u003c/script>"'




def test_chaque_page_du_panneau_a_son_adresse(deux_espaces):
    """Recharger /parametres/securite laisse sur les parametres : le serveur
    sert le panneau a ces adresses, et renvoie a la connexion sans session,
    avec le chemin a reprendre ensuite."""
    c = deux_espaces
    anonyme = app.flask_app.test_client()
    r = anonyme.get("/parametres/securite")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login?suite=/parametres/securite")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    for chemin in ("/apercu", "/applications", "/applications/site", "/applications/site/journal",
                   "/utilisateurs", "/parametres",
                   "/parametres/securite"):
        r = c.get(chemin)
        assert r.status_code == 200, chemin
        assert "routerDepuisAdresse" in _page_servie(c, chemin), chemin
    # Ces noms ne peuvent plus servir d'adresse a une application -- ni
    # aucun autre premier segment d'une route du panneau.
    reserves = app.adresses_reservees()
    for nom in app.PAGES_DU_PANNEAU + ("invitation", "qr", "polices", "mot-de-passe", "login"):
        assert nom in reserves, nom
    page = _page_panneau("login.html")
    assert "location.href=SUITE" in page
    regle = page.split("const SUITE=(()=>{", 1)[1].split("})();", 1)[0]
    assert "(?![\\/\\\\])" in regle and "'..'" in regle




def test_la_page_suit_l_adresse_et_l_historique():
    page = _page_panneau("dashboard.html")
    assert "window.addEventListener('popstate'" in page
    assert "history.pushState(null, '', a)" in page
    assert "'/parametres/'+paramOngletActif" in page
    # Les messages sont partis dans l'application Messages.
    assert "'/messages/taches'" not in page


# ---------- un seul style, dont le hub se regle ----------




def test_le_style_se_choisit_et_se_pose_cote_serveur(deux_espaces):
    """Le choix vit dans un cookie : le serveur pose les attributs avant la
    premiere peinture. Sans cookie, ou avec celui d'un ancien style, ce sont
    les reglages par defaut."""
    c = deux_espaces
    defaut = DEFAUT_HTML
    assert defaut in c.get("/login").get_data(as_text=True)
    for ancien in ("design", "classique"):
        c.set_cookie("codelab-style", ancien)
        assert defaut in c.get("/login").get_data(as_text=True), ancien
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert defaut in c.get("/parametres").get_data(as_text=True)
    c.set_cookie("codelab-style", '"><script>')
    racine = re.search(r"<html[^>]*>", c.get("/").get_data(as_text=True)).group(0)
    assert "script" not in racine and "codelab-style" not in racine, racine
    page = _page_panneau("dashboard.html")
    assert "function setHub(" in page and 'id="hub-reglages"' in page
    assert "codelab-hub=" in page
    # Plus de choix de style : un seul, qui se regle.
    assert "function setStyle(" not in page and 'id="style-toggle"' not in page




def test_les_parametres_s_ouvrent_depuis_le_menu_du_compte():
    """Le style Design a laisse sa mise en page au panneau : parametres
    depuis le menu du compte, menu en barre du bas sur un telephone.

    Ses jetons sont partis de theme.css, et la police Plus Jakarta Sans
    n'est plus servie par le panneau : plus rien ne la demandait. Le fichier
    reste, l'interface Mailpit le recopie a la construction."""
    racine = _racine_depot()
    assert "jakarta-latin.woff2" not in app.POLICES_SERVIES
    police = os.path.join(racine, "app-manager", "app", "jakarta-latin.woff2")
    assert open(police, "rb").read(4) == b"wOF2"
    assert os.path.exists(os.path.join(racine, "app-manager", "app", "LICENCE-jakarta.txt"))
    page = _page_panneau("dashboard.html")
    # La roue crantee a cote du compte, le menu en pilule (barre du bas sur
    # un telephone), les reglages en fenetre.
    # Pas de roue crantee : les parametres s'ouvrent depuis le menu du compte.
    assert "reglages-btn" not in page
    assert 'id="acct-parametres" data-agir="allerA" data-args=\'["parametres"]\'' in page
    assert '#sec-parametres{display:grid' in page
    assert '.sidebar{top:auto;bottom:0' in page
    # Un compte utilisateur garde ses entrees : le hub et ses messages.
    assert 'class="side-item utilisateur-seul"' in page
    assert "document.body.classList.add('compte-utilisateur')" in page
    login = _page_panneau("login.html")
    # Face ID / Touch ID n'est plus cache dans le style Design : en tete.
    assert "#pk-zone{display:none" not in login





def test_le_hub_se_regle_et_le_serveur_ne_pose_que_des_valeurs_connues(deux_espaces):
    """Vecu : « Ne laisse plus qu'un style en rendant personnalisable
    certains parametres comme le scroll horizontal ou vertical, mettre tous
    les widgets en haut, afficher les app avec juste l'icone ou avec la
    description. »

    Les reglages vivent dans le cookie codelab-hub, des valeurs separees par
    des points, dans n'importe quel ordre. Le serveur les pose sur <html> --
    et rien d'autre que les valeurs connues : le cookie vient du client."""
    c = deux_espaces
    assert set(app.REGLAGES_HUB) == {"tuiles", "defilement", "widgets", "couleurs"}
    c.set_cookie("codelab-hub", "haut.icones.prune.vertical")
    tete = re.search(r"<html[^>]*>", c.get("/login").get_data(as_text=True)).group(0)
    for attendu in ('data-tuiles="icones"', 'data-defilement="vertical"', 'data-widgets="haut"',
                    'data-couleurs="prune"', 'data-variante="bento"'):
        assert attendu in tete, attendu
    # Une partie seulement : le reste garde sa valeur par defaut.
    c.set_cookie("codelab-hub", "icones")
    tete = re.search(r"<html[^>]*>", c.get("/login").get_data(as_text=True)).group(0)
    assert 'data-tuiles="icones"' in tete and 'data-defilement="horizontal"' in tete
    # Rien d'inconnu n'arrive dans la page.
    c.set_cookie("codelab-hub", 'icones" onload="x.vertical')
    tete = re.search(r"<html[^>]*>", c.get("/login").get_data(as_text=True)).group(0)
    assert "onload" not in tete and 'data-defilement="vertical"' in tete
    assert 'data-tuiles="cartes"' in tete
    c.set_cookie("codelab-hub", "")
    assert DEFAUT_HTML in c.get("/login").get_data(as_text=True)
    # L'ancien cookie codelab-style n'est plus lu : les reglages par defaut.
    c.delete_cookie("codelab-hub")
    c.set_cookie("codelab-style", "lanceur")
    assert DEFAUT_HTML in c.get("/login").get_data(as_text=True)
    c.set_cookie("codelab-hub", "cartes")
    assert 'data-tuiles="cartes"' in re.search(r"<html[^>]*>", c.get("/login").get_data(as_text=True)).group(0)

    page = _page_panneau("dashboard.html")
    # Les memes reglages des deux cotes, dans le meme ordre (premiere valeur
    # = defaut).
    regles = page.split("const REGLAGES_HUB = {")[1].split("};")[0]
    for cle, valeurs in app.REGLAGES_HUB.items():
        assert f"{cle}:[" + ",".join(f"'{v}'" for v in valeurs) + "]" in regles, cle
    for cle, valeurs in app.REGLAGES_HUB.items():
        for v in valeurs:
            # La couleur personnalisee passe par son selecteur.
            attendu = ('data-saisir="choisirCouleurPerso"' if (cle, v) == ("couleurs", "perso")
                       else f'data-agir="setHub" data-args=\'["{cle}", "{v}"]\'')
            assert attendu in page, (cle, v)
    # Plus de modeles, d'en-tete centre ni de fond trame : retires a la
    # demande.
    for trace in ("modeleHub", "data-entete", "data-fond", "--trame"):
        assert trace not in page, trace
    # Plus de style « lanceur » ni de chemin a part pour lui.
    for trace in ("data-variante=\"lanceur\"", "peindreWidgetsLanceur", "styleCourant", 'id="hub-widgets"',
                  "const STYLES = "):
        assert trace not in page, trace
    # Dagster, Mailpit et Messages ont la tuile d'une application, dans la
    # meme grille.
    assert "hub-dock" not in page and "peindreDock" not in page
    corps = page.split("function peindreHub(){")[1].split("\n}")[0]
    assert "toutes.filter(correspond)" in corps and "peindreBento(liste, filtre);" in corps
    # Le script ne peint pas le hub avant que les donnees soient la.
    regler = page.split("function appliquerReglagesHub(")[1].split("\n}")[0]
    assert "if(hubApps.length || hubServices.length) peindreHub();" in regler




def test_les_applications_en_icones_ou_en_cartes():
    """Une application : sa carte (nom, etat, description) ou son icone
    seule. Les deux portent le meme lien, le meme menu et les memes gestes
    en mode Modifier -- l'icone se reorganise comme la carte."""
    page = _page_panneau("dashboard.html")
    corps = page.split("function peindreBento(")[1].split("\n}")[0]
    assert "reglageHub('tuiles')==='icones'" in corps
    icone, carte = corps.split("class=\"hub-icone ")[1], corps.split("class=\"bento-carte ")[1]
    for partie in (icone.split("</${balise}>")[0], carte.split("</${balise}>")[0]):
        assert 'data-el="${esc(cleElement(e))}"' in partie
        assert "hubEdition ? gestesEdition(e, nomAffiche(a)) : boutonMenuApp(a)" in partie
        assert "${pastille(a)}" in partie
    assert "bento-carte-desc" not in icone.split("</${balise}>")[0]
    # En icones, un widget prend 2x2, 4x2 ou 4x4 cases, comme sur un
    # telephone ; une application, une case.
    assert ':root[data-tuiles="icones"] .bw-petit{grid-row:span 2;grid-column:span 2}' in page
    assert ':root[data-tuiles="icones"] .bw-moyen{grid-row:span 2;grid-column:span 4}' in page
    assert ':root[data-tuiles="icones"] .bw-grand{grid-row:span 4;grid-column:span 4}' in page
    # Une application qui ne repond pas est cerclee.
    assert ".hub-icone.err img{box-shadow:" in page and ".hub-icone.warn img{box-shadow:" in page
    # En mode Modifier, l'icone tremble et se saisit comme une carte.
    assert "#hub-liste.edition [data-el]{position:relative;cursor:grab" in page




def test_les_widgets_en_haut_ont_leur_grille():
    """Widgets EN HAUT : deux grilles dans la mosaique, une pour les
    widgets, une pour les applications. Chaque tuile se reorganise dans la
    sienne -- au glisser comme aux fleches."""
    page = _page_panneau("dashboard.html")
    corps = page.split("function peindreBento(")[1].split("\n}")[0]
    assert "const enZones = !filtre && reglageHub('widgets')==='haut';" in corps
    assert 'class="hub-grille hub-zone-widgets"' in corps and 'class="hub-grille hub-zone-apps"' in corps
    assert "el.className='bento '+(enZones ? 'zones' : 'hub-grille')" in corps
    place = page.split("function chercherPlace(")[1].split("\n}")[0]
    assert "cible.parentNode!==g.t.parentNode" in place
    deplacer = page.split("function hubDeplacer(")[1].split("\n}")[0]
    assert "reglageHub('widgets')==='haut'" in deplacer and "elements[j].t!==elements[i].t" in deplacer
    # L'ordre enregistre est celui des tuiles a l'ecran, des deux grilles.
    assert "const ordreDesTuiles = () => [...$('hub-liste').querySelectorAll('[data-el]')]" in page
    fin = page.split("function finirGlisse(")[1].split("\n}")[0]
    assert "const ordre=ordreDesTuiles();" in fin




def test_le_defilement_vertical_laisse_la_page_defiler():
    """Defilement VERTICAL sur un grand ecran -- choisi, ou impose par un
    ecran en portrait (une tablette tenue droite) : la grille remplit la
    largeur et la page defile vers le bas ; pas de hauteur imposee, pas de
    molette detournee, et pres du bas la page defile pendant un glisser."""
    page = _page_panneau("dashboard.html")
    sens = page.split("function majSensDuHub(){")[1].split("\n}")[0]
    assert "r.getAttribute('data-defilement') === 'vertical'" in sens
    assert "matchMedia('(orientation: portrait)').matches" in sens
    assert ':root:not(.hub-vertical).hub-actif :is(body,#defile){overflow:hidden}' in page
    regle = page.split(':root.hub-vertical .hub-grille{')[1].split("}")[0]
    assert "grid-auto-flow:row" in regle and "repeat(auto-fill,minmax(var(--cel),1fr))" in regle
    assert ':root.hub-vertical #hub-liste.bento{height:auto;overflow:visible' in page
    assert "function horizontal(){ return !ecranMobile() && !document.documentElement.classList.contains('hub-vertical'); }" in page
    ajuster = page.split("function ajusterBento(")[1].split("\n}")[0]
    assert "if(!horizontal()){ el.style.removeProperty('--bento-h')" in ajuster
    bords = page.split("function defilerPresDesBords(")[1].split("\n}")[0]
    assert "defileur().scrollBy(0, w)" in bords




def test_le_nuancier_change_la_couleur_d_accent():
    """Vecu : « ajoute un parametrage des couleurs de l'app avec des
    nuanciers differents proposes. » Chaque nuancier remplace la couleur
    d'accent dans les trois etats du theme (clair, sombre du systeme, sombre
    choisi), avec une specificite au moins egale au bloc qu'il remplace."""
    theme = _page_panneau("theme.css")
    nuancier = theme.split("le nuancier ---", 1)[1].split("mode application", 1)[0]
    # « perso » n'a pas de bloc ici : sa feuille est calculee (/couleur.css),
    # avec les memes trois regles -- verifie a part.
    teintes = [n for n in app.REGLAGES_HUB["couleurs"][1:] if n != "perso"]
    for nom in teintes:
        r = f':root[data-variante="bento"][data-couleurs="{nom}"]'
        for bloc in (r + "{--accent:", r + ':not([data-theme="light"]):not([data-theme="dark"]){--accent:',
                     r + '[data-theme="dark"]{--accent:'):
            assert bloc in nuancier, bloc
    assert nuancier.count("--sur-accent:") == 3 * len(teintes)
    # « lagon » est la couleur de toujours : pas de bloc a lui.
    assert 'data-couleurs="lagon"' not in theme
    page = _page_panneau("dashboard.html")
    for nom in teintes:
        assert f'data-agir="setHub" data-args=\'["couleurs", "{nom}"]\'' in page, nom
    assert 'data-v="perso"' in page and 'data-saisir="choisirCouleurPerso"' in page
    assert "choisirCouleurPerso(){ choisirCouleurPerso(this.value); }" in page


def test_la_couleur_personnalisee_est_lisible_en_clair_et_en_sombre(deux_espaces):
    """Vecu : « Ajoute des choix de couleur + choix personnalise ». Une teinte
    quelconque -- un jaune pur, un noir -- donne un accent lisible : texte
    blanc dessus en clair (4,5:1), accent sur le fond sombre (7:1). Le cookie
    ne fait entrer que six chiffres hexadecimaux dans la page."""
    for teinte in ("#FFFF00", "#000000", "#0E7C86", "#C2255C"):
        p = app.palette_accent(teinte)
        assert app.contraste(p["clair"]["--accent"], "#FFFFFF") >= 4.5, teinte
        assert app.contraste(p["sombre"]["--accent"], app.FOND_SOMBRE) >= 7, teinte
        assert app.contraste(p["sombre"]["--accent"], p["sombre"]["--sur-accent"]) >= 4.5, teinte
    c = deux_espaces
    feuille = c.get("/couleur.css?c=ff8800")
    assert feuille.status_code == 200 and feuille.mimetype == "text/css"
    texte = feuille.get_data(as_text=True)
    assert texte.count('[data-couleurs="perso"]') == 3 and "--sur-accent:" in texte
    for mauvais in ("ff88", "ff8800;}", "red", "<x>"):
        assert c.get("/couleur.css", query_string={"c": mauvais}).status_code == 400, mauvais
    c.set_cookie("codelab-hub", "perso.xff8800")
    page = c.get("/login").get_data(as_text=True)
    assert 'data-couleurs="perso"' in page and 'data-couleur-perso="FF8800"' in page
    assert '<link rel="stylesheet" id="couleur-perso" href="/couleur.css?c=FF8800"></head>' in page
    # Rien d'autre qu'une couleur n'entre : le reglage retombe sur Lagon.
    c.set_cookie("codelab-hub", 'perso.x"><script>')
    page = c.get("/login").get_data(as_text=True)
    assert 'data-couleurs="lagon"' in page and "couleur-perso" not in page and "<script>\"" not in page
    # Une autre teinte choisie : la couleur reste notee, sans sa feuille.
    c.set_cookie("codelab-hub", "prune.xff8800")
    page = c.get("/login").get_data(as_text=True)
    assert 'data-couleur-perso="FF8800"' in page and 'id="couleur-perso"' not in page





# ---------- le hub Bento : defilement, widgets, a la une ----------

def test_la_mosaique_defile_a_l_horizontale_et_a_la_verticale_sur_telephone():
    """Sur un ecran large : trois rangees a la hauteur de la fenetre, les
    applications s'ajoutent sur la droite, jamais en bas. Sur un telephone :
    une colonne qu'on fait defiler vers le bas."""
    page = _page_panneau("dashboard.html")
    regle = page.split("#hub-liste.bento{--cel:")[1].split("}")[0]
    for attendu in ("overflow-x:auto", "overflow-y:hidden", "height:var(--bento-h"):
        assert attendu in regle, attendu
    grille = page.split(".hub-grille{display:grid;")[1].split("}")[0]
    for attendu in ("grid-auto-flow:column", "grid-template-rows:repeat(3"):
        assert attendu in grille, attendu
    mobile = page.split("@media (max-width:760px){\n  .hub-recherche{height:50px")[1]
    mobile = mobile.split("\n}")[0]
    assert "grid-auto-flow:row" in mobile and "overflow:visible" in mobile and "height:auto" in mobile
    # La molette verticale fait defiler de cote ; l'ajout d'application est
    # la derniere tuile, donc a droite.
    assert "$('hub-liste').addEventListener('wheel'" in page
    # Plus d'ajout d'application depuis le hub : cela se fait dans
    # « Applications ».
    corps = page.split("function peindreBento(")[1].split("\n}")[0]
    assert "openAdd" not in corps and "bento-ajout" not in page




def test_a_la_une_montre_un_apercu_de_la_derniere_application_ouverte(deux_espaces):
    page = _page_panneau("dashboard.html")
    une = page.split("function widgetApercu(")[1].split("\n}")[0]
    assert "<iframe" in une and "pointer-events" not in une  # inerte par le CSS
    assert 'sandbox="allow-scripts allow-same-origin allow-forms"' in une
    assert "a.ouverte_le" in page.split("function appALaUne(")[1].split("\n}")[0]
    assert ".bw-apercu iframe{" in page and "pointer-events:none" in page.split(".bw-apercu iframe{")[1].split("}")[0]
    # Le panneau autorise l'encadrement de l'origine des applications, et
    # elle seule ; il ne s'encadre toujours pas lui-meme.
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.get("/")
    csp = r.headers["Content-Security-Policy"]
    assert "frame-src" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"




def test_la_disposition_du_hub_est_propre_a_chaque_appareil(deux_espaces):
    """Vecu : « Une configuration de l'apparence doit etre propre a un
    appareil ». Ranger le hub sur le telephone ne derange plus
    l'ordinateur : chaque appareil (cookie codelab-appareil) garde la
    sienne ; un appareil neuf part de celle du compte."""
    ordi = app.flask_app.test_client()
    tel = app.flask_app.test_client()
    for c in (ordi, tel):
        c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
        c.get("/api/hub/disposition")
        assert c.get_cookie(app.COOKIE_APPAREIL), "chaque navigateur a son identifiant"
    assert ordi.get_cookie(app.COOKIE_APPAREIL).value != tel.get_cookie(app.COOKIE_APPAREIL).value
    sur_ordi = [{"t": "w", "id": "etat", "taille": "petit"}, {"t": "a", "id": "public"}]
    sur_tel = [{"t": "a", "id": "public"}]
    assert ordi.put("/api/hub/disposition", json={"elements": sur_ordi}).status_code == 200
    assert tel.put("/api/hub/disposition", json={"elements": sur_tel}).status_code == 200
    assert ordi.get("/api/hub/disposition").get_json()["elements"] == sur_ordi
    assert tel.get("/api/hub/disposition").get_json()["elements"] == sur_tel
    # Avant ce changement, la disposition suivait le compte : un appareil
    # neuf la reprend comme point de depart.
    app.ecrire_preference("hub_disposition", sur_ordi, app.NOM_ADMIN)
    neuf = app.flask_app.test_client()
    neuf.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert neuf.get("/api/hub/disposition").get_json()["elements"] == sur_ordi


def test_le_hub_se_compose_par_compte(deux_espaces):
    """Le hub Bento se compose : tuiles et widgets dans l'ordre voulu, chaque
    widget a sa taille. Peu de widgets, sur le hub ou sur une application ;
    le catalogue est ferme et depend du role."""
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = c.get("/api/hub/disposition").get_json()
    catalogue = {w["id"]: w for w in d["catalogue"]}
    assert set(catalogue) == {"une", "etat", "messages", "nouveautes", "sauvegardes", "apercu",
                              "serveur", "activite"}
    assert catalogue["apercu"]["portee"] == "app" and catalogue["etat"]["tailles"] == ["petit", "moyen"]
    # Ni horloge ni bloc-notes : rien qui ne parle du hub ou de ses applications.
    assert "horloge" not in catalogue and "notes" not in catalogue
    assert [e["id"] for e in d["elements"]][0] == "une"
    elements = [{"t": "a", "id": "public"}, {"t": "w", "id": "apercu", "taille": "grand", "app": "public"},
                {"t": "w", "id": "etat", "taille": "petit"}, {"t": "a", "id": "prive-autorise"},
                {"t": "a", "id": "disparue"}]
    r = c.put("/api/hub/disposition", json={"elements": elements})
    assert r.status_code == 200, r.data
    garde = c.get("/api/hub/disposition").get_json()["elements"]
    # Une application qui n'existe pas est oubliee, l'ordre est garde.
    assert garde == elements[:4]
    for mauvais in ([{"t": "w", "id": "horloge", "taille": "petit"}],
                    [{"t": "w", "id": "etat", "taille": "grand"}],
                    [{"t": "w", "id": "apercu", "taille": "moyen", "app": "inconnue"}],
                    [{"t": "w", "id": "etat", "taille": "petit"}, {"t": "w", "id": "etat", "taille": "moyen"}],
                    [{"t": "x"}], "x"):
        assert c.put("/api/hub/disposition", json={"elements": mauvais}).status_code == 400, mauvais
    # Un compte utilisateur : ses propres widgets, et seulement ses applications.
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    catalogue = {w["id"] for w in u.get("/api/hub/disposition").get_json()["catalogue"]}
    assert "message" in catalogue and "messages" not in catalogue and "etat" not in catalogue
    assert u.put("/api/hub/disposition", json={"elements": [
        {"t": "w", "id": "apercu", "taille": "moyen", "app": "prive-refuse"}]}).status_code == 400
    assert u.put("/api/hub/disposition", json={"elements": [
        {"t": "w", "id": "apercu", "taille": "moyen", "app": "prive-autorise"}]}).status_code == 200
    page = _page_panneau("dashboard.html")
    for trace in ('id="ov-galerie"', "function basculerEdition(", "function galerieAjouter(",
                  "'hub-deplacer'", "'hub-retirer'", "@keyframes hub-tremble", "prefers-reduced-motion:reduce",
                  # Glisser comme sur un iPhone : la tuile suit, les autres glissent,
                  # la mosaique defile pres des bords ; le tremblement laisse
                  # « transform » libre pour ces glissements.
                  "function glisserLesAutres(", "function defilerPresDesBords(", "{from{rotate:-",
                  # Bord a bord.
                  "margin-left:calc(50% - 50vw)"):
        assert trace in page, trace




# ---------- l'onglet Acces et la liste des applications ----------

def test_aucun_identifiant_du_panneau_n_est_en_double():
    """Deux elements du meme id : getElementById prend le premier, et la
    liste des comptes de l'onglet Acces se dessinait dans l'onglet Activite,
    cache. On ne pouvait plus autoriser personne."""
    page = _page_panneau("dashboard.html")
    ids = re.findall(r'\bid="([^"$]+)"', page)
    doubles = sorted({i for i in ids if ids.count(i) > 1})
    assert not doubles, doubles
    acces = page.split('id="onglet-acces"')[1].split('id="onglet-config"')[0]
    assert 'id="acces-liste"' in acces
    corps = page.split("async function chargerAcces(")[1].split("\nasync function acBasculer")[0]
    assert "$('acces-liste')" in corps and 'data-compte="' in corps




def test_l_onglet_acces_autorise_un_compte(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.put("/api/apps/prive-refuse/acces", json={"utilisateurs": ["marie"]}).status_code == 200
    comptes = c.get("/api/apps/prive-refuse/acces").get_json()["comptes"]
    assert comptes == [{"nom": "marie", "acces": True, "email": ""}]
    assert "prive-autorise" in app.lire_utilisateurs()["marie"]["projets"]




def test_la_liste_des_applications_se_cherche_se_filtre_et_se_trie():
    page = _page_panneau("dashboard.html")
    for trace in ('id="ap-filtre"', 'id="ap-etats"', 'id="ap-vis"', 'id="ap-cat"', 'id="ap-tri"',
                  'data-agir="apVue" data-args=\'["cartes"]\'',
                  'data-agir="apVue" data-args=\'["liste"]\'', 'data-agir="ap-trier"', "aria-sort=",
                  'class="ap-grille"', "'ap-trier':", "'ap-etat':"):
        assert trace in page, trace
    # Les outils sont HORS de la zone repeinte toutes les cinq secondes.
    outils = page.index('id="ap-outils"')
    assert outils < page.index('<div id="tbl-projets"></div>')
    corps = page.split("function apListe(){")[1].split("\n}")[0]
    for colonne in ("vis", "cat", "apEtat", "apTriSens"):
        assert colonne in corps, colonne


# ------------------------ La video de presentation ------------------------
#
# Vecu : « propose moi une maniere de voir les videos de presentation dans
# le hub » -- puis « oui, developpe mais pas le widget ».
#
# La video vit dans le projet (video/presentation.mp4, faite par
# « codelab video ») ; le panneau ne fait que la servir, AVEC LES DROITS DE
# L'APPLICATION : un compte qui ne voit pas l'application ne voit pas sa
# video, et l'administrateur peut la retirer du hub sans toucher au fichier.




def test_la_video_est_servie_a_qui_voit_l_application(presentation):
    c, _ = presentation
    c = _connexion_marie(c)
    r = c.get("/api/presentations/compta/video")
    assert r.status_code == 200
    assert r.mimetype == "video/mp4"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "private" in r.headers["Cache-Control"]
    assert c.get("/api/presentations/compta/affiche").mimetype == "image/jpeg"




def test_la_video_d_une_application_non_autorisee_n_existe_pas(presentation):
    """404 et pas 403 : on ne confirme pas l'existence d'un projet ferme."""
    c, _ = presentation
    c = _connexion_marie(c)
    assert c.get("/api/presentations/secret/video").status_code == 404
    assert c.get("/api/presentations/inconnue/video").status_code == 404
    assert app.flask_app.test_client().get(
        "/api/presentations/compta/video").status_code in (401, 302, 404)




def test_la_video_se_lit_par_morceaux(presentation):
    """Le lecteur du navigateur demande des plages (Range) pour avancer
    dans la video sans tout telecharger."""
    c, _ = presentation
    r = _connexion_marie(c).get("/api/presentations/compta/video",
                                headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.data) == 100




def test_seuls_les_deux_fichiers_de_la_video_sont_servis(presentation):
    c, dossiers = presentation
    (dossiers["compta"] / ".env").write_text("SECRET=1")
    admin = _admin_presentation()
    for quoi in (".env", "..%2F.env", "scenario", "presentation.mp4"):
        assert admin.get(f"/api/presentations/compta/{quoi}").status_code == 404




def test_un_lien_qui_sort_du_projet_ne_mene_a_rien(presentation, tmp_path):
    _, dossiers = presentation
    dehors = tmp_path / "dehors.mp4"
    dehors.write_bytes(b"x")
    video = dossiers["secret"] / "video" / "presentation.mp4"
    video.unlink()
    video.symlink_to(dehors)
    assert app.fichier_presentation(app.load()["secret"]) is None
    assert _admin_presentation().get("/api/presentations/secret/video").status_code == 404




def test_le_hub_annonce_la_video(presentation):
    c, _ = presentation
    apps = {a["name"]: a for a in _connexion_marie(c).get("/api/mes-apps").get_json()["apps"]}
    assert apps["compta"]["presentation"]["affiche"] is True
    assert isinstance(apps["compta"]["presentation"]["maj"], int)
    admin = {a["name"]: a for a in _admin_presentation().get("/api/mes-apps").get_json()["apps"]}
    assert admin["vitrine"]["presentation"] is None




def test_l_administrateur_retire_la_video_du_hub(presentation):
    c, dossiers = presentation
    admin = _admin_presentation()
    r = admin.put("/api/apps/compta/presentation", json={"visible": False})
    assert r.status_code == 200 and r.get_json()["visible"] is False
    # Le fichier reste ; la fiche le montre encore a l'administrateur.
    assert (dossiers["compta"] / "video" / "presentation.mp4").exists()
    fiche = {a["name"]: a for a in admin.get("/api/apps").get_json()["apps"]}["compta"]
    assert fiche["presentation_masquee"] is True and fiche["presentation"]
    assert admin.get("/api/presentations/compta/video").status_code == 200

    marie = _connexion_marie(c)
    apps = {a["name"]: a for a in marie.get("/api/mes-apps").get_json()["apps"]}
    assert apps["compta"]["presentation"] is None
    assert marie.get("/api/presentations/compta/video").status_code == 404

    admin.put("/api/apps/compta/presentation", json={"visible": True})
    assert marie.get("/api/presentations/compta/video").status_code == 200




def test_seul_l_administrateur_retire_une_video(presentation):
    c, _ = presentation
    r = _connexion_marie(c).put("/api/apps/compta/presentation", json={"visible": False})
    assert r.status_code in (401, 403)
    assert not app.load()["compta"].get("presentation_masquee")




# Vecu : « Je souhaiterai pouvoir selectionner dans les configuration de
# l'app le fichier video a utiliser. » -- un rendu Remotion, un montage fait
# ailleurs : toute video du dossier du projet.

def test_l_administrateur_choisit_une_autre_video_du_projet(presentation, tmp_path):
    c, dossiers = presentation
    d = dossiers["compta"]
    (d / "out").mkdir()
    (d / "out" / "demo.webm").write_bytes(b"\x1aE\xdf\xa3webm" + b"w" * 500)
    (d / "node_modules" / "x").mkdir(parents=True)
    (d / "node_modules" / "x" / "cache.mp4").write_bytes(b"x")
    (d / ".cache").mkdir()
    (d / ".cache" / "rendu.mp4").write_bytes(b"x")
    (d / "README.md").write_text("ici")
    dehors = tmp_path / "dehors.mp4"
    dehors.write_bytes(b"x")
    (d / "lien.mp4").symlink_to(dehors)
    admin = _admin_presentation()
    liste = admin.get("/api/apps/compta/videos").get_json()
    # Les videos du projet, sans dependances, caches ni liens vers dehors.
    assert liste["fichiers"] == ["out/demo.webm", "video/presentation.mp4"]
    assert liste["choisi"] == "" and liste["defaut"] == "video/presentation.mp4"

    r = admin.put("/api/apps/compta/presentation", json={"fichier": "out/demo.webm"})
    assert r.status_code == 200 and r.get_json()["fichier"] == "out/demo.webm"
    # La visibilite ne bouge pas quand on choisit le fichier.
    assert not app.load()["compta"].get("presentation_masquee")
    video = _connexion_marie(c).get("/api/presentations/compta/video")
    assert video.status_code == 200 and video.mimetype == "video/webm"
    assert video.data.startswith(b"\x1aE\xdf\xa3")
    # L'affiche de « codelab video » n'irait pas avec une autre video.
    apps = {a["name"]: a for a in _connexion_marie(c).get("/api/mes-apps").get_json()["apps"]}
    assert apps["compta"]["presentation"]["affiche"] is False
    fiche = {a["name"]: a for a in admin.get("/api/apps").get_json()["apps"]}["compta"]
    assert fiche["presentation_fichier"] == "out/demo.webm"

    # Rien hors de la liste : ni chemin qui sort, ni fichier qui n'est pas
    # une video, ni dossier ignore.
    for mauvais in ("../dehors.mp4", "README.md", "lien.mp4", "node_modules/x/cache.mp4", "/etc/passwd", 3):
        assert admin.put("/api/apps/compta/presentation", json={"fichier": mauvais}).status_code == 400, mauvais
    assert app.load()["compta"]["presentation_fichier"] == "out/demo.webm"
    # Vide : retour a video/presentation.mp4.
    assert admin.put("/api/apps/compta/presentation", json={"fichier": ""}).status_code == 200
    assert "presentation_fichier" not in app.load()["compta"]
    assert _connexion_marie(c).get("/api/presentations/compta/video").mimetype == "video/mp4"
    # Reserve a l'administrateur.
    marie = _connexion_marie(c)
    assert marie.get("/api/apps/compta/videos").status_code in (401, 403)
    assert marie.put("/api/apps/compta/presentation", json={"fichier": "out/demo.webm"}).status_code in (401, 403)




def test_la_fiche_propose_les_videos_du_projet():
    page = _page_panneau("dashboard.html")
    fiche = page.split("function peindrePresentationFiche(")[1].split("\n}")[0]
    assert 'data-changer="presentation-fichier"' in fiche and "optionsVideos(a)" in fiche
    assert "else if(n.dataset.changer === 'presentation-fichier')" in page
    assert "JSON.stringify({fichier})" in page.split("async function choisirVideo(")[1].split("\n}")[0]




def test_le_hub_porte_le_lecteur_de_video():
    page = _page_panneau("dashboard.html")
    assert 'id="ov-presentation"' in page
    # Depuis le menu de l'application, seulement si le projet a une video.
    menu = page.split("function ouvrirMenuApp(")[1].split("\n}")[0]
    assert "a.presentation ? item('menu-presentation'" in menu
    assert "function peindrePresentationFiche(" in page
    assert "/api/presentations/" in page
    # Le lecteur natif du navigateur, et l'application a un clic.
    assert '<video class="presentation-video"' in page
    assert "Ouvrir l'application" in page




def test_codelab_video_pose_un_scenario_a_completer(tmp_path):
    outils = os.path.join(_racine_depot(), "dev")
    (tmp_path / "ws" / "mon-site").mkdir(parents=True)
    r, espace = _lancer_codelab(tmp_path, ["video", "mon-site"],
                                {"CODELAB_VIDEO_OUTILS": outils})
    assert r.returncode == 0, r.stderr
    scenario = (espace / "mon-site" / "video" / "scenario.mjs").read_text(encoding="utf-8")
    assert "__" not in scenario
    assert 'titre: "mon-site"' in scenario
    assert "/mon-site/" in scenario
    # Relancee sans scenario modifie, la commande ne l'ecrase pas : elle tourne.
    (espace / "mon-site" / "video" / "scenario.mjs").write_text("// a moi\n")
    _lancer_codelab(tmp_path, ["video", "mon-site"],
                    {"CODELAB_VIDEO_OUTILS": str(tmp_path / "rien")})
    assert (espace / "mon-site" / "video" / "scenario.mjs").read_text() == "// a moi\n"




def test_codelab_video_dit_que_l_application_ne_repond_pas(tmp_path):
    """Vecu : « apiRequestContext.post: connect ECONNREFUSED ::1:8000 » --
    l'application etait arretee, et « localhost » visait l'IPv6. Le tournage
    verifie l'adresse d'abord, la lit en 127.0.0.1, et le dit en clair."""
    import shutil
    import socket
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node absent")
    env = dict(os.environ)
    env["NODE_PATH"] = os.pathsep.join(filter(None, [env.get("NODE_PATH"), "/opt/node-tools/node_modules"]))
    sonde = subprocess.run(["node", "-e", "require('playwright')"], env=env, capture_output=True)
    if sonde.returncode != 0:
        pytest.skip("playwright absent")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]          # libre, donc personne n'y repond
    (tmp_path / "video").mkdir()
    (tmp_path / "video" / "scenario.mjs").write_text(
        'export default { titre: "t", adresse: "http://localhost:%d/", etapes: [{ voix: "x" }] };\n' % port)
    # Sans laissez-passer (pas de cle), le hub ne peut pas prendre le relais.
    env["CODELAB_TOURNAGE_FICHIER"] = str(tmp_path / "pas-de-cle")
    r = subprocess.run(["node", os.path.join(_racine_depot(), "dev", "fabriquer.mjs"),
                        str(tmp_path)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, r.stdout + r.stderr
    # L'adresse locale muette, lue en 127.0.0.1, passe la main au hub (vecu :
    # « je ne veux pas avoir a ouvrir le navigateur ») ; sans laissez-passer,
    # le refus le dit en clair.
    assert f"rien sur http://127.0.0.1:{port}/ : tournage par le hub" in r.stdout, r.stdout
    assert "ne repond pas" in r.stderr and "autre terminal" in r.stderr, r.stderr




def test_codelab_video_trouve_le_chromium_de_l_image_depuis_une_session_ssh(tmp_path):
    """Vecu : « Executable doesn't exist at ~/.cache/ms-playwright/... ».
    L'ENV du Dockerfile n'atteint pas une session SSH : l'outil pose lui-meme
    le dossier des navigateurs de l'image."""
    faux = tmp_path / "faux-node"
    faux.mkdir()
    trace = tmp_path / "env.txt"
    (faux / "node").write_text('#!/bin/sh\necho "$PLAYWRIGHT_BROWSERS_PATH" > "' + str(trace) + '"\n')
    (faux / "node").chmod(0o755)
    outils = tmp_path / "outils"
    outils.mkdir()
    (tmp_path / "ws" / "cave" / "video").mkdir(parents=True)
    (tmp_path / "ws" / "cave" / "video" / "scenario.mjs").write_text("// scenario\n")
    env = {"CODELAB_VIDEO_OUTILS": str(outils), "PATH": f"{faux}{os.pathsep}{os.environ['PATH']}"}
    # Vide comme dans une session SSH (l'environnement de ce test peut en avoir un).
    r, _ = _lancer_codelab(tmp_path, ["video", "cave"], dict(env, PLAYWRIGHT_BROWSERS_PATH=""))
    assert r.returncode == 0, r.stdout + r.stderr
    assert trace.read_text().strip() == f"{outils}/navigateurs"
    # Un chemin deja pose (le conteneur lui-meme) est respecte.
    r, _ = _lancer_codelab(tmp_path, ["video", "cave"], dict(env, PLAYWRIGHT_BROWSERS_PATH="/ailleurs"))
    assert trace.read_text().strip() == "/ailleurs"




def test_codelab_video_refuse_un_chemin(tmp_path):
    r, _ = _lancer_codelab(tmp_path, ["video", "../etc"])
    assert r.returncode == 2




def test_les_scripts_video_sont_du_javascript_valide():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node absent")
    for f in ("fabriquer.mjs", "scenario-modele.mjs"):
        r = subprocess.run(["node", "--check", os.path.join(_racine_depot(), "dev", f)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr




def test_l_image_du_poste_a_les_outils_de_la_video():
    dockerfile = open(os.path.join(_racine_depot(), "dev", "Dockerfile"), encoding="utf-8").read()
    assert "ffmpeg" in dockerfile
    assert "playwright install --with-deps chromium" in dockerfile
    assert "piper-tts==" in dockerfile and "fr_FR-siwis-medium.onnx.json" in dockerfile
    assert "COPY dev/fabriquer.mjs dev/scenario-modele.mjs" in dockerfile
    assert "## 14. Video de presentation" in open(
        os.path.join(_racine_depot(), "dev", "AGENTS.md"), encoding="utf-8").read()




# ------------- Au telephone : on tient avant de deplacer -------------
#
# Vecu : « Sur telephone, ameliorer la reorganisation des app. Il faut
# selectionner un minimum de temps l'app pour la deplacer, sinon ca
# m'empeche de scroller. »

def test_au_doigt_une_tuile_se_saisit_par_un_appui_long():
    page = _page_panneau("dashboard.html")
    assert "const SAISIE_TACTILE_MS = 350;" in page
    bas = page.split("$('hub-liste').addEventListener('pointerdown', e=>{\n  if(!hubEdition")[1].split("\n});")[0]
    assert "tactile, saisie:!tactile" in bas          # la souris attrape tout de suite
    assert "setTimeout(" in bas and "SAISIE_TACTILE_MS" in bas
    deplacement = page.split("document.addEventListener('pointermove', e=>{\n  const g=glisse;")[1].split("\n});")[0]
    # Avant la saisie, un doigt qui bouge fait defiler : la tuile est lachee.
    assert "if(!g.saisie){" in deplacement and "glisse=null" in deplacement
    # Le defilement reste permis au doigt tant que rien n'est saisi...
    assert "#hub-liste.edition [data-el]{touch-action:pan-x pan-y" in page
    # ... et s'arrete une fois la tuile saisie.
    assert "if(glisse && glisse.tactile && glisse.saisie) e.preventDefault();" in page
    assert "}, {passive:false});" in page




def test_deployer_a_quitte_la_fiche():
    page = _page_panneau("dashboard.html")
    assert "app-deployer" not in page and "deployApp" not in page
    assert ">Déployer<" not in page.replace("${svgUp}", "")
    readme = open(os.path.join(_racine_depot(), "dev", "README.md"), encoding="utf-8").read()
    assert "## Déployer" not in readme




# ------------------------ Le mode application ------------------------
#
# Vecu : « Comment faire en sorte que la version mobile (webapp avec
# raccourci) se comporte comme une vraie application, pas de possibilite de
# zoom [...] Je ne veux pas la barre blanche au dessus qui casse l'effet
# application ».

# Messages n'y est plus : c'est une application, servie par le proxy, qui
# recoit environnement-app.js et le manifeste du proxy comme les autres.
@pytest.mark.parametrize("page", ["dashboard.html", "login.html"])
def test_chaque_page_du_panneau_se_comporte_en_application(page):
    html = _page_panneau(page)
    tete = html.split("</head>")[0]
    # Installable, ouverte en plein ecran.
    assert '<link rel="manifest" href="/manifest.webmanifest">' in tete
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in tete
    # La barre d'etat prend la couleur du bandeau, plus de bande blanche.
    assert '<meta name="theme-color" content="#E9EBEE" media="(prefers-color-scheme: light)">' in tete
    # Le mode se lit dans UN fichier, partage, avant tout affichage : plus
    # une copie par page.
    assert '<script src="/mode.js"></script>' in tete
    assert "display-mode: standalone" not in html and "function majBarreEtat(){" not in html




def test_le_mode_se_lit_une_fois_pour_tout_le_panneau(client):
    r = client.get("/mode.js")
    assert r.status_code == 200 and "javascript" in r.headers["Content-Type"]
    source = r.get_data(as_text=True)
    assert "function majBarreEtat(){" in source
    # Seulement depuis l'icone : dans Safari, le zoom reste permis.
    mode = source.split("(function(){")[1].split("})();")[0]
    assert "display-mode: standalone" in mode and "navigator.standalone === true" in mode
    assert "classList.add(app ? 'mode-app' : 'mode-site')" in mode
    assert "if(!app) return;" in mode
    assert "maximum-scale=1,user-scalable=no,viewport-fit=cover" in mode
    assert "'gesturestart'" in mode and "e.preventDefault()" in mode
    # Le hub ne redevine pas le mode : il lit celui que mode.js a pose.
    assert "const enModeApp = window.CODELAB_MODE_APP === true;" in _page_panneau("dashboard.html")
    # Le theme dit les deux questions, et les deux seuils de largeur.
    theme = _page_panneau("theme.css")
    assert "DEUX MODES, TROIS LARGEURS" in theme
    assert "@media (max-width:760px)" in theme and "(max-width:1180px)" in theme




def test_le_manuel_distingue_le_site_et_l_application():
    manuel = open(os.path.join(_racine_depot(), "dev", "AGENTS.md"), encoding="utf-8").read()
    section = manuel.split("### Site web et mode application")[1].split("\n## ")[0]
    assert "toujours\ndistinguer les deux" in section
    assert "Les deux modes sont responsives, sur les trois appareils." in section
    # Le hub pose l'environnement lui-meme : ce que l'application lit.
    for cle in ("window.CODELAB.environnement", "var(--codelab-bord-haut)", "data-appareil",
                "viewport-fit=cover", "Ecris donc **pour iOS, une seule fois**"):
        assert cle in section, cle
    assert "env(safe-area-inset-top)" in section and "scrollbar-width:none" in section
    liste = manuel.split("## Avant de declarer une application prete")[1]
    assert "en mode\n      site web **et** en mode application" in liste




def test_le_theme_fige_l_interface_en_mode_application():
    theme = _page_panneau("theme.css")
    regle = theme.split("html.mode-app{")[1].split("}")[0]
    for attendu in ("touch-action:pan-x pan-y", "overscroll-behavior:none",
                    "-webkit-tap-highlight-color:transparent"):
        assert attendu in regle, attendu
    assert "html.mode-app body{-webkit-user-select:none;user-select:none" in theme
    # Ce qu'on saisit ou copie reste selectionnable, et iOS ne zoome pas a la saisie.
    assert "html.mode-app :is(input,textarea,select,[contenteditable],pre,code,.selectionnable)" in theme
    assert "font-size:max(16px,1em)" in theme




def test_le_manifeste_n_a_plus_de_barre_blanche(hub):
    m = hub.get("/manifest.webmanifest").get_json()
    assert m["display"] == "standalone"
    assert m["theme_color"] == m["background_color"] == "#E9EBEE"




def test_la_barre_suit_le_theme_choisi():
    page = _page_panneau("dashboard.html")
    for fonction in ("function applyTheme(t){", "function appliquerReglagesHub(reglages){"):
        assert "setTimeout(majBarreEtat, 0);" in page.split(fonction)[1].split("\n}")[0], fonction




# ------------- Aucune barre de defilement, iPhone compris -------------
#
# Vecu : « J'ai toujours la barre de scroll visible sur telephone !!! » iOS
# ne laisse masquer que celle d'un bloc interieur, jamais celle de la page :
# au doigt, c'est #defile qui defile.

@pytest.mark.parametrize("page", ["dashboard.html", "login.html", PAGE_MESSAGES])
def test_au_doigt_un_bloc_interieur_defile_a_la_place_de_la_page(page):
    html = _page_panneau(page)
    corps = re.split(r"<body[^>]*>", html, maxsplit=1)[1]
    assert corps.lstrip().startswith('<div id="defile">'), page
    assert corps.rstrip().endswith("</div>\n</body></html>") or corps.rstrip().endswith("</div>\n</body>\n</html>"), page
    theme = _page_panneau("theme.css")
    regle = theme.split("@media (pointer:coarse){")[1].split("\n}")[0]
    assert "html:has(> body > #defile),html:has(> body > #defile) > body{height:100%;overflow:hidden}" in regle
    assert "#defile{height:100%;overflow-x:hidden;overflow-y:auto" in regle
    assert "*::-webkit-scrollbar{display:none}" in theme




def test_ce_qui_defile_suit_le_pointeur():
    page = _page_panneau("dashboard.html")
    assert "const defileur=()=>DEFILE_INTERNE.matches ? $('defile') : document.scrollingElement;" in page
    ajuster = page.split("function ajusterBento(")[1].split("\n}")[0]
    assert "const exces=d.scrollHeight - d.clientHeight;" in ajuster
    assert "defileur().scrollBy(0, w)" in page and "window.scrollBy(" not in page
    assert "document.getElementById('defile').addEventListener('scroll', majEnteteCollee" in page




# ------------- « A la une » : la bande-annonce et les couleurs -------------
#
# Vecu : « Fais le 4 et le 1 » -- la video de presentation en boucle, sans
# le son, et le widget aux couleurs de l'icone de l'application.

def test_a_la_une_joue_la_video_de_presentation_en_boucle():
    page = _page_panneau("dashboard.html")
    video = page.split("function videoALaUne(")[1].split("\n}")[0]
    for attendu in ("muted loop autoplay playsinline", "prefers-reduced-motion: reduce",
                    "navigator.connection.saveData", "if(!a.presentation) return '';"):
        assert attendu in video, attendu
    apercu = page.split("function widgetApercu(")[1].split("\n}")[0]
    # Seulement « a la une » (pas l'apercu en direct), et l'icone reste dessous.
    assert "const video = !enDirect && videoALaUne(a);" in apercu
    assert '<img class="bw-une-icone" src="${icone(a.name)}" alt="">${video}' in apercu
    # La video survit aux repeints du hub : elle ne recommence pas.
    assert "-${video ? a.presentation.maj : 0}" in apercu




def test_a_la_une_n_a_pas_de_lisere_de_couleur():
    """Vecu : « J'ai des traces de contour rouge » autour de la video de
    WineManager. Le lissage d'un coin arrondi melange le fond de la carte et
    celui de l'enfant qui le recouvre : peinte sous toute la carte, la
    couleur de l'application debordait d'un pixel sur les bords.

    La carte n'a donc pas de fond, ni d'espace entre ses deux blocs (il
    laisserait voir la page), et l'apercu qui montre une video ou une page
    arrete son fond un pixel avant le bord.
    """
    page = _page_panneau("dashboard.html")
    carte = page.split("\n.bw-une{")[1].split("}")[0]
    assert "background:none" in carte and "gap:0" in carte
    assert ".bw-apercu:is(.en-video,:not(.sans-direct)){padding:1px;background-clip:content-box}" in page
    # Et aucune regle ne repeint la carte au survol.
    assert ".bw-une:hover{background" not in page
    # Les fonds de l'apercu sans direct sont des images : la couleur reste
    # dessous (un raccourci « background: » l'effacerait).
    assert ".bw-apercu.sans-direct{background:" not in page
    lueur = page.split(".bw-apercu.sans-direct:not(.en-video){")[1].split("}")[0]
    assert "background-image:" in lueur and "background:" not in lueur.replace("background-", "")




def test_a_la_une_prend_les_couleurs_de_son_icone():
    page = _page_panneau("dashboard.html")
    # La couleur est portee par le bandeau et l'apercu sans direct, plus par
    # la carte : voir test_a_la_une_n_a_pas_de_lisere_de_couleur.
    assert (".bw-une-pied,.bw-apercu.sans-direct{background-color:var(--une-fond,var(--accent));"
            in page)
    assert "color:var(--une-txt,var(--sur-accent))" in page
    assert (".bw-une:hover :is(.bw-une-pied,.bw-apercu.sans-direct)"
            "{background-color:var(--une-fond-h,var(--accent-h))}") in page
    teinte = page.split("function teinteDominante(")[1].split("\n}")[0]
    assert "if(sat<.25 || max<40) continue;" in teinte        # ni blanc, ni gris, ni noir
    assert "return null;" in teinte                           # icone grise : l'accent reste
    poser = page.split("function poserTeinte(")[1].split("\n}")[0]
    assert "'#ffffff' : '#10141a'" in poser                   # le texte qui contraste le plus
    bento = page.split("function peindreBento(")[1].split("\n}")[0]
    assert "remplirEnGardant(el, html);\n  teinterALaUne(el);" in bento




# ------------- Un defilement sans saut -------------
#
# Vecu : « Fluidifie le scroll qui doit etre parfait. Pas de saut, pas de
# scroll trop rapide a l'horizontal » ; « l'app "saute" de temps en temps
# lorsque je scroll ».

def test_la_mosaique_n_a_plus_d_aimant_et_la_molette_glisse():
    page = _page_panneau("dashboard.html")
    # L'aimant ramenait la mosaique sur une colonne apres chaque cran.
    assert "scroll-snap-type" not in page and "scroll-snap-align" not in page
    molette = page.split("$('hub-liste').addEventListener('wheel', e=>{")[1].split("\n}, {passive:false});")[0]
    # Un cran de molette va moins loin, et glisse jusqu'a sa cible.
    assert "const cible=Math.max(0, Math.min(max, depart + pas*(cran ? MOLETTE_PAS : 1)));" in molette
    assert "const MOLETTE_PAS=2/3;" in page
    # Un pave tactile passe tel quel.
    assert "if(!cran && !molette){ el.scrollLeft += pas; return; }" in molette
    glisser = page.split("function glisserMolette(){")[1].split("\n}")[0]
    # Au moins un pixel par image : sinon l'arrondi de scrollLeft bloque.
    assert "Math.max(1, Math.abs(reste)*.2)" in glisser and "el.scrollLeft===avant" in glisser




def test_les_minuteries_ne_repeignent_pas_pendant_un_defilement():
    page = _page_panneau("dashboard.html")
    assert "const enDefilement = () => doigtsPoses>0 || performance.now()-defileLe < 700;" in page
    assert "document.addEventListener('scroll', ()=>{ defileLe=performance.now(); }, {capture:true, passive:true});" in page
    surveiller = page.split("function surveiller(fn, delai){")[1].split("\n}")[0]
    assert "quandImmobile(()=>{ enAttente=false; fn(); })" in surveiller
    assert "setInterval(tour, delai)" in surveiller




# ------------- Les parametres au telephone -------------
#
# Vecu : « Sur telephone, garde ce systeme d'onglet mais repartis bien les
# parametrages en 2, 3 ou 4 onglets dedies de taille homogene. »

def test_les_parametres_tiennent_en_six_onglets_de_meme_largeur():
    """Six depuis l'onglet Aide de l'administrateur (« un onglet dedie pour
    l'admin seulement ») ; un compte utilisateur en voit quatre."""
    page = _page_panneau("dashboard.html")
    onglets = page.split('<div class="fiche-onglets" id="param-onglets">')[1].split("</div>")[0]
    assert onglets.count("<button") == 6
    for p in ("compte", "apparence", "hub", "securite", "email", "aide"):
        assert f'data-p="{p}"' in onglets, p
    # Vecu : « Je ne veux pas avoir Acces depuis internet qui ne me sert a
    # rien. Tu peux retirer la partie Serveur. »
    assert 'data-p="serveur"' not in onglets and 'id="param-serveur"' not in page
    assert "pubEnregistrer" not in page and "chargerVps" not in page
    # Les categories rejoignent les applications du hub, dans « Mon hub » --
    # et seulement pour l'administrateur.
    hub = page.split('<div class="onglet-p" id="param-hub">')[1].split('<div class="onglet-p"')[0]
    assert 'id="zone-categories"' in hub and 'id="cat-liste"' in hub
    assert "$('zone-categories').remove();" in page
    assert "if(estAdmin) chargerVersion();" in page and "if(estAdmin) chargerCategories();" in page
    # Au telephone : une grille de colonnes egales, sans defilement.
    assert ("#sec-parametres #param-onglets{display:grid;grid-auto-flow:column;"
            "grid-auto-columns:1fr;") in page
    assert "#param-onglets .onglet-suite{display:none}" in page




def test_les_boutons_des_parametres_sont_des_pilules_au_pouce():
    page = _page_panneau("dashboard.html")
    assert "#sec-parametres .btn{border-radius:999px;font-weight:650;min-height:40px;padding:0 18px}" in page
    tel = page.split("#sec-parametres .actions{gap:10px}")[1].split("\n}")[0]
    assert "#sec-parametres .btn{min-height:46px;font-size:15px}" in tel
    assert "flex:1 1 100%;align-self:stretch" in tel




def test_sans_video_l_icone_a_la_une_respire():
    """Vecu : « Fais l'idee 2 pour le widget sans video » -- une lueur qui
    derive dans la couleur de l'icone, et l'icone qui se souleve a peine."""
    page = _page_panneau("dashboard.html")
    assert ".bw-apercu.sans-direct:not(.en-video){" in page
    assert "animation:une-lueur 14s ease-in-out infinite alternate" in page
    assert "animation:une-souffle 4.5s ease-in-out infinite" in page
    # Immobile quand l'appareil demande moins de mouvement, ou en edition.
    assert "@media (prefers-reduced-motion:reduce){ .bw-apercu.sans-direct,.bw-une-icone{animation:none} }" in page
    assert "#hub-liste.edition .bw-apercu,#hub-liste.edition .bw-une-icone{animation:none}" in page




# ---------------- La categorie CodeLab, les tuiles, les onglets ----------------
#
# Vecu : « Cree une categorie non modifiable CodeLab et regroupe toutes les
# apps restreintes a l'admin. » -- « Attention au nom des apps qui peuvent
# etre coupe par leur description ; afficher plus de caracteres dans la
# description, preciser la limite visible dans les configurations. »

def test_la_categorie_codelab_regroupe_les_applications_de_l_administrateur(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = c.get("/api/categories").get_json()
    assert d["categories"][0] == "CodeLab" and d["verrouillees"] == ["CodeLab"]
    # Elle ne se supprime ni ne se renomme : elle revient d'office, une fois.
    r = c.put("/api/categories", json={"categories": ["codelab", "Outils", "CODELAB"]}).get_json()
    assert r["categories"] == ["CodeLab", "Outils"]
    assert app.lire_categories() == ["Outils"]
    r = c.put("/api/categories", json={"categories": []}).get_json()
    assert r["categories"] == ["CodeLab"]
    # Les applications de la stack y sont rangees d'office, les autres non.
    apps = {a["name"]: a for a in c.get("/api/apps").get_json()["apps"]}
    for nom in apps:
        attendu = "CodeLab" if app.est_app_par_defaut(nom) else ""
        assert apps[nom]["categorie"] == attendu, nom
    assert app.categorie_de(app.DIAGNOSTIC_NOM) == "CodeLab"
    # Un projet ne la recoit pas.
    a = app.load()["public"]
    r = c.put("/api/app/public", json={"path": a["path"], "command": a["command"], "categorie": "CodeLab"})
    assert r.status_code == 400 and "réservée" in r.get_json()["error"]




def test_la_page_montre_codelab_sans_le_laisser_modifier():
    page = _page_panneau("dashboard.html")
    peindre = page.split("function peindreCategories(){")[1].split("\n}")[0]
    assert "cat-verrou" in peindre
    # Pas de champ pour CodeLab : la ligne verrouillee n'a qu'un texte.
    verrou = peindre.split("categoriesVerrouillees.map(")[1].split(".join('')")[0]
    assert "<input" not in verrou and "catRetirer" not in verrou
    assert ".cat-ligne:not(.cat-verrou) input" in page
    # Elle n'est jamais proposee pour un projet.
    assert "const libres=categoriesLibres();" in page.split("function optionsCategorie(")[1].split("\n}")[0]




def test_le_nom_d_une_tuile_passe_avant_sa_description():
    page = _page_panneau("dashboard.html")
    assert ".bento-carte-nom{flex:none;" in page
    assert ".bento-carte-desc{flex:0 1 auto;min-height:0;" in page and "-webkit-line-clamp:2;" in page
    assert "@media (hover:none) and (pointer:coarse){ .bento-carte-desc{padding-right:0} }" in page
    assert "const classeNomLong = n => n.length > 34 ? ' tres-long' : n.length > 20 ? ' long' : '';" in page
    # La fiche dit ce que la tuile montre, compteur compris.
    assert "const DESCRIPTION_VISIBLE=45, DESCRIPTION_MAX=140;" in page
    for champ in ("e-desc", "f-desc"):
        assert f'id="{champ}-compte"' in page and f'id="{champ}"' in page
    assert page.count('data-saisir="compterDescription"') == 2




def test_au_doigt_la_carte_touchee_ne_reste_pas_selectionnee():
    page = _page_panneau("dashboard.html")
    bloc = page.split("AU DOIGT, PAS DE SURVOL QUI RESTE.")[1].split("/* LES PASTILLES")[0]
    assert "@media (hover:none){" in bloc and ".card-top:hover{background:none}" in bloc
    assert ".card-top:active{background:var(--surface2)}" in bloc


@pytest.mark.parametrize("page", ["dashboard.html", "login.html", PAGE_MESSAGES])
def test_le_fond_des_pages_est_uni_sans_motif_ni_reglage(page):
    """Vecu : des etoiles, puis des croix, puis « retire completement les
    croix dans le fond d'ecran, ainsi que le reglage ». Le fond est la
    couleur du theme, sans calque ni masque, et le hub n'a plus de reglage
    de fond."""
    texte = _page_panneau(page)
    assert "fond-motif" not in texte and "data-motif" not in texte
    theme = _page_panneau("theme.css")
    for trace in ("fond-motif", "--motif", "data-motif"):
        assert trace not in theme, trace
    assert "motif" not in app.REGLAGES_HUB
    if page == "dashboard.html":
        reglages = texte.split('id="hub-reglages"')[1].split('<div class="choix-titre">')[0]
        assert "Fond d'écran" not in reglages and "setHub('motif'" not in texte





def test_le_nom_d_une_tuile_garde_ses_jambages():
    """Vecu : « Attention, les titres des apps sont coupes par la
    description » -- le bas du « g » de TradingBot manquait. Le nom est coupe
    a deux lignes (overflow:hidden) : sous 1.2, la ligne est plus basse que
    les jambages du Manrope gras, et g, j, p, y sont rognes."""
    import re as _re
    page = _page_panneau("dashboard.html")
    regle = page.split(".bento-carte-nom{")[1].split("}")[0]
    assert "overflow:hidden" in regle
    hauteur = float(_re.search(r"line-height:([0-9.]+)", regle).group(1))
    assert hauteur >= 1.2, hauteur
    for variante in (".bento-carte-nom.long{", ".bento-carte-nom.tres-long{"):
        assert "line-height" not in page.split(variante)[1].split("}")[0], variante




def test_la_video_prend_le_vrai_nom_de_l_application(tmp_path):
    """Vecu : « Prend le vrai nom de l'app avec les majuscules, pas le nom
    dans l'url ». Les noms techniques (dossier, adresse) du titre, du carton
    de fin et de la voix deviennent le nom affiche par le panneau ; un titre
    choisi a la main reste."""
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node absent")
    source = open(os.path.join(_racine_depot(), "dev", "fabriquer.mjs"), encoding="utf-8").read()
    fonction = source[source.index("function prendreLeVraiNom"):source.index("async function demanderAuPanneau")]
    essai = tmp_path / "essai.mjs"
    essai.write_text("const dire = () => {};\nlet scenario;\n" + fonction + """
const sorties = [];
scenario = {titre: "wine-manager", fin: {titre: "wine-manager"},
            etapes: [{voix: "Voici wine-manager, ta cave."}, {voix: "Le wine-manager-bis reste."}]};
prendreLeVraiNom("Wine Manager", ["wine-manager", "wine-manager"]);
sorties.push(scenario);
scenario = {titre: "Ma cave", etapes: [{voix: "Bienvenue"}]};
prendreLeVraiNom("Wine Manager", ["wine-manager"]);
sorties.push(scenario);
console.log(JSON.stringify(sorties));
""", encoding="utf-8")
    r = subprocess.run(["node", str(essai)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    import json as _json
    remplace, garde = _json.loads(r.stdout)
    assert remplace["titre"] == remplace["fin"]["titre"] == "Wine Manager"
    assert remplace["etapes"][0]["voix"] == "Voici Wine Manager, ta cave."
    assert remplace["etapes"][1]["voix"] == "Le wine-manager-bis reste."
    assert garde["titre"] == "Ma cave"


# ------------- Le texte au telephone, la finition des boutons -------------
#
# Vecu : « Ameliore completement le rendu sur telephone. Le texte s'affiche
# parfois mal, notamment dans les widgets » ; « Je veux que le rendu soit
# premium, des boutons de qualite ».

def _bloc_telephone(page):
    """Le dernier bloc @media (max-width:760px) du CSS du panneau : celui
    qui regle le hub et le texte au telephone."""
    return page.split("@media (max-width:760px){")[-1].split("\n}\n")[0]


def test_le_nom_a_la_une_tient_dans_sa_carte():
    """Au telephone, « Atlas des randonnees alpines » en 34 px debordait de
    la carte « A la une », et la petite icone, de la couleur du bandeau,
    laissait une lettre seule sous le surtitre."""
    import re as _re
    page = _page_panneau("dashboard.html")
    regle = page.split(".bw-une-nom{")[1].split("}")[0]
    assert "-webkit-line-clamp:2" in regle and "overflow:hidden" in regle
    assert "overflow-wrap:anywhere" in regle and "min-width:0" in regle
    assert float(_re.search(r"line-height:([0-9.]+)", regle).group(1)) >= 1.1
    assert ".bw-apercu.sans-direct + .bw-une-pied .bw-une-ligne img{display:none}" in page


def test_au_telephone_le_texte_se_coupe_au_lieu_de_deborder():
    """Les noms de tuile prennent trois lignes et se coupent dans le mot ;
    les onglets d'une fiche defilent au lieu de sortir de l'ecran ; le nom
    d'une barre de la vue d'ensemble a sa ligne ; l'adresse d'un compte se
    coupe au lieu de cacher « jamais connecte »."""
    tel = _bloc_telephone(_page_panneau("dashboard.html"))
    nom = tel.split(".bento-carte-nom{")[1].split("}")[0]
    assert "-webkit-line-clamp:3" in nom and "overflow-wrap:anywhere" in nom
    assert "line-height" not in nom          # celle de la regle de base (jambages)
    assert "#fiche-onglets{overflow-x:auto;" in tel
    assert "#fiche-onglets button{flex:none;white-space:nowrap}" in tel
    assert ".bar-name{grid-column:1 / -1}" in tel
    assert ".us-meta{white-space:normal;overflow-wrap:anywhere}" in tel


def test_les_boutons_repondent_au_doigt():
    """Un bouton s'enfonce a l'appui, a une hauteur reguliere, et 44 px au
    doigt (la cible tactile d'Apple et de Google)."""
    page = _page_panneau("dashboard.html")
    assert ".btn:not(:disabled):active,.icon-btn:not(:disabled):active{transform:scale(.97)}" in page
    assert ".btn{min-height:36px;font-weight:600}" in page
    doigt = page.split("@media (pointer:coarse){\n  .btn{")[1].split("}")[0]
    assert "min-height:44px" in doigt


def test_la_fiche_porte_le_nom_affiche():
    """« atlas » en titre d'une fiche que le hub appelle « Atlas des
    randonnees alpines » faisait douter d'etre sur la bonne."""
    page = _page_panneau("dashboard.html")
    assert "$('fa-nom').textContent=nomAffiche(a);" in page


def test_un_telephone_montre_les_applications_en_icones(deux_espaces):
    """Vecu : « les applications plutot sous forme d'icone pour la version
    mobile ». Un telephone part des icones ; « cartes » dans le cookie n'y
    compte que si elle a ete choisie (jeton « choisies ») -- le panneau
    ecrivait tous les reglages des qu'on changeait la couleur, et ce
    « cartes »-la n'etait que la valeur par defaut."""
    c = deux_espaces
    iphone = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148"}
    android = {"User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile Safari/537.36"}
    bureau = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15"}

    def tete(entetes):
        return re.search(r"<html[^>]*>", c.get("/login", headers=entetes).get_data(as_text=True)).group(0)

    c.delete_cookie("codelab-hub")
    assert 'data-tuiles="icones"' in tete(iphone)
    assert 'data-tuiles="icones"' in tete(android)
    assert 'data-tuiles="cartes"' in tete(bureau)
    c.set_cookie("codelab-hub", "cartes.horizontal.melanges.prune")
    t = tete(iphone)
    assert 'data-tuiles="icones"' in t and 'data-couleurs="prune"' in t
    assert "data-tuiles-choisies" not in t
    assert 'data-tuiles="cartes"' in tete(bureau)
    c.set_cookie("codelab-hub", "cartes.horizontal.melanges.prune.choisies")
    t = tete(iphone)
    assert 'data-tuiles="cartes"' in t and "data-tuiles-choisies" in t
    page = _page_panneau("dashboard.html")
    assert "if('tuiles' in reglages) racine.setAttribute('data-tuiles-choisies', '');" in page
    assert ".concat(racine.hasAttribute('data-tuiles-choisies') ? ['choisies'] : [])" in page


def test_au_telephone_chaque_taille_de_widget_a_sa_forme():
    """Vecu : « Gere la taille des widgets sur telephone ». Moyen et Grand
    prenaient la meme place ; un widget presque vide laissait un grand
    blanc (220 px, 400 pour « A la une »)."""
    tel = _bloc_telephone(_page_panneau("dashboard.html"))
    assert ".bw-moyen{min-height:150px}" in tel
    assert ".bw-grand{min-height:300px}" in tel
    assert ".bw-une{min-height:0}" in tel
    assert ".bw-grand .bw-apercu{min-height:280px}" in tel
    assert "min-height:400px" not in tel and "min-height:220px" not in tel


def test_un_mot_a_l_admin_garde_sa_zone_de_texte_en_taille_moyen():
    """Vecu : capture de l'utilisateur -- en taille Moyen sur un grand ecran,
    la zone de texte d'« Un mot a l'admin ? » tombait a 22 px, une ligne
    coupee en deux. Titre et sujets partagent une ligne, les boutons (et le
    choix de l'application) se rangent a droite : la zone garde au moins
    56 px ; une carte etroite cache le titre a l'oeil, et le texte d'exemple
    le reprend."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("@media (min-width:761px){\n  .bw-moyen.bw-message{")[1].split("\n}\n")[0]
    assert "container-type:inline-size;display:grid;" in bloc
    zone = bloc.split(".bw-moyen.bw-message textarea{")[1].split("}")[0]
    assert "grid-column:1 / 3" in zone and "min-height:56px" in zone
    assert ".bw-moyen.bw-message .bw-bas{grid-row:2;grid-column:3;" in bloc
    assert "width:0;min-width:100%" in bloc        # le choix d'application n'elargit pas la colonne
    etroit = bloc.split("@container (max-width:460px){")[1]
    assert ".bw-titre{position:absolute;" in etroit
    assert "placeholder=\"Pour l'admin : une idée, un souci, une demande d'accès.\"" in page


def test_le_tournage_s_arrete_devant_un_formulaire_de_connexion(tmp_path):
    """Vecu : « creer des videos de presentation sans avoir a renseigner des
    credentials ». Le laissez-passer donne l'identite CodeLab ; une
    application qui garde son propre ecran de connexion l'ignore. Plutot que
    de filmer ce formulaire (ou d'ecrire un mot de passe dans le scenario),
    codelab video s'arrete et dit de brancher codelab_auth."""
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node absent")
    source = open(os.path.join(_racine_depot(), "dev", "fabriquer.mjs"), encoding="utf-8").read()
    fonction = source[source.index("function formulaireDeConnexion"):]
    fonction = fonction[:fonction.index("\n}\n") + 2]
    essai = tmp_path / "essai.mjs"
    essai.write_text(fonction + """
const cas = [
  ['<form><input name="u"><input type="password" name="p"></form>', true],
  ["<input TYPE='password'>", true],
  ['<input type=password>', true],
  ['<p>Mot de passe oublie ?</p><input type="text">', false],
  ['', false],
];
for (const [html, attendu] of cas)
  if (formulaireDeConnexion(html) !== attendu) { console.log('KO', html); process.exit(1); }
console.log('ok');
""")
    r = subprocess.run(["node", str(essai)], capture_output=True, text=True)
    assert r.stdout.strip() == "ok", r.stdout + r.stderr
    tournage = source.split("if (ticket) {")[1].split("\n  }\n")[0]
    assert "formulaireDeConnexion(await sonde.text())" in tournage and "echouer(" in tournage
    manuel = open(os.path.join(_racine_depot(), "dev", "AGENTS.md"), encoding="utf-8").read()
    assert "**Obligatoire des que l'application a ses propres comptes**" in manuel


# ===========================================================================
# LES APPLICATIONS
# detection, configuration, lancement et arret, renommage, logo, applications
# par defaut, processus qui survivent, robustesse, dependances.
# ===========================================================================

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
    assert commande == "python3 -m http.server $PORT --bind 127.0.0.1 --directory dist"
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



def test_les_commandes_proposees_n_ecoutent_que_sur_127_0_0_1(tmp_path):
    """Vecu : python3 -m http.server et next start ecoutent partout par
    defaut, et la commande Django portait 0.0.0.0 en dur. Une application
    privee etait alors joignable depuis les autres conteneurs du reseau
    codelab, sans passer par le panneau, donc sans connexion."""
    cas = [
        ({"index.html": "<html>"}, "--bind 127.0.0.1"),
        ({"manage.py": ""}, "runserver 127.0.0.1:$PORT"),
        ({"package.json": json.dumps({"scripts": {"build": "vite build"},
                                      "devDependencies": {"vite": "^5"}})}, "--bind 127.0.0.1"),
        ({"package.json": json.dumps({"scripts": {"build": "next build"},
                                      "dependencies": {"next": "14"}})}, "--hostname 127.0.0.1"),
    ]
    for i, (fichiers, attendu) in enumerate(cas):
        dossier = tmp_path / f"cas{i}"
        dossier.mkdir()
        commande, _ = app.detect_project(_projet(dossier, fichiers))
        assert attendu in commande and "0.0.0.0" not in commande, commande


def test_la_fiche_signale_une_application_qui_ecoute_partout(monkeypatch):
    """Le panneau ne peut pas choisir l'adresse a la place du code de
    l'application : il releve les ports ecoutes sur 0.0.0.0 ou :: et le dit
    dans la fiche, sans compter ceux tenus sur 127.0.0.1."""
    from types import SimpleNamespace as N
    ecoute = app.psutil.CONN_LISTEN
    conns = [N(status=ecoute, laddr=N(ip="0.0.0.0", port=9101)),
             N(status=ecoute, laddr=N(ip="::", port=9102)),
             N(status=ecoute, laddr=N(ip="127.0.0.1", port=9103)),
             N(status="ESTABLISHED", laddr=N(ip="0.0.0.0", port=9104))]
    monkeypatch.setattr(app.psutil, "net_connections", lambda kind="tcp": conns)
    assert app.ports_ouverts_au_reseau() == {9101, 9102}
    monkeypatch.setattr(app, "load", lambda: {"large": {"port": 9101}, "sage": {"port": 9103}})
    monkeypatch.setattr(app, "is_running", lambda n: True)
    monkeypatch.setattr(app, "probe_port", lambda p: True)
    monkeypatch.setattr(app, "_listening", {})
    monkeypatch.setattr(app, "_ecoute_large", {})
    app.probe_tick()
    assert app._ecoute_large == {"large": True, "sage": False}
    page = _page_panneau("dashboard.html")
    assert 'id="fa-ecoute-large"' in page and "a.ecoute_large" in page


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
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
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
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.get("/api/logs/public/stream")
    assert r.status_code == 503
    assert "journaux" in r.get_json()["error"]




# ---------- 12 octies. plus de conteneurisation ----------
#
# La fonction a ete retiree : le panneau ne produit plus de Dockerfile.

def test_la_conteneurisation_a_ete_retiree(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.get("/api/app/site/conteneur").status_code == 404
    assert c.get("/api/app/site/conteneur.zip").status_code == 404
    assert not hasattr(app, "fichiers_conteneur")
    page = _page_panneau("dashboard.html")
    assert "onglet-conteneur" not in page and "ctTelecharger" not in page


# ---------- 12 ter. le logo d'une application ----------
#
# Il vit dans le DOSSIER du projet : un projet emporte son logo quand on le
# copie ailleurs. Et il est servi par /api/icon, donc DANS L'ORIGINE DU
# PANNEAU -- ce qui decide de ce qu'on accepte.




def test_un_logo_depose_vit_dans_le_dossier_du_projet(logo):
    """Et non dans un coin d'etat du panneau : le projet emporte son logo
    quand on le copie ailleurs, et un icon.png pose a la main donne
    exactement le meme resultat."""
    c, projet = logo
    r = c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    assert r.status_code == 200, r.data
    assert (projet / "icon.png").exists()
    assert r.get_json()["fichier"] == "icon.png"




def test_l_icone_ne_suit_pas_un_lien_vers_un_secret(logo, tmp_path):
    """Le panneau lit en root ; le dossier d'un projet est ecrit par
    l'application, du code arbitraire. Un « icon.png » en lien vers
    credentials.env faisait servir les secrets de la stack par /api/icon
    -- a tout compte qui voit l'application (regle 19)."""
    c, projet = logo
    secret = tmp_path / "credentials.env"
    secret.write_text("APP_MANAGER_ADMIN_PASSWORD=ne-doit-pas-sortir\n", encoding="utf-8")
    (projet / "icon.png").symlink_to(secret)
    (projet / "logo.png").symlink_to("/dev/zero")     # ni un tube, ni un peripherique
    r = c.get("/api/icon/site")
    assert r.status_code == 200
    assert b"ne-doit-pas-sortir" not in r.data
    assert r.mimetype == "image/svg+xml"              # l'initiale, faute d'icone sure
    # Un vrai fichier, lui, est servi.
    (projet / "favicon.png").write_bytes(_png())
    r = c.get("/api/icon/site")
    assert r.data == _png() and r.mimetype == "image/png"
    assert app.lire_icone(str(projet))[1] == "favicon.png"


def test_envoyer_un_logo_n_ecrit_pas_a_travers_un_lien(logo, tmp_path):
    """L'ecriture passait par un nom fixe, « icon.png.tmp » : pose d'avance
    en lien vers credentials.env, il etait suivi, et l'envoi d'un logo
    ecrasait le fichier vise en root."""
    c, projet = logo
    secret = tmp_path / "credentials.env"
    secret.write_text("intact\n", encoding="utf-8")
    for nom in ("icon.png.tmp", "icon.png"):
        (projet / nom).symlink_to(secret)
    r = c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    assert r.status_code == 200, r.data
    assert secret.read_text(encoding="utf-8") == "intact\n"
    assert not (projet / "icon.png").is_symlink()
    assert (projet / "icon.png").read_bytes() == _png()
    assert not [p for p in projet.iterdir() if p.name.startswith(".icon-")]




def test_un_svg_est_refuse(logo):
    """Une image SVG est un document qui peut porter du script. Servie par
    /api/icon, donc dans l'origine du panneau, elle l'executerait la."""
    c, projet = logo
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = c.put("/api/app/site/logo", data=svg, content_type="image/svg+xml")
    assert r.status_code == 400, r.data
    assert "SVG" in r.get_json()["error"]
    assert not list(projet.glob("icon.*"))




def test_le_format_est_lu_dans_les_octets_pas_dans_l_entete(logo):
    """Un envoi annonce ce qu'il veut : c'est la signature du fichier qui
    fait foi. Sans cela, il suffisait d'annoncer image/png."""
    c, projet = logo
    r = c.put("/api/app/site/logo",
              data=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
              content_type="image/png")
    assert r.status_code == 400, r.data
    assert not list(projet.glob("icon.*"))




def test_une_image_trop_lourde_est_refusee(logo):
    c, projet = logo
    r = c.put("/api/app/site/logo",
              data=_png() + b"\x00" * (app.LOGO_MAX_OCTETS + 1),
              content_type="image/png")
    assert r.status_code == 400, r.data
    assert "lourde" in r.get_json()["error"]
    assert not list(projet.glob("icon.*"))




def test_un_seul_logo_a_la_fois(logo):
    """find_icon prend le premier nom de sa liste : un icon.png laisse en
    place survivrait a l'envoi d'un icon.gif et continuerait de s'afficher."""
    c, projet = logo
    c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    r = c.put("/api/app/site/logo", data=b"GIF89a" + b"\x00" * 32,
              content_type="image/gif")
    assert r.status_code == 200, r.data
    assert not (projet / "icon.png").exists()
    assert (projet / "icon.gif").exists()




def test_retirer_le_logo_ne_touche_pas_a_ce_que_le_projet_a_pose(logo):
    """Un favicon.ico depose a la main dans le projet lui appartient : le
    panneau n'efface que ce qu'il a lui-meme ecrit."""
    c, projet = logo
    (projet / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    r = c.delete("/api/app/site/logo")
    assert r.status_code == 200, r.data
    assert not (projet / "icon.png").exists()
    assert (projet / "favicon.ico").exists()




def test_le_depot_d_un_logo_est_journalise(logo):
    """Une image deposee dans le dossier d'un projet est une ecriture : elle
    se retrouve dans le journal comme le reste."""
    c, _ = logo
    c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    evenements = [e for e in app.lire_acces() if e.get("genre") == "logo"]
    assert evenements and evenements[0]["app"] == "site"
    assert evenements[0]["action"] == "depose"




def test_l_icone_servie_ne_peut_pas_executer_de_script(logo):
    """Un SVG pose A LA MAIN dans le projet reste lu par find_icon -- c'est
    voulu, le projet a le droit. La politique de securite le rend inerte."""
    c, projet = logo
    (projet / "icon.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>')
    r = c.get("/api/icon/site")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"




def test_seul_l_administrateur_depose_un_logo(logo, monkeypatch):
    c, _ = logo
    anonyme = app.flask_app.test_client()
    assert anonyme.put("/api/app/site/logo", data=_png(),
                       content_type="image/png").status_code in (401, 403)


# ---------- 12 bis. enregistrer la configuration d'une application ----------
#
# Vecu : « Rend possible la sauvegarde de la configuration d'une application.
# Proposer un redemarrage. Les champs qui ne necessitent pas de redemarrage
# peuvent etre appliques directement. »
#
# La route refusait tout net pendant qu'une application tournait : corriger
# une faute dans une description demandait de couper le service. Le refus
# protegeait d'une illusion reelle -- croire qu'une commande modifiee
# s'applique au processus deja lance -- mais il la traitait en interdisant
# tout, alors qu'il suffit de DIRE lequel des champs attend un redemarrage.




def test_une_application_en_marche_s_enregistre_desormais(en_marche):
    """C'etait la demande : pouvoir enregistrer sans couper le service."""
    r = _editer(en_marche, description="apres")
    assert r.status_code == 200, r.data
    assert app.load()["site"]["description"] == "apres"




def test_ce_qui_se_relit_a_chaque_requete_s_applique_tout_de_suite(en_marche):
    """Description, categorie, visibilite, commande de build : le panneau les
    relit a chaque fois qu'il s'en sert. Rien a redemarrer."""
    r = _editer(en_marche, description="apres", visibility="privee",
                build_command="npm install")
    d = r.get_json()
    assert d["redemarrage_requis"] is False, d
    assert d["champs_en_attente"] == []
    assert app.load()["site"]["visibility"] == "privee"




def test_ce_qui_est_lu_au_lancement_attend_le_redemarrage(en_marche):
    """La commande, le dossier et la limite memoire sont lus quand le
    processus demarre. Les changer ne touche pas celui qui tourne -- et le
    taire laisserait croire le contraire."""
    r = _editer(en_marche, command="python3 autre.py", max_memory_mb=128)
    d = r.get_json()
    assert d["redemarrage_requis"] is True, d
    assert set(d["champs_en_attente"]) == {"command", "max_memory_mb"}
    # Enregistre malgre tout : c'est le prochain demarrage qui la prendra.
    assert app.load()["site"]["command"] == "python3 autre.py"




def test_une_application_arretee_n_a_rien_a_redemarrer(en_marche, monkeypatch):
    """Reclamer un redemarrage a qui ne tourne pas serait un faux message :
    le prochain demarrage prendra la nouvelle configuration tout seul."""
    monkeypatch.setattr(app, "is_running", lambda n: False)
    d = _editer(en_marche, command="python3 autre.py").get_json()
    assert d["redemarrage_requis"] is False
    assert d["running"] is False




def test_reenregistrer_a_l_identique_ne_reclame_pas_de_redemarrage(en_marche):
    """La comparaison porte sur la valeur NETTOYEE : un espace en fin de
    ligne ne doit pas annoncer un redemarrage necessaire."""
    d = _editer(en_marche, command="python3 app.py", max_memory_mb=256).get_json()
    assert d["redemarrage_requis"] is False, d




def test_la_liste_des_champs_qui_attendent_vit_du_cote_serveur():
    """Une copie dans le navigateur aurait diverge a la premiere evolution :
    c'est le serveur qui sait ce qu'il relit, et quand."""
    assert app.CHAMPS_AU_DEMARRAGE == ("path", "command", "max_memory_mb")
    page = _page_panneau("dashboard.html")
    # La page lit la reponse du serveur, elle ne rejoue pas la regle.
    assert "d.redemarrage_requis" in page
    assert "champs_en_attente" in page
    # Et l'ancien bandeau bloquant a bien disparu.
    assert "stopThenEdit" not in page
    assert "ne peut pas être enregistrée" not in page


# ---------- les processus survivent au panneau ----------
#
# Vecu : « Le bouton arreter l'application ne fonctionne pas. »
#
# Les applications sont lancees avec start_new_session=True : elles ont leur
# propre session, donc elles survivent a l'arret du panneau -- mise a jour de
# l'image, "docker restart", plantage. Le dictionnaire procs, lui, vit en
# memoire et ne survivait pas.
#
# Au redemarrage, le panneau ne connaissait donc plus les processus toujours
# en vie : l'interface affichait « Arretee » d'une application qui tournait,
# resume() en relancait une deuxieme qui mourait sur un port occupe, et
# ARRETER NE FAISAIT RIEN -- stop() ne trouvait rien a tuer, notait
# enabled=False et repondait que tout allait bien.




def test_un_processus_lance_est_note_sur_le_disque(survivants):
    """Sans note, rien ne permet de le retrouver apres un redemarrage : le
    panneau est son pere, et un pere mort ne laisse aucune trace."""
    assert app.start("site") is None
    note = app.lire_processus()["site"]
    assert note["pid"] == app.procs["site"].pid
    app.stop("site")
    # Et la note disparait avec lui : une note qui traine ferait adopter un
    # pid reutilise par n'importe quel autre programme.
    assert "site" not in app.lire_processus()




def test_le_panneau_reprend_la_main_sur_ce_qui_tourne_encore(survivants):
    """LE test de cette correction : on simule le redemarrage du panneau en
    vidant procs -- exactement ce que fait un "docker restart" -- et on
    verifie qu'arreter tue reellement le processus."""
    assert app.start("site") is None
    pid = app.procs["site"].pid

    app.procs.clear()                       # le panneau redemarre
    assert app.is_running("site") is False  # etat d'avant la correction
    repris = app.adopter_processus_survivants()
    assert repris and "site" in repris[0]
    assert app.is_running("site") is True

    app.stop("site")
    for _ in range(40):
        if not app._processus_est_le_notre(pid, "site"):
            break
        time.sleep(0.1)
    assert not app._processus_est_le_notre(pid, "site"), (
        "le processus tourne toujours apres un arret demande")




def test_un_pid_qui_n_est_plus_le_notre_n_est_pas_adopte(survivants):
    """Un numero de processus est reutilise par le noyau. Adopter sur le seul
    pid finirait par tuer un programme qui n'a rien demande -- pire que le
    defaut qu'on corrige."""
    app._ecrire_processus({"site": {"pid": os.getpid(), "depuis": 0}})
    app.procs.clear()
    assert app.adopter_processus_survivants() == []
    assert app.is_running("site") is False
    # La note fausse est nettoyee au passage.
    assert app.lire_processus() == {}




def test_la_marque_est_posee_dans_l_environnement_du_processus(survivants):
    """Dans l'environnement, et non dans le titre du processus : le noyau le
    fige a l'exec, l'application ne peut donc pas l'effacer."""
    assert app.start("site") is None
    pid = app.procs["site"].pid
    environ = open(f"/proc/{pid}/environ", "rb").read()
    assert b"CODELAB_APP=site\0" in environ




def test_l_adoption_passe_avant_la_reprise_automatique():
    """Ordre imperatif : resume() relancerait par-dessus une application deja
    en vie, et la nouvelle mourrait sur un port occupe."""
    src = _page_panneau("app.py")
    demarrage = src.split('if __name__ == "__main__":')[1]
    # Les commentaires citent les deux noms : on ne regarde que les appels.
    lignes = [l.strip() for l in demarrage.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    assert lignes.index("adopter_processus_survivants()") < lignes.index("resume()")




# ---------- 23 octies. demarrer ne se tait plus quand ca echoue ----------
#
# Signale par Lucas : "arreter et pause ne fonctionne pas dans la page
# applications". Mesure au navigateur : le clic partait bien, la requete
# aboutissait, l'API repondait 200 OK -- et l'application restait arretee,
# sans un mot.
#
# api_toggle repondait {"ok": True} sans rien verifier, et start() se taisait
# dans tous ses cas d'echec : dossier disparu, commande introuvable, port
# deja pris, isolement refuse. Il fallait aller lire le journal de
# l'application, en supposant qu'on sache qu'il existe.

def test_demarrer_verifie_que_l_application_vit_encore():
    src = _page_panneau("app.py")
    assert "def start(name, attendre=True):" in src, (
        "start() ne prend plus le temps de regarder l'application vivre")
    assert "DELAI_DEMARRAGE" in src
    assert "def derniere_ligne_utile(" in src, (
        "sans la derniere ligne du journal, le message n'apprend rien")
    # L'echec remonte a l'appelant, il ne se contente pas d'un print.
    bloc = src[src.index("def start(name, attendre=True):"):src.index("def stop(name):")]
    assert "return f\"Dossier introuvable" in bloc
    # Le message est LU par quelqu'un : il porte ses accents, contrairement
    # au code qui l'entoure.
    assert "s'est arr\u00eat\u00e9e aussit\u00f4t" in bloc




def test_l_api_toggle_rend_compte_de_l_echec():
    src = _page_panneau("app.py")
    bloc = src[src.index("def api_toggle("):]
    bloc = bloc[:bloc.index("\n@flask_app", 10)]
    assert "stop(n) if action" in bloc and "else start(n)" in bloc, (
        "l'API ignore ce que stop() ou start() lui rendent")
    assert "409" in bloc, (
        "un echec de demarrage doit se voir dans le code de reponse")
    # Le SENS de l'action voyage avec la reponse : sans lui, la page ne peut
    # que deviner, et son repli est ecrit pour le demarrage.
    assert '"action": action' in bloc
    # Et plus aucune exception ne sort d'ici en page HTML : la page n'y
    # trouverait aucun JSON et retomberait sur ce meme repli.
    assert "except Exception" in bloc and "500" in bloc




def test_le_port_deja_pris_se_dit_avant_de_lancer(tmp_path, monkeypatch):
    """« Address already in use » au fond d'un journal, dans la langue d'un
    autre programme, n'est pas une reponse. Le cas est frequent -- deux
    applications sur le meme port, un survivant d'un arret brutal -- et il se
    reconnait AVANT de lancer quoi que ce soit."""
    import socket as _s
    projet = tmp_path / "projet"
    projet.mkdir()
    prise = _s.socket()
    prise.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    prise.bind(("127.0.0.1", 0))
    prise.listen(1)
    port = prise.getsockname()[1]
    try:
        app.save({"bloquee": {"path": str(projet), "command": "sleep 60",
                              "port": port, "visibility": "privee"}})
        erreur = app.start("bloquee")
        assert erreur and str(port) in erreur, erreur
        assert "occup" in erreur, erreur
        # Rien n'a ete lance : un port pris ne doit pas laisser un processus
        # derriere lui.
        assert not app.is_running("bloquee")
    finally:
        prise.close()
        app.stop("bloquee")

    # Et le port libre ne declenche rien : sinon on refuserait tout.
    assert app.port_occupe_par(port) is None




def test_les_restes_d_une_application_morte_liberent_son_port(monkeypatch):
    """Vecu : « Le port 9104 est deja occupe sur la machine », WineManager en
    erreur et relancee en vain. Le processus lance par le panneau etait
    mort, mais un enfant tenait encore le port, et le panneau -- sans
    CAP_SYS_PTRACE -- ne pouvait pas le nommer. Avant de relancer, start()
    termine ce qui reste du groupe : le port se libere, sans rien d'autre."""
    import socket as _s
    import subprocess as _sp
    import time as _t
    libre = _s.socket()
    libre.bind(("127.0.0.1", 0))
    port = libre.getsockname()[1]
    libre.close()
    ecoute = ("import socket,time;s=socket.socket();"
              "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
              f"s.bind(('127.0.0.1',{port}));s.listen(1);time.sleep(60)")
    # Le chef lance un enfant qui ecoute, puis meurt : le cas de npm, d'un
    # rechargeur ou d'un serveur a workers.
    chef = _sp.Popen(["sh", "-c", f'python3 -c "{ecoute}" & exit 0'],
                     start_new_session=True)
    chef.wait()
    monkeypatch.setitem(app.procs, "restes", chef)
    monkeypatch.setattr(app, "uid_application", lambda nom: None)

    def pris():
        sonde = _s.socket()
        sonde.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
        try:
            sonde.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True
        finally:
            sonde.close()

    for _ in range(50):
        if pris():
            break
        _t.sleep(0.1)
    assert pris(), "l'enfant n'a pas pris le port"
    try:
        app.achever_les_restes("restes")
        assert not pris(), "le port reste tenu par les restes de l'application"
    finally:
        try:
            os.killpg(chef.pid, 9)
        except ProcessLookupError:
            pass

    # Branche aux deux endroits : avant de relancer, et a l'arret.
    src = _page_panneau("app.py")
    debut = src[src.index("def start(name, attendre=True):"):]
    assert debut.index("achever_les_restes(name)") < debut.index("port_occupe_par(a[")
    arret = src[src.index("def stop(name):"):].split("\ndef ")[0]
    assert "achever_les_restes(name)" in arret
    # Et le message ne renvoie plus a « ss », absent de l'image et aveugle
    # depuis l'hote.
    occupe = src[src.index("def port_occupe_par("):].split("\ndef ")[0]
    assert "ss -ltnp |" not in occupe




def test_un_port_libere_a_l_instant_ne_bloque_pas_le_redemarrage():
    """Un port fraichement ferme reste en TIME_WAIT. Sans SO_REUSEADDR sur la
    sonde, on refuserait de redemarrer ce qu'on vient d'arreter -- le geste
    le plus courant du panneau."""
    src = _page_panneau("app.py")
    bloc = src.split("def port_occupe_par(")[1].split("\ndef ")[0]
    assert "SO_REUSEADDR" in bloc
    # Sur 127.0.0.1 : c'est l'adresse que le proxy contacte, et une sonde sur
    # 0.0.0.0 refuserait un demarrage qu'un service sur une autre adresse
    # n'aurait pas empeche.
    assert '"127.0.0.1"' in bloc




def test_un_lancement_impossible_ne_devient_pas_une_erreur_500():
    """Popen leve pour tout ce qui empeche le lancement avant la commande.
    L'exception remontait a Flask : 500, page HTML, aucun message pour
    l'interface -- qui affichait alors son texte par defaut."""
    src = _page_panneau("app.py")
    bloc = src[src.index("def start(name, attendre=True):"):src.index("def port_occupe_par(")]
    assert "except (OSError, ValueError)" in bloc
    assert 'return f"Lancement impossible' in bloc




def test_le_redemarrage_rend_compte_de_son_echec():
    """restart_app() jetait l'erreur de start() : la route repondait « ok »,
    et le bouton laissait une application arretee sans un mot."""
    src = _page_panneau("app.py")
    bloc = src.split("def restart_app(n):")[1].split("\n\n\n")[0]
    assert "return start(n)" in bloc, "l'erreur de start() repart a la poubelle"
    route = src.split("def api_restart(n):")[1].split("\n\n\n")[0]
    assert "erreur = restart_app(n)" in route and "409" in route




def test_aucune_action_ne_se_rabat_sur_une_phrase_vide():
    """« L'application n'a pas demarre » n'apprend rien. Quand le serveur ne
    dit rien du tout, le code HTTP distingue au moins un refus d'une panne."""
    page = _page_panneau("dashboard.html")
    aide = page.split("async function echec(r, defaut){")[1].split("\n}")[0]
    assert "r.status" in aide and "401" in aide and "403" in aide
    for action in ("async function tg(n){", "async function restartApp(n){",
                   "async function buildApp(n){"):
        bloc = page.split(action)[1].split("\n}")[0]
        assert "await echec(r," in bloc, f"{action} ne rend pas compte de l'echec"




def test_le_bouton_lit_la_reponse():
    """Deux silences valaient mieux qu'un : meme si l'API avait repondu une
    erreur, tg() jetait la reponse sans la regarder."""
    _, script = _script_panneau_html()
    bloc = script[script.index("async function tg(n){"):]
    bloc = bloc[:bloc.index("async function", 10)]
    assert "r.ok" in bloc and "notifier(" in bloc, (
        "le bouton ignore la reponse : l'utilisateur clique dans le vide")




# ---------- 30. les applications par defaut ----------
#
# Demande : « Dagster doit se comporter comme les autres applications, meme si
# rien n'est configurable pour elle. De la meme maniere, Diagnostic ne doit
# pas etre configurable. »
#
# Deux applications que CodeLab apporte avec lui. Elles se lisent comme les
# autres -- meme tuile, meme ligne, meme fiche -- et c'est le but : ce sont
# des applications, pas des boutons a part. Ce qui change tient en une
# phrase : il n'y a rien a y configurer.

def test_les_applications_par_defaut_ne_se_configurent_pas(hub_dagster):
    """Le refus vient du SERVEUR, pas de la page.

    Masquer un onglet ne protege rien : la route reste appelable a la main,
    et c'est exactement ce que fait qui cherche a contourner.
    """
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    apps = app.load()
    # Le diagnostic est declare comme sur une vraie installation.
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "visibility": "privee"}
    app.save(apps)

    for nom in app.APPS_PAR_DEFAUT:
        refus = [
            ("put", f"/api/app/{nom}", {"path": "/w/x", "command": "x"}),
            ("delete", f"/api/app/{nom}", None),
            ("post", f"/api/app/{nom}/renommer", {"nom": "autre"}),
            ("post", f"/api/visibility/{nom}", {"visibility": "publique"}),
            ("put", f"/api/apps/{nom}/acces", {"utilisateurs": ["marie"]}),
            ("delete", f"/api/app/{nom}/logo", None),
        ]
        for methode, route, corps in refus:
            r = getattr(c, methode)(route, json=corps or {})
            assert r.status_code == 403, f"{methode.upper()} {route} -> {r.status_code}"
            assert "par défaut" in (r.get_json() or {}).get("error", ""), route

    # Et une application ordinaire, elle, se configure toujours.
    r = c.post("/api/visibility/site", json={"visibility": "privee"})
    assert r.status_code == 200, r.data




def test_dagster_figure_parmi_les_applications(hub_dagster):
    """Elle vivait sur le hub et nulle part ailleurs : on la cherchait dans
    « Applications », elle n'y etait pas."""
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    lignes = c.get("/api/apps").get_json()["apps"]
    par_nom = {a["name"]: a for a in lignes}
    assert app.DAGSTER_NOM in par_nom, "Dagster manque dans la liste"
    d = par_nom[app.DAGSTER_NOM]
    assert d["externe"] is True and d["par_defaut"] is True
    # Les memes cles que les autres lignes : la page les lit sans se demander
    # a chaque fois si elle a affaire a un service.
    ordinaire = par_nom["site"]
    assert set(ordinaire) - set(d) == set(), sorted(set(ordinaire) - set(d))
    # Et la liste reste triee : une tuile qui change de place d'un
    # rafraichissement a l'autre se cherche a chaque fois.
    assert [a["name"] for a in lignes] == sorted(par_nom)




def test_une_application_par_defaut_est_reservee_a_l_administrateur(hub_dagster):
    """Pas « privee, donc autorisable a un compte » : reservee.

    Dagster permet de lancer des jobs, donc d'executer du code sur cette
    machine ; le diagnostic nomme les conteneurs, les comptes sans second
    facteur et ce qui est expose. Ni un clic ni une faute de manipulation ne
    doivent pouvoir les accorder.
    """
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "x", "port": 9109,
                                "visibility": "privee"}
    app.save(apps)
    c = hub_dagster
    _connecte(c, "marie", "un-mot-de-passe")
    for nom in app.APPS_PAR_DEFAUT:
        r = c.get(f"/{nom}/")
        assert r.status_code in (302, 403, 404), f"{nom} -> {r.status_code}"
    # Son propre projet, lui, s'ouvre toujours.
    assert c.get("/site/").status_code != 403

    # Et son hub ne lui montre pas de tuile qui ne s'ouvre pas : une porte
    # fermee affichee comme une porte, c'est quelqu'un qui cherche ce qu'il a
    # mal fait. Meme si l'administrateur la lui a accordee par megarde dans
    # sa fiche -- ce que l'API refuse, mais qu'un fichier edite a la main
    # pourrait contenir.
    d = c.get("/api/mes-apps").get_json()
    noms = {a["name"] for a in d["apps"]} | {a["name"] for a in d.get("masquees", [])}
    assert not (noms & set(app.APPS_PAR_DEFAUT)), sorted(noms)
    # Dagster n'apparait pas : son interface execute du code sur la machine.
    # Et rien d'autre n'apparait a sa place : le hub d'un compte utilisateur
    # ne porte aucune tuile de service.
    services = {x["name"] for x in d.get("services", [])}
    assert services == set(), sorted(services)




def test_les_applications_par_defaut_suivent_la_frontiere_de_l_administration(
        hub_dagster, tmp_path, monkeypatch):
    """« Fais la meme chose pour diagnostic. »

    Le diagnostic dit ce qui tourne, ce qui est expose et quels comptes n'ont
    pas de second facteur : c'est une page d'administration par ce qu'elle
    montre. Elle s'arrete donc ou l'administration s'arrete -- meme reglage,
    meme frontiere, y compris au milieu d'une session deja ouverte.
    """
    monkeypatch.setattr(app, "admin_limite_au_reseau_local", lambda: True)
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "x", "port": 9109,
                                "visibility": "privee"}
    app.save(apps)
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"},
           environ_overrides={"REMOTE_ADDR": "192.168.1.10"})

    # Depuis le reseau local : la requete atteint le proxy (l'application
    # n'ecoute pas dans un test, d'ou l'erreur de passerelle).
    r = c.get("/diagnostic/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
    assert r.status_code != 403, r.data[:160]

    # La MEME session, vue d'ailleurs : refusee.
    r = c.get("/diagnostic/", environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code == 403
    assert b"reseau local" in r.data or b"r\xc3\xa9seau local" in r.data




def test_l_administrateur_du_reseau_local_reste_connecte(client, tmp_path, monkeypatch):
    """La question posee, verifiee plutot que lue.

    Toutes les adresses privees ouvrent l'administration et la GARDENT :
    le controle a lieu a chaque requete, il ne doit donc pas refuser une
    session qui n'a pas bouge. Tailscale (100.64/10) compte comme local --
    c'est ainsi qu'on administre depuis l'exterieur sans rien ouvrir.
    """
    monkeypatch.setattr(app, "admin_limite_au_reseau_local", lambda: True)
    locales = ("192.168.1.42", "10.8.0.5", "172.20.0.3", "127.0.0.1",
               "100.101.102.103")
    for ip in locales:
        app._login_attempts.clear()
        r = client.post("/login", json={"nom": "admin", "password": "secret-de-test"},
                        environ_overrides={"REMOTE_ADDR": ip})
        assert r.status_code == 200, f"{ip} : connexion refusee"
        # Et la session TIENT : trois requetes de suite, comme un usage reel.
        for _ in range(3):
            assert client.get("/api/apps",
                              environ_overrides={"REMOTE_ADDR": ip}).status_code == 200, ip
    # De l'exterieur, rien ne s'ouvre.
    for ip in ("2.15.44.7", "203.0.113.9"):
        app._login_attempts.clear()
        assert client.post("/login", json={"nom": "admin", "password": "secret-de-test"},
                           environ_overrides={"REMOTE_ADDR": ip}).status_code == 403, ip




def test_une_application_par_defaut_ne_s_arrete_pas_a_la_main(hub_dagster):
    """Le cycle de vie aussi est refuse cote SERVEUR, pas seulement masque.

    Le piege qu'on ferme : une application par defaut n'est pas
    configurable. La laisser s'arreter d'un clic, c'est offrir un bouton
    « Arreter » sans le bouton qui rallume -- l'installation se retrouverait
    sans diagnostic, sans rien pour le dire et sans moyen d'y revenir.

    Les gardes existaient deja dans le panneau, mais AUCUN test ne les
    tenait : un refactor pouvait les faire disparaitre en silence.
    """
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "visibility": "privee"}
    app.save(apps)

    for nom in app.APPS_PAR_DEFAUT:
        for route in (f"/api/toggle/{nom}", f"/api/restart/{nom}",
                      f"/api/build/{nom}"):
            r = c.post(route, json={})
            assert r.status_code == 403, f"POST {route} -> {r.status_code}"
            # Le message NOMME l'application et l'action : « 403 » seul
            # n'apprend rien a qui lit la reponse.
            message = (r.get_json() or {}).get("error", "")
            assert nom in message, route
            assert "interdite" in message, route

    # Et une application ordinaire n'est pas prise dans le meme filet -- sans
    # quoi un refus rendu pour tout le monde passerait ce test.
    assert app.refus_action_protegee("site", "arrêt/redémarrage") is None




def test_les_descriptions_par_defaut_tiennent_entieres_sur_leur_tuile():
    """Vecu : « synthetise les descriptions des applications par defaut du
    hub pour qu'elles soient visibles entierement. » Une tuile en montre
    environ 45 caracteres (DESCRIPTION_VISIBLE) : 40 au plus, avec de la
    marge pour les lettres larges."""
    page = _page_panneau("dashboard.html")
    assert "DESCRIPTION_VISIBLE=45" in page
    for d in (app.DIAGNOSTIC_DESCRIPTION, app.DESCRIPTION_DAGSTER,
              app.DESCRIPTION_MAILPIT, app.MESSAGERIE_DESCRIPTION):
        assert 0 < len(d) <= 40, d





def test_le_panneau_tient_ses_applications_par_defaut_debout(hub_dagster):
    """Le chemin qui mene a l'etat mort, et ce qui en sort.

    start() marque « enabled: False » quand le lancement echoue (limite
    memoire refusee, abandon de privileges impossible). Personne ne l'a
    voulu, et le bouton qui rallumerait est justement celui qu'on interdit
    au test precedent. Sans ce rattrapage au demarrage, l'application par
    defaut restait eteinte pour de bon.
    """
    apps = app.load()
    apps[app.DAGSTER_NOM] = {"path": "/w/dag", "command": "x", "port": 9108,
                             "enabled": False}
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False}
    apps[app.MESSAGERIE_NOM] = {"path": "/w/messages", "command": "python3 app.py",
                                "port": 9110, "enabled": False}
    app.save(apps)

    app.tenir_les_applications_par_defaut()

    apres = app.load()
    for nom in app.APPS_PAR_DEFAUT:
        assert apres[nom]["enabled"] is True, nom
    # La tuile du diagnostic dit ce qu'elle est : sa fiche n'ayant pas de
    # formulaire, cette phrase ne s'ecrit nulle part ailleurs.
    assert apres[app.DIAGNOSTIC_NOM]["description"] == app.DIAGNOSTIC_DESCRIPTION
    assert apres[app.MESSAGERIE_NOM]["description"] == app.MESSAGERIE_DESCRIPTION
    # Rien n'est impose a Dagster : ce qu'on repare ici, c'est l'etat mort.
    assert "description" not in apres[app.DAGSTER_NOM]
    # Une application ordinaire arretee le RESTE : un arret volontaire est un
    # choix, et le rattrapage ne vaut que pour celles qui n'ont pas le choix.
    assert not apres["site"].get("enabled")




def test_la_description_deja_posee_n_est_pas_ecrasee(hub_dagster):
    """Le rattrapage comble un manque, il ne decide pas a la place."""
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False,
                                "description": "Ma phrase à moi"}
    app.save(apps)

    app.tenir_les_applications_par_defaut()

    apres = app.load()
    assert apres[app.DIAGNOSTIC_NOM]["description"] == "Ma phrase à moi"
    assert apres[app.DIAGNOSTIC_NOM]["enabled"] is True




def test_le_demarrage_du_panneau_rallume_ce_qui_ne_se_rallume_pas_a_la_main(
        hub_dagster, monkeypatch):
    """Bout en bout : le rattrapage est BRANCHE, pas seulement ecrit.

    Une fonction correcte que resume() n'appelle pas ne repare rien.
    """
    demarrees = []
    monkeypatch.setattr(app, "start", lambda n, attendre=True: demarrees.append(n))
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False}
    app.save(apps)

    app.resume()

    assert app.DIAGNOSTIC_NOM in demarrees, demarrees
    assert app.load()[app.DIAGNOSTIC_NOM]["enabled"] is True




def test_le_menu_ne_defile_pas_avec_la_page():
    """Il partait avec elle.

    Sur une longue liste d'applications, changer de rubrique demandait de
    remonter d'abord -- le menu etait reste tout en haut.
    """
    page = _page_panneau("dashboard.html")
    # Le menu est une pilule FIXE en haut de l'ecran (une barre fixe en bas
    # sur un telephone) : il ne peut pas partir avec la page. La colonne
    # collante d'avant (position:sticky sous le bandeau) a disparu avec le
    # style « design », qui est toujours pose.
    regles = [r.split("}")[0] for r in page.split("\n.sidebar{")[1:]]
    assert any("position:fixed" in r for r in regles), regles
    telephone = page.split("@media (max-width:760px)")
    assert any('.sidebar{top:auto;bottom:0' in bloc for bloc in telephone), (
        "sur telephone, le menu doit rester colle au bas de l'ecran")




def test_le_moniteur_rallume_une_application_par_defaut_eteinte(hub_dagster, monkeypatch):
    """Le rattrapage ne vaut pas qu'au demarrage du panneau.

    Il n'avait lieu que dans resume(). Une application par defaut eteinte
    entre deux redemarrages -- par l'echec de lancement de start(), le seul
    chemin qui y mene -- le restait jusqu'au suivant. Sur une machine qui ne
    redemarre pas, c'est-a-dire pour de bon.
    """
    monkeypatch.setattr(app, "start", lambda n, attendre=True: demarrees.append(n))
    demarrees = []
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False}
    app.save(apps)

    app.monitor_tick()

    assert app.load()[app.DIAGNOSTIC_NOM]["enabled"] is True
    # Et le tour suivant la relance, puisqu'elle est desormais "enabled".
    assert app.DIAGNOSTIC_NOM in demarrees, demarrees




def test_le_moniteur_ne_rallume_pas_une_application_ordinaire_arretee(hub_dagster, monkeypatch):
    """Un arret volontaire est un choix, et il se respecte.

    Le rattrapage ne vaut que pour les deux qui n'ont pas le choix -- celles
    dont le bouton « Arreter » est refuse par le serveur.
    """
    monkeypatch.setattr(app, "start", lambda n, attendre=True: None)
    app.monitor_tick()
    assert not app.load()["site"].get("enabled")




# ---------- 30 bis. le nom se lit, le chemin se tape ----------
#
# Demande : « Le nom de l'application peut etre en majuscule, remplace par des
# minuscules dans le path, et les espaces sont autorises dans le nom,
# remplaces par des tirets dans le path. La validite du nom de l'app doit
# dependre du path resultant. »
#
# Avant, les deux etaient la MEME chaine, et la regle etait un refus sec :
# une majuscule rendait le nom invalide, sans rien dire de plus. On devinait
# la contrainte au lieu de la lire.

def test_le_chemin_se_tire_du_nom():
    """La transformation, cas par cas."""
    for nom, chemin in [
        ("Mon Site", "mon-site"),
        ("SITE", "site"),
        ("Mon  --  Site", "mon-site"),      # ni doubles tirets ni doubles espaces
        ("  Site  ", "site"),               # les bords se taillent
        ("Café", "cafe"),                   # l'accent se retire, la lettre reste
        ("API Météo", "api-meteo"),         # et non plus « api-m-t-o »
        ("Œuvre", "oeuvre"),                # les ligatures se deplient
        ("site_2", "site_2"),               # le tiret bas reste
        ("---", ""),                        # rien d'utilisable : pas de chemin
        ("", ""),
        (None, ""),
    ]:
        assert app.valid_name(nom) == chemin, nom




def test_le_nom_garde_sa_casse_et_ses_espaces():
    assert app.titre_propre("  Mon   Site  ") == "Mon Site"
    assert app.titre_propre("SITE") == "SITE"
    assert app.titre_propre(None) == ""




def test_une_application_se_declare_sous_son_nom_et_se_sert_sous_son_chemin(hub_dagster, monkeypatch):
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/add", json={"name": "Mon Site", "path": "/w/x",
                                 "command": "python3 app.py"})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["name"] == "mon-site" and d["titre"] == "Mon Site"

    # La cle du registre EST le chemin : c'est elle que porte l'adresse.
    assert "mon-site" in app.load()
    ligne = [a for a in c.get("/api/apps").get_json()["apps"]
             if a["name"] == "mon-site"][0]
    assert ligne["titre"] == "Mon Site"




def test_un_nom_qui_ne_donne_aucun_chemin_est_refuse(hub_dagster, monkeypatch):
    """La validite depend du chemin OBTENU, et le refus le dit.

    Un nom fait de tirets seuls, ou de caracteres accentues seuls, se lit
    tres bien et ne laisse pourtant rien dont faire une adresse. Refuser sans
    montrer ce que le nom donnait dans l'adresse n'apprend rien.
    """
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/add", json={"name": "---", "path": "/w/x",
                                 "command": "python3 app.py"})
    assert r.status_code == 400
    assert "adresse" in r.get_json()["error"]




def test_deux_noms_qui_donnent_le_meme_chemin_se_refusent(hub_dagster, monkeypatch):
    """« Mon Site » et « mon site » se lisent differemment et se servent au
    meme endroit. Le second doit tomber, sur le chemin et non sur le nom."""
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.post("/api/add", json={"name": "Mon Site", "path": "/w/x",
                                    "command": "x"}).status_code == 200
    r = c.post("/api/add", json={"name": "mon site", "path": "/w/x",
                                "command": "x"})
    assert r.status_code == 400
    assert "/mon-site/" in r.get_json()["error"], r.get_json()




def test_changer_la_seule_casse_ne_deplace_rien(hub_dagster):
    """« site » renomme en « Site » garde le chemin /site/.

    Sans ce cas, le renommage echouait sur « une adresse deja prise » : la
    sienne. Et rien ne doit s'arreter -- il n'y a rien a deplacer.
    """
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/app/site/renommer", json={"nom": "Site"})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["nom"] == "site" and d["titre"] == "Site"
    assert d["redemarree"] is False
    assert app.load()["site"]["titre"] == "Site"




def test_la_page_affiche_le_nom_et_designe_par_la_cle():
    """Deux chaines, deux usages, et il ne faut pas les echanger.

    Afficher la cle, c'est montrer « mon-site » a qui a tape « Mon Site ».
    Designer par le nom, c'est fabriquer une adresse qui n'existe pas.
    """
    page = _page_panneau("dashboard.html")
    assert "const nomAffiche" in page
    # La tuile du hub et la ligne du tableau LISENT le nom.
    assert '<span class="hub-icone-nom">${esc(nomAffiche(a))}</span>' in page
    assert '<div class="bento-carte-nom${classeNomLong(nomAffiche(a))}">${esc(nomAffiche(a))}</div>' in page
    assert '<div class="n">${esc(nomAffiche(a))}</div>' in page
    # Et tout ce qui DESIGNE garde la cle.
    assert 'data-cible="${esc(a.name)}"' in page




# ---------- 29. arreter une application ----------
#
# Signale par Lucas : « je ne peux pas arreter les applications », avec le
# message « L'application n'a pas demarre. (reponse 500) ».
#
# Deux defauts superposes, et le second cachait le premier. api_toggle ne
# disait jamais QUELLE action il avait tentee, et la page retombait donc sur
# un message de repli ecrit pour le demarrage : un arret rate s'annoncait
# « n'a pas demarre », ce qui envoie chercher a l'oppose de la panne. Et
# stop(), qui n'attrapait que ProcessLookupError, laissait filer toute autre
# erreur jusqu'a une page HTTP 500 sans JSON -- le motif reel ne sortait que
# dans les journaux du panneau, que l'interface ne montre pas.

def test_un_arret_qui_echoue_ne_parle_pas_de_demarrage(en_marche, monkeypatch):
    def _refuse(_nom):
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(app, "stop", _refuse)
    r = en_marche.post("/api/toggle/site")

    assert r.status_code == 500
    d = r.get_json()
    assert d is not None, "une page HTML 500 : la page n'y lira aucun motif"
    assert d["action"] == "arret"
    assert "arrêter" in d["error"], d["error"]
    assert "démarr" not in d["error"], "le message envoie chercher a l'oppose"
    # Et le motif reel est dans la reponse, pas seulement dans les journaux.
    assert "PermissionError" in d["error"]




def test_un_arret_refuse_rend_409_et_son_motif(en_marche, monkeypatch):
    monkeypatch.setattr(app, "stop", lambda _n: "Le processus ne s'arrete pas.")
    r = en_marche.post("/api/toggle/site")
    assert r.status_code == 409
    assert r.get_json()["action"] == "arret"
    assert "ne s'arrete pas" in r.get_json()["error"]




def test_un_arret_qui_marche_le_dit_aussi(en_marche, monkeypatch):
    vus = []
    monkeypatch.setattr(app, "stop", lambda n: vus.append(n))
    r = en_marche.post("/api/toggle/site")
    assert r.status_code == 200 and vus == ["site"]
    assert r.get_json() == {"ok": True, "action": "arret", "running": False}




def test_un_processus_qui_survit_n_est_pas_declare_arrete(survivants, monkeypatch):
    """Le cas le plus couteux : stop() repondait « tout va bien » sur un
    processus toujours vivant, le panneau affichait « Arretee », et
    l'application continuait de tenir son port."""
    class _Increvable:
        pid = 424242

        def poll(self):
            return None

    app.save({"site": {"path": str(survivants / "projet"), "command": "sleep 120",
                       "port": 9399, "enabled": True}})
    app.procs["site"] = _Increvable()
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", lambda *_a: None)     # signaux sans effet
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    erreur = app.stop("site")

    assert erreur and "424242" in erreur
    assert app.load()["site"]["enabled"] is True, (
        "le panneau a marque arretee une application toujours vivante")
    assert "site" in app.procs, "la seule trace qui permettait de la retrouver"
    app.procs.clear()




def test_un_signal_refuse_est_dit_et_non_leve(survivants, monkeypatch):
    class _Vivant:
        pid = 4242

        def poll(self):
            return None

    app.procs["site"] = _Vivant()

    def _refuse(*_a):
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", _refuse)
    erreur = app.stop("site")
    assert erreur and "Operation not permitted" in erreur
    app.procs.clear()




def test_un_arret_qui_marche_rend_none(survivants):
    """Temoin du contrat : None veut bien dire « c'est fait »."""
    assert app.start("site") is None
    assert app.stop("site") is None
    assert app.load()["site"]["enabled"] is False
    assert "site" not in app.lire_processus()




def test_le_bouton_ne_se_trompe_plus_de_sens():
    """La page doit dire le bon sens meme sur une reponse sans JSON.

    Le repli d'echec() est ecrit pour le demarrage : un arret rate
    s'annoncait « L'application n'a pas demarre (reponse 500) », ce qui
    envoie chercher exactement a l'oppose de la panne. La page connait
    l'etat qu'elle vient d'afficher -- c'est lui qui choisit la phrase quand
    le serveur ne dit rien du tout.
    """
    _, script = _script_panneau_html()
    bloc = script[script.index("async function tg(n){"):]
    bloc = bloc[:bloc.index("async function", 10)]
    assert "running===true" in bloc, (
        "la page ne regarde pas l'etat qu'elle vient d'afficher")
    assert "arrêtée" in bloc, "aucun message pour un arret qui echoue"
    assert "echec(r," in bloc, (
        "le message du serveur ne gagne plus sur le repli de la page")


# ---------- 30. renommer une application ----------
#
# Demande : « Il doit etre possible de changer de nom d'application en
# admin. » Il fallait jusqu'ici supprimer l'application et la redeclarer --
# donc perdre son journal, ses destinataires d'alerte et les comptes qu'on lui
# avait autorises.
#
# Ce qui rend l'operation delicate : le nom n'est pas une etiquette, c'est une
# CLE. Il designe l'application dans apps.json, dans son adresse, dans le nom
# de son journal, dans les projets autorises de chaque compte, dans ce que
# chacun a masque de son hub -- et il decide de l'uid sous lequel elle tourne.




def test_renommer_emporte_tout_ce_qui_designe_l_application(a_renommer):
    """La cle change, et avec elle tout ce qui la nommait."""
    c, tmp_path = a_renommer
    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["nom"] == "vitrine"

    apps = app.load()
    assert "vitrine" in apps and "site" not in apps
    # Ce qui accompagnait l'application la suit : sa description, ses
    # destinataires d'alerte, son port.
    assert apps["vitrine"]["description"] == "avant"
    assert apps["vitrine"]["alertes"] == ["moi@example.com"]
    assert apps["vitrine"]["port"] == 9401
    # Et sa place dans la liste : une application qui saute en fin de liste
    # parce qu'on l'a renommee se cherche.
    assert list(apps) == ["vitrine", "autre"]

    # Les droits d'un compte suivent, sinon marie perd l'acces sans que
    # personne ne le lui ait retire.
    assert app.lire_utilisateurs()["marie"]["projets"] == ["vitrine", "autre"]
    # Ce qu'elle avait masque de son hub aussi : sinon l'application
    # reapparait chez elle au premier rechargement.
    assert app.lire_masquees()["marie"] == ["vitrine"]
    # Le journal suit : c'est ce qu'on relit pour comprendre ce qui vient de
    # se passer.
    assert (tmp_path / "logs" / "vitrine.log").exists()
    assert not (tmp_path / "logs" / "site.log").exists()




def test_renommer_refuse_un_nom_deja_pris(a_renommer):
    c, _ = a_renommer
    r = c.post("/api/app/site/renommer", json={"nom": "autre"})
    assert r.status_code == 400
    assert "déjà" in r.get_json()["error"]
    assert "site" in app.load(), "l'application a disparu sur un refus"




def test_renommer_refuse_un_nom_reserve(a_renommer):
    c, _ = a_renommer
    for reserve in ("api", "health", "login"):
        r = c.post("/api/app/site/renommer", json={"nom": reserve})
        assert r.status_code == 400, reserve




def test_le_diagnostic_ne_se_renomme_pas(a_renommer):
    """Meme raison que pour la suppression : le panneau le retrouve par son
    nom, et le marqueur qui evite une seconde inscription porte dessus."""
    c, _ = a_renommer
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = dict(apps["site"])
    app.save(apps)
    r = c.post("/api/app/" + app.DIAGNOSTIC_NOM + "/renommer", json={"nom": "controle"})
    assert r.status_code == 403
    assert app.DIAGNOSTIC_NOM in app.load()




def test_renommer_une_application_qui_tourne_l_arrete_et_la_relance(a_renommer, monkeypatch):
    """Le processus porte l'ancien nom dans son environnement (CODELAB_APP) :
    c'est ce qui permet de le reconnaitre apres un redemarrage du panneau. Le
    laisser tourner sous l'ancien nom reviendrait a le perdre de vue."""
    c, _ = a_renommer
    faits = []
    monkeypatch.setattr(app, "is_running", lambda n: n == "site")
    monkeypatch.setattr(app, "stop", lambda n: faits.append(("stop", n)))
    monkeypatch.setattr(app, "start", lambda n: faits.append(("start", n)))
    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 200
    assert faits == [("stop", "site"), ("start", "vitrine")]
    assert r.get_json()["redemarree"] is True




def test_un_arret_impossible_annule_le_renommage(a_renommer, monkeypatch):
    """On ne renomme pas une application qu'on n'a pas su arreter : la cle
    changerait pendant qu'un processus tourne toujours sous l'ancienne."""
    c, _ = a_renommer
    monkeypatch.setattr(app, "is_running", lambda n: n == "site")
    monkeypatch.setattr(app, "stop", lambda n: "le processus ne s'arrête pas")
    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 409
    assert "site" in app.load() and "vitrine" not in app.load()




def test_le_dossier_est_rendu_au_nouvel_identifiant(a_renommer, monkeypatch):
    """L'uid d'une application est DERIVE de son nom : la renommer lui en
    donne un autre, et tout ce qu'elle avait ecrit deviendrait illisible pour
    elle. Un projet qui ne peut plus relire sa propre base apres un simple
    changement de nom serait un piege."""
    c, tmp_path = a_renommer
    donnees = tmp_path / "projet" / "donnees.sqlite"
    donnees.write_text("x")
    ancien, nouveau = app.uid_application("site"), app.uid_application("vitrine")
    assert ancien != nouveau, "les deux noms tombent sur le meme uid, test sans objet"

    vus = []
    monkeypatch.setattr(os, "lstat", lambda chemin: type(
        "S", (), {"st_uid": ancien, "st_gid": 2000})())
    monkeypatch.setattr(os, "lchown", lambda chemin, uid, gid: vus.append((chemin, uid)))
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 200
    assert r.get_json()["fichiers_repris"] >= 1
    assert all(uid == nouveau for _chemin, uid in vus), vus
    assert any(chemin.endswith("donnees.sqlite") for chemin, _uid in vus)




def test_la_page_offre_le_renommage(a_renommer):
    """Le champ existe vraiment, et il est fige pour le diagnostic."""
    page = _page_panneau("dashboard.html")
    assert 'id="e-nom"' in page and 'data-agir="renommerApp"' in page
    bloc = page.split("async function renommerApp(){")[1].split("\n}")[0]
    assert "/renommer" in bloc
    # Apres coup, la fiche doit suivre le nouveau nom : sinon elle interroge
    # une application qui n'existe plus.
    assert "ficheNom = d.nom" in bloc
    assert "await refresh()" in bloc


# ---------- 24. regressions app-manager : ports / metriques / logo --------
#
# Ces tests sont volontairement des tests de source et de comportement isole :
# ils ne demarrent ni Docker ni une application utilisateur. Ils garantissent
# que les trois regressions vues en production restent couvertes.




def test_un_port_externe_est_detecte_des_l_attribution(monkeypatch, tmp_path):
    """Un port occupe par un autre service ne doit jamais etre propose."""
    m = _charger_app_manager(monkeypatch, tmp_path)

    class Conn:
        status = m.psutil.CONN_LISTEN
        pid = 4242
        laddr = type("Addr", (), {"port": 9102})()

    monkeypatch.setattr(m.psutil, "net_connections", lambda kind=None: [Conn()])
    assert m.next_port({}) == 9101
    assert m.next_port({"demo": {"port": 9101}}) == 9103




def test_un_processus_codelab_survivant_est_repris(monkeypatch, tmp_path):
    """Après redémarrage du panneau, un listener connu est adopté."""
    m = _charger_app_manager(monkeypatch, tmp_path)

    class Conn:
        status = m.psutil.CONN_LISTEN
        pid = 4242
        laddr = type("Addr", (), {"port": 9102})()

    monkeypatch.setattr(m.psutil, "net_connections", lambda kind=None: [Conn()])
    monkeypatch.setattr(m, "load", lambda: {
        "demo": {"port": 9102, "enabled": True}
    })
    monkeypatch.setattr(m, "_processus_est_le_notre", lambda pid, sauf=None: pid == 4242)
    monkeypatch.setattr(m, "_noter_processus", lambda *args: None)
    monkeypatch.setattr(m, "save", lambda apps: None)
    monkeypatch.setattr(m, "ProcessusAdopte", lambda pid, name: ("adopte", pid, name))
    monkeypatch.setattr(m, "is_running", lambda name: True)

    m.procs.clear()
    assert m.port_occupe_par(9102, sauf="demo") is None
    assert m.procs["demo"] == ("adopte", 4242, "demo")




def test_un_port_externe_explique_le_pid(monkeypatch, tmp_path):
    """Le message de panne donne enfin le processus qui bloque le port."""
    m = _charger_app_manager(monkeypatch, tmp_path)

    class Conn:
        status = m.psutil.CONN_LISTEN
        pid = 9876
        laddr = type("Addr", (), {"port": 9102})()

    monkeypatch.setattr(m.psutil, "net_connections", lambda kind=None: [Conn()])
    monkeypatch.setattr(m, "_description_processus",
                        lambda pid: ("node", "node server.js"))
    monkeypatch.setattr(m, "load", lambda: {})

    erreur = m.port_occupe_par(9102)
    assert "9102" in erreur
    assert "9876" in erreur
    assert "node server.js" in erreur




def test_ouverture_des_stats_force_une_premiere_mesure(monkeypatch, tmp_path):
    """La fiche metriques ne doit pas dependre du rafraichissement precedent.

    Ce test s'ignorait en silence tant que la recherche des sources visait
    app-manager/app.py. Il passe par un contexte de requete explicite plutot
    que par une session de test : la route est appelee directement, elle n'a
    pas besoin de cookie, et le panneau pose sa propre interface de session.
    """
    m = _charger_app_manager(monkeypatch, tmp_path)
    mesures = []

    class Proc:
        pid = 4242

    # La route est protegee par require_admin, applique a l'import : on ouvre
    # la porte par les trois predicats qu'il interroge, pas en remplacant le
    # decorateur -- trop tard, il a deja enveloppe la vue.
    monkeypatch.setattr(m, "is_authed", lambda: True)
    monkeypatch.setattr(m, "est_admin", lambda: True)
    monkeypatch.setattr(m, "refus_admin_hors_reseau", lambda: None)
    monkeypatch.setattr(m, "load", lambda: {"demo": {}})
    monkeypatch.setattr(m, "procs", {"demo": Proc()})
    monkeypatch.setattr(m, "is_running", lambda name: True)
    monkeypatch.setattr(m, "proc_stats",
                        lambda pid: {"cpu_percent": 12.5, "memory_mb": 64})
    monkeypatch.setattr(m, "record_metrics",
                        lambda name, cpu, mem: mesures.append((name, cpu, mem)))
    monkeypatch.setattr(m, "get_metrics_history", lambda name: mesures)

    with m.flask_app.test_request_context("/api/metrics/demo"):
        reponse = m.api_metrics("demo")
    # L'ouverture a elle seule doit avoir produit une mesure : sans elle, les
    # deux cartes affichaient « Aucune mesure » jusqu'au prochain sondage.
    assert mesures == [("demo", 12.5, 64)]
    assert reponse.json["points"][-1] == {"t": "demo", "cpu": 12.5, "mem": 64}




def test_le_logo_dagster_est_vectoriel_et_net():
    bloc = app.ICONE_DAGSTER
    assert '<svg' in bloc
    assert 'aria-label="Dagster"' in bloc
    # Les couleurs OFFICIELLES, celles du favicon de dagster-webserver.
    assert "#4F43DD" in bloc and "#352D8E" in bloc
    assert "#4C9AFF" not in bloc
    # Toujours pose sur la plaque blanche : pas de variante sombre.
    assert "prefers-color-scheme" not in bloc
    assert '<path' in bloc




# ---------- 40. le nom du projet ne se devine pas ------------------------
#
# Sans "name:" en tete, Compose nomme le projet d'apres le DOSSIER qui
# contient le fichier. Trois chemins donnent trois noms : un clone donne
# « codelab », l'archive ZIP de GitHub donne « codelab-main », et une
# interface qui recopie le fichier dans un dossier a elle -- CasaOS le fait
# -- donne « compose-d4601441b33edb0f ».
#
# Ce nom prefixe les ressources de Compose et, sur CasaOS, donne son nom au
# DOSSIER de l'application. Le fixer, c'est le rendre identique partout.

def test_les_deux_composes_nomment_le_projet():
    """Le nom est ecrit, pas derive du chemin."""
    for chemin in _composes():
        texte = open(chemin, encoding="utf-8").read()
        noms = re.findall(r"^name:\s*(\S+)\s*$", texte, re.M)
        assert noms, (
            f"{os.path.basename(chemin)} ne declare pas « name: » : le nom du "
            f"projet dependra du dossier d'ou l'on demarre")
        assert noms[0] == "codelab", (
            f"{os.path.basename(chemin)} nomme le projet « {noms[0]} »")




def test_les_deux_composes_nomment_le_meme_projet():
    """Deux noms differents feraient deux installations qui s'ignorent.

    Les deux fichiers decrivent la MEME stack : demarrer avec l'un puis avec
    l'autre doit reprendre les memes conteneurs, pas en creer une seconde
    serie a cote.
    """
    noms = set()
    for chemin in _composes():
        texte = open(chemin, encoding="utf-8").read()
        trouve = re.findall(r"^name:\s*(\S+)\s*$", texte, re.M)
        if trouve:
            noms.add(trouve[0])
    assert len(noms) == 1, f"les composes nomment des projets differents : {sorted(noms)}"




# ---------- robustesse : double clic, plafonds, surveillance, erreurs ----------

def test_une_seule_action_a_la_fois_par_application(hub, monkeypatch):
    """Un double clic sur « Demarrer » demarrait puis arretait : la seconde
    requete arrivait quand l'application tournait deja. Pendant qu'une action
    tourne, les autres sont refusees en 409."""
    import threading
    c = _admin_du_hub(hub)
    entree, sortie = threading.Event(), threading.Event()

    def build_lent(n):
        entree.set()
        sortie.wait(5)
        return True, "ok"
    monkeypatch.setattr(app, "run_build", build_lent)
    resultat = {}
    # Le premier build tourne dans un fil, avec sa propre session admin.
    c2 = app.flask_app.test_client()
    c2.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    fil = threading.Thread(target=lambda: resultat.setdefault(
        "premier", c2.post("/api/build/compta").status_code))
    fil.start()
    assert entree.wait(5)
    r = c.post("/api/build/compta")
    assert r.status_code == 409 and r.get_json()["en_cours"] is True
    assert c.post("/api/toggle/compta").status_code == 409
    # Une AUTRE application n'est pas bloquee.
    monkeypatch.setattr(app, "stop", lambda n: None)
    assert c.post("/api/toggle/vitrine").status_code == 200
    sortie.set()
    fil.join(5)
    assert resultat["premier"] == 200
    # Le verrou est rendu.
    monkeypatch.setattr(app, "run_build", lambda n: (True, "ok"))
    assert c.post("/api/build/compta").status_code == 200




def test_un_second_clic_sur_demarrer_n_arrete_pas(hub, monkeypatch):
    """La page dit l'action VOULUE : si l'application tourne deja, un
    « demarrage » ne fait rien -- surtout pas un arret."""
    c = _admin_du_hub(hub)
    arrets = []
    monkeypatch.setattr(app, "stop", lambda n: arrets.append(n))
    r = c.post("/api/toggle/compta", json={"action": "demarrage"})
    assert r.status_code == 200 and r.get_json()["deja"] is True
    assert arrets == []
    page = _page_panneau("dashboard.html")
    assert "actionsEnCours" in page and "body:JSON.stringify({action:" in page




def test_la_surveillance_externe_n_accepte_que_https_et_verifie_l_adresse(client, monkeypatch, tmp_path):
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = client.post("/api/surveillance-externe", json={"url": "http://hc-ping.com/x"})
    assert r.status_code == 400
    appels = []
    monkeypatch.setattr(app, "signaler_vie", lambda url=None: (appels.append(url) or (False, "HTTP 404")))
    r = client.post("/api/surveillance-externe", json={"url": "https://hc-ping.com/abc"})
    assert r.status_code == 400 and appels == ["https://hc-ping.com/abc"]
    assert app.lire_surveillance() == "", "une adresse qui ne repond pas ne s'enregistre pas"
    monkeypatch.setattr(app, "signaler_vie", lambda url=None: (True, "HTTP 200"))
    assert client.post("/api/surveillance-externe",
                       json={"url": "https://hc-ping.com/abc"}).status_code == 200
    assert app.lire_surveillance() == "https://hc-ping.com/abc"
    # Au coffre : chiffree, en 0600, jamais en clair.
    assert stat.S_IMODE(os.stat(app.DB_FILE).st_mode) == 0o600
    assert b"hc-ping.com" not in _octets_de_l_etat()
    assert client.post("/api/surveillance-externe", json={"url": ""}).status_code == 200
    assert app.lire_surveillance() == ""




def test_le_panneau_compresse_sans_exposer_le_jeton(client):
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in r.headers.get("Vary", "")
    import gzip as gz
    assert b"<html" in gz.decompress(r.data).lower()
    # BREACH : une requete qui porte des parametres n'est jamais compressee.
    r = client.get("/?x=1", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in r.headers
    # Sans Accept-Encoding, rien ne change.
    assert "Content-Encoding" not in client.get("/").headers




def test_le_script_du_panneau_part_compresse_meme_avec_sa_version(client):
    """Vecu (test de charge) : le navigateur charge /panneau.js?v=<empreinte>,
    et compresser() saute toute requete a parametres (parade a BREACH) : le
    script partait en clair, 269 Ko au lieu de 87. Sans ?v=, il etait
    recompresse a chaque requete (12 ms). Il est compresse une fois, au
    demarrage : il ne porte aucun secret et ne recopie rien de la requete."""
    import gzip as gz
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    for adresse in ("/panneau.js?v=" + app.PANNEAU_JS_VERSION, "/panneau.js"):
        r = client.get(adresse, headers={"Accept-Encoding": "gzip, deflate, br"})
        assert r.status_code == 200, adresse
        assert r.headers.get("Content-Encoding") == "gzip", adresse
        assert "Accept-Encoding" in r.headers.get("Vary", "")
        assert gz.decompress(r.data).decode("utf-8") == app.PANNEAU_JS
        assert r.data == app.PANNEAU_JS_GZIP, "les octets compresses au demarrage, pas recompresses"
    assert len(app.PANNEAU_JS_GZIP) < len(app.PANNEAU_JS.encode()) / 2
    # Sans gzip annonce : le script en clair.
    r = client.get("/panneau.js?v=" + app.PANNEAU_JS_VERSION)
    assert "Content-Encoding" not in r.headers and r.get_data(as_text=True) == app.PANNEAU_JS
    # La parade a BREACH tient toujours pour ce qui porte un secret : la page
    # (et son jeton) n'est jamais compressee quand la requete a des
    # parametres.
    r = client.get("/?x=1", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in r.headers
    # Et le script ne porte toujours aucune valeur de session : le jeton de
    # la session n'y est pas.
    with client.session_transaction() as sess:
        jeton = sess.get("jeton")
    assert jeton and jeton not in app.PANNEAU_JS and "__JETON__" not in app.PANNEAU_JS




def test_une_liste_ne_releve_les_ports_qu_une_fois(client, monkeypatch):
    """Vecu (test de charge) : psutil.net_connections() passe en revue tout le
    conteneur. /api/mes-apps le faisait trois fois par appel, /api/apps une
    fois par application, next_port une fois par port essaye : 96 %
    du temps du hub, qui tombait de 46 a 11 requetes par seconde des dix
    clients. Une liste fait un releve, une tuile de service aucun."""
    appels = []
    vrai = app.psutil.net_connections
    def compte(*a, **k):
        appels.append(1)
        return vrai(*a, **k)
    monkeypatch.setattr(app.psutil, "net_connections", compte)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    app.save({f"p{i}": {"path": f"/w/p{i}", "command": "x", "port": 9101 + i, "enabled": False}
              for i in range(6)})
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    appels.clear()
    assert client.get("/api/mes-apps").status_code == 200
    assert len(appels) == 0, "les tuiles de service ne relevent rien"
    assert client.get("/api/apps").status_code == 200
    assert len(appels) == 1, f"un releve pour toute la liste, pas {len(appels)}"
    appels.clear()
    assert app.next_port(app.load()) is not None
    assert len(appels) == 1, "un releve neuf pour toute la plage de ports"




def test_le_panneau_accepte_plus_de_cent_connexions():
    """Vecu (test de charge) : waitress refuse au-dela de 100 connexions
    ouvertes, et une connexion inactive reste ouverte jusqu'a 600 s. Des 100
    a 200 clients, des requetes de 12 s sur tous les ecrans, healthcheck
    compris. Mille par defaut, reglable."""
    src = _lire_source(os.path.join(DOSSIER_PANNEAU, "app", "app.py"))
    assert 'WSGI_CONNEXIONS = int(os.environ.get("APP_MANAGER_CONNEXIONS", "1000"))' in src
    serveur = src.split("def servir(port):")[1].split("\ndef ")[0]
    assert "connection_limit=WSGI_CONNEXIONS" in serveur
    assert app.WSGI_CONNEXIONS >= 1000




def test_les_erreurs_du_navigateur_arrivent_dans_les_journaux(client, capsys):
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = client.post("/api/erreur-navigateur", json={
        "message": "x is not defined\n[faux] ligne", "source": "/", "ligne": 12,
        "page": "/#hub"})
    assert r.status_code == 200
    sortie = capsys.readouterr().out
    assert "[navigateur]" in sortie and "x is not defined [faux] ligne" in sortie
    assert "\n[faux]" not in sortie, "un saut de ligne fabriquerait une fausse ligne"
    for _ in range(40):
        r = client.post("/api/erreur-navigateur", json={"message": "boucle"})
    assert r.status_code == 429
    page = _page_panneau("dashboard.html")
    assert "addEventListener('unhandledrejection'" in page




def test_la_sauvegarde_est_restauree_a_l_essai():
    source = _entrypoint_postgres()
    assert 'verifier_restaurations "$bases"' in source
    bloc = source.split("verifier_restaurations() {")[1].split("\n}\n")[0]
    assert "pg_restore -U" in bloc and "tables_de_la_sauvegarde" in bloc
    assert "ECHEC" in bloc and "$VERIFICATION" in bloc
    # La base jetable n'est jamais elle-meme sauvegardee.
    assert "AND datname <> '$BASE_ESSAI'" in source




def test_le_diagnostic_rougit_si_la_restauration_d_essai_rate(tmp_path):
    for nom in ("base-diagnostic-20990101-000000.dump", "panneau-20990101-000000.tar.gz"):
        (tmp_path / nom).write_text("x")
    (tmp_path / "verification-restauration.txt").write_text(
        "2099-01-01T00:00:00Z ECHEC restauration d'essai ratee pour : diagnostic\n")
    etat, _, detail = checks.check_sauvegardes(str(tmp_path))
    assert etat == checks.Etat.ECHEC and "restauration d'essai" in detail
    (tmp_path / "verification-restauration.txt").write_text(
        "2099-01-01T00:00:00Z OK 2 base(s) restauree(s) sans erreur\n")
    etat, _, detail = checks.check_sauvegardes(str(tmp_path))
    assert etat == checks.Etat.OK and "sans erreur" in detail




def test_le_code_du_workspace_est_archive_sans_ce_qui_se_reconstruit(tmp_path, monkeypatch):
    racine, cible = tmp_path / "ws", tmp_path / "sv"
    (racine / "projet" / "node_modules" / "x").mkdir(parents=True)
    (racine / "projet" / "node_modules" / "x" / "index.js").write_text("lourd")
    (racine / "projet" / "app.py").write_text("print(1)")
    (racine / "projet" / ".env").write_text("CLE=1")
    (racine / "projet" / "gros.bin").write_bytes(b"0" * 100)
    cible.mkdir()
    monkeypatch.setattr(app, "ROOT", str(racine))
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(cible))
    monkeypatch.setattr(app, "WORKSPACE_FICHIER_MAX", 50)
    assert app.workspace_a_sauvegarder()
    chemin = app.sauvegarder_le_workspace()
    import tarfile
    with tarfile.open(chemin) as archive:
        noms = {n.lstrip("./") for n in archive.getnames()}
    assert "projet/app.py" in noms and "projet/.env" in noms
    assert not [n for n in noms if "node_modules" in n]
    assert "projet/gros.bin" not in noms
    assert stat.S_IMODE(os.stat(chemin).st_mode) == 0o600
    assert not app.workspace_a_sauvegarder(), "une par semaine, pas a chaque passage"


# ---------- nouvelle version depuis la derniere ouverture ----------




def test_une_nouvelle_version_se_signale_a_qui_a_deja_ouvert(tmp_path, monkeypatch, deux_espaces):
    c = deux_espaces
    projet = tmp_path / "projet"
    _depot(projet, "a" * 40)
    apps = app.load()
    apps["prive-autorise"]["path"] = str(projet)
    app.save(apps)
    assert app.commit_du_projet(str(projet)) == "a" * 40
    # Une application qui fonctionne : seule elle compte comme ouverte.
    monkeypatch.setattr(app, "is_running", lambda n: True)
    monkeypatch.setattr(app, "_listening", {})
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")

    def etat():
        (a,) = [x for x in u.get("/api/mes-apps").get_json()["apps"] if x["name"] == "prive-autorise"]
        return a["nouvelle_version"], a["ouverte_le"]

    # Jamais ouverte : pas d'indicateur.
    assert etat() == (False, None)
    assert u.post("/api/mes-apps/prive-autorise/ouverte").status_code == 200
    nouvelle, ouverte = etat()
    assert nouvelle is False and ouverte
    # Un nouveau commit, une seconde plus tard : nouvelle version.
    monkeypatch.setattr(app.time, "time", lambda: ouverte + 5)
    _depot(projet, "b" * 40)
    assert etat()[0] is True
    # La rouvrir l'efface.
    assert u.post("/api/mes-apps/prive-autorise/ouverte").status_code == 200
    assert etat()[0] is False
    # Un deploiement ou un build reussi compte aussi.
    monkeypatch.setattr(app.time, "time", lambda: ouverte + 10)
    app.marquer_nouvelle_version("prive-autorise")
    assert etat()[0] is True
    # On ne note pas l'ouverture d'une application qu'on ne peut pas voir.
    assert u.post("/api/mes-apps/prive-refuse/ouverte").status_code == 404
    assert u.post("/api/mes-apps/inconnue/ouverte").status_code == 404


def test_une_application_en_panne_ne_compte_pas_comme_derniere_ouverte(monkeypatch, deux_espaces):
    """Vecu : « une application qui ne fonctionne pas ne doit pas compter
    comme une derniere application ouverte dans le widget ». Arretee, ou
    lancee mais muette sur son port : rien n'est note, et la reponse est la
    meme que pour une application qui marche (l'etat ne regarde pas un
    compte utilisateur)."""
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")

    def ouverte_le():
        (a,) = [x for x in u.get("/api/mes-apps").get_json()["apps"] if x["name"] == "prive-autorise"]
        return a["ouverte_le"]
    monkeypatch.setattr(app, "is_running", lambda n: False)
    r = u.post("/api/mes-apps/prive-autorise/ouverte")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert ouverte_le() is None
    monkeypatch.setattr(app, "is_running", lambda n: True)
    monkeypatch.setattr(app, "_listening", {"prive-autorise": False})
    assert u.post("/api/mes-apps/prive-autorise/ouverte").get_json() == {"ok": True}
    assert ouverte_le() is None
    # Elle repond : la voila derniere ouverte.
    monkeypatch.setattr(app, "_listening", {"prive-autorise": True})
    u.post("/api/mes-apps/prive-autorise/ouverte")
    assert ouverte_le()
    page = _page_panneau("dashboard.html")
    assert "codelab-hub-derniere" not in page




def test_le_commit_se_lit_sans_lancer_git(tmp_path):
    assert app.commit_du_projet(str(tmp_path)) is None
    assert app.commit_du_projet(None) is None
    _depot(tmp_path, "c" * 40)
    (tmp_path / ".git" / "refs" / "heads" / "main").unlink()
    (tmp_path / ".git" / "packed-refs").write_text("# pack\n" + "d" * 40 + " refs/heads/main\n")
    assert app.commit_du_projet(str(tmp_path)) == "d" * 40
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/../../etc\n")
    assert app.commit_du_projet(str(tmp_path)) is None
    page = _page_panneau("dashboard.html")
    assert "Nouvelle version" in page and "/ouverte'" in page and "keepalive:true" in page


# ------------------------- audit des dependances des projets -------------------------




def test_l_audit_lit_les_versions_figees_de_chaque_projet(tmp_path):
    _projet_a_auditer(tmp_path)
    d = checks.dependances_du_workspace(str(tmp_path))
    assert sorted(d) == ["ancien", "boutique", "vitrine"]
    assert d["boutique"] == {("PyPI", "flask", "2.0.0"), ("PyPI", "requests", "2.19.0"),
                             ("PyPI", "jinja2", "3.1.2"), ("PyPI", "pandas", "1.0.0")}
    assert d["vitrine"] == {("npm", "lodash", "4.17.20"), ("npm", "@scope/b", "1.0.0")}
    assert d["ancien"] == {("npm", "minimist", "0.0.8"), ("npm", "sous", "2.0.0")}




def test_l_audit_rapporte_par_projet(tmp_path):
    _projet_a_auditer(tmp_path)
    vus = []

    def faux(paquets):
        vus.append(set(paquets))
        return {p: ["GHSA-x"] for p in paquets if p[1] in ("lodash", "flask")}
    nombre, rapport = checks.audit_dependances(str(tmp_path), interroger=faux)
    assert len(vus) == 1 and nombre == len(vus[0]) == 8   # une seule interrogation
    assert rapport == {"boutique": [("PyPI", "flask", "2.0.0", ["GHSA-x"])],
                       "vitrine": [("npm", "lodash", "4.17.20", ["GHSA-x"])]}
    # Workspace sans dependance figee : aucune interrogation.
    vide = tmp_path / "vide"
    vide.mkdir()
    assert checks.audit_dependances(str(vide), interroger=lambda p: 1 / 0) == (0, {})




def test_l_audit_interroge_osv_par_lots(monkeypatch):
    lots = []

    class Reponse:
        def __init__(self, corps):
            self.corps = corps

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return self.corps

    def urlopen(requete, timeout=None):
        assert requete.full_url == checks.OSV_URL and requete.get_method() == "POST"
        q = json.loads(requete.data)["queries"]
        lots.append(len(q))
        return Reponse(json.dumps({"results": [
            {"vulns": [{"id": "GHSA-2"}, {"id": "CVE-1"}]} if x["package"]["name"] == "p3" else {}
            for x in q]}).encode())
    monkeypatch.setattr(checks.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(checks, "OSV_LOT", 2)
    paquets = {("PyPI", f"p{i}", "1.0") for i in range(5)}
    assert checks.interroger_osv(paquets) == {("PyPI", "p3", "1.0"): ["CVE-1", "GHSA-2"]}
    assert lots == [2, 2, 1]




def test_l_audit_est_planifie_et_fait_echouer_le_run(asset_diagnostic, monkeypatch):
    """Vulnerabilite trouvee, et OSV injoignable : les deux font tomber le
    run, donc partir le mail du capteur. Rien a signaler : run vert."""
    d = asset_diagnostic
    assert d.audit_chaque_semaine["cron_schedule"] == "23 6 * * 1"
    assert d.audit_dependances in d.defs["assets"]
    assert d.audit_chaque_semaine in d.defs["schedules"]

    monkeypatch.setattr(checks, "audit_dependances", lambda: (
        3, {"boutique": [("PyPI", "flask", "2.0.0", ["GHSA-x"])]}))
    contexte = _ContexteDeTest()
    with pytest.raises(sys.modules["dagster"].Failure) as e:
        d.audit_dependances(contexte)
    assert "https://osv.dev/vulnerability/GHSA-x" in str(e.value)
    assert contexte.metadonnees["projets_touches"] == 1

    def hors_ligne():
        raise urllib.error.URLError("pas de reseau")
    monkeypatch.setattr(checks, "audit_dependances", hors_ligne)
    with pytest.raises(sys.modules["dagster"].Failure, match="Audit impossible"):
        d.audit_dependances(_ContexteDeTest())

    monkeypatch.setattr(checks, "audit_dependances", lambda: (3, {}))
    assert "aucune vulnerabilite" in d.audit_dependances(_ContexteDeTest())




# ------------------------ Le menu d'une application ------------------------
#
# Vecu : « je ne veux pas avoir le visuel de l'app dans le widget de derniere
# app ouverte. Je veux pouvoir maintenir appuye sur une app sur la version
# mobile, et avoir plusieurs options dont visionner la video. Sur pc, je veux
# 3 petits points dans le coin de l'app. »

def test_la_derniere_ouverte_n_affiche_plus_l_application():
    page = _page_panneau("dashboard.html")
    corps = page.split("function corpsWidget(")[1].split("\n}")[0]
    une = [l for l in corps.splitlines() if "e.id==='une'" in l][0]
    assert une.rstrip().endswith("false); }")
    # Le widget « Apercu » d'une application choisie, lui, reste en direct.
    assert "widgetApercu(hubApps.find(a=>a.name===e.app), e.taille, 'Aperçu')" in corps
    apercu = page.split("function widgetApercu(")[1].split("\n}")[0]
    # En direct quand l'application tourne -- et toujours pour un compte
    # utilisateur, qui ne connait pas l'etat (la page neutre s'y affiche).
    assert "enDirect && (a.running || !estAdmin)" in apercu




def test_chaque_tuile_porte_trois_points_qui_ouvrent_son_menu():
    page = _page_panneau("dashboard.html")
    assert page.count("boutonMenuApp(a)") >= 2      # cartes et icones
    bouton = page.split("function boutonMenuApp(")[1].split("\n}")[0]
    assert 'aria-haspopup="menu"' in bouton and 'data-agir="menu-app"' in bouton
    # Au doigt, pas de points : l'appui long les remplace.
    assert "@media (hover:none) and (pointer:coarse){ .btn-menu-app{display:none} }" in page
    menu = page.split("function ouvrirMenuApp(")[1].split("\n}")[0]
    for option in ('"Ouvrir"', "'Voir la présentation'", "'Copier le lien'", "'Masquer du hub'",
                   "\"Réglages de l'application\""):
        assert option in menu, option
    # Les reglages : administrateur, et pas pour un service de la stack.
    assert "estAdmin && !a.externe ? item('menu-reglages'" in menu
    # Ouvrir le menu n'ouvre pas l'application.
    clic = page.split("/* Les trois points sont DANS la tuile")[1].split("\n")[1]
    assert "e.target.closest('.btn-menu-app')" in clic




def test_un_appui_long_ouvre_le_menu_en_feuille():
    page = _page_panneau("dashboard.html")
    appui = page.split("let appuiLong=null, avalerClicJusqua=0;")[1].split("/* Le clic droit")[0]
    # Vecu : « je dois cliquer plusieurs fois en dehors pour sortir de la pop
    # up ». Le clic du relachement n'arrive pas toujours : un drapeau qui
    # l'attendait avalait le toucher suivant. Seul un clic DANS L'INSTANT
    # qui suit le relachement est avale.
    assert "if(appuiLong.fait) avalerClicJusqua=performance.now()+400;" in appui
    assert "if(performance.now() > avalerClicJusqua) return;" in appui
    assert "appuiLongFait" not in page
    # Le fond du menu a son propre ecouteur (Safari iOS ne remonte pas le
    # toucher d'un simple <div> jusqu'au document).
    assert "document.querySelector('#menu-app .menu-app-fond').addEventListener('click'" in appui
    assert 'class="menu-app-fond" data-agir' not in page
    assert "e.pointerType!=='touch' || hubEdition" in appui
    assert "ouvrirMenuApp(t.dataset.app, 'feuille')" in appui
    # Le doigt qui defile annule ; le relachement n'ouvre pas l'application.
    assert "> 10" in appui and "clearTimeout" in appui
    assert "e.stopImmediatePropagation()" in appui
    assert "#hub-liste [data-app]{-webkit-touch-callout:none;" in page
    assert "$('hub-liste').addEventListener('contextmenu'" in page


# ---------- l'archivage d'une application ----------

def test_une_application_archivee_garde_sa_place_avec_le_statut_archivee(hub, monkeypatch):
    """Vecu : « donner la possibilite d'archiver une app », puis « les archives
    ont un statut d'archive, qui est un statut de meme niveau que en ligne,
    arrete, etc. Il faut pouvoir les filtrer comme ceci ».
    Archivee : hors du hub et du proxy, jamais redemarree, mais toujours dans
    la page des applications (liste « archivees » de /api/apps), sa fiche et
    les droits des comptes gardes. Desarchivee : de retour, arretee."""
    c = hub
    monkeypatch.setattr(app, "stop", lambda n: None)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/app/compta/archiver")
    assert r.status_code == 200 and r.get_json()["effacement_le"]
    d = c.get("/api/apps").get_json()
    assert "compta" not in [a["name"] for a in d["apps"]]
    (arch,) = d["archivees"]
    assert arch["name"] == "compta" and arch["archivee"] is True and arch["command"] == "x"
    assert arch["effacement_le"] == arch["archivee_le"] + 30 * 86400
    assert app.load()["compta"]["enabled"] is False
    # Ni le proxy, ni un demarrage, ni un build.
    assert c.get("/compta/").status_code == 404
    monkeypatch.setattr(app, "is_running", lambda n: False)
    assert "archives" in app.start("compta")
    assert app.run_build("compta")[0] is False
    # Le nom reste reserve.
    r = c.post("/api/add", json={"name": "compta", "path": "/w/a"})
    assert r.status_code == 400 and "archives" in r.get_json()["error"]
    # La fiche de marie, enregistree sans compta (qu'elle ne voit plus), garde le droit.
    assert c.put("/api/utilisateurs/marie", json={"projets": ["vitrine"]}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["projets"] == ["compta", "vitrine"]
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    assert "compta" not in [a["name"] for a in u.get("/api/mes-apps").get_json()["apps"]]
    # Desarchivee : de retour, arretee, avec ses droits et sa configuration.
    r = c.post("/api/app/compta/desarchiver")
    assert r.status_code == 200 and r.get_json()["a_reconfigurer"] is False
    d = c.get("/api/apps").get_json()
    assert "compta" in [a["name"] for a in d["apps"]] and d["archivees"] == []
    assert "compta" in [a["name"] for a in u.get("/api/mes-apps").get_json()["apps"]]
    assert app.load()["compta"]["enabled"] is False and app.load()["compta"]["port"] == 9101
    assert c.post("/api/app/compta/desarchiver").status_code == 404
    page = _page_panneau("dashboard.html")
    assert "['arch','Archivées']" in page and "if(a.archivee) return ['arch','Archivée'];" in page


def test_trente_jours_apres_seule_la_configuration_est_effacee(hub, monkeypatch, tmp_path):
    """Vecu : « Une application archivee depuis trop longtemps n'est pas
    supprimee. Seule sa configuration l'est. » Au bout de 30 jours : plus de
    commandes, de port, de droits ni d'alertes ; l'application reste,
    archivee. Desarchivee ensuite, elle revient privee, avec un port et des
    commandes redetectees."""
    c = hub
    monkeypatch.setattr(app, "stop", lambda n: None)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    projet = tmp_path / "compta"
    projet.mkdir()
    (projet / "index.html").write_text("<html>")
    apps = app.load()
    apps["compta"]["path"] = str(projet)
    app.save(apps)
    assert c.post("/api/app/compta/archiver").status_code == 200
    quand = app.load()["compta"]["archivee_le"]
    assert app.purger_les_apps_archivees(quand + 29 * 86400) == []
    assert app.purger_les_apps_archivees(quand + 30 * 86400) == ["compta"]
    a = app.load()["compta"]
    assert app.est_archivee(a) and a["path"] == str(projet)
    for cle in ("command", "port", "visibility"):
        assert cle not in a, cle
    assert "compta" not in app.lire_utilisateurs()["marie"]["projets"]
    (arch,) = c.get("/api/apps").get_json()["archivees"]
    assert arch["configuration_effacee"] is True and arch["effacement_le"] is None
    # Une seule fois.
    assert app.purger_les_apps_archivees(quand + 60 * 86400) == []
    r = c.post("/api/app/compta/desarchiver")
    assert r.get_json()["a_reconfigurer"] is True
    a = app.load()["compta"]
    assert a["visibility"] == "privee" and a["port"] and "http.server" in a["command"]


def test_une_application_par_defaut_ne_s_archive_pas(hub):
    c = hub
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.post(f"/api/app/{app.DIAGNOSTIC_NOM}/archiver").status_code == 403
    assert c.post("/api/app/inconnue/archiver").status_code == 404
    page = _page_panneau("dashboard.html")
    assert 'id="e-archiver"' in page and 'id="zone-archives"' not in page
    # Vecu : « Je ne vois pas de bouton pour archiver. Je veux tout pouvoir
    # gerer depuis l'onglet Applications. » Les gestes sont sur la carte et
    # la ligne de chaque application, et en tete de sa fiche.
    assert page.count("${apBascule(a)}${apGestes(a)}") == 2
    for action in ("'app-archiver':", "'app-desarchiver':", "'app-supprimer':"):
        assert action in page, action
    assert 'data-agir="app-archiver"' in page


def test_une_application_ne_demarre_pas_pendant_son_build(tmp_path, monkeypatch):
    """Vecu : « Bloquer le bouton demarrer lorsque l'application est en train
    de build ». Lancee pendant son build, elle partirait sur des
    dependances a moitie installees. Le serveur refuse, /api/apps le dit,
    et la page grise « Demarrer »."""
    projet = tmp_path / "projet"
    projet.mkdir()
    app.save({"site": {"path": str(projet), "command": "sleep 60", "port": 9131,
                       "visibility": "privee"}})
    monkeypatch.setattr(app, "_builds_en_cours", {"site"})
    erreur = app.start("site")
    assert erreur and "build" in erreur and not app.is_running("site")
    c = app.flask_app.test_client()
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    app.flask_app.secret_key = "cle-de-test"
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    (d,) = [a for a in c.get("/api/apps").get_json()["apps"] if a["name"] == "site"]
    assert d["build_en_cours"] is True
    # Le verrou se pose et se leve avec le build lui-meme.
    monkeypatch.setattr(app, "_builds_en_cours", set())
    vus = []
    monkeypatch.setattr(app, "_construire", lambda n: vus.append(app.build_en_cours(n)) or (True, None))
    assert app.run_build("site") == (True, None)
    assert vus == [True] and not app.build_en_cours("site")
    page = _page_panneau("dashboard.html")
    assert "const enBuild = a => !!a.build_en_cours && !a.running;" in page
    bascule = page.split("function apBascule(a){")[1].split("\n}")[0]
    assert "disabled" in bascule and "enBuild(a)" in bascule


def test_le_choix_du_dossier_grise_ceux_deja_pris_et_cache_les_projets_par_defaut(tmp_path,
                                                                                    monkeypatch,
                                                                                    client):
    """Vecu : « Griser les dossiers d'applications qui sont deja configurees
    dans la configuration d'une nouvelle application. Ne pas montrer les
    applications par defaut, soient diagnostic et messages ». Le serveur
    refuse aussi de reprendre un dossier deja pris."""
    racine = tmp_path / "w"
    for nom in ("compta", "libre", "diagnostic", "messages"):
        (racine / nom).mkdir(parents=True)
    monkeypatch.setattr(app, "ROOT", str(racine))
    app.save({"compta": {"titre": "Compta", "path": str(racine / "compta"), "command": "x",
                         "port": 9101}})
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = client.get("/api/browse?path=" + str(racine)).get_json()
    assert d["dirs"] == ["compta", "libre"]
    assert d["pris"] == {"compta": "Compta"}
    # Dans la fiche de compta, son propre dossier n'est pas « pris ».
    assert client.get(f"/api/browse?path={racine}&pour=compta").get_json()["pris"] == {}
    r = client.post("/api/add", json={"name": "autre", "path": str(racine / "compta"),
                                      "command": "x"})
    assert r.status_code == 400 and "Compta" in r.get_json()["error"]
    r = client.post("/api/add", json={"name": "diag", "path": str(racine / "diagnostic"),
                                      "command": "x"})
    assert r.status_code == 400
    assert client.post("/api/add", json={"name": "libre", "path": str(racine / "libre"),
                                         "command": "x"}).status_code == 200
    # Une fiche garde son dossier ; elle ne prend pas celui d'une autre.
    assert client.put("/api/app/compta", json={"path": str(racine / "compta"),
                                               "command": "y"}).status_code == 200
    r = client.put("/api/app/libre", json={"path": str(racine / "compta"), "command": "y"})
    assert r.status_code == 400
    page = _page_panneau("dashboard.html")
    assert ".fb-l div.dossier-pris{" in page and "n.className = 'dossier-pris';" in page


def test_un_port_interne_pris_au_panneau_est_reattribue(monkeypatch):
    """Vecu : « Je vois des ports 9002, 9003 utilises dans mes apps
    configurees ». Le 9002 est le port PUBLIC des applications ; une
    application inscrite dessus ne peut pas l'ouvrir, et le proxy se
    relaierait a lui-meme. Elle recoit un port interne libre ; un autre port
    hors plage (9003) reste tel quel."""
    monkeypatch.setattr(app, "_ports_ecoutes", lambda: {})
    app.save({"a": {"path": "/w/a", "command": "x", "port": app.APPS_PORT},
              "b": {"path": "/w/b", "command": "x", "port": app.PANEL_PORT},
              "c": {"path": "/w/c", "command": "x", "port": 9003}})
    app.realigner_les_ports()
    ports = {n: a["port"] for n, a in app.load().items()}
    assert app.PORT_MIN <= ports["a"] <= app.PORT_MAX
    assert app.PORT_MIN <= ports["b"] <= app.PORT_MAX and ports["a"] != ports["b"]
    assert ports["c"] == 9003
    assert "Port interne ($PORT), relayé par le port des applications" in _page_panneau("dashboard.html")


def test_un_droit_ne_survit_pas_a_l_application_supprimee(hub, monkeypatch):
    """Vecu : audit de l'espace d'administration (« les droits sont bien
    proteges »). Supprimer une application laissait son nom dans les projets
    autorises des comptes : une application creee plus tard sous le meme nom
    s'ouvrait aussitot a ces comptes, sans que l'administrateur le leur ait
    donne (regle 12). Temoin : un renommage, lui, deplace le droit."""
    c = hub
    monkeypatch.setattr(app, "stop", lambda n: None)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    mes = lambda: {a["name"] for a in u.get("/api/mes-apps").get_json()["apps"]}
    assert "compta" in mes()

    assert c.delete("/api/app/compta").status_code == 200
    assert "compta" not in app.lire_utilisateurs()["marie"]["projets"]
    # Une nouvelle application prend le nom rendu : elle reste fermee a Marie.
    apps = app.load()
    apps["compta"] = {"path": "/w/n", "command": "x", "port": 9104, "enabled": True,
                      "visibility": "privee"}
    app.save(apps)
    assert "compta" not in mes()
    assert u.get("/compta/").status_code == 403

    # Temoin : le droit suit un renommage, il ne se perd pas.
    comptes = app.lire_utilisateurs()
    comptes["marie"]["projets"] = ["secret"]
    app.ecrire_utilisateurs(comptes)
    assert c.post("/api/app/secret/renommer", json={"nom": "Coffre"}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["projets"] == ["coffre"]
    assert "coffre" in mes()


def test_une_application_ouverte_au_reseau_local_reste_privee_depuis_internet(hub, monkeypatch):
    """Vecu : « Je dois pouvoir ou non rendre public une application sur mon
    reseau local seulement. » « locale » s'ouvre sans compte depuis le reseau
    local ; depuis internet -- adresse publique, ou Tailscale Funnel (reconnu
    a son port, jamais a un en-tete) -- elle redevient privee."""
    c = hub
    monkeypatch.setattr(app, "adresse_publique", lambda: "")
    apps = app.load()
    apps["secret"]["visibility"] = "locale"
    app.save(apps)
    maison = {"REMOTE_ADDR": "192.168.1.10"}
    internet = {"REMOTE_ADDR": "203.0.113.9"}
    funnel = {"REMOTE_ADDR": "127.0.0.1", "SERVER_PORT": str(app.TUNNEL_PORT_APPS)}

    def ouverte(client, env):
        r = client.get("/secret/", environ_overrides=env)
        return r.status_code not in (302, 401, 403), r.status_code

    assert ouverte(c, maison)[0]
    assert not ouverte(c, internet)[0]
    assert not ouverte(c, funnel)[0]
    # Un en-tete forge ne fait pas d'internet un reseau local.
    assert not ouverte(c, dict(internet, HTTP_X_FORWARDED_FOR="192.168.1.10"))[0]
    # Marie n'a pas le droit sur « secret » : chez elle elle entre, pas dehors.
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    assert ouverte(u, maison)[0]
    assert u.get("/secret/", environ_overrides=internet).status_code == 403
    # Temoin : une application privee reste fermee meme a la maison.
    assert app.flask_app.test_client().get("/compta/", environ_overrides=maison).status_code == 302


def test_sans_adresse_publique_on_ouvre_au_reseau_local(hub, monkeypatch):
    """Sans adresse publique, « Publique » ne ferait que retirer
    l'authentification : refusee par le bouton ET par le formulaire de
    configuration (qui l'acceptait). La bascule ouvre alors au reseau local,
    et referme toujours."""
    c = hub
    monkeypatch.setattr(app, "adresse_publique", lambda: "")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/visibility/secret", json={})
    assert r.status_code == 200 and r.get_json()["visibility"] == "locale"
    assert c.post("/api/visibility/secret", json={}).get_json()["visibility"] == "privee"
    assert c.post("/api/visibility/secret", json={"visibility": "publique"}).status_code == 400
    assert c.post("/api/visibility/secret", json={"visibility": "locale"}).status_code == 200
    r = c.put("/api/app/secret", json={"path": "/w/c", "command": "x", "visibility": "publique"})
    assert r.status_code == 400 and app.load()["secret"]["visibility"] == "locale"
    # Une application deja publique garde son choix et se referme.
    assert c.post("/api/visibility/vitrine", json={"visibility": "publique"}).status_code == 200
    assert c.post("/api/visibility/vitrine", json={}).get_json()["visibility"] == "privee"
    assert c.get("/api/apps/secret/acces").get_json()["locale"] is True
    page = _page_panneau("dashboard.html")
    for appel in ("setVisEdit", "setVisAdd"):
        assert (f'<button data-v="locale" data-agir="{appel}" data-args=\'["locale"]\'>'
                'Réseau local</button>') in page


# ===========================================================================
# LES COMPTES
# connexion, second facteur, cles d'acces, mots de passe, invitations,
# sessions, appareils connectes, retour apres connexion.
# ===========================================================================

# --------------------------- 3. authentification ---------------------------




def test_les_routes_api_refusent_sans_session(client):
    assert client.get("/api/apps").status_code == 401
    assert client.post("/api/toggle/quelconque").status_code == 401
    assert client.post("/login", json={"nom": "admin", "password": "secret-de-test"}).status_code == 200
    assert client.get("/api/apps").status_code == 200




def test_la_limite_de_tentatives_ne_se_contourne_pas_par_en_tete(client):
    """X-Forwarded-For est pose par le client quand le service est publie
    directement : le faire varier donnait un compteur neuf a chaque essai, ce
    qui annulait la limite."""
    assert not hasattr(app, "trust_proxy"), "X-Forwarded-For ne doit jamais etre cru"
    for i in range(app.RATE_LIMIT_MAX):
        # 403 : une requete relayee n'atteint pas l'administration. Le refus
        # compte quand meme comme une tentative.
        assert client.post("/login", json={"nom": "admin", "password": "faux"},
                           headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code in (401, 403)
    assert client.post("/login", json={"nom": "admin", "password": "faux"},
                       headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429




def test_le_point_d_appui_de_dagster_repond_par_un_code_sans_page(client):
    """nginx interroge cette route avant chaque requete vers Dagster : il lui
    faut un code, pas une redirection. Si elle se mettait a repondre 302
    comme les autres routes protegees, nginx la lirait comme un refus et
    Dagster deviendrait inaccessible meme connecte."""
    r = client.get("/api/auth-check")
    assert r.status_code == 401 and not r.data

    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = client.get("/api/auth-check")
    assert r.status_code == 204 and not r.data




def test_le_cookie_de_session_est_samesite_lax(client):
    """Les actions du panneau sont des POST sans corps : sans SameSite, un
    formulaire pose sur un autre site peut les declencher avec le cookie de
    l'utilisateur connecte."""
    r = client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
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
    app._apps_cache["signature"] = None
    app.save({"prive": {"path": str(tmp_path), "command": "x", "port": 9101,
                        "enabled": True, "visibility": "privee"}})
    monkeypatch.setattr(app, "is_running", lambda n: True)

    r = client.get("/prive/", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["Location"]

    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
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
    assert client.post("/login", json={"nom": "admin", "password": "secret-de-test"}).status_code == 200




def test_avec_un_secret_le_mot_de_passe_seul_ne_suffit_plus(client, monkeypatch):
    monkeypatch.setattr(app, "_totp_secret", app.totp_nouveau_secret())
    assert client.post("/login", json={"nom": "admin", "password": "secret-de-test"}).status_code == 401
    bon = app.totp_code(app._totp_secret, int(time.time()) // app.TOTP_PAS)
    assert client.post("/login", json={"nom": "admin", "password": "secret-de-test", "code": bon}).status_code == 200


# ---------- changer son propre mot de passe ----------
#
# Cela n'existait pas : le mot de passe d'administration ne se changeait
# qu'en editant credentials.env sur le serveur, donc en s'y connectant en
# SSH. Un secret qu'on ne peut pas changer facilement est un secret qu'on ne
# change jamais -- et celui-la donne l'execution de commandes sur la machine.




def test_l_admin_ne_change_pas_son_mot_de_passe_depuis_le_panneau(compte_admin):
    """CE QUI A CHANGE, ET POURQUOI. Le panneau reecrivait credentials.env.
    Or ce fichier est LA source : c'est lui qu'on lit pour se depanner quand
    le panneau ne repond plus, lui qu'on copie en changeant de machine, lui
    que le diagnostic controle. Deux sources pour un meme secret, c'est
    decouvrir laquelle fait foi le jour ou elles divergent -- au pire moment.
    """
    r = compte_admin.post("/api/compte/mot-de-passe",
                          json={"ancien": "ancien-mot-de-passe",
                                "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 403, r.data
    assert "credentials.env" in r.get_json()["error"]
    # Rien n'a bouge, ni en memoire ni sur le disque.
    assert app.admin_password() == "ancien-mot-de-passe"
    assert app.read_shared_value("APP_MANAGER_ADMIN_PASSWORD") == "ancien-mot-de-passe"
    assert app.read_shared_value("APP_MANAGER_SESSION_SECRET") == "cle-de-session-existante"




def test_une_session_volee_ne_verrouille_pas_le_compte(deux_espaces):
    """L'ancien mot de passe est exige meme sur une session deja ouverte :
    sans cela, un cookie capture suffirait a prendre la place de quelqu'un
    definitivement."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "pas-le-bon", "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 403, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"],
                                    "mot-de-passe-long")




def test_un_mot_de_passe_trop_court_est_refuse(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "mot-de-passe-long", "nouveau": "court"})
    assert r.status_code == 400, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"],
                                    "mot-de-passe-long")




def test_un_compte_nomme_change_le_sien_et_pas_celui_de_l_admin(deux_espaces):
    """Chacun le sien : la route ne prend aucun nom en parametre, elle agit
    sur la session qui appelle."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "mot-de-passe-long", "nouveau": "un-autre-mot-de-passe"})
    assert r.status_code == 200, r.data
    comptes = app.lire_utilisateurs()
    assert app.verifie_mot_de_passe(comptes["marie"], "un-autre-mot-de-passe")
    assert not app.verifie_mot_de_passe(comptes["marie"], "mot-de-passe-long")
    # Le sel a change aussi : deux mots de passe identiques ne doivent pas
    # produire la meme empreinte d'un compte a l'autre.
    assert comptes["marie"]["sel"] != "aa" * 16




def test_un_compte_nomme_doit_donner_son_ancien_mot_de_passe(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "pas-le-bon", "nouveau": "un-autre-mot-de-passe"})
    assert r.status_code == 403, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"], "mot-de-passe-long")


# ---------- 9. second facteur obligatoire pour les comptes utilisateurs ----------
#
# Ces comptes existent pour etre distribues : leur mot de passe circule par un
# canal qu'on ne maitrise pas, et sera reutilise ailleurs. Le point a tenir
# est qu'un mot de passe seul n'ouvre JAMAIS de session -- ni avant
# l'inscription du facteur, ni apres.




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




def test_une_inscription_perimee_ou_deja_doublee_ne_se_rejoue_pas(deux_espaces, monkeypatch):
    """Trouve a l'audit de securite (regle 5) : l'etat de l'inscription
    vivait dans le cookie sans date. Un cookie obtenu quand le compte n'avait
    aucun facteur restait rejouable trois jours, et inscrivait un nouveau
    code meme apres que le titulaire eut pose une cle d'acces."""
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    # Onze minutes plus tard : l'inscription a expire.
    maintenant = app.time.time()
    with monkeypatch.context() as m:
        m.setattr(app.time, "time", lambda: maintenant + app.INSCRIPTION_VALIDITE + 60)
        r = c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    assert r.status_code == 400
    assert not app.lire_utilisateurs()["marie"].get("totp")

    # Dans les temps, mais une cle d'acces a ete posee entre-temps.
    d = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"}).get_json()
    assert "secret" in d, d
    secret = d["secret"]
    app.ecrire_passkeys({"marie": [{"id": "k", "cle_publique": "x", "nom": "iPhone"}]})
    r = c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    assert r.status_code == 409
    assert not app.lire_utilisateurs()["marie"].get("totp")
    assert c.get("/api/mes-apps").status_code == 401




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

    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
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




def test_sans_l_obligation_l_administrateur_entre_au_mot_de_passe(deux_espaces):
    """APP_MANAGER_ADMIN_2FA=0 (pose par le filet) : la marche arriere du
    compose, le jour ou le telephone qui portait le facteur est perdu."""
    c = deux_espaces
    assert not app.totp_actif()
    assert c.post("/login", json={"nom": "admin", "password": "secret-de-test"}).status_code == 200




# ---------- 12 quinquies. la duree de vie d'une session ----------
#
# Deux limites, et il faut les deux. L'INACTIVITE ferme une session oubliee
# -- un poste public, un portable perdu -- au bout de trois jours ; elle est
# tenue par le cookie, dont Flask repousse la date a chaque requete. Elle ne
# dit rien d'une session ENTRETENUE : un onglet laisse ouvert sur une page
# qui interroge l'API la maintiendrait valable pour toujours. D'ou la limite
# ABSOLUE de trente jours, comptee par le serveur depuis l'ouverture.

def test_les_deux_limites_de_session_sont_posees():
    # Le cookie porte la limite d'inactivite : c'est lui qui la fait
    # respecter, y compris sur un navigateur qui ne revient jamais.
    assert app.flask_app.config["PERMANENT_SESSION_LIFETIME"] == datetime.timedelta(days=3)
    assert app.SESSION_ABSOLUE == datetime.timedelta(days=30)




def test_une_session_ouverte_est_datee(journal):
    """Sans date d'ouverture, la limite absolue n'a rien a compter."""
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    with c.session_transaction() as sess:
        assert isinstance(sess.get("ouverte"), (int, float))
        assert abs(time.time() - sess["ouverte"]) < 30




def test_une_session_de_plus_de_trente_jours_ne_vaut_plus_rien(journal):
    """Meme entretenue : la date d'ouverture ne se repousse pas."""
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.get("/api/apps").status_code == 200
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 31 * 86400
    assert c.get("/api/apps").status_code in (401, 403)
    # Et la session est bien VIDEE, pas seulement refusee une fois.
    with c.session_transaction() as sess:
        assert sess.get("authed") is not True




def test_une_session_de_vingt_neuf_jours_tient_encore(deux_espaces):
    """La limite est une limite, pas une approximation : on ne deconnecte
    pas quelqu'un la veille. (Un compte utilisateur : l'administrateur a la
    sienne, plus courte.)"""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 29 * 86400
    assert c.get("/api/mes-apps").status_code == 200




def test_la_session_d_administration_tombe_au_bout_de_douze_heures(journal):
    """L'administrateur ouvre tout, Dagster et Mailpit compris : sa session
    vit une journee de travail, pas un mois."""
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 11 * 3600
    assert c.get("/api/apps").status_code == 200
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 13 * 3600
    assert c.get("/api/apps").status_code in (401, 403)




def test_une_session_d_avant_ce_mecanisme_n_est_pas_jetee(journal):
    """Mettre a jour le panneau ne doit deconnecter personne : une session
    sans date est datee de maintenant, pas refusee."""
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    with c.session_transaction() as sess:
        del sess["ouverte"]
    assert c.get("/api/apps").status_code == 200
    with c.session_transaction() as sess:
        assert isinstance(sess.get("ouverte"), (int, float))




def test_l_expiration_est_journalisee(journal):
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 31 * 86400
    c.get("/api/apps")
    genres = [e.get("action") for e in app.lire_acces() if e.get("genre") == "session"]
    assert any("12 heures" in (a or "") for a in genres), genres




# ---------- 14. QR code du second facteur ----------
#
# Recopier une cle de 32 caracteres a la main est le moment ou l'inscription
# echoue. Le QR code supprime cette etape -- mais il porte le secret, donc il
# ne doit jamais voyager par l'adresse, et son absence ne doit rien casser.

def test_le_qr_code_ne_sort_pas_de_la_session(client):
    """Aucun secret dans l'URL : une adresse finit dans l'historique du
    navigateur, dans les journaux d'acces et dans le referer de la page
    suivante. La route ne lit QUE la session signee."""
    pytest.importorskip("qrcode")
    # Sans inscription en attente, rien a montrer.
    assert client.get("/qr/totp.svg").status_code == 404

    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = client.post("/api/securite/totp/preparer").get_json()
    assert d["qr"] == "/qr/totp.svg", "aucun secret ne doit apparaitre dans l'adresse"

    r = client.get("/qr/totp.svg")
    assert r.status_code == 200
    assert r.mimetype == "image/svg+xml"
    corps = r.get_data(as_text=True)
    assert corps.startswith("<svg") and "<rect" in corps
    # Le secret est encode dans les modules du QR, pas ecrit dans le SVG.
    assert d["secret"] not in corps




def test_le_qr_code_disparait_avec_l_inscription(client):
    """Une fois le facteur enregistre, la route ne doit plus rien servir."""
    pytest.importorskip("qrcode")
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = client.post("/api/securite/totp/preparer").get_json()
    assert client.get("/qr/totp.svg").status_code == 200
    code = app.totp_code(d["secret"], int(time.time()) // app.TOTP_PAS)
    r = client.post("/api/securite/totp/activer", json={"code": code})
    assert r.status_code == 200, r.data
    assert client.get("/qr/totp.svg").status_code == 404




def test_sans_la_bibliotheque_qr_l_inscription_marche_encore(client, monkeypatch):
    """Le panneau doit rester lancable avec Flask pour seule dependance.

    Sans qrcode, la page retombe sur la cle a saisir : la route repond 404,
    l'image se masque, et l'inscription se termine normalement.
    """
    # La bibliotheque rendue introuvable, pour de vrai : un sys.modules a None
    # fait lever ImportError a l'import, exactement comme si elle manquait.
    monkeypatch.setitem(sys.modules, "qrcode", None)
    assert app.qr_svg("otpauth://totp/CodeLab:admin?secret=AAAA") == ""

    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = client.post("/api/securite/totp/preparer").get_json()
    assert d["secret"], "la cle a recopier reste fournie"
    assert client.get("/qr/totp.svg").status_code == 404
    code = app.totp_code(d["secret"], int(time.time()) // app.TOTP_PAS)
    assert client.post("/api/securite/totp/activer", json={"code": code}).status_code == 200


# ---------- 15. adresse mail des comptes et inscription libre ----------
#
# L'adresse relie un compte a quelqu'un de joignable, et c'est elle qui rend
# l'inscription libre defendable. Ce qui doit rester vrai : une adresse n'est
# "verifiee" que si un code envoye dessus est revenu, un compte inscrit
# librement n'ouvre aucun projet et ne se connecte pas avant d'avoir confirme,
# et l'inscription reste fermee sans serveur d'envoi.


# ---------- mot de passe oublie ----------
#
# Par l'adresse mail quand il y en a une de VERIFIEE, et par l'administrateur
# sinon. Aucun des deux chemins ne suffit seul : sans adresse, personne ne
# peut prouver a distance qui il est ; et si le seul recours etait
# l'administrateur, il faudrait le deranger a chaque oubli.




def test_un_code_de_reinitialisation_part_a_l_adresse_verifiee(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    assert c.post("/mot-de-passe/oubli", json={"nom": "marie"}).status_code == 200
    assert partis and partis[-1][0] == ["marie@example.com"]
    assert "reinitialisation" in partis[-1][1] or "einitialisation" in partis[-1][1]

    code = _code_du_dernier_mail(partis)
    r = c.post("/mot-de-passe/reinitialiser",
               json={"nom": "marie", "code": code, "nouveau": "nouveau-mot-de-passe"})
    assert r.status_code == 200, r.data
    compte = app.lire_utilisateurs()["marie"]
    assert app.verifie_mot_de_passe(compte, "nouveau-mot-de-passe")
    assert not app.verifie_mot_de_passe(compte, "mot-de-passe-long")




def test_le_code_de_reinitialisation_ne_dort_pas_en_clair(comptes_mail):
    """Comme celui de la verification d'adresse : le fichier ne garde qu'une
    empreinte. Sinon le code attendrait, lisible, a cote du nom du compte."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    code = _code_du_dernier_mail(partis)
    assert code not in _base_brute()




def test_le_code_de_reinitialisation_ne_sert_qu_une_fois(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    code = _code_du_dernier_mail(partis)
    assert c.post("/mot-de-passe/reinitialiser",
                  json={"nom": "marie", "code": code,
                        "nouveau": "nouveau-mot-de-passe"}).status_code == 200
    r = c.post("/mot-de-passe/reinitialiser",
               json={"nom": "marie", "code": code, "nouveau": "encore-un-autre"})
    assert r.status_code == 400, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"],
                                    "nouveau-mot-de-passe")




def test_un_code_de_verification_ne_vaut_pas_reinitialisation(comptes_mail):
    """Deux pouvoirs tres differents : verifier une adresse rend un compte
    utilisable, reinitialiser un mot de passe le rend ACCESSIBLE. Un seul
    jeton pour les deux melangerait les deux."""
    c, partis = comptes_mail
    _compte_avec_adresse(verifiee=False)
    comptes = app.lire_utilisateurs()
    code_adresse = app.poser_code_email(comptes["marie"])
    app.ecrire_utilisateurs(comptes)
    r = c.post("/mot-de-passe/reinitialiser",
               json={"nom": "marie", "code": code_adresse, "nouveau": "nouveau-mot-de-passe"})
    assert r.status_code == 400, r.data




def test_sans_adresse_verifiee_rien_ne_part(comptes_mail):
    """Et l'utilisateur est renvoye vers l'administrateur : c'est l'autre
    chemin, celui qui ne depend d'aucun mail."""
    c, partis = comptes_mail
    _compte_avec_adresse(verifiee=False)
    assert c.post("/mot-de-passe/oubli", json={"nom": "marie"}).status_code == 200
    assert partis == []




def test_la_reponse_ne_dit_jamais_si_le_compte_existe(comptes_mail):
    """Sinon ce formulaire devient un annuaire : on y tape des noms jusqu'a
    trouver ceux qui existent."""
    c, _partis = comptes_mail
    _compte_avec_adresse()
    connue = c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    inconnue = c.post("/mot-de-passe/oubli", json={"nom": "fantome"})
    assert connue.status_code == inconnue.status_code == 200
    assert connue.get_json() == inconnue.get_json()
    # Et au moment de poser le mot de passe, un compte inconnu rend le meme
    # message qu'un code faux.
    faux = c.post("/mot-de-passe/reinitialiser",
                  json={"nom": "fantome", "code": "123456", "nouveau": "mot-de-passe-long"})
    mauvais = c.post("/mot-de-passe/reinitialiser",
                     json={"nom": "marie", "code": "000000", "nouveau": "mot-de-passe-long"})
    assert faux.get_json()["error"] == mauvais.get_json()["error"] == "Code incorrect."




def test_un_second_envoi_immediat_ne_part_pas(comptes_mail):
    """Sans cela, ce formulaire est un robinet a mails vers l'adresse de
    quelqu'un d'autre."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    assert len(partis) == 1, partis




def test_la_reinitialisation_est_journalisee(comptes_mail, tmp_path, monkeypatch):
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    c.post("/mot-de-passe/reinitialiser",
           json={"nom": "marie", "code": _code_du_dernier_mail(partis),
                 "nouveau": "nouveau-mot-de-passe"})
    actions = [e.get("action") for e in app.lire_acces()
               if e.get("genre") == "mot-de-passe"]
    assert "code envoye" in actions
    assert "reinitialise par mail" in actions




def test_aucun_mot_de_passe_n_est_ecrit_en_clair(comptes_mail):
    """La regle de fond : ce qui est persiste est une EMPREINTE, jamais le
    mot de passe. Vrai a la creation, vrai au changement, et vrai a la
    reinitialisation -- c'est le chemin le plus recent, donc le plus facile
    a oublier."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    c.post("/mot-de-passe/reinitialiser",
           json={"nom": "marie", "code": _code_du_dernier_mail(partis),
                 "nouveau": "nouveau-mot-de-passe"})
    registre = _base_brute()
    assert "nouveau-mot-de-passe" not in registre
    assert "mot-de-passe-long" not in registre
    compte = app.lire_utilisateurs()["marie"]
    # Empreinte derivee et sel propre a ce compte, refait a chaque changement.
    assert len(compte["sel"]) >= 32 and len(compte["hash"]) >= 32
    assert compte["hash"] == app.derive_mot_de_passe("nouveau-mot-de-passe", compte["sel"])




def test_la_base_ne_recoit_aucune_empreinte_de_mot_de_passe():
    """La base est joignable par les projets deployes. Y deposer des
    empreintes leur offrirait une attaque hors ligne sur les mots de passe
    du panneau : le miroir ne porte que ce qui se lit deja dans l'interface.
    """
    src = _page_panneau("app.py")
    table = src.split("CREATE TABLE IF NOT EXISTS utilisateurs")[1].split(")\"\"\"")[0]
    for interdit in ("hash", "sel", "mot_de_passe", "password", "totp"):
        assert interdit not in table, f"{interdit} n'a rien a faire dans cette table"
    ecriture = src.split("def _pg_ecrire_utilisateurs(")[1].split("\ndef ")[0]
    assert '"hash"' not in ecriture and '"sel"' not in ecriture




def test_le_code_de_verification_ne_dort_pas_en_clair(comptes_mail):
    """utilisateurs.json ne doit pas contenir le code qu'on vient d'envoyer.

    Il y resterait a cote du nom du compte, lisible par qui lit le fichier --
    exactement ce que la derivation des mots de passe evite par ailleurs.
    """
    c, partis = comptes_mail
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    c.post("/api/utilisateurs", json={"nom": "marie", "mot_de_passe": "mot-de-passe-long"})
    c.post("/logout")
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/mon-compte/email", json={"email": "marie@example.com"})
    assert r.status_code == 200, r.data
    code = _code_du_dernier_mail(partis)
    assert partis[-1][0] == ["marie@example.com"], "le code part a l'adresse declaree"

    assert code not in _base_brute(), "le code ne doit etre range que sous forme d'empreinte"

    assert c.post("/api/mon-compte/email/confirmer",
                  json={"code": "000000" if code != "000000" else "111111"}).status_code == 400
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False
    assert c.post("/api/mon-compte/email/confirmer", json={"code": code}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is True




def test_un_code_ne_se_devine_pas_par_essais_successifs(comptes_mail):
    """Six chiffres se devinent en un million d'essais : il en faut une borne."""
    c, partis = comptes_mail
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    c.post("/api/utilisateurs", json={"nom": "marie", "mot_de_passe": "mot-de-passe-long"})
    c.post("/logout")
    _connecte(c, "marie", "mot-de-passe-long")
    c.post("/api/mon-compte/email", json={"email": "marie@example.com"})
    juste = _code_du_dernier_mail(partis)
    faux = "000000" if juste != "000000" else "111111"

    for _ in range(app.CODE_EMAIL_ESSAIS):
        assert c.post("/api/mon-compte/email/confirmer", json={"code": faux}).status_code == 400
    # Le bon code ne passe plus : le code en cours a ete jete.
    r = c.post("/api/mon-compte/email/confirmer", json={"code": juste})
    assert r.status_code == 400
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False




def test_changer_d_adresse_annule_la_verification(comptes_mail):
    """Sinon il suffirait de remplacer une adresse verifiee par une autre
    pour heriter de son statut sans jamais rien recevoir."""
    c, partis = comptes_mail
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    c.post("/api/utilisateurs", json={"nom": "marie", "mot_de_passe": "mot-de-passe-long"})
    c.post("/logout")
    _connecte(c, "marie", "mot-de-passe-long")
    c.post("/api/mon-compte/email", json={"email": "marie@example.com"})
    c.post("/api/mon-compte/email/confirmer", json={"code": _code_du_dernier_mail(partis)})
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is True

    c.post("/api/mon-compte/email", json={"email": "autre@example.com"})
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False
    # Cote administrateur aussi : changer l'adresse de quelqu'un ne lui
    # transmet pas le statut de l'ancienne. On repart d'un etat verifie pour
    # que ce soit bien ce chemin-la qui soit mis a l'epreuve.
    comptes = app.lire_utilisateurs()
    comptes["marie"]["email_verifie"] = True
    app.ecrire_utilisateurs(comptes)
    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    c.put("/api/utilisateurs/marie", json={"email": "encore@example.com"})
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False




def test_il_n_y_a_plus_d_inscription_libre(comptes_mail):
    """Les comptes ne naissent que par invitation de l'administrateur : plus de
    formulaire public, meme avec un serveur d'envoi configure."""
    c, partis = comptes_mail
    r = c.post("/inscription", json={"nom": "paul", "email": "paul@example.com",
                                     "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code >= 300
    r = c.post("/inscription/", json={"nom": "paul", "email": "paul@example.com",
                                      "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 404
    assert "paul" not in app.lire_utilisateurs() and not partis
    assert c.get("/api/inscription").status_code in (401, 404)


# ---------- 18. cles d'acces (passkeys) ----------
#
# Le navigateur impose ses conditions : HTTPS, un nom de domaine, pas une
# adresse IP. Ce qui doit rester vrai cote serveur : on ANNONCE ces
# conditions au lieu de laisser le bouton echouer, on ne croit pas un proxy
# qu'on n'a pas declare, et une cle inconnue n'ouvre rien.
#
# La ceremonie WebAuthn elle-meme (signature, attestation) se verifie au
# navigateur, avec un authentificateur virtuel : elle ne tient pas dans un
# test sans navigateur.




def test_les_conditions_du_navigateur_sont_annoncees(cles, monkeypatch):
    """Un bouton qui echoue toujours est pire qu'un bouton absent."""
    pytest.importorskip("webauthn")
    # En clair, sur autre chose que localhost : impossible, et on dit pourquoi.
    d = _etat_passkeys(cles, Host="codelab.example.com")
    assert d["possible"] is False and "HTTPS" in d["empechement"]

    # Une adresse IP ne peut pas servir de relying party id -- meme en HTTPS,
    # et c'est bien la regle de l'IP qui doit refuser, pas celle du TLS.
    d = cles.get("/api/passkeys/etat", base_url="https://192.168.1.20:9001").get_json()
    assert d["possible"] is False
    assert "nom de domaine" in d["empechement"]

    # localhost est un contexte securise pour le navigateur : ca marche.
    assert _etat_passkeys(cles, Host="localhost:9001")["possible"] is True




def test_x_forwarded_proto_ne_fait_pas_du_https(cles, monkeypatch):
    """X-Forwarded-Proto se pose par n'importe quel client, et plus aucun
    intermediaire legitime ne le pose depuis le retrait du VPS : Funnel se
    reconnait a son port. Une requete en clair qui pretend etre en https
    n'en devient pas une, ni pour les cles d'acces ni pour le cookie."""
    pytest.importorskip("webauthn")
    entetes = {"Host": "codelab.example.com", "X-Forwarded-Proto": "https"}
    d = _etat_passkeys(cles, **entetes)
    assert d["possible"] is False and "HTTPS" in d["empechement"]
    with app.flask_app.test_request_context("/", headers=entetes):
        assert app.https_actif() is False




def test_un_defi_de_cle_d_acces_ne_sert_qu_une_fois(monkeypatch):
    """Trouve a l'audit de securite : le defi ne vivait que dans le cookie.
    Avec une cle dont le compteur reste a 0, une reponse signee capturee
    avec son cookie se rejouait jusqu'a l'expiration du cookie. Un defi
    invente, deja servi ou trop vieux n'est plus accepte."""
    defi = app.emettre_defi(b"une-fois")
    assert app.consommer_defi(defi) == defi
    assert app.consommer_defi(defi) is None                       # deja servi
    import base64 as _b64
    assert app.consommer_defi(_b64.b64encode(b"invente").decode()) is None
    vieux = app.emettre_defi(b"vieux")
    maintenant = app.time.time()
    monkeypatch.setattr(app.time, "time", lambda: maintenant + app.DEFI_DUREE + 1)
    assert app.consommer_defi(vieux) is None                      # expire




def test_une_cle_inconnue_n_ouvre_aucune_session(cles):
    """C'est la signature qui fait foi, jamais le nom annonce par le client."""
    pytest.importorskip("webauthn")
    entetes = {"Host": "localhost:9001"}
    with cles.session_transaction() as s:
        s["passkey_defi"] = app.emettre_defi(b"defi")
    r = cles.post("/login/passkey", headers=entetes, json={
        "credential": {"id": "cle-qui-n-existe-pas", "response": {}}})
    assert r.status_code == 401
    with cles.session_transaction() as s:
        assert s.get("authed") is not True
    assert [e["motif"] for e in app.lire_acces() if e["genre"] == "echec"] == \
        ["cle d'acces inconnue"]




def test_les_cles_sont_celles_du_compte_connecte(cles):
    """La liste et la suppression ne parlent jamais d'un autre compte."""
    pytest.importorskip("webauthn")
    app.ecrire_passkeys({
        "admin": [{"id": "AAA", "cle_publique": "x", "compteur": 0, "nom": "Telephone"}],
        "marie": [{"id": "BBB", "cle_publique": "y", "compteur": 0, "nom": "Portable"}],
    })
    assert cles.get("/api/mon-compte/passkeys").status_code == 401
    cles.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    liste = cles.get("/api/mon-compte/passkeys").get_json()["passkeys"]
    assert [k["id"] for k in liste] == ["AAA"]
    # La cle publique ne sort pas : elle n'apprend rien a l'interface.
    assert "cle_publique" not in liste[0]
    # Celle de quelqu'un d'autre ne se supprime pas depuis ce compte.
    assert cles.delete("/api/mon-compte/passkeys/BBB").status_code == 404
    assert len(app.lire_passkeys()["marie"]) == 1




# ---------- 19. modifier un compte ----------
#
# L'administrateur pouvait remettre un second facteur a zero, mais pas
# retirer les cles d'acces : un compte restait ouvrable par un telephone
# perdu. Et surtout, supprimer un compte laissait ses cles derriere lui.

def test_supprimer_un_compte_emporte_ses_cles_d_acces(cles):
    """Le cas qui rouvrirait la porte.

    Recreer un compte du meme nom lui rendrait les cles de l'ancien --
    l'appareil de la personne partie ouvrirait de nouveau la session.
    """
    sel = "dd" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": [], "cree": 0}})
    app.ecrire_passkeys({"marie": [{"id": "AAA", "cle_publique": "x", "compteur": 0}]})

    cles.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert cles.delete("/api/utilisateurs/marie").status_code == 200
    assert "marie" not in app.lire_passkeys()

    # Le nom redevient libre, et le compte recree part de zero.
    r = cles.post("/api/utilisateurs", json={"nom": "marie",
                                             "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 200, r.data
    assert app.lire_passkeys().get("marie", []) == []




def test_l_administrateur_peut_retirer_les_cles_d_un_compte(cles):
    """Appareil perdu : meme geste que la remise a zero du second facteur."""
    sel = "ee" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": [], "cree": 0, "totp": app.totp_nouveau_secret()}})
    app.ecrire_passkeys({"marie": [{"id": "AAA", "cle_publique": "x", "compteur": 0}],
                         "admin": [{"id": "BBB", "cle_publique": "y", "compteur": 0}]})
    cles.post("/login", json={"nom": "admin", "password": "secret-de-test"})

    # Le compte annonce ce qu'il a, sans le livrer.
    comptes = cles.get("/api/utilisateurs").get_json()["utilisateurs"]
    marie = [c for c in comptes if c["nom"] == "marie"][0]
    assert marie["passkeys"] == 1
    assert "cle_publique" not in json.dumps(comptes)

    assert cles.put("/api/utilisateurs/marie",
                    json={"retirer_passkeys": True}).status_code == 200
    assert "marie" not in app.lire_passkeys()
    # Celles des autres comptes ne bougent pas.
    assert len(app.lire_passkeys()["admin"]) == 1
    # Et le compte, lui, existe toujours : on retire un moyen d'entrer, pas
    # la personne.
    assert "marie" in app.lire_utilisateurs()


# ---------- 31. suspendre un compte ----------
#
# Demande : « Ajoute la fonctionnalite suspendre l'utilisateur. » Il n'y avait
# que supprimer -- et supprimer efface les projets autorises, l'adresse et les
# cles d'acces, qu'il faut tout ressaisir au retour. Un depart, un doute, un
# appareil perdu : on veut fermer l'acces TOUT DE SUITE et decider plus tard.
#
# Ce qui compte vraiment ici : la suspension doit valoir sur-le-champ. Une
# suspension qui n'agit qu'a la prochaine connexion ne ferme rien -- celui
# dont on vient de retirer l'acces garde sa session ouverte, c'est-a-dire en
# pratique indefiniment.




def test_sans_serveur_d_envoi_aucun_compte_ne_se_cree(deux_comptes, monkeypatch):
    """Une invitation est un mail, et chaque compte recoit ses codes par
    mail : sans serveur d'envoi, ni invitation ni creation de compte. La
    page le sait des son chargement, pour le proposer a l'admin."""
    monkeypatch.setattr(app, "envoi_mail_configure", _ENVOI_MAIL_REEL)
    monkeypatch.setattr(app, "lire_smtp_personnalise", lambda: None)
    monkeypatch.setattr(app, "smtp_origine", lambda: {})
    c = _admin(deux_comptes)
    assert app.envoi_mail_configure() is False
    r = c.post("/api/utilisateurs", json={"nom": "paul", "mot_de_passe": "x" * 16})
    assert r.status_code == 409 and r.get_json()["smtp_requis"] is True
    assert "paul" not in app.lire_utilisateurs()
    r = c.post("/api/invitations", json={"email": "paul@example.com"})
    assert r.status_code == 409 and r.get_json()["smtp_requis"] is True
    assert not app.lire_invitations()
    page = _page_servie(c)
    assert "smtp: false," in page and "let SMTP_OK = CODELAB_PAGE.smtp;" in page
    assert 'id="ov-smtp"' in page

    monkeypatch.setattr(app, "lire_smtp_personnalise", lambda: {
        "host": "smtp.example.com", "port": 587, "tls": "starttls",
        "expediteur": "codelab@example.com"})
    assert app.envoi_mail_configure() is True
    r = c.post("/api/utilisateurs", json={"nom": "paul", "mot_de_passe": "x" * 16})
    assert r.status_code == 200, r.get_json()
    assert "smtp: true," in c.get("/").get_data(as_text=True)




def test_suspendre_garde_le_compte_et_ses_droits(deux_comptes):
    """C'est toute la difference avec supprimer : la porte se ferme, la piece
    reste en l'etat."""
    c = _admin(deux_comptes)
    r = c.put("/api/utilisateurs/marie", json={"suspendu": True})
    assert r.status_code == 200, r.get_json()
    compte = app.lire_utilisateurs()["marie"]
    assert compte.get("suspendu") is True
    # Rien d'autre n'a bouge.
    assert compte["projets"] == ["prive"]
    assert compte.get("totp")
    # Et la liste le dit, sans qu'il faille ouvrir la fiche.
    vue = c.get("/api/utilisateurs").get_json()["utilisateurs"][0]
    assert vue["suspendu"] is True and vue["projets"] == ["prive"]




def test_reactiver_rend_l_acces_sans_rien_ressaisir(deux_comptes):
    c = _admin(deux_comptes)
    c.put("/api/utilisateurs/marie", json={"suspendu": True})
    c.put("/api/utilisateurs/marie", json={"suspendu": False})
    compte = app.lire_utilisateurs()["marie"]
    assert "suspendu" not in compte
    assert compte["projets"] == ["prive"], "les droits n'ont pas survecu"




def test_un_compte_suspendu_ne_se_connecte_plus(deux_comptes):
    """Le mot de passe est bon : le refus doit donc DIRE pourquoi, sinon on
    cherche une panne la ou il y a une decision."""
    c = _admin(deux_comptes)
    c.put("/api/utilisateurs/marie", json={"suspendu": True})
    c.post("/logout")
    r = c.post("/login", json={"nom": "marie", "password": "un-mot-de-passe"})
    assert r.status_code == 403
    assert "suspendu" in r.get_json()["error"].lower()




def test_la_session_ouverte_d_un_compte_suspendu_tombe(deux_comptes, monkeypatch):
    """LE test de cette fonction. Suspendre pendant qu'une session tourne
    doit la fermer a la requete suivante : sinon la suspension n'est qu'une
    promesse pour la prochaine connexion."""
    c = _admin(deux_comptes)
    # On ouvre une session utilisateur a la main : le second facteur n'a pas
    # sa place dans ce test-ci, il est verifie ailleurs.
    with c.session_transaction() as sess:
        sess["authed"] = True
        sess["role"] = app.ROLE_UTILISATEUR
        sess["utilisateur"] = "marie"
        sess["ouverte"] = int(time.time())
        sess["jeton"] = "j"
    assert c.get("/api/mon-compte").status_code == 200

    comptes = app.lire_utilisateurs()
    comptes["marie"]["suspendu"] = True
    app.ecrire_utilisateurs(comptes)

    # La requete suivante ne doit plus rien autoriser.
    assert c.get("/api/mon-compte").status_code == 401




def test_un_compte_suspendu_n_ouvre_plus_ses_projets(deux_comptes):
    """Les projets restent coches dans sa fiche -- c'est voulu -- mais ils ne
    s'ouvrent plus tant que le compte est suspendu."""
    c = _admin(deux_comptes)
    with app.flask_app.test_request_context("/"):
        from flask import session as s
        s["authed"] = True
        s["role"] = app.ROLE_UTILISATEUR
        s["utilisateur"] = "marie"
        assert app.projets_autorises() == {"prive"}
        comptes = app.lire_utilisateurs()
        comptes["marie"]["suspendu"] = True
        app.ecrire_utilisateurs(comptes)
        assert app.projets_autorises() == set()




def test_la_fenetre_du_compte_offre_les_deux_gestes():
    """Suspendre et supprimer cote a cote, et la difference expliquee : ce
    sont deux gestes qu'on confond tant qu'on ne l'a pas lue."""
    page = _page_panneau("dashboard.html")
    assert 'id="us-f-suspendre"' in page and 'data-agir="usSuspendre"' in page
    assert 'data-agir="usSupprimer"' in page
    bloc = page.split("async function usSuspendre(){")[1].split("\n}")[0]
    assert "suspendu: suspendre" in bloc, "le bouton n'envoie pas l'etat"
    assert "demander(" in bloc, "suspendre sans confirmation se clique par accident"




def test_la_fenetre_du_compte_ne_deroule_plus_tout():
    """Quatre rubriques deroulees, chacune precedee de son paragraphe : la
    fenetre depassait l'ecran et l'on faisait defiler pour trouver
    « Enregistrer »."""
    page = _page_panneau("dashboard.html")
    fenetre = page.split('<div class="ov" id="ov-user">')[1].split("</div></div>")[0]
    assert fenetre.count('class="repli"') >= 3, (
        "les explications sont de nouveau deroulees d'office")
    # Les deux champs d'identite tiennent sur une ligne.
    assert 'class="grid2"' in fenetre




def test_toutes_les_coches_du_panneau_choisissent_une_ligne():
    """« Pour tout CodeLab » : plus une seule case nue a viser.

    Les listes (applications du hub, comptes autorises, projets d'un compte)
    comme les reglages isoles (proxy, HTTPS, administration sur le reseau
    local) designent une LIGNE. La case reste dans le document -- elle porte
    l'etat et recoit le clavier -- simplement on ne la vise plus.
    """
    page = _page_panneau("dashboard.html")
    cases = re.findall(r'<label([^>]*)>\s*\n?\s*<input type="checkbox"', page)
    # Seule exception : l'interrupteur (Face ID / Touch ID), un reglage
    # marche/arret dessine comme tel.
    nues = [c for c in cases if "choix-ligne" not in c and "interrupteur" not in c
            and "tache-case" not in c]
    assert not nues, f"cases a cocher hors d'une ligne a choisir : {nues}"
    # Et le style existe, dans les deux formes : une ligne de liste se teinte,
    # un reglage isole ne prend que la coche et l'accent sur son libelle.
    assert ".choix-ligne:has(>input:checked){background:var(--accent-soft)" in page
    assert ".field>.choix-ligne:has(>input:checked){background:none" in page


# ---------------- invitations, codes par mail, envoi par Mailpit ----------------
#
# Creer un compte, c'est donner une adresse : la personne recoit un lien et
# choisit elle-meme son nom et son mot de passe. Le second facteur n'exige
# plus d'application : un code part par mail. Et les mails du panneau
# passent par Mailpit, comme ceux des applications.




def test_une_invitation_envoie_un_lien_et_le_rend_a_l_administrateur(invitations):
    c, partis, h = invitations
    r = c.post("/api/invitations", json={"email": "paul@example.com"}, headers=h)
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["envoye"] is True and partis[-1][0] == ["paul@example.com"]
    assert d["lien"].endswith("/invitation/" + _jeton_du_lien(partis))
    # Seule l'empreinte est rangee : le jeton ne se relit nulle part.
    assert _jeton_du_lien(partis) not in json.dumps(app.reglage("invitations"))
    assert [i["email"] for i in c.get("/api/invitations").get_json()["invitations"]] \
        == ["paul@example.com"]




def test_accepter_l_invitation_cree_le_compte_verifie_et_ouvre_la_session(invitations):
    c, partis, h = invitations
    c.post("/api/invitations", json={"email": "paul@example.com"}, headers=h)
    jeton = _jeton_du_lien(partis)
    c.post("/logout")
    assert c.get("/api/invitation/" + jeton).get_json()["email"] == "paul@example.com"
    r = c.post("/invitation/" + jeton, json={"nom": "paul", "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 200, r.data
    compte = app.lire_utilisateurs()["paul"]
    assert compte["email"] == "paul@example.com" and compte["email_verifie"] is True
    assert c.get("/api/mes-apps").status_code == 200
    # Le lien ne sert qu'une fois.
    c.post("/logout")
    assert c.post("/invitation/" + jeton, json={"nom": "autre",
                  "mot_de_passe": "mot-de-passe-long"}).status_code == 404




def test_l_invitation_cree_le_compte_avec_face_id_sans_mot_de_passe(invitations, monkeypatch):
    """Vecu : « proposer de configurer le passkey automatiquement sur apple
    et android, sans avoir a utiliser le qr code ou renseigner a la main le
    mot de passe ». Le compte nait avec sa cle ; son mot de passe, aleatoire,
    n'est connu de personne et n'ouvre rien seul (regle 1)."""
    _inscription_face_id(monkeypatch)
    c, partis, h = invitations
    c.post("/api/invitations", json={"email": "paul@example.com"}, headers=h)
    jeton = _jeton_du_lien(partis)
    c.post("/logout")
    r = c.post("/invitation/%s/passkey/options" % jeton, json={"nom": "paul"})
    assert r.status_code == 200, r.data
    assert r.get_json()["authenticatorSelection"]["userVerification"] == "required"
    # Rien n'existe avant la cle verifiee.
    assert "paul" not in app.lire_utilisateurs()
    r = c.post("/invitation/%s/passkey" % jeton, json={"credential": {}, "nom": "iPhone"})
    assert r.status_code == 200, r.data
    compte = app.lire_utilisateurs()["paul"]
    assert compte["sans_mot_de_passe"] is True and compte["email_verifie"] is True
    assert len(app.passkeys_du_compte("paul")) == 1
    assert c.get("/api/mon-compte").get_json()["sans_mot_de_passe"] is True
    # Le lien ne sert qu'une fois, par ce chemin comme par l'autre.
    c.post("/logout")
    assert c.post("/invitation/%s/passkey/options" % jeton, json={"nom": "autre"}).status_code == 404
    assert c.post("/invitation/" + jeton, json={"nom": "autre",
                  "mot_de_passe": "mot-de-passe-long"}).status_code == 404


def test_sans_defi_ni_invitation_valide_face_id_ne_cree_rien(invitations, monkeypatch):
    """La cle n'est acceptee que pour la creation demandee juste avant, sur
    le meme lien : ni un appel direct, ni un lien invente."""
    _inscription_face_id(monkeypatch)
    c, partis, h = invitations
    c.post("/api/invitations", json={"email": "paul@example.com"}, headers=h)
    jeton = _jeton_du_lien(partis)
    c.post("/logout")
    # Sans les options d'abord : aucune creation en attente.
    assert c.post("/invitation/%s/passkey" % jeton, json={"credential": {}}).status_code == 400
    assert c.post("/invitation/%s/passkey/options" % ("x" * 43),
                  json={"nom": "intrus"}).status_code == 404
    # Un nom deja pris ou reserve ne passe pas.
    assert c.post("/invitation/%s/passkey/options" % jeton,
                  json={"nom": app.NOM_ADMIN}).status_code == 400
    assert "paul" not in app.lire_utilisateurs() and not app.passkeys_du_compte("intrus")


def test_face_id_a_l_invitation_suit_les_memes_conditions_que_partout(invitations, monkeypatch):
    """En HTTP par une adresse IP, le navigateur refuserait : le serveur le
    dit plutot que d'ouvrir une creation vouee a l'echec."""
    pytest.importorskip("webauthn")
    monkeypatch.setattr(app, "passkey_contexte",
                        lambda: ("", "", "Les clés d'accès exigent une connexion HTTPS."))
    c, partis, h = invitations
    c.post("/api/invitations", json={"email": "paul@example.com"}, headers=h)
    jeton = _jeton_du_lien(partis)
    c.post("/logout")
    r = c.post("/invitation/%s/passkey/options" % jeton, json={"nom": "paul"})
    assert r.status_code == 400 and "HTTPS" in r.get_json()["error"]


def test_choisir_un_mot_de_passe_retire_la_mention_sans_mot_de_passe(monkeypatch):
    src = _source_du_panneau()
    assert src.count('compte.pop("sans_mot_de_passe", None)') == 3
    page = _lire_source("app-manager/app/login.html")
    assert "function invitationPasskey()" in page and 'id="inv-pk"' in page


def test_un_lien_d_invitation_invente_ne_cree_rien(invitations):
    c, _, _ = invitations
    c.post("/logout")
    r = c.post("/invitation/" + "x" * 43, json={"nom": "intrus",
                                                "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 404
    assert "intrus" not in app.lire_utilisateurs()




def test_une_invitation_annulee_ne_vaut_plus_rien(invitations):
    c, partis, h = invitations
    ident = c.post("/api/invitations", json={"email": "paul@example.com"},
                   headers=h).get_json()["id"]
    jeton = _jeton_du_lien(partis)
    assert c.delete("/api/invitations/" + ident, headers=h).status_code == 200
    assert c.get("/api/invitation/" + jeton).status_code == 404




def test_un_compte_verifie_recoit_son_code_par_mail_et_entre_avec(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    d = r.get_json()
    assert r.status_code == 401 and d["moyen"] == "email" and d["envoye"] is True
    assert "marie@example.com" not in json.dumps(d), "l'adresse ne doit pas s'afficher en entier"
    assert partis[-1][0] == ["marie@example.com"]
    assert c.get("/api/mes-apps").status_code == 401
    app._login_attempts.clear()
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                               "code": _code_du_dernier_mail(partis)})
    assert r.status_code == 200, r.data
    assert c.get("/api/mes-apps").status_code == 200




def test_le_code_de_connexion_ne_sert_qu_une_fois_et_exige_le_mot_de_passe(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    code = _code_du_dernier_mail(partis)
    app._login_attempts.clear()
    assert c.post("/login", json={"nom": "marie", "password": "faux",
                                  "code": code}).status_code == 401
    assert c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                                  "code": code}).status_code == 200
    c.post("/logout")
    app._login_attempts.clear()
    assert c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                                  "code": code}).status_code == 401




def test_les_mails_du_panneau_passent_par_mailpit(monkeypatch):
    remis = []
    monkeypatch.setattr(app, "MAIL_PAR_MAILPIT", True)
    monkeypatch.setattr(app, "_envoyer_par_mailpit", lambda msg: remis.append(msg))
    direct = []
    monkeypatch.setattr(app.smtplib, "SMTP", lambda *a, **k: direct.append(a))
    app.envoyer_mail({"host": "smtp.example.com", "port": 587, "tls": "starttls",
                      "user": "", "password": "", "expediteur": "moi@example.com"},
                     "sujet", "corps", destinataires=["toi@example.com"])
    assert remis and remis[0]["To"] == "toi@example.com" and not direct




def test_un_mail_de_securite_ne_passe_jamais_par_mailpit(monkeypatch):
    """Codes, liens d'invitation, alerte de connexion : ils partent en
    direct. Mailpit, que tout administrateur ouvre depuis le hub, n'en garde
    aucune copie. Les autres mails, eux, s'y relisent."""
    remis, envois = [], []
    monkeypatch.setattr(app, "MAIL_PAR_MAILPIT", True)
    monkeypatch.setattr(app, "_envoyer_par_mailpit", lambda msg: remis.append(msg))

    class FauxSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): envois.append(msg)

    monkeypatch.setattr(app.smtplib, "SMTP", FauxSMTP)
    cfg = {"host": "smtp.example.com", "port": 587, "tls": "starttls",
           "user": "", "password": "", "expediteur": "moi@example.com"}
    app.envoyer_mail(cfg, "code", "1234", destinataires=["toi@example.com"], sensible=True)
    assert envois and not remis
    # Sans serveur d'envoi, un mail de securite ne part pas -- il ne se
    # rabat pas sur Mailpit.
    with pytest.raises(RuntimeError):
        app.envoyer_mail(dict(cfg, host=""), "code", "1234",
                         destinataires=["toi@example.com"], sensible=True)
    assert not remis
    # Les quatre mails qui portent un secret sont marques sensibles.
    marques = []
    monkeypatch.setattr(app, "smtp_utilisable", lambda: (cfg, True))
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None, **kw:
                        marques.append((sujet, kw.get("sensible"))))
    app.envoyer_code_email("toi@example.com", "toi", "123456")
    app.envoyer_code_reinit("toi@example.com", "toi", "123456")
    app.envoyer_code_connexion({"email": "toi@example.com"}, "toi", "mail", "123456")
    app.envoyer_invitation("toi@example.com", "https://x/invitation/abc")
    assert len(marques) == 4 and all(sensible is True for _, sensible in marques), marques




def test_les_alertes_dagster_passent_par_mailpit():
    source = open(os.path.join(_racine_depot(), "workspace", "diagnostic", "definitions.py"),
                  encoding="utf-8").read()
    corps = source.split("def envoyer(cfg, sujet, corps):")[1].split("\ndef ")[0]
    assert "smtplib.SMTP(MAILPIT_SMTP_HOTE, MAILPIT_SMTP_PORT" in corps
    assert corps.index("MAILPIT_SMTP_HOTE") < corps.index("_login")




def test_mailpit_injoignable_l_envoi_repart_en_direct(monkeypatch):
    def refuse(msg):
        raise ConnectionRefusedError("arrete")
    monkeypatch.setattr(app, "MAIL_PAR_MAILPIT", True)
    monkeypatch.setattr(app, "_envoyer_par_mailpit", refuse)
    envois = []

    class FauxSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): envois.append(msg)

    monkeypatch.setattr(app.smtplib, "SMTP", FauxSMTP)
    app.envoyer_mail({"host": "smtp.example.com", "port": 587, "tls": "starttls",
                      "user": "", "password": "", "expediteur": "moi@example.com"},
                     "sujet", "corps", destinataires=["toi@example.com"])
    assert len(envois) == 1


# ---------- option A : la boite mail seule ne prend pas un compte ----------
#
# Le mail sert deja de second facteur (code de connexion). S'il servait AUSSI a
# reinitialiser le mot de passe, qui tient la boite tiendrait le compte. D'ou :
# reinitialisation par mail reservee aux comptes qui ont Face ID ou une
# application, et code par mail suspendu apres une reinitialisation.




def test_le_code_par_mail_passe_avant_l_application(comptes_mail):
    """Sans Face ID : le code par mail d'abord, l'application a un clic."""
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    comptes = app.lire_utilisateurs()
    comptes["marie"]["totp"] = "JBSWY3DPEHPK3PXP"
    app.ecrire_utilisateurs(comptes)
    d = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"}).get_json()
    assert d["moyens"] == ["email", "totp"] and d["moyen"] == "email" and d["envoye"]
    # Le mail porte le code en clair dans le texte, et en grand dans sa version mise en page.
    assert re.search(r": \d{6}", partis[-1][1])




def test_l_administrateur_recoit_aussi_son_code_par_mail(comptes_mail, monkeypatch, tmp_path):
    c, partis = comptes_mail
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n"
        "ALERTE_ADMIN=chef@example.com\n")
    monkeypatch.setenv("APP_MANAGER_ADMIN_2FA", "1")
    monkeypatch.setattr(app, "_totp_secret", "JBSWY3DPEHPK3PXP")
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = r.get_json()
    assert r.status_code == 401 and d["moyen"] == "email" and d["moyens"] == ["email", "totp"]
    assert d["envoye"] and partis[-1][0] == ["chef@example.com"]
    assert "chef@example.com" not in json.dumps(d)
    # Le code est au coffre, en empreinte : jamais en clair, jamais dans le cookie.
    code = _code_du_dernier_mail(partis)
    assert code not in json.dumps(app.reglage("code_connexion_admin"))
    # Un deuxieme appel dans la minute ne renvoie rien.
    nb = len(partis)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert len(partis) == nb
    # Un mauvais code est refuse, le bon ouvre la session, une seule fois.
    app._login_attempts.clear()
    faux = "000000" if code != "000000" else "111111"
    assert c.post("/login", json={"nom": "admin", "password": "secret-de-test",
                                  "code": faux}).status_code == 401
    assert c.post("/login", json={"nom": "admin", "password": "secret-de-test",
                                  "code": code}).status_code == 200
    assert c.get("/api/apps").status_code == 200
    c.post("/logout")
    app._login_attempts.clear()
    assert c.post("/login", json={"nom": "admin", "password": "secret-de-test",
                                  "code": code}).status_code == 401
    # L'application d'authentification marche toujours.
    totp = app.totp_code("JBSWY3DPEHPK3PXP", int(time.time()) // app.TOTP_PAS)
    assert c.post("/login", json={"nom": "admin", "password": "secret-de-test",
                                  "code": totp}).status_code == 200




def test_la_page_de_connexion_propose_face_id_puis_un_code_en_cases():
    login = _page_panneau("login.html")
    for trace in ('class="bouton-geste" id="pk-go"', 'id="code-cases"', 'autocomplete="one-time-code"',
                  "function remplirCases(", "function compteARebours(", 'id="pk-indispo"',
                  "Aucun code à saisir."):
        assert trace in login, trace
    # Six cases, et un seul champ porte la valeur envoyee.
    bloc = login.split('id="code-cases"', 1)[1].split("</div>", 1)[0]
    assert bloc.count("<input") == 6




def test_sans_facteur_fort_la_reinitialisation_par_mail_est_fermee(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    assert c.post("/mot-de-passe/oubli", json={"nom": "marie"}).status_code == 200
    assert partis == [], "un code de reinitialisation est parti sans facteur fort"




def test_apres_une_reinitialisation_par_mail_le_code_mail_ne_suffit_plus(comptes_mail):
    """Le scenario de la boite mail volee : mot de passe reinitialise par mail,
    puis tentative d'entrer avec le code... envoye par mail."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    assert _reinitialiser_par_mail(c, partis).status_code == 200
    app._login_attempts.clear()
    nb = len(partis)
    r = c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe",
                               "moyen": "email"})
    # Le mail n'est plus propose : il reste Face ID, a l'etape 2.
    assert r.status_code == 401 and r.get_json()["moyens"] == ["passkey"], r.data
    assert len(partis) == nb, "un code de connexion est parti par mail"
    assert c.get("/api/mes-apps").status_code == 401




def test_apres_reinitialisation_aucune_application_ne_s_inscrit_sur_le_mot_de_passe(comptes_mail):
    """Sans ce verrou, le mot de passe reinitialise suffirait a enregistrer
    une application d'authentification -- et a entrer."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    _reinitialiser_par_mail(c, partis)
    app._login_attempts.clear()
    r = c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe"})
    assert r.status_code == 401 and not r.get_json().get("inscription")
    assert r.get_json()["moyen"] == "passkey"
    assert c.get("/api/mes-apps").status_code == 401




def test_un_facteur_fort_rend_le_code_par_mail(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    comptes = app.lire_utilisateurs()
    comptes["marie"]["totp"] = "JBSWY3DPEHPK3PXP"
    app.ecrire_utilisateurs(comptes)
    _reinitialiser_par_mail(c, partis)
    assert app.lire_utilisateurs()["marie"]["code_mail_suspendu"] is True
    app._login_attempts.clear()
    d = c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe"}).get_json()
    # Le mail reste exclu ; Face ID vient apres l'application (il n'existe
    # qu'en HTTPS, derriere « Essayer une autre methode »).
    assert d["moyens"] == ["totp", "passkey"]
    code = app.totp_code("JBSWY3DPEHPK3PXP", int(time.time()) // app.TOTP_PAS)
    assert c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe",
                                  "code": code}).status_code == 200
    assert not app.lire_utilisateurs()["marie"].get("code_mail_suspendu")




def test_une_adresse_verifiee_sans_serveur_d_envoi_n_ouvre_pas_l_inscription(comptes_mail, tmp_path):
    """Serveur d'envoi en panne : le compte ne doit pas retomber sur
    l'inscription d'une application, qui n'exige que le mot de passe."""
    c, _ = comptes_mail
    _compte_avec_adresse(face_id=False)
    (tmp_path / "credentials.env").write_text("")
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    assert r.status_code == 403 and not r.get_json().get("inscription")




def test_un_compte_suspendu_n_entre_pas_par_face_id(comptes_mail, monkeypatch):
    """Le mot de passe refusait deja un compte suspendu ; la cle d'acces, non."""
    pytest.importorskip("webauthn")
    import webauthn
    c, _ = comptes_mail
    _compte_avec_adresse()
    comptes = app.lire_utilisateurs()
    comptes["marie"]["suspendu"] = True
    app.ecrire_utilisateurs(comptes)
    monkeypatch.setattr(app, "passkey_contexte",
                        lambda: ("codelab.exemple.fr", "https://codelab.exemple.fr", ""))

    class Verifiee:
        new_sign_count = 1
    monkeypatch.setattr(webauthn, "verify_authentication_response", lambda **k: Verifiee())
    with c.session_transaction() as sess:
        sess["passkey_defi"] = app.emettre_defi(b"defi")
    r = c.post("/login/passkey", json={"credential": {"id": "CLE"}})
    assert r.status_code == 403, r.data
    assert c.get("/api/mes-apps").status_code == 401




# ---------- un changement de mot de passe ferme les sessions ouvertes ----------

def test_un_nouveau_mot_de_passe_pose_par_l_admin_ferme_la_session_volee(deux_espaces):
    vol = app.flask_app.test_client()
    _connecte(vol, "marie", "mot-de-passe-long")
    assert vol.get("/api/mes-apps").status_code == 200
    time.sleep(1.1)
    comptes = app.lire_utilisateurs()
    app.fermer_les_autres_sessions(comptes["marie"])
    app.ecrire_utilisateurs(comptes)
    assert vol.get("/api/mes-apps").status_code == 401




def test_changer_son_mot_de_passe_garde_sa_session_et_ferme_les_autres(deux_espaces):
    c = deux_espaces
    autre = app.flask_app.test_client()
    _connecte(c, "marie", "mot-de-passe-long")
    _connecte(autre, "marie", "mot-de-passe-long")
    time.sleep(1.1)
    jeton = c.get("/").get_data(as_text=True)
    jeton = re.search(r"jeton: (\"[^\"]+\")", jeton).group(1).strip('"')
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "mot-de-passe-long", "nouveau": "un-tout-nouveau-mot-de-passe"},
               headers={app.JETON_ENTETE: jeton})
    assert r.status_code == 200, r.data
    assert c.get("/api/mes-apps").status_code == 200
    assert autre.get("/api/mes-apps").status_code == 401




def test_une_reinitialisation_par_mail_ferme_les_sessions_ouvertes(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    vol = app.flask_app.test_client()
    with vol.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 ouverte=int(time.time()) - 10, jeton="j")
    assert vol.get("/api/mes-apps").status_code == 200
    assert _reinitialiser_par_mail(c, partis).status_code == 200
    assert vol.get("/api/mes-apps").status_code == 401




def test_un_cookie_ancien_ne_suffit_pas_a_ajouter_une_cle_ni_changer_d_adresse(comptes_mail):
    """Une session volee ne doit pas pouvoir s'installer durablement : ajouter
    sa propre cle Face ID, ou detourner l'adresse qui recoit les codes."""
    c, _ = comptes_mail
    _compte_avec_adresse()
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 ouverte=int(time.time()) - app.AUTHENTIFICATION_RECENTE - 60, jeton="j")
    h = {app.JETON_ENTETE: "j"}
    r = c.post("/api/mon-compte/email", json={"email": "pirate@example.com"}, headers=h)
    assert r.status_code == 403 and r.get_json()["reconnexion"] is True
    assert app.lire_utilisateurs()["marie"]["email"] == "marie@example.com"
    if app.passkeys_disponibles():
        r = c.post("/api/mon-compte/passkeys/options", headers=h)
        assert r.status_code in (400, 403)




def test_juste_apres_la_connexion_l_adresse_se_change(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 ouverte=int(time.time()), jeton="j")
    r = c.post("/api/mon-compte/email", json={"email": "nouvelle@example.com"},
               headers={app.JETON_ENTETE: "j"})
    assert r.status_code == 200, r.data
    assert partis[-1][0] == ["nouvelle@example.com"]


# ---------- le compte d'administration : second facteur obligatoire ----------




def test_le_mot_de_passe_admin_seul_n_ouvre_pas_de_session(admin_2fa):
    c = admin_2fa
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert r.status_code == 200 and r.get_json()["inscription"] is True
    assert c.get("/api/apps").status_code == 401
    assert c.get("/api/auth-check").status_code == 401




def test_l_admin_enregistre_son_facteur_puis_doit_le_donner(admin_2fa):
    c = admin_2fa
    secret = c.post("/login", json={"nom": "admin", "password": "secret-de-test"}).get_json()["secret"]
    code = app.totp_code(secret, int(time.time()) // app.TOTP_PAS)
    assert c.post("/login/second-facteur", json={"code": code}).status_code == 200
    assert app._totp_secret == secret
    assert c.get("/api/apps").status_code == 200
    c.post("/logout")
    app._login_attempts.clear()
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert r.status_code == 401 and r.get_json()["totp"] is True




def test_un_admin_avec_face_id_seulement_ne_passe_pas_au_mot_de_passe(admin_2fa):
    """Sinon le mot de passe seul permettrait d'enregistrer une application,
    et de contourner Face ID."""
    app.ecrire_passkeys({app.NOM_ADMIN: [{"id": "A", "cle_publique": "AAAA",
                                          "compteur": 0, "nom": "Face ID"}]})
    c = admin_2fa
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    # Etape 2 : Face ID, ou rien. Jamais l'inscription d'une application.
    assert not r.get_json().get("inscription")
    assert r.status_code in (401, 403)
    if r.status_code == 401:
        assert r.get_json()["moyens"] == ["passkey"]
    assert c.get("/api/apps").status_code == 401




def test_premiere_connexion_admin_par_face_id_sans_rien_recopier(admin_2fa, monkeypatch):
    """Le mot de passe reconnu, Face ID s'enregistre en un geste et ouvre la
    session : aucune cle ni aucun code a recopier."""
    _inscription_face_id(monkeypatch)
    c = admin_2fa
    d = c.post("/login", json={"nom": "admin", "password": "secret-de-test"}).get_json()
    assert d["inscription"] is True and d["passkey"] is True
    assert d["uri"].startswith("otpauth://totp/")
    r = c.post("/login/inscription/passkey/options")
    assert r.status_code == 200 and r.get_json()["user"]["name"] == app.NOM_ADMIN
    r = c.post("/login/inscription/passkey", json={"credential": {"id": "x"}, "nom": "Face ID"})
    assert r.status_code == 200, r.data
    assert [k["nom"] for k in app.passkeys_du_compte(app.NOM_ADMIN)] == ["Face ID"]
    assert c.get("/api/apps").status_code == 200
    # Une seule fois : le facteur existe, la porte est refermee.
    c.post("/logout")
    with c.session_transaction() as sess:
        sess["totp_inscription"] = app.NOM_ADMIN
        sess["inscription_depuis"] = int(time.time())
    assert c.post("/login/inscription/passkey/options").status_code == 409




def test_face_id_a_l_inscription_exige_le_mot_de_passe_juste_avant(admin_2fa, monkeypatch):
    """Sans inscription en attente -- ou trop ancienne -- rien ne s'enregistre."""
    _inscription_face_id(monkeypatch)
    c = admin_2fa
    assert c.post("/login/inscription/passkey/options").status_code == 400
    assert c.post("/login/inscription/passkey", json={"credential": {}}).status_code == 400
    c.post("/login", json={"nom": "admin", "password": "faux-mot-de-passe"})
    assert c.post("/login/inscription/passkey/options").status_code == 400
    app._login_attempts.clear()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    with c.session_transaction() as sess:
        sess["inscription_depuis"] = int(time.time()) - app.INSCRIPTION_VALIDITE - 1
    assert c.post("/login/inscription/passkey/options").status_code == 400
    assert not app.passkeys_du_compte(app.NOM_ADMIN)
    assert c.get("/api/apps").status_code == 401




def test_face_id_a_l_inscription_reste_dans_le_reseau_local(admin_2fa, monkeypatch):
    _inscription_face_id(monkeypatch)
    c = admin_2fa
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    monkeypatch.setattr(app, "refus_admin_hors_reseau", lambda: "Hors du réseau local.")
    assert c.post("/login/inscription/passkey/options").status_code == 403
    assert c.post("/login/inscription/passkey", json={"credential": {}}).status_code == 403
    assert not app.passkeys_du_compte(app.NOM_ADMIN)




def test_la_page_de_connexion_propose_face_id_puis_un_lien_prerempli():
    page = _page_panneau("login.html")
    assert "/login/inscription/passkey/options" in page and "inscrirePasskey()" in page
    # Le lien otpauth:// ouvre l'application de codes avec tout deja rempli.
    assert "$$('ins-lien').href=d.uri" in page
    assert "Saisir la clé à la main" in page




def test_les_parametres_n_ont_plus_de_posture_de_securite():
    page = _page_panneau("dashboard.html")
    for trace in ("Posture de sécurité", "sec-liste", "peindrePosture",
                  "chargerPostureDuCompte", "POSTURE_"):
        assert trace not in page, trace




def test_face_id_se_desactive_d_un_geste(deux_espaces):
    """L'interrupteur eteint retire toutes les cles du compte connecte, et
    seulement les siennes."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    app.ecrire_passkeys({
        "marie": [{"id": "M1", "cle_publique": "AA", "compteur": 0, "nom": "Face ID"},
                  {"id": "M2", "cle_publique": "AA", "compteur": 0, "nom": "Touch ID"}],
        "paul": [{"id": "P1", "cle_publique": "AA", "compteur": 0, "nom": "Face ID"}]})
    r = c.delete("/api/mon-compte/passkeys")
    assert r.status_code == 200 and r.get_json()["retirees"] == 2
    assert app.passkeys_du_compte("marie") == []
    assert [k["id"] for k in app.passkeys_du_compte("paul")] == ["P1"]
    assert app.flask_app.test_client().delete("/api/mon-compte/passkeys").status_code == 401




def test_les_parametres_ont_un_interrupteur_face_id():
    page = _page_panneau("dashboard.html")
    assert 'id="pk-bascule" data-changer="pkBasculer"' in page
    assert "pkBasculer(){ pkBasculer(this); }" in page
    assert "fetch('/api/mon-compte/passkeys',{method:'DELETE'})" in page
    # Plus de champ « Nom de la cle » a remplir : le nom suit l'appareil.
    assert 'placeholder="Téléphone"' not in page




def test_la_liste_des_comptes_n_a_plus_de_bouton_modifier():
    page = _page_panneau("dashboard.html")
    ligne = page.split("function usLigne(c){", 1)[1].split("\nfunction ", 1)[0]
    assert ">Modifier</button>" not in ligne
    assert 'data-agir="compte-ouvrir"' in ligne and 'role="button"' in ligne




def test_la_marche_arriere_du_compose_rouvre_le_mot_de_passe(admin_2fa, monkeypatch):
    monkeypatch.setenv("APP_MANAGER_ADMIN_2FA", "0")
    assert admin_2fa.post("/login", json={"nom": "admin", "password": "secret-de-test"}).status_code == 200




def test_une_connexion_admin_depuis_une_adresse_inconnue_est_signalee(journal, monkeypatch):
    envois = []
    monkeypatch.setattr(app, "alertes_admin", lambda: ["moi@example.com"])
    monkeypatch.setattr(app, "envoyer_avec_repli",
                        lambda sujet, corps, dest, **kw: envois.append((sujet, dest, kw)))

    class ThreadImmediat:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.t, self.a, self.k = target, args, kwargs or {}
        def start(self):
            self.t(*self.a, **self.k)
    monkeypatch.setattr(app.threading, "Thread", ThreadImmediat)
    # L'adresse est celle de la connexion (jamais X-Forwarded-For).
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"},
           environ_base={"REMOTE_ADDR": "192.168.1.10"})       # l'installation
    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"},
           environ_base={"REMOTE_ADDR": "192.168.1.10"})       # deja vue
    assert envois == []
    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"},
           environ_base={"REMOTE_ADDR": "192.168.1.77"})       # nouvelle
    assert len(envois) == 1 and envois[0][1] == ["moi@example.com"]
    # Une alerte de securite : jamais par Mailpit.
    assert envois[0][2] == {"sensible": True}




# ---------- connexion en deux etapes ----------

def test_l_identifiant_admin_est_obligatoire(journal):
    c = journal
    r = c.post("/login", json={"password": "secret-de-test"})
    assert r.status_code == 400
    assert c.get("/api/apps").status_code == 401
    page = _page_panneau("login.html")
    assert 'placeholder="admin"' not in page
    assert "Laisse le nom vide pour te connecter" not in page
    assert "cat /DATA/AppData/codelab/config/credentials.env" not in page




def test_le_second_facteur_par_face_id_vient_apres_le_mot_de_passe(admin_2fa, monkeypatch):
    """Etape 1 : le mot de passe, sans code a cote. Etape 2 : Face ID, lie
    au mot de passe qui vient d'etre reconnu dans ce navigateur."""
    _facteur_cle(monkeypatch)
    app.ecrire_passkeys({app.NOM_ADMIN: [{"id": "QUJD", "cle_publique": "AAAA",
                                          "compteur": 0, "nom": "Face ID"}]})
    c = admin_2fa
    # Sans mot de passe juste, pas d'etape 2.
    assert c.post("/login/second-facteur/passkey/options").status_code == 400
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert r.status_code == 401 and r.get_json()["moyen"] == "passkey"
    assert c.get("/api/apps").status_code == 401
    assert c.post("/login/second-facteur/passkey/options").status_code == 200
    r = c.post("/login/second-facteur/passkey", json={"credential": {"id": "QUJD"}})
    assert r.status_code == 200, r.data
    assert c.get("/api/apps").status_code == 200




def test_la_cle_d_un_autre_compte_ne_confirme_rien(admin_2fa, monkeypatch):
    _facteur_cle(monkeypatch)
    app.ecrire_passkeys({app.NOM_ADMIN: [{"id": "QUJD", "cle_publique": "AAAA", "compteur": 0}],
                         "marie": [{"id": "TUFS", "cle_publique": "AAAA", "compteur": 0}]})
    c = admin_2fa
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    c.post("/login/second-facteur/passkey/options")
    r = c.post("/login/second-facteur/passkey", json={"credential": {"id": "TUFS"}})
    assert r.status_code == 401
    assert c.get("/api/apps").status_code == 401




def test_l_etape_2_expire(admin_2fa, monkeypatch):
    _facteur_cle(monkeypatch)
    app.ecrire_passkeys({app.NOM_ADMIN: [{"id": "QUJD", "cle_publique": "AAAA", "compteur": 0}]})
    c = admin_2fa
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    with c.session_transaction() as sess:
        attente = dict(sess["facteur_attente"])
        attente["depuis"] -= app.SECOND_FACTEUR_VALIDITE + 1
        sess["facteur_attente"] = attente
    assert c.post("/login/second-facteur/passkey/options").status_code == 400




def test_la_page_de_connexion_separe_les_deux_etapes():
    page = _page_panneau("login.html")
    # Plus de champ de code pose sous le mot de passe : ni la fonction qui
    # le montrait, ni le marqueur que le serveur remplissait pour elle.
    assert "majChamps" not in page and "__TOTP__" not in page
    assert "/login/second-facteur/passkey/options" in page
    assert 'id="facteur-pk"' in page and 'id="code-champ"' in page




def test_une_application_ne_prend_pas_le_nom_d_une_route_du_panneau(deux_espaces, tmp_path):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    dossier = tmp_path / "w"
    dossier.mkdir()
    for nom in ("invitation", "parametres", "qr"):
        r = c.post("/api/add", json={"name": nom, "path": str(dossier), "command": "x"})
        assert r.status_code == 400 and "réservée" in r.get_json()["error"], (nom, r.data)




def test_les_sous_taches_se_priorisent(boite):
    """Glisser une sous-tache change son rang ; la liste envoyee doit nommer
    exactement les sous-taches de la tache."""
    c, _ = boite
    r = c.post("/api/taches", json={"projet": "general", "titre": "T",
                                    "sous_taches": ["a", "b", "c"]})
    tache = r.get_json()["id"]
    (t,) = c.get("/api/taches").get_json()["taches"]
    a, b, cc = [st["id"] for st in t["sous_taches"]]
    assert c.put(f"/api/taches/{tache}/ordre", json={"ordre": [cc, a, b]}).status_code == 200
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert [st["titre"] for st in t["sous_taches"]] == ["c", "a", "b"]
    # Une liste incomplete, etrangere ou mal formee est refusee.
    assert c.put(f"/api/taches/{tache}/ordre", json={"ordre": [a, b]}).status_code == 409
    assert c.put(f"/api/taches/{tache}/ordre", json={"ordre": [a, b, "x"]}).status_code == 409
    assert c.put(f"/api/taches/{tache}/ordre", json={"ordre": "abc"}).status_code == 400
    assert c.put("/api/taches/inconnue/ordre", json={"ordre": []}).status_code == 404
    page = _page_panneau(PAGE_MESSAGES)
    assert "/ordre','PUT'" in page and 'data-agir="sous-monter"' in page


# ------------------ se connecter avec CodeLab, puis revenir ------------------
#
# La connexion silencieuse : une personne connectee a CodeLab entre dans ses
# applications sans formulaire ; apres une connexion, elle revient la ou
# elle etait ; la deconnexion depuis une application demande confirmation.




def test_le_jeton_dit_si_l_adresse_est_confirmee(monkeypatch):
    _cle_sso(monkeypatch)
    import base64, json as _json

    def charge(j):
        p = j.split(".")[1]
        return _json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    oui = charge(_jeton("site", email="alice@example.com", verifie=True, monkeypatch=monkeypatch))
    non = charge(_jeton("site", email="alice@example.com", verifie=False, monkeypatch=monkeypatch))
    assert oui["email"] == "alice@example.com" and oui["email_verified"] is True
    assert non["email_verified"] is False
    assert "email_verified" not in charge(_jeton("site", monkeypatch=monkeypatch))




@pytest.mark.parametrize("sans_cryptography", [False, True])
def test_le_module_python_verifie_le_jeton(monkeypatch, sans_cryptography):
    """Avec cryptography, et sans : l'implementation de reference incluse
    doit accepter exactement ce que le panneau signe, et rien d'autre."""
    _cle_sso(monkeypatch)
    m = _module_auth()
    if sans_cryptography:
        monkeypatch.setattr(m, "_verifier", m._ed25519_verifier)
    monkeypatch.setenv("CODELAB_SSO_PUBLIC_KEY", app._sso_public_key)
    monkeypatch.setenv("CODELAB_SSO_ISSUER", app.SSO_ISSUER)
    monkeypatch.setenv("CODELAB_SSO_AUDIENCE", "site")
    jeton = _jeton("site", email="a@x.fr", verifie=True, monkeypatch=monkeypatch)
    u = m.utilisateur({"X-CodeLab-Auth": jeton})
    assert u == {"sub": "alice", "nom": "alice", "role": app.ROLE_UTILISATEUR,
                 "email": "a@x.fr", "email_verified": True}
    # En-tete en minuscules (WSGI, Starlette...) : meme resultat.
    assert m.utilisateur({"x-codelab-auth": jeton}) == u
    # Un octet de la charge change : signature invalide.
    h, p, s = jeton.split(".")
    altere = h + "." + p[:-2] + ("A" if p[-2] != "A" else "B") + p[-1] + "." + s
    assert m.utilisateur({"X-CodeLab-Auth": altere}) is None
    # Jeton d'une AUTRE application : refuse.
    assert m.utilisateur({"X-CodeLab-Auth": _jeton("autre", monkeypatch=monkeypatch)}) is None
    # Sans jeton, ou sans cle : personne.
    assert m.utilisateur({}) is None
    monkeypatch.setenv("CODELAB_SSO_PUBLIC_KEY", "")
    assert m.utilisateur({"X-CodeLab-Auth": jeton}) is None




def test_le_module_python_refuse_un_jeton_expire(monkeypatch):
    _cle_sso(monkeypatch)
    m = _module_auth()
    monkeypatch.setenv("CODELAB_SSO_PUBLIC_KEY", app._sso_public_key)
    monkeypatch.setenv("CODELAB_SSO_AUDIENCE", "site")
    jeton = _jeton("site", monkeypatch=monkeypatch)
    monkeypatch.setattr(m.time, "time", lambda: 10 ** 12)
    assert m.utilisateur({"X-CodeLab-Auth": jeton}) is None




def test_la_connexion_silencieuse_et_la_pause_apres_deconnexion(monkeypatch):
    _cle_sso(monkeypatch)
    m = _module_auth()
    monkeypatch.setenv("CODELAB_SSO_PUBLIC_KEY", app._sso_public_key)
    monkeypatch.setenv("CODELAB_SSO_AUDIENCE", "site")
    entetes = {"X-CodeLab-Auth": _jeton("site", monkeypatch=monkeypatch)}
    appels = []

    def trouver_ou_creer(u):
        appels.append(u["sub"])
        return 42
    session = {}
    assert m.connexion_silencieuse(session, entetes, trouver_ou_creer) == 42
    assert session["utilisateur"] == 42
    # Session deja ouverte : plus de verification, plus d'appel.
    assert m.connexion_silencieuse(session, {}, trouver_ou_creer) == 42
    assert appels == ["alice"]
    # Deconnexion de l'application : CodeLab ne reconnecte PAS aussitot.
    m.deconnecter(session)
    assert m.connexion_silencieuse(session, entetes, trouver_ou_creer) is None
    m.reprendre(session)
    assert m.connexion_silencieuse(session, entetes, trouver_ou_creer) == 42
    # Visiteur sans CodeLab : rien, l'application montre son formulaire.
    assert m.connexion_silencieuse({}, {}, trouver_ou_creer) is None




def test_les_liens_du_module_passent_par_le_panneau(monkeypatch):
    m = _module_auth()
    monkeypatch.setenv("CODELAB_SSO_AUDIENCE", "site")
    entetes = {"X-CodeLab-Panneau": "http://serveur:9001/"}
    assert (m.lien_connexion(entetes, "/factures/12")
            == "http://serveur:9001/login?suite=/ouvrir.app/site/factures/12")
    assert (m.lien_deconnexion(entetes, "/")
            == "http://serveur:9001/deconnexion.html?app=site&chemin=")
    # Un en-tete douteux ne fait pas un lien vers ailleurs.
    assert m.lien_connexion({"X-CodeLab-Panneau": "javascript:alert(1)"}).startswith("/login")




def test_le_module_node_verifie_le_jeton(monkeypatch, tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node absent")
    _cle_sso(monkeypatch)
    jeton = _jeton("site", email="a@x.fr", verifie=True, monkeypatch=monkeypatch)
    module = os.path.join(_racine_depot(), "dev", "codelab-auth.js")
    script = tmp_path / "t.js"
    script.write_text(
        "const m = require(" + json.dumps(module) + ");\n"
        "const req = (h) => ({ headers: h });\n"
        "const j = process.argv[2];\n"
        "const ok = m.utilisateur(req({'x-codelab-auth': j}));\n"
        "const autre = m.utilisateur(req({'x-codelab-auth': j.slice(0, -4) + 'AAAA'}));\n"
        "const lien = m.lienConnexion(req({'x-codelab-panneau': 'http://serveur:9001/'}), '/a/b');\n"
        "const s = {}; m.deconnecter(s);\n"
        "console.log(JSON.stringify({ ok, autre, lien, s }));\n")
    env = dict(os.environ, CODELAB_SSO_PUBLIC_KEY=app._sso_public_key,
               CODELAB_SSO_ISSUER=app.SSO_ISSUER, CODELAB_SSO_AUDIENCE="site")
    r = subprocess.run([node, str(script), jeton], capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["ok"] == {"sub": "alice", "nom": "alice", "role": app.ROLE_UTILISATEUR,
                       "email": "a@x.fr", "email_verified": True}
    assert d["autre"] is None
    assert d["lien"] == "http://serveur:9001/login?suite=/ouvrir.app/site/a/b"
    assert d["s"] == {"codelab_pause": True}




def test_apres_la_connexion_on_revient_dans_l_application(deux_origines):
    c = deux_origines
    # Visiteur anonyme sur une application privee : connexion, AVEC la suite.
    r = c.get("/prive-autorise/factures/12", **_sur_port(9302))
    assert r.status_code == 302
    assert r.headers["Location"] == (
        "http://serveur:9001/login?suite=/ouvrir.app/prive-autorise/factures/12")
    # /ouvrir.app exige une session.
    r = c.get("/ouvrir.app/prive-autorise/factures/12", **_sur_port(9301))
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    _session_marie(c)
    r = c.get("/ouvrir.app/prive-autorise/factures/12", **_sur_port(9301))
    assert r.status_code == 302
    assert r.headers["Location"] == "http://serveur:9302/prive-autorise/factures/12"
    # Deja connecte, la page de connexion renvoie droit a la suite...
    r = c.get("/login?suite=/ouvrir.app/public/", **_sur_port(9301))
    assert r.headers["Location"].endswith("/ouvrir.app/public/")
    # ... mais jamais vers ailleurs.
    for mauvaise in ("//evil.example/x", "https://evil.example/", "/ouvrir.app/../x"):
        r = c.get("/login", query_string={"suite": mauvaise}, **_sur_port(9301))
        assert r.headers["Location"] in ("/", "http://serveur:9301/"), mauvaise
    # Une application inconnue : 404, pas une redirection.
    assert c.get("/ouvrir.app/inconnue/", **_sur_port(9301)).status_code == 404
    # Un chemin qui remonte retombe a la racine de l'application.
    r = c.get("/ouvrir.app/public/a/%2e%2e/%2e%2e/x", **_sur_port(9301))
    assert r.headers["Location"] in ("http://serveur:9302/public/",
                                     "http://serveur:9302/public/a/x"), r.headers["Location"]
    assert ".." not in r.headers["Location"]




def test_la_deconnexion_depuis_une_application_demande_confirmation(deux_origines):
    c = deux_origines
    r = c.get("/deconnexion.html?app=public&chemin=a", **_sur_port(9301))
    assert r.status_code == 200 and "pas connect" in r.get_data(as_text=True)
    _session_marie(c)
    page = c.get("/deconnexion.html?app=public&chemin=a", **_sur_port(9301)).get_data(as_text=True)
    assert "Se déconnecter" in page and "X-CodeLab-Jeton" in page
    assert '"http://serveur:9302/public/a"' in page
    # La page seule ne deconnecte pas : la session tient toujours.
    with c.session_transaction(**_sur_port(9301)) as s:
        assert s.get("authed")
    # Une application inconnue ne fait pas une cible : retour au panneau.
    page = c.get("/deconnexion.html?app=evil", **_sur_port(9301)).get_data(as_text=True)
    assert '"/"' in page and "evil" not in page.split("<script nonce=")[1]




def test_le_proxy_pose_l_adresse_du_panneau_et_jette_les_en_tetes_du_client(deux_origines, monkeypatch):
    c = deux_origines
    vus = {}

    def amont(port, methode, chemin, entetes, corps):
        vus.update({k.lower(): v for k, v in entetes.items()})
        return 200, [("Content-Type", "text/plain")], b"ok"
    monkeypatch.setattr(app, "demander_a_l_application", amont)
    r = c.get("/public/", headers={"X-CodeLab-Panneau": "http://evil/",
                                   "X-CodeLab-Nouveau": "faux", "X-CodeLab-User": "admin"},
              **_sur_port(9302))
    assert r.status_code == 200
    assert vus["x-codelab-panneau"] == "http://serveur:9001/"
    assert "x-codelab-nouveau" not in vus and "x-codelab-user" not in vus


# ------------------------ Les appareils connectes ------------------------
#
# Vecu : la liste d'ameliorations -- « Liste des appareils et bouton
# Deconnecter les autres ». Une session etait un cookie signe : le serveur
# ne pouvait ni la lister, ni la rappeler sans changer le mot de passe.




def test_chaque_session_est_un_appareil_de_la_liste(appareils):
    tel = _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    liste = pc.get("/api/mon-compte/appareils").get_json()["appareils"]
    assert sorted(a["appareil"] for a in liste) == ["Chrome sur Mac", "Safari sur iPhone"]
    assert [a["appareil"] for a in liste if a["courant"]] == ["Chrome sur Mac"]
    # Jamais l'identifiant de session lui-meme.
    sids = {r["sid"] for r in app.stockage.lire(app.DB_FILE, "SELECT sid FROM appareils")}
    assert not any(a["id"] in sids for a in liste)
    assert tel.get("/api/mon-compte/appareils").status_code == 200




def test_deconnecter_les_autres_ferme_le_telephone_perdu(appareils):
    tel = _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    d = pc.post("/api/mon-compte/appareils/deconnecter-autres").get_json()
    assert d["deconnectes"] == 1
    assert tel.get("/api/mes-apps").status_code == 401      # tout de suite
    assert pc.get("/api/mes-apps").status_code == 200       # celui-ci reste
    assert len(pc.get("/api/mon-compte/appareils").get_json()["appareils"]) == 1




def test_deconnecter_un_appareil_par_son_identifiant(appareils):
    tel = _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    autre = [a for a in pc.get("/api/mon-compte/appareils").get_json()["appareils"]
             if not a["courant"]][0]
    assert pc.delete(f"/api/mon-compte/appareils/{autre['id']}").status_code == 200
    assert tel.get("/api/mes-apps").status_code == 401
    assert pc.delete(f"/api/mon-compte/appareils/{autre['id']}").status_code == 404




def test_un_appareil_retire_tombe_meme_apres_un_redemarrage(appareils, monkeypatch):
    """Le registre fait foi : sans la memoire du processus (redemarrage),
    une ligne retiree de la base deconnecte l'appareil au controle suivant."""
    tel = _marie_sur(IPHONE)
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("DELETE FROM appareils")
    app._sid_controles.clear()          # le panneau redemarre
    monkeypatch.setattr(app, "APPAREIL_CONTROLE", 0)
    assert tel.get("/api/mes-apps").status_code == 401




def test_se_deconnecter_retire_l_appareil(appareils):
    tel = _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    tel.post("/logout")
    assert [a["appareil"] for a in pc.get("/api/mon-compte/appareils").get_json()["appareils"]] \
        == ["Chrome sur Mac"]




def test_l_administrateur_deconnecte_un_compte_partout(appareils):
    tel = _marie_sur(IPHONE)
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    liste = admin.get("/api/utilisateurs/marie/appareils").get_json()["appareils"]
    assert [a["appareil"] for a in liste] == ["Safari sur iPhone"]
    assert admin.delete("/api/utilisateurs/marie/appareils").get_json()["deconnectes"] == 1
    assert tel.get("/api/mes-apps").status_code == 401
    assert admin.get("/api/utilisateurs/personne/appareils").status_code == 404
    # Et un compte utilisateur n'a pas cette porte.
    autre = _marie_sur(MAC)
    assert autre.get("/api/utilisateurs/marie/appareils").status_code in (401, 403)
    assert autre.delete("/api/utilisateurs/marie/appareils").status_code in (401, 403)




def test_un_appareil_est_une_ligne_quelles_que_soient_ses_sessions(appareils):
    """Vecu : « La liste des appareils connectes recense plusieurs sessions
    differentes terminees sur un meme appareil. Raisonne en appareil et pas
    en session ». Une nouvelle session sur le meme navigateur remplace la
    precedente, qui est deconnectee ; un autre navigateur reste a part."""
    tel = _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    jar = tel.get_cookie(app.COOKIE_APPAREIL)
    assert jar is not None and jar.http_only and len(jar.value) == 32
    # Le telephone se reconnecte (cookie de session perdu, meme appareil),
    # trois fois.
    for _ in range(3):
        tel.delete_cookie(app.flask_app.config.get("SESSION_COOKIE_NAME") or "session")
        _connecte(tel, "marie", "mot-de-passe-long")
        assert tel.get("/api/mes-apps").status_code == 200
    assert tel.get_cookie(app.COOKIE_APPAREIL).value == jar.value   # le meme appareil
    liste = pc.get("/api/mon-compte/appareils").get_json()["appareils"]
    assert sorted(a["appareil"] for a in liste) == ["Chrome sur Mac", "Safari sur iPhone"]
    lignes = app.stockage.lire(app.DB_FILE, "SELECT * FROM appareils WHERE compte = 'marie'")
    assert len(lignes) == 2


def test_une_session_terminee_quitte_la_liste(appareils):
    """Sans requete depuis la duree d'inactivite du cookie, la session est
    terminee : le navigateur a jete son cookie. Elle ne se montre plus ; sa
    ligne reste, pour que ses notifications suivent jusqu'a la limite."""
    _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    vieux = int(time.time()) - 4 * 86400
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("UPDATE appareils SET vue = ? WHERE agent LIKE '%iPhone%'", (vieux,))
        # Une ligne d'avant la migration 9 (sans appareil), en double.
        cx.execute("INSERT INTO appareils (sid, compte, ouverte, vue, ip, agent, appareil) "
                   "SELECT sid || 'b', compte, ouverte, vue, ip, agent, '' FROM appareils "
                   "WHERE agent LIKE '%Mac%'")
    liste = pc.get("/api/mon-compte/appareils").get_json()["appareils"]
    assert [a["appareil"] for a in liste] == ["Chrome sur Mac"]
    # La ligne du telephone reste, pour ses notifications.
    assert app.stockage.lire(app.DB_FILE, "SELECT sid FROM appareils WHERE compte = 'marie' "
                                          "AND agent LIKE '%iPhone%'")


def test_l_identifiant_d_appareil_ne_va_jamais_a_une_application():
    """Il suivrait le visiteur d'une application a l'autre (regle 19)."""
    brut = f"a=1; {app.COOKIE_APPAREIL}={'0' * 32}; b=2"
    assert app.strip_session_cookie(brut) == "a=1; b=2"


def test_la_migration_9_garde_les_appareils_existants(tmp_path):
    """Ajoutee a la fin, jouee sur une base de la version 8 : les lignes
    existantes restent, avec un appareil vide."""
    import sqlite3
    chemin = str(tmp_path / "v8.db")
    cx = sqlite3.connect(chemin)
    for script in app.stockage.MIGRATIONS[:8]:
        for instruction in app.stockage._instructions(script):
            cx.execute(instruction)
    cx.execute("PRAGMA user_version = 8")
    cx.execute("INSERT INTO appareils (sid, compte, ouverte, vue) VALUES ('s', 'marie', 1, 2)")
    cx.commit()
    cx.close()
    (ligne,) = app.stockage.lire(chemin, "SELECT * FROM appareils")
    assert ligne["sid"] == "s" and ligne["appareil"] == ""
    assert app.stockage.lire(chemin, "PRAGMA user_version")[0][0] == app.stockage.VERSION




def test_le_registre_survit_aux_comptes_et_aux_mots_de_passe(appareils):
    """Changer de mot de passe fermait deja les autres sessions : la liste
    le reflete aussitot."""
    tel = _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    r = pc.post("/api/compte/mot-de-passe", json={"ancien": "mot-de-passe-long",
                                                  "nouveau": "un-tout-nouveau-mot-de-passe"})
    assert r.status_code == 200, r.data
    liste = pc.get("/api/mon-compte/appareils").get_json()["appareils"]
    assert [a["appareil"] for a in liste] == ["Chrome sur Mac"]
    assert tel.get("/api/mes-apps").status_code == 401




@pytest.mark.parametrize("route", ["/api/mon-compte/passkeys", "/api/mon-compte/passkeys/k-vole"])
def test_retirer_ses_cles_ferme_les_autres_sessions(appareils, route):
    """Regle 7, trouvee cassee a l'audit de securite : celui qui soupconne un
    vol de session retire ses cles d'acces -- et le cookie vole restait
    valable, la seconde session repondait encore 200."""
    tel = _marie_sur(IPHONE)
    pc = _marie_sur(MAC)
    app.ecrire_passkeys({"marie": [{"id": "k-vole", "cle_publique": "x", "nom": "iPhone"}]})
    r = pc.delete(route)
    assert r.status_code == 200, r.data
    assert tel.get("/api/mes-apps").status_code == 401
    assert pc.get("/api/mes-apps").status_code == 200
    assert [a["appareil"] for a in pc.get("/api/mon-compte/appareils").get_json()["appareils"]] \
        == ["Chrome sur Mac"]




@pytest.mark.parametrize("agent,attendu", [
    (IPHONE, "Safari sur iPhone"), (MAC, "Chrome sur Mac"),
    ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0", "Edge sur Windows"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0", "Firefox sur Linux"),
    ("Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36", "Chrome sur Android"),
    ("", "Navigateur"),
])
def test_un_appareil_se_lit_en_clair(agent, attendu):
    assert app.decrire_agent(agent) == attendu




def test_la_securite_montre_les_appareils():
    page = _page_panneau("dashboard.html")
    securite = page.split('<div class="onglet-p" id="param-securite">')[1].split('<div class="onglet-p"')[0]
    assert 'id="ap-liste"' in securite and "Déconnecter les autres appareils" in securite
    assert "chargerAppareils();" in page.split("if(o==='securite'){")[1].split("}")[0]
    fiche = page.split('<div class="ov" id="ov-user">')[1].split('<div class="ov"')[0]
    assert "Déconnecter tous ses appareils" in fiche




# ------------------------- Connexion et reglages -------------------------

def test_le_refus_de_connexion_dit_combien_d_essais_il_reste(deux_espaces):
    c = deux_espaces
    messages = [c.post("/login", json={"nom": "marie", "password": "faux"}).get_json()["error"]
                for _ in range(app.RATE_LIMIT_MAX)]
    assert messages[0] == "Identifiants incorrects."
    assert "Encore 2 essais" in messages[2]
    assert "Encore 1 essai " in messages[3]
    assert "Trop d'essais" in messages[4]




def test_les_reglages_ont_un_onglet_par_categorie_pour_tout_le_monde():
    """Vecu : « Met un systeme d'onglet pour chaque categorie de parametre. »
    La longue page a sections des comptes utilisateurs est remplacee par les
    memes onglets que ceux de l'administrateur."""
    page = _page_panneau("dashboard.html")
    onglets = page.split('<div class="fiche-onglets" id="param-onglets">')[1].split("</div>")[0]
    assert re.findall(r'data-p="([a-z]+)"', onglets) == ["compte", "apparence", "hub", "securite",
                                                         "email", "aide"]
    def onglet(nom):
        return page.split(f'<div class="onglet-p" id="param-{nom}">')[1].split('<div class="onglet-p"')[0]
    for nom, zones in (("compte", ("profil-mail", "zone-version", "zone-session")),
                       ("apparence", ("zone-apparence", "zone-notifications", "zone-installer")),
                       ("hub", ("zone-hub", "zone-categories")),
                       ("securite", ("profil-mdp", "zone-appareils", "zone-cles"))):
        for z in zones:
            assert f'id="{z}"' in onglet(nom), (nom, z)
    # Un compte utilisateur a ses quatre onglets, et rien d'autre.
    assert "const ONGLETS_UTILISATEUR=['compte','apparence','hub','securite'];" in page
    assert "classList.add('reglages-unifies')" not in page and "reglages-sommaire" not in page
    # Le mot de passe vit avec ce qui protege le compte.
    assert 'id="profil-mdp"' in onglet("securite")



def test_un_apercu_encadre_ne_previent_pas_l_administrateur(deux_espaces, monkeypatch):
    """Le widget « Apercu » encadre l'application : l'afficher n'est pas
    l'ouvrir, et ne doit ni journaliser ni envoyer de mail."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    monkeypatch.setattr(app, "is_running", lambda n: False)
    appels = []
    monkeypatch.setattr(app, "prevenir_admin_inaccessible", lambda *a: appels.append(a) or True)
    r = c.get("/prive-autorise/", headers={"Sec-Fetch-Dest": "iframe"})
    assert r.status_code == 503 and appels == []
    c.get("/prive-autorise/", headers={"Sec-Fetch-Dest": "document"})
    assert len(appels) == 1


# ---------------------- supprimer son propre compte ----------------------
#
# Vecu : « Le bouton deconnexion doit etre toujours present, dans la meme
# zone, proposer de supprimer le compte, avec suppression des donnees au bout
# de 30 jours si pas de changement d'avis (bien sur, l'admin n'a pas le droit
# de supprimer ce compte) ».

def _demander_la_suppression(c, mdp="mot-de-passe-long"):
    return c.post("/api/mon-compte/suppression", json={"mot_de_passe": mdp})


def test_supprimer_son_compte_exige_le_mot_de_passe_et_ferme_toutes_les_sessions(deux_espaces):
    """Une session volee ne doit pas pouvoir effacer le compte : le mot de
    passe est exige. Accepte, la demande ferme le compte tout de suite, sur
    chaque appareil, et n'efface encore rien."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    autre = app.flask_app.test_client()
    _connecte(autre, "marie", "mot-de-passe-long")
    # Les sessions se comparent a la seconde pres (sessions_depuis) : celles
    # qu'on veut voir tomber sont ouvertes avant.
    time.sleep(1.05)
    r = _demander_la_suppression(c, "pas-le-bon")
    assert r.status_code == 403 and "suppression_demandee" not in app.lire_utilisateurs()["marie"]
    avant = int(time.time())
    r = _demander_la_suppression(c)
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert avant + 30 * 86400 <= d["le"] <= int(time.time()) + 30 * 86400 and d["date"]
    compte = app.lire_utilisateurs()["marie"]
    assert compte["projets"] == ["prive-autorise"], "rien n'est efface pendant le delai"
    for client in (c, autre):
        assert client.get("/api/mon-compte").status_code == 401


def test_pendant_le_delai_le_compte_ne_voit_que_sa_page_de_suppression(deux_espaces):
    """Reconnecte avant la date, le compte voit la date et deux boutons ; le
    panneau, son API et ses projets restent fermes, cote serveur. Annuler
    rend le compte tel qu'il etait."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert _demander_la_suppression(c).status_code == 200
    _connecte(c, "marie", "mot-de-passe-long")
    for chemin in ("/", "/parametres", "/messages"):
        page = c.get(chemin).get_data(as_text=True)
        assert "Annuler la suppression" in page and "Me déconnecter" in page, chemin
        assert "__DATE__" not in page and "__JETON__" not in page
    assert c.get("/api/mes-apps").status_code == 403
    assert c.post("/api/messages", json={"texte": "bonjour"}).status_code == 403
    with app.flask_app.test_request_context("/"):
        from flask import session
        session.update(authed=True, role="utilisateur", utilisateur="marie")
        assert app.projets_autorises() == set()
    # Changer d'avis : tout revient.
    assert c.delete("/api/mon-compte/suppression").status_code == 200
    assert "suppression_demandee" not in app.lire_utilisateurs()["marie"]
    assert c.get("/api/mes-apps").status_code == 200
    assert "Annuler la suppression" not in c.get("/").get_data(as_text=True)


def test_le_compte_d_administration_ne_se_supprime_pas(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = _demander_la_suppression(c, "secret-de-test")
    assert r.status_code == 403
    assert "administration" in r.get_json()["error"]
    page = _page_panneau("dashboard.html")
    session_ = page.split('id="zone-session"')[1].split('<div class="zone"')[0]
    # Toujours la, jamais repliee, et la suppression pour un compte
    # utilisateur seulement.
    assert "settings-card sans-pli" in session_ and 'data-agir="doLogout"' in session_
    assert 'class="btn btn-default utilisateur-seul" id="compte-supprimer"' in session_
    assert "#zone-session .utilisateur-seul{display:none}" in page
    assert "confirm(" not in page.split("function confirmerSuppressionCompte")[1].split("\n}")[0]


def test_trente_jours_apres_le_compte_et_ses_donnees_sont_effaces(deux_espaces):
    """Le delai passe, la purge quotidienne efface la fiche, l'adresse, les
    cles d'acces, les messages et le journal du compte. Avant, elle n'y
    touche pas."""
    c = deux_espaces
    comptes = app.lire_utilisateurs()
    comptes["marie"]["email"] = "marie@example.com"
    demande = int(time.time()) - 10
    comptes["marie"]["suppression_demandee"] = demande
    app.ecrire_utilisateurs(comptes)
    app.ecrire_passkeys({"marie": [{"id": "k1", "cle_publique": "x"}]})
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("INSERT INTO masquees (compte, application) VALUES ('marie', 'public')")
        cx.execute("INSERT INTO preferences (compte, cle, valeur) VALUES ('marie', 'k', 'v')")
    app.journaliser("connexion", qui="marie", ip="1.2.3.4")
    assert app.purger_les_comptes_supprimes(demande + 29 * 86400) == []
    assert "marie" in app.lire_utilisateurs()
    assert app.purger_les_comptes_supprimes(demande + 30 * 86400) == ["marie"]
    assert "marie" not in app.lire_utilisateurs()
    assert "marie" not in app.lire_passkeys()
    assert "marie" not in (app.reglage("emails_comptes", {}) or {})
    for table, colonne in (("masquees", "compte"), ("preferences", "compte"), ("journal", "qui")):
        assert not app.stockage.lire(app.DB_FILE, f"SELECT 1 FROM {table} WHERE {colonne} = 'marie'"), table
    # Une trace reste : qu'un compte a ete efface, a sa demande.
    assert app.stockage.lire(app.DB_FILE, "SELECT 1 FROM journal WHERE genre = 'compte'")


# ----------------------------- le profil d'un compte -----------------------------

def test_chaque_compte_regle_son_prenom_et_son_nom(deux_espaces):
    """Vecu : « Rend parametrable le nom (facultatif) et prenom du compte ».
    Le prenom remplace l'identifiant la ou la page parle a la personne ;
    l'identifiant reste celui de la connexion. Le texte est nettoye : court,
    sans caractere de controle."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.put("/api/mon-compte/profil", json={"prenom": "  Marie\u0007 ", "nom": "x" * 80})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["prenom"] == "Marie" and d["nom"] == "x" * app.PROFIL_MAX
    assert c.get("/api/mon-compte").get_json()["profil"]["prenom"] == "Marie"
    page = _page_servie(c)
    assert '"prenom": "Marie"' in page.split("window.CODELAB_PAGE = {")[1].split("</script>")[0]
    # Le nom est facultatif, et rien ne se perd a le vider.
    assert c.put("/api/mon-compte/profil", json={"prenom": "Marie", "nom": ""}).get_json()["nom"] == ""
    # L'administrateur garde son nom (« Pas de modification du nom/prenom
    # pour l'admin ») : refuse cote serveur, pas de carte dans la page. Il
    # voit en revanche celui des comptes dans leur liste.
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert admin.put("/api/mon-compte/profil", json={"prenom": "Lucas"}).status_code == 403
    assert '"prenom": ""' in _page_servie(admin)
    assert "$('zone-profil').remove();" in _page_panneau("dashboard.html")
    liste = {u["nom"]: u for u in admin.get("/api/utilisateurs").get_json()["utilisateurs"]}
    assert liste["marie"]["profil"] == {"prenom": "Marie", "nom": ""}
    # Un type inattendu ne passe pas pour du texte.
    assert c.put("/api/mon-compte/profil", json={"prenom": {"x": 1}}).get_json()["prenom"] == ""


def test_la_carte_session_espace_ses_boutons_du_texte():
    """Vecu : « Le bouton doit etre espace correctement avec le texte ». La
    carte ne se replie plus, et sa tete n'a plus de marge : l'espace est
    porte par les boutons, comme le corps d'une carte depliee (18 px)."""
    page = _page_panneau("dashboard.html")
    assert ".session-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}" in page
    assert ".settings-card.pliable>.card-corps{margin-top:18px}" in page


# ---------------------- deux methodes au choix, la derniere d'office ----------------------
#
# Vecu : « je veux 2 methodes de 2Auth au choix sans https : un code par mail
# ou [...] retenir la derniere methode utilisee pour l'utilisateur comme
# methode par defaut (pas parametrable). Possibilite de cliquer sur Essayer
# une autre methode ». Choisi ensuite : le mail et l'application
# d'authentification, Face ID en plus en HTTPS.

def test_seul_le_code_de_la_methode_affichee_est_accepte(comptes_mail):
    """Vecu : « Lorsque je me connecte [...] 2 codes fonctionnent : celui de
    l'email et celui du code de verification qui provient de mon telephone.
    Separe les 2 comme 2 methodes differentes. » La page montre une methode :
    le code de l'autre est refuse, jusqu'a « Essayer une autre methode »."""
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    comptes = app.lire_utilisateurs()
    comptes["marie"]["totp"] = "JBSWY3DPEHPK3PXP"
    app.ecrire_utilisateurs(comptes)
    mdp = {"nom": "marie", "password": "mot-de-passe-long"}
    app._login_attempts.clear()
    partis.clear()
    # Le mail est montre : le code de l'application ne passe pas.
    assert c.post("/login", json=mdp).get_json()["moyen"] == "email"
    code_mail = re.search(r"\b(\d{6})\b", partis[-1][1]).group(1)
    appli = app.totp_code("JBSWY3DPEHPK3PXP", int(time.time()) // app.TOTP_PAS)
    if appli != code_mail:
        assert c.post("/login", json={**mdp, "code": appli}).status_code == 401
    # « Essayer une autre methode » : l'application. Le code du mail, encore
    # valable, ne passe plus ; celui de l'application, oui.
    app._login_attempts.clear()
    assert c.post("/login", json={**mdp, "moyen": "totp"}).get_json()["moyen"] == "totp"
    if appli != code_mail:
        assert c.post("/login", json={**mdp, "code": code_mail}).status_code == 401
    app._login_attempts.clear()
    assert c.post("/login", json={**mdp, "code": appli}).status_code == 200
    page = _page_panneau("login.html")
    assert '<form id="formulaire"' in page and 'type="submit"' in page


def test_la_derniere_methode_utilisee_est_proposee_d_office(comptes_mail):
    """Sans historique, le code par mail vient en tete ; une connexion par
    l'application la fait passer devant, sans qu'aucun mail ne parte."""
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    comptes = app.lire_utilisateurs()
    comptes["marie"]["totp"] = "JBSWY3DPEHPK3PXP"
    app.ecrire_utilisateurs(comptes)
    d = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"}).get_json()
    assert d["moyens"] == ["email", "totp"] and d["moyen"] == "email"
    app._login_attempts.clear()
    # Choisir l'application n'envoie rien : les methodes sont decorrelees.
    partis.clear()
    d = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                               "moyen": "totp"}).get_json()
    assert d["moyen"] == "totp" and partis == []
    code = app.totp_code("JBSWY3DPEHPK3PXP", int(time.time()) // app.TOTP_PAS)
    assert c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                                  "code": code}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["dernier_moyen"] == "totp"
    # La fois suivante, l'application est proposee d'office.
    autre = app.flask_app.test_client()
    app._login_attempts.clear()
    d = autre.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"}).get_json()
    assert d["moyens"] == ["totp", "email"] and d["moyen"] == "totp"
    page = _page_panneau("login.html")
    assert "Essayer une autre méthode" in page and "function basculerAutresMoyens()" in page


def test_choisir_l_application_ne_coute_pas_d_essai(comptes_mail):
    """Vecu : passer du code par mail a l'application (« Essayer une autre
    methode ») consommait un essai sur cinq sans rien envoyer. Le mail, lui,
    compte toujours : sans cela, le formulaire enverrait des mails en boucle."""
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    comptes = app.lire_utilisateurs()
    comptes["marie"]["totp"] = "JBSWY3DPEHPK3PXP"
    app.ecrire_utilisateurs(comptes)
    app._login_attempts.clear()
    for _ in range(8):
        r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                                   "moyen": "totp"})
        assert r.status_code == 401 and r.get_json()["moyen"] == "totp"
    assert not any(app._login_attempts.values())
    c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long", "moyen": "email"})
    assert any(app._login_attempts.values())


def test_un_compte_ajoute_et_retire_son_application_d_authentification(comptes_mail):
    """Le compte qui a son adresse peut ajouter l'application, avec une
    connexion recente (regle 8) ; il la retire avec un code valide, et jamais
    s'il n'a plus d'autre facteur."""
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    partis.clear()
    c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    code = re.search(r"\b(\d{6})\b", partis[-1][1]).group(1)
    assert c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                                  "code": code}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["dernier_moyen"] == "email"
    assert c.get("/api/mon-compte/totp").get_json() == {"actif": False, "retirable": True}
    d = c.post("/api/mon-compte/totp/preparer").get_json()
    secret = d["secret"]
    assert c.post("/api/mon-compte/totp/activer", json={"code": "000000"}).status_code == 400
    bon = app.totp_code(secret, int(time.time()) // app.TOTP_PAS)
    assert c.post("/api/mon-compte/totp/activer", json={"code": bon}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["totp"] == secret
    # Retirer exige un code de l'application.
    assert c.post("/api/mon-compte/totp/retirer", json={"code": "000000"}).status_code == 400
    assert c.post("/api/mon-compte/totp/retirer", json={"code": bon}).status_code == 200
    assert not app.lire_utilisateurs()["marie"].get("totp")
    # Le dernier facteur ne se retire pas.
    comptes = app.lire_utilisateurs()
    comptes["marie"].update(totp=secret, email_verifie=False)
    app.ecrire_utilisateurs(comptes)
    r = c.post("/api/mon-compte/totp/retirer", json={"code": bon})
    assert r.status_code == 400 and "seul second facteur" in r.get_json()["error"]
    # Une connexion trop ancienne ne suffit pas pour ajouter un facteur.
    comptes["marie"].pop("totp")
    app.ecrire_utilisateurs(comptes)
    with c.session_transaction() as s:
        s["ouverte"] = int(time.time()) - 3600
    r = c.post("/api/mon-compte/totp/preparer")
    assert r.status_code == 403 and r.get_json().get("reconnexion")


# ------------------------- valider sur mon telephone -------------------------

@pytest.fixture
def telephone(deux_espaces, monkeypatch):
    """marie a un telephone deja connecte et abonne aux notifications."""
    envoyes = []
    monkeypatch.setattr(app, "abonnements_push", lambda compte: [{"id": 1, "sid": "s"}]
                        if compte == "marie" else [])
    monkeypatch.setattr(app, "cle_vapid", lambda: object())
    monkeypatch.setattr(app, "notifier_compte",
                        lambda compte, titre, texte, url="/", **k: envoyes.append(
                            {"compte": compte, "texte": texte, "url": url}) or 1)
    app._approbations.clear()
    tel = app.flask_app.test_client()
    with tel.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 ouverte=int(time.time()), jeton="jeton-du-telephone")
    return deux_espaces, tel, envoyes


def _demande(c):
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    d = r.get_json()
    assert r.status_code == 401 and d["moyen"] == app.MOYEN_APPAREIL, d
    return d["nombre"]


def test_valider_sur_mon_telephone_ouvre_la_session_avec_le_bon_nombre(telephone):
    """Vecu : « une solution pour double authentification simplifie autant
    que faceid hors apple ». Le mot de passe sur l'ordinateur, puis un appui
    sur le telephone deja connecte : le nombre affiche sur l'ecran de
    connexion, et seulement lui."""
    ordi, tel, envoyes = telephone
    nombre = _demande(ordi)
    assert len(envoyes) == 1 and envoyes[0]["compte"] == "marie"
    # Le nombre n'est pas dans la notification : il faut voir l'ecran.
    assert str(nombre) not in envoyes[0]["texte"]
    ident = envoyes[0]["url"].rsplit("/", 1)[1]
    assert ordi.post("/login/second-facteur/appareil").status_code == 202
    page = tel.get("/approuver/" + ident).get_data(as_text=True)
    assert page.count('data-n="') == 3 and 'data-n="%d"' % nombre in page
    r = tel.post("/api/approbations/" + ident, json={"nombre": nombre})
    assert r.status_code == 200, r.data
    r = ordi.post("/login/second-facteur/appareil")
    assert r.status_code == 200, r.data
    with ordi.session_transaction() as s:
        assert s.get("authed") and s.get("utilisateur") == "marie"
    # La demande ne sert qu'une fois.
    assert tel.post("/api/approbations/" + ident, json={"nombre": nombre}).status_code == 404


def test_un_mauvais_nombre_refuse_la_connexion_sans_second_essai(telephone):
    ordi, tel, envoyes = telephone
    nombre = _demande(ordi)
    ident = envoyes[0]["url"].rsplit("/", 1)[1]
    faux = 10 if nombre != 10 else 11
    assert tel.post("/api/approbations/" + ident, json={"nombre": faux}).status_code == 200
    assert tel.post("/api/approbations/" + ident, json={"nombre": nombre}).status_code == 404
    assert ordi.post("/login/second-facteur/appareil").status_code == 403
    with ordi.session_transaction() as s:
        assert not s.get("authed")


def test_seul_un_appareil_du_compte_repond_et_seul_le_demandeur_entre(telephone):
    """Un autre compte ne voit ni ne valide la demande ; et l'approbation
    n'ouvre que le navigateur qui a donne le mot de passe."""
    ordi, tel, envoyes = telephone
    nombre = _demande(ordi)
    ident = envoyes[0]["url"].rsplit("/", 1)[1]
    autre = app.flask_app.test_client()
    with autre.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="paul",
                 ouverte=int(time.time()), jeton="j")
    app.ecrire_utilisateurs({**app.lire_utilisateurs(), "paul": {
        "sel": "ee" * 16, "hash": "x", "projets": [], "cree": 0}})
    assert autre.post("/api/approbations/" + ident, json={"nombre": nombre}).status_code == 404
    assert "plus valable" in autre.get("/approuver/" + ident).get_data(as_text=True)
    assert tel.post("/api/approbations/" + ident, json={"nombre": nombre}).status_code == 200
    intrus = app.flask_app.test_client()
    assert intrus.post("/login/second-facteur/appareil").status_code == 400
    with intrus.session_transaction() as s:
        assert not s.get("authed")


def test_valider_sur_mon_telephone_n_est_propose_qu_avec_un_appareil_abonne(telephone,
                                                                             monkeypatch):
    ordi, _, envoyes = telephone
    monkeypatch.setattr(app, "abonnements_push", lambda compte: [])
    r = ordi.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    assert app.MOYEN_APPAREIL not in (r.get_json().get("moyens") or [])
    assert not envoyes
    # Jamais pour l'administrateur : ses connexions restent au reseau local,
    # avec ses propres facteurs.
    monkeypatch.setattr(app, "abonnements_push", lambda compte: [{"id": 1, "sid": "s"}])
    assert not app.approbation_possible(app.NOM_ADMIN)


# ===========================================================================
# LA SECURITE
# les regles de SECURITE.md : SSO, CSRF, CSP, origines separees, reseau local,
# isolation, secrets transmis, coffre, une base par projet, ce qu'un compte
# utilisateur voit.
# ===========================================================================

# ------------------ SSO des applications ------------------
#
# L'application ne doit recevoir ni le cookie de session ni la cle privee.
# Le test verifie aussi que l'audience lie l'assertion au projet demande.

def test_assertion_sso_signee_et_limitee_a_l_application(monkeypatch):
    import base64
    import json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    cle = app._generer_cle_sso()
    monkeypatch.setattr(app, "_sso_private_key", cle)
    monkeypatch.setattr(app, "_sso_public_key", app._public_sso_b64(cle))

    app.flask_app.secret_key = "test-sso-secret"
    with app.flask_app.test_request_context("/"):
        app.session["authed"] = True
        app.session["role"] = app.ROLE_UTILISATEUR
        app.session["utilisateur"] = "alice"
        token = app.jeton_sso("wine-manager")

    h, p, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    signature = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    public = base64.urlsafe_b64decode(
        app._sso_public_key + "=" * (-len(app._sso_public_key) % 4)
    )
    Ed25519PublicKey.from_public_bytes(public).verify(
        signature, (h + "." + p).encode("ascii")
    )

    assert payload["sub"] == "alice"
    assert payload["aud"] == "wine-manager"
    assert payload["iss"] == app.SSO_ISSUER
    assert payload["exp"] - payload["iat"] == app.SSO_TOKEN_TTL




def test_secrets_partages_n_expose_pas_les_cles_sso(tmp_path, monkeypatch):
    env = tmp_path / "credentials.env"
    env.write_text(
        "CODELAB_SSO_PRIVATE_KEY=secret\n"
        "CODELAB_SSO_PUBLIC_KEY=public\n"
        "CODELAB_TEST=value\n"
    )
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(env))
    assert app.secrets_partages() == {"CODELAB_TEST": "value"}




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
    monkeypatch.setattr(app, "drop_privileges", lambda nom=None: ordre.append("drop"))
    app.child_setup()()
    assert ordre == ["drop"]
    ordre.clear()
    app.child_setup(64)()
    assert ordre == ["rlimit", "drop"]   # la limite avant la bascule


# ---------------- 6. secrets transmis aux applications ----------------
#
# credentials.env est en 0600 root ; les applications tournent sous l'uid
# 1001 et ne peuvent donc pas le lire, alors que c'est la que la
# documentation leur dit de prendre le mot de passe Postgres. Le panneau le
# lit pour elles et le transmet par l'environnement -- sans son propre bloc.




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
    # Ni le mot de passe du panneau, ni le compte administrateur de Postgres :
    # chaque application recoit celui de SA base (identifiants_base).
    assert "POSTGRES_PASSWORD" not in partages
    assert not [c for c in partages if c.startswith("APP_MANAGER_")]




def test_le_bloc_d_alertes_du_panneau_n_est_pas_transmis_aux_applications(tmp_path, monkeypatch):
    """Le meme defaut que ci-dessus, par une autre porte.

    La regle "le panneau ne transmet pas son propre bloc" reposait sur le
    prefixe APP_MANAGER_. Le bloc d'alertes n'en porte pas -- il ne le peut
    pas, le capteur Dagster lit ces noms-la dans ce fichier -- et passait
    donc entier dans l'environnement de chaque application : serveur
    d'envoi, identifiant, MOT DE PASSE, et l'adresse de l'administrateur.

    Ce que ca donnait : n'importe quelle application deployee pouvait
    expedier du courrier au nom de CodeLab, depuis l'adresse meme d'ou
    partent les alertes.
    """
    _credentials(tmp_path, monkeypatch, "\n".join([
        "REDIS_PASSWORD=mdp-redis",
        "SMTP_HOST=smtp.example.com",
        "SMTP_PORT=587",
        "SMTP_TLS=starttls",
        "SMTP_USER=panneau@example.com",
        "SMTP_PASSWORD=mot-de-passe-d-application",
        "ALERTE_FROM=panneau@example.com",
        "ALERTE_ADMIN=admin@example.com",
        "API_TOKEN=jeton-metier",
    ]))
    partages = app.secrets_partages()
    assert partages == {"REDIS_PASSWORD": "mdp-redis",
                        "API_TOKEN": "jeton-metier"}
    # Nomme la cle la plus grave separement : si la liste d'exclusion venait a
    # etre reduite un jour, l'echec doit designer ce qui a fuite.
    assert "SMTP_PASSWORD" not in partages


def test_une_application_sans_smtp_a_elle_prend_le_relais_du_hub(tmp_path, monkeypatch):
    """Vecu : « une nouvelle app doit prendre le SMTP du hub par defaut si je
    n'en parametre pas dans l'app ». Une application qui lisait SMTP_HOST
    (Nodemailer) ou EMAIL_HOST (Django) ne trouvait rien. Elle recoit
    desormais ces noms, pointes sur Mailpit -- qui relaie par le serveur du
    panneau -- et jamais l'hote, l'identifiant ni le mot de passe du panneau."""
    _credentials(tmp_path, monkeypatch, "\n".join([
        "SMTP_HOST=smtp.example.com",
        "SMTP_PORT=587",
        "SMTP_TLS=starttls",
        "SMTP_USER=panneau@example.com",
        "SMTP_PASSWORD=mot-de-passe-d-application",
        "MAIL_HOST=codelab-mailpit",
        "MAIL_PORT=1025",
        "MAIL_URL=smtp://codelab-mailpit:1025",
    ]))
    partages = app.secrets_partages()
    assert partages["SMTP_HOST"] == partages["EMAIL_HOST"] == "codelab-mailpit"
    assert partages["MAIL_SERVER"] == "codelab-mailpit"
    assert partages["SMTP_PORT"] == partages["EMAIL_PORT"] == "1025"
    assert partages["SMTP_URL"] == "smtp://codelab-mailpit:1025"
    for cle in ("SMTP_USER", "SMTP_PASSWORD", "SMTP_TLS"):
        assert cle not in partages
    assert not any("example.com" in v or "mot-de-passe" in v for v in partages.values())




def test_une_application_avec_son_smtp_ne_recoit_rien_du_relais(tmp_path):
    """« si je n'en parametre pas dans l'app » : le contraire doit tenir aussi.

    python-dotenv et le dotenv de Node n'ecrasent pas une variable deja
    recue : avec le SMTP_HOST du relais pose par le panneau, celui du .env
    du projet etait ignore, et un .env qui ne nommait que l'hote recevait le
    port 1025 de Mailpit. Un projet qui nomme son serveur ne recoit donc
    aucune variable du relais ; sans serveur a lui, il les garde toutes."""
    relais = {"SMTP_HOST": "codelab-mailpit", "SMTP_PORT": "1025",
              "SMTP_URL": "smtp://codelab-mailpit:1025", "EMAIL_HOST": "codelab-mailpit",
              "EMAIL_PORT": "1025", "MAIL_SERVER": "codelab-mailpit",
              "MAIL_HOST": "codelab-mailpit", "MAIL_PORT": "1025",
              "MAIL_URL": "smtp://codelab-mailpit:1025"}
    assert set(relais) == app.VARIABLES_DU_RELAIS

    sans = tmp_path / "sans"
    sans.mkdir()
    (sans / ".env").write_text("CODELAB_DB=sans\n# SMTP_HOST=commente\nSMTP_HOST=\n",
                               encoding="utf-8")
    env = dict(relais, AUTRE="garde")
    app.retirer_le_relais_si_le_projet_a_son_smtp(env, str(sans))
    assert env == dict(relais, AUTRE="garde")

    for cle in app.SERVEUR_MAIL_DU_PROJET:
        avec = tmp_path / cle.lower()
        avec.mkdir()
        (avec / ".env").write_text(f"export {cle}=smtp.example.com\n", encoding="utf-8")
        env = dict(relais, AUTRE="garde")
        app.retirer_le_relais_si_le_projet_a_son_smtp(env, str(avec))
        assert env == {"AUTRE": "garde"}, cle

    # Et le lancement passe bien par ce filtre, en dernier.
    lancement = _source_du_panneau().split("def _environnement_de_lancement(")[1]
    lancement = lancement.split("\ndef ")[0]
    assert lancement.rstrip().endswith(
        'retirer_le_relais_si_le_projet_a_son_smtp(env, a["path"])\n    return env')




def test_le_panneau_lit_toujours_son_propre_bloc_d_alertes(tmp_path, monkeypatch):
    """Le pendant du test precedent, et la raison d'etre de la separation.

    Retirer ces cles de secrets_partages() ne doit rien retirer au panneau :
    c'est sa configuration d'envoi de repli. Sans cette verification, le
    correctif pourrait couper les alertes sans que rien ne le signale --
    exactement la panne silencieuse qu'elles servent a eviter.
    """
    _credentials(tmp_path, monkeypatch, "\n".join([
        "SMTP_HOST=smtp.example.com",
        "SMTP_USER=panneau@example.com",
        "SMTP_PASSWORD=mot-de-passe-d-application",
    ]))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    origine = app.smtp_origine()
    assert origine["host"] == "smtp.example.com"
    assert origine["password"] == "mot-de-passe-d-application"




def test_la_liste_d_exclusion_suit_les_champs_smtp(tmp_path, monkeypatch):
    """CLES_PANNEAU derive de CHAMPS_SMTP plutot que d'etre recopiee.

    Ajouter un champ d'envoi sans penser a l'exclure rouvrirait la fuite en
    silence. Ce test tient ce lien : c'est la propriete, pas la liste.
    """
    assert set(app.CHAMPS_SMTP.values()) <= app.CLES_PANNEAU




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
        'REDIS_PASSWORD="perime"',
        "REDIS_PASSWORD='a-jour'",
    ]))
    assert app.secrets_partages() == {"REDIS_PASSWORD": "a-jour"}




def test_un_fichier_illisible_ne_empeche_pas_de_lancer(tmp_path, monkeypatch):
    """Volume config non monte : les applications se debrouillent avec leur
    propre .env, comme avant -- le panneau ne doit pas refuser de lancer."""
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "absent.env"))
    assert app.secrets_partages() == {}


# ------------------- 8. deux espaces : admin et utilisateur -------------------
#
# Le controle des droits est fait dans les routes, jamais dans l'interface :
# masquer un bouton ne protege rien, la route reste appelable a la main. Ces
# tests appellent donc les routes directement, comme le ferait quelqu'un qui
# a lu le code de la page.




def test_un_utilisateur_ne_peut_rien_administrer(deux_espaces):
    """Le coeur du sujet : un compte utilisateur ne deploie pas, ne configure
    pas, ne cree pas de compte -- meme en appelant les routes a la main."""
    c = deux_espaces
    assert _connecte(c, "marie", "mot-de-passe-long")["role"] == "utilisateur"
    for methode, route in [("get", "/api/apps"), ("post", "/api/add"),
                           ("post", "/api/toggle/public"),
                           ("delete", "/api/app/public"), ("get", "/api/utilisateurs"),
                           ("post", "/api/utilisateurs"), ("get", "/api/alertes"),
                           ("get", "/api/logs/public"),
                           ("get", "/api/apps/public/acces"),
                           ("put", "/api/apps/public/acces"),
                           ("put", "/api/alertes/application/public"),
                           ("get", "/api/securite"),
                           ("get", "/api/browse")]:
        r = getattr(c, methode)(route, json={})
        assert r.status_code == 403, f"{methode.upper()} {route} a repondu {r.status_code}"




def test_un_utilisateur_ne_voit_que_ses_projets(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    noms = [a["name"] for a in c.get("/api/mes-apps").get_json()["apps"]]
    assert noms == ["prive-autorise"]

    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
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
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
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
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/utilisateurs", json={"nom": "admin", "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 400
    assert "administration" in r.get_json()["error"]




def test_les_mots_de_passe_sont_derives_et_sales(deux_espaces):
    """Deux comptes avec le meme mot de passe ne doivent pas donner la meme
    empreinte : sinon le fichier revele qui partage un mot de passe."""
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
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
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.put("/api/utilisateurs/marie", json={"projets": ["public", "jamais-declare"]})
    assert r.status_code == 200
    assert app.lire_utilisateurs()["marie"]["projets"] == ["public"]




# ---------- l'administration ne passe pas par un intermediaire ----------

def test_l_administration_ne_passe_pas_par_le_proxy(tmp_path, monkeypatch):
    """L'administration ne repond qu'en connexion directe depuis le reseau
    local : une requete relayee (X-Forwarded-*) est refusee, quel que soit
    l'intermediaire."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.get("/api/securite", headers={"X-Forwarded-For": "203.0.113.7",
                                        "X-Forwarded-Proto": "https"})
    assert r.status_code == 403


# ---------- l'acces a une application, vu depuis l'application ----------
#
# La meme information que dans la fiche d'un compte, prise par l'autre bout.
# Elle n'existait que dans un sens : pour savoir qui ouvrait une application
# il fallait ouvrir les fiches une par une, et pour l'accorder a cinq
# personnes, cinq allers-retours.




def test_l_application_dit_qui_l_ouvre(acces):
    d = acces.get("/api/apps/facturation/acces").get_json()
    assert [c["nom"] for c in d["comptes"]] == ["marie", "paul"]
    assert all(c["acces"] is False for c in d["comptes"])
    assert d["publique"] is False
    # L'adresse aide a distinguer deux homonymes ; elle est deja visible
    # ailleurs dans le panneau pour un administrateur.
    assert d["comptes"][0]["email"] == "marie@example.com"




def test_accorder_l_acces_depuis_l_application_n_efface_pas_les_autres_projets(acces):
    """La propriete qui compte. Envoyer la liste complete des projets d'un
    compte aurait efface en silence ce qu'un autre onglet venait d'accorder :
    on n'ecrit donc QUE cette application dans chaque fiche."""
    r = acces.put("/api/apps/facturation/acces", json={"utilisateurs": ["marie"]})
    assert r.status_code == 200, r.data
    comptes = app.lire_utilisateurs()
    assert comptes["marie"]["projets"] == ["facturation", "vitrine"], (
        "l'acces accorde ailleurs a ete efface")
    assert comptes["paul"]["projets"] == []




def test_retirer_l_acces_ne_retire_que_celui_la(acces):
    acces.put("/api/apps/facturation/acces", json={"utilisateurs": ["marie"]})
    r = acces.put("/api/apps/facturation/acces", json={"utilisateurs": []})
    assert r.status_code == 200, r.data
    assert app.lire_utilisateurs()["marie"]["projets"] == ["vitrine"]




def test_une_application_publique_le_dit(acces):
    """Elle s'ouvre sans compte : laisser croire que cocher quelqu'un y change
    quelque chose serait pire que de ne rien afficher."""
    assert acces.get("/api/apps/vitrine/acces").get_json()["publique"] is True




def test_un_compte_inconnu_est_refuse_sans_rien_ecrire(acces):
    r = acces.put("/api/apps/facturation/acces",
                  json={"utilisateurs": ["marie", "fantome"]})
    assert r.status_code == 400, r.data
    assert "fantome" in r.get_json()["error"]
    assert app.lire_utilisateurs()["marie"]["projets"] == ["vitrine"], (
        "un refus a quand meme modifie les comptes")




def test_une_application_inconnue_repond_404(acces):
    assert acces.get("/api/apps/absente/acces").status_code == 404
    assert acces.put("/api/apps/absente/acces",
                     json={"utilisateurs": []}).status_code == 404


# ---------- 17. adresse publique du serveur ----------
#
# « Publique » veut dire : accessible sans compte. Sur un serveur que
# personne d'autre ne peut joindre, le mot promet une ouverture qui n'existe
# pas -- il ne retire que l'authentification. Tant qu'aucune adresse publique
# n'est declaree, le panneau ne le propose pas, et le serveur le refuse.




def test_sans_adresse_publique_on_ne_peut_pas_ouvrir_une_application(exposition):
    c = exposition
    r = c.post("/api/visibility/prive", json={"visibility": "publique"})
    assert r.status_code == 400
    assert "jamais été ouvert depuis internet" in r.get_json()["error"]
    assert app.load()["prive"]["visibility"] == "privee"

    # Le chemin qui REFERME n'est jamais bloque : une application deja
    # publique doit toujours pouvoir redevenir privee.
    assert c.post("/api/visibility/deja-public",
                  json={"visibility": "privee"}).status_code == 200
    assert app.load()["deja-public"]["visibility"] == "privee"




def test_une_application_neuve_nait_privee_sur_un_serveur_prive(exposition):
    """Le defaut sur lequel on ne peut pas se tromper : elle s'ouvre en une
    bascule, alors qu'une application ouverte par megarde ne se referme
    qu'apres coup."""
    c = exposition
    r = c.post("/api/add", json={"name": "neuf", "path": "/w/neuf",
                                 "command": "x", "visibility": "publique"})
    assert r.status_code == 200, r.data
    assert app.load()["neuf"]["visibility"] == "privee"




def test_une_fois_l_adresse_connue_le_partage_redevient_possible(exposition, monkeypatch):
    """Plus de champ a remplir : l'adresse publique est celle de Tailscale
    Funnel, des que l'acces depuis internet est allume."""
    c = exposition
    monkeypatch.setattr(app, "adresse_funnel", lambda: "https://codelab.tail1234.ts.net")
    assert app.adresse_publique() == "https://codelab.tail1234.ts.net"
    assert c.get("/api/securite").get_json()["adresse_publique"] == "https://codelab.tail1234.ts.net"
    assert c.post("/api/visibility/prive", json={"visibility": "publique"}).status_code == 200
    # La route qui la faisait saisir n'existe plus.
    assert c.put("/api/securite/exposition",
                 json={"adresse_publique": "https://autre.example"}).status_code in (404, 405)


# ---------- HTTPS et proxy de confiance, regles depuis la page ------------
#
# Ces deux reglages vivaient uniquement dans le compose, en commentaire. Le
# code disait pourquoi : « l'activer depuis une page servie en clair
# deconnecterait sur-le-champ la session qui vient de l'activer, sans moyen
# de revenir en arriere. »
#
# L'objection etait juste. Ce qui la leve n'est pas de l'ignorer, c'est de
# rendre le cas impossible : on n'allume que ce que la requete en cours
# justifie. Ces tests tiennent exactement cette promesse -- et le contraire,
# qui compte autant : ETEINDRE reste possible en toutes circonstances.




def test_le_cookie_est_securise_des_que_la_connexion_l_est():
    """Plus de case « Servi en HTTPS » : le cookie devient Secure, et HSTS
    part, des que la requete arrive en https -- directement ou par Tailscale
    Funnel (reconnu a son port). En clair, rien de tel (le navigateur ne
    renverrait plus le cookie), meme si un client annonce X-Forwarded-Proto :
    plus aucun intermediaire legitime ne le pose depuis le retrait du VPS."""
    app.flask_app.secret_key = "cle-de-test"
    app._login_attempts.clear()
    with app.flask_app.test_request_context("/", base_url="http://localhost"):
        assert app.https_actif() is False
    with app.flask_app.test_request_context("/", base_url="https://localhost"):
        assert app.https_actif() is True
    with app.flask_app.test_request_context("/", headers={"X-Forwarded-Proto": "https"}):
        assert app.https_actif() is False
    assert isinstance(app.flask_app.session_interface, app.SessionSelonLaConnexion)
    c = app.flask_app.test_client()
    r = c.get("/", base_url="https://localhost")
    assert "Strict-Transport-Security" in r.headers
    r = c.get("/", base_url="http://localhost")
    assert "Strict-Transport-Security" not in r.headers




def test_les_reglages_d_exposition_ne_sont_plus_reglables(exposition):
    """Proxy, HTTPS et administration locale ne sont plus des options : la
    configuration est toujours la plus stricte."""
    assert not hasattr(app, "trust_proxy")
    assert app.admin_limite_au_reseau_local() is True
    page = _page_panneau("dashboard.html")
    for trace in ("expo-proxy", "expo-https", "expo-admin-lan", "Proxy de confiance",
                  "Configuration d'origine", "alRevenirOrigine"):
        assert trace not in page, trace


# ---------- 12 bis. l'administration reste sur le reseau local ----------
#
# Le raisonnement : un compte nomme est fait pour etre distribue et n'ouvre
# que les projets qu'on lui a autorises. Le compte d'administration, lui,
# permet de declarer une application -- donc d'executer du code sur la
# machine. Les deux n'ont aucune raison d'etre joignables de la meme facon.
#
# Ce que ces tests tiennent, dans l'ordre d'importance :
#
#   1. un compte nomme entre toujours de l'exterieur -- c'est la moitie de
#      la demande, et celle qu'une implementation trop large casserait ;
#   2. on ne peut pas activer ce reglage depuis l'exterieur, sous peine de
#      se retirer l'administration dans la seconde ;
#   3. derriere un proxy non declare, l'administration est REFUSEE et non
#      accordee : toutes les requetes y paraissent locales, et une securite
#      qui ment est pire que pas de securite.




def test_une_adresse_illisible_n_ouvre_pas_l_administration():
    """Le sens du doute. Une adresse qu'on ne sait pas lire ne doit jamais
    valoir "locale" : c'est un controle d'acces, pas un affichage."""
    assert app.adresse_est_locale("192.168.1.42") is True
    assert app.adresse_est_locale("10.0.0.1") is True
    assert app.adresse_est_locale("127.0.0.1") is True
    assert app.adresse_est_locale("::1") is True
    assert app.adresse_est_locale("fd00::1") is True
    # 100.64/10, l'espace partage : un reseau prive type Tailscale est une
    # extension de la maison, pas l'Internet. Python l'EXCLUT de is_private
    # selon sa version -- raison pour laquelle la liste est ecrite a la main.
    assert app.adresse_est_locale("100.100.1.2") is True
    # Une pile double presente parfois une adresse v4 sous forme mappee.
    assert app.adresse_est_locale("::ffff:192.168.1.42") is True
    assert app.adresse_est_locale("8.8.8.8") is False
    # Les plages de DOCUMENTATION : is_private les dit "privees", elles n'ont
    # rien de local. C'est ce piege qui a fait ecrire la liste a la main.
    assert app.adresse_est_locale("203.0.113.7") is False
    assert app.adresse_est_locale("2001:db8::1") is False
    assert app.adresse_est_locale("unknown") is False
    assert app.adresse_est_locale("") is False
    assert app.adresse_est_locale(None) is False




def test_l_admin_ne_se_connecte_plus_depuis_l_exterieur(exposition):
    _active_le_verrou(exposition)
    c = app.flask_app.test_client()
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"}, environ_base=DEHORS)
    assert r.status_code == 403, r.data
    assert "reseau local" in r.get_json()["error"]
    # Et le mot de passe n'a meme pas ete regarde : aucune session ouverte.
    assert r.get_json().get("ok") is not True




def test_l_admin_se_connecte_toujours_depuis_chez_lui(exposition):
    _active_le_verrou(exposition)
    c = app.flask_app.test_client()
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"}, environ_base=CHEZ_SOI)
    assert r.status_code == 200, r.data




def test_un_compte_nomme_entre_toujours_depuis_l_exterieur(exposition):
    """LA moitie de la demande qu'une implementation trop large casserait :
    le verrou ne vise que l'administration, jamais les comptes nommes."""
    _active_le_verrou(exposition)
    sel = "bb" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["prive"], "cree": 0}})

    c = app.flask_app.test_client()
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"},
               environ_base=DEHORS)
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d.get("inscription") is True, "le compte nomme a ete refuse de l'exterieur"
    r = c.post("/login/second-facteur",
               json={"code": app.totp_code(d["secret"], int(time.time()) // app.TOTP_PAS)},
               environ_base=DEHORS)
    assert r.status_code == 200, r.data
    assert r.get_json()["role"] == app.ROLE_UTILISATEUR




def test_une_session_admin_qui_sort_cesse_d_administrer(exposition):
    """Verifie a chaque requete, pas seulement a la connexion : un portable
    ouvert dans le salon puis repris depuis un train -- ou un cookie vole --
    ne doit plus administrer une fois dehors."""
    c = exposition
    _active_le_verrou(c)
    # La meme session, le meme cookie, depuis l'exterieur.
    r = c.get("/api/apps", environ_base=DEHORS)
    assert r.status_code == 403, r.data
    assert "reseau local" in r.get_json()["error"]
    # Et elle fonctionne toujours depuis chez soi.
    assert c.get("/api/apps", environ_base=CHEZ_SOI).status_code == 200




def test_derriere_un_proxy_non_declare_l_administration_est_refusee(exposition):
    """Le choix qui compte : ne pas savoir vaut REFUSER.

    Si un proxy apparait apres coup sans etre declare, l'administration se
    ferme -- elle ne s'ouvre pas a tout le monde. La marche arriere est le
    compose, comme pour les autres reglages.
    """
    c = exposition
    _active_le_verrou(c)
    r = c.get("/api/apps", headers={"X-Forwarded-For": "192.168.1.42"})
    assert r.status_code == 403, r.data
    assert "proxy" in r.get_json()["error"].lower()




def test_une_adresse_locale_injectee_par_le_client_ne_passe_pas_le_verrou(exposition):
    """L'attaque : depuis Internet, envoyer soi-meme « X-Forwarded-For:
    192.168.1.42 ». L'en-tete n'est jamais cru, et sa seule presence ferme
    l'administration."""
    c = exposition
    for valeur in ("192.168.1.42", "192.168.1.42, 203.0.113.7", "203.0.113.7, 192.168.1.42"):
        r = c.get("/api/apps", headers={"X-Forwarded-For": valeur}, environ_base=DEHORS)
        assert r.status_code == 403, (valeur, r.data)




def test_changer_d_adresse_inventee_ne_contourne_pas_la_limite(client, monkeypatch):
    app._login_attempts.clear()
    for i in range(app.RATE_LIMIT_MAX):
        client.post("/login", json={"nom": "admin", "password": "faux"},
                    headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"})
    r = client.post("/login", json={"nom": "admin", "password": "faux"},
                    headers={"X-Forwarded-For": "10.9.9.200, 203.0.113.7"})
    assert r.status_code == 429




def test_une_cle_d_acces_ne_contourne_pas_le_verrou(exposition):
    """Une cle d'acces vaut mot de passe ET second facteur, mais elle voyage
    avec son porteur et ne dit rien d'ou l'on appelle. Sans verrou sur cette
    route, il suffirait de passer par cette porte-ci."""
    if not app.passkeys_disponibles():
        pytest.skip("webauthn absent de cette image")
    _active_le_verrou(exposition)
    app.ecrire_passkeys({app.NOM_ADMIN: [
        {"id": "cle-de-test", "cle": "", "compteur": 0, "cree": 0, "nom": "test"}]})

    c = app.flask_app.test_client()
    with c.session_transaction() as sess:
        sess["passkey_defi"] = app.emettre_defi(b"defi-de-test")
    r = c.post("/login/passkey", json={"credential": {"id": "cle-de-test"}},
               environ_base=DEHORS)
    assert r.status_code == 403, r.data
    assert "reseau local" in r.get_json()["error"]




def test_marteler_le_refus_finit_par_etre_limite(exposition):
    """Un refus doit couter quelque chose a qui le provoque.

    Sinon il est gratuit : on le martele sans jamais etre limite, et comme
    chaque appel ecrit une ligne de journal, la rotation finit par chasser
    l'historique reel -- une facon discrete d'effacer ses traces.
    """
    _active_le_verrou(exposition)
    c = app.flask_app.test_client()
    vus = set()
    for _ in range(app.RATE_LIMIT_MAX + 2):
        vus.add(c.post("/login", json={"nom": "admin", "password": "peu importe"},
                       environ_base=DEHORS).status_code)
    assert 403 in vus, "le verrou n'a jamais refuse"
    assert 429 in vus, "le refus est gratuit : aucune limite ne s'applique"








# ---------- 22. jeton CSRF ----------
#
# SameSite=Lax bloque deja un autre SITE. Ce jeton couvre ce qu'il ne couvre
# pas : une requete lancee depuis une AUTRE ORIGINE qui porterait quand meme
# le cookie. Ce qui doit rester vrai :
#
#   - une ecriture sans jeton, sur une session ouverte, est refusee ;
#   - un jeton d'une autre session ne vaut rien ;
#   - les routes de connexion restent atteignables sans jeton (on ne peut pas
#     exiger un jeton d'une session qui n'existe pas encore) ;
#   - le proxy des applications n'est pas concerne ;
#   - une session ouverte porte TOUJOURS un jeton, sinon la garde se
#     contournerait en n'en ayant pas.

def test_une_ecriture_sans_jeton_est_refusee(deux_espaces):
    c = deux_espaces
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert r.status_code == 200, r.get_json()

    with c.session_transaction() as sess:
        assert sess.get("jeton"), "une session ouverte doit porter un jeton"

    # Avec le jeton (le client de test le pose, comme fetch dans la page) :
    # l'action passe. C'est le temoin -- sans lui, un 403 ne prouverait que
    # l'existence d'un bug quelconque.
    assert c.post("/api/toggle/public").status_code == 200

    # Le meme cookie de session, jeton vide : c'est exactement la requete
    # qu'une autre origine peut declencher, elle a le cookie mais pas le
    # jeton. Elle doit etre refusee.
    r = c.post("/api/toggle/public", headers={app.JETON_ENTETE: ""})
    assert r.status_code == 403
    assert "jeton" in r.get_json()["error"].lower()




def test_le_jeton_d_une_autre_session_ne_vaut_rien(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/toggle/public",
               headers={app.JETON_ENTETE: secrets.token_urlsafe(32)})
    assert r.status_code == 403




def test_les_routes_de_connexion_restent_ouvertes_sans_jeton(deux_espaces):
    """Sinon plus personne ne peut se connecter : le jeton vit dans la
    session, et la session n'existe pas encore."""
    c = deux_espaces
    r = c.post("/login", json={"nom": "admin", "password": "mauvais"})
    assert r.status_code == 401           # refuse par le mot de passe...
    assert "jeton" not in r.get_json()["error"].lower()   # ...pas par le jeton
    r = c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert r.status_code == 200




def test_toutes_les_routes_d_ecriture_sont_couvertes(deux_espaces):
    """La garde est un before_request, pas un decorateur pose route par
    route : ce test le constate sur la table de routage plutot que sur une
    liste ecrite a la main, qui vieillirait mal.

    Une route d'ecriture ajoutee demain sans etre exemptee est protegee
    d'office. Le test echoue seulement si quelqu'un l'AJOUTE aux exemptions.
    """
    ecritures = set()
    for regle in app.flask_app.url_map.iter_rules():
        if app.JETON_METHODES & set(regle.methods or ()):
            ecritures.add(regle.endpoint)
    non_couvertes = ecritures - app.JETON_EXEMPTS
    assert non_couvertes, "aucune route d'ecriture : la table est vide ?"
    # Les exemptions sont celles qu'on a decidees, pas plus.
    assert app.JETON_EXEMPTS == {
        "login_submit", "login_second_facteur",
        "login_passkey_options", "login_passkey",
        # Premiere connexion de l'administrateur : Face ID comme second
        # facteur, apres le mot de passe, sans session encore ouverte.
        "login_inscription_passkey_options", "login_inscription_passkey",
        # Etape 2 de la connexion : Face ID confirme le mot de passe.
        "login_facteur_passkey_options", "login_facteur_passkey",
        # Etape 2 encore : la page attend que l'autre appareil approuve.
        "login_facteur_appareil",
        # Ouverte par la personne invitee, sans session -- ou dans le
        # navigateur de l'administrateur qui essaie son propre lien.
        "invitation_accepter",
        # La meme, avec Face ID au lieu d'un mot de passe : la personne
        # invitee n'a pas encore de session (regle 13 : routes d'invitation).
        "invitation_passkey_options", "invitation_passkey",
        "proxy", "proxy_noslash",
    }




def test_le_get_n_est_jamais_concerne(deux_espaces):
    """Un GET ne change rien. L'exiger la n'apporterait aucune protection et
    casserait la moitie de la page."""
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.get("/api/apps", headers={app.JETON_ENTETE: "n'importe quoi"})
    assert r.status_code == 200




# ---------- 23. un uid par application ----------
#
# L'uid partage protegeait les applications DU panneau, pas les unes DES
# autres : meme uid, donc /proc/<pid>/environ d'une application etait lisible
# par sa voisine -- c'est-a-dire le mot de passe Postgres qu'on lui transmet.

def test_l_uid_ne_depend_que_du_nom(monkeypatch):
    """Derive, pas attribue : rien a migrer, et une application qui redemarre
    retrouve ses fichiers. Doit tenir d'un processus a l'autre, donc pas de
    hash() (randomise par PYTHONHASHSEED)."""
    a = app.uid_application("facturier")
    assert a == app.uid_application("facturier")
    assert a != app.uid_application("cahier")
    assert app.UID_APP_BASE <= a < app.UID_APP_BASE + app.UID_APP_PLAGE
    # Jamais root, jamais l'uid du service, jamais l'uid de la session SSH.
    for nom in ("a", "site", "notes", "x" * 32, "projet-2"):
        assert app.uid_application(nom) not in (0, 1000, 1001)




def test_l_uid_survit_a_un_redemarrage_du_panneau():
    """Le vrai risque : un uid different a chaque demarrage rendrait les
    fichiers de l'application illisibles par elle-meme. On relance un
    interpreteur neuf, avec un PYTHONHASHSEED different."""
    import subprocess as sp
    chemin = os.path.join(DOSSIER_PANNEAU, "app", "app.py")
    code = ("import importlib.util,sys;"
            "spec=importlib.util.spec_from_file_location('m', %r);"
            "m=importlib.util.module_from_spec(spec);sys.modules['m']=m;"
            "spec.loader.exec_module(m);print(m.uid_application('facturier'))" % chemin)
    vus = set()
    for graine in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=graine)
        vus.add(sp.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True).stdout.strip())
    assert len(vus) == 1, f"uid instable entre processus : {vus}"




@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_deux_applications_ne_tournent_pas_sous_le_meme_uid(tmp_path):
    """Le test qui compte : on lance vraiment deux process et on regarde sous
    quel uid ils tournent. Verifier uid_application() ne prouverait que
    l'arithmetique -- pas que preexec_fn s'en sert."""
    import subprocess as sp

    def uid_reel(nom):
        r = sp.run(["python3", "-c", "import os;print(os.getuid(), os.getgid())"],
                   capture_output=True, text=True,
                   preexec_fn=app.child_setup(nom=nom))
        return r.stdout.strip()

    a, b = uid_reel("facturier"), uid_reel("cahier")
    assert a != b, f"les deux applications tournent sous le meme uid : {a}"
    # Le groupe, lui, reste commun : c'est lui qui garde /workspace editable
    # depuis une session SSH.
    assert a.split()[1] == b.split()[1] == str(app.RUN_AS_GID)
    # Et aucune ne tourne en root.
    assert a.split()[0] != "0" and b.split()[0] != "0"




@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_ne_lit_pas_le_dossier_personnel_d_une_autre(tmp_path, monkeypatch):
    """~/.npmrc et les jetons qu'un outil y depose appartiennent a une
    application, pas au voisinage."""
    import subprocess as sp
    # Les dossiers temporaires de pytest sont en 0700 root : un uid non
    # privilegie ne peut meme pas les traverser, et TOUT lui serait refuse --
    # le test passerait sans rien prouver. On ouvre la traversee (x) sans
    # ouvrir la lecture du contenu.
    for parent in list(tmp_path.parents)[:3] + [tmp_path]:
        try:
            os.chmod(parent, os.stat(parent).st_mode | 0o011)
        except OSError:
            pass
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    maison_a = app.ensure_child_home("facturier")
    with open(os.path.join(maison_a, "secret"), "w") as f:
        f.write("jeton-de-facturier")
    os.chown(os.path.join(maison_a, "secret"),
             app.uid_application("facturier"), app.RUN_AS_GID)

    # PermissionError precisement, pas "une exception quelconque" : un chemin
    # absent ou un interpreteur qui plante donnerait le meme "refuse" et le
    # test passerait pour la mauvaise raison.
    lecteur = ("import sys\n"
               "try:\n"
               "    open(sys.argv[1]).read()\n"
               "    print('LU')\n"
               "except PermissionError:\n"
               "    print('REFUSE')\n"
               "except Exception as e:\n"
               "    print('AUTRE:' + type(e).__name__)\n")
    cible = os.path.join(maison_a, "secret")

    # Temoin : l'application a qui ce dossier appartient, elle, le lit.
    sien = sp.run(["python3", "-c", lecteur, cible], capture_output=True,
                  text=True, preexec_fn=app.child_setup(nom="facturier"))
    assert sien.stdout.strip() == "LU", sien.stdout + sien.stderr

    autre = sp.run(["python3", "-c", lecteur, cible], capture_output=True,
                   text=True, preexec_fn=app.child_setup(nom="cahier"))
    assert autre.stdout.strip() == "REFUSE", autre.stdout + autre.stderr




def test_le_nom_arrive_bien_jusqu_au_preexec(tmp_path, monkeypatch):
    """uid_application() peut etre parfait et ne servir a rien si start() et
    build() oublient de passer le nom. C'est la jointure qui casse en
    silence : sans ce test, les deux applications repartent sous l'uid
    partage et tous les autres tests restent verts."""
    recu = {}
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app, "STATE_DIR", str(tmp_path))
    app._apps_cache["signature"] = None
    projet = tmp_path / "facturier"
    projet.mkdir()
    app.save({"facturier": {"path": str(projet), "command": "true",
                            "port": 9199, "enabled": False,
                            "build_command": "true"}})

    monkeypatch.setattr(app, "child_setup",
                        lambda max_memory_mb=None, nom=None: recu.setdefault("demarrage", nom))
    def _popen(*a, **kw):
        recu["home"] = (kw.get("env") or {}).get("HOME")
        return type("P", (), {"pid": 1, "poll": lambda s: None})()
    monkeypatch.setattr(app.subprocess, "Popen", _popen)
    app.start("facturier")
    assert recu.get("demarrage") == "facturier"
    # HOME suit l'uid : sinon ~/.npmrc et le cache npm restent en commun, et
    # la separation s'arrete a la porte du dossier personnel.
    assert recu["home"] == app.ensure_child_home("facturier")
    assert recu["home"] != app.ensure_child_home("cahier")

    recu.clear()
    monkeypatch.setattr(app, "child_setup",
                        lambda max_memory_mb=None, nom=None: recu.setdefault("build", nom))
    monkeypatch.setattr(app.subprocess, "run",
                        lambda *a, **kw: type("R", (), {"returncode": 0})())
    app.run_build("facturier")
    assert recu.get("build") == "facturier"




def test_l_enveloppe_de_fetch_regarde_l_origine():
    """Trouve par l'audit de la branche : l'enveloppe posait le jeton sur
    TOUTE ecriture, sans regarder la cible. Aucun appel de la page n'est
    absolu aujourd'hui, donc rien ne fuyait -- mais la protection ne tenait
    que par accident. Un fetch('https://...', {method:'POST'}) ajoute demain
    aurait envoye le jeton de session a un tiers (un en-tete non standard
    declenche un pre-vol CORS, qu'un serveur hostile autorise volontiers).

    Test structurel, et il faut le dire : il constate que la garde est encore
    la, pas qu'elle est correcte. Ce qui prouve qu'elle marche, c'est la
    verification au navigateur contre un vrai serveur tiers -- elle ne tient
    pas dans pytest.
    """
    page = _page_panneau("dashboard.html")
    debut = page.index("window.fetch = function")
    enveloppe = page[debut:debut + 600]
    assert "memeOrigine(cible)" in enveloppe, (
        "l'enveloppe de fetch n'appelle plus memeOrigine : le jeton peut "
        "partir vers une autre origine")
    # Et la garde compare bien des origines, pas des prefixes de chaine.
    verif = page[page.index("function memeOrigine"):page.index("window.fetch = function")]
    assert "new URL(url, location.href).origin === location.origin" in verif
    assert "return false" in verif, "une cible illisible doit priver du jeton, pas l'accorder"




# ---------- 25. une donnee ne devient jamais du programme ----------
#
# Le defaut, trouve en auditant la branche et REPRODUIT dans un navigateur :
# le navigateur de dossiers ecrivait
#
#     <div onclick="browse('<chemin>/<nom du dossier>')">
#
# Le libelle etait echappe, le gestionnaire ne l'etait pas. Un dossier nomme
# avec une apostrophe refermait la chaine JavaScript, et la suite s'executait
# dans la page du panneau -- celle qui detient le jeton CSRF et les boutons
# demarrer, arreter, deployer, renommer.
#
# CE QUI REND LA CHOSE SERIEUSE : /workspace est inscriptible depuis une
# session SSH, et par TOUTE application deployee. Chacune tourne sous son
# propre uid, precisement pour ne pas pouvoir toucher au reste ; il lui
# suffisait de creer un dossier et d'attendre un clic de l'administrateur
# pour franchir cette frontiere.
#
# Echapper l'apostrophe n'y aurait rien change : le navigateur decode les
# entites AVANT de lire le JavaScript, et &#39; y redevient une apostrophe.
# La seule parade est de ne pas fabriquer de programme a partir d'une donnee.

def test_aucun_gestionnaire_en_ligne_ne_porte_de_donnee():
    """Un attribut onclick qui interpole une chaine, et le defaut revient.

    La regle se verifie sur le caractere : une apostrophe suivie d'une
    interpolation, c'est une chaine de programme fabriquee a partir d'une
    donnee. Un argument numerique -- (i) sans apostrophes -- reste permis :
    un entier ne peut pas refermer quoi que ce soit.
    """
    page = _page_panneau("dashboard.html")
    fautes = [l.strip() for l in page.split("\n") if "'${" in l]
    assert not fautes, (
        "une donnee est interpolee dans une chaine JavaScript ecrite en "
        "attribut -- passe par data- et la table ACTIONS :\n"
        + "\n".join(fautes[:5]))
    # Et la table existe, avec son ecouteur unique.
    assert "const ACTIONS = {" in page
    assert "e.target.closest('[data-agir]')" in page, (
        "l'ecouteur delegue a disparu : les boutons generes ne font plus rien")




def test_le_navigateur_de_dossiers_construit_du_dom():
    """Le chemin est capture par une fermeture, jamais ecrit comme du texte.

    C'est ce qui rend la regle tenable : il n'y a plus d'echappement a poser
    correctement, donc plus d'echappement a oublier.
    """
    page = _page_panneau("dashboard.html")
    bloc = page.split("function peuplerDossiers(")[1].split("\n}")[0]
    assert "n.textContent = libelle" in bloc, (
        "le nom du dossier repasse par du HTML : textContent n'interprete "
        "rien, une chaine de HTML si")
    assert "n.onclick = () => aller(chemin)" in bloc, (
        "le chemin redevient du texte de programme")
    # Les DEUX navigateurs passent par la : celui du formulaire d'ajout et
    # celui du formulaire de modification. Un seul corrige, c'est l'autre qui
    # porte le defaut.
    for fonction in ("async function browse(p){", "async function browseEdit(p){"):
        corps = page.split(fonction)[1].split("\n}")[0]
        assert "peuplerDossiers(" in corps, fonction




def test_le_navigateur_de_dossiers_lit_ce_que_le_serveur_envoie(
        client, tmp_path, monkeypatch):
    """Les deux cotes du meme contrat, verifies ensemble.

    Le navigateur du formulaire de modification lisait x.path et x.name sur
    les entrees de "dirs". Or /api/browse renvoie des NOMS -- des chaines.
    Il affichait donc autant de lignes vides que de dossiers, toutes vers
    « undefined ». Personne ne l'avait vu : aucun test ne regardait ce
    rendu, et le navigateur du formulaire d'ajout, lui, marchait.
    """
    racine = tmp_path / "racine"
    (racine / "un projet").mkdir(parents=True)
    (racine / ".cache").mkdir()
    monkeypatch.setattr(app, "ROOT", str(racine))
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = client.get("/api/browse", query_string={"path": str(racine)}).get_json()
    # Des chaines, et les dossiers caches restent caches.
    assert d["dirs"] == ["un projet"], d["dirs"]
    assert all(isinstance(x, str) for x in d["dirs"])

    # Et la page les consomme comme des chaines.
    page = _page_panneau("dashboard.html")
    bloc = page.split("function peuplerDossiers(")[1].split("\n}")[0]
    assert "(d.dirs || []).forEach(nom =>" in bloc, (
        "la page relit un objet la ou le serveur envoie un nom")
    assert ".path" not in bloc.split("d.path + '/' + nom")[0].split("forEach")[-1], (
        "une entree de dirs est traitee comme un objet")




# ---------- 26. ce que le navigateur a le droit de faire ----------
#
# Le panneau ne posait aucun en-tete de securite. Ils ne reparent rien : ils
# reduisent ce qu'une faille permet, et c'est exactement pour le jour ou la
# premiere barriere cede qu'on les veut deja en place.

def test_le_panneau_pose_ses_entetes_de_securite(client):
    """Les quatre en-tetes, sur la page du panneau ET sur celle de connexion.

    Celle de connexion surtout : c'est elle qui porte le mot de passe, et
    c'est la seule que voit quelqu'un qui n'a pas encore de session.
    """
    for chemin in ("/login", "/"):
        client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
        r = client.get(chemin)
        for entete in ("Content-Security-Policy", "X-Frame-Options",
                       "Referrer-Policy", "X-Content-Type-Options"):
            assert entete in r.headers, f"{entete} manque sur {chemin}"
    csp = r.headers["Content-Security-Policy"]
    # Le clickjacking ne se rattrape pas au jeton CSRF : un clic dans une
    # iframe invisible est un VRAI clic, dans une VRAIE session.
    assert "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"
    # Un script injecte ne doit pouvoir ni charger du code ailleurs, ni
    # renvoyer ce qu'il a lu.
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp
    # Et la page doit continuer de s'afficher : sa police, son logo en
    # data: et ses styles en ligne sont dans la regle.
    for permis in ("img-src 'self' data:", "font-src 'self'",
                   "style-src 'self' 'unsafe-inline'"):
        assert permis in csp, permis




def _nonce_de_la_csp(csp):
    m = re.search(r"script-src 'self' 'nonce-([A-Za-z0-9_-]{20,})'(;|$)", csp)
    assert m, "script-src sans nonce : " + csp
    return m.group(1)


def test_la_csp_du_panneau_n_execute_que_ses_propres_scripts(client, monkeypatch):
    """Audit du 2026-11-11, R2 : 'unsafe-inline' laissait s'executer un
    script injecte dans une page du panneau. script-src n'a plus que 'self'
    et le nonce de la reponse ; chaque balise <script> en ligne de la page
    porte ce nonce, chaque <style> aussi, et il change a chaque reponse."""
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    vus = set()
    for chemin in ("/login?x", "/", "/parametres", "/hors-ligne.html", "/deconnexion.html"):
        r = client.get(chemin)
        csp = r.headers["Content-Security-Policy"]
        nonce = _nonce_de_la_csp(csp)
        assert "'unsafe-inline'" not in csp.split("script-src")[1], csp
        assert f"style-src-elem 'self' 'nonce-{nonce}'" in csp
        page = r.get_data(as_text=True)
        assert "__NONCE__" not in page, chemin
        for balise in re.findall(r"<(?:script|style)\b[^>]*>", page):
            if "src=" in balise:
                continue
            assert f'nonce="{nonce}"' in balise, (chemin, balise)
        vus.add(nonce)
    assert len(vus) == 5, "un nonce par reponse"
    # Une donnee qui contient le marqueur ne recoit pas le nonce : il est pose
    # sur le gabarit, avant les donnees.
    monkeypatch.setattr(app, "lire_profil", lambda *a: {"prenom": "__NONCE__", "nom": ""})
    page = client.get("/").get_data(as_text=True)
    assert '"prenom": "__NONCE__"' in page


def _noms_appeles(texte):
    return set(re.findall(r'data-(?:agir|changer|saisir|entree)="([^"$]+)"', texte))


def test_le_balisage_du_panneau_ne_porte_aucun_programme():
    """La CSP refuse les gestionnaires en ligne : il n'en reste aucun dans
    les pages du panneau ni dans le HTML que fabriquent leurs scripts, et
    chaque appel qu'elles nomment est dans une table FERMEE (ACTIONS ou
    APPELS) -- un element injecte ne peut rien nommer d'autre."""
    gestionnaire = re.compile(r"\son[a-z]+\s*=\s*[\"'\\\\]")
    panneau = (_lire_source("app-manager/app/dashboard.html")
               + _lire_source("app-manager/app/panneau.js"))
    pages = {"panneau": panneau,
             "login": _lire_source("app-manager/app/login.html"),
             "suppression": _lire_source("app-manager/app/suppression.html"),
             "ouvrir": _lire_source("app-manager/app/ouvrir.html"),
             "hors-ligne": app.PAGE_HORS_LIGNE}
    for nom, texte in pages.items():
        sans_commentaires = re.sub(r"<!--.*?-->|/\*.*?\*/", "", texte, flags=re.S)
        assert not gestionnaire.findall(sans_commentaires), nom
        assert "javascript:" not in sans_commentaires, nom
    # Les deux tables du panneau, disjointes, et rien hors d'elles.
    actions = set(re.findall(r"^\s*'([a-z-]+)':", panneau.split("const ACTIONS = {")[1]
                             .split("\n};")[0], re.M))
    bloc = panneau.split("const APPELS = {")[1].split("\n};")[0]
    appels = set(re.findall(r"\b([A-Za-z]\w*)(?=,|\(\)\{|\(cle\)\{)", bloc))
    assert actions and appels and not actions & appels
    speciaux = {"app-masquer", "presentation-visible", "presentation-fichier"}
    hors_table = _noms_appeles(panneau) - actions - appels - speciaux
    assert not hors_table, hors_table
    # Chaque reference directe de APPELS est une fonction declaree : un nom
    # absent arreterait tout le script au chargement.
    directs = re.findall(r"\b([A-Za-z]\w*),", bloc.split("/* Ceux qui lisent")[0])
    for nom in directs:
        assert (re.search(rf"^\s*(async\s+)?function {nom}\(", panneau, re.M)
                or f"{nom}=i=>" in panneau), nom
    login = pages["login"]
    table = login.split("const APPELS = {")[1].split("};")[0]
    assert _noms_appeles(login) <= set(re.findall(r"\w+", table))


def test_hsts_n_est_pose_que_derriere_du_tls(client, monkeypatch):
    """HSTS est un ENGAGEMENT : le navigateur refuse ensuite le http pendant
    toute sa duree. Le poser sur une installation servie en clair la rendrait
    injoignable, sans moyen de revenir en arriere."""
    monkeypatch.setattr(app, "https_actif", lambda: False)
    assert "Strict-Transport-Security" not in client.get("/login").headers
    monkeypatch.setattr(app, "https_actif", lambda: True)
    assert "Strict-Transport-Security" in client.get("/login").headers
    # Sans includeSubDomains : les sous-domaines ne nous appartiennent pas.
    assert "includeSubDomains" not in app.HSTS




def test_une_application_hebergee_garde_sa_propre_politique(deux_espaces):
    """La politique du panneau casserait la premiere application qui charge
    une police, une carte ou un script ailleurs -- et ce serait decider a sa
    place. Restent nosniff, et frame-ancestors -- qui ne dit rien de ce que
    l'application charge, seulement de QUI peut l'encadrer (voir
    proteger_l_encadrement)."""
    r = deux_espaces.get("/public/")
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp.startswith("frame-ancestors ") and ";" not in csp, (
        "seul frame-ancestors est impose aux applications : " + csp)
    assert "X-Frame-Options" not in r.headers
    assert r.headers.get("X-Content-Type-Options") == "nosniff"




# ---------- 27. la taille d'une requete ----------
#
# Il n'y en avait aucune. Seize fils, seize corps sans fin, et le service
# tombe sans qu'un seul mot de passe ait ete tente.

def test_un_corps_trop_gros_est_refuse(client):
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    trop = "x" * (app.CORPS_MAX_PANNEAU + 1)
    r = client.put("/api/categories", data=trop, content_type="application/json")
    assert r.status_code == 413
    # Un message lisible, et en JSON : la page attend du JSON, et la page
    # d'erreur par defaut de Flask lui aurait rendu du HTML.
    assert "maximum" in (r.get_json() or {}).get("error", "")
    # Ce qui passe, passe toujours.
    r = client.put("/api/categories", json={"categories": ["essais"]})
    assert r.status_code == 200




def test_le_plafond_des_applications_est_plus_large_que_celui_du_panneau():
    """Deux usages sans rapport : les API du panneau n'envoient qu'un logo,
    une application hebergee peut recevoir un fichier."""
    assert app.CORPS_MAX_APPLICATION > app.CORPS_MAX_PANNEAU
    # Le filet de Flask vaut le plus large des deux : c'est lui qui coupe un
    # corps annonce sans taille, et il ne doit pas bloquer les applications.
    assert app.flask_app.config["MAX_CONTENT_LENGTH"] == app.CORPS_MAX_APPLICATION
    # La garde decide sur le Content-Length : elle refuse AVANT de lire.
    corps = inspect.getsource(app.borner_le_corps)
    assert "request.content_length" in corps
    assert "get_data" not in corps


# ---------- 28. aucun service ne peut prendre toute la machine ----------
#
# Vecu a l'envers : rien ne bornait rien. Une application partie en boucle
# emportait la machine entiere -- et le panneau avec, donc on ne pouvait meme
# plus l'arreter depuis le panneau. Les journaux, eux, grossissaient jusqu'a
# remplir le disque, et la sonde l'annoncait quand il l'etait deja.




def test_chaque_service_est_borne_dans_les_deux_composes():
    """Memoire, processeur, processus, journaux. Les quatre, partout.

    Ce sont des PLAFONDS et non des reservations : ce qu'on empeche, c'est
    qu'UN service prenne tout -- pas de repartir la memoire a l'avance.
    """
    for chemin in _composes():
        services = _services_du_compose(chemin)
        # Les memes services des deux cotes : un compose qui en oublie un
        # est une installation qui ne demarre pas pareil selon la porte
        # d'entree.
        assert len(services) == 7, (chemin, sorted(services))
        for nom, reglages in services.items():
            assert "pids_limit" in reglages or "deploy_pids" in reglages, (
                f"{nom} n'a pas de limite processus dans {os.path.basename(chemin)}")
            assert "logging" in reglages, f"{nom} n'a pas de logging dans {os.path.basename(chemin)}"
            assert ("cpus" in reglages and "mem_limit" in reglages) or "deploy" in reglages, (
                f"{nom} n'a pas de limites de ressources dans {os.path.basename(chemin)}")
        texte = open(chemin, encoding="utf-8").read()
        # La rotation est ce qui empeche un journal de remplir le disque :
        # le driver seul ne borne rien.
        # Un compte par SERVICE, et non un nombre ecrit en dur : un service
        # ajoute demain doit faire echouer ce test tant qu'il n'a pas sa
        # rotation, sans qu'on ait a se souvenir de mettre un chiffre a jour.
        assert texte.count('max-size: "10m"') == len(services), chemin
        assert texte.count('max-file: "3"') == len(services), chemin




def test_le_panneau_a_la_part_la_plus_large():
    """Ce conteneur porte le panneau, les builds npm ET toutes les
    applications deployees, qui sont ses processus fils. Le borner comme un
    service ordinaire, c'est une application tuee au hasard en plein build."""
    def en_mo(v):
        return int(v[:-1]) * 1024 if v.endswith("g") else int(v.rstrip("m"))
    for chemin in _composes():
        services = _services_du_compose(chemin)
        part = {n: en_mo(r.get("mem_limit", r.get("deploy_memory")))
            for n, r in services.items()}
        assert part["codelab-app-manager"] == max(part.values()), chemin
        # Et le relais nginx, qui ne fait que relayer, reste AU PLANCHER --
        # il n'est plus seul dessus depuis qu'une reservation exterieure
        # empeche de descendre plus bas (voir le test du plancher), mais
        # aucun service ne doit passer sous lui.
        assert part["codelab-dagster-proxy"] == min(part.values()), chemin




def test_le_shell_tient_une_session_vscode():
    """A 896 Mo et 512 processus, l'agent IA et la vue Git de VS Code
    redemarraient en boucle : le noyau tuait l'hote d'extensions (serveur
    VS Code, Codex, serveurs de langage, JVM de Database Client JDBC, git).
    pids_limit compte les fils d'execution, et une JVM en ouvre des dizaines.
    """
    def en_mo(v):
        v = str(v)
        return int(v[:-1]) * 1024 if v.endswith("g") else int(v.rstrip("m"))
    for chemin in _composes():
        dev = _services_du_compose(chemin)["codelab-dev"]
        memoire = dev.get("mem_limit", dev.get("deploy_memory"))
        pids = dev.get("pids_limit", dev.get("deploy_pids"))
        assert en_mo(memoire) >= 1280, (chemin, memoire)
        assert int(pids) >= 1024, (chemin, pids)




# ---------- 31. une page d'erreur n'affiche pas ce qu'on lui envoie ----------

def test_la_page_d_erreur_echappe_ce_qu_elle_affiche(client):
    """Le nom vient de l'URL, et ressortait tel quel.

    « /zz<script>alert(1)</script>/ » sur l'origine des applications rendait
    une page portant la balise. Elle n'y porte pas la session du panneau --
    c'est tout l'interet de la separation des origines -- mais elle permettait
    de se faire passer pour n'importe quelle application hebergee, dans son
    origine, devant l'utilisateur qui vient de cliquer.
    """
    r = client.get("/zz<script>alert(1)</script>/")
    assert b"<script>alert" not in r.data
    assert b"&lt;script&gt;" in r.data
    # La page reste lisible : on echappe, on ne tronque pas.
    assert b"Aucune application" in r.data




def test_la_vue_d_ensemble_ne_double_plus_les_messages():
    """Deux endroits ou lire la meme chose, c'est deux endroits ou croire les
    avoir traites. Les demandes vivent dans leur application, et nulle part
    ailleurs dans le panneau."""
    page = _page_panneau("dashboard.html")
    ensemble = page.split('<div id="sec-overview"')[1].split('<div id="sec-apps"')[0]
    assert "zone-messages" not in ensemble
    assert "msg-liste" not in ensemble
    assert 'id="sec-messages"' not in page and "side-count-messages" not in page




def test_les_demandes_ont_quitte_le_menu():
    """Les demandes sont une application du hub, au meme rang que le
    diagnostic : le menu du panneau n'en a plus l'entree."""
    page = _page_panneau("dashboard.html")
    menu = page.split('<nav class="sidebar"')[1].split("</nav>")[0]
    rubriques = re.findall(r'data-sec="([a-z]+)"', menu)
    assert "messages" not in rubriques, rubriques
    assert rubriques[0] == "hub", rubriques




def test_le_diagnostic_porte_sa_marque_partout(hub_dagster):
    """Le meme dessin dans les trois endroits ou on le voit.

    La tuile du hub (servie par le panneau), la barre du haut de la page, et
    l'icone de l'onglet. Les deux services ne peuvent pas partager de code --
    le diagnostic tourne dans un autre processus, sans acces au panneau --
    mais ils partagent la FORME, et c'est ce qui doit rester vrai.
    """
    import app as diag
    # Un seul trace dans la page, utilise deux fois.
    assert diag.MARQUE_TRACE in diag._barre(""), "la barre a perdu la marque"
    assert diag.MARQUE_TRACE.replace(" ", "%20") in diag.FAVICON, (
        "l'icone de l'onglet ne suit plus la marque")
    page = diag.app.test_client().get("/").data.decode()
    assert 'rel="icon"' in page

    # Et le panneau sert bien une icone a la tuile, distincte de la lettre.
    hub_dagster.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = hub_dagster.get("/api/icon/diagnostic")
    assert r.status_code == 200 and b"<text" not in r.data
    # Le meme geste : un trace ouvert, pas une lettre ni un cercle.
    assert b"<path" in r.data and b"stroke" in r.data




def test_les_sondes_regardent_au_dela_de_la_stack():
    """Trois familles ajoutees, toutes en LECTURE SEULE.

    Les sondes d'avant s'arretaient a "le service repond". Celles-ci
    regardent ce que la stack porte (les applications une par une), ce
    qu'elle use (le disque, les journaux) et ce qu'elle laisse ouvert.
    """
    assert callable(check_applications)
    assert callable(check_espace_disque)
    assert callable(check_surface_exposee)
    for fn in (check_applications, check_espace_disque, check_surface_exposee):
        ok, nom, detail = fn()
        assert isinstance(ok, Etat) and nom and detail




def test_l_alerte_disque_demande_les_deux_conditions(tmp_path, monkeypatch):
    """Un pourcentage seul se trompe dans les deux sens.

    89 % d'un disque de 250 Go laisse 28 Go -- des mois de marge, et la sonde
    crierait pour rien. C'est le defaut qu'avait la premiere version, vu sur
    la machine de developpement.
    """
    assert SEUIL_DISQUE == 85 and SEUIL_LIBRE_GO == 5
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "checks.py"),
               encoding="utf-8").read()
    # On lit le CORPS de la sonde, pas le fichier : sinon l'assertion se
    # trouve elle-meme -- sa propre chaine est dans le fichier -- et la
    # verification passe quelle que soit la sonde. Defaut vu en mutant : la
    # mutation survivait.
    corps = src[src.index("def check_espace_disque("):]
    corps = corps[:corps.index("\ndef ", 10)]
    assert "libre < SEUIL_LIBRE_GO" in corps, (
        "l'alerte disque ne demande plus les deux conditions")




def test_la_sonde_des_applications_nomme_ce_qui_cloche(tmp_path, monkeypatch):
    """Elle ne dit pas "il y a un probleme" : elle dit lequel, et ou."""
    etat = tmp_path / "etat"
    etat.mkdir()
    # La base du panneau, telle que la sonde la lit (en lecture seule).
    monkeypatch.setattr(app, "DB_FILE", str(etat / "codelab.db"))
    app.save({
        "disparue": {"path": "/n-existe-pas", "command": "python3 a.py",
                     "port": 9201, "enabled": False},
        "muette": {"path": str(tmp_path), "command": "python3 -V",
                   "port": 9204, "enabled": True},
    })
    monkeypatch.setenv("APP_MANAGER_STATE", str(etat))
    ok, _, detail = check_applications()
    # Rang 2 : une application tombee n'est pas la stack tombee.
    assert ok is Etat.ALERTE
    assert "disparue" in detail and "dossier introuvable" in detail
    assert "muette" in detail and "9204" in detail




# ---------- 24. le dossier personnel ne se detourne pas ----------
#
# Trouve par l'audit de la branche, et c'etait une escalade de privileges
# introduite par la separation des uid elle-meme. CHILD_HOME etait ecrivable
# par le groupe partage : une application pouvait effacer son propre dossier,
# le remplacer par un lien symbolique vers n'importe quel dossier de la
# machine, et attendre. Au redemarrage, le chown pose par root suivait le
# lien -- le dossier des secrets devenait sa propriete, et il ne restait
# qu'a remplacer credentials.env.

@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_ne_detourne_pas_le_chown_de_root(tmp_path, monkeypatch):
    """Le scenario complet, joue tel quel : lien pose par l'application,
    ensure_child_home rappele par root, et la cible qui ne doit PAS changer
    de proprietaire."""
    import subprocess as sp
    for parent in list(tmp_path.parents)[:3] + [tmp_path]:
        try:
            os.chmod(parent, os.stat(parent).st_mode | 0o011)
        except OSError:
            pass
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    convoite = tmp_path / "secrets"
    convoite.mkdir(mode=0o700)
    (convoite / "credentials.env").write_text("POSTGRES_PASSWORD=secret\n")
    avant = os.stat(convoite).st_uid

    maison = app.ensure_child_home("alpha")
    # Le parent n'est pas ecrivable par le groupe : c'est ce qui bloque tout.
    assert os.stat(app.CHILD_HOME).st_mode & 0o020 == 0, \
        "CHILD_HOME est ecrivable par le groupe : une application peut y " \
        "remplacer un dossier par un lien"

    sp.run(["sh", "-c", f"rmdir '{maison}'; ln -s '{convoite}' '{maison}'"],
           capture_output=True, preexec_fn=app.child_setup(nom="alpha"))
    app.ensure_child_home("alpha")
    assert os.stat(convoite).st_uid == avant, \
        "root a suivi un lien pose par l'application et lui a donne la cible"




@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_n_ecrase_pas_le_dossier_d_une_autre(tmp_path, monkeypatch):
    """Meme cause, autre effet : deposer un .profile chez la voisine, que
    "bash -lc" execute sous SON uid -- l'isolation par uid annulee par le
    dossier personnel."""
    import subprocess as sp
    for parent in list(tmp_path.parents)[:3] + [tmp_path]:
        try:
            os.chmod(parent, os.stat(parent).st_mode | 0o011)
        except OSError:
            pass
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    app.ensure_child_home("alpha")
    chez_beta = app.ensure_child_home("beta")

    sp.run(["sh", "-c",
            f"rm -rf '{chez_beta}' && mkdir -m 0777 '{chez_beta}' && "
            f"echo charge > '{chez_beta}/.profile'"],
           capture_output=True, preexec_fn=app.child_setup(nom="alpha"))
    assert not os.path.exists(os.path.join(chez_beta, ".profile"))
    assert os.stat(chez_beta).st_uid == app.uid_application("beta")




@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_entree_hostile_deja_en_place_est_retiree(tmp_path, monkeypatch):
    """Une installation mise a jour peut deja porter un lien pose du temps ou
    c'etait possible. Il ne doit pas etre suivi, mais enleve."""
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    os.makedirs(app.CHILD_HOME, exist_ok=True)
    convoite = tmp_path / "secrets"
    convoite.mkdir()
    avant = os.stat(convoite).st_uid
    os.symlink(str(convoite),
               os.path.join(app.CHILD_HOME, str(app.uid_application("alpha"))))

    maison = app.ensure_child_home("alpha")
    assert not os.path.islink(maison), "le lien est toujours la"
    assert os.path.isdir(maison)
    assert os.stat(convoite).st_uid == avant, "la cible du lien a ete chownee"




def test_l_application_travaille_quand_meme_chez_elle(tmp_path, monkeypatch):
    """Une correction qui rend le dossier personnel inutilisable ne vaut
    rien : c'est la que vit le cache npm, d'un build a l'autre."""
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    maison = app.ensure_child_home("alpha")
    assert os.path.isdir(maison)
    # Le groupe traverse le parent, sinon l'application n'atteint pas son
    # propre dossier.
    assert os.stat(app.CHILD_HOME).st_mode & 0o010, \
        "le groupe ne peut plus traverser CHILD_HOME"
    # Et deux appels de suite ne se marchent pas dessus.
    assert app.ensure_child_home("alpha") == maison


# ---------- 25. une origine a part pour les applications ----------
#
# Tant que les applications etaient servies sous le port du panneau, elles
# vivaient dans SON origine : une XSS dans une application quelconque lisait
# la page du panneau, donc le jeton, donc pilotait la stack. Aucun jeton n'y
# pouvait rien -- le script hostile etait du bon cote de la barriere.
#
# Ce qui doit rester vrai : chaque port ne sert que ce qui lui appartient.




# ---------- 26. isolation du systeme de fichiers ----------
#
# L'uid par application separait les process. Il ne separait pas les
# fichiers : /workspace est partage par le groupe codelab -- il le faut,
# sinon le code n'est plus modifiable en SSH -- donc une application lisait
# le .env de sa voisine. Chaque application recoit maintenant sa propre vue,
# dans laquelle /workspace ne contient qu'elle.

def test_la_commande_est_enveloppee_par_defaut(monkeypatch):
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
    # Verdict fixe : ce test porte sur le branchement, pas sur le
    # noyau de la machine qui lance la suite.
    app._isolement.update(verdict=True, raison="")
    argv, env = app.commande_isolee("facturier", "/workspace/facturier", "npm start", {})
    assert argv[0] == "unshare"
    # --user : c'est ce qui evite d'avoir besoin de CAP_SYS_ADMIN, donc de
    # defaire le cap_drop du compose.
    assert "--user" in argv and "--mount" in argv
    # Le chemin et la commande passent par l'environnement : interpoles dans
    # le script, un nom avec une apostrophe le casserait, et un chemin venu
    # d'ailleurs deviendrait une injection.
    assert env["CODELAB_PROJET"] == "/workspace/facturier"
    assert env["CODELAB_COMMANDE"] == "npm start"
    assert "npm start" not in " ".join(argv)




def test_sans_unshare_l_application_demarre_quand_meme(monkeypatch):
    """Une image reconstruite ailleurs, un noyau ou les namespaces
    utilisateur sont coupes : mieux vaut une application qui tourne sans
    isolation qu'une application qui ne tourne pas."""
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    monkeypatch.setattr(app.shutil, "which", lambda n: None)
    argv, env = app.commande_isolee("facturier", "/workspace/facturier", "npm start", {})
    assert argv == ["bash", "-lc", "npm start"]
    assert env == {}




def test_l_isolation_se_coupe_par_application_et_globalement(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
    # Verdict fixe : ce test porte sur le branchement, pas sur le
    # noyau de la machine qui lance la suite.
    app._isolement.update(verdict=True, raison="")
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    apps = {"facturier": {"path": "/w/f", "isolation": False}}
    argv, _ = app.commande_isolee("facturier", "/w/f", "x", apps)
    assert argv[0] == "bash", "le reglage par application n'est pas lu"
    monkeypatch.setattr(app, "ISOLER_APPS", False)
    argv, _ = app.commande_isolee("facturier", "/w/f", "x", {})
    assert argv[0] == "bash", "le reglage global n'est pas lu"




@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_isolee_ne_voit_plus_sa_voisine(tmp_path, monkeypatch):
    """Le test qui compte : on lance vraiment la commande et on regarde ce
    qu'elle voit. Verifier la ligne de commande ne prouverait que la ligne
    de commande."""
    import subprocess as sp
    # Ce test-ci VEUT la vraie mesure : le filet pose un verdict de repli
    # pour que personne ne lance unshare par accident, on le leve ici.
    app._isolement.update(verdict=None, raison="")
    if not app.isolement_disponible():
        pytest.skip("l'isolement ne fonctionne pas sur cette machine : "
                    + (app._isolement["raison"] or "raison inconnue"))
    racine = tmp_path / "ws"
    (racine / "facturier").mkdir(parents=True)
    (racine / "cahier").mkdir()
    (racine / "cahier" / ".env").write_text("SECRET=xyz\n")
    _ouvrir_traversee(list(tmp_path.parents)[:3] + [tmp_path, racine])
    monkeypatch.setattr(app, "ROOT", str(racine))
    monkeypatch.setattr(app, "ISOLER_APPS", True)

    def voit(apps):
        cmd = ("ls %s; [ -r %s/cahier/.env ] && echo VOISINE_LISIBLE || echo VOISINE_INVISIBLE"
               % (racine, racine))
        argv, env_iso = app.commande_isolee("facturier", str(racine / "facturier"), cmd, apps)
        r = sp.run(argv, cwd=str(racine / "facturier"),
                   env=dict(os.environ, HOME="/tmp", **env_iso),
                   capture_output=True, text=True,
                   preexec_fn=app.child_setup(nom="facturier"))
        return r.stdout + r.stderr

    # Temoin : sans isolation la voisine est visible -- sinon ce test
    # passerait au vert pour la mauvaise raison.
    sortie = voit({"facturier": {"path": str(racine / "facturier"), "isolation": False}})
    assert "cahier" in sortie and "VOISINE_LISIBLE" in sortie, sortie

    sortie = voit({"facturier": {"path": str(racine / "facturier")}})
    assert "VOISINE_INVISIBLE" in sortie, sortie
    assert "cahier" not in sortie, "le projet voisin est encore visible : " + sortie
    assert "facturier" in sortie, "l'application ne voit plus son propre projet : " + sortie




@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_ce_qu_une_application_isolee_ecrit_arrive_sur_le_disque(tmp_path, monkeypatch):
    """Une tmpfs posee au mauvais endroit ferait disparaitre le resultat de
    chaque build, en silence -- le pire defaut possible ici."""
    import subprocess as sp
    # Ce test-ci VEUT la vraie mesure : le filet pose un verdict de repli
    # pour que personne ne lance unshare par accident, on le leve ici.
    app._isolement.update(verdict=None, raison="")
    if not app.isolement_disponible():
        pytest.skip("l'isolement ne fonctionne pas sur cette machine : "
                    + (app._isolement["raison"] or "raison inconnue"))
    racine = tmp_path / "ws"
    (racine / "facturier").mkdir(parents=True)
    _ouvrir_traversee(list(tmp_path.parents)[:3] + [tmp_path, racine])
    os.chown(racine / "facturier", app.uid_application("facturier"), app.RUN_AS_GID)
    monkeypatch.setattr(app, "ROOT", str(racine))
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    argv, env_iso = app.commande_isolee(
        "facturier", str(racine / "facturier"),
        "mkdir -p dist && echo resultat > dist/index.html", {})
    sp.run(argv, cwd=str(racine / "facturier"),
           env=dict(os.environ, HOME="/tmp", **env_iso),
           capture_output=True, text=True,
           preexec_fn=app.child_setup(nom="facturier"))
    produit = racine / "facturier" / "dist" / "index.html"
    assert produit.exists(), "le resultat du build a disparu avec le namespace"
    assert produit.read_text().strip() == "resultat"
    # Et il reste modifiable depuis une session SSH : c'est la raison d'etre
    # du groupe partage, l'isolation ne doit pas la casser.
    assert produit.stat().st_mode & 0o020, oct(produit.stat().st_mode)




# ---------- 33. arreter demande une capability ----------
#
# Signale par Lucas, apres la premiere correction : « Le panneau n'a pas pu
# signaler le processus (pid 11) : [Errno 1] Operation not permitted ». Le
# message etait enfin juste -- c'etait bien un refus du noyau -- mais le
# bouton ne marchait toujours pas.
#
# LA CAUSE. Le panneau tourne en root, avec cap_drop: ALL et cinq capabilities
# rendues. KILL n'y etait pas. Or le noyau n'autorise un signal que si
# l'expediteur a CAP_KILL, ou si son uid est celui de la cible -- et depuis
# que chaque application tourne sous son propre uid, root n'a jamais l'uid
# d'aucune. Toutes les applications etaient donc inarretables, sur toute
# installation durcie.

def test_le_panneau_a_le_droit_d_arreter_ses_applications():
    """La capability manquante, dans les DEUX composes.

    C'est un test de configuration et pas de code, parce que le defaut etait
    la : aucune ligne de Python n'aurait pu arreter quoi que ce soit sans
    elle.
    """
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    fichiers = [os.path.join(racine, n)
                for n in ("docker-compose.yml", "docker-compose-casaos.yml")]
    presents = [f for f in fichiers if os.path.exists(f)]
    if not presents:
        pytest.skip("composes absents de cette image")
    assert len(presents) == 2, "un des deux composes manque"
    for chemin in presents:
        texte = open(chemin, encoding="utf-8").read()
        # Le bloc du service, et lui seul : « codelab-app-manager » parait
        # aussi dans le nom de l'image et dans container_name, et un split
        # naif tomberait au milieu d'une ligne.
        bloc = texte.split("\n  codelab-app-manager:\n")[1]
        bloc = bloc.split("cap_add:")[1].split("\n\n")[0]
        assert "- KILL" in bloc, (
            f"{os.path.basename(chemin)} : sans CAP_KILL, le panneau ne peut "
            f"arreter aucune application -- chacune tourne sous son propre uid")
        # Temoin : les autres capabilities n'ont pas disparu au passage.
        for indispensable in ("- SETUID", "- SETGID"):
            assert indispensable in bloc, chemin




def test_un_refus_du_noyau_repart_sous_l_uid_de_l_application(monkeypatch):
    """Le repli, pour les installations qui tournent encore sur l'ancien
    compose : le signal part d'un enfant qui a pris l'uid de la cible."""
    essais = []

    def _killpg(pgid, sig):
        essais.append(("killpg", pgid, sig))
        if len(essais) == 1:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", _killpg)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # Le fork est simule : on execute le corps de l'enfant dans le test, et
    # l'on verifie qu'il abandonne bien l'uid AVANT de signaler.
    bascules = []
    monkeypatch.setattr(os, "setgroups", lambda g: bascules.append(("groupes", tuple(g))))
    monkeypatch.setattr(os, "setgid", lambda g: bascules.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: bascules.append(("uid", u)))

    sorties = []
    def _fork():
        return 0            # on joue l'enfant
    def _exit(code):
        sorties.append(code)
        raise SystemExit(code)
    monkeypatch.setattr(os, "fork", _fork)
    monkeypatch.setattr(os, "_exit", _exit)

    with pytest.raises(SystemExit):
        app._signaler_groupe(4242, 15, 1234)

    assert sorties == [0], "l'enfant n'a pas signale puis rendu la main"
    assert bascules == [("groupes", (app.RUN_AS_GID,)), ("gid", app.RUN_AS_GID),
                        ("uid", 1234)]
    # Deux tentatives : la directe, refusee, puis celle de l'enfant.
    assert [e[0] for e in essais] == ["killpg", "killpg"]




def test_un_refus_qui_persiste_est_dit_avec_son_remede(survivants, monkeypatch):
    """Quand meme le repli echoue, le message doit nommer la capability : sans
    elle, on cherche du cote de l'application, ou rien n'y peut quoi que ce
    soit."""
    class _Vivant:
        pid = 11

        def poll(self):
            return None

    app.procs["site"] = _Vivant()
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)   # pas root : pas de repli
    def _refus(*_a):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "killpg", _refus)

    erreur = app.stop("site")
    assert erreur and "KILL" in erreur, erreur
    assert "cap_add" in erreur
    assert "11" in erreur
    app.procs.clear()


# ---------- 36. ce que l'agent de developpement ne peut pas atteindre -----
#
# codelab-dev porte un agent qui execute des commandes sans qu'on relise
# chacune. Ce qui l'empeche de sortir de son perimetre ne tient PAS a ses
# consignes -- AGENTS.md est lu par un modele, pas applique par le noyau --
# mais a trois faits, verifies ici :
#
#   1. la session SSH tourne en « vscode », qui n'appartient a aucun groupe
#      d'administration ;
#   2. aucune regle sudoers ne lui est deposee, donc « sudo » reste installe
#      mais hors de portee depuis une session ;
#   3. credentials.env -- mot de passe d'administration du panneau, cle
#      privee SSO, mot de passe SMTP -- est monte en lecture seule et reste
#      en 0600 root, donc illisible par « vscode ».
#
# Ces trois faits tiennent aujourd'hui par ABSENCE : personne n'a ajoute la
# ligne qui les romprait. Un « usermod -aG sudo vscode » glisse un jour pour
# installer un paquet ouvrirait credentials.env a l'agent, sans que rien ne
# le signale. C'est exactement ce qu'un test doit retenir.




def test_l_utilisateur_des_sessions_n_est_dans_aucun_groupe_d_administration():
    """« vscode » appartient a « codelab », et a rien d'autre.

    Le groupe codelab donne l'ecriture partagee sur /workspace, et c'est tout
    ce qu'il donne. Tout autre groupe secondaire -- sudo, admin, wheel, root,
    docker -- rendrait la machine entiere accessible depuis une session, donc
    a l'agent qui y tourne.
    """
    source = _dockerfile_dev()
    groupes = re.findall(r"usermod\s+-aG\s+([A-Za-z0-9_,-]+)\s+vscode", source)
    assert groupes, "aucun usermod -aG pour vscode : le Dockerfile a change de forme"
    for liste in groupes:
        assert set(liste.split(",")) == {"codelab"}, (
            f"vscode ajoute au(x) groupe(s) {liste} : seul « codelab » est prevu")
    # Et pas par une autre porte : ni creation directe dans un groupe
    # d'administration, ni ajout par gpasswd/adduser.
    for interdit in ("-aG sudo", "-aG wheel", "-aG root", "-aG docker",
                     "gpasswd -a vscode", "adduser vscode sudo"):
        assert interdit not in source, f"« {interdit} » dans dev/Dockerfile"




def test_aucune_regle_sudoers_n_est_deposee_pour_les_sessions():
    """sudo est installe, et reste inutilisable depuis une session.

    L'image embarque sudo -- un environnement de developpement en a l'usage
    quand on entre en root par « docker exec ». Ce qui ne doit jamais
    arriver, c'est qu'une session SSH puisse s'en servir : ni regle dans
    /etc/sudoers.d, ni NOPASSWD, ni mot de passe pose sur le compte.
    """
    for source, nom in ((_dockerfile_dev(), "dev/Dockerfile"),
                        (_entrypoint_dev(), "dev/entrypoint.sh")):
        for interdit in ("NOPASSWD", "sudoers", "chpasswd", "passwd vscode"):
            assert interdit not in source, f"« {interdit} » dans {nom}"




def test_le_fichier_de_secrets_est_en_lecture_seule_pour_codelab_dev():
    """credentials.env n'est pas modifiable depuis le conteneur du shell.

    Trois services le montent en lecture-ecriture parce qu'ils y ecrivent
    leur propre bloc (postgres, app-manager, dagster-proxy). codelab-dev,
    lui, ne fait que lire le mot de passe Postgres : un montage accessible en
    ecriture lui permettrait de reecrire les secrets de la stack.
    """
    for chemin in _composes():
        services = _services_du_compose(chemin)
        assert "codelab-dev" in services, chemin
        texte = open(chemin, encoding="utf-8").read()
        bloc = texte.split("  codelab-dev:", 1)[1].split("\n  codelab-", 1)[0]
        montages = [l.strip()[2:] for l in bloc.splitlines()
                    if l.strip().startswith("- /DATA")]
        config = [m for m in montages if m.endswith(":/var/lib/codelab/config")
                  or m.endswith(":/var/lib/codelab/config:ro")]
        assert config, (chemin, montages)
        for m in config:
            assert m.endswith(":ro"), (
                f"{os.path.basename(chemin)} : codelab-dev monte le dossier de "
                f"secrets en ecriture ({m})")




def test_l_entrypoint_du_shell_ne_relache_pas_les_droits_du_fichier_de_secrets():
    """Personne ne rend credentials.env lisible « pour que ce soit plus simple ».

    Le fichier est ecrit en 0600 root par codelab-postgres. Un chmod ou un
    chown pose ici le rendrait lisible par vscode -- donc par l'agent -- et
    la lecture seule du montage n'y changerait rien.
    """
    source = _entrypoint_dev()
    for ligne in source.splitlines():
        nue = ligne.strip()
        if nue.startswith("#"):
            continue
        if ("chmod" in nue or "chown" in nue or "setfacl" in nue) and "credentials" in nue:
            raise AssertionError(f"dev/entrypoint.sh touche aux droits du fichier "
                                 f"de secrets : {nue}")




def test_le_gitconfig_pose_par_le_shell_ne_porte_ni_identite_ni_secret():
    """Un .gitconfig d'amorce, et rien de plus.

    Deux choses n'ont rien a faire dans un fichier ecrit par l'image :

      - une identite. Personne ici ne sait qui est l'utilisateur, et une
        valeur inventee signerait ses commits d'un faux nom. Sans user.name
        ni user.email, git refuse de commiter avec un message qui dit
        exactement quoi taper -- c'est le bon comportement.
      - un moyen de s'authentifier. Un jeton ou un credential helper pointant
        sur un fichier du workspace serait lisible par tout ce qui tourne en
        « vscode », dont l'agent, et par Dagster et app-manager qui montent le
        meme dossier. Les cles restent sur le poste du developpeur et sont
        pretees le temps d'une session (ssh -A).
    """
    source = _entrypoint_dev()
    debut = source.find('cat > "$GITCONFIG"')
    assert debut != -1, "le .gitconfig d'amorce a disparu de l'entrypoint"
    bloc = source[debut:source.find("GITCFG\nfi", debut)]
    for interdit in ("user.name", "user.email", "[user]", "credential",
                     "helper", "token", "PAT", "password"):
        assert interdit not in bloc, f"« {interdit} » dans le .gitconfig d'amorce"
    # Ecrit une seule fois : un reglage fait a la main depuis une session doit
    # survivre a une mise a jour d'image.
    assert '[ ! -f "$GITCONFIG" ]' in source, (
        "le .gitconfig est reecrit a chaque demarrage : les reglages de "
        "l'utilisateur seraient perdus")




def test_la_config_git_survit_a_une_mise_a_jour_d_image(tmp_path):
    """« git config --global » ecrivait dans /home, recree a chaque mise a
    jour : user.name et user.email disparaissaient a chaque compose pull.
    Le fichier vit desormais dans /workspace, ~/.gitconfig n'est qu'un lien."""
    import subprocess
    maison, ws, script = _bloc_gitconfig(tmp_path)
    subprocess.run(["bash", "-c", script], check=True, timeout=30)
    lien, vrai = maison / ".gitconfig", ws / ".codelab" / "gitconfig"
    assert lien.is_symlink() and lien.resolve() == vrai.resolve()
    assert "defaultBranch = main" in vrai.read_text()
    # git suit le lien en ecriture : le reglage atterrit dans /workspace.
    env = dict(os.environ, HOME=str(maison), XDG_CONFIG_HOME=str(maison / ".config"))
    subprocess.run(["git", "config", "--global", "user.name", "Quelqu'un"],
                   check=True, env=env, timeout=30)
    assert lien.is_symlink()
    assert "Quelqu'un" in vrai.read_text()
    # Mise a jour d'image : /home repart de zero, le reglage est toujours la.
    lien.unlink()
    subprocess.run(["bash", "-c", script], check=True, timeout=30)
    sortie = subprocess.run(["git", "config", "--global", "user.name"], env=env,
                            capture_output=True, text=True, check=True, timeout=30)
    assert sortie.stdout.strip() == "Quelqu'un"




def test_l_ancien_gitconfig_est_repris_jamais_efface(tmp_path):
    import subprocess
    maison, ws, script = _bloc_gitconfig(tmp_path)
    lien, vrai = maison / ".gitconfig", ws / ".codelab" / "gitconfig"
    # Premier demarrage de la nouvelle version : l'ancien fichier est repris.
    lien.write_text("[user]\n\tname = Ancien\n")
    subprocess.run(["bash", "-c", script], check=True, timeout=30)
    assert lien.is_symlink() and "Ancien" in vrai.read_text()
    # Un fichier ordinaire alors que /workspace en a deja un : mis de cote.
    lien.unlink()
    lien.write_text("[user]\n\tname = Autre\n")
    subprocess.run(["bash", "-c", script], check=True, timeout=30)
    assert lien.is_symlink() and "Ancien" in vrai.read_text()
    assert "Autre" in (maison / ".gitconfig.avant-codelab").read_text()




def test_l_entrypoint_du_shell_ne_depose_aucun_jeton_d_acces():
    """Aucun secret d'authentification ecrit dans le workspace.

    Vaut pour git comme pour le reste : ce conteneur ne fabrique pas de
    credentials permanents, parce qu'un agent qui tourne sans personne devant
    les utiliserait sans qu'on l'ait voulu.
    """
    source = _entrypoint_dev()
    for ligne in source.splitlines():
        nue = ligne.strip()
        if nue.startswith("#") or not nue:
            continue
        minuscules = nue.lower()
        if any(motif in minuscules for motif in
               ("git_askpass", "credential.helper", "ghp_", "github_token",
                ".git-credentials")):
            raise AssertionError(f"dev/entrypoint.sh depose un moyen "
                                 f"d'authentification : {nue}")




def test_l_image_dev_ne_porte_que_codex():
    """OpenClaw, acpx et codex-acp sont retires : un gateway, des
    connecteurs de messagerie et une chaine de six etapes pour un chat que
    Codex fait seul, dans le terminal. Ils pesaient des centaines de Mo sur
    une machine dont le disque a deborde."""
    source = _dockerfile_dev()
    assert "npm install -g @openai/codex" in source
    for retire in ("openclaw", "acpx", "codex-acp"):
        assert retire not in source.lower(), f"{retire} revient dans l'image"
    assert "deb.nodesource.com/setup_24.x" in source, (
        "Node vient encore d'apt : trop ancien")
    assert "\n        nodejs \\" not in source, (
        "nodejs est encore installe depuis apt, en conflit avec NodeSource")




def test_la_configuration_des_agents_vit_dans_le_workspace():
    """Elle doit survivre a une recreation du conteneur.

    Vecu a l'envers pour Codex : range sous /home/vscode, son auth.json
    disparaissait a chaque mise a jour d'image et il fallait refaire
    « codex login ».
    """
    source = _entrypoint_dev()
    assert 'CODEX_HOME_DIR="${CODEX_HOME:-$WORKSPACE_DIR/.codex}"' in source


# ---------- une base, un compte ----------




def test_le_mot_de_passe_d_une_base_se_derive_pareil_partout():
    """Panneau (Python), poste (sha256sum) et Postgres (SQL) : une seule
    formule, ecrite trois fois -- elles doivent donner la meme chose."""
    import subprocess
    attendu = app.mot_de_passe_base("recettes", "cle-admin")
    shell = subprocess.run(["sh", "-c", "printf '%s' 'cle-admin:codelab-base:recettes' | sha256sum | cut -c1-64"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert attendu == shell == hashlib.sha256(b"cle-admin:codelab-base:recettes").hexdigest()
    sql = open(_sql_proteger(), encoding="utf-8").read()
    assert "sha256(convert_to(:'cle' || ':codelab-base:' || :'base', 'UTF8'))" in sql
    entree = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"), encoding="utf-8").read()
    assert '"$PG_PASSWORD:codelab-base:$b" | sha256sum | cut -c1-64' in entree




def test_chaque_application_ne_recoit_que_le_compte_de_sa_base(tmp_path, monkeypatch):
    projet = tmp_path / "recettes"
    projet.mkdir()
    monkeypatch.setattr(app, "read_shared_value",
                        lambda k: {"POSTGRES_PASSWORD": "cle-admin", "POSTGRES_USER": "codelab"}.get(k))
    vues = []
    monkeypatch.setattr(app, "assurer_base", lambda b: vues.append(b) or True)
    a = {"path": str(projet)}
    assert app.identifiants_base(a) == {"POSTGRES_USER": "recettes", "CODELAB_DB": "recettes",
                                        "POSTGRES_PASSWORD": app.mot_de_passe_base("recettes", "cle-admin")}
    assert vues == ["recettes"], "la base est preparee avant le lancement"
    # CODELAB_DB dans le .env du projet l'emporte sur le nom du dossier.
    (projet / ".env").write_text("# config\nexport CODELAB_DB='carnet'\n")
    assert app.identifiants_base(a)["POSTGRES_USER"] == "carnet"
    # Une base de la stack, ou un nom qui n'en est pas un : aucun compte.
    for nom in ("dagster", "codelab", "1projet", "a b"):
        (projet / ".env").write_text(f"CODELAB_DB={nom}\n")
        assert app.identifiants_base(a) == {}, nom
    # Le compte administrateur ne part plus vers les applications.
    config = tmp_path / "credentials.env"
    config.write_text("POSTGRES_USER=codelab\nPOSTGRES_PASSWORD=cle-admin\nREDIS_URL=redis://x\n")
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(config))
    partages = app.secrets_partages()
    assert "POSTGRES_PASSWORD" not in partages and "POSTGRES_USER" not in partages
    assert partages["REDIS_URL"] == "redis://x"
    source = _source_du_panneau()
    assert source.count("**identifiants_base(a)") == 2, "lancement ET build"




def test_une_base_un_compte_dans_un_vrai_postgres(pg_reel, tmp_path, monkeypatch):
    """De bout en bout : le panneau prepare la base d'une application, son
    compte y fait tout, n'entre nulle part ailleurs ; ce que l'administrateur
    (Dagster) y ecrit lui reste accessible ; au demarrage, codelab-postgres
    reprend une base d'avant ce mecanisme et ferme celles de la stack."""
    import subprocess
    psql, cle = pg_reel["psql"], pg_reel["cle"]
    monkeypatch.setattr(app, "PROTEGER_SQL", _sql_proteger())
    monkeypatch.setattr(app, "_bases_assurees", {})
    monkeypatch.setattr(app, "read_shared_value", lambda k: {
        "POSTGRES_PASSWORD": cle, "POSTGRES_USER": "codelab", "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": str(pg_reel["port"]), "POSTGRES_DB": "dagster"}.get(k))
    for nom in ("alpha", "beta"):
        (tmp_path / nom).mkdir()
        ids = app.identifiants_base({"path": str(tmp_path / nom)})
        assert ids["POSTGRES_USER"] == nom
    mdp_alpha = app.mot_de_passe_base("alpha", cle)
    r = psql("-c", "CREATE TABLE notes(t text); INSERT INTO notes VALUES ('x'); "
                   "SELECT current_schema(), count(*) FROM notes",
             utilisateur="alpha", mdp=mdp_alpha, base="alpha")
    assert r.returncode == 0 and r.stdout.strip() == "dagster|1", r.stderr
    for autre in ("beta", "dagster", "codelab"):
        r = psql("-c", "SELECT 1", utilisateur="alpha", mdp=mdp_alpha, base=autre)
        assert r.returncode != 0, f"alpha entre dans {autre}"
    # Le compte n'a aucun pouvoir de plus.
    r = psql("-c", "CREATE DATABASE pirate", utilisateur="alpha", mdp=mdp_alpha, base="alpha")
    assert r.returncode != 0
    # Ce que l'administrateur cree ensuite (Dagster) reste au projet.
    assert psql("-c", "CREATE TABLE dagster.par_dagster(x int)", base="alpha").returncode == 0
    r = psql("-c", "INSERT INTO par_dagster VALUES (1); SELECT count(*) FROM par_dagster",
             utilisateur="alpha", mdp=mdp_alpha, base="alpha")
    assert r.returncode == 0 and r.stdout.strip() == "1", r.stderr

    # Une base d'avant : creee par l'administrateur, tables a lui.
    assert psql("-c", "CREATE DATABASE ancien", base="dagster").returncode == 0
    assert psql("-c", "CREATE SCHEMA dagster; CREATE TABLE dagster.mesures(id serial, v int); "
                      "INSERT INTO dagster.mesures(v) VALUES (7); "
                      "CREATE VIEW dagster.vue AS SELECT v FROM dagster.mesures",
                base="ancien").returncode == 0
    # Le bloc de codelab-postgres, tel quel, contre ce serveur.
    entree = open(os.path.join(_racine_depot(), "postgres", "entrypoint.sh"), encoding="utf-8").read()
    debut = entree.index("PROTEGER_SQL=/usr/local/share/codelab/proteger-base.sql")
    fin = entree.index("\n}\n", entree.index("proteger_les_bases() {")) + 3
    script = (f"PROTEGER_SQL={_sql_proteger()}\n" + entree[debut:fin].split("\n", 1)[1]
              + "\nproteger_les_bases\n")
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=120, env=dict(
        os.environ, PGHOST=pg_reel["socket"], PGPORT=str(pg_reel["port"]), POSTGRES_USER="codelab",
        CODELAB_INSTANCE_DB="dagster", BASE_ESSAI="codelab_essai_restauration", PG_PASSWORD=cle,
        CODELAB_PROJECT_SCHEMA="dagster"))
    assert r.returncode == 0 and "base(s) de projet sous leur propre compte" in r.stdout, r.stderr
    mdp_ancien = app.mot_de_passe_base("ancien", cle)
    r = psql("-c", "SELECT count(*) FROM vue; INSERT INTO mesures(v) VALUES (8)",
             utilisateur="ancien", mdp=mdp_ancien, base="ancien")
    assert r.returncode == 0 and r.stdout.strip().startswith("1"), r.stderr
    for pile in ("dagster", "codelab"):
        r = psql("-c", "SELECT 1", utilisateur="ancien", mdp=mdp_ancien, base=pile)
        assert r.returncode != 0, f"la base de la stack {pile} est restee ouverte"
    # Rejouer ne casse rien.
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=120, env=dict(
        os.environ, PGHOST=pg_reel["socket"], PGPORT=str(pg_reel["port"]), POSTGRES_USER="codelab",
        CODELAB_INSTANCE_DB="dagster", BASE_ESSAI="codelab_essai_restauration", PG_PASSWORD=cle,
        CODELAB_PROJECT_SCHEMA="dagster"))
    assert r.returncode == 0, r.stderr




def test_le_terminal_a_le_compte_du_projet_ou_il_se_trouve(tmp_path):
    import subprocess
    ws = tmp_path / "ws"
    (ws / "recettes" / "src").mkdir(parents=True)
    (ws / "autre").mkdir()
    (ws / "nomdiff").mkdir()
    (ws / "nomdiff" / ".env").write_text("CODELAB_DB='carnet'\n")
    (tmp_path / ".codelab-bases").write_text("recettes mdpR\ncarnet mdpC\n")
    crochet = os.path.join(_racine_depot(), "dev", "bases-du-dossier.sh")
    script = f""". {crochet}
for d in {ws}/recettes/src {ws}/autre {ws}/nomdiff {tmp_path}; do
  cd "$d"; codelab_base_du_dossier
  echo "${{PGUSER:-}}|${{PGPASSWORD:-}}|${{POSTGRES_USER:-}}|${{CODELAB_DB:-}}"
done
echo "$PROMPT_COMMAND"
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CODELAB_WORKSPACE": str(ws)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["recettes|mdpR|recettes|recettes", "|||", "carnet|mdpC|carnet|carnet",
                                     "|||", "codelab_base_du_dossier"]
    # Le profil des sessions n'exporte plus le compte administrateur.
    entree = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"), encoding="utf-8").read()
    bloc = entree[entree.index("echo '# Genere par codelab-dev au demarrage"):entree.index('} > "$PROFILE"')]
    assert "PG_PASSWORD" not in bloc and "PGUSER" not in bloc and ". $BASES_DU_DOSSIER" in bloc
    assert "export PGPASSWORD" not in open(os.path.join(_racine_depot(), "dev", "codelab"),
                                           encoding="utf-8").read()


# ---------- le coffre : reglages et adresses chiffres, en base et en local ----




def test_les_adresses_des_comptes_ne_sont_jamais_en_clair():
    app.ecrire_utilisateurs({"marie": {"sel": "aa", "hash": "bb", "projets": [],
                                       "email": "marie@exemple.fr"}})
    assert app.lire_utilisateurs()["marie"]["email"] == "marie@exemple.fr"
    assert b"marie@exemple.fr" not in _octets_de_l_etat()
    assert stat.S_IMODE(os.stat(app.DB_FILE).st_mode) == 0o600




def test_le_serveur_d_envoi_et_son_mot_de_passe_sont_chiffres():
    app.ecrire_smtp_personnalise(app._normalise_smtp({
        "host": "smtp.gmail.com", "port": 587, "user": "moi@gmail.com",
        "password": "motdepasse-tres-secret"}))
    assert app.lire_smtp_personnalise()["password"] == "motdepasse-tres-secret"
    for secret in (b"motdepasse-tres-secret", b"moi@gmail.com"):
        assert secret not in _octets_de_l_etat(), secret
    app.effacer_smtp_personnalise()
    assert app.lire_smtp_personnalise() is None




def test_une_cle_perdue_ne_casse_rien_et_ne_detruit_rien():
    """Cle changee : la valeur illisible n'est ni effacee ni ecrasee, le
    panneau fait comme si elle manquait plutot que de tomber -- et la cle
    retrouvee la rend de nouveau lisible."""
    from cryptography.fernet import Fernet
    app.ecrire_reglage("alertes_admin", ["moi@exemple.fr"])
    bonne = app._cle_chiffrement
    app._cle_chiffrement = Fernet.generate_key().decode()
    app._coffre["donnees"] = None
    assert app.reglage("alertes_admin") is None
    assert app.stockage.lire(app.DB_FILE, "SELECT 1 FROM reglages_chiffres "
                                          "WHERE cle = 'alertes_admin'")
    app._cle_chiffrement = bonne
    app._coffre["donnees"] = None
    assert app.reglage("alertes_admin") == ["moi@exemple.fr"]





def test_les_destinataires_suivent_un_renommage_et_partent_avec_l_application():
    app.ecrire_reglage("alertes_par_app", {"vieux": ["a@x.fr"]})
    app._renommer_references("vieux", "neuf")
    assert app.reglage("alertes_par_app") == {"neuf": ["a@x.fr"]}




def test_la_base_recoit_les_reglages_chiffres_et_sait_les_rendre():
    """En base : chiffre, jamais en clair. Et si la copie locale disparait
    (dossier d'etat restaure, disque change), le panneau se reprend depuis
    la base."""
    base = _FausseBase()
    app.ecrire_reglage("smtp", {"host": "smtp.x.fr", "password": "secret-en-base"})
    app._pg_synchroniser_reglages(base)
    assert set(base.lignes) == {"smtp"}
    assert b"secret-en-base" not in base.lignes["smtp"]

    with app.stockage.transaction(app.DB_FILE) as cx:     # copie locale perdue
        cx.execute("DELETE FROM reglages_chiffres")
    app._coffre["donnees"] = None
    app._pg_synchroniser_reglages(base)
    assert app.reglage("smtp")["password"] == "secret-en-base"
    app._coffre["donnees"] = None                        # et elle est reecrite
    assert app.reglage("smtp")["password"] == "secret-en-base"
    source = inspect.getsource(app._pg_ecrire_utilisateurs)
    assert 'UPDATE utilisateurs SET email = \'\'' in source, (
        "les adresses recopiees en clair par une version precedente doivent partir")




def test_credentials_env_porte_les_identifiants_par_defaut_et_la_cle():
    app.ecrire_bloc_panneau("mdp", "cle-session", "")
    env = open(app.SHARED_ENV_FILE).read()
    assert "APP_MANAGER_ADMIN_LOGIN=admin" in env
    assert f"APP_MANAGER_CHIFFREMENT_CLE={app._cle_chiffrement}" in env
    # Jamais transmise aux applications : prefixe du panneau.
    assert not [c for c in app.secrets_partages() if "CHIFFREMENT" in c]
    racine = _racine_depot()
    assert "REDIS_USER=default" in open(os.path.join(racine, "redis", "entrypoint.sh")).read()
    assert "CODELAB_INTERNE_MAILPIT_UI_USER=codelab" in open(
        os.path.join(racine, "dagster", "proxy", "entrypoint.sh")).read()




def test_la_cle_de_chiffrement_n_est_jamais_regeneree(monkeypatch):
    cle = app._cle_chiffrement
    app.ecrire_bloc_panneau("mdp", "cle-session", "")
    monkeypatch.setattr(app, "_cle_chiffrement", "")
    app.bootstrap_secrets()
    assert app._cle_chiffrement == cle




def test_l_onglet_d_une_application_prend_son_logo_si_elle_n_en_a_pas():
    page = b"<html><head><title>x</title></head><body>ok</body></html>"
    entetes = [("Content-Type", "text/html"), ("Content-Length", str(len(page)))]
    with app.flask_app.test_request_context("/site/"):
        data, sortants = app.injecter_icone(page, 200, entetes, "mon site")
    assert b'<link rel="icon" href="/api/icon/mon%20site"></head>' in data
    assert ("Content-Length", str(len(data))) in sortants
    # Une application qui declare deja son icone la garde.
    propre = page.replace(b"</head>", b'<link rel="icon" href="/f.png"></head>')
    with app.flask_app.test_request_context("/site/"):
        data, _ = app.injecter_icone(propre, 200, entetes, "site")
    assert data == propre
    # Le panneau garde le logo CodeLab dans ses onglets.
    panneau = _page_panneau("dashboard.html")
    assert panneau.count('rel="icon"') == 1




def test_une_application_s_installe_comme_une_app_sur_l_ecran_d_accueil():
    """Le proxy pose manifeste, icone et balises iOS dans chaque page --
    sans jamais ecraser ce que l'application declare elle-meme."""
    page = b"<html><head><title>x</title></head><body>ok</body></html>"
    entetes = [("Content-Type", "text/html"), ("Content-Length", str(len(page)))]
    with app.flask_app.test_request_context("/site/"):
        data, sortants = app.injecter_webapp(page, 200, entetes, "mon site",
                                             'Mon <site>')
    tete = data[:data.index(b"</head>")]
    assert b'<link rel="manifest" href="/mon%20site/__codelab/manifest.webmanifest">' in tete
    assert b'<link rel="apple-touch-icon" href="/mon%20site/__codelab/icone.png">' in tete
    assert b'name="apple-mobile-web-app-capable" content="yes"' in tete
    assert b'name="mobile-web-app-capable" content="yes"' in tete
    assert b'content="Mon &lt;site&gt;"' in tete, "le titre doit etre echappe"
    # Le ruban reste visible en mode app : rien ne le masque.
    assert b"display-mode" not in tete
    assert ("Content-Length", str(len(data))) in sortants

    # Ce que l'application declare deja reste a elle.
    propre = page.replace(b"</head>", b'<link rel="manifest" href="/m.json">'
                          b'<link rel="apple-touch-icon" href="/i.png">'
                          b'<meta name="apple-mobile-web-app-title" content="A"></head>')
    with app.flask_app.test_request_context("/site/"):
        data, _ = app.injecter_webapp(propre, 200, entetes, "site", "site")
    assert data.count(b'rel="manifest"') == 1
    assert data.count(b"apple-touch-icon") == 1
    assert data.count(b"apple-mobile-web-app-title") == 1

    # Les memes refus que le ruban : on ne touche ni a une erreur, ni a
    # autre chose que du HTML, ni a un corps compresse.
    with app.flask_app.test_request_context("/site/"):
        assert app.injecter_webapp(page, 404, entetes, "s", "s")[0] == page
        assert app.injecter_webapp(page, 200, [("Content-Type", "application/json")],
                                   "s", "s")[0] == page
        assert app.injecter_webapp(page, 200, entetes + [("Content-Encoding", "gzip")],
                                   "s", "s")[0] == page




def test_le_manifeste_d_une_application_reste_dans_son_projet(client, monkeypatch, tmp_path):
    """Portee /<nom>/ : l'app installee ne deborde pas sur les autres projets.
    Le manifeste et l'icone suivent les controles d'acces du proxy, et
    repondent meme application arretee."""
    import struct
    fichier = tmp_path / "apps.json"
    app._apps_cache["signature"] = None
    projet = tmp_path / "projet"
    projet.mkdir()
    app.save({"site": {"path": str(projet), "command": "x", "port": 9101,
                       "enabled": True, "titre": "Mon site"},
              "prive": {"path": str(projet), "command": "x", "port": 9102,
                        "enabled": True, "visibility": "privee"}})
    monkeypatch.setattr(app, "is_running", lambda n: False)

    r = client.get("/site/__codelab/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    m = json.loads(r.get_data(as_text=True))
    assert m["scope"] == m["start_url"] == "/site/"
    assert m["display"] == "standalone" and m["name"] == "Mon site"

    # Sans logo dans le projet : le logo CodeLab, opaque, en PNG.
    r = client.get("/site/__codelab/icone.png")
    assert r.status_code == 200 and r.data == app.ICONE_ECRAN_ACCUEIL_PNG
    # Un logo PNG depose dans le projet prend la place.
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + b"\x00" * 17
    (projet / "icon.png").write_bytes(png)
    r = client.get("/site/__codelab/icone.png")
    assert r.data == png and r.mimetype == "image/png"
    # Un SVG, qu'iOS ne sait pas afficher, retombe sur le logo CodeLab.
    (projet / "icon.png").unlink()
    (projet / "icon.svg").write_text("<svg/>")
    assert client.get("/site/__codelab/icone.png").data == app.ICONE_ECRAN_ACCUEIL_PNG

    # Application privee : rien sans session, pas meme son nom.
    r = client.get("/prive/__codelab/manifest.webmanifest", follow_redirects=False)
    # Vers la connexion, avec la suite qui ramenera dans l'application.
    assert r.status_code == 302 and "/login?suite=/ouvrir.app/prive/" in r.headers["Location"]




def test_le_panneau_installe_ouvre_les_applications_sans_en_sortir(client, monkeypatch, tmp_path):
    """Dans le panneau ajoute a l'ecran d'accueil, une application ouverte
    directement vivait sur une autre origine : iOS l'affichait dans son
    navigateur integre (« Done », fleches). Elle s'ouvre maintenant encadree
    dans une page du panneau, qui n'autorise a encadrer QUE l'origine des
    applications."""
    fichier = tmp_path / "apps.json"
    app._apps_cache["signature"] = None
    app.save({"site": {"path": str(tmp_path), "command": "x", "port": 9101,
                       "enabled": True, "titre": "Mon <site>"}})
    monkeypatch.setattr(app, "origines_separees", lambda: True)
    monkeypatch.setattr(app, "origine_applications", lambda: "http://hote:9002")

    # Sans session : rien, pas meme le nom.
    assert client.get("/ouvrir.html?app=site").status_code in (302, 401)

    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = client.get("/ouvrir.html?app=site")
    assert r.status_code == 200
    corps = r.get_data(as_text=True)
    assert '<iframe name="codelab-app" src="http://hote:9002/site/"' in corps
    assert "Mon &lt;site&gt;" in corps and "Mon <site>" not in corps
    # L'onglet porte l'icone de l'application ouverte, pas celle du panneau.
    assert '<link rel="icon" href="/api/icon/site">' in corps
    csp = r.headers["Content-Security-Policy"]
    assert csp.endswith("; frame-src http://hote:9002")
    assert "frame-ancestors 'none'" in csp, "la page elle-meme ne s'encadre pas"
    assert client.get("/ouvrir.html?app=inconnue").status_code == 404

    # Le ruban ramene la FENETRE au hub, pas le cadre.
    with app.flask_app.test_request_context("/site/"):
        assert b'target="_top"' in app.ruban_retour()

    # Le hub n'y passe qu'en mode app.
    html = _page_panneau("dashboard.html")
    assert "window.CODELAB_MODE_APP === true" in html and "/ouvrir.html?app=" in html




def test_seul_le_panneau_peut_encadrer_une_application(monkeypatch):
    """Clickjacking : aucun autre site ne pose une application dans un cadre.
    Et le panneau installe, qui l'encadre, n'est plus bloque par un
    X-Frame-Options pose d'office par le framework de l'application."""
    monkeypatch.setattr(app, "origines_separees", lambda: True)
    with app.flask_app.test_request_context("/site/", base_url="http://10.126.126.1:9002"):
        sortants = app.proteger_l_encadrement(
            [("Content-Type", "text/html"), ("X-Frame-Options", "SAMEORIGIN")])
    assert not [k for k, _ in sortants if k.lower() == "x-frame-options"]
    csp = dict(sortants)["Content-Security-Policy"]
    assert csp == ("frame-ancestors http://10.126.126.1:9001 "
                   "http://localhost:9001 http://127.0.0.1:9001")

    # Une application qui a SA regle frame-ancestors la garde.
    propre = [("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")]
    with app.flask_app.test_request_context("/site/"):
        assert app.proteger_l_encadrement(propre) == propre

    # Sans separation des origines, le panneau EST l'origine des applications.
    monkeypatch.setattr(app, "origines_separees", lambda: False)
    with app.flask_app.test_request_context("/site/"):
        assert dict(app.proteger_l_encadrement([]))["Content-Security-Policy"] \
            == "frame-ancestors 'self'"





def test_la_protection_d_encadrement_couvre_toute_reponse_d_application(
        client, monkeypatch, tmp_path):
    """Pages d'erreur et redirections comprises, pas seulement les pages
    relayees depuis l'application."""
    fichier = tmp_path / "apps.json"
    app._apps_cache["signature"] = None
    app.save({"site": {"path": str(tmp_path), "command": "x", "port": 9101,
                       "enabled": True}})
    monkeypatch.setattr(app, "is_running", lambda n: False)
    r = client.get("/site/")
    assert r.status_code == 503
    assert "frame-ancestors" in r.headers.get("Content-Security-Policy", "")
    assert "X-Frame-Options" not in r.headers




def test_mailpit_a_l_interface_de_codelab():
    """La page de Mailpit est la notre, construite sur son API : le proxy
    sert index.html sur / et /message/<id>, derriere la session, et laisse
    passer l'API, le direct et l'apercu jusqu'a Mailpit."""
    racine = _racine_depot()
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    page = conf.split("location ~ ^/(message/[A-Za-z0-9]+)?$ {", 1)[1].split("\n    }", 1)[0]
    assert "auth_request /_auth;" in page and "try_files /index.html =404;" in page
    # Aucun script ni style en ligne, aucune origine tierce.
    csp = re.search(r'Content-Security-Policy "([^"]+)"', page).group(1)
    assert "script-src 'self';" in csp and "style-src 'self';" in csp
    assert "unsafe" not in csp and "frame-ancestors 'self'" in csp
    # Le reste va a Mailpit, derriere la meme session et son mot de passe.
    reste = conf.split("    location / {", 1)[1]
    assert "auth_request /_auth;" in reste and "include /etc/nginx/codelab/mailpit-auth.conf;" in reste
    # Le direct (websocket) : Mailpit n'accepte que si Host porte le port.
    assert "proxy_set_header Host              $http_host;" in reste
    assert "proxy_set_header Upgrade    $http_upgrade;" in reste
    # Plus rien n'est injecte dans les pages de Mailpit.
    assert "sub_filter" not in conf
    dockerfile = open(os.path.join(racine, "dagster", "proxy", "Dockerfile"), encoding="utf-8").read()
    assert "dagster/proxy/index.html dagster/proxy/theme.js" in dockerfile
    assert "/usr/share/nginx/codelab/mailpit/" in dockerfile
    assert "jakarta-latin.woff2 /usr/share/nginx/codelab/mailpit/jakarta.woff2" in dockerfile
    assert "LICENCE-jakarta.txt /usr/share/nginx/codelab/mailpit/" in dockerfile

    index = _mailpit_ui("index.html")
    # Tous les scripts sont des fichiers ; aucun attribut style ni <style>.
    for balise in re.findall(r"<script[^>]*>", index):
        assert 'src="/_codelab/' in balise, balise
    assert "<style" not in index and ' style="' not in index
    assert re.search(r"\son[a-z]+=", index) is None, "gestionnaire en ligne"
    # Le retour au hub est LE MEME ruban que sur les autres applications :
    # meme balisage, et la meme feuille, regle pour regle (RUBAN_CSS).
    assert '<details id="codelab-ruban">' in index
    assert 'id="codelab-retour-hub" title="Revenir au hub CodeLab"' in index
    assert 'target="_top"' in index.split('id="codelab-retour-hub"', 1)[0].rsplit("<a ", 1)[1]
    assert _regles_css(_mailpit_ui("app.css"), "#codelab-r") == _regles_css(app.RUBAN_CSS, "#codelab-r")


def _regles_css(feuille, prefixe):
    """Les regles dont le selecteur commence par prefixe, sans espaces :
    {selecteur: [declarations]}. Pour comparer deux copies d'une feuille,
    l'une ecrite sur une ligne, l'autre aeree."""
    sans_commentaires = re.sub(r"/\*.*?\*/", "", feuille, flags=re.S)
    regles = {}
    for selecteur, corps in re.findall(r"([^{}]+)\{([^{}]*)\}", sans_commentaires):
        selecteur = re.sub(r"\s+", "", selecteur)
        if selecteur.startswith(prefixe):
            regles[selecteur] = [re.sub(r"\s+", "", d) for d in re.split(r";(?![^(]*\))", corps)
                                 if d.strip()]
    return regles


def test_le_ruban_est_replie_par_defaut_et_le_meme_partout():
    """Vecu : « Le bouton codelab dans les applications doit pouvoir se
    developper et reduire (petite fleche au bord de l'ecran) pour laisser la
    place a l'app par defaut », puis « plus gros, lorsque je developpe, pas
    de bouton pour reduire, il reste developpe que 10 secondes si je ne
    clique pas ». La meme feuille et le meme script dans les applications,
    Mailpit et Dagster."""
    with app.flask_app.test_request_context("/"):
        ruban = app.ruban_retour().decode()
    assert '<details id="codelab-ruban">' in ruban and "<details open" not in ruban
    assert "<summary" in ruban and 'aria-label="Afficher le bouton CodeLab"' in ruban
    regles = _regles_css(app.RUBAN_CSS, "#codelab")
    assert "left:var(--codelab-bord-gauche,0px)" in regles["#codelab-ruban"]
    assert "bottom:calc(14px+var(--codelab-bord-bas,0px))" in regles["#codelab-ruban"]
    # Plus grosse languette ; depliee, elle disparait (pas de bouton pour
    # reduire) et le script replie au bout de 10 s.
    assert {"width:30px", "height:46px"} <= set(regles["#codelab-ruban>summary"])
    assert regles["#codelab-ruban[open]>summary"] == ["display:none"]
    assert "setTimeout(function(){d.open=false;},10000)" in app.RUBAN_JS
    assert "try{" in app.RUBAN_JS and "</" not in app.RUBAN_JS
    assert "'" not in app.RUBAN_CSS + app.RUBAN_JS, "la chaine de nginx est delimitee par des apostrophes"
    nginx = open(os.path.join(_racine_depot(), "dagster", "proxy", "nginx.conf"), encoding="utf-8").read()
    assert "<style>" + app.RUBAN_CSS + "</style><details id=\"codelab-ruban\">" in nginx
    assert "</details><script>" + app.RUBAN_JS + "</script></body>" in nginx
    assert app.RUBAN_JS in _mailpit_ui("app.js")


def test_le_contenu_d_un_mail_ne_touche_jamais_la_page():
    """Les textes passent par textContent ; le HTML d'un mail ne s'affiche
    que dans un cadre isole, sans scripts."""
    js = _mailpit_ui("app.js")
    for interdit in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert interdit not in js, interdit
    cadre = js.split('h("iframe"', 1)[1].split("});", 1)[0]
    assert 'sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox"' in cadre
    assert "allow-scripts" not in cadre
    assert 'src: "/view/" + enc(id) + ".html"' in cadre
    # Les liens d'un mail s'ouvrent dans un nouvel onglet, sans referer.
    assert 'a.setAttribute("rel", "noopener noreferrer");' in js




def test_mailpit_a_sa_nouvelle_icone_sur_la_tuile_et_dans_la_page():
    """L'enveloppe blanche sur degrade violet des maquettes : la meme sur la
    tuile du hub et dans la barre de l'interface. Jamais dans un onglet."""
    # Une seule source pour l'enveloppe : ICONE_MAILPIT. La copie
    # logo.svg de l'interface Mailpit, que rien ne servait, est partie.
    logo = app.ICONE_MAILPIT
    assert not os.path.exists(os.path.join(_racine_depot(), "dagster", "proxy", "logo.svg"))
    assert "#7c6cf6" in logo and "#4f46e5" in logo
    assert 'fill="none" stroke="#fff"' in logo and 'fill="#fff"' not in logo
    assert _mailpit_ui("logo.png", binaire=True)[:8] == b"\x89PNG\r\n\x1a\n"
    index = _mailpit_ui("index.html")
    barre = index.split('class="marque"', 1)[1].split("</a>", 1)[0]
    assert 'stop-color="#7c6cf6"' in barre and 'stop-color="#4f46e5"' in barre




def test_postgres_realigne_le_mot_de_passe_a_chaque_demarrage():
    """credentials.env regenere, donnees gardees : la base doit reprendre le
    mot de passe du fichier, sinon tous les clients sont refuses."""
    racine = _racine_depot()
    script = open(os.path.join(racine, "postgres", "entrypoint.sh"),
                  encoding="utf-8").read()
    fonction = script.split("realigner_mot_de_passe() {", 1)[1].split("\n}\n", 1)[0]
    assert "ALTER ROLE" in fonction and "PASSWORD" in fonction
    # Par l'entree standard, jamais en argument de psql (visible dans ps).
    assert "| psql" in fonction and " -c " not in fonction
    # Apostrophes doublees pour le litteral SQL.
    assert "s/'/''/g" in fonction
    provision = script.split("provision_databases() {", 1)[1].split("\n}\n", 1)[0]
    assert "realigner_mot_de_passe" in provision




def test_seul_un_logo_transparent_a_une_plaque_sur_le_hub():
    """Le logo de Mailpit porte son fond turquoise, comme le diagnostic : pas
    de plaque blanche autour. Celui de Dagster, transparent, garde la sienne."""
    dagster = app._tuile_service(app.DAGSTER_NOM, "", "http://x:3000/", True, 3000)
    mailpit = app._tuile_service(app.MAILPIT_NOM, "", "http://x:8025/", True, 8025)
    assert dagster["plaque"] is True
    assert mailpit["plaque"] is False
    page = _page_panneau("dashboard.html")
    assert "${a.externe && a.plaque ? ' service-stack' : ''}" in page




def test_favicon_ico_est_le_logo_codelab_sur_tous_les_ports():
    """Safari range les icones par nom d'hote, sans le port : /favicon.ico
    rend le logo CodeLab sur le panneau, sur l'origine des applications et
    chez Mailpit -- quoi que Safari retienne pour l'hote, c'est le notre."""
    ancien = app._origines_separees["actif"]
    ancien_apps = app.APPS_PORT
    app._origines_separees["actif"] = True
    app.APPS_PORT = 9002
    try:
        client = app.flask_app.test_client()
        for port in (9001, 9002):
            for chemin in ("/favicon.ico", "/icone-codelab.png", "/codelab-onglet.png"):
                r = client.get(chemin, **_sur_port(port))
                assert r.status_code == 200, (port, chemin)
                assert r.headers["Content-Type"].startswith("image/png")
                assert r.data == app.ICONE_CODELAB_PNG
        assert base64.b64encode(app.ICONE_CODELAB_PNG).decode() in _page_panneau("dashboard.html")
        # Le reste du panneau ne repond toujours pas sur le port des applications.
        assert client.get("/login", **_sur_port(9002)).status_code == 404
        for nom in ("dashboard.html", "login.html"):
            page = _page_panneau(nom)
            assert 'rel="icon" type="image/png" href="data:' not in page, nom
    finally:
        app._origines_separees["actif"] = ancien
        app.APPS_PORT = ancien_apps
    racine = _racine_depot()
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    # Mailpit et Dagster partagent le nom d'hote du panneau : AUCUNE adresse
    # d'icone de leurs ports ne rend autre chose que le logo CodeLab -- pas
    # meme celles de l'ancienne page de Mailpit (/mailpit.svg,
    # /notification.png), qu'un navigateur peut avoir gardees.
    bloc = conf.split("location ~ ^/(favicon\\.ico", 1)[1].split("}", 1)[0]
    assert "mailpit/codelab.png" in bloc
    for adresse in ("icone-codelab\\.png", "notification\\.png", "apple-touch-icon"):
        assert adresse in conf.split("location ~ ^/(favicon\\.ico", 1)[1].split("{", 1)[0], adresse
    svg_bloc = conf.split("location ~ ^/(favicon\\.svg|mailpit\\.svg)$ {", 1)[1].split("}", 1)[0]
    assert "alias /usr/share/nginx/codelab/mailpit/codelab.svg;" in svg_bloc
    # Le logo de Mailpit n'a qu'UNE adresse, celle que sa page declare pour
    # son onglet ; aucune adresse d'icone generique ne le rend.
    assert conf.count("logo.png") == 1 and "logo.svg" not in conf
    seul = conf.split("location = /mailpit-onglet.png {", 1)[1].split("}", 1)[0]
    assert "alias /usr/share/nginx/codelab/mailpit/logo.png;" in seul
    index = _mailpit_ui("index.html")
    icones = re.findall(r'<link rel="(?:shortcut )?icon"[^>]*href="([^"]+)"', index)
    assert icones == ["/mailpit-onglet.png"]
    # Le panneau, lui, ne declare jamais cette adresse.
    for nom in ("dashboard.html", "login.html"):
        assert "mailpit-onglet" not in _page_panneau(nom), nom
    dagster = open(os.path.join(racine, "dagster", "proxy", "nginx.conf"), encoding="utf-8").read()
    assert "location ~ ^/favicon[-a-z0-9_]*\\.svg$ {\n        alias /usr/share/nginx/codelab/mailpit/codelab.svg;" in dagster
    assert "alias /usr/share/nginx/codelab/mailpit/codelab.png;" in dagster
    # Avant « location / » : sinon l'authentification passerait devant.
    assert dagster.index("favicon[-a-z0-9_]") < dagster.index("    location / {")
    png = _mailpit_ui("codelab.png", binaire=True)
    assert png == app.ICONE_CODELAB_PNG
    svg = _mailpit_ui("codelab.svg")
    assert base64.b64encode(png).decode() in svg




def test_dagster_garde_un_pilote_postgres_qu_il_sait_ouvrir():
    """SQLAlchemy 2.1 prend psycopg (v3) par defaut pour postgresql://, que
    dagster-postgres n'installe pas : Dagster redemarrait en boucle. L'image
    epingle SQLAlchemy et verifie le pilote AU BUILD."""
    racine = _racine_depot()
    dockerfile = open(os.path.join(racine, "dagster", "Dockerfile"), encoding="utf-8").read()
    assert '"sqlalchemy<2.1"' in dockerfile
    assert "sqlalchemy.create_engine('postgresql://" in dockerfile


# ---------- Dagster et Mailpit : a l'administrateur, et a personne d'autre ----------
#
# Dagster execute du code sur la machine ; Mailpit montre tous les mails,
# codes de connexion et liens d'invitation compris. Ni l'un ni l'autre ne
# doit jamais s'ouvrir a un compte utilisateur, par aucun chemin.




def test_le_proxy_de_dagster_et_mailpit_refuse_un_compte_utilisateur(hub_dagster):
    """C'est /api/auth-check que nginx interroge avant CHAQUE requete vers
    Dagster (port 3000) et Mailpit (port 8025)."""
    c = hub_dagster
    assert c.get("/api/auth-check").status_code == 401          # anonyme
    _session_utilisateur(c)
    assert c.get("/api/auth-check").status_code == 401          # utilisateur
    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.get("/api/auth-check").status_code == 204          # administrateur




def test_aucune_tuile_dagster_ni_mailpit_pour_un_compte_utilisateur(hub_dagster, monkeypatch):
    monkeypatch.setattr(app, "mailpit_repond", lambda: True)
    c = hub_dagster
    _session_utilisateur(c)
    d = c.get("/api/mes-apps").get_json()
    noms = {a["name"] for a in d.get("apps", []) + d.get("services", [])}
    assert not noms & {app.DAGSTER_NOM, app.MAILPIT_NOM}, noms




def test_dagster_et_mailpit_ne_s_accordent_a_aucun_compte(hub_dagster):
    c = hub_dagster
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    for service in (app.DAGSTER_NOM, app.MAILPIT_NOM):
        r = c.put(f"/api/apps/{service}/acces", json={"utilisateurs": ["marie"]},
                  headers={app.JETON_ENTETE: c.get("/").get_data(as_text=True)
                           .split("jeton: \"")[1].split("\"")[0]})
        assert r.status_code in (403, 404), (service, r.data)
    assert app.lire_utilisateurs()["marie"]["projets"] == ["site"]




# ------------- Ce qu'un compte utilisateur sait (et ne sait pas) -------------
#
# Demande : « Les erreurs techniques ne remontent qu'a l'admin, les etats
# d'application ne sont visibles que par l'admin. L'utilisateur ne doit
# jamais savoir ce qui marche ou non. Quand il lance une application en
# erreur, il atterrit sur une page disant qu'elle est momentanement
# inaccessible. »

def test_le_hub_d_un_utilisateur_ne_porte_aucun_etat(deux_espaces):
    """L'etat n'est pas cache dans la page : il n'est pas envoye. Un outil de
    developpement ne doit pas pouvoir le lire non plus."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    d = c.get("/api/mes-apps").get_json()
    assert d["apps"], d
    for a in d["apps"]:
        assert "running" not in a and "listening" not in a, a
    assert d.get("sauvegardes") is None

    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    for a in admin.get("/api/mes-apps").get_json()["apps"]:
        assert a["running"] is True




def test_une_application_arretee_est_momentanement_inaccessible(deux_espaces, monkeypatch):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    monkeypatch.setattr(app, "is_running", lambda n: False)
    r = c.get("/prive-autorise/")
    page = r.get_data(as_text=True)
    assert r.status_code == 503
    assert "momentanément inaccessible" in page
    assert "Retour au hub" in page
    # Pas un mot sur la cause : ni arretee, ni erreur, ni port. On lit le
    # TEXTE de la page, pas son code (viewport contient « port »).
    texte = re.sub(r"<[^>]+>", " ", page.split("<main>")[1]).lower()
    for mot in ("arrêt", "arret", "erreur", "port", "panneau", "journal", "9101"):
        assert mot not in texte, mot
    assert "<script" not in page




def test_l_administrateur_voit_la_cause_et_le_chemin_de_la_fiche(deux_espaces, monkeypatch):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    monkeypatch.setattr(app, "is_running", lambda n: False)
    page = c.get("/prive-autorise/").get_data(as_text=True)
    assert "est arrêtée" in page
    assert "applications/prive-autorise" in page and "Ouvrir sa fiche" in page




def test_une_page_qui_plante_reste_neutre_pour_un_utilisateur(deux_espaces, monkeypatch):
    """La trace d'une application qui plante ne s'affiche pas a un compte
    utilisateur ; un appel d'API de l'application, lui, garde sa reponse."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")

    def plante(port, methode, chemin, entetes, corps):
        return 500, [("Content-Type", "text/html")], b"Traceback (most recent call last)"
    monkeypatch.setattr(app, "demander_a_l_application", plante)
    r = c.get("/prive-autorise/", headers={"Sec-Fetch-Mode": "navigate"})
    assert r.status_code == 503 and b"Traceback" not in r.data
    assert "momentanément inaccessible" in r.get_data(as_text=True)
    r = c.get("/prive-autorise/api/meteo", headers={"Sec-Fetch-Mode": "cors"})
    assert r.status_code == 500




def test_les_erreurs_5xx_restent_simples_pour_un_utilisateur():
    page = _page_panneau("dashboard.html")
    echec = page.split("async function echec(")[1].split("\n}")[0]
    assert "if(r.status>=500 && !estAdmin) return defaut" in echec
    etat = page.split("function etatApp(")[1].split("\n}")[0]
    assert "if(!estAdmin) return ['', '']" in etat




def test_une_exception_technique_ne_remonte_qu_a_l_admin(deux_espaces):
    with app.flask_app.test_request_context("/"):
        assert app.detail_pour_admin("Clé refusée.", ValueError("signature")) == "Clé refusée."
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    with c.application.test_request_context("/"):
        from flask import session
        session["authed"] = True
        session["role"] = app.ROLE_ADMIN
        session["utilisateur"] = app.NOM_ADMIN
        assert "ValueError: signature" in app.detail_pour_admin("Clé refusée.", ValueError("signature"))




# ------------- Ce qui est reserve a l'administrateur le reste -------------
#
# Demande : « certaines applications ne sont pas partageables selon le type
# de compte. Pourtant je peux les autoriser dans l'onglet Utilisateurs. Idem
# pour les widgets admin, seul un admin peut les ajouter. »

def test_une_application_reservee_ne_s_accorde_pas_depuis_la_fiche_d_un_compte(deux_espaces):
    c = deux_espaces
    app.save(dict(app.load(), diagnostic={"path": "/w/d", "command": "x", "port": 9109,
                                          "enabled": True, "visibility": "privee"}))
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.put("/api/utilisateurs/marie", json={"projets": ["prive-autorise", "diagnostic"]})
    assert r.status_code == 200, r.data
    assert app.lire_utilisateurs()["marie"]["projets"] == ["prive-autorise"]
    # Un droit ecrit par une version plus ancienne ne vaut rien non plus.
    comptes = app.lire_utilisateurs()
    comptes["marie"]["projets"] = ["prive-autorise", "diagnostic"]
    app.ecrire_utilisateurs(comptes)
    liste = c.get("/api/utilisateurs").get_json()
    marie = [u for u in liste["utilisateurs"] if u["nom"] == "marie"][0]
    assert "diagnostic" not in marie["projets"]
    # La fiche grise la case, avec la raison.
    page = _page_panneau("dashboard.html")
    assert "Réservée à l\\'administrateur" in page and "reservee?' disabled':''" in page




def test_un_utilisateur_ne_pose_pas_de_widget_d_administration(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    d = c.get("/api/hub/disposition").get_json()
    ids = {w["id"] for w in d["catalogue"]}
    assert not ids & {"etat", "messages", "sauvegardes"}, ids
    assert not {e["id"] for e in d["elements"] if e["t"] == "w"} & {"etat", "messages", "sauvegardes"}
    for wid in ("etat", "messages", "sauvegardes"):
        r = c.put("/api/hub/disposition", json={"elements": [{"t": "w", "id": wid, "taille": "petit"}]})
        assert r.status_code == 400, wid




# ------------- Audit : la liste des 20 points de securite -------------

def test_les_mots_de_passe_passent_a_600000_iterations_sans_rien_casser():
    """OWASP : 600 000 iterations pour PBKDF2-SHA256. Une empreinte d'avant
    (200 000, sans prefixe) se verifie toujours, et se recalcule a la
    connexion suivante."""
    sel = "ab" * 16
    neuf = {"sel": sel, "hash": app.derive_mot_de_passe("un-mot-de-passe", sel)}
    assert neuf["hash"].startswith("pbkdf2-sha256$600000$")
    assert app.verifie_mot_de_passe(neuf, "un-mot-de-passe")
    assert not app.verifie_mot_de_passe(neuf, "un-autre")
    assert app.empreinte_a_jour(neuf)
    ancien = {"sel": sel, "hash": app._pbkdf2("un-mot-de-passe", sel, 200_000)}
    assert app.verifie_mot_de_passe(ancien, "un-mot-de-passe")
    assert not app.empreinte_a_jour(ancien)
    src = _page_panneau("app.py")
    connexion = src.split("def login_submit():")[1].split("\n@flask_app")[0]
    # Un nom inconnu coute le meme calcul qu'un vrai compte.
    assert "verifie_mot_de_passe(compte or _COMPTE_FANTOME, pw or \"\")" in connexion
    assert "if not empreinte_a_jour(compte):" in connexion




def test_les_dependances_du_panneau_sont_lisibles_par_le_scan_et_dependabot():
    req = open(os.path.join(DOSSIER_PANNEAU, "requirements.txt"), encoding="utf-8").read()
    lignes = [l for l in req.splitlines() if l and not l.startswith("#")]
    assert lignes and all("==" in l for l in lignes)
    docker = open(os.path.join(DOSSIER_PANNEAU, "Dockerfile"), encoding="utf-8").read()
    assert "pip install --no-cache-dir -r /tmp/requirements.txt" in docker
    racine = os.path.dirname(DOSSIER_PANNEAU)
    dependabot = open(os.path.join(racine, ".github", "dependabot.yml"), encoding="utf-8").read()
    assert "package-ecosystem: pip" in dependabot and "directory: /app-manager" in dependabot




def test_le_bandeau_hors_ligne_est_opaque_et_sous_la_barre():
    """Vecu : « le message d'erreur de connexion est transparent et a moitie
    en arriere-plan »."""
    page = _page_panneau("dashboard.html")
    bandeau = page.split(".hors-ligne{")[1].split("}")[0]
    assert "background:color-mix(in srgb,var(--warn) 16%,var(--surface))" in bandeau
    assert "top:calc(var(--haut-barre,68px) + 10px" in bandeau
    # Au telephone, les messages se posent AU-DESSUS de la barre du bas :
    # sa hauteur (--barre-bas) et le bord de la barre de geste compris.
    assert ("@media (max-width:760px){ .toasts{left:12px;right:12px;max-width:none;"
            "bottom:calc(var(--barre-bas) + 20px + env(safe-area-inset-bottom))} }") in page




def test_le_telephone_couche_garde_un_panneau_lisible():
    """Vecu : iOS ne laisse pas une page web verrouiller l'orientation, et
    l'utilisateur a choisi de « soigner le paysage ». Couche (844 x 390), le
    hub passait en mosaique horizontale et coupait la seconde rangee sans
    moyen de l'atteindre ; en mode application, le logo et le contenu
    passaient sous l'encoche. Mesure Chromium apres : la page defile (879 px
    de haut), le bandeau fait 56 px, le contenu commence apres les 59 px de
    l'encoche."""
    page = _page_panneau("dashboard.html")
    sens = page.split("function majSensDuHub(){")[1].split("\n}")[0]
    assert "matchMedia('(max-height: 500px)').matches" in sens
    couche = page.split("@media (max-height:500px) and (orientation:landscape){")[1].split("\n}")[0]
    assert ".topbar{height:56px}" in couche and "--haut-barre:56px" in couche
    # Les bords lateraux de l'ecran : l'encoche d'un iPhone couche.
    assert "padding:0 max(28px,env(safe-area-inset-right)) 0 max(28px,env(safe-area-inset-left))" in page
    assert ".wrap{max-width:1120px;padding:20px max(28px,env(safe-area-inset-right)) 64px max(28px,env(safe-area-inset-left))}" in page




def test_la_barre_du_bas_a_la_hauteur_d_une_barre_d_onglets_ios():
    """Vecu : « reduire la hauteur du footer sur app ». Elle faisait 102 px en
    mode application sur iPhone (68 + 34 de barre de geste), 19 de plus
    qu'une app native ; 84 desormais (mesure Chromium, bords emules). Une
    seule hauteur, --barre-bas, que la marge du contenu et les messages
    reprennent : recopiee en chiffres, elle divergeait."""
    page = _page_panneau("dashboard.html")
    tel = page.split("/* Telephone : le menu devient la barre du bas")[1].split("\n}\n")[0]
    assert "--barre-bas:49px" in tel
    assert "padding:3px 6px max(env(safe-area-inset-bottom),4px)" in tel
    assert "height:calc(var(--barre-bas) - 3px)" in tel
    assert ".main-area{padding-bottom:calc(var(--barre-bas) + env(safe-area-inset-bottom) + 20px)}" in tel
    assert "84px" not in tel




def test_rendre_le_focus_ne_fait_pas_sauter_la_page():
    """Vecu : « ne pas forcer a ramener le scroll, ce qui cree des sauts ». Le
    repeint du hub rendait le focus au champ du widget Message et la page
    defilait jusqu'a lui."""
    page = _page_panneau("dashboard.html")
    assert "gardes[actif].focus({preventScroll:true})" in page
    messages = _page_panneau(PAGE_MESSAGES)
    assert "if(performance.now()-defileLe < 1500) return;" in messages
    assert "n.focus({preventScroll:true}); try{" in messages


# ------------------- le laissez-passer de tournage (regle 12 ter) -------------------
#
# Vecu : « je ne veux pas avoir a me connecter » pour filmer la video d'une
# application privee. « codelab video » presente la cle du poste, recoit un
# ticket a usage unique pour UNE application, et le navigateur l'echange
# contre un cookie de dix minutes : l'administrateur est reconnu par
# l'application, jamais par le panneau.

CLE_TOURNAGE = "cle-de-tournage-de-test"


def _ticket(c, nom="prive-refuse", cle=CLE_TOURNAGE):
    return c.post(f"/api/tournage/{nom}", headers={"X-CodeLab-Tournage": cle})


def _charge_utile(jeton):
    import base64
    import json as _json
    p = jeton.split(".")[1]
    return _json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


@pytest.fixture
def tournage(deux_espaces, monkeypatch):
    monkeypatch.setattr(app, "_cle_tournage", CLE_TOURNAGE)
    monkeypatch.setattr(app, "_sso_private_key", app._generer_cle_sso())
    app._tournage_tickets.clear()
    app._tournage_sessions.clear()
    vus = {}

    def amont(port, methode, chemin, entetes, corps):
        vus.clear()
        vus.update({k.lower(): v for k, v in entetes.items()})
        vus["chemin"] = chemin
        return 200, [("Content-Type", "text/plain")], b"ok"
    monkeypatch.setattr(app, "demander_a_l_application", amont)
    return deux_espaces, vus


def test_le_laissez_passer_ouvre_une_application_privee_sans_connexion(tournage):
    c, vus = tournage
    assert c.get("/prive-refuse/").status_code == 302     # vers la connexion
    r = _ticket(c)
    assert r.status_code == 200, r.get_json()
    ticket = r.get_json()["ticket"]
    r = c.get(f"/prive-refuse/cave?vue=1&codelab-tournage={ticket}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/prive-refuse/cave?vue=1")
    cookie = r.headers["Set-Cookie"]
    assert "codelab-tournage=" in cookie and "HttpOnly" in cookie and "Max-Age=600" in cookie
    r = c.get("/prive-refuse/cave")
    assert r.status_code == 200
    # L'identite de l'administrateur, signee, marquee « tournage ».
    charge = _charge_utile(vus["x-codelab-auth"])
    assert charge["sub"] == "admin" and charge["role"] == "admin" and charge["tournage"] is True
    assert charge["aud"] == "prive-refuse"
    # Ni le cookie de tournage ni le ticket n'arrivent a l'application (regle 19).
    assert "codelab-tournage" not in vus.get("cookie", "") and "codelab-tournage" not in vus["chemin"]
    # Aucune session du panneau : l'administration reste fermee.
    assert c.get("/api/apps").status_code in (401, 403)


def test_le_ticket_ne_sert_qu_une_fois_et_qu_a_son_application(tournage):
    c, _vus = tournage
    ticket = _ticket(c).get_json()["ticket"]
    # Une autre application : refuse, et le ticket est brule.
    assert c.get(f"/prive-autorise/?codelab-tournage={ticket}").status_code == 403
    assert c.get(f"/prive-refuse/?codelab-tournage={ticket}").status_code == 403
    ticket = _ticket(c).get_json()["ticket"]
    assert c.get(f"/prive-refuse/?codelab-tournage={ticket}").status_code == 302
    assert c.get(f"/prive-refuse/?codelab-tournage={ticket}").status_code == 403
    # Le cookie de prive-refuse n'ouvre pas prive-autorise.
    assert c.get("/prive-refuse/").status_code == 200
    assert c.get("/prive-autorise/").status_code == 302


def test_le_cookie_de_tournage_expire(tournage, monkeypatch):
    c, _vus = tournage
    ticket = _ticket(c).get_json()["ticket"]
    c.get(f"/prive-refuse/?codelab-tournage={ticket}")
    assert c.get("/prive-refuse/").status_code == 200
    for jeton, (nom, _fin) in list(app._tournage_sessions.items()):
        app._tournage_sessions[jeton] = (nom, 0)
    assert c.get("/prive-refuse/").status_code == 302


def _cookie_pose(reponse, nom):
    return next((v for v in reponse.headers.getlist("Set-Cookie") if v.startswith(nom + "=")), "")


def test_le_cookie_de_tournage_est_secure_en_https(tournage, monkeypatch):
    """Audit du 2026-11-11, R4 : le cookie suivait request.is_secure, que le
    TLS termine par tailscaled (Funnel) ne rend pas vrai. Il suit maintenant
    la regle de la session (https_actif) : Secure en https, et seulement
    alors."""
    c, _vus = tournage
    ticket = _ticket(c).get_json()["ticket"]
    monkeypatch.setattr(app, "par_le_tunnel", lambda: True)
    r = c.get(f"/prive-refuse/?codelab-tournage={ticket}")
    assert r.status_code == 302
    assert "; Secure" in _cookie_pose(r, app.TOURNAGE_COOKIE)
    monkeypatch.setattr(app, "par_le_tunnel", lambda: False)
    ticket = _ticket(c).get_json()["ticket"]
    r = c.get(f"/prive-refuse/?codelab-tournage={ticket}")
    assert r.status_code == 302
    pose = _cookie_pose(r, app.TOURNAGE_COOKIE)
    assert pose and "Secure" not in pose, "en clair, le navigateur ne le renverrait plus"


def test_le_cookie_d_appareil_est_secure_comme_la_session(appareils, monkeypatch):
    """Audit du 2026-11-11, R4 : le cookie d'appareil suivait
    SESSION_COOKIE_SECURE, fixe au demarrage par APP_MANAGER_HTTPS seul. En
    https (par Funnel), la session devenait Secure, pas lui."""
    monkeypatch.setattr(app, "https_actif", lambda: True)
    tls = app.flask_app.test_client()
    _connecte(tls, "marie", "mot-de-passe-long")
    assert tls.get("/api/mes-apps").status_code == 200
    assert tls.get_cookie(app.COOKIE_APPAREIL).secure
    assert tls.get_cookie(app.flask_app.config.get("SESSION_COOKIE_NAME") or "session").secure
    monkeypatch.setattr(app, "https_actif", lambda: False)
    direct = _marie_sur(IPHONE)
    assert not direct.get_cookie(app.COOKIE_APPAREIL).secure
    source = _source_du_panneau()
    assert "SESSION_COOKIE_SECURE=" not in source and '"SESSION_COOKIE_SECURE"' not in source, \
        "un drapeau fixe au demarrage ne voit pas le TLS de Funnel"


def test_le_laissez_passer_refuse_sans_la_cle_et_compte_les_essais(tournage):
    c, _vus = tournage
    assert _ticket(c, cle="").status_code == 403
    for _ in range(4):
        assert _ticket(c, cle="fausse").status_code == 403
    assert _ticket(c).status_code == 429, "5 echecs par 5 minutes et par IP (regle 6)"
    app._login_attempts.clear()
    # Sans cle cote panneau (jamais generee) : rien ne s'ouvre, pas meme a vide.
    app._cle_tournage = ""
    assert _ticket(c, cle="").status_code == 403


def test_le_laissez_passer_ne_vaut_ni_hors_reseau_ni_pour_les_apps_reservees(tournage, monkeypatch):
    c, _vus = tournage
    monkeypatch.setattr(app, "client_est_local", lambda: False)
    assert _ticket(c).status_code == 403
    monkeypatch.setattr(app, "client_est_local", lambda: True)
    monkeypatch.setattr(app, "est_app_par_defaut", lambda n: n == "prive-refuse")
    assert _ticket(c).status_code == 404
    a = app.load()
    a["prive-autorise"]["archivee_le"] = 1
    app.save(a)
    assert _ticket(c, "prive-autorise").status_code == 404
    assert _ticket(c, "inconnue").status_code == 404


def test_la_cle_de_tournage_vit_dans_credentials_env_et_le_poste_la_recopie():
    """La cle est generee par le panneau, gardee dans son bloc de
    credentials.env, et recopiee par le gardien du poste pour la session
    (0600, sous son compte) -- jamais transmise a une application."""
    source = _source_du_panneau()
    assert '"APP_MANAGER_TOURNAGE_CLE"' in source.split("def ecrire_bloc_panneau")[1].split("def ")[0]
    assert "APP_MANAGER_TOURNAGE_CLE" not in source.split("def secrets_partages")[1].split("\ndef ")[0]
    entree = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"), encoding="utf-8").read()
    fonction = entree.split("ecrire_la_cle_de_tournage() {")[1].split("\n}")[0]
    assert "umask 077" in fonction and "runuser -u" in fonction and ".codelab-tournage" in fonction


def test_le_laissez_passer_demarre_une_application_arretee(tournage, monkeypatch):
    """Vecu : « Rien sur le port en question. je ne veux pas avoir a ouvrir
    le navigateur ». Arretee, l'application demarre par le panneau (start et
    ses controles) ; un demarrage refuse ne donne pas de ticket."""
    c, _vus = tournage
    monkeypatch.setattr(app, "is_running", lambda n: False)
    lances = []
    monkeypatch.setattr(app, "start", lambda n, attendre=True: lances.append(n))
    r = _ticket(c)
    assert r.status_code == 200 and r.get_json()["demarree"] is True and lances == ["prive-refuse"]
    monkeypatch.setattr(app, "start", lambda n, attendre=True: "Dossier introuvable : /w/b")
    r = _ticket(c)
    assert r.status_code == 409 and "Dossier introuvable" in r.get_json()["error"]
    # Sans la cle, rien ne demarre.
    lances.clear()
    monkeypatch.setattr(app, "start", lambda n, attendre=True: lances.append(n))
    assert _ticket(c, cle="fausse").status_code == 403 and lances == []


def test_le_laissez_passer_retrouve_l_application_par_son_dossier(tournage, monkeypatch):
    """Vecu : « pas de laissez-passer : Application introuvable » -- le
    projet wine-manager etait declare sous un autre nom. Le dossier du projet
    designe l'application, quel que soit son nom au panneau."""
    c, _vus = tournage
    monkeypatch.setattr(app, "ROOT", "/w")

    def par_dossier(dossier, cle=CLE_TOURNAGE):
        return c.post("/api/tournage", json={"dossier": dossier},
                      headers={"X-CodeLab-Tournage": cle})
    a = app.load()
    a["prive-refuse"]["titre"] = "Cave Privée"
    app.save(a)
    r = par_dossier("/w/b")
    assert r.status_code == 200 and r.get_json()["app"] == "prive-refuse", r.get_json()
    # Le nom affiche, pour les cartons de la video.
    assert r.get_json()["titre"] == "Cave Privée"
    assert c.get(f"/prive-refuse/?codelab-tournage={r.get_json()['ticket']}").status_code == 302
    # Le projet au-dessus d'une seule application declaree dans un sous-dossier.
    a = app.load()
    a["prive-refuse"]["path"] = "/w/cave/site"
    app.save(a)
    assert par_dossier("/w/cave").get_json()["app"] == "prive-refuse"
    # Rien, ou trop : refuse, et le dit.
    r = par_dossier("/w/rien")
    assert r.status_code == 404 and "déclare-la" in r.get_json()["error"]
    a["prive-autorise"]["path"] = "/w/cave/api"
    app.save(a)
    r = par_dossier("/w/cave")
    assert r.status_code == 404 and "prive-autorise, prive-refuse" in r.get_json()["error"]
    # Hors de /workspace, ou sans la cle : rien.
    assert par_dossier("/etc").status_code == 404
    assert par_dossier("/w/../etc").status_code == 404
    assert par_dossier({"x": 1}).status_code == 404
    r = par_dossier("/w/b", cle="fausse")
    assert r.status_code == 403 and "cave" not in r.get_data(as_text=True)


# ===========================================================================
# LE PROXY
# le relais vers les applications : connexions, redirections, en-tetes, pages
# d'erreur, mode application encadre.
# ===========================================================================

# ------------------- connexions du proxy vers les applications -------------------
#
# Une vraie application locale, sur un port libre : le proxy y garde ses
# connexions ouvertes, ne rejoue jamais un POST, et ne suit que les
# redirections vers l'application elle-meme.




def test_le_proxy_garde_sa_connexion_vers_l_application(appli_locale):
    """Une connexion TCP par requete coutait une poignee de main, et souvent
    un fil neuf cote application, pour chaque image et chaque appel d'API."""
    port = appli_locale["port"]
    for _ in range(3):
        statut, _e, data = app.demander_a_l_application(port, "GET", "/a", {}, None)
        assert statut == 200 and data == b"GET /a"
    assert len(appli_locale["connexions"]) == 1




def test_une_connexion_gardee_ne_coute_pas_40_ms_par_requete(appli_locale):
    """Vecu : connexions gardees, une page passait de 2,5 a 44 ms. http.server
    ecrit l'en-tete puis le corps, Nagle retient le corps, et Linux differe
    l'acquittement de 40 ms sur une connexion qui dure (TCP_QUICKACK)."""
    port = appli_locale["port"]
    app.demander_a_l_application(port, "GET", "/a", {}, None)
    debut = time.monotonic()
    for _ in range(20):
        app.demander_a_l_application(port, "GET", "/a", {}, None)
    assert time.monotonic() - debut < 0.4, "le delai d'acquittement est revenu"
    assert len(appli_locale["connexions"]) == 1




def test_le_proxy_n_envoie_jamais_un_post_sur_une_connexion_gardee(appli_locale):
    """Une connexion gardee peut avoir ete fermee par l'application : un POST
    qu'il faudrait rejouer, faute de savoir s'il a ete recu, part donc sur
    une connexion neuve, et une seule fois."""
    port = appli_locale["port"]
    app.demander_a_l_application(port, "GET", "/a", {}, None)
    statut, _e, data = app.demander_a_l_application(port, "POST", "/paye", {}, b"x=1")
    assert statut == 200 and data == b"POST /paye"
    assert len(appli_locale["connexions"]) == 2
    assert [r for r in appli_locale["requetes"] if r[0] == "POST"] == [("POST", "/paye", b"x=1")]




def test_une_connexion_coupee_par_l_application_ne_fait_pas_d_erreur(appli_locale):
    port = appli_locale["port"]
    assert app.demander_a_l_application(port, "GET", "/ferme", {}, None)[0] == 200
    time.sleep(0.1)
    statut, _e, data = app.demander_a_l_application(port, "GET", "/apres", {}, None)
    assert statut == 200 and data == b"GET /apres"




def test_le_proxy_ne_suit_que_les_redirections_vers_l_application(appli_locale):
    """Un « /login » absolu reste dans l'application (le navigateur l'aurait
    envoye hors du /<nom>/) ; une redirection vers un autre site repart au
    navigateur : le panneau allait chercher la page de Google a sa place."""
    port = appli_locale["port"]
    for chemin in ("/interne", "/absolue"):
        statut, _e, data = app.demander_a_l_application(port, "GET", chemin, {}, None)
        assert (statut, data) == (200, b"GET /arrivee"), chemin
    statut, entetes, _d = app.demander_a_l_application(port, "GET", "/dehors", {}, None)
    assert statut == 302 and dict(entetes)["Location"] == "https://accounts.example/o"
    assert not any(r[1] == "/o" for r in appli_locale["requetes"])
    # Les regles d'urllib : un POST redirige par 302 devient un GET sans
    # corps, un 307 ne se suit pas.
    statut, _e, data = app.demander_a_l_application(port, "POST", "/interne", {}, b"x=1")
    assert (statut, data) == (200, b"GET /arrivee")
    assert app.demander_a_l_application(port, "POST", "/temporaire", {}, b"x=1")[0] == 307




def test_une_application_injoignable_leve_une_erreur_de_connexion():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(OSError):
        app.demander_a_l_application(port, "GET", "/", {}, None)




def test_codelab_create_depose_les_modules_de_connexion(tmp_path):
    modeles = os.path.join(_racine_depot(), "dev")
    r, espace = _lancer_codelab(tmp_path, ["create", "boutique"], {"CODELAB_MODELES": modeles})
    assert r.returncode == 0, r.stdout + r.stderr
    for nom in ("codelab_auth.py", "codelab-auth.js"):
        assert (espace / "boutique" / nom).read_text() == open(os.path.join(modeles, nom)).read()
    dockerfile = open(os.path.join(_racine_depot(), "dev", "Dockerfile")).read()
    assert "dev/codelab_auth.py dev/codelab_web.py dev/codelab-auth.js dev/codelab-web.js" in dockerfile
    assert "/usr/local/share/codelab/modeles/" in dockerfile
    manuel = open(os.path.join(_racine_depot(), "dev", "AGENTS.md")).read()
    assert "connexion_silencieuse" in manuel and "email_verified" in manuel


# ------------------- pages d'erreur et en-tetes des applications -------------------
#
# codelab_web, depose par « codelab create » : une erreur ne montre jamais sa
# pile d'appels, et les en-tetes de securite sont un choix de l'application
# que le module rend facile -- sans ecraser ce qu'une route a decide.




def test_une_exception_est_journalisee_sans_rien_montrer_au_visiteur(caplog):
    m, c = _app_web()
    with caplog.at_level("ERROR", logger="codelab_web"):
        r = c.get("/factures/12/pdf")
    page = r.get_data(as_text=True)
    assert r.status_code == 500
    assert "SECRET-dans-la-trace" not in page and "Traceback" not in page
    assert "Erreur interne" in page and '/theme.css' in page
    # La trace, elle, est bien dans le journal.
    assert "SECRET-dans-la-trace" in caplog.text
    # Le lien de retour remonte vers la racine de l'APPLICATION (servie sous
    # /<nom>/ par le panneau), pas vers celle du hub.
    assert 'href="../../"' in page




def test_la_404_et_le_json_pour_une_api():
    m, c = _app_web()
    r = c.get("/nulle-part")
    assert r.status_code == 404 and "Page introuvable" in r.get_data(as_text=True)
    assert 'href="./"' in r.get_data(as_text=True)
    r = c.get("/nulle-part", headers={"Accept": "application/json"})
    assert r.status_code == 404 and r.get_json() == {"erreur": "Page introuvable", "code": 404}
    r = c.get("/factures/1/pdf", headers={"Accept": "application/json"})
    assert r.status_code == 500 and r.get_json()["code"] == 500




def test_les_en_tetes_de_securite_sont_poses_sans_ecraser_ceux_d_une_route():
    m, c = _app_web()
    r = c.get("/")
    assert r.headers["Content-Security-Policy"] == m.CSP_DEFAUT
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    # Pas de scripts en ligne, et pas de frame-ancestors : le panneau pose le sien.
    assert "unsafe-inline" not in m.CSP_DEFAUT.split("script-src")[1]
    assert "frame-ancestors" not in m.CSP_DEFAUT
    assert c.get("/a-soi").headers["Content-Security-Policy"] == "default-src *"
    # Les pages d'erreur aussi les recoivent.
    assert c.get("/nulle-part").headers["Content-Security-Policy"] == m.CSP_DEFAUT
    # Sans l'appel : rien n'est impose.
    m, c = _app_web(entetes=False)
    assert "Content-Security-Policy" not in c.get("/").headers




def test_le_module_node_des_erreurs_et_des_en_tetes(tmp_path):
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node absent")
    module = os.path.join(_racine_depot(), "dev", "codelab-web.js")
    script = tmp_path / "t.js"
    # Un faux Express : juste ce que le module appelle.
    script.write_text(
        "const m = require(" + json.dumps(module) + ");\n"
        "const pile = []; const app = { use: (f) => pile.push(f) };\n"
        "m.pagesDErreur(app);\n"
        "function res() { const r = { h: {}, code: 0, corps: null, type: () => r,\n"
        "  status: (c) => { r.code = c; return r; }, send: (b) => { r.corps = b; return r; },\n"
        "  json: (b) => { r.corps = b; return r; },\n"
        "  setHeader: (k, v) => { r.h[k.toLowerCase()] = v; },\n"
        "  getHeader: (k) => r.h[k.toLowerCase()] }; return r; }\n"
        "const req = (accepte, path) => ({ method: 'GET', path, accepts: () => accepte });\n"
        "const r404 = res(); pile[0](req('html', '/a/b'), r404);\n"
        "const r500 = res(); const erreurs = [];\n"
        "console.error = (t) => erreurs.push(String(t));\n"
        "pile[1](new Error('SECRET-trace'), req('html', '/x'), r500, () => {});\n"
        "const rjson = res(); pile[1](new Error('e'), req('json', '/x'), rjson, () => {});\n"
        "const rh = res(); rh.setHeader('Content-Security-Policy', 'default-src *');\n"
        "m.entetesDeSecurite()(req('html', '/'), rh, () => {});\n"
        "const rd = res(); m.entetesDeSecurite()(req('html', '/'), rd, () => {});\n"
        "process.stdout.write(JSON.stringify({ r404, r500, rjson, erreurs, rh: rh.h, rd: rd.h,"
        " csp: m.CSP_DEFAUT }));\n")
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["r404"]["code"] == 404 and "Page introuvable" in d["r404"]["corps"]
    assert 'href="../"' in d["r404"]["corps"]
    assert d["r500"]["code"] == 500 and "SECRET-trace" not in d["r500"]["corps"]
    assert any("SECRET-trace" in e for e in d["erreurs"])
    assert d["rjson"]["corps"] == {"erreur": "Erreur interne", "code": 500}
    assert d["rh"]["content-security-policy"] == "default-src *"
    assert d["rd"]["content-security-policy"] == d["csp"]
    assert d["rd"]["x-content-type-options"] == "nosniff"
    # Meme politique dans les deux langages.
    assert d["csp"] == _module_web().CSP_DEFAUT




def test_codelab_create_depose_les_modules_web_et_un_claude_md(tmp_path):
    modeles = os.path.join(_racine_depot(), "dev")
    r, espace = _lancer_codelab(tmp_path, ["create", "atelier"], {"CODELAB_MODELES": modeles})
    assert r.returncode == 0, r.stdout + r.stderr
    projet = espace / "atelier"
    for nom in ("codelab_web.py", "codelab-web.js"):
        assert (projet / nom).read_text() == open(os.path.join(modeles, nom)).read()
    assert (projet / "CLAUDE.md").read_text().startswith("@AGENTS.md\n")
    assert ".dagster-venv/" in (projet / ".gitignore").read_text().splitlines()




def test_codelab_update_ne_touche_pas_un_claude_md_existant(tmp_path):
    projet = tmp_path / "ws" / "vieux"
    projet.mkdir(parents=True)
    (projet / "CLAUDE.md").write_text("mes consignes\n")
    r, _ = _lancer_codelab(tmp_path, ["update", str(projet), "--sans-db"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert (projet / "CLAUDE.md").read_text() == "mes consignes\n"
    assert "n'importe pas AGENTS.md" in r.stdout
    # Un CLAUDE.md qui l'importe deja : rien a dire.
    (projet / "CLAUDE.md").write_text("@AGENTS.md\net le reste\n")
    r, _ = _lancer_codelab(tmp_path, ["update", str(projet), "--sans-db"])
    assert "CLAUDE.md" not in r.stdout




def test_le_manuel_porte_la_liste_avant_mise_en_ligne():
    manuel = open(os.path.join(_racine_depot(), "dev", "AGENTS.md"), encoding="utf-8").read()
    assert "## Avant de declarer une application prete" in manuel
    for attendu in ("375 px", "codelab_web", "entetes_de_securite", "requirements-dagster.txt",
                    "`.env` dans le `.gitignore`", "codelab db",
                    # Audit du 2026-11-11, R3 : une voisine joint le port sans le proxy.
                    "pas seulement la visibilite (*Privee*,\n      *Reseau local*)"):
        assert attendu in manuel, attendu
    assert "codelab_auth.utilisateur(request.headers)" in manuel, "l'exemple minimal du controle"
    # La liste est la derniere section : c'est ce que l'agent relit en
    # dernier avant de conclure.
    assert manuel.rstrip().rsplit("\n## ", 1)[1].startswith("Avant de declarer")





def test_le_manuel_donne_une_direction_artistique():
    """Les references visuelles des applications : chacune avec ce qu'on en
    prend, et la regle qui les garde dans CodeLab -- s'inspirer sans charger
    de librairie tierce."""
    manuel = open(os.path.join(_racine_depot(), "dev", "AGENTS.md"), encoding="utf-8").read()
    style = manuel.split("## 13. Le style de CodeLab")[1].split("\n## 14.")[0]
    assert "### Direction artistique" in style
    for site in ("rondesignlab.com", "refero.design", "kokonutui.com",
                 "ui.aceternity.com", "ui.shadcn.com", "www.usemotion.com"):
        assert "https://" + site in style, site
    assert "prefers-reduced-motion" in style and "CDN" in style




# ---- Mode application, suite : demarrage, hors ligne, applications encadrees ----
#
# Vecu : « ajoute les points 1, 2 et 3 dans la PR » -- l'ecran blanc au
# lancement, l'app qui ne s'ouvre pas sans serveur, les applications ouvertes
# depuis le hub. Et : « en tant qu'app codelab reagit comme une application,
# mais si je l'ouvre dans mon navigateur, comme un site ».

def test_l_ecran_de_demarrage_est_fabrique_au_pixel_pres(hub):
    import struct
    r = hub.get("/ecran-demarrage/1290x2796-clair.png")
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert r.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", r.data[16:24]) == (1290, 2796)
    assert hub.get("/ecran-demarrage/750x1334-sombre.png").status_code == 200
    # Seulement les tailles d'iPhone connues, et les deux themes.
    assert hub.get("/ecran-demarrage/5000x5000-clair.png").status_code == 404
    assert hub.get("/ecran-demarrage/1290x2796-rose.png").status_code == 404




@pytest.mark.parametrize("page", ["dashboard.html", "login.html"])
def test_chaque_page_declare_ses_ecrans_de_demarrage(page):
    html = _page_panneau(page)
    # Posees par le serveur : la page source n'en porte que la marque.
    assert "__ECRANS_DEMARRAGE__" in html and 'rel="apple-touch-startup-image"' not in html
    with app.flask_app.test_request_context("/"):
        servie = app.avec_style(html)
    for w, h in app.IMAGES_DEMARRAGE:
        for theme in ("clair", "sombre"):
            assert f'href="/ecran-demarrage/{w}x{h}-{theme}.png"' in servie, (w, h, theme)
    assert servie.count('rel="apple-touch-startup-image"') == 2 * len(app.IMAGES_DEMARRAGE)




def test_l_ipad_a_ses_ecrans_de_demarrage_dans_les_deux_sens():
    """Vecu : « Assure-toi que le rendu fonctionne bien comme une app sur
    tablette aussi. » Un iPad ouvre l'application debout ou couche : une
    image pour chaque sens, au pixel pres."""
    images = app.IMAGES_DEMARRAGE
    assert (1640, 2360) in images and (2360, 1640) in images        # iPad Air 11 pouces
    assert "(orientation: landscape)" in images[(2360, 1640)][1]
    assert (2752, 2064) in images                                    # iPad Pro 13 pouces
    # Un iPhone, lui, ne demarre qu'en portrait.
    assert (2796, 1290) not in images
    import struct
    png = app.ecran_de_demarrage(2360, 1640, "sombre")
    assert struct.unpack(">II", png[16:24]) == (2360, 1640)




def test_hors_ligne_l_application_s_ouvre_sur_une_page_de_reserve(hub):
    page = hub.get("/hors-ligne.html")
    assert page.status_code == 200
    corps = page.get_data(as_text=True)
    assert "Serveur injoignable" in corps and "fetch('/health'" in corps
    assert 'src="data:image/png;base64,' in corps      # rien a charger hors ligne
    sw = hub.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.mimetype
    code = sw.get_data(as_text=True)
    # Seules les pages, et seulement en cas d'echec ; aucune donnee en cache.
    assert "if (e.request.mode !== 'navigate') return;" in code
    assert "fetch(e.request).catch(() => caches.match('/hors-ligne.html'))" in code
    assert "const RESERVE = ['/hors-ligne.html'];" in code




def test_le_service_worker_n_existe_qu_en_mode_application():
    mode = _page_panneau("mode.js").split("(function(){")[1].split("})();")[0]
    avant, apres = mode.split("if(!app) return;")
    assert "serviceWorker" not in avant
    assert "navigator.serviceWorker.register('/sw.js')" in apres




def test_l_application_ne_tourne_pas_sur_un_telephone():
    """Vecu : « je ne veux pas que la rotation soit autorisee tout court ».
    Deux verrous et rien d'autre : le manifeste en portrait au telephone, et
    screen.orientation.lock sur Android. La contre-rotation (la page tournee
    en sens inverse sur iPhone) est retiree : iOS jouait d'abord sa propre
    animation, on voyait la page tourner puis revenir."""
    mode = _page_panneau("mode.js").split("(function(){")[1].split("})();")[0]
    avant, apres = mode.split("if(!app) return;")
    # En mode application seulement, et sur un telephone seulement : le petit
    # cote de l'ECRAN, au doigt. La tablette et l'ordinateur tournent.
    assert "lock('portrait')" not in avant
    assert "Math.min(screen.width, screen.height) < 600" in apres
    assert "matchMedia('(pointer: coarse)')" in apres
    assert "if(telephone && o && o.lock) o.lock('portrait').catch(function(e){})" in apres
    # Plus rien ne retourne la page ni ne redresse l'ecran.
    for retire in ("contre-rotation", "rotate(", "Object.defineProperty(window, 'innerWidth'",
                   "window.matchMedia = function", "Element.prototype.getBoundingClientRect =",
                   "document.elementFromPoint =", "'pivote'"):
        assert retire not in apres, retire
    theme = _page_panneau("theme.css")
    assert "contre-rotation" not in theme and "pivote" not in theme
    # Le manifeste : portrait au telephone, libre ailleurs (voir
    # test_le_manifeste_demande_le_portrait_au_telephone_seulement).
    assert "est_un_telephone(request.headers.get(\"User-Agent\"))" in _page_panneau("app.py")




def test_le_manifeste_demande_le_portrait_au_telephone_seulement(client):
    """Vecu : « je ne veux pas que la rotation soit autorisee tout court ».
    Le telephone recoit un manifeste en portrait (Android le verrouille) ; la
    tablette et l'ordinateur gardent l'orientation libre, ils se tiennent
    dans les deux sens. La reponse varie selon l'appareil : un cache ne doit
    pas servir l'une a l'autre."""
    agents = {
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15": "portrait",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36": "portrait",
        "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 Chrome/126 Safari/537.36": "any",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15": "any",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36": "any",
        "": "any",
    }
    for agent, attendu in agents.items():
        r = client.get("/manifest.webmanifest", headers={"User-Agent": agent})
        assert r.status_code == 200
        assert json.loads(r.get_data(as_text=True))["orientation"] == attendu, agent
        assert "User-Agent" in r.headers.get("Vary", ""), "la reponse depend de l'appareil"




def test_une_application_ouverte_dans_le_panneau_installe_se_comporte_en_app(hub):
    c = _connexion_marie(hub)
    page = c.get("/ouvrir.html?app=compta").get_data(as_text=True)
    assert "maximum-scale=1,user-scalable=no" in page
    assert '<meta name="theme-color" content="#ffffff">' in page
    # La couleur annoncee par l'application, de SON origine, et une couleur.
    assert 'if(e.origin!==origine||!e.data)return;' in page and 'e.data.codelab!=="couleur"' in page
    # Le geste retour : un glissement depuis le bord gauche ramene au hub.
    assert 'id="bord"' in page and 'if(d>70){location.href="/";}' in page




def test_le_ruban_ne_porte_plus_le_script_d_environnement():
    # Le script vit desormais EN TETE de page (injecter_environnement) : en
    # bas, il arrivait apres les scripts de l'application. Le ruban ne porte
    # que le sien (RUBAN_JS, qui le replie apres 10 s).
    with app.flask_app.test_request_context("/"):
        ruban = app.ruban_retour().decode()
    assert app.ENVIRONNEMENT_APP_JS[:300] not in ruban and "CODELAB.environnement" not in ruban
    assert re.findall(r"<script>(.*?)</script>", ruban, re.S) == [app.RUBAN_JS]


# ---------------- Les applications du hub, de vraies applications ----------------
#
# Vecu : « Fais en sorte que les applications de mon hub puissent etre de
# vraies applications et que le hub simule iOS pour renvoyer les memes
# informations, pour adapter au mieux l'affichage. »




def test_l_environnement_passe_avant_les_scripts_de_l_application(monkeypatch):
    monkeypatch.setattr(app, "lien_panneau", lambda: "https://hub.exemple.org/")
    data, entetes = _page_injectee(True)
    tete = data.split(b"</head>")[0]
    # Juste apres <head> : avant le premier script de l'application.
    assert tete.index(b"window.CODELAB") < tete.index(b"src=app.js")
    assert b'var PANNEAU = "https://hub.exemple.org";' in tete
    assert dict(entetes)["Content-Length"] == str(len(data))
    # Un visiteur anonyme n'apprend pas l'adresse du panneau.
    data, _ = _page_injectee(False)
    assert b'var PANNEAU = "";' in data and b"hub.exemple.org" not in data
    # Panneau et applications sur la meme adresse : le lien est relatif,
    # l'origine est celle de la requete.
    monkeypatch.setattr(app, "lien_panneau", lambda: "/")
    data, _ = _page_injectee(True)
    assert b'var PANNEAU = "http://localhost";' in data
    # Ni autre chose que du HTML, ni une page en erreur, ni un corps compresse.
    page = b"<html><head></head></html>"
    for code, entetes in ((200, [("Content-Type", "application/json")]),
                          (404, [("Content-Type", "text/html")]),
                          (200, [("Content-Type", "text/html"), ("Content-Encoding", "gzip")])):
        assert app.injecter_environnement(page, code, entetes, True)[0] == page




def test_l_environnement_ne_ferme_jamais_sa_balise():
    """Un « </script> » dans le script, meme en commentaire, couperait la page."""
    source = _page_panneau("environnement-app.js")
    assert "</" not in source
    assert source.count("__PANNEAU__") == 1




def test_encadree_par_le_hub_une_application_se_croit_installee():
    source = _page_panneau("environnement-app.js")
    # Encadree par LE panneau seulement : le nom du cadre, et une origine connue.
    assert "encadree = window.top !== window && window.name === 'codelab-app' && !!PANNEAU;" in source
    # Ce qu'iOS dirait d'une app installee.
    assert "Object.defineProperty(navigator, 'standalone'" in source
    assert "if(/display-mode\\s*:\\s*standalone/.test(t)) return faux(t, true);" in source
    # Comme iOS : viewport-fit=cover decide, et env() est reecrit pour la page qui s'etend.
    assert "if(/viewport-fit\\s*=\\s*cover/.test(l[i].getAttribute('content') || '')) cover = true;" in source
    # Lu quand la page est prete : le script passe avant la balise viewport.
    assert source.index("function pret(){\n    regarderViewport();") > 0
    assert "'var(--codelab-bord-' + NOMS[cote]" in source and "if(!cover) return;" in source
    # Les messages ne partent qu'au panneau, et ne s'acceptent que de lui.
    assert '"*"' not in source and "'*'" not in source
    assert "e.source !== window.parent || e.origin !== PANNEAU" in source
    # Les memes cles partout, ouverte seule ou encadree.
    for cle in ("'--codelab-bord-haut'", "'data-appareil'", "'data-plateforme'", "'codelab-environnement'"):
        assert cle in source, cle




def test_le_cadre_remplace_ses_marqueurs_en_un_seul_passage(hub, monkeypatch):
    """ouvrir.html est un fichier a marqueurs : une valeur remplacee n'est
    jamais relue (un titre qui contient un marqueur reste du texte), et
    l'origine, ecrite dans le script, ne peut pas fermer la balise."""
    page = app._lire_ressource("ouvrir.html")
    for marqueur in ("__TITRE__", "__ICONE__", "__CIBLE__", "__ORIGINE__"):
        assert marqueur in page, marqueur
    monkeypatch.setattr(app, "titre_de", lambda nom, a: "__CIBLE__ <b>")
    monkeypatch.setattr(app, "origine_de_l_application", lambda nom: "http://x</script>")
    c = _connexion_marie(hub)
    rendu = c.get("/ouvrir.html?app=compta").get_data(as_text=True)
    assert "<title>__CIBLE__ &lt;b&gt;</title>" in rendu
    assert "</script>\"" not in rendu and "http://x\\u003c/script>" in rendu
    # Hors du titre (balise et attribut), plus aucun marqueur.
    assert not re.search(r"__(TITRE|ICONE|CIBLE|ORIGINE)__", rendu.replace("__CIBLE__ &lt;b&gt;", ""))




def test_le_cadre_pose_l_application_dans_la_zone_sure_comme_ios(hub):
    c = _connexion_marie(hub)
    page = c.get("/ouvrir.html?app=compta").get_data(as_text=True)
    assert "height:calc(100% - env(safe-area-inset-top) - env(safe-area-inset-bottom))" in page
    assert "iframe.plein{top:0;left:0;width:100%;height:100%}" in page
    # « pret » : la page dit si elle s'etend ; le panneau repond par l'environnement.
    assert 'if(e.data.codelab==="pret"){plein=!!e.data.pleinEcran;' in page
    assert 'cadre.contentWindow.postMessage({codelab:"environnement",bords:b,' in page






def test_le_cadre_suit_le_manifeste_de_l_application():
    """Vecu : « Assure toi que les applications beneficient et heritent d'un
    vrai comportement d'application. Utiliser le manifest ». Encadree, la page
    lit le manifeste qu'elle declare et l'envoie au panneau ; le cadre en suit
    le fond, la barre et l'orientation -- apres avoir verifie l'origine et
    chaque valeur."""
    env = _page_panneau("environnement-app.js")
    assert "</" not in env and env.count("__PANNEAU__") == 1
    lecture = env.split("function annoncerManifeste(){")[1].split("\n  }\n")[0]
    assert "link[rel~=\"manifest\"]" in lecture and "XMLHttpRequest" in lecture
    assert "codelab: 'manifeste'" in lecture and "}, PANNEAU);" in lecture
    assert "annoncerManifeste();" in env.split("function pret(){")[1].split("\n  }")[0]
    cadre = _page_panneau("ouvrir.html")
    ecoute = cadre.split('addEventListener("message", function(e){')[1].split("});")[0]
    assert ecoute.lstrip().startswith("if(e.origin!==origine||!e.data)return;")
    assert 'e.data.codelab==="manifeste"' in ecoute
    suivre = cadre.split("function suivreManifeste(d){")[1].split("\n  }\n")[0]
    assert "estUneCouleur(fond)" in suivre and "estUneCouleur(barre)" in suivre
    assert "ORIENTATIONS.indexOf(sens) >= 0" in suivre and "screen.orientation.lock(sens)" in suivre
    # La couleur reellement peinte gagne sur celle du manifeste.
    assert "if(!peinte && estUneCouleur(fond))" in suivre and "peinte = true;" in cadre


# ===========================================================================
# L'ACCES DEPUIS INTERNET PAR TAILSCALE FUNNEL
# Funnel (14-tunnel) : une requete de Funnel se reconnait a son port, jamais a
# un en-tete ; l'administration y reste fermee ; les adresses sont celles de
# la machine Tailscale (443 et 8443) ; la session vaut des deux cotes ; la cle
# ne sort jamais du coffre ; tailscaled et tailscale sans secret ni privilege.
# ===========================================================================

HOTE = "codelab.tail1234.ts.net"
CLE = "tskey-auth-kAbCdEf1CNTRL-0123456789abcdefABCDEF"


def _par_funnel(quoi):
    """Une requete arrivee par Funnel sur le panneau ou les applications :
    son port local, sur l'hote public que tailscaled transmet."""
    if quoi == "panneau":
        return {"base_url": "https://%s:%d" % (HOTE, app.TUNNEL_PORT_PANNEAU)}
    return {"base_url": "https://%s:%d" % (HOTE, app.TUNNEL_PORT_APPS)}


@pytest.fixture
def funnel(deux_origines, monkeypatch):
    monkeypatch.setitem(app._tunnel, "hote", HOTE)
    monkeypatch.setitem(app._tunnel, "funnel", True)
    monkeypatch.setitem(app._tunnel, "erreur", "")
    return deux_origines


def test_par_funnel_l_administration_reste_fermee_meme_sans_en_tete(funnel):
    """La connexion de tailscaled vient de 127.0.0.1, donc « du reseau
    local ». Sans le port pour la reconnaitre, il suffisait que
    X-Forwarded-For manque pour ouvrir l'administration a tout internet."""
    local = {"REMOTE_ADDR": "127.0.0.1"}
    for quoi in ("panneau", "apps"):
        with app.flask_app.test_request_context("/", environ_overrides=local,
                                                **_par_funnel(quoi)):
            assert app.par_le_tunnel()
            assert not app.client_est_local()
            assert app.refus_admin_hors_reseau()
    # Le meme 127.0.0.1 sur le port du panneau reste local : Funnel ne
    # ferme que ce qui passe par lui.
    with app.flask_app.test_request_context("/", environ_overrides=local, **_sur_port(9001)):
        assert not app.par_le_tunnel()
        assert app.client_est_local()


def test_l_administrateur_ne_se_connecte_pas_par_funnel(funnel):
    r = funnel.post("/login", json={"nom": "admin", "password": "secret-de-test"},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, **_par_funnel("panneau"))
    assert r.status_code == 403
    with funnel.session_transaction(**_par_funnel("panneau")) as s:
        assert not s.get("authed")


def test_par_funnel_les_adresses_sont_celles_de_la_machine(funnel):
    """Un seul nom en .ts.net, deux ports : le panneau sur 443 (l'adresse
    nue), les applications sur 8443. Deux origines, comme 9001 et 9002."""
    entetes = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}
    with app.flask_app.test_request_context("/", headers=entetes, **_par_funnel("panneau")):
        assert app.origine_applications() == "https://%s:8443" % HOTE
        assert app.modele_adresse_application() == "https://%s:8443/{nom}/" % HOTE
        assert app.cadres_permis() == "https://%s:8443" % HOTE
        assert app.schema_vu() == "https" and app.https_actif()
        assert app.lien_panneau() == "/"
    with app.flask_app.test_request_context("/public/", headers=entetes, **_par_funnel("apps")):
        assert app.sur_le_port_des_apps()
        assert app.lien_panneau() == "https://%s/" % HOTE
        assert "https://%s" % HOTE in app.origines_encadrantes().split()


def test_une_adresse_douteuse_ne_sert_jamais(funnel, monkeypatch):
    """L'adresse vient de la reponse de tailscale, et part dans des liens,
    une CSP et un en-tete Location : elle se valide comme une donnee."""
    for douteuse in ("evil.example", "codelab.ts.net.evil.example",
                     'a"><script>.ts.net', "ts.net", ""):
        monkeypatch.setitem(app._tunnel, "hote", douteuse)
        assert app.origine_du_tunnel("apps") == "", douteuse
        assert app.origine_du_tunnel("panneau") == "", douteuse


def test_par_funnel_le_panneau_ne_sert_jamais_une_application_chez_lui(funnel, monkeypatch):
    """Sans adresse des applications, le panneau local sert l'application
    sur son propre port (degrade mais joignable). Par Funnel, ce serait la
    servir dans l'origine du panneau, a tout internet : on attend."""
    monkeypatch.setitem(app._tunnel, "hote", "")
    r = funnel.get("/public/", **_par_funnel("panneau"))
    assert r.status_code == 503
    monkeypatch.setitem(app._tunnel, "hote", HOTE)
    r = funnel.get("/public/", **_par_funnel("panneau"))
    assert r.status_code == 302 and r.headers["Location"] == "https://%s:8443/public/" % HOTE


def test_la_limite_des_essais_compte_chaque_visiteur_de_funnel(funnel):
    """Tous les visiteurs de Funnel arrivent de 127.0.0.1 : un seul qui se
    trompait cinq fois fermait la connexion a tout le monde. Par Funnel (et
    par lui seul), l'adresse est celle que tailscaled pose."""
    with app.flask_app.test_request_context(
            "/", headers={"X-Forwarded-For": "198.51.100.4"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, **_par_funnel("panneau")):
        assert app._client_ip() == "198.51.100.4"
    with app.flask_app.test_request_context(
            "/", headers={"X-Forwarded-For": "pas une adresse"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, **_par_funnel("panneau")):
        assert app._client_ip() == "tunnel"
    # Ailleurs, l'en-tete ne vaut rien : n'importe quel client le pose.
    with app.flask_app.test_request_context(
            "/", headers={"X-Forwarded-For": "198.51.100.4"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.20"}, **_sur_port(9001)):
        assert app._client_ip() == "192.168.1.20"


def test_par_funnel_la_session_vaut_pour_les_applications_sans_rien_transporter(funnel,
                                                                                monkeypatch):
    """Le meme hote, deux ports : le navigateur envoie le cookie de session
    aux deux (un cookie ne tient pas compte du port), comme en local avec
    9001 et 9002. Le proxy le retire avant l'application (regle 19)."""
    vus = {}

    def amont(port, methode, chemin, entetes, corps):
        vus.clear()
        vus.update({k.lower(): v for k, v in entetes.items()})
        return 200, [("Content-Type", "text/plain")], b"ok"
    monkeypatch.setattr(app, "demander_a_l_application", amont)
    _cle_sso(monkeypatch)
    c = funnel
    # Sans session : vers la connexion, sur l'adresse du panneau.
    r = c.get("/prive-autorise/factures/12", **_par_funnel("apps"))
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://%s/login?suite=" % HOTE)
    with c.session_transaction(**_par_funnel("panneau")) as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 jeton="jeton-de-test", ouverte=int(time.time()))
    r = c.get("/prive-autorise/factures/12", **_par_funnel("apps"))
    assert r.status_code == 200
    assert vus["x-codelab-user"] == "marie"
    assert "session=" not in vus.get("cookie", "")


def test_le_passage_de_cloudflare_a_disparu():
    """Deux sites distincts (Cloudflare) obligeaient a transporter la
    session par un ticket. Avec un seul hote, ce code n'a plus d'appelant :
    il ne reste pas en route ouverte."""
    regles = {r.rule for r in app.flask_app.url_map.iter_rules()}
    assert not any(r.startswith("/passage") for r in regles)
    assert not hasattr(app, "echanger_le_passage")
    assert "passage" not in _lire_source("app-manager/app/panneau.js").split(
        "const lienOuverture")[1].split(";")[0]


def test_tailscale_ne_recoit_aucun_secret_et_ne_tourne_pas_en_root(funnel, monkeypatch,
                                                                    tmp_path):
    """tailscaled parle a internet : il n'herite ni de l'environnement du
    panneau (mot de passe, cle de session) ni de root, ni meme de l'uid des
    applications (regles 19 et 20)."""
    vus = {}

    class Faux:
        pid = 4242

        def poll(self):
            return None

    def popen(cmd, **kw):
        vus.update(kw, cmd=cmd)
        return Faux()
    monkeypatch.setattr(app.subprocess, "Popen", popen)
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "TAILSCALE_DIR", str(tmp_path / "ts"))
    monkeypatch.setenv("APP_MANAGER_PASSWORD", "ne-doit-pas-sortir")
    app._lancer_tailscaled()
    monkeypatch.setitem(app._tunnel, "proc", None)
    assert set(vus["env"]) == {"HOME", "PATH"}
    assert vus["preexec_fn"] is app._sans_privileges_tunnel
    assert "--tun=userspace-networking" in vus["cmd"]
    corps = _source_du_panneau().split("def _sans_privileges_tunnel(")[1].split("\ndef ")[0]
    assert "os.setuid(TUNNEL_UID)" in corps and "os.setgroups([])" in corps
    assert app.TUNNEL_UID not in (0, app.RUN_AS_UID)
    assert not (app.UID_APP_BASE <= app.TUNNEL_UID < app.UID_APP_BASE + app.UID_APP_PLAGE)


def test_la_cle_tailscale_passe_par_un_fichier_jamais_en_argument(funnel, monkeypatch, tmp_path):
    """La ligne de commande de tout processus se lit dans /proc par tout le
    conteneur, donc par les applications : la cle n'y passe jamais. Elle est
    lue dans un fichier 0600, efface aussitot."""
    vus = {}

    def faux_tailscale(*args, timeout=20):
        fichier = next(a for a in args if a.startswith("--auth-key=")).split("file:", 1)[1]
        vus["args"] = args
        vus["contenu"] = open(fichier).read()
        vus["mode"] = oct(os.stat(fichier).st_mode & 0o777)
        return 0, ""
    monkeypatch.setattr(app, "_tailscale", faux_tailscale)
    monkeypatch.setattr(app, "TAILSCALE_DIR", str(tmp_path))
    app._se_connecter(CLE)
    assert vus["contenu"] == CLE and vus["mode"] == "0o600"
    assert not any(CLE in a for a in vus["args"])
    assert not os.path.exists(tmp_path / "cle")


def test_la_cle_tailscale_ne_sort_jamais_du_coffre(funnel, compte_admin, monkeypatch):
    """Qui tient la cle ajoute une machine au reseau Tailscale : chiffree
    dans le coffre, elle ne revient dans aucune reponse."""
    monkeypatch.setattr(app, "tunnel_disponible", lambda: True)
    monkeypatch.setattr(app, "tour_du_tunnel", lambda: None)
    r = compte_admin.put("/api/tunnel", json={"cle": CLE, "nom": "mon-labo", "actif": True})
    assert r.status_code == 200, r.get_json()
    assert CLE not in r.get_data(as_text=True)
    assert r.get_json()["cle"] is True and r.get_json()["nom"] == "mon-labo"
    assert CLE not in json.dumps(app.lire_tunnel())
    assert app.reglage("tunnel_cle") == CLE
    assert CLE not in compte_admin.get("/api/tunnel").get_data(as_text=True)
    with app.stockage.transaction(app.DB_FILE) as cx:
        brut = " ".join(str(tuple(l)) for l in cx.execute("SELECT * FROM reglages_chiffres"))
    assert CLE not in brut
    # Deconnecter : la cle est oubliee, l'acces s'eteint.
    monkeypatch.setattr(app, "_tailscaled_en_vie", lambda: False)
    monkeypatch.setattr(app, "_arreter_tailscaled", lambda: None)
    r = compte_admin.put("/api/tunnel", json={"deconnecter": True})
    assert r.get_json()["cle"] is False and r.get_json()["actif"] is False
    assert not app.reglage("tunnel_cle")


def test_l_interrupteur_refuse_ce_qui_ne_se_tient_pas(funnel, compte_admin, deux_espaces,
                                                      monkeypatch):
    monkeypatch.setattr(app, "tour_du_tunnel", lambda: None)
    avant = app.lire_tunnel()
    for corps in ({"actif": "oui"}, {"cle": "pas-une-cle"}, {"nom": "Mon Labo!"},
                  {"nom": "-codelab"}):
        assert compte_admin.put("/api/tunnel", json=corps).status_code == 400, corps
    assert app.lire_tunnel() == avant and not app.reglage("tunnel_cle")
    # Sans le jeton de la session : refuse (regle 13).
    r = compte_admin.put("/api/tunnel", json={"actif": False},
                         headers={app.JETON_ENTETE: "faux"})
    assert r.status_code == 403
    # Un compte utilisateur : refuse.
    _session_marie(deux_espaces)
    assert deux_espaces.get("/api/tunnel", **_sur_port(9301)).status_code == 403


def test_la_supervision_connecte_ouvre_funnel_et_annonce_l_adresse(funnel, monkeypatch):
    """Cle enregistree, machine a connecter : tailscale up, puis les deux
    ports de Funnel, puis l'adresse par mail -- une fois."""
    appels, envois = [], []
    etats = iter([{"BackendState": "NeedsLogin"},
                  {"BackendState": "Running", "Self": {"HostName": "codelab",
                                                       "DNSName": HOTE + "."}}])
    monkeypatch.setitem(app._tunnel, "hote", "")
    monkeypatch.setitem(app._tunnel, "funnel", False)
    monkeypatch.setitem(app._tunnel, "essai_cle", 0)
    monkeypatch.setattr(app, "tunnel_disponible", lambda: True)
    monkeypatch.setattr(app, "_tailscaled_en_vie", lambda: True)
    monkeypatch.setattr(app, "_etat_tailscale", lambda: next(etats))
    monkeypatch.setattr(app, "_se_connecter", lambda cle: appels.append(("up", cle)) or (0, ""))
    monkeypatch.setattr(app, "_tailscale", lambda *a, **k: appels.append(a) or (0, ""))
    monkeypatch.setattr(app, "alertes_admin", lambda: ["admin@example.test"])
    monkeypatch.setattr(app, "envoyer_avec_repli",
                        lambda sujet, corps, cibles, **kw: envois.append(corps) or (True, ""))
    app.ecrire_reglage("tunnel_cle", CLE)
    app.ecrire_parametre("tunnel", {"actif": True})
    app.tour_du_tunnel()
    assert appels == [("up", CLE)]
    app.tour_du_tunnel()
    assert ("funnel", "--bg", "--https=443", "http://127.0.0.1:%d" % app.TUNNEL_PORT_PANNEAU) in appels
    assert ("funnel", "--bg", "--https=8443", "http://127.0.0.1:%d" % app.TUNNEL_PORT_APPS) in appels
    assert app.etat_du_tunnel()["etat"] == "actif"
    assert app.etat_du_tunnel()["panneau"] == "https://" + HOTE
    assert len(envois) == 1 and HOTE in envois[0]


def test_un_refus_de_tailscale_s_affiche_tel_quel_et_sans_danger(funnel, monkeypatch):
    """« HTTPS must be enabled », « funnel node attribute not set » : le
    message de tailscale dit quoi activer dans sa console. Il s'affiche par
    textContent, sans caractere de controle, et court."""
    sortie = "Funnel not available; HTTPS must be enabled.\x1b[0m\n" + "x" * 500
    monkeypatch.setattr(app, "_tailscale", lambda *a, **k: (1, sortie))
    erreur = app._ouvrir_funnel()
    assert erreur.startswith("Funnel not available; HTTPS must be enabled.")
    assert "\x1b" not in erreur and "\n" not in erreur and len(erreur) <= 300
    js = _lire_source("app-manager/app/panneau.js")
    peindre = js.split("function peindreTunnel")[1].split("\nfunction ")[0]
    assert "erreur.textContent=d.erreur" in peindre and "innerHTML" not in peindre


def test_les_ports_de_funnel_n_ecoutent_que_sur_la_boucle_locale():
    """Leur port est la preuve qu'une requete vient d'internet : joignables
    depuis le reseau, n'importe qui s'y presenterait comme « internet »
    -- et y choisirait son X-Forwarded-For."""
    src = _source_du_panneau()
    corps = src.split("def _ouvrir_les_ports_du_tunnel(")[1].split("\ndef ")[0]
    assert 'args=(p, "127.0.0.1")' in corps
    assert set(app.TUNNEL_PORTS).isdisjoint(range(app.PORT_MIN, app.PORT_MAX + 1))
    docker = _lire_source("app-manager/Dockerfile")
    assert ("COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscaled "
            "/usr/local/bin/") in docker
    assert "cloudflared" not in docker


# ------------------------------ les cles d'acces par Funnel ------------------------------

def test_par_funnel_touch_id_s_active(funnel):
    """Vecu : « Je ne peux pas activer Touch ID ». Le navigateur parle en
    https, mais la requete arrive en http au panneau : sans le savoir, il
    refusait les cles d'acces « faute de HTTPS ». L'adresse est fixe : la
    cle reste valable d'un redemarrage a l'autre."""
    with app.flask_app.test_request_context(
            "/", base_url="http://%s:%d" % (HOTE, app.TUNNEL_PORT_PANNEAU)):
        rp_id, origine, empechement = app.passkey_contexte()
    assert (rp_id, empechement) == (HOTE, "")
    assert origine.startswith("https://" + HOTE)


def test_en_local_par_l_adresse_ip_la_raison_dit_quoi_faire():
    with app.flask_app.test_request_context("/", base_url="http://192.168.1.20:9001"):
        _, _, empechement = app.passkey_contexte()
    assert "HTTPS" in empechement and "Proxy de confiance" not in empechement
    with app.flask_app.test_request_context("/", base_url="http://localhost:9001"):
        assert app.passkey_contexte()[2] == ""


def test_la_carte_montre_l_adresse_finale(funnel):
    """Vecu : « Comment choisir mon url ? Je veux voir simplement l'url
    final ». L'adresse est le nom de la machine, puis celui du reseau
    Tailscale : la carte recoit la fin et l'assemble a chaque lettre."""
    assert app.etat_du_tunnel()["reseau"] == "tail1234.ts.net"
    js = _lire_source("app-manager/app/panneau.js")
    assert "function tunnelApercu()" in js and "$('tunnel-apercu').textContent=" in js
    assert 'data-saisir="tunnelApercu"' in _page_panneau("dashboard.html")


def test_funnel_qui_attend_un_accord_montre_le_lien_au_lieu_d_un_delai_depasse(funnel,
                                                                              monkeypatch):
    """Vecu : « TimeoutExpired: Command [... 'funnel', '--bg', '--https=443',
    ...] timed out after 30 seconds ». Funnel pas encore autorise, tailscale
    affiche un lien vers sa console puis attend : le lien doit arriver dans
    la carte, et seul ce domaine y devient cliquable."""
    lien = "https://login.tailscale.com/f/funnel?node=nAbC123CNTRL"

    def attend(cmd, timeout=20, **kw):
        raise app.subprocess.TimeoutExpired(
            cmd, timeout, output=("Funnel is not enabled on your tailnet.\n"
                                   "To enable, visit:\n\n         " + lien + "\n").encode())
    monkeypatch.setattr(app.subprocess, "run", attend)
    code, sortie = app._tailscale("funnel", "--bg", "--https=443", "http://127.0.0.1:9011")
    assert code == -2 and lien in sortie
    erreur = app._ouvrir_funnel()
    assert erreur.endswith(lien) and "TimeoutExpired" not in erreur
    # Un autre domaine reste du texte.
    monkeypatch.setattr(app, "_tailscale",
                        lambda *a, **k: (1, "visit https://evil.example/f/funnel"))
    assert "Tailscale attend" not in app._ouvrir_funnel()
    js = _lire_source("app-manager/app/panneau.js")
    assert "/https:\\/\\/login\\.tailscale\\.com\\/" in js and "a.textContent=" in js
    assert "innerHTML" not in js.split("function peindreTunnel")[1].split("\nfunction ")[0]


def test_un_funnel_refuse_n_est_pas_reessaye_a_chaque_tour(funnel, monkeypatch):
    """Chaque essai peut attendre 30 s, verrou tenu : une fois par minute,
    ou tout de suite si l'on rallume l'interrupteur."""
    essais = []
    monkeypatch.setitem(app._tunnel, "funnel", False)
    monkeypatch.setitem(app._tunnel, "essai_funnel", 0)
    monkeypatch.setattr(app, "_ouvrir_funnel", lambda: essais.append(1) or "refus")
    etat = {"BackendState": "Running", "Self": {"HostName": "codelab", "DNSName": HOTE + "."}}
    app._tour_connecte(etat)
    app._tour_connecte(etat)
    assert len(essais) == 1


def test_le_lien_d_invitation_porte_l_adresse_tailscale(funnel, monkeypatch):
    """Vecu : « Le lien partage aux nouveaux utilisateurs doit embarquer
    l'url tailscale configure si active. » L'administrateur invite depuis le
    reseau local : un lien fait sur l'adresse locale ne s'ouvrirait pas de
    l'exterieur."""
    app.ecrire_parametre("tunnel", {"actif": True})
    with app.flask_app.test_request_context("/", base_url="http://192.168.1.20:9001"):
        assert app.lien_invitation("abc") == "https://%s/invitation/abc" % HOTE
    # Au redemarrage, avant que tailscaled reponde : la derniere adresse
    # annoncee, revalidee.
    monkeypatch.setitem(app._tunnel, "hote", "")
    app.ecrire_parametre("tunnel", {"actif": True, "annoncee": "https://" + HOTE})
    assert app.adresse_publique() == "https://" + HOTE
    app.ecrire_parametre("tunnel", {"actif": True, "annoncee": "https://evil.example"})
    assert app.adresse_publique() == ""
    # Acces eteint : plus l'adresse Tailscale.
    monkeypatch.setitem(app._tunnel, "hote", HOTE)
    app.ecrire_parametre("tunnel", {"actif": False, "annoncee": "https://" + HOTE})
    with app.flask_app.test_request_context("/", base_url="http://192.168.1.20:9001"):
        assert app.lien_invitation("abc") == "http://192.168.1.20:9001/invitation/abc"


# ===========================================================================
# LE JOURNAL
# le journal des acces, ses analyses, son miroir Postgres, la base SQLite du
# panneau, le pouls du hub.
# ===========================================================================

# ---------- 16. journal des acces ----------
#
# Deux usages, deux seulement : reconnaitre une intrusion, et savoir si un
# projet sert encore. Ce qui doit rester vrai : une ouverture n'est notee
# qu'apres les controles d'acces (un refus n'est pas une visite), le journal
# ne grossit pas indefiniment, et une ecriture impossible ne casse rien.


# ---------- la carte de chaleur, et les analyses ----------
#
# Vecu : « Ajoute une carte de chaleur pour l'utilisation des applications, et
# la possibilite de filtrer sur un utilisateur. Toutes les statistiques
# doivent etre historisees en base, et ces donnees alimentent les analyses. »
#
# Le point qui compte, et qui ne se voit pas a l'ecran : le fichier du
# journal est PLAFONNE a 1 Mo, donc il oublie. Tant qu'il servait a afficher
# quarante lignes, cela n'avait aucune importance. Des qu'on COMPTE, un total
# calcule sur une fenetre glissante ne veut plus rien dire -- il diminue tout
# seul a mesure que le journal tourne. Les analyses lisent donc la base, le
# fichier restant le repli.




def test_la_carte_compte_par_jour_et_pas_par_evenement(journal, tmp_path, monkeypatch):
    """Une case par jour : trois ouvertures le meme jour font une case a
    trois, pas trois cases."""
    # Hier a midi : quatre evenements a une minute d'ecart ne doivent pas
    # tomber de part et d'autre de minuit (le test echouait vers 23 h 58).
    hier = (datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
            - datetime.timedelta(days=1)).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(hier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(hier) + 60, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "c", "ts": int(hier) + 120, "genre": "ouverture", "qui": "marie", "app": "public"},
        # Une connexion n'est pas une ouverture : la carte parle d'usage des
        # applications, pas de presence.
        {"id": "d", "ts": int(hier) + 180, "genre": "connexion", "qui": "marie"},
    ])
    jours = app.carte_activite()
    assert len(jours) == 1, jours
    assert jours[0]["ouvertures"] == 3
    # Et combien d'applications distinctes ce jour-la : c'est ce qui
    # distingue « j'ai ouvert dix fois la meme » de « j'ai travaille partout ».
    assert jours[0]["apps"] == 2




def test_la_case_de_la_carte_detaille_le_jour_par_utilisateur_et_par_application(
        journal, tmp_path, monkeypatch):
    """Vecu : « ajoute du detail pour chaque case », puis « je veux le detail
    par utilisateur, en chiffre ». La case ne disait que le total et le
    nombre d'applications : qui, et ou, il fallait le chercher dans le
    journal. Les deux details sont classes du plus grand au plus petit, et un
    visiteur anonyme garde son nom vide (la page ecrit « visiteur anonyme »)."""
    hier = (datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
            - datetime.timedelta(days=1)).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(hier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(hier) + 60, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "c", "ts": int(hier) + 120, "genre": "ouverture", "qui": "jean", "app": "public"},
        {"id": "d", "ts": int(hier) + 180, "genre": "ouverture", "app": "public"},
        {"id": "e", "ts": int(hier) + 240, "genre": "ouverture", "qui": "marie", "app": "public"},
    ])
    [jour] = app.carte_activite()
    assert jour["ouvertures"] == 5 and jour["apps"] == 2
    assert jour["par_qui"] == [{"qui": "marie", "ouvertures": 3},
                               {"qui": "", "ouvertures": 1},
                               {"qui": "jean", "ouvertures": 1}], jour["par_qui"]
    assert jour["par_app"] == [{"app": "public", "ouvertures": 3},
                               {"app": "prive", "ouvertures": 2}], jour["par_app"]
    # Postgres compte de meme, dans la base, et passe par la meme mise en
    # forme : les deux sources rendent la meme case.
    src = _lire_source("app-manager/app/app.py")
    carte = src.split("def pg_carte_activite(")[1].split("\ndef ")[0]
    assert "GROUP BY 1, 2, 3" in carte and "detail_du_jour(" in carte
    # La page les montre EN CHIFFRES dans une bulle, au survol et a l'appui,
    # et le title natif (qui doublait la bulle, et jamais au doigt) a cede.
    page = _page_panneau("dashboard.html")
    bloc = page.split("function contenuBulleCarte(cle){")[1].split("\nfunction ")[0]
    assert "'Par utilisateur'" in bloc and "'Par application'" in bloc
    assert "Visiteur anonyme" in bloc
    liste = page.split("function listeBulleCarte(")[1].split("\nfunction ")[0]
    assert "cb-n" in liste and "--part" not in liste, "des chiffres, pas des barres"
    colonnes = page.split("function carteColonnes(annee, parJour, sommet){")[1].split("\nfunction ")[0]
    assert "data-jour=" in colonnes and 'title="${esc(titre)}"' not in colonnes
    assert "addEventListener('pointerover'" in page.split("function cacherBulleCarte(){")[1]




def test_le_repeint_garde_les_images_deja_chargees():
    """Vecu : « fais en sorte que les icones des applications ne clignotent
    pas ». Le moindre changement du hub (une pastille, un etat, le « il y a
    3 min » d'un widget) repeignait toute la grille : chaque <img> etait
    recree, et une image neuve, meme en cache, se decode a part -- toutes les
    icones vides le temps d'un affichage. Mesure dans Chromium : 6 a 24
    images recreees par repeint avant, 0 apres."""
    page = _page_panneau("dashboard.html")
    remplir = page.split("function remplir(el, html){")[1].split("\n}")[0]
    assert "imagesChargees(el)" in remplir and "reposerImages(el, images)" in remplir
    assert remplir.index("imagesChargees(el)") < remplir.index("el.innerHTML = html")
    reposer = page.split("function reposerImages(el, parSource){")[1].split("\nfunction ")[0]
    assert "neuve.replaceWith(vieille)" in reposer
    # src n'est jamais repose sur l'image gardee : le reposer relancerait le
    # chargement, et c'est le clignotement qu'on retirait.
    assert "name!=='src'" in reposer
    # Les autres peintures d'icones passent par le meme chemin.
    masquees = page.split("function peindreMasquees(){")[1].split("\n}\n")[0]
    assert "zone.innerHTML" not in masquees and "remplir(zone," in masquees
    assert "$('fa-icone').src=" not in page and "poserSource($('fa-icone')" in page
    assert "$('pr-icone').src=" not in page and "poserSource($('pr-icone')" in page




def test_le_bandeau_se_fond_sur_ce_qui_defile_dessous():
    """Vecu : « le header doit etre fondu lorsque je scroll en vertical et
    rester au-dessus des apps ». Un bandeau opaque coupait les tuiles net.
    Le voile (le fond de la page, estompe vers le bas) n'apparait que
    la page defilee : en haut, le rendu est identique a l'ancien au pixel
    pres (10 rendus compares). Le bandeau lui-meme reste transparent : mode.js
    lit sa couleur pour la barre d'etat, qui serait sinon delavee."""
    page = _page_panneau("dashboard.html")
    barre = page.split(".topbar{height:68px;")[1].split("}")[0]
    assert "background:transparent" in barre
    voile = page.split(".topbar::before{")[1].split("}")[0]
    # Un fondu en degrade, sans filtre compose. Vecu : « je ne peux pas scroll
    # avec mon telephone en vertical » -- sur iPhone, le flou (backdrop-filter)
    # et le masque d'un calque place DANS #defile bloquaient le defilement.
    assert "background:linear-gradient(var(--bg) calc(100% - 28px),transparent)" in voile
    assert "backdrop-filter" not in voile and "mask-image" not in voile
    assert "z-index:-1" in voile and "opacity:0" in voile
    assert ":root.page-defilee .topbar::before{opacity:1}" in page
    assert "position:sticky;top:0;z-index:40" in page, "le bandeau reste au-dessus des tuiles"
    socle = page.split("function majPageDefilee(){")[1].split("\n}")[0]
    assert "'page-defilee'" in socle and "defileur().scrollTop" in socle
    mode = _lire_source("app-manager/app/mode.js")
    assert "c === 'rgba(0, 0, 0, 0)'" in mode, "la barre d'etat retombe sur le fond de la page"




def test_la_carte_se_filtre_par_personne_et_par_application(journal, tmp_path, monkeypatch):
    hier = int(time.time() - 86400)
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": hier, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": hier, "genre": "ouverture", "qui": "jean", "app": "prive"},
        {"id": "c", "ts": hier, "genre": "ouverture", "qui": "marie", "app": "public"},
    ])
    assert app.carte_activite(qui="marie")[0]["ouvertures"] == 2
    assert app.carte_activite(app="prive")[0]["ouvertures"] == 2
    assert app.carte_activite(qui="marie", app="prive")[0]["ouvertures"] == 1
    # Un visiteur anonyme se filtre aussi : "" est une valeur, pas l'absence
    # de filtre.
    assert app.carte_activite(qui="") == []




def test_la_carte_ne_montre_que_l_annee_demandee(journal, tmp_path, monkeypatch):
    """Une annee civile, de janvier a decembre : ce qui est arrive l'an
    dernier n'a rien a faire sur la carte de cette annee, et inversement."""
    import datetime as _dt
    fuseau = app._fuseau()
    cette_annee = app.annee_courante()
    milieu = _dt.datetime(cette_annee, 6, 15, 12, 0, tzinfo=fuseau).timestamp()
    an_dernier = _dt.datetime(cette_annee - 1, 6, 15, 12, 0, tzinfo=fuseau).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(an_dernier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(milieu), "genre": "ouverture", "qui": "marie", "app": "prive"},
    ])
    jours = app.carte_activite(cette_annee)
    assert [j["jour"] for j in jours] == [f"{cette_annee}-06-15"]
    jours = app.carte_activite(cette_annee - 1)
    assert [j["jour"] for j in jours] == [f"{cette_annee - 1}-06-15"]
    # Sans annee demandee, c'est l'annee en cours.
    assert app.carte_activite() == app.carte_activite(cette_annee)




def test_les_annees_proposees_vont_de_la_premiere_a_aujourd_hui(journal, tmp_path,
                                                                monkeypatch):
    """Le selecteur doit offrir chaque annee entre la premiere utilisation et
    l'annee en cours -- y compris celles ou rien ne s'est passe, sans quoi la
    liste aurait des trous et l'on croirait a une perte de donnees."""
    import datetime as _dt
    fuseau = app._fuseau()
    cette_annee = app.annee_courante()
    vieux = _dt.datetime(cette_annee - 2, 3, 2, 12, 0, tzinfo=fuseau).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(vieux), "genre": "ouverture", "qui": "marie", "app": "prive"},
    ])
    assert app.annees_activite() == (cette_annee - 2, cette_annee - 2)
    assert app.annees_proposees(*app.annees_activite()) == [
        cette_annee - 2, cette_annee - 1, cette_annee]
    # Journal vide : l'annee en cours reste proposee, un selecteur vide
    # n'etant pas une reponse.
    assert app.annees_proposees(None, None) == [cette_annee]




def test_le_journal_ne_garde_que_la_duree_choisie(journal, tmp_path, monkeypatch):
    """Vecu : « limiter la duree de conservation du journal ». Il note les
    adresses IP, des donnees personnelles (RGPD) : Postgres gardait tout, le
    fichier local 50 000 evenements quel que soit leur age. Douze mois par
    defaut ; « sans limite » reste un choix possible."""
    monkeypatch.setattr(app, "pg_disponible", lambda: False)
    maintenant = time.time()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "vieux", "ts": int(maintenant - 400 * 86400), "genre": "connexion",
         "qui": "marie", "ip": "203.0.113.7"},
        {"id": "an", "ts": int(maintenant - 200 * 86400), "genre": "ouverture",
         "qui": "marie", "app": "prive"},
        {"id": "hier", "ts": int(maintenant - 86400), "genre": "ouverture",
         "qui": "marie", "app": "prive"},
    ])
    ids = lambda: sorted(e["id"] for e in app.lire_acces(limite=100))
    # Par defaut : douze mois.
    assert app.lire_conservation() == 12
    assert app.purger_le_journal(maintenant) == (1, None)
    assert ids() == ["an", "hier"]
    # Sans limite : rien ne part.
    app.ecrire_conservation(0)
    assert app.purger_le_journal(maintenant) == (0, 0) and ids() == ["an", "hier"]
    # Trois mois : ce qui a deux cents jours part aussi.
    app.ecrire_conservation(3)
    app.purger_le_journal(maintenant)
    assert ids() == ["hier"]
    # Une valeur inconnue dans le coffre retombe sur le defaut : ni tout
    # garder, ni tout effacer.
    for abimee in ({"mois": 7}, {"mois": True}, {"mois": "12"}, "12", None):
        app.ecrire_reglage("conservation_journal", abimee)
        assert app.lire_conservation() == 12, abimee
    # Un an fait bien douze mois, pas 360 jours.
    assert abs((maintenant - app.seuil_conservation(12, maintenant)) - 365.25 * 86400) < 1
    assert app.seuil_conservation(0, maintenant) is None




def test_la_conservation_se_regle_par_l_administrateur_seul(journal):
    """Le reglage efface des donnees : il est reserve a l'administrateur,
    passe par le jeton (comme toute ecriture) et n'accepte que les durees
    proposees -- un booleen n'est pas « 1 mois »."""
    c = journal
    assert c.get("/api/journal/conservation").status_code in (401, 403)
    assert c.put("/api/journal/conservation", json={"mois": 3}).status_code in (401, 403)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = c.get("/api/journal/conservation").get_json()
    assert d["mois"] == 12 and d["choix"] == [3, 6, 12, 24, 0]
    for mauvais in (5, -1, "12", True, None, 12.5):
        assert c.put("/api/journal/conservation", json={"mois": mauvais}).status_code == 400, mauvais
    r = c.put("/api/journal/conservation", json={"mois": 6})
    assert r.status_code == 200 and r.get_json()["mois"] == 6
    assert app.lire_conservation() == 6
    # Le changement se lit dans le journal, comme les autres reglages.
    assert any(e.get("action") == "conservation du journal : 6 mois"
               for e in app.lire_acces(limite=20))
    # Sans le bon jeton de la session, l'ecriture est refusee.
    r = c.put("/api/journal/conservation", json={"mois": 3},
              headers={app.JETON_ENTETE: "faux"})
    assert r.status_code == 403 and app.lire_conservation() == 6




def test_une_liste_habillee_suit_la_valeur_posee_par_le_script():
    """Vecu : la duree de conservation, reglee a 12 mois, affichait « 3 mois »
    -- la premiere option -- tant qu'on n'ouvrait pas la liste. Le navigateur
    ne signale pas un .value pose par le script : l'habillage redefinit
    value et selectedIndex sur chaque select pour repeindre son etiquette."""
    page = _page_panneau("dashboard.html")
    habiller = page.split("function dlHabiller(select){")[1].split("\nfunction ")[0]
    assert "for(const nom of ['value', 'selectedIndex'])" in habiller
    assert "set(v){ d.set.call(this, v); dlPeindre(enveloppe); }" in habiller
    assert "observe(select, {childList:true})" in habiller




def test_la_purge_vaut_pour_postgres_et_pour_le_journal_local():
    """Purger Postgres seul ne suffit pas : au demarrage, le panneau recopie
    le journal local dans la base (_pg_rattraper), et les lignes effacees
    reviendraient. La purge touche les deux, chaque jour, et des que le
    reglage change."""
    src = _lire_source("app-manager/app/app.py")
    purge = src.split("def purger_le_journal(")[1].split("\ndef ")[0]
    assert "DELETE FROM journal WHERE ts < ?" in purge
    assert "DELETE FROM acces WHERE ts < to_timestamp(%s)" in purge
    route = src.split("def api_conservation_ecrire(")[1].split("\n@flask_app")[0]
    assert "purger_le_journal()" in route
    assert "    demarrer_la_purge_du_journal()\n    servir(" in src
    page = _page_panneau("dashboard.html")
    # Raccourcir efface tout de suite : une confirmation, bouton au verbe.
    changer = page.split("async function cjChanger(sel){")[1].split("\n}")[0]
    assert "demander(" in changer and "'Effacer', true" in changer




def test_la_carte_est_reservee_a_l_administrateur(journal):
    """Elle dit qui a ouvert quoi et quand : c'est le meme secret que le
    journal, elle se garde comme lui."""
    c = journal
    assert c.get("/api/activite/carte").status_code in (401, 403)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.get("/api/activite/carte")
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["source"] in ("postgres", "fichier")
    assert isinstance(d["jours"], list)
    assert d["annee"] in d["annees"], (
        "l'annee affichee doit figurer dans la liste proposee")




def test_une_annee_farfelue_ne_fait_pas_tomber_la_carte(journal):
    """Le parametre arrive par l'URL : il se borne, il ne se croit pas."""
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    courante = app.annee_courante()
    for valeur in ("0", "-5", "99999", "beaucoup", ""):
        r = c.get("/api/activite/carte?annee=" + valeur)
        assert r.status_code == 200, valeur
        assert r.get_json()["annee"] == courante, valeur
    # Une annee plausible, elle, est respectee.
    r = c.get("/api/activite/carte?annee=" + str(courante - 1))
    assert r.get_json()["annee"] == courante - 1




def test_les_analyses_preferent_la_base_au_fichier_plafonne():
    """Le resume etait calcule sur le fichier : le total d'ouvertures d'un
    projet DIMINUAIT tout seul a mesure que le journal tournait."""
    src = _page_panneau("app.py")
    bloc = src.split("def api_activite(")[1].split("@flask_app")[0]
    assert "pg_resume_acces()" in bloc, "le resume doit venir de la base quand elle repond"
    assert "resume_acces()" in bloc, "et le fichier doit rester le repli"
    # Le comptage par jour se fait DANS la base : ramener un an d'evenements
    # pour les additionner en Python s'ecroulerait le jour ou l'historique
    # compte pour de bon.
    carte = src.split("def pg_carte_activite(")[1].split("\ndef ")[0]
    assert "GROUP BY" in carte and "count(*)" in carte




def test_la_carte_couvre_l_annee_de_janvier_a_decembre():
    """Une fenetre glissante commencait un jour quelconque : les mois se
    decalaient d'un cran chaque jour, et deux cartes prises a deux dates ne
    se comparaient pas."""
    page = _page_panneau("dashboard.html")
    # Les colonnes se batissent dans carteColonnes, appelee par peindreCarte.
    assert "carteColonnes(annee, parJour, sommet)" in page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    bloc = page.split("function carteColonnes(annee, parJour, sommet){")[1].split("\nfunction ")[0]
    assert "new Date(annee,0,1)" in bloc, "la carte doit commencer au 1er janvier"
    assert "new Date(annee,11,31)" in bloc, "et finir au 31 decembre"
    assert "d.fenetre" not in bloc, "la fenetre glissante n'a plus cours"
    # Les jours a venir ne se peignent pas comme des jours a zero : ce serait
    # affirmer une inactivite qui n'a pas encore eu lieu.
    assert "carte-case avenir" in bloc and ".carte-case.avenir{" in page




def test_la_ligne_des_mois_couvre_toute_la_largeur_de_la_carte():
    """Une etiquette par colonne laissait onze mois muets et un mot de dix
    pixels de large : on ne lisait rien."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    assert "grid-column:span" in bloc, (
        "chaque mois doit couvrir ses colonnes, pas une seule")
    # Les MEMES colonnes et le meme ecart pour les deux rangees : sans cela,
    # les mois ne tombent pas en face de leurs semaines. La ligne des mois
    # n'a qu'une rangee, elle ne reprend donc pas les sept lignes.
    assert "${gabarit}" in bloc and "${gabaritMois}" in bloc
    mois = bloc.split("const gabaritMois=")[1].split("\n")[0]
    assert "repeat(${colonnes},${cote}px)" in mois and "gap:${ecart}px" in mois
    assert "grid-template-rows" not in mois




def test_l_annee_de_la_carte_se_choisit():
    """« Et l'an dernier ? » n'avait aucune reponse : la carte ne montrait
    que les douze derniers mois, sans moyen de remonter."""
    page = _page_panneau("dashboard.html")
    assert 'id="carte-annee"' in page
    bloc = page.split("async function chargerCarte(){")[1].split("\n}")[0]
    assert "p.set('annee'" in bloc, "le choix doit partir au serveur"
    # La liste des annees vient du serveur : la page ne connait pas
    # l'historique, et une liste devinee proposerait des annees vides.
    annees = page.split("function peindreAnnees(d){")[1].split("\n}")[0]
    assert "d.annees" in annees and "d.annee" in annees




def test_les_paliers_de_la_carte_sont_relatifs_au_plus_charge():
    """Un seuil absolu afficherait une carte toute pale sur une installation
    calme, et toute sombre sur une installation chargee. Ce qu'on lit dans
    une carte de chaleur, c'est le relief."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function carteNiveau(")[1].split("\n}")[0]
    assert "sommet" in bloc and "/ sommet" in bloc
    # Cinq paliers, comme le calendrier de GitHub.
    for niveau in range(5):
        assert f".carte-case.n{niveau}" in page or niveau == 0




def test_un_acces_refuse_n_est_pas_une_visite(journal):
    """Le journal sert a savoir si un projet sert encore.

    Compter les refus dedans donnerait a une application fermee l'air d'etre
    tres frequentee, ce qui est exactement l'inverse de ce qu'on demande.
    """
    c = journal
    # Sans session, un projet prive redirige vers la connexion.
    assert c.get("/prive/", follow_redirects=False).status_code == 302
    assert [e for e in app.lire_acces() if e.get("genre") == "ouverture"] == []

    # Un projet public, lui, est une vraie visite -- meme sans compte.
    c.get("/public/")
    ouvertures = [e for e in app.lire_acces() if e.get("genre") == "ouverture"]
    assert len(ouvertures) == 1
    assert ouvertures[0]["app"] == "public" and ouvertures[0]["qui"] == ""




def test_une_page_web_ne_fait_pas_cinquante_lignes_de_journal(journal):
    """Une page, c'est des dizaines de requetes. Les compter toutes ne dirait
    plus rien de la frequentation, et remplirait le disque."""
    c = journal
    for _ in range(30):
        c.get("/public/")
    assert len([e for e in app.lire_acces() if e.get("genre") == "ouverture"]) == 1

    # Le regroupement passe : la visite suivante compte pour une nouvelle.
    app._dernier_acces.clear()
    c.get("/public/")
    assert len([e for e in app.lire_acces() if e.get("genre") == "ouverture"]) == 2




def test_les_connexions_et_les_echecs_sont_notes(journal):
    c = journal
    c.post("/login", json={"nom": "admin", "password": "faux"})
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    genres = [e["genre"] for e in app.lire_acces()]
    assert genres[:2] == ["connexion", "echec"], genres   # plus recent d'abord
    resume = app.resume_acces()
    assert resume["echecs"] == 1
    assert resume["comptes"]["admin"]["connexions"] == 1




def test_le_journal_a_un_plafond_au_lieu_de_remplir_le_disque(journal, monkeypatch):
    """Un journal sans plafond transforme une curiosite en panne : au-dela,
    les plus anciens evenements partent (Postgres, lui, garde tout)."""
    monkeypatch.setattr(app, "JOURNAL_MAX", 50)
    app._journal_ecrits[0] = 0
    for i in range(400):
        app.journaliser("ouverture", qui=f"compte{i}", app="public", ip="10.0.0.1")
    restants = app.lire_acces(limite=1000)
    assert len(restants) <= 50 + 200          # la purge passe tous les 200
    # Ce sont les plus ANCIENS qui partent.
    assert restants[0]["qui"] == "compte399"




def test_journaliser_ne_fait_jamais_tomber_le_service(journal, monkeypatch):
    """Disque plein ou montage en lecture seule : le proxy doit continuer.

    Faire echouer une connexion pour proteger son journal reviendrait a
    eteindre le service au moment ou on veut justement l'observer.
    """
    app.journaliser("connexion", qui="marie")          # ne leve pas
    assert journal.get("/public/").status_code in (200, 502, 503)
    assert journal.post("/login", json={"nom": "admin", "password": "secret-de-test"}).status_code == 200




def test_le_journal_est_reserve_a_l_administrateur(journal):
    """Il contient des adresses IP et le detail de qui ouvre quoi."""
    c = journal
    assert c.get("/api/activite").status_code == 401
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/activite").status_code == 403




# ---------- 21. miroir Postgres ----------
#
# Le fichier reste la source de verite : il tient sans base et se lit depuis
# une session SSH. Postgres est la memoire longue -- le fichier est plafonne
# a 1 Mo et oublie. Ce qui doit rester vrai : ecrire dans la base n'est
# JAMAIS sur le chemin d'une requete, et le rejeu ne double aucune ligne.

def test_journaliser_ne_depend_jamais_de_la_base(tmp_path, monkeypatch):
    """Une base eteinte, lente, ou une file pleine : la connexion passe quand
    meme. C'est la seule propriete qui compte -- le reste n'est que du
    journal."""
    monkeypatch.setattr(app, "PG_ACTIF", True)
    # File pleine : chaque depot est perdu, sans exception ni attente.
    pleine = queue.Queue(maxsize=1)
    pleine.put(("acces", {}))
    monkeypatch.setattr(app, "_pg_file", pleine)
    perdus = app._pg_etat["perdus"]

    debut = time.time()
    for _ in range(50):
        app.journaliser("connexion", qui="marie", ip="10.0.0.1")
    assert time.time() - debut < 1.0, "journaliser doit rendre la main tout de suite"
    assert app._pg_etat["perdus"] == perdus + 50
    # Et malgre tout, les 50 evenements sont dans le fichier.
    assert len(app.lire_acces(limite=1000)) == 50




def test_chaque_evenement_porte_un_identifiant(tmp_path, monkeypatch):
    """C'est lui qui rend le rattrapage rejouable.

    Sans identifiant, un rejeu apres une coupure de la base reinsererait les
    memes lignes -- et l'historique compterait double.
    """
    monkeypatch.setattr(app, "PG_ACTIF", False)
    app.journaliser("connexion", qui="marie")
    app.journaliser("connexion", qui="marie")
    ids = [e["id"] for e in app.lire_acces()]
    assert len(ids) == 2 and len(set(ids)) == 2, "deux evenements, deux identifiants"




def test_le_miroir_se_tait_quand_il_est_debranche(monkeypatch):
    """APP_MANAGER_PG=0, ou psycopg absent : rien ne part, rien ne casse."""
    monkeypatch.setattr(app, "PG_ACTIF", False)
    avant = app._pg_file.qsize()
    app._pg_deposer(("acces", {"id": "x"}))
    assert app._pg_file.qsize() == avant
    assert app.pg_disponible() is False




def test_le_miroir_ecrit_vraiment_dans_postgres(tmp_path, monkeypatch):
    """Contre un vrai serveur, quand il y en a un.

    Ignore sans base joignable : ce test verifie la creation des tables, le
    rejeu idempotent et la trace d'un compte supprime -- des choses qu'un
    faux curseur ne prouverait pas.
    """
    pytest.importorskip("psycopg")
    import psycopg
    dsn = os.environ.get("CODELAB_TEST_PG")
    if not dsn:
        pytest.skip("CODELAB_TEST_PG non defini : pas de serveur de test")
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception as e:
        pytest.skip(f"serveur de test injoignable : {e}")

    reglages = psycopg.conninfo.conninfo_to_dict(dsn)
    monkeypatch.setattr(app, "PG_BASE", "codelab_test_" + secrets.token_hex(4))
    monkeypatch.setattr(app, "PG_ACTIF", True)
    monkeypatch.setattr(app, "_pg_reglages", lambda: {
        "host": reglages.get("host", "127.0.0.1"), "port": reglages.get("port", "5432"),
        "user": reglages.get("user", "codelab"), "password": reglages.get("password", ""),
        "instance": reglages.get("dbname", "dagster")})

    app.journaliser("connexion", qui="marie", ip="10.0.0.1", role="utilisateur")
    app.journaliser("ouverture", qui="marie", app="site", ip="10.0.0.1")
    app.ecrire_utilisateurs({"marie": {"sel": "aa", "hash": "bb", "projets": ["site"],
                                       "cree": 0, "email": "marie@example.com"}})
    try:
        app._pg_preparer()
        with app._pg_connexion(app.PG_BASE) as cx:
            app._pg_rattraper(cx)
            assert cx.execute("SELECT count(*) FROM acces").fetchone()[0] == 2
            # Rejoue : les memes lignes ne rentrent pas deux fois.
            app._pg_rattraper(cx)
            assert cx.execute("SELECT count(*) FROM acces").fetchone()[0] == 2
            # Aucun secret n'a traverse.
            colonnes = [c[0] for c in cx.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'utilisateurs'").fetchall()]
            assert not ({"hash", "sel", "totp", "cle_publique"} & set(colonnes))
            # Un compte supprime laisse sa trace : le journal le nomme encore.
            app.ecrire_utilisateurs({})
            app._pg_ecrire_utilisateurs(cx)
            reste = cx.execute("SELECT nom, supprime IS NOT NULL FROM utilisateurs").fetchall()
            assert reste == [("marie", True)]
    finally:
        with app._pg_connexion(app._pg_reglages()["instance"]) as cx:
            cx.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                psycopg.sql.Identifier(app.PG_BASE)))


# ------------------- la base du panneau (SQLite) -------------------
#
# Les donnees vivaient dans une quinzaine de fichiers JSON ; elles vivent dans
# codelab.db, une table par sujet. Ce qui doit rester vrai : l'import des
# anciens fichiers ne perd rien et ne se rejoue pas, les originaux sont gardes
# (sans adresse en clair), le schema est versionne, et le diagnostic lit la
# base sans pouvoir l'ecrire.







def test_le_schema_est_versionne_et_la_base_reservee_au_panneau():
    import sqlite3
    app.load()
    cx = sqlite3.connect(app.DB_FILE)
    assert cx.execute("PRAGMA user_version").fetchone()[0] == app.stockage.VERSION
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"applications", "comptes", "acces_applications", "cles_acces", "categories",
            "masquees", "journal", "messages", "processus", "parametres",
            "reglages_chiffres"} <= tables
    cx.close()
    assert stat.S_IMODE(os.stat(app.DB_FILE).st_mode) == 0o600




def test_supprimer_un_compte_retire_aussi_ses_droits():
    app.ecrire_utilisateurs({"marie": {"sel": "a", "hash": "b", "projets": ["site"]},
                             "paul": {"sel": "c", "hash": "d", "projets": ["site"]}})
    comptes = app.lire_utilisateurs()
    comptes.pop("marie")
    app.ecrire_utilisateurs(comptes)
    lignes = app.stockage.lire(app.DB_FILE, "SELECT compte FROM acces_applications")
    assert [l["compte"] for l in lignes] == ["paul"]




def test_le_diagnostic_lit_la_base_en_lecture_seule(monkeypatch):
    monkeypatch.setenv("APP_MANAGER_STATE", app.STATE_DIR)
    app.save({"site": {"path": "/w/s", "command": "x", "port": 9101,
                       "visibility": "publique"}})
    app.ecrire_utilisateurs({"marie": {"sel": "a", "hash": "b", "projets": []}})
    app.ecrire_parametre("tunnel", {"actif": True, "annoncee": "https://codelab.tail1234.ts.net"})
    app.journaliser("connexion", qui="marie", ip="203.0.113.9")
    assert checks._apps_panneau()["site"]["visibility"] == "publique"
    assert checks._comptes_panneau() == {"marie": {}}
    assert checks._adresse_publique_panneau() == "https://codelab.tail1234.ts.net"
    app.ecrire_parametre("tunnel", {"actif": True, "annoncee": "https://evil.example"})
    assert checks._adresse_publique_panneau() == ""
    app.ecrire_parametre("tunnel", {"actif": False, "annoncee": "https://codelab.tail1234.ts.net"})
    assert checks._adresse_publique_panneau() == ""
    assert checks._ips_du_journal() == ["203.0.113.9"]
    # Lecture seule : une ecriture par cette voie est refusee.
    assert checks._requete_panneau("DELETE FROM applications") is None
    assert list(app.load()) == ["site"]





def test_l_onglet_du_panneau_s_appelle_codelab():
    assert "<title>CodeLab</title>" in _page_panneau("dashboard.html")
    assert "App Manager" not in _page_panneau("dashboard.html")




def test_le_panneau_declare_toujours_sa_propre_icone():
    """Safari garde une icone par nom d'hote : celle d'une application ou de
    Mailpit (meme hote, autre port) ne doit pas reprendre l'onglet du
    panneau. Chaque page du panneau declare donc la sienne, versionnee."""
    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        # Une adresse qu'aucun autre service du meme hote ne sert.
        assert 'rel="icon" type="image/png" sizes="128x128" href="/codelab-onglet.png"' in page, nom
        assert 'href="/apple-touch-icon.png?v=4"' in page, nom
        assert "/favicon.png?v=" not in page and "/favicon.ico?v=" not in page, nom
        # Au retour sur l'onglet, les balises sont reposees.
        assert "window.addEventListener('pageshow', reposerIcone);" in page, nom




def test_les_connexions_recentes_se_filtrent_et_restent_courtes():
    page = _page_panneau("dashboard.html")
    assert 'id="ac-genre"' in page and "AC_MAX=200" in page
    # 10 par defaut : la liste ne deborde plus a l'ouverture.
    assert "return 10;" in page





def test_sur_telephone_le_menu_est_ferme_et_s_ouvre_par_dessus():
    """Sur un telephone, la colonne de gauche prenait la moitie de l'ecran."""
    page = _page_panneau("dashboard.html")
    assert "@media (max-width:760px)" in page
    assert ".sidebar.ouvert{transform:none}" in page
    assert "function fermerMenuMobile()" in page
    # Changer de rubrique referme le menu.
    debut = page.index("function showSection(name){")
    assert "fermerMenuMobile();" in page[debut:debut + 800]




def test_sur_telephone_le_hub_est_une_grille_d_icones():
    page = _page_panneau("dashboard.html")
    assert ':root[data-tuiles="icones"] .hub-grille{grid-template-columns:repeat(4,minmax(0,1fr))' in page




def test_la_page_de_connexion_ne_montre_pas_credentials_sur_telephone():
    page = _page_panneau("login.html")
    assert "@media (max-width:760px){.pied{display:none}}" in page




# ---------------- Le pouls du hub : Serveur et Activite ----------------
#
# Vecu : « je veux une nouvelle version un peu plus dynamique, des chiffres,
# des graphiques qui s'affichent progressivement, des reliefs ou des effets
# sur le texte ».

def test_le_pouls_du_serveur_est_reserve_a_l_administrateur(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/hub/pouls").status_code in (401, 403)
    d = c.get("/api/hub/disposition").get_json()
    assert not {w["id"] for w in d["catalogue"]} & {"serveur", "activite"}
    for wid, taille in (("serveur", "petit"), ("activite", "moyen")):
        r = c.put("/api/hub/disposition", json={"elements": [{"t": "w", "id": wid, "taille": taille}]})
        assert r.status_code == 400, wid




def test_le_pouls_donne_la_machine_et_les_ouvertures_recentes(deux_espaces, monkeypatch):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    monkeypatch.setattr(app, "pg_disponible", lambda: False)
    app._serveur_historique.clear()
    app.serveur_tick()
    app.serveur_tick()
    maintenant = app.time.time()
    journal = [{"genre": "ouverture", "ts": maintenant, "app": "prive-autorise"},
               {"genre": "ouverture", "ts": maintenant - 3600, "app": "prive-autorise"},
               {"genre": "ouverture", "ts": maintenant - 3 * 86400, "app": "autre"},
               # La quinzaine d'avant : comptee pour la tendance seulement.
               {"genre": "ouverture", "ts": maintenant - 20 * 86400, "app": "autre"},
               # Ni trop vieux, ni une connexion, ne comptent.
               {"genre": "ouverture", "ts": maintenant - 60 * 86400, "app": "autre"},
               {"genre": "connexion", "ts": maintenant, "qui": "marie"}]
    monkeypatch.setattr(app, "lire_acces", lambda **k: journal)
    d = c.get("/api/hub/pouls").get_json()
    s = d["serveur"]
    assert 0 <= s["cpu"] <= 100 * s["coeurs"]
    assert 0 < s["memoire"]["pct"] <= 100 and s["memoire"]["total"] > 0
    assert len(s["historique"]) == 2 and set(s["historique"][0]) == {"t", "cpu", "mem"}
    a = d["activite"]
    assert len(a["jours"]) == app.ACTIVITE_JOURS
    assert a["jours"][-1]["ouvertures"] == sum(1 for e in journal[:2]
                                               if app._jour(e["ts"]) == a["jours"][-1]["jour"])
    assert a["total"] == 3 and a["precedent"] == 1
    assert a["apps"][0] == {"name": "prive-autorise", "titre": a["apps"][0]["titre"], "ouvertures": 2}
    # On ne demande que ce qu'on affiche.
    assert set(c.get("/api/hub/pouls?quoi=serveur").get_json()) == {"serveur"}




def test_le_serveur_se_mesure_dans_le_moniteur_et_garde_dix_minutes():
    source = _source_du_panneau()
    boucle = source.split("def start_monitor_thread():")[1].split("t = threading.Thread")[0]
    assert "serveur_tick()" in boucle
    assert app.SERVEUR_HISTORIQUE_LEN * 10 == 600




def test_le_hub_anime_ses_chiffres_et_dessine_ses_graphiques():
    page = _page_panneau("dashboard.html")
    for trace in (
            # Les deux widgets, branches sur leur emplacement garde.
            "if(e.id==='serveur' || e.id==='activite') return emplacementPouls(e.id, e.taille);",
            'data-garder="pouls-${id}-${taille}"', "/api/hub/pouls?quoi=",
            # Les chiffres defilent depuis la derniere valeur montree.
            "function animerChiffres(", "valeursVues", "data-anim=\"nombre\"",
            "chiffre('etat-en-ligne', enLigne)", "chiffre('messages', n)",
            # Les graphiques ne jouent leur entree qu'une fois.
            "dejaApparus", "@keyframes rideau", "@keyframes barre-pousse",
            # Le relief : cascade d'entree, inclinaison, bonjour qui s'eclaire.
            "function entreeDuHub(", "@keyframes tuile-entree", ".survol::after",
            'class="salut-nom"', "@keyframes salut-reflet", 'id="hub-resume"',
            # Jamais la couleur seule pour dire « sature ».
            "const niveauCharge = v => v>=92 ? ['err','saturé'] : v>=80 ? ['warn','élevé'] : ['',''];"):
        assert trace in page, trace
    # Moins de mouvement demande : tout s'arrete.
    sobre = page.split("@media (prefers-reduced-motion:reduce){\n  :is(.salut")[1].split("}\n}")[0]
    for cible in ("#hub-liste.entree [data-el]", ".barres-jours.apparait i", ".courbe.apparait *"):
        assert cible in sobre, cible
    assert "if(sobre || avant===fin){ poserAnim(n, fin); return; }" in page




def test_les_parametres_n_ont_plus_d_erreurs_d_affichage():
    """Vecu : un titre qui passe au vert et des coins qui depassent au survol,
    une fenetre de cartes dans une carte, des gelules vertes en colonne, des
    tuiles du hub penchees en trapeze."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("LES PARAMETRES, DANS LE STYLE DU PANNEAU.")[1].split("LES REGLAGES D'UN COMPTE UTILISATEUR")[0]
    # Les cartes coupent ce qui deborde ; le titre ne change plus de couleur.
    assert "border-radius:20px;padding:20px 24px;overflow:hidden" in bloc
    assert ".settings-card.pliable>.card-top:hover .card-name b{color:inherit}" in bloc
    # Plus de fenetre dans la page : ni fond, ni cadre, la police du panneau.
    assert "background:none;border:0;border-radius:0;box-shadow:none;\n   overflow:visible;font-family:var(--sans)" in bloc
    # Une coche suffit : pas de ligne cerclee d'accent.
    assert "#sec-parametres .choix-ligne:has(>input:checked){background:none;border-color:transparent}" in bloc
    # Quatre appareils, le reste derriere un bouton ; cet appareil toujours en tete.
    assert "const AP_VISIBLES=4;" in page and "Afficher les ${caches} autres" in page
    assert "(y.courant?1:0)-(x.courant?1:0)" in page
    # Le telephone et la tablette (mode application compris) heritent des
    # memes cartes : la regle n'est plus reservee aux grands ecrans.
    cartes = bloc.split("LES CARTES, A TOUTES LES TAILLES.")[1]
    avant_media = cartes.split("@media (max-width:760px)")[0]
    assert "border-radius:20px;padding:20px 24px;overflow:hidden" in avant_media
    # Le hub ne penche plus ses tuiles.
    assert "rotateX(var(--rx" not in page and "--ry" not in page
    # Ni ne les souleve : la mosaique coupe ce qui depasse, le haut d'une
    # tuile de la premiere rangee paraissait tranche.
    survol = page.split("/* -- le relief au survol (souris)")[1].split("/* -- l'etat")[0]
    assert "translate" not in survol.split("*/", 1)[1]


# ===========================================================================
# SAUVEGARDES ET ALERTES
# sauvegardes, alertes et envoi des mails, Mailpit, surveillance externe,
# version, pannes et hors ligne.
# ===========================================================================

# ------------------- 7. alertes par mail -------------------
#
# Une alerte qui part en boucle est pire que pas d'alerte : la boite se
# remplit, et on prend l'habitude de ne plus la lire. Ces tests tiennent la
# seule propriete qui compte vraiment -- un incident, un mail.




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
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    (tmp_path / "credentials.env").write_text("")
    app.ecrire_alertes(True, [])
    _, manquants = app.config_smtp()
    assert manquants == ["serveur d'envoi", "adresse d'expedition",
                         "adresse d'alerte de l'administrateur"]




def test_les_adresses_saisies_sont_nettoyees():
    """Saisie humaine : virgules, espaces, doublons, ligne vide."""
    assert app._adresses("moi@example.com, autre@example.com ,moi@example.com,") == [
        "moi@example.com", "autre@example.com"]
    assert app._adresses(["pas-une-adresse", " ok@example.com "]) == ["ok@example.com"]


# ---------- 20. le serveur d'envoi n'est pas les alertes ----------
#
# Un serveur SMTP parfaitement configure passait pour incomplet tant qu'aucun
# destinataire d'alerte n'etait saisi -- alors qu'il sert aussi les codes de
# verification d'adresse et l'inscription libre, qui n'ont rien a voir avec
# les alertes. Les deux ont chacun leur onglet, et le test d'envoi doit
# pouvoir viser une adresse sans qu'aucune alerte soit reglee.




def test_le_serveur_d_envoi_se_declare_pret_sans_alerte_reglee(envoi):
    """Le serveur d'envoi et les alertes sont deux choses : un serveur
    parfaitement configure passerait pour incomplet tant qu'aucune adresse
    d'alerte n'est saisie, alors qu'il sert aussi les codes de verification."""
    etat = envoi.get("/api/alertes").get_json()
    assert etat["smtp_ok"] is True
    assert etat["source"] == "origine"
    assert etat["origine_utilisable"] is True
    assert etat["personnalisee"] is None
    # Ca, en revanche, c'est l'affaire des alertes.
    assert "adresse d'alerte de l'administrateur" in etat["manquants"]




def test_une_configuration_qui_ne_repond_pas_n_est_pas_enregistree(envoi, monkeypatch):
    """Ce qui remplace l'ancien bouton "mail de test" : un test qu'il fallait
    penser a lancer, et dont l'oubli ne se voyait pas. Ici une configuration
    qui ne marche pas n'entre tout simplement pas."""
    monkeypatch.setattr(app, "verifier_smtp",
                        lambda cfg: (False, "SMTPAuthenticationError: 535 refuse"))
    r = envoi.post("/api/alertes", json={
        "actif": False, "admin": ["moi@example.com"],
        "smtp": {"host": "faux.example.com", "user": "x@example.com",
                 "password": "mauvais"}})
    assert r.status_code == 400, r.data
    assert "535 refuse" in r.get_json()["detail"]
    assert app.lire_smtp_personnalise() is None, "une configuration refusee a ete ecrite"
    # Et surtout : celle d'origine sert toujours.
    assert app.smtp_origine()["host"] == "origine.example.com"




def test_une_configuration_qui_repond_devient_la_source(envoi, monkeypatch):
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    r = envoi.post("/api/alertes", json={
        "actif": False, "admin": ["moi@example.com"],
        "smtp": {"host": "perso.example.com", "user": "perso@example.com",
                 "password": "bon"}})
    assert r.status_code == 200, r.data
    assert r.get_json()["source"] == "personnalisee"
    cfg, _ = app.config_smtp()
    assert cfg["host"] == "perso.example.com"
    # L'ORIGINE EST INTACTE. C'est tout l'objet de la separation : se tromper
    # d'un caractere depuis une page ne doit pas supprimer le seul moyen de
    # prevenir qu'une application est tombee.
    assert app.smtp_origine()["host"] == "origine.example.com"
    assert app.smtp_origine()["password"] == "mot-de-passe-origine"




def test_enregistrer_l_adresse_admin_n_efface_pas_la_configuration_d_origine(envoi):
    """Le bloc de credentials.env est REMPLACE en entier par upsert_shared_block.
    Y ecrire la seule adresse d'administration effacerait le serveur d'envoi et
    son mot de passe -- donc le repli que tout ceci construit."""
    r = envoi.post("/api/alertes", json={"actif": True,
                                         "admin": ["chef@example.com"]})
    assert r.status_code == 200, r.data
    origine = app.smtp_origine()
    assert origine["host"] == "origine.example.com", "le serveur d'origine a ete efface"
    assert origine["password"] == "mot-de-passe-origine", "le mot de passe a ete efface"
    assert app.alertes_admin() == ["chef@example.com"]




def test_revenir_a_l_origine_est_toujours_permis(envoi, monkeypatch):
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    envoi.post("/api/alertes", json={"actif": False, "admin": ["moi@example.com"],
                                     "smtp": {"host": "perso.example.com",
                                              "user": "p@example.com",
                                              "password": "bon"}})
    assert app.lire_smtp_personnalise() is not None
    # Serveur vide = retour a l'origine. Sans condition : c'est la marche
    # arriere, et on la cherche justement quand rien ne va.
    r = envoi.post("/api/alertes", json={"actif": False, "admin": ["moi@example.com"],
                                         "smtp": {"host": ""}})
    assert r.status_code == 200, r.data
    assert app.lire_smtp_personnalise() is None
    assert app.config_smtp()[0]["host"] == "origine.example.com"




def test_l_envoi_repli_sur_l_origine_quand_la_personnalisee_tombe(envoi, monkeypatch):
    """Une configuration verifiee le jour de son enregistrement peut cesser de
    marcher : mot de passe revoque, quota, serveur eteint. Ce jour-la, l'alerte
    doit sortir quand meme."""
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    envoi.post("/api/alertes", json={"actif": True, "admin": ["moi@example.com"],
                                     "smtp": {"host": "perso.example.com",
                                              "user": "p@example.com",
                                              "password": "bon"}})
    essais = []

    def _envoi(cfg, sujet, corps, destinataires=None, **_):
        essais.append(cfg["host"])
        if cfg["host"] == "perso.example.com":
            raise OSError("serveur injoignable")
    monkeypatch.setattr(app, "envoyer_mail", _envoi)

    envoye, detail = app.envoyer_avec_repli("sujet", "corps", ["moi@example.com"])
    assert envoye is True, detail
    assert essais == ["perso.example.com", "origine.example.com"], (
        "le repli n'a pas eu lieu dans cet ordre")
    assert detail == "d'origine"




def test_chaque_application_a_ses_destinataires_et_l_admin_recoit_tout(envoi):
    """Une application de facturation ne previent pas les memes personnes
    qu'un site vitrine. Mais l'adresse d'administration s'ajoute TOUJOURS :
    une application dont la liste est vide ne tombe pas en silence."""
    envoi.post("/api/alertes", json={"actif": True, "admin": ["chef@example.com"]})
    r = envoi.put("/api/alertes/application/site",
                  json={"alertes": "equipe@example.com, autre@example.com"})
    assert r.status_code == 200, r.data
    assert r.get_json()["destinataires"] == ["chef@example.com",
                                             "equipe@example.com",
                                             "autre@example.com"]
    # Une application sans liste propre : l'admin reste, et il est seul.
    app.save(dict(app.load(), vitrine={"path": "/w/v", "command": "x",
                                       "port": 9102, "enabled": True}))
    assert app.destinataires_alerte("vitrine") == ["chef@example.com"]




def test_l_adresse_d_alerte_d_une_installation_existante_est_reprise(envoi):
    """Avant les alertes par application, tous les destinataires vivaient dans
    alertes.json. Sans repli sur cette liste, une installation existante aurait
    cesse d'etre prevenue a la mise a jour -- silencieusement."""
    _ecrire_bloc_alertes({"ALERTE_ADMIN": ""})
    app.ecrire_alertes(True, ["ancien@example.com"])
    assert app.alertes_admin() == ["ancien@example.com"]




def test_activer_les_alertes_sans_adresse_d_administration_est_refuse(envoi):
    r = envoi.post("/api/alertes", json={"actif": True, "admin": []})
    assert r.status_code == 400, r.data
    assert "administrateur" in r.get_json()["error"]


# ---------- 23 sexies. la surveillance se declenche toute seule ----------
#
# Le diagnostic n'avait AUCUN planning : l'asset ne tournait que si quelqu'un
# allait cliquer "Materialize" dans Dagster. Une surveillance qu'il faut
# declencher ne previent de rien -- on ne la declenche que quand on soupconne
# deja quelque chose.
#
# La consequence depassait le diagnostic : alerte_mail_echec est un
# run_failure_sensor, il reagit a un run EN ECHEC. Aucun run ne demarrant
# jamais tout seul, aucun ne pouvait echouer, donc aucune alerte ne partait.
# Un systeme d'alerte complet, avec son SMTP et son repli, qui n'attendait
# qu'un clic pour servir.
#
# Tests TEXTUELS, et il faut le dire : l'image du panneau ne contient pas
# dagster, donc importer definitions.py ici echouerait. Ils constatent que le
# planning est declare et branche, pas qu'il se declenche -- ce qui le prouve,
# c'est la page des schedules de Dagster.




def test_le_diagnostic_a_un_planning_et_il_est_actif():
    src = _definitions_diagnostic()
    assert "ScheduleDefinition(" in src, (
        "aucun planning : l'asset ne tournera que si on le declenche a la main, "
        "et le capteur d'alerte n'aura jamais de run en echec a signaler")
    # Une fois par heure, hors de l'heure pile : toutes les quinze minutes,
    # chaque run chargeait Dagster, le panneau et Postgres 96 fois par jour.
    assert 'cron_schedule="7 * * * *"' in src
    assert "*/15" not in src
    assert "DefaultScheduleStatus.RUNNING" in src, (
        "un planning qu'il faut activer a la main dans l'interface : on ne "
        "s'apercoit de l'oubli qu'en ratant une alerte")




def test_le_planning_est_branche_dans_les_definitions():
    """Declare ne suffit pas : Dagster ne voit que ce qui est dans defs.

    Sans cette verification, un planning parfaitement ecrit mais absent de
    Definitions() passerait le test precedent tout en ne tournant jamais.
    """
    src = _definitions_diagnostic()
    bloc = src[src.index("defs = Definitions("):]
    assert "schedules=[" in bloc, "le planning n'est pas passe a Definitions()"
    assert "jobs=[" in bloc, "le job du planning n'est pas passe a Definitions()"
    assert "sensors=[" in bloc




def test_la_verification_approfondie_reste_a_la_demande():
    """Ce qui AGIT ne se planifie pas.

    run_tests() et la suite du panneau ecrivent, traversent le proxy et
    laissent des traces. Les jouer quatre fois par heure remplirait les
    journaux de traces que personne n'a demandees. Seules les sondes en
    lecture seule tournent toutes les heures.
    """
    src = _definitions_diagnostic()
    assert "run_tests" not in src, (
        "la verification approfondie est entree dans le code planifie")
    assert "lancer_suite_du_panneau" not in src


# ---------- 29. il n'y avait aucune sauvegarde ----------
#
# Rien ne copiait utilisateurs.json, apps.json, le journal des acces ni la
# moindre base. Un disque perdu, et il fallait tout redeclarer de memoire :
# les comptes, les droits par projet, les applications, les categories,
# l'adresse publique -- et les donnees, elles, ne se redeclarent pas.
#
# Le partage des roles n'est pas un choix d'organisation : pg_dump REFUSE de
# sauvegarder un serveur plus recent que lui. Le panneau tourne sur une image
# dont le client Postgres a une version de retard ; codelab-postgres, lui,
# porte le pg_dump de la version exacte du serveur. Chacun sauvegarde ce
# qu'il est SEUL a pouvoir sauvegarder.




def test_l_archive_de_l_etat_porte_ce_qui_ne_se_redeclare_pas(sauvegardes):
    """Les comptes, les applications, le journal. Pas les journaux
    d'applications : ils se regenerent, et ils sont de loin ce qui pese."""
    _, cible = sauvegardes
    app.save({"demo": {"path": "/w/d", "command": "x", "port": 9101}})
    app.journaliser("connexion", qui="marie", ip="10.0.0.1")
    chemin = app.sauvegarder_l_etat()
    import sqlite3
    import tarfile
    with tarfile.open(chemin) as archive:
        noms = {n.lstrip("./") for n in archive.getnames()}
        # La base y est sous forme d'INSTANTANE coherent, pris par SQLite --
        # jamais le fichier vivant ni son -wal.
        archive.extract("./codelab.db", path=cible / "restauree")
    assert "codelab.db" in noms
    assert not {"codelab.db-wal", "codelab.db-shm"} & noms
    cx = sqlite3.connect(cible / "restauree" / "codelab.db")
    assert [r[0] for r in cx.execute("SELECT nom FROM applications")] == ["demo"]
    assert cx.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
    cx.close()
    # Les journaux d'applications pesent et se regenerent ; le dossier
    # personnel des applications est un cache, ECRIVABLE PAR ELLES -- l'y
    # inclure leur laisserait decider du poids de la sauvegarde.
    for exclu in app.ARCHIVE_EXCLUS:
        assert not [n for n in noms if n.startswith(exclu)], (exclu, sorted(noms))
    # 0600 : l'archive porte les empreintes de mots de passe et le journal
    # des acces. Les applications tournent sous un autre uid, et n'ont aucune
    # raison de pouvoir la lire.
    assert oct(os.stat(chemin).st_mode)[-3:] == "600"
    # Et aucun secret : credentials.env ne vit pas dans le dossier d'etat, et
    # c'est ce qui permet de recopier l'archive ailleurs sans la vider.
    assert not [n for n in noms if "credentials" in n]




def test_une_archive_a_moitie_ecrite_ne_prend_pas_la_place_de_la_bonne(sauvegardes):
    """Un arret au milieu de l'ecriture laisserait sinon une archive tronquee
    portant l'heure la plus recente -- celle qu'on choisirait pour
    restaurer."""
    source = inspect.getsource(app.sauvegarder_l_etat)
    assert ".partielle" in source and "os.replace(" in source
    # Elle est aussi RELUE avant d'etre gardee, comme les dumps Postgres.
    assert "getmembers()" in source
    _, cible = sauvegardes
    app.sauvegarder_l_etat()
    assert not [n for n in os.listdir(cible) if n.endswith(".partielle")]




def test_la_rotation_garde_les_plus_recentes(sauvegardes, monkeypatch):
    _, cible = sauvegardes
    monkeypatch.setattr(app, "SAUVEGARDES_GARDEES", 3)
    for jour in range(6):
        (cible / f"{app.SAUVEGARDE_PREFIXE}2026010{jour}-000000.tar.gz").write_text("x")
    app._rotation_sauvegardes()
    restantes = sorted(os.listdir(cible))
    assert len(restantes) == 3
    # Les trois DERNIERES : le nom porte la date, donc l'ordre alphabetique
    # est l'ordre chronologique -- y compris au passage d'une annee, la ou un
    # tri sur la date de fichier suivrait une copie.
    assert restantes[0].endswith("20260103-000000.tar.gz")




def test_par_defaut_seule_la_derniere_sauvegarde_reste(sauvegardes, monkeypatch):
    """Vecu : « Gestion des sauvegardes, garder que la derniere pour ne pas
    encombrer le stockage ». La rotation passe APRES l'ecriture et la
    relecture : la plus recente remplace l'ancienne, jamais l'inverse."""
    _, cible = sauvegardes
    assert app.SAUVEGARDES_GARDEES == 1 and app.WORKSPACE_GARDEES == 1
    ancienne = cible / f"{app.SAUVEGARDE_PREFIXE}20200101-000000.tar.gz"
    ancienne.write_text("x")
    nouvelle = app.sauvegarder_l_etat()
    assert sorted(os.listdir(cible)) == [os.path.basename(nouvelle)]
    # Et Postgres, par base.
    entree = open(os.path.join(_racine_depot(), "postgres", "entrypoint.sh"), encoding="utf-8").read()
    assert 'SAUVEGARDES_GARDEES="${CODELAB_SAUVEGARDES_GARDEES:-1}"' in entree




def test_sans_volume_monte_le_panneau_ne_sauvegarde_pas(monkeypatch, tmp_path):
    """Ecrire dans la couche du conteneur donnerait une sauvegarde qui
    disparait au premier « docker compose down » : pire que rien, parce
    qu'on croit en avoir une."""
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(tmp_path / "absent"))
    assert not app.sauvegardes_actives()
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(tmp_path))
    assert app.sauvegardes_actives()
    monkeypatch.setattr(app, "SAUVEGARDE_HEURES", 0)
    assert not app.sauvegardes_actives()




def test_la_sonde_des_sauvegardes_distingue_les_trois_cas(tmp_path):
    """Absente, en retard, morte : trois rangs, trois gestes differents."""
    dossier = tmp_path / "sauvegardes"
    dossier.mkdir()

    # Rien encore : une alerte, pas une panne -- au premier demarrage, c'est
    # l'etat normal pendant quelques secondes.
    etat, _, detail = check_sauvegardes(str(dossier))
    assert etat is Etat.ALERTE and "rien pour" in detail

    def poser(nom, heures):
        f = dossier / nom
        f.write_text("x")
        quand = time.time() - heures * 3600
        os.utime(f, (quand, quand))

    poser("base-codelab-20260101-000000.dump", 2)
    poser("panneau-20260101-000000.tar.gz", 2)
    assert check_sauvegardes(str(dossier))[0] is Etat.OK

    # Un passage manque. C'est la PLUS RECENTE de chaque famille qui compte :
    # en ajouter une vieille a cote d'une fraiche ne change rien, et c'est
    # bien ce qu'on veut -- sinon la rotation elle-meme declencherait
    # l'alerte.
    poser("panneau-20260101-000000.tar.gz", SAUVEGARDE_FRAICHE_H + 1)
    assert check_sauvegardes(str(dossier))[0] is Etat.ALERTE

    # Plus rien depuis trois jours : ce n'est plus un retard, c'est arrete.
    poser("panneau-20260101-000000.tar.gz", SAUVEGARDE_VIEILLE_H + 1)
    etat, _, detail = check_sauvegardes(str(dossier))
    assert etat is Etat.ECHEC and "ne tourne plus" in detail

    # Volume absent : le compose est a completer.
    etat, _, detail = check_sauvegardes(str(tmp_path / "jamais"))
    assert etat is Etat.ALERTE and "docker-compose.yml" in detail




def test_les_deux_services_ecrivent_dans_le_meme_dossier():
    """Les dumps et l'archive de l'etat au meme endroit : on copie un seul
    dossier pour tout emporter."""
    for chemin in _composes():
        services = _services_du_compose(chemin)
        texte = open(chemin, encoding="utf-8").read()
        assert texte.count("/sauvegardes:/sauvegardes") == 2, (
            f"{os.path.basename(chemin)} : les deux services doivent monter "
            f"le volume de sauvegarde")
        assert set(services) >= {"codelab-postgres", "codelab-app-manager"}




def test_la_sauvegarde_des_bases_est_verifiee_avant_d_etre_gardee():
    """Une sauvegarde qu'on decouvre illisible le jour de la restauration ne
    vaut pas mieux que pas de sauvegarde."""
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "entrypoint.sh")
    if not os.path.exists(chemin):
        pytest.skip("entrypoint postgres absent de cette image")
    script = open(chemin, encoding="utf-8").read()
    assert "pg_dump" in script and "-Fc" in script
    # La relecture complete du fichier, et le retrait s'il ne passe pas.
    bloc = script.split("sauvegarder_une_base()")[1].split("\n}")[0]
    assert "pg_restore --list" in bloc
    assert bloc.count("rm -f \"$fichier\"") == 2, (
        "un dump illisible ou rate doit etre ecarte, jamais garde")
    # Et la rotation, par base.
    assert "SAUVEGARDES_GARDEES" in bloc




def test_la_procedure_d_incident_ne_cite_que_ce_qui_existe():
    """Vecu : « une procedure d'incident ». Une page qu'on lit le jour ou tout
    va mal ne doit pas citer un conteneur renomme, une variable disparue ou
    un lien casse : ce jour-la, on n'a pas le temps de deviner. Chaque nom
    qu'elle cite est verifie contre le compose et le code."""
    racine = os.path.join(os.path.dirname(DOSSIER_PANNEAU))
    page = open(os.path.join(racine, "INCIDENTS.md"), encoding="utf-8").read()
    compose = open(os.path.join(racine, "docker-compose.yml"), encoding="utf-8").read()
    source = _lire_source(os.path.join(DOSSIER_PANNEAU, "app", "app.py"))
    # Les conteneurs nommes dans une commande (docker logs, restart...).
    conteneurs = set(re.findall(r"(?:docker logs[^\n]*?|restart |up -d |exec -i |exec )(codelab-[a-z-]+)", page))
    assert {"codelab-app-manager"} <= conteneurs
    for conteneur in conteneurs:
        assert f"container_name: {conteneur}" in compose, conteneur
    for variable in set(re.findall(r"\bAPP_MANAGER_[A-Z0-9_]+\b", page)):
        assert variable in source or variable in compose, variable
    assert 'APP_MANAGER_ADMIN_2FA: "0"' in compose, "la marche arriere du second facteur"
    for chemin in set(re.findall(r"/DATA/AppData/codelab/([a-z-]+)", page)):
        assert f"/DATA/AppData/codelab/{chemin}" in compose or chemin == "config", chemin
    # Les liens internes visent des titres qui existent.
    def ancres(texte):
        out = set()
        for t in re.findall(r"^#+ (.+)$", texte, re.M):
            a = re.sub(r"[^\w\- ]", "", t.lower()).replace(" ", "-")
            out.add(a)
        return out
    liens = re.findall(r"\]\(([A-Za-z/._-]*)#([^)]+)\)", page)
    assert len(liens) >= 10, "les renvois de la table et des sections"
    for cible, ancre_lien in liens:
        fichier = os.path.join(racine, cible) if cible else os.path.join(racine, "INCIDENTS.md")
        assert ancre_lien in ancres(open(fichier, encoding="utf-8").read()), (cible, ancre_lien)
    # On la trouve depuis le README, et la liste de mise en production y renvoie.
    assert "(INCIDENTS.md)" in open(os.path.join(racine, "README.md"), encoding="utf-8").read()
    assert "(INCIDENTS.md)" in open(os.path.join(racine, "SECURITE.md"), encoding="utf-8").read()




def test_la_restauration_est_documentee():
    """Une sauvegarde dont personne ne sait se servir n'est pas une
    sauvegarde. La procedure doit etre ecrite, avec les commandes exactes."""
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "README.md")
    if not os.path.exists(chemin):
        pytest.skip("README absent de cette image")
    texte = open(chemin, encoding="utf-8").read()
    assert "pg_restore" in texte, "la restauration des bases n'est pas ecrite"
    assert "panneau-" in texte and "tar" in texte, (
        "la restauration de l'etat du panneau n'est pas ecrite")
    assert "/sauvegardes" in texte




# ---------- Mailpit : l'envoi de mails, garde et relaye ----------

def test_mailpit_est_dans_la_stack_ferme_et_derriere_le_proxy():
    for chemin in _composes():
        bloc = _bloc_du_service(chemin, "codelab-mailpit")
        assert bloc, f"codelab-mailpit absent de {os.path.basename(chemin)}"
        assert "ports:" not in bloc, chemin
        assert "no-new-privileges:true" in bloc and "- ALL" in bloc, chemin
        assert 'CODELAB_MAILPIT_RELAIS: "auto"' in bloc, chemin
        proxy = _bloc_du_service(chemin, "codelab-dagster-proxy")
        assert '"8025:8025"' in proxy, chemin
    racine = _racine_depot()
    ci = open(os.path.join(racine, ".github", "workflows", "build-images.yml"),
              encoding="utf-8").read()
    assert "codelab-mailpit mailpit/Dockerfile mailpit/" in ci and '"mailpit/**"' in ci
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    emplacement = conf[conf.index("location / {"):]
    # L'autorisation du panneau d'abord, puis le mot de passe interne.
    assert "auth_request /_auth;" in emplacement
    assert "include /etc/nginx/codelab/mailpit-auth.conf;" in emplacement
    dockerfile = open(os.path.join(racine, "dagster", "proxy", "Dockerfile"), encoding="utf-8").read()
    assert "echo 'return 503;' > /etc/nginx/codelab/mailpit-auth.conf" in dockerfile




def test_le_proxy_genere_le_mot_de_passe_interne_de_mailpit(tmp_path):
    import base64 as b64
    import subprocess
    (tmp_path / "config").mkdir()
    env_file = tmp_path / "config" / "credentials.env"
    env_file.write_text("# ===== codelab-postgres =====\nPOSTGRES_PASSWORD=y\n"
                        "# ===== /codelab-postgres =====\n", encoding="utf-8")
    include = tmp_path / "ng" / "mailpit-auth.conf"
    env = {"CODELAB_CONFIG_DIR": str(tmp_path / "config"),
           "CODELAB_MAILPIT_AUTH_INCLUDE": str(include), "PATH": "/usr/bin:/bin"}
    script = os.path.join(_racine_depot(), "dagster", "proxy", "entrypoint.sh")
    for _ in range(2):
        subprocess.run(["sh", script, "true"], check=True, timeout=30, env=env,
                       capture_output=True)
    texte = env_file.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=y" in texte
    assert texte.count("# ===== codelab-proxy =====") == 1
    mdp = re.search(r"^CODELAB_INTERNE_MAILPIT_UI_PASSWORD=([0-9a-f]{64})$", texte, re.M).group(1)
    attendu = b64.b64encode(f"codelab:{mdp}".encode()).decode()
    assert include.read_text() == f'proxy_set_header Authorization "Basic {attendu}";\n'
    assert stat.S_IMODE(include.stat().st_mode) == 0o600




def test_mailpit_relaie_par_le_serveur_du_panneau(tmp_path):
    blocs = (_UI + "CODELAB_INTERNE_RELAIS_HOST=smtp.gmail.com\n"
             "CODELAB_INTERNE_RELAIS_PORT=587\nCODELAB_INTERNE_RELAIS_TLS=starttls\n"
             "CODELAB_INTERNE_RELAIS_USER=moi@gmail.com\n"
             "CODELAB_INTERNE_RELAIS_PASSWORD=abcd efgh\n"
             "CODELAB_INTERNE_RELAIS_FROM=moi@gmail.com\n")
    proc, vu = _lancer_entrypoint_mailpit(tmp_path, blocs)
    assert proc.returncode == 0, proc.stderr
    assert vu["MP_UI_AUTH"] == "codelab:" + "a" * 64
    assert vu["MP_SMTP_RELAY_ALL"] == "true"
    assert vu["MP_SMTP_RELAY_HOST"] == "smtp.gmail.com" and vu["MP_SMTP_RELAY_PORT"] == "587"
    assert vu["MP_SMTP_RELAY_STARTTLS"] == "true" and "MP_SMTP_RELAY_TLS" not in vu
    assert vu["MP_SMTP_RELAY_AUTH"] == "plain"
    assert vu["MP_SMTP_RELAY_PASSWORD"] == "abcd efgh"
    assert vu["MP_SMTP_RELAY_OVERRIDE_FROM"] == "moi@gmail.com"
    # Le mot de passe du serveur d'envoi ne sort pas dans les journaux.
    assert "abcd efgh" not in proc.stdout + proc.stderr
    texte = (tmp_path / "config" / "credentials.env").read_text()
    assert "MAIL_HOST=codelab-mailpit\nMAIL_PORT=1025\n" in texte




def test_mailpit_en_ssl_et_en_mode_test(tmp_path):
    blocs = (_UI + "CODELAB_INTERNE_RELAIS_HOST=ssl0.ovh.net\n"
             "CODELAB_INTERNE_RELAIS_PORT=465\nCODELAB_INTERNE_RELAIS_TLS=ssl\n")
    _, vu = _lancer_entrypoint_mailpit(tmp_path, blocs)
    assert vu["MP_SMTP_RELAY_TLS"] == "true" and "MP_SMTP_RELAY_STARTTLS" not in vu
    assert vu["MP_SMTP_RELAY_AUTH"] == "none"
    _, vu = _lancer_entrypoint_mailpit(tmp_path, blocs, {"CODELAB_MAILPIT_RELAIS": "non"})
    assert not [c for c in vu if c.startswith("MP_SMTP_RELAY")], vu
    # Sans serveur d'envoi : tout est garde, rien n'est relaye.
    _, vu = _lancer_entrypoint_mailpit(tmp_path, _UI)
    assert not [c for c in vu if c.startswith("MP_SMTP_RELAY")], vu




def test_mailpit_refuse_de_demarrer_sans_le_mot_de_passe_interne(tmp_path):
    """Sans lui, son API serait ouverte a toutes les applications du reseau."""
    proc, vu = _lancer_entrypoint_mailpit(tmp_path, "")
    assert proc.returncode == 1
    assert vu == {}, "Mailpit a ete lance sans mot de passe"




def test_le_panneau_publie_le_relais_mais_ne_le_transmet_pas(tmp_path, monkeypatch):
    env_file = tmp_path / "credentials.env"
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(env_file))
    monkeypatch.setattr(app, "lire_smtp_personnalise", lambda: app._normalise_smtp({
        "host": "smtp.gmail.com", "port": 587, "tls": "starttls",
        "user": "moi@gmail.com", "password": "secret"}))
    assert app.publier_relais_mail()
    texte = env_file.read_text()
    assert "CODELAB_INTERNE_RELAIS_HOST=smtp.gmail.com" in texte
    assert "CODELAB_INTERNE_RELAIS_PASSWORD=secret" in texte
    assert "CODELAB_INTERNE_RELAIS_FROM=moi@gmail.com" in texte
    with open(env_file, "a") as f:
        f.write("MAIL_HOST=codelab-mailpit\nCODELAB_INTERNE_MAILPIT_UI_PASSWORD=x\n")
    transmis = app.secrets_partages()
    assert transmis.get("MAIL_HOST") == "codelab-mailpit"
    assert not [c for c in transmis if c.startswith("CODELAB_INTERNE_")], transmis
    # Et il suit un enregistrement depuis Parametres > E-mail.
    assert "publier_relais_mail()" in _source_du_panneau()




# ---------- la tuile Mailpit ----------

def test_mailpit_a_sa_tuile_dans_le_hub_et_mene_au_port_8025(hub_dagster):
    hub_dagster.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = hub_dagster.get("/api/mes-apps").get_json()
    (mailpit,) = [s for s in d["services"] if s["name"] == app.MAILPIT_NOM]
    assert mailpit["url"].endswith(":8025/"), mailpit["url"]
    assert mailpit["externe"] is True and mailpit["par_defaut"] is True
    assert mailpit["titre"] == "Mailpit"
    # La porte du panneau journalise puis redirige vers le proxy.
    r = hub_dagster.get("/mailpit/")
    assert r.status_code == 302 and r.headers["Location"].endswith(":8025/")
    # Son icone, dessinee, distincte des autres.
    icone = hub_dagster.get("/api/icon/mailpit")
    assert icone.mimetype == "image/svg+xml" and b"<text" not in icone.data
    assert icone.data != hub_dagster.get("/api/icon/dagster").data




def test_mailpit_est_reserve_a_l_administrateur(hub_dagster):
    """Son interface montre tous les mails, liens de reinitialisation compris."""
    c = hub_dagster
    _connecte(c, "marie", "un-mot-de-passe")
    assert c.get("/mailpit/").status_code in (302, 403, 404)
    assert c.get("/mailpit/").headers.get("Location", "").endswith(":8025/") is False
    d = c.get("/api/mes-apps").get_json()
    assert not [s for s in d.get("services", []) if s["name"] == app.MAILPIT_NOM]
    assert c.post("/api/mes-apps/mailpit/masquer").status_code == 404




def test_mailpit_se_range_et_ne_se_configure_pas(hub, monkeypatch):
    monkeypatch.setattr(app, "origine_mailpit", lambda: "http://x:8025/")
    c = _admin_du_hub(hub)
    assert c.post("/api/mes-apps/mailpit/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert app.MAILPIT_NOM not in [s["name"] for s in d["services"]]
    assert app.MAILPIT_NOM in [s["name"] for s in d["services_masques"]]
    assert "par défaut" in app.refus_configuration(app.MAILPIT_NOM)
    assert app.refus_action_protegee(app.MAILPIT_NOM, "arrêt") is not None




def test_la_surveillance_de_mailpit_ne_note_que_les_changements(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "_mailpit_suivi", {"debout": None})
    etats = iter([(True, 3.0), (True, 4.0), (False, 5000.0)])
    monkeypatch.setattr(app, "mailpit_sonde", lambda: next(etats))
    for _ in range(3):
        app.mailpit_tick()
    lignes = (tmp_path / "mailpit.log").read_text().splitlines()
    assert len(lignes) == 2, lignes
    assert "Mailpit répond" in lignes[0] and "ne répond pas" in lignes[1]




# ------------- Erreurs, pannes, annuler, hors ligne, sauvegardes -------------
#
# Vecu : une liste d'ameliorations relue ensemble, puis « OK » pour le premier
# lot -- toujours dire quoi faire apres une erreur, garder le message, montrer
# la cause d'une panne, pouvoir annuler, rester utilisable quand le serveur
# se tait, et signaler des sauvegardes restees sur le seul serveur.

def test_une_exception_donne_une_reference_et_jamais_la_pile(hub, monkeypatch, capsys):
    monkeypatch.setitem(app.flask_app.config, "PROPAGATE_EXCEPTIONS", False)
    def casse():
        raise RuntimeError("detail interne a ne pas montrer")
    monkeypatch.setattr(app, "lire_categories", casse)
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = admin.get("/api/categories")
    assert r.status_code == 500
    ref = r.headers["X-CodeLab-Ref"]
    assert re.fullmatch(r"[0-9A-F]{6}", ref)
    d = r.get_json()
    assert d["ref"] == ref and "detail interne" not in r.get_data(as_text=True)
    sortie = capsys.readouterr().out
    assert f"erreur {ref} : GET /api/categories" in sortie and "detail interne" in sortie




def test_toute_reponse_5xx_du_panneau_porte_une_reference(hub, monkeypatch, capsys):
    def plein(_):
        raise OSError("disque plein")
    monkeypatch.setattr(app, "ecrire_categories", plein)
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = admin.put("/api/categories", json={"categories": ["Outils"]})
    assert r.status_code == 500 and r.headers.get("X-CodeLab-Ref")
    assert f"erreur {r.headers['X-CodeLab-Ref']}" in capsys.readouterr().out
    # Une reponse qui marche n'en porte pas.
    assert "X-CodeLab-Ref" not in admin.get("/api/categories").headers




def test_un_echec_dit_quoi_faire_et_donne_la_reference():
    page = _page_panneau("dashboard.html")
    aide = page.split("async function echec(r, defaut){")[1].split("\n}")[0]
    assert "(réponse" not in aide
    assert "X-CodeLab-Ref" in aide and "docker logs codelab-app-manager" in aide
    assert "donne-la à l'administrateur" in aide
    assert "Réessaie dans un instant." in aide




def test_une_erreur_reste_jusqu_a_ce_qu_on_la_ferme():
    page = _page_panneau("dashboard.html")
    notif = page.split("function notifier(texte, genre, action){")[1].split("\n}")[0]
    assert "if(!err) setTimeout(" in notif           # seuls les succes s'en vont
    assert "toast-fermer" in notif and "Fermer le message" in notif
    assert "erreurs.length>=3" in notif              # pas un mur d'erreurs
    assert "toast-action" in notif                   # le bouton « Annuler »




def test_masquer_ou_retirer_se_rattrape():
    page = _page_panneau("dashboard.html")
    masquage = page.split("async function basculerMasquage(nom, masquer){")[1].split("\n}")[0]
    assert "libelle:'Annuler', faire:()=>basculerMasquage(nom, false)" in masquage
    retirer = page.split("function hubRetirer(cle){")[1].split("\n}")[0]
    assert "basculerMasquage(e.id, false)" in retirer
    assert "faire:()=>enregistrerDisposition(elements)" in retirer




def test_la_fiche_montre_la_cause_d_une_panne():
    page = _page_panneau("dashboard.html")
    assert 'id="fa-cause"' in page and 'id="fa-cause-lignes"' in page
    cause = page.split("async function peindreCauseFiche(a){")[1].split("\n}")[0]
    for etat in ("a.crash_looping", "a.failed", "a.listening===false"):
        assert etat in cause, etat
    assert "/api/logs/" in cause and ".slice(-15)" in cause
    assert "a.externe || a.par_defaut ? ''" in cause   # rien pour un service
    assert "peindreCauseFiche(a);" in page.split("function peindreFiche(){")[1].split("\n}")[0]




def test_l_en_tete_de_la_fiche_reste_en_haut_sur_grand_ecran():
    page = _page_panneau("dashboard.html")
    regle = page.split("@media (min-width:761px){\n  #sec-app .fiche-tete{")[1].split("}")[0]
    assert "position:sticky" in regle and "top:52px" in regle




def test_un_serveur_injoignable_allume_un_bandeau_et_retente():
    page = _page_panneau("dashboard.html")
    assert 'id="hors-ligne"' in page
    enveloppe = page.split("const fetchAvecJeton = window.fetch;")[1].split("function passerHorsLigne")[0]
    assert "e.name!=='AbortError'" in enveloppe
    assert "!r.headers.get('X-CodeLab-Ref')" in enveloppe   # une panne du panneau n'est pas une coupure
    assert "horsLigneCompte=5;" in page
    essai = page.split("async function essayerConnexion(){")[1].split("\n}")[0]
    assert "fetchNu('/health'" in essai
    assert "window.addEventListener('offline', passerHorsLigne);" in page




def test_les_sauvegardes_restees_sur_le_serveur_sont_signalees(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    d = admin.get("/api/sauvegardes").get_json()
    assert [a["nom"] for a in d["archives"]][0] in ("panneau-20261011-030000.tar.gz",
                                                   "workspace-20261011-031000.tar.gz")
    assert "notes.txt" not in [a["nom"] for a in d["archives"]]
    assert d["alerte_hors_serveur"] is True and d["derniere_copie"] is None




def test_telecharger_une_sauvegarde_la_sort_du_serveur(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    r = admin.get("/api/sauvegardes/workspace-20261011-031000.tar.gz")
    assert r.status_code == 200 and r.data == b"w" * 4096
    assert "attachment" in r.headers["Content-Disposition"]
    d = admin.get("/api/sauvegardes").get_json()
    assert d["derniere_copie"] and d["alerte_hors_serveur"] is False
    assert any(e.get("genre") == "sauvegarde" and e.get("action") == "telechargee"
               for e in app.lire_acces())




def test_seuls_les_fichiers_de_sauvegarde_se_telechargent(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    for nom in ("notes.txt", "..%2Fsuivi.json", "panneau-x.tar.gz",
                "panneau-20991231-000000.tar.gz"):
        assert admin.get(f"/api/sauvegardes/{nom}").status_code == 404, nom




def test_une_copie_declaree_ailleurs_eteint_l_alerte(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    d = admin.put("/api/sauvegardes/copie", json={"externe": True}).get_json()
    assert d["copie_externe"] is True and d["alerte_hors_serveur"] is False
    d = admin.put("/api/sauvegardes/copie", json={"externe": False}).get_json()
    assert d["alerte_hors_serveur"] is True




def test_les_sauvegardes_sont_reservees_a_l_administrateur(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    marie = _connexion_marie(app.flask_app.test_client())
    assert marie.get("/api/sauvegardes").status_code in (401, 403)
    assert marie.get("/api/sauvegardes/panneau-20261011-030000.tar.gz").status_code in (401, 403)
    assert marie.put("/api/sauvegardes/copie", json={"externe": True}).status_code in (401, 403)




def test_la_securite_montre_les_sauvegardes_a_l_administrateur():
    page = _page_panneau("dashboard.html")
    securite = page.split('<div class="onglet-p" id="param-securite">')[1].split('<div class="onglet-p"')[0]
    assert 'id="zone-sauvegardes" hidden' in securite
    assert "Elles ne sont que sur ce serveur." in securite
    assert "if(estAdmin) chargerSauvegardes();" in page




def test_un_plantage_au_lancement_reste_visible_dans_la_fiche(survivants):
    """« Arretee » seul ne disait pas qu'elle avait plante : le lancement
    rate est retenu (et la fiche en montre la cause), puis oublie au premier
    demarrage reussi."""
    apps = app.load()
    apps["site"]["command"] = "echo 'ModuleNotFoundError: flask' && exit 3"
    app.save(apps)
    erreur = app.start("site")
    assert erreur and "aussitôt (code 3)" in erreur
    echec = app.load()["site"]["dernier_echec"]
    assert echec["code"] == 3 and echec["quand"] > 0
    apps = app.load()
    apps["site"]["command"] = "sleep 120"
    app.save(apps)
    assert app.start("site") is None
    assert "dernier_echec" not in app.load()["site"]




# ------------------------ La version de CodeLab ------------------------
#
# Vecu : « je ne vois pas les modifs » -- l'image n'avait pas ete retiree,
# et rien dans le panneau ne le disait. Puis, la verification aupres de
# GitHub ecrite : « annule la possibilite alors. C'est un risque a prendre
# inutile ». Le panneau affiche la version qu'il porte, et ne contacte rien.

def test_le_panneau_dit_quelle_version_tourne(hub, monkeypatch):
    monkeypatch.setattr(app, "CODELAB_VERSION", "a" * 40)
    monkeypatch.setattr(app, "CODELAB_VERSION_DATE", "2026-10-12T09:00:00+02:00")
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = admin.get("/api/version").get_json()
    assert d["court"] == "aaaaaaa" and d["dev"] is False and d["date"].startswith("2026-10-12")
    monkeypatch.setattr(app, "CODELAB_VERSION", "dev")
    d = admin.get("/api/version").get_json()
    assert d["dev"] is True and d["sha"] == "" and d["court"] == ""
    marie = _connexion_marie(app.flask_app.test_client())
    assert marie.get("/api/version").status_code in (401, 403)




def test_le_panneau_ne_contacte_jamais_github():
    """Aucun appel sortant pour la version : ni fonction qui interroge
    l'API, ni boucle de verification, ni route pour la declencher."""
    source = _page_panneau("app.py")
    assert "api.github.com" not in source
    for trace in ("def verifier_mise_a_jour", "_github(", "/api/version/verifier",
                  "APP_MANAGER_VERIFIER_MAJ"):
        assert trace not in source, trace
    page = _page_panneau("dashboard.html")
    assert "/api/version/verifier" not in page and "maj-dispo" not in page




def test_le_panneau_construit_avec_le_node_du_poste_de_developpement():
    """Vecu : le panneau prenait nodejs et npm de Debian (Node 20) quand
    codelab-dev developpe sous Node 24 -- un projet qui se construisait
    dans le poste pouvait echouer au Build. Et npm de Debian tirait pres de
    400 paquets, un second python3 compris : environ 75 Mo de plus que le
    binaire officiel."""
    racine = _racine_depot()
    docker = open(os.path.join(racine, "app-manager", "Dockerfile"), encoding="utf-8").read()
    assert "FROM node:24-trixie-slim AS node" in docker
    assert "COPY --from=node /usr/local/bin/node /usr/local/bin/node" in docker
    paquets = docker.split("RUN apt-get update")[1].split("rm -rf /var/lib/apt/lists")[0]
    for retire in ("nodejs", "npm"):
        assert retire not in paquets.split(), f"{retire} revient depuis apt"
    assert "deb.nodesource.com/setup_24.x" in _dockerfile_dev(), (
        "le poste a change de branche de Node : le panneau doit suivre")
    # Meme distribution que la base Python : meme libc, meme libstdc++.
    assert "FROM python:3.13-slim" in docker and "trixie" in docker




def test_l_image_porte_sa_version_et_la_ci_la_pose():
    racine = _racine_depot()
    docker = open(os.path.join(racine, "app-manager", "Dockerfile"), encoding="utf-8").read()
    assert "ARG CODELAB_VERSION=dev" in docker
    assert "ENV CODELAB_VERSION=${CODELAB_VERSION} CODELAB_VERSION_DATE=${CODELAB_VERSION_DATE}" in docker
    # En dernier : la version change a chaque build et ne doit pas casser le cache.
    assert docker.index("ARG CODELAB_VERSION=dev") > docker.index("COPY app-manager/app ")
    ci = open(os.path.join(racine, ".github", "workflows", "build-images.yml"), encoding="utf-8").read()
    assert "CODELAB_VERSION=${{ github.sha }}" in ci
    assert "CODELAB_VERSION_DATE=${{ steps.version.outputs.date }}" in ci




def test_les_parametres_montrent_la_version_et_comment_mettre_a_jour():
    page = _page_panneau("dashboard.html")
    # Plus d'onglet « Serveur » : la version est dans « Compte », pour
    # l'administrateur seulement.
    compte = page.split('<div class="onglet-p" id="param-compte">')[1].split('<div class="onglet-p"')[0]
    assert 'id="zone-version"' in compte and 'id="ver-carte"' in compte
    assert "$('zone-version').remove();" in page
    peint = page.split("function peindreVersion(d){")[1].split("\n}")[0]
    # Le lien des nouveautes : c'est le navigateur qui l'ouvre, sur un clic.
    assert "/compare/${esc(d.sha)}...main" in peint
    assert "COMMANDES_MAJ" in peint and "ver-copier" in peint
    assert "if(estAdmin) chargerVersion();" in page and "if(estAdmin) chargerCategories();" in page


# ===========================================================================
# LES MESSAGES
# la messagerie : le relais du hub, l'application Messages
# (workspace/messages) et sa boite, ses taches et ses projets, la reprise des
# anciennes donnees ; les notifications Web Push.
# ===========================================================================

# ---------- 12 sexies. un mot laisse depuis le hub ----------
#
# Trois sujets et pas un de plus : une application a laquelle on a acces, le
# hub lui-meme, ou l'idee d'une application qui manque. Un champ libre aurait
# demande a l'administrateur de deviner de quoi on parle -- ce qu'il ne peut
# pas faire depuis un mail de trois lignes.
#
# Le hub du panneau relaie ; l'application Messages (workspace/messages)
# range. Ces tests passent par les deux, comme dans l'installation
# (fixture appli_messages).


def _ranges():
    return _application_messages().donnees.lire_messages()


def test_un_message_est_range_puis_envoye_a_l_administrateur(messagerie):
    c, partis = messagerie
    r = c.post("/api/messages", json={"cible": "application", "app": "compta",
                                      "texte": "Le bouton demande deux clics."})
    assert r.status_code == 200, r.data
    assert r.get_json()["envoye"] is True

    garde = _ranges()
    assert len(garde) == 1
    assert garde[0]["qui"] == "marie" and garde[0]["app"] == "compta"
    assert garde[0]["texte"] == "Le bouton demande deux clics."

    destinataires, sujet, corps = partis[-1]
    assert destinataires == ["chef@example.com"]
    assert "marie" in sujet and "compta" in sujet
    assert "Le bouton demande deux clics." in corps


def test_un_message_survit_a_un_envoi_impossible(messagerie, monkeypatch):
    """ENREGISTRE D'ABORD, ENVOYE ENSUITE. Un SMTP mal configure ne doit pas
    faire perdre le message : personne ne saurait jamais qu'il a ete ecrit."""
    c, _partis = messagerie
    def refuse(*a, **kw):
        raise RuntimeError("serveur injoignable")
    monkeypatch.setattr(app, "envoyer_mail", refuse)
    r = c.post("/api/messages", json={"cible": "hub", "texte": "Le hub est lent."})
    assert r.status_code == 200, r.data
    assert r.get_json()["envoye"] is False
    assert len(_ranges()) == 1


def test_une_messagerie_arretee_refuse_sans_rien_perdre(messagerie, monkeypatch):
    """L'application Messages redemarre, ou est tombee : le hub le dit, ne
    previent personne d'un message qui n'est range nulle part, et laisse
    reessayer tout de suite (le delai d'une minute ne compte pas)."""
    c, partis = messagerie
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    monkeypatch.setattr(app, "_vers_messagerie", tombee)
    r = c.post("/api/messages", json={"cible": "hub", "texte": "Le hub est lent."})
    assert r.status_code == 503 and "réessaie" in r.get_json()["error"]
    assert partis == [] and app._dernier_message == {}
    assert c.get("/api/mes-messages").status_code == 503


def test_on_n_ecrit_que_sur_une_application_qu_on_peut_ouvrir(messagerie):
    """Sinon ce formulaire dirait l'existence de projets qu'on n'a pas le
    droit de connaitre -- exactement comme la liste des masquees."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "application", "app": "secret",
                                         "texte": "bonjour"}).status_code == 404
    assert c.post("/api/messages", json={"cible": "application", "app": "fantome",
                                         "texte": "bonjour"}).status_code == 404
    assert _ranges() == [] and partis == []


def test_une_idee_d_application_ne_porte_aucun_projet(messagerie):
    """« Une application qui manque » n'en designe aucune : garder un nom
    envoye au passage ferait croire a un message sur un projet existant."""
    c, _ = messagerie
    r = c.post("/api/messages", json={"cible": "idee", "app": "compta",
                                      "texte": "Il manque un suivi des congés."})
    assert r.status_code == 200, r.data
    assert _ranges()[0]["app"] == ""


def test_un_sujet_invente_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "autre chose",
                                         "texte": "bonjour"}).status_code == 400
    assert _ranges() == []


def test_un_message_vide_ou_trop_long_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "   "}).status_code == 400
    trop = "a" * (_application_messages().donnees.MESSAGE_LONGUEUR_MAX + 1)
    assert c.post("/api/messages", json={"cible": "hub", "texte": trop}).status_code == 400
    assert _ranges() == []
    # Refuse par l'application : le delai d'une minute n'a pas commence.
    assert c.post("/api/messages", json={"cible": "hub", "texte": "bonjour"}).status_code == 200


def test_un_deuxieme_message_immediat_est_refuse(messagerie):
    """De quoi corriger une faute de frappe, pas de quoi remplir la boite de
    l'administrateur."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "un"}).status_code == 200
    r = c.post("/api/messages", json={"cible": "hub", "texte": "deux"})
    assert r.status_code == 429, r.data
    assert len(_ranges()) == 1 and len(partis) == 1


def test_seul_l_administrateur_lit_les_messages(messagerie, appli_messages):
    """Ce sont des mots qui lui sont adresses, et ils portent le nom de qui
    les a ecrits. Le panneau ne les rend a personne ; l'application ne les
    rend qu'au jeton de l'administrateur."""
    c, _ = messagerie
    c.post("/api/messages", json={"cible": "hub", "texte": "bonjour"})
    assert c.get("/api/messages").status_code in (404, 405)
    marie = ClientMessages(appli_messages.module, "marie", "utilisateur")
    assert marie.get("/api/messages").status_code == 403
    d = appli_messages.get("/api/messages").get_json()
    assert [m["texte"] for m in d["messages"]] == ["bonjour"]


def test_les_messages_vivent_dans_la_base_de_l_application():
    """Vecu : « L'application Messages doit etre une application a part
    entiere, exactement comme Diagnostic ». Sa base est la sienne, dans
    Postgres (une base, un compte) ; le panneau n'en garde plus de copie, et
    retire le miroir qu'il en tenait."""
    src = _page_panneau("app.py")
    assert 'cx.execute("DROP TABLE IF EXISTS messages")' in src
    assert "_pg_ecrire_messages" not in src and "def lire_messages(" not in src
    donnees = open(os.path.join(DOSSIER_MESSAGES, "donnees.py"), encoding="utf-8").read()
    assert "psycopg.connect(" in donnees and 'os.environ.get("CODELAB_DB")' in donnees
    assert open(os.path.join(DOSSIER_MESSAGES, ".env"), encoding="utf-8").read().count(
        "CODELAB_DB=messages") == 1


# ---------- 35. les messages ont leur boite ----------
#
# Demande : « Ajoute une rubrique pour les messages. La vue d'ensemble doit
# recenser les messages lies au hub par defaut, et je dois pouvoir filtrer sur
# une autre application. Ajoute la possibilite de supprimer ou retenir les
# messages, mettre un scroll lorsqu'il y en a trop. »


def test_retenir_un_message_le_garde_en_tete(boite):
    """On retient ce qu'on veut traiter : le laisser descendre avec le temps,
    c'est le perdre."""
    c, _ = boite
    vieux = _message(c, "hub", "à traiter", quand=int(time.time()) - 3600)
    _message(c, "hub", "tout frais")

    assert c.put("/api/messages/" + vieux, json={"retenu": True}).status_code == 200
    liste = c.get("/api/messages").get_json()["messages"]
    assert liste[0]["id"] == vieux, "le message retenu n'est pas remonte"
    assert liste[0]["retenu"] is True

    # Et relacher le remet a sa place, par sa date.
    assert c.put("/api/messages/" + vieux, json={"retenu": False}).status_code == 200
    liste = c.get("/api/messages").get_json()["messages"]
    assert liste[0]["id"] != vieux
    assert liste[0]["retenu"] is False


def test_supprimer_un_message_le_retire_de_la_base(boite, capsys):
    c, _ = boite
    garde = _message(c, "hub", "on garde")
    parti = _message(c, "idee", "on jette")

    assert c.delete("/api/messages/" + parti).status_code == 200
    restants = [m["id"] for m in c.get("/api/messages").get_json()["messages"]]
    assert restants == [garde]
    # Dans la base aussi, pas seulement dans la reponse : un message qui
    # revient au redemarrage n'a pas ete supprime.
    with c.donnees.base() as b:
        ids = [l["id"] for l in b.lignes("SELECT id FROM messages")]
    assert parti not in ids and garde in ids
    # Et c'est trace dans le journal de l'application : supprimer un mot qui
    # vous est adresse se retrouve.
    assert f"message {parti} supprime" in capsys.readouterr().out


def test_le_guet_ne_redemande_que_ce_qui_est_arrive_depuis(boite):
    """La page interroge toutes les vingt secondes : lui renvoyer trois cents
    messages pour en decouvrir un serait payer cher une notification. Le
    panneau guette de meme, pour l'annoncer dans son coin."""
    c, panneau = boite
    repere = int(time.time()) - 100
    _message(c, "hub", "avant", quand=repere - 10)
    neuf = _message(c, "hub", "après", quand=repere + 10)

    liste = c.get("/api/messages?apres=%d" % repere).get_json()["messages"]
    assert [m["id"] for m in liste] == [neuf]
    # Un « apres » illisible ne fait pas tomber la route : elle rend tout.
    assert len(c.get("/api/messages?apres=demain").get_json()["messages"]) == 2
    vus = panneau.get("/api/messages/nouveaux?apres=%d" % repere).get_json()["messages"]
    assert vus == [{"id": neuf, "ts": repere + 10, "qui": "marie"}]


def test_les_messages_restent_reserves_a_l_administrateur(boite):
    """Sans jeton, ou avec celui d'un compte : rien. Le controle tient dans
    l'application meme, pas seulement dans le proxy."""
    c, _ = boite
    identifiant = _message(c, "hub", "un mot")
    anonyme = c.module.app.test_client()
    marie = ClientMessages(c.module, "marie", "utilisateur")
    for client in (anonyme, marie):
        assert client.get("/api/messages").status_code == 403
        assert client.get("/").status_code == 403
        assert client.delete("/api/messages/" + identifiant).status_code == 403
        assert client.put("/api/messages/" + identifiant,
                          json={"retenu": True}).status_code == 403
    # Un jeton pour une AUTRE application ne vaut rien ici.
    autre = anonyme.get("/api/messages", headers={
        "X-CodeLab-Auth": app.jeton_signe("compta", "admin", "admin")})
    assert autre.status_code == 403
    # Les routes du panneau ne s'ouvrent qu'au jeton du panneau.
    assert c.post("/interne/oublier", json={"comptes": ["marie"]}).status_code == 403
    assert _message(c, "hub", "toujours la") and len(c.donnees.lire_messages()) == 2


def test_une_page_d_un_autre_site_ne_fait_pas_agir_l_administrateur(boite):
    """CSRF : un formulaire d'un autre site poste du texte brut, que l'API
    lirait ; il ne sait pas poser l'en-tete X-Messages sans la permission du
    serveur, qui ne la donne pas."""
    c, _ = boite
    ident = _message(c, "hub", "un mot")
    jeton = {"X-CodeLab-Auth": app.jeton_signe(app.MESSAGERIE_NOM, "admin", "admin")}
    nu = c.module.app.test_client()
    r = nu.delete("/api/messages/" + ident, headers=jeton)
    assert r.status_code == 403
    r = nu.post("/api/messages/lus", headers=jeton, data='{"x":1}', content_type="text/plain")
    assert r.status_code == 403
    assert nu.get("/api/messages", headers=jeton).status_code == 200
    page = open(os.path.join(DOSSIER_MESSAGES, "index.html"), encoding="utf-8").read()
    assert "h.set('X-Messages', '1');" in page


def test_un_formulaire_d_une_application_voisine_n_ecrit_pas_au_nom_du_compte(
        messagerie, appli_messages, monkeypatch):
    """CSRF par /compte/ (audit du 2026-11-11, R1). Le proxy y laisse passer
    l'administrateur, et SameSite=Lax laisse passer un formulaire du meme
    site servi sur un autre port (Dagster, Mailpit, un serveur de
    developpement). Sans X-Messages, rien ne
    s'ecrit ; le panneau, lui, pose l'en-tete sur ce qu'il relaie du hub."""
    c, _partis = messagerie
    jeton = {"X-CodeLab-Auth": app.jeton_signe(app.MESSAGERIE_NOM, "admin", "admin")}
    nu = appli_messages.module.app.test_client()
    r = nu.post("/compte/messages", headers=jeton, content_type="text/plain",
                data='{"cible": "hub", "texte": "pose par un formulaire"}')
    assert r.status_code == 403
    r = nu.post("/compte/pieces", headers={**jeton, "X-Nom-Fichier": "capture.png"},
                data=b"\x89PNG\r\n\x1a\n")
    assert r.status_code == 403
    assert _ranges() == []
    # La lecture reste ouverte : un formulaire ne lit pas la reponse.
    assert nu.get("/compte/messages", headers=jeton).status_code == 200

    vus = []
    transport = app._vers_messagerie

    def espion(methode, chemin, entetes, corps):
        vus.append((chemin, entetes.get("X-Messages")))
        return transport(methode, chemin, entetes, corps)
    monkeypatch.setattr(app, "_vers_messagerie", espion)
    r = c.post("/api/messages", json={"cible": "hub", "texte": "Le hub écrit toujours."})
    assert r.status_code == 200, r.data
    assert ("/compte/messages", "1") in vus
    assert [m["texte"] for m in _ranges()] == ["Le hub écrit toujours."]


def test_un_message_inconnu_ne_fait_pas_semblant(boite):
    c, _ = boite
    assert c.delete("/api/messages/jamais-vu").status_code == 404
    assert c.put("/api/messages/jamais-vu", json={"retenu": True}).status_code == 404


def _page_messages():
    return _page_panneau(PAGE_MESSAGES)


def test_l_application_messages_range_et_confirme():
    """La boite de reception : trier, ranger, regrouper ; et rien ne se
    supprime sans confirmation."""
    page = _page_messages()
    for trace in ("'api/messages/ordre'", "'api/messages/grouper'", 'data-agir="grouper"',
                  'data-changer="message-projet"', 'data-agir="message-archiver"',
                  'data-agir="message-tache"', "function suggestions(", 'data-triable'):
        assert trace in page, trace
    for action in ("'message-supprimer'", "'tache-supprimer'"):
        corps = page.split(action)[1].split("\n  },")[0]
        assert "demander(" in corps, action + " supprime sans confirmation"
    corps = page.split("async function projetSupprimer(")[1].split("\n}")[0]
    assert "demander(" in corps
    # Toutes les adresses sont relatives : la page vit sous /messages/ (port
    # des applications), dont le proxy retire le prefixe.
    assert "'/api/" not in page and "`/api/" not in page and "/mode.js" not in page


def test_un_message_recu_se_signale_dans_le_coin():
    """« Ajoute des notifications en bas a droite des qu'un message est
    recu. » La page guette, le dit la ou elle dit tout le reste, et mene a
    l'application Messages."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("async function guetterMessages(){")[1].split("\n}")[0]
    assert "/api/messages/nouveaux?apres=" in bloc, "le guet retelecharge toute la boite"
    assert "notifier" in bloc
    assert "location.href=lienOuverture({name:'messages'})" in page.split("function notifierMessage(")[1].split("\n}")[0]
    # surveiller() et non setInterval() : les minuteries s'arretent quand
    # l'onglet passe en arriere-plan.
    assert "surveiller(guetterMessages" in page
    # Et les notifications paraissent bien en bas a droite.
    style = page.split(".toasts{")[1].split("}")[0]
    assert "bottom" in style and "right" in style, style


# ---------- messages : etats et taches ----------

def test_accepter_refuser_et_mettre_de_cote_un_message(boite):
    """La route ne lisait que « retenu » : accepter, refuser et archiver
    etaient ignores (et l'epingle tombait)."""
    c, _ = boite
    ident = _message(c, "hub", "Une idee", retenu=True)
    assert c.put(f"/api/messages/{ident}", json={"statut": "a_reflechir"}).status_code == 200
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "a_reflechir" and m["retenu"]
    assert c.put(f"/api/messages/{ident}", json={"statut": "refuse"}).status_code == 200
    assert c.put(f"/api/messages/{ident}", json={"archive": True}).status_code == 200
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "refuse" and m["archive"] and m["retenu"]
    assert c.put(f"/api/messages/{ident}", json={"statut": "n-importe"}).status_code == 400


def _lignes(c, table):
    with c.donnees.base() as b:
        return b.valeur(f"SELECT COUNT(*) FROM {table}")


def test_un_message_accepte_devient_une_tache(boite):
    c, _ = boite
    ident = _message(c, "hub", "La page ne charge plus")
    r = c.post(f"/api/messages/{ident}/accepter",
               json={"projet": "general", "titre": "  Réparer   la page ",
                     "sous_taches": ["Lire les journaux", "", "Corriger"]})
    assert r.status_code == 200, r.data
    tache = r.get_json()["tache"]
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "accepte"
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["id"] == tache and t["titre"] == "Réparer la page" and t["message"] == ident
    assert [st["titre"] for st in t["sous_taches"]] == ["Lire les journaux", "Corriger"]
    # Cocher, ajouter, retirer.
    st = t["sous_taches"][0]["id"]
    assert c.put(f"/api/sous-taches/{st}", json={"faite": True}).status_code == 200
    assert c.post(f"/api/taches/{tache}/sous-taches", json={"titre": "Prévenir"}).status_code == 200
    assert c.put(f"/api/taches/{tache}", json={"faite": True}).status_code == 200
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["faite"] and t["sous_taches"][0]["faite"] and len(t["sous_taches"]) == 3
    # Un projet qui n'existe pas est refuse ; supprimer la tache emporte ses sous-taches.
    assert c.post("/api/taches", json={"projet": "inconnu", "titre": "x"}).status_code == 400
    assert c.delete(f"/api/taches/{tache}").status_code == 200
    assert _lignes(c, "sous_taches") == 0


def test_l_utilisateur_suit_ses_messages(boite, deux_espaces):
    """Qui a ecrit voit ce que son message devient -- lu, a l'etude,
    planifie, en cours, realise, non retenu -- deduit de ce que fait
    l'administrateur. Il ne voit que ses messages."""
    c, panneau = boite
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")

    def suivi():
        d = u.get("/api/mes-messages").get_json()
        return {m["texte"]: m for m in d["messages"]}, d["nouveaux"]

    a = _message(c, "hub", "La page ne charge plus")
    b = _message(c, "idee", "Un carnet de recettes")
    autre = _message(c, "hub", "Pas le mien", qui="paul")
    ms, nouveaux = suivi()
    assert set(ms) == {"La page ne charge plus", "Un carnet de recettes"} and nouveaux == 0
    assert ms["La page ne charge plus"]["suivi"]["etape"] == "envoye"
    # Lu, puis mis a l'etude : du nouveau a annoncer.
    assert c.put(f"/api/messages/{a}/lu").status_code == 200
    assert c.put(f"/api/messages/{b}", json={"statut": "a_reflechir"}).status_code == 200
    ms, nouveaux = suivi()
    assert ms["La page ne charge plus"]["suivi"]["etape"] == "lu"
    assert ms["Un carnet de recettes"]["suivi"]["etape"] == "etude" and nouveaux == 2
    # Vu : les pastilles s'eteignent.
    assert u.post("/api/mes-messages/vus").status_code == 200
    assert suivi()[1] == 0
    # Accepte en tache, avec une date : planifie, puis fait.
    tache = c.post(f"/api/messages/{a}/accepter", json={"projet": "general", "titre": "Réparer"}
                   ).get_json()["tache"]
    assert c.put(f"/api/taches/{tache}", json={"echeance": "2030-01-15"}).status_code == 200
    ms, nouveaux = suivi()
    s = ms["La page ne charge plus"]["suivi"]
    assert s["etape"] == "planifie" and s["prevu"] == "2030-01-15" and nouveaux == 1
    assert s["taches"] == [{"titre": "Réparer", "faite": False}] and s["total"] == 1
    assert c.put(f"/api/taches/{tache}", json={"statut": "fait"}).status_code == 200
    s = suivi()[0]["La page ne charge plus"]["suivi"]
    assert s["etape"] == "fait" and s["faites"] == 1 and s["fait_le"] and s["prevu"] is None
    # Devenu un projet : ses taches comptent ; refuse : non retenu.
    r = c.post(f"/api/messages/{b}/accepter",
               json={"nouveau_projet": {"nom": "Recettes"}, "taches": ["Maquette", "Base"]})
    s = suivi()[0]["Un carnet de recettes"]["suivi"]
    assert s["etape"] == "planifie" and s["projet"] == "Recettes" and s["total"] == 2
    assert c.put(f"/api/messages/{b}", json={"statut": "refuse"}).status_code == 200
    assert suivi()[0]["Un carnet de recettes"]["suivi"]["etape"] == "refuse"
    # L'administrateur ne voit pas les messages de marie dans SON suivi.
    panneau.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert all(m["id"] != autre for m in panneau.get("/api/mes-messages").get_json()["messages"])
    assert "suivi" in {w["id"] for w in u.get("/api/hub/disposition").get_json()["catalogue"]}
    page = _page_panneau("dashboard.html")
    for trace in ('id="suivi-liste"', "function widgetSuivi(", "/api/mes-messages/vus", 'id="side-count-suivi"'):
        assert trace in page, trace


def test_un_message_porte_des_captures_et_des_maquettes(boite, deux_espaces):
    """Images et PDF, reconnus a leurs octets ; lus par l'administrateur et
    par qui les a deposes, par personne d'autre."""
    c, _ = boite
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    r = u.post("/api/pieces", data=PNG_1PX, headers={"X-Nom-Fichier": "Capture%20%C3%A9cran.png"})
    assert r.status_code == 200, r.data
    capture = r.get_json()
    assert capture["type"] == "image/png" and capture["nom"] == "Capture écran.png"
    maquette = u.post("/api/pieces", data=b"%PDF-1.4\n...", headers={"X-Nom-Fichier": "maquette"}).get_json()
    assert maquette["nom"] == "maquette.pdf"
    # Un SVG (du code), ou un faux PNG : refuses.
    assert u.post("/api/pieces", data=b"<svg onload=alert(1)>").status_code == 415
    assert u.post("/api/pieces", data=b"pas une image", headers={"X-Nom-Fichier": "x.png"}).status_code == 415
    r = u.post("/api/messages", json={"cible": "idee", "texte": "",
                                      "pieces": [capture["id"], maquette["id"]]})
    assert r.status_code == 200, r.data
    (m,) = [x for x in c.get("/api/messages").get_json()["messages"] if x["qui"] == "marie"]
    assert [p["nom"] for p in m["pieces"]] == ["Capture écran.png", "maquette.pdf"]
    lu = c.get("/api/pieces/" + capture["id"])
    assert lu.status_code == 200 and lu.data == PNG_1PX and lu.mimetype == "image/png"
    assert lu.headers["X-Content-Type-Options"] == "nosniff"
    relaye = u.get("/api/pieces/" + capture["id"])
    assert relaye.status_code == 200 and relaye.data == PNG_1PX
    assert relaye.mimetype == "image/png" and relaye.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in relaye.headers["Content-Security-Policy"]
    # Le suivi de marie montre ses pieces.
    assert u.get("/api/mes-messages").get_json()["messages"][0]["pieces"][0]["id"] == capture["id"]
    # Un autre compte ne lit rien ; une piece deja envoyee ne se rattache pas deux fois.
    paul = ClientMessages(c.module, "paul", "utilisateur")
    assert paul.get("/compte/pieces/" + capture["id"]).status_code == 404
    app._dernier_message.clear()
    assert u.post("/api/messages", json={"cible": "hub", "texte": "x",
                                         "pieces": [capture["id"]]}).status_code == 400
    # Supprimer le message emporte ses pieces.
    assert c.delete("/api/messages/" + m["id"]).status_code == 200
    assert c.get("/api/pieces/" + capture["id"]).status_code == 404
    assert _lignes(c, "pieces") == 0


def test_un_projet_s_exporte_en_entier(boite):
    c, _ = boite
    ident = _message(c, "idee", "Un carnet de recettes")
    with c.donnees.base() as b:
        b.executer("INSERT INTO pieces (id, message, qui, nom, type, taille, ts, donnees) "
                   "VALUES ('p1', ?, 'marie', 'maquette.png', 'image/png', ?, ?, ?)",
                   (ident, len(PNG_1PX), int(time.time()), PNG_1PX))
    projet = c.post(f"/api/messages/{ident}/accepter",
                    json={"nouveau_projet": {"nom": "Recettes", "description": "Partager nos recettes."},
                          "taches": ["Maquette", "Base"]}).get_json()["projet"]
    r = c.get(f"/api/projets/{projet}/export")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "recettes-" in r.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.data))
    noms = set(z.namelist())
    assert {"projet.md", "projet.json"} <= noms
    (piece,) = [n for n in noms if n.startswith("pieces/")]
    assert z.read(piece) == PNG_1PX
    md = z.read("projet.md").decode()
    assert md.startswith("# Recettes") and "Partager nos recettes." in md
    assert "- [ ] **Maquette**" in md and "Un carnet de recettes" in md and f"]({piece})" in md
    assert json.loads(z.read("projet.json"))["nom"] == "Recettes"
    # « Tout exporter » est retire, a la demande : seul le projet s'exporte.
    assert c.get("/api/projets-export").status_code == 404
    assert c.get("/api/projets/p-inconnu/export").status_code == 404
    # Reserve a l'administrateur.
    assert c.module.app.test_client().get(f"/api/projets/{projet}/export").status_code == 403


def test_accepter_sans_tache_et_droits(boite, deux_espaces):
    c, _ = boite
    ident = _message(c, "hub", "Merci !")
    r = c.post(f"/api/messages/{ident}/accepter", json={})
    assert r.status_code == 200 and r.get_json()["tache"] is None
    assert c.get("/api/taches").get_json()["taches"] == []
    # Un compte utilisateur n'y a pas acces : ni dans l'application, ni par
    # le panneau, qui n'a plus ces routes.
    marie = ClientMessages(c.module, "marie", "utilisateur")
    assert marie.get("/api/taches").status_code == 403
    assert marie.post(f"/api/messages/{ident}/accepter", json={}).status_code == 403
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    assert u.get("/api/taches").status_code == 404


def test_l_application_messages_a_ses_vues():
    page = _page_messages()
    for trace in ("aller('projet'", "'planning'", "'feuille'", "'boite'", 'id="fen-tache"',
                  'id="fen-projet"', 'id="fen-grouper"', 'id="fen-scinder"', "history.pushState",
                  "'api/taches/plan'", "/scinder'", "function planifier(",
                  "new URLSearchParams(location.search)"):
        assert trace in page, trace
    # Pas d'etiquette d'urgence : la priorite, c'est la place dans le planning.
    scripts = page.split("<script>")[-1].split("</script>")[0]
    assert "priorite" not in scripts
    # La feuille de route : aujourd'hui, semaine par semaine, sur des mois.
    for echelle in ("'jour'", "'semaines'", "'mois'", "[1,3,6,12]"):
        assert echelle in page, echelle


# ---------- l'application « Demandes » : tableau, priorites, projets ----------

def test_les_routes_sans_client_sont_retirees(boite):
    """« pas de code inutile ou Legacy » : l'ordre d'une colonne du vieux
    tableau et l'ordre des projets n'avaient plus aucun appelant (le planning
    passe par /api/taches/plan et /api/taches/<id>/ordre). Et le panneau n'a
    plus aucune route de la boite : elles sont dans l'application."""
    c, _ = boite
    regles = {r.rule for r in app.flask_app.url_map.iter_rules()}
    assert "/api/taches/ordre" not in regles and "/api/projets/ordre" not in regles
    assert "/api/deploy/<n>" not in regles
    assert not {"api_taches_ordre", "api_projets_ordre", "api_deploy"} & set(app.flask_app.view_functions)
    assert not {r for r in regles if r.startswith(("/api/taches", "/api/projets", "/api/sous-taches"))}
    regles_app = {r.rule for r in c.module.app.url_map.iter_rules()}
    assert "/api/taches/ordre" not in regles_app and "/api/projets/ordre" not in regles_app


def test_une_tache_a_priorite_echeance_et_notes(boite):
    c, _ = boite
    r = c.post("/api/taches", json={"projet": "general", "titre": "Urgent", "priorite": 0,
                                    "echeance": "2026-12-01", "notes": "  voir le journal  "})
    assert r.status_code == 200, r.data
    ident = r.get_json()["id"]
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert (t["priorite"], t["echeance"], t["notes"]) == (0, "2026-12-01", "voir le journal")
    assert c.put(f"/api/taches/{ident}", json={"statut": "en_cours", "echeance": None}).status_code == 200
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["statut"] == "en_cours" and t["echeance"] is None and not t["faite"]
    for mauvais in ({"priorite": 9}, {"priorite": "haute"}, {"echeance": "demain"},
                    {"statut": "fini"}, {"titre": "  "}, {}):
        assert c.put(f"/api/taches/{ident}", json=mauvais).status_code == 400, mauvais
    assert c.post("/api/taches", json={"projet": "general", "titre": "x",
                                       "echeance": "31/12/2026"}).status_code == 400
    assert c.put("/api/taches/inconnue", json={"priorite": 1}).status_code == 404


def test_un_projet_se_construit_puis_se_concretise(boite):
    """Idee, construction, realise : et un projet realise peut nommer
    l'application qui le concretise -- une application declaree, rien
    d'autre. L'application Messages connait la liste par le panneau, qui la
    lui donne a chaque synchronisation."""
    c, _ = boite
    app.save({"site": {"path": "/w/site", "command": "x", "port": 8101, "titre": "Site"}})
    assert app.synchroniser_la_messagerie()
    assert c.get("/api/apps").get_json()["apps"] == [{"name": "site", "titre": "Site"}]
    r = c.post("/api/projets", json={"nom": "Agenda partagé", "description": "Un agenda",
                                     "taches": ["Maquette", "", "Base"]})
    assert r.status_code == 200, r.data
    projet = r.get_json()["id"]
    assert projet.startswith("p-")
    (p,) = c.get("/api/projets").get_json()["projets"]
    assert (p["nom"], p["etape"], p["application"]) == ("Agenda partagé", "idee", None)
    taches = c.get("/api/taches").get_json()["taches"]
    assert [t["titre"] for t in taches] == ["Maquette", "Base"]
    assert {t["projet"] for t in taches} == {projet}
    assert c.post("/api/taches", json={"projet": projet, "titre": "Tests"}).status_code == 200
    assert c.post("/api/taches", json={"projet": "site", "titre": "Logo"}).status_code == 200
    assert c.put(f"/api/projets/{projet}", json={"etape": "construction"}).status_code == 200
    assert c.put(f"/api/projets/{projet}", json={"etape": "realise", "application": "site"}).status_code == 200
    (p,) = c.get("/api/projets").get_json()["projets"]
    assert (p["etape"], p["application"]) == ("realise", "site")
    assert c.put(f"/api/projets/{projet}", json={"application": "inconnue"}).status_code == 400
    assert c.put(f"/api/projets/{projet}", json={"etape": "fini"}).status_code == 400
    assert c.put(f"/api/projets/{projet}", json={"application": ""}).status_code == 200
    assert c.get("/api/projets").get_json()["projets"][0]["application"] is None
    # Supprimer le projet ne perd aucune tache : elles passent dans « general ».
    assert c.delete(f"/api/projets/{projet}").status_code == 200
    assert c.get("/api/projets").get_json()["projets"] == []
    assert {t["projet"] for t in c.get("/api/taches").get_json()["taches"]} == {"general", "site"}
    assert c.delete(f"/api/projets/{projet}").status_code == 404
    assert c.post("/api/taches", json={"projet": projet, "titre": "x"}).status_code == 400


def test_une_demande_acceptee_devient_un_projet(boite):
    c, _ = boite
    ident = _message(c, "idee", "Une appli pour les réservations de salle")
    r = c.post(f"/api/messages/{ident}/accepter",
               json={"nouveau_projet": {"nom": "Réservations", "description": "Salles"},
                     "taches": ["Recenser les salles"]})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["tache"] is None and d["projet"].startswith("p-")
    (p,) = c.get("/api/projets").get_json()["projets"]
    assert p["id"] == d["projet"] and p["message"] == ident and p["etape"] == "idee"
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["projet"] == p["id"] and t["titre"] == "Recenser les salles"
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "accepte"
    # Un projet sans nom ne se cree pas, et la demande reste a traiter.
    autre = _message(c, "idee", "Autre")
    assert c.post(f"/api/messages/{autre}/accepter",
                  json={"nouveau_projet": {"nom": " "}}).status_code == 400
    assert next(x for x in c.donnees.lire_messages() if x["id"] == autre)["statut"] == "en_attente"
    # Accepter en tache garde la priorite choisie.
    r = c.post(f"/api/messages/{autre}/accepter", json={"titre": "Vite", "projet": p["id"], "priorite": 1})
    assert r.status_code == 200
    t = next(x for x in c.get("/api/taches").get_json()["taches"] if x["id"] == r.get_json()["tache"])
    assert t["priorite"] == 1 and t["projet"] == p["id"]


def test_les_messages_se_rangent_et_se_regroupent(boite):
    """Ranger la boite a sa guise, rattacher un message a un projet,
    regrouper ceux qui parlent de la meme chose -- dans un projet neuf ou
    existant. Supprimer le projet rend ses messages « a classer »."""
    c, _ = boite
    a = _message(c, "idee", "Un agenda partage")
    b = _message(c, "idee", "Agenda commun pour les salles")
    h = _message(c, "hub", "Le hub est lent")
    assert c.put("/api/messages/ordre", json={"ordre": [h, a, b]}).status_code == 200
    rangs = {m["id"]: m.get("ordre") for m in c.get("/api/messages").get_json()["messages"]}
    assert (rangs[h], rangs[a], rangs[b]) == (0, 1, 2)
    assert c.put("/api/messages/ordre", json={"ordre": [h, "x"]}).status_code == 409
    assert c.put("/api/messages/ordre", json={"ordre": [h, h]}).status_code == 400
    r = c.post("/api/messages/grouper", json={"ids": [a, b], "nouveau_projet": {"nom": "Agenda"}})
    assert r.status_code == 200, r.data
    projet = r.get_json()["projet"]
    lus = {m["id"]: m for m in c.get("/api/messages").get_json()["messages"]}
    assert lus[a]["projet"] == lus[b]["projet"] == projet and lus[a]["lu"]
    assert c.post("/api/messages/grouper", json={"ids": [h], "projet": projet}).status_code == 200
    assert c.post("/api/messages/grouper", json={"ids": [h], "projet": "p-inconnu"}).status_code == 400
    assert c.post("/api/messages/grouper", json={"ids": [], "projet": projet}).status_code == 400
    assert c.post("/api/messages/grouper", json={"ids": ["x"], "projet": projet}).status_code == 404
    # Rattacher et detacher un seul message.
    assert c.put(f"/api/messages/{h}", json={"projet": ""}).status_code == 200
    assert {m["id"]: m for m in c.get("/api/messages").get_json()["messages"]}[h]["projet"] is None
    assert c.put(f"/api/messages/{h}", json={"projet": "general"}).status_code == 400
    assert c.put(f"/api/messages/{h}", json={"projet": projet}).status_code == 200
    assert c.delete(f"/api/projets/{projet}").status_code == 200
    assert not any(m.get("projet") for m in c.get("/api/messages").get_json()["messages"])


def test_le_planning_s_ordonne_et_une_tache_se_scinde(boite):
    """La priorite est une PLACE : le planning s'ordonne en entier, sans
    toucher aux statuts. Une tache a une duree, une date facultative, et se
    scinde en plusieurs, qui la suivent."""
    c, _ = boite
    m = _message(c, "hub", "Le hub est lent")
    ids = [c.post("/api/taches", json={"projet": "general", "titre": t, "duree": d}).get_json()["id"]
           for t, d in (("a", 1), ("b", 4), ("c", 2))]
    a, b, cc = ids
    assert c.put(f"/api/taches/{a}", json={"statut": "en_cours"}).status_code == 200
    assert c.put("/api/taches/plan", json={"ordre": [cc, a, b]}).status_code == 200
    taches = sorted(c.get("/api/taches").get_json()["taches"], key=lambda t: t["ordre"])
    assert [t["titre"] for t in taches] == ["c", "a", "b"]
    assert {t["titre"]: t["statut"] for t in taches}["a"] == "en_cours"
    assert c.put("/api/taches/plan", json={"ordre": ["x"]}).status_code == 409
    for mauvais in ({"duree": 0}, {"duree": 61}, {"duree": "long"}):
        assert c.put(f"/api/taches/{a}", json=mauvais).status_code == 400, mauvais
    # Une tache nee d'un message ; un message inconnu est refuse.
    r = c.post("/api/taches", json={"projet": "general", "titre": "depuis", "message": m})
    assert r.status_code == 200
    assert c.post("/api/taches", json={"projet": "general", "titre": "x", "message": "inconnu"}).status_code == 404
    # Cochee, une tache date sa fin ; rouverte, elle l'oublie.
    assert c.put(f"/api/taches/{a}", json={"statut": "fait"}).status_code == 200
    assert {t["id"]: t for t in c.get("/api/taches").get_json()["taches"]}[a]["fait_le"]
    assert c.put(f"/api/taches/{a}", json={"statut": "a_faire"}).status_code == 200
    assert {t["id"]: t for t in c.get("/api/taches").get_json()["taches"]}[a]["fait_le"] is None
    # Scinder « b » (4 jours) en deux : la duree se partage, la suite vient juste apres.
    r = c.post(f"/api/taches/{b}/scinder", json={"titres": ["b1", "b2"]})
    assert r.status_code == 200, r.data
    taches = sorted(c.get("/api/taches").get_json()["taches"], key=lambda t: t["ordre"])
    titres = [t["titre"] for t in taches]
    assert titres.index("b2") == titres.index("b1") + 1
    assert {t["titre"]: t["duree"] for t in taches}["b1"] == 2
    assert c.post(f"/api/taches/{b}/scinder", json={"titres": ["seul"]}).status_code == 400
    assert c.post("/api/taches/inconnue/scinder", json={"titres": ["x", "y"]}).status_code == 404


def test_messages_est_une_application_comme_le_diagnostic(boite, tmp_path, monkeypatch):
    """Vecu : « L'application Messages doit etre une application a part
    entiere, exactement comme Diagnostic ». Un dossier du workspace, inscrit
    par le panneau a chaque demarrage, servi par le proxy sur le port des
    applications : le ruban « ← CodeLab » est donc celui de toutes les
    applications, pose par le proxy, et rien dans la page ne le recopie."""
    c, panneau = boite
    racine = tmp_path / "workspace"
    (racine / "messages").mkdir(parents=True)
    (racine / "messages" / "app.py").write_text("")
    monkeypatch.setattr(app, "ROOT", str(racine))
    assert app.amorcer_projet(app.MESSAGERIE_NOM) == app.MESSAGERIE_NOM
    inscrite = app.load()[app.MESSAGERIE_NOM]
    assert inscrite["path"] == str(racine / "messages")
    assert inscrite["command"] == app.COMMANDE_PAR_DEFAUT
    assert inscrite["build_command"] == app.BUILD_PAR_DEFAUT
    assert inscrite["visibility"] == app.VISIBILITE_PRIVEE
    assert app.est_app_par_defaut(app.MESSAGERIE_NOM)
    assert app.MESSAGERIE_NOM in app.APPLICATIONS_PROTEGEES
    assert app.MESSAGERIE_NOM not in app.SERVICES_MASQUABLES
    # Ni arretee, ni supprimee, ni configuree a la main.
    assert panneau.post(f"/api/toggle/{app.MESSAGERIE_NOM}").status_code in (400, 403)
    page = _page_messages()
    assert "← Hub" not in page and 'id="codelab-ruban"' not in page
    assert "calc(72px + var(--codelab-bord-bas, 0px))" in page
    assert "env(safe-area-inset-bottom" not in page
    assert os.path.isfile(os.path.join(DOSSIER_MESSAGES, "icon.svg"))


def test_l_application_messages_est_reservee_a_l_administrateur(boite, deux_espaces):
    """Dans le panneau, /messages mene l'administrateur a l'application (sur
    le port des applications) ; un compte utilisateur y a SA rubrique, et
    jamais la boite ; un anonyme va a la connexion."""
    c, panneau = boite
    panneau.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = panneau.get("/messages")
    assert r.status_code == 302 and r.headers["Location"] == app_adresse_messages()
    assert c.get("/").status_code == 200 and "function peindrePlanning(" in c.get("/").get_data(as_text=True)
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    r = u.get("/messages")
    assert r.status_code == 200
    page_u = r.get_data(as_text=True)
    assert '<div id="sec-message" class="section"' in page_u
    assert "function peindrePlanning(" not in page_u
    with app.flask_app.test_request_context("/"):
        from flask import session
        session["authed"], session["role"], session["utilisateur"] = True, "utilisateur", "marie"
        assert not app.peut_voir(app.MESSAGERIE_NOM)
    assert app.flask_app.test_client().get("/messages").status_code == 302
    for nom in ("messages", "demandes"):
        assert nom in app.PAGES_DU_PANNEAU and nom in app.adresses_reservees()


def app_adresse_messages():
    with app.flask_app.test_request_context("/"):
        return app.adresse_application(app.MESSAGERIE_NOM)


def test_la_tuile_messages_compte_les_non_lus(boite, monkeypatch):
    """La tuile de l'application, dans le hub, dit combien de demandes
    attendent ; le compte se lit dans l'application, garde vingt secondes."""
    c, panneau = boite
    app.save({app.MESSAGERIE_NOM: {"titre": "Messages", "path": "/w/messages", "command": "python3 app.py",
                                   "port": 9120, "enabled": True, "visibility": "privee"}})
    monkeypatch.setattr(app, "is_running", lambda n: True)
    _message(c, "hub", "Une")
    _message(c, "hub", "Deux")
    tuiles = panneau.get("/api/mes-apps").get_json()["apps"]
    tuile = next(a for a in tuiles if a["name"] == app.MESSAGERIE_NOM)
    assert tuile["non_lues"] == 2 and tuile["titre"] == "Messages"
    assert tuile["description"] == "2 nouveaux messages à lire."
    assert c.post("/api/messages/lus").status_code == 200
    app._messagerie["ts"] = 0
    tuiles = panneau.get("/api/mes-apps").get_json()["apps"]
    assert next(a for a in tuiles if a["name"] == app.MESSAGERIE_NOM)["non_lues"] == 0
    # Ce n'est plus un service du panneau.
    services = panneau.get("/api/mes-apps").get_json()["services"]
    assert app.MESSAGERIE_NOM not in [s["name"] for s in services]
    page = _page_panneau("dashboard.html")
    assert "a.interne" not in page
    # L'application tombee : le hub s'affiche quand meme, sans compte.
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    monkeypatch.setattr(app, "_vers_messagerie", tombee)
    app._messagerie["ts"] = 0
    assert panneau.get("/api/mes-apps").status_code == 200


def test_les_anciennes_donnees_passent_dans_l_application(boite):
    """Une installation d'avant la separation garde ses messages, taches,
    projets et pieces dans codelab.db. Ils partent dans l'application, et ne
    sont effaces du panneau qu'une fois CHAQUE ligne arrivee ; rejouer la
    reprise ne double rien."""
    c, _ = boite
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("INSERT INTO messages (id, ts, qui, cible, app, texte, statut, lu, retenu, "
                   "archive, options) VALUES ('m1', 100, 'marie', 'hub', '', 'Ancien', "
                   "'accepte', 1, 1, NULL, ?)", (json.dumps({"projet": "p-1", "ordre": 3}),))
        cx.execute("INSERT INTO projets (id, nom, description, etape, application, message, "
                   "cree, maj, ordre) VALUES ('p-1', 'Vieux', '', 'idee', NULL, 'm1', 1, 1, 0)")
        cx.execute("INSERT INTO taches (id, projet, titre, message, cree, faite, ordre, statut, "
                   "priorite, echeance, notes, duree, fait_le) VALUES ('t1', 'p-1', 'Faire', "
                   "'m1', 1, 0, 0, 'a_faire', 2, NULL, '', 1, NULL)")
        cx.execute("INSERT INTO sous_taches (id, tache, titre, faite, ordre) "
                   "VALUES ('s1', 't1', 'Morceau', 1, 0)")
        cx.execute("INSERT INTO pieces VALUES ('p1', 'm1', 'marie', 'a.png', 'image/png', ?, 5, ?)",
                   (len(PNG_1PX), PNG_1PX))
    # L'application ne repond pas : rien n'est efface.
    vraie = app._vers_messagerie
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    app._vers_messagerie = tombee
    try:
        assert app.reprendre_la_messagerie() is False
    finally:
        app._vers_messagerie = vraie
    assert stockage_lignes("messages") == 1
    assert app.reprendre_la_messagerie() is True
    for table in ("messages", "projets", "taches", "sous_taches", "pieces"):
        assert stockage_lignes(table) == 0, table
    (m,) = c.get("/api/messages").get_json()["messages"]
    assert (m["texte"], m["statut"], m["lu"], m["retenu"], m["archive"]) == (
        "Ancien", "accepte", True, True, False)
    assert (m["projet"], m["ordre"]) == ("p-1", 3) and m["pieces"][0]["id"] == "p1"
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["sous_taches"] == [{"id": "s1", "titre": "Morceau", "faite": True}]
    assert c.get("/api/pieces/p1").data == PNG_1PX
    # Rejouer : rien de double, rien a reprendre.
    assert c.donnees.importer({"messages": [{"id": "m1", "ts": 1, "cible": "hub"}]})
    assert len(c.donnees.lire_messages()) == 1
    assert app.reprendre_la_messagerie() is True


def test_un_compte_efface_part_aussi_de_la_messagerie(boite, deux_espaces, monkeypatch):
    """Effacer un compte emporte ses messages et leurs pieces, dans
    l'application aussi ; une application qui ne repond pas l'apprend au
    tour suivant. Le travail ne de ses messages reste."""
    c, _ = boite
    _message(c, "hub", "De marie")
    _message(c, "hub", "De paul", qui="paul")
    c.post(f"/api/messages/{_message(c, 'idee', 'Projet de marie')}/accepter",
           json={"titre": "A faire", "projet": "general"})
    vraie = app._vers_messagerie
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    monkeypatch.setattr(app, "_vers_messagerie", tombee)
    app.effacer_le_compte("marie")
    assert app.reglage("comptes_a_effacer_messagerie") == ["marie"]
    assert len(c.donnees.lire_messages()) == 3
    monkeypatch.setattr(app, "_vers_messagerie", vraie)
    app.oublier_dans_la_messagerie()
    assert [m["qui"] for m in c.donnees.lire_messages()] == ["paul"]
    assert app.reglage("comptes_a_effacer_messagerie") is None
    assert len(c.get("/api/taches").get_json()["taches"]) == 1


# ------------------------ Les notifications (Web Push) ------------------------
#
# Vecu : « PWA suffit, complete-la ». Le panneau installe recoit des
# notifications -- messages, applications en panne, avancement d'un message --
# et le chiffrement est celui du standard, verifie sur le vecteur de la RFC.

def test_le_chiffrement_des_notifications_suit_la_rfc_8291():
    from cryptography.hazmat.primitives.asymmetric import ec
    d = app._de_b64url
    serveur = ec.derive_private_key(int.from_bytes(d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                    ec.SECP256R1())
    corps = app.chiffrer_push(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg", _ephemere=serveur, _sel=d("DGv6ra1nlYgDCS1FRnbzlw"))
    assert app._b64url(corps) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wE"
        "qKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")




@pytest.mark.parametrize("adresse, permise", [
    ("https://web.push.apple.com/QGuQyavXut", True),
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://wns2-par02p.notify.windows.com/w/?token=x", True),
    ("http://web.push.apple.com/x", False),              # jamais en clair
    ("https://push.apple.com.exemple.org/x", False),     # un suffixe, pas un prefixe
    ("https://127.0.0.1/x", False),
    ("https://codelab-postgres:5432/x", False),
    ("https://web.push.apple.com:8443/x", False),
    ("file:///etc/passwd", False),
    ("", False),
])
def test_le_panneau_ne_contacte_que_les_services_de_notification(adresse, permise):
    """Sans cette liste, l'adresse fournie par le navigateur ferait du panneau
    un relais vers le reseau local."""
    assert app.service_push_permis(adresse) is permise




def test_un_appareil_s_abonne_et_recoit_une_notification_chiffree(deux_espaces, envois_push):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    etat = c.get("/api/notifications").get_json()
    assert etat["disponible"] and not etat["abonne"] and len(app._de_b64url(etat["cle"])) == 65
    nav = _Navigateur()
    assert c.post("/api/notifications/abonnement", json=nav.abonnement()).status_code == 200
    assert c.get("/api/notifications").get_json()["abonne"]
    assert c.post("/api/notifications/essai").status_code == 200
    (req,) = envois_push
    assert req.full_url == "https://web.push.apple.com/abc"
    assert req.get_header("Content-encoding") == "aes128gcm" and req.get_header("Ttl") == "86400"
    jeton, cle = req.get_header("Authorization")[len("vapid t="):].split(", k=")
    assert cle == etat["cle"]
    # Le jeton VAPID est signe par la cle annoncee, pour ce service-la.
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    tete, corps, signature = jeton.split(".")
    revendique = json.loads(app._de_b64url(corps))
    assert revendique["aud"] == "https://web.push.apple.com" and revendique["sub"].startswith("https://")
    sig = app._de_b64url(signature)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), app._de_b64url(cle)).verify(
        utils.encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
        (tete + "." + corps).encode(), ec.ECDSA(hashes.SHA256()))
    # Seul l'appareil abonne lit le contenu.
    assert nav.lire(req.data)["titre"] == "CodeLab"
    # Couper : plus rien n'arrive.
    assert c.delete("/api/notifications/abonnement").status_code == 200
    assert c.post("/api/notifications/essai").status_code == 404




def test_un_abonnement_douteux_est_refuse(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    nav = _Navigateur()
    assert c.post("/api/notifications/abonnement",
                  json=nav.abonnement("http://192.168.1.10/x")).status_code == 400
    mauvais = nav.abonnement()
    mauvais["keys"]["auth"] = "court"
    assert c.post("/api/notifications/abonnement", json=mauvais).status_code == 400
    assert app.abonnements_push("marie") == []




def test_un_appareil_deconnecte_ou_desabonne_ne_recoit_plus_rien(deux_espaces, envois_push):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    c.post("/api/notifications/abonnement", json=_Navigateur().abonnement())
    # Le service dit que l'abonnement n'existe plus : il est oublie.
    envois_push.code = 410
    app.notifier_compte("marie", "t", "x")
    assert len(envois_push) == 1 and app.abonnements_push("marie") == []
    # Deconnecte depuis Securite : rien ne part, l'abonnement s'en va.
    envois_push.code = 201
    c.post("/api/notifications/abonnement", json=_Navigateur().abonnement("https://web.push.apple.com/b"))
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("DELETE FROM appareils WHERE compte = 'marie'")
    app.notifier_compte("marie", "t", "x")
    assert len(envois_push) == 1 and app.abonnements_push("marie") == []




def test_l_administrateur_est_prevenu_d_un_nouveau_message(deux_espaces, appli_messages, envois_push,
                                                           monkeypatch):
    c = deux_espaces
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    nav = _Navigateur()
    assert admin.post("/api/notifications/abonnement", json=nav.abonnement()).status_code == 200
    _connecte(c, "marie", "mot-de-passe-long")
    monkeypatch.setattr(app, "smtp_utilisable", lambda: ({}, False))
    monkeypatch.setattr(app, "_dernier_message", {})
    assert c.post("/api/messages", json={"cible": "hub", "texte": "Le hub est super"}).status_code == 200
    (req,) = envois_push
    recu = nav.lire(req.data)
    assert recu["titre"] == "Message de marie" and recu["texte"] == "Le hub est super"
    assert recu["url"] == "/messages"




def test_l_auteur_d_un_message_suit_son_avancement(deux_espaces, appli_messages, envois_push,
                                                   monkeypatch):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    nav = _Navigateur()
    c.post("/api/notifications/abonnement", json=nav.abonnement())
    monkeypatch.setattr(app, "smtp_utilisable", lambda: ({}, False))
    assert c.post("/api/messages", json={"cible": "hub", "texte": "Une idee"}).status_code == 200
    (m,) = [x for x in appli_messages.donnees.lire_messages() if x["qui"] == "marie"]
    # Le tour du moniteur ne parle qu'a une application inscrite et debout.
    apps = app.load()
    apps[app.MESSAGERIE_NOM] = {"path": "/w/messages", "command": "x", "port": 9120,
                                "enabled": True, "visibility": "privee"}
    app.save(apps)
    # Premier passage : on retient l'etat, on ne rejoue rien.
    app.messagerie_tick(maintenant=1e10)
    assert envois_push == []
    appli_messages.donnees.modifier_message(m["id"], {"statut": "refuse"})
    app.messagerie_tick(maintenant=1e10 + 120)
    (req,) = envois_push
    assert nav.lire(req.data)["titre"] == "Ton message n'est pas retenu"
    assert nav.lire(req.data)["texte"] == "Une idee"
    # Une fois annonce, plus rien tant que rien ne change.
    app.messagerie_tick(maintenant=1e10 + 240)
    assert len(envois_push) == 1


def test_le_manifeste_est_complet(hub):
    m = hub.get("/manifest.webmanifest").get_json()
    assert m["id"] == "/" and m["lang"] == "fr"
    tailles = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= tailles
    assert [r["url"] for r in m["shortcuts"]] == ["/", "/messages", "/parametres"]
    for icone in m["icons"]:
        r = hub.get(icone["src"])
        assert r.status_code == 200 and r.mimetype == "image/png", icone["src"]
        cote = int(icone["sizes"].split("x")[0])
        assert int.from_bytes(r.data[16:20], "big") == int.from_bytes(r.data[20:24], "big") == cote
    assert hub.get("/icone-1024.png").status_code == 404




def test_le_service_worker_affiche_et_ouvre_les_notifications(hub):
    code = hub.get("/sw.js").get_data(as_text=True)
    assert "self.addEventListener('push'" in code and "showNotification(" in code
    clic = code.split("self.addEventListener('notificationclick'")[1]
    # Jamais une adresse d'ailleurs.
    assert "if (u.origin === self.location.origin) cible = u;" in clic
    assert "self.clients.openWindow(cible.href)" in clic




def test_les_parametres_proposent_notifications_et_installation():
    page = _page_panneau("dashboard.html")
    for trace in ('id="zone-notifications"', 'id="notif-bascule"', 'id="zone-installer"',
                  "Notification.requestPermission()", "pushManager.subscribe({userVisibleOnly:true",
                  "navigator.serviceWorker.register('/sw.js')", "function majPastilleIcone(){",
                  "navigator.setAppBadge(n)", "invite.prompt();",
                  "<div class=\"onglet-p\" id=\"param-apparence\">"):
        assert trace in page, trace
    # La permission se demande dans le geste : rien n'est attendu avant.
    bascule = page.split("async function notifBasculer(b){")[1].split("\n}")[0]
    assert bascule.index("Notification.requestPermission()") < bascule.index("fetch(")
    mode = _page_panneau("mode.js")
    assert "window.addEventListener('beforeinstallprompt'" in mode


def test_un_compte_est_prevenu_d_une_application_qu_il_ne_connait_pas(deux_espaces, envois_push,
                                                                      monkeypatch):
    """Vecu : « un widget nouveautes ou un systeme de notifications pour
    avertir un utilisateur qu'une nouvelle application est disponible
    lorsqu'il a acces a une app qu'il ne connait pas ». Chaque acces accorde
    envoie une notification, une seule fois ; la tuile porte « nouvelle »
    tant que le compte ne l'a pas ouverte."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    nav = _Navigateur()
    c.post("/api/notifications/abonnement", json=nav.abonnement())

    def nouvelles():
        return {a["name"]: a["nouvelle"] for a in c.get("/api/mes-apps").get_json()["apps"]}
    assert nouvelles() == {"prive-autorise": True}
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    # Par la fiche de l'application.
    assert admin.put("/api/apps/prive-refuse/acces",
                     json={"utilisateurs": ["marie"]}).status_code == 200
    (req,) = envois_push
    message = nav.lire(req.data)
    assert message["titre"] == "Nouvelle application" and "prive-refuse" in message["texte"]
    # Le meme acces, redonne : rien de plus.
    admin.put("/api/apps/prive-refuse/acces", json={"utilisateurs": ["marie"]})
    assert len(envois_push) == 1
    # Par la fiche du compte.
    assert admin.put("/api/utilisateurs/marie",
                     json={"projets": ["prive-autorise", "prive-refuse", "public"]}).status_code == 200
    assert len(envois_push) == 2 and "public" in nav.lire(envois_push[1].data)["texte"]
    # L'ouvrir l'eteint.
    assert nouvelles() == {"prive-autorise": True, "prive-refuse": True, "public": True}
    monkeypatch.setattr(app, "is_running", lambda n: True)
    monkeypatch.setattr(app, "_listening", {})
    c.post("/api/mes-apps/public/ouverte")
    assert nouvelles()["public"] is False
    # L'administrateur ne voit pas « nouvelle » : il les a declarees.
    assert not any(a["nouvelle"] for a in admin.get("/api/mes-apps").get_json()["apps"])


def test_l_application_messages_tourne_sur_un_vrai_postgres(pg_reel, monkeypatch):
    """Les autres tests passent par SQLite (MESSAGES_SQLITE) ; l'installation,
    elle, range dans Postgres. Le meme SQL doit valoir pour les deux : les
    « ? » convertis, les octets en BYTEA, ON CONFLICT, la reprise."""
    pytest.importorskip("psycopg")
    module = _application_messages()
    d = module.donnees
    assert pg_reel["psql"]("-c", "CREATE DATABASE messages", base="postgres").returncode == 0
    for cle, valeur in (("POSTGRES_HOST", "127.0.0.1"), ("POSTGRES_PORT", str(pg_reel["port"])),
                        ("CODELAB_DB", "messages"), ("POSTGRES_USER", "codelab"),
                        ("POSTGRES_PASSWORD", pg_reel["cle"])):
        monkeypatch.setenv(cle, valeur)
    monkeypatch.setattr(d, "SQLITE", "")
    d.preparer()
    d.preparer()                      # rejouee, elle ne refait rien
    piece = d.deposer_piece("marie", "capture.png", PNG_1PX)
    m = d.nouveau_message("marie", "hub", "", "Bonjour", [piece["id"]])
    assert d.lire_piece(piece["id"])["donnees"] == PNG_1PX
    assert [x["texte"] for x in d.lire_messages(qui="marie")] == ["Bonjour"]
    projet = d.accepter_message(m["id"], {"nouveau_projet": {"nom": "Essai"},
                                          "taches": ["Un", "Deux"]})[1]
    octets, nom = d.exporter_projet(projet)
    assert nom.startswith("essai-") and zipfile.ZipFile(io.BytesIO(octets)).read(
        [n for n in zipfile.ZipFile(io.BytesIO(octets)).namelist() if n.startswith("pieces/")][0]
    ) == PNG_1PX
    presents = d.importer({"messages": [{"id": m["id"], "ts": 1, "cible": "hub"}]})
    assert presents["messages"] == [m["id"]] and len(presents["taches"]) == 2
    assert d.oublier_comptes(["marie"]) == 1 and d.lire_messages() == []


def test_messages_verifie_l_identite_avec_le_module_livre_aux_projets():
    """L'application verifie le jeton avec codelab_auth.py, le module que
    « codelab create » depose dans chaque projet. Une copie, et non un
    module a part : elle doit rester celle de dev/, octet pour octet
    (« codelab update » la tiendrait a jour dans un projet ordinaire)."""
    modele = os.path.join(_racine_depot(), "dev", "codelab_auth.py")
    if not os.path.exists(modele):
        pytest.skip("depot complet absent de cette image")
    copie = os.path.join(DOSSIER_MESSAGES, "codelab_auth.py")
    assert open(copie, encoding="utf-8").read() == open(modele, encoding="utf-8").read()
