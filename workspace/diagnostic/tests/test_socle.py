"""
CodeLab -- TESTS : LE SOCLE DE LA SUITE
---------------------------------------------------------------------------
ROLE        le filet qui detourne les chemins d'etat, le client de test, la
            detection de l'isolement, l'importabilite des sondes sans
            pytest, le cache du registre.
CHARGE PAR  pytest : python3 -m pytest workspace/diagnostic/tests -q
            (depuis la racine du depot), la CI, et la page /tests
            du diagnostic.
CONTRAINTES Les aides, constantes et modules viennent de commun.py ; les
            fixtures, et le filet qui detourne TOUS les chemins d'etat
            avant chaque test, de conftest.py. Une aide nouvelle va dans
            commun.py, une fixture dans conftest.py, un test dans le
            fichier de son sujet. Aucun test ne lit /var/lib/codelab ni
            /workspace reels.
"""

from commun import *  # noqa: F401,F403 -- aides, constantes, modules


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
