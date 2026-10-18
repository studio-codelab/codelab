# CodeLab -- LE PANNEAU, HUB ET PROXY
# ---------------------------------------------------------------------------
# ROLE        les versions et ouvertures, la composition du hub, le proxy (et son laissez-passer de tournage), les pages d'une application fermee, le diagnostic.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   versions et ouvertures, composition du hub, proxy, laissez-passer de tournage, application qu'on ne peut pas ouvrir, inscription du diagnostic.
#

# ------------------- versions des applications, et ouvertures -------------------
#
# « Une nouvelle version depuis la derniere fois que je l'ai ouverte. » Deux
# signaux font une version : un deploiement ou un build reussi depuis le
# panneau, et un nouveau commit dans le depot du projet (lu dans .git, sans
# lancer git). Chaque compte garde la version qu'il a vue en ouvrant
# l'application ; la tuile le dit quand elle a change depuis.

def commit_du_projet(chemin):
    """Le commit courant du depot du projet, ou None (pas de depot lisible).

    Lu directement dans .git -- HEAD, puis la reference, puis packed-refs --
    pour ne pas lancer un processus par application a chaque hub affiche.
    """
    if not chemin:
        return None
    racine = os.path.join(chemin, ".git")
    try:
        with open(os.path.join(racine, "HEAD"), encoding="utf-8") as f:
            tete = f.read().strip()
        if not tete.startswith("ref:"):
            return tete[:40] or None
        ref = tete[4:].strip()
        if ".." in ref.split("/"):
            return None
        chemin_ref = os.path.join(racine, *ref.split("/"))
        if os.path.isfile(chemin_ref):
            with open(chemin_ref, encoding="utf-8") as f:
                return f.read().strip()[:40] or None
        with open(os.path.join(racine, "packed-refs"), encoding="utf-8") as f:
            for ligne in f:
                morceaux = ligne.split()
                if len(morceaux) == 2 and morceaux[1] == ref:
                    return morceaux[0][:40]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def versions_des_apps(apps):
    """{nom: horodatage de la version courante}, en notant au passage un
    commit nouveau. Une application jamais vue prend sa version maintenant :
    personne ne l'a encore ouverte, rien ne s'affiche."""
    maintenant = int(time.time())
    connues = {l["app"]: l for l in stockage.lire(DB_FILE, "SELECT * FROM versions_apps")}
    resultat, ecrire = {}, []
    for nom, a in apps.items():
        commit = commit_du_projet(a.get("path"))
        ligne = connues.get(nom)
        if ligne is None:
            ecrire.append((nom, maintenant, commit))
            resultat[nom] = maintenant
        elif commit and commit != ligne["commit_"]:
            ecrire.append((nom, maintenant, commit))
            resultat[nom] = maintenant
        else:
            resultat[nom] = ligne["ts"]
    if ecrire:
        with stockage.transaction(DB_FILE) as cx:
            cx.executemany("INSERT OR REPLACE INTO versions_apps (app, ts, commit_) "
                           "VALUES (?, ?, ?)", ecrire)
    return resultat


def marquer_nouvelle_version(nom):
    """Un deploiement ou un build a reussi : c'est une nouvelle version."""
    a = load().get(nom) or {}
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("INSERT OR REPLACE INTO versions_apps (app, ts, commit_) VALUES (?, ?, ?)",
                   (nom, int(time.time()), commit_du_projet(a.get("path"))))


def ouvertures_du_compte(qui=None):
    """{application: {ts, version}} : ce que ce compte a ouvert, et quand."""
    qui = qui or utilisateur_courant() or NOM_ADMIN
    return {l["app"]: {"ts": l["ts"], "version": l["version"]}
            for l in stockage.lire(DB_FILE, "SELECT app, ts, version FROM apps_vues "
                                            "WHERE compte = ?", (qui,))}


# ------------------------- la composition du hub -------------------------
#
# Le style Bento se COMPOSE comme un ecran d'accueil : chaque compte range
# ses tuiles d'application et ses widgets dans l'ordre qu'il veut, et choisit
# la taille de chaque widget. La disposition est gardee cote serveur, pour
# suivre la personne d'un appareil a l'autre.
#
# Peu de widgets, et seulement ceux qui parlent du hub ou de ses
# applications : pas d'horloge ni de bloc-notes, que l'ordinateur a deja.
# Le catalogue est FERME : rien de ce que le client envoie ne devient un nom
# de widget.
#   portee « hub »  : un widget sur le hub lui-meme ;
#   portee « app »  : un widget sur UNE application, a choisir.
WIDGETS = {
    "une": {"titre": "Dernière ouverte", "portee": "hub", "tailles": ("moyen", "grand"),
            "description": "L'application ouverte en dernier, en aperçu."},
    "etat": {"titre": "État des applications", "portee": "hub", "tailles": ("petit", "moyen"),
             "description": "Combien répondent, et laquelle ne répond pas.", "admin": True},
    "messages": {"titre": "Messages", "portee": "hub", "tailles": ("petit", "moyen"),
                 "description": "Les messages à lire et à classer.", "admin": True},
    "sauvegardes": {"titre": "Sauvegardes", "portee": "hub", "tailles": ("petit", "moyen"),
                    "description": "La dernière sauvegarde, et si une copie existe ailleurs.",
                    "admin": True},
    "serveur": {"titre": "Serveur", "portee": "hub", "tailles": ("petit", "moyen", "grand"),
                "description": "Processeur, mémoire et disque, en direct.", "admin": True},
    "activite": {"titre": "Activité", "portee": "hub", "tailles": ("moyen", "grand"),
                 "description": "Les ouvertures des deux dernières semaines.", "admin": True},
    "nouveautes": {"titre": "Nouveautés", "portee": "hub", "tailles": ("petit", "moyen"),
                   "description": "Les applications que tu n'as pas encore ouvertes, et celles "
                                  "mises à jour depuis ta dernière visite."},
    "message": {"titre": "Écrire à l'administrateur", "portee": "hub", "tailles": ("moyen", "grand"),
                "description": "Une idée, un souci, une demande.", "utilisateur": True},
    "suivi": {"titre": "Mes messages", "portee": "hub", "tailles": ("petit", "moyen"),
              "description": "Où en sont les messages que tu as envoyés.", "utilisateur": True},
    "apercu": {"titre": "Aperçu", "portee": "app", "tailles": ("moyen", "grand"),
               "description": "La page d'une application, en direct."},
}
DISPOSITION_MAX = 300


def widgets_permis():
    admin = est_admin()
    return [w for w, d in WIDGETS.items()
            if not (d.get("admin") and not admin) and not (d.get("utilisateur") and admin)]


def lire_preference(cle, qui=None):
    qui = qui or utilisateur_courant() or NOM_ADMIN
    lignes = stockage.lire(DB_FILE, "SELECT valeur FROM preferences WHERE compte = ? AND cle = ?",
                           (qui, cle))
    if not lignes:
        return None
    try:
        return json.loads(lignes[0]["valeur"])
    except ValueError:
        return None


def ecrire_preference(cle, valeur, qui=None):
    qui = qui or utilisateur_courant() or NOM_ADMIN
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("INSERT OR REPLACE INTO preferences (compte, cle, valeur) VALUES (?, ?, ?)",
                   (qui, cle, json.dumps(valeur, ensure_ascii=False)))


def apps_du_hub_visibles():
    """Les tuiles qu'un compte peut ranger : ses applications, et les services."""
    autorises = projets_autorises()
    noms = {n for n in apps_actives() if peut_voir(n) and (autorises is None or n in autorises)}
    return noms, {s["name"] for s in services_du_hub()}


def disposition_propre(elements):
    """Valide une disposition. Rend (liste propre, erreur)."""
    if not isinstance(elements, list) or len(elements) > DISPOSITION_MAX:
        return None, "Disposition invalide."
    permis = widgets_permis()
    apps, services = apps_du_hub_visibles()
    propres, vus = [], set()
    for e in elements:
        if not isinstance(e, dict):
            return None, "Disposition invalide."
        if e.get("t") == "a":
            nom = e.get("id")
            if nom not in apps and nom not in services:
                continue        # une application disparue ou retiree : on l'oublie
            cle = "a:" + nom
            propre = {"t": "a", "id": nom}
        elif e.get("t") == "w":
            wid, taille = e.get("id"), e.get("taille")
            if wid not in permis:
                return None, "Widget inconnu."
            if taille not in WIDGETS[wid]["tailles"]:
                return None, "Taille inconnue pour ce widget."
            propre = {"t": "w", "id": wid, "taille": taille}
            if WIDGETS[wid]["portee"] == "app":
                if e.get("app") not in apps:
                    return None, "Application inconnue."
                propre["app"] = e["app"]
            cle = "w:" + wid + ":" + (propre.get("app") or "")
        else:
            return None, "Disposition invalide."
        if cle in vus:
            return None, "Élément en double."
        vus.add(cle)
        propres.append(propre)
    return propres, None


def disposition_du_compte():
    """La disposition enregistree, ou celle de depart."""
    elements = lire_preference("hub_disposition")
    if not isinstance(elements, list):
        # Serveur et Activite ne tiennent que chez l'administrateur ; le
        # filtre des widgets permis ci-dessous les retire pour les autres.
        elements = [{"t": "w", "id": "une", "taille": "grand"},
                    {"t": "w", "id": "nouveautes", "taille": "moyen"},
                    {"t": "w", "id": "serveur", "taille": "moyen"},
                    {"t": "w", "id": "activite", "taille": "moyen"},
                    {"t": "w", "id": "etat", "taille": "petit"},
                    {"t": "w", "id": "messages", "taille": "petit"},
                    {"t": "w", "id": "message", "taille": "moyen"},
                    {"t": "w", "id": "suivi", "taille": "moyen"}]
    permis = widgets_permis()
    apps, services = apps_du_hub_visibles()
    garde = []
    for e in elements:
        if not isinstance(e, dict):
            continue
        if e.get("t") == "w":
            d = WIDGETS.get(e.get("id"))
            if not d or e["id"] not in permis:
                continue
            if e.get("taille") not in d["tailles"]:
                e = dict(e, taille=d["tailles"][-1] if e.get("taille") == "grand" else d["tailles"][0])
            if d["portee"] == "app" and e.get("app") not in apps:
                continue
        elif e.get("t") == "a" and e.get("id") not in apps and e.get("id") not in services:
            continue
        garde.append(e)
    return garde


@flask_app.get("/api/hub/disposition")
@require_auth
def api_disposition():
    """La disposition du hub, et le catalogue des widgets que ce compte peut poser.

    Les applications absentes de la disposition (nouvelles, jamais rangees)
    viennent a la suite : c'est la page qui les y ajoute, dans l'ordre des noms.
    """
    return jsonify({"elements": disposition_du_compte(),
                    "catalogue": [{"id": w, "titre": WIDGETS[w]["titre"],
                                   "description": WIDGETS[w]["description"],
                                   "portee": WIDGETS[w]["portee"],
                                   "tailles": list(WIDGETS[w]["tailles"])}
                                  for w in widgets_permis()]})


@flask_app.get("/api/hub/pouls")
@require_admin
def api_hub_pouls():
    """Les chiffres des widgets « Serveur » et « Activité ».

    Reserve a l'administrateur : la charge de la machine et le rythme des
    ouvertures ne regardent que lui, comme le journal des acces dont le
    second est tire.
    """
    quoi = set((request.args.get("quoi") or "serveur,activite").split(","))
    out = {}
    if "serveur" in quoi:
        out["serveur"] = etat_serveur()
    if "activite" in quoi:
        out["activite"] = activite_recente()
    return jsonify(out)


@flask_app.put("/api/hub/disposition")
@require_auth
def api_disposition_modifier():
    """{elements: [{t: "a", id} | {t: "w", id, taille, app?}]}, dans l'ordre."""
    propres, erreur = disposition_propre((request.get_json(force=True, silent=True) or {})
                                         .get("elements"))
    if erreur:
        return jsonify({"error": erreur}), 400
    ecrire_preference("hub_disposition", propres)
    return jsonify({"ok": True, "elements": propres})


def application_fonctionne(nom):
    """Lancee, et son port repond -- ou pas encore sonde (None), ce qui
    arrive juste apres un demarrage : on ne la tient pas pour en panne sur
    une absence de mesure."""
    return is_running(nom) and _listening.get(nom) is not False


@flask_app.post("/api/mes-apps/<n>/ouverte")
@require_auth
def api_app_ouverte(n):
    """Le hub previent qu'il ouvre une application : on note la version vue.

    Seule une application que ce compte peut ouvrir est notee -- sinon la
    route dirait l'existence de projets qu'il n'a pas le droit de connaitre.

    Vecu : « une application qui ne fonctionne pas ne doit pas compter comme
    une derniere application ouverte dans le widget ». Arretee, ou lancee
    mais muette sur son port, elle n'est pas notee : le widget « Derniere
    ouverte » garde la precedente, et sa version n'est pas « vue ». La
    reponse est la meme dans les deux cas : un compte utilisateur ne doit
    pas lire l'etat des applications par ce biais.
    """
    apps = load()
    autorises = projets_autorises()
    if n not in apps or not peut_voir(n) or (autorises is not None and n not in autorises):
        return jsonify({"error": "Application inconnue."}), 404
    if not application_fonctionne(n):
        return jsonify({"ok": True})
    version = versions_des_apps({n: apps[n]})[n]
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("INSERT OR REPLACE INTO apps_vues (compte, app, ts, version) VALUES (?, ?, ?, ?)",
                   (utilisateur_courant() or NOM_ADMIN, n, int(time.time()), version))
    return jsonify({"ok": True})


@flask_app.post("/api/mes-apps/<n>/masquer")
@require_auth
def api_masquer(n):
    """Retire une application du hub de CE compte.

    Ce n'est pas un droit retire : le projet tourne toujours, les autres
    comptes le voient, et son adresse reste ouverte a qui la connait. On ne
    peut masquer que ce qu'on peut deja voir -- sinon la liste des masquees
    dirait l'existence de projets qu'on n'a pas le droit de connaitre.

    LES SERVICES DE LA STACK AUSSI. Dagster n'est pas une application
    declaree : il ne figure pas dans load(), et cette route le refusait donc
    en 404. Sa tuile etait la seule qu'on ne pouvait pas ecarter de son
    propre hub. peut_voir() garde le dernier mot -- pour une application par
    defaut, il exige l'administrateur -- donc ouvrir ce nom-ci n'ouvre rien
    de plus a un compte utilisateur.
    """
    if not (n in load() or n in SERVICES_MASQUABLES) or not peut_voir(n):
        return jsonify({"error": "Application inconnue."}), 404
    qui = utilisateur_courant() or NOM_ADMIN
    d = lire_masquees()
    liste = [x for x in (d.get(qui) or []) if isinstance(x, str)]
    if n not in liste:
        liste.append(n)
        d[qui] = sorted(liste)
        ecrire_masquees(d)
        # Journalise : l'administrateur doit pouvoir constater qu'un projet a
        # disparu d'un hub sans que personne n'ait touche a ses droits.
        journaliser("masquage", qui=qui, app=n, action="masque",
                    ip=_adresse_client())
    return jsonify({"ok": True, "masquees": sorted(liste)})


@flask_app.delete("/api/mes-apps/<n>/masquer")
@require_auth
def api_afficher(n):
    """Remet une application dans le hub de ce compte."""
    qui = utilisateur_courant() or NOM_ADMIN
    d = lire_masquees()
    liste = [x for x in (d.get(qui) or []) if isinstance(x, str)]
    if n in liste:
        liste.remove(n)
        # On retire la cle vide plutot que de laisser un tableau vide : le
        # fichier reste lisible a l'oeil, et un compte supprime ne laisse pas
        # d'entree derriere lui.
        if liste:
            d[qui] = sorted(liste)
        else:
            d.pop(qui, None)
        ecrire_masquees(d)
        journaliser("masquage", qui=qui, app=n, action="affiche",
                    ip=_adresse_client())
    return jsonify({"ok": True, "masquees": sorted(liste)})


# ------------------------------ proxy --------------------------------

def strip_session_cookie(raw):
    """Retire d'un en-tete Cookie ceux du panneau : la session, et le cookie
    de tournage (voir « le laissez-passer de tournage ») -- l'un comme
    l'autre ouvrirait une porte a qui le lirait (regle 19)."""
    noms = {flask_app.config.get("SESSION_COOKIE_NAME") or "session", TOURNAGE_COOKIE}
    gardes = [c.strip() for c in raw.split(";")
              if c.strip() and c.split("=", 1)[0].strip() not in noms]
    return "; ".join(gardes)


# Le ruban de retour, glisse dans les pages HTML servies par le proxy.
#
# POURQUOI L'INJECTER PLUTOT QUE LE DEMANDER AUX APPLICATIONS : une
# application deployee est du code quelconque, souvent ecrit avant d'arriver
# ici, et parfois pas par nous. Lui demander d'ajouter un lien vers le hub,
# c'est n'en avoir aucun dans la plupart des cas. Le proxy, lui, voit passer
# toutes les pages.
#
# REDUIT PAR DEFAUT. Vecu : « Le bouton codelab dans les applications doit
# pouvoir se developper et reduire (petite fleche au bord de l'ecran) pour
# laisser la place a l'app par defaut », puis « plus gros, lorsque je
# developpe, pas de bouton pour reduire, il reste developpe que 10 secondes
# si je ne clique pas ». Une languette de 30 x 46 px au bord gauche de
# l'ecran ; un appui la remplace par le bouton « ← CodeLab », qui se replie
# tout seul au bout de 10 s. Chaque page repart repliee.
# <details> pour l'ouverture : elle marche sans script, au clavier aussi. Le
# script (RUBAN_JS) ne fait que replier apres 10 s ; bloque par la CSP de
# l'application, le bouton reste deplie jusqu'a la page suivante, sans rien
# casser.
#
# Une feuille a part, aux identifiants improbables, et all:initial : la page
# de l'application a ses propres regles, et le ruban ne doit ni les subir ni
# les changer.
# La meme feuille et le meme script vivent dans l'interface Mailpit
# (mailpit-ui/app.css et app.js, sa politique interdit tout code en ligne) et
# dans le sub_filter de Dagster (dagster/proxy/nginx.conf) : un test les
# tient identiques. Pas d'apostrophe dedans : la chaine de nginx en est
# delimitee.
RUBAN_CSS = (
    # Au-dessus de la barre de geste et a cote de l'encoche : les bords que
    # pose environnement-app.js (0 hors du hub installe).
    "#codelab-ruban{all:initial;position:fixed;left:var(--codelab-bord-gauche, 0px);"
    "bottom:calc(14px + var(--codelab-bord-bas, 0px));z-index:2147483647;"
    "display:flex;align-items:center}"
    "#codelab-ruban>summary{all:initial;display:flex;align-items:center;justify-content:center;"
    "width:30px;height:46px;color:#fff;background:#141a21;border-radius:0 999px 999px 0;"
    "cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.28);list-style:none}"
    "#codelab-ruban>summary::-webkit-details-marker{display:none}"
    "#codelab-ruban>summary::marker{content:\"\"}"
    "#codelab-ruban>summary>span{all:initial;color:#fff;font:600 26px/1 sans-serif;cursor:pointer}"
    # Deplie : plus de languette, le bouton seul. Il se replie tout seul.
    "#codelab-ruban[open]>summary{display:none}"
    "#codelab-retour-hub{all:initial;display:inline-flex;align-items:center;gap:8px;"
    "margin-left:14px;padding:11px 16px;"
    "font:600 14px/1 -apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;"
    "color:#fff;background:#141a21;border-radius:999px;cursor:pointer;"
    "box-shadow:0 2px 10px rgba(0,0,0,.28);text-decoration:none}"
    "#codelab-retour-hub>span{all:initial;color:#fff;cursor:pointer;"
    "font:600 14px/1 -apple-system,BlinkMacSystemFont,sans-serif}"
    "#codelab-retour-hub>span:first-child{font:600 17px/1 sans-serif}"
    # Au clavier, la languette et le bouton se voient quand on les atteint.
    "#codelab-ruban>summary:focus-visible,#codelab-retour-hub:focus-visible"
    "{outline:2px solid #4c8eff;outline-offset:2px}"
)
# Replie le bouton 10 s apres l'avoir deplie. ES5, dans un try : il tourne
# dans des pages qu'on ne controle pas.
RUBAN_JS = (
    "(function(){try{var d=document.getElementById(\"codelab-ruban\"),t=null;"
    "d.addEventListener(\"toggle\",function(){clearTimeout(t);"
    "if(d.open){t=setTimeout(function(){d.open=false;},10000);}});}catch(e){}})();"
)


def ruban_retour():
    """Le ruban, construit a chaque page : son lien depend de l'origine."""
    return (
        "<style>" + RUBAN_CSS + "</style>"
        '<details id="codelab-ruban">'
        '<summary title="CodeLab" aria-label="Afficher le bouton CodeLab"><span>&#8250;</span></summary>'
        # target=_top : dans le panneau installe, l'application est encadree
        # (voir ouvrir_dans_le_panneau). Sans lui, le hub s'ouvrirait DANS
        # le cadre, un panneau dans le panneau.
        '<a href="' + lien_panneau() + '" target="_top" id="codelab-retour-hub" '
        'title="Revenir au hub CodeLab"><span>&#8592;</span><span>CodeLab</span></a>'
        "</details><script>" + RUBAN_JS + "</script>"
    ).encode()


# L'ENVIRONNEMENT D'UNE APPLICATION : ce qui l'entoure (site ou app, appareil,
# plateforme, bords de l'ecran), lu ou simule, et pose sur sa page avant ses
# propres scripts. Le detail est dans environnement-app.js. Glisse EN TETE :
# un script qui demande navigator.standalone doit deja avoir la reponse.
ENVIRONNEMENT_APP_JS = _lire_ressource("environnement-app.js")


def script_environnement(connecte):
    """Le script, avec l'origine du panneau pour qui est connecte.

    Un visiteur anonyme n'a pas a lire l'adresse du panneau : il recoit le
    script sans elle, qui lit l'environnement sans rien simuler -- il n'est
    de toute facon jamais encadre par le panneau.
    """
    panneau = ""
    if connecte:
        # Relatif (« / ») quand panneau et applications partagent l'adresse :
        # l'origine est alors celle de la requete.
        u = urllib.parse.urlsplit(urllib.parse.urljoin(request.host_url, lien_panneau()))
        if u.scheme and u.netloc:
            panneau = u.scheme + "://" + u.netloc
    return ("<script>" + ENVIRONNEMENT_APP_JS.replace("__PANNEAU__", json.dumps(panneau))
            + "</script>").encode()


def injecter_environnement(data, status, entetes, connecte):
    """Glisse le script juste apres <head>, avant tout ce que la page charge."""
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    m = re.search(rb"<head(\s[^>]*)?>", data[:65536], re.I)
    if not m:
        return data, entetes
    data = data[:m.end()] + script_environnement(connecte) + data[m.end():]
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


def _entete(entetes, nom):
    for k, v in entetes:
        if k.lower() == nom:
            return v
    return ""


def injecter_ruban(data, status, entetes):
    """Glisse le ruban de retour avant </body>, quand c'est sans risque.

    Quatre refus, et chacun evite de casser une application :

      - un code autre que 200 : une page d'erreur de l'application n'a pas a
        etre retouchee ;
      - autre chose que du HTML : une image ou du JSON ne se modifient pas ;
      - un corps COMPRESSE : les octets ne contiennent alors pas "</body>",
        et y ecrire ferait un flux illisible ;
      - pas de </body> : fragment HTML renvoye a du JavaScript, reponse
        partielle. On ne devine pas ou l'inserer.

    Travaille sur les OCTETS et jamais sur du texte decode : une page dans un
    encodage qu'on aurait mal devine reviendrait abimee, et une page n'a pas
    a payer le passage par le proxy.
    """
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    i = data.lower().rfind(b"</body>")
    if i < 0:
        return data, entetes
    data = data[:i] + ruban_retour() + data[i:]
    # Content-Length devient faux si on ne le refait pas : le navigateur
    # tronquerait la page a l'ancienne taille, juste avant le ruban.
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


# L'ICONE D'ONGLET D'UNE APPLICATION. Le panneau porte le logo CodeLab dans
# ses onglets ; une application porte le SIEN, et seulement la. Beaucoup
# d'applications n'en declarent aucun : l'onglet restait vide, ou prenait
# ce que le navigateur trouvait. Le logo qu'on voit dans le hub est donc
# pose comme icone d'onglet -- a condition que la page n'en declare pas
# deja une : celle de l'application gagne toujours.
_MARQUES_ICONE = (b'rel="icon"', b"rel='icon'", b"rel=icon", b'rel="shortcut icon"',
                  b"rel='shortcut icon'", b'rel="apple-touch-icon"')


def injecter_icone(data, status, entetes, nom):
    """Ajoute <link rel="icon"> avant </head>, si la page n'en a aucun."""
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    bas = data.lower()
    i = bas.find(b"</head>")
    if i < 0 or any(m in bas[:i] for m in _MARQUES_ICONE):
        return data, entetes
    lien = (lien_panneau() + "api/icon/" + urllib.parse.quote(nom, safe="")).encode()
    data = data[:i] + b'<link rel="icon" href="' + lien + b'">' + data[i:]
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


# CHAQUE APPLICATION S'AJOUTE A L'ECRAN D'ACCUEIL COMME UNE APP.
#
# Le panneau le fait pour lui-meme (voir ecran_accueil_manifeste). Une
# application deployee, elle, s'ajoutait comme un simple signet : capture de
# la page en guise d'icone, onglet Safari a l'ouverture, barre d'adresse et
# retour arriere compris. Le proxy lui donne donc ce qu'il faut, comme il lui
# donne deja son ruban et son icone d'onglet :
#
#   - un manifeste PROPRE A L'APPLICATION, servi sous son prefixe
#     (/<nom>/__codelab/manifest.webmanifest). Sa portee s'arrete a /<nom>/ :
#     l'app installee reste dans son projet, et un lien vers un autre projet
#     sort de l'app au lieu de la detourner ;
#   - les balises apple-mobile-web-app-*, et une icone servie sur l'ORIGINE
#     DE L'APPLICATION -- /api/icon vit sur celle du panneau et exige une
#     session, qu'une app fraichement ajoutee n'a pas encore.
#
# Chaque piece n'est posee que si la page ne la declare pas deja : une
# application qui a son propre manifeste ou sa propre icone garde les siens.
#
# Le ruban « ← CodeLab » reste affiche en mode app : c'est le seul chemin
# vers le hub depuis une app installee, et il est voulu.
PREFIXE_CODELAB = "__codelab/"


def racine_application(nom):
    """Le chemin ou commence l'application, vu de son navigateur.

    « / » sur son sous-domaine (blog.toi.duckdns.org), « /<nom>/ » sur
    l'adresse partagee. Le manifeste, son icone et sa portee en dependent :
    ecrits « /blog/ » sur le sous-domaine, ils pointaient vers une page que
    l'application n'a pas, et l'app installee s'ouvrait sur une erreur.
    """
    if getattr(g, "sous_domaine", ""):
        return "/"
    return "/" + urllib.parse.quote(nom, safe="") + "/"


def injecter_webapp(data, status, entetes, nom, titre):
    """Ajoute avant </head> ce qui fait de la page une app installable."""
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    bas = data.lower()
    i = bas.find(b"</head>")
    if i < 0:
        return data, entetes
    tete = bas[:i]
    base = racine_application(nom) + PREFIXE_CODELAB
    titre_html = (titre.replace("&", "&amp;").replace("<", "&lt;")
                  .replace(">", "&gt;").replace('"', "&quot;"))
    ajout = ""
    if b'rel="manifest"' not in tete and b"rel='manifest'" not in tete \
            and b"rel=manifest" not in tete:
        ajout += '<link rel="manifest" href="' + base + 'manifest.webmanifest">'
    if b"apple-touch-icon" not in tete:
        ajout += '<link rel="apple-touch-icon" href="' + base + 'icone.png">'
    if b"apple-mobile-web-app-capable" not in tete:
        ajout += '<meta name="apple-mobile-web-app-capable" content="yes">'
    if b"mobile-web-app-capable" not in tete.replace(b"apple-mobile-web-app-capable", b""):
        ajout += '<meta name="mobile-web-app-capable" content="yes">'
    if b"apple-mobile-web-app-title" not in tete:
        ajout += '<meta name="apple-mobile-web-app-title" content="' + titre_html + '">'
    data = data[:i] + ajout.encode() + data[i:]
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


def webapp_manifeste(nom, titre):
    """Le manifeste d'une application : sa portee, son nom, son icone."""
    racine = racine_application(nom)
    manifeste = {
        "name": titre,
        "short_name": titre,
        "start_url": racine,
        "scope": racine,
        "display": "standalone",
        # La couleur du fond et du bandeau (style par defaut, clair) : c'est
        # celle de l'ecran de lancement et de la barre d'etat d'Android. Une
        # barre blanche au-dessus d'un bandeau gris cassait l'effet
        # d'application ; la page l'ajuste ensuite au theme choisi.
        "background_color": "#E9EBEE",
        "theme_color": "#E9EBEE",
        "icons": [{"src": racine + PREFIXE_CODELAB + "icone.png",
                   "sizes": "180x180", "purpose": "any"}],
    }
    r = Response(json.dumps(manifeste), mimetype="application/manifest+json")
    r.headers["Cache-Control"] = "no-cache"
    # La reponse depend de l'appareil : un cache intermediaire ne doit pas
    # servir au telephone le manifeste de la tablette.
    r.headers["Vary"] = "User-Agent"
    return r


def webapp_icone(a):
    """L'icone d'ecran d'accueil d'une application.

    iOS ne lit ni le SVG ni l'initiale coloree du hub : il lui faut une image
    matricielle. Le logo depose dans le projet sert s'il en est une (PNG ou
    JPEG, reconnu a sa signature) ; sinon, le logo CodeLab.
    """
    chemin = find_icon(a["path"]) if a.get("path") else None
    if chemin:
        try:
            with open(chemin, "rb") as f:
                octets = f.read()
        except OSError:
            octets = b""
        for signature, type_mime in ((b"\x89PNG\r\n\x1a\n", "image/png"),
                                     (b"\xff\xd8\xff", "image/jpeg")):
            if octets.startswith(signature):
                r = Response(octets, mimetype=type_mime)
                break
        else:
            r = Response(ICONE_ECRAN_ACCUEIL_PNG, mimetype="image/png")
    else:
        r = Response(ICONE_ECRAN_ACCUEIL_PNG, mimetype="image/png")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


# QUI PEUT AFFICHER UNE APPLICATION DANS UN CADRE : LE PANNEAU, ET LUI SEUL.
#
# Sans regle, n'importe quel site pouvait poser une application dans une
# iframe invisible et faire cliquer l'utilisateur dedans a son insu
# (clickjacking) -- avec SA session, puisque le navigateur l'envoie. A
# l'inverse, une application qui se protegeait elle-meme (X-Frame-Options,
# pose d'office par Django, Rails, Helmet...) s'affichait vide dans le
# panneau installe, qui l'encadre (voir ouvrir_dans_le_panneau) : DENY
# refuse tout, et SAMEORIGIN refuse le panneau, qui vit sur un autre port.
#
# Le proxy tranche donc pour toutes : frame-ancestors n'autorise que le
# panneau, sous chacune des adresses par lesquelles on l'atteint -- celle
# publiee par un reverse proxy, le meme hote que la requete sur le port du
# panneau, et localhost pour l'adresse locale. X-Frame-Options est retire :
# il ne sait pas nommer une autre origine, et frame-ancestors le remplace.
#
# Une application qui pose SA propre regle frame-ancestors la garde : c'est
# un choix explicite, et les politiques CSP se cumulent -- la plus stricte
# l'emporte.
def origines_encadrantes():
    """Les sources frame-ancestors : le panneau, sous toutes ses adresses."""
    if not origines_separees():
        return "'self'"
    sources = []
    domaine = domaine_des_applications()
    if domaine:
        sources.append(f"{schema_vu()}://{domaine}")
    hote = hote_demande()
    if hote and has_request_context():
        sources.append(f"{request.scheme}://{hote}:{PANEL_PORT}")
    sources += [f"http://localhost:{PANEL_PORT}", f"http://127.0.0.1:{PANEL_PORT}"]
    return " ".join(dict.fromkeys(sources))


def proteger_l_encadrement(entetes):
    entetes = [(k, v) for k, v in entetes if k.lower() != "x-frame-options"]
    politiques = " ".join(v for k, v in entetes
                          if k.lower() == "content-security-policy").lower()
    if "frame-ancestors" not in politiques:
        entetes.append(("Content-Security-Policy",
                        "frame-ancestors " + origines_encadrantes()))
    return entetes


# Les connexions vers les applications. Le proxy gardait UNE connexion TCP par
# requete (urllib.request.urlopen) : poignee de main, puis souvent un fil neuf
# cote application (http.server, Flask en developpement), pour chaque image,
# chaque script, chaque appel d'API d'une page.
# Une connexion se rend apres usage et resert a la requete suivante vers le
# meme port. Mesure (banc de charge sous waitress, application http.server) :
# page de 20 Ko 378 -> 457 req/s a un client, 487 -> 521 a 200 ; JSON
# 455 -> 586 et 555 -> 626. Seul, sans waitress : 1255 -> 4670 req/s.
# Deux pieges mesures, expliques sur place : l'acquittement differe
# (_accuser_reception_sans_attendre) et le verrou tenu pendant un appel
# systeme (_connexion_vers).
#
# CE QUI NE CHANGE PAS, et que le proxy decide avant d'arriver ici : quels
# en-tetes partent (regle 19, en-tetes X-CodeLab-*), le delai de 30 s, la
# page neutre d'une application muette.
#
# UNE REQUETE N'EST JAMAIS ENVOYEE DEUX FOIS. Une connexion gardee peut avoir
# ete fermee par l'application entre deux requetes (Node la ferme apres 5 s,
# un redemarrage la coupe) : on ne le sait qu'en l'utilisant. Une requete
# qui ne modifie rien (GET, HEAD) se rejoue alors une fois sur une connexion
# neuve. Toute autre methode part sur une connexion neuve : rejouer un
# paiement, un envoi de formulaire ou une suppression, faute de savoir s'il
# avait ete recu, est pire que la poignee de main economisee. (PUT et DELETE
# sont idempotents selon HTTP, pas forcement dans le code d'une application.)
#
# LES REDIRECTIONS. urllib suivait toute redirection cote serveur, y compris
# vers un autre site : le panneau allait chercher la page a la place du
# navigateur, et la servait sous l'adresse de l'application (une connexion
# « avec Google » ne pouvait pas marcher). On garde ce que l'application
# attend vers ELLE-MEME -- un « /login » absolu, que le navigateur aurait
# envoye a la racine de l'origine des applications, sans le /<nom>/ -- avec
# les regles de methode d'urllib. Une redirection vers une autre adresse
# repart au navigateur telle quelle.
# ESSAYE ET ECARTE : renvoyer toutes les redirections au navigateur. Un
#   Location « /login » sortirait du prefixe de l'application, et c'est ce
#   qu'ecrivent la plupart des cadriciels.
# Autant que de fils servant des requetes : chacun peut en tenir une.
CONNEXIONS_LIBRES_PAR_PORT = WSGI_THREADS
CONNEXION_LIBRE_MAX_S = 4         # sous les 5 s de Node (keepAliveTimeout)
REDIRECTIONS_SUIVIES = 10         # comme urllib (max_redirections)
METHODES_REJOUABLES = ("GET", "HEAD")
_connexions_libres = {}           # port -> [(connexion, instant ou elle a ete rendue)]
_connexions_verrou = threading.Lock()


def _connexion_vers(port, reutilisable):
    """Une connexion libre et encore ouverte vers ce port, sinon une neuve.
    Rend (connexion, reutilisee)."""
    while reutilisable:
        # Rien sous le verrou qui rende la main a Python (appel systeme,
        # fermeture) : le fil qui le tient attendrait le GIL jusqu'a 5 ms,
        # et les 15 autres derriere lui. Vecu : a 200 clients, la moitie du
        # temps se passait a attendre ce verrou, et le debit tombait de moitie.
        with _connexions_verrou:
            libres = _connexions_libres.get(port)
            if not libres:
                break
            conn, rendue = libres.pop()
        # Lisible sans rien avoir demande : l'application a ferme (ou
        # envoye un reste). Elle ne resservira pas.
        if (time.monotonic() - rendue < CONNEXION_LIBRE_MAX_S and conn.sock
                and not select.select([conn.sock], [], [], 0)[0]):
            return conn, True
        conn.close()
    return http.client.HTTPConnection("127.0.0.1", port, timeout=30), False


def _rendre_connexion(port, conn):
    rendue = time.monotonic()
    with _connexions_verrou:
        libres = _connexions_libres.setdefault(port, [])
        if len(libres) < CONNEXIONS_LIBRES_PAR_PORT:
            libres.append((conn, rendue))
            return
    conn.close()


def _accuser_reception_sans_attendre(conn):
    """Accuse reception de chaque segment de la reponse sans le delai de 40 ms.

    Vecu : connexions gardees, une page de 20 Ko passait de 2,5 a 44 ms. Une
    application ecrite sur http.server (Flask en developpement compris)
    envoie l'en-tete, puis le corps : Nagle retient le corps tant que
    l'en-tete n'est pas acquitte, et Linux, sur une connexion qui dure,
    differe cet acquittement de 40 ms. Une connexion neuve ne le voyait pas
    (Linux acquitte tout de suite ses premiers segments).
    TCP_QUICKACK ne dure pas : Linux le retire de lui-meme, d'ou un appel par
    requete, juste avant d'attendre la reponse.
    ESSAYE ET ECARTE : TCP_NODELAY de notre cote. Il regle nos envois, pas
      ceux de l'application, qui sont ceux qui attendent.
    """
    try:
        conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
    except (AttributeError, OSError):
        pass


def _envoyer(port, methode, chemin, entetes, corps):
    rejouable = methode in METHODES_REJOUABLES
    for essai in (1, 2):
        conn, reutilisee = _connexion_vers(port, rejouable)
        try:
            conn.request(methode, chemin, body=corps, headers=entetes)
            _accuser_reception_sans_attendre(conn)
            r = conn.getresponse()
            data = r.read()
        except (http.client.RemoteDisconnected, ConnectionResetError,
                BrokenPipeError, ConnectionAbortedError):
            conn.close()
            if reutilisee and essai == 1:
                continue
            raise
        except BaseException:
            conn.close()
            raise
        if r.will_close:
            conn.close()
        else:
            _rendre_connexion(port, conn)
        return r.status, r.getheaders(), data


def _redirection_interne(port, chemin, location):
    """Le chemin d'une redirection qui vise l'application elle-meme, sinon None."""
    base = "http://127.0.0.1:" + str(port)
    cible = urllib.parse.urlsplit(urllib.parse.urljoin(base + chemin,
                                                        location.replace(" ", "%20")))
    try:
        port_cible = cible.port or 80
    except ValueError:
        return None
    if (cible.scheme != "http" or port_cible != port
            or cible.hostname not in ("127.0.0.1", "localhost")):
        return None
    return (cible.path or "/") + ("?" + cible.query if cible.query else "")


def demander_a_l_application(port, methode, chemin, entetes, corps):
    """Envoie la requete a l'application et rend (statut, en-tetes, corps).
    Une application injoignable leve OSError (ou HTTPException)."""
    entetes = dict(entetes)
    if corps is not None and not any(k.lower() == "content-type" for k in entetes):
        entetes["Content-Type"] = "application/x-www-form-urlencoded"
    for _ in range(REDIRECTIONS_SUIVIES + 1):
        statut, recus, data = _envoyer(port, methode, chemin, entetes, corps)
        # Les regles d'urllib (HTTPRedirectHandler.redirect_request) : un
        # POST redirige par 301/302/303 devient un GET sans corps, 307 et 308
        # ne se suivent qu'en GET ou HEAD.
        suivie = (statut in (301, 302, 303, 307, 308) and methode in ("GET", "HEAD")
                  or statut in (301, 302, 303) and methode == "POST")
        location = next((v for k, v in recus if k.lower() == "location"), None)
        interne = location and suivie and _redirection_interne(port, chemin, location)
        if not interne:
            return statut, recus, data
        chemin, corps = interne, None
        methode = "HEAD" if methode == "HEAD" else "GET"
        entetes = {k: v for k, v in entetes.items()
                   if k.lower() not in ("content-length", "content-type")}
    return statut, recus, data


# --------------------- le laissez-passer de tournage ---------------------
#
# Vecu : « je ne veux pas avoir a me connecter » pour filmer la video de
# presentation d'une application privee (« codelab video »). Le tournage
# passe par le hub, comme un visiteur ; l'application reconnait alors
# l'administrateur par « Se connecter avec CodeLab », sans formulaire.
#
# LE CHEMIN, EN TROIS TEMPS :
#   1. « codelab video » presente la cle APP_MANAGER_TOURNAGE_CLE (que le
#      poste de developpement recopie pour sa session, ~/.codelab-tournage)
#      a POST /api/tournage/<app>, sur le PANNEAU, depuis le reseau local ;
#   2. il recoit un ticket a usage unique, valable une minute, pour cette
#      application et elle seule -- et le panneau la demarre si elle est
#      arretee ;
#   3. le navigateur du tournage ouvre /<app>/?codelab-tournage=<ticket> :
#      le proxy l'echange contre le cookie « codelab-tournage », dix minutes,
#      et l'identite de l'administrateur est injectee comme pour sa session.
#
# POURQUOI : une demonstration a besoin des vraies pages, donc d'une
# identite ; demander un mot de passe a un script, c'est l'ecrire quelque
# part (vecu : un mot de passe administrateur reinitialise pour filmer).
# REGLE : 12 ter de SECURITE.md. La regle 19 s'etend au cookie et a la cle :
#   ni l'un ni l'autre n'arrive jamais a une application.
# CE QUE LA CLE NE DONNE PAS : aucune session du panneau (le cookie n'est lu
#   que par _proxy, jamais par is_authed), aucune application par defaut
#   (diagnostic, Dagster), aucune application archivee, et rien depuis
#   Internet (client_est_local). Le cookie ne vaut que pour l'application du
#   ticket.
# POURQUOI CE N'EST PAS UN ELARGISSEMENT : la session du poste a deja le
#   compte de chaque base de projet (~/.codelab-bases) et le code de toutes
#   les applications : elle peut deja lire et ecrire leurs donnees. Le
#   laissez-passer lui donne une page, pas un pouvoir de plus.
# DEPEND DE : jeton_sso (identite signee, avec « tournage »: true),
#   strip_session_cookie (retire aussi ce cookie), dev/entrypoint.sh (la
#   copie de la cle), dev/video/fabriquer.mjs (le client).
# ECARTE : une vraie session admin pour le tournage -- elle ouvrirait le
#   panneau, donc l'execution de commandes, a tout code du poste ; un compte
#   de demonstration -- l'application demarre vide, sans ses pages reelles.
TOURNAGE_ENTETE = "X-CodeLab-Tournage"
TOURNAGE_PARAMETRE = "codelab-tournage"
TOURNAGE_COOKIE = "codelab-tournage"
TOURNAGE_TICKET_TTL = 60
TOURNAGE_DUREE = 600
_tournage_tickets = {}      # ticket -> (application, expiration)
_tournage_sessions = {}     # cookie -> (application, expiration)


def _oublier_les_tournages_finis():
    maintenant = time.time()
    for table in (_tournage_tickets, _tournage_sessions):
        for cle, (_n, fin) in list(table.items()):
            if fin <= maintenant:
                table.pop(cle, None)


def application_filmable(nom):
    """Une application qu'un laissez-passer peut ouvrir : declaree, ni
    archivee, ni par defaut (reservees a l'administrateur, regle 12)."""
    a = load().get(nom)
    return bool(a) and not est_archivee(a) and not est_app_par_defaut(nom)


@flask_app.route("/api/tournage/<n>", methods=["POST"])
def api_tournage(n):
    """Un ticket de tournage pour une application (voir plus haut)."""
    # Meme reponse pour tout refus d'acces : rien ne dit ce qui manquait.
    refus = (jsonify({"error": "Laissez-passer refusé."}), 403)
    if not client_est_local():
        return refus
    if rate_limited():
        return jsonify({"error": "Trop d'essais. Réessaie dans quelques minutes."}), 429
    fourni = request.headers.get(TOURNAGE_ENTETE, "")
    if not _cle_tournage or not secrets.compare_digest(fourni.encode(), _cle_tournage.encode()):
        register_failed_attempt()
        return refus
    if not application_filmable(n):
        return jsonify({"error": "Application introuvable."}), 404
    # ARRETEE, ELLE DEMARRE. Vecu : « Rien sur le port en question. je ne
    # veux pas avoir a ouvrir le navigateur ». Le tournage filme une
    # application qui tourne : le laissez-passer la demarre, comme le bouton
    # du panneau (start, avec ses controles), et la laisse tourner ensuite.
    # Une application deja declaree, jamais une commande nouvelle : la cle ne
    # lance rien que l'administrateur n'ait enregistre.
    demarree = False
    if not is_running(n):
        erreur = start(n)
        if erreur:
            return jsonify({"error": erreur}), 409
        demarree = True
    _oublier_les_tournages_finis()
    ticket = secrets.token_urlsafe(32)
    _tournage_tickets[ticket] = (n, time.time() + TOURNAGE_TICKET_TTL)
    journaliser("tournage", app=n, ip=_adresse_client())
    return jsonify({"ticket": ticket, "duree": TOURNAGE_DUREE, "demarree": demarree})


def echanger_le_ticket(name, sub):
    """/<app>/?codelab-tournage=<ticket> : le ticket, une seule fois, contre
    le cookie de tournage ; puis la meme page, sans le ticket dans l'adresse."""
    ticket = request.args.get(TOURNAGE_PARAMETRE, "")
    entree = _tournage_tickets.pop(ticket, None)
    if not entree or entree[0] != name or entree[1] <= time.time() or not application_filmable(name):
        return Response(_page("Laissez-passer refusé",
                              "Ce laissez-passer de tournage est expiré ou déjà utilisé.",
                              "Relance « codelab video »."), 403, mimetype="text/html")
    jeton = secrets.token_urlsafe(32)
    _tournage_sessions[jeton] = (name, time.time() + TOURNAGE_DUREE)
    reste = [(k, v) for k, v in request.args.items(multi=True) if k != TOURNAGE_PARAMETRE]
    cible = request.path + ("?" + urllib.parse.urlencode(reste) if reste else "")
    r = redirect(cible, 302)
    r.set_cookie(TOURNAGE_COOKIE, jeton, max_age=TOURNAGE_DUREE, path="/",
                 httponly=True, samesite="Lax", secure=request.is_secure)
    return r


def tournage_en_cours(name):
    """Le cookie de tournage de cette requete vaut-il pour cette application ?"""
    entree = _tournage_sessions.get(request.cookies.get(TOURNAGE_COOKIE) or "")
    return bool(entree) and entree[0] == name and entree[1] > time.time() \
        and application_filmable(name)


def _proxy(name, sub):
    a = load().get(name)
    # Archivee : la meme reponse qu'une adresse inconnue. Le nom reste
    # reserve, mais rien ne dit qu'il existe.
    if not a or est_archivee(a):
        return Response(_page("Introuvable", "Aucune application \u00ab " + name + " \u00bb."),
                        404, mimetype="text/html")
    # Application privee : il faut une session, ET le droit sur ce projet
    # precis. Le controle est ici, dans le proxy, et pas dans l'interface --
    # une application dont le lien circule doit rester fermee quel que soit le
    # chemin emprunte, y compris par un compte utilisateur qui connaitrait
    # l'adresse d'un projet qu'on ne lui a pas autorise.
    # Une application par defaut suit la MEME frontiere que l'administration.
    # Le diagnostic dit ce qui tourne, ce qui est expose et quels comptes
    # n'ont pas de second facteur : c'est une page d'administration par ce
    # qu'elle montre, elle doit donc s'arreter ou l'administration s'arrete.
    # Verifie a chaque requete, pas seulement a l'ouverture : une session
    # commencee dans le salon puis reprise depuis l'exterieur n'emporte pas
    # le droit avec elle.
    if est_app_par_defaut(name):
        hors = refus_admin_hors_reseau()
        if hors:
            journaliser("refus", app=name, motif="hors reseau local",
                        ip=_adresse_client())
            return Response(_page("Accès refusé", hors), 403,
                            mimetype="text/html")
    # Le laissez-passer de tournage (voir plus haut) : l'echange d'abord,
    # puis le cookie, qui ouvre l'application privee de son ticket, et elle
    # seule. Une session ouverte garde la priorite (et son identite).
    if request.method == "GET" and TOURNAGE_PARAMETRE in request.args:
        return echanger_le_ticket(name, sub)
    tournage = not is_authed() and tournage_en_cours(name)
    if visibilite(a) == VISIBILITE_PRIVEE and not tournage:
        if not is_authed():
            # La connexion vit sur l'origine du PANNEAU : un "/login" relatif
            # tombait, sur le port des applications, sur « Ce n'est pas le
            # panneau ». C'est le premier ecran d'une app privee ajoutee a
            # l'ecran d'accueil, qui n'a pas encore de session a elle.
            # Avec la suite : apres la connexion, le visiteur revient dans
            # l'application, a la page qu'il demandait (/ouvrir.app/...).
            return redirect(lien_panneau() + "login?suite=" + urllib.parse.quote(
                suite_vers_application(name, sub), safe="/"))
        if not peut_voir(name):
            return Response(_page("Accès refusé",
                                  "Ton compte n'a pas accès à \u00ab " + name + " \u00bb.",
                                  "Demande l'accès à l'administrateur."),
                            403, mimetype="text/html")
    # Le manifeste et l'icone d'ecran d'accueil, APRES les controles d'acces
    # (le nom d'une app privee ne regarde que ceux qui peuvent l'ouvrir) mais
    # AVANT le journal : iOS les demande de lui-meme, ce ne sont pas des
    # visites. Repondus meme application arretee.
    if request.method == "GET" and sub == PREFIXE_CODELAB + "manifest.webmanifest":
        return webapp_manifeste(name, titre_de(name, a))
    if request.method == "GET" and sub == PREFIXE_CODELAB + "icone.png":
        return webapp_icone(a)
    # Note l'ouverture APRES les controles d'acces : un refus n'est pas une
    # visite, et le journal servirait mal s'il melangeait les deux.
    # Un tournage n'est pas une visite : il ne compte ni dans les ouvertures
    # ni dans « derniere ouverte ».
    if not tournage:
        journaliser_ouverture(name)
    if not is_running(name):
        return page_application_fermee(name, a)
    chemin = "/" + urllib.parse.quote(sub, safe="/")
    if request.query_string:
        chemin += "?" + request.query_string.decode()
    body = request.get_data() if request.method in ("POST", "PUT", "PATCH") else None
    entetes = {}
    # Les en-tetes SSO sont controles par le proxy. Ceux fournis par le
    # navigateur sont TOUJOURS supprimes avant injection : une application
    # publique ne doit pas pouvoir s'attribuer une identite, et une application
    # privee ne doit recevoir que l'identite de la session qui a franchi les
    # controles de ce proxy.
    # TOUT en-tete X-CodeLab-* : la liste des en-tetes poses par le proxy
    # grandit, et un en-tete oublie dans une liste serait un en-tete que le
    # navigateur pourrait fabriquer.
    for k, v in request.headers:
        if k.lower() in HOP or k.lower() == "host" or k.lower().startswith("x-codelab-"):
            continue
        if k.lower() == "cookie":
            # Les applications sont servies sur la MEME origine que le
            # panneau (:9001/<app>/) : le navigateur leur envoie donc le
            # cookie de session admin, et le proxy le transmettait tel quel.
            # Une application deployee pouvait ainsi lire cette session et
            # piloter le panneau -- c'est-a-dire faire executer n'importe
            # quelle commande. On retire ce seul cookie et on laisse passer
            # les autres, qui appartiennent a l'application.
            #
            # Cela ne supprime pas la meme origine elle-meme : une XSS dans
            # une application reste une XSS dans l'origine du panneau. Y
            # remedier demanderait un port ou un sous-domaine par
            # application, ce que ce proxy a justement pour but d'eviter.
            v = strip_session_cookie(v)
            if not v:
                continue
        entetes[k] = v

    # Assertion signee courte : l'application ne connait ni le cookie Flask
    # ni le secret de session. Pour un visiteur anonyme, aucune identite n'est
    # injectee, meme si l'application est publique.
    # L'adresse du panneau, vue du navigateur : les liens « Se connecter avec
    # CodeLab » et « Se deconnecter » de l'application en ont besoin, et
    # l'application ne peut pas la deviner (autre port, autre nom public).
    entetes["X-CodeLab-Panneau"] = lien_panneau()
    token = jeton_sso(name, tournage=tournage)
    if token and tournage:
        entetes["X-CodeLab-Auth"] = token
        entetes["X-CodeLab-User"] = NOM_ADMIN
        entetes["X-CodeLab-Role"] = "admin"
        entetes["X-CodeLab-Authenticated"] = "true"
    elif token:
        entetes["X-CodeLab-Auth"] = token
        entetes["X-CodeLab-User"] = utilisateur_courant()
        entetes["X-CodeLab-Role"] = role_courant() or ""
        entetes["X-CodeLab-Authenticated"] = "true"
        compte = None if est_admin() else lire_utilisateurs().get(utilisateur_courant())
        email = (compte or {}).get("email", "") if compte else ""
        if email:
            entetes["X-CodeLab-Email"] = email

    try:
        status, headers, data = demander_a_l_application(a["port"], request.method, chemin,
                                                         entetes, body)
    except Exception:
        return page_application_fermee(name, a, muette=True)
    # UNE PAGE QUI PLANTE, VUE PAR UN UTILISATEUR : la page neutre plutot que
    # la trace de l'application. Seulement pour une page qu'on OUVRE (une
    # navigation) : un appel d'API de l'application garde sa reponse, que
    # son propre code sait lire. L'administrateur voit la vraie reponse.
    if (status >= 500 and request.method == "GET" and not est_admin()
            and request.headers.get("Sec-Fetch-Mode") == "navigate"):
        return page_inaccessible(name, a)
    sortants = [(k, v) for k, v in headers if k.lower() not in HOP]
    # Le ruban n'est pose que pour quelqu'un de CONNECTE. Une application
    # publique vue par un visiteur anonyme ne doit pas lui annoncer qu'un
    # panneau existe derriere, ni lui offrir un lien qui le renverrait a une
    # page de connexion dont il n'a que faire.
    if is_authed():
        data, sortants = injecter_ruban(data, status, sortants)
        # Meme condition que le ruban : /api/icon exige une session, et un
        # visiteur anonyme n'a pas a voir d'adresse du panneau.
        data, sortants = injecter_icone(data, status, sortants, name)
    # Pour tout le monde, connecte ou non : une application publique doit
    # pouvoir s'installer aussi, et rien de ce qui est pose ici ne pointe
    # vers le panneau. Apres injecter_icone, qui renonce des qu'il voit une
    # apple-touch-icon -- et celle-ci en ajoute une.
    data, sortants = injecter_webapp(data, status, sortants, name,
                                     titre_de(name, a))
    data, sortants = injecter_environnement(data, status, sortants, is_authed())
    return Response(data, status, sortants)


def _from_referer():
    m = re.search(r"://[^/]+/([^/]+)/", request.headers.get("Referer", ""))
    if m and m.group(1) in load() and not request.path.startswith("/" + m.group(1) + "/"):
        return redirect("/" + m.group(1) + request.path, 302)
    return None


def _miss(name):
    return _from_referer() or Response(
        _page("Introuvable", "Aucune application \u00ab " + name + " \u00bb."),
        404, mimetype="text/html")


@flask_app.route("/<n>/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@flask_app.route("/<n>/<path:sub>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy(n, sub=""):
    return _proxy(n, sub) if n in load() else _miss(n)


@flask_app.route("/<n>")
def proxy_noslash(n):
    return redirect("/" + n + "/", 302) if n in load() else _miss(n)


@flask_app.errorhandler(404)
def not_found(e):
    return _miss(request.path.strip("/"))


def _page(title, msg, extra=""):
    """Une page d'erreur simple, servie par les DEUX origines.

    Elle echappe ses trois textes, et c'est la seule facon tenable : elle est
    appelee depuis le proxy avec un nom d'application qui vient de l'URL.
    « /zz<script>alert(1)</script>/ » ressortait tel quel dans la page --
    une XSS reflechie sur l'origine des applications. Elle n'y portait pas la
    session du panneau (c'est tout l'interet de la separation), mais elle
    permettait de se faire passer pour n'importe quelle application hebergee,
    dans son origine, devant l'utilisateur qui vient de cliquer.

    Echapper ICI plutot qu'a chaque appel : aucun appelant ne passe de balise
    volontairement, et la regle qui tient est celle qu'on ne peut pas oublier
    d'appliquer.
    """
    title, msg, extra = (str(x).replace("&", "&amp;").replace("<", "&lt;")
                         .replace(">", "&gt;").replace('"', "&quot;")
                         for x in (title, msg, extra))
    return ("<!doctype html><meta charset=utf-8><title>" + title + "</title>"
            "<body style=\"margin:0;background:#0d1117;color:#c9d1d9;display:flex;"
            "align-items:center;justify-content:center;height:100vh;font:15px/1.6 "
            "-apple-system,Segoe UI,Roboto,sans-serif\"><div style=\"text-align:center\">"
            "<div style=\"font-size:17px;font-weight:600;color:#e6edf3;margin-bottom:6px\">"
            + title + "</div><div style=\"color:#8b949e\">" + msg + "</div>"
            "<div style=\"color:#6e7681;font-size:13px;margin-top:6px\">" + extra + "</div>"
            # Meme raison que pour le ruban : ces pages sortent aussi du
            # port des applications, ou "/" ne mene pas au hub.
            "<div style=\"margin-top:22px\"><a href=\"" + lien_panneau() + "\" style=\"color:#4c8eff;"
            "text-decoration:none;font-size:14px\">Retour au panneau</a></div></div>")


# ------------------- une application qu'on ne peut pas ouvrir -------------------
#
# DEUX PAGES, SELON QUI REGARDE. L'administrateur a besoin de la cause --
# arretee, plantee, muette sur son port -- et du chemin vers la fiche ou il
# la reparera. Un compte utilisateur, lui, ne doit JAMAIS savoir ce qui
# marche ou non : il apprend seulement que l'application est momentanement
# inaccessible, et qu'il peut revenir au hub. Pas un mot technique, pas de
# code, pas de port. Un visiteur anonyme d'une application publique est
# traite comme un utilisateur, sans lien vers le hub qu'il n'a pas.

def _echapper(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _page_application(titre, texte, initiale, boutons, pied=""):
    """La mise en page commune : une carte au centre, dans les couleurs du
    panneau, claires ou sombres selon le systeme. Aucun script : la page
    sort sur l'origine des applications, ou rien ne doit s'executer."""
    return ("<!doctype html><html lang=fr><meta charset=utf-8>"
            "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
            "<title>" + titre + "</title><style>"
            ":root{--bg:#E9EBEE;--carte:#fff;--txt:#12161C;--dim:#525C67;--ligne:#DADEE3;"
            "--accent:#0E7C86;--accent-h:#0B646C;--sur:#fff}"
            "@media(prefers-color-scheme:dark){:root{--bg:#0D1015;--carte:#161B22;--txt:#EEF1F4;"
            "--dim:#A0AAB5;--ligne:#262E38;--accent:#5FD4DC;--accent-h:#8FE3E9;--sur:#04161A}}"
            "body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);"
            "color:var(--txt);font:15px/1.55 Manrope,-apple-system,BlinkMacSystemFont,'Segoe UI',"
            "Roboto,sans-serif;padding:24px;box-sizing:border-box}"
            "main{width:100%;max-width:520px;background:var(--carte);border-radius:28px;"
            "padding:40px 36px 30px;box-shadow:0 20px 50px rgba(18,22,28,.10);text-align:center}"
            ".ini{width:76px;height:76px;margin:0 auto 18px;border-radius:24px;background:var(--accent);"
            "color:var(--sur);display:grid;place-items:center;font-size:32px;font-weight:700}"
            "h1{margin:0;font-size:24px;line-height:1.2;letter-spacing:-.03em}"
            "p{margin:12px 0 0;color:var(--dim)}"
            ".actions{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:24px}"
            ".b{display:inline-flex;align-items:center;min-height:46px;padding:0 20px;border-radius:14px;"
            "border:1px solid var(--ligne);background:var(--carte);color:var(--txt);font-weight:700;"
            "text-decoration:none}.b:hover{border-color:var(--accent);color:var(--accent)}"
            ".p{background:var(--accent);border-color:var(--accent);color:var(--sur)}"
            ".p:hover{background:var(--accent-h);color:var(--sur)}"
            ".pied{margin-top:22px;padding-top:16px;border-top:1px solid var(--ligne);font-size:13px}"
            ".pied a{color:var(--accent);font-weight:700;text-decoration:none}"
            "</style><main><div class=ini aria-hidden=true>" + initiale + "</div>"
            "<h1>" + titre + "</h1><p>" + texte + "</p>"
            "<div class=actions>" + boutons + "</div>"
            + (("<div class=pied>" + pied + "</div>") if pied else "") + "</main></html>")


# Le dernier signalement, par application : un utilisateur qui recharge la
# page dix fois ne doit pas envoyer dix mails. Une heure entre deux.
_inaccessibles_signales = {}
INACCESSIBLE_RAPPEL = 3600


def prevenir_admin_inaccessible(name, titre):
    """Note qu'un compte n'a pas pu ouvrir l'application, et previent
    l'administrateur (au plus une fois par heure et par application).

    Renvoie True quand l'administrateur EST prevenu -- par ce signalement ou
    par l'alerte de chute deja partie -- et seulement alors : la page ne
    doit pas promettre un mail qui ne partira pas faute de destinataire.
    """
    qui = utilisateur_courant() if is_authed() else "visiteur"
    journaliser("inaccessible", app=name, qui=qui, ip=_adresse_client())
    try:
        joignable = bool(lire_alertes()["actif"] and alertes_admin())
    except Exception:
        joignable = False
    if not joignable:
        return False
    if name in _alertes_en_cours:
        return True
    maintenant = time.time()
    if maintenant - _inaccessibles_signales.get(name, 0) < INACCESSIBLE_RAPPEL:
        return True
    _inaccessibles_signales[name] = maintenant
    corps = "\n".join([
        f"Quelqu'un a voulu ouvrir « {titre} » et ne l'a pas pu.",
        "",
        f"Compte    : {qui}",
        f"Panneau   : {lien_panneau()}applications/{name}",
        "",
        "Il a vu une page « momentanément inaccessible », sans détail.",
        "",
        "-- CodeLab, panneau de gestion des applications",
    ])
    # En arriere-plan : un serveur d'envoi lent ne doit pas faire attendre
    # la page.
    threading.Thread(target=alerter, args=(f"[CodeLab] {titre} : ouverture impossible", corps),
                     daemon=True).start()
    return True


def page_inaccessible(name, a, code=503):
    """Ce que voit un compte utilisateur (ou un visiteur) quand
    l'application ne peut pas s'ouvrir. Aucune cause, aucun etat."""
    titre = titre_de(name, a)
    # Un APERCU (le widget du hub l'encadre) n'est pas une ouverture : sans
    # ce tri, chaque affichage du hub d'un compte enverrait un mail.
    encadre = request.headers.get("Sec-Fetch-Dest") in ("iframe", "frame")
    prevenu = False if encadre else prevenir_admin_inaccessible(name, titre)
    t = _echapper(titre)
    boutons = '<a class="b" href="">Réessayer</a>'
    pied = ""
    if is_authed():
        boutons = ('<a class="b p" href="' + _echapper(lien_panneau()) + '">Retour au hub</a>'
                   + boutons)
        pied = (("L'administrateur est prévenu. " if prevenu else "")
                + '<a href="' + _echapper(lien_panneau()) + 'messages">Lui laisser un message</a>')
    html = _page_application(t + " est momentanément inaccessible",
                             "Elle revient très vite. Réessaie dans quelques minutes.",
                             _echapper(titre[:1].upper() or "?"), boutons, pied)
    r = Response(html, code, mimetype="text/html")
    r.headers["Retry-After"] = "60"
    r.headers["Cache-Control"] = "no-store"
    return r


def page_panne_admin(name, a, cause, detail="", code=503):
    """Ce que voit l'administrateur : la cause, et le chemin vers la fiche."""
    titre = _echapper(titre_de(name, a))
    fiche = _echapper(lien_panneau() + "applications/" + name)
    boutons = ('<a class="b p" href="' + fiche + '">Ouvrir sa fiche</a>'
               '<a class="b" href="">Réessayer</a>')
    pied = "Un compte utilisateur voit seulement « momentanément inaccessible »."
    html = _page_application(titre + " " + _echapper(cause), _echapper(detail),
                             _echapper(titre_de(name, a)[:1].upper() or "?"), boutons, pied)
    r = Response(html, code, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r


def page_application_fermee(name, a, muette=False):
    """L'application ne repond pas : la page qui convient a qui regarde.

    502 quand le processus tourne mais que son port ne repond pas (la
    passerelle n'a rien obtenu), 503 quand il n'y a pas de processus."""
    code = 502 if muette else 503
    if not est_admin():
        return page_inaccessible(name, a, code)
    if muette:
        return page_panne_admin(name, a, "ne répond pas encore",
                                "Elle est lancée mais n'écoute pas encore sur son port. "
                                "Réessaie dans quelques secondes.", code)
    if is_crash_looping(name) or a.get("dernier_echec"):
        return page_panne_admin(name, a, "s'est arrêtée",
                                "Elle a planté au lancement. La cause est dans sa fiche, "
                                "avec la fin du journal.")
    return page_panne_admin(name, a, "est arrêtée",
                            "Démarre-la depuis sa fiche.")


# --------------------- inscription du projet de diagnostic ---------------------
#
# Le conteneur dagster depose le projet "diagnostic" dans /workspace au premier
# demarrage de la stack ; il fallait ensuite l'ajouter a la main dans le
# panneau, en recopiant deux commandes depuis son README. C'est la seule etape
# manuelle qui separait une stack fraiche d'une stack verifiee -- et c'est
# precisement celle qu'on saute quand on est presse, donc celle qui manque le
# jour ou quelque chose ne marche pas.
#
# Le projet est donc inscrit tout seul, et il l'est a CHAQUE demarrage --
# voir amorcer_diagnostic(), qui dit pourquoi ce n'est plus une amorce jouee
# une seule fois. Une seule condition subsiste : le dossier doit exister.
# app-manager et dagster demarrent en parallele, c'est dagster qui amorce
# /workspace, et on ne peut pas inscrire un chemin qui n'est pas la.

# Les deux commandes du README du projet, pas une detection automatique :
# detect_project() proposerait bien "python3 app.py", mais rendrait une
# commande de build vide (il cherche un requirements.txt, que ce projet n'a
# pas), et l'application demarrerait sans pilote Postgres.
DIAGNOSTIC_COMMANDE = "python3 app.py"
DIAGNOSTIC_BUILD = 'pip install --target vendor "psycopg[binary]"'
def amorcer_diagnostic():
    """Inscrit le projet de diagnostic et le configure. A CHAQUE demarrage.

    CE QUI A CHANGE. L'inscription etait une amorce a jouer UNE FOIS : elle
    ne se declenchait que sur un registre vide, et un marqueur sur le disque
    l'empechait de se rejouer. Trois facons de se retrouver sans diagnostic
    en decoulaient, et aucune ne se voyait :

      - le dossier n'etait pas encore la (dagster amorce /workspace, et les
        deux conteneurs demarrent en parallele) et une autre application
        avait ete inscrite entre-temps : plus jamais de diagnostic ;
      - le marqueur avait ete pose sans que l'inscription reussisse ;
      - une installation d'avant l'existence du diagnostic gardait un
        registre non vide, donc ne l'a jamais recu.

    Ce n'est plus une amorce, c'est un invariant : le diagnostic est une
    application PAR DEFAUT, au meme titre que Dagster. Elle ne se supprime
    pas (est_app_par_defaut la protege), ne se configure pas, ne s'arrete
    pas -- le marqueur qui evitait de la reinscrire n'a donc plus rien a
    eviter.

    Renvoie le nom si l'inscription vient d'avoir lieu (il reste a builder
    et a lancer), None si elle etait deja la ou si le dossier manque encore.
    """
    chemin = os.path.join(ROOT, DIAGNOSTIC_NOM)
    if not os.path.isfile(os.path.join(chemin, "app.py")):
        # Pas encore amorce par dagster : on retentera au prochain demarrage.
        # C'est maintenant vrai a chaque fois, et non plus seulement tant que
        # le registre reste vide.
        return None

    apps = load()
    a = apps.get(DIAGNOSTIC_NOM)
    if a is not None:
        # Deja inscrite : on ne recree rien, on remet seulement ce qui doit
        # etre vrai. Le chemin et la commande ne sont pas configurables pour
        # cette application-la ; les laisser deriver n'apporterait rien.
        change = False
        for cle, valeur in (("path", chemin),
                            ("command", DIAGNOSTIC_COMMANDE),
                            ("build_command", DIAGNOSTIC_BUILD)):
            if a.get(cle) != valeur:
                a[cle] = valeur
                change = True
        if not a.get("port"):
            a["port"] = next_port(apps)
            change = True
        if change:
            save(apps)
        return None

    apps[DIAGNOSTIC_NOM] = {
        "titre": TITRES_PAR_DEFAUT[DIAGNOSTIC_NOM],
        "path": chemin,
        "command": DIAGNOSTIC_COMMANDE,
        "port": next_port(apps),
        "enabled": True,
        # La commande de build EST la configuration par defaut : sans elle,
        # la fiche n'ayant pas de formulaire, le pilote Postgres ne
        # s'installait sur ordre de personne.
        "build_command": DIAGNOSTIC_BUILD,
        "max_memory_mb": None,
        "description": DIAGNOSTIC_DESCRIPTION,
        # Privee : la page nomme les conteneurs, l'utilisateur SSH et l'etat de
        # la base. Rien de secret, mais rien non plus a offrir a un visiteur
        # anonyme le jour ou le port 9001 est publie. L'utilisateur peut la
        # rendre publique en un clic depuis le panneau.
        "visibility": VISIBILITE_PRIVEE,
    }
    save(apps)
    return DIAGNOSTIC_NOM


def _preparer_diagnostic(name):
    """Build puis demarrage, en tache de fond.

    Le build installe le pilote Postgres (quelques secondes a quelques
    dizaines, et un acces reseau) : le faire dans le thread principal
    retarderait d'autant l'ouverture du panneau, c'est-a-dire la seule
    interface depuis laquelle on peut constater ce qui se passe.

    Un build en echec (pas de reseau, miroir pip injoignable) n'empeche pas le
    demarrage : l'application affiche alors "aucun pilote Postgres" sur la
    ligne concernee et toutes les autres sondes repondent normalement. Une
    page qui explique ce qui manque vaut mieux qu'une application absente.

    LE BUILD MANQUAIT. Ce texte decrivait « build puis demarrage » depuis le
    debut, et la fonction ne faisait que demarrer : la commande de build
    n'etait meme pas inscrite. Le pilote Postgres ne s'installait donc sur
    ordre de personne, et la page de diagnostic annoncait son absence sur une
    installation neuve, sans que rien ne dise quoi faire.
    """
    try:
        ok, message = run_build(name)
        if not ok:
            print(f"[app-manager] {name} : build initial en echec ({message}). "
                  f"L'application demarre quand meme.", flush=True)
    except Exception as e:
        print(f"[app-manager] {name} : build initial impossible ({e}).", flush=True)
    try:
        start(name)
    except Exception as e:
        print(f"[app-manager] {name} : echec du demarrage initial ({e}).", flush=True)
