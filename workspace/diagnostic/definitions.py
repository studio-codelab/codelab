"""
Definitions Dagster du projet "diagnostic".

C'est le point d'entree que /workspace/definitions.py va chercher : il expose
une variable "defs", seule convention a respecter pour qu'un projet apparaisse
dans l'interface Dagster.

Ce fichier contient tout le cote Dagster du projet :

  - l'asset diagnostic_codelab, qui execute les sondes et ecrit en base ;
  - le planning qui le declenche toutes les heures ;
  - le capteur alerte_mail_echec, qui envoie un mail a chaque run en echec ;
  - l'asset audit_dependances, qui confronte chaque semaine les dependances
    des projets a la base de vulnerabilites OSV.

CE QUI FAIT ECHOUER UN RUN, ET CE QUI NE LE FAIT PAS. Les sondes ne rendent
plus "oui / non" mais un RANG (checks.Etat) : ECHEC, ALERTE, SANS_OBJET, OK.
Seul le rang ECHEC fait tomber le run -- donc partir le mail. Une alerte est
journalisee en warning et le run reste vert.

C'est le coeur du reglage. Avant, un disque a 87 %, un namespace utilisateur
refuse par l'hote et une base de donnees injoignable faisaient exactement la
meme chose : un run rouge et un mail. Trois alertes sur quatre ne demandaient
aucun geste immediat, et c'est ainsi qu'on cesse de lire ses alertes -- puis
qu'on rate la quatrieme. La hierarchie se lit dans SEVERITE_MAX, en un seul
endroit, dans checks.py.

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
                     DefaultSensorStatus, Failure, MetadataValue, RunFailureSensorContext,
                     ScheduleDefinition, asset, define_asset_job, run_failure_sensor)

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

    Le run echoue sur les sondes de rang ECHEC, et sur elles seules : voir
    l'en-tete de ce fichier.
    """
    resultats = checks.run_all()
    for etat, nom, detail in resultats:
        journal = {checks.Etat.ECHEC: context.log.error,
                   checks.Etat.ALERTE: context.log.warning}.get(etat, context.log.info)
        journal(f"{etat.libelle:<10} {nom} -- {detail}")

    rangs = checks.noms_par_rang(resultats)
    critiques = list(rangs[checks.Etat.ECHEC])
    alertes = list(rangs[checks.Etat.ALERTE])
    sans_objet = list(rangs[checks.Etat.SANS_OBJET])

    # L'ecriture en base est tentee meme en cas d'echec des sondes : si c'est
    # Dagster qui n'arrive pas a joindre Postgres, l'exception ci-dessous le
    # dira plus precisement que la sonde elle-meme.
    #
    # ATTRAPEE, et non laissee filer comme avant. Une connexion refusee
    # sortait en trace de quarante lignes qui remplacait tout le reste : le
    # resume des sondes n'etait jamais ecrit, et la cause reelle -- le mot de
    # passe absent, dit deux lignes plus haut par la sonde credentials.env --
    # disparaissait sous la pile d'appels de psycopg2. On lisait "OperationalError"
    # la ou il fallait lire "le secret n'est pas arrive dans ce conteneur".
    ligne, cause = None, None
    try:
        conn = checks.connect_pg()
        try:
            ligne = checks.write_heartbeat(conn, SOURCE, _resume(critiques, alertes))
            context.log.info(
                f"Ligne #{ligne} ecrite dans {checks.TABLE_QUALIFIEE} (source={SOURCE}).")
        finally:
            conn.close()
    except Exception as e:                                        # noqa: BLE001
        cause = e
        critiques.append("ecriture du battement de coeur")
        context.log.error(f"Battement de coeur non ecrit ({type(e).__name__}: {e}) -- "
                          f"la chaine Dagster -> Postgres est coupee.")

    context.add_output_metadata({
        "sondes": len(resultats),
        "critiques": len(critiques),
        "alertes": len(alertes),
        "sans objet": len(sans_objet),
        "ligne ecrite": ligne if ligne is not None else "aucune",
        # Le tableau complet dans l'interface : lire un etat des lieux ne
        # doit pas demander d'ouvrir les journaux du run et de les derouler.
        "etat des lieux": MetadataValue.md(_tableau(resultats)),
    })

    if critiques:
        # Echec explicite : c'est ce qui declenche le capteur d'alerte mail,
        # et c'est aussi la seule facon de rendre le probleme visible dans
        # l'interface sans avoir a lire les logs du run.
        message = f"{len(critiques)} sonde(s) en echec : {', '.join(critiques)}"
        if alertes:
            message += f" (+ {len(alertes)} alerte(s) : {', '.join(alertes)})"
        if cause is not None:
            # Chainee, pas recopiee : la trace de psycopg2 reste consultable
            # sous le message qui, lui, se lit.
            raise RuntimeError(message) from cause
        raise RuntimeError(message)

    if alertes:
        # Le run reste VERT. C'est tout le propos : une alerte se regarde
        # dans la journee, elle ne reveille personne et n'use pas le credit
        # d'attention du mail d'echec.
        context.log.warning(
            f"{len(alertes)} alerte(s), aucune sonde critique : {', '.join(alertes)}. "
            f"Le run reste vert -- ces points se traitent quand tu passes, pas "
            f"dans l'urgence.")

    return _resume(critiques, alertes) + (f", ligne #{ligne} ecrite" if ligne else "")


def _resume(critiques, alertes):
    """La phrase que porte le battement de coeur, et le retour de l'asset."""
    if not critiques and not alertes:
        return "toutes les sondes passent"
    morceaux = []
    if critiques:
        morceaux.append(f"echecs : {', '.join(critiques)}")
    if alertes:
        morceaux.append(f"alertes : {', '.join(alertes)}")
    return " | ".join(morceaux)


def _tableau(resultats):
    """Les resultats en markdown, pour l'onglet des metadonnees de Dagster."""
    lignes = ["| Etat | Sonde | Detail |", "|---|---|---|"]
    for etat, nom, detail in resultats:
        detail = str(detail).replace("|", "\\|").replace("\n", " ")
        lignes.append(f"| {etat.libelle} | {nom} | {detail} |")
    return "\n".join(lignes)


# ==========================================================================
# Alertes par mail sur echec de job
#
# Le capteur est execute par le daemon de Dagster, qui tourne dans
# codelab-dagster. Il se declenche sur chaque run en echec, tous jobs confondus -- pas
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


# Comme les mails du panneau, l'alerte passe d'abord par Mailpit : elle se
# relit dans son interface, et Mailpit la relaie par le serveur d'envoi de
# CodeLab. Mailpit injoignable : envoi direct, pour qu'une alerte ne tombe
# pas avec le service qui devait la porter.
MAILPIT_SMTP_HOTE = checks.read_env("CODELAB_MAILPIT_SMTP_HOTE") or "codelab-mailpit"
MAILPIT_SMTP_PORT = int(checks.read_env("CODELAB_MAILPIT_SMTP_PORT") or 1025)


def envoyer(cfg, sujet, corps):
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = cfg["expediteur"]
    msg["To"] = ", ".join(cfg["destinataires"])
    msg.set_content(corps)

    try:
        with smtplib.SMTP(MAILPIT_SMTP_HOTE, MAILPIT_SMTP_PORT, timeout=10) as s:
            s.send_message(msg)
        return
    except (OSError, smtplib.SMTPException) as e:
        print(f"[diagnostic] Mailpit injoignable ({type(e).__name__}: {e}) : envoi direct.")

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
# UNE FOIS PAR HEURE, a la septieme minute. C'etait toutes les quinze
# minutes : sur une ZimaBlade, chaque run demarre un processus Python qui
# recharge Dagster et le code, interroge le panneau et ecrit dans Postgres --
# un pic sur trois conteneurs, 96 fois par jour. Une panne se voit toujours
# dans l'heure, pour quatre fois moins de travail (24 runs par jour).
#
# La minute 7 et pas 0 : l'heure pile est le moment ou tout le reste se
# declenche aussi (taches planifiees, sauvegardes, sondes exterieures).
#
# Le cron n'a pas de fuseau ici : la minute 7 tombe au meme moment partout.
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

diagnostic_toutes_les_heures = ScheduleDefinition(
    name="diagnostic_toutes_les_heures",
    job=job_diagnostic,
    cron_schedule="7 * * * *",
    # Actif des le chargement du code, pour la meme raison que le capteur :
    # sinon il faut penser a l'activer a la main, et l'on ne s'en apercoit
    # qu'en ratant une alerte.
    default_status=DefaultScheduleStatus.RUNNING,
    description="Toutes les heures. C'est ce qui fait partir les "
                "alertes : sans run, pas d'echec, donc pas de mail.",
)


# ==========================================================================
# Audit des dependances des projets, une fois par semaine
#
# Trivy ne regarde que le depot de la stack. Cet asset regarde les projets de
# /workspace : ce qu'ils figent (requirements, vendor/, package-lock.json) est
# confronte a la base publique OSV -- voir checks.audit_dependances.
#
# UNE DEPENDANCE VULNERABLE FAIT ECHOUER LE RUN, donc partir le mail du
# capteur ci-dessus : c'est le seul canal d'alerte qui existe deja, et un
# avis de securite est exactement ce qui doit le prendre. Le run reste rouge
# chaque semaine tant que la version n'a pas ete montee.
#
# OSV injoignable fait aussi echouer le run. Un audit qui ne peut pas se faire
# et ne le dit pas ressemble trait pour trait a un audit qui n'a rien trouve.
#
# Le lundi a 6 h 23 : apres la reconstruction hebdomadaire des images (5 h
# UTC) et loin de l'heure pile, ou se declenche deja tout le reste.
# ==========================================================================

@asset(
    name="audit_dependances",
    description="Confronte les dependances figees de chaque projet de /workspace "
                "a la base de vulnerabilites OSV.",
    group_name="diagnostic",
)
def audit_dependances(context: AssetExecutionContext):
    try:
        nombre, rapport = checks.audit_dependances()
    except (OSError, ValueError) as e:
        raise Failure(f"Audit impossible : OSV ({checks.OSV_URL}) ne repond pas "
                      f"({type(e).__name__}: {e}).")

    lignes = []
    for projet, paquets in rapport.items():
        for eco, nom, version, ids in paquets:
            lignes.append(f"{projet} : {nom} {version} ({eco}) -- "
                          + ", ".join(f"https://osv.dev/vulnerability/{i}" for i in ids))
    for ligne in lignes:
        context.log.error(ligne)
    context.add_output_metadata({
        "paquets_audites": nombre,
        "projets_touches": len(rapport),
        "rapport": MetadataValue.md("\n".join(f"- {l}" for l in lignes) or "Rien a signaler."),
    })
    if lignes:
        raise Failure(f"{len(lignes)} dependance(s) vulnerable(s) dans "
                      f"{len(rapport)} projet(s) :\n" + "\n".join(lignes))
    context.log.info(f"{nombre} paquet(s) audite(s), aucune vulnerabilite connue.")
    return f"{nombre} paquet(s), aucune vulnerabilite connue"


job_audit = define_asset_job(
    name="audit_dependances_hebdomadaire",
    selection=[audit_dependances],
    description="Audite les dependances des projets de /workspace (base OSV).",
)

audit_chaque_semaine = ScheduleDefinition(
    name="audit_dependances_chaque_semaine",
    job=job_audit,
    cron_schedule="23 6 * * 1",
    default_status=DefaultScheduleStatus.RUNNING,
    description="Le lundi matin. Une dependance vulnerable fait echouer le run, "
                "donc partir un mail.",
)


defs = Definitions(
    assets=[diagnostic_codelab, audit_dependances],
    jobs=[job_diagnostic, job_audit],
    schedules=[diagnostic_toutes_les_heures, audit_chaque_semaine],
    sensors=[alerte_mail_echec],
)
