"""
CodeLab -- TESTS : LES APPLICATIONS
---------------------------------------------------------------------------
ROLE        detection, configuration, lancement et arret, renommage, logo,
            applications par defaut, processus qui survivent, robustesse,
            dependances.
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
    # Le deploiement aussi : un build reussi suivi d'une mise en ligne ratee
    # repondait « ok ».
    deploiement = src.split("def api_deploy(n):")[1].split("\n\n\n")[0]
    assert "erreur = restart_app(n) if is_running(n) else start(n)" in deploiement
    assert "409" in deploiement




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
                      f"/api/deploy/{nom}", f"/api/build/{nom}"):
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
              app.DESCRIPTION_MAILPIT, app.DESCRIPTION_MESSAGERIE):
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
    app.save(apps)

    app.tenir_les_applications_par_defaut()

    apres = app.load()
    for nom in app.APPS_PAR_DEFAUT:
        assert apres[nom]["enabled"] is True, nom
    # La tuile du diagnostic dit ce qu'elle est : sa fiche n'ayant pas de
    # formulaire, cette phrase ne s'ecrit nulle part ailleurs.
    assert apres[app.DIAGNOSTIC_NOM]["description"] == app.DIAGNOSTIC_DESCRIPTION
    # Rien n'est impose a Dagster : ce qu'on repare ici, c'est l'etat mort.
    assert "description" not in apres[app.DAGSTER_NOM]
    # Une application ordinaire arretee le RESTE : un arret volontaire est un
    # choix, et le rattrapage ne vaut que pour les deux qui n'ont pas le choix.
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
    assert 'id="e-nom"' in page and 'onclick="renommerApp()"' in page
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
    r = c.post("/api/deploy/compta")
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
    assert "estAdmin && !a.externe && !a.interne ? item('menu-reglages'" in menu
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

def test_une_application_archivee_disparait_puis_revient_telle_quelle(hub, monkeypatch):
    """Vecu : « donner la possibilite d'archiver une app. La configuration
    disparait au bout de 30 jours si elle n'est pas desarchivee. Un espace
    dedie dans les configurations avec toutes les app archivees. »
    Archivee : hors de la liste, du hub et du proxy, jamais redemarree, sa
    fiche et les droits des comptes gardes. Desarchivee : de retour, arretee."""
    c = hub
    monkeypatch.setattr(app, "stop", lambda n: None)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.post("/api/app/compta/archiver")
    assert r.status_code == 200 and r.get_json()["suppression_le"]
    assert "compta" not in [a["name"] for a in c.get("/api/apps").get_json()["apps"]]
    archivees = c.get("/api/apps/archivees").get_json()
    assert [a["name"] for a in archivees["apps"]] == ["compta"] and archivees["delai_jours"] == 30
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
    # Desarchivee : de retour, arretee, avec ses droits.
    assert c.post("/api/app/compta/desarchiver").status_code == 200
    assert "compta" in [a["name"] for a in c.get("/api/apps").get_json()["apps"]]
    assert "compta" in [a["name"] for a in u.get("/api/mes-apps").get_json()["apps"]]
    assert app.load()["compta"]["enabled"] is False
    assert c.post("/api/app/compta/desarchiver").status_code == 404


def test_trente_jours_apres_l_archive_la_configuration_est_effacee(hub, monkeypatch):
    c = hub
    monkeypatch.setattr(app, "stop", lambda n: None)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.post("/api/app/secret/archiver").status_code == 200
    quand = app.load()["secret"]["archivee_le"]
    assert app.purger_les_apps_archivees(quand + 29 * 86400) == []
    assert "secret" in app.load()
    assert app.purger_les_apps_archivees(quand + 30 * 86400) == ["secret"]
    assert "secret" not in app.load() and "compta" in app.load()


def test_une_application_par_defaut_ne_s_archive_pas(hub):
    c = hub
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert c.post(f"/api/app/{app.DIAGNOSTIC_NOM}/archiver").status_code == 403
    assert c.post("/api/app/inconnue/archiver").status_code == 404
    page = _page_panneau("dashboard.html")
    assert 'id="e-archiver"' in page and 'id="param-archives"' in page
    assert 'data-p="archives" onclick="paramOnglet(\'archives\')" data-admin="1"' in page
