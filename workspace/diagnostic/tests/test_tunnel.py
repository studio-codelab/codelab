"""
CodeLab -- TESTS : L'ACCES PAR LE TUNNEL CLOUDFLARE
---------------------------------------------------------------------------
ROLE        les deux Quick Tunnels (14-tunnel) : une requete du tunnel se
            reconnait a son port, jamais a un en-tete ; l'administration y
            reste fermee ; les adresses sont celles des tunnels ; le passage
            de la session d'une adresse a l'autre ; cloudflared sans secret.
CHARGE PAR  pytest : python3 -m pytest workspace/diagnostic/tests -q
            (depuis la racine du depot), la CI, et la page /tests
            du diagnostic.
CONTRAINTES Les aides, constantes et modules viennent de commun.py ; les
            fixtures de conftest.py. Aucun test ne lance cloudflared ni ne
            lit /var/lib/codelab ou /workspace reels.
"""

from commun import *  # noqa: F401,F403 -- aides, constantes, modules

HOTE_PANNEAU = "mots-au-hasard.trycloudflare.com"
HOTE_APPS = "autres-mots.trycloudflare.com"


def _par_le_tunnel(quoi):
    """Une requete arrivee par le tunnel « panneau » ou « apps » : son port,
    sur l'hote public que Cloudflare transmet."""
    if quoi == "panneau":
        return {"base_url": "https://%s:%d" % (HOTE_PANNEAU, app.TUNNEL_PORT_PANNEAU)}
    return {"base_url": "https://%s:%d" % (HOTE_APPS, app.TUNNEL_PORT_APPS)}


@pytest.fixture
def tunnel(deux_origines, monkeypatch):
    monkeypatch.setitem(app._tunnel["panneau"], "hote", HOTE_PANNEAU)
    monkeypatch.setitem(app._tunnel["apps"], "hote", HOTE_APPS)
    app._passages.clear()
    return deux_origines


def _session_marie_sur_le_tunnel(c):
    with c.session_transaction(**_par_le_tunnel("panneau")) as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 jeton="jeton-de-test", ouverte=int(time.time()))


def test_par_le_tunnel_l_administration_reste_fermee_meme_sans_en_tete(tunnel):
    """La connexion de cloudflared vient de 127.0.0.1, donc « du reseau
    local ». Sans le port pour la reconnaitre, il suffisait que
    X-Forwarded-For manque pour ouvrir l'administration a tout internet."""
    local = {"REMOTE_ADDR": "127.0.0.1"}
    with app.flask_app.test_request_context("/", environ_overrides=local,
                                            **_par_le_tunnel("panneau")):
        assert app.par_le_tunnel()
        assert not app.client_est_local()
        assert app.refus_admin_hors_reseau()
    with app.flask_app.test_request_context("/", environ_overrides=local,
                                            **_par_le_tunnel("apps")):
        assert not app.client_est_local()
    # Le meme 127.0.0.1 sur le port du panneau reste local : le tunnel ne
    # ferme que ce qui passe par lui.
    with app.flask_app.test_request_context("/", environ_overrides=local, **_sur_port(9001)):
        assert not app.par_le_tunnel()
        assert app.client_est_local()


def test_l_administrateur_ne_se_connecte_pas_par_le_tunnel(tunnel):
    r = tunnel.post("/login", json={"nom": "admin", "password": "secret-de-test"},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, **_par_le_tunnel("panneau"))
    assert r.status_code == 403
    with tunnel.session_transaction(**_par_le_tunnel("panneau")) as s:
        assert not s.get("authed")


def test_par_le_tunnel_les_adresses_sont_celles_des_deux_tunnels(tunnel):
    """Cloudflare pose X-Forwarded-* et l'hote est un nom de domaine : sans
    exception, le panneau passait en mode « un sous-domaine par
    application », que trycloudflare.com ne donne pas."""
    entetes = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}
    with app.flask_app.test_request_context("/", headers=entetes, **_par_le_tunnel("panneau")):
        assert app.domaine_des_applications() == ""
        assert app.origine_applications() == "https://" + HOTE_APPS
        assert app.modele_adresse_application() == "https://" + HOTE_APPS + "/{nom}/"
        assert app.cadres_permis() == "https://" + HOTE_APPS
        assert app.schema_vu() == "https" and app.https_actif()
        assert app.lien_panneau() == "/"
    with app.flask_app.test_request_context("/public/", headers=entetes, **_par_le_tunnel("apps")):
        assert app.sur_le_port_des_apps()
        assert app.lien_panneau() == "https://" + HOTE_PANNEAU + "/"
        assert "https://" + HOTE_PANNEAU in app.origines_encadrantes()


def test_une_adresse_de_tunnel_douteuse_ne_sert_jamais(tunnel, monkeypatch):
    """L'adresse vient de la reponse de cloudflared, et part dans des liens,
    une CSP et un en-tete Location : elle se valide comme une donnee."""
    for douteuse in ("evil.example", "x.trycloudflare.com.evil.example",
                     'a"><script>.trycloudflare.com', "trycloudflare.com", ""):
        monkeypatch.setitem(app._tunnel["apps"], "hote", douteuse)
        assert app.origine_du_tunnel("apps") == "", douteuse


def test_par_le_tunnel_le_panneau_ne_sert_jamais_une_application_chez_lui(tunnel, monkeypatch):
    """Sans adresse des applications, le panneau local sert l'application
    sur son propre port (degrade mais joignable). Par le tunnel, ce serait la
    servir dans l'origine du panneau, a tout internet : on attend."""
    monkeypatch.setitem(app._tunnel["apps"], "hote", "")
    r = tunnel.get("/public/", **_par_le_tunnel("panneau"))
    assert r.status_code == 503
    monkeypatch.setitem(app._tunnel["apps"], "hote", HOTE_APPS)
    r = tunnel.get("/public/", **_par_le_tunnel("panneau"))
    assert r.status_code == 302 and r.headers["Location"] == "https://%s/public/" % HOTE_APPS


def test_la_limite_des_essais_compte_chaque_visiteur_du_tunnel(tunnel):
    """Tous les visiteurs du tunnel arrivent de 127.0.0.1 : un seul qui se
    trompait cinq fois fermait la connexion a tout le monde. Par le tunnel
    (et par lui seul), l'adresse est celle que Cloudflare transmet."""
    with app.flask_app.test_request_context(
            "/", headers={"CF-Connecting-IP": "198.51.100.4"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, **_par_le_tunnel("panneau")):
        assert app._client_ip() == "198.51.100.4"
    with app.flask_app.test_request_context(
            "/", headers={"CF-Connecting-IP": "pas une adresse"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, **_par_le_tunnel("panneau")):
        assert app._client_ip() == "tunnel"
    # Ailleurs, l'en-tete ne vaut rien : n'importe quel client le pose.
    with app.flask_app.test_request_context(
            "/", headers={"CF-Connecting-IP": "198.51.100.4"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.20"}, **_sur_port(9001)):
        assert app._client_ip() == "192.168.1.20"


def test_le_passage_porte_la_session_sur_l_adresse_des_applications(tunnel, monkeypatch):
    """*.trycloudflare.com est un suffixe public : la session ouverte sur
    l'adresse du panneau n'arrive pas a celle des applications, et une
    application privee y renvoyait sans fin a la connexion."""
    vus = {}

    def amont(port, methode, chemin, entetes, corps):
        vus.clear()
        vus.update({k.lower(): v for k, v in entetes.items()})
        return 200, [("Content-Type", "text/plain")], b"ok"
    monkeypatch.setattr(app, "demander_a_l_application", amont)
    _cle_sso(monkeypatch)
    c = tunnel
    # Sans session sur l'adresse des applications : vers le passage, sur
    # l'adresse du panneau, avec le chemin demande.
    r = c.get("/prive-autorise/factures/12", **_par_le_tunnel("apps"))
    assert r.status_code == 302
    assert r.headers["Location"] == "https://%s/passage/prive-autorise/factures/12" % HOTE_PANNEAU
    # Le passage exige une session : la connexion, qui y revient ensuite.
    r = c.get("/passage/prive-autorise/factures/12", **_par_le_tunnel("panneau"))
    assert r.status_code == 302
    assert "/login?suite=/passage/prive-autorise/factures/12" in r.headers["Location"]
    _session_marie_sur_le_tunnel(c)
    r = c.get("/passage/prive-autorise/factures/12", **_par_le_tunnel("panneau"))
    assert r.status_code == 302
    with c.session_transaction(**_par_le_tunnel("panneau")) as s:
        sid = s["sid"]
    cible = urllib.parse.urlsplit(r.headers["Location"])
    assert (cible.scheme, cible.netloc, cible.path) == (
        "https", HOTE_APPS, "/prive-autorise/factures/12")
    ticket = urllib.parse.parse_qs(cible.query)[app.PASSAGE_PARAMETRE][0]
    # L'echange : la meme page, sans le ticket dans l'adresse.
    r = c.get("/prive-autorise/factures/12", query_string={app.PASSAGE_PARAMETRE: ticket},
              **_par_le_tunnel("apps"))
    assert r.status_code == 302 and r.headers["Location"].endswith("/prive-autorise/factures/12")
    # La meme session (meme sid : les deconnexions la ferment des deux cotes).
    with c.session_transaction(**_par_le_tunnel("apps")) as s:
        assert s.get("authed") and s.get("utilisateur") == "marie" and s.get("sid") == sid
    r = c.get("/prive-autorise/factures/12", **_par_le_tunnel("apps"))
    assert r.status_code == 200
    assert vus["x-codelab-user"] == "marie"
    # Regle 19 : l'application ne recoit jamais le cookie de session.
    assert "session=" not in vus.get("cookie", "")
    # Le ticket ne sert qu'une fois.
    r = c.get("/prive-autorise/", query_string={app.PASSAGE_PARAMETRE: ticket},
              **_par_le_tunnel("apps"))
    assert r.status_code == 403


def test_un_passage_ne_vaut_que_pour_son_application_et_sa_minute(tunnel, monkeypatch):
    c = tunnel
    _session_marie_sur_le_tunnel(c)

    def ticket():
        r = c.get("/passage/prive-autorise/", **_par_le_tunnel("panneau"))
        return urllib.parse.parse_qs(urllib.parse.urlsplit(
            r.headers["Location"]).query)[app.PASSAGE_PARAMETRE][0]
    # Une autre application que la sienne.
    r = c.get("/public/", query_string={app.PASSAGE_PARAMETRE: ticket()}, **_par_le_tunnel("apps"))
    assert r.status_code == 403
    # Expire.
    t = ticket()
    app._passages[t]["fin"] = time.time() - 1
    r = c.get("/prive-autorise/", query_string={app.PASSAGE_PARAMETRE: t}, **_par_le_tunnel("apps"))
    assert r.status_code == 403
    # Hors du tunnel des applications (le port local), il ne s'echange pas :
    # le visiteur reste anonyme, renvoye a la connexion.
    t = ticket()
    r = c.get("/prive-autorise/", query_string={app.PASSAGE_PARAMETRE: t}, **_sur_port(9302))
    assert r.status_code == 302 and "/login?" in r.headers["Location"]
    with c.session_transaction(**_sur_port(9302)) as s:
        assert not s.get("authed")


def test_le_passage_n_est_jamais_accorde_a_l_administrateur(tunnel):
    c = tunnel
    with c.session_transaction(**_par_le_tunnel("panneau")) as s:
        s.update(authed=True, role=app.ROLE_ADMIN, utilisateur=app.NOM_ADMIN,
                 jeton="j", ouverte=int(time.time()))
    r = c.get("/passage/public/", **_par_le_tunnel("panneau"))
    assert r.status_code == 403 and not app._passages


def test_hors_tunnel_le_passage_mene_a_l_adresse_habituelle(tunnel):
    c = tunnel
    _session_marie(c)
    r = c.get("/passage/public/a", **_sur_port(9301))
    assert r.status_code == 302 and r.headers["Location"] == "http://serveur:9302/public/a"
    assert not app._passages


def test_par_le_tunnel_le_hub_ouvre_les_applications_par_le_passage(tunnel):
    """Un cadre d'un autre site ne recoit pas le cookie (SameSite=Lax) : par
    le tunnel, le hub ouvre l'application par /passage/, sans cadre."""
    c = tunnel
    _session_marie_sur_le_tunnel(c)
    page = c.get("/", **_par_le_tunnel("panneau")).get_data(as_text=True)
    assert "passage: true" in page
    _session_marie(c)
    assert "passage: false" in c.get("/", **_sur_port(9301)).get_data(as_text=True)
    socle = _lire_source("app-manager/app/panneau/00-socle.js")
    assert "CODELAB_PAGE.passage ? '/passage/' + encodeURIComponent(a.name) + '/'" in socle


def test_cloudflared_ne_recoit_aucun_secret_et_ne_tourne_pas_en_root(tunnel, monkeypatch, tmp_path):
    """cloudflared parle a internet : il n'herite ni de l'environnement du
    panneau (mot de passe, cle de session) ni de root (regles 19 et 20)."""
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
    monkeypatch.setenv("APP_MANAGER_PASSWORD", "ne-doit-pas-sortir")
    app._lancer_le_tunnel("apps")
    monkeypatch.setitem(app._tunnel["apps"], "proc", None)
    assert set(vus["env"]) == {"HOME", "PATH"}
    assert vus["preexec_fn"] is app.drop_privileges
    assert vus["cmd"][-2:] == ["--url", "http://127.0.0.1:%d" % app.TUNNEL_PORT_APPS]
    assert "127.0.0.1:%d" % app.TUNNEL_METRIQUES["apps"] in vus["cmd"]


def test_les_ports_du_tunnel_n_ecoutent_que_sur_la_boucle_locale():
    """Leur port est la preuve qu'une requete vient du tunnel : joignables
    depuis le reseau, n'importe qui s'y presenterait comme « internet »
    -- et y choisirait son CF-Connecting-IP."""
    src = _source_du_panneau()
    corps = src.split("def _ouvrir_les_ports_du_tunnel(")[1].split("\ndef ")[0]
    assert 'args=(p, "127.0.0.1")' in corps
    assert set(app.TUNNEL_PORTS).isdisjoint(range(app.PORT_MIN, app.PORT_MAX + 1))
    docker = _lire_source("app-manager/Dockerfile")
    assert "COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared" in docker


def test_l_interrupteur_du_tunnel_est_reserve_a_l_administrateur(tunnel, compte_admin, monkeypatch):
    monkeypatch.setattr(app, "tunnel_disponible", lambda: True)
    monkeypatch.setattr(app, "tour_du_tunnel", lambda: None)
    # Un compte utilisateur : refuse.
    c = tunnel
    _session_marie(c)
    assert c.get("/api/tunnel", **_sur_port(9301)).status_code == 403
    assert c.put("/api/tunnel", json={"actif": True}, **_sur_port(9301)).status_code == 403
    # L'administrateur, depuis le reseau local.
    r = compte_admin.get("/api/tunnel")
    assert r.status_code == 200 and r.get_json()["actif"] is False
    assert compte_admin.put("/api/tunnel", json={"actif": "oui"}).status_code == 400
    r = compte_admin.put("/api/tunnel", json={"actif": True})
    assert r.status_code == 200 and r.get_json()["actif"] is True
    assert app.tunnel_voulu()
    # Sans le jeton de la session : refuse (regle 13).
    r = compte_admin.put("/api/tunnel", json={"actif": False},
                         headers={app.JETON_ENTETE: "faux"})
    assert r.status_code == 403 and app.tunnel_voulu()


def test_la_nouvelle_adresse_part_une_fois_par_mail(tunnel, monkeypatch):
    """Loin de chez soi, l'adresse du tunnel est la seule porte, et elle
    change a chaque redemarrage : sans ce mail, une coupure de courant
    enferme dehors."""
    envois = []
    monkeypatch.setattr(app, "alertes_admin", lambda: ["admin@example.test"])
    monkeypatch.setattr(app, "envoyer_avec_repli",
                        lambda sujet, corps, cibles, **kw: envois.append(corps) or (True, ""))
    app._annoncer_l_adresse("https://" + HOTE_PANNEAU)
    app._annoncer_l_adresse("https://" + HOTE_PANNEAU)
    assert len(envois) == 1 and HOTE_PANNEAU in envois[0]
    app._annoncer_l_adresse("https://autre.trycloudflare.com")
    assert len(envois) == 2
