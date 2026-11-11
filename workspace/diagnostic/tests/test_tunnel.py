"""
CodeLab -- TESTS : L'ACCES DEPUIS INTERNET PAR TAILSCALE FUNNEL
---------------------------------------------------------------------------
ROLE        Funnel (14-tunnel) : une requete de Funnel se reconnait a son
            port, jamais a un en-tete ; l'administration y reste fermee ;
            les adresses sont celles de la machine Tailscale (443 et 8443) ;
            la session vaut des deux cotes ; la cle ne sort jamais du
            coffre ; tailscaled et tailscale sans secret ni privilege.
CHARGE PAR  pytest : python3 -m pytest workspace/diagnostic/tests -q
            (depuis la racine du depot), la CI, et la page /tests
            du diagnostic.
CONTRAINTES Les aides, constantes et modules viennent de commun.py ; les
            fixtures de conftest.py. Aucun test ne lance tailscaled ni ne
            lit /var/lib/codelab ou /workspace reels.
"""

from commun import *  # noqa: F401,F403 -- aides, constantes, modules

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
    assert "passage" not in _lire_source("app-manager/app/panneau/00-socle.js").split(
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
    js = _lire_source("app-manager/app/panneau/08-reglages.js")
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
    js = _lire_source("app-manager/app/panneau/08-reglages.js")
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
    js = _lire_source("app-manager/app/panneau/08-reglages.js")
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
