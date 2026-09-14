"""
Definitions Dagster du projet "diagnostic".

C'est le point d'entree que /workspace/definitions.py va chercher : il expose
une variable "defs", seule convention a respecter pour qu'un projet apparaisse
dans l'interface Dagster.

Ce fichier contient tout le cote Dagster du projet :

  - l'asset diagnostic_codelab, qui execute les sondes et ecrit en base ;
  - le planning qui le declenche toutes les quinze minutes ;
  - le capteur alerte_mail_echec, qui envoie un mail a chaque run en echec.

Les sondes elles-memes vivent dans checks.py, a cote. Ce n'est pas un decoupage
arbitraire : checks.py est aussi importe par app.py, qui tourne dans
codelab-app-manager -- une image qui ne contient pas Dagster. Un module partage
entre les deux ne peut donc importer ni dagster ni flask, et c'est exactement ce
que checks.py respecte.

Deux endroits a parametrer, et un seul contient un secret :

  - les DESTINATAIRES des alertes, juste en dessous, dans ce fichier ;
  - les identifiants SMTP, dans un bloc "codelab-alertes" de credentials.env.

Le fichier credentials.env est gere par bloc -- chaque service ne reecrit que le
sien -- donc un bloc ajoute a la main sous un nom qu'aucun service ne connait
survit aux redemarrages. C'est le seul endroit ou mettre un mot de passe dans
CodeLab.

    # ===== codelab-alertes =====
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_TLS=starttls          # starttls (defaut) | ssl (port 465) | none
    SMTP_USER=moi@gmail.com
    SMTP_PASSWORD=xxxxxxxxxxxxxxxx
    # ===== /codelab-alertes =====

Sans ce bloc, le capteur ne fait rien et le dit dans ses logs : il ne fait
jamais echouer un run.
"""
import smtplib
import ssl
from email.message import EmailMessage

from dagster import (AssetExecutionContext, Definitions, DefaultScheduleStatus,
                     DefaultSensorStatus, RunFailureSensorContext, ScheduleDefinition,
                     asset, define_asset_job, run_failure_sensor)

import checks

SOURCE = "dagster"


# ==========================================================================
# Asset de diagnostic
# ==========================================================================

@asset(
    name="diagnostic_codelab",
    description="Execute les sondes de diagnostic depuis Dagster et ecrit le "
                "resultat dans la table partagee avec l'application web.",
    group_name="diagnostic",
)
def diagnostic_codelab(context: AssetExecutionContext):
    """Les memes sondes que la page web, executees depuis l'autre bout de la
    chaine.

    L'interet n'est pas de les executer deux fois pour le plaisir : une sonde
    qui passe dans app-manager mais echoue ici designe un volume mal monte ou
    un service hors du reseau, pas une panne du service teste. La comparaison
    des deux cotes localise la panne bien plus vite que chaque cote pris
    isolement.
    """
    resultats = checks.run_all()
    for ok, nom, detail in resultats:
        (context.log.info if ok else context.log.error)(
            f"{'OK   ' if ok else 'ECHEC'} {nom} -- {detail}")

    echecs = [nom for ok, nom, _ in resultats if not ok]

    # L'ecriture en base est tentee meme en cas d'echec des sondes : si c'est
    # Dagster qui n'arrive pas a joindre Postgres, l'exception ci-dessous le
    # dira plus precisement que la sonde elle-meme.
    conn = checks.connect_pg()
    try:
        detail = "toutes les sondes passent" if not echecs else f"echecs : {', '.join(echecs)}"
        ligne = checks.write_heartbeat(conn, SOURCE, detail)
        context.log.info(f"Ligne #{ligne} ecrite dans {checks.TABLE_QUALIFIEE} (source={SOURCE}).")
    finally:
        conn.close()

    if echecs:
        # Echec explicite : c'est ce qui declenche le capteur d'alerte mail,
        # et c'est aussi la seule facon de rendre le probleme visible dans
        # l'interface sans avoir a lire les logs du run.
        raise RuntimeError(f"{len(echecs)} sonde(s) en echec : {', '.join(echecs)}")

    return f"{len(resultats)} sondes OK, ligne #{ligne} ecrite"


# ==========================================================================
# Alertes par mail sur echec de job
#
# Le capteur est execute par codelab-dagster-daemon, qui tourne deja dans la
# stack. Il se declenche sur chaque run en echec, tous jobs confondus -- pas
# seulement sur l'asset ci-dessus.
# ==========================================================================

# --------------------------------------------------------------------------
# Destinataires des alertes. C'est ICI qu'on les change, pas dans
# credentials.env : une adresse de destination n'est pas un secret. La garder
# dans le code la rend visible en relecture, suivie par git, et evite de
# toucher au fichier d'identifiants pour un changement anodin.
DESTINATAIRES = [
    "moi@example.com",
]
# --------------------------------------------------------------------------

# Adresse publique de l'interface Dagster, pour que le mail contienne un lien
# cliquable vers le run. A ajuster si tu accedes au serveur autrement.
DAGSTER_URL = checks.read_env("CODELAB_DAGSTER_URL") or "http://<IP-du-serveur>:3000"


def config_smtp():
    """Renvoie la configuration, ou None si le bloc est absent/incomplet."""
    cfg = {
        "host": checks.read_env("SMTP_HOST"),
        "port": int(checks.read_env("SMTP_PORT") or 587),
        "tls": (checks.read_env("SMTP_TLS") or "starttls").lower(),
        "user": checks.read_env("SMTP_USER"),
        "password": checks.read_env("SMTP_PASSWORD"),
        # Gmail et la plupart des fournisseurs refusent d'expedier au nom
        # d'une autre adresse que celle du compte : l'expediteur suit donc
        # SMTP_USER, sauf ALERTE_FROM explicite.
        "expediteur": checks.read_env("ALERTE_FROM") or checks.read_env("SMTP_USER"),
        "destinataires": [a.strip() for a in DESTINATAIRES if a.strip()],
    }
    # user/password restent optionnels : un relais interne peut ne pas
    # demander d'authentification.
    manquants = []
    if not cfg["host"]:
        manquants.append("SMTP_HOST")
    if not cfg["expediteur"]:
        manquants.append("SMTP_USER (ou ALERTE_FROM)")
    if not cfg["destinataires"]:
        manquants.append("DESTINATAIRES")
    return (None, manquants) if manquants else (cfg, [])


def envoyer(cfg, sujet, corps):
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = cfg["expediteur"]
    msg["To"] = ", ".join(cfg["destinataires"])
    msg.set_content(corps)

    def _login(s):
        if cfg["user"] and cfg["password"]:
            s.login(cfg["user"], cfg["password"])

    if cfg["tls"] == "ssl":
        # SMTPS : session chiffree des la connexion (port 465 en general).
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                              context=ssl.create_default_context(), timeout=20) as s:
            _login(s)
            s.send_message(msg)
    elif cfg["tls"] == "none":
        # Relais interne sans chiffrement. A ne faire que sur un reseau de
        # confiance : les identifiants passeraient en clair.
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            _login(s)
            s.send_message(msg)
    else:
        # STARTTLS : on ouvre en clair puis on chiffre AVANT de s'authentifier.
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            _login(s)
            s.send_message(msg)


def corps_du_mail(context: RunFailureSensorContext):
    run = context.dagster_run
    erreur = context.failure_event.message or "(aucun message)"
    if context.failure_event.event_specific_data is not None:
        err = getattr(context.failure_event.event_specific_data, "error", None)
        if err is not None:
            erreur = err.to_string()

    return "\n".join([
        f"Job     : {run.job_name}",
        f"Run     : {run.run_id}",
        f"Statut  : ECHEC",
        f"Lien    : {DAGSTER_URL}/runs/{run.run_id}",
        "",
        "Erreur",
        "------",
        erreur.strip()[:3000],
        "",
        "-- CodeLab, capteur alerte_mail_echec",
    ])


@run_failure_sensor(
    name="alerte_mail_echec",
    description="Envoie un mail a chaque run Dagster en echec.",
    # Actif des le chargement du code : sans ca, il faut penser a l'activer a
    # la main dans l'interface, et on ne s'en apercoit qu'en ratant une alerte.
    default_status=DefaultSensorStatus.RUNNING,
)
def alerte_mail_echec(context: RunFailureSensorContext):
    cfg, manquants = config_smtp()
    if cfg is None:
        # Volontairement non fatal : une alerte qui ne part pas ne doit pas
        # ajouter une panne a la panne qu'elle signale.
        context.log.warning(
            f"Alerte mail non envoyee, configuration incomplete : {', '.join(manquants)}. "
            f"Les identifiants SMTP vont dans le bloc codelab-alertes de "
            f"{checks.ENV_FILE} ; les destinataires dans DESTINATAIRES, en tete "
            f"de definitions.py.")
        return

    run = context.dagster_run
    sujet = f"[CodeLab] Echec du job {run.job_name}"
    try:
        envoyer(cfg, sujet, corps_du_mail(context))
        context.log.info(f"Alerte envoyee a {', '.join(cfg['destinataires'])} "
                         f"pour le run {run.run_id}.")
    except Exception as e:
        context.log.error(f"Envoi de l'alerte impossible ({type(e).__name__}: {e}). "
                          f"Verifie le bloc codelab-alertes dans {checks.ENV_FILE}.")


# ==========================================================================
# Le planning : c'est lui qui rend la surveillance utile
#
# Sans planning, l'asset ne tournait que si quelqu'un allait cliquer
# "Materialize" dans Dagster. Une surveillance qu'il faut declencher ne
# previent de rien : on ne la declenche que quand on soupconne deja un
# probleme, c'est-a-dire trop tard.
#
# Et la consequence etait plus large que le seul diagnostic : le capteur
# ci-dessus est un run_failure_sensor, il reagit a un run EN ECHEC. Aucun run
# ne demarrant jamais tout seul, aucun ne pouvait echouer, donc AUCUNE alerte
# ne partait -- un systeme d'alerte complet, avec son SMTP et son repli, qui
# n'attendait qu'un clic pour servir.
#
# QUINZE MINUTES. Assez court pour qu'une panne se voie dans l'heure, assez
# long pour que le journal des runs reste lisible (96 runs par jour).
#
# Le cron n'a pas de fuseau ici : toutes les quinze minutes tombe au meme
# moment partout.
#
# CE QUI N'EST PAS PLANIFIE, et volontairement : la verification approfondie
# de /tests. Ces tests-la AGISSENT -- ils ecrivent, traversent le proxy,
# laissent des traces. Les jouer quatre fois par heure remplirait les
# journaux de traces qu'on n'a pas demandees. Les sondes de l'asset, elles,
# ne font que lire.
# ==========================================================================

job_diagnostic = define_asset_job(
    name="diagnostic_periodique",
    selection=[diagnostic_codelab],
    description="Execute les sondes de diagnostic et ecrit un battement de coeur.",
)

diagnostic_toutes_les_quinze_minutes = ScheduleDefinition(
    name="diagnostic_toutes_les_quinze_minutes",
    job=job_diagnostic,
    cron_schedule="*/15 * * * *",
    # Actif des le chargement du code, pour la meme raison que le capteur :
    # sinon il faut penser a l'activer a la main, et l'on ne s'en apercoit
    # qu'en ratant une alerte.
    default_status=DefaultScheduleStatus.RUNNING,
    description="Toutes les quinze minutes. C'est ce qui fait partir les "
                "alertes : sans run, pas d'echec, donc pas de mail.",
)


defs = Definitions(
    assets=[diagnostic_codelab],
    jobs=[job_diagnostic],
    schedules=[diagnostic_toutes_les_quinze_minutes],
    sensors=[alerte_mail_echec],
)
