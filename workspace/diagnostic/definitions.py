"""
Definitions Dagster du projet "diagnostic".

Ce fichier est le point d'entree que /workspace/definitions.py va chercher.
Il expose une variable "defs" -- c'est la seule convention a respecter pour
qu'un projet apparaisse dans l'interface Dagster.

Le projet contient un asset (qui execute les sondes cote Dagster et les
enregistre en base) et un capteur d'alerte mail importe depuis alertes.py.
"""
import checks
from alertes import alerte_mail_echec
from dagster import AssetExecutionContext, Definitions, asset

SOURCE = "dagster"


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
        context.log.info(f"Ligne #{ligne} ecrite dans {checks.TABLE} (source={SOURCE}).")
    finally:
        conn.close()

    if echecs:
        # Echec explicite : c'est ce qui declenche le capteur d'alerte mail,
        # et c'est aussi la seule facon de rendre le probleme visible dans
        # l'interface sans avoir a lire les logs du run.
        raise RuntimeError(f"{len(echecs)} sonde(s) en echec : {', '.join(echecs)}")

    return f"{len(resultats)} sondes OK, ligne #{ligne} ecrite"


defs = Definitions(
    assets=[diagnostic_codelab],
    sensors=[alerte_mail_echec],
)
