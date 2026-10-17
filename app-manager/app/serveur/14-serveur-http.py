# CodeLab -- LE PANNEAU, SERVEUR HTTP
# ---------------------------------------------------------------------------
# ROLE        waitress, les deux origines sur leurs ports.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   serveur HTTP.
#

# ------------------------------ serveur HTTP ------------------------------
#
# Le serveur de developpement de Flask affiche lui-meme un avertissement, et
# il est merite : ce panneau ne sert pas que ses propres pages, il relaie TOUT
# le trafic de toutes les applications deployees. waitress est un serveur WSGI
# de production, en Python pur, sans configuration -- le remplacement le moins
# couteux possible, et le comportement est identique cote application.
#
# Repli sur le serveur de Flask si waitress n'est pas installe : le service
# doit rester lancable depuis un depot fraichement clone, sans rien installer
# de plus que Flask.

def _ouvrir_port_des_applications(servir_sur):
    """Le second ecouteur, celui des applications.

    Ouvert AVANT le port du panneau et de maniere synchrone jusqu'a ce qu'on
    sache s'il tient : c'est ce resultat qui decide si la separation des
    origines est active, et le panneau doit le savoir des sa premiere reponse.

    S'il ne s'ouvre pas -- port deja pris, non publie par le compose -- on ne
    separe rien et les applications restent servies par le panneau, comme
    avant. Une stack qui marche moins bien vaut mieux qu'une stack morte, et
    le panneau le dit dans le diagnostic.
    """
    if not APPS_PORT:
        print("[app-manager] APP_MANAGER_APPS_PORT=0 : les applications "
              "restent servies sur le port du panneau.", flush=True)
        return
    pret = threading.Event()
    souci = {}

    def _servir():
        try:
            import socket as _s
            sonde = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
            sonde.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
            sonde.bind(("0.0.0.0", APPS_PORT))
            sonde.close()
        except OSError as e:
            souci["erreur"] = e
            pret.set()
            return
        pret.set()
        servir_sur(APPS_PORT)

    threading.Thread(target=_servir, daemon=True).start()
    pret.wait(timeout=10)
    if souci:
        print(f"[app-manager] port {APPS_PORT} indisponible ({souci['erreur']}) : "
              f"les applications restent servies sur le port du panneau. "
              f"Publie ce port dans docker-compose.yml pour les isoler.", flush=True)
        return
    _origines_separees["actif"] = True
    print(f"[app-manager] applications isolees sur 0.0.0.0:{APPS_PORT} "
          f"-- origine distincte de celle du panneau.", flush=True)


def servir(port):
    # Le cookie de session suit le reglage d'exposition. Pose ici et non a
    # l'import : STATE_DIR peut etre redirige (tests, autre installation),
    # et lire le fichier trop tot donnerait la valeur du mauvais endroit.
    appliquer_cookie_securise()

    try:
        from waitress import serve
        def _sur(p):
            # ident : l'en-tete Server annonce "CodeLab" plutot que la version
            # exacte de waitress, qui ne renseigne que celui qui cherche une
            # faille connue.
            serve(flask_app, host="0.0.0.0", port=p, threads=WSGI_THREADS,
                  channel_timeout=WSGI_TIMEOUT, connection_limit=WSGI_CONNEXIONS,
                  ident="CodeLab")
    except ImportError:
        print("[app-manager] waitress absent : repli sur le serveur de "
              "developpement de Flask (a eviter en service).", flush=True)
        def _sur(p):
            flask_app.run(host="0.0.0.0", port=p, threaded=True)

    # Dans les DEUX cas : le repli sans waitress doit separer les origines
    # comme le service normal. Le premier jet ne le faisait pas -- il rendait
    # la main avant -- et une installation sans waitress se serait retrouvee
    # avec des applications dans l'origine du panneau, sans que rien ne le
    # dise.
    _ouvrir_port_des_applications(_sur)
    print(f"[app-manager] panneau sur 0.0.0.0:{port}.", flush=True)
    _sur(port)
