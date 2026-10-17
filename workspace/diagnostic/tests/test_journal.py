"""
CodeLab -- TESTS : LE JOURNAL
---------------------------------------------------------------------------
ROLE        le journal des acces, ses analyses, son miroir Postgres, la base
            SQLite du panneau, le pouls du hub.
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


# ---------- 16. journal des acces ----------
#
# Deux usages, deux seulement : reconnaitre une intrusion, et savoir si un
# projet sert encore. Ce qui doit rester vrai : une ouverture n'est notee
# qu'apres les controles d'acces (un refus n'est pas une visite), le journal
# ne grossit pas indefiniment, et une ecriture impossible ne casse rien.


# ---------- la carte de chaleur, et les analyses ----------
#
# Vecu : « Ajoute une carte de chaleur pour l'utilisation des applications, et
# la possibilite de filtrer sur un utilisateur. Toutes les statistiques
# doivent etre historisees en base, et ces donnees alimentent les analyses. »
#
# Le point qui compte, et qui ne se voit pas a l'ecran : le fichier du
# journal est PLAFONNE a 1 Mo, donc il oublie. Tant qu'il servait a afficher
# quarante lignes, cela n'avait aucune importance. Des qu'on COMPTE, un total
# calcule sur une fenetre glissante ne veut plus rien dire -- il diminue tout
# seul a mesure que le journal tourne. Les analyses lisent donc la base, le
# fichier restant le repli.




def test_la_carte_compte_par_jour_et_pas_par_evenement(journal, tmp_path, monkeypatch):
    """Une case par jour : trois ouvertures le meme jour font une case a
    trois, pas trois cases."""
    # Hier a midi : quatre evenements a une minute d'ecart ne doivent pas
    # tomber de part et d'autre de minuit (le test echouait vers 23 h 58).
    hier = (datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
            - datetime.timedelta(days=1)).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(hier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(hier) + 60, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "c", "ts": int(hier) + 120, "genre": "ouverture", "qui": "marie", "app": "public"},
        # Une connexion n'est pas une ouverture : la carte parle d'usage des
        # applications, pas de presence.
        {"id": "d", "ts": int(hier) + 180, "genre": "connexion", "qui": "marie"},
    ])
    jours = app.carte_activite()
    assert len(jours) == 1, jours
    assert jours[0]["ouvertures"] == 3
    # Et combien d'applications distinctes ce jour-la : c'est ce qui
    # distingue « j'ai ouvert dix fois la meme » de « j'ai travaille partout ».
    assert jours[0]["apps"] == 2




def test_la_case_de_la_carte_detaille_le_jour_par_utilisateur_et_par_application(
        journal, tmp_path, monkeypatch):
    """Vecu : « ajoute du detail pour chaque case », puis « je veux le detail
    par utilisateur, en chiffre ». La case ne disait que le total et le
    nombre d'applications : qui, et ou, il fallait le chercher dans le
    journal. Les deux details sont classes du plus grand au plus petit, et un
    visiteur anonyme garde son nom vide (la page ecrit « visiteur anonyme »)."""
    hier = (datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
            - datetime.timedelta(days=1)).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(hier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(hier) + 60, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "c", "ts": int(hier) + 120, "genre": "ouverture", "qui": "jean", "app": "public"},
        {"id": "d", "ts": int(hier) + 180, "genre": "ouverture", "app": "public"},
        {"id": "e", "ts": int(hier) + 240, "genre": "ouverture", "qui": "marie", "app": "public"},
    ])
    [jour] = app.carte_activite()
    assert jour["ouvertures"] == 5 and jour["apps"] == 2
    assert jour["par_qui"] == [{"qui": "marie", "ouvertures": 3},
                               {"qui": "", "ouvertures": 1},
                               {"qui": "jean", "ouvertures": 1}], jour["par_qui"]
    assert jour["par_app"] == [{"app": "public", "ouvertures": 3},
                               {"app": "prive", "ouvertures": 2}], jour["par_app"]
    # Postgres compte de meme, dans la base, et passe par la meme mise en
    # forme : les deux sources rendent la meme case.
    src = _lire_source("app-manager/app/app.py")
    carte = src.split("def pg_carte_activite(")[1].split("\ndef ")[0]
    assert "GROUP BY 1, 2, 3" in carte and "detail_du_jour(" in carte
    # La page les montre EN CHIFFRES dans une bulle, au survol et a l'appui,
    # et le title natif (qui doublait la bulle, et jamais au doigt) a cede.
    page = _page_panneau("dashboard.html")
    bloc = page.split("function contenuBulleCarte(cle){")[1].split("\nfunction ")[0]
    assert "'Par utilisateur'" in bloc and "'Par application'" in bloc
    assert "Visiteur anonyme" in bloc
    liste = page.split("function listeBulleCarte(")[1].split("\nfunction ")[0]
    assert "cb-n" in liste and "--part" not in liste, "des chiffres, pas des barres"
    colonnes = page.split("function carteColonnes(annee, parJour, sommet){")[1].split("\nfunction ")[0]
    assert "data-jour=" in colonnes and 'title="${esc(titre)}"' not in colonnes
    assert "addEventListener('pointerover'" in page.split("function cacherBulleCarte(){")[1]




def test_le_repeint_garde_les_images_deja_chargees():
    """Vecu : « fais en sorte que les icones des applications ne clignotent
    pas ». Le moindre changement du hub (une pastille, un etat, le « il y a
    3 min » d'un widget) repeignait toute la grille : chaque <img> etait
    recree, et une image neuve, meme en cache, se decode a part -- toutes les
    icones vides le temps d'un affichage. Mesure dans Chromium : 6 a 24
    images recreees par repeint avant, 0 apres."""
    page = _page_panneau("dashboard.html")
    remplir = page.split("function remplir(el, html){")[1].split("\n}")[0]
    assert "imagesChargees(el)" in remplir and "reposerImages(el, images)" in remplir
    assert remplir.index("imagesChargees(el)") < remplir.index("el.innerHTML = html")
    reposer = page.split("function reposerImages(el, parSource){")[1].split("\nfunction ")[0]
    assert "neuve.replaceWith(vieille)" in reposer
    # src n'est jamais repose sur l'image gardee : le reposer relancerait le
    # chargement, et c'est le clignotement qu'on retirait.
    assert "name!=='src'" in reposer
    # Les autres peintures d'icones passent par le meme chemin.
    masquees = page.split("function peindreMasquees(){")[1].split("\n}\n")[0]
    assert "zone.innerHTML" not in masquees and "remplir(zone," in masquees
    assert "$('fa-icone').src=" not in page and "poserSource($('fa-icone')" in page
    assert "$('pr-icone').src=" not in page and "poserSource($('pr-icone')" in page




def test_le_bandeau_se_fond_sur_ce_qui_defile_dessous():
    """Vecu : « le header doit etre fondu lorsque je scroll en vertical et
    rester au-dessus des apps ». Un bandeau opaque coupait les tuiles net.
    Le voile (le fond de la page, estompe vers le bas) n'apparait que
    la page defilee : en haut, le rendu est identique a l'ancien au pixel
    pres (10 rendus compares). Le bandeau lui-meme reste transparent : mode.js
    lit sa couleur pour la barre d'etat, qui serait sinon delavee."""
    page = _page_panneau("dashboard.html")
    barre = page.split(".topbar{height:68px;")[1].split("}")[0]
    assert "background:transparent" in barre
    voile = page.split(".topbar::before{")[1].split("}")[0]
    # Un fondu en degrade, sans filtre compose. Vecu : « je ne peux pas scroll
    # avec mon telephone en vertical » -- sur iPhone, le flou (backdrop-filter)
    # et le masque d'un calque place DANS #defile bloquaient le defilement.
    assert "background:linear-gradient(var(--bg) calc(100% - 28px),transparent)" in voile
    assert "backdrop-filter" not in voile and "mask-image" not in voile
    assert "z-index:-1" in voile and "opacity:0" in voile
    assert ":root.page-defilee .topbar::before{opacity:1}" in page
    assert "position:sticky;top:0;z-index:40" in page, "le bandeau reste au-dessus des tuiles"
    socle = page.split("function majPageDefilee(){")[1].split("\n}")[0]
    assert "'page-defilee'" in socle and "defileur().scrollTop" in socle
    mode = _lire_source("app-manager/app/mode.js")
    assert "c === 'rgba(0, 0, 0, 0)'" in mode, "la barre d'etat retombe sur le fond de la page"




def test_la_carte_se_filtre_par_personne_et_par_application(journal, tmp_path, monkeypatch):
    hier = int(time.time() - 86400)
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": hier, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": hier, "genre": "ouverture", "qui": "jean", "app": "prive"},
        {"id": "c", "ts": hier, "genre": "ouverture", "qui": "marie", "app": "public"},
    ])
    assert app.carte_activite(qui="marie")[0]["ouvertures"] == 2
    assert app.carte_activite(app="prive")[0]["ouvertures"] == 2
    assert app.carte_activite(qui="marie", app="prive")[0]["ouvertures"] == 1
    # Un visiteur anonyme se filtre aussi : "" est une valeur, pas l'absence
    # de filtre.
    assert app.carte_activite(qui="") == []




def test_la_carte_ne_montre_que_l_annee_demandee(journal, tmp_path, monkeypatch):
    """Une annee civile, de janvier a decembre : ce qui est arrive l'an
    dernier n'a rien a faire sur la carte de cette annee, et inversement."""
    import datetime as _dt
    fuseau = app._fuseau()
    cette_annee = app.annee_courante()
    milieu = _dt.datetime(cette_annee, 6, 15, 12, 0, tzinfo=fuseau).timestamp()
    an_dernier = _dt.datetime(cette_annee - 1, 6, 15, 12, 0, tzinfo=fuseau).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(an_dernier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(milieu), "genre": "ouverture", "qui": "marie", "app": "prive"},
    ])
    jours = app.carte_activite(cette_annee)
    assert [j["jour"] for j in jours] == [f"{cette_annee}-06-15"]
    jours = app.carte_activite(cette_annee - 1)
    assert [j["jour"] for j in jours] == [f"{cette_annee - 1}-06-15"]
    # Sans annee demandee, c'est l'annee en cours.
    assert app.carte_activite() == app.carte_activite(cette_annee)




def test_les_annees_proposees_vont_de_la_premiere_a_aujourd_hui(journal, tmp_path,
                                                                monkeypatch):
    """Le selecteur doit offrir chaque annee entre la premiere utilisation et
    l'annee en cours -- y compris celles ou rien ne s'est passe, sans quoi la
    liste aurait des trous et l'on croirait a une perte de donnees."""
    import datetime as _dt
    fuseau = app._fuseau()
    cette_annee = app.annee_courante()
    vieux = _dt.datetime(cette_annee - 2, 3, 2, 12, 0, tzinfo=fuseau).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(vieux), "genre": "ouverture", "qui": "marie", "app": "prive"},
    ])
    assert app.annees_activite() == (cette_annee - 2, cette_annee - 2)
    assert app.annees_proposees(*app.annees_activite()) == [
        cette_annee - 2, cette_annee - 1, cette_annee]
    # Journal vide : l'annee en cours reste proposee, un selecteur vide
    # n'etant pas une reponse.
    assert app.annees_proposees(None, None) == [cette_annee]




def test_le_journal_ne_garde_que_la_duree_choisie(journal, tmp_path, monkeypatch):
    """Vecu : « limiter la duree de conservation du journal ». Il note les
    adresses IP, des donnees personnelles (RGPD) : Postgres gardait tout, le
    fichier local 50 000 evenements quel que soit leur age. Douze mois par
    defaut ; « sans limite » reste un choix possible."""
    monkeypatch.setattr(app, "pg_disponible", lambda: False)
    maintenant = time.time()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "vieux", "ts": int(maintenant - 400 * 86400), "genre": "connexion",
         "qui": "marie", "ip": "203.0.113.7"},
        {"id": "an", "ts": int(maintenant - 200 * 86400), "genre": "ouverture",
         "qui": "marie", "app": "prive"},
        {"id": "hier", "ts": int(maintenant - 86400), "genre": "ouverture",
         "qui": "marie", "app": "prive"},
    ])
    ids = lambda: sorted(e["id"] for e in app.lire_acces(limite=100))
    # Par defaut : douze mois.
    assert app.lire_conservation() == 12
    assert app.purger_le_journal(maintenant) == (1, None)
    assert ids() == ["an", "hier"]
    # Sans limite : rien ne part.
    app.ecrire_conservation(0)
    assert app.purger_le_journal(maintenant) == (0, 0) and ids() == ["an", "hier"]
    # Trois mois : ce qui a deux cents jours part aussi.
    app.ecrire_conservation(3)
    app.purger_le_journal(maintenant)
    assert ids() == ["hier"]
    # Une valeur inconnue dans le coffre retombe sur le defaut : ni tout
    # garder, ni tout effacer.
    for abimee in ({"mois": 7}, {"mois": True}, {"mois": "12"}, "12", None):
        app.ecrire_reglage("conservation_journal", abimee)
        assert app.lire_conservation() == 12, abimee
    # Un an fait bien douze mois, pas 360 jours.
    assert abs((maintenant - app.seuil_conservation(12, maintenant)) - 365.25 * 86400) < 1
    assert app.seuil_conservation(0, maintenant) is None




def test_la_conservation_se_regle_par_l_administrateur_seul(journal):
    """Le reglage efface des donnees : il est reserve a l'administrateur,
    passe par le jeton (comme toute ecriture) et n'accepte que les durees
    proposees -- un booleen n'est pas « 1 mois »."""
    c = journal
    assert c.get("/api/journal/conservation").status_code in (401, 403)
    assert c.put("/api/journal/conservation", json={"mois": 3}).status_code in (401, 403)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = c.get("/api/journal/conservation").get_json()
    assert d["mois"] == 12 and d["choix"] == [3, 6, 12, 24, 0]
    for mauvais in (5, -1, "12", True, None, 12.5):
        assert c.put("/api/journal/conservation", json={"mois": mauvais}).status_code == 400, mauvais
    r = c.put("/api/journal/conservation", json={"mois": 6})
    assert r.status_code == 200 and r.get_json()["mois"] == 6
    assert app.lire_conservation() == 6
    # Le changement se lit dans le journal, comme les autres reglages.
    assert any(e.get("action") == "conservation du journal : 6 mois"
               for e in app.lire_acces(limite=20))
    # Sans le bon jeton de la session, l'ecriture est refusee.
    r = c.put("/api/journal/conservation", json={"mois": 3},
              headers={app.JETON_ENTETE: "faux"})
    assert r.status_code == 403 and app.lire_conservation() == 6




def test_une_liste_habillee_suit_la_valeur_posee_par_le_script():
    """Vecu : la duree de conservation, reglee a 12 mois, affichait « 3 mois »
    -- la premiere option -- tant qu'on n'ouvrait pas la liste. Le navigateur
    ne signale pas un .value pose par le script : l'habillage redefinit
    value et selectedIndex sur chaque select pour repeindre son etiquette."""
    page = _page_panneau("dashboard.html")
    habiller = page.split("function dlHabiller(select){")[1].split("\nfunction ")[0]
    assert "for(const nom of ['value', 'selectedIndex'])" in habiller
    assert "set(v){ d.set.call(this, v); dlPeindre(enveloppe); }" in habiller
    assert "observe(select, {childList:true})" in habiller




def test_la_purge_vaut_pour_postgres_et_pour_le_journal_local():
    """Purger Postgres seul ne suffit pas : au demarrage, le panneau recopie
    le journal local dans la base (_pg_rattraper), et les lignes effacees
    reviendraient. La purge touche les deux, chaque jour, et des que le
    reglage change."""
    src = _lire_source("app-manager/app/app.py")
    purge = src.split("def purger_le_journal(")[1].split("\ndef ")[0]
    assert "DELETE FROM journal WHERE ts < ?" in purge
    assert "DELETE FROM acces WHERE ts < to_timestamp(%s)" in purge
    route = src.split("def api_conservation_ecrire(")[1].split("\n@flask_app")[0]
    assert "purger_le_journal()" in route
    assert "    demarrer_la_purge_du_journal()\n    servir(" in src
    page = _page_panneau("dashboard.html")
    # Raccourcir efface tout de suite : une confirmation, bouton au verbe.
    changer = page.split("async function cjChanger(sel){")[1].split("\n}")[0]
    assert "demander(" in changer and "'Effacer', true" in changer




def test_la_carte_est_reservee_a_l_administrateur(journal):
    """Elle dit qui a ouvert quoi et quand : c'est le meme secret que le
    journal, elle se garde comme lui."""
    c = journal
    assert c.get("/api/activite/carte").status_code in (401, 403)
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = c.get("/api/activite/carte")
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["source"] in ("postgres", "fichier")
    assert isinstance(d["jours"], list)
    assert d["annee"] in d["annees"], (
        "l'annee affichee doit figurer dans la liste proposee")




def test_une_annee_farfelue_ne_fait_pas_tomber_la_carte(journal):
    """Le parametre arrive par l'URL : il se borne, il ne se croit pas."""
    c = journal
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    courante = app.annee_courante()
    for valeur in ("0", "-5", "99999", "beaucoup", ""):
        r = c.get("/api/activite/carte?annee=" + valeur)
        assert r.status_code == 200, valeur
        assert r.get_json()["annee"] == courante, valeur
    # Une annee plausible, elle, est respectee.
    r = c.get("/api/activite/carte?annee=" + str(courante - 1))
    assert r.get_json()["annee"] == courante - 1




def test_les_analyses_preferent_la_base_au_fichier_plafonne():
    """Le resume etait calcule sur le fichier : le total d'ouvertures d'un
    projet DIMINUAIT tout seul a mesure que le journal tournait."""
    src = _page_panneau("app.py")
    bloc = src.split("def api_activite(")[1].split("@flask_app")[0]
    assert "pg_resume_acces()" in bloc, "le resume doit venir de la base quand elle repond"
    assert "resume_acces()" in bloc, "et le fichier doit rester le repli"
    # Le comptage par jour se fait DANS la base : ramener un an d'evenements
    # pour les additionner en Python s'ecroulerait le jour ou l'historique
    # compte pour de bon.
    carte = src.split("def pg_carte_activite(")[1].split("\ndef ")[0]
    assert "GROUP BY" in carte and "count(*)" in carte




def test_la_carte_couvre_l_annee_de_janvier_a_decembre():
    """Une fenetre glissante commencait un jour quelconque : les mois se
    decalaient d'un cran chaque jour, et deux cartes prises a deux dates ne
    se comparaient pas."""
    page = _page_panneau("dashboard.html")
    # Les colonnes se batissent dans carteColonnes, appelee par peindreCarte.
    assert "carteColonnes(annee, parJour, sommet)" in page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    bloc = page.split("function carteColonnes(annee, parJour, sommet){")[1].split("\nfunction ")[0]
    assert "new Date(annee,0,1)" in bloc, "la carte doit commencer au 1er janvier"
    assert "new Date(annee,11,31)" in bloc, "et finir au 31 decembre"
    assert "d.fenetre" not in bloc, "la fenetre glissante n'a plus cours"
    # Les jours a venir ne se peignent pas comme des jours a zero : ce serait
    # affirmer une inactivite qui n'a pas encore eu lieu.
    assert "carte-case avenir" in bloc and ".carte-case.avenir{" in page




def test_la_ligne_des_mois_couvre_toute_la_largeur_de_la_carte():
    """Une etiquette par colonne laissait onze mois muets et un mot de dix
    pixels de large : on ne lisait rien."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    assert "grid-column:span" in bloc, (
        "chaque mois doit couvrir ses colonnes, pas une seule")
    # Les MEMES colonnes et le meme ecart pour les deux rangees : sans cela,
    # les mois ne tombent pas en face de leurs semaines. La ligne des mois
    # n'a qu'une rangee, elle ne reprend donc pas les sept lignes.
    assert "${gabarit}" in bloc and "${gabaritMois}" in bloc
    mois = bloc.split("const gabaritMois=")[1].split("\n")[0]
    assert "repeat(${colonnes},${cote}px)" in mois and "gap:${ecart}px" in mois
    assert "grid-template-rows" not in mois




def test_l_annee_de_la_carte_se_choisit():
    """« Et l'an dernier ? » n'avait aucune reponse : la carte ne montrait
    que les douze derniers mois, sans moyen de remonter."""
    page = _page_panneau("dashboard.html")
    assert 'id="carte-annee"' in page
    bloc = page.split("async function chargerCarte(){")[1].split("\n}")[0]
    assert "p.set('annee'" in bloc, "le choix doit partir au serveur"
    # La liste des annees vient du serveur : la page ne connait pas
    # l'historique, et une liste devinee proposerait des annees vides.
    annees = page.split("function peindreAnnees(d){")[1].split("\n}")[0]
    assert "d.annees" in annees and "d.annee" in annees




def test_les_paliers_de_la_carte_sont_relatifs_au_plus_charge():
    """Un seuil absolu afficherait une carte toute pale sur une installation
    calme, et toute sombre sur une installation chargee. Ce qu'on lit dans
    une carte de chaleur, c'est le relief."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function carteNiveau(")[1].split("\n}")[0]
    assert "sommet" in bloc and "/ sommet" in bloc
    # Cinq paliers, comme le calendrier de GitHub.
    for niveau in range(5):
        assert f".carte-case.n{niveau}" in page or niveau == 0




def test_un_acces_refuse_n_est_pas_une_visite(journal):
    """Le journal sert a savoir si un projet sert encore.

    Compter les refus dedans donnerait a une application fermee l'air d'etre
    tres frequentee, ce qui est exactement l'inverse de ce qu'on demande.
    """
    c = journal
    # Sans session, un projet prive redirige vers la connexion.
    assert c.get("/prive/", follow_redirects=False).status_code == 302
    assert [e for e in app.lire_acces() if e.get("genre") == "ouverture"] == []

    # Un projet public, lui, est une vraie visite -- meme sans compte.
    c.get("/public/")
    ouvertures = [e for e in app.lire_acces() if e.get("genre") == "ouverture"]
    assert len(ouvertures) == 1
    assert ouvertures[0]["app"] == "public" and ouvertures[0]["qui"] == ""




def test_une_page_web_ne_fait_pas_cinquante_lignes_de_journal(journal):
    """Une page, c'est des dizaines de requetes. Les compter toutes ne dirait
    plus rien de la frequentation, et remplirait le disque."""
    c = journal
    for _ in range(30):
        c.get("/public/")
    assert len([e for e in app.lire_acces() if e.get("genre") == "ouverture"]) == 1

    # Le regroupement passe : la visite suivante compte pour une nouvelle.
    app._dernier_acces.clear()
    c.get("/public/")
    assert len([e for e in app.lire_acces() if e.get("genre") == "ouverture"]) == 2




def test_les_connexions_et_les_echecs_sont_notes(journal):
    c = journal
    c.post("/login", json={"nom": "admin", "password": "faux"})
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    genres = [e["genre"] for e in app.lire_acces()]
    assert genres[:2] == ["connexion", "echec"], genres   # plus recent d'abord
    resume = app.resume_acces()
    assert resume["echecs"] == 1
    assert resume["comptes"]["admin"]["connexions"] == 1




def test_le_journal_a_un_plafond_au_lieu_de_remplir_le_disque(journal, monkeypatch):
    """Un journal sans plafond transforme une curiosite en panne : au-dela,
    les plus anciens evenements partent (Postgres, lui, garde tout)."""
    monkeypatch.setattr(app, "JOURNAL_MAX", 50)
    app._journal_ecrits[0] = 0
    for i in range(400):
        app.journaliser("ouverture", qui=f"compte{i}", app="public", ip="10.0.0.1")
    restants = app.lire_acces(limite=1000)
    assert len(restants) <= 50 + 200          # la purge passe tous les 200
    # Ce sont les plus ANCIENS qui partent.
    assert restants[0]["qui"] == "compte399"




def test_journaliser_ne_fait_jamais_tomber_le_service(journal, monkeypatch):
    """Disque plein ou montage en lecture seule : le proxy doit continuer.

    Faire echouer une connexion pour proteger son journal reviendrait a
    eteindre le service au moment ou on veut justement l'observer.
    """
    app.journaliser("connexion", qui="marie")          # ne leve pas
    assert journal.get("/public/").status_code in (200, 502, 503)
    assert journal.post("/login", json={"nom": "admin", "password": "secret-de-test"}).status_code == 200




def test_le_journal_est_reserve_a_l_administrateur(journal):
    """Il contient des adresses IP et le detail de qui ouvre quoi."""
    c = journal
    assert c.get("/api/activite").status_code == 401
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/activite").status_code == 403




# ---------- 21. miroir Postgres ----------
#
# Le fichier reste la source de verite : il tient sans base et se lit depuis
# une session SSH. Postgres est la memoire longue -- le fichier est plafonne
# a 1 Mo et oublie. Ce qui doit rester vrai : ecrire dans la base n'est
# JAMAIS sur le chemin d'une requete, et le rejeu ne double aucune ligne.

def test_journaliser_ne_depend_jamais_de_la_base(tmp_path, monkeypatch):
    """Une base eteinte, lente, ou une file pleine : la connexion passe quand
    meme. C'est la seule propriete qui compte -- le reste n'est que du
    journal."""
    monkeypatch.setattr(app, "PG_ACTIF", True)
    # File pleine : chaque depot est perdu, sans exception ni attente.
    pleine = queue.Queue(maxsize=1)
    pleine.put(("acces", {}))
    monkeypatch.setattr(app, "_pg_file", pleine)
    perdus = app._pg_etat["perdus"]

    debut = time.time()
    for _ in range(50):
        app.journaliser("connexion", qui="marie", ip="10.0.0.1")
    assert time.time() - debut < 1.0, "journaliser doit rendre la main tout de suite"
    assert app._pg_etat["perdus"] == perdus + 50
    # Et malgre tout, les 50 evenements sont dans le fichier.
    assert len(app.lire_acces(limite=1000)) == 50




def test_chaque_evenement_porte_un_identifiant(tmp_path, monkeypatch):
    """C'est lui qui rend le rattrapage rejouable.

    Sans identifiant, un rejeu apres une coupure de la base reinsererait les
    memes lignes -- et l'historique compterait double.
    """
    monkeypatch.setattr(app, "PG_ACTIF", False)
    app.journaliser("connexion", qui="marie")
    app.journaliser("connexion", qui="marie")
    ids = [e["id"] for e in app.lire_acces()]
    assert len(ids) == 2 and len(set(ids)) == 2, "deux evenements, deux identifiants"




def test_le_miroir_se_tait_quand_il_est_debranche(monkeypatch):
    """APP_MANAGER_PG=0, ou psycopg absent : rien ne part, rien ne casse."""
    monkeypatch.setattr(app, "PG_ACTIF", False)
    avant = app._pg_file.qsize()
    app._pg_deposer(("acces", {"id": "x"}))
    assert app._pg_file.qsize() == avant
    assert app.pg_disponible() is False




def test_le_miroir_ecrit_vraiment_dans_postgres(tmp_path, monkeypatch):
    """Contre un vrai serveur, quand il y en a un.

    Ignore sans base joignable : ce test verifie la creation des tables, le
    rejeu idempotent et la trace d'un compte supprime -- des choses qu'un
    faux curseur ne prouverait pas.
    """
    pytest.importorskip("psycopg")
    import psycopg
    dsn = os.environ.get("CODELAB_TEST_PG")
    if not dsn:
        pytest.skip("CODELAB_TEST_PG non defini : pas de serveur de test")
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception as e:
        pytest.skip(f"serveur de test injoignable : {e}")

    reglages = psycopg.conninfo.conninfo_to_dict(dsn)
    monkeypatch.setattr(app, "PG_BASE", "codelab_test_" + secrets.token_hex(4))
    monkeypatch.setattr(app, "PG_ACTIF", True)
    monkeypatch.setattr(app, "_pg_reglages", lambda: {
        "host": reglages.get("host", "127.0.0.1"), "port": reglages.get("port", "5432"),
        "user": reglages.get("user", "codelab"), "password": reglages.get("password", ""),
        "instance": reglages.get("dbname", "dagster")})

    app.journaliser("connexion", qui="marie", ip="10.0.0.1", role="utilisateur")
    app.journaliser("ouverture", qui="marie", app="site", ip="10.0.0.1")
    app.ecrire_utilisateurs({"marie": {"sel": "aa", "hash": "bb", "projets": ["site"],
                                       "cree": 0, "email": "marie@example.com"}})
    try:
        app._pg_preparer()
        with app._pg_connexion(app.PG_BASE) as cx:
            app._pg_rattraper(cx)
            assert cx.execute("SELECT count(*) FROM acces").fetchone()[0] == 2
            # Rejoue : les memes lignes ne rentrent pas deux fois.
            app._pg_rattraper(cx)
            assert cx.execute("SELECT count(*) FROM acces").fetchone()[0] == 2
            # Aucun secret n'a traverse.
            colonnes = [c[0] for c in cx.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'utilisateurs'").fetchall()]
            assert not ({"hash", "sel", "totp", "cle_publique"} & set(colonnes))
            # Un compte supprime laisse sa trace : le journal le nomme encore.
            app.ecrire_utilisateurs({})
            app._pg_ecrire_utilisateurs(cx)
            reste = cx.execute("SELECT nom, supprime IS NOT NULL FROM utilisateurs").fetchall()
            assert reste == [("marie", True)]
    finally:
        with app._pg_connexion(app._pg_reglages()["instance"]) as cx:
            cx.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                psycopg.sql.Identifier(app.PG_BASE)))


# ------------------- la base du panneau (SQLite) -------------------
#
# Les donnees vivaient dans une quinzaine de fichiers JSON ; elles vivent dans
# codelab.db, une table par sujet. Ce qui doit rester vrai : l'import des
# anciens fichiers ne perd rien et ne se rejoue pas, les originaux sont gardes
# (sans adresse en clair), le schema est versionne, et le diagnostic lit la
# base sans pouvoir l'ecrire.







def test_le_schema_est_versionne_et_la_base_reservee_au_panneau():
    import sqlite3
    app.load()
    cx = sqlite3.connect(app.DB_FILE)
    assert cx.execute("PRAGMA user_version").fetchone()[0] == app.stockage.VERSION
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"applications", "comptes", "acces_applications", "cles_acces", "categories",
            "masquees", "journal", "messages", "processus", "parametres",
            "reglages_chiffres"} <= tables
    cx.close()
    assert stat.S_IMODE(os.stat(app.DB_FILE).st_mode) == 0o600




def test_supprimer_un_compte_retire_aussi_ses_droits():
    app.ecrire_utilisateurs({"marie": {"sel": "a", "hash": "b", "projets": ["site"]},
                             "paul": {"sel": "c", "hash": "d", "projets": ["site"]}})
    comptes = app.lire_utilisateurs()
    comptes.pop("marie")
    app.ecrire_utilisateurs(comptes)
    lignes = app.stockage.lire(app.DB_FILE, "SELECT compte FROM acces_applications")
    assert [l["compte"] for l in lignes] == ["paul"]




def test_le_diagnostic_lit_la_base_en_lecture_seule(monkeypatch):
    monkeypatch.setenv("APP_MANAGER_STATE", app.STATE_DIR)
    app.save({"site": {"path": "/w/s", "command": "x", "port": 9101,
                       "visibility": "publique"}})
    app.ecrire_utilisateurs({"marie": {"sel": "a", "hash": "b", "projets": []}})
    app.ecrire_exposition({"admin_reseau_local": True})
    app.journaliser("connexion", qui="marie", ip="203.0.113.9")
    assert checks._apps_panneau()["site"]["visibility"] == "publique"
    assert checks._comptes_panneau() == {"marie": {}}
    assert checks._exposition_panneau() == {"admin_reseau_local": True}
    assert checks._ips_du_journal() == ["203.0.113.9"]
    # Lecture seule : une ecriture par cette voie est refusee.
    assert checks._requete_panneau("DELETE FROM applications") is None
    assert list(app.load()) == ["site"]





def test_l_onglet_du_panneau_s_appelle_codelab():
    assert "<title>CodeLab</title>" in _page_panneau("dashboard.html")
    assert "App Manager" not in _page_panneau("dashboard.html")




def test_le_panneau_declare_toujours_sa_propre_icone():
    """Safari garde une icone par nom d'hote : celle d'une application ou de
    Mailpit (meme hote, autre port) ne doit pas reprendre l'onglet du
    panneau. Chaque page du panneau declare donc la sienne, versionnee."""
    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        # Une adresse qu'aucun autre service du meme hote ne sert.
        assert 'rel="icon" type="image/png" sizes="128x128" href="/codelab-onglet.png"' in page, nom
        assert 'href="/apple-touch-icon.png?v=4"' in page, nom
        assert "/favicon.png?v=" not in page and "/favicon.ico?v=" not in page, nom
        # Au retour sur l'onglet, les balises sont reposees.
        assert "window.addEventListener('pageshow', reposerIcone);" in page, nom




def test_les_connexions_recentes_se_filtrent_et_restent_courtes():
    page = _page_panneau("dashboard.html")
    assert 'id="ac-genre"' in page and "AC_MAX=200" in page
    # 10 par defaut : la liste ne deborde plus a l'ouverture.
    assert "return 10;" in page





def test_sur_telephone_le_menu_est_ferme_et_s_ouvre_par_dessus():
    """Sur un telephone, la colonne de gauche prenait la moitie de l'ecran."""
    page = _page_panneau("dashboard.html")
    assert "@media (max-width:760px)" in page
    assert ".sidebar.ouvert{transform:none}" in page
    assert "function fermerMenuMobile()" in page
    # Changer de rubrique referme le menu.
    debut = page.index("function showSection(name){")
    assert "fermerMenuMobile();" in page[debut:debut + 800]




def test_sur_telephone_le_hub_est_une_grille_d_icones():
    page = _page_panneau("dashboard.html")
    assert ':root[data-tuiles="icones"] .hub-grille{grid-template-columns:repeat(4,minmax(0,1fr))' in page




def test_la_page_de_connexion_ne_montre_pas_credentials_sur_telephone():
    page = _page_panneau("login.html")
    assert "@media (max-width:760px){.pied{display:none}}" in page




# ---------------- Le pouls du hub : Serveur et Activite ----------------
#
# Vecu : « je veux une nouvelle version un peu plus dynamique, des chiffres,
# des graphiques qui s'affichent progressivement, des reliefs ou des effets
# sur le texte ».

def test_le_pouls_du_serveur_est_reserve_a_l_administrateur(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/hub/pouls").status_code in (401, 403)
    d = c.get("/api/hub/disposition").get_json()
    assert not {w["id"] for w in d["catalogue"]} & {"serveur", "activite"}
    for wid, taille in (("serveur", "petit"), ("activite", "moyen")):
        r = c.put("/api/hub/disposition", json={"elements": [{"t": "w", "id": wid, "taille": taille}]})
        assert r.status_code == 400, wid




def test_le_pouls_donne_la_machine_et_les_ouvertures_recentes(deux_espaces, monkeypatch):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    monkeypatch.setattr(app, "pg_disponible", lambda: False)
    app._serveur_historique.clear()
    app.serveur_tick()
    app.serveur_tick()
    maintenant = app.time.time()
    journal = [{"genre": "ouverture", "ts": maintenant, "app": "prive-autorise"},
               {"genre": "ouverture", "ts": maintenant - 3600, "app": "prive-autorise"},
               {"genre": "ouverture", "ts": maintenant - 3 * 86400, "app": "autre"},
               # La quinzaine d'avant : comptee pour la tendance seulement.
               {"genre": "ouverture", "ts": maintenant - 20 * 86400, "app": "autre"},
               # Ni trop vieux, ni une connexion, ne comptent.
               {"genre": "ouverture", "ts": maintenant - 60 * 86400, "app": "autre"},
               {"genre": "connexion", "ts": maintenant, "qui": "marie"}]
    monkeypatch.setattr(app, "lire_acces", lambda **k: journal)
    d = c.get("/api/hub/pouls").get_json()
    s = d["serveur"]
    assert 0 <= s["cpu"] <= 100 * s["coeurs"]
    assert 0 < s["memoire"]["pct"] <= 100 and s["memoire"]["total"] > 0
    assert len(s["historique"]) == 2 and set(s["historique"][0]) == {"t", "cpu", "mem"}
    a = d["activite"]
    assert len(a["jours"]) == app.ACTIVITE_JOURS
    assert a["jours"][-1]["ouvertures"] == sum(1 for e in journal[:2]
                                               if app._jour(e["ts"]) == a["jours"][-1]["jour"])
    assert a["total"] == 3 and a["precedent"] == 1
    assert a["apps"][0] == {"name": "prive-autorise", "titre": a["apps"][0]["titre"], "ouvertures": 2}
    # On ne demande que ce qu'on affiche.
    assert set(c.get("/api/hub/pouls?quoi=serveur").get_json()) == {"serveur"}




def test_le_serveur_se_mesure_dans_le_moniteur_et_garde_dix_minutes():
    source = _source_du_panneau()
    boucle = source.split("def start_monitor_thread():")[1].split("t = threading.Thread")[0]
    assert "serveur_tick()" in boucle
    assert app.SERVEUR_HISTORIQUE_LEN * 10 == 600




def test_le_hub_anime_ses_chiffres_et_dessine_ses_graphiques():
    page = _page_panneau("dashboard.html")
    for trace in (
            # Les deux widgets, branches sur leur emplacement garde.
            "if(e.id==='serveur' || e.id==='activite') return emplacementPouls(e.id, e.taille);",
            'data-garder="pouls-${id}-${taille}"', "/api/hub/pouls?quoi=",
            # Les chiffres defilent depuis la derniere valeur montree.
            "function animerChiffres(", "valeursVues", "data-anim=\"nombre\"",
            "chiffre('etat-en-ligne', enLigne)", "chiffre('messages', n)",
            # Les graphiques ne jouent leur entree qu'une fois.
            "dejaApparus", "@keyframes rideau", "@keyframes barre-pousse",
            # Le relief : cascade d'entree, inclinaison, bonjour qui s'eclaire.
            "function entreeDuHub(", "@keyframes tuile-entree", ".survol::after",
            'class="salut-nom"', "@keyframes salut-reflet", 'id="hub-resume"',
            # Jamais la couleur seule pour dire « sature ».
            "const niveauCharge = v => v>=92 ? ['err','saturé'] : v>=80 ? ['warn','élevé'] : ['',''];"):
        assert trace in page, trace
    # Moins de mouvement demande : tout s'arrete.
    sobre = page.split("@media (prefers-reduced-motion:reduce){\n  :is(.salut")[1].split("}\n}")[0]
    for cible in ("#hub-liste.entree [data-el]", ".barres-jours.apparait i", ".courbe.apparait *"):
        assert cible in sobre, cible
    assert "if(sobre || avant===fin){ poserAnim(n, fin); return; }" in page




def test_les_parametres_n_ont_plus_d_erreurs_d_affichage():
    """Vecu : un titre qui passe au vert et des coins qui depassent au survol,
    une fenetre de cartes dans une carte, des gelules vertes en colonne, des
    tuiles du hub penchees en trapeze."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("LES PARAMETRES, DANS LE STYLE DU PANNEAU.")[1].split("LES REGLAGES D'UN COMPTE UTILISATEUR")[0]
    # Les cartes coupent ce qui deborde ; le titre ne change plus de couleur.
    assert "border-radius:20px;padding:20px 24px;overflow:hidden" in bloc
    assert ".settings-card.pliable>.card-top:hover .card-name b{color:inherit}" in bloc
    # Plus de fenetre dans la page : ni fond, ni cadre, la police du panneau.
    assert "background:none;border:0;border-radius:0;box-shadow:none;\n   overflow:visible;font-family:var(--sans)" in bloc
    # Une coche suffit : pas de ligne cerclee d'accent.
    assert "#sec-parametres .choix-ligne:has(>input:checked){background:none;border-color:transparent}" in bloc
    # Quatre appareils, le reste derriere un bouton ; cet appareil toujours en tete.
    assert "const AP_VISIBLES=4;" in page and "Afficher les ${caches} autres" in page
    assert "(y.courant?1:0)-(x.courant?1:0)" in page
    # Le telephone et la tablette (mode application compris) heritent des
    # memes cartes : la regle n'est plus reservee aux grands ecrans.
    cartes = bloc.split("LES CARTES, A TOUTES LES TAILLES.")[1]
    avant_media = cartes.split("@media (max-width:760px)")[0]
    assert "border-radius:20px;padding:20px 24px;overflow:hidden" in avant_media
    # Le hub ne penche plus ses tuiles.
    assert "rotateX(var(--rx" not in page and "--ry" not in page
    # Ni ne les souleve : la mosaique coupe ce qui depasse, le haut d'une
    # tuile de la premiere rangee paraissait tranche.
    survol = page.split("/* -- le relief au survol (souris)")[1].split("/* -- l'etat")[0]
    assert "translate" not in survol.split("*/", 1)[1]
