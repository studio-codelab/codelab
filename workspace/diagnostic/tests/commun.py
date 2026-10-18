"""
CodeLab -- TESTS : CE QUE LES TESTS PARTAGENT
---------------------------------------------------------------------------
ROLE        Les imports, les aides, les constantes et le chargement des
            sondes (checks.py) et du panneau (checks.app), partages par
            tous les fichiers test_*.py et par conftest.py. Aucun test ici.
CHARGE PAR  « from commun import * » en tete de chaque fichier de tests ;
            __all__, en fin de fichier, exporte aussi les noms en _.

La suite de regressions CodeLab -- les tests, et rien d'autre.

Ce fichier a ete detache de checks.py, qui portait les deux : les sondes que
le diagnostic execute en production, et les tests qui verifient le depot.
Les melanger avait un cout concret, pas seulement esthetique :

  - checks.py est importe par app.py et par l'asset Dagster, dans des images
    qui n'embarquent pas pytest -- il a donc fallu un faux module pytest pour
    que les decorateurs s'evaluent quand meme a l'import. Ce repli n'a plus
    lieu d'etre : les sondes n'ont plus une seule ligne de pytest.
  - 12 000 lignes dont 10 000 de tests rendaient illisible le fichier que
    deux services chargent en production.

CE QUI VIT ICI : les tests, les fixtures, et le filet qui detourne les
chemins d'etat pour qu'aucun test ne touche l'installation.

CE QUI VIT DANS checks.py : les sondes, leur classement, et la facon de les
lancer. Le diagnostic n'a besoin que de ce fichier-la.

Lancer la suite :

    python3 -m pytest workspace/diagnostic/tests -q -rs
"""

import ast


import base64


import builtins


import datetime


import enum


import importlib.util


import inspect


import hashlib


import io


import json


import zipfile


import os


from pathlib import Path


import queue


import re


import secrets


import shutil


import socket


import stat


import sys


import time


import urllib.error


import urllib.parse


import urllib.request



import pytest



# checks.py est dans le meme dossier. L'ajout explicite au chemin vaut pour
# les cas ou ce fichier est lance autrement que par pytest depuis la racine.
# Le dossier du diagnostic, au-dessus de tests/ : checks.py, son app.py.
DOSSIER_DIAGNOSTIC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DOSSIER_DIAGNOSTIC)



import checks                                                   # noqa: E402


from checks import (                                            # noqa: E402
    CLES_MOT_DE_PASSE_PG, DOSSIER_PANNEAU, Etat,
    NOM_SUITE_PANNEAU, PORT_APPS_DEFAUT, SAUVEGARDE_FRAICHE_H,
    SAUVEGARDE_VIEILLE_H, SEUIL_DISQUE, SEUIL_DISQUE_CRITIQUE,
    SEUIL_LIBRE_CRITIQUE_GO, SEUIL_LIBRE_GO, SEVERITE_MAX,
    TESTS_APPROFONDIS, THEMES, THEME_DES_SONDES, THEME_DES_TESTS,
    _hierarchiser, _hotes_panneau, _port_applications, app,
    check_applications, check_config, check_espace_disque,
    check_exposition, check_isolation, check_origine_applications,
    check_panneau_ferme, check_panneau_joignable, check_provenance,
    check_sauvegardes, check_surface_exposee, indices_par_rubrique,
    mot_de_passe_pg, noms_des_tests, pg_settings, pire, run_all, run_test,
    sondes_par_theme,
)



# Le filet remplace envoi_mail_configure ; les tests de la regle reprennent
# la vraie.
_ENVOI_MAIL_REEL = app.envoi_mail_configure





# Tous les chemins que le panneau ecrit. La liste est verifiee : si le
# panneau en gagne un et qu'on oublie de l'ajouter ici, le test qui suit
# echoue au lieu de laisser passer une ecriture reelle.
CHEMINS_ETAT = [
    "STATE_DIR",
    "LOG_DIR",
    "CHILD_HOME",
    "SHARED_CONFIG_DIR",
    "SHARED_ENV_FILE",
    "REPLI_ADMIN_PASSWORD_FILE",
    "REPLI_SECRET_KEY_FILE",
    "SSO_PRIVATE_FILE",
    "DB_FILE",
    "SAUVEGARDES_SUIVI_FILE",
]





def _faux_unshare(tmp_path, code, message=""):
    dossier = tmp_path / "_faux-bin"
    dossier.mkdir(exist_ok=True)
    outil = dossier / "unshare"
    outil.write_text("#!/bin/sh\n"
                     + (("echo '%s' >&2\n" % message) if message else "")
                     + "exit %d\n" % code)
    outil.chmod(0o755)
    return str(dossier)





CHEMINS_OUTIL_CODELAB = [
    "/usr/local/bin/codelab",
    os.path.join(os.path.dirname(os.path.dirname(DOSSIER_DIAGNOSTIC)), "dev", "codelab"),
]




def _outil_codelab():
    for chemin in CHEMINS_OUTIL_CODELAB:
        if os.path.exists(chemin):
            return chemin
    return None




def _lancer_codelab(tmp_path, args, env_sup=None):
    import subprocess
    outil = _outil_codelab()
    if outil is None:
        pytest.skip("l'outil codelab n'est pas la (image sans le conteneur dev)")

    espace = tmp_path / "ws"
    espace.mkdir(exist_ok=True)
    manuel = tmp_path / "AGENTS-source.md"
    manuel.write_text("# Manuel CodeLab\n")

    env = dict(os.environ)
    env.update({"CODELAB_WORKSPACE": str(espace),
                "CODELAB_AGENTS_SOURCE": str(manuel),
                "HOME": str(tmp_path / "home")})
    env.pop("VSCODE_IPC_HOOK_CLI", None)
    env.update(env_sup or {})
    (tmp_path / "home").mkdir(exist_ok=True)

    r = subprocess.run(["sh", outil] + args, capture_output=True, text=True,
                       env=env, timeout=120)
    return r, espace




def _faux_code(tmp_path, code=0):
    """Un faux "code" qui ecrit ce qu'on lui demande dans un fichier."""
    dossier = tmp_path / "_faux-vscode"
    dossier.mkdir(exist_ok=True)
    trace = tmp_path / "appel-code.txt"
    outil = dossier / "code"
    # Ecrit sans %-formatage : le script shell contient lui-meme des "%s"
    # (ceux de printf), et les melanger donnait un TypeError obscur.
    outil.write_text("#!/bin/sh\n"
                     'printf "%s\\n" "$*" >> ' + '"' + str(trace) + '"\n'
                     "exit " + str(code) + "\n")
    outil.chmod(0o755)
    return str(dossier), trace





_DEBUT_RECONCILIATION = "# ------------------- mise a jour des fichiers non modifies"


_FIN_RECONCILIATION = "# ----------------------- abandon des privileges"




def _racine_depot():
    return os.path.join(DOSSIER_DIAGNOSTIC,
                        os.pardir, os.pardir)




def _bloc_reconciliation():
    """Le bloc de l'entrypoint, tel quel, avec le seul chemin d'image redirige."""
    entree = os.path.join(_racine_depot(), "dagster", "entrypoint.sh")
    if not os.path.exists(entree):
        pytest.skip("depot complet absent de cette image")
    texte = open(entree, encoding="utf-8").read()
    # Un assert, pas un skip : si les reperes ont bouge, le test doit crier
    # plutot que disparaitre en silence -- c'est exactement ainsi qu'une
    # regression passe inapercue.
    assert _DEBUT_RECONCILIATION in texte, "repere de debut introuvable dans entrypoint.sh"
    assert _FIN_RECONCILIATION in texte, "repere de fin introuvable dans entrypoint.sh"
    return texte[texte.index(_DEBUT_RECONCILIATION):texte.index(_FIN_RECONCILIATION)]




def _somme_texte(contenu):
    import hashlib
    return hashlib.sha256(contenu.encode()).hexdigest()




class _Atelier:
    """Un faux /workspace, son squelette d'image, et de quoi rejouer le bloc.

    Volontairement un objet et non une fonction : plusieurs tests ont besoin
    de faire tourner DEUX demarrages de suite, ce qui est justement le coeur
    du mecanisme -- ce que le premier note, le second s'en sert.
    """

    def __init__(self, tmp_path):
        self.base = tmp_path
        self.seed = tmp_path / "seed" / "diagnostic"
        self.ws = tmp_path / "ws" / "diagnostic"
        self.seed.mkdir(parents=True)
        self.ws.mkdir(parents=True)
        (tmp_path / "ws" / ".codelab").mkdir()
        self.marqueur = tmp_path / "ws" / ".codelab" / "workspace-v1"
        self.marqueur.write_text("marqueur")
        self.empreintes = tmp_path / "ws" / ".codelab" / "empreintes"
        self.cible = self.ws / "checks.py"
        self.livre = self.seed / "checks.py"

    def noter(self, contenu):
        """Note ce contenu comme le dernier que CodeLab a ecrit pour
        checks.py : ce que ferait un demarrage precedent."""
        self.empreintes.write_text(_somme_texte(contenu) + " diagnostic/checks.py\n",
                                   encoding="utf-8")

    def demarrer(self):
        import subprocess
        env = dict(os.environ,
                   WORKSPACE_SEED=str(self.base / "seed"),
                   WORKSPACE_DIR=str(self.base / "ws"),
                   SEED_MARKER=str(self.marqueur),
                   CODELAB_GROUP="root")
        r = subprocess.run(["sh", "-c", _bloc_reconciliation()],
                           capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout + r.stderr

    def sur_disque(self):
        return (self.cible.read_text(encoding="utf-8")
                if self.cible.exists() else None)

    def note(self):
        """L'empreinte notee pour checks.py, ou None."""
        if not self.empreintes.exists():
            return None
        for ligne in self.empreintes.read_text(encoding="utf-8").splitlines():
            if ligne.endswith(" diagnostic/checks.py"):
                return ligne.split(" ")[0]
        return None




# Tout ce bloc suppose que le module du panneau a ete trouve. Dans les
# images Dagster et dev il ne l'est pas -- elles n'embarquent ni le
# panneau ni flask -- et seules les sondes du haut de ce fichier y servent.
if app is not None:
    class ClientAvecJeton(app.flask_app.test_client_class or __import__(
            "flask.testing", fromlist=["FlaskClient"]).FlaskClient):
        def open(self, *a, **kw):
            methode = (kw.get("method") or (a[1] if len(a) > 1 else "GET") or "GET").upper()
            if methode not in ("GET", "HEAD"):
                with self.session_transaction() as sess:
                    jeton = sess.get("jeton")
                if jeton:
                    entetes = dict(kw.get("headers") or {})
                    entetes.setdefault(app.JETON_ENTETE, jeton)
                    kw["headers"] = entetes
            return super().open(*a, **kw)


    app.flask_app.test_client_class = ClientAvecJeton




def _projet(tmp_path, fichiers):
    for nom, contenu in fichiers.items():
        cible = tmp_path / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu)
    return str(tmp_path)





def _amorcage(tmp_path, monkeypatch, avec_projet=True):
    etat = tmp_path / "etat"
    etat.mkdir()
    racine = tmp_path / "workspace"
    racine.mkdir()
    if avec_projet:
        (racine / "diagnostic").mkdir()
        (racine / "diagnostic" / "app.py").write_text("")
    monkeypatch.setattr(app, "ROOT", str(racine))
    app._apps_cache["signature"] = None
    return racine





def _credentials(tmp_path, monkeypatch, contenu):
    fichier = tmp_path / "credentials.env"
    fichier.write_text(contenu)
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(fichier))
    return fichier




def _etat(monkeypatch, tourne, en_boucle):
    monkeypatch.setattr(app, "is_running", lambda n: tourne)
    monkeypatch.setattr(app, "is_crash_looping", lambda n: en_boucle)




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





def _code_valide(secret):
    return app.totp_code(secret, int(time.time()) // app.TOTP_PAS)




def _connexion_marie(c):
    """Second facteur compris : ces comptes n'ouvrent jamais de session sur
    le seul mot de passe."""
    _connecte(c, "marie", "mot-de-passe-long")
    return c





def _png(octets=64):
    """Un vrai PNG minuscule : la sonde lit la signature, pas l'extension."""
    import struct
    import zlib

    def bloc(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    brut = b"".join(b"\x00" + b"\x0e\x7c\x86" * 8 for _ in range(8))
    return (b"\x89PNG\r\n\x1a\n"
            + bloc(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut)) + bloc(b"IEND", b""))




def _editer(c, **champs):
    """Le formulaire envoie TOUS ses champs, comme la page le fait : une
    requete partielle viderait ce qu'elle omet, et le test mesurerait alors
    cet effacement plutot que ce qu'il croit mesurer."""
    corps = {"path": "/w/a", "command": "python3 app.py", "max_memory_mb": 256}
    corps.update(champs)
    return c.put("/api/app/site", json=corps)




def _code_du_dernier_mail(partis):
    """Le code tel que la personne le lit dans son mail."""
    return re.search(r": (\d{6})", partis[-1][1]).group(1)





def _base_brute():
    """Tout ce que la base du panneau garde sur le disque, octet par octet :
    le fichier, et ce qui attend encore dans son journal d'ecriture (-wal).
    C'est ce que lirait quiconque copie le dossier d'etat."""
    brut = b""
    for suffixe in ("", "-wal"):
        try:
            with open(app.DB_FILE + suffixe, "rb") as f:
                brut += f.read()
        except OSError:
            pass
    return brut.decode("latin-1")




def _compte_avec_adresse(nom="marie", mdp="mot-de-passe-long", verifiee=True,
                         face_id=True):
    """face_id : une cle d'acces enregistree -- la condition, depuis l'option A,
    pour que la reinitialisation par mail soit ouverte."""
    sel = "ee" * 16
    app.ecrire_utilisateurs({nom: {
        "sel": sel, "hash": app.derive_mot_de_passe(mdp, sel),
        "projets": [], "cree": 0,
        "email": f"{nom}@example.com", "email_verifie": verifiee}})
    app.ecrire_passkeys({nom: [{"id": "CLE", "cle_publique": "AAAA", "compteur": 0,
                                "nom": "Face ID", "cree": 0, "dernier": 0}]}
                        if face_id else {})





def _semer_ouvertures(monkeypatch, tmp_path, evenements):
    """Verse des evenements dans le journal (table journal de la base)."""
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("DELETE FROM journal")
        for e in evenements:
            reste = {k: v for k, v in e.items()
                     if k not in ("id", "ts", "genre", "qui", "app", "ip")}
            cx.execute("INSERT INTO journal (id, ts, genre, qui, app, ip, details) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (e["id"], e["ts"], e["genre"], e.get("qui"), e.get("app"),
                        e.get("ip"), json.dumps(reste)))





DEHORS = {"REMOTE_ADDR": "203.0.113.7"}      # bloc de documentation, jamais prive


CHEZ_SOI = {"REMOTE_ADDR": "192.168.1.42"}




def _active_le_verrou(client):
    """Le verrou est toujours actif : plus rien a activer."""
    assert app.admin_limite_au_reseau_local() is True




def _etat_passkeys(client, **entetes):
    return client.get("/api/passkeys/etat", headers=entetes).get_json()





def _source_du_panneau():
    """app.py tel qu'il s'execute : les morceaux de serveur/ remis a la place
    de leur chargeur, dans l'ordre de MODULES_DU_SERVEUR. Ce qu'un test
    cherche dans le source du panneau (une chaine, l'ordre de deux
    fonctions, le corps d'une route) se lit donc la ou il le lisait quand
    tout tenait en un fichier."""
    dossier = os.path.join(DOSSIER_PANNEAU, "app")
    texte = open(os.path.join(dossier, "app.py"), encoding="utf-8").read()
    appel = "\n_charger_le_serveur()\n"
    assert texte.count(appel) == 1, "app.py ne charge plus ses morceaux"
    morceaux = "".join(open(os.path.join(dossier, "serveur", nom), encoding="utf-8").read()
                       for nom in app.MODULES_DU_SERVEUR)
    return texte.replace(appel, "\n" + morceaux + appel)




def _page_panneau(nom):
    """Un fichier du panneau, tel que les tests le lisent.

    dashboard.html charge son script depuis /panneau.js (les modules de
    panneau/, assembles par app.py, mis en cache) : pour
    les tests, la page est RECONSTITUEE, le script remis a la place de sa
    balise. Ce qu'un test verifie du script (presence, ordre, corps d'une
    fonction) se lit donc la ou il le lisait quand il etait en ligne.
    """
    if nom == "app.py":
        return _source_du_panneau()
    texte = open(os.path.join(DOSSIER_PANNEAU, "app", nom), encoding="utf-8").read()
    if nom == "dashboard.html":
        balise = '<script src="/panneau.js?v=__PANNEAU_VERSION__"></script>'
        assert texte.count(balise) == 1, "dashboard.html ne charge plus panneau.js"
        script = app.PANNEAU_JS     # les modules de panneau/, assembles dans l'ordre
        texte = texte.replace(balise, "<script>\n" + script + "</script>")
    return texte




def _lire_source(chemin):
    """Un fichier par son chemin ; dashboard.html reconstitue avec son script,
    app.py avec ses morceaux (voir _page_panneau).

    Un chemin « app-manager/... » se lit dans le panneau trouve par
    checks.py, pas depuis le dossier courant. Vecu : lancee par la page
    /tests du diagnostic, depuis son dossier, la suite echouait sur les trois
    tests qui l'ecrivaient ainsi (FileNotFoundError).
    """
    if chemin.startswith("app-manager/") and DOSSIER_PANNEAU:
        chemin = os.path.join(DOSSIER_PANNEAU, chemin[len("app-manager/"):])
    for nom in ("dashboard.html", "app.py"):
        if os.path.abspath(chemin) == os.path.abspath(os.path.join(DOSSIER_PANNEAU, "app", nom)):
            return _page_panneau(nom)
    return open(chemin, encoding="utf-8").read()




def _page_servie(client, chemin="/"):
    """La page du panneau telle qu'une session la recoit, script compris.

    La page ne porte plus son script : elle le charge depuis
    /panneau.js?v=<empreinte>. On verifie qu'elle reference bien la version
    courante, on le demande AVEC LA MEME SESSION, et on le remet en ligne :
    ce qu'un test cherche « dans la page servie » est alors ce que le
    navigateur execute vraiment.
    """
    page = client.get(chemin).get_data(as_text=True)
    adresse = f"/panneau.js?v={app.PANNEAU_JS_VERSION}"
    balise = f'<script src="{adresse}"></script>'
    assert balise in page, "la page servie ne charge pas la version courante du script"
    script = client.get(adresse)
    assert script.status_code == 200, script.status_code
    return page.replace(balise, "<script>\n" + script.get_data(as_text=True) + "</script>")





def _scripts_de_la_page(nom):
    """Le contenu de chaque <script> de la page, marqueurs remplaces.

    Le serveur substitue __ROLE__ et compagnie avant d'envoyer : les laisser
    tels quels donnerait une erreur de syntaxe qui n'existe pas en vrai.
    """
    page = _page_panneau(nom)
    scripts = []
    for bloc in re.findall(r"<script>(.*?)</script>", page, re.S):
        bloc = (bloc.replace("__ROLE__", '"admin"')
                    .replace("__UTILISATEUR__", '"moi"'))
        scripts.append(re.sub(r"__[A-Z_]+__", '""', bloc))
    return scripts





def _script_panneau():
    page = _page_panneau("dashboard.html")
    return page[page.index("</style>"):]





def _definitions_diagnostic():
    return open(os.path.join(DOSSIER_DIAGNOSTIC,
                             "definitions.py"), encoding="utf-8").read()





def _script_panneau_html():
    page = _page_panneau("dashboard.html")
    return page, page[page.index("</style>"):]




def _textes_visibles(html):
    """Les textes affiches d'une page : ni CSS, ni script, ni commentaire."""
    import html as _html
    corps = html
    for balise in ("style", "script"):
        corps = re.sub(r"<%s[^>]*>.*?</%s>" % (balise, balise), " ", corps, flags=re.S)
    corps = re.sub(r"<!--.*?-->", " ", corps, flags=re.S)
    return [_html.unescape(t).strip()
            for t in re.split(r"<[^>]+>", corps) if t.strip()]




# Les fautes qu'on retrouve toujours : un participe passe ou un verbe prive
# de son accent. La liste est volontairement courte et SURE -- un mot qui
# existe aussi sans accent (« ou », « a », « la ») n'y figure pas, sous peine
# de faire echouer le test sur des phrases correctes -- « autorises » en est
# un bon exemple : « les projets que tu lui autorises » est juste.
MOTS_SANS_ACCENT = [
    "verifie", "verifier", "verification", "verifications", "execute", "executee",
    "executees", "reponse", "reponses", "detail", "details", "etat", "etats",
    "deja", "apres", "etre", "meme", "memes", "tres", "ete", "creee", "creees",
    "creer", "donnee", "donnees", "annee", "arretee", "arretees", "declaree",
    "declarees", "repond", "deconnecte", "desactive", "recu", "securite",
    "systeme", "numero", "acces", "reserve", "derniere", "dernieres",
    "premiere", "supprimee", "enregistree", "demarree", "redemarre", "ajoutee",
    "retiree", "activee", "refusee", "acceptee", "envoyee", "memoire",
    "categorie", "categories", "duree", "presentation", "frequentation",
    "notee", "inseree", "depasser", "ecouter", "repeter", "identite",
    "expediteur", "interessante", "derriere", "dependent", "fermee", "revoque",
    "eteint", "ecrase", "posee", "restee", "oubliee", "prevenir", "previent",
    "reinitialiser", "disparait", "controle", "creent", "sante",
    "gerez", "visibilite", "releve", "decoche", "redemarrer",
    # Deuxieme passe, en relisant TOUS les textes visibles du panneau, de la
    # page de connexion et du diagnostic. Chacun de ces mots a ete trouve en
    # place : « Connexion chiffree », « Heberge chez toi », « Applications
    # centralisees », « Adresse jamais confirmee », « Fixe par
    # APP_MANAGER_HTTPS ».
    #
    # La liste ne retient que des mots dont AUCUNE forme francaise ne
    # s'ecrit sans accent. « marque », « cote », « regle », « nommes »,
    # « constate » en sont volontairement absents : ce sont aussi des mots
    # ou des conjugaisons parfaitement corrects sans accent, et les y mettre
    # ferait echouer le test sur des phrases justes.
    "chiffree", "chiffrees", "confirmee", "confirmees", "frequente",
    "frequentes", "centralisee", "centralisees", "copiee", "copiees",
    "accede", "heberge", "protegerait", "paraitraient", "releves",
    "severite", "deuxieme", "defaut", "reseau", "echec", "echecs",
    "probleme", "problemes", "parametre", "parametres", "requete",
    "requetes", "resultat", "resultats", "necessaire", "periode",
    "integrite", "telecharger", "demarrer", "demarrage", "reussi",
    "reussie", "echoue",
    # « Desactivee », « Desactiver » : trouves sur les pastilles d'etat, que
    # personne ne relit parce qu'elles font deux mots. « active », lui, n'y
    # est pas -- une sonde active s'ecrit sans accent.
    "desactive", "desactivee", "desactivees", "desactiver", "desactives",
]


MOTIF_SANS_ACCENT = re.compile(
    r"\b(" + "|".join(sorted(MOTS_SANS_ACCENT, key=len, reverse=True)) + r")\b", re.I)




def _phrases_du_script(page):
    """Les chaines du JavaScript qui sont des PHRASES, donc des textes lus.

    Un test qui ne regarde que le HTML rate tout ce que la page ecrit
    elle-meme -- et c'est la que la moitie de l'interface est produite. Le
    filtre est simple : au moins trois mots, et pas d'adresse ni de selecteur
    (ils contiennent presque toujours une barre, un diese ou un signe egal).

    Le signe egal ecarte un artefact du decoupage : entre deux chaines
    voisines d'une meme ligne de code, le texte qui les separe est du CODE,
    et il etait pris pour une phrase -- « + r).textContent = d.detail || »
    en est un.
    """
    phrases = []
    for js in re.findall(r"<script[^>]*>(.*?)</script>", page, re.S):
        # Les COMMENTAIRES d'abord : ils sont ecrits sans accents, c'est la
        # regle, et leurs apostrophes ressemblent a s'y meprendre a des
        # chaines de caracteres.
        js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
        js = re.sub(r"(?m)^\s*//.*$", " ", js)
        for m in re.finditer(r"'((?:[^'\\\n]|\\.){12,200})'"
                             r'|"((?:[^"\\\n]|\\.){12,200})"', js):
            t = next(g for g in m.groups() if g is not None)
            if len(t.split()) < 3:
                continue
            if re.search(r"[/<>{}#=]|\bfunction\b|\bvar\b", t):
                continue
            # MEME ARTEFACT QUE LE SIGNE EGAL, autre forme. Le texte pris
            # entre deux chaines voisines est du code, et une concatenation
            # commence ou finit par « + » : « + (c.requetes ? » en est un.
            # Aucune phrase ne commence ni ne finit par un plus.
            if t.strip().startswith("+") or t.strip().endswith("+"):
                continue
            phrases.append(t.replace("\\'", "'"))
    return phrases




# Les verbes a l'infinitif derriere « a » : « rien a taper », « A activer
# ci-dessous », « A poser une fois le TLS en place ». La preposition prend
# l'accent, le verbe avoir non -- et « avoir » ne se suit jamais directement
# d'un infinitif. Les infinitifs en -re sont listes : trop de mots courants
# finissent par ces deux lettres (« encore », « autre », « contre ») pour
# qu'une terminaison suffise.
INFINITIFS_EN_RE = ("faire", "mettre", "prendre", "ecrire", "écrire", "lire",
                    "dire", "suivre", "remettre", "reprendre", "comprendre",
                    "permettre", "croire", "vivre", "rendre", "attendre",
                    "repondre", "répondre", "perdre", "revivre")


MOTIF_A_INFINITIF = re.compile(
    r"(?<![\wÀ-ÿ'])[Aa]\s+(?:[a-zà-ÿ]+(?:er|ir)\b|(?:" +
    "|".join(INFINITIFS_EN_RE) + r")\b)")



# « ne protégé plus rien » : un participe passe la ou il faut un present.
# Sans auxiliaire, « ne ... plus » demande un verbe conjugue.
MOTIF_PARTICIPE_SANS_AUXILIAIRE = re.compile(
    r"\bne\s+[a-zà-ÿ]+é\s+(?:plus|pas|rien|jamais|guere|guère)\b")




def _messages_des_sondes():
    """Ce que les sondes AFFICHENT : leur nom et leur detail.

    Pris dans l'arbre syntaxique plutot qu'en jouant les sondes : le
    resultat ne doit pas dependre de la machine qui lance le test. Une sonde
    qui echoue ici et passe en CI montrerait des textes differents, et le
    controle des accents deviendrait une loterie.
    """
    import ast as _ast
    # checks.py, ou vivent les sondes. Vecu : depuis que les tests en ont ete
    # detaches, ce controle lisait CE fichier, qui n'a pas une sonde -- il
    # ne trouvait aucun message et passait toujours, sans rien verifier.
    src = open(checks.__file__, encoding="utf-8").read()
    messages = []
    for f in _ast.walk(_ast.parse(src)):
        if not isinstance(f, _ast.FunctionDef):
            continue
        if not (f.name.startswith("check_") or f.name.startswith("verif_")
                or f.name == "lancer_suite_du_panneau"):
            continue
        for n in _ast.walk(f):
            if (isinstance(n, _ast.Return) and isinstance(n.value, _ast.Tuple)
                    and len(n.value.elts) == 3):
                for e in n.value.elts[1:]:
                    messages += [x.value for x in _ast.walk(e)
                                 if isinstance(x, _ast.Constant)
                                 and isinstance(x.value, str)]
    assert messages, "aucun message de sonde trouve : le controle ne verifierait rien"
    return messages




def _textes_du_diagnostic():
    """Tout ce que la page du diagnostic affiche : ses fragments et son
    script. Le CSS est ecarte -- c'est du style, pas du texte."""
    import ast as _ast
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    arbre = _ast.parse(src)
    # Les DOCSTRINGS sont du commentaire : elles s'ecrivent sans accents,
    # c'est la regle du depot, et les inclure ferait echouer le test sur
    # chaque explication du fichier.
    ecartes = set()
    for n in _ast.walk(arbre):
        corps = getattr(n, "body", None)
        if (isinstance(corps, list) and corps
                and isinstance(corps[0], _ast.Expr)
                and isinstance(corps[0].value, _ast.Constant)
                and isinstance(corps[0].value.value, str)):
            ecartes.add(id(corps[0].value))
    phrases = []
    for n in arbre.body:
        if (isinstance(n, _ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], _ast.Name)
                and isinstance(n.value, _ast.Constant)
                and isinstance(n.value.value, str)):
            nom = n.targets[0].id
            if nom == "CSS":
                ecartes.add(id(n.value))
            elif nom == "SCRIPT":
                ecartes.add(id(n.value))
                # Le script : seules ses CHAINES sont du texte lu.
                phrases += _phrases_du_script(
                    "<script>" + n.value.value + "</script>")
    morceaux = []
    for n in _ast.walk(arbre):
        if isinstance(n, _ast.Constant) and isinstance(n.value, str):
            if id(n) not in ecartes:
                morceaux.append(n.value)
    textes = []
    for m in morceaux:
        # Un fragment coupe au milieu d'une balise -- « ... data-verifier=" »
        # -- laisserait passer ses ATTRIBUTS pour du texte affiche.
        m = re.sub(r"<[^>]*$", "", m)
        if "<" not in m and len(m.split()) < 2:
            continue      # un mot seul et sans balise : une cle, pas une phrase
        textes += _textes_visibles(m)
    return textes + phrases





def _noms_jamais_definis(chemin):
    # Le panneau se lit ENTIER, morceaux de serveur/ compris : un nom
    # defini dans un morceau et appele par main est bien defini.
    arbre = ast.parse(_lire_source(chemin))
    definis = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            definis.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definis.add(n.name)
        elif isinstance(n, ast.arg):
            definis.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                definis.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            definis.add(n.name)
        elif isinstance(n, ast.Global):
            definis.update(n.names)
    # __file__ et compagnie sont poses par l'interpreteur, pas par le code.
    definis.update(["__file__", "__name__", "__doc__"])
    return sorted({n.id for n in ast.walk(arbre)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                   and n.id not in definis and not hasattr(builtins, n.id)})




def checks_noms():
    return noms_des_tests()





def _composes():
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    fichiers = [os.path.join(racine, n)
                for n in ("docker-compose.yml", "docker-compose-casaos.yml")]
    presents = [f for f in fichiers if os.path.exists(f)]
    if not presents:
        pytest.skip("composes absents de cette image")
    assert len(presents) == 2, "un des deux composes manque"
    return presents




def _services_du_compose(chemin):
    """Les services et leurs reglages, lus sans PyYAML.

    L'image du diagnostic n'embarque pas PyYAML, et l'ajouter pour un test
    ferait porter une dependance a la production. Le fichier est indente de
    facon reguliere : deux espaces par service, quatre par reglage.
    """
    services, courant, dans_services = {}, None, False
    for ligne in open(chemin, encoding="utf-8"):
        # Les sections de premier niveau : seule « services: » nous
        # interesse. Sans ce garde, « codelab-ia: » declare sous
        # « networks: » passait pour un service de plus.
        if ligne[:1] not in (" ", "\n", "#", ""):
            dans_services = ligne.rstrip() == "services:"
            courant = None
            continue
        if not dans_services:
            continue
        if ligne.startswith("  ") and not ligne.startswith("   ") and ligne.rstrip().endswith(":"):
            courant = ligne.strip().rstrip(":")
            services[courant] = {}
        elif courant and ligne.startswith("    ") and not ligne.startswith("     "):
            cle, _, valeur = ligne.strip().partition(":")
            services[courant][cle] = valeur.strip()
        elif courant and ligne.startswith("          ") and not ligne.startswith("           "):
            cle, _, valeur = ligne.strip().partition(":")
            if cle in ("memory", "cpus", "pids"):
                services[courant][f"deploy_{cle}"] = valeur.strip()
    return {n: r for n, r in services.items() if n.startswith("codelab-")}




def _bloc_du_service(chemin, nom):
    """Les lignes brutes d'un service, listes imbriquees comprises."""
    lignes, dedans, dans_services = [], False, False
    for ligne in open(chemin, encoding="utf-8"):
        if ligne[:1] not in (" ", "\n", "#", ""):
            dans_services, dedans = ligne.rstrip() == "services:", False
            continue
        if not dans_services:
            continue
        if ligne.startswith("  ") and not ligne.startswith("   ") and ligne.rstrip().endswith(":"):
            dedans = ligne.strip().rstrip(":") == nom
            continue
        if dedans:
            lignes.append(ligne)
    return "".join(lignes)




def _sur_port(port):
    """L'adresse de base qui fait arriver la requete sur ce port.

    base_url et pas environ_base : Werkzeug reconstruit SERVER_PORT et
    HTTP_HOST a partir de l'URL, et ecrase ce qu'on aurait pose a la main --
    les premiers tests ecrits ainsi mesuraient le port par defaut sans le
    dire.
    """
    return {"base_url": "http://serveur:%d" % port}





def _fichier_du_depot(*morceaux):
    """Un fichier du depot, ou None s'il n'est pas dans cette image.

    Les sondes tournent aussi dans le conteneur du diagnostic, qui ne
    contient pas le depot : un test qui suppose le contraire echouerait la
    ou il n'a rien a dire.
    """
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, *morceaux)
    return chemin if os.path.exists(chemin) else None




# Vecu : « il faut utiliser le nom de l'app simplement comme sous domaine,
# celui parametre dans la configuration, comme ca pas la peine de renseigner
# quoi que ce soit dans APP_MANAGER_APPS_URL ».

_PAR_LE_VPS = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.5"}




def _ouvrir_traversee(chemins):
    for c in chemins:
        try:
            os.chmod(c, os.stat(c).st_mode | 0o011)
        except OSError:
            pass





def _faux_disque(monkeypatch, pourcent, libre_go):
    total = 100 * 1024 ** 3
    libre = int(libre_go * 1024 ** 3)
    # Le pourcentage occupe se calcule sur (total - libre) : on choisit donc
    # le total pour que les deux chiffres soient coherents entre eux.
    total = int(libre / (1 - pourcent / 100))

    class _St:
        f_blocks = total // 4096
        f_bsize = 4096
        f_frsize = 4096
        f_bavail = libre // 4096

    monkeypatch.setattr(os, "statvfs", lambda _chemin: _St())





class _JournalDeTest:
    def __init__(self):
        self.lignes = []

    def info(self, message):
        self.lignes.append(("info", message))

    def warning(self, message):
        self.lignes.append(("warning", message))

    def error(self, message):
        self.lignes.append(("error", message))




class _ContexteDeTest:
    def __init__(self):
        self.log = _JournalDeTest()
        self.metadonnees = {}

    def add_output_metadata(self, valeurs):
        self.metadonnees.update(valeurs)

    def niveaux(self, mot):
        return [n for n, m in self.log.lignes if mot in m]




def _sondes(monkeypatch, resultats):
    monkeypatch.setattr(checks, "run_all", lambda *a, **k: resultats)




def _admin(client):
    client.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return client




def _message(c, cible, texte, app_nom="", qui="marie", quand=None, retenu=False):
    m = {"id": secrets.token_hex(12), "ts": quand or int(time.time()),
         "qui": qui, "cible": cible, "app": app_nom, "texte": texte}
    if retenu:
        m["retenu"] = True
    app.enregistrer_message(m)
    return m["id"]






def _racine_app_manager():
    """Le dossier qui contient app.py, quelle que soit la mise en page.

    Vecu a l'envers : cette fonction ne cherchait que app-manager/app.py. Le
    panneau vit dans app-manager/app/app.py depuis longtemps, donc aucun
    candidat ne correspondait, tous ces tests s'ignoraient en silence -- et la
    CI restait verte en ne verifiant plus rien. Les deux dispositions sont
    desormais essayees, et une image sans les sources est le SEUL cas qui
    justifie encore un saut.
    """
    bases = []
    configured = os.environ.get("CODELAB_REPO_ROOT")
    if configured:
        bases.append(Path(configured) / "app-manager")
    ici = Path(__file__).resolve()
    bases.extend((parent / "app-manager" for parent in ici.parents))
    for base in bases:
        for candidate in (base / "app", base):
            if (candidate / "app.py").is_file():
                return candidate
    pytest.skip("sources app-manager absentes de cette image")




def _charger_app_manager(monkeypatch, tmp_path):
    racine = _racine_app_manager()
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("APP_MANAGER_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("APP_MANAGER_SHARED_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("CODELAB_TESTS", "1")
    spec = importlib.util.spec_from_file_location(
        "codelab_app_manager_regression", racine / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module





def _entrypoint_postgres():
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "entrypoint.sh")
    if not os.path.exists(chemin):
        pytest.skip("entrypoint postgres absent de cette image")
    return open(chemin, encoding="utf-8").read()




def _dockerfile_dev():
    chemin = os.path.join(_racine_depot(), "dev", "Dockerfile")
    if not os.path.exists(chemin):
        pytest.skip("depot complet absent de cette image")
    return open(chemin, encoding="utf-8").read()




def _entrypoint_dev():
    chemin = os.path.join(_racine_depot(), "dev", "entrypoint.sh")
    if not os.path.exists(chemin):
        pytest.skip("depot complet absent de cette image")
    return open(chemin, encoding="utf-8").read()




def _bloc_gitconfig(tmp_path):
    """La section git de l'entrypoint, jouee dans un faux /home et /workspace."""
    source = _entrypoint_dev()
    debut = source.index('GITCONFIG="$WORKSPACE_DIR/.codelab/gitconfig"')
    fin = source.index("# ------------------------------ cles SSH")
    maison, ws = tmp_path / "home", tmp_path / "ws"
    maison.mkdir(exist_ok=True)
    ws.mkdir(exist_ok=True)
    return maison, ws, (f'set -e\nSSH_USER=$(id -un)\nCODELAB_GROUP=$(id -gn)\n'
                        f'WORKSPACE_DIR="{ws}"\nGITCONFIG_LIEN="{maison}/.gitconfig"\n'
                        + source[debut:fin])





def _readme_racine():
    chemin = os.path.join(_racine_depot(), "README.md")
    if not os.path.exists(chemin):
        pytest.skip("depot complet absent de cette image")
    return open(chemin, encoding="utf-8").read()




RAM_CIBLE_MO = 4096


COEURS_CIBLE = 2




# 256 Mio : la reservation memoire que CasaOS/ZimaOS ajoute a CHAQUE
# service en recopiant le compose. Mesuree, pas choisie -- voir le test.
RESERVATION_HOTE_MO = 256





def _dockerfile_postgres():
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "Dockerfile")
    if not os.path.exists(chemin):
        pytest.skip("Dockerfile postgres absent de cette image")
    return open(chemin, encoding="utf-8").read()





def _plaques_du_theme():
    """La valeur de --plaque-logo dans chacun des blocs de theme.css.

    Trois blocs : clair, « prefers-color-scheme: dark », et le sombre choisi
    a la main. Les trois doivent etre peints, et un jeton oublie dans l'un
    d'eux ne se voit qu'en basculant le theme a la main -- ce qui n'arrive
    jamais au moment ou l'on ecrit la regle.
    """
    feuille = _page_panneau("theme.css")
    valeurs = re.findall(r"--plaque-logo:\s*(#[0-9a-fA-F]{3,6})\s*;", feuille)
    # Deux styles : celui des applications (:root) et celui du panneau (bento).
    assert len(valeurs) == 6, (
        f"theme.css definit --plaque-logo {len(valeurs)} fois, trois par style attendues")
    return valeurs




def _luminance(hexa):
    """La luminance relative WCAG d'une couleur, pour dire « clair » sans
    se fier a l'impression qu'en donne le nom du jeton."""
    h = hexa.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    canaux = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    canaux = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in canaux]
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]





def _admin_du_hub(c):
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c





def _bloc_liste_rangement():
    page = _page_panneau("dashboard.html")
    debut = page.index("const lignes = toutes.map(a=>{")
    return page[debut:page.index("}).join('');", debut)]





def _bloc_codex(dossier):
    """Le bloc Codex de dev/entrypoint.sh, pris tel quel et execute."""
    import subprocess
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"),
                  encoding="utf-8").read()
    debut = source.index('CODEX_CFG="$CODEX_HOME_DIR/config.toml"')
    fin = source.index("# ~/.codex vers CODEX_HOME.")
    bloc = source[debut:fin]
    return subprocess.run(["sh", "-c", bloc],
                          env=dict(os.environ, CODEX_HOME_DIR=str(dossier),
                                   SSH_USER=str(os.getuid())),
                          capture_output=True, text=True, timeout=30)




# ---------- Redis : un service partage, ferme, et un verrou sur les secrets --

def _lancer_entrypoint_redis(tmp_path, extra_env=None):
    """Execute redis/entrypoint.sh tel quel, avec un faux entrypoint officiel
    qui se contente d'afficher ce qu'il recoit."""
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    faux = bin_dir / "docker-entrypoint.sh"
    faux.write_text('#!/bin/sh\necho "LANCE $*"\n', encoding="utf-8")
    faux.chmod(0o755)
    (tmp_path / "config").mkdir(exist_ok=True)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin",
           "CODELAB_CONFIG_DIR": str(tmp_path / "config"),
           "CODELAB_REDIS_CONF": str(tmp_path / "redis.conf"),
           "CODELAB_REDIS_DIR": str(tmp_path / "data")}
    env.update(extra_env or {})
    return subprocess.Popen(
        ["sh", os.path.join(_racine_depot(), "redis", "entrypoint.sh"),
         "redis-server", str(tmp_path / "redis.conf")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)




def _port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]




def _lancer_entrypoint_mailpit(tmp_path, blocs, extra_env=None):
    """mailpit/entrypoint.sh tel quel, avec un faux binaire Mailpit qui note son
    environnement et s'arrete : l'entrypoint doit alors sortir avec lui."""
    import subprocess
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "credentials.env").write_text(blocs, encoding="utf-8")
    faux = tmp_path / "mailpit"
    faux.write_text(f'#!/bin/sh\nenv | grep "^MP_" | sort > "{tmp_path}/env.txt"\nexit 0\n',
                    encoding="utf-8")
    faux.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "CODELAB_CONFIG_DIR": str(tmp_path / "config"),
           "CODELAB_MAILPIT_BIN": str(faux), "CODELAB_MAILPIT_INTERVALLE": "1",
           "CODELAB_MAILPIT_ATTENTE": "2"}
    env.update(extra_env or {})
    proc = subprocess.run(["sh", os.path.join(_racine_depot(), "mailpit", "entrypoint.sh")],
                          env=env, capture_output=True, text=True, timeout=30)
    vu = {}
    if (tmp_path / "env.txt").exists():
        for ligne in (tmp_path / "env.txt").read_text().splitlines():
            cle, _, valeur = ligne.partition("=")
            vu[cle] = valeur
    return proc, vu




_UI = "CODELAB_INTERNE_MAILPIT_UI_PASSWORD=" + "a" * 64 + "\n"





def _bloc_extensions_vscode(tmp_path):
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"), encoding="utf-8").read()
    debut = source.index('VSCODE_EXTENSIONS="${CODELAB_VSCODE_EXTENSIONS')
    fin = source.index('if [ -n "$VSCODE_EXTENSIONS" ]; then\n    ( while')
    return source, source[debut:fin]





def _initdb():
    import glob
    for chemin in sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"), reverse=True) + [
            shutil.which("initdb") or ""]:
        if chemin and os.path.exists(chemin):
            return os.path.dirname(chemin)
    return None




def _sql_proteger():
    return os.path.join(_racine_depot(), "postgres", "proteger-base.sql")





def _octets_de_l_etat():
    """Tout ce que le panneau a ecrit sur disque, concatene."""
    tout = b""
    for dossier, _, fichiers in os.walk(app.STATE_DIR):
        for nom in fichiers:
            with open(os.path.join(dossier, nom), "rb") as f:
                tout += f.read()
    if os.path.exists(app.SHARED_ENV_FILE):
        tout += open(app.SHARED_ENV_FILE, "rb").read()
    return tout




class _FauxCurseur:
    def __init__(self, base):
        self.base, self.resultat = base, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if sql.startswith("SELECT cle, valeur FROM reglages"):
            self.resultat = list(self.base.items())
        elif sql.startswith("INSERT INTO reglages"):
            self.base[params[0]] = bytes(params[1])
        elif sql.startswith("DELETE FROM reglages"):
            self.base.pop(params[0], None)

    def fetchall(self):
        return self.resultat




class _FausseBase:
    def __init__(self):
        self.lignes = {}

    def cursor(self):
        return _FauxCurseur(self.lignes)




def _mailpit_ui(nom, binaire=False):
    chemin = os.path.join(_racine_depot(), "dagster", "proxy", "mailpit-ui", nom)
    return open(chemin, "rb").read() if binaire else open(chemin, encoding="utf-8").read()





def _jeton_du_lien(partis):
    return re.search(r"/invitation/([A-Za-z0-9_-]+)", partis[-1][1]).group(1)





def _reinitialiser_par_mail(c, partis, nouveau="nouveau-mot-de-passe"):
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    code = _code_du_dernier_mail(partis)
    app._login_attempts.clear()
    return c.post("/mot-de-passe/reinitialiser",
                  json={"nom": "marie", "code": code, "nouveau": nouveau})





def _session_utilisateur(c, nom="marie"):
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur=nom,
                 ouverte=int(time.time()), jeton="j")




def _inscription_face_id(monkeypatch):
    """Un contexte HTTPS, et un navigateur dont la cle est toujours valide."""
    pytest.importorskip("webauthn")
    import webauthn
    monkeypatch.setattr(app, "passkey_contexte",
                        lambda: ("codelab.exemple.fr", "https://codelab.exemple.fr", ""))

    class Verifiee:
        credential_id = b"cle-admin"
        credential_public_key = b"publique"
        sign_count = 0
    monkeypatch.setattr(webauthn, "verify_registration_response", lambda **k: Verifiee())






PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082")




def stockage_lignes(table):
    return app.stockage.lire(app.DB_FILE, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]





DEFAUT_HTML = ('<html lang="fr" data-style="design" data-variante="bento" data-tuiles="cartes" '
               'data-defilement="horizontal" data-widgets="melanges" data-couleurs="lagon" '
               'data-motif="croix">')






def _depot(chemin, commit):
    (chemin / ".git" / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (chemin / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (chemin / ".git" / "refs" / "heads" / "main").write_text(commit + "\n")




def _facteur_cle(monkeypatch, proprietaire_ok=True):
    pytest.importorskip("webauthn")
    import webauthn
    monkeypatch.setattr(app, "passkey_contexte",
                        lambda: ("codelab.exemple.fr", "https://codelab.exemple.fr", ""))

    class Verifiee:
        new_sign_count = 1
    monkeypatch.setattr(webauthn, "verify_authentication_response", lambda **k: Verifiee())





def _module_auth():
    import importlib.util
    chemin = os.path.join(_racine_depot(), "dev", "modeles", "codelab_auth.py")
    spec = importlib.util.spec_from_file_location("codelab_auth_test", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




def _cle_sso(monkeypatch):
    cle = app._generer_cle_sso()
    monkeypatch.setattr(app, "_sso_private_key", cle)
    monkeypatch.setattr(app, "_sso_public_key", app._public_sso_b64(cle))
    return cle




def _jeton(nom_app, utilisateur="alice", email="", verifie=False, monkeypatch=None):
    app.flask_app.secret_key = "test-sso-secret"
    comptes = {utilisateur: {"email": email, "email_verifie": verifie}} if email else {}
    if monkeypatch is not None:
        monkeypatch.setattr(app, "lire_utilisateurs", lambda: comptes)
    with app.flask_app.test_request_context("/"):
        app.session["authed"] = True
        app.session["role"] = app.ROLE_UTILISATEUR
        app.session["utilisateur"] = utilisateur
        return app.jeton_sso(nom_app)




def _session_marie(c):
    """Une session ouverte sur l'hote des tests (« serveur ») : le cookie
    d'une connexion faite sur localhost ne suivrait pas."""
    with c.session_transaction(**_sur_port(9301)) as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 jeton="jeton-de-test")





def _module_web():
    chemin = os.path.join(_racine_depot(), "dev", "modeles", "codelab_web.py")
    spec = importlib.util.spec_from_file_location("codelab_web_test", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




def _app_web(entetes=True):
    from flask import Flask, Response
    m = _module_web()
    a = Flask("essai_codelab_web")

    @a.get("/")
    def accueil():
        return "accueil"

    @a.get("/factures/<n>/pdf")
    def casse(n):
        raise RuntimeError("SECRET-dans-la-trace")

    @a.get("/a-soi")
    def a_soi():
        return Response("x", headers={"Content-Security-Policy": "default-src *"})

    m.pages_d_erreur(a)
    if entetes:
        m.entetes_de_securite(a)
    return m, a.test_client()




def _projet_a_auditer(racine):
    app_ = racine / "boutique"
    (app_ / "vendor" / "Flask-2.0.0.dist-info").mkdir(parents=True)
    (app_ / "vendor" / "Flask-2.0.0.dist-info" / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: Flask\nVersion: 2.0.0\n\nName: pas-un-en-tete\n")
    (app_ / "requirements.txt").write_text(
        "requests==2.19.0\nJinja2[i18n] == 3.1.2 ; python_version > '3'\n"
        "flask>=2\n# urllib3==1.0\n-r autre.txt\n")
    venv = app_ / ".dagster-venv" / "lib" / "python3.13" / "site-packages" / "pandas-1.0.0.dist-info"
    venv.mkdir(parents=True)
    (venv / "METADATA").write_text("Name: pandas\nVersion: 1.0.0\n")
    front = racine / "vitrine"
    front.mkdir()
    (front / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {
        "": {"name": "vitrine", "version": "0.0.1"},
        "node_modules/lodash": {"version": "4.17.20"},
        "node_modules/a/node_modules/@scope/b": {"version": "1.0.0"},
        "node_modules/lien": {"link": True, "resolved": "../x"}}}))
    ancien = racine / "ancien"
    ancien.mkdir()
    (ancien / "package-lock.json").write_text(json.dumps({"lockfileVersion": 1, "dependencies": {
        "minimist": {"version": "0.0.8", "dependencies": {"sous": {"version": "2.0.0"}}}}}))
    (racine / "_mis-de-cote").mkdir()
    (racine / "_mis-de-cote" / "requirements.txt").write_text("django==1.0\n")
    (racine / ".codelab").mkdir()





def _isolation():
    chemin = os.path.join(_racine_depot(), "dagster", "isolation.py")
    spec = importlib.util.spec_from_file_location("isolation_test", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




def _workspace_isole(racine):
    for nom in ("commun", "lourd", "_cache", "sans-defs"):
        (racine / nom).mkdir(parents=True)
    for nom in ("commun", "lourd", "_cache"):
        (racine / nom / "definitions.py").write_text("defs = None\n")
    for nom in ("lourd", "_cache", "sans-defs"):
        (racine / nom / "requirements-dagster.txt").write_text("pandas==2.2.3\n")




def _admin_presentation():
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return c




def _marie_sur(agent):
    c = app.flask_app.test_client()
    c.environ_base["HTTP_USER_AGENT"] = agent
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/mes-apps").status_code == 200
    return c




IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"


MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"





def _page_injectee(connecte, page=b"<!doctype html><html><head><script src=app.js></script></head><body>ok</body></html>"):
    entetes = [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(page)))]
    with app.flask_app.test_request_context("/site/"):
        return app.injecter_environnement(page, 200, entetes, connecte)




class _Navigateur:
    """Ce que fait un navigateur abonne : une paire de cles, un secret, et le
    dechiffrement de ce qu'il recoit (RFC 8291, cote destinataire)."""

    def __init__(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        self.prive = ec.generate_private_key(ec.SECP256R1())
        self.public = self.prive.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = secrets.token_bytes(16)

    def abonnement(self, endpoint="https://web.push.apple.com/abc"):
        return {"endpoint": endpoint, "keys": {"p256dh": app._b64url(self.public),
                                               "auth": app._b64url(self.auth)}}

    def lire(self, corps):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        sel, n = corps[:16], corps[20]
        cle_serveur, chiffre = corps[21:21 + n], corps[21 + n:]
        partage = self.prive.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), cle_serveur))
        ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=self.auth,
                   info=b"WebPush: info\x00" + self.public + cle_serveur).derive(partage)
        cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=sel,
                   info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=sel,
                     info=b"Content-Encoding: nonce\x00").derive(ikm)
        clair = AESGCM(cek).decrypt(nonce, chiffre, None)
        assert clair.endswith(b"\x02")
        return json.loads(clair[:-1])




class _Envois(list):
    pass


# Tout ce qui precede, noms en _ compris : « import * » ne les prendrait pas.
__all__ = [nom for nom in list(globals()) if not nom.startswith("__")]
