"""
CodeLab -- TESTS : LES MESSAGES
---------------------------------------------------------------------------
ROLE        la messagerie, les demandes, les taches, les notifications Web
            Push.
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




def test_un_message_est_range_puis_envoye_a_l_administrateur(messagerie):
    c, partis = messagerie
    r = c.post("/api/messages", json={"cible": "application", "app": "compta",
                                      "texte": "Le bouton demande deux clics."})
    assert r.status_code == 200, r.data
    assert r.get_json()["envoye"] is True

    garde = app.lire_messages()
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
    assert len(app.lire_messages()) == 1




def test_on_n_ecrit_que_sur_une_application_qu_on_peut_ouvrir(messagerie):
    """Sinon ce formulaire dirait l'existence de projets qu'on n'a pas le
    droit de connaitre -- exactement comme la liste des masquees."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "application", "app": "secret",
                                         "texte": "bonjour"}).status_code == 404
    assert c.post("/api/messages", json={"cible": "application", "app": "fantome",
                                         "texte": "bonjour"}).status_code == 404
    assert app.lire_messages() == [] and partis == []




def test_une_idee_d_application_ne_porte_aucun_projet(messagerie):
    """« Une application qui manque » n'en designe aucune : garder un nom
    envoye au passage ferait croire a un message sur un projet existant."""
    c, _ = messagerie
    r = c.post("/api/messages", json={"cible": "idee", "app": "compta",
                                      "texte": "Il manque un suivi des congés."})
    assert r.status_code == 200, r.data
    assert app.lire_messages()[0]["app"] == ""




def test_un_sujet_invente_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "autre chose",
                                         "texte": "bonjour"}).status_code == 400
    assert app.lire_messages() == []




def test_un_message_vide_ou_trop_long_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "   "}).status_code == 400
    trop = "a" * (app.MESSAGE_LONGUEUR_MAX + 1)
    assert c.post("/api/messages", json={"cible": "hub", "texte": trop}).status_code == 400
    assert app.lire_messages() == []




def test_un_deuxieme_message_immediat_est_refuse(messagerie):
    """De quoi corriger une faute de frappe, pas de quoi remplir la boite de
    l'administrateur."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "un"}).status_code == 200
    r = c.post("/api/messages", json={"cible": "hub", "texte": "deux"})
    assert r.status_code == 429, r.data
    assert len(app.lire_messages()) == 1 and len(partis) == 1




def test_seul_l_administrateur_lit_les_messages(messagerie):
    """Ce sont des mots qui lui sont adresses, et ils portent le nom de qui
    les a ecrits."""
    c, _ = messagerie
    c.post("/api/messages", json={"cible": "hub", "texte": "bonjour"})
    assert c.get("/api/messages").status_code in (401, 403)
    admin = app.flask_app.test_client()
    admin.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = admin.get("/api/messages").get_json()
    assert [m["texte"] for m in d["messages"]] == ["bonjour"]




def test_les_messages_partent_aussi_vers_la_base():
    """Le fichier tient sans base et se lit en SSH ; la base garde tout et
    s'interroge en SQL. Le meme couple que pour le journal des acces."""
    src = _page_panneau("app.py")
    assert "CREATE TABLE IF NOT EXISTS messages" in src
    assert "_pg_deposer((\"message\"" in src
    # Et le rattrapage les rejoue apres une coupure de la base.
    rattrapage = src.split("def _pg_rattraper(")[1].split("\ndef ")[0]
    assert "_pg_ecrire_messages" in rattrapage


# ---------- 35. les messages ont leur rubrique ----------
#
# Demande : « Ajoute une rubrique pour les messages. La vue d'ensemble doit
# recenser les messages lies au hub par defaut, et je dois pouvoir filtrer sur
# une autre application. Ajoute la possibilite de supprimer ou retenir les
# messages, mettre un scroll lorsqu'il y en a trop. »
#
# Ils vivaient au bas de la vue d'ensemble, sous les graphiques : on ne les
# trouvait qu'en descendant, tous sujets meles, et rien ne permettait ni d'en
# ecarter un ni d'en garder un sous la main.




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
    assert "retenu" not in liste[0]




def test_supprimer_un_message_le_retire_du_fichier(boite):
    c, tmp_path = boite
    garde = _message(c, "hub", "on garde")
    parti = _message(c, "idee", "on jette")

    assert c.delete("/api/messages/" + parti).status_code == 200
    restants = [m["id"] for m in c.get("/api/messages").get_json()["messages"]]
    assert restants == [garde]
    # Dans la base aussi, pas seulement dans la reponse : un message qui
    # revient au redemarrage n'a pas ete supprime.
    ids = [l["id"] for l in app.stockage.lire(app.DB_FILE, "SELECT id FROM messages")]
    assert parti not in ids and garde in ids
    # Et c'est journalise : supprimer un mot qui vous est adresse se trace.
    genres = [e.get("genre") for e in app.lire_acces()]
    assert "message" in genres





def test_le_guet_ne_redemande_que_ce_qui_est_arrive_depuis(boite):
    """La page interroge toutes les vingt secondes : lui renvoyer trois cents
    messages pour en decouvrir un serait payer cher une notification."""
    c, _ = boite
    repere = int(time.time()) - 100
    _message(c, "hub", "avant", quand=repere - 10)
    neuf = _message(c, "hub", "après", quand=repere + 10)

    liste = c.get("/api/messages?apres=%d" % repere).get_json()["messages"]
    assert [m["id"] for m in liste] == [neuf]
    # Un « apres » illisible ne fait pas tomber la route : elle rend tout.
    assert len(c.get("/api/messages?apres=demain").get_json()["messages"]) == 2




def test_les_messages_restent_reserves_a_l_administrateur(boite):
    c, _ = boite
    identifiant = _message(c, "hub", "un mot")
    anonyme = app.flask_app.test_client()
    assert anonyme.get("/api/messages").status_code in (401, 403)
    assert anonyme.delete("/api/messages/" + identifiant).status_code in (401, 403)
    assert anonyme.put("/api/messages/" + identifiant,
                       json={"retenu": True}).status_code in (401, 403)




def test_un_message_inconnu_ne_fait_pas_semblant(boite):
    c, _ = boite
    assert c.delete("/api/messages/jamais-vu").status_code == 404
    assert c.put("/api/messages/jamais-vu", json={"retenu": True}).status_code == 404




def test_l_application_messages_range_et_confirme():
    """La boite de reception : trier, ranger, regrouper ; et rien ne se
    supprime sans confirmation."""
    page = _page_panneau("messages.html")
    for trace in ("'/api/messages/ordre'", "'/api/messages/grouper'", 'data-agir="grouper"',
                  'data-changer="message-projet"', 'data-agir="message-archiver"',
                  'data-agir="message-tache"', "function suggestions(", 'data-triable'):
        assert trace in page, trace
    for action in ("'message-supprimer'", "'tache-supprimer'"):
        corps = page.split(action)[1].split("\n  },")[0]
        assert "demander(" in corps, action + " supprime sans confirmation"
    corps = page.split("async function projetSupprimer(")[1].split("\n}")[0]
    assert "demander(" in corps
    # Le jeton anti-falsification part avec toute requete qui modifie.
    assert "X-CodeLab-Jeton" in page and "const JETON=__JETON__;" in page




def test_un_message_recu_se_signale_dans_le_coin():
    """« Ajoute des notifications en bas a droite des qu'un message est
    recu. » La page guette, le dit la ou elle dit tout le reste, et mene a
    l'application Messages."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("async function guetterMessages(){")[1].split("\n}")[0]
    assert "apres=" in bloc, "le guet retelecharge toute la boite"
    assert "notifier" in bloc
    assert "location.href='/messages'" in page.split("function notifierMessage(")[1].split("\n}")[0]
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
    m = next(x for x in app.lire_messages() if x["id"] == ident)
    assert m["statut"] == "a_reflechir" and m.get("retenu")
    assert c.put(f"/api/messages/{ident}", json={"statut": "refuse"}).status_code == 200
    assert c.put(f"/api/messages/{ident}", json={"archive": True}).status_code == 200
    m = next(x for x in app.lire_messages() if x["id"] == ident)
    assert m["statut"] == "refuse" and m.get("archive") and m.get("retenu")
    assert c.put(f"/api/messages/{ident}", json={"statut": "n-importe"}).status_code == 400




def test_un_message_accepte_devient_une_tache(boite):
    c, _ = boite
    ident = _message(c, "hub", "La page ne charge plus")
    r = c.post(f"/api/messages/{ident}/accepter",
               json={"projet": "general", "titre": "  Réparer   la page ",
                     "sous_taches": ["Lire les journaux", "", "Corriger"]})
    assert r.status_code == 200, r.data
    tache = r.get_json()["tache"]
    m = next(x for x in app.lire_messages() if x["id"] == ident)
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
    assert stockage_lignes("sous_taches") == 0




def test_l_utilisateur_suit_ses_messages(boite, deux_espaces):
    """Qui a ecrit voit ce que son message devient -- lu, a l'etude,
    planifie, en cours, realise, non retenu -- deduit de ce que fait
    l'administrateur. Il ne voit que ses messages."""
    c, _ = boite
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
    assert all(m["id"] != autre for m in c.get("/api/mes-messages").get_json()["messages"])
    assert "suivi" in {w["id"] for w in u.get("/api/hub/disposition").get_json()["catalogue"]}
    page = _page_panneau("dashboard.html")
    for trace in ('id="suivi-liste"', "function widgetSuivi(", "/api/mes-messages/vus", 'id="side-count-suivi"'):
        assert trace in page, trace




def test_un_message_porte_des_captures_et_des_maquettes(boite, deux_espaces):
    """Images et PDF, reconnus a leurs octets ; lus par l'administrateur et
    par qui les a deposes, par personne d'autre."""
    c, _ = boite
    app._dernier_message.clear()
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    h = {}
    r = u.post("/api/pieces", data=PNG_1PX, headers=dict(h, **{"X-Nom-Fichier": "Capture%20%C3%A9cran.png"}))
    assert r.status_code == 200, r.data
    capture = r.get_json()
    assert capture["type"] == "image/png" and capture["nom"] == "Capture écran.png"
    maquette = u.post("/api/pieces", data=b"%PDF-1.4\n...", headers=dict(h, **{"X-Nom-Fichier": "maquette"})).get_json()
    assert maquette["nom"] == "maquette.pdf"
    # Un SVG (du code), ou un faux PNG : refuses.
    assert u.post("/api/pieces", data=b"<svg onload=alert(1)>", headers=h).status_code == 415
    assert u.post("/api/pieces", data=b"pas une image", headers=dict(h, **{"X-Nom-Fichier": "x.png"})).status_code == 415
    r = u.post("/api/messages", json={"cible": "idee", "texte": "",
                                      "pieces": [capture["id"], maquette["id"]]}, headers=h)
    assert r.status_code == 200, r.data
    (m,) = [x for x in c.get("/api/messages").get_json()["messages"] if x["qui"] == "marie"]
    assert [p["nom"] for p in m["pieces"]] == ["Capture écran.png", "maquette.pdf"]
    lu = c.get("/api/pieces/" + capture["id"])
    assert lu.status_code == 200 and lu.data == PNG_1PX and lu.mimetype == "image/png"
    assert lu.headers["X-Content-Type-Options"] == "nosniff"
    assert u.get("/api/pieces/" + capture["id"]).status_code == 200
    # Le suivi de marie montre ses pieces.
    assert u.get("/api/mes-messages").get_json()["messages"][0]["pieces"][0]["id"] == capture["id"]
    # Un autre compte ne lit rien ; une piece deja envoyee ne se rattache pas deux fois.
    app._dernier_message.clear()
    assert u.post("/api/messages", json={"cible": "hub", "texte": "x", "pieces": [capture["id"]]},
                  headers=h).status_code == 400
    # Supprimer le message emporte ses pieces.
    assert c.delete("/api/messages/" + m["id"]).status_code == 200
    assert c.get("/api/pieces/" + capture["id"]).status_code == 404
    assert stockage_lignes("pieces") == 0




def test_un_projet_s_exporte_en_entier(boite):
    c, _ = boite
    ident = _message(c, "idee", "Un carnet de recettes")
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("INSERT INTO pieces VALUES ('p1', ?, 'marie', 'maquette.png', 'image/png', ?, ?, ?)",
                   (ident, len(PNG_1PX), int(time.time()), PNG_1PX))
    app.modifier_messages({ident: {"pieces": [{"id": "p1", "nom": "maquette.png",
                                               "type": "image/png", "taille": len(PNG_1PX)}]}})
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
    # Tout exporter : un dossier par projet, et un sommaire.
    r = c.get("/api/projets-export")
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert "LISEZMOI.md" in z.namelist() and "recettes/projet.md" in z.namelist()
    assert c.get("/api/projets/p-inconnu/export").status_code == 404
    # Reserve a l'administrateur.
    u = app.flask_app.test_client()
    assert u.get(f"/api/projets/{projet}/export").status_code == 401




def test_accepter_sans_tache_et_droits(boite, deux_espaces):
    c, _ = boite
    ident = _message(c, "hub", "Merci !")
    r = c.post(f"/api/messages/{ident}/accepter", json={})
    assert r.status_code == 200 and r.get_json()["tache"] is None
    assert c.get("/api/taches").get_json()["taches"] == []
    # Un compte utilisateur n'y a pas acces.
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    assert u.get("/api/taches").status_code == 403
    assert u.post(f"/api/messages/{ident}/accepter", json={}).status_code == 403




def test_l_application_messages_a_ses_vues():
    page = _page_panneau("messages.html")
    for trace in ("aller('projet'", "'planning'", "'feuille'", "'boite'", 'id="fen-tache"',
                  'id="fen-projet"', 'id="fen-grouper"', 'id="fen-scinder"', "history.pushState",
                  "'/api/taches/plan'", "/scinder'", "function planifier("):
        assert trace in page, trace
    # Pas d'etiquette d'urgence : la priorite, c'est la place dans le planning.
    assert "priorite" not in "".join(_scripts_de_la_page("messages.html"))
    # La feuille de route : aujourd'hui, semaine par semaine, sur des mois.
    for echelle in ("'jour'", "'semaines'", "'mois'", "[1,3,6,12]"):
        assert echelle in page, echelle





# ---------- l'application « Demandes » : tableau, priorites, projets ----------

def test_le_tableau_range_les_taches_par_colonne(boite):
    """Glisser une carte : la colonne d'arrivee est renvoyee entiere, dans son
    nouvel ordre ; une carte venue d'ailleurs y change de statut."""
    c, _ = boite
    ids = [c.post("/api/taches", json={"projet": "general", "titre": t}).get_json()["id"]
           for t in ("a", "b", "c")]
    a, b, cc = ids
    taches = c.get("/api/taches").get_json()["taches"]
    assert [t["statut"] for t in taches] == ["a_faire"] * 3
    assert all(t["priorite"] == 2 and t["echeance"] is None for t in taches)
    # « c » passe en cours, puis « a » le rejoint, au-dessus.
    assert c.put("/api/taches/ordre", json={"statut": "en_cours", "ordre": [cc]}).status_code == 200
    assert c.put("/api/taches/ordre", json={"statut": "en_cours", "ordre": [a, cc]}).status_code == 200
    taches = {t["id"]: t for t in c.get("/api/taches").get_json()["taches"]}
    assert taches[a]["statut"] == taches[cc]["statut"] == "en_cours"
    assert taches[a]["ordre"] < taches[cc]["ordre"] and taches[b]["statut"] == "a_faire"
    # « fait » tient la vieille case a cocher en accord.
    assert c.put("/api/taches/ordre", json={"statut": "fait", "ordre": [b]}).status_code == 200
    assert {t["id"]: t for t in c.get("/api/taches").get_json()["taches"]}[b]["faite"]
    # Une liste qui invente, repete ou se trompe de colonne est refusee.
    assert c.put("/api/taches/ordre", json={"statut": "fait", "ordre": ["x"]}).status_code == 409
    assert c.put("/api/taches/ordre", json={"statut": "fait", "ordre": [a, a]}).status_code == 400
    assert c.put("/api/taches/ordre", json={"statut": "ailleurs", "ordre": [a]}).status_code == 400




def test_une_tache_a_priorite_echeance_et_notes(boite):
    c, _ = boite
    r = c.post("/api/taches", json={"projet": "general", "titre": "Urgent", "priorite": 0,
                                    "echeance": "2026-12-01", "notes": "  voir le journal  "})
    assert r.status_code == 200, r.data
    ident = r.get_json()["id"]
    (t,) = c.get("/api/taches").get_json()["taches"]
    assert (t["priorite"], t["echeance"], t["notes"]) == (0, "2026-12-01", "voir le journal")
    # Changer de statut envoie la tache en fin de sa nouvelle colonne.
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
    l'application qui le concretise -- une application declaree, rien d'autre."""
    c, _ = boite
    app.save({"site": {"port": 8101, "titre": "Site"}})
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
    # Une tache peut rejoindre le projet par son identifiant.
    assert c.post("/api/taches", json={"projet": projet, "titre": "Tests"}).status_code == 200
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
    assert {t["projet"] for t in c.get("/api/taches").get_json()["taches"]} == {"general"}
    assert c.delete(f"/api/projets/{projet}").status_code == 404
    assert c.post("/api/taches", json={"projet": projet, "titre": "x"}).status_code == 400




def test_les_projets_se_reordonnent_en_entier(boite):
    c, _ = boite
    p1 = c.post("/api/projets", json={"nom": "Un"}).get_json()["id"]
    p2 = c.post("/api/projets", json={"nom": "Deux"}).get_json()["id"]
    assert c.put("/api/projets/ordre", json={"ordre": [p2, p1]}).status_code == 200
    assert [p["nom"] for p in c.get("/api/projets").get_json()["projets"]] == ["Deux", "Un"]
    assert c.put("/api/projets/ordre", json={"ordre": [p2]}).status_code == 409
    assert c.put("/api/projets/ordre", json={"ordre": "x"}).status_code == 400




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
    m = next(x for x in app.lire_messages() if x["id"] == ident)
    assert m["statut"] == "accepte"
    # Un projet sans nom ne se cree pas, et la demande reste a traiter.
    autre = _message(c, "idee", "Autre")
    assert c.post(f"/api/messages/{autre}/accepter",
                  json={"nouveau_projet": {"nom": " "}}).status_code == 400
    assert next(x for x in app.lire_messages() if x["id"] == autre).get("statut", "en_attente") == "en_attente"
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
    assert "projet" not in {m["id"]: m for m in c.get("/api/messages").get_json()["messages"]}[h]
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




def test_l_application_messages_est_reservee_a_l_administrateur(boite, deux_espaces):
    c, _ = boite
    r = c.get("/messages/planning")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "__JETON__" not in page and "function peindrePlanning(" in page
    assert c.get("/messages/projet/p-abc").status_code == 200
    # Un compte utilisateur a, a la meme adresse, SA rubrique Messages du
    # panneau (et jamais la page de l'administrateur) ; un anonyme va a la
    # connexion.
    u = app.flask_app.test_client()
    _connecte(u, "marie", "mot-de-passe-long")
    r = u.get("/messages")
    assert r.status_code == 200
    page_u = r.get_data(as_text=True)
    assert '<div id="sec-message" class="section"' in page_u
    assert "function peindrePlanning(" not in page_u
    for route in ("/api/projets", "/api/taches"):
        assert u.get(route).status_code == 403, route
    assert u.put("/api/taches/ordre", json={"statut": "fait", "ordre": []}).status_code == 403
    assert app.flask_app.test_client().get("/messages").status_code == 302
    for nom in ("messages", "demandes"):
        assert nom in app.PAGES_DU_PANNEAU and nom in app.adresses_reservees()




def test_la_tuile_messages_compte_les_non_lus(boite):
    """Une tuile du hub au meme rang que le diagnostic, qui dit combien de
    demandes attendent -- et que le panneau ouvre chez lui."""
    c, _ = boite
    _message(c, "hub", "Une")
    _message(c, "hub", "Deux")
    services = c.get("/api/mes-apps").get_json()["services"]
    tuile = next(s for s in services if s["name"] == app.MESSAGERIE_NOM)
    assert tuile["interne"] is True and tuile["url"] == "/messages" and tuile["non_lues"] == 2
    assert tuile["titre"] == "Messages"
    assert c.post("/api/messages/lus").status_code == 200
    services = c.get("/api/mes-apps").get_json()["services"]
    assert next(s for s in services if s["name"] == app.MESSAGERIE_NOM)["non_lues"] == 0
    # Le tableau des applications, lui, ne la compte pas parmi les siennes.
    assert app.MESSAGERIE_NOM not in [a.get("name") for a in c.get("/api/apps").get_json().get("apps", [])]
    page = _page_panneau("dashboard.html")
    assert "a.interne ? a.url" in page




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




def test_l_administrateur_est_prevenu_d_un_nouveau_message(deux_espaces, envois_push, monkeypatch):
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




def test_l_auteur_d_un_message_suit_son_avancement(deux_espaces, envois_push, monkeypatch):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    nav = _Navigateur()
    c.post("/api/notifications/abonnement", json=nav.abonnement())
    monkeypatch.setattr(app, "smtp_utilisable", lambda: ({}, False))
    monkeypatch.setattr(app, "_dernier_message", {})        # un message par minute
    assert c.post("/api/messages", json={"cible": "hub", "texte": "Une idee"}).status_code == 200
    (m,) = [x for x in app.lire_messages() if x["qui"] == "marie"]
    # Premier passage : on retient l'etat, on ne rejoue rien.
    app.suivi_push_tick(maintenant=1e10)
    assert envois_push == []
    app.modifier_message(m["id"], statut="refuse")
    app.suivi_push_tick(maintenant=1e10 + 120)
    (req,) = envois_push
    assert nav.lire(req.data)["titre"] == "Ton message n'est pas retenu"
    # Une fois annonce, plus rien tant que rien ne change.
    app.suivi_push_tick(maintenant=1e10 + 240)
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
