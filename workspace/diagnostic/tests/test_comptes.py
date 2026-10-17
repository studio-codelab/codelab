"""
CodeLab -- TESTS : LES COMPTES
---------------------------------------------------------------------------
ROLE        connexion, second facteur, cles d'acces, mots de passe,
            invitations, sessions, appareils connectes, retour apres
            connexion.
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
    assert not app.trust_proxy(), "X-Forwarded-For ne doit jamais etre cru"
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
    assert app.SESSION_INACTIVITE == datetime.timedelta(days=3)
    assert app.SESSION_ABSOLUE == datetime.timedelta(days=30)
    # Le cookie porte la limite d'inactivite : c'est lui qui la fait
    # respecter, y compris sur un navigateur qui ne revient jamais.
    assert app.flask_app.config["PERMANENT_SESSION_LIFETIME"] == app.SESSION_INACTIVITE




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
    monkeypatch.setattr(app, "trust_proxy", lambda: True)
    d = _etat_passkeys(cles, Host="192.168.1.20:9001", **{"X-Forwarded-Proto": "https"})
    assert d["possible"] is False
    assert "nom de domaine" in d["empechement"]
    monkeypatch.setattr(app, "trust_proxy", lambda: False)

    # localhost est un contexte securise pour le navigateur : ca marche.
    assert _etat_passkeys(cles, Host="localhost:9001")["possible"] is True




def test_un_proxy_non_declare_n_est_pas_cru_sur_parole(cles, monkeypatch):
    """X-Forwarded-Proto se pose par n'importe quel client.

    Le message change alors de nature : ce n'est pas « mets du TLS », c'est
    « declare ton proxy » -- et c'est la difference entre chercher une heure
    et poser une variable.
    """
    pytest.importorskip("webauthn")
    # Le schema annonce suffit pour les cles : la signature porte l'origine
    # que le navigateur a REELLEMENT vue, et ne concorde pas avec un schema
    # invente. Aucun « proxy de confiance » a declarer.
    entetes = {"Host": "codelab.example.com", "X-Forwarded-Proto": "https"}
    assert _etat_passkeys(cles, **entetes)["possible"] is True




def test_une_cle_inconnue_n_ouvre_aucune_session(cles):
    """C'est la signature qui fait foi, jamais le nom annonce par le client."""
    pytest.importorskip("webauthn")
    entetes = {"Host": "localhost:9001"}
    with cles.session_transaction() as s:
        s["passkey_defi"] = base64.b64encode(b"defi").decode()
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
    assert "smtp: false}" in page and "let SMTP_OK = CODELAB_PAGE.smtp;" in page
    assert 'id="ov-smtp"' in page

    monkeypatch.setattr(app, "lire_smtp_personnalise", lambda: {
        "host": "smtp.example.com", "port": 587, "tls": "starttls",
        "expediteur": "codelab@example.com"})
    assert app.envoi_mail_configure() is True
    r = c.post("/api/utilisateurs", json={"nom": "paul", "mot_de_passe": "x" * 16})
    assert r.status_code == 200, r.get_json()
    assert "smtp: true}" in c.get("/").get_data(as_text=True)




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
    assert 'id="us-f-suspendre"' in page and 'onclick="usSuspendre()"' in page
    assert 'onclick="usSupprimer()"' in page
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
    assert d["moyens"] == ["passkey", "totp"]
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
        sess["passkey_defi"] = base64.b64encode(b"defi").decode()
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
    assert 'id="pk-bascule" onchange="pkBasculer(this)"' in page
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
    monkeypatch.setattr(app, "trust_proxy", lambda: True)
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"},
           headers={"X-Forwarded-For": "192.168.1.10"})       # l'installation
    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"},
           headers={"X-Forwarded-For": "192.168.1.10"})       # deja vue
    assert envois == []
    c.post("/logout")
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"},
           headers={"X-Forwarded-For": "192.168.1.77"})       # nouvelle
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
    page = _page_panneau("dashboard.html")
    page = _page_panneau("messages.html")
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
    module = os.path.join(_racine_depot(), "dev", "modeles", "codelab-auth.js")
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
    assert '"/"' in page and "evil" not in page.split("<script>")[1]




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
    assert re.findall(r'data-p="([a-z]+)"', onglets) == ["compte", "apparence", "hub", "securite", "email"]
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
    assert "settings-card sans-pli" in session_ and "doLogout()" in session_
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
