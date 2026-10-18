"""
CodeLab -- TESTS : LE DIAGNOSTIC
---------------------------------------------------------------------------
ROLE        les sondes de checks.py, leur hierarchie, la verification
            approfondie, la documentation qui ne ment pas, la separation
            sondes et tests.
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


# ------------------- 5. inscription du projet de diagnostic -------------------
#
# CE QUE CETTE SECTION DISAIT AVANT, ET POURQUOI ELLE DIT L'INVERSE.
#
# L'inscription etait une amorce a jouer UNE FOIS, et la propriete tenue ici
# etait « il ne se reinscrit jamais » : un projet supprime depuis le panneau
# ne devait pas revenir au redemarrage suivant.
#
# Le diagnostic n'est plus un projet parmi d'autres. C'est une application
# PAR DEFAUT : le serveur refuse de la supprimer, de la configurer et de
# l'arreter. Il n'y a donc plus de suppression volontaire a respecter, et la
# garantie qui compte s'est inversee -- sur une installation en service, le
# diagnostic EST LA, inscrit et configure.
#
# Ce n'est pas un confort. Trois chemins menaient a une installation qui n'en
# avait pas, et aucun ne se voyait : le dossier pas encore amorce par dagster
# pendant qu'une autre application etait inscrite, un marqueur pose sans que
# l'inscription reussisse, et toute installation anterieure au diagnostic.




def test_le_diagnostic_est_inscrit_et_configure(tmp_path, monkeypatch):
    """« Deja cree ET configure » : la fiche n'a pas de formulaire.

    Une application par defaut ne se configure pas depuis le panneau. Tout ce
    dont elle a besoin doit donc etre pose a l'inscription -- a commencer par
    la commande de build, sans laquelle le pilote Postgres ne s'installe sur
    ordre de personne.
    """
    racine = _amorcage(tmp_path, monkeypatch)
    assert app.amorcer_diagnostic() == "diagnostic"
    inscrit = app.load()["diagnostic"]
    assert inscrit["path"] == str(racine / "diagnostic")
    assert inscrit["command"] == app.DIAGNOSTIC_COMMANDE
    assert inscrit["build_command"] == app.DIAGNOSTIC_BUILD
    assert inscrit["enabled"] is True
    assert inscrit["titre"] == app.TITRES_PAR_DEFAUT["diagnostic"]
    assert inscrit["description"] == app.DIAGNOSTIC_DESCRIPTION
    assert inscrit["visibility"] == app.VISIBILITE_PRIVEE
    assert app.PORT_MIN <= inscrit["port"] <= app.PORT_MAX




def test_un_diagnostic_efface_revient_au_redemarrage(tmp_path, monkeypatch):
    """L'INVERSE de ce que ce fichier tenait avant, et c'est voulu.

    Le diagnostic ne se supprime pas depuis le panneau -- la route repond
    403. Un registre sans lui n'est donc pas un choix de l'utilisateur : c'est
    un etat casse, et le seul geste utile est de le reparer.
    """
    _amorcage(tmp_path, monkeypatch)
    app.amorcer_diagnostic()
    app.save({})
    assert app.amorcer_diagnostic() == "diagnostic"
    assert "diagnostic" in app.load()




def test_une_installation_deja_en_service_le_recoit_aussi(tmp_path, monkeypatch):
    """Le chemin qui privait une installation entiere de diagnostic.

    L'inscription ne se declenchait que sur un registre VIDE. Une stack qui
    avait declare une application avant que dagster ait depose le projet --
    les deux conteneurs demarrent en parallele -- ne le recevait jamais. Ni
    au redemarrage suivant, ni aucun autre.
    """
    _amorcage(tmp_path, monkeypatch)
    app.save({"mon-site": {"port": 9101}})
    assert app.amorcer_diagnostic() == "diagnostic"
    apres = app.load()
    assert "diagnostic" in apres
    # Et l'application qui etait la n'a pas bouge.
    assert apres["mon-site"]["port"] == 9101




def test_une_inscription_deja_faite_n_est_pas_recreee(tmp_path, monkeypatch):
    """Inscrire n'est pas ecraser : le port attribue ne doit pas changer.

    Une application dont le port change a chaque demarrage du panneau, c'est
    une adresse qu'on ne peut pas noter.
    """
    _amorcage(tmp_path, monkeypatch)
    app.amorcer_diagnostic()
    port = app.load()["diagnostic"]["port"]
    assert app.amorcer_diagnostic() is None
    assert app.load()["diagnostic"]["port"] == port




def test_une_configuration_derivee_est_remise_droite(tmp_path, monkeypatch):
    """Ce que le panneau remet, et ce qu'il laisse.

    Le chemin, la commande et le build ne sont pas configurables pour cette
    application-la : les laisser deriver n'apporte rien et prive du
    diagnostic. Le reste -- ce que l'utilisateur PEUT changer, comme la
    visibilite -- n'est pas touche.
    """
    racine = _amorcage(tmp_path, monkeypatch)
    app.amorcer_diagnostic()
    apps = app.load()
    apps["diagnostic"]["command"] = "sleep 1"
    apps["diagnostic"]["build_command"] = ""
    apps["diagnostic"]["visibility"] = app.VISIBILITE_PUBLIQUE
    app.save(apps)

    app.amorcer_diagnostic()

    remis = app.load()["diagnostic"]
    assert remis["command"] == app.DIAGNOSTIC_COMMANDE
    assert remis["build_command"] == app.DIAGNOSTIC_BUILD
    assert remis["path"] == str(racine / "diagnostic")
    assert remis["visibility"] == app.VISIBILITE_PUBLIQUE




def test_un_projet_pas_encore_amorce_est_retente_au_demarrage_suivant(tmp_path, monkeypatch):
    """app-manager et dagster demarrent en parallele, et c'est dagster qui
    depose le projet dans /workspace : au premier demarrage le dossier peut
    ne pas encore exister. On ne peut pas inscrire un chemin absent -- mais
    renoncer DEFINITIVEMENT, comme avant, privait du projet pour une simple
    question d'ordre de demarrage."""
    racine = _amorcage(tmp_path, monkeypatch, avec_projet=False)
    assert app.amorcer_diagnostic() is None
    assert app.load() == {}

    (racine / "diagnostic").mkdir()
    (racine / "diagnostic" / "app.py").write_text("")
    assert app.amorcer_diagnostic() == "diagnostic"




# ---------- la verification approfondie, en direct ----------

def test_la_verification_annonce_ses_lignes_avant_de_les_jouer():
    """Elle jouait tout avant de repondre : on cliquait, et le navigateur
    restait blanc jusqu'a trois minutes -- le temps de la suite de
    regressions -- sans dire ou il en etait, ni s'il avancait encore."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert "/api/test/<int:indice>" in src, "un test a la fois, sinon rien n'avance"
    # Les lignes sont posees AVANT d'etre jouees, chacune « en attente ».
    assert 'class="st attente"><span>en attente</span>' in src
    # La roue avance quand un test FINIT : pas d'animation qui tourne dans le
    # vide et laisse croire que ca progresse.
    assert "roue(r + 1, liste.length)" in src
    roue = src.split("function roue(fait, total){")[1].split("\n}")[0]
    assert "strokeDashoffset" in roue and "fait / total" in roue




def test_la_verification_se_lance_sur_tout_ou_sur_une_rubrique():
    """« Le proxy ne sert plus cette application » ne demande pas de rejouer
    trois minutes de tests de base : on relance le SUJET qu'on vient de voir
    en echec."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    # Le bouton global n'a pas de sujet, celui d'une rubrique porte sa cle.
    assert 'data-verifier=""' in src, "le bouton global a disparu"
    assert 'data-verifier="{esc(cle)}"' in src, (
        "chaque rubrique doit pouvoir se verifier seule")
    choix = src.split("function choisis(cle, avecSuite){")[1].split("\n}")[0]
    assert "cle === null || t.theme === cle" in choix
    # La suite de regressions reste optionnelle, quelle que soit la rubrique.
    assert "avecSuite || t.theme !== 'code'" in choix




def test_la_verification_se_joue_dans_une_fenetre_posee_des_le_depart():
    """Une fenetre construite en JavaScript au moment du clic n'existe pas
    tant qu'on n'a pas clique : elle ne se teste pas, et elle ne se lit
    pas."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert 'id="voile"' in src and 'aria-modal="true"' in src
    # Elle se ferme de trois facons : la croix, le voile, la touche Echap.
    for geste in ("$('fermer').addEventListener('click', fermer)",
                  "e.target === $('voile')", "e.key === 'Escape'"):
        assert geste in src, geste
    # Mais jamais pendant qu'une verification tourne : fermer ne l'arrete
    # pas, et la rouvrir montrerait une fenetre qui avance toute seule.
    fermer = src.split("function fermer(){")[1].split("\n}")[0]
    assert "if(enCours) return;" in fermer




def test_les_sondes_sont_rangees_par_thematique():
    """Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce
    qu'on cherche. Rangees, ce sont cinq questions distinctes."""
    groupes = sondes_par_theme(run_all())
    assert groupes, "aucune rubrique"
    cles = [cle for cle, _, _, _ in groupes]
    assert "autres" not in cles, (
        "une sonde n'a pas de rubrique : ajoute-la a THEME_DES_SONDES")
    # Chaque sonde apparait une fois et une seule.
    rangees = [nom for _, _, _, sondes in groupes for _, nom, _ in sondes]
    attendues = [nom for _, nom, _ in run_all()]
    assert sorted(rangees) == sorted(attendues)
    # Et chaque test approfondi aussi.
    par_theme = indices_par_rubrique()
    assert "autres" not in par_theme, (
        "un test approfondi n'a pas de rubrique : ajoute-le a THEME_DES_TESTS")
    rangs = sorted(i for indices in par_theme.values() for i in indices)
    assert rangs == list(range(len(noms_des_tests())))




def test_un_indice_de_test_hors_liste_ne_casse_pas_la_page():
    """Le rang vient de l'URL : il se borne, il ne se croit pas. Une
    IndexError rendrait une page d'erreur au milieu d'une verification."""
    import app as diag
    client = diag.app.test_client()
    for mauvais in ("99", "-1"):
        r = client.get(f"/api/test/{mauvais}")
        assert r.status_code in (404, 400), mauvais




def test_les_noms_annonces_sont_ceux_des_resultats():
    """La page annonce ses lignes puis les remplit : une ligne qui change
    d'intitule en cours de route n'est plus la meme ligne."""
    noms = checks_noms()
    assert len(noms) == len(TESTS_APPROFONDIS) + 1
    assert noms[-1] == NOM_SUITE_PANNEAU
    # run_test rend le nom de la LISTE, pas celui que la fonction se donne.
    src = inspect.getsource(run_test)
    assert "return etat, nom, detail" in src




def test_la_page_d_etat_range_ses_sondes_en_rubriques():
    """Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce
    qu'on cherche. Une carte par sujet, son etat en pastille, et son propre
    bouton : on lit la rubrique qui cloche, et on approfondit celle-la."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert "checks.sondes_par_theme(resultats)" in src, (
        "la page doit prendre le regroupement du module, pas s'en inventer un")
    bloc = src.split("def _rubrique(")[1].split("\n\n\n")[0]
    # Chaque rubrique porte son compte : « 4 sur 7 » se lit sans compter les
    # lignes, et la couleur dit tout de suite si l'on doit s'y arreter.
    assert "passees} sur {total}" in bloc
    assert "checks.pire(resultats).classe" in bloc, (
        "la pastille doit prendre la couleur du pire rang de la rubrique : "
        "une rubrique qui ne porte qu'une alerte est orange, pas rouge")
    # Et son bouton, qui ne vaut que pour elle.
    assert 'data-verifier="{esc(cle)}"' in bloc




def test_la_page_d_etat_partage_un_seul_gabarit():
    """Deux gabarits divergent au premier changement, et le diagnostic se
    mettrait a ressembler a deux applications selon la page ouverte."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert src.count("<!doctype") == 1, (
        "un seul gabarit : les autres pages passent par page()")
    # L'ancienne adresse /tests continue de mener a la verification : le lien
    # a circule, il est dans les favoris et dans les journaux.
    assert '@app.get("/tests")' in src
    assert "ouvrir_verification=True" in src




def test_le_diagnostic_suit_le_theme_du_panneau():
    """Il CHARGE le theme, il ne le recopie pas.

    Une palette recopiee dans une deuxieme page est une palette qui
    divergera, et le diagnostic finirait par annoncer une stack saine dans
    des couleurs qui ne sont plus celles de la stack.
    """
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert 'href="/theme.css"' in src, "le diagnostic ne charge pas le theme partage"
    assert "codelab-theme" in src, "le choix clair / sombre n'est pas repris"
    # Il arrive par un COOKIE : le localStorage du panneau appartient a une
    # autre origine, et n'est pas lisible d'ici -- il ne doit pas l'etre.
    assert "document.cookie.match" in src, (
        "sans le cookie, le diagnostic retombe sur le reglage du systeme")
    panneau = _page_panneau("dashboard.html")
    assert "function noterThemePartage(t){" in panneau, (
        "le panneau ne partage pas son choix : personne ne pose le cookie")
    bloc = panneau.split("function setTheme(t){")[1].split("\n}")[0]
    assert "noterThemePartage(t)" in bloc
    # Pose aussi au chargement : une installation en place ne doit pas avoir
    # a refaire son choix pour que les applications le voient.
    assert "noterThemePartage(themeChoisi)" in panneau
    # Aucune couleur en dur ne doit revenir dans sa feuille de style.
    css = src[src.index("CSS = "):src.index('"""', src.index("CSS = ") + 10)]
    assert "#" not in css.replace("#{", ""), (
        "une couleur en dur est revenue dans le CSS du diagnostic")




def test_le_theme_traverse_la_separation_des_origines():
    """C'etait LA cause du « tres mauvais rendu » du diagnostic.

    La garde qui separe les deux ports ne laissait passer que le proxy et la
    sonde de sante. Une application hebergee qui demandait "/theme.css" --
    le diagnostic le fait a chaque page -- recevait une page 404 a la place
    de la feuille, et s'affichait alors SANS UN SEUL JETON : fond blanc,
    texte brut, pastilles invisibles. Rien dans son propre CSS n'etait en
    cause, et c'est bien pour cela que le defaut a tenu.

    Les deux routes sont deja publiques sur le port du panneau -- la page de
    connexion en a besoin avant toute session. Les ouvrir ici ne revele donc
    rien de plus : des couleurs, et un fichier de police.
    """
    if app is None:
        pytest.skip("panneau absent")
    ancien = app._origines_separees["actif"]
    ancien_apps = app.APPS_PORT
    app._origines_separees["actif"] = True
    app.APPS_PORT = 9002
    try:
        client = app.flask_app.test_client()
        for chemin, type_attendu in (("/theme.css", "text/css"),
                                     ("/polices/manrope-latin.woff2", "font/woff2")):
            r = client.get(chemin, environ_overrides={"SERVER_PORT": "9002"})
            assert r.status_code == 200, f"{chemin} ne passe pas la garde"
            assert type_attendu in r.headers["Content-Type"], chemin
        # Et le panneau, lui, ne s'affiche toujours pas sur ce port : c'est
        # tout l'interet de la separation, et elle ne doit pas s'ouvrir avec.
        for chemin in ("/", "/api/mes-apps", "/login"):
            r = client.get(chemin, environ_overrides={"SERVER_PORT": "9002"})
            assert r.status_code == 404, f"{chemin} repond sur le port des apps"
    finally:
        app._origines_separees["actif"] = ancien
        app.APPS_PORT = ancien_apps




def test_le_diagnostic_sert_son_theme_lui_meme():
    """Le second chemin, celui qui ne depend d'aucune garde d'origine.

    La page charge le theme par deux liens vers le MEME fichier : celui du
    panneau, a la racine de l'origine des applications, et un lien RELATIF
    servi par cette route-ci. Il faudrait que les deux tombent pour qu'une
    page nue revienne.

    Le fichier n'est pas recopie : il est lu la ou il est. Une palette
    recopiee dans une deuxieme page est une palette qui divergera.
    """
    import app as diag
    if not diag.fichier_du_theme():
        pytest.skip("le theme du panneau n'est pas sur ce disque")
    client = diag.app.test_client()
    r = client.get("/theme.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["Content-Type"]
    # C'est bien la feuille du panneau, avec ses jetons.
    for jeton in (b"--accent", b"--ok", b"--warn", b"--err", b"--surface"):
        assert jeton in r.data, jeton
    # La police que cette feuille reclame, prise a cote d'elle.
    assert client.get("/polices/manrope-latin.woff2").status_code == 200
    # Et rien d'autre : sans liste blanche, ce chemin deviendrait une lecture
    # de fichier arbitraire.
    for mauvais in ("theme.css", "LICENCE-manrope.txt", "manrope-latin.woff2.bak"):
        assert client.get("/polices/" + mauvais).status_code == 404, mauvais




def test_la_page_d_accueil_est_un_cockpit():
    """Elle repond d'abord a « est-ce que tout va bien ? ».

    Un anneau pour la part de sondes vertes, le mot d'etat, les quatre rangs
    comptes, et le bouton qui lance tout. Une liste de seize lignes ne
    repond a cette question qu'apres l'avoir entierement lue.
    """
    import app as diag
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    # Le denominateur de l'anneau ecarte les sondes SANS OBJET : un anneau
    # qui n'arrive jamais au bout parce que trois sondes ne s'appliquent pas
    # ici fait chercher une panne qui n'existe pas.
    bloc = src.split("def _anneau(")[1].split("\n\n\n")[0]
    assert "verts / applicables" in bloc
    assert "len(resultats) - len(sans_objet)" in src

    html = diag.app.test_client().get("/").data.decode()
    for morceau in ('class="cockpit', 'class="anneau', 'class="etat-mot',
                    'class="compteur', 'data-verifier=""', 'class="grille"'):
        assert morceau in html, morceau
    # Les quatre rangs, nommes. Un rang a zero reste affiche : « aucune
    # erreur » est une information.
    for libelle in ("erreurs", "attention", "au vert", "sans objet"):
        assert f"<span>{libelle}</span>" in html, libelle
    # Le theme arrive par les deux chemins.
    assert 'href="/theme.css"' in html and 'href="theme.css"' in html
    # Et la barre du panneau coiffe la page, sans trait vertical a cote de
    # la marque.
    assert 'class="barre"' in html and "CodeLab" in html




def test_la_page_suit_la_forme_de_cleanmymac():
    """« Mettre a jour l'application Diagnostic pour qu'elle reprenne le style
    de CleanMyMac » : une colonne de rubriques a gauche, chacune marquee de
    son pire etat, et une seule vue a la fois au centre.

    Chaque entree de la colonne doit designer une vue qui existe : une
    entree sans vue ne montrerait rien au clic.
    """
    import re
    import app as diag
    html = diag.app.test_client().get("/").data.decode()
    assert 'class="cote verre"' in html and 'class="scene"' in html
    assert 'class="analyser"' in html and 'data-verifier=""' in html
    vues = re.findall(r'data-vue="([a-z]+)"', html)
    assert vues[0] == "accueil" and len(vues) > 1, vues
    for cle in vues:
        assert f'data-vue-de="{cle}"' in html, f"la vue {cle} n'existe pas"
    # Le point de chaque rubrique porte un rang connu, jamais une couleur.
    points = re.findall(r'class="point ([a-z]+)"', html)
    assert len(points) == len(vues) - 1
    assert set(points) <= {"ok", "warn", "ko", "neutre"}, points




def test_le_detail_d_une_sonde_n_est_plus_en_chasse_fixe():
    """Une phrase entiere en monospace se lit deux fois moins vite.

    Et toutes les lignes de cette page en etaient : le detail d'une sonde
    est une PHRASE, pas une valeur. La chasse fixe ne reste que pour ce qui
    est vraiment du code -- un chemin, un port, un extrait de configuration,
    dans une balise <code>.
    """
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    css = src[src.index("CSS = "):src.index('"""', src.index("CSS = ") + 10)]
    detail = css.split(".det{")[1].split("}")[0]
    assert "--mono" not in detail, (
        "le detail des sondes est revenu en chasse fixe")
    assert "--mono" in css.split("code{")[1].split("}")[0], (
        "plus rien n'est en chasse fixe, pas meme un chemin")




# ---------- 27. le projet de diagnostic ne se supprime pas ----------
#
# C'est l'etat des lieux de l'installation : il dit si les services se
# parlent, si le panneau est ferme, si les applications sont isolees. Une
# stack sans lui n'a plus aucun moyen de se controler elle-meme -- et comme
# son inscription n'a lieu qu'UNE fois (marqueur), le supprimer le ferait
# disparaitre pour de bon, pas jusqu'au prochain redemarrage.

def test_le_diagnostic_ne_se_supprime_pas(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    app.save({app.DIAGNOSTIC_NOM: {"path": "/w/d", "command": "x", "port": 9100},
              "autre": {"path": "/w/a", "command": "x", "port": 9101}})

    r = c.delete("/api/app/" + app.DIAGNOSTIC_NOM)
    assert r.status_code == 403
    assert app.DIAGNOSTIC_NOM in app.load(), "le diagnostic a ete supprime"

    # Temoin : une autre application se supprime normalement -- sinon ce test
    # passerait au vert parce que la suppression est cassee pour tout le monde.
    assert c.delete("/api/app/autre").status_code == 200
    assert "autre" not in app.load()




def test_le_diagnostic_voit_l_ensemble_du_workspace(monkeypatch):
    """L'observateur est la seule application non isolee, et c'est sa raison
    d'etre : isole, il ne verrait ni /workspace/definitions.py ni les autres
    projets, et rapporterait une stack en panne alors que tout va bien."""
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
    # Verdict fixe : ce test porte sur le branchement, pas sur le
    # noyau de la machine qui lance la suite.
    app._isolement.update(verdict=True, raison="")
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    argv, _ = app.commande_isolee(app.DIAGNOSTIC_NOM, "/workspace/diagnostic", "x", {})
    assert argv[0] == "bash", "le diagnostic est isole : il deviendrait aveugle"
    # Temoin : n'importe quelle autre application, elle, est bien isolee.
    argv, _ = app.commande_isolee("autre", "/workspace/autre", "x", {})
    assert argv[0] == "unshare"




# ---------- 28. la hierarchie des sondes de diagnostic ----------
#
# Releve dans les journaux Dagster du 15 septembre : six sondes en ECHEC sur
# dix-sept, un run rouge, un mail -- et la lecture du detail donnait ceci.
#
#   credentials.env          le secret n'etait pas arrive dans le conteneur
#   Postgres                 consequence directe du precedent
#   panneau ferme            127.0.0.1:9001 depuis codelab-dagster : personne
#   origine des applications 127.0.0.1:9002, meme erreur d'adresse
#   isolation                refus du noyau de l'HOTE, rien a corriger ici
#   espace disque            87 %, 2.1 Go libres -- des jours de marge
#
# Deux vrais problemes, deux mauvaises adresses et deux points a regarder
# quand on passe : tous au meme rang, avec le meme rouge et le meme mail.
# C'est ainsi qu'on cesse de lire ses alertes, et qu'on rate la prochaine
# vraie panne.

def test_chaque_sonde_declare_son_rang(monkeypatch):
    """La table est la hierarchie : une sonde qui n'y figure pas peut faire
    tomber un run sans que personne ne l'ait decide."""
    # Les sondes reseau sont jouees pour de vrai, elles echouent ici, et
    # c'est tres bien : ce qu'on verifie est le NOM de chaque ligne.
    noms = [nom for _etat, nom, _detail in run_all()]
    assert len(noms) == len(set(noms)), "deux sondes portent le meme nom"
    manquantes = [n for n in noms if n not in SEVERITE_MAX]
    assert not manquantes, (
        f"sondes absentes de SEVERITE_MAX : {manquantes} -- ajoute-les, avec "
        f"le rang que tu leur donnes")
    en_trop = [n for n in SEVERITE_MAX if n not in noms]
    assert not en_trop, f"SEVERITE_MAX parle de sondes disparues : {en_trop}"




def test_une_sonde_de_confort_ne_peut_pas_faire_tomber_un_run():
    """Le filet, teste tel quel : meme si une sonde de rang 2 renvoyait un
    echec -- demain, par inadvertance -- elle ne reveille personne."""
    etat, _, _ = _hierarchiser((Etat.ECHEC, "isolation des applications", "x"))
    assert etat is Etat.ALERTE
    # Et le filet ne relache rien de ce qui compte.
    etat, _, _ = _hierarchiser((Etat.ECHEC, "Postgres", "x"))
    assert etat is Etat.ECHEC
    # Une sonde inconnue reste au rang le plus severe : on ne se tait pas sur
    # ce qu'on n'a pas encore classe.
    etat, _, _ = _hierarchiser((Etat.ECHEC, "sonde ajoutee demain", "x"))
    assert etat is Etat.ECHEC




def test_le_verdict_global_est_le_pire_des_rangs():
    assert pire([(Etat.OK, "a", ""), (Etat.ALERTE, "b", "")]) is Etat.ALERTE
    assert pire([(Etat.ALERTE, "a", ""), (Etat.ECHEC, "b", "")]) is Etat.ECHEC
    assert pire([(Etat.OK, "a", ""), (Etat.SANS_OBJET, "b", "")]) is Etat.SANS_OBJET
    assert pire([]) is Etat.OK
    # ECHEC est faux, les trois autres sont vrais : tout le code qui lisait
    # "ok" continue de le lire, avec le bon sens.
    assert not Etat.ECHEC
    assert Etat.ALERTE and Etat.SANS_OBJET and Etat.OK


# ---------- 28 bis. le secret Postgres, la ou l'entrypoint l'a pose ----------
#
# LA panne de ces journaux, et la seule qui demandait un geste. Dans
# codelab-dagster, credentials.env est en 0600 root et Dagster tourne sous
# l'uid 1002 : le fichier est illisible, c'est prevu, et l'entrypoint exporte
# la valeur en DAGSTER_PG_PASSWORD avant d'abandonner root. checks.py ne
# lisait que POSTGRES_PASSWORD : le secret etait dans l'environnement du
# processus, a portee de main, et la sonde repondait "fe_sendauth: no
# password supplied" quatre fois par heure.




def test_le_secret_est_lu_la_ou_l_entrypoint_l_a_pose(config_isolee, monkeypatch):
    monkeypatch.setenv("DAGSTER_PG_PASSWORD", "mot-de-passe-dagster")
    assert mot_de_passe_pg() == ("mot-de-passe-dagster", "DAGSTER_PG_PASSWORD")
    assert pg_settings()["password"] == "mot-de-passe-dagster"

    etat, _nom, detail = check_config()
    assert etat is Etat.OK, detail
    # Et la sonde dit PAR OU il est arrive : "illisible ici" et "personne ne
    # l'a transmis" se depannent de deux facons opposees.
    assert "DAGSTER_PG_PASSWORD" in detail




def test_une_session_ssh_lit_le_secret_de_son_profil(config_isolee, monkeypatch):
    """Meme mecanique dans codelab-dev : l'entrypoint pre-remplit PGPASSWORD
    dans le profil, et le fichier reste illisible sous l'uid 1000."""
    monkeypatch.setenv("PGPASSWORD", "mot-de-passe-ssh")
    assert pg_settings()["password"] == "mot-de-passe-ssh"




def test_le_fichier_reste_prioritaire(config_isolee, monkeypatch):
    """Une valeur perimee dans l'environnement ne doit pas gagner contre le
    fichier partage, qui est la source de verite."""
    config_isolee.write_text("POSTGRES_PASSWORD=celui-du-fichier\n")
    monkeypatch.setenv("DAGSTER_PG_PASSWORD", "un-autre")
    assert pg_settings()["password"] == "celui-du-fichier"
    _etat, _nom, detail = check_config()
    assert "lu dans" in detail




def test_sans_aucun_secret_la_sonde_nomme_les_trois_chemins(config_isolee):
    etat, _nom, detail = check_config()
    assert etat is Etat.ECHEC
    for cle in CLES_MOT_DE_PASSE_PG:
        assert cle in detail, "la sonde ne dit pas ou le secret aurait pu passer"


# ---------- 28 ter. deux paliers pour le disque ----------




def test_un_disque_qui_se_remplit_est_une_alerte(tmp_path, monkeypatch):
    """87 %, 2.1 Go libres : exactement la ligne du journal du 15 septembre.
    Il reste des jours -- cela se traite dans la journee, pas la nuit."""
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path))
    _faux_disque(monkeypatch, 87, 2.1)
    etat, _nom, detail = check_espace_disque()
    assert etat is Etat.ALERTE, detail
    assert "87 %" in detail




def test_un_disque_presque_plein_est_un_echec(tmp_path, monkeypatch):
    """La prochaine ecriture de Postgres echoue, et son message ne parlera
    jamais d'espace disque."""
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path))
    _faux_disque(monkeypatch, 97, 0.4)
    etat, _nom, detail = check_espace_disque()
    assert etat is Etat.ECHEC, detail
    assert SEUIL_DISQUE_CRITIQUE == 95 and SEUIL_LIBRE_CRITIQUE_GO == 1




def test_un_grand_disque_bien_rempli_ne_dit_rien(tmp_path, monkeypatch):
    """89 % de 250 Go laisse 28 Go : des mois de marge, aucune alerte."""
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path))
    _faux_disque(monkeypatch, 89, 28)
    etat, _nom, _detail = check_espace_disque()
    assert etat is Etat.OK




# ---------- 28 quater. ce qui ne se regarde pas d'ici ----------
#
# L'etat du panneau n'est monte que dans codelab-app-manager. Depuis Dagster,
# quatre sondes y trouvaient un panneau vide -- zero application, zero compte,
# rien d'expose -- et affichaient un vert rassurant sur une question qu'elles
# n'avaient jamais pu poser. Un faux OK est pire qu'une case vide : il fait
# cesser de chercher.

def test_les_sondes_du_panneau_ne_repondent_pas_depuis_ailleurs(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path / "jamais-monte"))
    for fonction in (check_applications, check_surface_exposee,
                     check_provenance, check_exposition):
        etat, nom, detail = fonction()
        assert etat is Etat.SANS_OBJET, f"{nom} conclut sans rien avoir lu"
        assert "n'est pas monté dans ce conteneur" in detail




def test_l_etat_du_panneau_monte_les_sondes_repondent(tmp_path, monkeypatch):
    """Temoin : le meme code, avec le dossier sous les yeux, conclut bien --
    sinon le test ci-dessus passerait au vert parce que tout est casse."""
    etat_dir = tmp_path / "etat"
    etat_dir.mkdir()
    monkeypatch.setenv("APP_MANAGER_STATE", str(etat_dir))
    for fonction in (check_applications, check_surface_exposee, check_provenance):
        etat, nom, _detail = fonction()
        assert etat is Etat.OK, nom




# ---------- 28 quinquies. le panneau n'est pas toujours sur 127.0.0.1 ----------

def test_la_sonde_cherche_le_panneau_par_son_nom_de_service(monkeypatch):
    monkeypatch.delenv("CODELAB_PANNEAU_HOTE", raising=False)
    assert _hotes_panneau() == ("127.0.0.1", "codelab-app-manager")
    monkeypatch.setenv("CODELAB_PANNEAU_HOTE", "autre-hote")
    assert _hotes_panneau() == ("autre-hote",)




def test_un_panneau_ferme_est_un_ok(faux_panneau):
    etat, _nom, detail = check_panneau_ferme()
    assert etat is Etat.OK, detail




def test_une_route_d_administration_ouverte_est_un_echec(faux_panneau):
    """La seule chose qui justifie un run rouge ici : une breche CONSTATEE."""
    faux_panneau["/api/utilisateurs"] = 200
    etat, _nom, detail = check_panneau_ferme()
    assert etat is Etat.ECHEC
    assert "/api/utilisateurs" in detail




def test_un_panneau_injoignable_est_une_alerte(monkeypatch):
    """Une absence de preuve n'est pas une preuve. C'est ce que les journaux
    du 15 septembre montraient : deux ECHEC pour une adresse mal choisie."""
    monkeypatch.setenv("CODELAB_PANNEAU_HOTE", "127.0.0.1")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        libre = s.getsockname()[1]      # un port ou plus personne n'ecoute
    monkeypatch.setenv("MANAGER_PORT", str(libre))
    monkeypatch.setenv("APP_MANAGER_APPS_PORT", str(libre))
    for fonction in (check_panneau_ferme, check_origine_applications):
        etat, nom, _detail = fonction()
        assert etat is Etat.ALERTE, nom
    # Le panneau, lui, est bien declare injoignable : la panne est dite une
    # fois, par la sonde dont c'est le travail.
    etat, _nom, detail = check_panneau_joignable()
    assert etat is Etat.ECHEC and "docker logs" in detail




def test_le_panneau_joignable_par_son_nom_de_service(faux_panneau):
    etat, _nom, detail = check_panneau_joignable()
    assert etat is Etat.OK, detail




# ---------- 28 sexies. l'isolement refuse n'est plus une panne ----------

def test_isoler_a_zero_est_un_choix_assume(monkeypatch):
    """La sonde promet « pour assumer le choix et faire taire cette sonde :
    APP_MANAGER_ISOLER=0 ». Elle tenait si peu parole qu'elle continuait a
    marquer l'installation en faute une fois la variable posee."""
    monkeypatch.setenv("APP_MANAGER_ISOLER", "0")
    etat, _nom, detail = check_isolation()
    assert etat is Etat.OK
    assert "volontairement" in detail


# ---------- 28 septies. l'asset Dagster, et ce qui fait tomber un run ----------
#
# C'est la decision qui compte : le capteur alerte_mail_echec se declenche sur
# un run EN ECHEC. Ce qui fait echouer le run fait donc partir le mail -- et
# un mail toutes les heures pour un disque a 87 % est un mail qu'on
# apprend a ne plus ouvrir.
#
# definitions.py importe dagster, absent de cette image (et de celle du
# panneau). On le remplace par un module d'emprunt : l'asset n'utilise aucune
# API de Dagster pour decider, seulement les rangs -- c'est precisement ce
# qu'on veut verifier.




def test_une_alerte_ne_fait_pas_echouer_le_run(asset_diagnostic, monkeypatch):
    """LE reglage demande : le disque a 87 % et l'isolement refuse par l'hote
    se journalisent en warning, et le run reste vert."""
    _sondes(monkeypatch, [
        (Etat.OK, "Postgres", "connecte"),
        (Etat.ALERTE, "espace disque", "/workspace : 87 % occupe, 2.1 Go libres"),
        (Etat.ALERTE, "isolation des applications", "le noyau refuse"),
        (Etat.SANS_OBJET, "surface exposée", "pas monté ici"),
    ])
    contexte = _ContexteDeTest()

    resultat = asset_diagnostic.diagnostic_codelab(contexte)

    assert "alertes" in resultat
    # Chaque rang dans le bon canal : c'est ce que lit la page des runs. On
    # vise la ligne de la SONDE ("nom -- detail"), pas le resume final, qui
    # les cite toutes.
    assert contexte.niveaux("espace disque -- ") == ["warning"]
    assert contexte.niveaux("Postgres -- ") == ["info"]
    assert contexte.niveaux("surface exposée -- ") == ["info"]
    assert contexte.metadonnees["critiques"] == 0
    assert contexte.metadonnees["alertes"] == 2
    assert contexte.metadonnees["sans objet"] == 1
    # Et le battement de coeur porte les alertes : la page du panneau les
    # relit sans avoir a rejouer les sondes.
    assert "alertes :" in asset_diagnostic.ecrit["detail"]
    assert asset_diagnostic.ecrit["fermee"] is True




def test_une_sonde_critique_fait_bien_echouer_le_run(asset_diagnostic, monkeypatch):
    """Temoin : sans lui, le test precedent passerait au vert parce que plus
    rien ne fait jamais echouer quoi que ce soit."""
    _sondes(monkeypatch, [
        (Etat.ECHEC, "Postgres", "connexion refusee"),
        (Etat.ALERTE, "espace disque", "87 %"),
    ])
    contexte = _ContexteDeTest()
    with pytest.raises(RuntimeError) as leve:
        asset_diagnostic.diagnostic_codelab(contexte)
    assert "Postgres" in str(leve.value)
    # L'alerte reste citee, mais apres : elle n'est pas la raison de l'echec.
    assert "espace disque" in str(leve.value)
    assert str(leve.value).index("Postgres") < str(leve.value).index("espace disque")
    assert contexte.niveaux("Postgres -- ") == ["error"]




def test_la_base_injoignable_ne_sort_plus_en_trace_nue(asset_diagnostic, monkeypatch):
    """L'exception de psycopg2 remplacait tout : quarante lignes de pile, et
    la cause -- dite deux lignes plus haut par la sonde credentials.env --
    disparaissait dessous."""
    _sondes(monkeypatch, [(Etat.OK, "Postgres", "connecte")])

    class _Refus(Exception):
        pass

    def _refuse(*_a, **_k):
        raise _Refus("no password supplied")

    monkeypatch.setattr(checks, "connect_pg", _refuse)
    contexte = _ContexteDeTest()
    with pytest.raises(RuntimeError) as leve:
        asset_diagnostic.diagnostic_codelab(contexte)

    assert "battement de coeur" in str(leve.value)
    # Chainee, pas perdue : la trace d'origine reste consultable dessous.
    assert isinstance(leve.value.__cause__, _Refus)
    # Et les sondes ont ete journalisees AVANT : c'est la ou se lit la cause.
    assert contexte.niveaux("Postgres -- ") == ["info"]




# ---------- 27. ce qui ne s'applique pas ----------------------------------
#
# Une sonde qui ne concerne pas une installation ne doit pas etre rouge :
# il n'y a rien a reparer. « Sans objet » est un troisieme etat, et il doit se propager
# jusqu'au compte affiche.

def test_une_sonde_sans_objet_ne_compte_ni_au_numerateur_ni_au_denominateur():
    """« 5 sur 11 » sur une installation ou tout va bien inquiete pour rien.

    L'anneau du cockpit et la pastille de chaque rubrique doivent raconter la
    meme chose : six sondes qui ne s'appliquent pas ici ne sont pas six
    sondes en attente.
    """
    import app as diag
    resultats = [
        (Etat.OK, "a", ""),
        (Etat.OK, "b", ""),
        (Etat.SANS_OBJET, "c", ""),
        (Etat.SANS_OBJET, "d", ""),
    ]
    html = diag._rubrique("services", "Services", "", resultats)
    assert "2 sur 2" in html, html
    # Et une rubrique entierement sans objet le dit avec des mots plutot que
    # d'afficher « 0 sur 0 ».
    html = diag._rubrique("services", "Services", "",
                          [(Etat.SANS_OBJET, "c", "")])
    assert "sans objet" in html
    assert "0 sur 0" not in html




def test_le_titre_du_cockpit_compte_les_memes_sondes_que_la_pastille():
    """« 8 vérifications en échec » au-dessus d'une pastille qui en montrait
    sept : le premier reflexe est de chercher la huitieme.

    L'erreur d'ecriture en base est deja portee par la sonde Postgres. La
    compter une seconde fois dans le titre faisait diverger les deux nombres
    des qu'elle survenait -- c'est-a-dire exactement quand on lit la page.
    """
    import app as diag
    source = (Path(diag.__file__).resolve()).read_text(encoding="utf-8")
    bloc = source.split("if critiques or erreur_db:")[1].split("elif alertes:")[0]
    assert "combien = len(critiques) or 1" in bloc
    assert "len(critiques) + (1 if erreur_db else 0)" not in bloc




def test_la_sonde_de_port_garde_un_cache_par_service(monkeypatch, tmp_path):
    """Un cache unique pour deux services rendait la reponse de l'un pour
    l'autre -- et le hub interroge les deux a chaque rafraichissement."""
    m = _charger_app_manager(monkeypatch, tmp_path)
    m._sondes_de_port.clear()
    appels = []

    class FauxSocket:
        def settimeout(self, _):
            pass

        def connect_ex(self, cible):
            appels.append(cible)
            return 0 if cible[1] == 3001 else 1

        def close(self):
            pass

    monkeypatch.setattr(m.socket, "socket", lambda *a, **k: FauxSocket())
    assert m._port_repond("a", 3001) is True
    assert m._port_repond("b", 3000) is False
    # Chacun son entree : deux appels, deux reponses distinctes.
    assert appels == [("a", 3001), ("b", 3000)]
    # Et le cache tient : le second passage ne rouvre pas de connexion.
    assert m._port_repond("a", 3001) is True
    assert len(appels) == 2




def test_les_graphiques_ecartent_les_services_que_le_panneau_ne_mesure_pas():
    """Le panneau n'a pas le pid d'un service externe : il ne mesure rien.

    Une barre a 0.0 % a cote des applications qu'il mesure vraiment se lit
    comme « ce service ne consomme rien ». C'est faux -- on ne sait pas.
    """
    page = _page_panneau("dashboard.html")
    bloc = page.split("function barChart(")[1].split("\n}")[0]
    assert "!a.externe" in bloc, (
        "les graphiques CPU et mémoire doivent ecarter les services externes")


# ---------- 37. la documentation ne ment pas ------------------------------
#
# Le depot tient sa documentation pour une source, pas pour un commentaire :
# les README expliquent POURQUOI chaque chose est ainsi, et quelqu'un les lit
# avant de toucher au code. Une documentation perimee est donc pire qu'une
# documentation absente -- elle fait prendre une decision fausse en toute
# confiance.
#
# Vecu a l'envers : le retrait de l'interface de chat a laisse dans
# un README une section entiere decrivant son portier, son reseau ferme
# et le secret partage -- trois mecanismes qui n'existaient plus. Le code
# etait juste, les tests verts, et la documentation racontait l'installation
# d'avant.




def test_le_readme_racine_liste_les_services_qui_existent():
    """Le tableau des services, confronte au compose.

    Un service ajoute sans sa ligne se decouvre en lisant le compose ; un
    service retire dont la ligne reste envoie chercher un conteneur qui
    n'existe pas.
    """
    texte = _readme_racine().lower()
    services = set(_services_du_compose(_composes()[0]))
    # Les noms tels qu'ils se lisent dans le tableau, sans le prefixe.
    for service in services:
        court = service.replace("codelab-", "")
        assert court in texte, (
            f"le service {service} n'apparait nulle part dans README.md")
    # Et l'inverse : rien qui ne tourne plus.
    for disparu in ("open-webui", "openwebui", "aichat"):
        for ligne in _readme_racine().splitlines():
            if ligne.startswith("|") and disparu in ligne.lower():
                raise AssertionError(
                    f"README.md decrit encore « {disparu} » comme un service")




def test_aucun_readme_ne_decrit_un_composant_retire():
    """Les mecanismes disparus ne doivent plus etre expliques au present.

    On cherche ce qui designe sans ambiguite l'ancienne installation. Les
    passages qui RACONTENT le retrait sont legitimes et restent : c'est la
    difference entre « l'interface vit sur un reseau ferme » et « l'interface
    a ete retiree ».
    """
    racine = _racine_depot()
    if not os.path.exists(os.path.join(racine, "README.md")):
        pytest.skip("depot complet absent de cette image")
    interdits = ("codelab-open-webui", "X-OpenWebUI-User-Id",
                 "CODELAB_OPENWEBUI_KEY", "/api/sso-check")
    fautes = []
    for dossier, _, fichiers in os.walk(racine):
        if ".git" in dossier:
            continue
        for nom in fichiers:
            if not nom.endswith(".md"):
                continue
            chemin = os.path.join(dossier, nom)
            texte = open(chemin, encoding="utf-8", errors="replace").read()
            for motif in interdits:
                if motif in texte:
                    fautes.append(f"{os.path.relpath(chemin, racine)} : {motif}")
    assert not fautes, "documentation perimee :\n  " + "\n  ".join(fautes)




def test_aucun_plafond_ne_descend_sous_le_plancher_de_demarrage():
    """Un plafond trop BAS casse le demarrage, et pas seulement la marche.

    Vecu : le plafond du proxy Dagster est passe a 96 Mo par souci
    d'economie, et Docker a refuse de CREER le conteneur -- « Minimum memory
    limit can not be less than memory reservation limit ». Toute la stack
    s'arretait la, sur 32 Mo economises.

    LA CAUSE, MESUREE. « docker compose config » sur la machine de
    reference montre ce que CasaOS/ZimaOS ajoute en recopiant le compose --
    a CHAQUE service, la meme valeur, absente de nos deux fichiers :

        deploy.resources.reservations.memory: "268435456"   (256 Mio)

    Docker refuse alors de creer un conteneur dont le plafond est sous cette
    reservation. Le proxy, plafonne a 96 Mo, etait le seul en dessous.

    Le plancher vaut donc pour TOUS les services, pas seulement le proxy :
    la reservation s'applique a chaque conteneur, et les autres passaient
    seulement parce qu'ils etaient deja au-dessus. Le jour ou quelqu'un
    economise 100 Mo sur n'importe lequel, c'est la stack entiere qui ne
    demarre plus -- et le message de Docker ne nomme pas le coupable.

    Les tests voisins tiennent le PLAFOND -- qu'aucun service ne prenne plus
    du tiers de la machine. Celui-ci tient le PLANCHER, l'autre facon de se
    tromper, dont rien ne protegeait.
    """
    for chemin in _composes():
        for nom, reglages in _services_du_compose(chemin).items():
            brut = str(reglages.get("mem_limit", reglages.get("deploy_memory")))
            mo = int(brut[:-1]) * 1024 if brut.endswith("g") else int(brut.rstrip("m"))
            assert mo >= RESERVATION_HOTE_MO, (
                f"{os.path.basename(chemin)} : {nom} est plafonne a {mo} Mo, "
                f"sous la reservation de {RESERVATION_HOTE_MO} Mio que l'hote "
                f"ajoute -- Docker refusera de creer ce conteneur")




def test_aucun_plafond_ne_depasse_la_machine_visee():
    """Un plafond au-dessus de la RAM disponible n'est pas une securite large.

    Vecu a l'envers : les valeurs visaient 8 Go et le panneau avait 4 Go de
    plafond. Sur un serveur de 4 Go, ce plafond ne bornait plus rien -- ce
    seul service pouvait emporter la machine, ce que ces lignes existent
    precisement pour empecher.

    La regle : aucun service ne prend a lui seul plus du TIERS de la machine.
    Il en reste alors assez pour que le panneau reponde et qu'on puisse
    arreter le fautif depuis le panneau -- ce qui etait impossible avant que
    ces limites existent.
    """
    maximum = RAM_CIBLE_MO // 3
    def en_mo(v):
        v = str(v)
        return int(v[:-1]) * 1024 if v.endswith("g") else int(v.rstrip("m"))
    for chemin in _composes():
        for nom, reglages in _services_du_compose(chemin).items():
            brut = reglages.get("mem_limit", reglages.get("deploy_memory"))
            assert brut, (chemin, nom)
            assert en_mo(brut) <= maximum, (
                f"{nom} : plafond de {en_mo(brut)} Mo, au-dela du tiers de la "
                f"machine visee ({maximum} Mo pour {RAM_CIBLE_MO} Mo)")




def test_aucun_plafond_processeur_ne_depasse_la_machine_visee():
    """Meme raisonnement pour le processeur.

    Un plafond de 2.0 sur un serveur bi-coeur ne limite rien : il autorise
    un service a prendre les deux coeurs, donc a rendre la machine sourde.
    Aucun ne depasse les trois quarts du total.
    """
    maximum = COEURS_CIBLE * 0.75
    for chemin in _composes():
        for nom, reglages in _services_du_compose(chemin).items():
            brut = reglages.get("cpus", reglages.get("deploy_cpus"))
            assert brut, (chemin, nom)
            part = float(str(brut).strip('"'))
            assert part <= maximum, (
                f"{nom} : {part} cpus, au-dela des trois quarts des "
                f"{COEURS_CIBLE} coeurs vises")




def test_le_compose_annonce_la_meme_cible_que_les_tests():
    """Le commentaire et le test doivent dire le meme chiffre.

    Un commentaire qui annonce 8 Go au-dessus de valeurs calculees pour 4 Go
    ferait remonter les plafonds a la premiere relecture, en toute bonne foi.

    Seul docker-compose.yml est concerne : c'est LUI qu'on lit et qu'on
    modifie. Son jumeau CasaOS ne porte aucun commentaire, pour la raison
    que dit le test suivant -- et les plafonds eux-memes y sont verifies
    chiffre par chiffre par les tests ci-dessus, pas par une phrase.
    """
    chemin = [c for c in _composes()
              if os.path.basename(c) == "docker-compose.yml"][0]
    texte = open(chemin, encoding="utf-8").read()
    assert f"CIBLE : {RAM_CIBLE_MO // 1024} Go et {COEURS_CIBLE} coeurs" in texte, (
        f"docker-compose.yml n'annonce pas la cible "
        f"{RAM_CIBLE_MO // 1024} Go / {COEURS_CIBLE} coeurs")




def test_le_compose_casaos_ne_porte_aucun_commentaire():
    """Ce fichier-la se colle dans une zone de texte, il ne se lit pas.

    La meme explication ecrite a deux endroits finit par diverger, et c'est
    la copie qu'on ne lit jamais qui se met a mentir. Ce jumeau ne porte donc
    que les valeurs ; tout ce qu'il y a a dire est dans docker-compose.yml,
    commente ligne a ligne, et dans les README.

    Le test tient la contrainte dans le sens ou elle se perd : on ajoute un
    commentaire par reflexe, on ne l'enleve jamais par hasard.
    """
    chemin = [c for c in _composes()
              if os.path.basename(c) == "docker-compose-casaos.yml"][0]
    fautives = [(n, l) for n, l in enumerate(
        open(chemin, encoding="utf-8").read().split("\n"), 1) if "#" in l]
    assert not fautives, ("docker-compose-casaos.yml porte un commentaire : "
                          + "; ".join(f"ligne {n} : {l.strip()}" for n, l in fautives))




# ---------- 38. les sondes et les tests restent separes ------------------
#
# Ce decoupage a une raison de production, pas de confort : checks.py est
# importe par app.py et par l'asset Dagster, dans des images qui n'embarquent
# pas pytest. Tant que les deux vivaient dans le meme fichier, il fallait un
# faux module pytest pour que les decorateurs de la suite s'evaluent a
# l'import -- et le jour ou ce repli manquait, l'asset de diagnostic
# disparaissait sans bruit des journaux Dagster.

def test_les_sondes_n_importent_pas_pytest():
    """checks.py doit rester lisible par une image sans pytest.

    On lit le fichier plutot que le module : un import reussi ici ne
    prouverait rien, puisque pytest est evidemment present quand cette suite
    tourne.
    """
    source = open(checks.__file__, encoding="utf-8").read()
    for ligne in source.splitlines():
        nue = ligne.strip()
        if nue.startswith("#") or '"' in nue or "'" in nue:
            continue
        assert not nue.startswith(("import pytest", "from pytest")), (
            f"checks.py importe pytest : {nue}")
    # Et aucun decorateur de test n'y subsiste.
    assert "@pytest." not in source
    assert "\ndef test_" not in source, (
        "un test a ete ecrit dans checks.py : il va dans tests/")




def test_les_tests_importent_les_sondes_et_jamais_l_inverse():
    """Le sens de la dependance, verifie dans les deux fichiers.

    checks.py qui importerait la suite ramenerait pytest dans les images de
    production par la bande -- exactement ce que la separation evite.
    """
    sondes = open(checks.__file__, encoding="utf-8").read()
    for motif in (r"^\s*(?:import|from)\s+(?:commun|conftest|test_)",
                  r"\bimportlib\b.*\b(?:commun|conftest)\b"):
        assert not re.search(motif, sondes, re.M), (
            "checks.py importe la suite : pytest reviendrait dans les images de production")
    assert sondes.count('"tests")') == 1, (
        "checks.py reference la suite ailleurs que pour la lancer")
    # Les tests, eux, chargent les sondes : une fois, pour tous, dans commun.py.
    commun = open(os.path.join(DOSSIER_DIAGNOSTIC, "tests", "commun.py"), encoding="utf-8").read()
    assert "\nimport checks" in commun




def test_la_ci_et_la_documentation_lancent_la_suite_au_bon_endroit():
    """Un chemin perime ici, et la CI ne teste plus rien en silence.

    C'est deja arrive une fois, autrement : cinq tests se sont ignores
    pendant des semaines parce qu'un chemin ne correspondait plus.
    """
    racine = _racine_depot()
    ci = os.path.join(racine, ".github", "workflows", "build-images.yml")
    if not os.path.exists(ci):
        pytest.skip("depot complet absent de cette image")
    texte = open(ci, encoding="utf-8").read()
    assert "pytest workspace/diagnostic/tests -q" in texte, (
        "la CI ne lance pas la suite au bon chemin")
    assert "pytest workspace/diagnostic/checks.py" not in texte
    assert "test_regressions.py" not in texte, "la CI vise l'ancien fichier de la suite"
    # La documentation donne la meme commande, sans l'ancien chemin.
    for doc in ("AGENTS.md", "README.md", os.path.join("app-manager", "README.md"),
                os.path.join("workspace", "diagnostic", "README.md")):
        contenu = open(os.path.join(racine, doc), encoding="utf-8").read()
        assert "test_regressions.py" not in contenu, f"{doc} cite l'ancien fichier"
        assert "pytest workspace/diagnostic/tests" in contenu, f"{doc} ne dit pas comment lancer la suite"
