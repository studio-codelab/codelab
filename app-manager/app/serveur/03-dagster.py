# CodeLab -- LE PANNEAU, DAGSTER
# ---------------------------------------------------------------------------
# ROLE        Dagster dans le hub, et ce que le panneau en sait.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   Dagster dans le hub, ce que le panneau sait de Dagster.
#

# ------------------------- Dagster, dans le hub -------------------------
#
# Dagster n'est pas une application du panneau : personne ne le declare,
# personne ne le demarre d'ici, il n'a ni dossier ni commande. C'est un
# SERVICE de la stack, servi sur son propre port derriere le proxy qui exige
# la session du panneau.
#
# Il manquait pourtant la ou on le cherche. Le hub est la page d'accueil de
# CodeLab -- « la liste des projets ouvrables » -- et Dagster s'ouvrait en
# retapant une adresse et un numero de port de memoire. Une tuile l'y remet,
# avec les autres, et elle mene au port 3000.
DAGSTER_NOM = "dagster"
DIAGNOSTIC_NOM = "diagnostic"
# « Messages » : l'application qui recense les messages laisses depuis le hub
# et en fait des projets, des taches et une feuille de route. Un projet a
# part entiere, comme le diagnostic (/workspace/messages, sa base, son
# processus) : voir « la messagerie, une application » (06).
MESSAGERIE_NOM = "messages"

# LES APPLICATIONS PAR DEFAUT : celles que CodeLab apporte avec lui.
#
# Elles se comportent comme les autres -- meme tuile dans le hub, meme ligne
# dans la liste, meme fiche -- et c'est voulu : ce sont des applications, pas
# des boutons a part. Ce qui les distingue tient en une phrase : IL N'Y A
# RIEN A Y CONFIGURER.
#
# Rien a configurer, donc rien a casser : on ne renomme pas « diagnostic »,
# on ne change pas sa commande de lancement, on ne le supprime pas par megarde
# -- et il ne se rend pas publique, ce qui offrirait a un visiteur anonyme
# l'etat des lieux complet de l'installation.
#
# Deux consequences, tenues cote SERVEUR et pas seulement dans la page :
#
#   1. reservees a l'administrateur. Pas « privees, donc autorisables a un
#      compte » : reservees. Dagster permet de lancer des jobs, donc
#      d'executer du code ici ; le diagnostic nomme les conteneurs, les
#      comptes sans second facteur et ce qui est expose.
#   2. soumises a la meme regle de reseau local que l'administration. Si
#      l'administration est limitee au reseau local, elles le sont aussi --
#      meme session, meme frontiere.
APPS_PAR_DEFAUT = (DAGSTER_NOM, DIAGNOSTIC_NOM, MESSAGERIE_NOM)
# Mailpit : un service de la stack, comme Dagster, mais qui n'a jamais ete
# declare dans apps.json. Il en a les memes regles -- reserve a
# l'administrateur (son interface montre TOUS les mails envoyes, liens de
# reinitialisation compris), rien a configurer, rien a demarrer ni arreter.
MAILPIT_NOM = "mailpit"
APPLICATIONS_PROTEGEES = {DAGSTER_NOM, DIAGNOSTIC_NOM, MAILPIT_NOM, MESSAGERIE_NOM}

# Le nom AFFICHE des deux applications par defaut. Leur cle est le chemin --
# « dagster », « diagnostic » -- et c'est elle qui s'affichait : deux tuiles
# en minuscules au milieu de projets nommes par leur auteur. Elles n'ont pas
# de formulaire ou corriger cela, la valeur est donc posee ici.
TITRES_PAR_DEFAUT = {DAGSTER_NOM: "Dagster", DIAGNOSTIC_NOM: "Diagnostic",
                     MAILPIT_NOM: "Mailpit", MESSAGERIE_NOM: "Messages"}

# La tuile du diagnostic a besoin d'une phrase, et cette phrase ne peut pas
# s'ecrire a la main : la fiche d'une application par defaut n'a pas de
# formulaire. Elle est donc posee ici, et le panneau la met en place au
# demarrage si elle manque (voir tenir_les_applications_par_defaut).
# Les descriptions des applications par defaut tiennent ENTIERES sur leur
# tuile du hub (DESCRIPTION_VISIBLE dans dashboard.html, environ 45
# caracteres sur deux lignes) : 40 au plus, verifie par les tests.
DIAGNOSTIC_DESCRIPTION = "L'état de santé de CodeLab"
MESSAGERIE_DESCRIPTION = "Les messages du hub, rangés en projets."


def refus_action_protegee(nom, action):
    if nom in APPLICATIONS_PROTEGEES:
        return f"« {nom} » est une application de supervision : l'action « {action} » est interdite."
    return None


def est_app_par_defaut(nom):
    return nom in APPS_PAR_DEFAUT or nom == MAILPIT_NOM


def refus_configuration(nom):
    """Le message de refus quand on tente de configurer une application par
    defaut, ou None si ce n'en est pas une.

    Le controle est ICI, pas seulement dans la page : masquer un bouton ne
    protege rien, la route reste appelable a la main.
    """
    if not est_app_par_defaut(nom):
        return None
    return (f"« {nom} » est une application par défaut de CodeLab : "
            f"elle n'est pas configurable.")


DAGSTER_PORT = int(os.environ.get("APP_MANAGER_DAGSTER_PORT", "3000") or 3000)
# Vu depuis ce conteneur, pour savoir s'il repond. C'est le PROXY qu'on
# interroge et non Dagster : c'est lui qui publie le port, et un Dagster
# debout derriere un proxy tombe ne s'ouvre pas davantage.
DAGSTER_HOTE_INTERNE = os.environ.get("APP_MANAGER_DAGSTER_HOTE") or "codelab-dagster-proxy"
DAGSTER_AMONT = os.environ.get("APP_MANAGER_DAGSTER_AMONT") or "codelab-dagster"


def origine_dagster():
    """L'adresse de Dagster vue DU NAVIGATEUR, pas du conteneur.

    Le meme hote que le panneau, un autre port : c'est ainsi que le compose
    le publie, et le cookie de session traverse -- sa portee ignore le
    numero de port.
    """
    hote = hote_demande()
    if not hote:
        return ""
    return f"{request.scheme}://{hote}:{DAGSTER_PORT}/"


_sondes_de_port = {}


def _port_repond(hote, port):
    """Ce port est-il ouvert ? Reponse mise en cache 10 s.

    Le hub se rafraichit toutes les dix secondes : sans ce cache, chaque
    passage ouvrirait une connexion de plus, pour une reponse qui ne change
    pas d'une seconde a l'autre.
    """
    cle = (hote, port)
    sonde = _sondes_de_port.setdefault(cle, {"quand": 0.0, "ouvert": False})
    maintenant = time.time()
    if maintenant - sonde["quand"] < 10:
        return sonde["ouvert"]
    ouvert = False
    s = socket.socket()
    s.settimeout(1.0)
    try:
        ouvert = s.connect_ex((hote, port)) == 0
    except OSError:
        ouvert = False
    finally:
        s.close()
    sonde.update(quand=maintenant, ouvert=ouvert)
    return ouvert


def dagster_repond():
    return _port_repond(DAGSTER_HOTE_INTERNE, DAGSTER_PORT)


# Mailpit : meme montage que Dagster. Son interface est publiee par le meme
# proxy authentifiant, sur un autre port.
MAILPIT_PORT = int(os.environ.get("APP_MANAGER_MAILPIT_PORT", "8025") or 8025)
MAILPIT_HOTE_INTERNE = os.environ.get("APP_MANAGER_MAILPIT_HOTE") or "codelab-dagster-proxy"
MAILPIT_AMONT = os.environ.get("APP_MANAGER_MAILPIT_AMONT") or "codelab-mailpit"


def origine_mailpit():
    """L'adresse de Mailpit vue du navigateur : meme hote, port 8025."""
    hote = hote_demande()
    if not hote:
        return ""
    return f"{request.scheme}://{hote}:{MAILPIT_PORT}/"


def mailpit_repond():
    return _port_repond(MAILPIT_HOTE_INTERNE, MAILPIT_PORT)


# ------------------- ce que le panneau sait de Dagster -------------------
#
# Dagster tourne dans un AUTRE conteneur : le panneau n'a pas son pid, donc
# ni son processeur ni sa memoire -- et il n'aura jamais rien de plus, sauf a
# lui donner la socket Docker, ce qui reviendrait a lui donner la machine.
#
# Ce qu'il peut mesurer, en revanche, est exactement ce qui compte pour un
# service qu'on ne fait pas tourner soi-meme : REPOND-IL, ET EN COMBIEN DE
# TEMPS. Les deux courbes de sa fiche portent donc cela, et la fiche le dit
# -- une courbe intitulee « CPU » qui montrerait autre chose serait pire que
# pas de courbe du tout.
_dagster_suivi = {"debout": None}


def dagster_sonde():
    """(debout, millisecondes). Interroge l'AMONT, pas le proxy.

    Mesurer le proxy dirait que nginx va bien, ce qu'on sait deja : ce qu'on
    veut savoir, c'est si Dagster derriere lui repond.
    """
    debut = time.time()
    try:
        with urllib.request.urlopen(f"http://{DAGSTER_AMONT}:{DAGSTER_PORT}/",
                                    timeout=5) as r:
            debout = 200 <= r.status < 500
    except Exception:
        return False, (time.time() - debut) * 1000
    return debout, (time.time() - debut) * 1000


def dagster_tick():
    """Un tour de surveillance, appele par le moniteur.

    Les mesures alimentent la meme reserve que les applications du panneau,
    donc la meme fiche et les memes courbes. Le JOURNAL, lui, ne recoit que
    les CHANGEMENTS d'etat : une ligne toutes les dix secondes pour dire que
    tout va bien n'est pas un journal, c'est un mur.
    """
    debout, ms = dagster_sonde()
    record_metrics(DAGSTER_NOM, round(ms, 1), 100.0 if debout else 0.0)
    if _dagster_suivi["debout"] is debout:
        return
    premier = _dagster_suivi["debout"] is None
    _dagster_suivi["debout"] = debout
    if debout:
        journal_du_service(DAGSTER_NOM,
                           f"Dagster répond ({ms:.0f} ms)."
                           if premier else
                           f"Dagster répond de nouveau ({ms:.0f} ms).")
    else:
        journal_du_service(
            DAGSTER_NOM,
            f"Dagster ne répond pas sur http://{DAGSTER_AMONT}:{DAGSTER_PORT}/ "
            f"-- conteneur arrêté ? « docker compose up -d codelab-dagster ».")


_mailpit_suivi = {"debout": None}


def mailpit_sonde():
    """(debout, millisecondes). /readyz est la seule route de Mailpit ouverte
    sans son mot de passe interne -- et c'est exactement la question."""
    debut = time.time()
    try:
        with urllib.request.urlopen(f"http://{MAILPIT_AMONT}:{MAILPIT_PORT}/readyz",
                                    timeout=5) as r:
            debout = r.status == 200
    except Exception:
        return False, (time.time() - debut) * 1000
    return debout, (time.time() - debut) * 1000


def mailpit_tick():
    """Comme dagster_tick : des mesures a chaque tour, une ligne de journal
    seulement quand l'etat change."""
    debout, ms = mailpit_sonde()
    record_metrics(MAILPIT_NOM, round(ms, 1), 100.0 if debout else 0.0)
    if _mailpit_suivi["debout"] is debout:
        return
    premier = _mailpit_suivi["debout"] is None
    _mailpit_suivi["debout"] = debout
    if debout:
        journal_du_service(MAILPIT_NOM,
                           f"Mailpit répond ({ms:.0f} ms)."
                           if premier else
                           f"Mailpit répond de nouveau ({ms:.0f} ms).")
    else:
        journal_du_service(
            MAILPIT_NOM,
            f"Mailpit ne répond pas sur http://{MAILPIT_AMONT}:{MAILPIT_PORT}/ "
            f"-- les mails des applications ne partent plus. "
            f"« docker compose up -d codelab-mailpit ».")


def journal_du_service(nom, ligne):
    """Ecrit dans le journal d'une application que le panneau ne lance pas.

    Le meme fichier que pour une application lancee par le panneau : la fiche
    et son flux en direct n'ont donc rien a savoir de la difference, et le
    journal d'un service se lit exactement comme les autres.
    """
    rotate_log_if_needed(nom)
    quand = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, nom + ".log"), "a") as f:
            f.write(f"[{quand}] {ligne}\n")
    except OSError:
        pass        # journaliser n'est pas le travail : voir journaliser()


# Les services de la stack qu'un compte peut ranger hors de son hub. Une
# liste nommee plutot qu'un « tout ce qui n'est pas une application » : ce
# qui est masquable se decide ici, et se lit d'un coup d'oeil.
SERVICES_MASQUABLES = frozenset({DAGSTER_NOM, MAILPIT_NOM})

# La phrase de la tuile Dagster, lue par les deux listes ci-dessous. Deux
# copies divergeraient, et la tuile rangee ne se decrirait plus comme celle
# qu'on retrouve en la remettant.
DESCRIPTION_DAGSTER = "Les jobs de données et leurs plannings."
DESCRIPTION_MAILPIT = "Les mails envoyés par tes applications."


def services_masques():
    """Les services que CE compte a ranges hors de son hub.

    Meme forme que services_du_hub() -- nom, titre, description -- et rien de
    plus : cette liste ne sert qu'a les reproposer dans les reglages. Elle est
    vide pour un compte qui n'est pas administrateur, comme l'autre : un
    service qu'on ne peut pas voir n'est pas un service qu'on range.
    """
    if not est_admin():
        return []
    masquees = masquees_du_compte()
    return [{"name": nom, "titre": TITRES_PAR_DEFAUT[nom],
             "description": description, "externe": True}
            for nom, description in ((DAGSTER_NOM, DESCRIPTION_DAGSTER),
                                     (MAILPIT_NOM, DESCRIPTION_MAILPIT))
            if nom in masquees]


def services_du_hub():
    """Les tuiles du hub qui ne sont pas des applications declarees.

    Dagster est RESERVE A L'ADMINISTRATEUR, et ce n'est pas une politesse :
    son interface permet de lancer des jobs, donc d'executer du code sur
    cette machine. Le proxy le refuse deja a un compte utilisateur (voir
    /api/auth-check) -- lui montrer une tuile qui le renverrait a la page de
    connexion serait promettre une porte qui ne s'ouvre pas.
    """
    services = []
    if not est_admin():
        return services
    # RANGE EST RANGE, meme pour un service. Dagster occupait une tuile que
    # rien ne permettait de retirer : sur une installation ou l'on n'ouvre
    # jamais l'orchestrateur, c'etait la seule tuile impossible a ecarter de
    # son propre hub. Le masquage reste ce qu'il est -- du rangement, par
    # compte, sans rien fermer : Dagster tourne, son adresse repond, et
    # l'administrateur le retrouve dans Parametres > Compte.
    #
    # Mailpit suit les memes regles, pour une raison voisine : son interface
    # montre tous les mails envoyes par les applications, liens de
    # reinitialisation de mot de passe compris.
    masquees = masquees_du_compte()
    if DAGSTER_NOM not in masquees:
        adresse = origine_dagster()
        if adresse:
            debout = dagster_repond()
            services.append(_tuile_service(DAGSTER_NOM, DESCRIPTION_DAGSTER,
                                           adresse, debout, DAGSTER_PORT))
    if MAILPIT_NOM not in masquees:
        adresse = origine_mailpit()
        if adresse:
            debout = mailpit_repond()
            services.append(_tuile_service(MAILPIT_NOM, DESCRIPTION_MAILPIT,
                                           adresse, debout, MAILPIT_PORT))
    return services


# Les services dont le logo n'a pas de fond propre (voir "plaque" plus bas).
SERVICES_LOGO_TRANSPARENT = frozenset({DAGSTER_NOM})


def _tuile_service(nom, description, adresse, debout, port):
    """Une tuile de service de la stack, avec les cles d'une application."""
    return {
        "name": nom,
        "titre": TITRES_PAR_DEFAUT[nom],
        "description": description,
        "categorie": CATEGORIE_CODELAB,
        "running": debout,
        "listening": debout,
        "visibility": VISIBILITE_PRIVEE,
        # Ce qui la distingue d'une application : elle a sa propre adresse,
        # et le panneau ne la demarre ni ne l'arrete.
        "externe": True,
        # Le fond clair sous le logo, sur la tuile du hub : seulement pour
        # un logo TRANSPARENT (Dagster), qui flotterait sans lui. Celui de
        # Mailpit porte deja son fond turquoise, comme le diagnostic : une
        # plaque blanche autour l'encadrait pour rien.
        "plaque": nom in SERVICES_LOGO_TRANSPARENT,
        "par_defaut": True,
        "url": adresse,
        # Les memes cles que les autres lignes, avec des valeurs vides : la
        # page lit ces champs sans se demander a chaque fois si elle a
        # affaire a un service. Un « undefined » dans une fiche est un defaut
        # d'affichage de plus a prevoir, et il finit toujours par passer.
        "path": "", "command": "", "port": port,
        # Vide : un service vit dans son propre conteneur, et le releve des
        # ports ne voit que celui du panneau. Le calculer coutait un releve
        # complet par tuile, a chaque rafraichissement du hub, pour une
        # valeur que rien ne lit (voir _ports_ecoutes).
        "port_listeners": [],
        # Le panneau ne lance pas ce service : son adresse d'ecoute n'est pas
        # la sienne a surveiller.
        "ecoute_large": False,
        # Un service de la stack ne s'archive pas.
        "archivee": False, "archivee_le": None, "effacement_le": None,
        "configuration_effacee": False,
        "failed": False, "crash_looping": False, "has_build": False, "dernier_echec": None,
        "build_command": "", "max_memory_mb": None, "alertes": [],
        "cpu_percent": 0.0, "memory_mb": 0.0,
        "presentation": None, "presentation_masquee": False, "presentation_fichier": "",
    }


def lien_panneau():
    """Le lien qui ramene au hub, ecrit pour l'origine qui sert la page.

    Un simple "/" a suffi tant que tout vivait sur le meme port. Depuis que
    les applications ont leur propre origine, "/" designe la racine du PORT
    DES APPLICATIONS -- et cette racine ne sert rien : la garde des origines
    y repond « Ce n'est pas le panneau ». Le ruban de retour, glisse dans
    chaque page d'application, ramenait donc vers une page d'erreur au lieu
    du hub.

    Sur le port du panneau, ou quand la separation n'a pas pu s'activer, "/"
    reste juste et reste relatif -- donc valable quel que soit le nom d'hote
    par lequel on est arrive.
    """
    if not sur_le_port_des_apps():
        return "/"
    # Sous un sous-domaine (blog.toi.duckdns.org), le panneau est au domaine.
    domaine = domaine_des_applications()
    if domaine:
        return f"{schema_vu()}://{domaine}/"
    hote = hote_demande()
    if not hote:
        return "/"
    return f"{request.scheme}://{hote}:{PANEL_PORT}/"
