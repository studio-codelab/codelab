"""
CodeLab -- TESTS : LA SECURITE
---------------------------------------------------------------------------
ROLE        les regles de SECURITE.md : SSO, CSRF, CSP, origines separees,
            reseau local, isolation, secrets transmis, coffre, une base par
            projet, ce qu'un compte utilisateur voit.
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




# ---------- l'administration ne passe pas par le VPS ----------

def test_l_administration_ne_passe_pas_par_le_proxy(tmp_path, monkeypatch):
    """L'administration ne repond qu'en connexion directe depuis le reseau
    local : une requete relayee (X-Forwarded-*) est refusee, VPS compris."""
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




def test_une_fois_l_adresse_connue_le_partage_redevient_possible(exposition):
    """Plus de champ a remplir : l'adresse vient du domaine par lequel le
    panneau a ete ouvert depuis internet (ou du compose)."""
    c = exposition
    app.ecrire_exposition({"adresse_publique": "https://codelab.example.com"})
    assert app.adresse_publique() == "https://codelab.example.com"
    assert c.get("/api/securite").get_json()["adresse_publique"] == "https://codelab.example.com"
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
    part, des que la requete arrive en https -- directement ou annoncee par
    un proxy. En clair, rien de tel (le navigateur ne renverrait plus le
    cookie)."""
    app.flask_app.secret_key = "cle-de-test"
    app._login_attempts.clear()
    with app.flask_app.test_request_context("/", base_url="http://localhost"):
        assert app.https_actif() is False
    with app.flask_app.test_request_context("/", base_url="https://localhost"):
        assert app.https_actif() is True
    with app.flask_app.test_request_context("/", headers={"X-Forwarded-Proto": "https"}):
        assert app.https_actif() is True
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




def test_l_adresse_du_compose_l_emporte_sur_celle_de_la_page(exposition, monkeypatch):
    """Sinon la page laisserait modifier ce qu'un redemarrage remettrait."""
    c = exposition
    monkeypatch.setenv("APP_MANAGER_PUBLIC_URL", "https://depuis-le-compose.example/")
    assert app.adresse_publique() == "https://depuis-le-compose.example"
    assert c.get("/api/securite").get_json()["adresse_publique"] == "https://depuis-le-compose.example"




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
        # Ouverte par la personne invitee, sans session -- ou dans le
        # navigateur de l'administrateur qui essaie son propre lien.
        "invitation_accepter",
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
    assert "COPY dagster/proxy/mailpit-ui/ /usr/share/nginx/codelab/mailpit/" in dockerfile
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
    # logo.svg de mailpit-ui, que rien ne servait, est partie.
    logo = app.ICONE_MAILPIT
    assert not os.path.exists(os.path.join(_racine_depot(), "dagster", "proxy", "mailpit-ui", "logo.svg"))
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
    messages = _page_panneau("messages.html")
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
