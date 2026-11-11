"""
CodeLab -- TESTS : LES AIDES ET LES FIXTURES
---------------------------------------------------------------------------
ROLE        Les imports, les aides, les constantes et le chargement des
            sondes (checks.py) et du panneau (checks.app), puis les
            fixtures, dont le filet (_bac_a_sable, autouse) qui detourne
            tous les chemins d'etat avant chaque test. Aucun test ici.
CHARGE PAR  pytest, de lui-meme (ses fixtures valent pour test_panneau.py
            et test_services.py), et « from conftest import * » en tete de
            ces deux fichiers pour les aides : __all__, pose apres les
            aides et avant les fixtures, exporte aussi les noms en _.
STRUCTURE   les aides (ex-commun.py), puis les fixtures.

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
    "TAILSCALE_DIR",
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
    env.update(env_sup or {})
    (tmp_path / "home").mkdir(exist_ok=True)

    r = subprocess.run(["sh", outil] + args, capture_output=True, text=True,
                       env=env, timeout=120)
    return r, espace




_DEBUT_RECONCILIATION = "# --------------------- les projets par defaut, toujours la"


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
    return hashlib.sha256(contenu.encode()).hexdigest()




def _ecrire_bloc_alertes(valeurs):
    """Le bloc SMTP « d'origine » de credentials.env, tel qu'une installation
    le pose a la main : le panneau ne l'ecrit jamais (il le garde en repli).
    Fusionne avec ce qui y est deja, comme on editerait le fichier."""
    cles = list(app.CHAMPS_SMTP.values()) + ["ALERTE_ADMIN"]
    fusion = {}
    for cle in cles:
        valeur = valeurs.get(cle) if cle in valeurs else app.read_shared_value(cle)
        if str(valeur or "").strip():
            fusion[cle] = valeur
    # Le nom du bloc tel qu'une installation l'ecrit : le panneau, lui, lit
    # ses cles une par une (read_shared_value), jamais le bloc par son nom.
    return app.upsert_shared_block("codelab-alertes", [], fusion)




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
    """app.py, le panneau entier (un seul fichier)."""
    return open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()




def _page_panneau(nom):
    """Un fichier du panneau, tel que les tests le lisent.

    dashboard.html charge son script depuis /panneau.js (les modules de
    panneau.js, mis en cache) : pour
    les tests, la page est RECONSTITUEE, le script remis a la place de sa
    balise. Ce qu'un test verifie du script (presence, ordre, corps d'une
    fonction) se lit donc la ou il le lisait quand il etait en ligne.
    """
    if nom == "app.py":
        return _source_du_panneau()
    if nom == PAGE_MESSAGES:
        return open(os.path.join(DOSSIER_MESSAGES, "index.html"), encoding="utf-8").read()
    texte = open(os.path.join(DOSSIER_PANNEAU, "app", nom), encoding="utf-8").read()
    if nom == "dashboard.html":
        balise = '<script src="/panneau.js?v=__PANNEAU_VERSION__"></script>'
        assert texte.count(balise) == 1, "dashboard.html ne charge plus panneau.js"
        script = app.PANNEAU_JS     # panneau.js
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
    for bloc in re.findall(r'<script(?: nonce="__NONCE__")?>(.*?)</script>', page, re.S):
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
    # Le panneau se lit ENTIER : un nom defini dans une partie et appele
    # par main est bien defini.
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
    """Range un message dans l'application Messages branchee (appli_messages),
    sans passer par le hub : pour les tests de la boite, pas du relais."""
    ident = secrets.token_hex(12)
    with c.donnees.base() as b:
        b.executer("INSERT INTO messages (id, ts, qui, cible, app, texte, retenu) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (ident, quand or int(time.time()), qui, cible, app_nom, texte,
                    1 if retenu else 0))
    return ident


DOSSIER_MESSAGES = os.path.join(os.path.dirname(DOSSIER_DIAGNOSTIC), "messages")
# La page de l'application Messages, lue par _page_panneau comme les pages du
# panneau : les memes garanties d'affichage la couvrent.
PAGE_MESSAGES = "messages/index.html"
_module_messages = {}


def _application_messages():
    """L'application Messages (workspace/messages/app.py), chargee une fois.

    Sous un nom a elle : « app » est deja le panneau dans cette suite."""
    if "module" not in _module_messages:
        if not os.path.isfile(os.path.join(DOSSIER_MESSAGES, "app.py")):
            pytest.skip("projet messages absent de cette installation")
        spec = importlib.util.spec_from_file_location(
            "codelab_messages_regression", os.path.join(DOSSIER_MESSAGES, "app.py"))
        module = importlib.util.module_from_spec(spec)
        # Son dossier passe en tete de sys.path le temps de l'import (pour
        # donnees et codelab_auth) : laisse la, il ferait trouver SON app.py a
        # qui importe celui du diagnostic.
        avant = list(sys.path)
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = avant
        _module_messages["module"] = module
    return _module_messages["module"]


class ClientMessages:
    """Un client de l'application Messages qui se presente comme le proxy
    le ferait : le jeton signe d'un compte (l'administrateur par defaut), et
    l'en-tete X-Messages que pose la page sur ce qui modifie."""

    def __init__(self, module, sujet="admin", role="admin"):
        self.module, self.donnees = module, module.donnees
        self.client = module.app.test_client()
        self.sujet, self.role = sujet, role

    def entetes(self, en_plus=None):
        h = {"X-CodeLab-Auth": app.jeton_signe(app.MESSAGERIE_NOM, self.sujet, self.role),
             "X-Messages": "1"}
        h.update(en_plus or {})
        return h

    def open(self, chemin, method="GET", headers=None, **kw):
        return self.client.open(chemin, method=method, headers=self.entetes(headers), **kw)

    def get(self, chemin, **kw):
        return self.open(chemin, "GET", **kw)

    def post(self, chemin, **kw):
        return self.open(chemin, "POST", **kw)

    def put(self, chemin, **kw):
        return self.open(chemin, "PUT", **kw)

    def delete(self, chemin, **kw):
        return self.open(chemin, "DELETE", **kw)






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
    chemin = os.path.join(_racine_depot(), "dagster", "proxy", nom)
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
               'data-defilement="horizontal" data-widgets="melanges" data-couleurs="lagon">')






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
    chemin = os.path.join(_racine_depot(), "dev", "codelab_auth.py")
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
    chemin = os.path.join(_racine_depot(), "dev", "codelab_web.py")
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


# ===========================================================================
# LES FIXTURES
# Le filet qui detourne les chemins d'etat, et les fixtures de la suite.
# Apres __all__ : elles ne s'exportent pas dans les fichiers de tests, pytest
# les y trouve de lui-meme.
# ===========================================================================

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
        "TAILSCALE_DIR": str(etat / "tailscale"),
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
def messagerie(tmp_path, monkeypatch, appli_messages):
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
    _ecrire_bloc_alertes({"SMTP_HOST": "origine.example.com",
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
def appli_messages(tmp_path, monkeypatch):
    """L'application Messages, branchee au panneau comme dans l'installation :
    sa base (SQLite, dans tmp_path), la cle de signature du panneau, et le
    transport du panneau (_vers_messagerie) qui la joint par son client de
    test au lieu de 127.0.0.1. Rend l'administrateur de l'application."""
    module = _application_messages()
    _cle_sso(monkeypatch)
    monkeypatch.setenv("CODELAB_SSO_PUBLIC_KEY", app._sso_public_key)
    monkeypatch.setenv("CODELAB_SSO_ISSUER", app.SSO_ISSUER)
    monkeypatch.setenv("CODELAB_SSO_AUDIENCE", app.MESSAGERIE_NOM)
    monkeypatch.setattr(module.donnees, "SQLITE", str(tmp_path / "messages.db"))
    module.donnees.preparer()
    monkeypatch.setattr(module, "_etat", {"prete": True})
    monkeypatch.setattr(app, "_messagerie", {"ts": 0, "non_lus": 0, "suivis": {}})
    monkeypatch.setattr(app, "_dernier_message", {})
    client = module.app.test_client()

    def transport(methode, chemin, entetes, corps):
        r = client.open(chemin, method=methode, headers=entetes, data=corps)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.data
    monkeypatch.setattr(app, "_vers_messagerie", transport)
    return ClientMessages(module)


@pytest.fixture
def boite(tmp_path, monkeypatch, appli_messages):
    """L'administrateur, des deux cotes : dans l'application Messages (la
    boite, le planning) et dans le panneau (le hub, les notifications)."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    app._apps_cache["signature"] = None
    app.save({})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    return appli_messages, c




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
