"""
CodeLab -- TESTS : LES MESSAGES
---------------------------------------------------------------------------
ROLE        la messagerie : le relais du hub, l'application Messages
            (workspace/messages) et sa boite, ses taches et ses projets, la
            reprise des anciennes donnees ; les notifications Web Push.
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


# ---------- 12 sexies. un mot laisse depuis le hub ----------
#
# Trois sujets et pas un de plus : une application a laquelle on a acces, le
# hub lui-meme, ou l'idee d'une application qui manque. Un champ libre aurait
# demande a l'administrateur de deviner de quoi on parle -- ce qu'il ne peut
# pas faire depuis un mail de trois lignes.
#
# Le hub du panneau relaie ; l'application Messages (workspace/messages)
# range. Ces tests passent par les deux, comme dans l'installation
# (fixture appli_messages).


def _ranges():
    return _application_messages().donnees.lire_messages()


def test_un_message_est_range_puis_envoye_a_l_administrateur(messagerie):
    c, partis = messagerie
    r = c.post("/api/messages", json={"cible": "application", "app": "compta",
                                      "texte": "Le bouton demande deux clics."})
    assert r.status_code == 200, r.data
    assert r.get_json()["envoye"] is True

    garde = _ranges()
    assert len(garde) == 1
    assert garde[0]["qui"] == "marie" and garde[0]["app"] == "compta"
    assert garde[0]["texte"] == "Le bouton demande deux clics."

    destinataires, sujet, corps = partis[-1]
    assert destinataires == ["chef@example.com"]
    assert "marie" in sujet and "compta" in sujet
    assert "Le bouton demande deux clics." in corps


def test_un_message_survit_a_un_envoi_impossible(messagerie, monkeypatch):
    """ENREGISTRE D'ABORD, ENVOYE ENSUITE. Un SMTP mal configure ne doit pas
    faire perdre le message : personne ne saurait jamais qu'il a ete ecrit."""
    c, _partis = messagerie
    def refuse(*a, **kw):
        raise RuntimeError("serveur injoignable")
    monkeypatch.setattr(app, "envoyer_mail", refuse)
    r = c.post("/api/messages", json={"cible": "hub", "texte": "Le hub est lent."})
    assert r.status_code == 200, r.data
    assert r.get_json()["envoye"] is False
    assert len(_ranges()) == 1


def test_une_messagerie_arretee_refuse_sans_rien_perdre(messagerie, monkeypatch):
    """L'application Messages redemarre, ou est tombee : le hub le dit, ne
    previent personne d'un message qui n'est range nulle part, et laisse
    reessayer tout de suite (le delai d'une minute ne compte pas)."""
    c, partis = messagerie
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    monkeypatch.setattr(app, "_vers_messagerie", tombee)
    r = c.post("/api/messages", json={"cible": "hub", "texte": "Le hub est lent."})
    assert r.status_code == 503 and "réessaie" in r.get_json()["error"]
    assert partis == [] and app._dernier_message == {}
    assert c.get("/api/mes-messages").status_code == 503


def test_on_n_ecrit_que_sur_une_application_qu_on_peut_ouvrir(messagerie):
    """Sinon ce formulaire dirait l'existence de projets qu'on n'a pas le
    droit de connaitre -- exactement comme la liste des masquees."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "application", "app": "secret",
                                         "texte": "bonjour"}).status_code == 404
    assert c.post("/api/messages", json={"cible": "application", "app": "fantome",
                                         "texte": "bonjour"}).status_code == 404
    assert _ranges() == [] and partis == []


def test_une_idee_d_application_ne_porte_aucun_projet(messagerie):
    """« Une application qui manque » n'en designe aucune : garder un nom
    envoye au passage ferait croire a un message sur un projet existant."""
    c, _ = messagerie
    r = c.post("/api/messages", json={"cible": "idee", "app": "compta",
                                      "texte": "Il manque un suivi des congés."})
    assert r.status_code == 200, r.data
    assert _ranges()[0]["app"] == ""


def test_un_sujet_invente_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "autre chose",
                                         "texte": "bonjour"}).status_code == 400
    assert _ranges() == []


def test_un_message_vide_ou_trop_long_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "   "}).status_code == 400
    trop = "a" * (_application_messages().donnees.MESSAGE_LONGUEUR_MAX + 1)
    assert c.post("/api/messages", json={"cible": "hub", "texte": trop}).status_code == 400
    assert _ranges() == []
    # Refuse par l'application : le delai d'une minute n'a pas commence.
    assert c.post("/api/messages", json={"cible": "hub", "texte": "bonjour"}).status_code == 200


def test_un_deuxieme_message_immediat_est_refuse(messagerie):
    """De quoi corriger une faute de frappe, pas de quoi remplir la boite de
    l'administrateur."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "un"}).status_code == 200
    r = c.post("/api/messages", json={"cible": "hub", "texte": "deux"})
    assert r.status_code == 429, r.data
    assert len(_ranges()) == 1 and len(partis) == 1


def test_seul_l_administrateur_lit_les_messages(messagerie, appli_messages):
    """Ce sont des mots qui lui sont adresses, et ils portent le nom de qui
    les a ecrits. Le panneau ne les rend a personne ; l'application ne les
    rend qu'au jeton de l'administrateur."""
    c, _ = messagerie
    c.post("/api/messages", json={"cible": "hub", "texte": "bonjour"})
    assert c.get("/api/messages").status_code in (404, 405)
    marie = ClientMessages(appli_messages.module, "marie", "utilisateur")
    assert marie.get("/api/messages").status_code == 403
    d = appli_messages.get("/api/messages").get_json()
    assert [m["texte"] for m in d["messages"]] == ["bonjour"]


def test_les_messages_vivent_dans_la_base_de_l_application():
    """Vecu : « L'application Messages doit etre une application a part
    entiere, exactement comme Diagnostic ». Sa base est la sienne, dans
    Postgres (une base, un compte) ; le panneau n'en garde plus de copie, et
    retire le miroir qu'il en tenait."""
    src = _page_panneau("app.py")
    assert 'cx.execute("DROP TABLE IF EXISTS messages")' in src
    assert "_pg_ecrire_messages" not in src and "def lire_messages(" not in src
    donnees = open(os.path.join(DOSSIER_MESSAGES, "donnees.py"), encoding="utf-8").read()
    assert "psycopg.connect(" in donnees and 'os.environ.get("CODELAB_DB")' in donnees
    assert open(os.path.join(DOSSIER_MESSAGES, ".env"), encoding="utf-8").read().count(
        "CODELAB_DB=messages") == 1


# ---------- 35. les messages ont leur boite ----------
#
# Demande : « Ajoute une rubrique pour les messages. La vue d'ensemble doit
# recenser les messages lies au hub par defaut, et je dois pouvoir filtrer sur
# une autre application. Ajoute la possibilite de supprimer ou retenir les
# messages, mettre un scroll lorsqu'il y en a trop. »


def test_retenir_un_message_le_garde_en_tete(boite):
    """On retient ce qu'on veut traiter : le laisser descendre avec le temps,
    c'est le perdre."""
    c, _ = boite
    vieux = _message(c, "hub", "à traiter", quand=int(time.time()) - 3600)
    _message(c, "hub", "tout frais")

    assert c.put("/api/messages/" + vieux, json={"retenu": True}).status_code == 200
    liste = c.get("/api/messages").get_json()["messages"]
    assert liste[0]["id"] == vieux, "le message retenu n'est pas remonte"
    assert liste[0]["retenu"] is True

    # Et relacher le remet a sa place, par sa date.
    assert c.put("/api/messages/" + vieux, json={"retenu": False}).status_code == 200
    liste = c.get("/api/messages").get_json()["messages"]
    assert liste[0]["id"] != vieux
    assert liste[0]["retenu"] is False


def test_supprimer_un_message_le_retire_de_la_base(boite, capsys):
    c, _ = boite
    garde = _message(c, "hub", "on garde")
    parti = _message(c, "idee", "on jette")

    assert c.delete("/api/messages/" + parti).status_code == 200
    restants = [m["id"] for m in c.get("/api/messages").get_json()["messages"]]
    assert restants == [garde]
    # Dans la base aussi, pas seulement dans la reponse : un message qui
    # revient au redemarrage n'a pas ete supprime.
    with c.donnees.base() as b:
        ids = [l["id"] for l in b.lignes("SELECT id FROM messages")]
    assert parti not in ids and garde in ids
    # Et c'est trace dans le journal de l'application : supprimer un mot qui
    # vous est adresse se retrouve.
    assert f"message {parti} supprime" in capsys.readouterr().out


def test_le_guet_ne_redemande_que_ce_qui_est_arrive_depuis(boite):
    """La page interroge toutes les vingt secondes : lui renvoyer trois cents
    messages pour en decouvrir un serait payer cher une notification. Le
    panneau guette de meme, pour l'annoncer dans son coin."""
    c, panneau = boite
    repere = int(time.time()) - 100
    _message(c, "hub", "avant", quand=repere - 10)
    neuf = _message(c, "hub", "après", quand=repere + 10)

    liste = c.get("/api/messages?apres=%d" % repere).get_json()["messages"]
    assert [m["id"] for m in liste] == [neuf]
    # Un « apres » illisible ne fait pas tomber la route : elle rend tout.
    assert len(c.get("/api/messages?apres=demain").get_json()["messages"]) == 2
    vus = panneau.get("/api/messages/nouveaux?apres=%d" % repere).get_json()["messages"]
    assert vus == [{"id": neuf, "ts": repere + 10, "qui": "marie"}]


def test_les_messages_restent_reserves_a_l_administrateur(boite):
    """Sans jeton, ou avec celui d'un compte : rien. Le controle tient dans
    l'application meme, pas seulement dans le proxy."""
    c, _ = boite
    identifiant = _message(c, "hub", "un mot")
    anonyme = c.module.app.test_client()
    marie = ClientMessages(c.module, "marie", "utilisateur")
    for client in (anonyme, marie):
        assert client.get("/api/messages").status_code == 403
        assert client.get("/").status_code == 403
        assert client.delete("/api/messages/" + identifiant).status_code == 403
        assert client.put("/api/messages/" + identifiant,
                          json={"retenu": True}).status_code == 403
    # Un jeton pour une AUTRE application ne vaut rien ici.
    autre = anonyme.get("/api/messages", headers={
        "X-CodeLab-Auth": app.jeton_signe("compta", "admin", "admin")})
    assert autre.status_code == 403
    # Les routes du panneau ne s'ouvrent qu'au jeton du panneau.
    assert c.post("/interne/oublier", json={"comptes": ["marie"]}).status_code == 403
    assert _message(c, "hub", "toujours la") and len(c.donnees.lire_messages()) == 2


def test_une_page_d_un_autre_site_ne_fait_pas_agir_l_administrateur(boite):
    """CSRF : un formulaire d'un autre site poste du texte brut, que l'API
    lirait ; il ne sait pas poser l'en-tete X-Messages sans la permission du
    serveur, qui ne la donne pas."""
    c, _ = boite
    ident = _message(c, "hub", "un mot")
    jeton = {"X-CodeLab-Auth": app.jeton_signe(app.MESSAGERIE_NOM, "admin", "admin")}
    nu = c.module.app.test_client()
    r = nu.delete("/api/messages/" + ident, headers=jeton)
    assert r.status_code == 403
    r = nu.post("/api/messages/lus", headers=jeton, data='{"x":1}', content_type="text/plain")
    assert r.status_code == 403
    assert nu.get("/api/messages", headers=jeton).status_code == 200
    page = open(os.path.join(DOSSIER_MESSAGES, "index.html"), encoding="utf-8").read()
    assert "h.set('X-Messages', '1');" in page


def test_un_message_inconnu_ne_fait_pas_semblant(boite):
    c, _ = boite
    assert c.delete("/api/messages/jamais-vu").status_code == 404
    assert c.put("/api/messages/jamais-vu", json={"retenu": True}).status_code == 404


def _page_messages():
    return _page_panneau(PAGE_MESSAGES)


def test_l_application_messages_range_et_confirme():
    """La boite de reception : trier, ranger, regrouper ; et rien ne se
    supprime sans confirmation."""
    page = _page_messages()
    for trace in ("'api/messages/ordre'", "'api/messages/grouper'", 'data-agir="grouper"',
                  'data-changer="message-projet"', 'data-agir="message-archiver"',
                  'data-agir="message-tache"', "function suggestions(", 'data-triable'):
        assert trace in page, trace
    for action in ("'message-supprimer'", "'tache-supprimer'"):
        corps = page.split(action)[1].split("\n  },")[0]
        assert "demander(" in corps, action + " supprime sans confirmation"
    corps = page.split("async function projetSupprimer(")[1].split("\n}")[0]
    assert "demander(" in corps
    # Toutes les adresses sont relatives : la page vit sous /messages/ (port
    # des applications) comme a la racine d'un sous-domaine.
    assert "'/api/" not in page and "`/api/" not in page and "/mode.js" not in page


def test_un_message_recu_se_signale_dans_le_coin():
    """« Ajoute des notifications en bas a droite des qu'un message est
    recu. » La page guette, le dit la ou elle dit tout le reste, et mene a
    l'application Messages."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("async function guetterMessages(){")[1].split("\n}")[0]
    assert "/api/messages/nouveaux?apres=" in bloc, "le guet retelecharge toute la boite"
    assert "notifier" in bloc
    assert "location.href=lienOuverture({name:'messages'})" in page.split("function notifierMessage(")[1].split("\n}")[0]
    # surveiller() et non setInterval() : les minuteries s'arretent quand
    # l'onglet passe en arriere-plan.
    assert "surveiller(guetterMessages" in page
    # Et les notifications paraissent bien en bas a droite.
    style = page.split(".toasts{")[1].split("}")[0]
    assert "bottom" in style and "right" in style, style


# ---------- messages : etats et taches ----------

def test_accepter_refuser_et_mettre_de_cote_un_message(boite):
    """La route ne lisait que « retenu » : accepter, refuser et archiver
    etaient ignores (et l'epingle tombait)."""
    c, _ = boite
    ident = _message(c, "hub", "Une idee", retenu=True)
    assert c.put(f"/api/messages/{ident}", json={"statut": "a_reflechir"}).status_code == 200
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "a_reflechir" and m["retenu"]
    assert c.put(f"/api/messages/{ident}", json={"statut": "refuse"}).status_code == 200
    assert c.put(f"/api/messages/{ident}", json={"archive": True}).status_code == 200
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "refuse" and m["archive"] and m["retenu"]
    assert c.put(f"/api/messages/{ident}", json={"statut": "n-importe"}).status_code == 400


def _lignes(c, table):
    with c.donnees.base() as b:
        return b.valeur(f"SELECT COUNT(*) FROM {table}")


def test_un_message_accepte_devient_une_tache(boite):
    c, _ = boite
    ident = _message(c, "hub", "La page ne charge plus")
    r = c.post(f"/api/messages/{ident}/accepter",
               json={"projet": "general", "titre": "  Réparer   la page ",
                     "sous_taches": ["Lire les journaux", "", "Corriger"]})
    assert r.status_code == 200, r.data
    tache = r.get_json()["tache"]
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "accepte"
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["id"] == tache and t["titre"] == "Réparer la page" and t["message"] == ident
    assert [st["titre"] for st in t["sous_taches"]] == ["Lire les journaux", "Corriger"]
    # Cocher, ajouter, retirer.
    st = t["sous_taches"][0]["id"]
    assert c.put(f"/api/sous-taches/{st}", json={"faite": True}).status_code == 200
    assert c.post(f"/api/taches/{tache}/sous-taches", json={"titre": "Prévenir"}).status_code == 200
    assert c.put(f"/api/taches/{tache}", json={"faite": True}).status_code == 200
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["faite"] and t["sous_taches"][0]["faite"] and len(t["sous_taches"]) == 3
    # Un projet qui n'existe pas est refuse ; supprimer la tache emporte ses sous-taches.
    assert c.post("/api/taches", json={"projet": "inconnu", "titre": "x"}).status_code == 400
    assert c.delete(f"/api/taches/{tache}").status_code == 200
    assert _lignes(c, "sous_taches") == 0


def test_l_utilisateur_suit_ses_messages(boite, deux_espaces):
    """Qui a ecrit voit ce que son message devient -- lu, a l'etude,
    planifie, en cours, realise, non retenu -- deduit de ce que fait
    l'administrateur. Il ne voit que ses messages."""
    c, panneau = boite
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")

    def suivi():
        d = u.get("/api/mes-messages").get_json()
        return {m["texte"]: m for m in d["messages"]}, d["nouveaux"]

    a = _message(c, "hub", "La page ne charge plus")
    b = _message(c, "idee", "Un carnet de recettes")
    autre = _message(c, "hub", "Pas le mien", qui="paul")
    ms, nouveaux = suivi()
    assert set(ms) == {"La page ne charge plus", "Un carnet de recettes"} and nouveaux == 0
    assert ms["La page ne charge plus"]["suivi"]["etape"] == "envoye"
    # Lu, puis mis a l'etude : du nouveau a annoncer.
    assert c.put(f"/api/messages/{a}/lu").status_code == 200
    assert c.put(f"/api/messages/{b}", json={"statut": "a_reflechir"}).status_code == 200
    ms, nouveaux = suivi()
    assert ms["La page ne charge plus"]["suivi"]["etape"] == "lu"
    assert ms["Un carnet de recettes"]["suivi"]["etape"] == "etude" and nouveaux == 2
    # Vu : les pastilles s'eteignent.
    assert u.post("/api/mes-messages/vus").status_code == 200
    assert suivi()[1] == 0
    # Accepte en tache, avec une date : planifie, puis fait.
    tache = c.post(f"/api/messages/{a}/accepter", json={"projet": "general", "titre": "Réparer"}
                   ).get_json()["tache"]
    assert c.put(f"/api/taches/{tache}", json={"echeance": "2030-01-15"}).status_code == 200
    ms, nouveaux = suivi()
    s = ms["La page ne charge plus"]["suivi"]
    assert s["etape"] == "planifie" and s["prevu"] == "2030-01-15" and nouveaux == 1
    assert s["taches"] == [{"titre": "Réparer", "faite": False}] and s["total"] == 1
    assert c.put(f"/api/taches/{tache}", json={"statut": "fait"}).status_code == 200
    s = suivi()[0]["La page ne charge plus"]["suivi"]
    assert s["etape"] == "fait" and s["faites"] == 1 and s["fait_le"] and s["prevu"] is None
    # Devenu un projet : ses taches comptent ; refuse : non retenu.
    r = c.post(f"/api/messages/{b}/accepter",
               json={"nouveau_projet": {"nom": "Recettes"}, "taches": ["Maquette", "Base"]})
    s = suivi()[0]["Un carnet de recettes"]["suivi"]
    assert s["etape"] == "planifie" and s["projet"] == "Recettes" and s["total"] == 2
    assert c.put(f"/api/messages/{b}", json={"statut": "refuse"}).status_code == 200
    assert suivi()[0]["Un carnet de recettes"]["suivi"]["etape"] == "refuse"
    # L'administrateur ne voit pas les messages de marie dans SON suivi.
    panneau.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    assert all(m["id"] != autre for m in panneau.get("/api/mes-messages").get_json()["messages"])
    assert "suivi" in {w["id"] for w in u.get("/api/hub/disposition").get_json()["catalogue"]}
    page = _page_panneau("dashboard.html")
    for trace in ('id="suivi-liste"', "function widgetSuivi(", "/api/mes-messages/vus", 'id="side-count-suivi"'):
        assert trace in page, trace


def test_un_message_porte_des_captures_et_des_maquettes(boite, deux_espaces):
    """Images et PDF, reconnus a leurs octets ; lus par l'administrateur et
    par qui les a deposes, par personne d'autre."""
    c, _ = boite
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    r = u.post("/api/pieces", data=PNG_1PX, headers={"X-Nom-Fichier": "Capture%20%C3%A9cran.png"})
    assert r.status_code == 200, r.data
    capture = r.get_json()
    assert capture["type"] == "image/png" and capture["nom"] == "Capture écran.png"
    maquette = u.post("/api/pieces", data=b"%PDF-1.4\n...", headers={"X-Nom-Fichier": "maquette"}).get_json()
    assert maquette["nom"] == "maquette.pdf"
    # Un SVG (du code), ou un faux PNG : refuses.
    assert u.post("/api/pieces", data=b"<svg onload=alert(1)>").status_code == 415
    assert u.post("/api/pieces", data=b"pas une image", headers={"X-Nom-Fichier": "x.png"}).status_code == 415
    r = u.post("/api/messages", json={"cible": "idee", "texte": "",
                                      "pieces": [capture["id"], maquette["id"]]})
    assert r.status_code == 200, r.data
    (m,) = [x for x in c.get("/api/messages").get_json()["messages"] if x["qui"] == "marie"]
    assert [p["nom"] for p in m["pieces"]] == ["Capture écran.png", "maquette.pdf"]
    lu = c.get("/api/pieces/" + capture["id"])
    assert lu.status_code == 200 and lu.data == PNG_1PX and lu.mimetype == "image/png"
    assert lu.headers["X-Content-Type-Options"] == "nosniff"
    relaye = u.get("/api/pieces/" + capture["id"])
    assert relaye.status_code == 200 and relaye.data == PNG_1PX
    assert relaye.mimetype == "image/png" and relaye.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in relaye.headers["Content-Security-Policy"]
    # Le suivi de marie montre ses pieces.
    assert u.get("/api/mes-messages").get_json()["messages"][0]["pieces"][0]["id"] == capture["id"]
    # Un autre compte ne lit rien ; une piece deja envoyee ne se rattache pas deux fois.
    paul = ClientMessages(c.module, "paul", "utilisateur")
    assert paul.get("/compte/pieces/" + capture["id"]).status_code == 404
    app._dernier_message.clear()
    assert u.post("/api/messages", json={"cible": "hub", "texte": "x",
                                         "pieces": [capture["id"]]}).status_code == 400
    # Supprimer le message emporte ses pieces.
    assert c.delete("/api/messages/" + m["id"]).status_code == 200
    assert c.get("/api/pieces/" + capture["id"]).status_code == 404
    assert _lignes(c, "pieces") == 0


def test_un_projet_s_exporte_en_entier(boite):
    c, _ = boite
    ident = _message(c, "idee", "Un carnet de recettes")
    with c.donnees.base() as b:
        b.executer("INSERT INTO pieces (id, message, qui, nom, type, taille, ts, donnees) "
                   "VALUES ('p1', ?, 'marie', 'maquette.png', 'image/png', ?, ?, ?)",
                   (ident, len(PNG_1PX), int(time.time()), PNG_1PX))
    projet = c.post(f"/api/messages/{ident}/accepter",
                    json={"nouveau_projet": {"nom": "Recettes", "description": "Partager nos recettes."},
                          "taches": ["Maquette", "Base"]}).get_json()["projet"]
    r = c.get(f"/api/projets/{projet}/export")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "recettes-" in r.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.data))
    noms = set(z.namelist())
    assert {"projet.md", "projet.json"} <= noms
    (piece,) = [n for n in noms if n.startswith("pieces/")]
    assert z.read(piece) == PNG_1PX
    md = z.read("projet.md").decode()
    assert md.startswith("# Recettes") and "Partager nos recettes." in md
    assert "- [ ] **Maquette**" in md and "Un carnet de recettes" in md and f"]({piece})" in md
    assert json.loads(z.read("projet.json"))["nom"] == "Recettes"
    # « Tout exporter » est retire, a la demande : seul le projet s'exporte.
    assert c.get("/api/projets-export").status_code == 404
    assert c.get("/api/projets/p-inconnu/export").status_code == 404
    # Reserve a l'administrateur.
    assert c.module.app.test_client().get(f"/api/projets/{projet}/export").status_code == 403


def test_accepter_sans_tache_et_droits(boite, deux_espaces):
    c, _ = boite
    ident = _message(c, "hub", "Merci !")
    r = c.post(f"/api/messages/{ident}/accepter", json={})
    assert r.status_code == 200 and r.get_json()["tache"] is None
    assert c.get("/api/taches").get_json()["taches"] == []
    # Un compte utilisateur n'y a pas acces : ni dans l'application, ni par
    # le panneau, qui n'a plus ces routes.
    marie = ClientMessages(c.module, "marie", "utilisateur")
    assert marie.get("/api/taches").status_code == 403
    assert marie.post(f"/api/messages/{ident}/accepter", json={}).status_code == 403
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    assert u.get("/api/taches").status_code == 404


def test_l_application_messages_a_ses_vues():
    page = _page_messages()
    for trace in ("aller('projet'", "'planning'", "'feuille'", "'boite'", 'id="fen-tache"',
                  'id="fen-projet"', 'id="fen-grouper"', 'id="fen-scinder"', "history.pushState",
                  "'api/taches/plan'", "/scinder'", "function planifier(",
                  "new URLSearchParams(location.search)"):
        assert trace in page, trace
    # Pas d'etiquette d'urgence : la priorite, c'est la place dans le planning.
    scripts = page.split("<script>")[-1].split("</script>")[0]
    assert "priorite" not in scripts
    # La feuille de route : aujourd'hui, semaine par semaine, sur des mois.
    for echelle in ("'jour'", "'semaines'", "'mois'", "[1,3,6,12]"):
        assert echelle in page, echelle


# ---------- l'application « Demandes » : tableau, priorites, projets ----------

def test_les_routes_sans_client_sont_retirees(boite):
    """« pas de code inutile ou Legacy » : l'ordre d'une colonne du vieux
    tableau et l'ordre des projets n'avaient plus aucun appelant (le planning
    passe par /api/taches/plan et /api/taches/<id>/ordre). Et le panneau n'a
    plus aucune route de la boite : elles sont dans l'application."""
    c, _ = boite
    regles = {r.rule for r in app.flask_app.url_map.iter_rules()}
    assert "/api/taches/ordre" not in regles and "/api/projets/ordre" not in regles
    assert "/api/deploy/<n>" not in regles
    assert not {"api_taches_ordre", "api_projets_ordre", "api_deploy"} & set(app.flask_app.view_functions)
    assert not {r for r in regles if r.startswith(("/api/taches", "/api/projets", "/api/sous-taches"))}
    regles_app = {r.rule for r in c.module.app.url_map.iter_rules()}
    assert "/api/taches/ordre" not in regles_app and "/api/projets/ordre" not in regles_app


def test_une_tache_a_priorite_echeance_et_notes(boite):
    c, _ = boite
    r = c.post("/api/taches", json={"projet": "general", "titre": "Urgent", "priorite": 0,
                                    "echeance": "2026-12-01", "notes": "  voir le journal  "})
    assert r.status_code == 200, r.data
    ident = r.get_json()["id"]
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert (t["priorite"], t["echeance"], t["notes"]) == (0, "2026-12-01", "voir le journal")
    assert c.put(f"/api/taches/{ident}", json={"statut": "en_cours", "echeance": None}).status_code == 200
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["statut"] == "en_cours" and t["echeance"] is None and not t["faite"]
    for mauvais in ({"priorite": 9}, {"priorite": "haute"}, {"echeance": "demain"},
                    {"statut": "fini"}, {"titre": "  "}, {}):
        assert c.put(f"/api/taches/{ident}", json=mauvais).status_code == 400, mauvais
    assert c.post("/api/taches", json={"projet": "general", "titre": "x",
                                       "echeance": "31/12/2026"}).status_code == 400
    assert c.put("/api/taches/inconnue", json={"priorite": 1}).status_code == 404


def test_un_projet_se_construit_puis_se_concretise(boite):
    """Idee, construction, realise : et un projet realise peut nommer
    l'application qui le concretise -- une application declaree, rien
    d'autre. L'application Messages connait la liste par le panneau, qui la
    lui donne a chaque synchronisation."""
    c, _ = boite
    app.save({"site": {"path": "/w/site", "command": "x", "port": 8101, "titre": "Site"}})
    assert app.synchroniser_la_messagerie()
    assert c.get("/api/apps").get_json()["apps"] == [{"name": "site", "titre": "Site"}]
    r = c.post("/api/projets", json={"nom": "Agenda partagé", "description": "Un agenda",
                                     "taches": ["Maquette", "", "Base"]})
    assert r.status_code == 200, r.data
    projet = r.get_json()["id"]
    assert projet.startswith("p-")
    (p,) = c.get("/api/projets").get_json()["projets"]
    assert (p["nom"], p["etape"], p["application"]) == ("Agenda partagé", "idee", None)
    taches = c.get("/api/taches").get_json()["taches"]
    assert [t["titre"] for t in taches] == ["Maquette", "Base"]
    assert {t["projet"] for t in taches} == {projet}
    assert c.post("/api/taches", json={"projet": projet, "titre": "Tests"}).status_code == 200
    assert c.post("/api/taches", json={"projet": "site", "titre": "Logo"}).status_code == 200
    assert c.put(f"/api/projets/{projet}", json={"etape": "construction"}).status_code == 200
    assert c.put(f"/api/projets/{projet}", json={"etape": "realise", "application": "site"}).status_code == 200
    (p,) = c.get("/api/projets").get_json()["projets"]
    assert (p["etape"], p["application"]) == ("realise", "site")
    assert c.put(f"/api/projets/{projet}", json={"application": "inconnue"}).status_code == 400
    assert c.put(f"/api/projets/{projet}", json={"etape": "fini"}).status_code == 400
    assert c.put(f"/api/projets/{projet}", json={"application": ""}).status_code == 200
    assert c.get("/api/projets").get_json()["projets"][0]["application"] is None
    # Supprimer le projet ne perd aucune tache : elles passent dans « general ».
    assert c.delete(f"/api/projets/{projet}").status_code == 200
    assert c.get("/api/projets").get_json()["projets"] == []
    assert {t["projet"] for t in c.get("/api/taches").get_json()["taches"]} == {"general", "site"}
    assert c.delete(f"/api/projets/{projet}").status_code == 404
    assert c.post("/api/taches", json={"projet": projet, "titre": "x"}).status_code == 400


def test_une_demande_acceptee_devient_un_projet(boite):
    c, _ = boite
    ident = _message(c, "idee", "Une appli pour les réservations de salle")
    r = c.post(f"/api/messages/{ident}/accepter",
               json={"nouveau_projet": {"nom": "Réservations", "description": "Salles"},
                     "taches": ["Recenser les salles"]})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["tache"] is None and d["projet"].startswith("p-")
    (p,) = c.get("/api/projets").get_json()["projets"]
    assert p["id"] == d["projet"] and p["message"] == ident and p["etape"] == "idee"
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["projet"] == p["id"] and t["titre"] == "Recenser les salles"
    m = next(x for x in c.donnees.lire_messages() if x["id"] == ident)
    assert m["statut"] == "accepte"
    # Un projet sans nom ne se cree pas, et la demande reste a traiter.
    autre = _message(c, "idee", "Autre")
    assert c.post(f"/api/messages/{autre}/accepter",
                  json={"nouveau_projet": {"nom": " "}}).status_code == 400
    assert next(x for x in c.donnees.lire_messages() if x["id"] == autre)["statut"] == "en_attente"
    # Accepter en tache garde la priorite choisie.
    r = c.post(f"/api/messages/{autre}/accepter", json={"titre": "Vite", "projet": p["id"], "priorite": 1})
    assert r.status_code == 200
    t = next(x for x in c.get("/api/taches").get_json()["taches"] if x["id"] == r.get_json()["tache"])
    assert t["priorite"] == 1 and t["projet"] == p["id"]


def test_les_messages_se_rangent_et_se_regroupent(boite):
    """Ranger la boite a sa guise, rattacher un message a un projet,
    regrouper ceux qui parlent de la meme chose -- dans un projet neuf ou
    existant. Supprimer le projet rend ses messages « a classer »."""
    c, _ = boite
    a = _message(c, "idee", "Un agenda partage")
    b = _message(c, "idee", "Agenda commun pour les salles")
    h = _message(c, "hub", "Le hub est lent")
    assert c.put("/api/messages/ordre", json={"ordre": [h, a, b]}).status_code == 200
    rangs = {m["id"]: m.get("ordre") for m in c.get("/api/messages").get_json()["messages"]}
    assert (rangs[h], rangs[a], rangs[b]) == (0, 1, 2)
    assert c.put("/api/messages/ordre", json={"ordre": [h, "x"]}).status_code == 409
    assert c.put("/api/messages/ordre", json={"ordre": [h, h]}).status_code == 400
    r = c.post("/api/messages/grouper", json={"ids": [a, b], "nouveau_projet": {"nom": "Agenda"}})
    assert r.status_code == 200, r.data
    projet = r.get_json()["projet"]
    lus = {m["id"]: m for m in c.get("/api/messages").get_json()["messages"]}
    assert lus[a]["projet"] == lus[b]["projet"] == projet and lus[a]["lu"]
    assert c.post("/api/messages/grouper", json={"ids": [h], "projet": projet}).status_code == 200
    assert c.post("/api/messages/grouper", json={"ids": [h], "projet": "p-inconnu"}).status_code == 400
    assert c.post("/api/messages/grouper", json={"ids": [], "projet": projet}).status_code == 400
    assert c.post("/api/messages/grouper", json={"ids": ["x"], "projet": projet}).status_code == 404
    # Rattacher et detacher un seul message.
    assert c.put(f"/api/messages/{h}", json={"projet": ""}).status_code == 200
    assert {m["id"]: m for m in c.get("/api/messages").get_json()["messages"]}[h]["projet"] is None
    assert c.put(f"/api/messages/{h}", json={"projet": "general"}).status_code == 400
    assert c.put(f"/api/messages/{h}", json={"projet": projet}).status_code == 200
    assert c.delete(f"/api/projets/{projet}").status_code == 200
    assert not any(m.get("projet") for m in c.get("/api/messages").get_json()["messages"])


def test_le_planning_s_ordonne_et_une_tache_se_scinde(boite):
    """La priorite est une PLACE : le planning s'ordonne en entier, sans
    toucher aux statuts. Une tache a une duree, une date facultative, et se
    scinde en plusieurs, qui la suivent."""
    c, _ = boite
    m = _message(c, "hub", "Le hub est lent")
    ids = [c.post("/api/taches", json={"projet": "general", "titre": t, "duree": d}).get_json()["id"]
           for t, d in (("a", 1), ("b", 4), ("c", 2))]
    a, b, cc = ids
    assert c.put(f"/api/taches/{a}", json={"statut": "en_cours"}).status_code == 200
    assert c.put("/api/taches/plan", json={"ordre": [cc, a, b]}).status_code == 200
    taches = sorted(c.get("/api/taches").get_json()["taches"], key=lambda t: t["ordre"])
    assert [t["titre"] for t in taches] == ["c", "a", "b"]
    assert {t["titre"]: t["statut"] for t in taches}["a"] == "en_cours"
    assert c.put("/api/taches/plan", json={"ordre": ["x"]}).status_code == 409
    for mauvais in ({"duree": 0}, {"duree": 61}, {"duree": "long"}):
        assert c.put(f"/api/taches/{a}", json=mauvais).status_code == 400, mauvais
    # Une tache nee d'un message ; un message inconnu est refuse.
    r = c.post("/api/taches", json={"projet": "general", "titre": "depuis", "message": m})
    assert r.status_code == 200
    assert c.post("/api/taches", json={"projet": "general", "titre": "x", "message": "inconnu"}).status_code == 404
    # Cochee, une tache date sa fin ; rouverte, elle l'oublie.
    assert c.put(f"/api/taches/{a}", json={"statut": "fait"}).status_code == 200
    assert {t["id"]: t for t in c.get("/api/taches").get_json()["taches"]}[a]["fait_le"]
    assert c.put(f"/api/taches/{a}", json={"statut": "a_faire"}).status_code == 200
    assert {t["id"]: t for t in c.get("/api/taches").get_json()["taches"]}[a]["fait_le"] is None
    # Scinder « b » (4 jours) en deux : la duree se partage, la suite vient juste apres.
    r = c.post(f"/api/taches/{b}/scinder", json={"titres": ["b1", "b2"]})
    assert r.status_code == 200, r.data
    taches = sorted(c.get("/api/taches").get_json()["taches"], key=lambda t: t["ordre"])
    titres = [t["titre"] for t in taches]
    assert titres.index("b2") == titres.index("b1") + 1
    assert {t["titre"]: t["duree"] for t in taches}["b1"] == 2
    assert c.post(f"/api/taches/{b}/scinder", json={"titres": ["seul"]}).status_code == 400
    assert c.post("/api/taches/inconnue/scinder", json={"titres": ["x", "y"]}).status_code == 404


def test_messages_est_une_application_comme_le_diagnostic(boite, tmp_path, monkeypatch):
    """Vecu : « L'application Messages doit etre une application a part
    entiere, exactement comme Diagnostic ». Un dossier du workspace, inscrit
    par le panneau a chaque demarrage, servi par le proxy sur le port des
    applications : le ruban « ← CodeLab » est donc celui de toutes les
    applications, pose par le proxy, et rien dans la page ne le recopie."""
    c, panneau = boite
    racine = tmp_path / "workspace"
    (racine / "messages").mkdir(parents=True)
    (racine / "messages" / "app.py").write_text("")
    monkeypatch.setattr(app, "ROOT", str(racine))
    assert app.amorcer_projet(app.MESSAGERIE_NOM) == app.MESSAGERIE_NOM
    inscrite = app.load()[app.MESSAGERIE_NOM]
    assert inscrite["path"] == str(racine / "messages")
    assert inscrite["command"] == app.COMMANDE_PAR_DEFAUT
    assert inscrite["build_command"] == app.BUILD_PAR_DEFAUT
    assert inscrite["visibility"] == app.VISIBILITE_PRIVEE
    assert app.est_app_par_defaut(app.MESSAGERIE_NOM)
    assert app.MESSAGERIE_NOM in app.APPLICATIONS_PROTEGEES
    assert app.MESSAGERIE_NOM not in app.SERVICES_MASQUABLES
    # Ni arretee, ni supprimee, ni configuree a la main.
    assert panneau.post(f"/api/toggle/{app.MESSAGERIE_NOM}").status_code in (400, 403)
    page = _page_messages()
    assert "← Hub" not in page and 'id="codelab-ruban"' not in page
    assert "calc(72px + var(--codelab-bord-bas, 0px))" in page
    assert "env(safe-area-inset-bottom" not in page
    assert os.path.isfile(os.path.join(DOSSIER_MESSAGES, "icon.svg"))


def test_l_application_messages_est_reservee_a_l_administrateur(boite, deux_espaces):
    """Dans le panneau, /messages mene l'administrateur a l'application (sur
    le port des applications) ; un compte utilisateur y a SA rubrique, et
    jamais la boite ; un anonyme va a la connexion."""
    c, panneau = boite
    panneau.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = panneau.get("/messages")
    assert r.status_code == 302 and r.headers["Location"] == app_adresse_messages()
    assert c.get("/").status_code == 200 and "function peindrePlanning(" in c.get("/").get_data(as_text=True)
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    r = u.get("/messages")
    assert r.status_code == 200
    page_u = r.get_data(as_text=True)
    assert '<div id="sec-message" class="section"' in page_u
    assert "function peindrePlanning(" not in page_u
    with app.flask_app.test_request_context("/"):
        from flask import session
        session["authed"], session["role"], session["utilisateur"] = True, "utilisateur", "marie"
        assert not app.peut_voir(app.MESSAGERIE_NOM)
    assert app.flask_app.test_client().get("/messages").status_code == 302
    for nom in ("messages", "demandes"):
        assert nom in app.PAGES_DU_PANNEAU and nom in app.adresses_reservees()


def app_adresse_messages():
    with app.flask_app.test_request_context("/"):
        return app.adresse_application(app.MESSAGERIE_NOM)


def test_la_tuile_messages_compte_les_non_lus(boite, monkeypatch):
    """La tuile de l'application, dans le hub, dit combien de demandes
    attendent ; le compte se lit dans l'application, garde vingt secondes."""
    c, panneau = boite
    app.save({app.MESSAGERIE_NOM: {"titre": "Messages", "path": "/w/messages", "command": "python3 app.py",
                                   "port": 9120, "enabled": True, "visibility": "privee"}})
    monkeypatch.setattr(app, "is_running", lambda n: True)
    _message(c, "hub", "Une")
    _message(c, "hub", "Deux")
    tuiles = panneau.get("/api/mes-apps").get_json()["apps"]
    tuile = next(a for a in tuiles if a["name"] == app.MESSAGERIE_NOM)
    assert tuile["non_lues"] == 2 and tuile["titre"] == "Messages"
    assert tuile["description"] == "2 nouveaux messages à lire."
    assert c.post("/api/messages/lus").status_code == 200
    app._messagerie["ts"] = 0
    tuiles = panneau.get("/api/mes-apps").get_json()["apps"]
    assert next(a for a in tuiles if a["name"] == app.MESSAGERIE_NOM)["non_lues"] == 0
    # Ce n'est plus un service du panneau.
    services = panneau.get("/api/mes-apps").get_json()["services"]
    assert app.MESSAGERIE_NOM not in [s["name"] for s in services]
    page = _page_panneau("dashboard.html")
    assert "a.interne" not in page
    # L'application tombee : le hub s'affiche quand meme, sans compte.
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    monkeypatch.setattr(app, "_vers_messagerie", tombee)
    app._messagerie["ts"] = 0
    assert panneau.get("/api/mes-apps").status_code == 200


def test_les_anciennes_donnees_passent_dans_l_application(boite):
    """Une installation d'avant la separation garde ses messages, taches,
    projets et pieces dans codelab.db. Ils partent dans l'application, et ne
    sont effaces du panneau qu'une fois CHAQUE ligne arrivee ; rejouer la
    reprise ne double rien."""
    c, _ = boite
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("INSERT INTO messages (id, ts, qui, cible, app, texte, statut, lu, retenu, "
                   "archive, options) VALUES ('m1', 100, 'marie', 'hub', '', 'Ancien', "
                   "'accepte', 1, 1, NULL, ?)", (json.dumps({"projet": "p-1", "ordre": 3}),))
        cx.execute("INSERT INTO projets (id, nom, description, etape, application, message, "
                   "cree, maj, ordre) VALUES ('p-1', 'Vieux', '', 'idee', NULL, 'm1', 1, 1, 0)")
        cx.execute("INSERT INTO taches (id, projet, titre, message, cree, faite, ordre, statut, "
                   "priorite, echeance, notes, duree, fait_le) VALUES ('t1', 'p-1', 'Faire', "
                   "'m1', 1, 0, 0, 'a_faire', 2, NULL, '', 1, NULL)")
        cx.execute("INSERT INTO sous_taches (id, tache, titre, faite, ordre) "
                   "VALUES ('s1', 't1', 'Morceau', 1, 0)")
        cx.execute("INSERT INTO pieces VALUES ('p1', 'm1', 'marie', 'a.png', 'image/png', ?, 5, ?)",
                   (len(PNG_1PX), PNG_1PX))
    # L'application ne repond pas : rien n'est efface.
    vraie = app._vers_messagerie
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    app._vers_messagerie = tombee
    try:
        assert app.reprendre_la_messagerie() is False
    finally:
        app._vers_messagerie = vraie
    assert stockage_lignes("messages") == 1
    assert app.reprendre_la_messagerie() is True
    for table in ("messages", "projets", "taches", "sous_taches", "pieces"):
        assert stockage_lignes(table) == 0, table
    (m,) = c.get("/api/messages").get_json()["messages"]
    assert (m["texte"], m["statut"], m["lu"], m["retenu"], m["archive"]) == (
        "Ancien", "accepte", True, True, False)
    assert (m["projet"], m["ordre"]) == ("p-1", 3) and m["pieces"][0]["id"] == "p1"
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert t["sous_taches"] == [{"id": "s1", "titre": "Morceau", "faite": True}]
    assert c.get("/api/pieces/p1").data == PNG_1PX
    # Rejouer : rien de double, rien a reprendre.
    assert c.donnees.importer({"messages": [{"id": "m1", "ts": 1, "cible": "hub"}]})
    assert len(c.donnees.lire_messages()) == 1
    assert app.reprendre_la_messagerie() is True


def test_un_compte_efface_part_aussi_de_la_messagerie(boite, deux_espaces, monkeypatch):
    """Effacer un compte emporte ses messages et leurs pieces, dans
    l'application aussi ; une application qui ne repond pas l'apprend au
    tour suivant. Le travail ne de ses messages reste."""
    c, _ = boite
    _message(c, "hub", "De marie")
    _message(c, "hub", "De paul", qui="paul")
    c.post(f"/api/messages/{_message(c, 'idee', 'Projet de marie')}/accepter",
           json={"titre": "A faire", "projet": "general"})
    vraie = app._vers_messagerie
    def tombee(*a, **kw):
        raise app.MessagerieIndisponible("arretee")
    monkeypatch.setattr(app, "_vers_messagerie", tombee)
    app.effacer_le_compte("marie")
    assert app.reglage("comptes_a_effacer_messagerie") == ["marie"]
    assert len(c.donnees.lire_messages()) == 3
    monkeypatch.setattr(app, "_vers_messagerie", vraie)
    app.oublier_dans_la_messagerie()
    assert [m["qui"] for m in c.donnees.lire_messages()] == ["paul"]
    assert app.reglage("comptes_a_effacer_messagerie") is None
    assert len(c.get("/api/taches").get_json()["taches"]) == 1


# ------------------------ Les notifications (Web Push) ------------------------
#
# Vecu : « PWA suffit, complete-la ». Le panneau installe recoit des
# notifications -- messages, applications en panne, avancement d'un message --
# et le chiffrement est celui du standard, verifie sur le vecteur de la RFC.

def test_le_chiffrement_des_notifications_suit_la_rfc_8291():
    from cryptography.hazmat.primitives.asymmetric import ec
    d = app._de_b64url
    serveur = ec.derive_private_key(int.from_bytes(d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                    ec.SECP256R1())
    corps = app.chiffrer_push(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg", _ephemere=serveur, _sel=d("DGv6ra1nlYgDCS1FRnbzlw"))
    assert app._b64url(corps) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wE"
        "qKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")




@pytest.mark.parametrize("adresse, permise", [
    ("https://web.push.apple.com/QGuQyavXut", True),
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://wns2-par02p.notify.windows.com/w/?token=x", True),
    ("http://web.push.apple.com/x", False),              # jamais en clair
    ("https://push.apple.com.exemple.org/x", False),     # un suffixe, pas un prefixe
    ("https://127.0.0.1/x", False),
    ("https://codelab-postgres:5432/x", False),
    ("https://web.push.apple.com:8443/x", False),
    ("file:///etc/passwd", False),
    ("", False),
])
def test_le_panneau_ne_contacte_que_les_services_de_notification(adresse, permise):
    """Sans cette liste, l'adresse fournie par le navigateur ferait du panneau
    un relais vers le reseau local."""
    assert app.service_push_permis(adresse) is permise




def test_un_appareil_s_abonne_et_recoit_une_notification_chiffree(deux_espaces, envois_push):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    etat = c.get("/api/notifications").get_json()
    assert etat["disponible"] and not etat["abonne"] and len(app._de_b64url(etat["cle"])) == 65
    nav = _Navigateur()
    assert c.post("/api/notifications/abonnement", json=nav.abonnement()).status_code == 200
    assert c.get("/api/notifications").get_json()["abonne"]
    assert c.post("/api/notifications/essai").status_code == 200
    (req,) = envois_push
    assert req.full_url == "https://web.push.apple.com/abc"
    assert req.get_header("Content-encoding") == "aes128gcm" and req.get_header("Ttl") == "86400"
    jeton, cle = req.get_header("Authorization")[len("vapid t="):].split(", k=")
    assert cle == etat["cle"]
    # Le jeton VAPID est signe par la cle annoncee, pour ce service-la.
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    tete, corps, signature = jeton.split(".")
    revendique = json.loads(app._de_b64url(corps))
    assert revendique["aud"] == "https://web.push.apple.com" and revendique["sub"].startswith("https://")
    sig = app._de_b64url(signature)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), app._de_b64url(cle)).verify(
        utils.encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
        (tete + "." + corps).encode(), ec.ECDSA(hashes.SHA256()))
    # Seul l'appareil abonne lit le contenu.
    assert nav.lire(req.data)["titre"] == "CodeLab"
    # Couper : plus rien n'arrive.
    assert c.delete("/api/notifications/abonnement").status_code == 200
    assert c.post("/api/notifications/essai").status_code == 404




def test_un_abonnement_douteux_est_refuse(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    nav = _Navigateur()
    assert c.post("/api/notifications/abonnement",
                  json=nav.abonnement("http://192.168.1.10/x")).status_code == 400
    mauvais = nav.abonnement()
    mauvais["keys"]["auth"] = "court"
    assert c.post("/api/notifications/abonnement", json=mauvais).status_code == 400
    assert app.abonnements_push("marie") == []




def test_un_appareil_deconnecte_ou_desabonne_ne_recoit_plus_rien(deux_espaces, envois_push):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    c.post("/api/notifications/abonnement", json=_Navigateur().abonnement())
    # Le service dit que l'abonnement n'existe plus : il est oublie.
    envois_push.code = 410
    app.notifier_compte("marie", "t", "x")
    assert len(envois_push) == 1 and app.abonnements_push("marie") == []
    # Deconnecte depuis Securite : rien ne part, l'abonnement s'en va.
    envois_push.code = 201
    c.post("/api/notifications/abonnement", json=_Navigateur().abonnement("https://web.push.apple.com/b"))
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("DELETE FROM appareils WHERE compte = 'marie'")
    app.notifier_compte("marie", "t", "x")
    assert len(envois_push) == 1 and app.abonnements_push("marie") == []




def test_l_administrateur_est_prevenu_d_un_nouveau_message(deux_espaces, appli_messages, envois_push,
                                                           monkeypatch):
    c = deux_espaces
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    nav = _Navigateur()
    assert admin.post("/api/notifications/abonnement", json=nav.abonnement()).status_code == 200
    _connecte(c, "marie", "mot-de-passe-long")
    monkeypatch.setattr(app, "smtp_utilisable", lambda: ({}, False))
    monkeypatch.setattr(app, "_dernier_message", {})
    assert c.post("/api/messages", json={"cible": "hub", "texte": "Le hub est super"}).status_code == 200
    (req,) = envois_push
    recu = nav.lire(req.data)
    assert recu["titre"] == "Message de marie" and recu["texte"] == "Le hub est super"
    assert recu["url"] == "/messages"




def test_l_auteur_d_un_message_suit_son_avancement(deux_espaces, appli_messages, envois_push,
                                                   monkeypatch):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    nav = _Navigateur()
    c.post("/api/notifications/abonnement", json=nav.abonnement())
    monkeypatch.setattr(app, "smtp_utilisable", lambda: ({}, False))
    assert c.post("/api/messages", json={"cible": "hub", "texte": "Une idee"}).status_code == 200
    (m,) = [x for x in appli_messages.donnees.lire_messages() if x["qui"] == "marie"]
    # Le tour du moniteur ne parle qu'a une application inscrite et debout.
    apps = app.load()
    apps[app.MESSAGERIE_NOM] = {"path": "/w/messages", "command": "x", "port": 9120,
                                "enabled": True, "visibility": "privee"}
    app.save(apps)
    # Premier passage : on retient l'etat, on ne rejoue rien.
    app.messagerie_tick(maintenant=1e10)
    assert envois_push == []
    appli_messages.donnees.modifier_message(m["id"], {"statut": "refuse"})
    app.messagerie_tick(maintenant=1e10 + 120)
    (req,) = envois_push
    assert nav.lire(req.data)["titre"] == "Ton message n'est pas retenu"
    assert nav.lire(req.data)["texte"] == "Une idee"
    # Une fois annonce, plus rien tant que rien ne change.
    app.messagerie_tick(maintenant=1e10 + 240)
    assert len(envois_push) == 1


def test_le_manifeste_est_complet(hub):
    m = hub.get("/manifest.webmanifest").get_json()
    assert m["id"] == "/" and m["lang"] == "fr"
    tailles = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= tailles
    assert [r["url"] for r in m["shortcuts"]] == ["/", "/messages", "/parametres"]
    for icone in m["icons"]:
        r = hub.get(icone["src"])
        assert r.status_code == 200 and r.mimetype == "image/png", icone["src"]
        cote = int(icone["sizes"].split("x")[0])
        assert int.from_bytes(r.data[16:20], "big") == int.from_bytes(r.data[20:24], "big") == cote
    assert hub.get("/icone-1024.png").status_code == 404




def test_le_service_worker_affiche_et_ouvre_les_notifications(hub):
    code = hub.get("/sw.js").get_data(as_text=True)
    assert "self.addEventListener('push'" in code and "showNotification(" in code
    clic = code.split("self.addEventListener('notificationclick'")[1]
    # Jamais une adresse d'ailleurs.
    assert "if (u.origin === self.location.origin) cible = u;" in clic
    assert "self.clients.openWindow(cible.href)" in clic




def test_les_parametres_proposent_notifications_et_installation():
    page = _page_panneau("dashboard.html")
    for trace in ('id="zone-notifications"', 'id="notif-bascule"', 'id="zone-installer"',
                  "Notification.requestPermission()", "pushManager.subscribe({userVisibleOnly:true",
                  "navigator.serviceWorker.register('/sw.js')", "function majPastilleIcone(){",
                  "navigator.setAppBadge(n)", "invite.prompt();",
                  "<div class=\"onglet-p\" id=\"param-apparence\">"):
        assert trace in page, trace
    # La permission se demande dans le geste : rien n'est attendu avant.
    bascule = page.split("async function notifBasculer(b){")[1].split("\n}")[0]
    assert bascule.index("Notification.requestPermission()") < bascule.index("fetch(")
    mode = _page_panneau("mode.js")
    assert "window.addEventListener('beforeinstallprompt'" in mode


def test_un_compte_est_prevenu_d_une_application_qu_il_ne_connait_pas(deux_espaces, envois_push,
                                                                      monkeypatch):
    """Vecu : « un widget nouveautes ou un systeme de notifications pour
    avertir un utilisateur qu'une nouvelle application est disponible
    lorsqu'il a acces a une app qu'il ne connait pas ». Chaque acces accorde
    envoie une notification, une seule fois ; la tuile porte « nouvelle »
    tant que le compte ne l'a pas ouverte."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    nav = _Navigateur()
    c.post("/api/notifications/abonnement", json=nav.abonnement())

    def nouvelles():
        return {a["name"]: a["nouvelle"] for a in c.get("/api/mes-apps").get_json()["apps"]}
    assert nouvelles() == {"prive-autorise": True}
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    # Par la fiche de l'application.
    assert admin.put("/api/apps/prive-refuse/acces",
                     json={"utilisateurs": ["marie"]}).status_code == 200
    (req,) = envois_push
    message = nav.lire(req.data)
    assert message["titre"] == "Nouvelle application" and "prive-refuse" in message["texte"]
    # Le meme acces, redonne : rien de plus.
    admin.put("/api/apps/prive-refuse/acces", json={"utilisateurs": ["marie"]})
    assert len(envois_push) == 1
    # Par la fiche du compte.
    assert admin.put("/api/utilisateurs/marie",
                     json={"projets": ["prive-autorise", "prive-refuse", "public"]}).status_code == 200
    assert len(envois_push) == 2 and "public" in nav.lire(envois_push[1].data)["texte"]
    # L'ouvrir l'eteint.
    assert nouvelles() == {"prive-autorise": True, "prive-refuse": True, "public": True}
    monkeypatch.setattr(app, "is_running", lambda n: True)
    monkeypatch.setattr(app, "_listening", {})
    c.post("/api/mes-apps/public/ouverte")
    assert nouvelles()["public"] is False
    # L'administrateur ne voit pas « nouvelle » : il les a declarees.
    assert not any(a["nouvelle"] for a in admin.get("/api/mes-apps").get_json()["apps"])


def test_l_application_messages_tourne_sur_un_vrai_postgres(pg_reel, monkeypatch):
    """Les autres tests passent par SQLite (MESSAGES_SQLITE) ; l'installation,
    elle, range dans Postgres. Le meme SQL doit valoir pour les deux : les
    « ? » convertis, les octets en BYTEA, ON CONFLICT, la reprise."""
    pytest.importorskip("psycopg")
    module = _application_messages()
    d = module.donnees
    assert pg_reel["psql"]("-c", "CREATE DATABASE messages", base="postgres").returncode == 0
    for cle, valeur in (("POSTGRES_HOST", "127.0.0.1"), ("POSTGRES_PORT", str(pg_reel["port"])),
                        ("CODELAB_DB", "messages"), ("POSTGRES_USER", "codelab"),
                        ("POSTGRES_PASSWORD", pg_reel["cle"])):
        monkeypatch.setenv(cle, valeur)
    monkeypatch.setattr(d, "SQLITE", "")
    d.preparer()
    d.preparer()                      # rejouee, elle ne refait rien
    piece = d.deposer_piece("marie", "capture.png", PNG_1PX)
    m = d.nouveau_message("marie", "hub", "", "Bonjour", [piece["id"]])
    assert d.lire_piece(piece["id"])["donnees"] == PNG_1PX
    assert [x["texte"] for x in d.lire_messages(qui="marie")] == ["Bonjour"]
    projet = d.accepter_message(m["id"], {"nouveau_projet": {"nom": "Essai"},
                                          "taches": ["Un", "Deux"]})[1]
    octets, nom = d.exporter_projet(projet)
    assert nom.startswith("essai-") and zipfile.ZipFile(io.BytesIO(octets)).read(
        [n for n in zipfile.ZipFile(io.BytesIO(octets)).namelist() if n.startswith("pieces/")][0]
    ) == PNG_1PX
    presents = d.importer({"messages": [{"id": m["id"], "ts": 1, "cible": "hub"}]})
    assert presents["messages"] == [m["id"]] and len(presents["taches"]) == 2
    assert d.oublier_comptes(["marie"]) == 1 and d.lire_messages() == []


def test_messages_verifie_l_identite_avec_le_module_livre_aux_projets():
    """L'application verifie le jeton avec codelab_auth.py, le module que
    « codelab create » depose dans chaque projet. Une copie, et non un
    module a part : elle doit rester celle de dev/modeles, octet pour octet
    (« codelab update » la tiendrait a jour dans un projet ordinaire)."""
    modele = os.path.join(_racine_depot(), "dev", "modeles", "codelab_auth.py")
    if not os.path.exists(modele):
        pytest.skip("depot complet absent de cette image")
    copie = os.path.join(DOSSIER_MESSAGES, "codelab_auth.py")
    assert open(copie, encoding="utf-8").read() == open(modele, encoding="utf-8").read()
