"""
CodeLab -- TESTS : LE PROXY
---------------------------------------------------------------------------
ROLE        le relais vers les applications : connexions, redirections,
            en-tetes, pages d'erreur, mode application encadre.
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




def test_codelab_new_depose_les_modules_de_connexion(tmp_path):
    modeles = os.path.join(_racine_depot(), "dev", "modeles")
    r, espace = _lancer_codelab(tmp_path, ["new", "boutique"], {"CODELAB_MODELES": modeles})
    assert r.returncode == 0, r.stdout + r.stderr
    for nom in ("codelab_auth.py", "codelab-auth.js"):
        assert (espace / "boutique" / nom).read_text() == open(os.path.join(modeles, nom)).read()
    dockerfile = open(os.path.join(_racine_depot(), "dev", "Dockerfile")).read()
    assert "COPY dev/modeles/ /usr/local/share/codelab/modeles/" in dockerfile
    manuel = open(os.path.join(_racine_depot(), "dev", "AGENTS.md")).read()
    assert "connexion_silencieuse" in manuel and "email_verified" in manuel


# ------------------- pages d'erreur et en-tetes des applications -------------------
#
# codelab_web, depose par « codelab new » : une erreur ne montre jamais sa
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
    module = os.path.join(_racine_depot(), "dev", "modeles", "codelab-web.js")
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




def test_codelab_new_depose_les_modules_web_et_un_claude_md(tmp_path):
    modeles = os.path.join(_racine_depot(), "dev", "modeles")
    r, espace = _lancer_codelab(tmp_path, ["new", "atelier"], {"CODELAB_MODELES": modeles})
    assert r.returncode == 0, r.stdout + r.stderr
    projet = espace / "atelier"
    for nom in ("codelab_web.py", "codelab-web.js"):
        assert (projet / nom).read_text() == open(os.path.join(modeles, nom)).read()
    assert (projet / "CLAUDE.md").read_text().startswith("@AGENTS.md\n")
    assert ".dagster-venv/" in (projet / ".gitignore").read_text().splitlines()




def test_codelab_agents_ne_touche_pas_un_claude_md_existant(tmp_path):
    projet = tmp_path / "ws" / "vieux"
    projet.mkdir(parents=True)
    (projet / "CLAUDE.md").write_text("mes consignes\n")
    r, _ = _lancer_codelab(tmp_path, ["agents", str(projet)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert (projet / "CLAUDE.md").read_text() == "mes consignes\n"
    assert "n'importe pas AGENTS.md" in r.stdout
    # Un CLAUDE.md qui l'importe deja : rien a dire.
    (projet / "CLAUDE.md").write_text("@AGENTS.md\net le reste\n")
    r, _ = _lancer_codelab(tmp_path, ["agents", str(projet)])
    assert "CLAUDE.md" not in r.stdout




def test_le_manuel_porte_la_liste_avant_mise_en_ligne():
    manuel = open(os.path.join(_racine_depot(), "dev", "AGENTS.md"), encoding="utf-8").read()
    assert "## Avant de declarer une application prete" in manuel
    for attendu in ("375 px", "codelab_web", "entetes_de_securite", "requirements-dagster.txt",
                    "`.env` dans le `.gitignore`", "codelab db"):
        assert attendu in manuel, attendu
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




@pytest.mark.parametrize("page", ["dashboard.html", "login.html", "messages.html"])
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




def test_sur_son_sous_domaine_l_application_installee_s_ouvre_chez_elle():
    page = b"<html><head><title>x</title></head><body>ok</body></html>"
    entetes = [("Content-Type", "text/html")]
    with app.flask_app.test_request_context("/"):
        app.g.sous_domaine = "blog"
        data, _ = app.injecter_webapp(page, 200, entetes, "blog", "Blog")
        manifeste = json.loads(app.webapp_manifeste("blog", "Blog").get_data(as_text=True))
    assert b'<link rel="manifest" href="/__codelab/manifest.webmanifest">' in data
    assert manifeste["start_url"] == manifeste["scope"] == "/"
    assert manifeste["icons"][0]["src"] == "/__codelab/icone.png"


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
