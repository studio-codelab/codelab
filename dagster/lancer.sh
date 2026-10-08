#!/bin/sh
# Lance les deux programmes de Dagster dans CE conteneur : le daemon
# (plannings, capteurs, file des runs) en tache de fond, et l'interface web au
# premier plan.
#
# POURQUOI UN SEUL CONTENEUR. Ils etaient deux (codelab-dagster et
# codelab-dagster-daemon) : meme image, memes montages, memes capacites, meme
# utilisateur, meme configuration. Deux blocs a tenir d'accord pour rien, et
# 384 Mo de plafond de plus. La securite n'y perd rien : ce script est lance
# par l'entrypoint APRES la bascule sur l'utilisateur dagster (gosu), le daemon
# n'a jamais eu de port, et l'interface reste derriere codelab-dagster-proxy.
#
# LE DAEMON EST RELANCE S'IL S'ARRETE. Seul, il tombait et Docker le relancait
# (restart: unless-stopped) ; ici c'est cette boucle qui s'en charge, avec une
# pause pour ne pas tourner a vide si le code de /workspace ne se charge pas.
# Ses lignes arrivent dans « docker logs codelab-dagster », avec celles de
# l'interface ; les siennes portent « dagster.daemon ».
#
# L'ARRET RESTE PROPRE. Docker envoie SIGTERM a ce script (PID 1 apres les
# exec de l'entrypoint) ; il le transmet aux deux programmes et attend qu'ils
# aient fini, au lieu de les laisser tuer net au bout du delai de grace.
set -u

# « -m definitions » : le module /workspace/definitions.py (PYTHONPATH). Sans
# lui, dagster-daemon echoue au demarrage -- voir dagster/README.md.
MODULE="${CODELAB_DAGSTER_MODULE:-definitions}"
PAUSE_RELANCE=10

# LES PROJETS ISOLES. Un projet qui porte un requirements-dagster.txt tourne
# dans son propre environnement Python : isolation.py le construit, puis
# ecrit un workspace.yaml qui le declare a cote de l'emplacement commun --
# voir dagster/isolation.py. Sans projet isole, il n'ecrit rien et l'on garde
# « -m definitions », exactement comme avant.
#
# Le script est facultatif (une image ancienne, les tests) : absent ou en
# echec, Dagster demarre comme avant, sans isolation, plutot que pas du tout.
ISOLATION="${CODELAB_ISOLATION:-/opt/dagster/isolation.py}"
CIBLE_CODE="-m $MODULE"
if [ -f "$ISOLATION" ]; then
  fichier=$(python3 "$ISOLATION") || fichier=""
  [ -n "$fichier" ] && [ -f "$fichier" ] && CIBLE_CODE="-w $fichier"
fi

arret=0
daemon_pid=""
web_pid=""

arreter() {
  arret=1
  [ -n "$web_pid" ] && kill -TERM "$web_pid" 2>/dev/null
  [ -n "$daemon_pid" ] && kill -TERM "$daemon_pid" 2>/dev/null
}
trap arreter TERM INT

# Le daemon, dans une boucle qui s'arrete quand le conteneur s'arrete.
(
  enfant=""
  trap '[ -n "$enfant" ] && kill -TERM "$enfant" 2>/dev/null; exit 0' TERM INT
  while :; do
    # PAS de tube (« | sed ») : $! serait alors le PID du dernier programme
    # du tube, et l'arret tuerait le filtre en laissant le daemon tourner.
    # $CIBLE_CODE sans guillemets, a dessein : deux mots, l'option et sa
    # valeur. Ni l'une ni l'autre ne contient d'espace.
    dagster-daemon run $CIBLE_CODE &
    enfant=$!
    wait "$enfant"
    echo "[codelab-dagster] daemon arrete -- relance dans ${PAUSE_RELANCE} s." >&2
    sleep "$PAUSE_RELANCE" &
    wait $!
  done
) &
daemon_pid=$!

# L'interface web. Arguments passes au conteneur, s'il y en a (commande du
# compose) ; sinon la commande par defaut.
if [ "$#" -gt 0 ]; then
  "$@" &
else
  dagster-webserver -h 0.0.0.0 -p 3000 $CIBLE_CODE &
fi
web_pid=$!

# Si l'interface s'arrete d'elle-meme, le conteneur s'arrete avec elle : c'est
# ce que surveille le healthcheck, et Docker le relancera.
wait "$web_pid"
statut=$?
# Interrompu par SIGTERM, « wait » rend la main tout de suite : on attend que
# l'interface ait vraiment fini de s'arreter.
interrompu=$arret
[ "$interrompu" -eq 1 ] && wait "$web_pid" 2>/dev/null
arreter
wait "$daemon_pid" 2>/dev/null
[ "$interrompu" -eq 1 ] && exit 0
exit "$statut"
