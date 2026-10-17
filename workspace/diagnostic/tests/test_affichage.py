"""
CodeLab -- TESTS : L'AFFICHAGE
---------------------------------------------------------------------------
ROLE        theme, script des pages, accents, parametres, hub et tuiles,
            categories, mode application, defilement, telephone, video.
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




def test_chaque_morceau_du_serveur_est_charge_et_s_analyse_seul():
    """Vecu : « Decoupe app.py ». Le panneau vit dans serveur/, execute dans
    l'ordre de MODULES_DU_SERVEUR : un fichier absent de la liste ne
    tournerait jamais, sans un mot. Et chaque morceau s'analyse seul -- une
    frontiere qui couperait une fonction en deux ne s'executerait pas."""
    dossier = os.path.join(DOSSIER_PANNEAU, "app", "serveur")
    presents = sorted(n for n in os.listdir(dossier) if n.endswith(".py"))
    assert list(app.MODULES_DU_SERVEUR) == presents, (
        "serveur/ et MODULES_DU_SERVEUR divergent")
    for nom in presents:
        source = open(os.path.join(dossier, nom), encoding="utf-8").read()
        ast.parse(source, nom)
        assert source.startswith("# CodeLab -- LE PANNEAU, "), f"{nom} sans en-tete"
    # Les fonctions viennent de leur vrai fichier, et vivent dans app : un
    # remplacement par monkeypatch.setattr(app, ...) atteint leurs appels.
    assert inspect.getsourcefile(app._proxy).endswith(os.path.join("serveur", "13-hub-et-proxy.py"))
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
    for nom in ("dashboard.html", "login.html", "messages.html"):
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
    declencheurs = page.split('onclick="doLogout()"')[:-1]
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

    polices = os.path.join(DOSSIER_PANNEAU, "app", "polices")
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




def test_sous_un_sous_domaine_le_lien_du_hub_mene_au_domaine(monkeypatch):
    """blog.toi.duckdns.org ramene a toi.duckdns.org : le panneau est au
    domaine, chaque application a un sous-domaine a son nom."""
    monkeypatch.setitem(app._origines_separees, "actif", True)
    monkeypatch.setattr(app, "APPS_PORT", 9002)
    monkeypatch.setattr(app, "load", lambda: {"blog": {"port": 8100}})
    entetes = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.5"}
    with app.flask_app.test_request_context(
            "/", environ_overrides={"HTTP_HOST": "blog.toi.duckdns.org", "SERVER_PORT": "9002"},
            headers=entetes):
        assert app.application_du_sous_domaine() == "blog"
        assert app.lien_panneau() == "https://toi.duckdns.org/"
    # Separation inactive : rien ne change, le lien reste relatif.
    monkeypatch.setitem(app._origines_separees, "actif", False)
    with app.flask_app.test_request_context(
            "/", environ_overrides={"HTTP_HOST": "blog.toi.duckdns.org", "SERVER_PORT": "9002"},
            headers=entetes):
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
    assert "if not (domaine_des_applications() or origine_applications()):" in bloc
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
    assert "allerA('message')" in entree
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
def test_le_script_du_panneau_est_l_assemblage_de_ses_modules():
    """Le script du panneau est range en modules (panneau/*.js), assembles dans
    l'ordre de PANNEAU_MODULES. Chaque fichier du dossier est dans la liste --
    un module oublie disparaitrait sans bruit -- et chacun s'analyse SEUL :
    une frontiere de module ne coupe donc jamais une fonction en deux."""
    import subprocess
    dossier = os.path.join(DOSSIER_PANNEAU, "app", "panneau")
    assert sorted(os.listdir(dossier)) == sorted(app.PANNEAU_MODULES)
    assert list(app.PANNEAU_MODULES) == sorted(app.PANNEAU_MODULES), "l'ordre suit les numeros"
    for nom in app.PANNEAU_MODULES:
        r = subprocess.run(["node", "--check", os.path.join(dossier, nom)],
                           capture_output=True, text=True)
        assert r.returncode == 0, nom + " : " + r.stderr[:300]
    assert not os.path.exists(os.path.join(DOSSIER_PANNEAU, "app", "panneau.js"))




def test_la_feuille_de_style_du_panneau_est_equilibree():
    """Vecu, pendant la fusion du CSS : la fin d'un commentaire est partie
    avec une regle vide ; le « /* » reste ouvert a englouti la regle suivante
    (le compteur du menu a perdu ses coins arrondis), et aucun autre test ne
    l'a vu. Un commentaire ou une accolade desequilibres font disparaitre des
    regles EN SILENCE : le navigateur ne signale rien."""
    for nom in ("dashboard.html", "login.html", "messages.html"):
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
    police = os.path.join(racine, "app-manager", "app", "polices", "jakarta-latin.woff2")
    assert open(police, "rb").read(4) == b"wOF2"
    assert os.path.exists(os.path.join(racine, "app-manager", "app", "polices", "LICENCE-jakarta.txt"))
    page = _page_panneau("dashboard.html")
    # La roue crantee a cote du compte, le menu en pilule (barre du bas sur
    # un telephone), les reglages en fenetre.
    # Pas de roue crantee : les parametres s'ouvrent depuis le menu du compte.
    assert "reglages-btn" not in page
    assert 'id="acct-parametres" onclick="allerA(\'parametres\')"' in page
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
            assert f"setHub('{cle}','{v}')" in page, (cle, v)
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
    for nom in app.REGLAGES_HUB["couleurs"][1:]:
        r = f':root[data-variante="bento"][data-couleurs="{nom}"]'
        for bloc in (r + "{--accent:", r + ':not([data-theme="light"]):not([data-theme="dark"]){--accent:',
                     r + '[data-theme="dark"]{--accent:'):
            assert bloc in nuancier, bloc
    assert nuancier.count("--sur-accent:") == 3 * (len(app.REGLAGES_HUB["couleurs"]) - 1)
    # « lagon » est la couleur de toujours : pas de bloc a lui.
    assert 'data-couleurs="lagon"' not in theme
    page = _page_panneau("dashboard.html")
    for nom in app.REGLAGES_HUB["couleurs"]:
        assert f"setHub('couleurs','{nom}')" in page, nom





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
                  "apVue('cartes')", "apVue('liste')", 'data-agir="ap-trier"', "aria-sort=",
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
    assert "if(n && n.dataset.changer === 'presentation-fichier')" in page
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
    outils = os.path.join(_racine_depot(), "dev", "video")
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




def test_codelab_video_refuse_un_chemin(tmp_path):
    r, _ = _lancer_codelab(tmp_path, ["video", "../etc"])
    assert r.returncode == 2




def test_les_scripts_video_sont_du_javascript_valide():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node absent")
    for f in ("fabriquer.mjs", "scenario-modele.mjs"):
        r = subprocess.run(["node", "--check", os.path.join(_racine_depot(), "dev", "video", f)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr




def test_l_image_du_poste_a_les_outils_de_la_video():
    dockerfile = open(os.path.join(_racine_depot(), "dev", "Dockerfile"), encoding="utf-8").read()
    assert "ffmpeg" in dockerfile
    assert "playwright install --with-deps chromium" in dockerfile
    assert "piper-tts==" in dockerfile and "fr_FR-siwis-medium.onnx.json" in dockerfile
    assert "COPY dev/video/fabriquer.mjs dev/video/scenario-modele.mjs" in dockerfile
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

@pytest.mark.parametrize("page", ["dashboard.html", "login.html", "messages.html"])
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

@pytest.mark.parametrize("page", ["dashboard.html", "login.html", "messages.html"])
def test_au_doigt_un_bloc_interieur_defile_a_la_place_de_la_page(page):
    html = _page_panneau(page)
    corps = html.split("<body>", 1)[1]
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




def test_a_la_une_prend_les_couleurs_de_son_icone():
    page = _page_panneau("dashboard.html")
    assert "background:var(--une-fond,var(--accent))" in page
    assert "color:var(--une-txt,var(--sur-accent))" in page
    assert ".bw-une:hover{background:var(--une-fond-h,var(--accent-h))}" in page
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

def test_les_parametres_tiennent_en_cinq_onglets_de_meme_largeur():
    page = _page_panneau("dashboard.html")
    onglets = page.split('<div class="fiche-onglets" id="param-onglets">')[1].split("</div>")[0]
    assert onglets.count("<button") == 5
    for p in ("compte", "apparence", "hub", "securite", "email"):
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
    assert page.count('oninput="compterDescription(this)"') == 2




def test_au_doigt_la_carte_touchee_ne_reste_pas_selectionnee():
    page = _page_panneau("dashboard.html")
    bloc = page.split("AU DOIGT, PAS DE SURVOL QUI RESTE.")[1].split("/* LES PASTILLES")[0]
    assert "@media (hover:none){" in bloc and ".card-top:hover{background:none}" in bloc
    assert ".card-top:active{background:var(--surface2)}" in bloc
