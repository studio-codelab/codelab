"""
CodeLab -- TESTS : SAUVEGARDES ET ALERTES
---------------------------------------------------------------------------
ROLE        sauvegardes, alertes et envoi des mails, Mailpit, surveillance
            externe, version, pannes et hors ligne.
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


# ------------------- 7. alertes par mail -------------------
#
# Une alerte qui part en boucle est pire que pas d'alerte : la boite se
# remplit, et on prend l'habitude de ne plus la lire. Ces tests tiennent la
# seule propriete qui compte vraiment -- un incident, un mail.




def test_une_application_tombee_alerte_une_seule_fois(alertes, monkeypatch):
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    assert alertes == ["[CodeLab] site est tombee"]
    # Le moniteur repasse toutes les 10 secondes : sans memoire de l'incident,
    # c'est un mail toutes les 10 secondes tant que l'application est a terre.
    app.alerte_tick()
    app.alerte_tick()
    assert len(alertes) == 1




def test_le_retour_a_la_normale_est_signale_puis_oublie(alertes, monkeypatch):
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    _etat(monkeypatch, tourne=True, en_boucle=False)
    app.alerte_tick()
    assert alertes == ["[CodeLab] site est tombee", "[CodeLab] site est revenue"]
    app.alerte_tick()
    assert len(alertes) == 2   # l'incident est clos, plus rien a dire




def test_un_plantage_rattrape_par_un_redemarrage_n_alerte_pas(alertes, monkeypatch):
    """Le filet de securite qui fonctionne n'est pas un incident : une
    application relancee avec succes ne doit reveiller personne."""
    _etat(monkeypatch, tourne=False, en_boucle=False)
    app.alerte_tick()
    assert alertes == []




def test_un_arret_volontaire_n_alerte_pas(alertes, monkeypatch):
    """Personne n'a besoin d'un mail pour une action qu'il vient de faire."""
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    apps = app.load()
    apps["site"]["enabled"] = False       # arret depuis le panneau
    app.save(apps)
    app.alerte_tick()
    assert len(alertes) == 1              # ni deuxieme alerte, ni mail de retour
    assert app._alertes_en_cours == set()




def test_l_interrupteur_coupe_vraiment_les_alertes(alertes, monkeypatch):
    app.ecrire_alertes(False, ["moi@example.com"])
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    assert alertes == []




def test_la_configuration_incomplete_est_dite_champ_par_champ(tmp_path, monkeypatch):
    """"Ca ne marche pas" est inutilisable ; le nom de la cle manquante se
    corrige en dix secondes."""
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    (tmp_path / "credentials.env").write_text("")
    app.ecrire_alertes(True, [])
    _, manquants = app.config_smtp()
    assert manquants == ["serveur d'envoi", "adresse d'expedition",
                         "adresse d'alerte de l'administrateur"]




def test_les_adresses_saisies_sont_nettoyees():
    """Saisie humaine : virgules, espaces, doublons, ligne vide."""
    assert app._adresses("moi@example.com, autre@example.com ,moi@example.com,") == [
        "moi@example.com", "autre@example.com"]
    assert app._adresses(["pas-une-adresse", " ok@example.com "]) == ["ok@example.com"]


# ---------- 20. le serveur d'envoi n'est pas les alertes ----------
#
# Un serveur SMTP parfaitement configure passait pour incomplet tant qu'aucun
# destinataire d'alerte n'etait saisi -- alors qu'il sert aussi les codes de
# verification d'adresse et l'inscription libre, qui n'ont rien a voir avec
# les alertes. Les deux ont chacun leur onglet, et le test d'envoi doit
# pouvoir viser une adresse sans qu'aucune alerte soit reglee.




def test_le_serveur_d_envoi_se_declare_pret_sans_alerte_reglee(envoi):
    """Le serveur d'envoi et les alertes sont deux choses : un serveur
    parfaitement configure passerait pour incomplet tant qu'aucune adresse
    d'alerte n'est saisie, alors qu'il sert aussi les codes de verification."""
    etat = envoi.get("/api/alertes").get_json()
    assert etat["smtp_ok"] is True
    assert etat["source"] == "origine"
    assert etat["origine_utilisable"] is True
    assert etat["personnalisee"] is None
    # Ca, en revanche, c'est l'affaire des alertes.
    assert "adresse d'alerte de l'administrateur" in etat["manquants"]




def test_une_configuration_qui_ne_repond_pas_n_est_pas_enregistree(envoi, monkeypatch):
    """Ce qui remplace l'ancien bouton "mail de test" : un test qu'il fallait
    penser a lancer, et dont l'oubli ne se voyait pas. Ici une configuration
    qui ne marche pas n'entre tout simplement pas."""
    monkeypatch.setattr(app, "verifier_smtp",
                        lambda cfg: (False, "SMTPAuthenticationError: 535 refuse"))
    r = envoi.post("/api/alertes", json={
        "actif": False, "admin": ["moi@example.com"],
        "smtp": {"host": "faux.example.com", "user": "x@example.com",
                 "password": "mauvais"}})
    assert r.status_code == 400, r.data
    assert "535 refuse" in r.get_json()["detail"]
    assert app.lire_smtp_personnalise() is None, "une configuration refusee a ete ecrite"
    # Et surtout : celle d'origine sert toujours.
    assert app.smtp_origine()["host"] == "origine.example.com"




def test_une_configuration_qui_repond_devient_la_source(envoi, monkeypatch):
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    r = envoi.post("/api/alertes", json={
        "actif": False, "admin": ["moi@example.com"],
        "smtp": {"host": "perso.example.com", "user": "perso@example.com",
                 "password": "bon"}})
    assert r.status_code == 200, r.data
    assert r.get_json()["source"] == "personnalisee"
    cfg, _ = app.config_smtp()
    assert cfg["host"] == "perso.example.com"
    # L'ORIGINE EST INTACTE. C'est tout l'objet de la separation : se tromper
    # d'un caractere depuis une page ne doit pas supprimer le seul moyen de
    # prevenir qu'une application est tombee.
    assert app.smtp_origine()["host"] == "origine.example.com"
    assert app.smtp_origine()["password"] == "mot-de-passe-origine"




def test_enregistrer_l_adresse_admin_n_efface_pas_la_configuration_d_origine(envoi):
    """Le bloc de credentials.env est REMPLACE en entier par upsert_shared_block.
    Y ecrire la seule adresse d'administration effacerait le serveur d'envoi et
    son mot de passe -- donc le repli que tout ceci construit."""
    r = envoi.post("/api/alertes", json={"actif": True,
                                         "admin": ["chef@example.com"]})
    assert r.status_code == 200, r.data
    origine = app.smtp_origine()
    assert origine["host"] == "origine.example.com", "le serveur d'origine a ete efface"
    assert origine["password"] == "mot-de-passe-origine", "le mot de passe a ete efface"
    assert app.alertes_admin() == ["chef@example.com"]




def test_revenir_a_l_origine_est_toujours_permis(envoi, monkeypatch):
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    envoi.post("/api/alertes", json={"actif": False, "admin": ["moi@example.com"],
                                     "smtp": {"host": "perso.example.com",
                                              "user": "p@example.com",
                                              "password": "bon"}})
    assert app.lire_smtp_personnalise() is not None
    # Serveur vide = retour a l'origine. Sans condition : c'est la marche
    # arriere, et on la cherche justement quand rien ne va.
    r = envoi.post("/api/alertes", json={"actif": False, "admin": ["moi@example.com"],
                                         "smtp": {"host": ""}})
    assert r.status_code == 200, r.data
    assert app.lire_smtp_personnalise() is None
    assert app.config_smtp()[0]["host"] == "origine.example.com"




def test_l_envoi_repli_sur_l_origine_quand_la_personnalisee_tombe(envoi, monkeypatch):
    """Une configuration verifiee le jour de son enregistrement peut cesser de
    marcher : mot de passe revoque, quota, serveur eteint. Ce jour-la, l'alerte
    doit sortir quand meme."""
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    envoi.post("/api/alertes", json={"actif": True, "admin": ["moi@example.com"],
                                     "smtp": {"host": "perso.example.com",
                                              "user": "p@example.com",
                                              "password": "bon"}})
    essais = []

    def _envoi(cfg, sujet, corps, destinataires=None, **_):
        essais.append(cfg["host"])
        if cfg["host"] == "perso.example.com":
            raise OSError("serveur injoignable")
    monkeypatch.setattr(app, "envoyer_mail", _envoi)

    envoye, detail = app.envoyer_avec_repli("sujet", "corps", ["moi@example.com"])
    assert envoye is True, detail
    assert essais == ["perso.example.com", "origine.example.com"], (
        "le repli n'a pas eu lieu dans cet ordre")
    assert detail == "d'origine"




def test_chaque_application_a_ses_destinataires_et_l_admin_recoit_tout(envoi):
    """Une application de facturation ne previent pas les memes personnes
    qu'un site vitrine. Mais l'adresse d'administration s'ajoute TOUJOURS :
    une application dont la liste est vide ne tombe pas en silence."""
    envoi.post("/api/alertes", json={"actif": True, "admin": ["chef@example.com"]})
    r = envoi.put("/api/alertes/application/site",
                  json={"alertes": "equipe@example.com, autre@example.com"})
    assert r.status_code == 200, r.data
    assert r.get_json()["destinataires"] == ["chef@example.com",
                                             "equipe@example.com",
                                             "autre@example.com"]
    # Une application sans liste propre : l'admin reste, et il est seul.
    app.save(dict(app.load(), vitrine={"path": "/w/v", "command": "x",
                                       "port": 9102, "enabled": True}))
    assert app.destinataires_alerte("vitrine") == ["chef@example.com"]




def test_l_adresse_d_alerte_d_une_installation_existante_est_reprise(envoi):
    """Avant les alertes par application, tous les destinataires vivaient dans
    alertes.json. Sans repli sur cette liste, une installation existante aurait
    cesse d'etre prevenue a la mise a jour -- silencieusement."""
    _ecrire_bloc_alertes({"ALERTE_ADMIN": ""})
    app.ecrire_alertes(True, ["ancien@example.com"])
    assert app.alertes_admin() == ["ancien@example.com"]




def test_activer_les_alertes_sans_adresse_d_administration_est_refuse(envoi):
    r = envoi.post("/api/alertes", json={"actif": True, "admin": []})
    assert r.status_code == 400, r.data
    assert "administrateur" in r.get_json()["error"]


# ---------- 23 sexies. la surveillance se declenche toute seule ----------
#
# Le diagnostic n'avait AUCUN planning : l'asset ne tournait que si quelqu'un
# allait cliquer "Materialize" dans Dagster. Une surveillance qu'il faut
# declencher ne previent de rien -- on ne la declenche que quand on soupconne
# deja quelque chose.
#
# La consequence depassait le diagnostic : alerte_mail_echec est un
# run_failure_sensor, il reagit a un run EN ECHEC. Aucun run ne demarrant
# jamais tout seul, aucun ne pouvait echouer, donc aucune alerte ne partait.
# Un systeme d'alerte complet, avec son SMTP et son repli, qui n'attendait
# qu'un clic pour servir.
#
# Tests TEXTUELS, et il faut le dire : l'image du panneau ne contient pas
# dagster, donc importer definitions.py ici echouerait. Ils constatent que le
# planning est declare et branche, pas qu'il se declenche -- ce qui le prouve,
# c'est la page des schedules de Dagster.




def test_le_diagnostic_a_un_planning_et_il_est_actif():
    src = _definitions_diagnostic()
    assert "ScheduleDefinition(" in src, (
        "aucun planning : l'asset ne tournera que si on le declenche a la main, "
        "et le capteur d'alerte n'aura jamais de run en echec a signaler")
    # Une fois par heure, hors de l'heure pile : toutes les quinze minutes,
    # chaque run chargeait Dagster, le panneau et Postgres 96 fois par jour.
    assert 'cron_schedule="7 * * * *"' in src
    assert "*/15" not in src
    assert "DefaultScheduleStatus.RUNNING" in src, (
        "un planning qu'il faut activer a la main dans l'interface : on ne "
        "s'apercoit de l'oubli qu'en ratant une alerte")




def test_le_planning_est_branche_dans_les_definitions():
    """Declare ne suffit pas : Dagster ne voit que ce qui est dans defs.

    Sans cette verification, un planning parfaitement ecrit mais absent de
    Definitions() passerait le test precedent tout en ne tournant jamais.
    """
    src = _definitions_diagnostic()
    bloc = src[src.index("defs = Definitions("):]
    assert "schedules=[" in bloc, "le planning n'est pas passe a Definitions()"
    assert "jobs=[" in bloc, "le job du planning n'est pas passe a Definitions()"
    assert "sensors=[" in bloc




def test_la_verification_approfondie_reste_a_la_demande():
    """Ce qui AGIT ne se planifie pas.

    run_tests() et la suite du panneau ecrivent, traversent le proxy et
    laissent des traces. Les jouer quatre fois par heure remplirait les
    journaux de traces que personne n'a demandees. Seules les sondes en
    lecture seule tournent toutes les heures.
    """
    src = _definitions_diagnostic()
    assert "run_tests" not in src, (
        "la verification approfondie est entree dans le code planifie")
    assert "lancer_suite_du_panneau" not in src


# ---------- 29. il n'y avait aucune sauvegarde ----------
#
# Rien ne copiait utilisateurs.json, apps.json, le journal des acces ni la
# moindre base. Un disque perdu, et il fallait tout redeclarer de memoire :
# les comptes, les droits par projet, les applications, les categories,
# l'adresse publique -- et les donnees, elles, ne se redeclarent pas.
#
# Le partage des roles n'est pas un choix d'organisation : pg_dump REFUSE de
# sauvegarder un serveur plus recent que lui. Le panneau tourne sur une image
# dont le client Postgres a une version de retard ; codelab-postgres, lui,
# porte le pg_dump de la version exacte du serveur. Chacun sauvegarde ce
# qu'il est SEUL a pouvoir sauvegarder.




def test_l_archive_de_l_etat_porte_ce_qui_ne_se_redeclare_pas(sauvegardes):
    """Les comptes, les applications, le journal. Pas les journaux
    d'applications : ils se regenerent, et ils sont de loin ce qui pese."""
    _, cible = sauvegardes
    app.save({"demo": {"path": "/w/d", "command": "x", "port": 9101}})
    app.journaliser("connexion", qui="marie", ip="10.0.0.1")
    chemin = app.sauvegarder_l_etat()
    import sqlite3
    import tarfile
    with tarfile.open(chemin) as archive:
        noms = {n.lstrip("./") for n in archive.getnames()}
        # La base y est sous forme d'INSTANTANE coherent, pris par SQLite --
        # jamais le fichier vivant ni son -wal.
        archive.extract("./codelab.db", path=cible / "restauree")
    assert "codelab.db" in noms
    assert not {"codelab.db-wal", "codelab.db-shm"} & noms
    cx = sqlite3.connect(cible / "restauree" / "codelab.db")
    assert [r[0] for r in cx.execute("SELECT nom FROM applications")] == ["demo"]
    assert cx.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
    cx.close()
    # Les journaux d'applications pesent et se regenerent ; le dossier
    # personnel des applications est un cache, ECRIVABLE PAR ELLES -- l'y
    # inclure leur laisserait decider du poids de la sauvegarde.
    for exclu in app.ARCHIVE_EXCLUS:
        assert not [n for n in noms if n.startswith(exclu)], (exclu, sorted(noms))
    # 0600 : l'archive porte les empreintes de mots de passe et le journal
    # des acces. Les applications tournent sous un autre uid, et n'ont aucune
    # raison de pouvoir la lire.
    assert oct(os.stat(chemin).st_mode)[-3:] == "600"
    # Et aucun secret : credentials.env ne vit pas dans le dossier d'etat, et
    # c'est ce qui permet de recopier l'archive ailleurs sans la vider.
    assert not [n for n in noms if "credentials" in n]




def test_une_archive_a_moitie_ecrite_ne_prend_pas_la_place_de_la_bonne(sauvegardes):
    """Un arret au milieu de l'ecriture laisserait sinon une archive tronquee
    portant l'heure la plus recente -- celle qu'on choisirait pour
    restaurer."""
    source = inspect.getsource(app.sauvegarder_l_etat)
    assert ".partielle" in source and "os.replace(" in source
    # Elle est aussi RELUE avant d'etre gardee, comme les dumps Postgres.
    assert "getmembers()" in source
    _, cible = sauvegardes
    app.sauvegarder_l_etat()
    assert not [n for n in os.listdir(cible) if n.endswith(".partielle")]




def test_la_rotation_garde_les_plus_recentes(sauvegardes, monkeypatch):
    _, cible = sauvegardes
    monkeypatch.setattr(app, "SAUVEGARDES_GARDEES", 3)
    for jour in range(6):
        (cible / f"{app.SAUVEGARDE_PREFIXE}2026010{jour}-000000.tar.gz").write_text("x")
    app._rotation_sauvegardes()
    restantes = sorted(os.listdir(cible))
    assert len(restantes) == 3
    # Les trois DERNIERES : le nom porte la date, donc l'ordre alphabetique
    # est l'ordre chronologique -- y compris au passage d'une annee, la ou un
    # tri sur la date de fichier suivrait une copie.
    assert restantes[0].endswith("20260103-000000.tar.gz")




def test_par_defaut_seule_la_derniere_sauvegarde_reste(sauvegardes, monkeypatch):
    """Vecu : « Gestion des sauvegardes, garder que la derniere pour ne pas
    encombrer le stockage ». La rotation passe APRES l'ecriture et la
    relecture : la plus recente remplace l'ancienne, jamais l'inverse."""
    _, cible = sauvegardes
    assert app.SAUVEGARDES_GARDEES == 1 and app.WORKSPACE_GARDEES == 1
    ancienne = cible / f"{app.SAUVEGARDE_PREFIXE}20200101-000000.tar.gz"
    ancienne.write_text("x")
    nouvelle = app.sauvegarder_l_etat()
    assert sorted(os.listdir(cible)) == [os.path.basename(nouvelle)]
    # Et Postgres, par base.
    entree = open(os.path.join(_racine_depot(), "postgres", "entrypoint.sh"), encoding="utf-8").read()
    assert 'SAUVEGARDES_GARDEES="${CODELAB_SAUVEGARDES_GARDEES:-1}"' in entree




def test_sans_volume_monte_le_panneau_ne_sauvegarde_pas(monkeypatch, tmp_path):
    """Ecrire dans la couche du conteneur donnerait une sauvegarde qui
    disparait au premier « docker compose down » : pire que rien, parce
    qu'on croit en avoir une."""
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(tmp_path / "absent"))
    assert not app.sauvegardes_actives()
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(tmp_path))
    assert app.sauvegardes_actives()
    monkeypatch.setattr(app, "SAUVEGARDE_HEURES", 0)
    assert not app.sauvegardes_actives()




def test_la_sonde_des_sauvegardes_distingue_les_trois_cas(tmp_path):
    """Absente, en retard, morte : trois rangs, trois gestes differents."""
    dossier = tmp_path / "sauvegardes"
    dossier.mkdir()

    # Rien encore : une alerte, pas une panne -- au premier demarrage, c'est
    # l'etat normal pendant quelques secondes.
    etat, _, detail = check_sauvegardes(str(dossier))
    assert etat is Etat.ALERTE and "rien pour" in detail

    def poser(nom, heures):
        f = dossier / nom
        f.write_text("x")
        quand = time.time() - heures * 3600
        os.utime(f, (quand, quand))

    poser("base-codelab-20260101-000000.dump", 2)
    poser("panneau-20260101-000000.tar.gz", 2)
    assert check_sauvegardes(str(dossier))[0] is Etat.OK

    # Un passage manque. C'est la PLUS RECENTE de chaque famille qui compte :
    # en ajouter une vieille a cote d'une fraiche ne change rien, et c'est
    # bien ce qu'on veut -- sinon la rotation elle-meme declencherait
    # l'alerte.
    poser("panneau-20260101-000000.tar.gz", SAUVEGARDE_FRAICHE_H + 1)
    assert check_sauvegardes(str(dossier))[0] is Etat.ALERTE

    # Plus rien depuis trois jours : ce n'est plus un retard, c'est arrete.
    poser("panneau-20260101-000000.tar.gz", SAUVEGARDE_VIEILLE_H + 1)
    etat, _, detail = check_sauvegardes(str(dossier))
    assert etat is Etat.ECHEC and "ne tourne plus" in detail

    # Volume absent : le compose est a completer.
    etat, _, detail = check_sauvegardes(str(tmp_path / "jamais"))
    assert etat is Etat.ALERTE and "docker-compose.yml" in detail




def test_les_deux_services_ecrivent_dans_le_meme_dossier():
    """Les dumps et l'archive de l'etat au meme endroit : on copie un seul
    dossier pour tout emporter."""
    for chemin in _composes():
        services = _services_du_compose(chemin)
        texte = open(chemin, encoding="utf-8").read()
        assert texte.count("/sauvegardes:/sauvegardes") == 2, (
            f"{os.path.basename(chemin)} : les deux services doivent monter "
            f"le volume de sauvegarde")
        assert set(services) >= {"codelab-postgres", "codelab-app-manager"}




def test_la_sauvegarde_des_bases_est_verifiee_avant_d_etre_gardee():
    """Une sauvegarde qu'on decouvre illisible le jour de la restauration ne
    vaut pas mieux que pas de sauvegarde."""
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "entrypoint.sh")
    if not os.path.exists(chemin):
        pytest.skip("entrypoint postgres absent de cette image")
    script = open(chemin, encoding="utf-8").read()
    assert "pg_dump" in script and "-Fc" in script
    # La relecture complete du fichier, et le retrait s'il ne passe pas.
    bloc = script.split("sauvegarder_une_base()")[1].split("\n}")[0]
    assert "pg_restore --list" in bloc
    assert bloc.count("rm -f \"$fichier\"") == 2, (
        "un dump illisible ou rate doit etre ecarte, jamais garde")
    # Et la rotation, par base.
    assert "SAUVEGARDES_GARDEES" in bloc




def test_la_procedure_d_incident_ne_cite_que_ce_qui_existe():
    """Vecu : « une procedure d'incident ». Une page qu'on lit le jour ou tout
    va mal ne doit pas citer un conteneur renomme, une variable disparue ou
    un lien casse : ce jour-la, on n'a pas le temps de deviner. Chaque nom
    qu'elle cite est verifie contre le compose et le code."""
    racine = os.path.join(os.path.dirname(DOSSIER_PANNEAU))
    page = open(os.path.join(racine, "INCIDENTS.md"), encoding="utf-8").read()
    compose = open(os.path.join(racine, "docker-compose.yml"), encoding="utf-8").read()
    source = _lire_source(os.path.join(DOSSIER_PANNEAU, "app", "app.py"))
    # Les conteneurs nommes dans une commande (docker logs, restart...).
    conteneurs = set(re.findall(r"(?:docker logs[^\n]*?|restart |up -d |exec -i |exec )(codelab-[a-z-]+)", page))
    assert {"codelab-app-manager"} <= conteneurs
    for conteneur in conteneurs:
        assert f"container_name: {conteneur}" in compose, conteneur
    for variable in set(re.findall(r"\bAPP_MANAGER_[A-Z0-9_]+\b", page)):
        assert variable in source or variable in compose, variable
    assert 'APP_MANAGER_ADMIN_2FA: "0"' in compose, "la marche arriere du second facteur"
    for chemin in set(re.findall(r"/DATA/AppData/codelab/([a-z-]+)", page)):
        assert f"/DATA/AppData/codelab/{chemin}" in compose or chemin == "config", chemin
    # Les liens internes visent des titres qui existent.
    def ancres(texte):
        out = set()
        for t in re.findall(r"^#+ (.+)$", texte, re.M):
            a = re.sub(r"[^\w\- ]", "", t.lower()).replace(" ", "-")
            out.add(a)
        return out
    liens = re.findall(r"\]\(([A-Za-z/._-]*)#([^)]+)\)", page)
    assert len(liens) >= 10, "les renvois de la table et des sections"
    for cible, ancre_lien in liens:
        fichier = os.path.join(racine, cible) if cible else os.path.join(racine, "INCIDENTS.md")
        assert ancre_lien in ancres(open(fichier, encoding="utf-8").read()), (cible, ancre_lien)
    # On la trouve depuis le README, et la liste de mise en production y renvoie.
    assert "(INCIDENTS.md)" in open(os.path.join(racine, "README.md"), encoding="utf-8").read()
    assert "(INCIDENTS.md)" in open(os.path.join(racine, "SECURITE.md"), encoding="utf-8").read()




def test_la_restauration_est_documentee():
    """Une sauvegarde dont personne ne sait se servir n'est pas une
    sauvegarde. La procedure doit etre ecrite, avec les commandes exactes."""
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "README.md")
    if not os.path.exists(chemin):
        pytest.skip("README absent de cette image")
    texte = open(chemin, encoding="utf-8").read()
    assert "pg_restore" in texte, "la restauration des bases n'est pas ecrite"
    assert "panneau-" in texte and "tar" in texte, (
        "la restauration de l'etat du panneau n'est pas ecrite")
    assert "/sauvegardes" in texte




# ---------- Mailpit : l'envoi de mails, garde et relaye ----------

def test_mailpit_est_dans_la_stack_ferme_et_derriere_le_proxy():
    for chemin in _composes():
        bloc = _bloc_du_service(chemin, "codelab-mailpit")
        assert bloc, f"codelab-mailpit absent de {os.path.basename(chemin)}"
        assert "ports:" not in bloc, chemin
        assert "no-new-privileges:true" in bloc and "- ALL" in bloc, chemin
        assert 'CODELAB_MAILPIT_RELAIS: "auto"' in bloc, chemin
        proxy = _bloc_du_service(chemin, "codelab-dagster-proxy")
        assert '"8025:8025"' in proxy, chemin
    racine = _racine_depot()
    ci = open(os.path.join(racine, ".github", "workflows", "build-images.yml"),
              encoding="utf-8").read()
    assert "codelab-mailpit mailpit/Dockerfile mailpit/" in ci and '"mailpit/**"' in ci
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    emplacement = conf[conf.index("location / {"):]
    # L'autorisation du panneau d'abord, puis le mot de passe interne.
    assert "auth_request /_auth;" in emplacement
    assert "include /etc/nginx/codelab/mailpit-auth.conf;" in emplacement
    dockerfile = open(os.path.join(racine, "dagster", "proxy", "Dockerfile"), encoding="utf-8").read()
    assert "echo 'return 503;' > /etc/nginx/codelab/mailpit-auth.conf" in dockerfile




def test_le_proxy_genere_le_mot_de_passe_interne_de_mailpit(tmp_path):
    import base64 as b64
    import subprocess
    (tmp_path / "config").mkdir()
    env_file = tmp_path / "config" / "credentials.env"
    env_file.write_text("# ===== codelab-postgres =====\nPOSTGRES_PASSWORD=y\n"
                        "# ===== /codelab-postgres =====\n", encoding="utf-8")
    include = tmp_path / "ng" / "mailpit-auth.conf"
    env = {"CODELAB_CONFIG_DIR": str(tmp_path / "config"),
           "CODELAB_MAILPIT_AUTH_INCLUDE": str(include), "PATH": "/usr/bin:/bin"}
    script = os.path.join(_racine_depot(), "dagster", "proxy", "entrypoint.sh")
    for _ in range(2):
        subprocess.run(["sh", script, "true"], check=True, timeout=30, env=env,
                       capture_output=True)
    texte = env_file.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=y" in texte
    assert texte.count("# ===== codelab-proxy =====") == 1
    mdp = re.search(r"^CODELAB_INTERNE_MAILPIT_UI_PASSWORD=([0-9a-f]{64})$", texte, re.M).group(1)
    attendu = b64.b64encode(f"codelab:{mdp}".encode()).decode()
    assert include.read_text() == f'proxy_set_header Authorization "Basic {attendu}";\n'
    assert stat.S_IMODE(include.stat().st_mode) == 0o600




def test_mailpit_relaie_par_le_serveur_du_panneau(tmp_path):
    blocs = (_UI + "CODELAB_INTERNE_RELAIS_HOST=smtp.gmail.com\n"
             "CODELAB_INTERNE_RELAIS_PORT=587\nCODELAB_INTERNE_RELAIS_TLS=starttls\n"
             "CODELAB_INTERNE_RELAIS_USER=moi@gmail.com\n"
             "CODELAB_INTERNE_RELAIS_PASSWORD=abcd efgh\n"
             "CODELAB_INTERNE_RELAIS_FROM=moi@gmail.com\n")
    proc, vu = _lancer_entrypoint_mailpit(tmp_path, blocs)
    assert proc.returncode == 0, proc.stderr
    assert vu["MP_UI_AUTH"] == "codelab:" + "a" * 64
    assert vu["MP_SMTP_RELAY_ALL"] == "true"
    assert vu["MP_SMTP_RELAY_HOST"] == "smtp.gmail.com" and vu["MP_SMTP_RELAY_PORT"] == "587"
    assert vu["MP_SMTP_RELAY_STARTTLS"] == "true" and "MP_SMTP_RELAY_TLS" not in vu
    assert vu["MP_SMTP_RELAY_AUTH"] == "plain"
    assert vu["MP_SMTP_RELAY_PASSWORD"] == "abcd efgh"
    assert vu["MP_SMTP_RELAY_OVERRIDE_FROM"] == "moi@gmail.com"
    # Le mot de passe du serveur d'envoi ne sort pas dans les journaux.
    assert "abcd efgh" not in proc.stdout + proc.stderr
    texte = (tmp_path / "config" / "credentials.env").read_text()
    assert "MAIL_HOST=codelab-mailpit\nMAIL_PORT=1025\n" in texte




def test_mailpit_en_ssl_et_en_mode_test(tmp_path):
    blocs = (_UI + "CODELAB_INTERNE_RELAIS_HOST=ssl0.ovh.net\n"
             "CODELAB_INTERNE_RELAIS_PORT=465\nCODELAB_INTERNE_RELAIS_TLS=ssl\n")
    _, vu = _lancer_entrypoint_mailpit(tmp_path, blocs)
    assert vu["MP_SMTP_RELAY_TLS"] == "true" and "MP_SMTP_RELAY_STARTTLS" not in vu
    assert vu["MP_SMTP_RELAY_AUTH"] == "none"
    _, vu = _lancer_entrypoint_mailpit(tmp_path, blocs, {"CODELAB_MAILPIT_RELAIS": "non"})
    assert not [c for c in vu if c.startswith("MP_SMTP_RELAY")], vu
    # Sans serveur d'envoi : tout est garde, rien n'est relaye.
    _, vu = _lancer_entrypoint_mailpit(tmp_path, _UI)
    assert not [c for c in vu if c.startswith("MP_SMTP_RELAY")], vu




def test_mailpit_refuse_de_demarrer_sans_le_mot_de_passe_interne(tmp_path):
    """Sans lui, son API serait ouverte a toutes les applications du reseau."""
    proc, vu = _lancer_entrypoint_mailpit(tmp_path, "")
    assert proc.returncode == 1
    assert vu == {}, "Mailpit a ete lance sans mot de passe"




def test_le_panneau_publie_le_relais_mais_ne_le_transmet_pas(tmp_path, monkeypatch):
    env_file = tmp_path / "credentials.env"
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(env_file))
    monkeypatch.setattr(app, "lire_smtp_personnalise", lambda: app._normalise_smtp({
        "host": "smtp.gmail.com", "port": 587, "tls": "starttls",
        "user": "moi@gmail.com", "password": "secret"}))
    assert app.publier_relais_mail()
    texte = env_file.read_text()
    assert "CODELAB_INTERNE_RELAIS_HOST=smtp.gmail.com" in texte
    assert "CODELAB_INTERNE_RELAIS_PASSWORD=secret" in texte
    assert "CODELAB_INTERNE_RELAIS_FROM=moi@gmail.com" in texte
    with open(env_file, "a") as f:
        f.write("MAIL_HOST=codelab-mailpit\nCODELAB_INTERNE_MAILPIT_UI_PASSWORD=x\n")
    transmis = app.secrets_partages()
    assert transmis.get("MAIL_HOST") == "codelab-mailpit"
    assert not [c for c in transmis if c.startswith("CODELAB_INTERNE_")], transmis
    # Et il suit un enregistrement depuis Parametres > E-mail.
    assert "publier_relais_mail()" in _source_du_panneau()




# ---------- la tuile Mailpit ----------

def test_mailpit_a_sa_tuile_dans_le_hub_et_mene_au_port_8025(hub_dagster):
    hub_dagster.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = hub_dagster.get("/api/mes-apps").get_json()
    (mailpit,) = [s for s in d["services"] if s["name"] == app.MAILPIT_NOM]
    assert mailpit["url"].endswith(":8025/"), mailpit["url"]
    assert mailpit["externe"] is True and mailpit["par_defaut"] is True
    assert mailpit["titre"] == "Mailpit"
    # La porte du panneau journalise puis redirige vers le proxy.
    r = hub_dagster.get("/mailpit/")
    assert r.status_code == 302 and r.headers["Location"].endswith(":8025/")
    # Son icone, dessinee, distincte des autres.
    icone = hub_dagster.get("/api/icon/mailpit")
    assert icone.mimetype == "image/svg+xml" and b"<text" not in icone.data
    assert icone.data != hub_dagster.get("/api/icon/dagster").data




def test_mailpit_est_reserve_a_l_administrateur(hub_dagster):
    """Son interface montre tous les mails, liens de reinitialisation compris."""
    c = hub_dagster
    _connecte(c, "marie", "un-mot-de-passe")
    assert c.get("/mailpit/").status_code in (302, 403, 404)
    assert c.get("/mailpit/").headers.get("Location", "").endswith(":8025/") is False
    d = c.get("/api/mes-apps").get_json()
    assert not [s for s in d.get("services", []) if s["name"] == app.MAILPIT_NOM]
    assert c.post("/api/mes-apps/mailpit/masquer").status_code == 404




def test_mailpit_se_range_et_ne_se_configure_pas(hub, monkeypatch):
    monkeypatch.setattr(app, "origine_mailpit", lambda: "http://x:8025/")
    c = _admin_du_hub(hub)
    assert c.post("/api/mes-apps/mailpit/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert app.MAILPIT_NOM not in [s["name"] for s in d["services"]]
    assert app.MAILPIT_NOM in [s["name"] for s in d["services_masques"]]
    assert "par défaut" in app.refus_configuration(app.MAILPIT_NOM)
    assert app.refus_action_protegee(app.MAILPIT_NOM, "arrêt") is not None




def test_la_surveillance_de_mailpit_ne_note_que_les_changements(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "_mailpit_suivi", {"debout": None})
    etats = iter([(True, 3.0), (True, 4.0), (False, 5000.0)])
    monkeypatch.setattr(app, "mailpit_sonde", lambda: next(etats))
    for _ in range(3):
        app.mailpit_tick()
    lignes = (tmp_path / "mailpit.log").read_text().splitlines()
    assert len(lignes) == 2, lignes
    assert "Mailpit répond" in lignes[0] and "ne répond pas" in lignes[1]




# ------------- Erreurs, pannes, annuler, hors ligne, sauvegardes -------------
#
# Vecu : une liste d'ameliorations relue ensemble, puis « OK » pour le premier
# lot -- toujours dire quoi faire apres une erreur, garder le message, montrer
# la cause d'une panne, pouvoir annuler, rester utilisable quand le serveur
# se tait, et signaler des sauvegardes restees sur le seul serveur.

def test_une_exception_donne_une_reference_et_jamais_la_pile(hub, monkeypatch, capsys):
    monkeypatch.setitem(app.flask_app.config, "PROPAGATE_EXCEPTIONS", False)
    def casse():
        raise RuntimeError("detail interne a ne pas montrer")
    monkeypatch.setattr(app, "lire_categories", casse)
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = admin.get("/api/categories")
    assert r.status_code == 500
    ref = r.headers["X-CodeLab-Ref"]
    assert re.fullmatch(r"[0-9A-F]{6}", ref)
    d = r.get_json()
    assert d["ref"] == ref and "detail interne" not in r.get_data(as_text=True)
    sortie = capsys.readouterr().out
    assert f"erreur {ref} : GET /api/categories" in sortie and "detail interne" in sortie




def test_toute_reponse_5xx_du_panneau_porte_une_reference(hub, monkeypatch, capsys):
    def plein(_):
        raise OSError("disque plein")
    monkeypatch.setattr(app, "ecrire_categories", plein)
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = admin.put("/api/categories", json={"categories": ["Outils"]})
    assert r.status_code == 500 and r.headers.get("X-CodeLab-Ref")
    assert f"erreur {r.headers['X-CodeLab-Ref']}" in capsys.readouterr().out
    # Une reponse qui marche n'en porte pas.
    assert "X-CodeLab-Ref" not in admin.get("/api/categories").headers




def test_un_echec_dit_quoi_faire_et_donne_la_reference():
    page = _page_panneau("dashboard.html")
    aide = page.split("async function echec(r, defaut){")[1].split("\n}")[0]
    assert "(réponse" not in aide
    assert "X-CodeLab-Ref" in aide and "docker logs codelab-app-manager" in aide
    assert "donne-la à l'administrateur" in aide
    assert "Réessaie dans un instant." in aide




def test_une_erreur_reste_jusqu_a_ce_qu_on_la_ferme():
    page = _page_panneau("dashboard.html")
    notif = page.split("function notifier(texte, genre, action){")[1].split("\n}")[0]
    assert "if(!err) setTimeout(" in notif           # seuls les succes s'en vont
    assert "toast-fermer" in notif and "Fermer le message" in notif
    assert "erreurs.length>=3" in notif              # pas un mur d'erreurs
    assert "toast-action" in notif                   # le bouton « Annuler »




def test_masquer_ou_retirer_se_rattrape():
    page = _page_panneau("dashboard.html")
    masquage = page.split("async function basculerMasquage(nom, masquer){")[1].split("\n}")[0]
    assert "libelle:'Annuler', faire:()=>basculerMasquage(nom, false)" in masquage
    retirer = page.split("function hubRetirer(cle){")[1].split("\n}")[0]
    assert "basculerMasquage(e.id, false)" in retirer
    assert "faire:()=>enregistrerDisposition(elements)" in retirer




def test_la_fiche_montre_la_cause_d_une_panne():
    page = _page_panneau("dashboard.html")
    assert 'id="fa-cause"' in page and 'id="fa-cause-lignes"' in page
    cause = page.split("async function peindreCauseFiche(a){")[1].split("\n}")[0]
    for etat in ("a.crash_looping", "a.failed", "a.listening===false"):
        assert etat in cause, etat
    assert "/api/logs/" in cause and ".slice(-15)" in cause
    assert "a.externe || a.par_defaut ? ''" in cause   # rien pour un service
    assert "peindreCauseFiche(a);" in page.split("function peindreFiche(){")[1].split("\n}")[0]




def test_l_en_tete_de_la_fiche_reste_en_haut_sur_grand_ecran():
    page = _page_panneau("dashboard.html")
    regle = page.split("@media (min-width:761px){\n  #sec-app .fiche-tete{")[1].split("}")[0]
    assert "position:sticky" in regle and "top:52px" in regle




def test_un_serveur_injoignable_allume_un_bandeau_et_retente():
    page = _page_panneau("dashboard.html")
    assert 'id="hors-ligne"' in page
    enveloppe = page.split("const fetchAvecJeton = window.fetch;")[1].split("function passerHorsLigne")[0]
    assert "e.name!=='AbortError'" in enveloppe
    assert "!r.headers.get('X-CodeLab-Ref')" in enveloppe   # une panne du panneau n'est pas une coupure
    assert "horsLigneCompte=5;" in page
    essai = page.split("async function essayerConnexion(){")[1].split("\n}")[0]
    assert "fetchNu('/health'" in essai
    assert "window.addEventListener('offline', passerHorsLigne);" in page




def test_les_sauvegardes_restees_sur_le_serveur_sont_signalees(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    d = admin.get("/api/sauvegardes").get_json()
    assert [a["nom"] for a in d["archives"]][0] in ("panneau-20261011-030000.tar.gz",
                                                   "workspace-20261011-031000.tar.gz")
    assert "notes.txt" not in [a["nom"] for a in d["archives"]]
    assert d["alerte_hors_serveur"] is True and d["derniere_copie"] is None




def test_telecharger_une_sauvegarde_la_sort_du_serveur(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    r = admin.get("/api/sauvegardes/workspace-20261011-031000.tar.gz")
    assert r.status_code == 200 and r.data == b"w" * 4096
    assert "attachment" in r.headers["Content-Disposition"]
    d = admin.get("/api/sauvegardes").get_json()
    assert d["derniere_copie"] and d["alerte_hors_serveur"] is False
    assert any(e.get("genre") == "sauvegarde" and e.get("action") == "telechargee"
               for e in app.lire_acces())




def test_seuls_les_fichiers_de_sauvegarde_se_telechargent(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    for nom in ("notes.txt", "..%2Fsuivi.json", "panneau-x.tar.gz",
                "panneau-20991231-000000.tar.gz"):
        assert admin.get(f"/api/sauvegardes/{nom}").status_code == 404, nom




def test_une_copie_declaree_ailleurs_eteint_l_alerte(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    d = admin.put("/api/sauvegardes/copie", json={"externe": True}).get_json()
    assert d["copie_externe"] is True and d["alerte_hors_serveur"] is False
    d = admin.put("/api/sauvegardes/copie", json={"externe": False}).get_json()
    assert d["alerte_hors_serveur"] is True




def test_les_sauvegardes_sont_reservees_a_l_administrateur(archives_sauvegarde):
    admin, _ = archives_sauvegarde
    marie = _connexion_marie(app.flask_app.test_client())
    assert marie.get("/api/sauvegardes").status_code in (401, 403)
    assert marie.get("/api/sauvegardes/panneau-20261011-030000.tar.gz").status_code in (401, 403)
    assert marie.put("/api/sauvegardes/copie", json={"externe": True}).status_code in (401, 403)




def test_la_securite_montre_les_sauvegardes_a_l_administrateur():
    page = _page_panneau("dashboard.html")
    securite = page.split('<div class="onglet-p" id="param-securite">')[1].split('<div class="onglet-p"')[0]
    assert 'id="zone-sauvegardes" hidden' in securite
    assert "Elles ne sont que sur ce serveur." in securite
    assert "if(estAdmin) chargerSauvegardes();" in page




def test_un_plantage_au_lancement_reste_visible_dans_la_fiche(survivants):
    """« Arretee » seul ne disait pas qu'elle avait plante : le lancement
    rate est retenu (et la fiche en montre la cause), puis oublie au premier
    demarrage reussi."""
    apps = app.load()
    apps["site"]["command"] = "echo 'ModuleNotFoundError: flask' && exit 3"
    app.save(apps)
    erreur = app.start("site")
    assert erreur and "aussitôt (code 3)" in erreur
    echec = app.load()["site"]["dernier_echec"]
    assert echec["code"] == 3 and echec["quand"] > 0
    apps = app.load()
    apps["site"]["command"] = "sleep 120"
    app.save(apps)
    assert app.start("site") is None
    assert "dernier_echec" not in app.load()["site"]




# ------------------------ La version de CodeLab ------------------------
#
# Vecu : « je ne vois pas les modifs » -- l'image n'avait pas ete retiree,
# et rien dans le panneau ne le disait. Puis, la verification aupres de
# GitHub ecrite : « annule la possibilite alors. C'est un risque a prendre
# inutile ». Le panneau affiche la version qu'il porte, et ne contacte rien.

def test_le_panneau_dit_quelle_version_tourne(hub, monkeypatch):
    monkeypatch.setattr(app, "CODELAB_VERSION", "a" * 40)
    monkeypatch.setattr(app, "CODELAB_VERSION_DATE", "2026-10-12T09:00:00+02:00")
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = admin.get("/api/version").get_json()
    assert d["court"] == "aaaaaaa" and d["dev"] is False and d["date"].startswith("2026-10-12")
    monkeypatch.setattr(app, "CODELAB_VERSION", "dev")
    d = admin.get("/api/version").get_json()
    assert d["dev"] is True and d["sha"] == "" and d["court"] == ""
    marie = _connexion_marie(app.flask_app.test_client())
    assert marie.get("/api/version").status_code in (401, 403)




def test_le_panneau_ne_contacte_jamais_github():
    """Aucun appel sortant pour la version : ni fonction qui interroge
    l'API, ni boucle de verification, ni route pour la declencher."""
    source = _page_panneau("app.py")
    assert "api.github.com" not in source
    for trace in ("def verifier_mise_a_jour", "_github(", "/api/version/verifier",
                  "APP_MANAGER_VERIFIER_MAJ"):
        assert trace not in source, trace
    page = _page_panneau("dashboard.html")
    assert "/api/version/verifier" not in page and "maj-dispo" not in page




def test_le_panneau_construit_avec_le_node_du_poste_de_developpement():
    """Vecu : le panneau prenait nodejs et npm de Debian (Node 20) quand
    codelab-dev developpe sous Node 24 -- un projet qui se construisait
    dans le poste pouvait echouer au Build. Et npm de Debian tirait pres de
    400 paquets, un second python3 compris : environ 75 Mo de plus que le
    binaire officiel."""
    racine = _racine_depot()
    docker = open(os.path.join(racine, "app-manager", "Dockerfile"), encoding="utf-8").read()
    assert "FROM node:24-trixie-slim AS node" in docker
    assert "COPY --from=node /usr/local/bin/node /usr/local/bin/node" in docker
    paquets = docker.split("RUN apt-get update")[1].split("rm -rf /var/lib/apt/lists")[0]
    for retire in ("nodejs", "npm"):
        assert retire not in paquets.split(), f"{retire} revient depuis apt"
    assert "deb.nodesource.com/setup_24.x" in _dockerfile_dev(), (
        "le poste a change de branche de Node : le panneau doit suivre")
    # Meme distribution que la base Python : meme libc, meme libstdc++.
    assert "FROM python:3.13-slim" in docker and "trixie" in docker




def test_l_image_porte_sa_version_et_la_ci_la_pose():
    racine = _racine_depot()
    docker = open(os.path.join(racine, "app-manager", "Dockerfile"), encoding="utf-8").read()
    assert "ARG CODELAB_VERSION=dev" in docker
    assert "ENV CODELAB_VERSION=${CODELAB_VERSION} CODELAB_VERSION_DATE=${CODELAB_VERSION_DATE}" in docker
    # En dernier : la version change a chaque build et ne doit pas casser le cache.
    assert docker.index("ARG CODELAB_VERSION=dev") > docker.index("COPY app-manager/app ")
    ci = open(os.path.join(racine, ".github", "workflows", "build-images.yml"), encoding="utf-8").read()
    assert "CODELAB_VERSION=${{ github.sha }}" in ci
    assert "CODELAB_VERSION_DATE=${{ steps.version.outputs.date }}" in ci




def test_les_parametres_montrent_la_version_et_comment_mettre_a_jour():
    page = _page_panneau("dashboard.html")
    # Plus d'onglet « Serveur » : la version est dans « Compte », pour
    # l'administrateur seulement.
    compte = page.split('<div class="onglet-p" id="param-compte">')[1].split('<div class="onglet-p"')[0]
    assert 'id="zone-version"' in compte and 'id="ver-carte"' in compte
    assert "$('zone-version').remove();" in page
    peint = page.split("function peindreVersion(d){")[1].split("\n}")[0]
    # Le lien des nouveautes : c'est le navigateur qui l'ouvre, sur un clic.
    assert "/compare/${esc(d.sha)}...main" in peint
    assert "COMMANDES_MAJ" in peint and "ver-copier" in peint
    assert "if(estAdmin) chargerVersion();" in page and "if(estAdmin) chargerCategories();" in page
